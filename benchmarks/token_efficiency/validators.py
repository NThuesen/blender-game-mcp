# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic benchmark validation outside the bpy process."""

# The explicit Popen lifecycle owns nonblocking pipes and private-group cleanup.
# pylint: disable=consider-using-with,duplicate-code,too-many-lines

__all__ = (
    "run_runtime_inspector",
    "validate_answer_data",
    "validate_artifact_paths",
    "validate_assign_principled_material",
    "validate_batch_edit_save_as",
    "validate_create_exact_cube",
    "validate_data_block_counts",
    "validate_evaluated_mesh_polygons",
    "validate_find_missing_external_files",
    "validate_glb_roundtrip_structure",
    "validate_low_resolution_render",
    "validate_parent_world_transforms",
    "validate_recover_stale_operator",
    "validate_semantics",
)

import hashlib
import json
import os
import select
import selectors
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_INSPECTOR = Path(__file__).resolve().parent / "fixtures" / "inspect_artifact.py"
_TIMEOUT_SECONDS = 120
_MAX_ANSWER_BYTES = 64 * 1024
_MAX_RUNTIME_PAYLOAD_CHARS = 1024 * 1024
_MAX_RUNTIME_CAPTURE_BYTES = 2 * 1024 * 1024
_MAX_DIAGNOSTIC_CHARS = 2000

_EXPECTED_ANSWERS = {
    "data_block_counts": {
        "objects": 6,
        "meshes": 3,
        "materials": 1,
        "images": 1,
        "collections": 2,
    },
    "evaluated_mesh_polygons": {"object": "SubdividedCube", "polygon_count": 96},
    "find_missing_external_files": {"missing": ["//missing/textures/checker.png"]},
}

_EXPECTED_SEMANTICS = {
    "create_exact_cube": {
        "object": {
            "name": "BenchmarkCube",
            "type": "MESH",
            "dimensions": [2.0, 3.0, 4.0],
            "location": [1.0, -2.0, 0.5],
            "rotation_euler": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "vertices": 8,
            "polygons": 6,
        },
        "object_count": 1,
    },
    "assign_principled_material": {
        "object": "MaterialCube",
        "object_record": {
            "name": "MaterialCube",
            "type": "MESH",
            "dimensions": [2.0, 2.0, 2.0],
            "location": [0.0, 0.0, 0.0],
            "rotation_euler": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "vertices": 8,
            "polygons": 6,
        },
        "object_count": 1,
        "assigned_materials": ["BenchmarkPrincipled"],
        "material": {
            "name": "BenchmarkPrincipled",
            "node_type": "BSDF_PRINCIPLED",
            "base_color": [0.12, 0.34, 0.56, 1.0],
            "metallic": 0.25,
            "roughness": 0.4,
            "surface_connected": True,
        },
    },
    "parent_world_transforms": {
        "parent": "BenchmarkParent",
        "child": "BenchmarkChild",
        "child_parent": "BenchmarkParent",
        "parent_type": "EMPTY",
        "child_type": "EMPTY",
        "parent_world_translation": [3.0, 1.0, 2.0],
        "child_world_translation": [-1.0, 4.0, 2.5],
        "object_count": 2,
    },
    "recover_stale_operator": {
        "object": {
            "name": "RecoveredPointLight",
            "type": "LIGHT",
            "location": [2.0, -3.0, 4.0],
            "light_type": "POINT",
            "energy": 750.0,
        },
        "object_count": 1,
    },
    "batch_edit_save_as": {
        "object_count": 4,
        "objects": [
            {
                "name": "Batch_01",
                "type": "MESH",
                "location": [0.0, 0.0, 0.5],
                "vertices": 8,
                "polygons": 6,
            },
            {
                "name": "Batch_02",
                "type": "MESH",
                "location": [2.0, 0.0, 1.0],
                "vertices": 8,
                "polygons": 6,
            },
            {
                "name": "Batch_03",
                "type": "MESH",
                "location": [4.0, 0.0, 1.5],
                "vertices": 8,
                "polygons": 6,
            },
            {
                "name": "Batch_04",
                "type": "MESH",
                "location": [6.0, 0.0, 2.0],
                "vertices": 8,
                "polygons": 6,
            },
        ],
        "filepath_name": "batch_edit.blend",
    },
    "glb_roundtrip_structure": {
        "objects": [
            {
                "name": "RootNode",
                "type": "EMPTY",
                "parent": None,
                "unique_vertex_positions": None,
            },
            {
                "name": "RoundtripCube",
                "type": "MESH",
                "parent": "RootNode",
                "unique_vertex_positions": 8,
            },
            {
                "name": "RoundtripTriangle",
                "type": "MESH",
                "parent": "RootNode",
                "unique_vertex_positions": 3,
            },
        ]
    },
}

