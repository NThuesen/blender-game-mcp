# Camera3 mixed-scene compatibility and safe retry

Camera3's pinned input opens `Yoga studio scene`, whose engine is already CYCLES,
with Camera1, 512 samples, and 512x512 at 100%. A second inactive scene named
`Scene` uses BLENDER_EEVEE. The old runtime rejected that inactive scene before
rendering the active Cycles scene. Only the active scene now receives the CUDA
runtime-local GPU device override. Neither scene's engine changes, and inactive
scene device settings remain untouched. Full RNA audits still cover both scenes.
Active non-Cycles rendering remains unsupported and fails closed; this is not
an EEVEE/EGL implementation or a Cycles conversion.

Use an isolated copy of direct_runtime.py, direct_codex.py, direct_suite.py and
direct_camera_retry.py. Do not overwrite files imported by an active dispatcher.
Launch the retry script durably (detached session, retained stdout/stderr):

```
/path/to/custom-bpy/python -u /isolated/direct_camera_retry.py \
  --output /fresh/camera3-retry \
  --after-suite /existing/remaining-suite
```

The wrapper requires a normally finished predecessor event AND acquisition of
`/home/mg/blenderbench-direct/direct-suite.lock`. It holds that lock throughout
admission, ten direct Codex rounds, full independent audits and post-generation
GPU CLIP scoring. No VLM judge or feedback into generation is added. The exact
four approved MCP tools and existing deadlines/config checks are unchanged.
The original failed Camera3 directory remains evidence, never a resume target.

A predecessor crash does not authorize concurrent retry: the wrapper keeps
waiting without launching work. A STOP file in either the predecessor output or
retry output cancels the queue. After generation starts the existing suite's
STOP limitation applies: direct Codex notices cancellation after invoke returns.

Monitor `process.json`, `status.json`, `events.jsonl`, and retained process stdout.
Actual native admission evidence is in `level1-camera3/{work,admission,reopen}`:
initialization and independent render must produce parent-decoded PNGs; all three
full audits must agree and source/saved-scene hashes must remain unchanged.
A queue status of `waiting_for_suite` is NOT native GPU admission or completion.

Tests: `python -m unittest discover -s tests -p 'test_direct*.py' -v`.
The mixed-scene regression was observed failing at the old engine assertion
before the fix. Queue tests exercise real flock contention and a real waiting
subprocess with STOP cancellation. Synthetic tests are not GPU render evidence.
