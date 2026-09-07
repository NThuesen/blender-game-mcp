# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for interactive server response writes."""

__all__ = ()

import importlib.util
import json
import os
import sys
import types
import unittest
from unittest import mock

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_PATH = os.path.join(
    _REPO_DIR,
    "addon",
    "blender_mcp_addon",
    "mcp_to_blender_server.py",
)
_PACKAGE_NAME = "blender_mcp_addon_test"
_PACKAGE = types.ModuleType(_PACKAGE_NAME)
_PACKAGE.__path__ = [os.path.dirname(_MODULE_PATH)]
sys.modules[_PACKAGE_NAME] = _PACKAGE
_SPEC = importlib.util.spec_from_file_location(
    _PACKAGE_NAME + ".mcp_to_blender_server",
    _MODULE_PATH,
)
assert _SPEC is not None
assert _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = server
_SPEC.loader.exec_module(server)

_DEFERRED_MODULE_PATH = os.path.join(
    os.path.dirname(_MODULE_PATH), "deferred_tool.py")
_DEFERRED_SPEC = importlib.util.spec_from_file_location(
    _PACKAGE_NAME + ".deferred_tool",
    _DEFERRED_MODULE_PATH,
)
assert _DEFERRED_SPEC is not None
assert _DEFERRED_SPEC.loader is not None
deferred_tool = importlib.util.module_from_spec(_DEFERRED_SPEC)
sys.modules[_DEFERRED_SPEC.name] = deferred_tool
_DEFERRED_SPEC.loader.exec_module(deferred_tool)


class _PartialSendSocket:
    def __init__(
        self,
        limit: int,
        block_once: bool = False,
        incoming: bytes = b"",
        always_block: bool = False,
        disconnected: bool = False,
    ) -> None:
        self.limit = limit
        self.block_once = block_once
        self.incoming = incoming
        self.always_block = always_block
        self.disconnected = disconnected
        self.sent = bytearray()
        self.closed = False

    def recv(self, _size: int, _flags: int = 0) -> bytes:
        if self.disconnected:
            return b""
        incoming = self.incoming
        self.incoming = b""
        if not incoming:
            raise BlockingIOError
        return incoming

    def send(self, data: bytes | bytearray) -> int:
        if self.always_block:
            raise BlockingIOError
        if self.block_once:
            self.block_once = False
            raise BlockingIOError
        size = min(self.limit, len(data))
        self.sent.extend(data[:size])
        return size

    def sendall(self, data: bytes | bytearray) -> None:
        """Model the truncation caused by sendall on a non-blocking socket."""
        self.sent.extend(data[:self.limit])
        raise BlockingIOError

    def close(self) -> None:
        self.closed = True


