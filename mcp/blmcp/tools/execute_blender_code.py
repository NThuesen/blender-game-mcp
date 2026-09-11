# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

# pylint: disable=C0114  # See tool doc-string.

__all__ = ("register",)

import base64
import hashlib
from pathlib import Path
import tempfile

from blmcp.tools_helpers.blender_cli import run_blender_cli, synced_blend_for_cli
from blmcp.tools_helpers.connection import send_code
from mcp.server.fastmcp import FastMCP, Image  # pylint: disable=import-error,no-name-in-module
from mcp.types import ImageContent, ToolAnnotations  # pylint: disable=import-error,no-name-in-module


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Execute Python Code",
            destructiveHint=True,
        )
    )
    def execute_blender_code(code: str) -> dict[str, object]:
        """
        Execute Python code in the connected Blender instance.

        The code runs in Blender's Python environment with full access to ``bpy``.
        To return data, assign a JSON-serialisable dict to a variable named ``result``.
        Deferred completion via ``check_is_finished`` is only supported by the
        interactive addon server, and is rejected in background mode.
        """
        # Not strict: LLM-generated code may return non-JSON-serializable values
        # (e.g. Blender objects). Use `repr` as a fallback instead of erroring.
        return send_code(code, strict_json=False)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Execute Python Code for Command-Line",
            destructiveHint=True,
        )
    )
    def execute_blender_code_for_cli(blend_file: str, code: str,
                                     expected_output_blend: str | None = None) -> dict[str, object]:
        """
        Execute Python code in a background Blender process.

        Opens *blend_file* with ``blender --background`` and runs *code*.
        Assign a dict to ``result`` to return data. When *expected_output_blend*
        is supplied, that path must be newly created with bytes different from
        the source; the result includes server-computed checkpoint hashes.
        """
        output = Path(expected_output_blend).absolute() if expected_output_blend else None
        source = (Path(blend_file).resolve(strict=True) if output is not None
                  else Path(blend_file))
        if output is not None and (output == source or output.exists() or output.is_symlink()):
            raise ValueError("expected output checkpoint must be a fresh distinct path")
        source_sha256 = None
        if output is not None:
            with source.open("rb") as stream:
                source_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        # LLM-generated code may return non-JSON-serializable values
        # (e.g. Blender objects), handled by `run_blender_cli` via `default=repr`.
        if output is None:
            with synced_blend_for_cli(str(source)) as synced_path:
                value = run_blender_cli(synced_path, code, arbitrary_code=True)
        else:
            value = run_blender_cli(str(source), code, arbitrary_code=True)
        assert isinstance(value, dict), (
            "Expected dict from `run_blender_cli`, got {!r}".format(type(value))
        )
        if "_checkpoint" in value:
            raise ValueError("result uses reserved _checkpoint field")
        if output is not None:
            if output.is_symlink() or not output.is_file():
                raise ValueError("expected output checkpoint was not created as a regular file")
            with output.open("rb") as stream:
                output_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            if output_sha256 == source_sha256:
                raise ValueError("expected output checkpoint does not differ from source")
            if "_checkpoint" in value:
                raise ValueError("result uses reserved _checkpoint field")
            value["_checkpoint"] = {"path": str(output), "source": str(source),
                "source_sha256": source_sha256, "output_sha256": output_sha256,
                "existed_before": False}
        return value

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Render Scene as Image",
            readOnlyHint=True,
        )
    )
    def get_render_as_image() -> Image:
        """Render the connected Blender scene and return PNG image content without saving overrides."""
        with tempfile.TemporaryDirectory(prefix="blmcp-render-") as temporary:
            render_path = Path(temporary) / "render.png"
            code = (
                "import bpy\n"
                "_scene = bpy.context.scene\n"
                "_old = (_scene.render.filepath, _scene.render.image_settings.file_format, "
                "_scene.render.use_file_extension)\n"
                "try:\n"
                f"    _scene.render.filepath = {str(render_path)!r}\n"
                "    _scene.render.image_settings.file_format = 'PNG'\n"
                "    _scene.render.use_file_extension = True\n"
                "    bpy.ops.render.render(write_still=True)\n"
                "    result = {'rendered': True}\n"
                "finally:\n"
                "    (_scene.render.filepath, _scene.render.image_settings.file_format, "
                "_scene.render.use_file_extension) = _old"
            )
            response = send_code(code, strict_json=True)
            if response.get("status") != "ok" or not render_path.is_file():
                raise RuntimeError("Blender did not produce the requested PNG render")
            return Image(data=render_path.read_bytes(), format="png")

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Render Saved Scene as Image for Command-Line",
            readOnlyHint=True,
        )
    )
    def get_render_as_image_for_cli(blend_file: str) -> ImageContent:
        """Open a saved blend in a background process, render it, and return PNG image content."""
        source = Path(blend_file).resolve(strict=True)
        with source.open("rb") as stream:
            source_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        with tempfile.TemporaryDirectory(prefix="blmcp-render-") as temporary:
            render_path = Path(temporary) / "render.png"
            code = (
                "import bpy\n"
                "prefs=bpy.context.preferences.addons['cycles'].preferences\n"
                "prefs.compute_device_type='CUDA'\n"
                "prefs.refresh_devices()\n"
                "assert any(d.type=='CUDA' for d in prefs.devices)\n"
                "for d in prefs.devices: d.use=(d.type=='CUDA')\n"
                "s=bpy.context.scene\n"
                "assert s.render.engine=='CYCLES'\n"
                "s.cycles.device='GPU'\n"
                f"s.render.filepath = {str(render_path)!r}\n"
                "s.render.image_settings.file_format = 'PNG'\n"
                "s.render.use_file_extension = True\n"
                "bpy.ops.render.render(write_still=True)\n"
                "result = {'rendered': True}"
            )
            run_blender_cli(str(source), code, arbitrary_code=False)
            if not render_path.is_file():
                raise RuntimeError("Blender CLI did not produce the requested PNG render")
            return ImageContent(type="image",
                data=base64.b64encode(render_path.read_bytes()).decode("ascii"),
                mimeType="image/png", _meta={"source": str(source),
                                              "source_sha256": source_sha256})
