# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Render one animation frame and return it to the MCP client as an image."""

__all__ = ("register",)

from pathlib import Path
import tempfile

from blmcp.tools_helpers.connection import send_code
from mcp.server.fastmcp import FastMCP, Image  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module


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

        Intended for visual QA by the agent. The scene frame and temporary
        render overrides are restored afterwards. *max_dimension* bounds the
        preview size while preserving the scene aspect ratio; use 0 to render
        at the scene's current resolution.
        """
        if max_dimension < 0 or max_dimension > 4096:
            raise ValueError("max_dimension must be between 0 and 4096")

        with tempfile.TemporaryDirectory(prefix="blmcp-game-frame-") as temporary:
            render_path = Path(temporary) / "frame.png"
            code = (
                "import bpy\n"
                "_scene = bpy.context.scene\n"
                "_rd = _scene.render\n"
                f"_frame = {frame!r}\n"
                f"_max_dim = {max_dimension!r}\n"
                "if _frame < _scene.frame_start or _frame > _scene.frame_end:\n"
                "    raise ValueError('frame is outside the active scene frame range')\n"
                "_old_frame = _scene.frame_current\n"
                "_old = (_rd.filepath, _rd.resolution_x, _rd.resolution_y, "
                "_rd.resolution_percentage, _rd.image_settings.file_format, "
                "_rd.image_settings.color_mode, _rd.image_settings.color_depth, "
                "_rd.use_file_extension)\n"
                "try:\n"
                "    _scene.frame_set(_frame)\n"
                f"    _rd.filepath = {str(render_path)!r}\n"
                "    if _max_dim > 0:\n"
                "        _largest = max(_rd.resolution_x, _rd.resolution_y)\n"
                "        if _largest > _max_dim:\n"
                "            _scale = _max_dim / float(_largest)\n"
                "            _rd.resolution_x = max(1, int(round(_rd.resolution_x * _scale)))\n"
                "            _rd.resolution_y = max(1, int(round(_rd.resolution_y * _scale)))\n"
                "    _rd.resolution_percentage = 100\n"
                "    _rd.image_settings.file_format = 'PNG'\n"
                "    _rd.image_settings.color_mode = 'RGBA'\n"
                "    _rd.image_settings.color_depth = '8'\n"
                "    _rd.use_file_extension = True\n"
                "    bpy.ops.render.render(write_still=True)\n"
                "    result = {'rendered': True, 'frame': _frame}\n"
                "finally:\n"
                "    (_rd.filepath, _rd.resolution_x, _rd.resolution_y, "
                "_rd.resolution_percentage, _rd.image_settings.file_format, "
                "_rd.image_settings.color_mode, _rd.image_settings.color_depth, "
                "_rd.use_file_extension) = _old\n"
                "    _scene.frame_set(_old_frame)\n"
            )
            response = send_code(code, strict_json=True, sandbox=False)
            if response.get("status") != "ok" or not render_path.is_file():
                raise RuntimeError(
                    "Blender did not produce the requested game-frame PNG: {:s}".format(
                        str(response.get("message", "unknown render error"))
                    )
                )
            return Image(data=render_path.read_bytes(), format="png")
