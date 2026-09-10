# Direct suite: live edits and saved-deliverable validation

User-approved workflow supersedes the former independent reopen admission gate.
Initialization still verifies pinned inputs, applies reviewed literal task initialization,
saves a protected initial blend, records the full native RNA baseline, and renders a
CUDA-only PNG which the parent decodes. Generation starts without independently
reopening and comparing that initialized file first.

The deployed standalone MCP backend is **not a persistent Blender session**:
`blender_cli.py` launches `bpy_cli_runner.py` as a subprocess per request, and its
`main()` opens `blend_file` before executing code. Direct Codex continues edits from
the previous saved checkpoint across calls and edits the open scene within each call.
Unsaved memory cannot persist across these calls. This change does not introduce a
persistent server, replace the backend, or change the stock 120-second code deadline.

After all ten direct meaningful rounds, the parent independently reopens/renders
saved deliverables, validates each against the initialization baseline and prior
checkpoint, checks distinct allowed state, and then performs GPU CLIP scoring.
No oracle/goal scripts or score feedback are given to generation. The exact four
MCP tools and all five task policy families remain unchanged.

## Narrow cleanup exception

The native audit records material lifecycle evidence separately from content hashes.
Only a material **missing from a later saved audit** can be excused, and only when
the earlier independent evidence records integer `users == 0`, `use_fake_user == false`,
`use_extra_user == false`, and `library == null`. No fake users are added for auditing.
Missing evidence fails closed. Present orphan material contents, new materials,
assigned materials, all object/material slot references, geometry, cameras, lights,
worlds, scenes and other datablocks remain audited exactly. Cleanup alone never
counts as a meaningful edit. No other datablock class is exempted without evidence.

`tests/test_direct_saved_cleanup.py` exercises accepted orphan loss, rejected assigned
material changes/deletions, protected/missing lifecycle evidence, other forbidden
changes, no-op rejection, the actual worker lifecycle serializer, and generation
reaching Codex without any independent pre-generation reopen call.

## Authorized restarts

`direct_suite.py --output FRESH_ROOT --tasks TASK_ID ...` permits an explicit unique
canonical subset, never camera1. Operators must verify the canonical manifest,
completed results and artifacts, stop the preceding dispatcher and retry through
STOP, verify both processes and descendants exited, then construct the unfinished
subset. Retain previous roots including failures. The unchanged shared
`direct-suite.lock` serializes the new dispatcher through initialization, generation,
validation and scoring. Keep process.json, events.jsonl, status.json and launcher
stdout/stderr as durable handles. A running task is not a completed result.
