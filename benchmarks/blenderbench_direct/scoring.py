"""Portable post-generation BlenderBench scoring and aggregation."""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable

from . import dataset, protocol


CANONICAL_SCORER = Path(__file__).with_name("score_pair.py")
SCORING_LOCK = Path(__file__).with_name("requirements-scoring.lock")
CLIP_SNAPSHOT_MANIFEST = Path(__file__).with_name("clip-snapshot-manifest.json")
_ALLOWED_BOOTSTRAP_DISTRIBUTIONS = {"pip"}
_SCORER_ENV_KEYS = {"CUDA_VISIBLE_DEVICES", "HOME"}
_EXPECTED_RUNTIME = {
    "python_version": "3.13.15", "python_compiler": "Clang 22.1.3",
    "implementation": "CPython", "cache_tag": "cpython-313",
    "soabi": "cpython-313-x86_64-linux-gnu", "system": "Linux", "machine": "x86_64",
    "bits": 64, "torch_version": "2.8.0+cu128", "torch_cuda": "12.8",
    "transformers_version": "4.55.4", "numpy_version": "2.4.6", "pillow_version": "12.3.0",
}


def _normalized_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_distributions(lock: Path = SCORING_LOCK) -> dict[str, str]:
    """Read the exact distributions from the generated hash lock."""
    expected: dict[str, str] = {}
    for line in Path(lock).read_text(encoding="utf-8").splitlines():
        if not line or line[0].isspace() or line.startswith("#"):
            continue
        requirement = line.removesuffix(" \\")
        if "==" in requirement:
            name, version = requirement.split("==", 1)
        elif " @ " in requirement:
            name, url = requirement.split(" @ ", 1)
            filename = __import__("urllib.parse", fromlist=["unquote"]).unquote(url).rsplit("/", 1)[-1]
            match = re.search(r"-([0-9][A-Za-z0-9.+]*)-cp\d", filename)
            if match is None:
                raise ValueError(f"cannot derive locked version for {name}")
            version = match.group(1).replace("%2B", "+")
        else:
            raise ValueError(f"unsupported scoring lock requirement: {requirement}")
        normalized = _normalized_distribution(name.strip())
        if normalized in expected:
            raise ValueError(f"duplicate scoring lock distribution: {normalized}")
        expected[normalized] = version.strip()
    if not expected:
        raise ValueError("scoring lock has no distributions")
    return expected


