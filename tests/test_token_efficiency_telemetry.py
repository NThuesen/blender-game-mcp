"""Offline fixtures only: these tests make no model or network calls."""
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.token_efficiency.telemetry import (
    Journal, Telemetry, digest, normalize_usage, estimate_cost,
)


class UsageTests(unittest.TestCase):
    def test_missing_is_not_zero(self):
        usage = normalize_usage("openai", {})
        self.assertTrue(all(value is None for value in usage.values()))

    def test_openai_cache_reasoning(self):
        usage = normalize_usage("openai", {
            "prompt_tokens": 100, "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 40},
            "completion_tokens_details": {"reasoning_tokens": 12},
        })
        self.assertEqual(usage["uncached_input"], 60)
        self.assertEqual(usage["cached_input"], 40)
        self.assertEqual(usage["output"], 30)  # includes reasoning, not additive
        self.assertEqual(usage["reasoning"], 12)
        self.assertIsNone(usage["cache_creation"])
        self.assertIsNone(normalize_usage("openai", {"prompt_tokens": 10})["uncached_input"])

    def test_claude_cache(self):
        usage = normalize_usage("claude", {"input_tokens": 10, "output_tokens": 0,
                                          "cache_read_input_tokens": 20,
                                          "cache_creation_input_tokens": 5})
        self.assertEqual(usage["uncached_input"], 10)
        self.assertEqual(usage["cache_creation"], 5)
        self.assertEqual(usage["output"], 0)
        self.assertIsNone(usage["reasoning"])

    def test_cost_requires_complete_explicit_rates(self):
        usage = dict(uncached_input=10, cached_input=20, cache_creation=0,
                     output=5, reasoning=None)
        rates = {"version": "fixture-v1", "source": "fixture://not-billing",
                 "model": "fixture", "currency": "USD", "per_million": {
                     "uncached_input": 1, "cached_input": 0.5,
                     "cache_creation": 2, "output": 4}}
        self.assertIsNone(estimate_cost(usage, None, "fixture"))
        self.assertEqual(estimate_cost(usage, rates, "fixture")["amount"], 0.00004)
        self.assertTrue(estimate_cost(usage, rates, "fixture")["estimated"])
        self.assertIsNone(estimate_cost(usage, rates, "other"))
        self.assertIsNone(estimate_cost(dict(usage, output=None), rates, "fixture"))

    def test_measurements_and_failure_definition(self):
        telemetry = Telemetry("openai", "fixture", clock=iter([1.0, 3.5]).__next__)
        telemetry.api_response({"model": "fixture-returned", "usage": {}})
        telemetry.tool_result("inspect", "é", duration=0.5, error=True)
        telemetry.tool_result("inspect", "ok", duration=0.25)
        result = telemetry.finish([{"content": "é"}], [{"name": "inspect"}])
        self.assertEqual(result["wall_seconds"], 2.5)
        self.assertEqual(result["tool_seconds"], 0.75)
        self.assertEqual(result["tool_result_bytes"], 4)
        self.assertEqual(result["tool_counts"], {"inspect": 2})
        self.assertEqual(result["repair_calls"], 1)
        self.assertEqual(result["tool_errors"], 1)
        self.assertEqual(result["returned_models"], ["fixture-returned"])
        self.assertIsNone(result["tokens"]["output"])
        self.assertGreater(result["transcript_utf8_bytes"], 2)


def complete_record(artifact, trial=1, ok=True):
    """Complete SYNTHETIC journal fixture, including hashed configuration."""
    config = {"synthetic": True}
    return {"identity": {"task": "t", "condition": "C", "trial": trial,
                         "config_hash": digest(config), "source_hash": digest(b"source")},
            "status": "complete", "synthetic": True, "config": config,
            "attempt_root": str(artifact.parent),
            "artifacts": {str(artifact): digest(artifact.read_bytes())},
            "validator": {"ok": ok, "errors": [], "details": {}},
            "telemetry": Telemetry("openai", "synthetic-fixture").finish([], [])}


class JournalTests(unittest.TestCase):
    def test_operational_values_round_trip_verbatim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "task-output"
            root.mkdir()
            artifact = root / "task-answer.json"
            artifact.write_text("{}", encoding="utf-8")
            record = complete_record(artifact)
            record["identity"]["task"] = "task-output"
            record["config"]["runtime"] = {"validator_executable": "/tmp/task-output/python"}
            record["config"]["operational_id"] = "sk-SYNTHETIC_OPERATIONAL_ID"
            record["identity"]["config_hash"] = digest(record["config"])
            journal = Journal(root / "trials.jsonl")
            journal.append(record)
            self.assertEqual(journal.read(), ([record], []))
            self.assertTrue(journal.resumable(record["identity"], lambda row: {
                "ok": Path(row["attempt_root"]).is_dir(), "errors": [], "details": {}}))

    def test_unsanitized_payload_rejected_without_writing(self):
        from benchmarks.token_efficiency.telemetry import sanitized_config, sanitized_record

        fake = "sk-SYNTHETIC_NOT_A_REAL_CREDENTIAL"
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "answer.json"
            artifact.write_text("{}", encoding="utf-8")
            record = complete_record(artifact)
            record["config"]["policy"] = "Synthetic fixture " + fake
            record["identity"]["config_hash"] = digest(record["config"])
            journal = Journal(Path(directory) / "trials.jsonl")
            with self.assertRaises(ValueError):
                journal.append(record)
            self.assertFalse(journal.path.exists())
            record["config"] = sanitized_config(record["config"])
            record["identity"]["config_hash"] = digest(record["config"])
            record["resolved_prompt"] = "Bearer SYNTHETIC_NOT_A_REAL_TOKEN"
            record["validator"]["errors"] = [{"message": fake, "path": "/tmp/sk-operational-path"}]
            with self.assertRaises(ValueError):
                journal.append(record)
            self.assertFalse(journal.path.exists())
            safe = sanitized_record(record)
            journal.append(safe)
            self.assertEqual(journal.read(), ([safe], []))
            self.assertEqual(safe["validator"]["errors"][0]["path"], "/tmp/sk-operational-path")
            self.assertNotIn(fake, journal.path.read_text())
            self.assertNotIn("SYNTHETIC_NOT_A_REAL_TOKEN", journal.path.read_text())

    def test_interruption_resume_and_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "answer.json"
            artifact.write_text("{}", encoding="utf-8")
            record = complete_record(artifact)
            identity = record["identity"]
            journal = Journal(root / "trials.jsonl")
            journal.append(record)
            validator = lambda _: {"ok": True, "errors": [], "details": {}}
            self.assertTrue(journal.resumable(identity, validator))
            self.assertFalse(journal.resumable(dict(identity, config_hash="other"), validator))
            self.assertFalse(journal.resumable(identity, lambda _: {"ok": False}))
            with journal.path.open("ab") as handle:
                handle.write(b'{"partial":')
            journal.append(record)
            self.assertEqual(len(journal.read()[0]), 2)
            self.assertEqual(len(journal.read()[1]), 1)
            artifact.write_text("changed", encoding="utf-8")
            self.assertFalse(journal.resumable(identity, validator))

    def test_invalid_and_failed_records_not_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "trials.jsonl")
            journal.path.write_text('{}\n{"status":"partial"}\n', encoding="utf-8")
            self.assertFalse(journal.resumable({}, lambda _: {"ok": True}))
            self.assertEqual(len(journal.read()[1]), 2)


if __name__ == "__main__":
    unittest.main()
