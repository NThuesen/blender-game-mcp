# Portable BlenderBench camera controller

Camera-only exploratory harness for the enhanced Blender MCP. Supported reviewed
inputs are `camera2` through `camera9`; this is not the full BlenderBench dataset.
**BlenderGym is unsupported. DGX Spark/Linux execution is unverified.** Linux
CUDA/OPTIX setup and CoW failure behavior have synthetic tests, not Spark hardware
validation. No VIGA agent or VLM judge is run. Published-agent equivalence and
sandbox isolation are not claimed.

Run commands from the repository root. All data, scene copies, evidence, scores
and videos must be outside the repository. Never commit downloaded assets,
weights, frozen runtime copies, credentials, or generated trial outputs.

## Environments and lockfile

The controller needs Python 3.11+ and Pillow. Use the checked-in local `uv.lock`:

```sh
uv lock --check --project benchmarks/blenderbench_camera
uv sync --frozen --project benchmarks/blenderbench_camera
CONTROL="$PWD/benchmarks/blenderbench_camera/.venv/bin/python"
"$CONTROL" -m benchmarks.blenderbench_camera --help
```

There are three distinct Python roles. `CONTROL` runs the harness and decodes
images. `SERVER_PYTHON` must have the MCP server dependencies from
`mcp/pyproject.toml` (notably **MCP 1.x**, PyYAML and docutils). `BPY_PYTHON`, if
selected, must contain a working GPU-capable standalone bpy build. Keep virtual
environment executable paths intact: do not apply `realpath`/symlink resolution
to their Python executables. MCP 2.x lacks `mcp.server.fastmcp` and is not a
compatible server environment.

The optional scoring dependencies are locked separately from platform-specific
Torch and bpy. `uv sync --frozen --extra score --project
benchmarks/blenderbench_camera` installs the former, not Torch. Install and verify
a platform-compatible PyTorch in a separate scoring environment, then install
the locked `score` dependencies there. Do not assume a generic PyPI bpy/Torch
wheel supports Spark's Linux aarch64 GPU. PL-only requires NumPy/Pillow, no Torch.
Tests that import the existing token-efficiency adapters also require the
repository chat-client dependencies, including MCP. A CLIP-only environment is
not automatically a complete test or MCP server environment.

## macOS and prospective Spark setup

Set actual absolute executable/cache paths before using the examples:

```sh
BLENDER=/absolute/path/to/blender
SERVER_PYTHON=/absolute/path/to/server-env/bin/python
BPY_PYTHON=/absolute/path/to/bpy-env/bin/python
DATA=/external/blenderbench-data
OUT=/external/fresh-camera-run
VIGA=/external/VIGA
GPU=METAL  # macOS; CUDA or OPTIX on Linux
```

On Spark, first obtain a native Linux aarch64 Blender build with Cycles support
for the installed GPU/driver and verify a real render using the command below.
Use `--runtime blender` initially; standalone bpy is optional and must be tested
separately with `--runtime bpy --bpy-python "$BPY_PYTHON"`. No CPU render fallback
is allowed. GPU setup is subprocess-local, applied after scene open; it preserves
engine, samples and resolution and does not save global preferences. Backend and
independent render deadlines are 600 seconds. A dual render smoke is sequential,
not a controlled speed comparison.

Generation requires a same-filesystem copy-on-write-capable volume: APFS
`clonefile` on macOS or Linux `FICLONE` (for example, a reflink-enabled filesystem).
Hardlinks and full-copy fallback are refused. Keep ample external disk space;
each round checks a conservative scene-size reserve. A storage or GPU failure is
a blocker, not permission to weaken the gate.

## Pinned inputs and evaluator

```sh
git clone https://github.com/Fugtemypt123/VIGA.git "$VIGA"
git -C "$VIGA" checkout 69cb8ef0651bf68124815682df4a0f8e8b57c141
"$CONTROL" -m benchmarks.blenderbench_camera verify-sources --viga "$VIGA"
"$CONTROL" -m benchmarks.blenderbench_camera download --dataset-dir "$DATA" --tasks camera4
"$CONTROL" -m benchmarks.blenderbench_camera initialize --dataset-dir "$DATA" --tasks camera4 --blender "$BLENDER" --gpu "$GPU"
```

