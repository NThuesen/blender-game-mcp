"""Bounded Codex exec transport and offline launch-spec checkpoint.

No model loop: a future approved caller launches ONE Codex process per trial.
This checkpoint exposes dry-run and synthetic replay only, never a live switch.
"""
import argparse
from collections import Counter
import io
import json
import math
import os
from pathlib import Path
import pty
import selectors
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

from . import validators
from .models import ORIGINAL_REVISION
from .run import HERE, REPO, tasks, validate_trial
from .telemetry import Journal, TOKEN_FIELDS, digest, encoded, redact, sanitized_record

POLICY = (
    "Use only the allowlisted blender MCP tools for scene execution and documentation. "
    "Do not use shell, file editing, web, other integrations or delegated agents. "
    "Do not inspect benchmark source, validators, manifests or oracle answers. "
    "Preserve the input scene; save only the requested new output. "
    "For answer JSON tasks return the requested JSON as your final message."
)
BASE_TOOLS = ["search_api_docs", "get_python_api_docs", "execute_blender_code_for_cli"]
CONDITIONS = {
    "official_original": {"revision": ORIGINAL_REVISION, "backend": "blender_cli",
                          "docs": "static", "tools": BASE_TOOLS},
    "enhanced_bpy": {"revision": None, "backend": "bpy_cli", "docs": "static+runtime",
                     "tools": BASE_TOOLS + ["get_runtime_python_api_docs_for_cli"]},
}
BLOCKERS = [
    "Independent runner review and explicit live-trial authorization pending",
    "Actual MCP initialize instructions, tools/list schemas and Codex exposure not verified",
    "Original pinned server dependency/runtime compatibility not verified",
    "OS read isolation for Codex AND MCP execution against oracle/validator paths not established",
    "PTY transport tested with synthetic child only; Codex model invocation untested",
    "Internal model response count/20-turn contract is not exposed by exec JSONL",
]


def count(value):
    return value if type(value) is int and value >= 0 else None


def usage_counts(usage):
    """exec turn.completed fields, not Chat Completions usage field names."""
    usage = usage if isinstance(usage, dict) else {}
    raw = {key: value for key in ("input_tokens", "cached_input_tokens", "output_tokens",
                                  "reasoning_output_tokens")
           if (value := count(usage.get(key))) is not None}
    total, cached = raw.get("input_tokens"), raw.get("cached_input_tokens")
    result = dict.fromkeys(TOKEN_FIELDS)
    result.update(cached_input=cached, output=raw.get("output_tokens"),
                  reasoning=raw.get("reasoning_output_tokens"))
    if total is not None and cached is not None and cached <= total:
        result["uncached_input"] = total - cached
    return raw, result


