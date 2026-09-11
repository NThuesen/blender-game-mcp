# BlenderBench direct suite

The former host-specific dispatcher has been replaced by the model-neutral,
full-27 runner in [`benchmarks/blenderbench_direct`](../benchmarks/blenderbench_direct/README.md).

Use:

```sh
python -m benchmarks.blenderbench_direct.runner --help
```

All dataset, MCP, Codex, bpy, scorer, output, model, and version inputs are
explicit. New generation always requires a fresh output root. Recovery and
scoring are separate commands and never regenerate missing rounds.
