# Token-efficiency benchmark tasks

This directory defines ten deterministic Blender MCP tasks, executable oracles, and an **offline Task 11 checkpoint** for telemetry, provider protocol adapters, persistence, and reports. It does not yet run live model trials or establish token-efficiency/A/B claims.

## Scope

The tasks isolate saved-scene inspection, evaluated geometry, exact scene edits, missing-file discovery, stale API recovery, batch save-as, GLB round-trip, and a small render. Every prompt is a single canonical string reused unchanged across execution variants. Placeholders `{{fixture}}` and `{{output}}` are resolved by a future runner without adding backend guidance.

`tasks.json` records the fixture, prompt, 20-turn cap, render requirement, validator, expected answer/output, source-safety contract, applicable execution variants, runtime-documentation purpose, and deterministic validator inputs. Task 7 deliberately supplies the removed `bpy.ops.object.lamp_add(...)` call; in Blender and standalone `bpy` 5.2.1 it raises `AttributeError` with `could not be found`, while the current call is `bpy.ops.object.light_add(...)`. Its oracle checks the resulting light, not whether a documentation tool was called. Tool behavior is telemetry for a later stage.

## Fixture generation

Run either Blender or the matching standalone `bpy` Python in a temporary directory first:

```sh
BLENDER=/path/to/blender
"$BLENDER" --background --factory-startup \
  --python benchmarks/token_efficiency/fixtures/generate_fixtures.py -- \
  --output-dir /tmp/token-efficiency-fixtures --include-oracles

BPY_PYTHON=/path/to/python
"$BPY_PYTHON" benchmarks/token_efficiency/fixtures/generate_fixtures.py \
  --output-dir /tmp/token-efficiency-fixtures-bpy --include-oracles
```

The script creates all `.blend` sources. `--include-oracles` additionally creates disposable known-good and known-bad outputs for validator testing; those outputs are not checked in. Each artifact is stored under `oracle_outputs/<task-id>/` or `known_bad_outputs/<task-id>/` with the task's canonical output filename. A test harness must copy each one into a separate trial root outside the source-fixture directory before invoking the public validator. This makes filename/containment checks real without masking the intended semantic failure. Install generated source files intentionally only after reopening them in a fresh runtime and reviewing `manifest.json`.

Generation emits flushed `__TOKEN_EFFICIENCY_GENERATE__` start/done records with per-stage elapsed time. Use repeatable `--source FILE` and `--oracle-stage STAGE` options to isolate work; `--help` lists the accepted names. An `--oracle-stage` selection enables only those oracle stages, while `--include-oracles` enables all of them. Invoke each generator process with the same 120-second external timeout used by validators so a Blender operator or runtime defect cannot stall automation indefinitely.

The low-level ASCII writer is intentional. In the tested macOS Blender 5.2.1 executable, buffered Python text-file creation after `bpy.ops.wm.save_as_mainfile` blocks indefinitely, although `os.open`/`os.write`, Blender saves, GLB export, and Workbench rendering complete normally. Standalone `bpy` does not exhibit that buffered-write defect. The generator therefore uses unbuffered low-level writes for its manifest and JSON oracle files in both runtimes.

The manifest's SHA-256 values protect the exact checked-in fixture bytes and records the generating `bpy` and Python versions. Blender serialization can vary across repeated saves or Blender builds, so byte-for-byte fixture regeneration is not promised. Each entry separately includes a canonical semantic oracle and its SHA-256. Semantic equivalence, not raw `.blend` byte identity, is the regeneration criterion. The render fixture uses deterministic flat Workbench settings to avoid platform-specific Eevee shader compilation; runtime probing on 5.2.1 confirms `BLENDER_EEVEE` and `BLENDER_WORKBENCH` are valid while the older transitional identifier `BLENDER_EEVEE_NEXT` is invalid.

## Validation

