# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for Blender CLI backend configuration."""

__all__ = ()

import os
import subprocess
import sys
import unittest
from unittest import mock

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_DIR = os.path.join(_REPO_DIR, "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

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
                "BLENDER_MCP_BPY_PYTHON is required when "
                "BLENDER_MCP_CLI_BACKEND=bpy",
            ),
        ):
            blender_cli.run_blender_cli("scene.blend", "result = {}")

        run.assert_not_called()

    def test_unknown_backend_fails_before_subprocess_and_lists_valid_values(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