class ExecEvents:
    """Incremental JSONL observer; never dispatches tools or submits a model turn.

    Limits react to observed events, not pre-dispatch authorization. A started
    event may arrive after execution began. Unknown execution items fail closed.
    """
    def __init__(self, tools=(), max_tools=20, max_turns=1):
        if type(max_tools) is not int or max_tools < 1 or type(max_turns) is not int or max_turns < 1:
            raise ValueError("positive integer event budgets required")
        self.tools = set(tools)
        self.max_tools, self.max_turns = max_tools, max_turns
        self.pending = b""
        self.events, self.errors, self.usages = [], [], []
        self.items, self.completed_items = {}, set()
        self.tool_counts = Counter()
        self.tool_errors = self.turns = self.completed_turns = self.threads = 0
        self.stop = None
        self.final = None

    def fail(self, reason):
        self.errors.append(reason)
        self.stop = self.stop or reason

    def feed(self, chunk):
        self.pending += chunk
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise ValueError("event object required")
                self.observe(event)
            except (ValueError, UnicodeError, RecursionError, TypeError):
                self.fail("malformed_event")

    def observe(self, event):
        self.events.append(redact(event))
        kind = event["type"]
        if kind == "thread.started":
            self.threads += 1
            if self.threads != 1 or not isinstance(event.get("thread_id"), str):
                self.fail("invalid_thread")
        elif kind == "turn.started":
            self.turns += 1
            if self.turns > self.max_turns:
                self.fail("turn_event_budget")
        elif kind == "turn.completed":
            self.completed_turns += 1
            if self.completed_turns > self.turns:
                self.fail("unexpected_turn_completion")
            raw, tokens = usage_counts(event.get("usage"))
            self.usages.append((raw, tokens))
            if (raw.get("cached_input_tokens", 0) > raw.get("input_tokens", math.inf)
                    or raw.get("reasoning_output_tokens", 0) > raw.get("output_tokens", math.inf)):
                self.fail("inconsistent_usage")
        elif kind in ("error", "turn.failed"):
            self.fail(kind)
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                self.fail("malformed_item")
                return
            ident, item_type = item["id"], item.get("type")
            signature = (item_type, item.get("server"), item.get("tool"))
            if ident in self.items and self.items[ident] != signature:
                self.fail("changed_item_identity")
            if item_type == "mcp_tool_call":
                if item.get("server") != "blender" or item.get("tool") not in self.tools:
                    self.fail("tool_allowlist_bypass")
                if ident not in self.items:
                    self.tool_counts[str(item.get("tool"))] += 1
                    if sum(self.tool_counts.values()) > self.max_tools:
                        self.fail("tool_event_budget")
            elif item_type not in ("agent_message", "reasoning", "plan"):
                self.fail("builtin_or_unknown_tool_bypass")
            self.items[ident] = signature
            if kind == "item.completed":
                if ident in self.completed_items:
                    self.fail("duplicate_item_completion")
                    return
                self.completed_items.add(ident)
                if item_type == "mcp_tool_call":
                    result = item.get("result")
                    self.tool_errors += int(bool(item.get("error")) or item.get("status") == "failed"
                                            or (isinstance(result, dict) and result.get("isError") is True))
                elif item_type == "agent_message":
                    self.final = item.get("text")
        else:
            self.fail("unknown_event")

    def finish(self):
        if self.pending:
            self.fail("truncated_event")
        if self.threads != 1 or not self.turns or self.completed_turns != self.turns:
            self.fail("incomplete_stream")
        if any(sig[0] == "mcp_tool_call" and ident not in self.completed_items
               for ident, sig in self.items.items()):
            self.fail("unfinished_tool")
        # A failed or incomplete turn may have unreported usage. Never present
        # its preceding completed usage as a complete trial total.
        tokens = {key: sum(pair[1][key] for pair in self.usages)
                  if not self.errors and self.usages
                  and all(pair[1][key] is not None for pair in self.usages) else None
                  for key in TOKEN_FIELDS}
        return {"provider": "codex_exec", "tokens": tokens,
                "raw_usage_redacted": [pair[0] for pair in self.usages],
                "usage_stream_complete": not self.errors and bool(self.usages),
                "turns": self.turns, "turn_definition": "exec user turns; NOT internal model responses",
                "api_attempts": None, "returned_models": [], "cost": None,
                "tool_counts": dict(self.tool_counts), "tool_errors": self.tool_errors,
                "tool_seconds": None, "image_count": None, "image_bytes": None,
                "errors": self.errors, "comparison_eligible": False}


