# Game asset workflow

The game extension is intentionally structured around a short agent loop:

1. `inspect_game_scene` before changing render/camera assumptions.
2. `configure_game_scene` when a deterministic sprite/VFX canvas is needed.
3. Create or edit artwork/animation with narrow Blender tools where possible.
4. Use `show_game_frame_preview` when a ChatGPT user asks to show/preview a
   frame, see the render, or inspect an animation pose. It renders the frame in
   the inline MCP Apps viewer without putting the PNG base64 in model-visible
   structured content.
5. Use `get_game_frame_as_image` when a client such as Work/Codex natively
   displays raw MCP image content.
6. Correct visual problems before any batch export.

## ChatGPT frame preview

`show_game_frame_preview(frame, max_dimension=512)` serves its inline viewer
from `ui://blender-game/frame-preview.html` with the MCP Apps MIME type
`text/html;profile=mcp-app`. The tool returns only `frame`, `width`, `height`,
`mime_type` and `rendered` as structured content. The PNG data URL is carried in
tool-result `_meta`, which is delivered to the component without adding the
base64 payload to the model transcript.

Both frame-preview tools temporarily select the requested frame and enforce
PNG/RGBA output. They restore the previous frame, output path, resolution,
resolution percentage, image format/depth/mode and file-extension setting even
when rendering fails. `max_dimension=0` keeps the scene's effective resolution;
values from 1 through 4096 bound the largest output dimension.
The transient PNG is written beneath the MCP server's working directory so
the Blender process and Secure MCP Tunnel share the same filesystem boundary;
the temporary directory is removed immediately after the response is built.

## Rendering conventions

For 2D sprite-style output, default to:

- orthographic camera;
- transparent film;
- PNG with RGBA;
- resolution percentage 100;
- explicit FPS and frame range;
- stable canvas dimensions across every frame.

Do not infer that PNG alone contains alpha. Check `color_mode == "RGBA"` and
`film_transparent == true`.

## Loop review

For a looping animation, inspect at least:

- the first frame;
- roughly 25%, 50% and 75% through the loop;
- the final rendered frame.

Avoid a duplicate terminal pose when the game engine will loop directly back
to the first frame, unless the target pipeline explicitly requires it.

## Camera and pivot

The camera is an orthographic render camera, not game-engine pivot metadata.
Do not silently equate the Blender object origin, camera center and Unity/Godot
sprite pivot. Pivot metadata will be handled explicitly by the export layer.

## Arbitrary Python

Prefer structured game tools for common operations. Use
`execute_blender_code` only when a typed tool cannot express the requested
operation, and keep generated code scoped to the current scene/workspace.
