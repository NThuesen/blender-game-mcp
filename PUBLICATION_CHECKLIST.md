# Publication Checklist

Use this checklist before making the enhanced MCP repository public or linking it from `bpy.dev`.

## Legal and provenance

- [x] Add repository license file: `LICENSE`.
- [x] Add attribution/provenance notice: `NOTICE.md`.
- [x] Confirm upstream SPDX license markers: GPL-3.0-or-later.
- [ ] Decide final public name/package namespace (`bpy-dev-mcp`, `bpydev-mcp`, or another name).
- [ ] Decide whether this repo remains a fork or is imported into a clean public repo.

## Repo hygiene

- [x] Ignore local Hermes planning/session files via `/.hermes/`.
- [x] Ignore common build/cache artifacts.
- [x] Confirm no generated trial media or temporary outputs are tracked.
- [x] Confirm no obvious credentials, tokens, private keys, or private paths are tracked by the publication-prep scan. Remaining API-key references are environment-variable examples/tests, not literal secrets.
- [x] Confirm tracked file sizes are ordinary source/docs/fixture sizes; largest tracked files are bundled API docs and small deterministic `.blend` fixtures.

## Product docs

- [x] Add security model: `SECURITY.md`.
- [x] Add developer-preview overview to `readme.md`.
- [x] Add backend documentation: `readme_bpy_backend.rst`.
- [x] Document benchmark limitations in `benchmarks/token_efficiency/README.md`.
- [ ] Add final package install commands after package naming is decided.
- [ ] Add public `bpy.dev` link after the website repo is live.

## Functional gates

- [x] Install/help smoke via `uv run --directory mcp blender-mcp --help`.
- [x] Run saved-file/runtime validator smoke with Blender executable backend.
- [x] Run saved-file/runtime validator smoke with standalone `bpy` backend.
- [x] Run runtime API docs smoke through the unit/integration suite.
- [x] Run deterministic benchmark validator tests.
- [x] Run tracked-file secret scan.
- [x] Run `git diff --check`.

## Benchmark/demo artifacts

- [ ] Copy Trial 017 videos into the website repo or CDN/object storage.
- [ ] Publish Trial 017 only as an exploratory case study.
- [ ] Keep deterministic benchmark results separate from creative/VLM demo results.
- [ ] Do not claim definitive superiority from a single creative trial.

## Release notes draft

Developer preview focus:

- saved-file/headless Blender MCP execution;
- standalone `bpy` backend option;
- runtime Blender Python API lookup;
- subprocess/capture/transport hardening;
- deterministic benchmark tasks and telemetry scaffolding;
- exploratory creative comparison tooling.
