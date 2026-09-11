"""Canonical BlenderBench protocol manifests and provenance."""
from __future__ import annotations

import hashlib
import json
import os
import platform
from pathlib import Path
import re
import subprocess
from typing import Any

TOOLS = (
    "execute_blender_code_for_cli",
    "get_render_as_image_for_cli",
    "get_runtime_python_api_docs_for_cli",
    "search_api_docs",
)
PROTOCOL_VERSION = "blenderbench-direct-v1"
EVALUATOR_REPOSITORY = "https://github.com/Fugtemypt123/VIGA-release"
EVALUATOR_REVISION = "69cb8ef0651bf68124815682df4a0f8e8b57c141"
EVALUATOR_SHA256 = "9ee08818aa858dccfff3efbb36cb9a98e8887f14e6168ad1a23731351cdeb3b5"
CLIP_MODEL = "openai/clip-vit-base-patch32"
CLIP_REVISION = "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"
CODEX_VERSION = "codex-cli 0.154.0"
CODEX_SOURCE_REVISION = "6b9826e3aa83b1a5947db50f4332cb9c65f1b340"
REPOSITORY = Path(__file__).resolve().parents[2]
IMPLEMENTATION_PATHS = (
    "benchmarks/blenderbench_direct/dataset.py",
    "benchmarks/blenderbench_direct/protocol.py",
    "benchmarks/blenderbench_direct/runner.py",
    "benchmarks/blenderbench_direct/ref_based_eval.py",
    "benchmarks/blenderbench_direct/score_pair.py",
    "benchmarks/blenderbench_direct/scoring.py",
    "benchmarks/blenderbench_direct/suite.py",
    "mcp/blmcp/tools/execute_blender_code.py",
    "mcp/blmcp/tools_helpers/blender_cli.py",
    "tools/direct_codex.py",
    "tools/direct_runtime.py",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def redact(value: Any, key: str = "") -> Any:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    if normalized in {"args", "env", "variables"}:
        return "<omitted:unsafe-runtime-config>"
    sensitive = re.compile(
        r"(^|_)(token|secret|password|passwd|pwd|credential|authorization|auth|api_?key|pat|cookie|session)($|_)")
    if sensitive.search(normalized):
        return "<redacted>"
    if isinstance(value, dict):
        return {name: redact(child, str(name)) for name, child in value.items()}
    if isinstance(value, list):
        return [redact(child, key) for child in value]
    if isinstance(value, str) and (re.search(r"(?i)\bbearer\s+\S+", value)
            or re.search(r"(?i)https?://[^/@\s:]+:[^@\s]+@", value)
            or re.search(r"(?i)[?&](?:token|key|secret|signature|auth)=[^&\s]+", value)):
        return "<redacted>"
    return value


def read_mcp_config(path: Path) -> dict:
    import tomllib
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def resolved_version(binary: Path, *arguments: str) -> str:
    return subprocess.check_output([str(binary), *arguments], text=True,
                                   stderr=subprocess.STDOUT).strip()


def runtime_provenance() -> dict:
    cpu = platform.processor() or platform.machine()
    provenance = {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu": cpu,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "logical_cpu_count": os.cpu_count(),
    }
    try:
        provenance["gpu"] = resolved_version(Path("nvidia-smi"),
            "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader")
    except (OSError, subprocess.SubprocessError):
        provenance["gpu"] = None
    return provenance


def collect_source_identity(repository: Path = REPOSITORY) -> dict:
    """Bind the protocol to source bytes and Git state without host-specific data."""
    repository = Path(repository).resolve()
    initialization = repository / "benchmarks/blenderbench_direct/initialization-manifest.json"
    assets = repository / "benchmarks/blenderbench_direct/assets-manifest.json"
    clip_manifest_path = repository / "benchmarks/blenderbench_direct/clip-snapshot-manifest.json"
    scoring_lock = repository / "benchmarks/blenderbench_direct/requirements-scoring.lock"
    implementation = {}
    for relative in IMPLEMENTATION_PATHS:
        path = repository / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"protocol implementation is missing or a symlink: {relative}")
        implementation[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository, text=True,
            stderr=subprocess.STDOUT).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repository, text=True, stderr=subprocess.STDOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("protocol source must be inside a readable Git checkout") from exc
    if len(revision) != 40:
        raise ValueError("Git revision must be a full 40-hex commit")
    assets_bytes = assets.read_bytes()
    clip_manifest_bytes = clip_manifest_path.read_bytes()
    try:
        assets_manifest = json.loads(assets_bytes)
        clip_manifest = json.loads(clip_manifest_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("assets manifest is not valid JSON") from exc
    return {
        "assets_manifest": assets_manifest,
        "assets_manifest_sha256": hashlib.sha256(assets_bytes).hexdigest(),
        "clip_snapshot_manifest": clip_manifest,
        "clip_snapshot_manifest_sha256": hashlib.sha256(clip_manifest_bytes).hexdigest(),
        "initialization_manifest_sha256": hashlib.sha256(initialization.read_bytes()).hexdigest(),
        "scoring_lock_sha256": hashlib.sha256(scoring_lock.read_bytes()).hexdigest(),
        "implementation_sha256": implementation,
        "git_revision": revision,
        "git_dirty": bool(status),
    }


def build_manifest(config: dict, *, dataset_revision: str, evaluator_revision: str,
                   model_revision: str | None, versions: dict,
                   provenance: dict | None = None, source_identity: dict | None = None) -> dict:
    public_config = redact(config)
    protocol = {
        "protocol_version": PROTOCOL_VERSION,
        "dataset_revision": dataset_revision,
        "evaluator_revision": evaluator_revision,
        "evaluator_sha256": EVALUATOR_SHA256,
        "clip_model": CLIP_MODEL,
        "clip_revision": CLIP_REVISION,
        "model": config.get("model"),
        "model_revision": model_revision,
        "rounds": config.get("rounds"),
        "tools": list(TOOLS),
        "generation": {
            "reasoning": "direct model reasoning",
            "sequential_meaningful_saved_rounds": config.get("rounds"),
            "shell_scene_operations": False,
            "goal_code_or_solution_access": False,
            "vlm_judge": False,
            "score_feedback_to_generation": False,
            "saved_scene_audit_timing": "after generation",
        },
        "rendering": {"engine": "Cycles", "device": "GPU", "cpu_fallback": False},
        "versions": versions,
        "config": public_config,
        "source_identity": (collect_source_identity() if source_identity is None
                            else source_identity),
    }
    return {
        "schema_version": 1,
        "protocol": protocol,
        "protocol_sha256": digest(protocol),
        "config": public_config,
        "versions": versions,
        "provenance": provenance if provenance is not None else runtime_provenance(),
    }


def validate_run_manifest(manifest: dict, *, tasks: list[str], rounds: int) -> dict:
    """Validate a generation manifest before recovery or standalone scoring."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("run manifest schema is invalid")
    body = manifest.get("protocol")
    if not isinstance(body, dict) or manifest.get("protocol_sha256") != digest(body):
        raise ValueError("run manifest protocol hash is invalid")
    if (body.get("protocol_version") != PROTOCOL_VERSION
            or body.get("dataset_revision") != __import__(
                "benchmarks.blenderbench_direct.dataset", fromlist=["DATASET_REVISION"]).DATASET_REVISION
            or body.get("evaluator_revision") != EVALUATOR_REVISION
            or body.get("evaluator_sha256") != EVALUATOR_SHA256
            or body.get("rounds") != rounds
            or body.get("config", {}).get("tasks") != tasks):
        raise ValueError("run manifest does not match the requested frozen protocol")
    expected_generation = {
        "reasoning": "direct model reasoning",
        "sequential_meaningful_saved_rounds": rounds,
        "shell_scene_operations": False,
        "goal_code_or_solution_access": False,
        "vlm_judge": False,
        "score_feedback_to_generation": False,
        "saved_scene_audit_timing": "after generation",
    }
    config = body.get("config", {})
    versions = body.get("versions")
    frozen_rules_match = (
        body.get("clip_model") == CLIP_MODEL
        and body.get("clip_revision") == CLIP_REVISION
        and body.get("tools") == list(TOOLS)
        and body.get("generation") == expected_generation
        and body.get("rendering") == {"engine": "Cycles", "device": "GPU", "cpu_fallback": False}
        and body.get("model") == config.get("model")
        and body.get("model_revision") == config.get("model_revision")
        and isinstance(versions, dict)
        and versions.get("codex") == CODEX_VERSION
        and versions.get("scorer_python") == "Python 3.13.15"
        and isinstance(versions.get("bpy"), str)
        and versions["bpy"].startswith(str(config.get("bpy_version", "")))
    )
    if not frozen_rules_match:
        raise ValueError("run manifest does not match the requested frozen protocol")
    source = body.get("source_identity")
    current = collect_source_identity()
    required = {"assets_manifest", "assets_manifest_sha256", "clip_snapshot_manifest",
                "clip_snapshot_manifest_sha256", "initialization_manifest_sha256",
                "scoring_lock_sha256", "implementation_sha256", "git_revision", "git_dirty"}
    if not isinstance(source, dict) or not required.issubset(source):
        raise ValueError("run manifest source identity is incomplete")
    if source["git_dirty"] is not False:
        raise ValueError("publication run manifest requires a clean Git source identity")
    for key in ("assets_manifest", "assets_manifest_sha256", "clip_snapshot_manifest",
                "clip_snapshot_manifest_sha256", "initialization_manifest_sha256",
                "scoring_lock_sha256", "implementation_sha256", "git_revision"):
        if source.get(key) != current[key]:
            raise ValueError(f"run manifest {key} differs from local scoring protocol")
    return manifest


def load_run_manifest(run_root: Path, *, tasks: list[str], rounds: int) -> dict:
    path = Path(run_root) / "run-manifest.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("standalone scoring requires a regular run-manifest.json")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("run-manifest.json is unreadable") from exc
    return validate_run_manifest(manifest, tasks=tasks, rounds=rounds)


def write_manifest(path: Path, manifest: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)
    return path
