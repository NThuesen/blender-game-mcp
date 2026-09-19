# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Render one animation frame and return it to the MCP client as an image."""

__all__ = ("RenderedFrame", "register", "render_game_frame_png")

from pathlib import Path
from typing import NamedTuple
import uuid

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.get_game_frame_as_image_toolcode import Params
from mcp.server.fastmcp import FastMCP, Image  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


class RenderedFrame(NamedTuple):
    data: bytes
    width: int
    height: int


def render_game_frame_png(frame: int, max_dimension: int) -> RenderedFrame:
    """Render *frame* to a temporary PNG and return its bytes and dimensions."""
    if max_dimension < 0 or max_dimension > 4096:
        raise ValueError("max_dimension must be between 0 and 4096")

    # Blender may run with a different Windows temp-directory boundary and may
    # reject newly-created subdirectories. Use a unique file directly under
    # the shared MCP working directory, then remove it in ``finally``.
    render_path = Path.cwd() / (".blmcp-game-frame-" + uuid.uuid4().hex + ".png")
    try:
        params = Params(
            frame=frame,
            max_dimension=max_dimension,
            output_path=str(render_path),
        )
        code = toolcode_format_call(_TOOL_CALL, params)
        response = send_code(code, strict_json=True)
        if response.get("status") != "ok":
            message = str(response.get("message", "unknown render error"))
            raise RuntimeError("Blender did not render the requested game frame: " + message)
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Blender returned an invalid game-frame render result")
        if result.get("status") != "ok":
            message = str(result.get("message", "unknown render error"))
            if "outside the active scene frame range" in message:
                raise ValueError(message)
            raise RuntimeError("Blender did not render the requested game frame: " + message)
        if not render_path.is_file():
            raise RuntimeError("Blender did not produce the requested game-frame PNG")
        return RenderedFrame(
            data=render_path.read_bytes(),
            width=int(result["width"]),
            height=int(result["height"]),
        )
    finally:
        render_path.unlink(missing_ok=True)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Render Game Frame as Image",
            readOnlyHint=True,
        )
    )
    def get_game_frame_as_image(frame: int, max_dimension: int = 512) -> Image:
        """
        Render *frame* from the connected scene and return a PNG image.

        Intended for visual QA by clients that display MCP image content.
        The scene frame and temporary render overrides are restored afterwards.
        *max_dimension* bounds the preview size while preserving the scene aspect
        ratio; use 0 to render at the scene's current resolution.
        """
        rendered = render_game_frame_png(frame, max_dimension)
        return Image(data=rendered.data, format="png")
