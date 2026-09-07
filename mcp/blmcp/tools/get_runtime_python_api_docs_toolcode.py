# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compact, callback-safe runtime docs for Blender Python symbols."""

# Exact-type checks are security boundaries here: subclasses may add conversion
# or descriptor callbacks that an ordinary isinstance check would admit.
# pylint: disable=too-many-lines,unidiomatic-typecheck

from __future__ import annotations

__all__ = ("main",)

import importlib
import json
import math
import re
import sys
import types
from collections.abc import Iterable
from typing import Any, NamedTuple, cast

_MAX_OUTPUT_BYTES = 8000
_MAX_IDENTIFIER_CHARS = 256
_MAX_DESCRIPTION_CHARS = 768
_MAX_PARAMETER_DESCRIPTION_CHARS = 192
_MAX_PARAMETERS = 32
_MAX_ENUM_VALUES = 8
_MAX_STRING_CHARS = 192
_MAX_COLLECTION_ITEMS = 4
_MAX_SAFE_INTEGER_BITS = 4096
_MAX_SAFE_KEY_INTEGER_BITS = 256
_COMPONENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_MISSING = object()
# CPython's stable Py_TPFLAGS_HEAPTYPE bit. Blender's foundational runtime C
# types are static extension types; Python monkeypatches can only manufacture
# heap types with lookalike names.
_PY_TPFLAGS_HEAPTYPE = 1 << 9
_CO_VARARGS = 0x04
_CO_VARKEYWORDS = 0x08
_TRUSTED_BUILTIN_CALLABLE_TYPES = (
    types.BuiltinFunctionType,
    types.MethodDescriptorType,
    types.WrapperDescriptorType,
    types.MethodWrapperType,
    types.ClassMethodDescriptorType,
)
_TYPE_METADATA_NAMES = (
    "__dict__",
    "__flags__",
    "__module__",
    "__mro__",
    "__name__",
)
_RNA_REGISTRY_TYPE_NAMES = (
    "Struct",
    "Function",
    "Property",
    "BoolProperty",
    "IntProperty",
    "FloatProperty",
    "StringProperty",
    "EnumProperty",
    "PointerProperty",
    "CollectionProperty",
    "EnumPropertyItem",
)


# Blender 5.2.1's genuine RNA wrapper types themselves carry
# Py_TPFLAGS_HEAPTYPE. They are therefore trusted only as exact identities
# returned by bpy.types' built-in C registry getter; no metadata from a heap
# type is consulted to establish that trust.
class _RNAProvenance(NamedTuple):
    """Exact C identities required before any RNA field access."""

    rna_types: tuple[type, ...]
    collection_type: type
    types_getter: object
    operator_rna_type: type


def _clip_text(  # pylint: disable=unidiomatic-typecheck
    value: object, limit: int, truncated: list[bool]
) -> str:
    """Convert only exact primitive values to bounded text."""
    if type(value) is str:
        text = value
    elif type(value) is bool:
        text = "True" if value else "False"
    elif type(value) is int:
        integer = value
        bits = int.bit_length(integer)
        if bits > _MAX_SAFE_INTEGER_BITS:
            truncated[0] = True
            sign = "negative " if integer < 0 else ""
            return "<" + sign + str(bits) + "-bit integer>"
        text = str(integer)
    elif type(value) is float:
        text = str(value)
    else:
        truncated[0] = True
        return "<unsupported>"
    text = " ".join(text.split())
    if len(text) > limit:
        truncated[0] = True
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _exact_mapping_value(
    mapping: object, name: str, default: object = _MISSING
) -> object:
    """Scan an exact dict or mappingproxy without hashing an untrusted key."""
    if type(name) is not str:
        return default
    if type(mapping) is dict:
        items = dict.items(cast(dict[object, object], mapping))
    elif type(mapping) is types.MappingProxyType:
        items = cast(Any, mapping).items()
    else:
        return default
    for key, item in items:
        if type(key) is str and key == name:
            return item
    return default


def _module_namespace(value: object) -> object:
    """Return an exact module dict through ModuleType's trusted base path."""
    if type(value) is not types.ModuleType:
        return _MISSING
    try:
        namespace = types.ModuleType.__getattribute__(value, "__dict__")
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return _MISSING
    return namespace if type(namespace) is dict else _MISSING


def _module_value(
    value: object, name: str, default: object = _MISSING
) -> object:
    namespace = _module_namespace(value)
    if namespace is _MISSING:
        return default
    return _exact_mapping_value(namespace, name, default)


