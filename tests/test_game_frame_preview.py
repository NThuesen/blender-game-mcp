# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""MCP Apps contract tests for the ChatGPT game-frame preview."""

__all__ = ()

import asyncio
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_DIR = os.path.join(_REPO_DIR, "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from blmcp.tools.get_game_frame_as_image import RenderedFrame
import blmcp.tools.get_game_frame_as_image as frame_image
import blmcp.tools.show_game_frame_preview as preview

_RESOURCE_URI = "ui://blender-game/frame-preview.html"
_RESOURCE_MIME = "text/html;profile=mcp-app"


async def _discover_preview_contract() -> tuple[object, object, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(_REPO_DIR, "mcp")
    params = StdioServerParameters(command=sys.executable, args=["-m", "blmcp"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            resources = await session.list_resources()
            resource = await session.read_resource(_RESOURCE_URI)
            preview_tool = next(tool for tool in tools.tools if tool.name == "show_game_frame_preview")
            preview_resource = next(item for item in resources.resources if str(item.uri) == _RESOURCE_URI)
            return preview_tool, preview_resource, resource


class TestGameFramePreviewContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool, cls.resource_listing, cls.resource = asyncio.run(_discover_preview_contract())

    def test_tool_is_discoverable_with_stable_input_schema(self) -> None:
        self.assertEqual(self.tool.name, "show_game_frame_preview")
        self.assertEqual(
            self.tool.inputSchema,
            {
                "properties": {
                    "frame": {"title": "Frame", "type": "integer"},
                    "max_dimension": {"default": 512, "title": "Max Dimension", "type": "integer"},
                },
                "required": ["frame"],
                "title": "show_game_frame_previewArguments",
                "type": "object",
            },
        )

    def test_output_schema_is_concise_and_exact(self) -> None:
        schema = self.tool.outputSchema
        self.assertIsNotNone(schema)
        self.assertEqual(
            set(schema["properties"]),
            {"frame", "width", "height", "mime_type", "rendered"},
        )
        self.assertEqual(schema["properties"]["mime_type"]["const"], "image/png")
        self.assertEqual(schema["properties"]["rendered"]["const"], True)

    def test_tool_maps_to_standard_mcp_apps_resource_uri(self) -> None:
        self.assertEqual(self.tool.meta, {"ui": {"resourceUri": _RESOURCE_URI}})

    def test_ui_resource_is_registered_with_mcp_apps_mime_type(self) -> None:
        self.assertEqual(str(self.resource_listing.uri), _RESOURCE_URI)
        self.assertEqual(self.resource_listing.mimeType, _RESOURCE_MIME)
        self.assertEqual(len(self.resource.contents), 1)
        content = self.resource.contents[0]
        self.assertEqual(content.mimeType, _RESOURCE_MIME)
        self.assertIn('id="preview"', content.text)
        self.assertIn("ui/notifications/tool-result", content.text)
        self.assertIn("png_data_url", content.text)

    def test_png_payload_is_ui_only_tool_result_metadata(self) -> None:
        mcp = FastMCP("preview-test")
        preview.register(mcp)
        tool = mcp._tool_manager.get_tool("show_game_frame_preview")
        self.assertIsNotNone(tool)
        with mock.patch.object(
            preview,
            "render_game_frame_png",
            return_value=RenderedFrame(data=b"png", width=8, height=4),
        ):
            result = asyncio.run(tool.run({"frame": 3, "max_dimension": 8}, convert_result=True))

        self.assertIsInstance(result, CallToolResult)
        self.assertEqual(
            result.structuredContent,
            {"frame": 3, "width": 8, "height": 4, "mime_type": "image/png", "rendered": True},
        )
        self.assertEqual(result.meta, {"png_data_url": "data:image/png;base64,cG5n"})
        self.assertNotIn("cG5n", result.content[0].text)

    def test_legacy_frame_tool_still_returns_mcp_image_content(self) -> None:
        mcp = FastMCP("legacy-preview-test")
        frame_image.register(mcp)
        tool = mcp._tool_manager.get_tool("get_game_frame_as_image")
        self.assertIsNotNone(tool)
        with mock.patch.object(
            frame_image,
            "render_game_frame_png",
            return_value=RenderedFrame(data=b"png", width=8, height=4),
        ):
            result = asyncio.run(tool.run({"frame": 3}, convert_result=True))

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], ImageContent)
        self.assertEqual(result[0].mimeType, "image/png")
        self.assertEqual(result[0].data, "cG5n")

    def test_server_render_helper_unwraps_blender_result(self) -> None:
        render_path = Path(_REPO_DIR, ".blmcp-game-frame-test-frame-preview.png")
        render_path.write_bytes(b"png")
        try:
            with (
                mock.patch.object(
                    frame_image.uuid,
                    "uuid4",
                    return_value=mock.Mock(hex="test-frame-preview"),
                ),
                mock.patch.object(
                    frame_image,
                    "send_code",
                    return_value={
                        "status": "ok",
                        "result": {"status": "ok", "frame": 3, "width": 8, "height": 4},
                    },
                ),
            ):
                rendered = frame_image.render_game_frame_png(3, 8)
        finally:
            render_path.unlink(missing_ok=True)

        self.assertEqual(rendered, RenderedFrame(data=b"png", width=8, height=4))

    def test_server_render_helper_reports_invalid_frame(self) -> None:
        with mock.patch.object(
            frame_image,
            "send_code",
            return_value={
                "status": "ok",
                "result": {
                    "status": "error",
                    "frame": 999,
                    "message": "frame is outside the active scene frame range",
                },
            },
        ):
            with self.assertRaisesRegex(ValueError, "outside the active scene frame range"):
                frame_image.render_game_frame_png(999, 512)


if __name__ == "__main__":
    unittest.main()
