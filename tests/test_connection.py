# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the MCP-to-add-on socket wire protocol."""

__all__ = ()

import importlib.util
import json
import os
import unittest
from typing import cast
from unittest import mock

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_PATH = os.path.join(
    _REPO_DIR, "mcp", "blmcp", "tools_helpers", "connection.py"
)
_SPEC = importlib.util.spec_from_file_location("blmcp_connection_test", _MODULE_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
connection = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(connection)


class _Socket:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = json.dumps(response).encode("utf-8") + b"\0"
        self.sent = bytearray()
        self.timeout: float | None = None
        self.address: tuple[str, int] | None = None

    def __enter__(self) -> "_Socket":
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, address: tuple[str, int]) -> None:
        self.address = address

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, _size: int) -> bytes:
        response = self.response
        self.response = b""
        return response


class TestSendCodeWirePayload(unittest.TestCase):
    def _send(self, sandbox: object = mock.DEFAULT) -> tuple[dict[str, object], _Socket]:
        sock = _Socket({"status": "ok", "result": {"received": True}})
        with mock.patch.object(connection.socket, "socket", return_value=sock):
            if sandbox is mock.DEFAULT:
                response = connection.send_code("result = request_id", False)
            else:
                response = connection.send_code(
                    "result = request_id", False, sandbox=sandbox
                )
        return response, sock

    @staticmethod
    def _decode_request(sock: _Socket) -> dict[str, object]:
        payload = bytes(sock.sent)
        if not payload.endswith(b"\0"):
            raise AssertionError("request is not NUL terminated")
        return cast(
            dict[str, object], json.loads(payload[:-1].decode("utf-8"))
        )

    def test_two_argument_call_emits_sandbox_true_and_preserves_protocol(self) -> None:
        response, sock = self._send()
        request = self._decode_request(sock)

        self.assertEqual(response, {"status": "ok", "result": {"received": True}})
        self.assertEqual(request["sandbox"], True)
        self.assertIs(type(request["sandbox"]), bool)
        self.assertEqual(request["code"], "result = request_id")
        self.assertEqual(request["type"], "execute")
        self.assertEqual(request["strict_json"], False)
        self.assertIs(type(request["strict_json"]), bool)
        self.assertEqual(sock.timeout, connection._TIMEOUT)
        self.assertEqual(sock.address, connection.get_connection_params())

    def test_explicit_sandbox_true_emits_json_true(self) -> None:
        _response, sock = self._send(True)
        request = self._decode_request(sock)

        self.assertEqual(request["sandbox"], True)
        self.assertIs(type(request["sandbox"]), bool)

    def test_explicit_sandbox_false_emits_json_false(self) -> None:
        _response, sock = self._send(False)
        request = self._decode_request(sock)

        self.assertEqual(request["sandbox"], False)
        self.assertIs(type(request["sandbox"]), bool)


if __name__ == "__main__":
    unittest.main()