def _type_field(
    value: object,
    name: str,
    default: object = _MISSING,
    *,
    trusted_type_identities: tuple[type, ...] = (),
) -> object:
    """Read metadata only from an exact type or a pre-proven C identity."""
    if type(name) is not str or type(trusted_type_identities) is not tuple:
        return default
    if not any(name == expected for expected in _TYPE_METADATA_NAMES):
        return default
    trusted_identity = any(
        value is identity for identity in trusted_type_identities
    )
    if type(value) is not type and not trusted_identity:
        return default
    descriptor = _exact_mapping_value(type.__dict__, name)
    if descriptor is _MISSING:
        return default
    try:
        # Bind the exact type-owned descriptor directly so a custom metaclass
        # cannot substitute a property for this fixed metadata field.
        return cast(Any, descriptor).__get__(  # pylint: disable=unnecessary-dunder-call
            cast(type, value), type(value)
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return default


def _object_field(value: object, name: str, default: object = _MISSING) -> object:
    """Read C-owned object metadata without instance dispatch."""
    try:
        return object.__getattribute__(value, name)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return default


def _module_name(value: object) -> object:
    """Return an exact module name without subclass or instance dispatch."""
    name = _module_value(value, "__name__")
    return name if type(name) is str else _MISSING


def _is_exact_module(value: object, name: str) -> bool:
    if type(name) is not str:
        return False
    module_name = _module_name(value)
    return type(module_name) is str and module_name == name


def _is_trusted_builtin(value: object) -> bool:
    value_type = type(value)
    return any(value_type is expected for expected in _TRUSTED_BUILTIN_CALLABLE_TYPES)


def _is_static_c_type(value: object, module: str, name: str) -> bool:
    if type(module) is not str or type(name) is not str:
        return False
    if type(value) is not type:
        return False
    flags = _type_field(value, "__flags__")
    if type(flags) is not int or flags & _PY_TPFLAGS_HEAPTYPE:
        return False
    type_module = _type_field(value, "__module__")
    type_name = _type_field(value, "__name__")
    return (
        type(type_module) is str
        and type_module == module
        and type(type_name) is str
        and type_name == name
    )


def _is_nonheap_type(value: object) -> bool:
    if type(value) is not type:
        return False
    flags = _type_field(value, "__flags__")
    return type(flags) is int and not flags & _PY_TPFLAGS_HEAPTYPE


def _type_namespace_value(  # pylint: disable=too-many-return-statements
    value: object,
    name: str,
    default: object = _MISSING,
    *,
    trusted_heap_identities: tuple[type, ...] = (),
) -> object:
    """Scan a proven C type's MRO dictionaries without key lookup."""
    if type(name) is not str or type(trusted_heap_identities) is not tuple:
        return default
    for identity in trusted_heap_identities:
        identity_flags = _type_field(
            identity,
            "__flags__",
            trusted_type_identities=trusted_heap_identities,
        )
        if type(identity_flags) is not int:
            return default
    trusted_heap = any(value is identity for identity in trusted_heap_identities)
    if type(value) is not type and not trusted_heap:
        return default
    proven_type = cast(type, value)
    proven_identities = trusted_heap_identities if trusted_heap else ()
    flags = _type_field(
        proven_type,
        "__flags__",
        trusted_type_identities=proven_identities,
    )
    if type(flags) is not int:
        return default
    if flags & _PY_TPFLAGS_HEAPTYPE and not trusted_heap:
        return default
    mro = _type_field(
        proven_type,
        "__mro__",
        trusted_type_identities=proven_identities,
    )
    if type(mro) is not tuple:
        return default
    for base in mro:
        trusted_base = any(
            base is identity for identity in trusted_heap_identities
        )
        if type(base) is not type and not trusted_base:
            return default
        namespace = _type_field(
            base,
            "__dict__",
            trusted_type_identities=(trusted_heap_identities if trusted_base else ()),
        )
        if type(namespace) is not types.MappingProxyType:
            return default
        item = _exact_mapping_value(namespace, name)
        if item is not _MISSING:
            return item
    return default


def _is_rna(value: object, provenance: _RNAProvenance) -> bool:
    value_type = type(value)
    identities = provenance.rna_types
    if type(identities) is not tuple:
        return False
    return any(value_type is expected for expected in identities)


def _rna_field(
    value: object,
    name: str,
    provenance: _RNAProvenance,
    default: object = _MISSING,
) -> object:
    """Read a fixed C RNA field only after exact provenance validation."""
    if type(name) is not str or not _is_rna(value, provenance):
        return default
    try:
        return getattr(value, name)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return default


def _rna_collection(owner: object, name: str, provenance: _RNAProvenance) -> object:
    collection = _rna_field(owner, name, provenance)
    if collection is _MISSING or type(collection) is not provenance.collection_type:
        return _MISSING
    return collection


def _provenance_from_rna(  # pylint: disable=too-many-return-statements
    rna: object, types_getter: object = _MISSING
) -> object:
    """Establish exact RNA and collection identities from C-returned metadata."""
    if type(types_getter) is not types.BuiltinFunctionType:
        return _MISSING
    identities: list[type] = []
    try:
        for name in _RNA_REGISTRY_TYPE_NAMES:
            candidate = cast(Any, types_getter)(name)
            if any(candidate is prior for prior in identities):
                return _MISSING
            identities.append(cast(type, candidate))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return _MISSING
    operator_rna_type = type(rna)
    # ``rna`` was returned by the validated C operator metadata descriptor, so
    # its exact type identity is provenance before any field is read from it.
    identity_tuple = (operator_rna_type, *identities)
    provisional = _RNAProvenance(
        identity_tuple, object, types_getter, operator_rna_type
    )
    properties = _rna_field(rna, "properties", provisional)
    if properties is _MISSING:
        return _MISSING
    collection_type = type(properties)
    if not _is_static_c_type(collection_type, "builtins", "bpy_prop_collection"):
        return _MISSING
    return _RNAProvenance(
        identity_tuple, collection_type, types_getter, operator_rna_type
    )


def _safe_key(value: object, truncated: list[bool]) -> str:
    """Bound a dictionary key before any textual conversion."""
    if type(value) is str:
        return _clip_text(value, _MAX_STRING_CHARS, truncated)
    if type(value) is int:
        integer = value
        if int.bit_length(integer) > _MAX_SAFE_KEY_INTEGER_BITS:
            truncated[0] = True
            return "<large-integer-key>"
        return str(integer)
    truncated[0] = True
    return "<unsupported-key>"


def _json_value(  # pylint: disable=too-many-return-statements,unidiomatic-typecheck
    value: object, truncated: list[bool], depth: int = 0
) -> Any:
    """Convert metadata defaults without invoking user conversion callbacks."""
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if int.bit_length(value) > _MAX_SAFE_INTEGER_BITS:
            return _clip_text(value, _MAX_STRING_CHARS, truncated)
        return value
    if type(value) is float:
        number = value
        return number if math.isfinite(number) else str(number)
    if type(value) is str:
        return _clip_text(value, _MAX_STRING_CHARS, truncated)
    if depth >= 2:
        truncated[0] = True
        return "<truncated>"
    if type(value) is list or type(value) is tuple:
        sequence = value
        if len(sequence) > _MAX_COLLECTION_ITEMS:
            truncated[0] = True
        return [
            _json_value(item, truncated, depth + 1)
            for item in sequence[:_MAX_COLLECTION_ITEMS]
        ]
    if type(value) is dict:
        converted: list[tuple[str, Any]] = []
        source = cast(dict[object, object], value)
        for index, (key, item) in enumerate(source.items()):
            if index >= _MAX_COLLECTION_ITEMS:
                truncated[0] = True
                break
            converted.append(
                (_safe_key(key, truncated), _json_value(item, truncated, depth + 1))
            )
        names = [key for key, _item in converted]
        if len(names) != len(set(names)):
            # A list preserves every value when bounded placeholders collide.
            return [{"key": key, "value": item} for key, item in converted]
        converted.sort(key=lambda pair: pair[0])
        return dict(converted)
    truncated[0] = True
    return "<unsupported>"


def _safe_literal(  # pylint: disable=too-many-return-statements
    value: object, truncated: list[bool], depth: int = 0
) -> str:
    """Format an allowlisted signature value without arbitrary repr/str hooks."""
    if value is None:
        return "None"
    if type(value) is bool:
        return "True" if value else "False"
    if type(value) is int:
        integer = value
        if int.bit_length(integer) > _MAX_SAFE_INTEGER_BITS:
            truncated[0] = True
            return "<large-integer>"
        return str(integer)
    if type(value) is float:
        return repr(value)
    if type(value) is str:
        return repr(_clip_text(value, 96, truncated))
    if depth >= 2:
        truncated[0] = True
        return "<truncated>"
    if type(value) is list or type(value) is tuple:
        sequence = cast(list[object] | tuple[object, ...], value)
        if len(sequence) > _MAX_COLLECTION_ITEMS:
            truncated[0] = True
        parts = [
            _safe_literal(item, truncated, depth + 1)
            for item in sequence[:_MAX_COLLECTION_ITEMS]
        ]
        if len(sequence) > _MAX_COLLECTION_ITEMS:
            parts.append("...")
        if type(value) is tuple:
            return "(" + ", ".join(parts) + ("," if len(parts) == 1 else "") + ")"
        return "[" + ", ".join(parts) + "]"
    truncated[0] = True
    return "<unsupported>"


def _parameter_name(value: object, truncated: list[bool]) -> str:
    """Return only an exact code-object parameter name."""
    if type(value) is str:
        return value
    truncated[0] = True
    return "<unsupported>"


def _keyword_defaults(value: object) -> dict[str, object]:
    """Copy exact-string keys without hashing or comparing hostile keys."""
    if type(value) is not dict:
        return {}
    result: dict[str, object] = {}
    for key, default in dict.items(cast(dict[object, object], value)):
        if type(key) is str:
            result[key] = default
    return result


def _python_function_signature(value: object, truncated: list[bool]) -> str:
    """Format an exact Python function solely from code/default metadata."""
    if type(value) is not types.FunctionType:
        return ""
    function = value
    code = function.__code__
    positional_count = code.co_argcount
    positional_only_count = code.co_posonlyargcount
    keyword_only_count = code.co_kwonlyargcount
    varnames = code.co_varnames
    defaults = function.__defaults__
    if type(defaults) is tuple and len(defaults) <= positional_count:
        positional_defaults = defaults
    else:
        positional_defaults = ()
        if defaults is not None:
            truncated[0] = True
    first_default = positional_count - len(positional_defaults)
    keyword_defaults = _keyword_defaults(function.__kwdefaults__)
    pieces: list[str] = []

    for index in range(positional_count):
        part = _parameter_name(varnames[index], truncated)
        if index >= first_default:
            part += "=" + _safe_literal(
                positional_defaults[index - first_default], truncated
            )
        pieces.append(part)
        if index + 1 == positional_only_count:
            pieces.append("/")

    keyword_start = positional_count
    vararg_index = keyword_start + keyword_only_count
    has_varargs = bool(code.co_flags & _CO_VARARGS)
    if has_varargs:
        pieces.append("*" + _parameter_name(varnames[vararg_index], truncated))
        varkw_index = vararg_index + 1
    else:
        varkw_index = vararg_index
        if keyword_only_count:
            pieces.append("*")

    for index in range(keyword_start, keyword_start + keyword_only_count):
        name = _parameter_name(varnames[index], truncated)
        part = name
        if name in keyword_defaults:
            part += "=" + _safe_literal(keyword_defaults[name], truncated)
        pieces.append(part)

    if code.co_flags & _CO_VARKEYWORDS:
        pieces.append("**" + _parameter_name(varnames[varkw_index], truncated))
    return _clip_text("(" + ", ".join(pieces) + ")", 384, truncated)


def _builtin_function_signature(value: object, truncated: list[bool]) -> str:
    """Use only C-owned text signatures from exact built-in callable types."""
    if not _is_trusted_builtin(value):
        return ""
    try:
        text_signature = object.__getattribute__(value, "__text_signature__")
    except (AttributeError, RuntimeError, TypeError):
        return "(...)"
    if (
        type(text_signature) is not str
        or not text_signature.startswith("(")
        or not text_signature.endswith(")")
    ):
        return "(...)"
    inner = text_signature[1:-1].strip()
    if inner.startswith(("$module,", "$self,", "$type,")):
        inner = inner.partition(",")[2].strip()
        if inner == "/":
            inner = ""
        elif inner.startswith("/,"):
            inner = inner[2:].strip()
    return _clip_text("(" + inner + ")", 384, truncated)


def _function_signature(value: object, truncated: list[bool]) -> str:
    """Build a signature without consulting callable-controlled metadata."""
    if type(value) is types.FunctionType:
        return _python_function_signature(value, truncated)
    return _builtin_function_signature(value, truncated)


def _property_info(  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
    prop: object, provenance: _RNAProvenance, truncated: list[bool]
) -> dict[str, Any]:
    identifier = _rna_field(prop, "identifier", provenance, "")
    result: dict[str, Any] = {
        "name": _clip_text(identifier, _MAX_STRING_CHARS, truncated),
        "type": _clip_text(
            _rna_field(prop, "type", provenance, "UNKNOWN"),
            _MAX_STRING_CHARS,
            truncated,
        ),
    }
    array_length = _rna_field(prop, "array_length", provenance, 0)
    is_enum_flag = (
        result["type"] == "ENUM"
        and _rna_field(prop, "is_enum_flag", provenance, False) is True
    )
    default = _rna_field(
        prop, "default_flag" if is_enum_flag else "default", provenance
    )
    if type(array_length) is int and array_length:
        default_array = _rna_field(prop, "default_array", provenance)
        if default_array is not _MISSING:
            array_type = type(default_array)
            if _is_static_c_type(array_type, "builtins", "bpy_prop_array"):
                try:
                    item_count = min(array_length, _MAX_COLLECTION_ITEMS)
                    default = [
                        default_array[index]  # type: ignore[index]
                        for index in range(item_count)
                    ]
                    if array_length > item_count:
                        truncated[0] = True
                except (IndexError, ReferenceError, RuntimeError, TypeError):
                    default = _MISSING
            else:
                default = _MISSING
                truncated[0] = True
    if is_enum_flag:
        result["is_enum_flag"] = True
        if type(default) is set or type(default) is frozenset:
            flag_set = cast(set[object] | frozenset[object], default)
            default_values = [value for value in flag_set if type(value) is str]
            if len(default_values) != len(flag_set):
                truncated[0] = True
            default_values.sort()
            if len(default_values) > _MAX_ENUM_VALUES:
                default_values = default_values[:_MAX_ENUM_VALUES]
                truncated[0] = True
            result["default"] = [
                _clip_text(value, 96, truncated) for value in default_values
            ]
        elif default is not _MISSING:
            truncated[0] = True
    elif default is not _MISSING:
        result["default"] = _json_value(default, truncated)
    result["required"] = _rna_field(prop, "is_required", provenance, False) is True
    description = _clip_text(
        _rna_field(prop, "description", provenance, ""),
        _MAX_PARAMETER_DESCRIPTION_CHARS,
        truncated,
    )
    if description:
        result["description"] = description
    if type(array_length) is int and array_length:
        result["array_length"] = array_length
    if _rna_field(prop, "is_readonly", provenance, False) is True:
        result["readonly"] = True
    if result["type"] == "ENUM":
        enum_items = _rna_collection(prop, "enum_items", provenance)
        values: list[str] = []
        if enum_items is not _MISSING:
            try:
                for index, item in enumerate(cast(Iterable[Any], enum_items)):
                    if index >= _MAX_ENUM_VALUES:
                        truncated[0] = True
                        break
                    if not _is_rna(item, provenance):
                        truncated[0] = True
                        values = []
                        break
                    values.append(
                        _clip_text(
                            _rna_field(item, "identifier", provenance, ""),
                            96,
                            truncated,
                        )
                    )
            except (ReferenceError, RuntimeError, TypeError):
                truncated[0] = True
                values = []
        if values:
            result["enum_values"] = values
    return result


def _rna_parameters(
    rna: object, provenance: _RNAProvenance, truncated: list[bool]
) -> list[dict[str, Any]]:
    properties = _rna_collection(rna, "properties", provenance)
    if properties is _MISSING:
        return []
    result: list[dict[str, Any]] = []
    try:
        for prop in cast(Iterable[Any], properties):
            if not _is_rna(prop, provenance):
                truncated[0] = True
                return []
            identifier = _rna_field(prop, "identifier", provenance, "")
            if type(identifier) is str and identifier == "rna_type":
                continue
            if len(result) >= _MAX_PARAMETERS:
                truncated[0] = True
                break
            result.append(_property_info(prop, provenance, truncated))
    except (ReferenceError, RuntimeError, TypeError):
        truncated[0] = True
        return []
    return result


def _rna_property(rna: object, name: str, provenance: _RNAProvenance) -> object:
    """Find a property only by iterating the exact C collection type."""
    properties = _rna_collection(rna, "properties", provenance)
    if properties is _MISSING:
        return _MISSING
    try:
        for prop in cast(Iterable[Any], properties):
            if not _is_rna(prop, provenance):
                return _MISSING
            identifier = _rna_field(prop, "identifier", provenance, "")
            if type(identifier) is str and identifier == name:
                return prop
    except (ReferenceError, RuntimeError, TypeError):
        return _MISSING
    return _MISSING


def _validate_identifier(identifier: object) -> tuple[list[str] | None, str | None]:
    if type(identifier) is not str or not identifier:
        return None, "invalid_identifier"
    if len(identifier) > _MAX_IDENTIFIER_CHARS or any(
        char.isspace() for char in identifier
    ):
        return None, "invalid_identifier"
    components = identifier.split(".")
    if any(
        not component or not _COMPONENT_RE.fullmatch(component)
        for component in components
    ):
        return None, "invalid_identifier"
    if components[0] != "bpy":
        return None, "disallowed_identifier"
    if any(
        component.startswith("__") and component.endswith("__")
        for component in components
    ):
        return None, "disallowed_identifier"
    return components, None


def _static_path(root: object, components: list[str]) -> tuple[object, str]:
    current = root
    for index, component in enumerate(components):
        owner = current
        if type(current) is types.ModuleType:
            value = _module_value(current, component)
        elif _is_nonheap_type(current):
            value = _type_namespace_value(current, component)
        else:
            return _MISSING, "unsupported"
        if value is _MISSING:
            return _MISSING, "not_found"
        if index == len(components) - 1 and type(owner) is not types.ModuleType:
            return _MISSING, "unsupported"
        current = value
    if type(current) is types.ModuleType:
        kind = "module"
    elif type(current) is types.FunctionType or _is_trusted_builtin(current):
        kind = "function"
    elif any(
        type(current) is expected for expected in (type(None), bool, int, float, str)
    ) or _is_nonheap_type(current):
        kind = "attribute"
    else:
        return _MISSING, "unsupported"
    return current, kind


def _trusted_types_getter(  # pylint: disable=too-many-return-statements
    types_namespace: object,
) -> object:
    """Return bpy.types' exact built-in registry getter, never a module value."""
    if not _is_exact_module(types_namespace, "bpy.types"):
        return _MISSING
    namespace_getter = _module_value(types_namespace, "__getattr__")
    if type(namespace_getter) is not types.BuiltinFunctionType:
        return _MISSING
    getter_name = _object_field(namespace_getter, "__name__")
    if type(getter_name) is not str or getter_name != "__getattr__":
        return _MISSING
    getter_module = _object_field(namespace_getter, "__module__")
    if type(getter_module) is not str or getter_module != "bpy.types":
        return _MISSING
    receiver = _object_field(namespace_getter, "__self__")
    if receiver is not types_namespace:
        return _MISSING
    return namespace_getter


def _resolve_rna_type(types_namespace: object, name: str) -> object:
    """Resolve only through Blender's C registry, ignoring injected globals."""
    if type(name) is not str:
        return _MISSING
    namespace_getter = _trusted_types_getter(types_namespace)
    if namespace_getter is _MISSING:
        return _MISSING
    try:
        return cast(Any, namespace_getter)(name)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return _MISSING


def _type_rna(value: object, provenance: _RNAProvenance) -> object:
    identities = provenance.rna_types
    if type(identities) is not tuple:
        return _MISSING
    trusted_identity = any(value is identity for identity in identities)
    if type(value) is not type and not trusted_identity:
        return _MISSING
    rna = _type_namespace_value(
        value,
        "bl_rna",
        trusted_heap_identities=identities if trusted_identity else (),
    )
    if rna is _MISSING or not _is_rna(rna, provenance):
        return _MISSING
    return rna


def _trusted_operator_create_function(  # pylint: disable=too-many-return-statements
    ops_namespace: object,
) -> object:
    """Return Blender's cached C wrapper factory, never an imported hook."""
    create_function = _module_value(ops_namespace, "_op_create_function")
    if type(create_function) is not types.BuiltinFunctionType:
        return _MISSING
    function_name = _object_field(create_function, "__name__")
    if type(function_name) is not str or function_name != "create_function":
        return _MISSING
    function_module = _object_field(create_function, "__module__")
    if type(function_module) is not str or function_module != "_bpy.ops":
        return _MISSING
    receiver = _object_field(create_function, "__self__")
    if not _is_exact_module(receiver, "_bpy.ops"):
        return _MISSING
    if _module_value(receiver, "create_function") is not create_function:
        return _MISSING
    return create_function


def _trusted_ops_factory(bpy: object) -> object:
    ops_namespace = _module_value(bpy, "ops")
    if not _is_exact_module(ops_namespace, "bpy.ops"):
        return _MISSING
    return _trusted_operator_create_function(ops_namespace)


def _operator_rna_from_wrapper(  # pylint: disable=too-many-return-statements
    wrapper: object,
) -> tuple[object, str]:
    """Call only the exact method descriptor of Blender's static C wrapper type."""
    wrapper_type = type(wrapper)
    if not _is_static_c_type(wrapper_type, "builtins", "BPyOpFunction"):
        return _MISSING, "unsupported"
    method = _type_namespace_value(wrapper_type, "get_rna_type")
    if type(method) is not types.MethodDescriptorType:
        return _MISSING, "unsupported"
    method_name = _object_field(method, "__name__")
    if type(method_name) is not str or method_name != "get_rna_type":
        return _MISSING, "unsupported"
    if _object_field(method, "__objclass__") is not wrapper_type:
        return _MISSING, "unsupported"
    try:
        bound_method = cast(Any, method).__get__(  # pylint: disable=unnecessary-dunder-call
            wrapper, wrapper_type
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return _MISSING, "metadata_unavailable"
    if type(bound_method) is not types.BuiltinFunctionType:
        return _MISSING, "unsupported"
    bound_name = _object_field(bound_method, "__name__")
    if type(bound_name) is not str or bound_name != "get_rna_type":
        return _MISSING, "unsupported"
    if _object_field(bound_method, "__self__") is not wrapper:
        return _MISSING, "unsupported"
    try:
        return cast(Any, bound_method)(), "operator"
    except KeyError:
        return _MISSING, "not_found"
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return _MISSING, "metadata_unavailable"


def _operator_metadata(
    bpy: object, category: str, operator: str
) -> tuple[object, object, str]:
    create_function = _trusted_ops_factory(bpy)
    if create_function is _MISSING:
        return _MISSING, _MISSING, "unsupported"
    types_namespace = _module_value(bpy, "types")
    types_getter = _trusted_types_getter(types_namespace)
    if types_getter is _MISSING:
        return _MISSING, _MISSING, "unsupported"
    try:
        wrapper = cast(Any, create_function)(category, operator)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return _MISSING, _MISSING, "metadata_unavailable"
    rna, status = _operator_rna_from_wrapper(wrapper)
    if rna is _MISSING or rna is None:
        return _MISSING, _MISSING, status
    provenance = _provenance_from_rna(rna, types_getter)
    if provenance is _MISSING:
        return _MISSING, _MISSING, "metadata_unavailable"
    return rna, provenance, "operator"


def _runtime_rna_provenance(bpy: object) -> object:
    _rna, provenance, _status = _operator_metadata(bpy, "wm", "redraw_timer")
    return provenance


def _resolve_operator(components: list[str], bpy: object) -> tuple[object, str, object]:
    if len(components) != 4:
        return _MISSING, "unsupported", _MISSING
    rna, provenance, status = _operator_metadata(bpy, components[2], components[3])
    if rna is _MISSING:
        return _MISSING, status, _MISSING
    return rna, "operator", provenance


def _resolve_types(  # pylint: disable=too-many-return-statements
    bpy: object, components: list[str]
) -> tuple[object, str, object]:
    if len(components) not in (3, 4):
        return _MISSING, "unsupported", _MISSING
    provenance = _runtime_rna_provenance(bpy)
    if provenance is _MISSING:
        return _MISSING, "unsupported", _MISSING
    types_namespace = _module_value(bpy, "types")
    if not _is_exact_module(types_namespace, "bpy.types"):
        return _MISSING, "unsupported", _MISSING
    rna_type = _resolve_rna_type(types_namespace, components[2])
    if rna_type is _MISSING:
        return _MISSING, "not_found", _MISSING
    typed_provenance = cast(_RNAProvenance, provenance)
    if not any(rna_type is identity for identity in typed_provenance.rna_types):
        typed_provenance = typed_provenance._replace(
            rna_types=(*typed_provenance.rna_types, cast(type, rna_type))
        )
    rna = _type_rna(rna_type, typed_provenance)
    if rna is _MISSING:
        return _MISSING, "unsupported", _MISSING
    if len(components) == 3:
        return rna_type, "rna_type", typed_provenance
    prop = _rna_property(rna, components[3], typed_provenance)
    if prop is _MISSING:
        return _MISSING, "not_found", _MISSING
    return prop, "rna_property", typed_provenance


def _trusted_app(bpy: object) -> object:
    app = _module_value(bpy, "app")
    if app is _MISSING:
        return _MISSING
    app_type = type(app)
    if not _is_static_c_type(app_type, "bpy", "app"):
        return _MISSING
    return app


def _app_value(  # pylint: disable=too-many-return-statements
    bpy: object, name: str, default: object = _MISSING
) -> object:
    """Invoke only a fixed member descriptor on Blender's exact C app type."""
    if type(name) is not str or name not in {"version", "version_string"}:
        return default
    app = _trusted_app(bpy)
    if app is _MISSING:
        return default
    app_type = type(app)
    descriptor = _type_namespace_value(app_type, name)
    if type(descriptor) is not types.MemberDescriptorType:
        return default
    descriptor_name = _object_field(descriptor, "__name__")
    if type(descriptor_name) is not str or descriptor_name != name:
        return default
    if _object_field(descriptor, "__objclass__") is not app_type:
        return default
    try:
        return cast(Any, descriptor).__get__(  # pylint: disable=unnecessary-dunder-call
            app, app_type
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return default


def _resolve(bpy: object, components: list[str]) -> tuple[object, str, object]:
    if len(components) == 1:
        return bpy, "module", _MISSING
    if components == ["bpy", "app", "version_string"]:
        value = _app_value(bpy, "version_string")
        status = "attribute" if value is not _MISSING else "unsupported"
        return value, status, _MISSING
    if components[1] == "types":
        return _resolve_types(bpy, components)
    if components[1] == "ops":
        return _resolve_operator(components, bpy)
    if components[1] in {"context", "data"} and len(components) > 2:
        return _MISSING, "unsupported", _MISSING
    value, kind = _static_path(bpy, components[1:])
    return value, kind, _MISSING


def _doc_description(value: object, truncated: list[bool]) -> str:
    if type(value) is types.ModuleType:
        doc = _module_value(value, "__doc__", "")
    elif type(value) is types.FunctionType or _is_trusted_builtin(value):
        doc = _object_field(value, "__doc__", "")
    elif _is_nonheap_type(value):
        doc = _type_namespace_value(value, "__doc__", "")
    else:
        doc = ""
    if type(doc) is not str:
        return ""
    lines = [line.strip() for line in doc.splitlines() if line.strip()]
    for line in lines:
        if line.startswith((":param ", ":type ", ":return", ":rtype")):
            continue
        if line.startswith("bpy.") and "(" in line:
            continue
        return _clip_text(line, _MAX_DESCRIPTION_CHARS, truncated)
    return ""


def _runtime_info_from_values(
    version: object,
    version_string: object,
    python_version: object,
    truncated: list[bool],
) -> dict[str, str]:
    """Explicit primitive-only seam for runtime metadata formatting tests."""
    if type(version) is tuple and all(
        type(item) is int for item in cast(tuple[object, ...], version)[:3]
    ):
        blender = ".".join(
            _clip_text(item, 64, truncated)
            for item in cast(tuple[object, ...], version)[:3]
        )
    else:
        blender = _clip_text(version_string, 64, truncated).split(" ", 1)[0]
    python_components: list[str] = []
    if type(python_version) is tuple:
        for item in cast(tuple[object, ...], python_version)[:3]:
            if type(item) is not int:
                python_components = []
                break
            python_components.append(_clip_text(item, 64, truncated))
    if not python_components:
        python_components = ["unknown"]
    return {
        "blender": _clip_json_string(blender, 256),
        "python": _clip_json_string(".".join(python_components), 256),
    }


def _runtime_info(bpy: object, truncated: list[bool]) -> dict[str, str]:
    version_info = sys.version_info
    try:
        python_version = tuple(version_info[:3])
    except (IndexError, RuntimeError, TypeError):
        python_version = ()
        truncated[0] = True
    return _runtime_info_from_values(
        _app_value(bpy, "version"),
        _app_value(bpy, "version_string", "unknown"),
        python_version,
        truncated,
    )


def _operator_result(
    identifier: str,
    rna: object,
    provenance: _RNAProvenance,
    result: dict[str, Any],
    truncated: list[bool],
) -> None:
    parameters = _rna_parameters(rna, provenance, truncated)
    result["description"] = _clip_text(
        _rna_field(rna, "description", provenance, ""),
        _MAX_DESCRIPTION_CHARS,
        truncated,
    )
    result["parameters"] = parameters
    parts = []
    for parameter in parameters:
        name = parameter["name"]
        if parameter.get("required") or "default" not in parameter:
            parts.append(name)
        else:
            parts.append(name + "=" + _safe_literal(parameter["default"], truncated))
    if truncated[0] and len(parameters) >= _MAX_PARAMETERS:
        parts.append("...")
    result["signature"] = identifier + "(" + ", ".join(parts) + ")"


def _build_result(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    identifier: str,
    bpy: object,
    value: object,
    kind: str,
    provenance: object = _MISSING,
    runtime_values: tuple[object, object, object] | None = None,
) -> dict[str, Any]:
    truncated = [False]
    runtime = (
        _runtime_info_from_values(*runtime_values, truncated)
        if runtime_values is not None
        else _runtime_info(bpy, truncated)
    )
    result: dict[str, Any] = {
        "found": True,
        "identifier": identifier,
        "runtime": runtime,
        "kind": kind,
        "signature": "",
        "description": "",
    }
    if kind in {"operator", "rna_type", "rna_property"} and provenance is _MISSING:
        raise ValueError("RNA provenance required")
    if kind == "operator":
        _operator_result(
            identifier,
            value,
            cast(_RNAProvenance, provenance),
            result,
            truncated,
        )
    elif kind == "rna_type":
        rna = _type_rna(value, cast(_RNAProvenance, provenance))
        result["description"] = _clip_text(
            _rna_field(rna, "description", cast(_RNAProvenance, provenance), ""),
            _MAX_DESCRIPTION_CHARS,
            truncated,
        )
        result["parameters"] = _rna_parameters(
            rna, cast(_RNAProvenance, provenance), truncated
        )
    elif kind == "rna_property":
        prop_info = _property_info(value, cast(_RNAProvenance, provenance), truncated)
        result["description"] = prop_info.pop("description", "")
        result.update(prop_info)
    else:
        result["description"] = _doc_description(value, truncated)
        if kind == "attribute" and any(
            type(value) is expected
            for expected in (type(None), bool, int, float, str)
        ):
            result["value"] = _json_value(value, truncated)
        if kind == "function":
            signature = _function_signature(value, truncated)
            if signature:
                result["signature"] = identifier + signature
    result["truncated"] = truncated[0]
    return _bound_result(result)


def _encoded_size(result: dict[str, Any]) -> int:
    """Measure the exact default JSON representation used by transport."""
    try:
        return len(json.dumps(result).encode("utf-8"))
    except (OverflowError, TypeError, ValueError):
        return _MAX_OUTPUT_BYTES + 1


def _clip_json_string(text: str, byte_limit: int) -> str:
    """Clip text against its default-JSON wire size, including escaping."""
    if len(json.dumps(text).encode("utf-8")) <= byte_limit:
        return text
    if byte_limit <= len(json.dumps("...").encode("utf-8")):
        return ""
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle].rstrip() + "..."
        if len(json.dumps(candidate).encode("utf-8")) <= byte_limit:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "..."


def _bound_result(  # pylint: disable=too-many-branches
    result: dict[str, Any],
) -> dict[str, Any]:
    if _encoded_size(result) <= _MAX_OUTPUT_BYTES:
        return result
    result["truncated"] = True
    parameters = result.get("parameters")
    if isinstance(parameters, list):
        while len(parameters) > 1 and _encoded_size(result) > _MAX_OUTPUT_BYTES:
            parameters.pop()
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            for key in ("description", "enum_values", "default"):
                if _encoded_size(result) <= _MAX_OUTPUT_BYTES:
                    break
                parameter.pop(key, None)
    for key in ("description", "signature"):
        value = result.get(key)
        if isinstance(value, str) and _encoded_size(result) > _MAX_OUTPUT_BYTES:
            result[key] = _clip_json_string(value, 256)
    if _encoded_size(result) > _MAX_OUTPUT_BYTES and isinstance(parameters, list):
        parameters.clear()
    for key in ("value", "default", "enum_values"):
        if _encoded_size(result) > _MAX_OUTPUT_BYTES:
            result.pop(key, None)
    if _encoded_size(result) > _MAX_OUTPUT_BYTES:
        result["description"] = ""
        result["signature"] = ""
    runtime = result.get("runtime")
    mandatory_strings: list[tuple[dict[str, Any], str]] = []
    if isinstance(runtime, dict):
        mandatory_strings.extend((runtime, key) for key in ("blender", "python"))
    mandatory_strings.extend(
        (result, key) for key in ("kind", "description", "signature")
    )
    for byte_limit in (128, 64, 16, 0):
        if _encoded_size(result) <= _MAX_OUTPUT_BYTES:
            break
        for owner, key in mandatory_strings:
            value = owner.get(key)
            if isinstance(value, str):
                owner[key] = _clip_json_string(value, byte_limit)
    assert _encoded_size(result) <= _MAX_OUTPUT_BYTES
    return result


def _error_result(
    identifier: str, error: str, truncated: bool = False
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "found": False,
        "identifier": _clip_json_string(identifier[:_MAX_IDENTIFIER_CHARS], 1024),
        "error": error,
        "truncated": truncated or len(identifier) > _MAX_IDENTIFIER_CHARS,
    }
    if _encoded_size(result) > _MAX_OUTPUT_BYTES:
        result["identifier"] = _clip_json_string(result["identifier"], 256)
        result["truncated"] = True
    assert _encoded_size(result) <= _MAX_OUTPUT_BYTES
    return result


def main(  # pylint: disable=too-many-return-statements
    params: object,
) -> dict[str, Any]:
    """Return compact documentation for one validated ``bpy`` identifier."""
    identifier = params.get("identifier") if type(params) is dict else None
    components, error = _validate_identifier(identifier)
    output_identifier = identifier if type(identifier) is str else ""
    if error is not None or components is None:
        return _error_result(output_identifier, error or "invalid_identifier")
    assert type(identifier) is str
    try:
        bpy = importlib.import_module("bpy")
    except (ImportError, ModuleNotFoundError):
        return _error_result(identifier, "runtime_unavailable")
    except Exception:  # pylint: disable=broad-exception-caught
        return _error_result(identifier, "internal_error")
    try:
        value, kind, provenance = _resolve(bpy, components)
        if value is _MISSING:
            return _error_result(identifier, kind)
        return _build_result(identifier, bpy, value, kind, provenance)
    except (
        AttributeError,
        KeyError,
        ReferenceError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return _error_result(identifier, "metadata_unavailable")
    except Exception:  # pylint: disable=broad-exception-caught
        return _error_result(identifier, "internal_error")