def capture(argv, cwd, observer, *, seconds=120, output_bytes=2 * 1024 * 1024, evidence=None):
    """Bounded pipes plus a private controlling PTY; no inherited terminal needed.

    Reuses validators' non-reaping observer and cleanup primitives unchanged.
    PTY side-channel output is also captured/capped, never merged into JSONL.
    """
    if not math.isfinite(seconds) or seconds <= 0 or type(output_bytes) is not int or output_bytes < 1:
        raise ValueError("positive finite capture bounds required")
    started = time.monotonic()
    if evidence is not None:
        evidence.start(started)
    master, slave = pty.openpty()
    process = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray(), "tty": bytearray()}
    primary = None
    reason = None
    capture_complete = False
    try:
        process = subprocess.Popen(
            [sys.executable, str(HERE / "codex_pty.py"), *argv], cwd=cwd,
            stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, bufsize=0)
        os.close(slave)
        slave = None
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr"), (master, "tty")):
            fd = stream if isinstance(stream, int) else stream.fileno()
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ, name)
        total, exit_seen = 0, None
        while True:
            now = time.monotonic()
            exited = validators._child_exited(process)
            if exited:
                exit_seen = exit_seen or now
                if not selector.get_map():
                    capture_complete = True
                    break
                if now - exit_seen >= 0.05:
                    reason = "inherited_output_handles"
                    break
            if now - started >= seconds:
                reason = "deadline"
                break
            if observer.stop:
                reason = observer.stop
                break
            if evidence is not None:
                evidence.poll()
            for key, _ in selector.select(min(0.02, seconds - (now - started))):
                try:
                    chunk = os.read(key.fd, min(65536, output_bytes - total))
                except BlockingIOError:
                    continue
                except OSError as exc:
                    if key.data == "tty" and exc.errno == 5:  # POSIX PTY EOF
                        chunk = b""
                    else:
                        raise
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                buffers[key.data].extend(chunk)
                total += len(chunk)
                if evidence is not None:
                    evidence.raw(key.data, chunk)
                if key.data == "stdout":
                    observer.feed(chunk)
                if total >= output_bytes:
                    reason = "output_cap"
                    break
            if reason:
                break
    except BaseException as exc:
        primary = exc
        raise
    finally:
        errors = []
        # Like the public validator, normal exit + complete EOF needs no signal.
        # Required signals reserve the group ID by keeping the leader unreaped.
        if process is not None and not capture_complete:
            validators._cleanup_attempt(errors, "killpg", lambda: validators._terminate_group(process))
        validators._cleanup_attempt(errors, "selector.close", selector.close)
        for fd in (master, slave):
            if fd is not None:
                validators._cleanup_attempt(errors, "pty.close", lambda fd=fd: os.close(fd))
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    validators._cleanup_attempt(errors, "pipe.close", stream.close)
            validators._cleanup_attempt(errors, "reap", lambda: validators._bounded_reap(process))
        validators._report_cleanup(primary, errors)
    if reason:
        observer.fail(reason)
    return {"returncode": process.returncode, "stop_reason": reason,
            "wall_seconds": time.monotonic() - started,
            **{key: bytes(value) for key, value in buffers.items()}}


def source_snapshot(condition):
    """Read explicit server source paths only; never scan auth/config/.env files."""
    revision = CONDITIONS[condition]["revision"]
    if revision:
        archive = subprocess.run(["git", "archive", revision, "mcp"], cwd=REPO,
                                 capture_output=True, check=True, timeout=30).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
            files = {member.name: handle.extractfile(member).read() for member in handle
                     if member.isfile() and source_path(member.name)}
    else:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                                  capture_output=True, check=True, timeout=10).stdout.decode().strip()
        files = {str(path.relative_to(REPO)): path.read_bytes()
                 for path in (REPO / "mcp").rglob("*")
                 if source_path(str(path.relative_to(REPO))) and path.is_file() and not path.is_symlink()}
    return {"revision": revision, "mode": "git_objects" if condition == "official_original" else "working_tree",
            "files": {path: digest(data) for path, data in sorted(files.items())},
            "scope": "mcp Python/YAML/RST/TOML plus uv.lock; excludes environments/caches"}, files


class CodexSubprocessAdapter:
    """One new exec process; admission remains blocked for this checkpoint."""
    def run(self, spec):
        if spec.get("launch_blockers") or not spec.get("runner_review_approved"):
            raise ValueError("live launch blocked: independent review and prerequisites required")
        observer = ExecEvents(spec["tool_allowlist"], spec["limits"]["tool_events"],
                              spec["limits"]["exec_turn_events"])
        argv = spec["argv"]
        result = capture(argv, argv[argv.index("--cd") + 1], observer,
                         seconds=spec["limits"]["seconds"], output_bytes=spec["limits"]["output_bytes"])
        if result["returncode"] != 0:
            observer.fail("process_exit")
        return result, observer.finish()


def source_path(path):
    parts = Path(path).parts
    return (not any(part.startswith(".") or part == "__pycache__" for part in parts)
            and (Path(path).suffix in (".py", ".yml", ".yaml", ".rst", ".toml")
                 or Path(path).name == "uv.lock"))


