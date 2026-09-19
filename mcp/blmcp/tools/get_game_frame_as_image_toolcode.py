# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender-side implementation shared by game-frame image tools."""

__all__ = ("Params", "Result", "main")

from typing import NamedTuple


class Params(NamedTuple):
    frame: int
    max_dimension: int
    output_path: str


class Result(NamedTuple):
    status: str
    frame: int
    width: int | None = None
    height: int | None = None
    message: str | None = None


def main(params: Params) -> Result:
    import bpy  # pylint: disable=import-error,no-name-in-module

    scene = bpy.context.scene
    render = scene.render
    if params.frame < scene.frame_start or params.frame > scene.frame_end:
        return Result(
            status="error",
            frame=params.frame,
            message="frame is outside the active scene frame range",
        )

    old_frame = scene.frame_current
    old_settings = (
        render.filepath,
        render.resolution_x,
        render.resolution_y,
        render.resolution_percentage,
        render.image_settings.file_format,
        render.image_settings.color_mode,
        render.image_settings.color_depth,
        render.use_file_extension,
    )
    try:
        scene.frame_set(params.frame)
        render.filepath = params.output_path
        if params.max_dimension > 0:
            percentage = render.resolution_percentage / 100.0
            base_x = max(1, int(round(render.resolution_x * percentage)))
            base_y = max(1, int(round(render.resolution_y * percentage)))
            largest = max(base_x, base_y)
            scale = min(1.0, params.max_dimension / float(largest))
            render.resolution_x = max(1, int(round(base_x * scale)))
            render.resolution_y = max(1, int(round(base_y * scale)))
            render.resolution_percentage = 100
        width = max(1, int(round(render.resolution_x * render.resolution_percentage / 100.0)))
        height = max(1, int(round(render.resolution_y * render.resolution_percentage / 100.0)))
        render.image_settings.file_format = "PNG"
        render.image_settings.color_mode = "RGBA"
        render.image_settings.color_depth = "8"
        render.use_file_extension = True
        bpy.ops.render.render(write_still=False)
        bpy.data.images["Render Result"].save_render(filepath=params.output_path, scene=scene)
        return Result(status="ok", frame=params.frame, width=width, height=height)
    finally:
        (
            render.filepath,
            render.resolution_x,
            render.resolution_y,
            render.resolution_percentage,
            render.image_settings.file_format,
            render.image_settings.color_mode,
            render.image_settings.color_depth,
            render.use_file_extension,
        ) = old_settings
        scene.frame_set(old_frame)
