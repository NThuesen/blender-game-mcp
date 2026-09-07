# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Opt-in real Blender/standalone-bpy integration matrix for Task 10."""

# One method intentionally owns the disposable end-to-end matrix, and the hash
# helper duplicates production logic to verify it independently.
# pylint: disable=duplicate-code,missing-function-docstring,too-many-locals

__all__ = ()

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from benchmarks.token_efficiency import validators  # pylint: disable=import-error

_FIXTURE_ROOT = Path("benchmarks/token_efficiency/fixtures").resolve()
_GENERATOR = _FIXTURE_ROOT / "generate_fixtures.py"
_TASKS = json.loads(
    Path("benchmarks/token_efficiency/tasks.json").read_text(encoding="utf-8")
)
_BAD_FAILURES = {
    "data_block_counts": ("value_mismatch", "result.objects"),
    "evaluated_mesh_polygons": ("value_mismatch", "result.object"),
    "create_exact_cube": ("value_mismatch", "result.object.dimensions[2]"),
    "assign_principled_material": ("value_mismatch", "result.material.metallic"),
    "parent_world_transforms": ("value_mismatch", "result.child_world_translation[0]"),
    "find_missing_external_files": ("value_mismatch", "result.missing"),
    "recover_stale_operator": ("value_mismatch", "result.object.energy"),
    "batch_edit_save_as": ("value_mismatch", "result.object_count"),
    "glb_roundtrip_structure": ("value_mismatch", "result.objects"),
    "low_resolution_render": ("value_mismatch", "result.width"),
}


def _configured_runtimes() -> list[Path]:
    values = [
        os.environ.get(name)
        for name in ("BLENDER_MCP_BPY_PYTHON", "BLENDER_BIN", "BLENDER_PATH")
    ]
    paths: list[Path] = []
    for value in values:
        if value:
            path = Path(value).expanduser().absolute()
            if path not in paths:
                paths.append(path)
    return paths


