# Direct full-suite execution on mghavn

`tools/direct_suite.py` is an external sequential dispatcher, not a model reasoning controller. It verifies the pinned input-only manifest, applies reviewed literal initialization using standalone bpy 5.2.1, independently renders/reopens the saved baseline, executes a genuine four-tool Codex preflight, and invokes one direct Codex generation session per task with ten requested meaningful rounds. No evaluator scores or VLM judge enter generation.

Exactly 26 policy tasks are selected by excluding completed `level1/camera1` from the 27 canonical tasks. Existing completed output is not modified. Active staging is observed, never duplicated. Task work directories must be fresh; there is no automatic generation restart or hidden budget reset.

Post-generation, artifacts are decoded/hashed, each scene is independently audited/rendered on CUDA, exact task-policy preservation and meaningful edits are checked against both baseline and preceding checkpoint, and repeated complete scene states are rejected. The offline pinned GPU CLIP pair scorer runs after the generation process exits. `scores.json` retains paired NCLIP/PL and `result.json` selects minimum raw NCLIP, earliest-round tie.

Runtime provenance: `direct_runtime.py` was copied from the reviewed full-suite runtime in the original worktree without editing the original. Its admitted interpreter is the authorized mghavn custom-bpy venv. Embedded RNA graphs are serialized with deterministic repeated-node references to avoid exponential armature expansion; heterogeneous collections retain each element's RNA schema. Neither fix drops audited fields.

## Invocation

```sh
nohup /home/mg/blenderbench-direct/enhanced-mcp/mcp/.venv/bin/python -u \
  /home/mg/blenderbench-direct/direct_suite.py \
  --output /home/mg/blenderbench-direct/remaining-direct-03 \
  > /home/mg/blenderbench-direct/remaining-direct-03.log 2>&1 < /dev/null &
```

The process holds a host-wide advisory lock. `process.json`, `status.json`, dispatcher `events.jsonl`, per-task native heartbeat logs, and per-task `codex/{preflight,run}.{jsonl,stderr,process.json,argv.json,prompt.txt}` are retained. The output-root `STOP` cancels native workers and prevents the next task; **during an active direct Codex invocation it is only observed after that invocation returns**, not immediate cancellation. Kill that exact Codex process if immediate interruption is needed. No claims of isolation beyond workspace-write and explicit per-tool approval are made.

**Deadlines remain backend 120 seconds and MCP client 86400 seconds.** They are not unlimited and have not been changed. Native verification has manual cancellation/disk-floor safety and no operation deadline.

Initialization/audit failures and generation/scoring failures are retained as failed tasks; later tasks continue sequentially. A started suite is not a completed benchmark. No merge, push, publication, or automatic repair/retry is performed.
