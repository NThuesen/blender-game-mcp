# Game asset workflow

The game extension is intentionally structured around a short agent loop:

1. `inspect_game_scene` before changing render/camera assumptions.
2. `configure_game_scene` when a deterministic sprite/VFX canvas is needed.
3. Create or edit artwork/animation with narrow Blender tools where possible.
4. Use `get_game_frame_as_image` to inspect important animation poses.
5. Correct visual problems before any batch export.

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

## Structured transform animation

For common sprite/cut-out motion, prefer `set_game_transform_keyframes` over
arbitrary Python. The v0.2 tool intentionally supports only location,
rotation_euler and scale and requires explicit frame/value batches.

Use `inspect_game_transform_frames` after broad changes to sample the
evaluated transforms at important frames while restoring the user's current
frame. This avoids depending on Blender's internal Action representation just
to verify timing and loop boundaries.

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
