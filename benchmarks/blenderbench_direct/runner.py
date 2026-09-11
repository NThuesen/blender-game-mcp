"""Publication BlenderBench CLI and explicit portable configuration."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Sequence

from . import dataset, protocol, scoring, video


@dataclass(frozen=True)
class RunConfig:
    dataset_root: Path
    mcp_toml: Path
    codex_binary: Path
    codex_version: str
    bpy_python: Path
    bpy_version: str
    clip_snapshot: Path
    output_root: Path
    model: str
    model_revision: str | None
    rounds: int
    tasks: tuple[str, ...]


def _path(value: str) -> Path:
    return Path(value).expanduser().absolute()


def _external(path: Path) -> Path:
    repository = Path(__file__).resolve().parents[2]
    candidate = path.expanduser().resolve()
    if candidate == repository or repository in candidate.parents:
        raise ValueError(f"dataset/output roots must stay outside this repository: {candidate}")
    return candidate


def _run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset-root", type=_path, required=True)
    parser.add_argument("--mcp-toml", type=_path, required=True)
    parser.add_argument("--codex-binary", type=_path, required=True)
    parser.add_argument("--codex-version", required=True, help="Exact output expected from CODEx_BINARY --version")
    parser.add_argument("--bpy-python", type=_path, required=True)
    parser.add_argument("--bpy-version", required=True, help="Exact bpy.app.version_string prefix required")
    parser.add_argument("--clip-snapshot", type=_path, required=True,
                        help="Offline CLIP snapshot matching clip-snapshot-manifest.json")
    parser.add_argument("--output-root", type=_path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision")
    parser.add_argument("--rounds", type=int, choices=[10], default=10,
                        help="Frozen publication protocol: exactly 10 rounds")
    parser.add_argument("--tasks", nargs="+", help="Explicit unique subset; omit for all 27 tasks")
    parser.add_argument("--approve-blender-tools", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download", help="Download and verify the pinned 27-task dataset")
    download.add_argument("--dataset-root", type=_path, required=True)
    download.add_argument("--tasks", nargs="+")
    run = commands.add_parser("run", help="Fresh generation; never resumes or regenerates existing output")
    _run_options(run)
    recover = commands.add_parser("recover", help="Audit/score saved output only; never generate")
    recover.add_argument("--source-root", type=_path, required=True)
    recover.add_argument("--output-root", type=_path, required=True, help="Fresh recovery evidence root")
    recover.add_argument("--dataset-root", type=_path, required=True)
    recover.add_argument("--bpy-python", type=_path, required=True)
    recover.add_argument("--bpy-version", required=True)
    recover.add_argument("--clip-snapshot", type=_path, required=True)
    recover.add_argument("--tasks", nargs="+")
    recover.add_argument("--rounds", type=int, choices=[10], default=10)
    score = commands.add_parser("score", help="Score saved renders only; never generate")
    score.add_argument("--run-root", type=_path, required=True)
    score.add_argument("--dataset-root", type=_path, required=True)
    score.add_argument("--clip-snapshot", type=_path, required=True)
    score.add_argument("--output", type=_path)
    score.add_argument("--tasks", nargs="+")
    score.add_argument("--rounds", type=int, choices=[10], default=10)
    movie = commands.add_parser("video", help="Generate a labelled compilation video from a JSON spec")
    movie.add_argument("--spec", type=_path, required=True)
    movie.add_argument("--output", type=_path, required=True)
    movie.add_argument("--ffmpeg")
    movie.add_argument("--fps", type=int, default=24)
    movie.add_argument("--seconds-per-task", type=float, default=3.0)
    return parser


def resolve_config(args: argparse.Namespace, *, check_files: bool = True) -> RunConfig:
    selected = tuple(dataset.select_tasks(args.tasks))
    args.dataset_root = _external(args.dataset_root)
    args.output_root = _external(args.output_root)
    config = RunConfig(dataset_root=args.dataset_root, mcp_toml=args.mcp_toml,
        codex_binary=args.codex_binary, codex_version=args.codex_version,
        bpy_python=args.bpy_python, bpy_version=args.bpy_version,
        clip_snapshot=args.clip_snapshot,
        output_root=args.output_root, model=args.model, model_revision=args.model_revision,
        rounds=args.rounds, tasks=selected)
    if (not config.model.strip() or config.codex_version != protocol.CODEX_VERSION
            or not config.bpy_version.strip()):
        raise ValueError("model and exact Codex 0.154.0/bpy pins are required")
    if check_files:
        for name in ("mcp_toml", "codex_binary", "bpy_python"):
            if not getattr(config, name).is_file():
                raise FileNotFoundError(f"--{name.replace('_', '-')} is not a file: {getattr(config, name)}")
        scoring.verify_clip_snapshot(config.clip_snapshot)
        scoring.preflight_scorer_runtime()
        for task in config.tasks:
            dataset.verify(task, config.dataset_root)
    return config


def _score(args: argparse.Namespace) -> int:
    args.run_root = _external(args.run_root)
    args.dataset_root = _external(args.dataset_root)
    if args.output is not None:
        args.output = _external(args.output)
    tasks = dataset.select_tasks(args.tasks)
    result = scoring.score_saved_run(args.run_root, args.dataset_root, tasks, args.rounds,
                                     args.clip_snapshot)
    json_path, csv_path = scoring.write_outputs(result, args.output or args.run_root / "scoring")
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), **result["summary"]}))
    return int(result["summary"]["failed_tasks"] != 0)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "download":
        args.dataset_root = _external(args.dataset_root)
        for task in dataset.select_tasks(args.tasks):
            print(dataset.stage(task, args.dataset_root))
        return 0
    if args.command == "score":
        return _score(args)
    if args.command == "video":
        args.output = _external(args.output)
        video.generate(args.spec, args.output, fps=args.fps,
                       seconds_per_task=args.seconds_per_task, ffmpeg=args.ffmpeg)
        print(args.output)
        return 0
    if args.command == "recover":
        args.source_root = _external(args.source_root)
        args.output_root = _external(args.output_root)
        args.dataset_root = _external(args.dataset_root)
        from .suite import recover_suite
        return recover_suite(args)
    if args.command == "run":
        config = resolve_config(args)
        from .suite import run_suite
        return run_suite(config, approved=args.approve_blender_tools)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
