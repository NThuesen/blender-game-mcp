"""Offline checkpoint CLI. No live provider calls or Blender scene execution.

Answer tasks generate explicit synthetic oracle answers; artifact tasks ingest
already generated oracle outputs and run the unchanged public validators.
"""
import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

from . import validators
from .models import CONDITIONS, FixtureTransport, ModelAdapter, condition_config
from .report import write_report
from .telemetry import Journal, Telemetry, digest, encoded, redact, sanitized_config, sanitized_record

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
POLICY = "Preserve the source scene. Write a new output at the requested path."


def tasks():
    return json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))


def provenance():
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()
    # Dirty harness changes cannot reuse a journal produced by older harness code.
    files = [*HERE.glob("*.py"), HERE / "tasks.json", HERE / "fixtures/inspect_artifact.py",
             REPO / "chat_client/chat_client.py"]
    return {"repo_revision": revision,
            "harness_source_hash": digest({str(path.relative_to(REPO)): digest(path.read_bytes())
                                           for path in sorted(files)})}


def validate_trial(task, fixture_root, attempt, runtime=None):
    output = Path(attempt) / task["outputs"]["path"]
    return getattr(validators, task["validator"])(
        fixture_root=Path(fixture_root), output_root=Path(attempt), answer_path=output,
        output_path=output, runtime_executable=runtime)