`validators.py` never imports `bpy`. It performs JSON answer checks and path/hash checks in the ordinary test process, then invokes `fixtures/inspect_artifact.py` in a configured Blender executable or standalone `bpy` Python. Subprocess argument arrays are used without a shell. POSIX nonblocking pipes and a selector cap combined stdout/stderr at 2 MiB, with a shared 120-second process/drain deadline and at most one additional second for direct-child reaping. No drain threads, blocking reads, joins, or disk spooling are used. After observed leader exit, pending output is drained under the existing cap/deadline with a short (50 ms, checked at 20 ms polling intervals) post-exit allowance. Leader exit plus actual EOF on both pipes completes capture without signaling, including nonzero exits. Persistent inherited pipes require private-group termination; timeout, output cap, observer failure, and interruption also retain termination. Required signaling failures remain errors. Selector closure, both pipe closures, and bounded direct-child reap are attempted independently. The leader is observed with `waitid(WNOWAIT)` or macOS `kqueue` and reaped only after capture/group cleanup so its PID cannot be recycled before required signaling. Protocol payloads have a separate 1 MiB limit and diagnostics are bounded to 2,000 characters. Results are JSON-serializable objects with `ok`, `errors`, and `details`; expected failures do not escape as bare assertions.

Every public validator requires an explicit output root. Outputs must be contained by that root, outside the source-fixture directory, use the declared filename, and may not overwrite source fixtures. Edited `.blend` files must also have a byte hash distinct from the source. Read-only tasks validate bounded, exact-schema answer JSON and the source hash under the same explicit-root contract. The batch validator proves preservation by checking exactly four objects, each source name/type, 8-vertex/6-polygon topology, unchanged X/Y, requested Z, and no extras. GLB validation imports into factory-empty state in a fresh runtime before checking object types, names, hierarchy, and unique mesh-position counts (glTF may duplicate vertices at normal or UV seams). Render validation checks file readability, PNG format, 64 by 64 RGBA structure, bounded non-transparent subject coverage, and an integer-valued red-dominant opaque center pixel. It does not use subjective visual scoring.

The pure suite exercises complete task schema, semantic good/bad oracles, malformed results, unsafe paths, source mutation, timeout mapping, and live bounded-capture behavior without importing `bpy`:

```sh
python3 -m unittest -v tests.test_token_efficiency_validators
```

The opt-in real-runtime suite generates disposable fixtures and canonical good/bad trials, runs all ten public validators, verifies each intended bad error code/field, semantically inspects both checked-in and regenerated copies of all five fixtures, checks source hashes, and performs fresh-runtime GLB import and PNG loading. It runs a subtest for every configured executable and skips cleanly when none is configured:

```sh
BLENDER_MCP_BPY_PYTHON=/path/to/bpy-python \
BLENDER_BIN=/path/to/blender \
python3 -m unittest -v tests.test_token_efficiency_real_runtime
```

`BLENDER_PATH` is accepted as an alias for `BLENDER_BIN`. Configure both standalone `bpy` and Blender to reproduce the supported dual-runtime matrix.

Material validation supports a Principled BSDF connected directly from its BSDF socket to the active Material Output Surface socket. Shader node names are unrestricted. Disconnected decoys and inactive outputs do not select the shader; shader groups, reroutes, and mixed shader graphs are outside this task's supported scope. Answer JSON must be UTF-8 and no more than 64 levels deep; encoding, parser recursion, and excess-depth failures return `malformed_result`.

## Limitations

Runtime capture requires POSIX process groups, nonblocking pipes, and `waitid(WNOWAIT)` or macOS `kqueue`; unsupported platforms fail before launching a child with a structured `runtime_launch_error`. This is not Windows process-tree support. This bounds capture and direct-child cleanup, not universal descendant containment: a descendant that closes its inherited stdout/stderr can survive normal leader exit without being detected or signaled. Descendants deliberately escaping the private group (for example via `setsid`) cannot be terminated safely by this mechanism; their inherited pipes still cannot extend the drain deadline. Orphaned descendants are reaped by the OS, not this non-parent validator. An OS-level uninterruptible process or stalled process creation cannot be given an absolute userspace cleanup guarantee; a direct child that does not reap within the cleanup allowance is reported as an error, not successful cleanup.

The oracles target Blender/`bpy` 5.2 semantics. GLB serialization bytes and rendered pixel values can vary by build, platform, GPU, or color-management implementation; the validators therefore use imported structure and bounded image properties rather than output byte hashes. A process boundary limits crashes and state leakage but is not an operating-system sandbox. Benchmark code still has the invoking user's filesystem and network permissions.

