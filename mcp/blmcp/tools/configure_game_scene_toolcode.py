# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tool-code for configuring a scene for 2D game-asset rendering."""

__all__ = ("Params", "Result", "main")

from typing import NamedTuple


class Params(NamedTuple):
    width: int
    height: int
    fps: int
    frame_start: int
    frame_end: int
    transparent: bool
    camera_name: str | None
    create_camera_if_missing: bool
    orthographic_scale: float


class Result(NamedTuple):
    status: str
    scene: str | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    transparent: bool | None = None
    camera: str | None = None
    camera_created: bool = False
    camera_type: str | None = None
    orthographic_scale: float | None = None
    message: str | None = None


def main(params: Params) -> Result:
    import bpy  # pylint: disable=import-error,no-name-in-module

    if not 1 <= params.width <= 16384 or not 1 <= params.height <= 16384:
        return Result(status="error", message="width and height must be between 1 and 16384 pixels")
    if not 1 <= params.fps <= 240:
        return Result(status="error", message="fps must be between 1 and 240")
    if params.frame_end < params.frame_start:
        return Result(status="error", message="frame_end must be greater than or equal to frame_start")
    if params.orthographic_scale <= 0:
        return Result(status="error", message="orthographic_scale must be greater than zero")

    scene = bpy.context.scene

    # Resolve every fallible camera precondition before changing render or frame
    # settings. Invalid user input should fail without leaving a half-configured
    # scene behind.
    camera = None
    if params.camera_name:
        camera = bpy.data.objects.get(params.camera_name)
        if camera is None:
            return Result(status="error", message="camera_name was not found: " + params.camera_name)
        if camera.type != "CAMERA":
            return Result(
                status="error",
                message="camera_name does not refer to a CAMERA object: " + params.camera_name,
            )
    else:
        camera = scene.camera

    if camera is None and not params.create_camera_if_missing:
        return Result(
            status="error",
            scene=scene.name,
            message="No scene camera is assigned. Pass camera_name or allow camera creation.",
        )

    created = False
    if camera is None:
        camera_data = bpy.data.cameras.new("GameCamera")
        camera = bpy.data.objects.new("GameCamera", camera_data)
        scene.collection.objects.link(camera)
        camera.location = (0.0, 0.0, 10.0)
        camera.rotation_euler = (0.0, 0.0, 0.0)
        created = True

    render = scene.render
    render.resolution_x = params.width
    render.resolution_y = params.height
    render.resolution_percentage = 100
    render.fps = params.fps
    render.fps_base = 1.0
    render.film_transparent = params.transparent
    render.image_settings.file_format = "PNG"
    render.image_settings.color_mode = "RGBA"
    render.image_settings.color_depth = "8"
    render.use_file_extension = True
    scene.frame_start = params.frame_start
    scene.frame_end = params.frame_end
    scene.camera = camera

    camera.data.type = "ORTHO"
    camera.data.ortho_scale = params.orthographic_scale

    return Result(
        status="ok",
        scene=scene.name,
        width=render.resolution_x,
        height=render.resolution_y,
        fps=render.fps,
        frame_start=scene.frame_start,
        frame_end=scene.frame_end,
        transparent=render.film_transparent,
        camera=camera.name,
        camera_created=created,
        camera_type=camera.data.type,
        orthographic_scale=camera.data.ortho_scale,
    )