def _isolated_env(clip_snapshot: Path | None = None) -> dict[str, str]:
    result = {key: value for key, value in os.environ.items() if key in _SCORER_ENV_KEYS}
    result.update(PYTHONNOUSERSITE="1", PYTHONSAFEPATH="1", PYTHONDONTWRITEBYTECODE="1",
                  HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    if clip_snapshot is not None:
        result["BLENDERBENCH_CLIP_SNAPSHOT"] = str(Path(clip_snapshot).absolute())
    return result


def verify_clip_snapshot(snapshot: Path) -> dict:
    """Verify the complete offline snapshot allowlist, sizes, and SHA-256 bytes."""
    root = Path(snapshot).expanduser().absolute()
    manifest_bytes = CLIP_SNAPSHOT_MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("revision") != protocol.CLIP_REVISION or manifest.get("schema_version") != 1:
        raise ValueError("CLIP snapshot manifest identity is invalid")
    expected = {row["path"]: row for row in manifest.get("files", [])}
    if len(expected) != 8 or any(Path(name).name != name for name in expected):
        raise ValueError("CLIP snapshot manifest allowlist is invalid")
    if not root.is_dir():
        raise ValueError("offline CLIP snapshot directory is missing")
    actual = {entry.name for entry in root.iterdir()}
    if actual != set(expected):
        raise ValueError(f"CLIP snapshot entries differ from exact allowlist: {sorted(actual ^ set(expected))}")
    cache_root = root.parent.parent if root.parent.name == "snapshots" else None
    verified = []
    for name in sorted(expected):
        path = root / name
        if not path.is_file():
            raise ValueError(f"CLIP snapshot file is missing: {name}")
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if cache_root is None or cache_root.resolve() not in resolved.parents:
                raise ValueError(f"CLIP snapshot symlink escapes declared Hugging Face cache: {name}")
        row = expected[name]
        size, sha256 = path.stat().st_size, dataset.sha256(path)
        if size != row["size"] or sha256 != row["sha256"]:
            raise ValueError(f"CLIP snapshot bytes differ from manifest: {name}")
        verified.append({"path": name, "size": size, "sha256": sha256})
    return {"repository": manifest["repository"], "revision": manifest["revision"],
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(), "files": verified}


def _runtime_probe() -> str:
    return (
        "import importlib.metadata as m,json,os,platform,struct,sys,sysconfig;"
        "import ctypes,numpy,PIL,torch,transformers;"
        "c=ctypes.CDLL('libcuda.so.1');v=ctypes.c_int();"
        "assert c.cuInit(0)==0 and c.cuDriverGetVersion(ctypes.byref(v))==0;"
        "d=[(x.metadata.get('Name'),x.version) for x in m.distributions()];"
        "f=sys.flags;print(json.dumps({'implementation':platform.python_implementation(),"
        "'python_version':platform.python_version(),'python_compiler':platform.python_compiler().strip(),"
        "'python_build':platform.python_build(),"
        "'cache_tag':sys.implementation.cache_tag,'soabi':sysconfig.get_config_var('SOABI'),"
        "'system':platform.system(),'machine':platform.machine(),'bits':struct.calcsize('P')*8,"
        "'lexical_executable':sys.executable,'resolved_executable':os.path.realpath(sys.executable),"
        "'prefix':sys.prefix,'base_prefix':sys.base_prefix,'isolated':f.isolated,"
        "'ignore_environment':f.ignore_environment,'no_user_site':f.no_user_site,"
        "'safe_path':f.safe_path,'distributions':d,'torch_version':torch.__version__,"
        "'torch_cuda':torch.version.cuda,'cuda_available':torch.cuda.is_available(),"
        "'gpu':torch.cuda.get_device_name(0),'cuda_driver_api_version':v.value,"
        "'transformers_version':transformers.__version__,'numpy_version':numpy.__version__,"
        "'pillow_version':PIL.__version__},sort_keys=True))"
    )


def preflight_scorer_runtime(lock: Path = SCORING_LOCK) -> dict:
    """Local reproducibility preflight for the lexical runner venv; not host attestation."""
    lexical = Path(__import__("sys").executable).expanduser().absolute()
    process = subprocess.run([str(lexical), "-I", "-c", _runtime_probe()], capture_output=True,
                             text=True, cwd=CANONICAL_SCORER.parent, env=_isolated_env())
    if process.returncode:
        raise ValueError(f"scorer runtime preflight failed: {process.stderr.strip()}")
    try:
        identity = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("scorer runtime preflight was not valid JSON") from exc
    if not isinstance(identity, dict) or any(identity.get(key) != value
            for key, value in _EXPECTED_RUNTIME.items()):
        raise ValueError("runner is not the exact historical CPython 3.13.15 scoring runtime")
    prefix = Path(identity.get("prefix", "")).absolute()
    if (identity.get("base_prefix") == identity.get("prefix")
            or lexical.parent != prefix / "bin"
            or Path(identity.get("lexical_executable", "")).absolute() != lexical
            or any(identity.get(key) != expected for key, expected in
                   {"isolated": 1, "ignore_environment": 1, "no_user_site": 1,
                    "safe_path": True, "cuda_available": True}.items())):
        raise ValueError("scorer must run from the lexical isolated CUDA-enabled publication venv")
    installed: dict[str, str] = {}
    for entry in identity.get("distributions", []):
        if not isinstance(entry, list) or len(entry) != 2 or not all(isinstance(x, str) for x in entry):
            raise ValueError("scorer distribution preflight is malformed")
        name = _normalized_distribution(entry[0])
        if name in installed:
            raise ValueError(f"duplicate installed scoring distribution: {name}")
        installed[name] = entry[1]
    expected = locked_distributions(lock)
    mismatches = {name: {"expected": version, "installed": installed.get(name)}
                  for name, version in expected.items() if installed.get(name) != version}
    extras = sorted(set(installed) - set(expected) - _ALLOWED_BOOTSTRAP_DISTRIBUTIONS)
    if mismatches or extras:
        raise ValueError(f"scorer distributions do not exactly match scoring lock: "
                         f"mismatches={mismatches}, extras={extras}")
    return {"trust_boundary": "local reproducibility preflight; not remote or hostile-host attestation",
            "remote_attestation": False, **{key: identity[key] for key in identity if key != "distributions"},
            "executable_sha256": dataset.sha256(lexical),
            "scoring_lock_sha256": dataset.sha256(Path(lock)), "locked_distributions": expected,
            "excluded_bootstrap_distributions": {name: installed[name] for name in sorted(installed)
                                                  if name in _ALLOWED_BOOTSTRAP_DISTRIBUTIONS},
            "invocation": {"argv_prefix": [str(lexical), "-I"], "isolated": True}}


def scorer_sha256() -> str:
    if CANONICAL_SCORER.is_symlink() or not CANONICAL_SCORER.is_file():
        raise ValueError("canonical score_pair.py must be a regular repository file")
    return dataset.sha256(CANONICAL_SCORER)


def scoring_provenance(runtime_preflight: dict, clip_snapshot: dict) -> dict:
    return {"dataset_revision": dataset.DATASET_REVISION,
        "evaluator_repository": protocol.EVALUATOR_REPOSITORY,
        "evaluator_revision": protocol.EVALUATOR_REVISION,
        "evaluator_sha256": protocol.EVALUATOR_SHA256,
        "clip_model": protocol.CLIP_MODEL, "clip_revision": protocol.CLIP_REVISION,
        "clip_snapshot": clip_snapshot, "scorer_sha256": scorer_sha256(),
        "scorer_runtime": runtime_preflight,
        "identity_validation": "outputs checked after local runtime and byte-manifest preflight"}


def verified_audit_images(task_root: Path, rounds: int, bpy_version: str) -> dict[int, Path]:
    """Admit only renders recorded after successful independent scene validation."""
    task_root = Path(task_root).absolute()
    admission_path = task_root / "audit-admission.json"
    if admission_path.is_symlink() or not admission_path.is_file():
        raise ValueError("standalone scoring requires audit-admission.json")
    try:
        rows = json.loads(admission_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("audit admission is unreadable") from exc
    if (not isinstance(rows, list) or [row.get("round") for row in rows]
            != list(range(1, rounds + 1))):
        raise ValueError("audit admission rounds are incomplete")
    images = {}
    for number, row in enumerate(rows, 1):
        image = task_root / f"audit{number:02}" / "verified.png"
        metadata_path = image.parent / "metadata.json"
        blend = task_root / "work" / f"iteration{number:02}.blend"
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise ValueError(f"audit metadata is missing for round {number}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"audit metadata is unreadable for round {number}") from exc
        runtime = metadata.get("runtime", {})
        evidence = metadata.get("evidence", {})
        devices = evidence.get("devices", []) if isinstance(evidence, dict) else []
        if (image.is_symlink() or blend.is_symlink() or not image.is_file() or not blend.is_file()
                or row.get("meaningful_change_validated") is not True
                or row.get("image") != str(image) or row.get("source") != str(blend)
                or row.get("image_sha256") != dataset.sha256(image)
                or row.get("source_sha256") != dataset.sha256(blend)
                or metadata.get("png") != str(image) or metadata.get("source") != str(blend)
                or metadata.get("png_sha256") != row.get("image_sha256")
                or metadata.get("source_sha256") != row.get("source_sha256")
                or not isinstance(metadata.get("audit"), dict)
                or row.get("audit_sha256") != protocol.digest(metadata["audit"])
                or not isinstance(runtime, dict) or runtime.get("returncode") != 0
                or not isinstance(evidence, dict) or evidence.get("blender_version") != bpy_version
                or evidence.get("compute_device_type") != "CUDA"
                or evidence.get("scene_device") != "GPU"
                or evidence.get("engine") != "CYCLES"
                or not any(isinstance(device, dict) and device.get("type") == "CUDA"
                           and device.get("use") is True for device in devices)
                or any(isinstance(device, dict) and device.get("use") is True
                       and device.get("type") != "CUDA" for device in devices)):
            raise ValueError(f"audit admission bytes are invalid for round {number}")
        images[number] = image
    return images


def _normalized(row: dict) -> dict:
    result = dict(row)
    if result.get("status") == "complete":
        cosine = float(result["clip_image_cosine_similarity"])
        loss = float(result["photometric_loss"])
        if not math.isfinite(cosine) or not math.isfinite(loss):
            raise ValueError("score metrics must be finite")
        result["clip_image_cosine_similarity"] = cosine
        result["nclip"] = 1.0 - cosine
        result["photometric_loss"] = loss
    return result


def aggregate(rows: Iterable[dict], expected_tasks: Iterable[str], expected_rounds: int | None = None,
              provenance: dict | None = None) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for raw in rows:
        row = _normalized(raw)
        grouped[row["task"]].append(row)
    tasks = []
    for task in expected_tasks:
        task_rows = sorted(grouped.get(task, []), key=lambda row: row.get("round", 0))
        successful = [row for row in task_rows if row.get("status") == "complete"]
        failed = [row for row in task_rows if row.get("status") != "complete"]
        if not task_rows:
            status = "missing"
        elif expected_rounds is not None and len(successful) != expected_rounds:
            status = "failed"
            failed.append({"task": task, "status": "failed",
                           "error": f"expected {expected_rounds} completed rounds; found {len(successful)}"})
        elif failed:
            status = "failed"
        else:
            status = "complete"
        # Explicit round secondary key makes equal N-CLIP choose the earliest round.
        best = min(successful, key=lambda row: (row["nclip"], row["round"])) if successful else None
        tasks.append({"task": task, "status": status, "completed_rounds": len(successful),
                      "failed_rounds": len(failed), "best_round": best, "rounds": task_rows,
                      "failures": failed})
    completed = sum(task["status"] == "complete" for task in tasks)
    completed_best = [task["best_round"] for task in tasks
                      if task["status"] == "complete" and task["best_round"] is not None]
    mean_nclip = (sum(row["nclip"] for row in completed_best) / len(completed_best)
                  if completed_best else None)
    mean_pl = (sum(row["photometric_loss"] for row in completed_best) / len(completed_best)
               if completed_best else None)
    result = {
        "schema_version": 1,
        "metric_definition": {
            "clip_image_cosine_similarity": "cosine similarity between CLIP image embeddings",
            "nclip": "1 - CLIP image cosine similarity",
            "photometric_loss": "pinned VIGA evaluator photometric_loss output",
            "best_round": "minimum N-CLIP; earliest round wins exact ties",
        },
        "tasks": tasks,
        "summary": {"expected_tasks": len(tasks), "completed_tasks": completed,
                    "failed_tasks": len(tasks) - completed,
                    "completion_rate": completed / len(tasks) if tasks else 0.0,
                    "mean_best_nclip_completed_tasks": mean_nclip,
                    "mean_best_photometric_loss_completed_tasks": mean_pl},
    }
    if provenance is not None:
        result["provenance"] = provenance
    return result


def write_outputs(result: dict, output: Path) -> tuple[Path, Path]:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "aggregate.json"
    csv_path = output / "aggregate.csv"
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(json_path)
    fields = ["task", "status", "completed_rounds", "failed_rounds", "best_round",
              "best_clip_image_cosine_similarity", "best_nclip", "best_photometric_loss", "error",
              "dataset_revision", "evaluator_repository", "evaluator_revision",
              "evaluator_sha256", "clip_model", "clip_revision", "scorer_sha256",
              "identity_validation", "protocol_sha256", "git_revision",
              "scoring_lock_sha256", "model", "model_revision", "codex_version",
              "bpy_version", "scorer_python_version", "scorer_python_implementation",
              "scorer_python_sha256"]
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        provenance = result.get("provenance", {})
        source = provenance.get("source_identity", {})
        versions = provenance.get("versions", {})
        scorer_runtime = provenance.get("scorer_runtime", {})
        flat_provenance = {key: provenance.get(key, "") for key in fields[9:]}
        flat_provenance.update({
            "git_revision": source.get("git_revision", ""),
            "scoring_lock_sha256": source.get("scoring_lock_sha256", ""),
            "codex_version": versions.get("codex", ""),
            "bpy_version": versions.get("bpy", ""),
            "scorer_python_version": scorer_runtime.get("python_version", ""),
            "scorer_python_implementation": scorer_runtime.get("implementation", ""),
            "scorer_python_sha256": scorer_runtime.get("executable_sha256", ""),
        })
        for task in result["tasks"]:
            best = task["best_round"] or {}
            errors = "; ".join(str(row.get("error", "unknown failure")) for row in task["failures"])
            writer.writerow({"task": task["task"], "status": task["status"],
                "completed_rounds": task["completed_rounds"], "failed_rounds": task["failed_rounds"],
                "best_round": best.get("round", ""),
                "best_clip_image_cosine_similarity": best.get("clip_image_cosine_similarity", ""),
                "best_nclip": best.get("nclip", ""),
                "best_photometric_loss": best.get("photometric_loss", ""), "error": errors,
                **flat_provenance})
    return json_path, csv_path


def score_pair(image: Path, target: Path, clip_snapshot: Path,
               runtime_preflight: dict | None = None) -> dict:
    """Score exact image bytes with the canonical scorer under this lexical venv."""
    runtime_preflight = runtime_preflight or preflight_scorer_runtime()
    snapshot_before = verify_clip_snapshot(clip_snapshot)
    image, target = Path(image).absolute(), Path(target).absolute()
    image_sha256, target_sha256 = dataset.sha256(image), dataset.sha256(target)
    lexical = str(Path(__import__("sys").executable).expanduser().absolute())
    argv = [lexical, "-I", str(CANONICAL_SCORER), str(image), str(target)]
    process = subprocess.run(argv, capture_output=True, text=True, cwd=CANONICAL_SCORER.parent,
                             env=_isolated_env(clip_snapshot))
    snapshot_after = verify_clip_snapshot(clip_snapshot)
    if snapshot_after != snapshot_before:
        raise ValueError("CLIP snapshot changed during scoring")
    if process.returncode:
        raise RuntimeError(f"scorer exited {process.returncode}: {process.stderr.strip()}")
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("scorer must emit one JSON object") from exc
    identity = {"clip_model": protocol.CLIP_MODEL, "clip_revision": protocol.CLIP_REVISION,
                "viga_revision": protocol.EVALUATOR_REVISION,
                "evaluator_sha256": protocol.EVALUATOR_SHA256,
                "torch_version": _EXPECTED_RUNTIME["torch_version"],
                "transformers_version": _EXPECTED_RUNTIME["transformers_version"],
                "numpy_version": _EXPECTED_RUNTIME["numpy_version"],
                "pillow_version": _EXPECTED_RUNTIME["pillow_version"]}
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in identity.items()):
        raise ValueError("scorer evaluator/model/runtime identity does not match pinned protocol")
    if not str(payload.get("device", "")).startswith("cuda"):
        raise ValueError("scorer identity must report a CUDA device")
    try:
        cosine = payload.get("clip_image_cosine_similarity", payload.get("clip_similarity"))
        loss = payload.get("photometric_loss", payload.get("raw_pl"))
        return {"clip_image_cosine_similarity": float(cosine), "photometric_loss": float(loss),
                "candidate_image_sha256": image_sha256, "target_image_sha256": target_sha256,
                "scorer_payload": payload}
    except (ValueError, TypeError) as exc:
        raise ValueError("scorer must emit JSON with CLIP image cosine similarity and photometric loss") from exc


def score_saved_run(run_root: Path, dataset_root: Path, tasks: Iterable[str], rounds: int,
                    clip_snapshot: Path) -> dict:
    tasks = list(tasks)
    manifest = protocol.load_run_manifest(Path(run_root), tasks=tasks, rounds=rounds)
    runtime_preflight = preflight_scorer_runtime()
    snapshot = verify_clip_snapshot(clip_snapshot)
    for task in tasks:
        dataset.verify(task, Path(dataset_root))
    rows = []
    for task in tasks:
        task_root = Path(run_root) / task.replace("/", "-")
        target = Path(dataset_root) / task / "target.png"
        try:
            admitted_images = verified_audit_images(
                task_root, rounds, manifest["protocol"]["versions"]["bpy"])
        except Exception as exc:
            rows.extend({"task": task, "round": number, "status": "failed",
                         "error": f"{type(exc).__name__}: {exc}"}
                        for number in range(1, rounds + 1))
            continue
        for number in range(1, rounds + 1):
            # Only score the independently reopened/GPU-rendered audit image. The
            # model-written checkpoint PNG is generation evidence, not an admitted
            # standalone publication score input.
            image = admitted_images[number]
            row = {"task": task, "round": number}
            try:
                if not image.is_file():
                    raise FileNotFoundError(image)
                row.update(score_pair(image, target, clip_snapshot, runtime_preflight),
                           status="complete", image=str(image))
            except Exception as exc:
                row.update(status="failed", error=f"{type(exc).__name__}: {exc}", image=str(image))
            rows.append(row)
    generation = manifest["protocol"]
    provenance = scoring_provenance(runtime_preflight, snapshot)
    provenance.update({"protocol_sha256": manifest["protocol_sha256"],
        "source_identity": generation["source_identity"], "model": generation["model"],
        "model_revision": generation["model_revision"], "versions": generation["versions"]})
    return aggregate(rows, tasks, expected_rounds=rounds, provenance=provenance)