## Task 11 offline checkpoint

Run from the repository root using the existing environment; these commands do not install packages or access credentials:

```sh
.venv/bin/python3 -m benchmarks.token_efficiency.run list
.venv/bin/python3 -m benchmarks.token_efficiency.run dry-run --condition C
.venv/bin/python3 -m benchmarks.token_efficiency.run offline \
  --condition C --trials 3 --output-root /tmp/token-efficiency-offline
# Repeat the same command: re-hash artifacts and rerun validators, then skip valid trials.
.venv/bin/python3 -m benchmarks.token_efficiency.run validate \
  --output-root /tmp/token-efficiency-offline
.venv/bin/python3 -m benchmarks.token_efficiency.run report \
  --output-root /tmp/token-efficiency-offline
.venv/bin/python3 -m unittest -v \
  tests.test_token_efficiency_telemetry tests.test_token_efficiency_runner \
  tests.test_token_efficiency_validators
```

`tasks` aliases `list`. `offline` defaults to the three answer-JSON tasks and generates answers from their committed oracle contracts. Every response is a **synthetic fixture**; usage is absent, not fabricated. `--task ID` is repeatable. Artifact tasks additionally require generated oracle outputs (see Fixture generation above), an explicit validator runtime, and a separate output root:

```sh
.venv/bin/python3 -m benchmarks.token_efficiency.run offline \
  --task create_exact_cube --condition C --trials 3 \
  --fixture-root /tmp/token-efficiency-fixtures-bpy \
  --oracle-root /tmp/token-efficiency-fixtures-bpy/oracle_outputs \
  --runtime /path/to/bpy-python --output-root /tmp/token-efficiency-artifact-offline
```

Artifact trials copy the generated known-good output and invoke the unchanged public validator in a fresh runtime. They do **not** measure model scene construction. The trial wall timer includes fixture parsing/copying and excludes validation; subprocess duration, CPU, RSS, runtime Blender/bpy versions and cost remain null when not measured. The fixture manifest hash identifies generating-runtime provenance but does not pretend to probe the validator runtime. Tool image bytes mean decoded provider/MCP payload bytes, not rendered artifact file size. No tools are exposed to the synthetic completion; the planned condition allowlist and actual empty schema set are both recorded.

### Conditions and fairness

| ID | Planned backend | Planned documentation |
|---|---|---|
| A | Blender CLI | bundled |
| B | standalone bpy CLI | bundled |
| C | standalone bpy CLI | bundled + runtime |
| D | live Blender MCP | bundled + runtime |

These are **enhanced-code backend ablations**. None is the official original baseline, pinned separately to `4309a39646e644261624bfcd2bca669b343b7621`. In particular A vs C changes both backend and docs and cannot support a docs-only causal conclusion. `models.py` declares a deliberately minimal execution/API-documentation allowlist; it is not a claim to reproduce either server's full default tool set. Each task retains its saved/live applicability contract. Offline execution is labeled `synthetic_oracle` regardless of its planned condition ID.

Canonical task prompts are unchanged, with only source/output path substitutions. Records retain canonical and resolved prompts, the complete offline policy, requested/returned model identifiers, task contract, exact selected schemas, planned tools, revision, harness/task/inspector/chat-client source hash, fixture manifest/source hashes, and unsupported generation controls. Fresh conversations and unique attempt roots prevent history/output reuse. Live scene initialization is not implemented. Cache/warmth are explicitly `not_applicable_synthetic`, not cold-cache claims.

### Telemetry and persistence semantics

