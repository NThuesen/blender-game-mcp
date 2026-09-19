# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Inspect whether the active Blender scene is ready for game-asset rendering."""

__all__ = ("register",)

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Inspect Game Scene",
            readOnlyHint=True,
        )
    )
    def inspect_game_scene() -> dict[str, object]:
        """
        Return compact game-render readiness information for the active scene.

        Reports resolution, FPS, frame range, transparency, PNG/RGBA settings,
        active camera type/scale, render engine and warnings. Use this before
        rendering or after broad edits instead of dumping the whole scene.
        """
        code = toolcode_format_call(_TOOL_CALL, None)
        return send_code(code, strict_json=True, sandbox=False)
