# Token-efficiency benchmark tasks

This directory defines ten deterministic Blender MCP tasks and executable oracles. It does not contain model runners, telemetry, token accounting, model A/B results, or claims about which execution path is faster. Those belong to a later benchmark stage.

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

## Future suite extension

A separate `creative_quality` suite may coexist beside this one, but must not be mixed into deterministic efficiency aggregates. Its optional schema should include `task_family`, `evaluation_mode`, fixed `seeds`, `reference_assets`, a repeated-trial policy, visual and geometry metrics, and a versioned multi-rater protocol with individual ratings retained for provenance. Candidate categories include Curve Bevel Profile, Ellie Pose Library, sculpt demos, and hidden-render reconstruction; these names are examples, not license claims.

Official demo files may seed that future suite only when every external asset records a pinned download URL, per-file license and attribution, downloaded SHA-256, compatible Blender version, and local-cache key. A mutable URL alone is never sufficient provenance. Large demo files are neither downloaded nor checked in by this deterministic suite. Creative validators should use the same bounded result envelope while keeping rater scores and repeated trials distinct from exact-oracle pass/fail. No creative-quality tasks, assets, raters, visual metrics, or repeated-trial runner are implemented here.
