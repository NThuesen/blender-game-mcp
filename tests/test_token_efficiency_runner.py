"""SYNTHETIC integration tests: no network, Blender execution, or billing."""
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.token_efficiency.models import FixtureTransport, ModelAdapter, condition_config
from benchmarks.token_efficiency.report import aggregate, write_report
from benchmarks.token_efficiency.run import HERE, main, offline_trial, tasks
from benchmarks.token_efficiency.telemetry import Journal, Telemetry, digest, redact
from tests.test_token_efficiency_telemetry import complete_record


def response(arguments="{}", name="allowed"):
    return {"model": "fixture", "choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "1", "function": {"name": name, "arguments": arguments}}]},
        "finish_reason": "tool_calls"}]}


class AdapterTests(unittest.TestCase):
    tools = [{"name": "allowed", "description": "fixture", "inputSchema": {"type": "object"}},
             {"name": "forbidden", "description": "fixture", "inputSchema": {"type": "object"}}]

    def test_filter_and_preserve_exact_model(self):
        transport = FixtureTransport([response()])
        adapter = ModelAdapter("openai", "exact-requested", transport)
        _, parsed, _ = adapter.call([], self.tools, ["allowed"])
        self.assertEqual(parsed[1][0][1], "allowed")
        self.assertEqual(transport.requests[0]["model"], "exact-requested")
        self.assertEqual(len(transport.requests[0]["tools"]), 1)
        self.assertEqual(transport.requests[0]["tools"][0]["function"]["name"], "allowed")

    def test_restrictions_and_malformed_calls(self):
        for fixture in (response(name="forbidden"), response("[1]"), response("bad")):
            adapter = ModelAdapter("openai", "fixture", FixtureTransport([fixture]))
            with self.assertRaises(ValueError):
                adapter.call([], self.tools, ["allowed"])
        with self.assertRaises(ValueError):
            ModelAdapter("openai", "fixture", None, temperature=0)
        with self.assertRaises(ValueError):
            ModelAdapter("unknown", "fixture", None)
        with self.assertRaises(ValueError):
            ModelAdapter("openai", "fixture", None).prepare(self.tools, ["missing"])

    def test_claude_existing_protocol(self):
        transport = FixtureTransport([{"model": "fixture", "stop_reason": "end_turn",
                                       "content": [{"type": "text", "text": "fixture"}]}])
        adapter = ModelAdapter("claude", "fixture", transport)
        _, parsed, _ = adapter.call([], self.tools, ["allowed"], policy="policy")
        self.assertTrue(parsed[3])
        self.assertEqual(transport.requests[0]["system"], "policy")

    def test_conditions_are_not_original_baseline(self):
        task = tasks()[0]
        config = condition_config("A", task, "enhanced-revision")
        self.assertEqual(config["role"], "enhanced_backend_ablation")
        self.assertNotEqual(config["revision"], config["original_baseline_revision"])
        with self.assertRaises(ValueError):
            condition_config("D", dict(task, execution_variants=["bpy_cli"]), "revision")


