# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate compact source fixtures and optional oracle artifacts."""

# Source fixture values intentionally duplicate independent validator oracles.
# pylint: disable=duplicate-code

__all__ = ("main",)

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import bpy  # pylint: disable=import-error  # Available only in Blender/bpy runtimes.

_GENERATOR_VERSION = 1
_SOURCE_FILES = (
    "scene.blend",
    "mutation_source.blend",
    "batch_source.blend",
    "roundtrip_source.blend",
    "render_source.blend",
)
_ORACLE_STAGES = (
    "cube",
    "material",
    "hierarchy",
    "light",
    "batch",
    "glb",
    "render",
    "answers",
)


def _progress(stage, state, started=None):
    payload = {"stage": stage, "state": state}
    if started is not None:
        payload["seconds"] = round(time.monotonic() - started, 3)
    print(
        "__TOKEN_EFFICIENCY_GENERATE__" + json.dumps(payload, sort_keys=True),
        flush=True,
    )


def _write_ascii(path, text):
    """Atomically write ASCII without buffered I/O after Blender saves."""
    data = text.encode("ascii")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view) :]
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _cube(name, location=(0.0, 0.0, 0.0)):
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = name + "Mesh"
    return obj


def _triangle(name, location=(0.0, 0.0, 0.0)):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(
        [(-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)], [], [(0, 1, 2)]
    )
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    return obj


def _save(path):
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)


def _generate_scene(path):
    _clear()
    primary = bpy.data.collections.new("BenchmarkMain")
    secondary = bpy.data.collections.new("BenchmarkSecondary")
    bpy.context.scene.collection.children.link(primary)
    bpy.context.scene.collection.children.link(secondary)

    cube = _cube("SubdividedCube")
    for collection in list(cube.users_collection):
        collection.objects.unlink(cube)
    primary.objects.link(cube)
    modifier = cube.modifiers.new("EvaluatedSubdivision", "SUBSURF")
    modifier.subdivision_type = "SIMPLE"
    modifier.levels = 2
    modifier.render_levels = 2
    material = bpy.data.materials.new("FixtureMaterial")
    cube.data.materials.append(material)

    triangle = _triangle("Triangle")
    for collection in list(triangle.users_collection):
        collection.objects.unlink(triangle)
    primary.objects.link(triangle)

    hidden = _cube("HiddenMesh", (10.0, 0.0, 0.0))
    for collection in list(hidden.users_collection):
        collection.objects.unlink(hidden)
    secondary.objects.link(hidden)

    parent = bpy.data.objects.new("HierarchyParent", None)
    child = bpy.data.objects.new("HierarchyChild", None)
    carrier = bpy.data.objects.new("MissingImageCarrier", None)
    secondary.objects.link(parent)
    secondary.objects.link(child)
    secondary.objects.link(carrier)
    child.parent = parent

    missing_path = path.parent / "missing" / "textures" / "checker.png"
    missing_path.parent.mkdir(parents=True, exist_ok=True)
    generated = bpy.data.images.new("MissingCheckerGenerated", width=1, height=1)
    generated.pixels = (1.0, 0.0, 1.0, 1.0)
    generated.filepath_raw = str(missing_path)
    generated.file_format = "PNG"
    generated.save()
    bpy.data.images.remove(generated)
    image = bpy.data.images.load(str(missing_path), check_existing=False)
    image.name = "MissingChecker"
    image.filepath = "//missing/textures/checker.png"
    image.use_fake_user = True
    _save(path)
    missing_path.unlink()
    missing_path.parent.rmdir()
    missing_path.parent.parent.rmdir()


def _generate_mutation_source(path):
    _clear()
    bpy.context.scene["benchmark_source"] = "mutation_source_v1"
    _save(path)


def _generate_batch_source(path):
    _clear()
    for index in range(4):
        _cube(f"Batch_{index + 1:02d}", (float(index * 2), 0.0, 0.0))
    _save(path)


