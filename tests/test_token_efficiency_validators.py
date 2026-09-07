# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure tests for deterministic token-efficiency benchmark contracts."""

# Tests intentionally call a required-argument API incorrectly and duplicate
# independent oracle values so schema drift cannot make implementation and test agree.
# pylint: disable=duplicate-code,missing-function-docstring,no-value-for-parameter

__all__ = ()

import hashlib
import inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from benchmarks.token_efficiency import validators  # pylint: disable=import-error

_TASK_IDS = [
    "data_block_counts",
    "evaluated_mesh_polygons",
    "create_exact_cube",
    "assign_principled_material",
    "parent_world_transforms",
    "find_missing_external_files",
    "recover_stale_operator",
    "batch_edit_save_as",
    "glb_roundtrip_structure",
    "low_resolution_render",
]
_ANSWER_IDS = {
    "data_block_counts",
    "evaluated_mesh_polygons",
    "find_missing_external_files",
}
_KIND_SUFFIXES = {
    "answer_json": ".json",
    "blend": ".blend",
    "glb": ".glb",
    "png": ".png",
}


def _cube_record(
    name: str, dimensions: list[float], location: list[float]
) -> dict[str, object]:
    return {
        "name": name,
        "type": "MESH",
        "dimensions": dimensions,
        "location": location,
        "rotation_euler": [0.0, 0.0, 0.0],
        "scale": [1.0, 1.0, 1.0],
        "vertices": 8,
        "polygons": 6,
    }


def _artifact_semantics() -> dict[str, dict[str, Any]]:
    batch_objects = [
        {
            "name": f"Batch_{index + 1:02d}",
            "type": "MESH",
            "location": [float(index * 2), 0.0, 0.5 * (index + 1)],
            "vertices": 8,
            "polygons": 6,
        }
        for index in range(4)
    ]
    return {
        "create_exact_cube": {
            "object": _cube_record("BenchmarkCube", [2.0, 3.0, 4.0], [1.0, -2.0, 0.5]),
            "object_count": 1,
        },
        "assign_principled_material": {
            "object": "MaterialCube",
            "object_record": _cube_record(
                "MaterialCube", [2.0, 2.0, 2.0], [0.0, 0.0, 0.0]
            ),
            "object_count": 1,
            "assigned_materials": ["BenchmarkPrincipled"],
            "material": {
                "name": "BenchmarkPrincipled",
                "node_type": "BSDF_PRINCIPLED",
                "base_color": [0.12, 0.34, 0.56, 1.0],
                "metallic": 0.25,
                "roughness": 0.4,
                "surface_connected": True,
            },
        },
        "parent_world_transforms": {
            "parent": "BenchmarkParent",
            "child": "BenchmarkChild",
            "child_parent": "BenchmarkParent",
            "parent_type": "EMPTY",
            "child_type": "EMPTY",
            "parent_world_translation": [3.0, 1.0, 2.0],
            "child_world_translation": [-1.0, 4.0, 2.5],
            "object_count": 2,
        },
        "recover_stale_operator": {
            "object": {
                "name": "RecoveredPointLight",
                "type": "LIGHT",
                "location": [2.0, -3.0, 4.0],
                "light_type": "POINT",
                "energy": 750.0,
            },
            "object_count": 1,
        },
        "batch_edit_save_as": {
            "object_count": 4,
            "objects": batch_objects,
            "filepath_name": "batch_edit.blend",
        },
        "glb_roundtrip_structure": {
            "objects": [
                {
                    "name": "RootNode",
                    "type": "EMPTY",
                    "parent": None,
                    "unique_vertex_positions": None,
                },
                {
                    "name": "RoundtripCube",
                    "type": "MESH",
                    "parent": "RootNode",
                    "unique_vertex_positions": 8,
                },
                {
                    "name": "RoundtripTriangle",
                    "type": "MESH",
                    "parent": "RootNode",
                    "unique_vertex_positions": 3,
                },
            ]
        },
        "low_resolution_render": {
            "format": "PNG",
            "width": 64,
            "height": 64,
            "channels": 4,
            "non_background_pixels": 900,
            "center_rgba": [204, 51, 26, 255],
        },
    }


