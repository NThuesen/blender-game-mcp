# Portable full-27 BlenderBench runner

This package runs the 27 public BlenderBench tasks with a model selected at the
CLI. It is an external sequential dispatcher, not a reasoning controller. One
direct Codex session per task receives exactly four Blender MCP tools:

- `execute_blender_code_for_cli`
- `get_render_as_image_for_cli`
- `get_runtime_python_api_docs_for_cli`
- `search_api_docs`

`get_render_as_image_for_cli` independently reopens a saved checkpoint, renders
the exact on-disk bytes with CUDA-only Cycles in the configured CLI backend, and
returns actual PNG image content plus the source SHA-256 through MCP for each
required visual-inspection round. Each edit uses `expected_output_blend`; the MCP
server rejects pre-existing or unchanged output and reports server-computed
source/output hashes. Codex's built-in `view_image` remains disabled.

The frozen publication protocol requests exactly 10 sequential, meaningful,
saved edit/render/visual-inspection rounds per task. Generation has no shell
scene operations, goal/solution code or goal-scene access, VLM judge, CLIP or
photometric feedback, or evaluator feedback. Saved scenes are opened, audited,
GPU-rendered, and scored only after generation exits. A stopped/failed run is
never regenerated or resumed automatically.

## External provenance and attribution

