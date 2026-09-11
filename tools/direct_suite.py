"""Compatibility entry point for the portable BlenderBench runner.

Use ``python -m benchmarks.blenderbench_direct.runner`` for new automation.
"""
from __future__ import annotations

from pathlib import Path

from benchmarks.blenderbench_direct.runner import main
from benchmarks.blenderbench_direct.suite import run_suite
ROOT = Path.cwd()


if __name__ == "__main__":
    raise SystemExit(main())
