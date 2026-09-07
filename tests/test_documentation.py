# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Checks for command-line backend documentation examples and links."""

__all__ = ()

import io
import json
import os
import re
import unittest

import docutils.core  # type: ignore[import-untyped]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_README = os.path.join(_REPO_ROOT, "readme.md")
_BACKEND_DOC = os.path.join(_REPO_ROOT, "readme_bpy_backend.rst")


class TestBackendDocumentation(unittest.TestCase):
    """Keep published backend configuration executable and complete."""

    def test_native_mcp_config_is_valid_json(self) -> None:
        with open(_README, "r", encoding="utf-8") as fh:
            readme = fh.read()
        matches = re.findall(r"```json\n(.*?)\n```", readme, flags=re.DOTALL)
        self.assertEqual(len(matches), 1)
        config = json.loads(matches[0])
        server = config["mcpServers"]["blender"]
        self.assertEqual(server["command"], "uv")
        self.assertEqual(
            server["args"],
            [
                "--directory",
                "/absolute/path/to/blender_mcp/mcp",
                "run",
                "blender-mcp",
            ],
        )
        self.assertEqual(server["env"]["BLENDER_MCP_CLI_BACKEND"], "bpy")
        self.assertEqual(
            server["env"]["BLENDER_MCP_BPY_PYTHON"],
            "/absolute/path/to/python-with-bpy",
        )

    def test_backend_rst_parses_without_warnings(self) -> None:
        warning_stream = io.StringIO()
        with open(_BACKEND_DOC, "r", encoding="utf-8") as fh:
            source = fh.read()
        docutils.core.publish_doctree(
            source,
            source_path=_BACKEND_DOC,
            settings_overrides={
                "halt_level": 2,
                "report_level": 2,
                "warning_stream": warning_stream,
            },
        )
        self.assertEqual(warning_stream.getvalue(), "")

    def test_documentation_is_ascii_and_wrapped(self) -> None:
        with open(_BACKEND_DOC, "r", encoding="utf-8") as fh:
            source = fh.read()
        source.encode("ascii")
        long_lines = [
            (line_number, len(line))
            for line_number, line in enumerate(source.splitlines(), 1)
            if len(line) > 120
        ]
        self.assertEqual(long_lines, [])

    def test_readme_local_links_exist(self) -> None:
        with open(_README, "r", encoding="utf-8") as fh:
            readme = fh.read()
        local_links = [
            target
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", readme)
            if "://" not in target and not target.startswith("#")
        ]
        self.assertTrue(local_links)
        for target in local_links:
            with self.subTest(target=target):
                self.assertTrue(os.path.exists(os.path.join(_REPO_ROOT, target)))


if __name__ == "__main__":
    unittest.main()
