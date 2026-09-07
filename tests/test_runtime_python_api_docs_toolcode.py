# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for bounded Blender runtime API documentation lookup."""

# Test fixtures intentionally expose minimal sentinel classes, inspect private
# safety helpers, and use descriptive test names instead of method docstrings.
# pylint: disable=consider-using-f-string,invalid-hash-returned,invalid-repr-returned
# pylint: disable=invalid-str-returned,missing-class-docstring,missing-function-docstring
# pylint: disable=non-iterator-returned,protected-access,too-few-public-methods
# pylint: disable=too-many-boolean-expressions,too-many-lines,too-many-public-methods

from __future__ import annotations

__all__ = ()

import ast
import copy
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import types
import unittest
from collections.abc import Iterator
from typing import Any, ClassVar, cast
from unittest import mock

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLCODE_PATH = os.path.join(
    _REPO_DIR, "mcp", "blmcp", "tools", "get_runtime_python_api_docs_toolcode.py"
)
_MAX_OUTPUT_BYTES = 8192
_TEST_RUNTIME = ((5, 2, 1), "5.2.1", (3, 13, 13))


def _load_toolcode() -> Any:
    spec = importlib.util.spec_from_file_location(
        "runtime_api_docs_toolcode", _TOOLCODE_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ExplodingDescriptor:
    def __init__(self, sentinel: list[str], label: str) -> None:
        self._sentinel = sentinel
        self._label = label

    def __get__(self, _instance: object, _owner: object) -> Any:
        self._sentinel.append(self._label)
        raise AssertionError("documentation lookup invoked a descriptor")


class _HostileText:
    def __init__(self, sentinel: list[str], label: str) -> None:
        self._sentinel = sentinel
        self._label = label

    def __repr__(self) -> str:
        self._sentinel.append("repr:" + self._label)
        raise AssertionError("documentation lookup invoked repr")

    def __str__(self) -> str:
        self._sentinel.append("str:" + self._label)
        raise AssertionError("documentation lookup invoked str")


class _HostileAnnotationMeta(type):
    sentinel: ClassVar[list[str]] = []

    def __hash__(cls) -> int:
        cls.sentinel.append("hash")
        raise AssertionError("documentation lookup invoked annotation hash")

    def __eq__(cls, _other: object) -> bool:
        cls.sentinel.append("eq")
        raise AssertionError("documentation lookup invoked annotation equality")

    def __repr__(cls) -> str:
        cls.sentinel.append("repr")
        raise AssertionError("documentation lookup invoked annotation repr")

    def __str__(cls) -> str:
        cls.sentinel.append("str")
        raise AssertionError("documentation lookup invoked annotation str")

    def __getattribute__(cls, name: str) -> Any:
        if name == "sentinel":
            return type.__getattribute__(cls, name)
        type.__getattribute__(cls, "sentinel").append("getattribute:" + name)
        raise AssertionError("documentation lookup read annotation metadata")


class _HostileAnnotation(metaclass=_HostileAnnotationMeta):
    pass


class _HostileDoc(str):
    sentinel: ClassVar[list[str]] = []

    def __getattribute__(self, name: str) -> Any:
        type(self).sentinel.append("getattribute:" + name)
        raise AssertionError("documentation lookup read hostile doc metadata")

    def __repr__(self) -> str:
        type(self).sentinel.append("repr")
        raise AssertionError("documentation lookup invoked hostile doc repr")

    def __str__(self) -> str:
        type(self).sentinel.append("str")
        raise AssertionError("documentation lookup invoked hostile doc str")

    def splitlines(self, *_args: object, **_kwargs: object) -> list[str]:
        type(self).sentinel.append("splitlines")
        raise AssertionError("documentation lookup invoked hostile doc splitlines")

    def strip(self, *_args: object, **_kwargs: object) -> str:
        type(self).sentinel.append("strip")
        raise AssertionError("documentation lookup invoked hostile doc strip")


class _CollidingNamespaceKey:
    callbacks: ClassVar[list[str]] = []

    def __init__(self, target: str) -> None:
        self._target_hash = hash(target)

    def __hash__(self) -> int:
        type(self).callbacks.append("hash")
        return self._target_hash

    def __eq__(self, _other: object) -> bool:
        type(self).callbacks.append("eq")
        return False

    def __repr__(self) -> str:
        type(self).callbacks.append("repr")
        raise AssertionError("documentation lookup represented a namespace key")

    def __str__(self) -> str:
        type(self).callbacks.append("str")
        raise AssertionError("documentation lookup stringified a namespace key")


class TestRuntimePythonAPIDocsToolcode(unittest.TestCase):
    def setUp(self) -> None:
        self.toolcode = _load_toolcode()
        fake_bpy: Any = types.ModuleType("bpy")
        fake_bpy.__doc__ = "Blender Python API root module."
        fake_bpy.answer = 42
        self.fake_bpy = fake_bpy

    def _lookup(self, identifier: str) -> dict[str, Any]:
        with mock.patch.dict(sys.modules, {"bpy": self.fake_bpy}):
            result = self.toolcode.main({"identifier": identifier})
        json.dumps(result)
        return cast(dict[str, Any], result)

    def test_resolves_module_and_safe_regular_attribute(self) -> None:
        module_result = self._lookup("bpy")
        attribute_result = self._lookup("bpy.answer")

        self.assertTrue(module_result["found"])
        self.assertEqual(module_result["kind"], "module")
        self.assertEqual(
            module_result["description"], "Blender Python API root module."
        )
        self.assertTrue(attribute_result["found"])
        self.assertEqual(attribute_result["kind"], "attribute")
        self.assertEqual(attribute_result["value"], 42)

    def test_exact_module_lookup_ignores_colliding_non_string_key(self) -> None:
        hostile_key = _CollidingNamespaceKey("probe")
        self.fake_bpy.__dict__[hostile_key] = "hostile"
        self.fake_bpy.probe = 42
        _CollidingNamespaceKey.callbacks = []

        result = self._lookup("bpy.probe")

        self.assertTrue(result["found"], result)
        self.assertEqual(result["value"], 42)
        self.assertEqual(_CollidingNamespaceKey.callbacks, [])

    def test_intermediate_module_ignores_colliding_non_string_key(self) -> None:
        intermediate: Any = types.ModuleType("bpy.safe")
        hostile_key = _CollidingNamespaceKey("probe")
        intermediate.__dict__[hostile_key] = "hostile"
        intermediate.probe = 42
        self.fake_bpy.safe = intermediate
        _CollidingNamespaceKey.callbacks = []

        result = self._lookup("bpy.safe.probe")

        self.assertTrue(result["found"], result)
        self.assertEqual(result["value"], 42)
        self.assertEqual(_CollidingNamespaceKey.callbacks, [])

    def test_static_c_type_namespace_scan_resolves_exact_string_key(self) -> None:
        value = self.toolcode._type_namespace_value(str, "upper")

        self.assertIs(type(value), types.MethodDescriptorType)

    def test_mro_does_not_trust_unestablished_custom_metaclass_base(self) -> None:
        callbacks: list[str] = []

        def hostile_namespace(_cls: object) -> types.MappingProxyType[str, Any]:
            callbacks.append("__dict__")
            raise AssertionError("read untrusted MRO base namespace")

        HostileMeta = cast(Any, type)(
            "HostileMeta", (type,), {"__dict__": property(hostile_namespace)}
        )
        Base2 = HostileMeta("Base2", (), {"inherited": 42})
        TrustedDerived = HostileMeta("TrustedDerived", (Base2,), {})

        unsupported = object()
        value = self.toolcode._type_namespace_value(
            TrustedDerived,
            "inherited",
            unsupported,
            trusted_heap_identities=(TrustedDerived,),
        )

        self.assertIs(value, unsupported)
        self.assertEqual(callbacks, [])

    def test_independently_trusted_heap_identity_scans_its_namespace(self) -> None:
        class RegistryMeta(type):
            pass

        class RegistryRNAType(metaclass=RegistryMeta):
            bl_rna = 42

        self.assertTrue(
            RegistryRNAType.__flags__ & self.toolcode._PY_TPFLAGS_HEAPTYPE
        )
        self.assertEqual(
            self.toolcode._type_namespace_value(
                RegistryRNAType,
                "bl_rna",
                trusted_heap_identities=(RegistryRNAType,),
            ),
            42,
        )

    def test_type_namespace_rejects_malformed_trusted_identity_tuple(self) -> None:
        self.assertIs(
            self.toolcode._type_namespace_value(
                str,
                "upper",
                trusted_heap_identities=cast(Any, [str]),
            ),
            self.toolcode._MISSING,
        )
        self.assertIs(
            self.toolcode._type_namespace_value(
                str,
                "upper",
                trusted_heap_identities=cast(Any, (object(),)),
            ),
            self.toolcode._MISSING,
        )

    def test_class_mappingproxy_scan_ignores_colliding_non_string_key(self) -> None:
        hostile_key = _CollidingNamespaceKey("probe")
        namespace = {hostile_key: "hostile", "probe": 42}
        mapping = types.MappingProxyType(namespace)
        _CollidingNamespaceKey.callbacks = []

        value = self.toolcode._exact_mapping_value(mapping, "probe")

        self.assertEqual(value, 42)
        self.assertEqual(_CollidingNamespaceKey.callbacks, [])

    def test_module_level_python_function_is_documented_without_execution(self) -> None:
        sentinel = [0]

        def ordinary_function(count: int, label: str = "cube") -> bool:
            """Return whether a named collection has enough objects."""
            _ = count, label
            sentinel[0] += 1
            raise AssertionError("documentation lookup executed a function")

        self.fake_bpy.ordinary_function = ordinary_function
        result = self._lookup("bpy.ordinary_function")

        self.assertTrue(result["found"])
        self.assertEqual(result["kind"], "function")
        self.assertEqual(
            result["signature"], "bpy.ordinary_function(count, label='cube')"
        )
        self.assertEqual(sentinel, [0])

    def test_code_signature_preserves_parameter_kinds_and_safe_defaults(self) -> None:
        callbacks: list[str] = []
        hostile_default = _HostileText(callbacks, "keyword-only default")
        body_calls = [0]

        def ordinary_function(
            first: object,
            second: object = 2,
            /,
            third: object = "three",
            *args: object,
            required: object,
            option: object = hostile_default,
            **kwargs: object,
        ) -> None:
            _ = first, second, third, args, required, option, kwargs
            body_calls[0] += 1
            raise AssertionError("documentation lookup executed a function")

        self.fake_bpy.ordinary_function = ordinary_function
        result = self._lookup("bpy.ordinary_function")

        self.assertTrue(result["found"], result)
        self.assertEqual(
            result["signature"],
            "bpy.ordinary_function(first, second=2, /, third='three', *args, "
            "required, option=<unsupported>, **kwargs)",
        )
        self.assertTrue(result["truncated"])
        self.assertEqual(body_calls, [0])
        self.assertEqual(callbacks, [])

    def test_signature_ignores_hostile_signature_subclass_without_callbacks(
        self,
    ) -> None:
        callbacks: list[str] = []

        class HostileSignature(inspect.Signature):
            def __getattribute__(self, name: str) -> Any:
                callbacks.append("getattribute:" + name)
                raise AssertionError("documentation lookup read __signature__")

            def __str__(self) -> str:
                callbacks.append("str")
                raise AssertionError("documentation lookup stringified __signature__")

        def ordinary_function(value: object = 1) -> object:
            raise AssertionError("documentation lookup executed a function")

        ordinary_function.__signature__ = HostileSignature()  # type: ignore[attr-defined]
        self.fake_bpy.ordinary_function = ordinary_function
        result = self._lookup("bpy.ordinary_function")

        self.assertTrue(result["found"], result)
        self.assertEqual(result["signature"], "bpy.ordinary_function(value=1)")
        self.assertEqual(callbacks, [])

    def test_exact_builtin_uses_its_c_text_signature(self) -> None:
        self.fake_bpy.builtin_function = len

        result = self._lookup("bpy.builtin_function")

        self.assertTrue(result["found"], result)
        self.assertEqual(result["kind"], "function")
        self.assertEqual(result["signature"], "bpy.builtin_function(obj, /)")

    def test_signature_does_not_stringify_hostile_defaults_or_annotations(self) -> None:
        sentinel: list[str] = []
        hostile_default = _HostileText(sentinel, "default")
        hostile_annotation = _HostileText(sentinel, "annotation")

        def ordinary_function(value: object = hostile_default) -> object:
            """A function with hostile metadata."""
            return value

        ordinary_function.__annotations__ = {
            "value": hostile_annotation,
            "return": hostile_annotation,
        }
        self.fake_bpy.ordinary_function = ordinary_function
        result = self._lookup("bpy.ordinary_function")

        self.assertTrue(result["found"], result)
        self.assertEqual(result["kind"], "function")
        self.assertEqual(
            result["signature"], "bpy.ordinary_function(value=<unsupported>)"
        )
        self.assertTrue(result["truncated"])
        self.assertEqual(sentinel, [])

    def test_signature_does_not_invoke_hostile_annotation_metaclass(self) -> None:
        _HostileAnnotationMeta.sentinel = []

        def ordinary_function(value: object) -> object:
            """A function with a hostile type annotation."""
            return value

        ordinary_function.__annotations__ = {
            "value": _HostileAnnotation,
            "return": _HostileAnnotation,
        }
        self.fake_bpy.ordinary_function = ordinary_function
        result = self._lookup("bpy.ordinary_function")

        self.assertTrue(result["found"], result)
        self.assertEqual(result["kind"], "function")
        self.assertEqual(result["signature"], "bpy.ordinary_function(value)")
        self.assertFalse(result["truncated"])
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), _MAX_OUTPUT_BYTES)
        self.assertEqual(_HostileAnnotationMeta.sentinel, [])

    def test_doc_description_rejects_hostile_str_subclass_without_callbacks(
        self,
    ) -> None:
        _HostileDoc.sentinel = []

        def ordinary_function() -> None:
            return None

        ordinary_function.__doc__ = _HostileDoc("hostile documentation")
        self.fake_bpy.ordinary_function = ordinary_function
        result = self._lookup("bpy.ordinary_function")

        self.assertTrue(result["found"], result)
        self.assertEqual(result["kind"], "function")
        self.assertEqual(result["description"], "")
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), _MAX_OUTPUT_BYTES)
        self.assertEqual(_HostileDoc.sentinel, [])

    def test_instance_property_remains_unsupported_and_uninvoked(self) -> None:
        sentinel: list[str] = []

        class Dangerous:
            ordinary_function = _ExplodingDescriptor(sentinel, "ordinary_function")

        self.fake_bpy.dangerous_property = Dangerous()
        result = self._lookup("bpy.dangerous_property.ordinary_function")

        self.assertFalse(result["found"])
        self.assertEqual(result["error"], "unsupported")
        self.assertEqual(sentinel, [])

    def test_context_and_data_paths_remain_unsupported(self) -> None:
        sentinel: list[str] = []

        class Scene:
            addon_property = _ExplodingDescriptor(sentinel, "addon_property")

        self.fake_bpy.context = types.SimpleNamespace(scene=Scene())
        result = self._lookup("bpy.context.scene.addon_property")

        self.assertFalse(result["found"])
        self.assertEqual(result["error"], "unsupported")
        self.assertEqual(sentinel, [])

    def test_substituted_bpy_app_descriptor_is_never_invoked(self) -> None:
        sentinel: list[str] = []

        class DangerousBpy(types.ModuleType):
            app = _ExplodingDescriptor(sentinel, "bpy.app")

        dangerous_bpy = DangerousBpy("bpy")
        with mock.patch.dict(sys.modules, {"bpy": dangerous_bpy}):
            result = self.toolcode.main({"identifier": "bpy.app.version_string"})

        self.assertFalse(result["found"])
        self.assertEqual(result["error"], "unsupported")
        self.assertEqual(sentinel, [])

    def test_substituted_app_field_descriptors_are_never_invoked(self) -> None:
        sentinel: list[str] = []

        class DangerousApp:
            version = _ExplodingDescriptor(sentinel, "app.version")
            version_string = _ExplodingDescriptor(sentinel, "app.version_string")

        self.fake_bpy.app = DangerousApp()
        result = self._lookup("bpy")
        field_result = self._lookup("bpy.app.version_string")

        self.assertTrue(result["found"])
        self.assertEqual(result["runtime"]["blender"], "unknown")
        self.assertFalse(field_result["found"])
        self.assertEqual(field_result["error"], "unsupported")
        self.assertEqual(sentinel, [])

    def test_substituted_app_custom_metaclass_flags_are_never_read(self) -> None:
        callbacks: list[str] = []

        class Meta(type):
            @property
            def __flags__(cls) -> int:
                callbacks.append("__flags__")
                raise AssertionError("read substituted app type flags")

        class DangerousApp(metaclass=Meta):
            pass

        self.fake_bpy.app = DangerousApp()
        result = self._lookup("bpy.app.version_string")

        self.assertFalse(result["found"], result)
        self.assertEqual(result["error"], "unsupported")
        self.assertEqual(callbacks, [])

    def test_passive_operator_rna_and_operator_hooks_are_never_read_or_called(
        self,
    ) -> None:
        sentinel: list[str] = []

        class DangerousRNA:
            identifier = _ExplodingDescriptor(sentinel, "rna.identifier")
            properties = _ExplodingDescriptor(sentinel, "rna.properties")

        class DangerousOperator:
            _rna = DangerousRNA()

            def get_rna_type(self) -> object:
                sentinel.append("operator.get_rna_type")
                raise AssertionError("called substituted operator")

            def __iter__(self) -> Iterator[object]:
                sentinel.append("operator.iter")
                raise AssertionError("iterated substituted operator")

        fake_ops: Any = types.ModuleType("bpy.ops")
        fake_ops.mesh = types.SimpleNamespace(primitive_cube_add=DangerousOperator())
        self.fake_bpy.ops = fake_ops
        result = self._lookup("bpy.ops.mesh.primitive_cube_add")

        self.assertFalse(result["found"])
        self.assertEqual(result["error"], "unsupported")
        self.assertEqual(sentinel, [])

    def test_substituted_factory_and_low_level_import_hooks_are_never_called(
        self,
    ) -> None:
        cached_calls: list[object] = []
        imported_calls: list[object] = []
        fake_ops: Any = types.ModuleType("bpy.ops")
        fake_ops._op_create_function = cached_calls.append
        self.fake_bpy.ops = fake_ops
        fake_low_level: Any = types.ModuleType("_bpy")
        fake_low_level.ops = types.SimpleNamespace(get_rna_type=imported_calls.append)

        with mock.patch.dict(
            sys.modules, {"bpy": self.fake_bpy, "_bpy": fake_low_level}
        ):
            result = self.toolcode.main(
                {"identifier": "bpy.ops.mesh.primitive_cube_add"}
            )

        self.assertFalse(result["found"])
        self.assertEqual(result["error"], "unsupported")
        self.assertEqual(cached_calls, [])
        self.assertEqual(imported_calls, [])

    def test_fake_rna_collections_get_and_iteration_are_never_invoked(self) -> None:
        sentinel: list[str] = []

        class DangerousCollection:
            def get(self, _name: str) -> object:
                sentinel.append("properties.get")
                raise AssertionError("called properties.get")

            def __iter__(self) -> Iterator[object]:
                sentinel.append("properties.iter")
                raise AssertionError("iterated fake properties")

        class FakeRNA:
            __module__ = "bpy.types"

            def __init__(self) -> None:
                self.properties = DangerousCollection()

        provenance = self.toolcode._provenance_from_rna(FakeRNA())

        self.assertIs(provenance, self.toolcode._MISSING)
        self.assertEqual(sentinel, [])

    def test_fake_rna_property_descriptor_is_rejected_before_access(self) -> None:
        sentinel: list[str] = []

        class FakeRNA:
            __module__ = "bpy.types"
            properties = _ExplodingDescriptor(sentinel, "rna.properties")

        provenance = self.toolcode._provenance_from_rna(FakeRNA())

        self.assertIs(provenance, self.toolcode._MISSING)
        self.assertEqual(sentinel, [])

    def test_heap_rna_subclass_is_rejected_before_dynamic_field_access(self) -> None:
        sentinel: list[str] = []

        class CallbackProbe(list[object]):
            __module__ = "bpy.types"
            __slots__ = ()

            def __getattribute__(self, name: str) -> Any:
                sentinel.append(name)
                raise AssertionError("read heap-allocated RNA metadata")

        probe = CallbackProbe()
        provenance = self.toolcode._RNAProvenance(list, list, len, dict)

        self.assertTrue(type(probe).__flags__ & self.toolcode._PY_TPFLAGS_HEAPTYPE)
        self.assertIs(
            self.toolcode._rna_field(probe, "description", provenance),
            self.toolcode._MISSING,
        )
        self.assertEqual(sentinel, [])

    def test_heap_type_is_rejected_before_spoofable_metadata_or_type_callbacks(
        self,
    ) -> None:
        callbacks: list[str] = []

        class HostileMetadata:
            def __eq__(self, _other: object) -> bool:
                callbacks.append("metadata:eq")
                raise AssertionError("compared spoofed type metadata")

        class HostileMeta(type):
            def __getattribute__(cls, name: str) -> Any:
                callbacks.append("metaclass:getattribute:" + name)
                if name in {"__module__", "__name__", "__mro__"}:
                    return HostileMetadata()
                return type.__getattribute__(cls, name)

            def __eq__(cls, _other: object) -> bool:
                callbacks.append("metaclass:eq")
                raise AssertionError("compared hostile type")

            def __hash__(cls) -> int:
                callbacks.append("metaclass:hash")
                raise AssertionError("hashed hostile type")

        class HostileRNA(metaclass=HostileMeta):
            __module__ = "bpy.types"

        metadata_reads: list[str] = []
        trusted_type_field = self.toolcode._type_field

        def recording_type_field(
            value: type, name: str, default: object = self.toolcode._MISSING
        ) -> object:
            metadata_reads.append(name)
            return trusted_type_field(value, name, default)

        with mock.patch.object(
            self.toolcode, "_type_field", side_effect=recording_type_field
        ):
            self.assertFalse(
                self.toolcode._is_static_c_type(
                    HostileRNA, "bpy.types", "bpy_struct"
                )
            )
        provenance = self.toolcode._RNAProvenance((dict,), list, len, dict)
        self.assertFalse(self.toolcode._is_rna(HostileRNA(), provenance))

        self.assertEqual(metadata_reads, [])
        self.assertEqual(callbacks, [])

    def test_custom_metaclasses_are_rejected_at_type_helper_entrances(self) -> None:
        callbacks: list[str] = []

        class HostileMeta(type):
            @property
            def __flags__(cls) -> int:
                callbacks.append("__flags__")
                raise AssertionError("read hostile flags")

            def __getattribute__(cls, name: str) -> Any:
                if name == "__flags__":
                    return type.__getattribute__(cls, name)
                callbacks.append(name)
                raise AssertionError("read hostile type metadata")

        class HostileType(metaclass=HostileMeta):
            pass

        provenance = self.toolcode._RNAProvenance((dict,), list, len, dict)

        self.assertIs(
            self.toolcode._type_field(HostileType, "__flags__"),
            self.toolcode._MISSING,
        )
        self.assertIs(
            self.toolcode._type_namespace_value(HostileType, "bl_rna"),
            self.toolcode._MISSING,
        )
        self.assertIs(
            self.toolcode._type_rna(HostileType, provenance),
            self.toolcode._MISSING,
        )
        self.assertIs(
            self.toolcode._rna_collection(
                HostileType(), "properties", provenance
            ),
            self.toolcode._MISSING,
        )
        operator_rna, operator_status = self.toolcode._operator_rna_from_wrapper(
            HostileType()
        )
        self.assertIs(operator_rna, self.toolcode._MISSING)
        self.assertEqual(operator_status, "unsupported")
        self.assertEqual(callbacks, [])

    def test_module_subclasses_and_hostile_name_lookalikes_are_callback_free(
        self,
    ) -> None:
        callbacks: list[str] = []

        class HostileModule(types.ModuleType):
            def __getattribute__(self, name: str) -> Any:
                callbacks.append("module:getattribute:" + name)
                raise AssertionError("read ModuleType subclass metadata")

        class HostileName(str):
            def __eq__(self, _other: object) -> bool:
                callbacks.append("name:eq")
                raise AssertionError("compared module-name lookalike")

            def __hash__(self) -> int:
                callbacks.append("name:hash")
                raise AssertionError("hashed module-name lookalike")

        self.fake_bpy.ops = HostileModule("bpy.ops")
        subclass_ops_result = self._lookup("bpy.ops.mesh.primitive_cube_add")
        self.fake_bpy.types = HostileModule("bpy.types")
        subclass_types_result = self._lookup("bpy.types.Object")

        self.fake_bpy.ops = types.ModuleType(HostileName("bpy.ops"))
        lookalike_ops_result = self._lookup("bpy.ops.mesh.primitive_cube_add")
        self.fake_bpy.types = types.ModuleType(HostileName("bpy.types"))
        lookalike_types_result = self._lookup("bpy.types.Object")

        for result in (
            subclass_ops_result,
            subclass_types_result,
            lookalike_ops_result,
            lookalike_types_result,
        ):
            self.assertFalse(result["found"])
            self.assertEqual(result["error"], "unsupported")
        self.assertEqual(callbacks, [])

    def test_rejects_malformed_and_disallowed_identifiers(self) -> None:
        invalid = (
            "",
            "bpy.",
            ".bpy",
            "bpy..types",
            "bpy.__dict__",
            "bpy.types.__class__",
            'bpy.types["Object"]',
            "bpy.types.Object()",
            " bpy.types.Object",
            "bpy.types.Object ",
            "bpy.types. Object",
            "os.path",
        )
        for identifier in invalid:
            with self.subTest(identifier=identifier):
                result = self._lookup(identifier)
                self.assertFalse(result["found"])
                self.assertEqual(result["identifier"], identifier)
                self.assertIn(
                    result["error"],
                    {"invalid_identifier", "disallowed_identifier"},
                )
                self.assertNotIn("traceback", result)

    def test_missing_safe_module_symbol_is_structured(self) -> None:
        result = self._lookup("bpy.does_not_exist")
        self.assertEqual(
            result,
            {
                "found": False,
                "identifier": "bpy.does_not_exist",
                "error": "not_found",
                "truncated": False,
            },
        )

    def test_large_error_is_deterministically_bounded(self) -> None:
        identifier = "bpy." + (chr(0x1F9CA) * 20000)
        first = self._lookup(identifier)
        second = self._lookup(identifier)
        encoded = json.dumps(first).encode("utf-8")

        self.assertEqual(first, second)
        self.assertFalse(first["found"])
        self.assertTrue(first["truncated"])
        self.assertLessEqual(len(encoded), _MAX_OUTPUT_BYTES)

    def test_huge_integer_dictionary_key_keeps_complete_bounded_success(self) -> None:
        huge_key = 10**20000

        def build_result() -> dict[str, Any]:
            truncated = [False]
            default = self.toolcode._json_value(
                {huge_key: {"nested": chr(0x1F9CA) * 20000}}, truncated
            )
            return cast(
                dict[str, Any],
                self.toolcode._bound_result(
                    {
                        "found": True,
                        "identifier": "bpy.types.Object.pathological",
                        "runtime": {"blender": "5.2.1", "python": "3.13.13"},
                        "kind": "rna_property",
                        "signature": "",
                        "description": "",
                        "name": "pathological",
                        "type": "POINTER",
                        "default": default,
                        "required": False,
                        "truncated": truncated[0],
                    }
                ),
            )

        first = build_result()
        second = build_result()
        encoded = json.dumps(first).encode("utf-8")

        self.assertEqual(first, second)
        self.assertTrue(first["found"])
        self.assertEqual(first["kind"], "rna_property")
        self.assertEqual(first["type"], "POINTER")
        self.assertIn("default", first)
        self.assertIn("<large-integer-key>", first["default"])
        self.assertTrue(first["truncated"])
        self.assertLessEqual(len(encoded), _MAX_OUTPUT_BYTES)

    def test_dictionary_keys_do_not_invoke_text_hooks_and_collisions_preserve_data(
        self,
    ) -> None:
        sentinel: list[str] = []
        first_key = _HostileText(sentinel, "first")
        second_key = _HostileText(sentinel, "second")
        truncated = [False]

        converted = self.toolcode._json_value(
            {first_key: "one", second_key: "two"}, truncated
        )

        self.assertEqual(
            converted,
            [
                {"key": "<unsupported-key>", "value": "one"},
                {"key": "<unsupported-key>", "value": "two"},
            ],
        )
        self.assertTrue(truncated[0])
        self.assertEqual(sentinel, [])

    def test_pathological_runtime_values_use_primitive_only_seam(self) -> None:
        huge_integer = 10**20000

        def build_result() -> dict[str, Any]:
            return cast(
                dict[str, Any],
                self.toolcode._build_result(
                    "bpy",
                    self.fake_bpy,
                    self.fake_bpy,
                    "module",
                    runtime_values=(
                        (huge_integer, -huge_integer, huge_integer),
                        chr(0x1F9CA) * 20000,
                        (huge_integer, huge_integer, huge_integer),
                    ),
                ),
            )

        first = build_result()
        second = build_result()
        encoded = json.dumps(first).encode("utf-8")

        self.assertEqual(first, second)
        self.assertTrue(first["found"])
        self.assertTrue(first["truncated"])
        self.assertIn("bit integer", first["runtime"]["blender"])
        self.assertIn("bit integer", first["runtime"]["python"])
        self.assertLessEqual(len(encoded), _MAX_OUTPUT_BYTES)

    def test_multibyte_runtime_version_is_bounded_through_data_seam(self) -> None:
        result = self.toolcode._build_result(
            "bpy",
            self.fake_bpy,
            self.fake_bpy,
            "module",
            runtime_values=(("invalid",), chr(0x1F9CA) * 20000, (3, 13, 13)),
        )
        encoded = json.dumps(result).encode("utf-8")

        self.assertTrue(result["found"])
        self.assertTrue(result["truncated"])
        self.assertTrue(result["runtime"]["blender"].endswith("..."))
        self.assertLessEqual(len(encoded), _MAX_OUTPUT_BYTES)

    def test_adversarial_non_ascii_metadata_uses_default_json_wire_bound(self) -> None:
        emoji = chr(0x1F9CA)

        def build_result() -> dict[str, Any]:
            return cast(
                dict[str, Any],
                self.toolcode._bound_result(
                    {
                        "found": True,
                        "identifier": "bpy.ops.mesh.synthetic",
                        "runtime": {"blender": "5.2.1", "python": "3.13.13"},
                        "kind": "operator",
                        "signature": emoji * 20000,
                        "description": ("résumé " + emoji) * 20000,
                        "parameters": [
                            {
                                "name": "参数{:02d}".format(index),
                                "type": "ENUM",
                                "description": emoji * 20000,
                                "enum_values": [emoji * 192] * 8,
                                "default": [emoji * 192] * 8,
                                "required": False,
                            }
                            for index in range(32)
                        ],
                        "truncated": False,
                    }
                ),
            )

        first = build_result()
        second = build_result()
        default_wire = json.dumps(first).encode("utf-8")
        wrapped_wire = json.dumps({"result": first}).encode("utf-8")

        self.assertEqual(first, second)
        self.assertTrue(first["found"])
        self.assertTrue(first["truncated"])
        self.assertLessEqual(len(default_wire), self.toolcode._MAX_OUTPUT_BYTES)
        self.assertLessEqual(len(wrapped_wire), _MAX_OUTPUT_BYTES)

    def test_production_main_keeps_useful_multibyte_description_bounded(self) -> None:
        self.fake_bpy.__doc__ = ("café " + chr(0x1F9CA) + " ") * 20000

        first = self._lookup("bpy")
        second = self._lookup("bpy")

        self.assertEqual(first, second)
        self.assertTrue(first["description"])
        self.assertIn("caf", first["description"])
        self.assertLessEqual(
            len(json.dumps(first).encode("utf-8")), self.toolcode._MAX_OUTPUT_BYTES
        )
        self.assertLessEqual(
            len(json.dumps({"result": first}).encode("utf-8")), _MAX_OUTPUT_BYTES
        )

    def test_unexpected_failures_are_distinct_from_metadata_unavailable(self) -> None:
        with mock.patch.object(
            self.toolcode.importlib, "import_module", side_effect=AssertionError("boom")
        ):
            import_result = self.toolcode.main({"identifier": "bpy"})
        with (
            mock.patch.object(
                self.toolcode, "_resolve", side_effect=AssertionError("boom")
            ),
            mock.patch.dict(sys.modules, {"bpy": self.fake_bpy}),
        ):
            metadata_result = self.toolcode.main({"identifier": "bpy"})

        self.assertEqual(import_result["error"], "internal_error")
        self.assertEqual(metadata_result["error"], "internal_error")
        self.assertNotIn("traceback", import_result)
        self.assertNotIn("traceback", metadata_result)

    def test_import_does_not_require_bpy(self) -> None:
        with mock.patch.dict(sys.modules, {"bpy": None}):
            module = _load_toolcode()
        self.assertTrue(callable(module.main))

    def test_static_safety_gates_forbid_dynamic_execution_and_low_level_import(
        self,
    ) -> None:
        with open(_TOOLCODE_PATH, encoding="utf-8") as handle:
            module = ast.parse(handle.read(), filename=_TOOLCODE_PATH)

        forbidden_calls = []
        imports_low_level_bpy = False
        inspect_signature_call = False
        inspect_getattr_static_call = False
        generic_properties_get = False
        passive_rna_access = False
        mro_membership = False
        mro_identity_trust = False
        mro_trust_forwarding = False
        module_subclass_check = False
        type_object_membership = False
        type_subclass_check = False
        for node in ast.walk(module):
            if isinstance(node, ast.Call):
                mro_trust_forwarding |= any(
                    keyword.arg
                    in {"trusted_heap_identities", "trusted_type_identities"}
                    and isinstance(keyword.value, ast.Name)
                    and keyword.value.id == "mro"
                    for keyword in node.keywords
                )
                if isinstance(node.func, ast.Name) and node.func.id in {
                    "eval",
                    "exec",
                    "compile",
                }:
                    forbidden_calls.append(node.func.id)
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "inspect"
                    and node.func.attr == "signature"
                ):
                    inspect_signature_call = True
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "inspect"
                    and node.func.attr == "getattr_static"
                ):
                    inspect_getattr_static_call = True
                if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "properties"
                    ):
                        generic_properties_get = True
            if isinstance(node, ast.Constant) and node.value == "_rna":
                passive_rna_access = True
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "_bpy"
            ):
                imports_low_level_bpy = True
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports_low_level_bpy |= any(
                    alias.name == "_bpy" or alias.name.startswith("_bpy.")
                    for alias in node.names
                )
            if isinstance(node, ast.Compare) and any(
                isinstance(operator, (ast.In, ast.NotIn)) for operator in node.ops
            ):
                compared_names = {
                    child.id for child in ast.walk(node) if isinstance(child, ast.Name)
                }
                mro_membership |= "mro" in compared_names
                type_object_membership |= isinstance(node.left, ast.Call) and (
                    isinstance(node.left.func, ast.Name) and node.left.func.id == "type"
                )
            if isinstance(node, ast.GeneratorExp):
                mro_identity_trust |= any(
                    isinstance(generator.iter, ast.Name)
                    and generator.iter.id == "mro"
                    for generator in node.generators
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance"
                and len(node.args) > 1
                and isinstance(node.args[1], ast.Attribute)
                and isinstance(node.args[1].value, ast.Name)
                and node.args[1].value.id == "types"
                and node.args[1].attr == "ModuleType"
            ):
                module_subclass_check = True
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance"
                and len(node.args) > 1
                and isinstance(node.args[1], ast.Name)
                and node.args[1].id == "type"
            ):
                type_subclass_check = True

        self.assertEqual(forbidden_calls, [])
        self.assertFalse(imports_low_level_bpy)
        self.assertFalse(inspect_signature_call)
        self.assertFalse(inspect_getattr_static_call)
        self.assertFalse(generic_properties_get)
        self.assertFalse(passive_rna_access)
        self.assertFalse(mro_membership)
        self.assertFalse(mro_identity_trust)
        self.assertFalse(mro_trust_forwarding)
        self.assertFalse(module_subclass_check)
        self.assertFalse(type_object_membership)
        self.assertFalse(type_subclass_check)