- OpenAI Chat Completions and Claude Messages adapters reuse the existing `chat_client` schema converters and response parsers. Malformed argument JSON, non-object arguments, missing tools, duplicate tool names, and forbidden returned tool calls fail closed before dispatch. No new agent loop or network/auth transport is added. Temperature/reasoning options fail explicitly if supplied; unset options record unknown provider defaults.
- Token totals are provider-reported only. Missing categories stay null, including when any response lacks that category. OpenAI uncached input requires both prompt total and cached count. Reasoning is a subset of output, never added again. Claude cache creation/read are separate input buckets; reasoning is unavailable. Known numeric raw usage is retained; arbitrary provider usage strings/unknown fields are discarded.
- Turns count returned API responses; API attempts also count explicitly recorded API errors. Tool calls/errors are counted by invocation/result, not inferred from prose. A repair is the next invocation of the **same tool** after that tool errors, not inferred model intent. Failure categories are `planning`, `api_misuse`, `execution`, `timeout`, `validation`, `visual_mismatch`; offline oracle failures currently map to validation. Live classification remains to be wired.
- UTF-8 byte counts use canonical redacted JSON for saved transcript/schema snapshots (not cumulative wire bytes), raw text/JSON bytes for tool results, and decoded bytes for images. Wall/tool durations use a monotonic clock; unmeasured child-process/resource data stays null with platform/method recorded.
- Cost defaults to null. The optional estimator requires a matching returned model, explicit version/source/currency and all four per-million input/cache/output prices plus complete counts. It is labeled estimated, never billing, and does not invent rates. No tokenizer estimate is enabled.
- `trials.jsonl` is single-writer, append-only and fsynced per completed record. Interrupted tails are fenced with a newline, never truncated. Corrupt/incomplete lines remain visible as diagnostics. Interrupted attempts have no complete record; reruns use a new attempt directory and preserve partial files. Failed complete trials remain evidence but are retried. Resume requires exact identity/config/source hashes, all saved artifact/transcript hashes, a stored passing validator and a fresh passing validator. Existing files alone never qualify.
- `summary.csv`, `samples.csv`, and `summary.md` deduplicate by complete identity and retain completed/unique/superseded attempt counts. Config/source hashes and synthetic status partition aggregates. Raw N, correctness and successful-sample min/median/max are descriptive. Token summaries retain category-specific N; missing is not zero. At least three successful repeats and no failures are necessary, not sufficient, for real comparisons. Synthetic trials are never eligible; no significance or efficiency win is claimed. Reports summarize stored evidence; `validate`/resume recheck current artifacts.

### Required before a real small pilot

This is a coherent offline checkpoint, **not completion of Task 11's live orchestration**. The Codex subprocess checkpoint below supersedes the earlier proposed chat-client loop refactor: reuse Codex's agent loop. Before spending on models, review its runner setup and satisfy the documented MCP discovery, dependency, isolation and budget prerequisites. Capture actual server instructions, tool schemas and backend/Python/Blender versions; freeze exact model/control/cache policies and versioned rate evidence if estimates are wanted. Review transcript redaction at that boundary: the current defense-in-depth filter cannot guarantee sanitizing arbitrary live model text. Compare correctness first on identical prompts, then collect at least three repeats per matched task/model/condition before considering efficiency comparisons. Do not relabel A as the original baseline.

## Task 11 Codex subprocess checkpoint (offline only)

`codex_runner.py` constructs a fresh `codex exec --json --ephemeral` invocation
per task/trial and observes its events. Codex owns the agent loop. No model
request is made by the commands below. `CodexSubprocessAdapter.run` refuses the
generated specs because review and prerequisite blockers remain; the CLI has
no live mode. Synthetic replay is always labeled synthetic, including when its
answer passes a real public validator. No model performance is measured here.

Use the existing interpreter directly. Each dry-run root must be new and outside
the repository; replace the suffixes if already used:

```sh
.venv/bin/python3 -m benchmarks.token_efficiency.codex_runner dry-run \
  --condition official_original --task data_block_counts \
  --output-root /tmp/task11-original-review-01
.venv/bin/python3 -m benchmarks.token_efficiency.codex_runner dry-run \
  --condition enhanced_bpy --task data_block_counts \
  --output-root /tmp/task11-enhanced-review-01
.venv/bin/python3 -m unittest -q tests.test_token_efficiency_codex \
  tests.test_token_efficiency_telemetry tests.test_token_efficiency_runner \
  tests.test_token_efficiency_validators tests.test_token_efficiency_real_runtime
```

Optional `--server-python /absolute/existing/python`, `--runtime /absolute/runtime`
and `--model exact-model-id` freeze launch choices. Defaults are explicit blocked
placeholders, not a recommendation or a working environment. Original runtime
means a Blender executable; enhanced runtime means Python with standalone bpy.
To exercise the subprocess/persistence boundary with your own **synthetic** JSONL:

