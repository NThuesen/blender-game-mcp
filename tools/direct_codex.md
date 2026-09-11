# Low-level direct Codex launcher

`direct_codex.py` is the low-level one-task launcher used by the portable
[full BlenderBench runner](../benchmarks/blenderbench_direct/README.md). Prefer the
full runner for publication runs because it owns pinned data, initialization,
post-generation saved-scene audit, scoring, aggregation, and provenance.

The launcher admits exactly these MCP tools:

- `execute_blender_code_for_cli`
- `get_render_as_image_for_cli`
- `get_runtime_python_api_docs_for_cli`
- `search_api_docs`

It requires explicit scoped preapproval with `--approve-blender-tools`, retains
Codex `workspace-write` plus `approval_policy="never"`, rejects extra MCP servers
or tools, and verifies a real successful preflight MCP result. MCP execution has
the server process's OS privileges; this is not a security sandbox.

For benchmark rounds, pass `expected_output_blend` to
`execute_blender_code_for_cli`. The server requires a fresh changed checkpoint
and returns source/output SHA-256 evidence. The following
`get_render_as_image_for_cli` call renders those exact bytes with CUDA-only
Cycles and returns PNG content plus its source hash.

Every path, model, and runtime version pin is explicit:

```sh
python tools/direct_codex.py run \
  --config /external/blender-mcp.toml \
  --codex /absolute/bin/codex \
  --codex-version 'EXACT CODEX --version OUTPUT' \
  --bpy-version 'EXACT BPY VERSION PREFIX' \
  --workdir /external/fresh-task-work \
  --evidence /external/fresh-private-evidence \
  --model 'PROVIDER/MODEL-ID' \
  --prompt /external/task-prompt.txt \
  --target /external/target.png \
  --rounds 10 --approve-blender-tools
```

`tools/direct_codex.example.toml` documents the MCP schema. Keep credentials out
of TOML and inspect retained raw argv/JSONL/stderr before publication. Existing
iteration files are rejected. This launcher does not resume, regenerate, score,
or establish full BlenderBench completion by itself.