The input manifest pins the [BlenderBench dataset](https://huggingface.co/datasets/DietCoke4671/BlenderBench)
by DietCoke4671 and contributors at revision
`203e4d325e9438ca55b29bdfc4f6a90842d74e68`, under **CC BY 4.0**. Preserve
upstream and per-asset attribution when redistributing downloaded assets. The
repository stores only manifests; downloaded `.blend` files and images belong
outside Git.

The metric wrapper extracts `clip_similarity` and `photometric_loss` from
[VIGA release](https://github.com/Fugtemypt123/VIGA-release) revision
`69cb8ef0651bf68124815682df4a0f8e8b57c141`. The hash-verified upstream
`ref_based_eval.py` is included under its MIT license; see
[`VIGA-LICENSE.txt`](VIGA-LICENSE.txt). The wrapper refuses evaluator bytes unless
SHA-256 is
`9ee08818aa858dccfff3efbb36cb9a98e8887f14e6168ad1a23731351cdeb3b5`.
CLIP is `openai/clip-vit-base-patch32` at revision
`3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`. Scoring reports CLIP **image
embedding cosine similarity**, N-CLIP (`1 - cosine`), and photometric loss.

## Public setup

1. Install exactly `codex-cli 0.154.0` (upstream source revision
   `6b9826e3aa83b1a5947db50f4332cb9c65f1b340`). The launcher ignores user
   configuration, uses a read-only Codex sandbox, and disables the 0.154.0
   built-in shell, image, dynamic/code-mode, app/plugin, browser, computer-use,
   and image-generation feature paths. MCP tools are forced onto the direct
   surface (`omit_tools_from = ["deferred", "code_mode"]`). Exactly the four
   listed Blender tools remain available; ordinary model reasoning is unchanged.
2. Install the MCP server and a GPU-capable standalone `bpy` Python. Record
   `bpy.app.version_string`. The runtime requires Cycles CUDA, enables CUDA
   devices only, disables CPU, and has no CPU-render fallback.
3. Copy `tools/direct_codex.example.toml` outside the repository and replace all
   absolute placeholders. It must enable exactly the four tools above and name
   the same bpy interpreter passed to the runner.
4. Create a separate exact CPython **3.13.15 x86_64 Linux / CUDA 12.8** scoring
   environment and install the complete hash-locked dependency set with
   `python -m pip install --require-hashes -r
   benchmarks/blenderbench_direct/requirements-scoring.lock`. The lock pins the
   `torch 2.8.0+cu128` CPython-3.13 wheel by immutable URL and hash and is the
   normative reproduction environment. Enter this venv and invoke every `run`,
   `recover`, and `score` command with that venv's `python`; external scorer
   executable/script options are intentionally not accepted. Before generation
   or scoring, an isolated child verifies lexical `sys.executable`, CPython and
   venv identity, x86_64/64-bit/SOABI, exact imported runtime versions, CUDA,
   and the exact 41-distribution lock (apart from allowed `pip`).

   Download the eight required CLIP files at the pinned revision into a dedicated
   snapshot directory. `clip-snapshot-manifest.json` is the exact path/size/SHA-256
   allowlist. `--clip-snapshot` is verified before and after each scorer process;
   offline/local-only loading rejects extras, missing files, byte changes, and
   symlinks outside a standard Hugging Face model cache root.

   **Security boundary:** the historical lock intentionally retains
   `setuptools 78.1.0` and `transformers 4.55.4` for exact environment fidelity.
   A 2026-09-11 `pip-audit` reports 16 findings across those two distributions;
   fixed versions would change the locked environment. The wrapper therefore
   accepts only the pinned CLIP revision from an offline cache, requests
   `weights_only=True`, and must not be used with untrusted model caches or
   checkpoints. Any dependency upgrade requires a separately hashed evaluation
   condition and numerical-equivalence validation.
5. Use external dataset and output directories. Keep credentials out of MCP TOML;
   public manifests omit MCP `args`, `env`, and `variables` and redact recognized
   secret-bearing keys and values recursively, but raw Codex evidence can still
   contain sensitive model output and must be reviewed before publication.

Download all 27 pinned tasks (or pass an explicit `--tasks` subset):

```sh
python -m benchmarks.blenderbench_direct.runner download \
  --dataset-root /external/blenderbench-data
```

Run all 27 tasks by omitting `--tasks`:

```sh
python -m benchmarks.blenderbench_direct.runner run \
  --dataset-root /external/blenderbench-data \
  --mcp-toml /external/blender-mcp.toml \
  --codex-binary /absolute/bin/codex \
  --codex-version 'EXACT CODEX --version OUTPUT' \
  --bpy-python /absolute/bpy-venv/bin/python \
  --bpy-version 'EXACT BPY VERSION PREFIX' \
  --clip-snapshot /external/clip-vit-base-patch32-snapshot \
  --output-root /external/fresh-run \
  --model 'PROVIDER/MODEL-ID' \
  --model-revision 'PROVIDER-REVISION-IF-KNOWN' \
  --rounds 10 --approve-blender-tools
```

An explicit subset uses the same fresh-run command plus, for example:

```sh
--tasks level1/camera1 level2/attribute1
```

`--rounds` defaults to 10 and accepts only 10. Every runtime path, requested
model, Codex pin, bpy pin, and CLIP snapshot is explicit. The output root must not
exist. Existing task output, checkpoint files, or run roots are never treated as
authorization to regenerate.

## Recovery and standalone scoring

Recovery is an explicit audit-and-scoring operation. It never calls Codex or
rewrites retained generation artifacts; it writes reopened-scene audit renders
and scores to a separate fresh output root. Before doing so it revalidates the
retained generation JSONL, attested checkpoint hashes, and ordered visual-image
evidence:

```sh
python -m benchmarks.blenderbench_direct.runner recover \
  --source-root /external/retained-run --output-root /external/fresh-recovery \
  --dataset-root /external/blenderbench-data \
  --bpy-python /absolute/bpy-venv/bin/python --bpy-version 'EXACT BPY VERSION PREFIX' \
  --clip-snapshot /external/clip-vit-base-patch32-snapshot
```

Standalone scoring also never generates. It admits only the
`auditNN/verified.png` images produced by independent saved-scene reopen and GPU
rendering whose `audit-admission.json` records successful meaningful-change
validation and whose source, render, and full audit hashes match the retained
audit metadata. Model-written checkpoint PNGs are not scoring inputs:

```sh
python -m benchmarks.blenderbench_direct.runner score \
  --run-root /external/retained-run \
  --dataset-root /external/blenderbench-data \
  --clip-snapshot /external/clip-vit-base-patch32-snapshot \
  --output /external/retained-run/scoring
```

`aggregate.json` and `aggregate.csv` include complete, failed, and missing tasks.
The JSON aggregate records the pinned dataset, evaluator, CLIP, and canonical
scorer digest. Every target is hash-verified before standalone scoring starts,
and every completed scorer output must repeat the exact evaluator and CLIP identity.
Best round is minimum N-CLIP; an exact tie selects the earliest round. Paired
photometric loss comes from that same round. Failures are not dropped from the
denominator.

## Canonical protocol and artifacts

`run-manifest.json` records resolved Codex/bpy/scorer versions, scorer SHA-256,
redacted effective config, exact tool list, dataset/evaluator/model revisions,
render and generation rules, initialization-manifest digest, implementation-file
digests, full Git revision and dirty state, and host/CPU/GPU/runtime provenance. Its
`protocol_sha256` is SHA-256 over canonical sorted UTF-8 JSON of the `protocol`
object; volatile hardware provenance is recorded but excluded from that hash.
The Git state and source digests are included: identical clean checkouts reproduce
the identity, while a dirty checkout is explicitly distinct and cannot claim clean
source provenance. Generation JSONL is checked after Codex exits and before any
audit or scoring; shell/file-change/builtin-tool operations, non-allowlisted MCP
calls, goal/solution access, and score/evaluator feedback fail the task closed.

Large datasets, raw model captures, `.blend` files, renders, scores, and videos
stay outside Git. Publish immutable artifacts separately and add only verified
metadata to a report, for example:

```json
{"url":"https://YOUR-ARTIFACT-HOST/path","sha256":"MEASURED-64-HEX-DIGEST"}
```

Do not replace placeholders with invented URLs or digests. Hash the uploaded
bytes, download by the public URL, hash again, and require equality before
claiming publication.

## Compilation video

Create a JSON spec with task-local `duration_seconds`, target/initial images,
model, optional final `task_tokens`, and round rows containing `round`,
`elapsed_seconds`, `image`, and `clip_image_cosine_similarity`. Then run:

```sh
python -m benchmarks.blenderbench_direct.runner video \
  --spec /external/video-spec.json --output /external/compilation.mp4 \
  --ffmpeg /absolute/bin/ffmpeg
```

Overlays say “CLIP image cosine similarity” exactly. Token displays say
“Task-level tokens (linear interpolation)” explicitly; they are not event-level
usage and remain unavailable when task usage is unavailable. A sidecar JSON
records every rendered frame.

## Limitations

- Codex's built-in `view_image` path remains disabled. Visual inspection is
  instead restricted to `get_render_as_image_for_cli`; post-run JSONL validation
  requires exactly one successful PNG image response for each sequential round
  checkpoint.
- MCP Python runs with server OS privileges; Codex's read-only sandbox does not
  sandbox the MCP server. Use a disposable isolated account/runtime.
- Version pins establish identity, not equivalence across hardware or providers.
- GPU admission and full runs require Blender/bpy, CUDA, model access, and CLIP
  weights; CI tests exercise only pure/fixture orchestration.
- Meaningful-change auditing detects repeated/nonfinite complete scene state, but
  it is not a semantic judge.
- The direct 10-round protocol is not demonstrated to have the same budget,
  interaction pattern, or judging procedure as VIGA. Do **not** describe results
  as a fair same-budget VIGA comparison.
