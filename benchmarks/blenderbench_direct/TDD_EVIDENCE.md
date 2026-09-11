# Publication-readiness TDD evidence

All commands ran from the repository root. Goal/solution code and benchmark generation were not executed.

| Behavior | RED (before production patch) | GREEN (after production patch) |
|---|---|---|
| Protocol source identity | `python -m unittest -v tests.test_blenderbench_publication.ProtocolManifestTests.test_protocol_hash_binds_initialization_implementation_and_git_state` → `TypeError: build_manifest() got an unexpected keyword argument 'source_identity'` | same command → `Ran 1 test ... OK` |
| Scoring-lock protocol binding | `...ProtocolManifestTests.test_source_identity_binds_hash_locked_scoring_environment` → `KeyError: 'scoring_lock_sha256'` | same command → `Ran 1 test ... OK` |
| Evaluator/CLIP output identity | `...ScoringTests.test_score_pair_rejects_wrong_evaluator_or_clip_identity` → `AssertionError: ValueError not raised` | same command → `Ran 1 test ... OK` |
| Standalone target verification | `...ScoringTests.test_standalone_scoring_verifies_dataset_before_reading_renders` → `AssertionError: ValueError not raised` | same command → `Ran 1 test ... OK` |
| Aggregate provenance | `...ScoringTests.test_standalone_aggregate_records_dataset_evaluator_and_scorer_provenance` → `KeyError: 'provenance'` | same command → `Ran 1 test ... OK` |
| CSV aggregate provenance | `...ScoringTests.test_json_and_csv_outputs_are_machine_readable` → `KeyError: 'dataset_revision'` | same command → `Ran 1 test ... OK` |
| Generation event enforcement | `python -m unittest -v tests.test_direct_codex.DirectCodexTests.test_generation_stream_fails_closed_on_nonapproved_operations_and_feedback` → `AttributeError: ... no attribute 'verify_generation_events'` | same command → `Ran 1 test ... OK` |
| Evaluator URL | `...ProtocolManifestTests.test_evaluator_url_resolves_to_verified_release_repository` → obsolete repository URL assertion failure | corrected test after eliminating an over-structural concatenated-literal assertion → `Ran 1 test ... OK` |
| Vendored evaluator bytes | `...ProtocolManifestTests.test_pinned_evaluator_bytes_are_present_and_bound_into_source_identity` → `AssertionError: False is not true` because `ref_based_eval.py` was absent | same command after adding the hash-verified upstream file and binding it into source identity → `Ran 1 test ... OK` |
| Recursive public redaction | `...ProtocolManifestTests.test_public_config_omits_nested_args_env_variables_and_bearer_urls` → synthetic bearer value remained in serialized output | same command → `Ran 1 test ... OK` |
| Dataset task-path symlink | `...DatasetManifestTests.test_task_path_components_may_not_be_symlinks` → verifier reached an asset through the symlink instead of rejecting the path component | same command → `Ran 1 test ... OK` |
| Broken legacy hooks | `python -m unittest -v tests.test_direct_recovery.RecoveryTests.test_broken_legacy_recovery_hooks_are_removed` → `hasattr(..., 'score_checkpoints')` / later `hasattr(..., 'run_task')` was true | same command → `Ran 1 test ... OK` |
| Immutable CI/lock | `...SuiteProtocolTests.test_ci_uses_immutable_actions_python_and_hashed_scoring_lock` → mutable action refs; after first patch the required lock command was split differently than asserted | exact full-SHA actions, CPython 3.13 CI selection, fixed runner image, and hash lock → `Ran 1 test ... OK` |
| Lexical scorer executable | `...ScoringTests.test_fake_path_executable_is_never_used_and_argv_is_canonical` → a PATH-selected executable remained possible | same command → `Ran 1 test ... OK` |
| Exact scorer runtime and distribution lock | `...ScoringTests.test_runtime_preflight_rejects_wrong_python_and_extra_distribution` → mismatched Python and extra distributions were not rejected | same command → `Ran 1 test ... OK` |
| Immutable CLIP snapshot | `...ScoringTests.test_clip_snapshot_manifest_rejects_extra_or_mutated_bytes` → extra and modified snapshot files were accepted | same command → `Ran 1 test ... OK` |
| Publication manifests | `...SuiteProtocolTests.test_publication_result_manifests_are_small_complete_and_path_free` → assertions failed against the older copied manifest bytes | same command → `Ran 1 test ... OK` |
| Root README result link | `...SuiteProtocolTests.test_root_readme_pins_latest_video_and_metric_meaning` → the clickable immutable video link and metric clarification were absent | same command → `Ran 1 test ... OK` |
| Package-safe runtime import | `...DirectCodexTests.test_generation_workdir_accepts_canonical_worker_when_imported_as_tools_module` → `ModuleNotFoundError: No module named 'direct_runtime'` | same command → `Ran 1 test ... OK` |
| Frozen manifest validation | `...ProtocolManifestTests.test_run_manifest_validation_rejects_rehashed_protocol_rule_drift` → five subtests accepted rehashed drift in tools, CLIP, rendering, generation, and Codex version | same command → `Ran 1 test ... OK` |
| Resumed download cleanup | `...DatasetManifestTests.test_successful_resumed_download_removes_superseded_partial` → retained partial still existed after successful installation | same command → `Ran 1 test ... OK` |
| Standalone audited-render scoring | `...ScoringTests.test_standalone_scoring_uses_independent_audit_render_not_model_png` → scorer received `work/iteration01.png` instead of `audit01/verified.png` | same command → `Ran 1 test ... OK` |
| Standalone audit admission | `...ScoringTests.test_standalone_scoring_rejects_unverified_audit_image` → an unverified `audit01/verified.png` produced a completed score | same command → `Ran 1 test ... OK` |
| Recovery renderer identity | `...RunnerConfigTests.test_recovery_rejects_renderer_different_from_generation_manifest` → recovery accepted a different bpy version when the new CLI pin matched it | same command → `Ran 1 test ... OK` |
| Audit metadata binding | `...ScoringTests.test_standalone_scoring_rejects_admission_without_audit_metadata` → a render with a hand-written admission but no retained audit metadata produced a completed score | same command after requiring matching source/render/full-audit hashes and explicit meaningful-change admission → `Ran 1 test ... OK` |
| MCP image inspection | `...TestRunBlenderCLI.test_cli_render_tool_returns_image_content` → `get_render_as_image_for_cli` was absent | same command after adding the PNG-returning saved-scene render tool → `Ran 1 test ... OK` |
| Per-round visual evidence | `...DirectCodexTests.test_generation_stream_requires_one_successful_image_inspection_per_round` → `verify_generation_events()` did not accept or enforce a round count; `...test_generation_stream_binds_visual_inspection_to_each_round_checkpoint` then accepted the wrong checkpoint sequence | both commands after requiring one successful PNG response for each ordered `iterationNN.blend` → `Ran 1 test ... OK` |
| Visual-tool source binding | `...ProtocolManifestTests.test_visual_inspection_tool_bytes_are_bound_into_source_identity` → the new MCP tool implementation was absent from `implementation_sha256` | same command after binding the MCP tool and CLI backend bytes → `Ran 1 test ... OK` |
| Blender resolution fields | `...DirectCodexTests.test_generation_stream_allows_blender_resolution_properties` → the `solution` filter falsely rejected `resolution_x` | same command after matching `solution` only as a complete prohibited word/file stem → `Ran 1 test ... OK` |
| Visual checkpoint byte binding | `...DirectCodexTests.test_generation_stream_rejects_visual_inspection_from_wrong_directory` → validation accepted an identically named checkpoint from another directory | same command after requiring exact ordered paths, source SHA-256 metadata, retained-byte equality, and edit/inspection alternation → `Ran 1 test ... OK` |
| Audit runtime admission | `...ScoringTests.test_standalone_scoring_rejects_failed_or_non_cuda_audit_metadata` → matching source/render/audit hashes could pass despite failed non-CUDA runtime evidence | same command after requiring successful process, exact generation renderer identity, CUDA-only devices, and GPU scene evidence → `Ran 1 test ... OK` |
| Recovery generation evidence | `...RunnerConfigTests.test_recovery_revalidates_retained_generation_events` → retained generation JSONL had no recovery validation entrypoint | same command after recovery revalidates the regular retained event stream, visual evidence, exact checkpoint paths, and source hashes before audit/scoring → `Ran 1 test ... OK` |
| Exact CUDA visual render | Additional assertions in `...TestRunBlenderCLI.test_cli_render_tool_returns_image_content` → the tool rendered a synchronized unsaved copy and lacked CUDA-only setup | same command after rendering the exact hashed checkpoint bytes with explicit CUDA-only Cycles setup → `Ran 1 test ... OK` |
| Fresh checkpoint attestation | `...TestRunBlenderCLI.test_execute_code_cli_attests_fresh_checkpoint_creation` → `expected_output_blend` was unsupported; `...DirectCodexTests.test_generation_stream_rejects_unattested_precomputed_checkpoint` then accepted precomputed bytes plus a no-op | both commands after server-side fresh-output/source/output hashing and event-level attestation validation → `Ran 1 test ... OK` |
| Cycles audit admission | The EEVEE case in `...ScoringTests.test_standalone_scoring_rejects_failed_or_non_cuda_audit_metadata` → matching hashes and CUDA preference fields were accepted without checking the engine | same command after requiring `engine == "CYCLES"` → `Ran 1 test ... OK` |
| Edit/inspection dispatch ordering | `...DirectCodexTests.test_generation_stream_rejects_next_edit_dispatched_before_prior_inspection` → an interleaved second edit's `item.started` event was accepted before the first edit had completed and been visually inspected | same command after tracking the active edit from dispatch through completion and rejecting any overlapping or pre-inspection edit → `Ran 1 test ... OK` |

