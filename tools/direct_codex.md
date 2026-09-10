# Direct Codex + standalone bpy

`direct_codex.py` is a direct-process launcher and artifact gate, **not a benchmark
controller**. It runs one read-only MCP preflight and, in `run` mode, one continuous
Codex generation session. It does not initialize data, orchestrate refinement
rounds, run a judge, score checkpoints, or publish anything. Camera1 is permitted;
it does not use the legacy camera controller's camera2–camera9 task registry.

## Approval fix and scope

Supported/tested config contract: **Codex CLI 0.154.0**, standalone **bpy 5.2.1**.
The launcher fails closed on other Codex versions pending a schema review.

The observed failure was `MCP tool call requires approval, but approval policy is
never`. Exit 0 and final assistant text did not prove that any round ran. That is
an MCP approval denial, not evidence of a shell sandbox failure.

Official references (version-pinned source is authoritative for this launcher):

- [Config reference](https://developers.openai.com/codex/config-reference)
- [Live schema](https://developers.openai.com/codex/config-schema.json)
- [0.154.0 schema](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/config.schema.json):
  `RawMcpServerConfig.tools`, `McpServerToolConfig.approval_mode`, `AppToolApproval`.
- [0.154.0 approval implementation](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/mcp_tool_call.rs#L2345-L2356):
  `auto` consults tool annotations; `prompt` requires approval; `writes` requires
  approval unless read-only; **`approve` does not request tool approval**.
  `auto` is NOT synonymous with unconditional preapproval.

Only after the user approves arbitrary Python execution by this particular server,
pass `--approve-blender-tools`. The launcher adds
`mcp_servers.blender.tools.<tool>.approval_mode="approve"` to exactly:

- `execute_blender_code_for_cli`
- `get_runtime_python_api_docs_for_cli`
- `search_api_docs`
- `get_python_api_docs`

It retains `--sandbox workspace-write`, explicitly sets `approval_policy="never"`,
and uses `--ignore-user-config --strict-config`. It never enables a global approval
bypass, changes user config, or silently replaces a conflicting approval setting.
Server defaults, per-tool policy in input, extra servers/tools, or unrelated config
keys are rejected. The complete MCP table is supplied as a TOML inline CLI override,
not lossy recursive dotted-key flattening. Runtime paths preserve venv spelling.

**This is real preauthorization, not a sandbox.** MCP Python has the invoking
server's OS privileges, potentially beyond Codex workspace-write. Review
[SECURITY.md](../SECURITY.md) and [backend docs](../readme_bpy_backend.rst).
Use an isolated disposable account/runtime and an external workspace without
project Codex config/plugins. Built-in Codex tools are not an MCP allowlist;
the prompt prohibits shell scene operations and permits `view_image` inspection.
Managed policies may still deny tools. No attempt is made to override them.

## Usage

Run from the repository root. Copy `tools/direct_codex.example.toml` outside the
repo and fill in verified absolute server/interpreter paths. Use an installed MCP
1.x server environment. Keep credentials out of TOML: effective config and argv
are deliberately retained in the evidence directory. Check `codex login status`
without reading credential files.

Prepare a fresh external workspace with reviewed `initial.blend`; do not give the
model solution scripts/oracles. Independently admit the GPU runtime with real
renders before generation. The preflight below only checks tool execution,
`sys.executable`, and bpy version, **not GPU rendering**. Both modes make paid model
calls and require authorization for the selected exact model.

```sh
uv run --frozen --project benchmarks/blenderbench_camera python tools/direct_codex.py preflight \
  --config /external/server.toml --codex /absolute/bin/codex \
  --workdir /external/camera1 --evidence /external/preflight-evidence \
  --model YOUR_EXACT_MODEL --approve-blender-tools

uv run --frozen --project benchmarks/blenderbench_camera python tools/direct_codex.py run \
  --config /external/server.toml --codex /absolute/bin/codex \
  --workdir /external/camera1 --evidence /external/generation-evidence \
  --model YOUR_EXACT_MODEL --approve-blender-tools \
  --prompt /external/camera1-prompt.txt --target /external/target.png --rounds 10
```

Using this locked Python environment provides Pillow; **no camera controller code
is imported**. The prompt is user-owned: specify Camera1-only changes, fixed lens,
render settings and scene invariants, CUDA GPU enabled/CPU disabled, exact ten
meaningful rounds, source preservation, and no scoring until generation ends.
The launcher appends only the tool/output contract. Each round must save
`iteration01.blend`/`iteration01.png` through `iteration10.*`, plus one JSONL row:

```json
{"round":1,"pose":{"location":[1,2,3],"rotation":[0,0,0]},"blend":"/external/camera1/iteration01.blend","png":"/external/camera1/iteration01.png","rationale":"camera adjustment"}
```

The preflight requires an actual completed successful execution-tool JSONL result
containing a fresh nonce, the exact configured Python path, and version 5.2.1.
A final assistant claim, denied/failed tool, empty transcript, or exit 0 alone
cannot pass. Generation is not launched when preflight fails. Raw JSONL, stderr,
argv, prompt, child PID, and exit code are retained in a fresh private directory.
Existing iteration paths/ledger are refused; resume is unsupported.

## Completion and limitations

After generation, the artifact gate requires exact sequential ledger rounds,
expected absolute checkpoint paths, nonempty distinct checkpoint bytes, distinct
finite reported poses, and fully decoded PNGs. It records SHA-256 and dimensions.
A run that exits 0 with zero rounds fails. Success is explicitly labelled
`artifacts_complete_not_scene_audited`: this gate does **not** reopen `.blend` files
or prove pose truth, scene preservation, meaningful edits, GPU selection, image
correspondence, or source immutability. Independent checkpoint audits remain
mandatory before claiming ten valid benchmark rounds. Report missing/invalid
rounds honestly. Post-generation GPU CLIP scoring/best checkpoint selection is
separate; no scores or VLM judge are fed into generation.

No launcher wall-clock timeout is imposed. **Stock backend execution still has a
120-second timeout**, and the example's MCP client timeout is **86400 seconds**.
Neither is unlimited and this launcher does not patch either. Long render runtime
changes require separate review; do not describe this setup as no-hard-timeouts.
Cancellation/supervision and disk-floor checks belong to the operator; interrupted
runs retain logs and must use fresh destinations. No full live ten-round run or
new GPU admission is claimed by the tests here.

```sh
uv run --frozen --project benchmarks/blenderbench_camera python -m unittest tests.test_direct_codex -v
```

Tests use synthetic local subprocess fixtures, not model calls or Blender results.
