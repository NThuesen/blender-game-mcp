# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Opt-in parity tests for real Blender and standalone ``bpy`` CLI backends."""

__all__ = ()

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_DIR = os.path.join(_REPO_DIR, "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from blmcp.tools import (
    execute_blender_code,
    get_blendfile_summary_datablocks,
    get_blendfile_summary_missing_files,
    get_blendfile_summary_of_linked_libraries,
    get_blendfile_summary_path_info,
    get_blendfile_summary_usage_guess,
)

_BPY_PYTHON = os.environ.get("BLENDER_MCP_BPY_PYTHON")
_BPY_GATE_REASON = (
    "BLENDER_MCP_BPY_PYTHON is required for real Blender/bpy parity tests"
)

_SUMMARY_MODULES = (
    get_blendfile_summary_datablocks,
    get_blendfile_summary_missing_files,
    get_blendfile_summary_of_linked_libraries,
    get_blendfile_summary_path_info,
    get_blendfile_summary_usage_guess,
)
_SUMMARY_TOOL_NAMES = (
    "get_blendfile_summary_datablocks_for_cli",
    "get_blendfile_summary_missing_files_for_cli",
    "get_blendfile_summary_of_linked_libraries_for_cli",
    "get_blendfile_summary_path_info_for_cli",
    "get_blendfile_summary_usage_guess_for_cli",
)

# Only these schema fields are filesystem paths. The wildcard validates that its
# value is a list, permits an empty list, and applies the remaining path to every
# existing item.
_EACH_LIST_ITEM = "[]"
_SUMMARY_PATH_JSON_PATHS = (
    (
        "get_blendfile_summary_missing_files_for_cli",
        "missing_files",
        _EACH_LIST_ITEM,
        "path",
    ),
    (
        "get_blendfile_summary_of_linked_libraries_for_cli",
        "direct_libraries",
        _EACH_LIST_ITEM,
        "filepath",
    ),
    (
        "get_blendfile_summary_of_linked_libraries_for_cli",
        "indirect_libraries",
        _EACH_LIST_ITEM,
        "filepath",
    ),
    ("get_blendfile_summary_path_info_for_cli", "filepath"),
    (
        "get_blendfile_summary_path_info_for_cli",
        "backups",
        _EACH_LIST_ITEM,
        "path",
    ),
)


class _ToolRegistry:
    """Minimal FastMCP-compatible registry exposing registered tool callables."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., dict[str, object]]] = {}

    def tool(self, **_kwargs: object) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[function.__name__] = function
            return function

        return decorator


def _sha256(filepath: str) -> str:
    digest = hashlib.sha256()
    with open(filepath, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_json_path(json_path: tuple[str, ...]) -> str:
    """Format schema keys and list wildcards as a readable JSON path."""
    formatted = ""
    for key in json_path:
        if key.startswith("["):
            formatted += key
        elif formatted:
            formatted += ".{:s}".format(key)
        else:
            formatted = key
    return formatted


def _canonicalize_schema_path(
    value: object,
    json_path: tuple[str, ...],
    *,
    declared_path: tuple[str, ...] | None = None,
    location: tuple[str, ...] = (),
) -> None:
    """Validate and canonicalize one required, explicitly path-bearing field."""
    if declared_path is None:
        declared_path = json_path
    if not json_path:
        raise AssertionError(
            "path schema {!r} has no string leaf".format(
                _format_json_path(declared_path)
            )
        )

    key = json_path[0]
    remaining_path = json_path[1:]
    declared_name = _format_json_path(declared_path)
    location_name = _format_json_path(location) or "<root>"
    if key == _EACH_LIST_ITEM:
        if not isinstance(value, list):
            raise AssertionError(
                "path schema {:s} expected list at {:s}; got {:s}".format(
                    declared_name, location_name, type(value).__name__
                )
            )
        for index, item in enumerate(value):
            _canonicalize_schema_path(
                item,
                remaining_path,
                declared_path=declared_path,
                location=location + ("[{:d}]".format(index),),
            )
        return

    if not isinstance(value, dict):
        raise AssertionError(
            "path schema {:s} expected object at {:s}; got {:s}".format(
                declared_name, location_name, type(value).__name__
            )
        )
    field_location = location + (key,)
    if key not in value:
        raise AssertionError(
            "path schema {:s} missing required key at {:s}".format(
                declared_name, _format_json_path(field_location)
            )
        )
    if remaining_path:
        _canonicalize_schema_path(
            value[key],
            remaining_path,
            declared_path=declared_path,
            location=field_location,
        )
        return
    if not isinstance(value[key], str):
        raise AssertionError(
            "path schema {:s} expected string at {:s}; got {:s}".format(
                declared_name,
                _format_json_path(field_location),
                type(value[key]).__name__,
            )
        )
    value[key] = os.path.realpath(value[key])


def _normalize_for_summary_comparison(
    results: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Strictly validate/copy results, remove noise, and canonicalize paths."""
    normalized = copy.deepcopy(results)

    for tool_name in _SUMMARY_TOOL_NAMES:
        if tool_name not in normalized:
            raise AssertionError(
                "summary schema missing required tool payload at {:s}".format(tool_name)
            )
        if not isinstance(normalized[tool_name], dict):
            raise AssertionError(
                "summary schema expected object at {:s}; got {:s}".format(
                    tool_name, type(normalized[tool_name]).__name__
                )
            )

    for json_path in _SUMMARY_PATH_JSON_PATHS:
        _canonicalize_schema_path(normalized, json_path)

    path_info = normalized["get_blendfile_summary_path_info_for_cli"]
    path_info.pop("age_seconds", None)
    backups = path_info["backups"]
    for backup in backups:
        backup.pop("age_seconds", None)
    return normalized


