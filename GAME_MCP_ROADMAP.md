# Blender Game MCP roadmap

This fork keeps the Blender Lab / bpy.dev architecture and adds a focused
game-asset workflow on top of Blender 5.2 LTS.

## v0.1 - visual QA foundation

- `configure_game_scene`: deterministic resolution, FPS, frame range,
  PNG/RGBA, transparent film and orthographic game camera
- `inspect_game_scene`: compact readiness report instead of scene dumps
- `get_game_frame_as_image`: render one requested frame back to the agent
  for visual inspection while restoring the user's current frame/settings
- game-specific agent guidance
- regression tests for the structured scene setup

## v0.2 - animation tools (in progress)

Implemented on `game-mcp-v0.2-animation`:

- `set_game_transform_keyframes` for bounded batch location, Euler rotation
  and scale keys
- `inspect_game_transform_frames` for evaluated loop/timing samples with
  current-frame restoration

Still planned:

- shape-key helpers for squash/stretch and other 2D deformation
- interpolation/easing controls validated against Blender 5.2
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

## v0.5 - ChatGPT Desktop plugin

- package MCP + game-asset skill for ChatGPT Desktop
- keep normal Chat / GPT-5.6 Sol High as the primary agent
- use Work/Codex mainly for development and maintenance
