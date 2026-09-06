# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Standalone subprocess runner for Python interpreters that provide ``bpy``."""

__all__ = ("main",)

import json
import os
import sys
from typing import Any

_RESULT_PREFIX = "__BLMCP_RESULT__"
_ERROR_PREFIX = "__BLMCP_ERROR__"
_MAX_DIAGNOSTIC_CHARS = 2000
_DIAGNOSTIC_TRUNCATION_MARKER = "\n... <truncated> ...\n"
_MAX_FRAME_TOKEN_CHARS = 256
_FRAME_TOKEN_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)
_FRAME_TOKEN_ENV = "BLENDER_MCP_BPY_RUNNER_FRAME_TOKEN"
_BOOTSTRAP_ERROR = (
    "bpy CLI runner error: missing or invalid runner frame token bootstrap"
)


def _frame_prefix(stem: str, frame_token: str) -> str:
    return "{:s}{:s}__".format(stem, frame_token)


def _bounded_message(message: str) -> str:
    if len(message) <= _MAX_DIAGNOSTIC_CHARS:
        return message
    remaining = _MAX_DIAGNOSTIC_CHARS - len(_DIAGNOSTIC_TRUNCATION_MARKER)
    head_chars = remaining // 2
    tail_chars = remaining - head_chars
    return message[:head_chars] + _DIAGNOSTIC_TRUNCATION_MARKER + message[-tail_chars:]


def _print_error(frame_token: str, error: Exception) -> None:
    message = _bounded_message(str(error))
    print(_frame_prefix(_ERROR_PREFIX, frame_token) + json.dumps(message))


def _is_valid_frame_token(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= _MAX_FRAME_TOKEN_CHARS
        and all(character in _FRAME_TOKEN_ALPHABET for character in value)
    )


def _validated_request(
    raw_input: str, *, expected_frame_token: str
) -> tuple[str, str, str, bool]:
    try:
        request: Any = json.loads(raw_input)
    except json.JSONDecodeError as ex:
        raise ValueError("Invalid JSON input: {:s}".format(str(ex))) from ex

    if not isinstance(request, dict):
        raise TypeError("Runner input must be a JSON object")

    blend_file = request.get("blend_file")
    code = request.get("code")
    frame_token = request.get("frame_token")
    arbitrary_code = request.get("arbitrary_code", False)

    if not isinstance(blend_file, str):
        raise TypeError("`blend_file` must be a string")
    if not isinstance(code, str):
        raise TypeError("`code` must be a string")
    if not isinstance(frame_token, str):
        raise TypeError("`frame_token` must be a string")
    if not frame_token:
        raise ValueError("`frame_token` must not be empty")
    if not _is_valid_frame_token(frame_token):
        raise ValueError("`frame_token` is invalid")
    if frame_token != expected_frame_token:
        raise ValueError("`frame_token` does not match bootstrap token")
    if not isinstance(arbitrary_code, bool):
        raise TypeError("`arbitrary_code` must be a boolean")
    if not os.path.isabs(blend_file):
        raise ValueError("`blend_file` must be an absolute path")
    if not os.path.isfile(blend_file):
        raise ValueError("Blend file does not exist: {!r}".format(blend_file))

    return blend_file, code, frame_token, arbitrary_code


def main() -> int:
    """Read one JSON request from stdin and emit one token-bound JSON frame."""
    frame_token = os.environ.get(_FRAME_TOKEN_ENV)
    if not _is_valid_frame_token(frame_token):
        print(_BOOTSTRAP_ERROR, file=sys.stderr)
        return 2
    assert isinstance(frame_token, str)

    raw_input = sys.stdin.read()
    try:
        blend_file, code, payload_frame_token, arbitrary_code = _validated_request(
            raw_input, expected_frame_token=frame_token
        )

        import bpy  # pylint: disable=import-error,import-outside-toplevel

        bpy.ops.wm.open_mainfile(filepath=blend_file)
        namespace: dict[str, object] = {"result": {}}
        exec(code, namespace)  # noqa: S102  # pylint: disable=exec-used
        result = namespace["result"]
        if not isinstance(result, dict):
            raise TypeError(
                "The `result` variable must be a dict, not {:s}. "
                'Wrap your return value: `result = {{"key": value}}`'.format(
                    type(result).__name__
                )
            )
        if arbitrary_code:
            payload = json.dumps(result, default=repr)
        else:
            payload = json.dumps(result)
        print(_frame_prefix(_RESULT_PREFIX, payload_frame_token) + payload)
    except Exception as ex:  # pylint: disable=broad-exception-caught
        _print_error(frame_token, ex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
