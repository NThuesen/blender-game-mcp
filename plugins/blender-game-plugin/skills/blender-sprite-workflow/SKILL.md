---
name: blender-sprite-workflow
description: Inspect, configure, and visually verify Blender game scenes and PNG/RGBA sprite workflows with the structured blender-game MCP tools.
---

# Blender Sprite Workflow

Use this workflow whenever a user asks to inspect, configure, modify, render, or visually check a Blender game or sprite scene through this plugin.

## Required workflow

1. Call `inspect_game_scene` first to establish the current scene, frame, render, camera, and sprite-related state before making changes.
2. For PNG sprites or any workflow that needs transparency, call `configure_game_scene` and configure the scene for PNG output with RGBA color mode. Preserve compatible existing settings rather than resetting unrelated scene state.
3. Use the structured `blender-game` tools for scene inspection and changes whenever they can express the requested operation. Do not reach for arbitrary Python merely for convenience.
4. When the user asks to show or preview a frame, see the render, or visually inspect an animation pose, prefer `show_game_frame_preview` so normal Chat displays the PNG inline. Use `get_game_frame_as_image` for clients that natively display MCP image content.
5. After a visual change, use the appropriate preview tool and inspect the returned image for framing, transparency, composition, sprite placement, and obvious rendering defects.
6. Preserve the user's active scene, current frame, selections, camera, and other working state whenever possible. Change only what the task requires, and restore temporary QA changes when the tools make that practical.

## Tool choice

- Prefer `inspect_game_scene` for discovery over guessing or reconstructing scene state.
- Prefer `configure_game_scene` for supported scene and render configuration, especially PNG/RGBA sprite scenes.
- Prefer `show_game_frame_preview` for phrases such as "show frame 1", "preview frame 1", "let me see the render", and requests to inspect an animation pose.
- Use `get_game_frame_as_image` when the active client supports raw MCP image results and an MCP Apps UI is unnecessary.
- Use arbitrary Python only when no structured blender-game tool supports the required operation. Keep any such code narrowly scoped and explain why it is necessary.

## Completion

Report the scene changes made, the frame visually checked, and any state that could not be preserved. If visual QA reveals a problem, correct it with structured tools when possible and check the frame again before finishing.