def offline_trial(task, condition, trial, root, fixture_root, *, runtime=None, oracle_root=None):
    """One synthetic completion, real artifact/answer validator, durable resume.

    There is no agent loop here: reuse chat_client parsing through ModelAdapter.
    No scene is opened by a model. Runtime validators reopen artifact scenes in
    fresh processes. Source bytes are hashed both before and after validation.
    """
    root, fixture_root = Path(root).resolve(), Path(fixture_root).resolve()
    source = fixture_root / Path(task["fixture"]).name
    source_hash = digest(source.read_bytes())
    metadata = provenance()
    config = {**metadata, "condition": condition_config(condition, task, metadata["repo_revision"]),
              "task_contract": task, "canonical_prompt": task["prompt"], "policy": POLICY,
              "provider": "openai", "requested_model": "synthetic-oracle-fixture-v1",
              "synthetic": True, "cache_mode": "not_applicable_synthetic",
              "warmth": "not_applicable_synthetic", "fresh_conversation": True,
              "scene_initialization": "no model scene; validator fresh subprocess for artifacts",
              "tool_schemas": [], "tool_policy": "offline fixture completion; zero tools exposed",
              "runtime": {"backend": "synthetic_oracle", "python": platform.python_version(),
                          "blender_version": None, "bpy_version": None,
                          "validator_executable": str(runtime) if runtime else None},
              "fixture_manifest_hash": digest((fixture_root / "manifest.json").read_bytes())}
    adapter = ModelAdapter("openai", config["requested_model"], None)
    config["controls"] = adapter.controls
    config["oracle_hash"] = (digest((Path(oracle_root) / task["id"] / task["outputs"]["path"]).read_bytes())
                             if oracle_root else None)
    config = sanitized_config(config)
    identity = {"task": task["id"], "condition": condition, "trial": trial,
                "config_hash": digest(config), "source_hash": source_hash}
    journal = Journal(root / "trials.jsonl")
    if journal.resumable(identity, lambda record: validate_trial(
            task, fixture_root, record["attempt_root"], runtime)):
        return {"identity": identity, "resumed": True, "synthetic": True}
    attempt_parent = root / "attempts" / digest(identity)
    attempt_parent.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=attempt_parent))
    output = attempt / task["outputs"]["path"]
    if fixture_root == attempt or fixture_root in attempt.parents:
        raise ValueError("trial root must be outside source-fixture directory")
    prompt = task["prompt"].replace("{{fixture}}", str(source)).replace("{{output}}", str(output))
    transcript = [{"role": "system", "content": POLICY}, {"role": "user", "content": prompt}]
    telemetry = Telemetry("openai", config["requested_model"])
    # These are explicitly fixtures, never plausible-looking paid model results.
    content = (json.dumps(task["expected_answer"]) if task["outputs"]["kind"] == "answer_json"
               else "SYNTHETIC: copy pre-generated oracle artifact; no model execution")
    transport = FixtureTransport([{"model": config["requested_model"], "choices": [
        {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}])
    adapter.transport = transport
    response, parsed, schemas = adapter.call(transcript[1:], [], [], policy=POLICY)
    telemetry.api_response(response)
    transcript.append(parsed[0])
    if task["outputs"]["kind"] == "answer_json":
        output.write_text(parsed[2], encoding="utf-8")
    else:
        if not oracle_root or not runtime:
            raise ValueError("artifact offline tasks require --oracle-root and --runtime")
        shutil.copyfile(Path(oracle_root) / task["id"] / task["outputs"]["path"], output)
    measured = telemetry.finish(transcript, schemas)
    result = validate_trial(task, fixture_root, attempt, runtime)
    if digest(source.read_bytes()) != source_hash:
        result = {"ok": False, "errors": [{"code": "source_changed_during_trial"}],
                  "details": {"validator_result": result}}
    if not result["ok"]:
        measured["failure_categories"].append("validation")
    transcript_path = attempt / "transcript.json"
    transcript_path.write_bytes(encoded(redact(transcript)))
    record = {"identity": identity, "status": "complete", "synthetic": True,
              "attempt_root": str(attempt), "config": config, "resolved_prompt": prompt,
              "validator": result, "telemetry": measured,
              "artifacts": {str(path): digest(path.read_bytes()) for path in (output, transcript_path)}}
    record = sanitized_record(record)
    journal.append(record)
    # Read the exact persisted target, not merely a successful write status.
    if digest(journal.read()[0][-1]) != digest(record):
        raise RuntimeError("journal readback mismatch")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("list", "tasks", "dry-run", "offline", "validate", "report"))
    parser.add_argument("--task", action="append", help="repeatable canonical task ID")
    parser.add_argument("--condition", choices=CONDITIONS, default="C")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--fixture-root", type=Path, default=HERE / "fixtures")
    parser.add_argument("--oracle-root", type=Path)
    parser.add_argument("--runtime", help="validator executable, not a model/backend configuration")
    args = parser.parse_args(argv)
    selected = [task for task in tasks() if not args.task or task["id"] in args.task]
    if args.task and set(args.task) - {task["id"] for task in selected}:
        parser.error("unknown task ID")
    if args.trials < 1:
        parser.error("--trials must be positive")
    if args.command in ("list", "tasks", "dry-run"):
        print(json.dumps({"tasks": selected, "conditions": CONDITIONS, "live_enabled": False,
                          "note": "offline fixtures only; no A/B claims"}, indent=2))
        return 0
    if args.output_root is None:
        parser.error("--output-root required")
    journal = Journal(args.output_root / "trials.jsonl")
    if args.command == "report":
        print(json.dumps(write_report(journal.path, args.output_root), indent=2))
        return 0
    if args.command == "validate":
        records, errors = journal.read()
        latest = {digest(record["identity"]): record for record in records}
        checks = []
        lookup = {task["id"]: task for task in tasks()}
        for record in latest.values():
            task = lookup[record["identity"]["task"]]
            source = args.fixture_root / Path(task["fixture"]).name
            ok = digest(source.read_bytes()) == record["identity"]["source_hash"] and journal.resumable(
                record["identity"], lambda row: validate_trial(
                    task, args.fixture_root, row["attempt_root"], args.runtime))
            checks.append({"identity": record["identity"], "valid": ok})
        print(json.dumps({"checks": checks, "journal_errors": errors}, indent=2))
        return int(bool(errors) or not checks or not all(check["valid"] for check in checks))
    if not args.task:
        selected = [task for task in selected if task["outputs"]["kind"] == "answer_json"]
    if any(task["outputs"]["kind"] != "answer_json" for task in selected) and (
            not args.runtime or not args.oracle_root):
        parser.error("artifact tasks require --runtime and --oracle-root")
    for task in selected:
        for trial in range(1, args.trials + 1):
            result = offline_trial(task, args.condition, trial, args.output_root, args.fixture_root,
                                   runtime=args.runtime, oracle_root=args.oracle_root)
            print(json.dumps({"task": task["id"], "trial": trial, "synthetic": True,
                              "resumed": result.get("resumed", False),
                              "ok": result.get("validator", {}).get("ok")}), flush=True)
    summary = write_report(journal.path, args.output_root)
    return int(any(row["failed"] for row in summary["rows"]))


if __name__ == "__main__":
    raise SystemExit(main())
