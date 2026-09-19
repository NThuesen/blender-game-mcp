# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Insert deterministic transform keyframes for game animation."""

__all__ = ("register",)

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.set_game_transform_keyframes_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Set Game Transform Keyframes",
            destructiveHint=True,
        )
    )
    def set_game_transform_keyframes(
        object_name: str,
        data_path: str,
        frames: list[int],
        values: list[list[float]],
    ) -> dict[str, object]:
        """
        Insert transform keyframes on one object for game animation.

        data_path is intentionally limited to location, rotation_euler or
        scale. frames and values must have the same length; every value is a
        three-number vector. Frames must stay inside the active scene range.

        Repeating the tool at an existing frame updates that keyed value
        instead of requiring arbitrary Python.
        """
        p = Params(
            object_name=object_name,
            data_path=data_path,
            frames=frames,
            values=values,
        )
        code = toolcode_format_call(_TOOL_CALL, p)
        return send_code(code, strict_json=True, sandbox=False)