class TestExecuteRequestSandbox(unittest.TestCase):
    _CODE = "result = {'marker': 'unchanged'}"

    @staticmethod
    def _request(*, sandbox: object = mock.DEFAULT) -> bytes:
        payload: dict[str, object] = {
            "type": "execute",
            "code": TestExecuteRequestSandbox._CODE,
            "strict_json": True,
        }
        if sandbox is not mock.DEFAULT:
            payload["sandbox"] = sandbox
        return json.dumps(payload).encode("utf-8")

    def test_explicit_sandbox_false_bypasses_weak_sandbox(self) -> None:
        request = self._request(sandbox=False)
        expected = server._ExecResult({"status": "ok", "result": {}})

        with mock.patch.object(
            server, "_execute_code", return_value=expected
        ) as execute:
            result, strict_json = server._execute_code_from_request(request)

        self.assertIs(result, expected)
        self.assertTrue(strict_json)
        execute.assert_called_once_with(
            self._CODE, strict_json=True, use_weak_sandbox=False
        )

    def test_omitted_sandbox_preserves_weak_sandbox_default(self) -> None:
        request = self._request()
        expected = server._ExecResult({"status": "ok", "result": {}})

        with mock.patch.object(
            server, "_execute_code", return_value=expected
        ) as execute:
            server._execute_code_from_request(request)

        execute.assert_called_once_with(
            self._CODE, strict_json=True, use_weak_sandbox=True
        )

    def test_explicit_sandbox_true_applies_weak_sandbox(self) -> None:
        expected = server._ExecResult({"status": "ok", "result": {}})

        with mock.patch.object(
            server, "_execute_code", return_value=expected
        ) as execute:
            result, strict_json = server._execute_code_from_request(
                self._request(sandbox=True)
            )

        self.assertIs(result, expected)
        self.assertTrue(strict_json)
        execute.assert_called_once_with(
            self._CODE, strict_json=True, use_weak_sandbox=True
        )

    def test_sandbox_false_does_not_change_the_next_request_default(self) -> None:
        expected = server._ExecResult({"status": "ok", "result": {}})

        with mock.patch.object(
            server, "_execute_code", return_value=expected
        ) as execute:
            server._execute_code_from_request(self._request(sandbox=False))
            server._execute_code_from_request(self._request())

        self.assertEqual(
            execute.call_args_list,
            [
                mock.call(
                    self._CODE, strict_json=True, use_weak_sandbox=False
                ),
                mock.call(
                    self._CODE, strict_json=True, use_weak_sandbox=True
                ),
            ],
        )

    def test_invalid_sandbox_values_are_rejected_without_execution(self) -> None:
        invalid_values: tuple[object, ...] = (
            None, 0, 1, "false", [], {}, [False], {"value": True}
        )
        expected_response = {
            "status": "error",
            "message": "Internal error: 'sandbox' must be a boolean",
        }

        for value in invalid_values:
            with self.subTest(value=value), mock.patch.object(
                server, "_execute_code"
            ) as execute:
                result, strict_json = server._execute_code_from_request(
                    self._request(sandbox=value)
                )

                self.assertEqual(result.response, expected_response)
                self.assertTrue(strict_json)
                execute.assert_not_called()


class TestNonBlockingResponseWrite(unittest.TestCase):
    def test_partial_writes_are_retained_until_complete(self) -> None:
        sock = _PartialSendSocket(limit=7)
        pending = bytearray(b"response larger than one socket write")
        expected = bytes(pending)

        complete = False
        while not complete:
            complete = server._send_buffer_nonblocking(sock, pending)

        self.assertEqual(bytes(sock.sent), expected)
        self.assertEqual(pending, b"")

    def test_blocked_write_is_retried_without_losing_data(self) -> None:
        sock = _PartialSendSocket(limit=1024, block_once=True)
        pending = bytearray(b"complete response")

        self.assertFalse(server._send_buffer_nonblocking(sock, pending))
        self.assertEqual(pending, b"complete response")
        self.assertTrue(server._send_buffer_nonblocking(sock, pending))
        self.assertEqual(bytes(sock.sent), b"complete response")

    def test_interactive_server_flushes_large_response_before_closing(self) -> None:
        sock = _PartialSendSocket(limit=97, incoming=b"request\0")
        client = server._Client(sock)
        response = {"status": "ok", "result": {"image_base64": "A" * 32_768}}
        expected = server._encode_response(response)
        server._state.clients.append(client)
        self.addCleanup(server._state.clients.clear)

        with mock.patch.object(
            server,
            "_execute_code_from_request",
            return_value=(server._ExecResult(response), True),
        ):
            for _index in range(1000):
                server._service_clients()
                if not server._state.clients:
                    break

        self.assertFalse(server._state.clients)
        self.assertTrue(sock.closed)
        self.assertEqual(bytes(sock.sent), expected)

    def test_interactive_server_retries_blocked_response_before_closing(self) -> None:
        sock = _PartialSendSocket(
            limit=1024, block_once=True, incoming=b"request\0")
        client = server._Client(sock)
        response = {"status": "ok", "result": {"message": "complete response"}}
        expected = server._encode_response(response)
        server._state.clients.append(client)
        self.addCleanup(server._state.clients.clear)

        with mock.patch.object(
            server,
            "_execute_code_from_request",
            return_value=(server._ExecResult(response), True),
        ):
            server._service_clients()

        self.assertIn(client, server._state.clients)
        self.assertFalse(sock.closed)
        self.assertEqual(sock.sent, b"")

        server._service_clients()

        self.assertNotIn(client, server._state.clients)
        self.assertTrue(sock.closed)
        self.assertEqual(bytes(sock.sent), expected)

    def test_interactive_server_flushes_timeout_error_before_closing(self) -> None:
        sock = _PartialSendSocket(limit=1024)
        client = server._Client(sock)
        client.timeout = 1
        expected = server._encode_response(
            {"status": "error", "message": "Client timed out"})
        server._state.clients.append(client)
        self.addCleanup(server._state.clients.clear)

        server._service_clients()

        self.assertIn(client, server._state.clients)
        self.assertFalse(sock.closed)
        self.assertEqual(sock.sent, b"")

        server._service_clients()

        self.assertNotIn(client, server._state.clients)
        self.assertTrue(sock.closed)
        self.assertEqual(bytes(sock.sent), expected)

    def test_interactive_server_evicts_peer_that_never_accepts_response(self) -> None:
        sock = _PartialSendSocket(limit=1024, always_block=True)
        client = server._Client(sock)
        client.response_buffer = bytearray(b"pending response")
        client.timeout = 1
        server._state.clients.append(client)
        self.addCleanup(server._state.clients.clear)

        server._service_clients()

        self.assertNotIn(client, server._state.clients)
        self.assertTrue(sock.closed)
        self.assertEqual(sock.sent, b"")