```sh
.venv/bin/python3 -m benchmarks.token_efficiency.codex_runner synthetic-replay \
  --task data_block_counts --fixture-stream /tmp/SYNTHETIC-events.jsonl \
  --output-root /tmp/task11-synthetic-replay-01
```

Replay uses a plain Python child that emits the supplied file, not Codex. It writes
fresh attempt directories, redacted events/capture diagnostics, answer output,
and fsynced append-only `trials.jsonl` records with actual validator results.
Launch/capture errors also produce failed records; interruptions are not complete
trials. Replay does not resume or manufacture a successful answer when missing.
The existing offline runner remains available with its prior resume behavior.

### Configuration identity and prerequisites

The two conditions are `official_original` at
`4309a39646e644261624bfcd2bca669b343b7621` (Blender CLI and static docs) and
`enhanced_bpy` (current working-tree source, bpy CLI and static/runtime docs).
This compares whole saved-file configurations, not documentation alone. No live
scene lane is included. These names do not repurpose the prior A–D conditions.

`control/launch-spec.json` stores argv, exact canonical/resolved prompts, policy,
requested model, limits, allowlist, fixture/manifest/task-contract/harness hashes,
revision and a per-file server source manifest. `control/identity.json` hashes
the entire spec. `control/server-source/` preserves those source bytes, including
static docs and `prompts.yml`. Original bytes come from Git objects; enhanced
bytes come from the dirty working tree. The manifest explicitly scopes Python,
YAML, RST, TOML and `uv.lock`, excluding environments/caches; it is not a dependency
environment snapshot. No auth/config or `.env` contents are read. Canonical prompts
are unchanged; only fixture/output placeholders are resolved in the agent prompt.
Backend guidance is separate, recorded developer policy.

Actual MCP initialization instructions, `tools/list` schemas and effective Codex
tool exposure remain **null/unverified**, never replaced with guessed schemas or
the committed expected-tool test snapshots. Before launch, the parent must capture
the exact initialize response and tool listing from each frozen server, verify
the allowlist as exposed by Codex, and include these complete artifacts in the
reviewed configuration identity. Capture resolved model/control/runtime versions,
dependency versions and effective policy as well. The original package specifies
`mcp[cli]>=1.2.0` without an upper bound; enhanced specifies `<2`. Compatibility of
the original with an existing interpreter has not been tested and may require a
separately approved compatible environment. Original lacks `blmcp.__main__`; both
specs call the declared `blmcp.main` entry point directly, without installation.

Installed help measured `codex-cli 0.153.4`, `--ignore-user-config`, `--ephemeral`,
`--strict-config`, JSONL and sandbox options. MCP command/args/cwd/env,
`enabled_tools`, `required`, `features.shell_tool`, `web_search`, and developer
instructions use documented dotted `-c key=TOML` overrides. Settings are only on
that subprocess argv. `--ignore-user-config` skips user config while retaining
existing login; no global config is written. Specs retain `workspace-write` and
`on-request` approvals and do not ignore execpolicy rules. Managed requirements
may still constrain or reject them; failure is not permission to reroute.
No live config parse or server initialization was performed in this checkpoint.