def _generate_roundtrip_source(path):
    _clear()
    root = bpy.data.objects.new("RootNode", None)
    bpy.context.scene.collection.objects.link(root)
    cube = _cube("RoundtripCube", (-1.5, 0.0, 0.0))
    triangle = _triangle("RoundtripTriangle", (1.5, 0.0, 0.0))
    cube.parent = root
    triangle.parent = root
    _save(path)


def _generate_render_source(path):
    _clear()
    scene = bpy.context.scene
    # Workbench avoids platform-specific Eevee shader compilation while still
    # exercising Blender's real render pipeline in both supported runtimes.
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 64
    scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.image_settings.color_depth = "8"
    scene.display.shading.light = "FLAT"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = False
    scene.display.shading.show_cavity = False
    scene.display.shading.show_specular_highlight = False

    cube = _cube("RenderSubject")
    cube.rotation_euler = (math.radians(18.0), 0.0, math.radians(25.0))
    material = bpy.data.materials.new("RenderRed")
    material.diffuse_color = (0.8, 0.05, 0.02, 1.0)
    node = material.node_tree.nodes.get("Principled BSDF")
    node.inputs["Base Color"].default_value = (0.8, 0.05, 0.02, 1.0)
    node.inputs["Roughness"].default_value = 0.5
    cube.data.materials.append(material)

    bpy.ops.object.camera_add(location=(0.0, -7.0, 1.0))
    camera = bpy.context.object
    camera.name = "BenchmarkCamera"
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4.5
    direction = cube.location - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera

    bpy.ops.object.light_add(type="AREA", location=(2.0, -4.0, 5.0))
    light = bpy.context.object
    light.name = "BenchmarkKey"
    light.data.energy = 800.0
    light.data.shape = "DISK"
    light.data.size = 5.0
    light.rotation_euler = (
        (cube.location - light.location).to_track_quat("-Z", "Y").to_euler()
    )
    _save(path)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _manifest(output):
    semantic_oracles = {
        "scene.blend": {
            "counts": {
                "objects": 6,
                "meshes": 3,
                "materials": 1,
                "images": 1,
                "collections": 2,
            },
            "highest_evaluated_mesh": {"object": "SubdividedCube", "polygon_count": 96},
            "missing": ["//missing/textures/checker.png"],
        },
        "mutation_source.blend": {
            "objects": 0,
            "benchmark_source": "mutation_source_v1",
        },
        "batch_source.blend": {
            "object_count": 4,
            "objects": [
                {
                    "name": f"Batch_{index + 1:02d}",
                    "type": "MESH",
                    "location": [float(index * 2), 0.0, 0.0],
                    "vertices": 8,
                    "polygons": 6,
                }
                for index in range(4)
            ],
        },
        "roundtrip_source.blend": {
            "root": "RootNode",
            "meshes": {"RoundtripCube": 8, "RoundtripTriangle": 3},
        },
        "render_source.blend": {
            "camera": "BenchmarkCamera",
            "subject": "RenderSubject",
            "resolution": [64, 64],
            "format": "PNG",
            "transparent": True,
            "engine": "BLENDER_WORKBENCH",
        },
    }
    files = {}
    for name in _SOURCE_FILES:
        semantic = semantic_oracles[name]
        files[name] = {
            "sha256": _sha256(output / name),
            "semantic_sha256": _semantic_hash(semantic),
            "semantic_oracle": semantic,
        }
    return {
        "schema_version": 1,
        "generator": "generate_fixtures.py",
        "generator_version": _GENERATOR_VERSION,
        "generated_with": {
            "bpy_version": bpy.app.version_string,
            "python_version": sys.version.split()[0],
        },
        "determinism": (
            "Semantic oracles are reproducible. Blend byte hashes identify checked-in bytes only; "
            "Blender may change serialization bytes across saves or builds."
        ),
        "files": files,
    }


def _make_cube_output(source, output, name="BenchmarkCube", dimensions=(2.0, 3.0, 4.0)):
    bpy.ops.wm.open_mainfile(filepath=str(source))
    cube = _cube(name, (1.0, -2.0, 0.5))
    cube.dimensions = dimensions
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    _save(output)


def _oracle_directories(output):
    oracle = output / "oracle_outputs"
    bad = output / "known_bad_outputs"
    oracle.mkdir(exist_ok=True)
    bad.mkdir(exist_ok=True)
    return oracle, bad