_EXPECTED_OUTPUT_NAMES = {
    "data_block_counts": "data_block_counts.json",
    "evaluated_mesh_polygons": "evaluated_mesh_polygons.json",
    "create_exact_cube": "exact_cube.blend",
    "assign_principled_material": "principled_material.blend",
    "parent_world_transforms": "hierarchy.blend",
    "find_missing_external_files": "missing_external_files.json",
    "recover_stale_operator": "recovered_light.blend",
    "batch_edit_save_as": "batch_edit.blend",
    "glb_roundtrip_structure": "roundtrip.glb",
    "low_resolution_render": "verification.png",
}


def _result(errors, details=None):
    bounded_errors = list(errors[:50])
    return {
        "ok": not bounded_errors,
        "errors": bounded_errors,
        "details": details or {},
    }


def _error(code, field, expected, actual, message):
    return {
        "code": code,
        "field": field,
        "expected": _bounded(expected),
        "actual": _bounded(actual),
        "message": message,
    }


def _bounded(value):
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:497] + "..."
    if isinstance(value, list):
        return [_bounded(item) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): _bounded(item) for key, item in list(value.items())[:30]}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded(repr(value))


def _compare_mapping(
    expected: dict[Any, Any], actual: Any, field: str
) -> list[dict[str, Any]]:
    if not isinstance(actual, dict):
        return [
            _error(
                "type_mismatch",
                field,
                "object",
                type(actual).__name__,
                "Expected a JSON object",
            )
        ]
    errors = []
    for key, value in expected.items():
        next_field = f"{field}.{key}"
        if key not in actual:
            errors.append(
                _error(
                    "missing_field",
                    next_field,
                    value,
                    None,
                    "Required field is missing",
                )
            )
        else:
            errors.extend(_compare(value, actual[key], next_field))
    for key in actual.keys() - expected.keys():
        errors.append(
            _error(
                "unexpected_field",
                f"{field}.{key}",
                None,
                actual[key],
                "Unexpected field",
            )
        )
    return errors


def _compare_sequence(
    expected: list[Any], actual: Any, field: str
) -> list[dict[str, Any]]:
    if not isinstance(actual, list):
        return [
            _error(
                "type_mismatch",
                field,
                "array",
                type(actual).__name__,
                "Expected a JSON array",
            )
        ]
    if len(expected) != len(actual):
        return [
            _error(
                "value_mismatch",
                field,
                expected,
                actual,
                "Array length or contents differ",
            )
        ]
    errors = []
    for index, value in enumerate(expected):
        errors.extend(_compare(value, actual[index], f"{field}[{index}]"))
    return errors


