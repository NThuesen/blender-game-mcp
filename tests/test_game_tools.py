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
from blmcp.tools.get_game_frame_as_image_toolcode import Params as FrameParams
from blmcp.tools.get_game_frame_as_image_toolcode import main as render_frame_main
from blmcp.tools.inspect_game_scene_toolcode import main as inspect_main


class _ImageSettings:
    def __init__(self) -> None:
        self.file_format = "JPEG"
        self.color_mode = "RGB"
        self.color_depth = "8"


class _Render:
    def __init__(self) -> None:
        self.filepath = "//original.png"
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
            images={
                "Render Result": types.SimpleNamespace(
                    save_render=lambda *, filepath, scene: None,
                ),
            },
            objects=types.SimpleNamespace(
                new=object_new,
                get=objects_by_name.get,
            ),
        ),
        ops=types.SimpleNamespace(
            render=types.SimpleNamespace(render=lambda *, write_still: None),
        ),
    )


class TestGameSceneTools(unittest.TestCase):
    def test_frame_render_restores_frame_and_render_settings(self) -> None:
        scene = _Scene()
        scene.frame_current = 17
        fake_bpy = _fake_bpy(scene)
        original = (
            scene.frame_current,
            scene.render.filepath,
            scene.render.resolution_x,
            scene.render.resolution_y,
            scene.render.resolution_percentage,
            scene.render.image_settings.file_format,
            scene.render.image_settings.color_mode,
            scene.render.image_settings.color_depth,
            scene.render.use_file_extension,
        )

        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            result = render_frame_main(FrameParams(frame=24, max_dimension=512, output_path="preview.png"))

        self.assertEqual(result.status, "ok")
        self.assertEqual((result.width, result.height), (512, 288))
        restored = (
            scene.frame_current,
            scene.render.filepath,
            scene.render.resolution_x,
            scene.render.resolution_y,
            scene.render.resolution_percentage,
            scene.render.image_settings.file_format,
            scene.render.image_settings.color_mode,
            scene.render.image_settings.color_depth,
            scene.render.use_file_extension,
        )
        self.assertEqual(restored, original)

    def test_frame_render_rejects_out_of_range_frame_without_mutation(self) -> None:
        scene = _Scene()
        scene.frame_current = 9
        fake_bpy = _fake_bpy(scene)

        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            result = render_frame_main(FrameParams(frame=251, max_dimension=512, output_path="preview.png"))

        self.assertEqual(result.status, "error")
        self.assertIn("outside the active scene frame range", str(result.message))
        self.assertEqual(scene.frame_current, 9)
        self.assertEqual(scene.render.filepath, "//original.png")
        self.assertEqual(scene.render.resolution_percentage, 50)

    def test_frame_render_restores_state_when_blender_render_fails(self) -> None:
        scene = _Scene()
        scene.frame_current = 11
        fake_bpy = _fake_bpy(scene)

        def fail_render(*, write_still: bool) -> None:
            self.assertFalse(write_still)
            raise RuntimeError("render failed")

        fake_bpy.ops.render.render = fail_render
        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            with self.assertRaisesRegex(RuntimeError, "render failed"):
                render_frame_main(FrameParams(frame=12, max_dimension=256, output_path="preview.png"))

        self.assertEqual(scene.frame_current, 11)
        self.assertEqual(scene.render.filepath, "//original.png")
        self.assertEqual((scene.render.resolution_x, scene.render.resolution_y), (1920, 1080))
        self.assertEqual(scene.render.resolution_percentage, 50)
        self.assertEqual(scene.render.image_settings.file_format, "JPEG")
        self.assertEqual(scene.render.image_settings.color_mode, "RGB")
        self.assertFalse(scene.render.use_file_extension)

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
