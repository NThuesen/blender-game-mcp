"""Hash-pinned, input-only 27-task BlenderBench dataset downloader.

Only task text, start script, initial scene, and target image are staged. Upstream
Python is data: it is hashed and never imported, compiled, or executed here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import urllib.parse
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
DATASET_REPOSITORY = "DietCoke4671/BlenderBench"
DATASET_SOURCE_URL = "https://huggingface.co/datasets/DietCoke4671/BlenderBench"
DATASET_ATTRIBUTION = "BlenderBench dataset by DietCoke4671 and contributors"
DATASET_LICENSE = "CC BY 4.0"
DATASET_REVISION = "203e4d325e9438ca55b29bdfc4f6a90842d74e68"
_ASSET_NAMES = {"task.txt", "start.py", "scene.blend", "target.png"}


def _read(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def load_manifest() -> dict:
    assets = _read("assets-manifest.json")
    initialization = _read("initialization-manifest.json")
    if assets["revision"] != DATASET_REVISION or initialization["revision"] != DATASET_REVISION:
        raise ValueError("dataset manifest revision drift")
    if set(assets["tasks"]) != set(initialization["tasks"]):
        raise ValueError("asset and initialization task sets differ")
    tasks = {}
    for task in sorted(assets["tasks"]):
        tasks[task] = {**initialization["tasks"][task], "assets": assets["tasks"][task]}
    if len(tasks) != 27:
        raise ValueError("pinned manifest must contain exactly 27 tasks")
    return {"schema_version": 1, "repository": assets["repository"],
            "source_url": DATASET_SOURCE_URL, "attribution": DATASET_ATTRIBUTION,
            "license": DATASET_LICENSE, "revision": DATASET_REVISION, "tasks": tasks}


def task_ids() -> list[str]:
    return sorted(load_manifest()["tasks"])


def select_tasks(selected: list[str] | None) -> list[str]:
    canonical = task_ids()
    if selected is None:
        return canonical
    if not selected or len(selected) != len(set(selected)) or set(selected) - set(canonical):
        raise ValueError("--tasks must be a nonempty, unique subset of the 27 canonical task IDs")
    return list(selected)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_root(root: Path) -> Path:
    root = root.expanduser().absolute()
    for parent in (root, *root.parents):
        if parent.is_symlink():
            raise ValueError(f"symlink forbidden in dataset path: {parent}")
    return root


def _safe_task_root(root: Path, task: str) -> Path:
    current = _safe_root(root)
    for component in Path(task).parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"symlink forbidden in dataset path: {current}")
    return current


def _verify(path: Path, record: dict) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"asset must be a regular non-symlink file: {path}")
    if path.stat().st_size != record["size"] or sha256(path) != record["sha256"]:
        raise ValueError(f"asset size/hash mismatch: {path}")
    return {"size": record["size"], "sha256": record["sha256"]}


def verify(task: str, root: Path) -> dict:
    manifest = load_manifest()
    verify_dataset_surface(root, manifest)
    record = manifest["tasks"][task]
    directory = _safe_task_root(Path(root), task)
    allowed = set(record["assets"]) | {"checkpoint.json"}
    try:
        entries = {entry.name for entry in directory.iterdir()}
    except OSError as exc:
        raise ValueError(f"cannot enumerate dataset task root: {directory}") from exc
    unexpected = sorted(entries - allowed)
    if unexpected:
        raise ValueError(f"unexpected dataset entries outside the input-only manifest: {unexpected}")
    files = {name: _verify(directory / name, asset) for name, asset in record["assets"].items()}
    return {"task": task, "dataset_revision": DATASET_REVISION, "files": files}


def verify_dataset_surface(root: Path, manifest: dict | None = None) -> None:
    """Reject every path outside the declared input-only dataset surface."""
    root = Path(root).expanduser().absolute()
    manifest = load_manifest() if manifest is None else manifest
    allowed_files: set[Path] = set()
    allowed_dirs: set[Path] = set()
    for task, record in manifest["tasks"].items():
        task_path = Path(task)
        allowed_dirs.update(task_path.parents)
        allowed_dirs.add(task_path)
        allowed_files.update(task_path / name for name in record["assets"])
        allowed_files.add(task_path / "checkpoint.json")
    allowed_dirs.discard(Path("."))
    if root.is_symlink() or not root.is_dir():
        raise ValueError("dataset root must be a regular directory")
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"symlink forbidden in dataset root: {relative}")
        if path.is_dir():
            allowed = relative in allowed_dirs
        else:
            allowed = path.is_file() and relative in allowed_files
        if not allowed:
            raise ValueError(f"unexpected path in input-only dataset root: {relative}")


def _download(url: str, destination: Path, record: dict) -> None:
    partial = destination.with_name(destination.name + f".{uuid.uuid4().hex}.partial")
    older = sorted(destination.parent.glob(destination.name + ".*.partial"),
                   key=lambda item: item.stat().st_size, reverse=True)
    offset = 0
    try:
        with partial.open("xb") as output:
            if older:
                if older[0].is_symlink() or older[0].stat().st_size > record["size"]:
                    raise ValueError("invalid retained partial download")
                offset = older[0].stat().st_size
                with older[0].open("rb") as source:
                    shutil.copyfileobj(source, output)
            request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"}) if offset else url
            if offset < record["size"]:
                with urllib.request.urlopen(request) as response:
                    if offset:
                        expected = f"bytes {offset}-{record['size'] - 1}/{record['size']}"
                        if response.status != 206 or response.headers.get("Content-Range") != expected:
                            raise ValueError("server did not honor exact resume range")
                    while block := response.read(8 * 1024 * 1024):
                        output.write(block)
                        if output.tell() > record["size"]:
                            raise ValueError("download exceeds pinned size")
            output.flush()
            os.fsync(output.fileno())
        _verify(partial, record)
        partial.replace(destination)
        for retained in older:
            retained.unlink()
    except BaseException:
        # Partial bytes are retained for explicit later resume and diagnosis.
        raise


def stage(task: str, root: Path) -> Path:
    manifest = load_manifest()
    record = manifest["tasks"][task]
    destination = _safe_task_root(Path(root), task)
    destination.mkdir(parents=True, exist_ok=True)
    state_path = destination / "checkpoint.json"
    state = {"task": task, "dataset_revision": DATASET_REVISION, "status": "staging", "files": {}}

    def save() -> None:
        temporary = state_path.with_name(state_path.name + f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(state_path)

    save()
    try:
        for name, asset in record["assets"].items():
            if name not in _ASSET_NAMES:
                raise ValueError(f"unapproved asset name: {name}")
            path = destination / name
            if path.exists():
                details = _verify(path, asset)
                details["cached"] = True
            else:
                url = (f"https://huggingface.co/datasets/{DATASET_REPOSITORY}/resolve/"
                       f"{DATASET_REVISION}/{urllib.parse.quote(asset['path'], safe='/')}")
                _download(url, path, asset)
                details = {**_verify(path, asset), "cached": False, "url": url}
            state["files"][name] = details
            save()
        state["status"] = "verified"
        state["verification"] = verify(task, root)
        save()
    except BaseException as exc:
        state["status"] = "failed"
        state["error"] = f"{type(exc).__name__}: {exc}"
        save()
        raise
    return destination