References checked 2026-09-07:
[official non-interactive documentation](https://developers.openai.com/codex/noninteractive),
[configuration reference](https://developers.openai.com/codex/config-reference),
and [MCP documentation](https://developers.openai.com/codex/mcp).
The tests contain handwritten synthetic streams matching documented exec shapes;
they are not locally observed model events. MCP lifecycle fixture compatibility
with this installed build still needs the approved parent smoke.

### Bounds, PTY, telemetry and eligibility

Capture uses a shared deadline (default 120 seconds) and combined stdout/stderr/PTY
byte cap (2 MiB), nonblocking pipes, and a private process group. It reuses the
unchanged validator's non-reaping exit observer and cleanup helpers. Timeout,
output/event stops, observer exceptions, and persistent inherited handles trigger
group cleanup, independent handle closure and a one-second bounded reap. Normal
leader exit plus EOF requires no signal. Descendants escaping the group, or closing
all handles before normal leader exit, are not contained; this is not an OS sandbox.

`codex_pty.py` acquires a private controlling PTY on stdin while stdout/stderr remain
pipes. Synthetic tests measured terminal stdin and pipe capture without needing
an inherited parent PTY. A separate `/dev/tty` open probe was denied by this
environment; it was not rerouted. Its assertions remain as an opt-in test requiring
`TASK11_PARENT_TTY_PROBE=1` in a parent environment where that operation is allowed.
The ordinary suite reports it skipped. No claim is made that a controlled PTY
fully solves Codex launch here. The parent must run the preserved probe in its
fresh working PTY before any separately approved model smoke.

Exec JSONL exposes user turns, not every internal model response. The task's
20-model-turn contract cannot be proven or enforced through these events. One
exec-turn and 20 unique MCP-call event limits are reactive watchdogs, not strict
pre-dispatch budgets: calls may already be running and buffered events may overshoot.
Deadline/output are external bounds; exact model/tool dispatch caps need further
CLI support or an MCP gate. This mismatch remains an eligibility blocker.

`turn.completed.usage.input_tokens` includes `cached_input_tokens`; uncached input
is computed only when both exist. `output_tokens` already includes
`reasoning_output_tokens`; reasoning is retained as a subset, never added again.
Missing categories remain null; explicit zero stays zero. Cache creation, internal
API attempts, returned model, tool duration/image/resource measurements and cost
are unavailable here. Incomplete/error streams retain reported usage fields but
do not claim complete totals. No cost estimate or implicit rates are supplied.
Started/updated/completed MCP events share item IDs and count once. Malformed,
truncated, unknown, duplicate or incomplete streams fail closed; tool failures
remain visible even when the model subsequently recovers.

`workspace/` contains only a copied input and a new output directory. Source
hashes and public validators verify preservation and outputs. No manifest,
validator, task oracle or server source is copied into the agent workspace.
However, ordinary workspace-write restricts writes, not all reads, and MCP Python
can access host files. Separation plus policy text is only an instruction until
the parent establishes a read boundary for BOTH Codex and MCP/backend processes.
Use an externally isolated filesystem containing vetted source/docs/runtime and
trial input/output only; do not expose the benchmark repo or control/oracle tree.

`features.shell_tool=false` requests disabling the built-in shell. It is not proof
that every alternative execution path is disabled. Command execution, file changes,
web search, delegation, unknown items, unrelated MCP servers or non-allowlisted
tools stop observation and invalidate eligibility. MCP execution itself can bypass
the intended task policy, so a clean trace cannot prove oracle isolation. All
checkpoint specs and replay records are ineligible. Reports now additionally
require an affirmative per-record eligibility field; three passing validators
alone cannot make an unverified/bypassing runner eligible.

Smallest parent plan: independently review code/spec artifacts; run the preserved
PTY probe in a permitted fresh parent PTY; separately authorize MCP-only discovery
for both frozen sources using existing compatible interpreters; freeze actual
schemas/instructions/exposure and isolated read permissions; resolve the model-turn
contract limitation. Only after that review and explicit live authorization, run
one saved-file `data_block_counts` trial per configuration with the same selected
model/prompt, unique output roots, source checks and fresh public validators.
Keep these as setup smokes, not efficiency results. No commit or live pilot is
authorized by this checkpoint.

## Future suite extension

A separate `creative_quality` suite may coexist beside this one, but must not be mixed into deterministic efficiency aggregates. Its optional schema should include `task_family`, `evaluation_mode`, fixed `seeds`, `reference_assets`, a repeated-trial policy, visual and geometry metrics, and a versioned multi-rater protocol with individual ratings retained for provenance. Candidate categories include Curve Bevel Profile, Ellie Pose Library, sculpt demos, and hidden-render reconstruction; these names are examples, not license claims.

Official demo files may seed that future suite only when every external asset records a pinned download URL, per-file license and attribution, downloaded SHA-256, compatible Blender version, and local-cache key. A mutable URL alone is never sufficient provenance. Large demo files are neither downloaded nor checked in by this deterministic suite. Creative validators should use the same bounded result envelope while keeping rater scores and repeated trials distinct from exact-oracle pass/fail. No creative-quality tasks, assets, raters, visual metrics, or repeated-trial runner are implemented here.