# Recursive type dispatch returns the first exact scalar/type outcome; mapping and list checks
# recurse separately.
def _compare(  # pylint: disable=too-many-return-statements
    expected: Any, actual: Any, field: str = "result"
) -> list[dict[str, Any]]:
    if isinstance(expected, dict):
        return _compare_mapping(expected, actual, field)
    if isinstance(expected, list):
        return _compare_sequence(expected, actual, field)
    if isinstance(expected, bool):
        if isinstance(actual, bool) and actual is expected:
            return []
        return [
            _error(
                "type_mismatch",
                field,
                "boolean",
                type(actual).__name__,
                "Expected a boolean",
            )
        ]
    if (
        isinstance(expected, float)
        and isinstance(actual, (int, float))
        and not isinstance(actual, bool)
    ):
        if abs(expected - float(actual)) <= 1.0e-5:
            return []
        return [
            _error("value_mismatch", field, expected, actual, "Numeric value differs")
        ]
    if isinstance(expected, int) and (
        not isinstance(actual, int) or isinstance(actual, bool)
    ):
        return [
            _error(
                "type_mismatch",
                field,
                "integer",
                type(actual).__name__,
                "Expected an integer",
            )
        ]
    if actual != expected or type(actual) is not type(expected):
        return [_error("value_mismatch", field, expected, actual, "Value differs")]
    return []


def validate_answer_data(task_id: str, answer: Any) -> dict[str, Any]:
    """Validate an in-memory answer for a read-only benchmark task."""
    expected = _EXPECTED_ANSWERS.get(task_id)
    if expected is None:
        return _result(
            [
                _error(
                    "unknown_task",
                    "task_id",
                    "known read-only task",
                    task_id,
                    "No answer oracle",
                )
            ]
        )
    return _result(_compare(expected, answer), {"task_id": task_id})


def validate_semantics(task_id: str, semantics: Any) -> dict[str, Any]:
    """Validate runtime-inspected semantics for an artifact task."""
    if task_id == "low_resolution_render":
        if not isinstance(semantics, dict):
            return _result(
                [
                    _error(
                        "type_mismatch",
                        "result",
                        "object",
                        type(semantics).__name__,
                        "Expected image metadata",
                    )
                ]
            )
        expected = {"format": "PNG", "width": 64, "height": 64, "channels": 4}
        structure = {key: semantics.get(key) for key in expected}
        errors = _compare(expected, structure)
        allowed_fields = set(expected) | {"non_background_pixels", "center_rgba"}
        for field in semantics.keys() - allowed_fields:
            errors.append(
                _error(
                    "unexpected_field",
                    f"result.{field}",
                    None,
                    semantics[field],
                    "Unexpected field",
                )
            )
        count = semantics.get("non_background_pixels")
        if not isinstance(count, int) or not 400 <= count <= 2500:
            errors.append(
                _error(
                    "range_mismatch",
                    "result.non_background_pixels",
                    "400..2500",
                    count,
                    "Rendered subject coverage is outside the deterministic range",
                )
            )
        center = semantics.get("center_rgba")
        center_has_valid_type = (
            isinstance(center, list)
            and len(center) == 4
            and all(
                isinstance(channel, int) and not isinstance(channel, bool)
                for channel in center
            )
        )
        if not center_has_valid_type:
            errors.append(
                _error(
                    "type_mismatch",
                    "result.center_rgba",
                    "array of four integers",
                    center,
                    "Center pixel metadata has invalid channel types",
                )
            )
        elif isinstance(center, list) and not (
            center[0] >= 120 and center[0] > center[1] and center[3] >= 250
        ):
            errors.append(
                _error(
                    "pixel_mismatch",
                    "result.center_rgba",
                    "opaque red-dominant pixel",
                    center,
                    "Center pixel does not contain the expected subject",
                )
            )
        return _result(errors, {"task_id": task_id})
    semantic_expected = _EXPECTED_SEMANTICS.get(task_id)
    if semantic_expected is None:
        return _result(
            [
                _error(
                    "unknown_task",
                    "task_id",
                    "known artifact task",
                    task_id,
                    "No semantic oracle",
                )
            ]
        )
    return _result(_compare(semantic_expected, semantics), {"task_id": task_id})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# Early returns keep each filesystem trust-boundary failure isolated and avoid unsafe follow-on I/O.
