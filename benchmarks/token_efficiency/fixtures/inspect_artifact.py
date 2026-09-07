# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Inspect benchmark artifacts inside Blender or standalone bpy."""

__all__ = ("main",)

import argparse
import json
import sys
from pathlib import Path

import bpy  # pylint: disable=import-error  # Available only in Blender/bpy runtimes.


def _rounded(values):
    return [round(float(value), 6) for value in values]


def _object_record(obj):
    return {
        "name": obj.name,
        "type": obj.type,
        "dimensions": _rounded(obj.dimensions),
        "location": _rounded(obj.location),
        "rotation_euler": _rounded(obj.rotation_euler),
        "scale": _rounded(obj.scale),
        "vertices": len(obj.data.vertices) if obj.type == "MESH" else None,
        "polygons": len(obj.data.polygons) if obj.type == "MESH" else None,
    }


def _unique_mesh_positions(obj):
    return len({tuple(_rounded(vertex.co)) for vertex in obj.data.vertices})


def _open_blend(path):
    bpy.ops.wm.open_mainfile(filepath=str(path))


def _scene_fixture_semantics():
    depsgraph = bpy.context.evaluated_depsgraph_get()
    meshes = [
        (len(obj.evaluated_get(depsgraph).data.polygons), obj.name)
        for obj in bpy.data.objects
        if obj.type == "MESH"
    ]
    polygon_count, object_name = max(meshes)
    missing = sorted(
        image.filepath
        for image in bpy.data.images
        if image.source == "FILE"
        and not Path(bpy.path.abspath(image.filepath)).exists()
    )
    return {
        "counts": {
            "objects": len(bpy.data.objects),
            "meshes": len(bpy.data.meshes),
            "materials": len(bpy.data.materials),
            "images": len(bpy.data.images),
            "collections": len(bpy.data.collections),
        },
        "highest_evaluated_mesh": {
            "object": object_name,
            "polygon_count": polygon_count,
        },
        "missing": missing,
    }


def _fixture_semantics(artifact):
    """Return the canonical semantic oracle for a source fixture."""
    _open_blend(artifact)
    if artifact.name == "scene.blend":
        return _scene_fixture_semantics()
    if artifact.name == "mutation_source.blend":
        return {
            "objects": len(bpy.data.objects),
            "benchmark_source": bpy.context.scene.get("benchmark_source"),
        }
    if artifact.name == "batch_source.blend":
        return {
            "object_count": len(bpy.data.objects),
            "objects": [
                {
                    "name": obj.name,
                    "type": obj.type,
                    "location": _rounded(obj.location),
                    "vertices": len(obj.data.vertices) if obj.type == "MESH" else None,
                    "polygons": len(obj.data.polygons) if obj.type == "MESH" else None,
                }
                for obj in sorted(bpy.data.objects, key=lambda item: item.name)
            ],
        }
    if artifact.name == "roundtrip_source.blend":
        return {
            "root": "RootNode" if bpy.data.objects.get("RootNode") else None,
            "meshes": {
                name: _unique_mesh_positions(bpy.data.objects[name])
                for name in ("RoundtripCube", "RoundtripTriangle")
                if name in bpy.data.objects
            },
        }
    if artifact.name == "render_source.blend":
        scene = bpy.context.scene
        return {
            "camera": scene.camera.name if scene.camera else None,
            "subject": "RenderSubject"
            if bpy.data.objects.get("RenderSubject")
            else None,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "format": scene.render.image_settings.file_format,
            "transparent": scene.render.film_transparent,
            "engine": scene.render.engine,
        }
    raise ValueError(f"unknown fixture: {artifact.name}")