class TestTaskSchema(unittest.TestCase):
    """Verify the complete declarative task and fixture contracts."""

    def test_all_ten_tasks_have_complete_schema_and_callable_validators(self) -> None:
        tasks_path = Path("benchmarks/token_efficiency/tasks.json")
        tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
        self.assertEqual([task["id"] for task in tasks], _TASK_IDS)
        self.assertEqual(len({task["id"] for task in tasks}), 10)
        required = {
            "id",
            "task_family",
            "evaluation_mode",
            "fixture",
            "prompt",
            "max_turns",
            "requires_render",
            "validator",
            "mutation",
            "outputs",
            "execution_variants",
            "runtime_docs",
            "validator_inputs",
            "expected_answer",
            "expected_output",
            "source_safety",
        }
        for task in tasks:
            with self.subTest(task=task["id"]):
                self.assertEqual(set(task), required)
                self.assertEqual(task["task_family"], "deterministic_efficiency")
                self.assertEqual(task["max_turns"], 20)
                self.assertIs(type(task["requires_render"]), bool)
                self.assertEqual(
                    task["requires_render"], task["id"] == "low_resolution_render"
                )
                self.assertEqual(
                    task["execution_variants"], ["live_mcp", "blender_cli", "bpy_cli"]
                )
                self.assertNotIn("variant_prompts", task)
                self.assertEqual(task["prompt"].count("{{fixture}}"), 1)
                self.assertEqual(task["prompt"].count("{{output}}"), 1)
                fixture = tasks_path.parent / task["fixture"]
                self.assertTrue(fixture.is_file(), fixture)
                self.assertEqual(
                    task["outputs"],
                    {key: task["expected_output"][key] for key in ("kind", "path")},
                )
                self.assertEqual(
                    Path(task["outputs"]["path"]).suffix,
                    _KIND_SUFFIXES[task["outputs"]["kind"]],
                )
                self.assertEqual(task["validator"], f"validate_{task['id']}")
                function = getattr(validators, task["validator"])
                self.assertTrue(callable(function))
                parameters = inspect.signature(function).parameters
                self.assertIn("fixture_root", parameters)
                self.assertIn("output_root", parameters)
                path_key = "answer_path" if task["id"] in _ANSWER_IDS else "output_path"
                self.assertEqual(
                    task["validator_inputs"]["fixture_root"], "{{fixture_root}}"
                )
                self.assertEqual(
                    task["validator_inputs"]["output_root"], "{{output_root}}"
                )
                self.assertEqual(task["validator_inputs"][path_key], "{{output}}")
                self.assertIn(path_key, parameters)
                if task["id"] not in _ANSWER_IDS:
                    self.assertEqual(
                        task["validator_inputs"]["runtime_executable"],
                        "{{runtime_executable}}",
                    )
                    self.assertIn("runtime_executable", parameters)
                self.assertEqual(
                    task["mutation"]["source_must_remain_unchanged"],
                    task["source_safety"]["must_remain_unchanged"],
                )
                self.assertIs(type(task["mutation"]["scene_changes_allowed"]), bool)
                self.assertEqual(
                    task["source_safety"],
                    {
                        "must_remain_unchanged": True,
                        "output_must_be_distinct": True,
                        "output_must_be_within_explicit_root": True,
                    },
                )
                self.assertTrue(task["runtime_docs"]["purpose"])
                self.assertTrue(task["runtime_docs"]["reason"])
                self.assertIn(
                    task["runtime_docs"]["expected_need"], {"none", "optional", "high"}
                )
                self.assertIsNotNone(task["validator_inputs"])
                if task["id"] in _ANSWER_IDS:
                    self.assertEqual(
                        task["expected_answer"], task["expected_output"]["oracle"]
                    )
                else:
                    self.assertIsNone(task["expected_answer"])
                    self.assertIsInstance(task["expected_output"]["oracle"], str)

    def test_manifest_byte_and_semantic_hashes_match(self) -> None:
        fixture_dir = Path("benchmarks/token_efficiency/fixtures")
        manifest = json.loads(
            (fixture_dir / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(manifest["files"]),
            {
                "scene.blend",
                "mutation_source.blend",
                "batch_source.blend",
                "roundtrip_source.blend",
                "render_source.blend",
            },
        )
        for relative, metadata in manifest["files"].items():
            with self.subTest(path=relative):
                self.assertEqual(
                    hashlib.sha256((fixture_dir / relative).read_bytes()).hexdigest(),
                    metadata["sha256"],
                )
                encoded = json.dumps(
                    metadata["semantic_oracle"], sort_keys=True, separators=(",", ":")
                ).encode("ascii")
                self.assertEqual(
                    hashlib.sha256(encoded).hexdigest(), metadata["semantic_sha256"]
                )


class TestPureValidators(unittest.TestCase):
    """Exercise exact semantic oracles without bpy."""

    def test_read_only_answer_oracles(self) -> None:
        answers: dict[str, dict[str, Any]] = {
            "data_block_counts": {
                "objects": 6,
                "meshes": 3,
                "materials": 1,
                "images": 1,
                "collections": 2,
            },
            "evaluated_mesh_polygons": {
                "object": "SubdividedCube",
                "polygon_count": 96,
            },
            "find_missing_external_files": {
                "missing": ["//missing/textures/checker.png"]
            },
        }
        for task_id, answer in answers.items():
            with self.subTest(task=task_id):
                self.assertTrue(validators.validate_answer_data(task_id, answer)["ok"])
                bad = dict(answer)
                bad[next(iter(bad))] = None
                self.assertFalse(validators.validate_answer_data(task_id, bad)["ok"])
        bool_count = {**answers["data_block_counts"], "objects": True}
        self.assertEqual(
            validators.validate_answer_data("data_block_counts", bool_count)["errors"][
                0
            ]["code"],
            "type_mismatch",
        )

    def test_all_seven_artifact_semantic_oracles(self) -> None:
        cases = _artifact_semantics()
        self.assertEqual(set(cases), set(_TASK_IDS) - _ANSWER_IDS)
        for task_id, good in cases.items():
            with self.subTest(task=task_id):
                self.assertTrue(validators.validate_semantics(task_id, good)["ok"])
                extra = {**good, "unexpected": "field"}
                self.assertEqual(
                    validators.validate_semantics(task_id, extra)["errors"][-1]["code"],
                    "unexpected_field",
                )

    def test_batch_rejects_wrong_axes_replacement_topology_and_extras(self) -> None:
        good = _artifact_semantics()["batch_edit_save_as"]
        mutations = []
        for field, value in (
            ("location", [99.0, 0.0, 0.5]),
            ("type", "EMPTY"),
            ("vertices", 7),
            ("polygons", 5),
        ):
            bad = json.loads(json.dumps(good))
            bad["objects"][0][field] = value
            mutations.append(bad)
        extra = json.loads(json.dumps(good))
        extra["object_count"] = 5
        extra["objects"].append(
            {
                "name": "Extra",
                "type": "EMPTY",
                "location": [0.0, 0.0, 0.0],
                "vertices": None,
                "polygons": None,
            }
        )
        mutations.append(extra)
        for bad in mutations:
            with self.subTest(bad=bad):
                self.assertFalse(
                    validators.validate_semantics("batch_edit_save_as", bad)["ok"]
                )

    def test_render_metadata_types_are_structured_and_never_raise(self) -> None:
        good = _artifact_semantics()["low_resolution_render"]
        for value in (
            ["red", 0, 0, 255],
            [True, 0, 0, 255],
            [204, 0, 0, False],
            [204.0, 0, 0, 255],
            "red",
        ):
            with self.subTest(value=value):
                bad = {**good, "center_rgba": value}
                result = validators.validate_semantics("low_resolution_render", bad)
                self.assertFalse(result["ok"])
                self.assertEqual(result["errors"][-1]["code"], "type_mismatch")
                json.dumps(result)


# These white-box lifecycle tests deliberately exercise private cleanup helpers.
# pylint: disable=protected-access,too-many-instance-attributes
class TestMockedProcessCleanup(unittest.TestCase):
    """Exercise denied syscalls without launching or signaling real processes."""

    def setUp(self) -> None:
        self.process = mock.Mock(pid=12345, returncode=0)
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self.addCleanup(self.stdout.close)
        self.addCleanup(self.stderr.close)
        self.process.stdout = mock.Mock(wraps=self.stdout)
        self.process.stderr = mock.Mock(wraps=self.stderr)
        self.process.stdout.fileno.return_value = 42
        self.process.stderr.fileno.return_value = 43
        self.selector = mock.Mock()
        self.selector.get_map.return_value = {}
        self.popen = self._patch(validators.subprocess, "Popen", return_value=self.process)
        self.factory = self._patch(
            validators.selectors, "DefaultSelector", return_value=self.selector
        )
        self._patch(validators.os, "set_blocking")
        self.killpg = self._patch(validators.os, "killpg")
        self.exited = self._patch(validators, "_child_exited", return_value=True)

    def _patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _run(self):
        return validators.run_runtime_inspector("mock-runtime", __file__, "synthetic")

    def _assert_released(self, *, stdout_closed=True):
        self.process.stdout.close.assert_called_once_with()
        self.process.stderr.close.assert_called_once_with()
        self.assertEqual(self.stdout.closed, stdout_closed)
        self.assertTrue(self.stderr.closed)
        self.process.wait.assert_called_once_with(timeout=1)
        self.assertTrue(self.popen.call_args.kwargs["start_new_session"])
        for call in self.killpg.call_args_list:
            self.assertEqual(call.args, (self.process.pid, validators.signal.SIGKILL))

    def test_observer_creation_denied_preserves_primary_with_cleanup_denied(self):
        original = PermissionError(1, "observer creation denied")
        self.factory.side_effect = original
        self.killpg.side_effect = PermissionError(1, "group signal denied")
        self.process.wait.side_effect = subprocess.TimeoutExpired("mock-runtime", 1)
        with self.assertRaises(validators._RuntimeProcessError) as caught:
            validators._bounded_process(["mock-runtime"])
        self.assertIs(caught.exception.__cause__, original)
        self.assertEqual(caught.exception.stage, "observer")
        self.assertEqual(caught.exception.operation, "selectors.DefaultSelector")
        self.assertEqual(len(caught.exception.cleanup_errors), 2)
        self._assert_released()

    def test_observer_creation_is_not_reported_as_launch(self):
        self.factory.side_effect = PermissionError(1, "observer creation denied")
        result = self._run()
        self.assertEqual(result["errors"][0]["code"], "runtime_observer_error")
        self.assertEqual(result["details"]["operation"], "selectors.DefaultSelector")
        self.assertEqual(result["details"]["errno"], 1)
        self._assert_released()

    def test_observer_poll_denied_preserves_primary_and_closes_selector(self):
        self.exited.return_value = False
        self.selector.select.side_effect = PermissionError(1, "observer poll denied")
        self.killpg.side_effect = PermissionError(1, "group signal denied")
        self.selector.close.side_effect = PermissionError(1, "selector close denied")
        result = self._run()
        self.assertEqual(result["errors"][0]["code"], "runtime_observer_error")
        self.assertIn("observer poll denied", result["errors"][0]["message"])
        self.assertEqual(result["details"]["operation"], "selector.select")
        self.assertEqual(len(result["details"]["cleanup_errors"]), 2)
        self.selector.close.assert_called_once_with()
        self._assert_released()

    def test_completed_capture_never_signals(self):
        self.killpg.side_effect = PermissionError(1, "must not signal completion")
        self.assertEqual(validators._bounded_process(["mock-runtime"]), (0, b"", b"", False))
        self.killpg.assert_not_called()
        self._assert_released()

    def test_exited_leader_drains_pending_bytes_and_preserves_status(self):
        self.process.returncode = 3
        self.selector.get_map.side_effect = [{42: object(), 43: object()}, {42: object()}, {}]
        self.selector.select.side_effect = [
            [(mock.Mock(fd=42, data="stdout"), 1), (mock.Mock(fd=43, data="stderr"), 1)],
            [(mock.Mock(fd=42, data="stdout"), 1), (mock.Mock(fd=43, data="stderr"), 1)],
        ]
        self._patch(validators.os, "read", side_effect=[b"buffered", b"broken", b"", b""])
        self.assertEqual(
            validators._bounded_process(["mock-runtime"]), (3, b"buffered", b"broken", False)
        )
        self.assertEqual(self.selector.unregister.call_count, 2)
        self.killpg.assert_not_called()
        self._assert_released()

    def test_group_signal_denied_is_cleanup_failure_not_success(self):
        # The exited leader still has inherited pipes held by a descendant.
        self.selector.get_map.return_value = {42: object()}
        self.selector.select.return_value = []
        self.killpg.side_effect = PermissionError(1, "group signal denied")
        result = self._run()
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "runtime_cleanup_error")
        self.assertEqual(result["details"]["operation"], "os.killpg")
        self._assert_released()

    def test_close_failure_does_not_skip_other_pipe_or_reap(self):
        self.process.stdout.close.side_effect = PermissionError(1, "pipe close denied")
        result = self._run()
        self.assertEqual(result["errors"][0]["code"], "runtime_cleanup_error")
        self.assertEqual(result["details"]["operation"], "stdout.close")
        self._assert_released(stdout_closed=False)

    def test_reap_timeout_is_cleanup_failure(self):
        self.process.wait.side_effect = subprocess.TimeoutExpired("mock-runtime", 1)
        result = self._run()
        self.assertEqual(result["errors"][0]["code"], "runtime_cleanup_error")
        self.assertEqual(result["details"]["operation"], "process.wait")
        self._assert_released()

    def test_deadline_survives_denied_cleanup(self):
        self.exited.return_value = False
        self._patch(validators, "_TIMEOUT_SECONDS", new=0)
        self.killpg.side_effect = PermissionError(1, "group signal denied")
        result = self._run()
        self.assertEqual(result["errors"][0]["code"], "runtime_timeout")
        self.assertIn("group signal denied", result["details"]["cleanup_errors"][0])
        self._assert_released()

    def test_output_cap_survives_denied_cleanup(self):
        self.exited.return_value = False
        self.selector.select.return_value = [(mock.Mock(fd=42, data="stdout"), 1)]
        self._patch(validators.os, "read", return_value=b"x")
        self._patch(validators, "_MAX_RUNTIME_CAPTURE_BYTES", new=1)
        self.killpg.side_effect = PermissionError(1, "group signal denied")
        result = self._run()
        self.assertEqual(result["errors"][0]["code"], "runtime_output_too_large")
        self.assertIn("group signal denied", result["details"]["cleanup_errors"][0])
        self._assert_released()

    def test_unexpected_primary_exception_is_not_masked(self):
        original = KeyboardInterrupt("interrupted")
        self.factory.side_effect = original
        self.killpg.side_effect = PermissionError(1, "group signal denied")
        with self.assertRaises(KeyboardInterrupt) as caught:
            self._run()
        self.assertIs(caught.exception, original)
        self.assertIn("group signal denied", original.__notes__[0])  # pylint: disable=no-member
        self._assert_released()

    def test_launch_denied_has_no_child_to_cleanup(self):
        self.popen.side_effect = PermissionError(1, "launch denied")
        result = self._run()
        self.assertEqual(result["errors"][0]["code"], "runtime_launch_error")
        self.assertEqual(result["details"]["operation"], "subprocess.Popen")
        self.killpg.assert_not_called()
        self.process.wait.assert_not_called()

    @unittest.skipUnless(hasattr(validators.select, "kqueue"), "kqueue observer")
    def test_kqueue_creation_and_control_denied_retain_exact_operation(self):
        # Restore the production observer; every underlying OS call stays mocked.
        observer = self.exited
        for operation in ("select.kqueue", "kqueue.control"):
            with self.subTest(operation=operation):
                queue = mock.Mock()
                original = PermissionError(1, operation + " denied")
                with (
                    mock.patch.object(validators.os, "waitid", None, create=True),
                    mock.patch.object(
                        validators.select, "kqueue", return_value=queue
                    ) as factory,
                    mock.patch.object(validators.select, "kevent"),
                ):
                    if operation == "select.kqueue":
                        factory.side_effect = original
                    else:
                        queue.control.side_effect = original
                        queue.close.side_effect = PermissionError(1, "queue close denied")
                    observer.side_effect = self._production_observer
                    result = self._run()
                    self.assertEqual(
                        result["errors"][0]["code"], "runtime_observer_error"
                    )
                    self.assertEqual(result["details"]["operation"], operation)
                    self.assertIn(str(original), result["errors"][0]["message"])
                    self._assert_released()
                    if operation == "kqueue.control":
                        queue.close.assert_called_once_with()
                        self.assertIn(
                            "queue close denied", result["details"]["cleanup_errors"][0]
                        )
                self.process.reset_mock()
                self.killpg.reset_mock()

    _production_observer = staticmethod(validators._child_exited)


