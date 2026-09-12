# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for Blender CLI backend configuration."""

__all__ = ()

import contextlib
import io
import json
import os
import secrets
import subprocess
import sys
import unittest
from collections.abc import Callable
from unittest import mock

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_DIR = os.path.join(_REPO_DIR, "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from blmcp.tools import execute_blender_code
from blmcp.tools_helpers import blender_cli


class TestCLIBackendConfiguration(unittest.TestCase):
    def test_default_backend_uses_default_blender_executable(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            config = blender_cli._resolve_cli_backend()

        self.assertEqual(config.backend, "blender")
        self.assertEqual(config.executable, "blender")

    def test_explicit_blender_backend_preserves_blender_path(self) -> None:
        env = {
            "BLENDER_MCP_CLI_BACKEND": "blender",
            "BLENDER_PATH": "/opt/blender/Blender",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = blender_cli._resolve_cli_backend()

        self.assertEqual(config.backend, "blender")
        self.assertEqual(config.executable, "/opt/blender/Blender")

    def test_explicit_bpy_backend_uses_configured_python(self) -> None:
        env = {
            "BLENDER_MCP_CLI_BACKEND": "bpy",
            "BLENDER_MCP_BPY_PYTHON": "/opt/bpy/bin/python",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = blender_cli._resolve_cli_backend()

        self.assertEqual(config.backend, "bpy")
        self.assertEqual(config.executable, "/opt/bpy/bin/python")

    def test_bpy_backend_requires_explicit_python(self) -> None:
        env = {"BLENDER_MCP_CLI_BACKEND": "bpy"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(subprocess, "run") as run,
            self.assertRaisesRegex(
                ValueError,
                "BLENDER_MCP_BPY_PYTHON is required when BLENDER_MCP_CLI_BACKEND=bpy",
            ),
        ):
            blender_cli.run_blender_cli("scene.blend", "result = {}")

        run.assert_not_called()

    def test_unknown_backend_fails_before_subprocess_and_lists_valid_values(
        self,
    ) -> None:
        env = {"BLENDER_MCP_CLI_BACKEND": "invalid"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(subprocess, "run") as run,
            self.assertRaisesRegex(
                ValueError,
                "Unknown BLENDER_MCP_CLI_BACKEND 'invalid'; "
                "expected one of: blender, bpy",
            ),
        ):
            blender_cli.run_blender_cli("scene.blend", "result = {}")

        run.assert_not_called()


class TestRunBlenderCLI(unittest.TestCase):
    _FRAME_TOKEN = "test-invocation-token"

    def test_frame_token_uses_256_bits_of_randomness(self) -> None:
        with mock.patch.object(
            secrets, "token_urlsafe", return_value=self._FRAME_TOKEN
        ) as token_urlsafe:
            token = blender_cli._new_frame_token()

        self.assertEqual(token, self._FRAME_TOKEN)
        token_urlsafe.assert_called_once_with(32)

    @classmethod
    def _result_frame(cls, payload: str) -> str:
        return "__BLMCP_RESULT__{:s}__{:s}".format(cls._FRAME_TOKEN, payload)

    @classmethod
    def _error_frame(cls, payload: str) -> str:
        return "__BLMCP_ERROR__{:s}__{:s}".format(cls._FRAME_TOKEN, payload)

    @staticmethod
    def _execute_cli_wrapper(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        stdout = io.StringIO()
        with open(argv[4], encoding="utf-8") as script:
            wrapper = script.read()
        with contextlib.redirect_stdout(stdout):
            exec(wrapper, {})  # noqa: S102 - wrapper execution is under test.
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=stdout.getvalue(),
            stderr="",
        )

    @staticmethod
    def _execute_bpy_runner_with_input_transform(
        transform: Callable[[str], str],
    ) -> Callable[..., subprocess.CompletedProcess[str]]:
        def execute(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            stdout = io.StringIO()
            stderr = io.StringIO()
            stdin_text = kwargs["input"]
            child_env = kwargs["env"]
            assert isinstance(stdin_text, str)
            assert isinstance(child_env, dict)
            with (
                mock.patch.dict(os.environ, child_env, clear=True),
                mock.patch.object(sys, "stdin", io.StringIO(transform(stdin_text))),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                from blmcp.tools_helpers import bpy_cli_runner

                returncode = bpy_cli_runner.main()
            return subprocess.CompletedProcess(
                args=argv,
                returncode=returncode,
                stdout=stdout.getvalue(),
                stderr=stderr.getvalue(),
            )

        return execute

    def _run_with_completed_process(
        self,
        *,
        stdout: str,
        stderr: str = "",
        returncode: int = 0,
    ) -> tuple[dict[str, object], mock.Mock]:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )
        with (
            mock.patch.dict(os.environ, {"BLENDER_PATH": "/opt/blender"}, clear=True),
            mock.patch.object(
                blender_cli, "_new_frame_token", return_value=self._FRAME_TOKEN
            ),
            mock.patch.object(subprocess, "run", return_value=completed) as run,
        ):
            result = blender_cli.run_blender_cli(
                "scene.blend", "result = {'answer': object()}"
            )
        return result, run

    def test_result_is_parsed_without_captured_output(self) -> None:
        result, run = self._run_with_completed_process(
            stdout="Blender startup\n{:s}\nBlender quit\n".format(
                self._result_frame('{"answer": 42}')
            ),
            stderr="harmless warning",
        )

        self.assertEqual(result, {"answer": 42})
        self.assertNotIn("stdout", result)
        self.assertNotIn("stderr", result)
        argv = run.call_args.args[0]
        self.assertEqual(
            argv[:4],
            ["/opt/blender", "--background", "scene.blend", "--python"],
        )
        self.assertFalse(os.path.exists(argv[4]))
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_default_strict_json_rejects_non_serializable_result_in_error_frame(
        self,
    ) -> None:
        with (
            mock.patch.dict(os.environ, {"BLENDER_PATH": "/opt/blender"}, clear=True),
            mock.patch.object(
                blender_cli, "_new_frame_token", return_value=self._FRAME_TOKEN
            ),
            mock.patch.object(subprocess, "run", side_effect=self._execute_cli_wrapper),
            self.assertRaisesRegex(RuntimeError, "is not JSON serializable"),
        ):
            blender_cli.run_blender_cli("scene.blend", "result = {'answer': object()}")

    def test_arbitrary_code_repr_fallback_serializes_non_json_result(self) -> None:
        with (
            mock.patch.dict(os.environ, {"BLENDER_PATH": "/opt/blender"}, clear=True),
            mock.patch.object(
                blender_cli, "_new_frame_token", return_value=self._FRAME_TOKEN
            ),
            mock.patch.object(
                subprocess, "run", side_effect=self._execute_cli_wrapper
            ) as run,
        ):
            result = blender_cli.run_blender_cli(
                "scene.blend",
                "result = {'answer': object()}",
                arbitrary_code=True,
            )

        answer = result["answer"]
        self.assertIsInstance(answer, str)
        assert isinstance(answer, str)
        self.assertIn("object object at", answer)
        self.assertFalse(os.path.exists(run.call_args.args[0][4]))

    def test_long_unicode_code_runs_in_child_with_short_command_line(self) -> None:
        original_run = subprocess.run
        paths: list[str] = []

        def run_script(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            paths.append(argv[4])
            self.assertLess(len(subprocess.list2cmdline(argv)), 32767)
            self.assertTrue(os.path.isfile(argv[4]))
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            # A real child must be able to reopen the closed UTF-8 script on
            # Windows. It needs only Python, keeping this regression portable.
            return original_run([sys.executable, argv[4]], **kwargs)

        code = (
            "# " + ("x" * 70000)
            + "\nimport sys\nresult = {'label': 'caf\u00e9 \U0001f3fa8', 'stdin': sys.stdin.read()}"
        )
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(subprocess, "run", side_effect=run_script),
        ):
            result = blender_cli.run_blender_cli("scene with spaces.blend", code)

        self.assertEqual(result, {"label": "caf\u00e9 \U0001f3fa8", "stdin": ""})
        self.assertEqual(len(paths), 1)
        self.assertFalse(os.path.exists(os.path.dirname(paths[0])))

    def test_temporary_script_is_removed_after_child_failures(self) -> None:
        failures = (
            subprocess.TimeoutExpired(cmd=["blender"], timeout=3),
            FileNotFoundError("missing executable"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                paths: list[str] = []

                def fail(argv: list[str], **_kwargs: object) -> None:
                    paths.append(argv[4])
                    self.assertTrue(os.path.isfile(argv[4]))
                    raise failure

                with (
                    mock.patch.dict(os.environ, {}, clear=True),
                    mock.patch.object(subprocess, "run", side_effect=fail),
                    self.assertRaises(RuntimeError),
                ):
                    blender_cli.run_blender_cli("scene.blend", "result = {}", timeout=3)
                self.assertEqual(len(paths), 1)
                self.assertFalse(os.path.exists(os.path.dirname(paths[0])))

    def test_temporary_script_is_removed_after_execution_error(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(subprocess, "run", side_effect=self._execute_cli_wrapper) as run,
            self.assertRaisesRegex(RuntimeError, "Blender error: deliberate"),
        ):
            blender_cli.run_blender_cli("scene.blend", "raise ValueError('deliberate')")
        self.assertFalse(os.path.exists(os.path.dirname(run.call_args.args[0][4])))

    def test_repr_fallback_is_only_enabled_for_arbitrary_code(self) -> None:
        arbitrary_wrapper = blender_cli._build_cli_wrapper(
            "result = {}", frame_token=self._FRAME_TOKEN, arbitrary_code=True
        )
        regular_wrapper = blender_cli._build_cli_wrapper(
            "result = {}", frame_token=self._FRAME_TOKEN, arbitrary_code=False
        )

        self.assertIn("json.dumps(_result, default=repr)", arbitrary_wrapper)
        self.assertIn("json.dumps(_result)", regular_wrapper)
        self.assertNotIn("default=repr", regular_wrapper)

    def test_execute_code_cli_caller_explicitly_opts_into_arbitrary_code(self) -> None:
        registered: dict[str, object] = {}

        class FakeMCP:
            def tool(
                self, **_kwargs: object
            ) -> Callable[[Callable[..., object]], object]:
                def decorator(function: Callable[..., object]) -> object:
                    registered[function.__name__] = function
                    return function

                return decorator

        execute_blender_code.register(FakeMCP())  # type: ignore[arg-type]
        tool = registered["execute_blender_code_for_cli"]
        with (
            mock.patch.object(
                execute_blender_code,
                "synced_blend_for_cli",
                return_value=contextlib.nullcontext("synced.blend"),
            ),
            mock.patch.object(
                execute_blender_code,
                "run_blender_cli",
                return_value={"answer": "<object object>"},
            ) as run,
        ):
            result = tool("scene.blend", "result = {'answer': object()}")  # type: ignore[operator]

        self.assertEqual(result, {"answer": "<object object>"})
        run.assert_called_once_with(
            "synced.blend",
            "result = {'answer': object()}",
            arbitrary_code=True,
        )

    def test_cli_render_tool_returns_image_content(self) -> None:
        registered: dict[str, object] = {}

        class FakeMCP:
            def tool(self, **_kwargs: object) -> Callable[[Callable[..., object]], object]:
                def decorator(function: Callable[..., object]) -> object:
                    registered[function.__name__] = function
                    return function
                return decorator

        execute_blender_code.register(FakeMCP())  # type: ignore[arg-type]
        self.assertIn("get_render_as_image_for_cli", registered)
        tool = registered["get_render_as_image_for_cli"]
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "scene.blend")
            with open(source, "wb") as stream:
                stream.write(b"blend-bytes")
            render = os.path.join(tmp, "render.png")
            def produce(_blend: str, _code: str, *, arbitrary_code: bool) -> dict[str, object]:
                self.assertFalse(arbitrary_code)
                with open(render, "wb") as stream:
                    stream.write(b"png-bytes")
                return {"rendered": True}
            temporary = mock.MagicMock()
            temporary.__enter__.return_value = tmp
            temporary.__exit__.return_value = False
            with mock.patch.object(execute_blender_code.tempfile, "TemporaryDirectory",
                                   return_value=temporary), \
                 mock.patch.object(execute_blender_code, "synced_blend_for_cli",
                                   return_value=contextlib.nullcontext("synced.blend")), \
                 mock.patch.object(execute_blender_code, "run_blender_cli",
                                   side_effect=produce) as run:
                image = tool(source)  # type: ignore[operator]
            self.assertEqual(image.mimeType, "image/png")
            self.assertEqual(image.data, "cG5nLWJ5dGVz")
            self.assertEqual(run.call_args.args[0], source)
            code = run.call_args.args[1]
            self.assertIn("bpy.ops.render.render(write_still=True)", code)
            self.assertIn("compute_device_type='CUDA'", code)
            self.assertIn("d.use=(d.type=='CUDA')", code)

    def test_execute_code_cli_attests_fresh_checkpoint_creation(self) -> None:
        registered: dict[str, object] = {}
        class FakeMCP:
            def tool(self, **_kwargs: object) -> Callable[[Callable[..., object]], object]:
                def decorator(function: Callable[..., object]) -> object:
                    registered[function.__name__] = function
                    return function
                return decorator
        execute_blender_code.register(FakeMCP())  # type: ignore[arg-type]
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "initial.blend")
            output = os.path.join(tmp, "iteration01.blend")
            with open(source, "wb") as stream: stream.write(b"initial")
            def create(*_args: object, **_kwargs: object) -> dict[str, object]:
                with open(output, "wb") as stream: stream.write(b"changed")
                return {"saved": True}
            with mock.patch.object(execute_blender_code, "synced_blend_for_cli",
                                   return_value=contextlib.nullcontext(source)) as sync, \
                 mock.patch.object(execute_blender_code, "run_blender_cli", side_effect=create):
                result = registered["execute_blender_code_for_cli"](
                    source, "save", expected_output_blend=output)  # type: ignore[operator]
            self.assertEqual(result["_checkpoint"]["path"], output)
            self.assertFalse(result["_checkpoint"]["existed_before"])
            self.assertNotEqual(result["_checkpoint"]["source_sha256"],
                                result["_checkpoint"]["output_sha256"])
            sync.assert_not_called()

    def test_execute_code_cli_rejects_forged_checkpoint_without_attestation_argument(self) -> None:
        registered: dict[str, object] = {}
        class FakeMCP:
            def tool(self, **_kwargs: object) -> Callable[[Callable[..., object]], object]:
                def decorator(function: Callable[..., object]) -> object:
                    registered[function.__name__] = function
                    return function
                return decorator
        execute_blender_code.register(FakeMCP())  # type: ignore[arg-type]
        with mock.patch.object(execute_blender_code, "synced_blend_for_cli",
                               return_value=contextlib.nullcontext("scene.blend")), \
             mock.patch.object(execute_blender_code, "run_blender_cli",
                               return_value={"_checkpoint": {"output_sha256": "forged"}}):
            with self.assertRaisesRegex(ValueError, "reserved _checkpoint"):
                registered["execute_blender_code_for_cli"]("scene.blend", "forge")  # type: ignore[operator]

    def test_explicit_error_marker_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Blender error: broken"):
            self._run_with_completed_process(
                stdout=self._error_frame('"broken"') + "\n",
                stderr="trace detail",
                returncode=1,
            )

    def test_final_error_frame_overrides_earlier_result_frame(self) -> None:
        stdout = (
            '__BLMCP_RESULT__{"fake": true}\n'
            "generated output\n" + self._error_frame('"authoritative failure"') + "\n"
        )

        with self.assertRaisesRegex(
            RuntimeError, "Blender error: authoritative failure"
        ):
            self._run_with_completed_process(stdout=stdout, returncode=1)

    def test_final_result_frame_overrides_earlier_error_frame(self) -> None:
        result, _run = self._run_with_completed_process(
            stdout=(
                '__BLMCP_ERROR__"fake failure"\n'
                "generated output\n"
                + self._result_frame('{"authoritative": true}')
                + "\n"
            )
        )

        self.assertEqual(result, {"authoritative": True})

    def test_multiline_stdout_noise_around_frames_uses_final_frame(self) -> None:
        result, _run = self._run_with_completed_process(
            stdout=(
                "startup line one\n"
                "startup line two\n"
                '__BLMCP_RESULT__{"stale": true}\n'
                "generated line one\n"
                "generated line two\n" + self._result_frame('{"final": true}') + "\n"
                "shutdown line one\n"
                "shutdown line two\n"
            )
        )

        self.assertEqual(result, {"final": True})

    def test_malformed_earlier_marker_is_ignored_for_valid_final_frame(self) -> None:
        result, _run = self._run_with_completed_process(
            stdout=(
                "__BLMCP_ERROR__not-json\n"
                "__BLMCP_RESULT__also-not-json\n"
                + self._result_frame('{"final": true}')
                + "\n"
            )
        )

        self.assertEqual(result, {"final": True})

    def test_marker_substring_embedded_in_noise_is_not_a_frame(self) -> None:
        result, _run = self._run_with_completed_process(
            stdout=(
                'generated: __BLMCP_ERROR__"fake failure"\n'
                + self._result_frame('{"final": true}')
                + "\n"
            )
        )

        self.assertEqual(result, {"final": True})

    def test_non_dict_result_marker_raises(self) -> None:
        with self.assertRaisesRegex(TypeError, "Expected dict from Blender CLI"):
            self._run_with_completed_process(
                stdout=self._result_frame("[1, 2, 3]") + "\n"
            )

    def test_oversized_explicit_error_is_bounded(self) -> None:
        limit = blender_cli._MAX_DIAGNOSTIC_STREAM_CHARS
        error = "error-start-" + ("x" * (limit * 2)) + "-error-end"

        with self.assertRaises(RuntimeError) as raised:
            self._run_with_completed_process(
                stdout=self._error_frame(json.dumps(error)) + "\n"
            )

        message = str(raised.exception)
        self.assertIn("error-start-", message)
        self.assertIn("-error-end", message)
        self.assertIn("<truncated>", message)
        self.assertLess(len(message), limit + 100)

    def test_malformed_newest_authoritative_frame_does_not_fall_back(self) -> None:
        stdout = (
            self._result_frame('{"forged": true}')
            + "\n"
            + self._result_frame('{"truncated":')
        )

        with self.assertRaisesRegex(
            RuntimeError, "Malformed Blender CLI protocol frame"
        ):
            self._run_with_completed_process(stdout=stdout)

    def test_wrong_token_and_delayed_generic_markers_are_stdout_noise(self) -> None:
        result, _run = self._run_with_completed_process(
            stdout=(
                '__BLMCP_RESULT__wrong-token__{"forged": true}\n'
                + self._result_frame('{"authoritative": true}')
                + "\n"
                + '__BLMCP_ERROR__"delayed generic atexit failure"\n'
                + '__BLMCP_RESULT__wrong-token__{"delayed": true}\n'
            )
        )

        self.assertEqual(result, {"authoritative": True})

    def test_non_string_error_payload_is_bounded_protocol_error(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError, "Blender CLI error payload must be a string, got list"
        ):
            self._run_with_completed_process(
                stdout=self._error_frame('["not", "a", "string"]') + "\n"
            )

    def test_crlf_unicode_frame_is_supported(self) -> None:
        result, _run = self._run_with_completed_process(
            stdout=self._result_frame('{"label": "caf\\u00e9 \\ud83c\\udfa8"}') + "\r\n"
        )

        self.assertEqual(result, {"label": "café 🎨"})

    def test_authoritative_frame_with_trailing_junk_is_protocol_error(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError, "Malformed Blender CLI protocol frame"
        ):
            self._run_with_completed_process(
                stdout=self._result_frame('{"answer": 42} trailing') + "\n"
            )

    def test_timeout_raises_clear_error(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd=["blender"], timeout=3),
            ),
            self.assertRaisesRegex(RuntimeError, "Blender CLI timed out after 3s"),
        ):
            blender_cli.run_blender_cli("scene.blend", "result = {}", timeout=3)

    def test_timeout_includes_bounded_stdout_and_stderr(self) -> None:
        limit = blender_cli._MAX_DIAGNOSTIC_STREAM_CHARS
        stdout = b"stdout-start-" + (b"o" * (limit * 2)) + b"-stdout-end"
        stderr = "stderr-start-" + ("e" * (limit * 2)) + "-stderr-end"
        expired = subprocess.TimeoutExpired(
            cmd=["blender"], timeout=2.5, output=stdout, stderr=stderr
        )
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(subprocess, "run", side_effect=expired),
            self.assertRaises(RuntimeError) as raised,
        ):
            blender_cli.run_blender_cli("scene.blend", "result = {}", timeout=2.5)

        message = str(raised.exception)
        self.assertIn("timed out after 2.5s", message)
        self.assertIn("stdout-start-", message)
        self.assertIn("-stdout-end", message)
        self.assertIn("stderr-start-", message)
        self.assertIn("-stderr-end", message)
        self.assertEqual(message.count("<truncated>"), 2)
        self.assertLess(len(message), (limit * 2) + 200)

    def test_positive_subsecond_timeout_is_not_reported_as_zero_seconds(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd=["blender"], timeout=0.25),
            ),
            self.assertRaisesRegex(RuntimeError, "timed out after 0.25s"),
        ):
            blender_cli.run_blender_cli("scene.blend", "result = {}", timeout=0.25)

    def test_missing_executable_raises_clear_error(self) -> None:
        with (
            mock.patch.dict(
                os.environ, {"BLENDER_PATH": "/missing/blender"}, clear=True
            ),
            mock.patch.object(subprocess, "run", side_effect=FileNotFoundError),
            self.assertRaisesRegex(
                RuntimeError,
                "Blender executable not found at '/missing/blender'",
            ),
        ):
            blender_cli.run_blender_cli("scene.blend", "result = {}")

    def test_bpy_backend_uses_absolute_runner_and_json_stdin(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=self._result_frame('{"backend": "bpy"}') + "\n",
            stderr="",
        )
        env = {
            "BLENDER_MCP_CLI_BACKEND": "bpy",
            "BLENDER_MCP_BPY_PYTHON": "/opt/bpy/bin/python",
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(
                blender_cli, "_new_frame_token", return_value=self._FRAME_TOKEN
            ),
            mock.patch.object(subprocess, "run", return_value=completed) as run,
        ):
            result = blender_cli.run_blender_cli(
                "/absolute/scene.blend",
                "result = {'value': object()}",
                timeout=17,
                arbitrary_code=True,
            )

        self.assertEqual(result, {"backend": "bpy"})
        self.assertEqual(len(run.call_args.args), 1)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "/opt/bpy/bin/python")
        self.assertEqual(len(argv), 2)
        self.assertTrue(os.path.isabs(argv[1]))
        self.assertEqual(os.path.basename(argv[1]), "bpy_cli_runner.py")
        self.assertEqual(
            json.loads(run.call_args.kwargs["input"]),
            {
                "blend_file": "/absolute/scene.blend",
                "code": "result = {'value': object()}",
                "frame_token": self._FRAME_TOKEN,
                "arbitrary_code": True,
            },
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 17)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertTrue(run.call_args.kwargs["text"])
        self.assertFalse(run.call_args.kwargs["check"])
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(
            child_env[blender_cli._BPY_RUNNER_FRAME_TOKEN_ENV], self._FRAME_TOKEN
        )

    def test_bpy_runner_malformed_stdin_error_is_parsed_with_expected_token(
        self,
    ) -> None:
        self._assert_bpy_runner_validation_error(
            lambda _raw: "not json", "Invalid JSON input"
        )

    def test_bpy_runner_missing_payload_token_error_is_parsed_with_expected_token(
        self,
    ) -> None:
        def remove_token(raw: str) -> str:
            request = json.loads(raw)
            del request["frame_token"]
            return json.dumps(request)

        self._assert_bpy_runner_validation_error(
            remove_token, "`frame_token` must be a string"
        )

    def test_bpy_runner_corrupt_payload_token_error_is_parsed_with_expected_token(
        self,
    ) -> None:
        def corrupt_token(raw: str) -> str:
            request = json.loads(raw)
            request["frame_token"] = "wrong-token"
            return json.dumps(request)

        self._assert_bpy_runner_validation_error(
            corrupt_token, "does not match bootstrap token"
        )

    def _assert_bpy_runner_validation_error(
        self, transform: Callable[[str], str], expected_error: str
    ) -> None:
        env = {
            "BLENDER_MCP_CLI_BACKEND": "bpy",
            "BLENDER_MCP_BPY_PYTHON": "/opt/bpy/bin/python",
            "INHERITED_ASSET_PATH": "/assets",
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(
                blender_cli, "_new_frame_token", return_value=self._FRAME_TOKEN
            ),
            mock.patch.object(
                subprocess,
                "run",
                side_effect=self._execute_bpy_runner_with_input_transform(transform),
            ) as run,
            self.assertRaisesRegex(RuntimeError, expected_error),
        ):
            blender_cli.run_blender_cli("/absolute/scene.blend", "result = {}")

        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["INHERITED_ASSET_PATH"], "/assets")

    def test_bpy_backend_missing_executable_names_bpy_configuration(self) -> None:
        env = {
            "BLENDER_MCP_CLI_BACKEND": "bpy",
            "BLENDER_MCP_BPY_PYTHON": "/missing/bpy-python",
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(subprocess, "run", side_effect=FileNotFoundError),
            self.assertRaisesRegex(
                RuntimeError,
                "bpy Python executable not found at '/missing/bpy-python'",
            ),
        ):
            blender_cli.run_blender_cli("scene.blend", "result = {}")

    def test_bpy_backend_crash_without_marker_reports_captured_output(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=-11, stdout="startup", stderr="native crash"
        )
        env = {
            "BLENDER_MCP_CLI_BACKEND": "bpy",
            "BLENDER_MCP_BPY_PYTHON": "/opt/bpy/bin/python",
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(subprocess, "run", return_value=completed),
            self.assertRaises(RuntimeError) as raised,
        ):
            blender_cli.run_blender_cli("scene.blend", "result = {}")

        message = str(raised.exception)
        self.assertIn("exit code -11", message)
        self.assertIn("startup", message)
        self.assertIn("native crash", message)

    def test_no_marker_reports_exit_code_and_bounded_output(self) -> None:
        limit = blender_cli._MAX_DIAGNOSTIC_STREAM_CHARS
        stdout = "stdout-start-" + ("o" * (limit * 2)) + "-stdout-end"
        stderr = "stderr-start-" + ("e" * (limit * 2)) + "-stderr-end"

        with self.assertRaises(RuntimeError) as raised:
            self._run_with_completed_process(
                stdout=stdout,
                stderr=stderr,
                returncode=7,
            )

        message = str(raised.exception)
        self.assertIn("exit code 7", message)
        self.assertIn("stdout-start-", message)
        self.assertIn("-stdout-end", message)
        self.assertIn("stderr-start-", message)
        self.assertIn("-stderr-end", message)
        self.assertIn("<truncated>", message)
        self.assertLess(len(message), (limit * 2) + 500)


if __name__ == "__main__":
    unittest.main()