# One dispatch point intentionally owns bpy state transitions for all artifact kinds.
def inspect(  # pylint: disable=too-many-locals,too-many-return-statements,too-many-branches
    task_id, artifact
):
    """Inspect one task artifact and return JSON-compatible semantics."""
    if task_id == "fixture_semantics":
        return _fixture_semantics(artifact)
    if task_id == "glb_roundtrip_structure":
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(artifact))
        objects = sorted(bpy.data.objects, key=lambda item: item.name)
        return {
            "objects": [
                {
                    "name": obj.name,
                    "type": obj.type,
                    "parent": obj.parent.name if obj.parent else None,
                    "unique_vertex_positions": _unique_mesh_positions(obj)
                    if obj.type == "MESH"
                    else None,
                }
                for obj in objects
            ]
        }
    if task_id == "low_resolution_render":
        bpy.ops.wm.read_factory_settings(use_empty=True)
        image = bpy.data.images.load(str(artifact), check_existing=False)
        width, height = image.size
        pixels = list(image.pixels)
        channels = image.channels
        alpha_index = 3 if channels >= 4 else None
        non_background = 0
        if alpha_index is not None:
            non_background = sum(
                1
                for index in range(alpha_index, len(pixels), channels)
                if pixels[index] > 0.01
            )
        center_offset = ((height // 2) * width + (width // 2)) * channels
        rgba = [
            round(pixels[center_offset + index] * 255)
            for index in range(min(channels, 4))
        ]
        if channels == 3:
            rgba.append(255)
        return {
            "format": image.file_format,
            "width": width,
            "height": height,
            "channels": channels,
            "non_background_pixels": non_background,
            "center_rgba": rgba,
        }

    _open_blend(artifact)
    if task_id in {
        "data_block_counts",
        "evaluated_mesh_polygons",
        "find_missing_external_files",
    }:
        if task_id == "data_block_counts":
            return {
                "objects": len(bpy.data.objects),
                "meshes": len(bpy.data.meshes),
                "materials": len(bpy.data.materials),
                "images": len(bpy.data.images),
                "collections": len(bpy.data.collections),
            }
        if task_id == "evaluated_mesh_polygons":
            depsgraph = bpy.context.evaluated_depsgraph_get()
            meshes = []
            for obj in bpy.data.objects:
                if obj.type == "MESH":
                    evaluated = obj.evaluated_get(depsgraph)
                    meshes.append((len(evaluated.data.polygons), obj.name))
            count, name = max(meshes)
            return {"object": name, "polygon_count": count}
        missing = []
        for image in bpy.data.images:
            if (
                image.source == "FILE"
                and not Path(bpy.path.abspath(image.filepath)).exists()
            ):
                missing.append(image.filepath)
        return {"missing": sorted(missing)}
    if task_id == "create_exact_cube":
        obj = bpy.data.objects.get("BenchmarkCube")
        return {
            "object": _object_record(obj) if obj else None,
            "object_count": len(bpy.data.objects),
        }
    if task_id == "assign_principled_material":
        obj = bpy.data.objects.get("MaterialCube")
        material = bpy.data.materials.get("BenchmarkPrincipled")
        # Scope: one direct Principled BSDF -> active Material Output Surface.
        node = (
            next(
                (
                    link.from_node
                    for link in material.node_tree.links
                    if link.to_node.type == "OUTPUT_MATERIAL"
                    and link.to_node.is_active_output
                    and link.to_socket.name == "Surface"
                    and link.from_node.type == "BSDF_PRINCIPLED"
                    and link.from_socket.name == "BSDF"
                    and link.is_valid
                ),
                None,
            )
            if material and material.use_nodes
            else None
        )
        return {
            "object": obj.name if obj else None,
            "object_record": _object_record(obj) if obj else None,
            "object_count": len(bpy.data.objects),
            "assigned_materials": [
                slot.material.name if slot.material else None
                for slot in obj.material_slots
            ]
            if obj
            else [],
            "material": {
                "name": material.name,
                "node_type": node.type,
                "base_color": _rounded(node.inputs["Base Color"].default_value),
                "metallic": round(float(node.inputs["Metallic"].default_value), 6),
                "roughness": round(float(node.inputs["Roughness"].default_value), 6),
                "surface_connected": any(
                    link.to_node.type == "OUTPUT_MATERIAL"
                    and link.to_socket.name == "Surface"
                    for link in material.node_tree.links
                    if link.from_node == node
                ),
            }
            if node
            else None,
        }
    if task_id == "parent_world_transforms":
        parent = bpy.data.objects.get("BenchmarkParent")
        child = bpy.data.objects.get("BenchmarkChild")
        return {
            "parent": parent.name if parent else None,
            "child": child.name if child else None,
            "child_parent": child.parent.name if child and child.parent else None,
            "parent_type": parent.type if parent else None,
            "child_type": child.type if child else None,
            "parent_world_translation": _rounded(parent.matrix_world.translation)
            if parent
            else None,
            "child_world_translation": _rounded(child.matrix_world.translation)
            if child
            else None,
            "object_count": len(bpy.data.objects),
        }
    if task_id == "recover_stale_operator":
        light = bpy.data.objects.get("RecoveredPointLight")
        return {
            "object": {
                "name": light.name,
                "type": light.type,
                "location": _rounded(light.location),
                "light_type": light.data.type,
                "energy": round(float(light.data.energy), 6),
            }
            if light and light.type == "LIGHT"
            else None,
            "object_count": len(bpy.data.objects),
        }
    if task_id == "batch_edit_save_as":
        return {
            "object_count": len(bpy.data.objects),
            "objects": [
                {
                    "name": obj.name,
                    "type": obj.type,
                    "location": _rounded(obj.location),
                    "vertices": len(obj.data.vertices) if obj.type == "MESH" else None,
                    "polygons": len(obj.data.polygons) if obj.type == "MESH" else None,
                }
                for obj in sorted(bpy.data.objects, key=lambda item: item.name)
            ],
            "filepath_name": Path(bpy.data.filepath).name,
        }
    raise ValueError(f"unknown task: {task_id}")


def main():
    """Run the inspector protocol and emit exactly one final result frame."""
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = {"ok": True, "data": inspect(args.task, args.artifact)}
    except Exception as ex:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        payload = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
    print("__TOKEN_EFFICIENCY_INSPECT__" + json.dumps(payload, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