def launch_spec(task, condition, workspace, server_root, server_python, runtime, model):
    """Construct documented argv only. Does not assert MCP compatibility."""
    selected = CONDITIONS[condition]
    if selected["backend"] not in task["execution_variants"]:
        raise ValueError("task does not support saved-file condition")
    workspace, server_root = Path(workspace).resolve(), Path(server_root).resolve()
    output = workspace / "output" / task["outputs"]["path"]
    prompt = task["prompt"].replace("{{fixture}}", str(workspace / "input" / Path(task["fixture"]).name))
    prompt = prompt.replace("{{output}}", str(output))
    env = {"PYTHONPATH": str(server_root / "mcp")}
    if condition == "official_original":
        env["BLENDER_PATH"] = str(runtime)
    else:
        env.update(BLENDER_MCP_CLI_BACKEND="bpy", BLENDER_MCP_BPY_PYTHON=str(runtime))
    overrides = {"mcp_servers.blender.command": str(server_python),
                 "mcp_servers.blender.args": ["-c", "import sys; from blmcp import main; sys.exit(main())"],
                 "mcp_servers.blender.cwd": str(server_root / "mcp"),
                 "mcp_servers.blender.required": True,
                 "mcp_servers.blender.enabled_tools": selected["tools"],
                 "features.shell_tool": False, "web_search": "disabled",
                 "developer_instructions": POLICY}
    overrides.update({f"mcp_servers.blender.env.{key}": value for key, value in env.items()})
    argv = ["codex", "--ask-for-approval", "on-request", "exec", "--ignore-user-config",
            "--strict-config", "--ephemeral", "--json", "--color", "never",
            "--sandbox", "workspace-write", "--skip-git-repo-check", "--cd", str(workspace),
            "--model", model]
    for key, value in overrides.items():
        argv += ["-c", key + "=" + json.dumps(value, ensure_ascii=False)]
    argv.append(prompt)
    return {"argv": argv, "canonical_prompt": task["prompt"], "resolved_prompt": prompt,
            "policy": POLICY, "condition": condition, "configuration": selected,
            "comparison": "whole saved-file configurations, NOT docs-only",
            "cli_version_expected": "codex-cli 0.153.4", "fresh_conversation": True,
            "tool_allowlist": selected["tools"], "actual_tool_schemas": None,
            "actual_server_instructions": None, "effective_codex_tools": None,
            "generation_controls": {"model": model, "reasoning_effort": None, "temperature": None},
            "limits": {"seconds": 120, "output_bytes": 2097152, "tool_events": 20,
                       "exec_turn_events": 1, "task_model_turn_cap": task["max_turns"],
                       "internal_model_turn_cap_enforceable": False},
            "launch_blockers": BLOCKERS, "comparison_eligible": False}


def dry_run(root, condition, task_id, server_python, runtime, model):
    """Fresh clean workspace and complete control artifacts; launches nothing."""
    task = next(task for task in tasks() if task["id"] == task_id)
    root = Path(root).resolve()
    if root.is_relative_to(REPO):
        raise ValueError("trial root must be outside repository")
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "workspace"
    (workspace / "input").mkdir(parents=True)
    (workspace / "output").mkdir()
    control = root / "control"
    control.mkdir()
    source = HERE / task["fixture"]
    shutil.copyfile(source, workspace / "input" / source.name)
    provenance, files = source_snapshot(condition)
    for path, data in files.items():
        destination = control / "server-source" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    spec = launch_spec(task, condition, workspace, control / "server-source", server_python, runtime, model)
    spec["source_provenance"] = provenance
    spec["source_content_hash"] = digest(provenance["files"])
    spec["fixture_hash"] = digest(source.read_bytes())
    spec["fixture_manifest_hash"] = digest((HERE / "fixtures/manifest.json").read_bytes())
    spec["task_contract_hash"] = digest(task)
    spec["harness_files"] = {str(path.relative_to(REPO)): digest(path.read_bytes())
                             for path in sorted(HERE.glob("*.py"))}
    (control / "launch-spec.json").write_bytes(encoded(spec))
    (control / "identity.json").write_bytes(encoded({"config_hash": digest(spec)}))
    (control / "canonical-prompt.txt").write_text(task["prompt"], encoding="utf-8")
    return spec