class TestValidationBoundary(unittest.TestCase):
    """Test filesystem and bounded subprocess trust boundaries."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        fixture_root = self.root / "fixtures"
        fixture_root.mkdir()
        self.fixture = fixture_root / "source.blend"
        self.fixture.write_bytes(b"source")
        self.manifest = {
            "files": {"source.blend": {"sha256": hashlib.sha256(b"source").hexdigest()}}
        }

    def test_path_contract_rejects_traversal_overwrite_name_missing_and_mutation(
        self,
    ) -> None:
        output_root = self.root / "outputs"
        output_root.mkdir()
        output = output_root / "expected.blend"
        output.write_bytes(b"output")
        cases = (
            (self.root / "escape.blend", None, "unsafe_path"),
            (self.fixture, None, "unsafe_path"),
            (output, "expected.glb", "wrong_output_name"),
            (output_root / "missing.blend", None, "missing_output"),
        )
        for path, name, code in cases:
            with self.subTest(code=code):
                result = validators.validate_artifact_paths(
                    output_root, path, self.fixture, self.manifest, name
                )
                self.assertEqual(result["errors"][0]["code"], code)
        self.fixture.write_bytes(b"changed")
        self.assertEqual(
            validators.validate_artifact_paths(
                output_root, output, self.fixture, self.manifest
            )["errors"][0]["code"],
            "source_modified",
        )

    def test_path_contract_rejects_output_inside_fixture_area(self) -> None:
        output = self.fixture.parent / "expected.blend"
        output.write_bytes(b"output")
        result = validators.validate_artifact_paths(
            self.fixture.parent, output, self.fixture, self.manifest
        )
        self.assertEqual(result["errors"][0]["code"], "source_area_output")

    def test_public_answer_validator_requires_and_enforces_explicit_root(self) -> None:
        fixture_root = self.root / "answer-fixtures"
        fixture_root.mkdir()
        scene = fixture_root / "scene.blend"
        scene.write_bytes(b"scene")
        (fixture_root / "manifest.json").write_text(
            json.dumps(
                {
                    "files": {
                        "scene.blend": {"sha256": hashlib.sha256(b"scene").hexdigest()}
                    }
                }
            ),
            encoding="utf-8",
        )
        output_root = self.root / "outputs"
        output_root.mkdir()
        answer = output_root / "data_block_counts.json"
        answer.write_text(
            json.dumps(
                {
                    "objects": 6,
                    "meshes": 3,
                    "materials": 1,
                    "images": 1,
                    "collections": 2,
                }
            ),
            encoding="utf-8",
        )
        self.assertTrue(
            validators.validate_data_block_counts(fixture_root, output_root, answer)[
                "ok"
            ]
        )
        outside = self.root / "data_block_counts.json"
        outside.write_bytes(answer.read_bytes())
        rejected = validators.validate_data_block_counts(
            fixture_root, output_root, outside
        )
        self.assertEqual(rejected["errors"][0]["code"], "unsafe_path")
        with self.assertRaises(TypeError):
            validators.validate_data_block_counts(fixture_root, answer)  # type: ignore[call-arg]

    def test_public_answers_reject_encoding_and_recursion(self) -> None:
        fixture_root = self.fixture.parent
        (fixture_root / "scene.blend").write_bytes(b"source")
        (fixture_root / "manifest.json").write_text(
            json.dumps(
                {"files": {"scene.blend": self.manifest["files"]["source.blend"]}}
            ),
            encoding="utf-8",
        )
        output_root = self.root / "outputs"
        output_root.mkdir()
        names = {
            "data_block_counts": "data_block_counts.json",
            "evaluated_mesh_polygons": "evaluated_mesh_polygons.json",
            "find_missing_external_files": "missing_external_files.json",
        }
        for task, name in names.items():
            for content in (b"\xff", b"[" * 2000 + b"0" + b"]" * 2000):
                with self.subTest(task=task, encoding=content[:1]):
                    answer = output_root / name
                    answer.write_bytes(content)
                    result = getattr(validators, "validate_" + task)(
                        fixture_root, output_root, answer
                    )
                    self.assertEqual(result["errors"][0]["code"], "malformed_result")
                    json.dumps(result)

    def _over_limit_integer_payload(self, prefix: str, suffix: str, cap: int) -> str:
        limit = sys.get_int_max_str_digits()
        if limit == 0:
            self.skipTest("Integer string conversion limit is disabled")
        if len(prefix) + limit + 1 + len(suffix) > cap:
            self.skipTest("Active integer limit exceeds the JSON payload size cap")
        return prefix + "9" * (limit + 1) + suffix

    def test_public_answer_rejects_over_limit_json_integer(self) -> None:
        payload = self._over_limit_integer_payload(
            '{"objects":', ',"meshes":3,"materials":1,"images":1,"collections":2}',
            64 * 1024,
        )
        fixture_root = self.fixture.parent
        (fixture_root / "scene.blend").write_bytes(b"source")
        (fixture_root / "manifest.json").write_text(
            json.dumps(
                {"files": {"scene.blend": self.manifest["files"]["source.blend"]}}
            ),
            encoding="utf-8",
        )
        output_root = self.root / "outputs"
        output_root.mkdir()
        answer = output_root / "data_block_counts.json"
        answer.write_text(payload, encoding="utf-8")
        result = validators.validate_data_block_counts(fixture_root, output_root, answer)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "malformed_result")
        json.dumps(result)

    def test_runtime_rejects_over_limit_json_integer(self) -> None:
        payload = self._over_limit_integer_payload(
            '{"ok":true,"data":{"objects":', '}}', 1024 * 1024
        )
        result = self._run_script(
            f"print({'__TOKEN_EFFICIENCY_INSPECT__' + payload!r})\n"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "malformed_runtime_result")
        json.dumps(result)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group lifecycle")
    def test_descendant_pipes_have_bounded_lifecycle_and_cleanup(self) -> None:
        for mode in ("exit", "timeout", "cap"):
            with self.subTest(mode=mode):
                pid_file = self.root / (mode + ".pid")
                source = (
                    "import subprocess, sys, time, os\n"
                    "child = subprocess.Popen([sys.executable, '-c', "
                    "'import time; time.sleep(2)'])\n"
                    f"with open({str(pid_file)!r}, 'w') as f: f.write(str(child.pid))\n"
                    'print(\'__TOKEN_EFFICIENCY_INSPECT__{"ok":true,"data":{}}\', flush=True)\n'
                )
                if mode == "timeout":
                    source += "time.sleep(2)\n"
                elif mode == "cap":
                    source += "while True: os.write(1, b'x'*65536)\n"
                before = {thread.ident for thread in threading.enumerate()}
                started = time.monotonic()
                result = self._run_script(source, timeout=0.3)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 1.5)
                if mode == "exit":
                    self.assertTrue(result["ok"], result)
                else:
                    expected = (
                        "runtime_timeout"
                        if mode == "timeout"
                        else "runtime_output_too_large"
                    )
                    self.assertEqual(result["errors"][0]["code"], expected)
                self.assertEqual(
                    before, {thread.ident for thread in threading.enumerate()}
                )
                pid = int(pid_file.read_text(encoding="utf-8"))
                status = subprocess.run(
                    ["ps", "-o", "stat=", "-p", str(pid)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                ).stdout.strip()
                self.assertTrue(not status or status.startswith("Z"), status)
                print(
                    json.dumps(
                        {
                            "lifecycle": mode,
                            "seconds": round(elapsed, 3),
                            "descendant_status": status,
                        }
                    )
                )

    def _run_script(self, source: str, *, timeout: float = 120) -> dict[str, Any]:
        script = self.root / "synthetic_inspector.py"
        script.write_text(source, encoding="utf-8")
        with (
            mock.patch.object(validators, "_INSPECTOR", script),
            mock.patch.object(validators, "_TIMEOUT_SECONDS", timeout),
        ):
            return validators.run_runtime_inspector(
                sys.executable, self.fixture, "synthetic"
            )

    def test_normal_exit_drains_buffered_output_without_signaling(self) -> None:
        marker = "__TOKEN_EFFICIENCY_INSPECT__"
        cases = (
            (f"print('{marker}'+ '{{\"ok\":true,\"data\":{{}}}}')", None),
            ("import sys; sys.stderr.write('broken'); sys.exit(3)", "runtime_error"),
            (f"print('{marker}not-json')", "malformed_runtime_result"),
        )
        for source, code in cases:
            with self.subTest(code=code), mock.patch.object(
                validators.os, "killpg", side_effect=AssertionError("completed capture signaled")
            ) as killpg:
                result = self._run_script("print('x' * 200000)\n" + source)
                if code is None:
                    self.assertTrue(result["ok"], result)
                else:
                    self.assertEqual(result["errors"][0]["code"], code)
                killpg.assert_not_called()

    def test_pipe_eof_while_leader_alive_requires_timeout_cleanup(self) -> None:
        with mock.patch.object(validators.os, "killpg", wraps=os.killpg) as killpg:
            result = self._run_script(
                "import os, time; os.close(1); os.close(2); time.sleep(2)", timeout=0.1
            )
        self.assertEqual(result["errors"][0]["code"], "runtime_timeout")
        killpg.assert_called_once()

    def test_runtime_timeout_launch_nonzero_and_newest_frame_are_structured(
        self,
    ) -> None:
        timeout = self._run_script("import time\ntime.sleep(2)\n", timeout=0.05)
        self.assertEqual(timeout["errors"][0]["code"], "runtime_timeout")
        launch = validators.run_runtime_inspector(
            self.root / "absent-runtime", self.fixture, "synthetic"
        )
        self.assertEqual(launch["errors"][0]["code"], "runtime_launch_error")
        failed = self._run_script(
            "import sys\nsys.stderr.write('broken')\nsys.exit(3)\n"
        )
        self.assertEqual(failed["errors"][0]["code"], "runtime_error")
        marker = "__TOKEN_EFFICIENCY_INSPECT__"
        newest = self._run_script(
            f"import json\nprint('{marker}'+json.dumps({{'ok':False,'error':'stale'}}))\n"
            f"print('{marker}'+json.dumps({{'ok':True,'data':{{'newest':True}}}}))\n"
        )
        self.assertTrue(newest["ok"])
        self.assertEqual(newest["details"]["data"], {"newest": True})

    def test_runtime_capture_is_killed_at_aggregate_cap(self) -> None:
        result = self._run_script(
            "import os\nchunk=b'x'*65536\nwhile True: os.write(1, chunk)\n"
        )
        self.assertEqual(result["errors"][0]["code"], "runtime_output_too_large")
        self.assertLess(len(json.dumps(result)), 5000)

    def test_runtime_rejects_malformed_and_oversize_protocol_payload(self) -> None:
        marker = "__TOKEN_EFFICIENCY_INSPECT__"
        malformed = self._run_script(f"print('{marker}not-json')\n")
        self.assertEqual(malformed["errors"][0]["code"], "malformed_runtime_result")
        oversize = self._run_script(f"print('{marker}' + ' ' * ({1024 * 1024 + 1}))\n")
        self.assertEqual(oversize["errors"][0]["code"], "runtime_result_too_large")


if __name__ == "__main__":
    unittest.main()
