# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tool-code for inspecting 2D game-asset render readiness."""

__all__ = ("Result", "main")

from typing import NamedTuple


class Result(NamedTuple):
    status: str
    scene: str
    width: int
    height: int
    fps: int
    frame_start: int
    frame_end: int
    frame_current: int
    transparent: bool
    file_format: str
    color_mode: str
    color_depth: str
    render_engine: str
    camera: str | None
    camera_type: str | None
    orthographic_scale: float | None
    ready_for_png_sprite_render: bool
    warnings: list[str]


def main(_params: None) -> Result:
    import bpy  # pylint: disable=import-error,no-name-in-module

    scene = bpy.context.scene
    render = scene.render
    camera = scene.camera
    warnings: list[str] = []

    if camera is None:
        warnings.append("No active scene camera.")
    elif camera.type != "CAMERA":
        warnings.append("Active scene camera reference is not a CAMERA object.")
    elif camera.data.type != "ORTHO":
        warnings.append("Camera is not orthographic; sprite framing may vary with depth.")

    if render.image_settings.file_format != "PNG":
        warnings.append("Render format is not PNG.")
    if render.image_settings.color_mode != "RGBA":
        warnings.append("PNG color mode is not RGBA; alpha may be missing.")
    if not render.film_transparent:
        warnings.append("Film transparency is disabled.")
    if render.resolution_percentage != 100:
        warnings.append("Resolution percentage is not 100.")
    if scene.frame_end < scene.frame_start:
        warnings.append("Frame range is invalid.")

    camera_name = camera.name if camera is not None else None
    camera_type = camera.data.type if camera is not None and camera.type == "CAMERA" else None
    ortho_scale = (
        float(camera.data.ortho_scale)
        if camera is not None and camera.type == "CAMERA" and camera.data.type == "ORTHO"
        else None
    )

    ready = len(warnings) == 0
    return Result(
        status="ok",
        scene=scene.name,
        width=render.resolution_x,
        height=render.resolution_y,
        fps=render.fps,
        frame_start=scene.frame_start,
        frame_end=scene.frame_end,
        frame_current=scene.frame_current,
        transparent=render.film_transparent,
        file_format=render.image_settings.file_format,
        color_mode=render.image_settings.color_mode,
        color_depth=render.image_settings.color_depth,
        render_engine=render.engine,
        camera=camera_name,
        camera_type=camera_type,
        orthographic_scale=ortho_scale,
        ready_for_png_sprite_render=ready,
        warnings=warnings,
    )
