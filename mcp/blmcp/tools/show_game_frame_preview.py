# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Chat-oriented game-frame preview backed by an MCP Apps UI resource."""

__all__ = (
    "FRAME_PREVIEW_MIME_TYPE",
    "FRAME_PREVIEW_RESOURCE_URI",
    "FramePreviewResult",
    "register",
)

import base64
from typing import Literal, cast

from blmcp.tools.get_game_frame_as_image import render_game_frame_png
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import CallToolResult, TextContent, ToolAnnotations  # pylint: disable=import-error,no-name-in-module
from pydantic import BaseModel

FRAME_PREVIEW_RESOURCE_URI = "ui://blender-game/frame-preview.html"
FRAME_PREVIEW_MIME_TYPE = "text/html;profile=mcp-app"


class FramePreviewResult(BaseModel):
    """Model-visible result for a successfully rendered frame preview."""

    frame: int
    width: int
    height: int
    mime_type: Literal["image/png"]
    rendered: Literal[True]


_FRAME_PREVIEW_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; padding: 10px; }
    figure { margin: 0; }
    .canvas {
      display: grid;
      min-height: 120px;
      place-items: center;
      overflow: hidden;
      border: 1px solid color-mix(in srgb, CanvasText 20%, transparent);
      border-radius: 10px;
      background-color: #d7d7d7;
      background-image:
        linear-gradient(45deg, #bdbdbd 25%, transparent 25%),
        linear-gradient(-45deg, #bdbdbd 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, #bdbdbd 75%),
        linear-gradient(-45deg, transparent 75%, #bdbdbd 75%);
      background-position: 0 0, 0 8px, 8px -8px, -8px 0;
      background-size: 16px 16px;
    }
    img { display: none; width: 100%; height: auto; max-height: 70vh; object-fit: contain; }
    figcaption { padding-top: 7px; font-size: 13px; color: color-mix(in srgb, CanvasText 72%, transparent); }
    #status { padding: 18px; text-align: center; }
  </style>
</head>
<body>
  <figure>
    <div class="canvas">
      <img id="preview" alt="Rendered Blender game frame">
      <div id="status">Waiting for rendered frame...</div>
    </div>
    <figcaption id="caption">Blender game frame preview</figcaption>
  </figure>
  <script>
    const image = document.getElementById("preview");
    const status = document.getElementById("status");
    const caption = document.getElementById("caption");

    function renderToolResult(toolResult) {
      const details = toolResult?.structuredContent ?? {};
      const dataUrl = toolResult?._meta?.png_data_url;
      const frame = Number.isInteger(details.frame) ? details.frame : null;
      caption.textContent = frame === null
        ? "Blender game frame preview"
        : `Frame ${frame} - ${details.width} x ${details.height}`;
      if (typeof dataUrl === "string" && dataUrl.startsWith("data:image/png;base64,")) {
        image.src = dataUrl;
        image.style.display = "block";
        status.style.display = "none";
      } else {
        image.removeAttribute("src");
        image.style.display = "none";
        status.style.display = "block";
        status.textContent = details.rendered
          ? "The frame rendered, but no PNG payload reached this UI."
          : "Waiting for rendered frame...";
      }
    }

    window.addEventListener("message", (event) => {
      if (event.source !== window.parent) return;
      const message = event.data;
      if (!message || message.jsonrpc !== "2.0") return;
      if (message.method === "ui/notifications/tool-result") {
        renderToolResult(message.params);
      }
    }, { passive: true });
  </script>
</body>
</html>"""


def register(mcp: FastMCP) -> None:
    @mcp.resource(
        FRAME_PREVIEW_RESOURCE_URI,
        name="Blender game frame preview",
        description="Responsive inline PNG viewer for a rendered Blender game frame.",
        mime_type=FRAME_PREVIEW_MIME_TYPE,
        meta={"ui": {"prefersBorder": True}},
    )
    def frame_preview_resource() -> str:
        return _FRAME_PREVIEW_HTML

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Show Game Frame Preview",
            readOnlyHint=True,
        ),
        meta={
            "ui": {"resourceUri": FRAME_PREVIEW_RESOURCE_URI},
        },
        structured_output=True,
    )
    def show_game_frame_preview(frame: int, max_dimension: int = 512) -> FramePreviewResult:
        """
        Render *frame* and show it inline using the Blender Game preview UI.

        Prefer this tool when the user asks to show or preview a frame, see a
        render, or visually inspect an animation pose. The current scene frame
        and temporary render settings are restored afterwards. *max_dimension*
        bounds the PNG while preserving aspect ratio; use 0 for scene resolution.
        """
        rendered = render_game_frame_png(frame, max_dimension)
        structured = FramePreviewResult(
            frame=frame,
            width=rendered.width,
            height=rendered.height,
            mime_type="image/png",
            rendered=True,
        ).model_dump(mode="json")
        result = CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text="Rendered Blender game frame {:d} ({:d} x {:d}) successfully.".format(
                        frame,
                        rendered.width,
                        rendered.height,
                    ),
                )
            ],
            structuredContent=structured,
            _meta={
                "png_data_url": "data:image/png;base64," + base64.b64encode(rendered.data).decode("ascii"),
            },
        )
        # FastMCP uses the return annotation to publish outputSchema, while
        # CallToolResult supplies the UI-only metadata at runtime.
        return cast(FramePreviewResult, result)