def validate_artifact_paths(  # pylint: disable=too-many-return-statements
    output_root, output_path, source_path, manifest, expected_output_name=None
):
    """Validate output containment, naming, existence, and source integrity."""
    root = Path(output_root).resolve()
    output = Path(output_path).resolve()
    source = Path(source_path).resolve()
    try:
        output.relative_to(root)
    except ValueError:
        return _result(
            [
                _error(
                    "unsafe_path",
                    "output",
                    "path inside output root",
                    str(output),
                    "Output escapes the permitted root",
                )
            ]
        )
    if output == source:
        return _result(
            [
                _error(
                    "source_overwrite",
                    "output",
                    "separate output path",
                    str(output),
                    "Output must not overwrite the source fixture",
                )
            ]
        )
    try:
        output.relative_to(source.parent)
    except ValueError:
        pass
    else:
        return _result(
            [
                _error(
                    "source_area_output",
                    "output",
                    "path outside source fixture directory",
                    str(output),
                    "Output must not be written inside the source fixture area",
                )
            ]
        )
    if expected_output_name is not None and output.name != expected_output_name:
        return _result(
            [
                _error(
                    "wrong_output_name",
                    "output.name",
                    expected_output_name,
                    output.name,
                    "Output filename does not match the task definition",
                )
            ]
        )
    if not output.is_file():
        return _result(
            [
                _error(
                    "missing_output",
                    "output",
                    "existing file",
                    str(output),
                    "Expected output file does not exist",
                )
            ]
        )
    if not source.is_file():
        return _result(
            [
                _error(
                    "missing_source",
                    "source",
                    "existing fixture",
                    str(source),
                    "Source fixture does not exist",
                )
            ]
        )
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        return _result(
            [
                _error(
                    "manifest_invalid",
                    "manifest.files",
                    "object",
                    type(manifest).__name__,
                    "Manifest has no files object",
                )
            ]
        )
    source_key = source.name
    metadata = manifest.get("files", {}).get(source_key)
    if not isinstance(metadata, dict) or not isinstance(metadata.get("sha256"), str):
        return _result(
            [
                _error(
                    "manifest_missing",
                    "source",
                    "manifest entry",
                    source_key,
                    "Source fixture is not in the manifest",
                )
            ]
        )
    try:
        actual_hash = _sha256(source)
    except OSError as ex:
        return _result(
            [
                _error(
                    "source_read_error",
                    "source",
                    "readable fixture",
                    str(source),
                    str(ex),
                )
            ]
        )
    if actual_hash != metadata.get("sha256"):
        return _result(
            [
                _error(
                    "source_modified",
                    "source.sha256",
                    metadata.get("sha256"),
                    actual_hash,
                    "Source fixture changed",
                )
            ]
        )
    try:
        output_hash = _sha256(output)
    except OSError as ex:
        return _result(
            [
                _error(
                    "output_read_error",
                    "output",
                    "readable artifact",
                    str(output),
                    str(ex),
                )
            ]
        )
    return _result(
        [],
        {
            "output": str(output),
            "output_sha256": output_hash,
            "source_sha256": actual_hash,
        },
    )


class _RuntimeProcessError(OSError):
    """Retain the failing operation and any secondary cleanup failures."""

    def __init__(self, stage, operation, original):
        super().__init__(f"{operation}: {original}")
        self.stage = stage
        self.operation = operation
        self.original = original
        self.cleanup_errors = []
        self.outcome_code: str | None = None


@contextmanager
def _process_stage(stage, operation):
    try:
        yield
    except _RuntimeProcessError:
        raise
    except OSError as ex:
        raise _RuntimeProcessError(stage, operation, ex) from ex


def _cleanup_attempt(errors, operation, action):
    # Even an interrupt during one cleanup action must not skip the others.
    try:
        with _process_stage("cleanup", operation):
            action()
    except BaseException as ex:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        errors.append(ex)


