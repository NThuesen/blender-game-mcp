# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the standalone bpy CLI subprocess runner."""

__all__ = ()

import builtins
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_DIR = os.path.join(_REPO_DIR, "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from blmcp.tools_helpers import bpy_cli_runner


class _FakeWindowManager:
    def __init__(self, *, open_error: Exception | None = None) -> None:
        self.open_error = open_error
        self.opened: list[str] = []

    def open_mainfile(self, *, filepath: str) -> None:
        self.opened.append(filepath)
        if self.open_error is not None:
            raise self.open_error


class TestBpyCLIRunner(unittest.TestCase):  # pylint: disable=too-many-public-methods
    _FRAME_TOKEN = "runner-test-token"
    _INVALID_FRAME_TOKENS = (
        "line\nfeed",
        "carriage\rreturn",
        "vertical\vtab",
        "form\ffeed",
        "next\x85line",
        "line\u2028separator",
        "has space",
        "punctuation!",
        "padding=",
        "dot.token",
        "slash/token",
        "plus+token",
    )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.temp_dir.cleanup)
        self.blend_file = os.path.join(self.temp_dir.name, "input.blend")
        with open(self.blend_file, "wb") as blend_handle:
            blend_handle.write(b"fixture")

    def _invoke(
        self,
        request: object,
        *,
        open_error: Exception | None = None,
        raw_input: str | None = None,
        bootstrap_token: str | None = _FRAME_TOKEN,
    ) -> tuple[list[str], _FakeWindowManager]:
        window_manager = _FakeWindowManager(open_error=open_error)
        fake_bpy = types.SimpleNamespace(
            ops=types.SimpleNamespace(wm=window_manager),
        )
        stdin = io.StringIO(json.dumps(request) if raw_input is None else raw_input)
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = {}
        if bootstrap_token is not None:
            environment[bpy_cli_runner._FRAME_TOKEN_ENV] = bootstrap_token
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch.dict(sys.modules, {"bpy": fake_bpy}),
            mock.patch.object(sys, "stdin", stdin),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            returncode = bpy_cli_runner.main()
        self.assertEqual(returncode, 0)
        self.assertEqual(stderr.getvalue(), "")
        return stdout.getvalue().splitlines(), window_manager

    def _request(self, code: str, *, arbitrary_code: bool = False) -> dict[str, object]:
        return {
            "blend_file": self.blend_file,
            "code": code,
            "frame_token": self._FRAME_TOKEN,
            "arbitrary_code": arbitrary_code,
        }

    def _token_payload(self, line: str) -> str:
        prefix = "__BLMCP_ERROR__{:s}__".format(self._FRAME_TOKEN)
        self.assertTrue(line.startswith(prefix))
        payload = json.loads(line[len(prefix):])
        self.assertIsInstance(payload, str)
        assert isinstance(payload, str)
        return payload

    def test_frame_token_accepts_token_urlsafe_alphabet(self) -> None:
        valid_tokens = (
            "A",
            "Z",
            "a",
            "z",
            "0",
            "9",
            "_",
            "-",
            "AZaz09_-",
            "a" * bpy_cli_runner._MAX_FRAME_TOKEN_CHARS,
        )

        for token in valid_tokens:
            with self.subTest(token=token):
                self.assertTrue(bpy_cli_runner._is_valid_frame_token(token))

    def test_frame_token_rejects_non_token_urlsafe_characters_and_bounds(self) -> None:
        invalid_tokens = (
            "",
            *self._INVALID_FRAME_TOKENS,
            "a" * (bpy_cli_runner._MAX_FRAME_TOKEN_CHARS + 1),
        )

        for token in invalid_tokens:
            with self.subTest(token=token):
                self.assertFalse(bpy_cli_runner._is_valid_frame_token(token))

    def test_opens_blend_executes_code_and_prints_one_result_frame(self) -> None:
        lines, window_manager = self._invoke(self._request("result = {'answer': 42}"))

        self.assertEqual(window_manager.opened, [self.blend_file])
        self.assertEqual(
            lines,
            ['__BLMCP_RESULT__runner-test-token__{"answer": 42}'],
        )

    def test_malformed_json_prints_one_bootstrap_token_bound_error_frame(self) -> None:
        lines, window_manager = self._invoke({}, raw_input="not json")

        self.assertEqual(window_manager.opened, [])
        self.assertEqual(len(lines), 1)
        self.assertIn("Invalid JSON input", self._token_payload(lines[0]))

    def test_missing_payload_token_uses_bootstrap_token_error_frame(self) -> None:
        request = self._request("result = {}")
        del request["frame_token"]

        lines, window_manager = self._invoke(request)

        self.assertEqual(window_manager.opened, [])
        self.assertIn("`frame_token` must be a string", self._token_payload(lines[0]))

    def test_invalid_payload_token_uses_parser_safe_bootstrap_token_frame(self) -> None:
        for invalid_token in self._INVALID_FRAME_TOKENS:
            with self.subTest(token=invalid_token):
                request = self._request("result = {}")
                request["frame_token"] = invalid_token

                lines, window_manager = self._invoke(request)

                self.assertEqual(window_manager.opened, [])
                self.assertEqual(len(lines), 1)
                self.assertIn("`frame_token` is invalid", self._token_payload(lines[0]))

    def test_payload_token_must_match_bootstrap_token(self) -> None:
        request = self._request("result = {}")
        request["frame_token"] = "different-valid-token"

        lines, window_manager = self._invoke(request)

        self.assertEqual(window_manager.opened, [])
        self.assertIn("does not match bootstrap token", self._token_payload(lines[0]))

    def test_missing_bootstrap_token_has_generic_stderr_and_nonzero_exit(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                sys, "stdin", io.StringIO(json.dumps(self._request("result = {}")))
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            returncode = bpy_cli_runner.main()

        self.assertNotEqual(returncode, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn(
            "missing or invalid runner frame token bootstrap", stderr.getvalue()
        )

    def test_invalid_bootstrap_token_has_bounded_stderr_exit_2_and_no_frame(
        self,
    ) -> None:
        for invalid_token in self._INVALID_FRAME_TOKENS:
            with self.subTest(token=invalid_token):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.dict(
                        os.environ,
                        {bpy_cli_runner._FRAME_TOKEN_ENV: invalid_token},
                        clear=True,
                    ),
                    mock.patch.object(
                        sys,
                        "stdin",
                        io.StringIO(json.dumps(self._request("result = {}"))),
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    returncode = bpy_cli_runner.main()

                diagnostic = stderr.getvalue()
                self.assertEqual(returncode, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(diagnostic, bpy_cli_runner._BOOTSTRAP_ERROR + "\n")
                self.assertLessEqual(
                    len(diagnostic), bpy_cli_runner._MAX_DIAGNOSTIC_CHARS
                )
                self.assertNotIn("__BLMCP_RESULT__", diagnostic)
                self.assertNotIn("__BLMCP_ERROR__", diagnostic)

    def test_all_request_validation_happens_before_importing_bpy(self) -> None:
        request = self._request("result = {}")
        request["arbitrary_code"] = "not-a-boolean"
        stdout = io.StringIO()
        with (
            mock.patch.dict(
                os.environ,
                {bpy_cli_runner._FRAME_TOKEN_ENV: self._FRAME_TOKEN},
                clear=True,
            ),
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(request))),
            mock.patch.object(
                builtins,
                "__import__",
                side_effect=AssertionError(
                    "module imported before request validation completed"
                ),
            ) as import_module,
            contextlib.redirect_stdout(stdout),
        ):
            returncode = bpy_cli_runner.main()

        self.assertEqual(returncode, 0)
        import_module.assert_not_called()
        self.assertIn(
            "must be a boolean", self._token_payload(stdout.getvalue().strip())
        )

    def test_rejects_non_object_input_before_import_or_open(self) -> None:
        lines, window_manager = self._invoke(["not", "an", "object"])

        self.assertEqual(window_manager.opened, [])
        self.assertEqual(len(lines), 1)
        self.assertIn("input must be a JSON object", self._token_payload(lines[0]))

    def test_rejects_invalid_field_types_and_relative_path_before_open(self) -> None:
        invalid_requests = (
            {"blend_file": 3, "code": "result = {}", "frame_token": self._FRAME_TOKEN},
            {
                "blend_file": self.blend_file,
                "code": [],
                "frame_token": self._FRAME_TOKEN,
            },
            {"blend_file": self.blend_file, "code": "result = {}", "frame_token": 3},
            {
                "blend_file": self.blend_file,
                "code": "result = {}",
                "frame_token": self._FRAME_TOKEN,
                "arbitrary_code": "yes",
            },
            {
                "blend_file": "relative.blend",
                "code": "result = {}",
                "frame_token": self._FRAME_TOKEN,
            },
        )

        for request in invalid_requests:
            with self.subTest(request=request):
                lines, window_manager = self._invoke(request)
                self.assertEqual(window_manager.opened, [])
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0].startswith("__BLMCP_ERROR__"))

    def test_rejects_missing_blend_file_before_open(self) -> None:
        request = self._request("result = {}")
        request["blend_file"] = os.path.join(self.temp_dir.name, "missing.blend")

        lines, window_manager = self._invoke(request)

        self.assertEqual(window_manager.opened, [])
        self.assertIn("does not exist", self._token_payload(lines[0]))

    def test_open_failure_prints_token_bound_error_frame(self) -> None:
        lines, window_manager = self._invoke(
            self._request("result = {}"), open_error=RuntimeError("cannot open blend")
        )

        self.assertEqual(window_manager.opened, [self.blend_file])
        self.assertEqual(
            lines,
            ['__BLMCP_ERROR__runner-test-token__"cannot open blend"'],
        )

    def test_error_diagnostic_is_bounded_and_keeps_both_ends(self) -> None:
        limit = bpy_cli_runner._MAX_DIAGNOSTIC_CHARS
        error = RuntimeError("error-start-" + ("x" * (limit * 2)) + "-error-end")

        lines, _window_manager = self._invoke(
            self._request("result = {}"), open_error=error
        )

        message = self._token_payload(lines[0])
        self.assertIn("error-start-", message)
        self.assertIn("-error-end", message)
        self.assertIn("<truncated>", message)
        self.assertLessEqual(len(message), limit)

    def test_result_must_be_a_dict(self) -> None:
        lines, _window_manager = self._invoke(self._request("result = [1, 2, 3]"))

        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("__BLMCP_ERROR__runner-test-token__"))
        self.assertIn("must be a dict, not list", self._token_payload(lines[0]))

    def test_strict_serialization_reports_error(self) -> None:
        lines, _window_manager = self._invoke(
            self._request("result = {'value': object()}")
        )

        self.assertEqual(len(lines), 1)
        self.assertIn("is not JSON serializable", self._token_payload(lines[0]))

    def test_arbitrary_code_uses_repr_fallback(self) -> None:
        lines, _window_manager = self._invoke(
            self._request("result = {'value': object()}", arbitrary_code=True)
        )

        self.assertEqual(len(lines), 1)
        prefix = "__BLMCP_RESULT__{:s}__".format(self._FRAME_TOKEN)
        self.assertTrue(lines[0].startswith(prefix))
        payload = json.loads(lines[0][len(prefix):])
        self.assertIsInstance(payload["value"], str)
        self.assertIn("object object at", payload["value"])


if __name__ == "__main__":
    unittest.main()
