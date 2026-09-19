# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Configure a Blender scene for deterministic 2D game-asset rendering."""

__all__ = ("register",)

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.configure_game_scene_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Configure Game Scene",
            destructiveHint=True,
        )
    )
    def configure_game_scene(
        width: int = 512,
        height: int = 512,
        fps: int = 24,
        frame_start: int = 1,
        frame_end: int = 24,
        transparent: bool = True,
        camera_name: str | None = None,
        create_camera_if_missing: bool = True,
        orthographic_scale: float = 5.0,
    ) -> dict[str, object]:
        """
        Configure the active Blender scene for 2D game-asset rendering.

        Sets deterministic resolution, FPS, frame range, PNG/RGBA output and
        transparent film. Uses *camera_name* when supplied; otherwise uses the
        scene camera. When no camera exists and *create_camera_if_missing* is
        true, creates an orthographic camera aimed down -Z at the origin.

        This tool intentionally does not change the render engine.
        """
        p = Params(
            width=width,
            height=height,
            fps=fps,
            frame_start=frame_start,
            frame_end=frame_end,
            transparent=transparent,
            camera_name=camera_name,
            create_camera_if_missing=create_camera_if_missing,
            orthographic_scale=orthographic_scale,
        )
        code = toolcode_format_call(_TOOL_CALL, p)
        return send_code(code, strict_json=True, sandbox=False)