Dataset: DietCoke4671/BlenderBench, revision
`203e4d325e9438ca55b29bdfc4f6a90842d74e68`, CC-BY-4.0; attribution belongs to the
dataset authors and original asset creators. The manifest selects only scene,
reviewed start script, task text and target image, never `goal.py`. Downloaded
Git/LFS bytes and the start script AST are verified. Preserve upstream notices.
The VIGA evaluator file and checkout revision are verified before executing only
its official metric functions; evaluator dependencies and licenses remain with
upstream. `verify-sources` verifies metadata, not downloaded asset availability.

## No-model render, score and report

```sh
"$CONTROL" -m benchmarks.blenderbench_camera render \
  --blend /external/saved/candidate.blend --output /external/fresh-render-smoke \
  --blender "$BLENDER" --gpu "$GPU" --runtime blender \
  --server-python "$SERVER_PYTHON"
```

This independently reopens/audits/renders the saved checkpoint, then renders it
through the frozen enhanced backend and checks that the source hash is unchanged.
Omit `--server-python` for independent Blender only. Inspect `audit.device.json`,
`backend-probe.json`, logs and both fully decoded PNG dimensions before admitting
a runtime. Outputs must be fresh; failed artifacts are retained, not overwritten.

For a saved run with `round-ledger.jsonl`, use a scoring interpreter with the
above dependencies. Full CLIP inference is local/offline after the explicit
weight download; it is not a paid model call:

```sh
SCORE_PYTHON=/absolute/path/to/scoring-env/bin/python
"$SCORE_PYTHON" -m benchmarks.blenderbench_camera cache-clip
"$SCORE_PYTHON" -m benchmarks.blenderbench_camera score \
  --root "$OUT/camera4" --input "$DATA/camera4" \
  --output /external/fresh-scores --viga "$VIGA"
"$CONTROL" -m benchmarks.blenderbench_camera report \
  --root "$OUT/camera4" --scores /external/fresh-scores/clip_scores.json \
  --output /external/fresh-report
```

Add `--pl-only` to score without CLIP; the filename becomes `pl_scores.json` and
no CLIP-selected checkpoint is claimed. Raw NCLIP is minimized; PL is paired with
the same selected checkpoint, earliest tie retained. Reports emit JSON, CSV and
Markdown for every budgeted round, preserving failed/unreached/unscored nulls.
Legacy saved ledgers support scoring/reporting, not automatic generation resume.

## Explicitly opted-in generation

This command makes paid native Codex model calls. Only run it after authorization,
CLI authentication and successful local runtime checks:

```sh
"$CONTROL" -m benchmarks.blenderbench_camera run \
  --dataset-dir "$DATA" --tasks camera4 --output "$OUT" \
  --blender "$BLENDER" --gpu "$GPU" --runtime blender \
  --server-python "$SERVER_PYTHON" --model YOUR_EXACT_MODEL_ID \
  --seconds 3600 --model-seconds 180 --max-tools 30 \
  --accept-unverified-isolation
```

Ten verified scene-changing rounds are enforced, with at most two edit attempts
per round. Render-only retries and no-op edits do not increment completed rounds.
Each candidate uses a fresh native model session with current and target images;
independent verification supplies feedback, not evaluator scores. This context
reset is a protocol change from a single-session published agent. `--max-tools`
is a reactive per-session observed MCP call limit, not a round budget.

For a portable interrupted run, use a fresh output parent and
`--resume-from /external/old-run/camera4` with the original model/runtime/GPU/task
budget. Sources/checkpoints are verified and prior active elapsed is subtracted;
downtime is explicitly excluded. Ambiguous launches or source/config drift block
resume. Old evidence is preserved.

Optional offline video (requires `ffmpeg` and `ffprobe` on PATH):

```sh
"$CONTROL" -m benchmarks.blenderbench_camera video \
  --root "$OUT/camera4" --input "$DATA/camera4" \
  --output /external/fresh-video --model YOUR_EXACT_MODEL_ID
```

The 15-second chronological TARGET/progress video labels exact observed tool
calls separately from `Tokens (interpolated)`. Best round and last round can differ.

## Verification scope

```sh
python -m unittest tests.test_blenderbench_camera tests.test_camera_round_report \
  tests.test_token_efficiency_codex tests.test_token_efficiency_runner \
  tests.test_token_efficiency_telemetry tests.test_token_efficiency_video_evidence \
  tests.test_token_efficiency_validators tests.test_token_efficiency_real_runtime -v
```

Use a dependency-complete test interpreter. Synthetic controller tests exercise
ten rounds without paid calls, but do not establish a ten-round live portable
run. Preserve declared PTY and real-runtime skips; report them separately.
Repository-wide integration tests have additional Blender/bpy/server requirements
and are not implied by these benchmark gates passing.