def _case_paths(oracle, bad, task_id, canonical_name):
    """Create isolated trial roots that each contain the canonical output name."""
    good_path = oracle / task_id / canonical_name
    bad_path = bad / task_id / canonical_name
    good_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    return good_path, bad_path


def _generate_cube_oracles(output, oracle, bad):
    source = output / "mutation_source.blend"
    good_path, bad_path = _case_paths(
        oracle, bad, "create_exact_cube", "exact_cube.blend"
    )
    _make_cube_output(source, good_path)
    _make_cube_output(source, bad_path, dimensions=(2.0, 3.0, 4.25))


def _generate_material_oracles(output, oracle, bad):
    source = output / "mutation_source.blend"
    bpy.ops.wm.open_mainfile(filepath=str(source))
    cube = _cube("MaterialCube")
    material = bpy.data.materials.new("BenchmarkPrincipled")
    node = material.node_tree.nodes["Principled BSDF"]
    node.inputs["Base Color"].default_value = (0.12, 0.34, 0.56, 1.0)
    node.inputs["Metallic"].default_value = 0.25
    node.inputs["Roughness"].default_value = 0.4
    cube.data.materials.append(material)
    good_path, bad_path = _case_paths(
        oracle, bad, "assign_principled_material", "principled_material.blend"
    )
    _save(good_path)
    node.inputs["Metallic"].default_value = 0.5
    _save(bad_path)


def _generate_hierarchy_oracles(output, oracle, bad):
    source = output / "mutation_source.blend"
    bpy.ops.wm.open_mainfile(filepath=str(source))
    parent = bpy.data.objects.new("BenchmarkParent", None)
    child = bpy.data.objects.new("BenchmarkChild", None)
    bpy.context.scene.collection.objects.link(parent)
    bpy.context.scene.collection.objects.link(child)
    parent.location = (3.0, 1.0, 2.0)
    child.parent = parent
    bpy.context.view_layer.update()
    child.matrix_world.translation = (-1.0, 4.0, 2.5)
    good_path, bad_path = _case_paths(
        oracle, bad, "parent_world_transforms", "hierarchy.blend"
    )
    _save(good_path)
    child.matrix_world.translation = (0.0, 4.0, 2.5)
    _save(bad_path)


def _generate_light_oracles(output, oracle, bad):
    source = output / "mutation_source.blend"
    bpy.ops.wm.open_mainfile(filepath=str(source))
    bpy.ops.object.light_add(type="POINT", location=(2.0, -3.0, 4.0))
    light = bpy.context.object
    light.name = "RecoveredPointLight"
    light.data.energy = 750.0
    good_path, bad_path = _case_paths(
        oracle, bad, "recover_stale_operator", "recovered_light.blend"
    )
    _save(good_path)
    light.data.energy = 500.0
    _save(bad_path)


def _generate_batch_oracles(output, oracle, bad):
    bpy.ops.wm.open_mainfile(filepath=str(output / "batch_source.blend"))
    for index, name in enumerate(("Batch_01", "Batch_02", "Batch_03", "Batch_04")):
        bpy.data.objects[name].location.z = 0.5 * (index + 1)
    good_path, bad_path = _case_paths(
        oracle, bad, "batch_edit_save_as", "batch_edit.blend"
    )
    _save(good_path)
    extra = bpy.data.objects.new("UnexpectedExtra", None)
    bpy.context.scene.collection.objects.link(extra)
    _save(bad_path)


def _generate_glb_oracles(output, oracle, bad):
    good_path, bad_path = _case_paths(
        oracle, bad, "glb_roundtrip_structure", "roundtrip.glb"
    )
    bpy.ops.wm.open_mainfile(filepath=str(output / "roundtrip_source.blend"))
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(good_path), export_format="GLB", use_selection=True
    )
    bpy.ops.object.select_all(action="DESELECT")
    bpy.data.objects["RoundtripCube"].select_set(True)
    bpy.context.view_layer.objects.active = bpy.data.objects["RoundtripCube"]
    bpy.ops.export_scene.gltf(
        filepath=str(bad_path), export_format="GLB", use_selection=True
    )


