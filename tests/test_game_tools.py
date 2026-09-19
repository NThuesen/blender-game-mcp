# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the structured Blender Game MCP scene tools."""

__all__ = ()

import os
import sys
import types
import unittest
from unittest import mock

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_DIR = os.path.join(_REPO_DIR, "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from blmcp.tools.configure_game_scene_toolcode import Params as ConfigureParams
from blmcp.tools.configure_game_scene_toolcode import main as configure_main
from blmcp.tools.inspect_game_scene_toolcode import main as inspect_main
from blmcp.tools.inspect_game_transform_frames_toolcode import Params as InspectFramesParams
from blmcp.tools.inspect_game_transform_frames_toolcode import main as inspect_frames_main
from blmcp.tools.set_game_transform_keyframes_toolcode import Params as KeyframeParams
from blmcp.tools.set_game_transform_keyframes_toolcode import main as keyframe_main


class _ImageSettings:
    def __init__(self) -> None:
        self.file_format = "JPEG"
        self.color_mode = "RGB"
        self.color_depth = "8"


class _Render:
    def __init__(self) -> None:
        self.resolution_x = 1920
        self.resolution_y = 1080
        self.resolution_percentage = 50
        self.fps = 30
        self.fps_base = 1.0
        self.film_transparent = False
        self.image_settings = _ImageSettings()
        self.use_file_extension = False
        self.engine = "BLENDER_EEVEE_NEXT"


class _CameraData:
    def __init__(self, name: str) -> None:
        self.name = name
        self.type = "PERSP"
        self.ortho_scale = 6.0


class _Object:
    def __init__(self, name: str, data: _CameraData) -> None:
        self.name = name
        self.data = data
        self.type = "CAMERA"
        self.location = (0.0, 0.0, 0.0)
        self.rotation_euler = (0.0, 0.0, 0.0)
        self.scale = (1.0, 1.0, 1.0)
        self.keyed: list[tuple[str, int, str]] = []

    def keyframe_insert(self, data_path: str, frame: int, group: str) -> bool:
        self.keyed.append((data_path, frame, group))
        return True


class _ObjectCollection:
    def __init__(self) -> None:
        self.linked: list[_Object] = []

    def link(self, obj: _Object) -> None:
        self.linked.append(obj)


class _Scene:
    def __init__(self) -> None:
        self.name = "Scene"
        self.render = _Render()
        self.frame_start = 1
        self.frame_end = 250
        self.frame_current = 1
        self.camera = None
        self.collection = types.SimpleNamespace(objects=_ObjectCollection())

    def frame_set(self, frame: int) -> None:
        self.frame_current = frame


def _fake_bpy(scene: _Scene) -> types.SimpleNamespace:
    objects_by_name: dict[str, _Object] = {}

    def camera_new(name: str) -> _CameraData:
        return _CameraData(name)

    def object_new(name: str, data: _CameraData) -> _Object:
        obj = _Object(name, data)
        objects_by_name[name] = obj
        return obj

    return types.SimpleNamespace(
        context=types.SimpleNamespace(scene=scene),
        data=types.SimpleNamespace(
            cameras=types.SimpleNamespace(new=camera_new),
            objects=types.SimpleNamespace(
                new=object_new,
                get=objects_by_name.get,
            ),
        ),
    )


class TestGameSceneTools(unittest.TestCase):
    def test_configure_creates_orthographic_game_camera(self) -> None:
        scene = _Scene()
        fake_bpy = _fake_bpy(scene)
        params = ConfigureParams(
            width=512,
            height=512,
            fps=24,
            frame_start=1,
            frame_end=24,
            transparent=True,
            camera_name=None,
            create_camera_if_missing=True,
            orthographic_scale=4.5,
        )

        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            result = configure_main(params)

        self.assertEqual(result.status, "ok")
        self.assertEqual((scene.render.resolution_x, scene.render.resolution_y), (512, 512))
        self.assertEqual(scene.render.resolution_percentage, 100)
        self.assertEqual(scene.render.fps, 24)
        self.assertEqual((scene.frame_start, scene.frame_end), (1, 24))
        self.assertTrue(scene.render.film_transparent)
        self.assertEqual(scene.render.image_settings.file_format, "PNG")
        self.assertEqual(scene.render.image_settings.color_mode, "RGBA")
        self.assertEqual(scene.render.image_settings.color_depth, "8")
        self.assertIsNotNone(scene.camera)
        self.assertEqual(scene.camera.data.type, "ORTHO")
        self.assertEqual(scene.camera.data.ortho_scale, 4.5)
        self.assertEqual(scene.camera.location, (0.0, 0.0, 10.0))
        self.assertTrue(result.camera_created)

    def test_configure_rejects_invalid_frame_range_without_mutating_scene(self) -> None:
        scene = _Scene()
        fake_bpy = _fake_bpy(scene)
        params = ConfigureParams(
            width=512,
            height=512,
            fps=24,
            frame_start=25,
            frame_end=24,
            transparent=True,
            camera_name=None,
            create_camera_if_missing=True,
            orthographic_scale=4.5,
        )

        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            result = configure_main(params)

        self.assertEqual(result.status, "error")
        self.assertEqual(scene.frame_start, 1)
        self.assertEqual(scene.frame_end, 250)
        self.assertIsNone(scene.camera)

    def test_missing_named_camera_fails_before_render_mutation(self) -> None:
        scene = _Scene()
        fake_bpy = _fake_bpy(scene)
        params = ConfigureParams(
            width=512,
            height=512,
            fps=24,
            frame_start=1,
            frame_end=24,
            transparent=True,
            camera_name="MissingCamera",
            create_camera_if_missing=True,
            orthographic_scale=4.5,
        )

        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            result = configure_main(params)

        self.assertEqual(result.status, "error")
        self.assertEqual(scene.render.resolution_x, 1920)
        self.assertEqual(scene.render.resolution_y, 1080)
        self.assertEqual(scene.render.image_settings.file_format, "JPEG")
        self.assertIsNone(scene.camera)

    def test_inspect_reports_ready_after_configuration(self) -> None:
        scene = _Scene()
        fake_bpy = _fake_bpy(scene)
        params = ConfigureParams(
            width=256,
            height=256,
            fps=12,
            frame_start=1,
            frame_end=12,
            transparent=True,
            camera_name=None,
            create_camera_if_missing=True,
            orthographic_scale=3.0,
        )

        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            configured = configure_main(params)
            inspected = inspect_main(None)

        self.assertEqual(configured.status, "ok")
        self.assertEqual(inspected.status, "ok")
        self.assertTrue(inspected.ready_for_png_sprite_render)
        self.assertEqual(inspected.warnings, [])
        self.assertEqual(inspected.camera_type, "ORTHO")
        self.assertEqual(inspected.width, 256)
        self.assertEqual(inspected.fps, 12)

    def test_transform_keyframes_are_batched_and_frame_is_restored(self) -> None:
        scene = _Scene()
        fake_bpy = _fake_bpy(scene)
        obj = _Object("Hero", _CameraData("Unused"))
        obj.type = "MESH"
        fake_bpy.data.objects.get = lambda name: obj if name == "Hero" else None
        scene.frame_current = 7
        params = KeyframeParams(
            object_name="Hero",
            data_path="scale",
            frames=[1, 12, 24],
            values=[
                [1.0, 1.0, 1.0],
                [1.1, 0.9, 1.0],
                [1.0, 1.0, 1.0],
            ],
        )

        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            result = keyframe_main(params)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.inserted, 3)
        self.assertEqual(
            obj.keyed,
            [
                ("scale", 1, "Game"),
                ("scale", 12, "Game"),
                ("scale", 24, "Game"),
            ],
        )
        self.assertEqual(scene.frame_current, 7)

    def test_transform_keyframes_reject_duplicates_before_writes(self) -> None:
        scene = _Scene()
        fake_bpy = _fake_bpy(scene)
        obj = _Object("Hero", _CameraData("Unused"))
        obj.type = "MESH"
        fake_bpy.data.objects.get = lambda name: obj if name == "Hero" else None
        params = KeyframeParams(
            object_name="Hero",
            data_path="location",
            frames=[1, 1],
            values=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        )

        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            result = keyframe_main(params)

        self.assertEqual(result.status, "error")
        self.assertEqual(obj.keyed, [])

    def test_transform_sampling_restores_current_frame(self) -> None:
        scene = _Scene()
        fake_bpy = _fake_bpy(scene)
        obj = _Object("Hero", _CameraData("Unused"))
        obj.type = "MESH"
        fake_bpy.data.objects.get = lambda name: obj if name == "Hero" else None
        scene.frame_current = 8

        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            result = inspect_frames_main(
                InspectFramesParams(object_name="Hero", frames=[1, 12, 24])
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual([sample["frame"] for sample in result.samples], [1, 12, 24])
        self.assertEqual(scene.frame_current, 8)
        self.assertEqual(result.restored_frame, 8)

    def test_inspect_warns_for_default_non_game_settings(self) -> None:
        scene = _Scene()
        fake_bpy = _fake_bpy(scene)

        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            result = inspect_main(None)

        self.assertFalse(result.ready_for_png_sprite_render)
        self.assertIn("No active scene camera.", result.warnings)
        self.assertIn("Render format is not PNG.", result.warnings)
        self.assertIn("PNG color mode is not RGBA; alpha may be missing.", result.warnings)
        self.assertIn("Film transparency is disabled.", result.warnings)


if __name__ == "__main__":
    unittest.main()
