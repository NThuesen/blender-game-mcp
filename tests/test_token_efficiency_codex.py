"""Clearly SYNTHETIC exec JSONL streams; no Codex model invocation.

Usage/lifecycle shape: official non-interactive docs (accessed 2026-09-07).
MCP shape mirrors Codex exec item lifecycle: server/tool/arguments/result/error.
These are handwritten fixtures, NOT observed benchmark output or billing.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.token_efficiency.codex_runner import (
    BASE_TOOLS, CodexSubprocessAdapter, ExecEvents, capture, dry_run,
    launch_spec, replay, usage_counts,
)
from benchmarks.token_efficiency.run import tasks
from benchmarks.token_efficiency.report import aggregate
from benchmarks.token_efficiency.telemetry import Journal, digest


def stream(*items, usage=None):
    events = [{"type": "thread.started", "thread_id": "SYNTHETIC-thread"}, {"type": "turn.started"}]
    events += list(items)
    events.append({"type": "turn.completed", "usage": usage})
    return b"".join(json.dumps(event).encode() + b"\n" for event in events)


def mcp(kind="item.completed", **changes):
    item = {"id": "item_0", "type": "mcp_tool_call", "server": "blender",
            "tool": BASE_TOOLS[0], "arguments": {"query": "SYNTHETIC"},
            "result": {"content": [{"type": "text", "text": "SYNTHETIC result"}],
                       "structured_content": {"matches": 1}},
            "error": None, "status": "completed"}
    item.update(changes)
    return {"type": kind, "item": item}


class EventTests(unittest.TestCase):
    def test_documented_usage_zero_missing_subsets(self):
        raw, usage = usage_counts({"input_tokens": 100, "cached_input_tokens": 40,
                                   "output_tokens": 30, "reasoning_output_tokens": 12,
                                   "unknown": "SYNTHETIC ignored"})
        self.assertEqual(usage, dict(uncached_input=60, cached_input=40, output=30,
                                     reasoning=12, cache_creation=None))
        self.assertNotIn("unknown", raw)
        self.assertIsNone(usage_counts({"input_tokens": 10})[1]["uncached_input"])
        self.assertEqual(usage_counts({"output_tokens": 0, "reasoning_output_tokens": 0})[1]["reasoning"], 0)
        self.assertIsNone(usage_counts({"output_tokens": True})[1]["output"])

    def test_chunked_mcp_lifecycle_counts_once(self):
        observer = ExecEvents(BASE_TOOLS)
        payload = stream(mcp("item.started", status="in_progress", result=None),
                         mcp("item.updated"), mcp(),
                         usage={"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 0})
        for value in payload:
            observer.feed(bytes([value]))
        result = observer.finish()
        self.assertEqual(result["tool_counts"], {BASE_TOOLS[0]: 1})
        self.assertEqual(result["tokens"]["output"], 0)
        self.assertIsNone(result["tokens"]["reasoning"])
        self.assertEqual(result["errors"], [])
        self.assertIsNone(result["api_attempts"])
        self.assertIsNone(result["cost"])

    def test_error_and_missing_usage_shapes(self):
        observer = ExecEvents(BASE_TOOLS)
        observer.feed(stream(mcp(status="failed", result=None, error={"message": "SYNTHETIC failure"})))
        result = observer.finish()
        self.assertEqual(result["tool_errors"], 1)
        self.assertTrue(all(value is None for value in result["tokens"].values()))
        for event in ({"type": "error", "message": "SYNTHETIC API failure"},
                      {"type": "turn.failed", "error": {"message": "SYNTHETIC error"}}):
            observer = ExecEvents()
            observer.feed(stream(event))
            self.assertIn(event["type"], observer.finish()["errors"])

    def test_malformed_truncated_unknown_duplicate_and_bypass(self):
        for payload in (b"not json\n", b'{"type":', b'[]\n', b'\xff\n',
                        stream({"type": "future_event"}), stream(mcp(), mcp()),
                        stream(mcp(server="unrelated")), stream(mcp(tool="forbidden")),
                        stream({"type": "item.completed", "item": {"id": "s", "type": "command_execution"}}),
                        stream(mcp("item.started"))):
            observer = ExecEvents(BASE_TOOLS)
            observer.feed(payload)
            result = observer.finish()
            self.assertTrue(result["errors"], payload)
            self.assertFalse(result["comparison_eligible"])
            self.assertIsNone(result["tokens"]["output"])

    def test_budgets_and_partial_usage(self):
        observer = ExecEvents(BASE_TOOLS, max_tools=1)
        observer.feed(stream(mcp(), mcp(id="second")))
        self.assertIn("tool_event_budget", observer.finish()["errors"])
        observer = ExecEvents()
        observer.feed(stream() + b'{"type":"turn.started"}\n')
        self.assertIn("turn_event_budget", observer.finish()["errors"])
        observer = ExecEvents(max_turns=2)
        observer.feed(stream(usage={"output_tokens": 10}))
        observer.feed(b'{"type":"turn.started"}\n{"type":"turn.completed"}\n')
        self.assertIsNone(observer.finish()["tokens"]["output"])


class CaptureTests(unittest.TestCase):
    def run_child(self, script, **bounds):
        with tempfile.TemporaryDirectory() as root:
            observer = ExecEvents(BASE_TOOLS)
            result = capture([sys.executable, "-c", script], root, observer, **bounds)
            return result, observer

    @unittest.skipUnless(os.environ.get("TASK11_PARENT_TTY_PROBE") == "1",
                         "parent prerequisite: sandbox denied /dev/tty open; do not reroute")
    def test_controlled_pty_and_separate_jsonl(self):
        # /dev/tty is a terminal handle, not a credential/config read.
        script = ("import os; assert os.isatty(0); "
                  "fd=os.open('/dev/tty',os.O_WRONLY); os.write(fd,b'SYNTHETIC tty'); os.close(fd); "
                  f"os.write(1,{stream()!r})")
        result, observer = self.run_child(script)
        self.assertEqual(result["returncode"], 0, result)
        self.assertIsNone(result["stop_reason"])
        self.assertIn(b"SYNTHETIC tty", result["tty"])
        self.assertFalse(observer.finish()["errors"])

    def test_pty_stdin_and_jsonl_without_tty_device_open(self):
        result, observer = self.run_child(f"import os; assert os.isatty(0); os.write(1,{stream()!r})")
        self.assertEqual(result["returncode"], 0, result)
        self.assertEqual(result["stdout"], stream())
        self.assertFalse(observer.finish()["errors"])

    def test_deadline_output_cap_and_event_budget_stop(self):
        result, _ = self.run_child("import time; time.sleep(10)", seconds=0.1)
        self.assertEqual(result["stop_reason"], "deadline")
        self.assertLess(result["wall_seconds"], 2)
        result, _ = self.run_child("import os; os.write(2,b'x'*10000)", output_bytes=100)
        self.assertEqual(result["stop_reason"], "output_cap")
        self.assertEqual(sum(len(result[key]) for key in ("stdout", "stderr", "tty")), 100)
        payload = stream(mcp(tool="forbidden"))
        result, observer = self.run_child(f"import os,time; os.write(1,{payload!r}); time.sleep(10)")
        self.assertEqual(result["stop_reason"], "tool_allowlist_bypass")
        self.assertLess(result["wall_seconds"], 2)
        self.assertTrue(observer.finish()["errors"])

    def test_nonzero_and_inherited_pipes_cleanup(self):
        result, _ = self.run_child("raise SystemExit(7)")
        self.assertEqual(result["returncode"], 7)
        result, _ = self.run_child("import os,time,signal; signal.signal(signal.SIGHUP,signal.SIG_IGN); "
                                  "pid=os.fork(); time.sleep(10) if pid==0 else None")
        self.assertEqual(result["stop_reason"], "inherited_output_handles")
        self.assertLess(result["wall_seconds"], 2)

    def test_observer_exception_cleanup(self):
        observer = ExecEvents()
        with tempfile.TemporaryDirectory() as root, patch.object(observer, "feed", side_effect=RuntimeError("SYNTHETIC")):
            with self.assertRaisesRegex(RuntimeError, "SYNTHETIC"):
                capture([sys.executable, "-c", "import os,time; os.write(1,b'x'); time.sleep(10)"], root, observer)


class LaunchTests(unittest.TestCase):
    def test_per_process_settings_and_fresh_prompt(self):
        task = tasks()[0]
        for condition in ("official_original", "enhanced_bpy"):
            spec = launch_spec(task, condition, "/tmp/clean", "/tmp/server", "/python", "/runtime", "exact-model")
            argv = spec["argv"]
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--ephemeral", argv)
            self.assertIn("workspace-write", argv)
            self.assertIn("on-request", argv)
            self.assertNotIn("resume", argv)
            self.assertNotIn("--ignore-rules", argv)
            self.assertEqual(spec["canonical_prompt"], task["prompt"])
            self.assertNotIn("expected_answer", json.dumps(spec))
            self.assertIn("features.shell_tool=false", argv)
            self.assertIsNone(spec["actual_tool_schemas"])
            self.assertFalse(spec["comparison_eligible"])
            with self.assertRaisesRegex(ValueError, "live launch blocked"):
                CodexSubprocessAdapter().run(spec)

    def test_fresh_workspace_and_identity_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "new"
            files = {"mcp/blmcp/example.py": b"# SYNTHETIC source snapshot"}
            provenance = {"revision": "SYNTHETIC", "files": {key: digest(value) for key, value in files.items()}}
            with patch("benchmarks.token_efficiency.codex_runner.source_snapshot", return_value=(provenance, files)):
                spec = dry_run(root, "enhanced_bpy", tasks()[0]["id"], "/python", "/runtime", "SYNTHETIC")
                self.assertEqual(json.loads((root / "control/identity.json").read_text())["config_hash"], digest(spec))
                self.assertEqual([p.name for p in (root / "workspace/input").iterdir()], ["scene.blend"])
                self.assertEqual(list((root / "workspace/output").iterdir()), [])
                with self.assertRaises(FileExistsError):
                    dry_run(root, "enhanced_bpy", tasks()[0]["id"], "/python", "/runtime", "SYNTHETIC")

    def test_synthetic_replay_journal_real_validator(self):
        task = tasks()[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "SYNTHETIC.jsonl"
            fixture.write_bytes(stream({"type": "item.completed", "item": {
                "id": "answer", "type": "agent_message", "text": json.dumps(task["expected_answer"])}}))
            record = replay(root / "records", fixture, task["id"])
            self.assertTrue(record["synthetic"])
            self.assertTrue(record["validator"]["ok"], record["validator"])
            journal = Journal(root / "records/trials.jsonl")
            self.assertEqual(journal.read(), ([record], []))
            before = journal.path.read_bytes()
            fixture.write_bytes(b"SYNTHETIC malformed\n")
            failed = replay(root / "records", fixture, task["id"])
            self.assertFalse(failed["validator"]["ok"])
            self.assertTrue(journal.path.read_bytes().startswith(before))
            self.assertNotEqual(record["attempt_root"], failed["attempt_root"])

    def test_report_requires_explicit_eligibility_and_capture_failure_persists(self):
        task = tasks()[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "SYNTHETIC.jsonl"
            fixture.write_bytes(stream())
            with patch("benchmarks.token_efficiency.codex_runner.capture", side_effect=OSError("SYNTHETIC launch error")):
                record = replay(root / "records", fixture, task["id"])
            self.assertIn("capture_or_cleanup_error", record["telemetry"]["errors"])
            self.assertEqual(Journal(root / "records/trials.jsonl").read(), ([record], []))
            # Hypothetical real records still need affirmative eligibility;
            # three passing validators alone cannot erase bypass/setup blockers.
            records = []
            for trial in range(1, 4):
                row = json.loads(json.dumps(record))
                row["synthetic"] = row["config"]["synthetic"] = False
                row["identity"].update(trial=trial, config_hash=digest(row["config"]))
                row["validator"] = {"ok": True, "errors": [], "details": {}}
                records.append(row)
            self.assertFalse(aggregate(records)["rows"][0]["comparison_eligible"])


if __name__ == "__main__":
    unittest.main()
