"""Sequential direct-model generation followed by independent audit and scoring."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

from . import dataset, protocol, scoring
from tools import direct_codex as direct
from tools import direct_runtime as runtime


def generation_prompt(task_description: str, work: Path, rounds: int) -> str:
    tools = ", ".join(protocol.TOOLS)
    return f"""Solve this BlenderBench task using direct model reasoning and only the four Blender MCP tools.
Task: {task_description}
Initial scene: {work / 'initial.blend'}
All scene properties may be edited, including geometry, materials, lights, objects, and cameras.
Perform exactly {rounds} meaningful edit, render, and visual-inspection rounds sequentially. Each round must
change the saved scene meaningfully and continue from the preceding saved checkpoint. Inspect generated
images only through observable Blender MCP responses. Call get_render_as_image_for_cli after each saved round
and visually inspect the returned image content; Codex's built-in view_image is disabled. No CLIP/scoring,
score feedback, VLM judge, benchmark controller, goal_code,
solution code, goal scene, or oracle access. Do not read evaluator, audit, or post-generation result files.
Use only {tools} for scene operations; no shell scene operations. The standalone bpy backend opens the
specified blend file afresh for each call, so save each edit before the call returns. Use GPU-only Cycles:
enable CUDA devices, disable CPU, and never silently fall back to CPU. Save the scene before transient PNG
filepath/encoding changes, and do not save those output overrides. Never modify initial.blend, dataset
files, or target.png. Save absolute paths {work / 'iteration01.blend'} and {work / 'iteration01.png'}
through {work / f'iteration{rounds:02}.blend'} and {work / f'iteration{rounds:02}.png'}.
For each edit call, set blend_file to initial.blend for round 1 or the preceding iteration blend thereafter,
and set expected_output_blend to that round's new iterationNN.blend so the MCP server attests fresh changed bytes.
Append rounds.jsonl via MCP with one JSON object per completed round containing round, absolute blend,
absolute png, and a nonempty rationale describing the visual hypothesis and actual edit. Stop only after
all {rounds} sequential saved rounds and ledger rows exist."""


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _bpy_version(config) -> str:
    code = "import bpy; print(bpy.app.version_string)"
    return subprocess.check_output([str(config.bpy_python), "-c", code], text=True,
                                   stderr=subprocess.STDOUT).strip().splitlines()[-1]


def _versions(config) -> dict:
    codex = protocol.resolved_version(config.codex_binary, "--version")
    if codex != protocol.CODEX_VERSION or config.codex_version != protocol.CODEX_VERSION:
        raise ValueError(f"Codex version {codex!r} does not equal frozen {protocol.CODEX_VERSION!r}")
    bpy = _bpy_version(config)
    if not bpy.startswith(config.bpy_version):
        raise ValueError(f"bpy version {bpy!r} does not match pin {config.bpy_version!r}")
    scorer_runtime = scoring.preflight_scorer_runtime()
    clip_snapshot = scoring.verify_clip_snapshot(config.clip_snapshot)
    return {"codex": codex, "codex_source_revision": protocol.CODEX_SOURCE_REVISION,
            "bpy": bpy, "scorer_python": "Python " + scorer_runtime["python_version"],
            "scorer_script_sha256": scoring.scorer_sha256(),
            "scorer_runtime": scorer_runtime, "clip_snapshot": clip_snapshot}


def _preflight(config, mcp_config: dict, work: Path, evidence: Path) -> None:
    args = SimpleNamespace(codex=str(config.codex_binary), model=config.model, workdir=work)
    nonce = uuid.uuid4().hex
    code = ("import bpy,sys; result={'preflight':" + repr(nonce)
            + ", 'python':sys.executable, 'version':bpy.app.version_string}")
    prompt = (f"Call only execute_blender_code_for_cli once with blend_file={work / 'initial.blend'} "
              f"and code={code}. Do not mutate/save. No shell or other tools.")
    events = direct.invoke(args, mcp_config, evidence, "preflight", prompt)
    direct.verify_preflight(events, nonce, str(config.bpy_python), config.bpy_version)


def verify_retained_generation(task_root: Path, work: Path, rounds: int) -> None:
    """Revalidate the retained generation event stream before recovery admission."""
    path = Path(task_root) / "codex" / "run.jsonl"
    if path.is_symlink() or not path.is_file():
        raise ValueError("recovery requires regular retained codex/run.jsonl evidence")
    try:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("retained generation event evidence is unreadable") from exc
    direct.verify_generation_events(events, rounds=rounds, workdir=work)


def _task(config, task: str, mcp_config: dict, scorer_runtime: dict) -> list[dict]:
    record = dataset.load_manifest()["tasks"][task]
    source = config.dataset_root / task
    verification = dataset.verify(task, config.dataset_root)
    task_root = config.output_root / task.replace("/", "-")
    task_root.mkdir(parents=True, exist_ok=False)
    _json(task_root / "input-verification.json", verification)
    events = task_root / "events.jsonl"
    policy = {key: record[key] for key in ("reviewed", "review_basis", "start_sha256",
                                           "task_description", "initialization")}
    policy.update(task=task, pin_verified=True, stop_file=str(config.output_root / "STOP"))
    runtime.emit(events, "initializing", task=task)
    initial = runtime.initialize(source, policy, config.bpy_python, config.bpy_version,
                                 task_root / "work", events)
    work = task_root / "work"
    initialized = Path(initial["blend"])
    baseline_path = work / "initial.blend"
    initialized.rename(baseline_path)
    initial_hash = runtime.sha(baseline_path)
    evidence = task_root / "codex"
    evidence.mkdir(mode=0o700)
    _json(evidence / "mcp-config.redacted.json", protocol.redact(mcp_config))
    direct.verify_generation_workdir(work)
    _preflight(config, mcp_config, work, evidence)
    runtime.emit(events, "generation_started", task=task)
    args = SimpleNamespace(codex=str(config.codex_binary), model=config.model, workdir=work)
    generation_events = direct.invoke(args, mcp_config, evidence, "run",
        generation_prompt(record["task_description"], work, config.rounds), source / "target.png")
    direct.verify_generation_events(generation_events, rounds=config.rounds, workdir=work)
    runtime.emit(events, "generation_finished", task=task)
    if runtime.sha(baseline_path) != initial_hash:
        raise ValueError("generation modified initial.blend")
    dataset.verify(task, config.dataset_root)
    artifacts = direct.verify_checkpoints(work, config.rounds, policy=policy)
    _json(task_root / "artifact-check.json", artifacts)

    previous = initial
    seen = {json.dumps(initial["audit"], sort_keys=True, allow_nan=False)}
    rows = []
    admissions = []
    for number in range(1, config.rounds + 1):
        audit = runtime.inspect_render(work / f"iteration{number:02}.blend", policy,
            config.bpy_python, config.bpy_version, task_root / f"audit{number:02}", events)
        direct.validate_edit(previous, audit, policy)
        state = json.dumps(audit["audit"], sort_keys=True, allow_nan=False)
        if state in seen:
            raise ValueError(f"round {number} repeats an earlier complete scene state")
        seen.add(state); previous = audit
        admissions.append({"round": number, "source": audit["source"],
            "source_sha256": audit["source_sha256"], "image": audit["png"],
            "image_sha256": audit["png_sha256"], "audit_sha256": protocol.digest(audit["audit"]),
            "meaningful_change_validated": True})
        _json(task_root / "audit-admission.json", admissions)
        try:
            metrics = scoring.score_pair(Path(audit["png"]), source / "target.png",
                                         config.clip_snapshot, scorer_runtime)
            row = {"task": task, "round": number, "status": "complete", **metrics,
                   "blend": audit["blend"], "blend_sha256": audit["blend_sha256"],
                   "image": audit["png"], "image_sha256": audit["png_sha256"]}
        except Exception as exc:
            row = {"task": task, "round": number, "status": "failed",
                   "error": f"{type(exc).__name__}: {exc}", "blend": audit["blend"],
                   "image": audit["png"]}
        rows.append(row)
        _json(task_root / "scores.json", rows)
    return rows


def recover_suite(args) -> int:
    """Reopen, audit, render, and score retained rounds; never call generation."""
    tasks = dataset.select_tasks(args.tasks)
    for name in ("bpy_python",):
        if not getattr(args, name).is_file():
            raise FileNotFoundError(getattr(args, name))
    manifest = protocol.load_run_manifest(args.source_root, tasks=tasks, rounds=args.rounds)
    scorer_runtime = scoring.preflight_scorer_runtime()
    clip_snapshot = scoring.verify_clip_snapshot(args.clip_snapshot)
    score_provenance = scoring.scoring_provenance(scorer_runtime, clip_snapshot)
    score_provenance.update({"protocol_sha256": manifest["protocol_sha256"],
                             "source_identity": manifest["protocol"]["source_identity"],
                             "model": manifest["protocol"]["model"],
                             "model_revision": manifest["protocol"]["model_revision"],
                             "versions": manifest["protocol"]["versions"]})
    actual_bpy = _bpy_version(args)
    generation_bpy = manifest["protocol"]["versions"].get("bpy")
    if actual_bpy != generation_bpy:
        raise ValueError(f"recovery bpy version {actual_bpy!r} differs from generation manifest {generation_bpy!r}")
    if not actual_bpy.startswith(args.bpy_version):
        raise ValueError(f"bpy version {actual_bpy!r} does not match pin {args.bpy_version!r}")
    if args.output_root.exists():
        raise FileExistsError("fresh --output-root required for recovery evidence")
    args.output_root.mkdir(parents=True, exist_ok=False)
    all_rows = []
    for task in tasks:
        record = dataset.load_manifest()["tasks"][task]
        dataset.verify(task, args.dataset_root)
        source_task = args.source_root / task.replace("/", "-")
        work = source_task / "work"
        policy = {key: record[key] for key in ("reviewed", "review_basis", "start_sha256",
                                               "task_description", "initialization")}
        policy.update(task=task, pin_verified=True, stop_file=str(args.output_root / "STOP"))
        verify_retained_generation(source_task, work, args.rounds)
        direct.verify_checkpoints(work, args.rounds, policy=policy)
        baseline = json.loads((work / "metadata.json").read_text(encoding="utf-8"))
        initial_path = work / "initial.blend"
        initial_hash = runtime.sha(initial_path)
        if initial_hash != baseline["blend_sha256"]:
            raise ValueError(f"retained initial hash mismatch for {task}")
        previous = baseline
        seen = {json.dumps(baseline["audit"], sort_keys=True, allow_nan=False)}
        rows = []
        admissions = []
        task_out = args.output_root / task.replace("/", "-")
        task_out.mkdir(parents=True, exist_ok=False)
        for number in range(1, args.rounds + 1):
            audit = runtime.inspect_render(work / f"iteration{number:02}.blend", policy,
                args.bpy_python, args.bpy_version, task_out / f"audit{number:02}",
                task_out / "events.jsonl")
            direct.validate_edit(previous, audit, policy)
            state = json.dumps(audit["audit"], sort_keys=True, allow_nan=False)
            if state in seen:
                raise ValueError(f"round {number} repeats an earlier scene state")
            seen.add(state); previous = audit
            admissions.append({"round": number, "source": audit["source"],
                "source_sha256": audit["source_sha256"], "image": audit["png"],
                "image_sha256": audit["png_sha256"], "audit_sha256": protocol.digest(audit["audit"]),
                "meaningful_change_validated": True})
            _json(task_out / "audit-admission.json", admissions)
            try:
                metrics = scoring.score_pair(Path(audit["png"]),
                    args.dataset_root / task / "target.png", args.clip_snapshot, scorer_runtime)
                row = {"task": task, "round": number, "status": "complete", **metrics,
                       "blend": audit["blend"], "blend_sha256": audit["blend_sha256"],
                       "image": audit["png"], "image_sha256": audit["png_sha256"]}
            except Exception as exc:
                row = {"task": task, "round": number, "status": "failed",
                       "error": f"{type(exc).__name__}: {exc}"}
            rows.append(row); all_rows.append(row)
            _json(task_out / "scores.json", rows)
        if runtime.sha(initial_path) != initial_hash:
            raise ValueError(f"recovery modified retained initial scene for {task}")
    result = scoring.aggregate(all_rows, tasks, expected_rounds=args.rounds,
                               provenance=score_provenance)
    scoring.write_outputs(result, args.output_root / "scoring")
    _json(args.output_root / "recovery-manifest.json", {"schema_version": 1,
        "source_root": str(args.source_root), "regenerated": False, "tasks": tasks,
        "bpy_version": actual_bpy, **score_provenance,
        "dataset_revision": dataset.DATASET_REVISION,
        "evaluator_revision": protocol.EVALUATOR_REVISION,
        "evaluator_sha256": protocol.EVALUATOR_SHA256})
    return int(result["summary"]["failed_tasks"] != 0)


def run_suite(config, *, approved: bool) -> int:
    if not approved:
        raise ValueError("--approve-blender-tools is required for arbitrary MCP Python execution")
    if config.output_root.exists():
        raise FileExistsError("fresh --output-root required; automatic resume/regeneration is forbidden")
    source_identity = protocol.collect_source_identity()
    if source_identity["git_dirty"]:
        raise ValueError("publication generation requires a clean Git source tree")
    raw_mcp = protocol.read_mcp_config(config.mcp_toml)
    mcp_config = direct.prepare_config(raw_mcp, approved=True)
    configured_bpy = mcp_config["mcp_servers"]["blender"]["env"]["BLENDER_MCP_BPY_PYTHON"]
    if Path(configured_bpy).absolute() != config.bpy_python:
        raise ValueError("MCP TOML bpy interpreter differs from --bpy-python")
    versions = _versions(config)
    config.output_root.mkdir(parents=True, exist_ok=False)
    public_config = asdict(config)
    public_config = {key: ([str(item) for item in value] if key == "tasks" else str(value)
                          if isinstance(value, Path) else value) for key, value in public_config.items()}
    public_config["mcp"] = raw_mcp
    manifest = protocol.build_manifest(public_config, dataset_revision=dataset.DATASET_REVISION,
        evaluator_revision=protocol.EVALUATOR_REVISION, model_revision=config.model_revision,
        versions=versions, source_identity=source_identity)
    manifest["dataset"] = {"source_url": dataset.DATASET_SOURCE_URL,
        "attribution": dataset.DATASET_ATTRIBUTION, "license": dataset.DATASET_LICENSE}
    manifest["evaluator"] = {"source_url": protocol.EVALUATOR_REPOSITORY,
        "revision": protocol.EVALUATOR_REVISION, "sha256": protocol.EVALUATOR_SHA256}
    protocol.write_manifest(config.output_root / "run-manifest.json", manifest)
    _json(config.output_root / "process.json", {"pid": os.getpid(), "argv": sys.argv,
                                                 "protocol_sha256": manifest["protocol_sha256"]})
    all_rows = []
    states = {}
    runtime.emit(config.output_root / "events.jsonl", "dispatcher_started", tasks=list(config.tasks))
    for task in config.tasks:
        if (config.output_root / "STOP").exists():
            states[task] = {"status": "cancelled"}; break
        try:
            rows = _task(config, task, mcp_config, versions["scorer_runtime"])
            all_rows.extend(rows)
            state = "complete" if all(row["status"] == "complete" for row in rows) else "failed"
            states[task] = {"status": state}
        except Exception as exc:
            states[task] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        _json(config.output_root / "status.json", states)
    # Every selected task is represented, including cancellation/not-started cases.
    score_provenance = scoring.scoring_provenance(
        versions["scorer_runtime"], versions["clip_snapshot"])
    score_provenance.update({"protocol_sha256": manifest["protocol_sha256"],
                             "source_identity": manifest["protocol"]["source_identity"],
                             "model": manifest["protocol"]["model"],
                             "model_revision": manifest["protocol"]["model_revision"],
                             "versions": versions})
    result = scoring.aggregate(all_rows, config.tasks, expected_rounds=config.rounds,
                               provenance=score_provenance)
    scoring.write_outputs(result, config.output_root / "scoring")
    runtime.emit(config.output_root / "events.jsonl", "dispatcher_finished", states=states)
    return int(result["summary"]["failed_tasks"] != 0)
