"""Compatibility entrypoint must not expose broken legacy recovery hooks."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import direct_suite as suite


class RecoveryTests(unittest.TestCase):
    def test_broken_legacy_recovery_hooks_are_removed(self):
        self.assertFalse(hasattr(suite, "score_checkpoints"))
        self.assertFalse(hasattr(suite, "recover_task"))
        self.assertFalse(hasattr(suite, "run_task"))
        self.assertFalse(hasattr(suite, "task_plan"))
        self.assertTrue(callable(suite.main))
        self.assertTrue(callable(suite.run_suite))


if __name__ == "__main__":
    unittest.main()