def _generate_render_oracles(output, oracle, bad):
    good_path, bad_path = _case_paths(
        oracle, bad, "low_resolution_render", "verification.png"
    )
    bpy.ops.wm.open_mainfile(filepath=str(output / "render_source.blend"))
    bpy.context.scene.render.filepath = str(good_path)
    bpy.ops.render.render(write_still=True)
    bpy.context.scene.render.resolution_x = 32
    bpy.context.scene.render.resolution_y = 32
    bpy.context.scene.render.filepath = str(bad_path)
    bpy.ops.render.render(write_still=True)


def _generate_answer_oracles(_output, oracle, bad):
    answers = {
        "data_block_counts.json": {
            "objects": 6,
            "meshes": 3,
            "materials": 1,
            "images": 1,
            "collections": 2,
        },
        "evaluated_mesh_polygons.json": {
            "object": "SubdividedCube",
            "polygon_count": 96,
        },
        "missing_external_files.json": {"missing": ["//missing/textures/checker.png"]},
    }
    task_names = {
        "data_block_counts": "data_block_counts.json",
        "evaluated_mesh_polygons": "evaluated_mesh_polygons.json",
        "find_missing_external_files": "missing_external_files.json",
    }
    bad_answers = {
        "data_block_counts": {**answers["data_block_counts.json"], "objects": 5},
        "evaluated_mesh_polygons": {"object": "Triangle", "polygon_count": 1},
        "find_missing_external_files": {"missing": []},
    }
    for task_id, name in task_names.items():
        good_path, bad_path = _case_paths(oracle, bad, task_id, name)
        _write_ascii(good_path, json.dumps(answers[name], sort_keys=True) + "\n")
        _write_ascii(bad_path, json.dumps(bad_answers[task_id], sort_keys=True) + "\n")


def _generate_oracles(output, stages):
    oracle, bad = _oracle_directories(output)
    generators = {
        "cube": _generate_cube_oracles,
        "material": _generate_material_oracles,
        "hierarchy": _generate_hierarchy_oracles,
        "light": _generate_light_oracles,
        "batch": _generate_batch_oracles,
        "glb": _generate_glb_oracles,
        "render": _generate_render_oracles,
        "answers": _generate_answer_oracles,
    }
    for stage in stages:
        started = time.monotonic()
        _progress(f"oracle:{stage}", "start")
        generators[stage](output, oracle, bad)
        _progress(f"oracle:{stage}", "done", started)


def main():
    """Generate selected fixtures and disposable oracle outputs."""
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--include-oracles", action="store_true")
    parser.add_argument(
        "--source",
        action="append",
        choices=_SOURCE_FILES,
        help="Generate only this source; repeat to select multiple sources",
    )
    parser.add_argument(
        "--oracle-stage",
        action="append",
        choices=_ORACLE_STAGES,
        help="Generate only this oracle stage; repeat to select multiple stages",
    )
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_generators = {
        "scene.blend": _generate_scene,
        "mutation_source.blend": _generate_mutation_source,
        "batch_source.blend": _generate_batch_source,
        "roundtrip_source.blend": _generate_roundtrip_source,
        "render_source.blend": _generate_render_source,
    }
    sources = args.source or list(_SOURCE_FILES)
    for name in sources:
        started = time.monotonic()
        _progress(f"source:{name}", "start")
        source_generators[name](output / name)
        _progress(f"source:{name}", "done", started)
    if all((output / name).is_file() for name in _SOURCE_FILES):
        started = time.monotonic()
        _progress("manifest", "start")
        manifest_text = json.dumps(_manifest(output), indent=2, sort_keys=True) + "\n"
        _write_ascii(output / "manifest.json", manifest_text)
        _progress("manifest", "done", started)
    stages = args.oracle_stage or (list(_ORACLE_STAGES) if args.include_oracles else [])
    if stages:
        _generate_oracles(output, stages)
    print(
        json.dumps(
            {"output_dir": str(output), "sources": sources, "oracle_stages": stages},
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