Final direct/publication gate: 76 tests, all passed. The related MCP CLI/tool-listing gate: 41 tests, all passed.

## Verification notes

- Static parse: all 22 changed Python files parsed; all 6 benchmark JSON files decoded.
- Manifest validation: 27 tasks, 108 pinned assets, 11 implementation hashes, deterministic repeated dirty-tree identity.
- The final source identity reported revision `4a9f5cc6e616080185252bb919fa3d1e730760b7` and `git_dirty: true`, as required for this uncommitted verification worktree.
- Scoring lock TDD: the focused CI-lock test first failed because the ARM-generated lock omitted CUDA dependencies, then failed again while it still selected stale HTTP packages. It passed after switching to the historical hash-pinned `torch 2.8.0+cu128` x86_64 wheel, pinning the observed transitive versions, and regenerating the lock for CPython 3.13 / x86_64 manylinux.
- Scoring lock verification: `uv pip install --dry-run --system --python-platform x86_64-manylinux_2_28 --python-version 3.13 --require-hashes` resolved all 41 locked packages, including CUDA 12.8 dependencies, successfully. A fresh `pip-audit` reports 16 findings across fidelity-pinned `setuptools 78.1.0` and `transformers 4.55.4`; the README records the offline pinned-model trust boundary and treats any dependency upgrade as a separately hashed condition.
- Full repository suite with `mcp/.venv/bin/python -m unittest discover -s tests -p 'test*.py' -q`: 435 tests ran; 8 errors and 23 skips. The exact environment-only errors were the three missing-Blender `setUpClass` failures in `TestBackgroundServer`, `TestForegroundServer`, and `TestInteractiveServer`, plus missing-Pillow failures in `CameraTests.test_video_calls_tokens`, three `DirectCodexTests` checkpoint tests, and `test_token_efficiency_video_evidence`. The known baseline was 403 tests with 1 failure, 7 errors, and 23 skips: the same three missing-Blender blockers plus five Pillow-dependent outcomes (four errors and one dependent assertion failure). There were no current assertion failures and no focused publication failure.
- The root README poster and immutable Hugging Face URL were fetched successfully. The downloaded video was H.264, 848×464, 134.900 seconds, 10,161,884 bytes, and SHA-256 `04966320de680203baed6c01c3e211059350454139dc8a802c557cce24815c17`.

## Review state

The prior `REQUEST_CHANGES` findings were addressed without running benchmark
generation: visual inspection now has a hash-bound MCP image-returning path with
ordered per-round evidence validation, standalone scoring validates retained
audit metadata and admission hashes, and recovery requires the renderer version
recorded by the generation manifest. The final independent re-review returned
`APPROVED`: overlapping edit dispatch and edits pending checkpoint inspection
are rejected, and no Critical or Important regressions were found.