def replay(root, fixture_stream, task_id):
    """SYNTHETIC stream transport, actual answer validator, append-only evidence.

    Stream is fixture input, never accepted as a real model transcript. Every
    replay gets a fresh workspace. Failures still produce complete trial records.
    """
    root = Path(root).resolve()
    if root.is_relative_to(REPO):
        raise ValueError("replay root must be outside repository")
    root.mkdir(parents=True, exist_ok=True)
    task = next(task for task in tasks() if task["id"] == task_id)
    attempt = Path(tempfile.mkdtemp(prefix="synthetic-", dir=root))
    observer = ExecEvents(BASE_TOOLS)
    stream = Path(fixture_stream).resolve()
    config = {"synthetic": True, "provider": "codex_exec_synthetic_replay",
              "canonical_prompt": task["prompt"], "policy": POLICY,
              "fixture_stream_hash": digest(stream.read_bytes()),
              "harness_hash": digest({str(path.name): digest(path.read_bytes())
                                      for path in sorted(HERE.glob("*.py"))}),
              "comparison_eligible": False}
    started = time.monotonic()
    try:
        result = capture([sys.executable, "-c", "import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
                          str(stream)], attempt, observer)
    except (OSError, validators._RuntimeProcessError) as exc:
        observer.fail("capture_or_cleanup_error")
        result = {"returncode": None, "stop_reason": "capture_or_cleanup_error",
                  "wall_seconds": time.monotonic() - started, "diagnostic": redact(str(exc))[:2000]}
    if result["returncode"] != 0:
        observer.fail("process_exit")
    telemetry = observer.finish()
    telemetry["wall_seconds"] = result["wall_seconds"]
    telemetry["requested_model"] = "SYNTHETIC_NO_MODEL"
    transcript = attempt / "events.json"
    transcript.write_bytes(encoded(observer.events))
    diagnostics = attempt / "capture.json"
    diagnostics.write_bytes(encoded({key: redact(value.decode("utf-8", errors="replace"))
                                    if isinstance(value, bytes) else value
                                    for key, value in result.items()}))
    if task["outputs"]["kind"] == "answer_json" and isinstance(observer.final, str):
        (attempt / task["outputs"]["path"]).write_text(redact(observer.final), encoding="utf-8")
    validation = validate_trial(task, HERE / "fixtures", attempt)
    record = sanitized_record({"status": "complete", "synthetic": True, "config": config,
              "identity": {"task": task_id, "condition": "codex_synthetic_replay", "trial": 1,
                           "config_hash": digest(config),
                           "source_hash": digest((HERE / task["fixture"]).read_bytes())},
              "attempt_root": str(attempt), "telemetry": telemetry, "validator": validation,
              "comparison_eligible": False,
              "artifacts": {str(path): digest(path.read_bytes()) for path in attempt.iterdir() if path.is_file()}})
    Journal(root / "trials.jsonl").append(record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["dry-run", "synthetic-replay"])
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--task", default="data_block_counts")
    parser.add_argument("--condition", choices=CONDITIONS, default="enhanced_bpy")
    parser.add_argument("--server-python", default="/REQUIRES_REVIEW/server-python")
    parser.add_argument("--runtime", default="/REQUIRES_REVIEW/runtime")
    parser.add_argument("--model", default="REQUIRES_PARENT_MODEL_SELECTION")
    parser.add_argument("--fixture-stream")
    args = parser.parse_args(argv)
    if args.mode == "dry-run":
        result = dry_run(args.output_root, args.condition, args.task,
                         args.server_python, args.runtime, args.model)
        print(json.dumps({"config_hash": digest(result), "artifacts": args.output_root,
                          "launch_blockers": result["launch_blockers"]}, indent=2))
    else:
        if not args.fixture_stream:
            parser.error("--fixture-stream required for synthetic-replay")
        result = replay(args.output_root, args.fixture_stream, args.task)
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