def _report_cleanup(primary, errors):
    if not errors:
        return
    if primary is None:
        primary = errors.pop(0)
        _report_cleanup(primary, errors)
        raise primary
    for error in errors:
        primary.add_note(f"Secondary cleanup failure: {error}")
    if isinstance(primary, _RuntimeProcessError):
        primary.cleanup_errors.extend(errors)


def _terminate_group(process):
    """Signal only the private session created for this invocation."""
    with _process_stage("cleanup", "os.killpg"):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _child_exited(process):
    """Observe exit without reaping, keeping the process-group ID reserved."""
    waitid = getattr(os, "waitid", None)
    if waitid is not None:
        with _process_stage("observer", "os.waitid"):
            return (
                waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                is not None
            )
    # macOS Python does not expose waitid; kqueue observes the unreaped PID.
    with _process_stage("observer", "select.kqueue"):
        queue = select.kqueue()
    primary = None
    try:
        with _process_stage("observer", "select.kevent"):
            event = select.kevent(
                process.pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD,
                fflags=select.KQ_NOTE_EXIT,
            )
        with _process_stage("observer", "kqueue.control"):
            try:
                return bool(queue.control([event], 1, 0))
            except ProcessLookupError:
                return True
    except BaseException as ex:
        primary = ex
        raise
    finally:
        errors = []
        _cleanup_attempt(errors, "kqueue.close", queue.close)
        _report_cleanup(primary, errors)