class TestSummaryComparisonNormalization(unittest.TestCase):
    """Verify only schema-declared filesystem paths use canonical equality."""

    def test_path_aliases_compare_equal_without_mutating_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            canonical_dir = os.path.join(tmpdir, "canonical")
            alias_dir = os.path.join(tmpdir, "alias")
            os.mkdir(canonical_dir)
            os.symlink(canonical_dir, alias_dir)

            canonical = self._summary_payload(canonical_dir, semantic_label="canonical")
            aliased = self._summary_payload(alias_dir, semantic_label="canonical")
            original_canonical = copy.deepcopy(canonical)
            original_aliased = copy.deepcopy(aliased)

            self.assertEqual(
                _normalize_for_summary_comparison(canonical),
                _normalize_for_summary_comparison(aliased),
            )
            self.assertEqual(canonical, original_canonical)
            self.assertEqual(aliased, original_aliased)

    def test_missing_required_path_key_fails_with_declared_path(self) -> None:
        payload = self._summary_payload("/tmp/schema", semantic_label="unchanged")
        del payload["get_blendfile_summary_missing_files_for_cli"]["missing_files"]

        with self.assertRaisesRegex(
            AssertionError,
            r"get_blendfile_summary_missing_files_for_cli\.missing_files\[\]\.path"
            r" missing required key.*missing_files",
        ):
            _normalize_for_summary_comparison(payload)

    def test_wrong_list_container_fails_with_declared_path(self) -> None:
        payload = self._summary_payload("/tmp/schema", semantic_label="unchanged")
        payload["get_blendfile_summary_of_linked_libraries_for_cli"][
            "direct_libraries"
        ] = {}

        with self.assertRaisesRegex(
            AssertionError,
            r"get_blendfile_summary_of_linked_libraries_for_cli"
            r"\.direct_libraries\[\]\.filepath expected list",
        ):
            _normalize_for_summary_comparison(payload)

    def test_wrong_list_item_container_names_its_index(self) -> None:
        payload = self._summary_payload("/tmp/schema", semantic_label="unchanged")
        payload["get_blendfile_summary_of_linked_libraries_for_cli"][
            "direct_libraries"
        ] = [None]

        with self.assertRaisesRegex(
            AssertionError,
            r"get_blendfile_summary_of_linked_libraries_for_cli"
            r"\.direct_libraries\[\]\.filepath expected object"
            r".*direct_libraries\[0\]",
        ):
            _normalize_for_summary_comparison(payload)

    def test_non_string_path_leaf_fails_with_declared_path(self) -> None:
        payload = self._summary_payload("/tmp/schema", semantic_label="unchanged")
        payload["get_blendfile_summary_path_info_for_cli"]["backups"][0][
            "path"
        ] = None

        with self.assertRaisesRegex(
            AssertionError,
            r"get_blendfile_summary_path_info_for_cli\.backups\[\]\.path"
            r" expected string.*backups\[0\]\.path",
        ):
            _normalize_for_summary_comparison(payload)

    def test_empty_path_collections_are_valid(self) -> None:
        payload = self._summary_payload("/tmp/schema", semantic_label="unchanged")
        payload["get_blendfile_summary_missing_files_for_cli"]["missing_files"] = []
        libraries = payload["get_blendfile_summary_of_linked_libraries_for_cli"]
        libraries["direct_libraries"] = []
        libraries["indirect_libraries"] = []
        payload["get_blendfile_summary_path_info_for_cli"]["backups"] = []

        normalized = _normalize_for_summary_comparison(payload)

        self.assertEqual(
            normalized["get_blendfile_summary_missing_files_for_cli"][
                "missing_files"
            ],
            [],
        )
        self.assertEqual(libraries["direct_libraries"], [])
        self.assertEqual(libraries["indirect_libraries"], [])
        self.assertEqual(
            normalized["get_blendfile_summary_path_info_for_cli"]["backups"], []
        )

    def test_all_five_tool_payloads_are_required(self) -> None:
        payload = self._summary_payload("/tmp/schema", semantic_label="unchanged")
        del payload["get_blendfile_summary_usage_guess_for_cli"]

        with self.assertRaisesRegex(
            AssertionError,
            r"missing required tool payload at get_blendfile_summary_usage_guess_for_cli",
        ):
            _normalize_for_summary_comparison(payload)

    def test_only_declared_paths_and_runtime_age_fields_change(self) -> None:
        payload = self._summary_payload("/tmp/../tmp/schema", semantic_label="../label")
        expected = copy.deepcopy(payload)
        expected["get_blendfile_summary_missing_files_for_cli"]["missing_files"][0][
            "path"
        ] = os.path.realpath("/tmp/../tmp/schema/missing.png")
        libraries = expected["get_blendfile_summary_of_linked_libraries_for_cli"]
        libraries["direct_libraries"][0]["filepath"] = os.path.realpath(
            "/tmp/../tmp/schema/direct.blend"
        )
        libraries["indirect_libraries"][0]["filepath"] = os.path.realpath(
            "/tmp/../tmp/schema/indirect.blend"
        )
        path_info = expected["get_blendfile_summary_path_info_for_cli"]
        path_info["filepath"] = os.path.realpath("/tmp/../tmp/schema/fixture.blend")
        path_info.pop("age_seconds")
        path_info["backups"][0]["path"] = os.path.realpath(
            "/tmp/../tmp/schema/fixture.blend1"
        )
        path_info["backups"][0].pop("age_seconds")

        self.assertEqual(_normalize_for_summary_comparison(payload), expected)

    def test_different_paths_and_non_path_fields_still_compare_unequal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            first_root = os.path.join(tmpdir, "first")
            alias_root = os.path.join(tmpdir, "first_alias")
            os.mkdir(first_root)
            os.symlink(first_root, alias_root)
            first = self._summary_payload(first_root, semantic_label=first_root)
            different_path = self._summary_payload(
                os.path.join(tmpdir, "second"), semantic_label=first_root
            )
            different_semantics = self._summary_payload(
                first_root, semantic_label=alias_root
            )

            normalized_first = _normalize_for_summary_comparison(first)
            self.assertNotEqual(
                normalized_first,
                _normalize_for_summary_comparison(different_path),
            )
            self.assertNotEqual(
                normalized_first,
                _normalize_for_summary_comparison(different_semantics),
            )

    @staticmethod
    def _summary_payload(root: str, *, semantic_label: str) -> dict[str, dict[str, Any]]:
        return {
            "get_blendfile_summary_datablocks_for_cli": {
                "datablock_counts": {"objects": 1},
                "scene_name": "SyntheticScene",
            },
            "get_blendfile_summary_missing_files_for_cli": {
                "missing_files": [
                    {
                        "id_type": "Image",
                        "id_name": "MissingTexture",
                        "path": os.path.join(root, "missing.png"),
                    }
                ],
                "semantic_label": semantic_label,
                "total_checked": 1,
            },
            "get_blendfile_summary_of_linked_libraries_for_cli": {
                "direct_libraries": [
                    {
                        "filepath": os.path.join(root, "direct.blend"),
                        "name": "Direct",
                        "linked_datablocks_count": 1,
                    }
                ],
                "indirect_libraries": [
                    {
                        "filepath": os.path.join(root, "indirect.blend"),
                        "name": "Indirect",
                        "parent_library": "Direct",
                        "linked_datablocks_count": 2,
                    }
                ],
                "total_library_count": 2,
            },
            "get_blendfile_summary_path_info_for_cli": {
                "filepath": os.path.join(root, "fixture.blend"),
                "age_seconds": 1.0,
                "backups": [
                    {
                        "path": os.path.join(root, "fixture.blend1"),
                        "age_seconds": 2.0,
                        "size_bytes": 10,
                    }
                ],
            },
            "get_blendfile_summary_usage_guess_for_cli": {
                "usage_guesses": {
                    "Modeling": {"score": 1, "reason": semantic_label},
                },
            },
        }


