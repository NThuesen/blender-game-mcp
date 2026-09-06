# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Deferred response handling for Blender background jobs.

When tool-code starts a background job (e.g. rendering with
``INVOKE_DEFAULT``), the response cannot be sent immediately. This
module holds the client connection open and polls a checker callable
until the operation completes, then sends the result.

The checker (``check_fn``) is called on the server's standard timer
which starts at an active interval but backs off when idle.

Checkers should be lightweight (e.g. check a flag or file existence)
so they don't block the UI, yet return promptly so the user is not left waiting after the job finishes.

"""

__all__ = (
    "add",
    "close_all",
    "has_pending",
    "poll",
)

import json
import socket
import time
import traceback
from collections.abc import Callable

from .mcp_to_blender_server import _encode_response, _send_buffer_nonblocking

# Total wall-time in seconds allowed for a background task (e.g. rendering) to complete.
# When exceeded, an error response is sent and the connection is closed.
# The background task itself continues to run in Blender.
# One hour is long for what should typically be an interactive experience,
# but renders can take a long time and it's not desirable for them to simply give up.
_DEFERRED_TIMEOUT = (60.0 * 60.0)


class _DeferredClient:
    """
    A client connection waiting for a background job to complete.
    """

    __slots__ = (
        "conn",
        "check_fn",
        "strict_json",
        "stdout",
        "stderr",
        "deadline",
        "response_buffer",
    )

    def __init__(
        self,
        conn: socket.socket,
        check_fn: Callable[[], dict[str, object] | None],
        strict_json: bool,
        stdout: str,
        stderr: str,
    ) -> None:
        self.conn: socket.socket = conn
        self.check_fn: Callable[[], dict[str, object] | None] = check_fn
        self.strict_json: bool = strict_json
        self.stdout: str = stdout
        self.stderr: str = stderr
        self.deadline: float = time.monotonic() + _DEFERRED_TIMEOUT
        self.response_buffer: bytearray | None = None


# Connections waiting for a background job to finish, polled each timer tick.
_deferred_clients: list[_DeferredClient] = []


def _close(dc: _DeferredClient) -> None:
    try:
        dc.conn.close()
    except OSError:
        pass
    try:
        _deferred_clients.remove(dc)
    except ValueError:
        pass


def _queue_response(dc: _DeferredClient, response: dict[str, object]) -> None:
    dc.response_buffer = bytearray(_encode_response(response))
    dc.deadline = time.monotonic() + _DEFERRED_TIMEOUT


def _flush_response(dc: _DeferredClient) -> None:
    response_buffer = dc.response_buffer
    assert response_buffer is not None
    size_before = len(response_buffer)
    try:
        if _send_buffer_nonblocking(dc.conn, response_buffer):
            _close(dc)
        elif len(response_buffer) < size_before:
            # Bound only inactivity: a reader making progress may take as long
            # as needed to receive a large response.
            dc.deadline = time.monotonic() + _DEFERRED_TIMEOUT
    except OSError:
        _close(dc)


def _is_disconnected(conn: socket.socket) -> bool:
    """
    Return ``True`` if the remote end has closed the connection.
    """
    try:
        data = conn.recv(1, socket.MSG_PEEK)
        # Empty data means the peer closed the connection.
        return len(data) == 0
    except BlockingIOError:
        # No data available - connection is still alive.
        return False
    except OSError:
        return True


def add(
    conn: socket.socket,
    check_fn: Callable[[], dict[str, object] | None],
    strict_json: bool,
    stdout: str,
    stderr: str,
) -> None:
    """
    Register a deferred client to be polled for completion.
    """
    _deferred_clients.append(_DeferredClient(
        conn, check_fn, strict_json, stdout, stderr))


def poll() -> bool:
    """
    Check all deferred clients for completion.

    Return ``True`` if at least one client was resolved, written, or removed.
    """
    did_work = False
    for dc in _deferred_clients[:]:
        # Check for client disconnection before polling or writing.
        if _is_disconnected(dc.conn):
            _close(dc)
            did_work = True
            continue

        if dc.response_buffer is not None:
            if time.monotonic() > dc.deadline:
                # The peer stopped accepting response bytes.
                _close(dc)
            else:
                _flush_response(dc)
            did_work = True
            continue

        response: dict[str, object] | None = None

        # Check for background-operation timeout.
        if time.monotonic() > dc.deadline:
            response = {
                "status": "error",
                "message": "Deferred operation timed out after {:.0f} seconds".format(_DEFERRED_TIMEOUT),
            }
        else:
            # Call the checker.
            try:
                result = dc.check_fn()
            except Exception:  # pylint: disable=broad-exception-caught
                response = {
                    "status": "error",
                    "message": traceback.format_exc(),
                }
            else:
                if result is None:
                    # Still pending.
                    continue

                if not isinstance(result, dict):
                    response = {
                        "status": "error",
                        "message": "check_is_finished must return None or dict, not {:s}".format(
                            type(result).__name__,
                        ),
                    }
                else:
                    # Validate JSON serializability when strict_json is set.
                    if dc.strict_json:
                        try:
                            json.dumps(result)
                        except (TypeError, ValueError) as ex:
                            response = {
                                "status": "error",
                                "message": "Deferred result is not JSON-serializable: {:s}".format(str(ex)),
                            }

                    if response is None:
                        # Build the final response with the standard envelope.
                        response = {"status": "ok", "result": result}
                        if dc.stdout:
                            response["stdout"] = dc.stdout
                        if dc.stderr:
                            response["stderr"] = dc.stderr

        assert response is not None
        _queue_response(dc, response)
        _flush_response(dc)
        did_work = True

    return did_work


def has_pending() -> bool:
    """
    Return ``True`` if there are deferred clients awaiting completion.
    """
    return bool(_deferred_clients)


def close_all() -> None:
    """
    Close all deferred client connections without sending responses.
    """
    for dc in _deferred_clients:
        try:
            dc.conn.close()
        except OSError:
            pass
    _deferred_clients.clear()
