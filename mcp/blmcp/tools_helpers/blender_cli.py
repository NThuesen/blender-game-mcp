# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Run tool-code via ``blender --background``.
"""

__all__ = (
    "run_blender_cli",
    "synced_blend_for_cli",
)

import contextlib
import json
import logging
import os
import secrets
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from typing import Literal

from blmcp.tools_helpers.connection import send_code

_log = logging.getLogger(__name__)

_RESULT_PREFIX = "__BLMCP_RESULT__"
_ERROR_PREFIX = "__BLMCP_ERROR__"
_CLI_TIMEOUT = 120.0
_MAX_DIAGNOSTIC_STREAM_CHARS = 2000
_DIAGNOSTIC_TRUNCATION_MARKER = "\n... <truncated> ...\n"
_MAX_NUMBERED_PATHS = 10000
_CLI_BACKENDS = ("blender", "bpy")


@dataclass(frozen=True)
class _CLIBackendConfig:
    backend: Literal["blender", "bpy"]
    executable: str


def _resolve_cli_backend() -> _CLIBackendConfig:
    backend = os.environ.get("BLENDER_MCP_CLI_BACKEND", "blender")
    if backend == "blender":
        return _CLIBackendConfig(
            backend="blender",
            executable=os.environ.get("BLENDER_PATH", "blender"),
        )
    if backend == "bpy":
        executable = os.environ.get("BLENDER_MCP_BPY_PYTHON")
        if not executable:
            raise ValueError(
                "BLENDER_MCP_BPY_PYTHON is required when BLENDER_MCP_CLI_BACKEND=bpy"
            )
        return _CLIBackendConfig(backend="bpy", executable=executable)
    raise ValueError(
        "Unknown BLENDER_MCP_CLI_BACKEND {!r}; expected one of: {:s}".format(
            backend, ", ".join(_CLI_BACKENDS)
        )
    )


def _new_frame_token() -> str:
    """Return a high-entropy token binding one wrapper to its parser."""
    return secrets.token_urlsafe(32)


def _frame_prefix(stem: str, frame_token: str) -> str:
    """Build a token-bound frame prefix while preserving the protocol stem."""
    return "{:s}{:s}__".format(stem, frame_token)


def _build_cli_wrapper(code: str, *, frame_token: str, arbitrary_code: bool) -> str:
    """Wrap *code* in the shared CLI result framing protocol."""
    result_dumps = (
        "json.dumps(_result, default=repr)" if arbitrary_code else "json.dumps(_result)"
    )
    return (
        "import json\n"
        "try:\n"
        "    _ns = {{'result': {{}}}}\n"
        "    exec({!r}, _ns)\n"
        "    _result = _ns['result']\n"
        "    if not isinstance(_result, dict):\n"
        "        raise TypeError(\n"
        "            'The `result` variable must be a dict, not ' +\n"
        "            type(_result).__name__ +\n"
        "            '. Wrap your return value: `result = {{\"key\": value}}`')\n"
        '    print("{:s}" + {:s})\n'
        "except Exception as ex:\n"
        '    print("{:s}" + json.dumps(str(ex)))\n'
    ).format(
        code,
        _frame_prefix(_RESULT_PREFIX, frame_token),
        result_dumps,
        _frame_prefix(_ERROR_PREFIX, frame_token),
    )


def _bounded_diagnostic_stream(stream: str) -> str:
    """Bound a captured stream while retaining its beginning and end."""
    if not stream:
        return "<empty>"
    if len(stream) <= _MAX_DIAGNOSTIC_STREAM_CHARS:
        return stream

    remaining = _MAX_DIAGNOSTIC_STREAM_CHARS - len(_DIAGNOSTIC_TRUNCATION_MARKER)
    head_chars = remaining // 2
    tail_chars = remaining - head_chars
    return stream[:head_chars] + _DIAGNOSTIC_TRUNCATION_MARKER + stream[-tail_chars:]


def _decode_diagnostic_stream(stream: str | bytes) -> str:
    """Decode subprocess diagnostics consistently and without failure."""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def _parse_cli_output(
    stdout: str,
    stderr: str,
    returncode: int,
    *,
    frame_token: str,
) -> dict[str, object]:
    """Parse the newest frame bound to *frame_token*.

    Generic and wrong-token markers are stdout noise. Once a token-bound frame
    is found, it is authoritative even when its payload is malformed.
    """
    result_prefix = _frame_prefix(_RESULT_PREFIX, frame_token)
    error_prefix = _frame_prefix(_ERROR_PREFIX, frame_token)
    for line in reversed(stdout.splitlines()):
        if line.startswith(result_prefix):
            payload = line[len(result_prefix) :]
            try:
                result = json.loads(payload)
            except json.JSONDecodeError as ex:
                raise RuntimeError(
                    "Malformed Blender CLI protocol frame: {:s}".format(
                        _bounded_diagnostic_stream(payload)
                    )
                ) from ex
            if not isinstance(result, dict):
                raise TypeError(
                    "Expected dict from Blender CLI, got {!r}".format(type(result))
                )
            return result
        if line.startswith(error_prefix):
            payload = line[len(error_prefix) :]
            try:
                error = json.loads(payload)
            except json.JSONDecodeError as ex:
                raise RuntimeError(
                    "Malformed Blender CLI protocol frame: {:s}".format(
                        _bounded_diagnostic_stream(payload)
                    )
                ) from ex
            if not isinstance(error, str):
                raise RuntimeError(
                    "Blender CLI error payload must be a string, got {:s}".format(
                        type(error).__name__
                    )
                )
            raise RuntimeError(
                "Blender error: {:s}".format(_bounded_diagnostic_stream(error))
            )

    raise RuntimeError(
        "No result marker in Blender output (exit code {:d}).\n"
        "stdout: {:s}\nstderr: {:s}".format(
            returncode,
            _bounded_diagnostic_stream(stdout),
            _bounded_diagnostic_stream(stderr),
        )
    )


def run_blender_cli(
    blend_file: str,
    code: str,
    timeout: float = _CLI_TIMEOUT,
    *,
    arbitrary_code: bool = False,
) -> dict[str, object]:
    """
    Run Python code inside ``blender --background``.

    *blend_file* is the path to the ``.blend`` file to open.
    *code* is executed via ``exec()`` and should assign to ``result``.
    *arbitrary_code* enables ``repr`` fallback for non-JSON values and must
    only be used for model-generated code. Repository-owned code is strict.

    Returns the JSON-de-serialized ``result`` value.
    """
    config = _resolve_cli_backend()
    if config.backend != "blender":
        raise RuntimeError("The bpy CLI backend runner is not implemented")
    blender = config.executable

    frame_token = _new_frame_token()
    wrapper = _build_cli_wrapper(
        code, frame_token=frame_token, arbitrary_code=arbitrary_code
    )

    try:
        proc = subprocess.run(
            [blender, "--background", blend_file, "--python-expr", wrapper],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as ex:
        message = "Blender CLI timed out after {:g}s".format(timeout)
        if ex.stdout is not None:
            message += "\nstdout: {:s}".format(
                _bounded_diagnostic_stream(_decode_diagnostic_stream(ex.stdout))
            )
        if ex.stderr is not None:
            message += "\nstderr: {:s}".format(
                _bounded_diagnostic_stream(_decode_diagnostic_stream(ex.stderr))
            )
        raise RuntimeError(message) from ex
    except FileNotFoundError as ex:
        raise RuntimeError(
            "Blender executable not found at '{:s}'. "
            "Set the BLENDER_PATH environment variable to the correct path.".format(
                blender
            )
        ) from ex

    return _parse_cli_output(
        proc.stdout, proc.stderr, proc.returncode, frame_token=frame_token
    )


def _numbered_blend_path(filepath: str) -> str:
    """
    Return an unused path like ``/path/to/file_mcp_0001.blend``.
    """
    base, ext = os.path.splitext(filepath)
    for i in range(1, _MAX_NUMBERED_PATHS):
        candidate = "{:s}_mcp_{:04d}{:s}".format(base, i, ext)
        if not os.path.exists(candidate):
            return candidate
    raise RuntimeError(
        "Could not find an unused numbered path for '{:s}'".format(filepath)
    )


@contextlib.contextmanager
def synced_blend_for_cli(blend_file: str) -> Generator[str, None, None]:
    """
    Context manager that ensures *blend_file* reflects unsaved changes.

    When a running Blender instance has the same file open with unsaved
    modifications, a numbered copy is saved and yielded. The copy is
    deleted on exit. When no instance is reachable or the file is clean,
    *blend_file* is yielded unchanged.
    """
    temp_path: str | None = None
    try:
        try:
            response = send_code(
                "import bpy, os\n"
                'result = {"is_dirty": bpy.data.is_dirty, "filepath": bpy.data.filepath}\n',
                strict_json=True,
            )
        except ConnectionError:
            # No running Blender instance, use the on-disk file as-is.
            yield blend_file
            return

        if response.get("status") != "ok":
            raise RuntimeError(str(response.get("message", "Unknown error")))
        result = response["result"]
        assert isinstance(result, dict)
        is_dirty = result.get("is_dirty", False)
        blender_filepath = str(result.get("filepath", ""))

        # Compare normalized paths to see if this is the same file.
        if not blender_filepath or os.path.realpath(blend_file) != os.path.realpath(
            blender_filepath
        ):
            yield blend_file
            return

        if not is_dirty:
            yield blend_file
            return

        # Dirty file, save a numbered copy.
        temp_path = _numbered_blend_path(blend_file)
        save_response = send_code(
            "import bpy\n"
            "bpy.ops.wm.save_as_mainfile(filepath={!r}, copy=True)\n".format(temp_path),
            strict_json=True,
        )
        if save_response.get("status") != "ok":
            raise RuntimeError(str(save_response.get("message", "Unknown error")))
        yield temp_path
    finally:
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except OSError as ex:
                _log.warning(
                    "Failed to remove temporary file '{:s}': {:s}".format(
                        temp_path, str(ex)
                    )
                )