@unittest.skipUnless(_BPY_PYTHON, _BPY_GATE_REASON)
class TestRealBlenderBpyParity(unittest.TestCase):
    """Exercise identical saved files through separate real runtime processes."""

    _tools: dict[str, Callable[..., dict[str, object]]]
    _tmpdir: tempfile.TemporaryDirectory[str]
    _blender: str
    _bpy_python: str
    _library_path: str
    _fixture_path: str
    _missing_image_path: str

    def assertPathsResolveSame(self, actual: object, expected: str) -> None:
        """Assert path identity while preserving the original values in diagnostics."""
        self.assertEqual(
            os.path.realpath(str(actual)),
            os.path.realpath(expected),
            "paths differ: actual={!r}, expected={!r}".format(actual, expected),
        )

    @classmethod
    def setUpClass(cls) -> None:
        assert _BPY_PYTHON is not None
        cls._blender = os.environ.get("BLENDER_PATH", "blender")
        cls._bpy_python = _BPY_PYTHON
        cls._tmpdir = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        cls.addClassCleanup(cls._tmpdir.cleanup)

        registry = _ToolRegistry()
        for module in _SUMMARY_MODULES:
            module.register(registry)
        execute_blender_code.register(registry)  # type: ignore[arg-type]
        cls._tools = registry.tools

        cls._library_path = os.path.join(cls._tmpdir.name, "parity_library.blend")
        cls._fixture_path = os.path.join(cls._tmpdir.name, "parity_fixture.blend")
        cls._missing_image_path = os.path.join(
            cls._tmpdir.name, "assets", "missing_texture.png"
        )
        cls._create_fixture()

    @classmethod
    def _create_fixture(cls) -> None:
        code = "\n".join((
            "import bpy",
            "import os",
            "os.makedirs({!r}, exist_ok=True)".format(
                os.path.dirname(cls._missing_image_path)
            ),
            "bpy.ops.wm.read_factory_settings(use_empty=True)",
            "mesh = bpy.data.meshes.new('LinkedMesh')",
            "mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])",
            "linked_object = bpy.data.objects.new('LinkedAsset', mesh)",
            "bpy.context.scene.collection.objects.link(linked_object)",
            "bpy.ops.wm.save_as_mainfile(filepath={!r})".format(cls._library_path),
            "bpy.ops.wm.read_factory_settings(use_empty=True)",
            "scene = bpy.context.scene",
            "scene.name = 'ParityScene'",
            "scene.render.filepath = '//renders/parity.png'",
            "mesh = bpy.data.meshes.new('ParityMesh')",
            (
                "mesh.from_pydata([(0, 0, 0), (2, 0, 0), (0, 2, 0), "
                "(0, 0, 2)], [], [(0, 1, 2), (0, 1, 3)])"
            ),
            "mesh.uv_layers.new(name='UVMap')",
            "mesh.uv_layers.new(name='DetailUV')",
            "local_object = bpy.data.objects.new('ParityObject', mesh)",
            "scene.collection.objects.link(local_object)",
            "local_object.modifiers.new(name='ParityBevel', type='BEVEL')",
            "armature = bpy.data.armatures.new('ParityRig')",
            "rig_object = bpy.data.objects.new('ParityRig', armature)",
            "scene.collection.objects.link(rig_object)",
            "bpy.data.actions.new('ParityAction')",
            "bpy.data.texts.new('parity_script.py').write('print(\\\"parity\\\")')",
            "missing_image = bpy.data.images.new('MissingTexture', width=1, height=1)",
            "missing_image.filepath_raw = {!r}".format(cls._missing_image_path),
            "missing_image.file_format = 'PNG'",
            "missing_image.save()",
            "bpy.data.images.remove(missing_image)",
            "missing_image = bpy.data.images.load({!r})".format(cls._missing_image_path),
            "missing_image.name = 'MissingTexture'",
            "missing_image.use_fake_user = True",
            "with bpy.data.libraries.load({!r}, link=True) as "
            "(data_from, data_to):".format(cls._library_path),
            "    data_to.objects = ['LinkedAsset']",
            "for linked in data_to.objects:",
            "    if linked is not None:",
            "        scene.collection.objects.link(linked)",
            "bpy.ops.wm.save_as_mainfile(filepath={!r})".format(cls._fixture_path),
        ))
        completed = subprocess.run(
            [
                cls._blender,
                "--background",
                "--factory-startup",
                "--python-expr",
                code,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Fixture creation failed (exit {:d})\nstdout:\n{:s}\nstderr:\n{:s}".format(
                    completed.returncode, completed.stdout, completed.stderr
                )
            )
        if not os.path.isfile(cls._fixture_path):
            raise RuntimeError("Fixture creation did not produce {!r}".format(cls._fixture_path))
        if not os.path.isfile(cls._missing_image_path):
            raise RuntimeError(
                "Fixture creation did not produce {!r}".format(cls._missing_image_path)
            )
        os.remove(cls._missing_image_path)
        print("fixture created: {:s}".format(cls._fixture_path))

    @classmethod
    def _backend_env(cls, backend: str) -> dict[str, str]:
        if backend == "blender":
            return {
                "BLENDER_MCP_CLI_BACKEND": "blender",
                "BLENDER_PATH": cls._blender,
            }
        return {
            "BLENDER_MCP_CLI_BACKEND": "bpy",
            "BLENDER_MCP_BPY_PYTHON": cls._bpy_python,
        }

    @classmethod
    def _call_tool(
        cls,
        backend: str,
        name: str,
        *,
        blend_file: str | None = None,
        code: str | None = None,
    ) -> dict[str, object]:
        arguments: dict[str, object] = {
            "blend_file": blend_file or cls._fixture_path,
        }
        if code is not None:
            arguments["code"] = code
        with mock.patch.dict(os.environ, cls._backend_env(backend), clear=False):
            return cls._tools[name](**arguments)

    @classmethod
    def _run_summaries(cls, backend: str) -> dict[str, dict[str, object]]:
        results = {
            name: cls._call_tool(backend, name)
            for name in _SUMMARY_TOOL_NAMES
        }
        print("{:s} summary tools complete: {:s}".format(
            backend, ", ".join(_SUMMARY_TOOL_NAMES)
        ))
        return results

    def test_standalone_runner_reports_accepted_versions(self) -> None:
        code = (
            "import bpy, platform\n"
            "result = {\n"
            "    'bpy_version': '.'.join(str(value) for value in bpy.app.version),\n"
            "    'python_version': platform.python_version(),\n"
            "}\n"
        )
        standalone = self._call_tool(
            "bpy", "execute_blender_code_for_cli", code=code
        )
        blender = self._call_tool(
            "blender", "execute_blender_code_for_cli", code=code
        )

        self.assertEqual(standalone["bpy_version"], "5.2.1")
        standalone_python_version = str(standalone["python_version"])
        standalone_python_abi = tuple(
            int(value) for value in standalone_python_version.split(".")[:2]
        )
        self.assertEqual(
            standalone_python_abi,
            (3, 13),
            "unsupported standalone Python version: {!r}".format(
                standalone_python_version
            ),
        )
        self.assertEqual(blender["bpy_version"], standalone["bpy_version"])
        print("runtime versions: standalone={!r}, blender={!r}".format(
            standalone, blender
        ))

    def test_all_cli_summaries_have_semantic_parity(self) -> None:
        blender_results = self._run_summaries("blender")
        bpy_results = self._run_summaries("bpy")

        normalized_blender = _normalize_for_summary_comparison(blender_results)
        normalized_bpy = _normalize_for_summary_comparison(bpy_results)
        self.assertEqual(normalized_blender, normalized_bpy)

        datablocks = normalized_bpy["get_blendfile_summary_datablocks_for_cli"]
        counts = datablocks["datablock_counts"]
        self.assertIsInstance(counts, dict)
        assert isinstance(counts, dict)
        self.assertGreaterEqual(counts["objects"], 3)
        self.assertGreaterEqual(counts["meshes"], 2)
        self.assertEqual(datablocks["scene_name"], "ParityScene")

        missing = normalized_bpy["get_blendfile_summary_missing_files_for_cli"]
        missing_files = missing["missing_files"]
        self.assertIsInstance(missing_files, list)
        assert isinstance(missing_files, list)
        self.assertEqual(len(missing_files), 1)
        self.assertEqual(missing_files[0]["id_name"], "MissingTexture")
        self.assertTrue(missing_files[0]["path"].endswith("assets/missing_texture.png"))

        libraries = normalized_bpy[
            "get_blendfile_summary_of_linked_libraries_for_cli"
        ]
        self.assertEqual(libraries["total_library_count"], 1)
        direct_libraries = libraries["direct_libraries"]
        self.assertIsInstance(direct_libraries, list)
        assert isinstance(direct_libraries, list)
        self.assertEqual(len(direct_libraries), 1)
        self.assertEqual(libraries["indirect_libraries"], [])

        path_info = normalized_bpy["get_blendfile_summary_path_info_for_cli"]
        self.assertPathsResolveSame(path_info["filepath"], self._fixture_path)
        self.assertTrue(path_info["is_saved"])
        self.assertFalse(path_info["is_dirty"])
        self.assertEqual(path_info["backups"], [])

        usage = normalized_bpy["get_blendfile_summary_usage_guess_for_cli"]
        guesses = usage["usage_guesses"]
        self.assertIsInstance(guesses, dict)
        assert isinstance(guesses, dict)
        for classification in ("Animation", "Modeling", "Scripting", "UV Unwrapping"):
            self.assertGreater(guesses[classification]["score"], 0)

        print("semantic parity complete: {:s}".format(
            ", ".join(sorted(normalized_bpy))
        ))
        print("normalized parity result: {:s}".format(
            json.dumps(normalized_bpy, sort_keys=True)
        ))

    def test_mutation_saves_new_file_without_changing_source(self) -> None:
        source_hash = _sha256(self._fixture_path)
        mutation_hashes: dict[str, str] = {}

        for index, backend in enumerate(("blender", "bpy"), start=1):
            with self.subTest(backend=backend):
                output_path = os.path.join(
                    self._tmpdir.name, "mutated_{:s}.blend".format(backend)
                )
                self.assertNotEqual(
                    os.path.realpath(output_path),
                    os.path.realpath(self._fixture_path),
                )
                mutation = self._call_tool(
                    backend,
                    "execute_blender_code_for_cli",
                    code=(
                        "import bpy\n"
                        "obj = bpy.data.objects['ParityObject']\n"
                        "obj.location.x = {:d}\n"
                        "bpy.ops.wm.save_as_mainfile(filepath={!r})\n"
                        "result = {{'filepath': bpy.data.filepath, 'location_x': obj.location.x}}\n"
                    ).format(index * 10, output_path),
                )
                self.assertPathsResolveSame(mutation["filepath"], output_path)
                self.assertEqual(mutation["location_x"], index * 10)
                self.assertTrue(os.path.isfile(output_path))
                self.assertEqual(_sha256(self._fixture_path), source_hash)

                reopened = self._call_tool(
                    backend,
                    "execute_blender_code_for_cli",
                    blend_file=output_path,
                    code=(
                        "import bpy\n"
                        "result = {\n"
                        "    'filepath': bpy.data.filepath,\n"
                        "    'location_x': bpy.data.objects['ParityObject'].location.x,\n"
                        "}\n"
                    ),
                )
                self.assertPathsResolveSame(reopened["filepath"], output_path)
                self.assertEqual(reopened["location_x"], index * 10)
                output_hash = _sha256(output_path)
                self.assertNotEqual(output_hash, source_hash)
                mutation_hashes[backend] = output_hash

        self.assertEqual(_sha256(self._fixture_path), source_hash)
        print(
            "mutation safety complete: source={:s} blender={:s} bpy={:s}".format(
                source_hash,
                mutation_hashes["blender"],
                mutation_hashes["bpy"],
            )
        )


if __name__ == "__main__":
    unittest.main()