class TestRuntimePythonAPIDocsRealRuntimes(unittest.TestCase):
    _STANDALONE = os.environ.get("BLENDER_MCP_BPY_PYTHON", "")
    _BLENDER = os.environ.get("BLENDER_BIN", "")
    _IDENTIFIERS = (
        "bpy",
        "bpy.app.version_string",
        "bpy.ops.mesh.primitive_cube_add",
        "bpy.types.Object",
        "bpy.types.Object.location",
        "bpy.types.BakeSettings.pass_filter",
        "bpy.types.Image.source",
    )

    def _script(self, identifiers: tuple[str, ...]) -> str:
        return "\n".join(
            (
                "import importlib.util, json",
                f"spec = importlib.util.spec_from_file_location('runtime_docs', {_TOOLCODE_PATH!r})",
                "module = importlib.util.module_from_spec(spec)",
                "spec.loader.exec_module(module)",
                f"identifiers = {identifiers!r}",
                (
                    "pairs = [(module.main({'identifier': value}), "
                    "module.main({'identifier': value})) for value in identifiers]"
                ),
                (
                    "assert all(json.dumps(first, sort_keys=True) == "
                    "json.dumps(second, sort_keys=True) for first, second in pairs)"
                ),
                (
                    "print('__RUNTIME_DOCS__' + json.dumps("
                    "[first for first, _second in pairs], sort_keys=True))"
                ),
            )
        )

    def _run(
        self,
        executable: str,
        *,
        blender: bool,
        identifiers: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        script = self._script(identifiers or self._IDENTIFIERS)
        command = [executable]
        if blender:
            command.extend(
                ("--background", "--factory-startup", "--python-expr", script)
            )
        else:
            command.extend(("-c", script))
        completed = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=120
        )
        marker = "__RUNTIME_DOCS__"
        line = next(
            line for line in completed.stdout.splitlines() if line.startswith(marker)
        )
        return cast(list[dict[str, Any]], json.loads(line[len(marker) :]))

    def _run_operator_with_substituted_low_level_hook(
        self, executable: str, *, blender: bool
    ) -> tuple[dict[str, Any], list[object]]:
        script = "\n".join(
            (
                "import bpy, importlib.util, json, sys, types",
                "calls = []",
                "sentinel = types.ModuleType('_bpy')",
                "sentinel.ops = types.SimpleNamespace(get_rna_type=calls.append)",
                "sys.modules['_bpy'] = sentinel",
                f"spec = importlib.util.spec_from_file_location('runtime_docs', {_TOOLCODE_PATH!r})",
                "module = importlib.util.module_from_spec(spec)",
                "spec.loader.exec_module(module)",
                (
                    "result = module.main("
                    "{'identifier': 'bpy.ops.mesh.primitive_cube_add'})"
                ),
                "print('__RUNTIME_DOCS__' + json.dumps([result, calls], sort_keys=True))",
            )
        )
        command = [executable]
        if blender:
            command.extend(
                ("--background", "--factory-startup", "--python-expr", script)
            )
        else:
            command.extend(("-c", script))
        completed = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=120
        )
        marker = "__RUNTIME_DOCS__"
        line = next(
            line for line in completed.stdout.splitlines() if line.startswith(marker)
        )
        return cast(
            tuple[dict[str, Any], list[object]], json.loads(line[len(marker) :])
        )

    def _run_heap_rna_callback_probe(
        self, executable: str, *, blender: bool
    ) -> dict[str, Any]:
        script = "\n".join(
            (
                "import bpy, importlib.util, json",
                "calls = []",
                "class MaliciousRNA(bpy.types.Struct):",
                "    __module__ = 'bpy.types'",
                "    __slots__ = ()",
                "    def __getattribute__(self, name):",
                "        calls.append(name)",
                "        raise AssertionError('RNA callback invoked: ' + name)",
                "payload = {'capability': False, 'calls': calls}",
                "try:",
                "    malicious_rna = MaliciousRNA(bpy.types.Object.bl_rna)",
                "    class CallbackProbe(bpy.types.Object):",
                "        __module__ = 'bpy.types'",
                "        __slots__ = ()",
                "        bl_rna = malicious_rna",
                "    bpy.types.CallbackProbe = CallbackProbe",
                "    payload['capability'] = True",
                f"    spec = importlib.util.spec_from_file_location('runtime_docs', {_TOOLCODE_PATH!r})",
                "    module = importlib.util.module_from_spec(spec)",
                "    spec.loader.exec_module(module)",
                "    payload['heaptype'] = bool(type(malicious_rna).__flags__ & (1 << 9))",
                "    payload['result'] = module.main({'identifier': 'bpy.types.CallbackProbe'})",
                "except (TypeError, RuntimeError) as exc:",
                "    payload['construction_error'] = type(exc).__name__",
                "finally:",
                "    if 'CallbackProbe' in vars(bpy.types):",
                "        del bpy.types.CallbackProbe",
                "print('__RUNTIME_DOCS__' + json.dumps(payload, sort_keys=True))",
            )
        )
        command = [executable]
        if blender:
            command.extend(
                ("--background", "--factory-startup", "--python-expr", script)
            )
        else:
            command.extend(("-c", script))
        completed = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=120
        )
        marker = "__RUNTIME_DOCS__"
        line = next(
            line for line in completed.stdout.splitlines() if line.startswith(marker)
        )
        return cast(dict[str, Any], json.loads(line[len(marker) :]))

    def _assert_heap_rna_callback_probe(
        self, payload: dict[str, Any], runtime_name: str, *, required: bool
    ) -> None:
        status = (
            payload.get("result", {}).get("error", "unexpected_success")
            if payload["capability"]
            else payload.get("construction_error", "unsupported")
        )
        print(
            "runtime heap RNA probe: {:s} capability={!r} callbacks={:d} status={!r}".format(
                runtime_name, payload["capability"], len(payload["calls"]), status
            )
        )
        if not payload["capability"]:
            self.assertFalse(required, payload)
            self.assertEqual(payload["calls"], [])
            return
        self.assertTrue(payload["heaptype"])
        self.assertEqual(payload["calls"], [])
        self.assertFalse(payload["result"]["found"], payload)
        self.assertIn(payload["result"]["error"], {"not_found", "unsupported"})

    def _assert_runtime_versions(
        self, results: list[dict[str, Any]], runtime_name: str
    ) -> None:
        self.assertTrue(all(result["found"] for result in results), results)
        blender_versions = {result["runtime"]["blender"] for result in results}
        python_versions = {result["runtime"]["python"] for result in results}
        self.assertEqual(blender_versions, {"5.2.1"})
        self.assertEqual(len(python_versions), 1)
        python_version = next(iter(python_versions))
        self.assertEqual(
            tuple(int(value) for value in python_version.split(".")[:2]), (3, 13)
        )
        print(
            "runtime docs versions: {:s} blender=5.2.1 python={:s}".format(
                runtime_name, python_version
            )
        )

    def _assert_enum_fidelity(self, results: list[dict[str, Any]]) -> None:
        flag_enum = results[5]
        ordinary_enum = results[6]
        self.assertTrue(flag_enum["is_enum_flag"])
        self.assertEqual(
            flag_enum["default"],
            [
                "COLOR",
                "DIFFUSE",
                "DIRECT",
                "EMIT",
                "GLOSSY",
                "INDIRECT",
                "TRANSMISSION",
            ],
        )
        self.assertEqual(
            flag_enum["enum_values"],
            [
                "NONE",
                "EMIT",
                "DIRECT",
                "INDIRECT",
                "COLOR",
                "DIFFUSE",
                "GLOSSY",
                "TRANSMISSION",
            ],
        )
        self.assertEqual(ordinary_enum["default"], "FILE")
        self.assertNotIn("is_enum_flag", ordinary_enum)
        self.assertIsInstance(ordinary_enum["default"], str)

    def _assert_missing_operator_is_not_found(
        self, executable: str, *, blender: bool
    ) -> None:
        result = self._run(
            executable,
            blender=blender,
            identifiers=("bpy.ops.mesh.definitely_missing",),
        )[0]
        self.assertFalse(result["found"], result)
        self.assertEqual(result["error"], "not_found")

    @unittest.skipUnless(
        os.path.isfile(_STANDALONE), "standalone bpy runtime unavailable"
    )
    def test_standalone_bpy_representative_identifiers(self) -> None:
        results = self._run(self._STANDALONE, blender=False)
        self._assert_runtime_versions(results, "standalone")
        self.assertEqual(results[2]["kind"], "operator")
        self.assertEqual(results[3]["kind"], "rna_type")
        self.assertEqual(results[4]["kind"], "rna_property")
        self._assert_enum_fidelity(results)
        self._assert_missing_operator_is_not_found(self._STANDALONE, blender=False)
        self.assertTrue(
            all(
                len(json.dumps(result).encode("utf-8")) <= _MAX_OUTPUT_BYTES
                for result in results
            )
        )

    @unittest.skipUnless(
        os.path.isfile(_STANDALONE), "standalone bpy runtime unavailable"
    )
    def test_standalone_operator_ignores_substituted_low_level_hook(self) -> None:
        result, calls = self._run_operator_with_substituted_low_level_hook(
            self._STANDALONE, blender=False
        )
        self.assertTrue(result["found"], result)
        self.assertEqual(result["kind"], "operator")
        self.assertEqual(calls, [])

    @unittest.skipUnless(
        os.path.isfile(_STANDALONE), "standalone bpy runtime unavailable"
    )
    def test_standalone_rejects_heap_rna_callback_probe(self) -> None:
        payload = self._run_heap_rna_callback_probe(self._STANDALONE, blender=False)
        self._assert_heap_rna_callback_probe(payload, "standalone", required=False)

    @unittest.skipUnless(os.path.isfile(_BLENDER), "Blender runtime unavailable")
    def test_blender_representative_identifiers(self) -> None:
        results = self._run(self._BLENDER, blender=True)
        self._assert_runtime_versions(results, "blender")
        self.assertEqual(results[2]["kind"], "operator")
        self.assertEqual(results[3]["kind"], "rna_type")
        self.assertEqual(results[4]["kind"], "rna_property")
        self._assert_enum_fidelity(results)
        self._assert_missing_operator_is_not_found(self._BLENDER, blender=True)
        self.assertTrue(
            all(
                len(json.dumps(result).encode("utf-8")) <= _MAX_OUTPUT_BYTES
                for result in results
            )
        )

    @unittest.skipUnless(os.path.isfile(_BLENDER), "Blender runtime unavailable")
    def test_blender_operator_ignores_substituted_low_level_hook(self) -> None:
        result, calls = self._run_operator_with_substituted_low_level_hook(
            self._BLENDER, blender=True
        )
        self.assertTrue(result["found"], result)
        self.assertEqual(result["kind"], "operator")
        self.assertEqual(calls, [])

    @unittest.skipUnless(os.path.isfile(_BLENDER), "Blender runtime unavailable")
    def test_blender_rejects_heap_rna_callback_probe(self) -> None:
        payload = self._run_heap_rna_callback_probe(self._BLENDER, blender=True)
        self._assert_heap_rna_callback_probe(payload, "blender", required=True)

    @unittest.skipUnless(
        os.path.isfile(_STANDALONE) and os.path.isfile(_BLENDER),
        "both Blender runtimes are required for parity",
    )
    def test_normalized_docs_match_between_runtimes(self) -> None:
        standalone = self._run(self._STANDALONE, blender=False)
        blender = self._run(self._BLENDER, blender=True)
        self._assert_runtime_versions(standalone, "standalone")
        self._assert_runtime_versions(blender, "blender")
        normalized_standalone = copy.deepcopy(standalone)
        normalized_blender = copy.deepcopy(blender)
        for standalone_result, blender_result in zip(
            normalized_standalone, normalized_blender, strict=True
        ):
            standalone_result["runtime"]["python"] = "3.13.x"
            blender_result["runtime"]["python"] = "3.13.x"
        self.assertEqual(normalized_standalone, normalized_blender)


if __name__ == "__main__":
    unittest.main()