class TestDeferredResponseWrite(unittest.TestCase):
    def setUp(self) -> None:
        deferred_tool.close_all()

    def tearDown(self) -> None:
        deferred_tool.close_all()

    def test_success_partial_writes_are_retried_before_close(self) -> None:
        sock = _PartialSendSocket(limit=7)
        result = {"image_base64": "A" * 128}
        expected = server._encode_response({"status": "ok", "result": result})
        deferred_tool.add(sock, lambda: result, True, "", "")

        deferred_tool.poll()

        self.assertTrue(deferred_tool.has_pending())
        self.assertFalse(sock.closed)
        self.assertEqual(bytes(sock.sent), expected[:7])

        for _index in range(100):
            deferred_tool.poll()
            if not deferred_tool.has_pending():
                break

        self.assertFalse(deferred_tool.has_pending())
        self.assertTrue(sock.closed)
        self.assertEqual(bytes(sock.sent), expected)

    def test_error_blocking_write_is_retried_before_close(self) -> None:
        sock = _PartialSendSocket(limit=1024, block_once=True)

        def check_error() -> dict[str, object] | None:
            raise RuntimeError("checker failed")

        deferred_tool.add(sock, check_error, True, "", "")

        deferred_tool.poll()

        self.assertTrue(deferred_tool.has_pending())
        self.assertFalse(sock.closed)
        self.assertEqual(sock.sent, b"")

        deferred_tool.poll()

        self.assertFalse(deferred_tool.has_pending())
        self.assertTrue(sock.closed)
        response = json.loads(bytes(sock.sent[:-1]))
        self.assertEqual(response["status"], "error")
        self.assertIn("RuntimeError: checker failed", response["message"])

    def test_stalled_response_writer_is_evicted_after_deadline(self) -> None:
        sock = _PartialSendSocket(limit=1024, always_block=True)
        deferred_tool.add(sock, lambda: {"done": True}, True, "", "")

        deferred_tool.poll()

        self.assertTrue(deferred_tool.has_pending())
        self.assertFalse(sock.closed)
        deferred_tool._deferred_clients[0].deadline = -1.0

        deferred_tool.poll()

        self.assertFalse(deferred_tool.has_pending())
        self.assertTrue(sock.closed)
        self.assertEqual(sock.sent, b"")

    def test_pending_checker_keeps_connection_until_close_all(self) -> None:
        sock = _PartialSendSocket(limit=1024)
        deferred_tool.add(sock, lambda: None, True, "", "")

        self.assertFalse(deferred_tool.poll())
        self.assertTrue(deferred_tool.has_pending())
        self.assertFalse(sock.closed)

        deferred_tool.close_all()

        self.assertFalse(deferred_tool.has_pending())
        self.assertTrue(sock.closed)

    def test_disconnected_socket_is_removed_without_calling_checker(self) -> None:
        sock = _PartialSendSocket(limit=1024, disconnected=True)
        checker = mock.Mock(return_value={"done": True})
        deferred_tool.add(sock, checker, True, "", "")

        self.assertTrue(deferred_tool.poll())

        checker.assert_not_called()
        self.assertFalse(deferred_tool.has_pending())
        self.assertTrue(sock.closed)
        self.assertEqual(sock.sent, b"")


if __name__ == "__main__":
    unittest.main()