def _runtime_command(runtime: Path, script: Path, *arguments: str) -> list[str]:
    if "blender" in runtime.name.lower() or ".app/" in str(runtime).lower():
        return [
            str(runtime),
            "--background",
            "--factory-startup",
            "--python",
            str(script),
            "--",
            *arguments,
        ]
    return [str(runtime), str(script), *arguments]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@unittest.skipUnless(
    _configured_runtimes(), "set BLENDER_MCP_BPY_PYTHON and/or BLENDER_BIN/BLENDER_PATH"
)
class TestTokenEfficiencyRealRuntime(unittest.TestCase):
    """Generate disposable artifacts and exercise every public validator."""

    def test_material_shader_names_and_disconnected_decoy(self) -> None:
        for runtime in _configured_runtimes():
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                script = root / "material.py"
                good_root, bad_root = root / "good", root / "bad"
                good_root.mkdir()
                bad_root.mkdir()
                good = good_root / "principled_material.blend"
                bad = bad_root / good.name
                script.write_text(
                    "import bpy\n"
                    "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
                    "bpy.ops.mesh.primitive_cube_add()\n"
                    "obj = bpy.context.object\n"
                    "obj.name = 'MaterialCube'\n"
                    "mat = bpy.data.materials.new('BenchmarkPrincipled')\n"
                    "mat.use_nodes = True\n"
                    "obj.data.materials.append(mat)\n"
                    "node = mat.node_tree.nodes.get('Principled BSDF')\n"
                    "node.name = 'Renamed Valid Shader'\n"
                    "node.inputs['Base Color'].default_value = (0.12, 0.34, 0.56, 1)\n"
                    "node.inputs['Metallic'].default_value = 0.25\n"
                    "node.inputs['Roughness'].default_value = 0.4\n"
                    f"bpy.ops.wm.save_as_mainfile(filepath={str(good)!r})\n"
                    "decoy = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')\n"
                    "decoy.name = 'Principled BSDF'\n"
                    "for key in ('Base Color', 'Metallic', 'Roughness'):\n"
                    "    decoy.inputs[key].default_value = node.inputs[key].default_value\n"
                    "node.inputs['Metallic'].default_value = 0.9\n"
                    f"bpy.ops.wm.save_as_mainfile(filepath={str(bad)!r})\n",
                    encoding="utf-8",
                )
                completed = subprocess.run(
                    _runtime_command(runtime, script),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                for output, expected_ok in ((good, True), (bad, False)):
                    with self.subTest(runtime=str(runtime), artifact=str(output)):
                        result = validators.validate_assign_principled_material(
                            _FIXTURE_ROOT, output.parent, output, runtime
                        )
                        self.assertEqual(result["ok"], expected_ok, result)
                        if not expected_ok:
                            self.assertEqual(
                                (
                                    result["errors"][0]["code"],
                                    result["errors"][0]["field"],
                                ),
                                ("value_mismatch", "result.material.metallic"),
                            )

    def test_real_runtime_matrix(self) -> None:
        checked_manifest = json.loads(
            (_FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        checked_hashes_before = {
            name: _sha256(_FIXTURE_ROOT / name) for name in checked_manifest["files"]
        }
        for runtime in _configured_runtimes():
            with self.subTest(runtime=str(runtime)):
                self.assertTrue(runtime.is_file(), runtime)
                started = time.monotonic()
                with tempfile.TemporaryDirectory(
                    prefix="token-efficiency-runtime-"
                ) as temporary:
                    generated_root = Path(temporary) / "fixtures"
                    command = _runtime_command(
                        runtime,
                        _GENERATOR,
                        "--output-dir",
                        str(generated_root),
                        "--include-oracles",
                    )
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        (completed.stderr or completed.stdout)[-4000:],
                    )
                    generated_manifest = json.loads(
                        (generated_root / "manifest.json").read_text(encoding="utf-8")
                    )

                    semantic_comparisons = 0
                    for fixture_name, metadata in checked_manifest["files"].items():
                        expected = metadata["semantic_oracle"]
                        self.assertEqual(
                            generated_manifest["files"][fixture_name][
                                "semantic_oracle"
                            ],
                            expected,
                        )
                        for root in (_FIXTURE_ROOT, generated_root):
                            with self.subTest(
                                runtime=str(runtime),
                                fixture=fixture_name,
                                root=str(root),
                            ):
                                inspection = validators.run_runtime_inspector(
                                    runtime, root / fixture_name, "fixture_semantics"
                                )
                                self.assertTrue(inspection["ok"], inspection)
                                self.assertEqual(
                                    inspection["details"]["data"], expected
                                )
                                semantic_comparisons += 1

                    for task in _TASKS:
                        task_id = task["id"]
                        output_name = task["outputs"]["path"]
                        validator = getattr(validators, task["validator"])
                        good_root = Path(temporary) / "trials" / "good" / task_id
                        bad_root = Path(temporary) / "trials" / "bad" / task_id
                        good_root.mkdir(parents=True, exist_ok=True)
                        bad_root.mkdir(parents=True, exist_ok=True)
                        good_path = good_root / output_name
                        bad_path = bad_root / output_name
                        shutil.copy2(
                            generated_root / "oracle_outputs" / task_id / output_name,
                            good_path,
                        )
                        shutil.copy2(
                            generated_root
                            / "known_bad_outputs"
                            / task_id
                            / output_name,
                            bad_path,
                        )
                        common = {
                            "fixture_root": generated_root,
                            "output_root": good_root,
                        }
                        if task["outputs"]["kind"] == "answer_json":
                            good = validator(**common, answer_path=good_path)
                            bad = validator(
                                fixture_root=generated_root,
                                output_root=bad_root,
                                answer_path=bad_path,
                            )
                        else:
                            good = validator(
                                **common,
                                output_path=good_path,
                                runtime_executable=runtime,
                            )
                            bad = validator(
                                fixture_root=generated_root,
                                output_root=bad_root,
                                output_path=bad_path,
                                runtime_executable=runtime,
                            )
                        with self.subTest(
                            runtime=str(runtime), task=task_id, artifact="good"
                        ):
                            self.assertTrue(good["ok"], good)
                        with self.subTest(
                            runtime=str(runtime), task=task_id, artifact="bad"
                        ):
                            self.assertFalse(bad["ok"], bad)
                            self.assertEqual(
                                (bad["errors"][0]["code"], bad["errors"][0]["field"]),
                                _BAD_FAILURES[task_id],
                            )
                            self.assertNotEqual(
                                bad["errors"][0]["code"], "wrong_output_name"
                            )

                    self.assertEqual(semantic_comparisons, 10)
                    self.assertEqual(
                        {
                            name: _sha256(generated_root / name)
                            for name in generated_manifest["files"]
                        },
                        {
                            name: metadata["sha256"]
                            for name, metadata in generated_manifest["files"].items()
                        },
                    )
                    print(
                        json.dumps(
                            {
                                "runtime": str(runtime),
                                "seconds": round(time.monotonic() - started, 3),
                                "public_validator_good": 10,
                                "public_validator_bad": 10,
                                "fixture_semantic_comparisons": semantic_comparisons,
                                "generated_with": generated_manifest["generated_with"],
                            },
                            sort_keys=True,
                        )
                    )
        self.assertEqual(
            {name: _sha256(_FIXTURE_ROOT / name) for name in checked_manifest["files"]},
            checked_hashes_before,
        )


if __name__ == "__main__":
    unittest.main()
