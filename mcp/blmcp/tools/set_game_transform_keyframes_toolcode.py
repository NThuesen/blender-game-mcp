# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tool-code for deterministic transform keyframes."""

__all__ = ("Params", "Result", "main")

from typing import NamedTuple


_ALLOWED_PATHS = frozenset({"location", "rotation_euler", "scale"})
_MAX_KEYFRAMES_PER_CALL = 256


class Params(NamedTuple):
    object_name: str
    data_path: str
    frames: list[int]
    values: list[list[float]]


class Result(NamedTuple):
    status: str
    object_name: str | None = None
    data_path: str | None = None
    inserted: int = 0
    frames: list[int] | None = None
    message: str | None = None


def main(params: Params) -> Result:
    import bpy  # pylint: disable=import-error,no-name-in-module

    if params.data_path not in _ALLOWED_PATHS:
        return Result(
            status="error",
            message="data_path must be one of: location, rotation_euler, scale",
        )
    if not params.frames:
        return Result(status="error", message="frames must not be empty")
    if len(params.frames) != len(params.values):
        return Result(status="error", message="frames and values must have the same length")
    if len(params.frames) > _MAX_KEYFRAMES_PER_CALL:
        return Result(
            status="error",
            message="too many keyframes in one call; maximum is 256",
        )
    if len(set(params.frames)) != len(params.frames):
        return Result(status="error", message="frames must not contain duplicates")

    scene = bpy.context.scene
    for frame in params.frames:
        if frame < scene.frame_start or frame > scene.frame_end:
            return Result(
                status="error",
                message="frame {:d} is outside the active scene frame range".format(frame),
            )

    for value in params.values:
        if len(value) != 3:
            return Result(
                status="error",
                message="every transform value must have exactly three numbers",
            )

    obj = bpy.data.objects.get(params.object_name)
    if obj is None:
        return Result(status="error", message="object was not found: " + params.object_name)

    if params.data_path == "rotation_euler" and obj.rotation_mode in {"QUATERNION", "AXIS_ANGLE"}:
        return Result(
            status="error",
            object_name=obj.name,
            data_path=params.data_path,
            message=(
                "rotation_euler cannot drive an object whose rotation_mode is "
                + str(obj.rotation_mode)
                + "; change rotation mode deliberately before inserting Euler keys"
            ),
        )

    current_frame = scene.frame_current
    inserted = 0
    inserted_frames: list[int] = []
    try:
        for frame, value in zip(params.frames, params.values, strict=True):
            setattr(obj, params.data_path, tuple(float(component) for component in value))
            ok = obj.keyframe_insert(
                data_path=params.data_path,
                frame=frame,
                group="Game",
            )
            if not ok:
                return Result(
                    status="error",
                    object_name=obj.name,
                    data_path=params.data_path,
                    inserted=inserted,
                    frames=inserted_frames,
                    message="Blender refused keyframe insertion at frame {:d}".format(frame),
                )
            inserted += 1
            inserted_frames.append(frame)
    finally:
        scene.frame_set(current_frame)

    return Result(
        status="ok",
        object_name=obj.name,
        data_path=params.data_path,
        inserted=inserted,
        frames=inserted_frames,
    )
