# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tool-code for sampling evaluated object transforms across frames."""

__all__ = ("Params", "Result", "main")

from typing import NamedTuple


_MAX_SAMPLE_FRAMES = 64


class Params(NamedTuple):
    object_name: str
    frames: list[int]


class Result(NamedTuple):
    status: str
    object_name: str | None = None
    samples: list[dict[str, object]] | None = None
    restored_frame: int | None = None
    message: str | None = None


def _vector(values: object) -> list[float]:
    return [float(component) for component in values]


def main(params: Params) -> Result:
    import bpy  # pylint: disable=import-error,no-name-in-module

    if not params.frames:
        return Result(status="error", message="frames must not be empty")
    if len(params.frames) > _MAX_SAMPLE_FRAMES:
        return Result(
            status="error",
            message="too many sample frames in one call; maximum is 64",
        )

    scene = bpy.context.scene
    for frame in params.frames:
        if frame < scene.frame_start or frame > scene.frame_end:
            return Result(
                status="error",
                message="frame {:d} is outside the active scene frame range".format(frame),
            )

    obj = bpy.data.objects.get(params.object_name)
    if obj is None:
        return Result(status="error", message="object was not found: " + params.object_name)

    current_frame = scene.frame_current
    samples: list[dict[str, object]] = []
    try:
        for frame in params.frames:
            scene.frame_set(frame)
            samples.append(
                {
                    "frame": frame,
                    "location": _vector(obj.location),
                    "rotation_mode": str(obj.rotation_mode),
                    "rotation_euler": _vector(obj.rotation_euler),
                    "scale": _vector(obj.scale),
                }
            )
    finally:
        scene.frame_set(current_frame)

    return Result(
        status="ok",
        object_name=obj.name,
        samples=samples,
        restored_frame=current_frame,
    )