class OfflineTests(unittest.TestCase):
    def test_real_answer_validators_persistence_resume_report(self):
        selected = [task for task in tasks() if task["outputs"]["kind"] == "answer_json"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for task in selected:
                for trial in range(1, 4):
                    record = offline_trial(task, "C", trial, root, HERE / "fixtures")
                    self.assertTrue(record["validator"]["ok"], record["validator"])
                    self.assertTrue(record["synthetic"])
                    self.assertIsNone(record["telemetry"]["tokens"]["output"])
                    self.assertTrue(offline_trial(task, "C", trial, root, HERE / "fixtures")["resumed"])
            journal = Journal(root / "trials.jsonl")
            records, errors = journal.read()
            self.assertFalse(errors)
            self.assertEqual(len(records), len(selected) * 3)
            summary = write_report(journal.path, root)
            self.assertEqual(summary["unique_trials"], len(selected) * 3)
            self.assertTrue(all(row["n"] == 3 and row["passed"] == 3 for row in summary["rows"]))
            self.assertFalse(any(row["comparison_eligible"] for row in summary["rows"]))
            self.assertIn("SYNTHETIC", (root / "summary.md").read_text())
            # A changed artifact is a retry, never a file-exists resume.
            target = Path(records[0]["attempt_root"]) / selected[0]["outputs"]["path"]
            target.write_text("{}", encoding="utf-8")
            retry = offline_trial(selected[0], "C", 1, root, HERE / "fixtures")
            self.assertNotIn("resumed", retry)
            self.assertNotEqual(retry["attempt_root"], records[0]["attempt_root"])
            summary = write_report(journal.path, root)
            self.assertEqual(summary["superseded_attempts"], 1)

    def test_payload_sanitized_before_identity_and_exact_resume(self):
        task = copy.deepcopy(next(t for t in tasks() if t["outputs"]["kind"] == "answer_json"))
        fake = "sk-SYNTHETIC_NOT_A_REAL_CREDENTIAL"
        task["prompt"] += " Synthetic redaction fixture: " + fake
        original = copy.deepcopy(task)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "task-output"
            record = offline_trial(task, "C", 1, root, HERE / "fixtures")
            journal = Journal(root / "trials.jsonl")
            self.assertEqual(journal.read(), ([record], []))
            self.assertEqual(record["identity"]["config_hash"], digest(record["config"]))
            self.assertTrue(Path(record["attempt_root"]).is_relative_to(root))
            self.assertIn("task-output", record["resolved_prompt"])
            self.assertNotIn(fake, journal.path.read_text())
            transcript = Path(record["attempt_root"]) / "transcript.json"
            self.assertNotIn(fake, transcript.read_text())
            self.assertIn("[REDACTED]", transcript.read_text())
            self.assertEqual(record["telemetry"]["transcript_utf8_bytes"], len(transcript.read_bytes()))
            self.assertEqual(task, original)
            before = journal.path.read_bytes()
            self.assertTrue(offline_trial(task, "C", 1, root, HERE / "fixtures")["resumed"])
            self.assertEqual(journal.path.read_bytes(), before)
            self.assertEqual(write_report(journal.path, root)["unique_trials"], 1)

    def test_failed_tasks_not_efficiency_wins(self):
        records = []
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "answer.json"
            artifact.write_text("{}", encoding="utf-8")
            for trial, ok in ((1, True), (2, False), (3, True)):
                record = complete_record(artifact, trial, ok)
                record["telemetry"]["wall_seconds"] = trial
                records.append(record)
        summary = aggregate(records + [copy.deepcopy(records[-1])])
        self.assertEqual(summary["unique_trials"], 3)
        row = summary["rows"][0]
        self.assertEqual((row["passed"], row["failed"], row["wall_n"]), (2, 1, 2))
        self.assertFalse(row["comparison_eligible"])
        self.assertEqual(row["wall_median"], 2)

    def test_corruption_rejected_across_resume_validate_and_report(self):
        task = next(task for task in tasks() if task["outputs"]["kind"] == "answer_json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = offline_trial(task, "C", 1, root, HERE / "fixtures")
            journal = Journal(root / "trials.jsonl")
            mutations = [
                ("changed config", lambda r: r["config"].update(policy="changed")),
                ("removed config", lambda r: r.pop("config")),
                ("invalid config", lambda r: r.update(config=[])),
                ("missing synthetic", lambda r: r.pop("synthetic")),
                ("string synthetic", lambda r: r.update(synthetic="true")),
                ("integer synthetic", lambda r: r.update(synthetic=1)),
                ("null synthetic", lambda r: r.update(synthetic=None)),
                ("contradictory synthetic", lambda r: r.update(synthetic=False)),
                ("missing config synthetic", lambda r: r["config"].pop("synthetic")),
                ("invalid config synthetic", lambda r: r["config"].update(synthetic=1)),
                ("missing attempt root", lambda r: r.pop("attempt_root")),
                ("invalid attempt root", lambda r: r.update(attempt_root=[])),
                ("empty attempt root", lambda r: r.update(attempt_root="")),
                ("missing telemetry", lambda r: r.pop("telemetry")),
                ("invalid telemetry", lambda r: r.update(telemetry=[])),
                ("empty telemetry", lambda r: r.update(telemetry={})),
            ]
            for label, mutate in mutations:
                with self.subTest(label=label):
                    damaged = copy.deepcopy(good)
                    mutate(damaged)
                    if label in ("missing config synthetic", "invalid config synthetic"):
                        # Provenance must fail even when the config hash is valid.
                        damaged["identity"]["config_hash"] = digest(damaged["config"])
                    journal.path.write_text(json.dumps(damaged) + "\n", encoding="utf-8")
                    records, errors = journal.read()
                    self.assertEqual(records, [])
                    self.assertEqual(len(errors), 1)
                    with self.assertRaises(ValueError):
                        journal.append(damaged)
                    with self.assertRaises(ValueError):
                        aggregate([damaged])
                    self.assertFalse(journal.resumable(good["identity"], lambda _: self.fail(
                        "corrupt record reached artifact validator")))
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        exit_code = main(["validate", "--output-root", str(root)])
                    self.assertEqual(exit_code, 1)
                    checked = json.loads(output.getvalue())
                    self.assertEqual(checked["checks"], [])
                    self.assertEqual(len(checked["journal_errors"]), 1)
                    summary = write_report(journal.path, root)
                    self.assertEqual(summary["completed_attempts"], 0)
                    self.assertEqual(summary["unique_trials"], 0)
                    self.assertEqual(summary["rows"], [])
                    self.assertEqual(len(summary["journal_errors"]), 1)
            journal.path.write_text(json.dumps(good) + "\n", encoding="utf-8")
            self.assertTrue(offline_trial(task, "C", 1, root, HERE / "fixtures")["resumed"])
            self.assertEqual(len(journal.read()[0]), 1)

    def test_images_redaction_api_error_and_partial_usage(self):
        telemetry = Telemetry("openai", "fixture")
        telemetry.api_response({"usage": {"completion_tokens": 0, "api_key": "private"}})
        telemetry.api_response({"usage": {}})
        telemetry.failure("timeout", api=True)
        telemetry.tool_result("image", "ok", duration=0, images=[b"abc"])
        measured = telemetry.finish([], [])
        self.assertEqual((measured["image_count"], measured["image_bytes"]), (1, 3))
        self.assertEqual(measured["api_errors"], 1)
        self.assertEqual(measured["api_attempts"], 3)
        self.assertIsNone(measured["tokens"]["output"])
        self.assertNotIn("private", json.dumps(measured))
        self.assertEqual(redact({"api_key": "private"})["api_key"], "[REDACTED]")
        with self.assertRaises(ValueError):
            telemetry.tool_result("bad", "", duration=-1)


if __name__ == "__main__":
    unittest.main()
