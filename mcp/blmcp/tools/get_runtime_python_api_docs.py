# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

# pylint: disable=C0114  # See tool doc-string.

__all__ = (
    "register",
)

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.blender_cli import run_blender_cli, synced_blend_for_cli
from blmcp.tools_helpers.connection import send_code
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(
    toolcode_load_from_filepath(__file__), use_result=False
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Runtime Python API Docs",
            readOnlyHint=True,
        )
    )
    def get_runtime_python_api_docs(identifier: str) -> dict[str, object]:
        """
        Return compact docs for an exact Python API *identifier* from the connected Blender runtime.

        Use a fully-qualified identifier such as ``bpy.ops.mesh.primitive_cube_add``
        or ``bpy.types.Object.location``. Use ``search_api_docs`` to discover identifiers.
        """
        code = toolcode_format_call(_TOOL_CALL, {"identifier": identifier})
        # The add-on's weak sandbox replaces bpy.ops' C wrapper factory. Runtime
        # introspection validates that exact factory identity, so audited shared
        # tool-code must execute without the LLM-code monkeypatches.
        return send_code(code, strict_json=True, sandbox=False)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Runtime Python API Docs for Command-Line",
            readOnlyHint=True,
        )
    )
    def get_runtime_python_api_docs_for_cli(
        blend_file: str, identifier: str
    ) -> dict[str, object]:
        """
        Return compact docs for an exact Python API *identifier* from a background runtime.

        Opens *blend_file* with the selected Blender or ``bpy`` CLI backend. Use a
        fully-qualified identifier such as ``bpy.ops.mesh.primitive_cube_add`` or
        ``bpy.types.Object.location``. Use ``search_api_docs`` to discover identifiers.
        """
        code = toolcode_format_call(_TOOL_CALL, {"identifier": identifier})
        with synced_blend_for_cli(blend_file) as synced_path:
            return run_blender_cli(synced_path, code)
