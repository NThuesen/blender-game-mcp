# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sample evaluated transforms at selected animation frames."""

__all__ = ("register",)

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.inspect_game_transform_frames_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Inspect Game Transform Frames",
            readOnlyHint=True,
        )
    )
    def inspect_game_transform_frames(
        object_name: str,
        frames: list[int],
    ) -> dict[str, object]:
        """
        Sample one object's evaluated transform at selected animation frames.

        Returns location, rotation mode, Euler rotation and scale for each requested frame,
        then restores the user's current frame. Use this for loop-boundary and
        timing checks without inspecting Blender's internal Action structure.
        """
        p = Params(object_name=object_name, frames=frames)
        code = toolcode_format_call(_TOOL_CALL, p)
        return send_code(code, strict_json=True, sandbox=False)