# One owner keeps deadline, pipe cap, and cleanup on every exit path together.
def _bounded_process(args):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Drain nonblocking POSIX pipes under one deadline, without reader threads."""
    if os.name != "posix" or not (hasattr(os, "waitid") or hasattr(select, "kqueue")):
        raise OSError(
            "Bounded runtime capture requires POSIX waitid/kqueue and process groups"
        )
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    with _process_stage("launch", "subprocess.Popen"):
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            bufsize=0,
        )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    exceeded = False
    timed_out = False
    group_stopped = False
    capture_complete = False
    post_exit_deadline = None
    primary = None
    selector = None
    try:
        with _process_stage("observer", "capture"):
            with _process_stage("observer", "selectors.DefaultSelector"):
                selector = selectors.DefaultSelector()
            for name, stream in (
                ("stdout", process.stdout),
                ("stderr", process.stderr),
            ):
                if stream is None:
                    raise OSError("runtime capture pipes were not created")
                with _process_stage("observer", "os.set_blocking"):
                    os.set_blocking(stream.fileno(), False)
                with _process_stage("observer", "selector.register"):
                    selector.register(stream, selectors.EVENT_READ, name)
            while True:
                # WNOWAIT reserves the leader PID until group cleanup, preventing
                # a recycled PID from targeting an unrelated process group.
                exited = _child_exited(process)
                if exited and not selector.get_map():
                    capture_complete = True
                    break
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    timed_out = True
                    break
                if exited and not group_stopped:
                    if post_exit_deadline is None:
                        post_exit_deadline = time.monotonic() + 0.05
                    elif time.monotonic() >= post_exit_deadline:
                        # Persistent inherited pipes require group termination.
                        _terminate_group(process)
                        group_stopped = True
                with _process_stage("observer", "selector.select"):
                    events = selector.select(min(remaining_time, 0.02))
                for key, _events in events:
                    with _process_stage("observer", "os.read"):
                        try:
                            chunk = os.read(key.fd, 64 * 1024)
                        except BlockingIOError:
                            continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = _MAX_RUNTIME_CAPTURE_BYTES - total
                    accepted = chunk[:remaining]
                    buffers[key.data].extend(accepted)
                    total += len(accepted)
                    if total >= _MAX_RUNTIME_CAPTURE_BYTES:
                        exceeded = True
                        break
                if exceeded:
                    break
    except BaseException as ex:
        primary = ex
        raise
    finally:
        errors = []
        if not group_stopped and not capture_complete:
            _cleanup_attempt(errors, "os.killpg", lambda: _terminate_group(process))
        if selector is not None:
            _cleanup_attempt(errors, "selector.close", selector.close)
        # No blocking read/close or join; direct-child reap has a separate bound.
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            if stream is not None:
                _cleanup_attempt(errors, f"{name}.close", stream.close)
        _cleanup_attempt(errors, "process.wait", lambda: _bounded_reap(process))
        if primary is None and errors and (timed_out or exceeded):
            primary = _RuntimeProcessError(
                "capture",
                "deadline" if timed_out else "output_cap",
                "Artifact inspection timed out"
                if timed_out else "Runtime output exceeded the aggregate capture bound",
            )
            primary.outcome_code = (
                "runtime_timeout" if timed_out else "runtime_output_too_large"
            )
            _report_cleanup(primary, errors)
            raise primary
        _report_cleanup(primary, errors)
    return (
        None if timed_out else process.returncode,
        bytes(buffers["stdout"]),
        bytes(buffers["stderr"]),
        exceeded,
    )


def _bounded_reap(process):
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired as ex:
        raise OSError(
            "Runtime did not reap within the one-second cleanup bound"
        ) from ex


def run_runtime_inspector(  # pylint: disable=too-many-locals,too-many-return-statements
    runtime_executable, artifact, task_id
):
    """Inspect an artifact in a bounded Blender or standalone bpy process."""
    runtime = str(runtime_executable)
    artifact_path = Path(artifact).resolve()
    if not artifact_path.is_file():
        return _result(
            [
                _error(
                    "missing_artifact",
                    "artifact",
                    "existing file",
                    str(artifact_path),
                    "Artifact does not exist",
                )
            ]
        )
    runtime_name = Path(runtime).name.lower()
    args = [runtime]
    if "blender" in runtime_name or ".app/" in runtime.lower():
        args.extend(
            ["--background", "--factory-startup", "--python", str(_INSPECTOR), "--"]
        )
    else:
        args.append(str(_INSPECTOR))
    args.extend(["--task", task_id, "--artifact", str(artifact_path)])
    try:
        returncode, stdout_bytes, stderr_bytes, capture_exceeded = _bounded_process(
            args
        )
    except OSError as ex:
        stage = ex.stage if isinstance(ex, _RuntimeProcessError) else "launch"
        code = f"runtime_{stage}_error"
        details = {}
        if isinstance(ex, _RuntimeProcessError):
            code = ex.outcome_code or code
            details = {
                "stage": stage,
                "operation": ex.operation,
                "errno": getattr(ex.original, "errno", None),
                # Pylint loses the narrowed exception type in this comprehension.
                "cleanup_errors": [str(error) for error in ex.cleanup_errors],  # pylint: disable=no-member
            }
        return _result(
            [
                _error(
                    code,
                    "runtime",
                    "successful runtime lifecycle",
                    runtime,
                    str(ex),
                )
            ],
            details,
        )
    if returncode is None:
        return _result(
            [
                _error(
                    "runtime_timeout",
                    "runtime",
                    "completion within 120 seconds",
                    runtime,
                    "Artifact inspection timed out",
                )
            ]
        )
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if capture_exceeded:
        diagnostic = (stderr or stdout)[-_MAX_DIAGNOSTIC_CHARS:]
        return _result(
            [
                _error(
                    "runtime_output_too_large",
                    "runtime.output",
                    "at most 2 MiB aggregate stdout/stderr",
                    _MAX_RUNTIME_CAPTURE_BYTES,
                    diagnostic or "Runtime output exceeded the aggregate capture bound",
                )
            ]
        )
    marker = "__TOKEN_EFFICIENCY_INSPECT__"
    payload_lines = [
        line[len(marker) :] for line in stdout.splitlines() if line.startswith(marker)
    ]
    payload_line = payload_lines[-1] if payload_lines else None
    if returncode != 0 or payload_line is None:
        diagnostic = (stderr or stdout)[-_MAX_DIAGNOSTIC_CHARS:]
        return _result(
            [
                _error(
                    "runtime_error",
                    "runtime",
                    "exit 0 with inspector payload",
                    returncode,
                    diagnostic,
                )
            ]
        )
    if len(payload_line) > _MAX_RUNTIME_PAYLOAD_CHARS:
        return _result(
            [
                _error(
                    "runtime_result_too_large",
                    "runtime.stdout",
                    "at most 1 MiB",
                    len(payload_line),
                    "Inspector payload exceeds the validation bound",
                )
            ]
        )
    try:
        payload = json.loads(payload_line)
    except ValueError as ex:
        return _result(
            [
                _error(
                    "malformed_runtime_result",
                    "runtime.stdout",
                    "valid JSON payload",
                    payload_line[:200],
                    str(ex),
                )
            ]
        )
    if not isinstance(payload, dict):
        return _result(
            [
                _error(
                    "malformed_runtime_result",
                    "runtime.stdout",
                    "JSON object",
                    type(payload).__name__,
                    "Inspector payload is not an object",
                )
            ]
        )
    if not payload.get("ok"):
        return _result(
            [
                _error(
                    "inspection_error",
                    "artifact",
                    "inspectable artifact",
                    payload.get("error"),
                    "Runtime could not inspect artifact",
                )
            ]
        )
    return _result([], {"data": payload.get("data"), "runtime": runtime})


def _load_manifest(fixture_root):
    try:
        value = json.loads((Path(fixture_root) / "manifest.json").read_text("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("manifest root must be an object")
        return value
    except (OSError, TypeError, json.JSONDecodeError) as ex:
        return None, _result(
            [
                _error(
                    "manifest_error",
                    "manifest",
                    "readable JSON manifest",
                    None,
                    str(ex),
                )
            ]
        )


def _validate_answer_file(task_id, fixture_root, output_root, answer_path):
    manifest_data = _load_manifest(fixture_root)
    if isinstance(manifest_data, tuple):
        return manifest_data[1]
    source = Path(fixture_root) / "scene.blend"
    source_check = validate_artifact_paths(
        output_root,
        answer_path,
        source,
        manifest_data,
        _EXPECTED_OUTPUT_NAMES[task_id],
    )
    if not source_check["ok"]:
        return source_check
    try:
        answer_file = Path(answer_path)
        if answer_file.stat().st_size > _MAX_ANSWER_BYTES:
            return _result(
                [
                    _error(
                        "result_too_large",
                        "answer",
                        "at most 64 KiB",
                        answer_file.stat().st_size,
                        "Answer exceeds the validation bound",
                    )
                ]
            )
        answer_text = answer_file.read_text("utf-8")
        try:
            answer = json.loads(answer_text)
        except ValueError as ex:
            return _result(
                [_error("malformed_result", "answer", "JSON object", None, str(ex))]
            )
        pending = [(answer, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 64:
                raise RecursionError("Answer nesting exceeds 64 levels")
            if isinstance(value, dict):
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
    except (OSError, UnicodeError, RecursionError) as ex:
        return _result(
            [_error("malformed_result", "answer", "JSON object", None, str(ex))]
        )
    result = validate_answer_data(task_id, answer)
    if result["ok"]:
        result["details"].update(
            {"source_sha256": source_check["details"]["source_sha256"]}
        )
    return result


# These six values are the complete artifact-validation boundary and are clearer than a loose
# kwargs mapping.
def _validate_artifact(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    task_id, fixture_root, output_root, output_path, source_name, runtime_executable
):
    manifest_data = _load_manifest(fixture_root)
    if isinstance(manifest_data, tuple):
        return manifest_data[1]
    source = Path(fixture_root) / source_name
    paths = validate_artifact_paths(
        output_root,
        output_path,
        source,
        manifest_data,
        _EXPECTED_OUTPUT_NAMES[task_id],
    )
    if not paths["ok"]:
        return paths
    if (
        Path(output_path).suffix == ".blend"
        and paths["details"]["output_sha256"] == paths["details"]["source_sha256"]
    ):
        return _result(
            [
                _error(
                    "output_not_distinct",
                    "output.sha256",
                    "different from source",
                    paths["details"]["output_sha256"],
                    "Edited blend output is byte-identical to its source",
                )
            ]
        )
    inspected = run_runtime_inspector(runtime_executable, output_path, task_id)
    if not inspected["ok"]:
        return inspected
    return validate_semantics(task_id, inspected["details"]["data"])


def validate_data_block_counts(fixture_root, output_root, answer_path, **_kwargs):
    """Validate the data-block count answer file."""
    return _validate_answer_file(
        "data_block_counts", fixture_root, output_root, answer_path
    )


def validate_evaluated_mesh_polygons(fixture_root, output_root, answer_path, **_kwargs):
    """Validate the evaluated mesh polygon answer file."""
    return _validate_answer_file(
        "evaluated_mesh_polygons", fixture_root, output_root, answer_path
    )


def validate_find_missing_external_files(
    fixture_root, output_root, answer_path, **_kwargs
):
    """Validate the missing external files answer file."""
    return _validate_answer_file(
        "find_missing_external_files", fixture_root, output_root, answer_path
    )


def validate_create_exact_cube(
    fixture_root, output_root, output_path, runtime_executable, **_kwargs
):
    """Validate the exact cube artifact."""
    return _validate_artifact(
        "create_exact_cube",
        fixture_root,
        output_root,
        output_path,
        "mutation_source.blend",
        runtime_executable,
    )


def validate_assign_principled_material(
    fixture_root, output_root, output_path, runtime_executable, **_kwargs
):
    """Validate the Principled material artifact."""
    return _validate_artifact(
        "assign_principled_material",
        fixture_root,
        output_root,
        output_path,
        "mutation_source.blend",
        runtime_executable,
    )


def validate_parent_world_transforms(
    fixture_root, output_root, output_path, runtime_executable, **_kwargs
):
    """Validate parent and child world transforms."""
    return _validate_artifact(
        "parent_world_transforms",
        fixture_root,
        output_root,
        output_path,
        "mutation_source.blend",
        runtime_executable,
    )


def validate_recover_stale_operator(
    fixture_root, output_root, output_path, runtime_executable, **_kwargs
):
    """Validate the stale-operator recovery artifact."""
    return _validate_artifact(
        "recover_stale_operator",
        fixture_root,
        output_root,
        output_path,
        "mutation_source.blend",
        runtime_executable,
    )


def validate_batch_edit_save_as(
    fixture_root, output_root, output_path, runtime_executable, **_kwargs
):
    """Validate the batch edit and save-as artifact."""
    return _validate_artifact(
        "batch_edit_save_as",
        fixture_root,
        output_root,
        output_path,
        "batch_source.blend",
        runtime_executable,
    )


def validate_glb_roundtrip_structure(
    fixture_root, output_root, output_path, runtime_executable, **_kwargs
):
    """Validate the freshly re-imported GLB structure."""
    return _validate_artifact(
        "glb_roundtrip_structure",
        fixture_root,
        output_root,
        output_path,
        "roundtrip_source.blend",
        runtime_executable,
    )


def validate_low_resolution_render(
    fixture_root, output_root, output_path, runtime_executable, **_kwargs
):
    """Validate the rendered PNG structure and bounded pixels."""
    return _validate_artifact(
        "low_resolution_render",
        fixture_root,
        output_root,
        output_path,
        "render_source.blend",
        runtime_executable,
    )
