# Blender Game MCP roadmap

This fork keeps the Blender Lab / bpy.dev architecture and adds a focused
game-asset workflow on top of Blender 5.2 LTS.

## v0.1 - visual QA foundation

- `configure_game_scene`: deterministic resolution, FPS, frame range,
  PNG/RGBA, transparent film and orthographic game camera
- `inspect_game_scene`: compact readiness report instead of scene dumps
- `get_game_frame_as_image`: render one requested frame back to the agent
  for visual inspection while restoring the user's current frame/settings
- `show_game_frame_preview`: ChatGPT-normal-Chat inline PNG preview through the
  shared MCP Apps UI contract, with concise model-visible render metadata and
  the image payload confined to UI-only tool-result metadata
- `ui://blender-game/frame-preview.html`: responsive checkerboard-backed frame
  viewer registered as `text/html;profile=mcp-app`
- game-specific agent guidance
- regression tests for the structured scene setup

## v0.2 - animation tools

- typed keyframe helpers for transform, shape keys and common 2D deformation
- loop-boundary inspection
- animation checkpoints before broad edits
- Grease Pencil 5.2 helpers where they materially improve 2D workflows

## v0.3 - batch visual QA

- multi-frame contact sheet
- optional reference-image comparison
- deterministic frame sampling for idle / attack / hit / UI animations

## v0.4 - game export

- background PNG RGBA sequence rendering from saved `.blend`
- workspace/output allowlist
- sprite-sheet packing
- `animation.json` metadata: FPS, frame order, canvas size, loop flag and pivot
- Unity and Godot import presets

## v0.5 - ChatGPT Desktop plugin (in progress)

- package MCP + game-asset skill for ChatGPT Desktop
- keep normal Chat / GPT-5.6 Sol High as the primary agent
- use Work/Codex mainly for development and maintenance
- validate the MCP Apps frame preview over the Secure MCP Tunnel in normal Chat
