"""CI-safe publication runner tests; no Blender, GPU, model, or network."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.blenderbench_direct import dataset, protocol, scoring, video
from benchmarks.blenderbench_direct.runner import build_parser, resolve_config


class DatasetManifestTests(unittest.TestCase):
    def test_task_path_components_may_not_be_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            data = root / "data"
            data.mkdir()
            (data / "level1").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "^symlink forbidden in dataset"):
                dataset.verify("level1/camera1", data)

    def test_verified_dataset_task_contains_only_input_assets_and_checkpoint(self):
        from unittest.mock import patch
        record = {name: {"size": 1, "sha256": "a" * 64}
                  for name in ("task.txt", "start.py", "scene.blend", "target.png")}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "level1/camera1"
            task.mkdir(parents=True)
            for name in record:
                (task / name).write_bytes(b"x")
            (task / "disguised_payload.py").write_text("pass")
            manifest = {"tasks": {"level1/camera1": {"assets": record}}}
            with patch.object(dataset, "load_manifest", return_value=manifest), \
                 patch.object(dataset, "_verify", return_value={"size": 1, "sha256": "a" * 64}):
                with self.assertRaisesRegex(ValueError, "unexpected path"):
                    dataset.verify("level1/camera1", root)

    def test_verified_dataset_rejects_contamination_outside_selected_task(self):
        from unittest.mock import patch
        record = {name: {"size": 1, "sha256": "a" * 64}
                  for name in ("task.txt", "start.py", "scene.blend", "target.png")}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); task = root / "level1/camera1"; task.mkdir(parents=True)
            for name in record: (task / name).write_bytes(b"x")
            (root / "obfuscated-payload.bin").write_bytes(b"hidden code")
            manifest = {"tasks": {"level1/camera1": {"assets": record}}}
            with patch.object(dataset, "load_manifest", return_value=manifest), \
                 patch.object(dataset, "_verify", return_value={}):
                with self.assertRaisesRegex(ValueError, "dataset root"):
                    dataset.verify("level1/camera1", root)

    def test_successful_resumed_download_removes_superseded_partial(self):
        import hashlib
        import io
        from unittest.mock import patch
        payload = b"complete-payload"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "asset.bin"
            retained = root / "asset.bin.retained.partial"
            retained.write_bytes(payload[:5])
            class Response(io.BytesIO):
                status = 206
                headers = {"Content-Range": f"bytes 5-{len(payload) - 1}/{len(payload)}"}
            record = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            with patch.object(dataset.urllib.request, "urlopen",
                              return_value=Response(payload[5:])):
                dataset._download("https://example.invalid/asset", destination, record)
            self.assertEqual(destination.read_bytes(), payload)
            self.assertFalse(retained.exists())

    def test_manifest_is_pinned_complete_and_has_no_edit_policy_fields(self):
        manifest = dataset.load_manifest()
        self.assertEqual(manifest["revision"], dataset.DATASET_REVISION)
        self.assertEqual(len(manifest["tasks"]), 27)
        self.assertEqual(dataset.task_ids(), sorted(manifest["tasks"]))
        forbidden = {"allowed_transforms", "allowed_camera_data", "allowed_light_data",
                     "allowed_shape_keys", "location_bounds", "bounds", "unsupported_blocker"}
        def keys(value):
            if isinstance(value, dict):
                yield from value
                for key, child in value.items():
                    yield key
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)
        self.assertFalse(forbidden.intersection(keys(manifest)))
        for task, record in manifest["tasks"].items():
            self.assertEqual(set(record["assets"]), {"task.txt", "start.py", "scene.blend", "target.png"})
            self.assertTrue(record["task_description"])
            self.assertEqual(len(record["start_sha256"]), 64)
            self.assertIsInstance(record["initialization"], list)
            self.assertNotIn("goal.py", json.dumps(record))

    def test_subset_selection_defaults_to_all_and_rejects_duplicates(self):
        all_tasks = dataset.task_ids()
        self.assertEqual(dataset.select_tasks(None), all_tasks)
        self.assertEqual(dataset.select_tasks([all_tasks[2], all_tasks[0]]), [all_tasks[2], all_tasks[0]])
        for invalid in [[], [all_tasks[0], all_tasks[0]], ["unknown/task"]]:
            with self.assertRaises(ValueError):
                dataset.select_tasks(invalid)


class RunnerConfigTests(unittest.TestCase):
    def test_external_scorer_options_are_not_accepted(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["score", "--run-root", "/run", "--dataset-root", "/data",
                               "--clip-snapshot", "/clip", "--scorer-python", "/fake",
                               "--scorer-python-sha256", "a" * 64, "--scorer-script", "/fake.py"])

    def required_argv(self, root: Path) -> list[str]:
        return ["run", "--dataset-root", str(root / "data"), "--mcp-toml", str(root / "mcp.toml"),
                "--codex-binary", str(root / "codex"), "--codex-version", "codex-cli 0.154.0",
                "--bpy-python", str(root / "bpy-python"), "--bpy-version", "5.2.1",
                "--clip-snapshot", str(root / "clip-snapshot"),
                "--output-root", str(root / "output"), "--model", "provider/model"]

    def test_all_runtime_paths_and_version_pins_are_explicit_and_rounds_frozen(self):
        parser = build_parser()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = parser.parse_args(self.required_argv(root))
            config = resolve_config(args, check_files=False)
        self.assertEqual(config.rounds, 10)
        self.assertEqual(config.model, "provider/model")
        self.assertEqual(config.codex_version, "codex-cli 0.154.0")
        self.assertEqual(config.bpy_version, "5.2.1")
        for field in ("dataset_root", "mcp_toml", "codex_binary", "bpy_python",
                      "clip_snapshot", "output_root"):
            self.assertTrue(getattr(config, field).is_absolute(), field)
        with self.assertRaises(SystemExit):
            parser.parse_args(self.required_argv(root) + ["--rounds", "9"])

    def test_generated_roots_must_stay_outside_git_repository(self):
        parser = build_parser()
        root = Path(__file__).resolve().parents[1]
        args = parser.parse_args(self.required_argv(root))
        with self.assertRaisesRegex(ValueError, "outside"):
            resolve_config(args, check_files=False)

    def test_recover_and_score_are_explicit_and_never_fall_back_to_generation(self):
        parser = build_parser()
        recover = parser.parse_args(["recover", "--source-root", "/old", "--output-root", "/recovery",
            "--dataset-root", "/data", "--bpy-python", "/bpy", "--bpy-version", "5.2.1",
            "--clip-snapshot", "/clip"])
        self.assertEqual(recover.command, "recover")
        self.assertEqual(recover.output_root, Path("/recovery"))
        self.assertEqual(parser.parse_args(["score", "--run-root", "/run", "--dataset-root", "/data",
                                            "--clip-snapshot", "/clip"]).command,
                         "score")

    def test_recovery_rejects_renderer_different_from_generation_manifest(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from benchmarks.blenderbench_direct import suite
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bpy = root / "bpy"; bpy.write_bytes(b"binary")
            args = SimpleNamespace(tasks=None, bpy_python=bpy, bpy_version="5.3.0",
                source_root=root / "source", output_root=root / "recovery",
                dataset_root=root / "dataset", clip_snapshot=root / "clip", rounds=10)
            manifest = {"protocol_sha256": "a" * 64, "protocol": {
                "source_identity": {}, "model": "m", "model_revision": None,
                "versions": {"bpy": "5.2.1"}}}
            with patch.object(dataset, "select_tasks", return_value=[]), \
                 patch.object(protocol, "load_run_manifest", return_value=manifest), \
                 patch.object(scoring, "preflight_scorer_runtime", return_value={}), \
                 patch.object(scoring, "verify_clip_snapshot", return_value={}), \
                 patch.object(suite, "_bpy_version", return_value="5.3.0"):
                with self.assertRaisesRegex(ValueError, "generation manifest"):
                    suite.recover_suite(args)
    def test_recovery_revalidates_retained_generation_events(self):
        from benchmarks.blenderbench_direct import suite
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "level1-camera1"
            work = task / "work"; work.mkdir(parents=True)
            (work / "iteration01.blend").write_bytes(b"checkpoint")
            codex = task / "codex"; codex.mkdir()
            (codex / "run.jsonl").write_text("\n".join(map(json.dumps, [
                {"type": "item.completed", "item": {"id": "edit", "type": "mcp_tool_call",
                    "server": "blender", "tool": "execute_blender_code_for_cli",
                    "status": "completed", "error": None, "result": {}}},
                {"type": "turn.completed"},
            ])))
            with self.assertRaisesRegex(ValueError, "visual inspection"):
                suite.verify_retained_generation(task, work, 1)


class ProtocolManifestTests(unittest.TestCase):
    def test_evaluator_url_resolves_to_verified_release_repository(self):
        self.assertEqual(protocol.EVALUATOR_REPOSITORY,
                         "https://github.com/Fugtemypt123/VIGA-release")
        source = (Path(__file__).resolve().parents[1] /
                  "benchmarks/blenderbench_direct/score_pair.py").read_text()
        self.assertIn("https://github.com/Fugtemypt123/VIGA-release/blob/", source)
        self.assertIn('VIGA_REVISION = "' + protocol.EVALUATOR_REVISION + '"', source)

    def test_pinned_evaluator_bytes_are_present_and_bound_into_source_identity(self):
        root = Path(__file__).resolve().parents[1]
        relative = "benchmarks/blenderbench_direct/ref_based_eval.py"
        evaluator = root / relative
        self.assertTrue(evaluator.is_file())
        self.assertFalse(evaluator.is_symlink())
        self.assertEqual(dataset.sha256(evaluator), protocol.EVALUATOR_SHA256)
        identity = protocol.collect_source_identity()
        self.assertEqual(identity["implementation_sha256"][relative], protocol.EVALUATOR_SHA256)

    def test_visual_inspection_tool_bytes_are_bound_into_source_identity(self):
        relative = "mcp/blmcp/tools/execute_blender_code.py"
        identity = protocol.collect_source_identity()
        self.assertIn(relative, identity["implementation_sha256"])
        self.assertEqual(identity["implementation_sha256"][relative],
                         dataset.sha256(Path(__file__).resolve().parents[1] / relative))

    def test_protocol_hash_is_deterministic_and_runtime_provenance_is_not_hashed(self):
        base = {"model": "provider/model", "rounds": 10, "mcp": {"token": "secret", "enabled_tools": list(protocol.TOOLS)}}
        first = protocol.build_manifest(base, dataset_revision="dataset-rev", evaluator_revision="eval-rev",
                                        model_revision="model-rev", versions={"codex": "1", "bpy": "2"},
                                        provenance={"host": "one"})
        second = protocol.build_manifest(base, dataset_revision="dataset-rev", evaluator_revision="eval-rev",
                                         model_revision="model-rev", versions={"codex": "1", "bpy": "2"},
                                         provenance={"host": "two"})
        self.assertEqual(first["protocol_sha256"], second["protocol_sha256"])
        self.assertEqual(first["protocol"]["tools"], list(protocol.TOOLS))
        self.assertEqual(first["config"]["mcp"]["token"], "<redacted>")
        self.assertNotIn("protocol_sha256", first["protocol"])

    def test_protocol_hash_binds_initialization_implementation_and_git_state(self):
        base = {"model": "provider/model", "rounds": 10}
        source = {"initialization_manifest_sha256": "a" * 64,
                  "implementation_sha256": {"suite.py": "b" * 64},
                  "git_revision": "c" * 40, "git_dirty": False}
        first = protocol.build_manifest(base, dataset_revision="dataset", evaluator_revision="eval",
            model_revision=None, versions={}, source_identity=source, provenance={})
        self.assertEqual(first["protocol"]["source_identity"], source)
        dirty = protocol.build_manifest(base, dataset_revision="dataset", evaluator_revision="eval",
            model_revision=None, versions={}, source_identity={**source, "git_dirty": True}, provenance={})
        changed = protocol.build_manifest(base, dataset_revision="dataset", evaluator_revision="eval",
            model_revision=None, versions={}, source_identity={**source,
                "implementation_sha256": {"suite.py": "d" * 64}}, provenance={})
        self.assertNotEqual(first["protocol_sha256"], dirty["protocol_sha256"])
        self.assertNotEqual(first["protocol_sha256"], changed["protocol_sha256"])

    def test_run_manifest_validation_rejects_rehashed_protocol_rule_drift(self):
        import copy
        source = protocol.collect_source_identity()
        source["git_dirty"] = False
        config = {"model": "provider/model", "model_revision": None, "rounds": 10,
                  "tasks": ["level1/camera1"], "bpy_version": "5.2.1"}
        versions = {"codex": protocol.CODEX_VERSION,
                    "codex_source_revision": protocol.CODEX_SOURCE_REVISION,
                    "bpy": "5.2.1", "scorer_python": "Python 3.13.15"}
        manifest = protocol.build_manifest(config, dataset_revision=dataset.DATASET_REVISION,
            evaluator_revision=protocol.EVALUATOR_REVISION, model_revision=None,
            versions=versions, source_identity=source, provenance={})
        protocol.validate_run_manifest(manifest, tasks=config["tasks"], rounds=10)
        mutations = [
            ("tools", ["shell"]),
            ("clip_revision", "wrong"),
            ("rendering", {"engine": "Cycles", "device": "CPU", "cpu_fallback": True}),
            ("generation", {**manifest["protocol"]["generation"],
                            "score_feedback_to_generation": True}),
            ("versions", {**versions, "codex": "codex-cli 999"}),
        ]
        for key, value in mutations:
            changed = copy.deepcopy(manifest)
            changed["protocol"][key] = value
            changed["protocol_sha256"] = protocol.digest(changed["protocol"])
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "frozen protocol"):
                protocol.validate_run_manifest(changed, tasks=config["tasks"], rounds=10)

    def test_source_identity_binds_hash_locked_scoring_environment(self):
        identity = protocol.collect_source_identity()
        root = Path(__file__).resolve().parents[1] / "benchmarks/blenderbench_direct"
        self.assertEqual(identity["scoring_lock_sha256"], dataset.sha256(root / "requirements-scoring.lock"))
        self.assertEqual(identity["assets_manifest_sha256"], dataset.sha256(root / "assets-manifest.json"))
        self.assertEqual(identity["assets_manifest"],
                         json.loads((root / "assets-manifest.json").read_text(encoding="utf-8")))
        self.assertEqual(identity["clip_snapshot_manifest_sha256"],
                         "90813610730d94e7b677552a4488a23ce3c2e2cd2ddfdd332756ea8e70db4ebb")
        self.assertEqual(identity["clip_snapshot_manifest"]["revision"], protocol.CLIP_REVISION)

    def test_public_config_omits_nested_args_env_variables_and_bearer_urls(self):
        config = {"mcp": {"mcp_servers": {"blender": {
            "args": ["--header", "Bearer SYNTHETIC-NOT-A-SECRET"],
            "env": {"GITHUB_PAT": "SYNTHETIC-PAT", "SAFE": [{"nested": "SYNTHETIC"}]},
            "variables": [{"CUSTOM_LOGIN": "https://user:SYNTHETIC@example.invalid/path"}],
            "command": "/public/bin/server"}}}}
        public = protocol.redact(config)
        encoded = json.dumps(public)
        for secret in ("SYNTHETIC-NOT-A-SECRET", "SYNTHETIC-PAT", "SYNTHETIC@example", "SYNTHETIC"):
            self.assertNotIn(secret, encoded)
        server = public["mcp"]["mcp_servers"]["blender"]
        self.assertEqual(server["command"], "/public/bin/server")
        self.assertEqual(server["args"], "<omitted:unsafe-runtime-config>")


class ScoringTests(unittest.TestCase):
    def write_run_manifest(self, root: Path, tasks=("level1/camera1",), rounds=10) -> dict:
        config = {"model": "gpt-6-astra", "model_revision": None,
                  "rounds": rounds, "tasks": list(tasks)}
        source = protocol.collect_source_identity()
        source["git_dirty"] = False
        manifest = protocol.build_manifest(config, dataset_revision=dataset.DATASET_REVISION,
            evaluator_revision=protocol.EVALUATOR_REVISION, model_revision=None,
            versions={"codex": "codex-cli 0.154.0", "bpy": "5.2.1",
                      "scorer_python": "Python 3.13.15"}, provenance={}, source_identity=source)
        protocol.write_manifest(root / "run-manifest.json", manifest)
        return manifest

    def scorer_payload(self, **changes):
        payload = {"clip_image_cosine_similarity": .5, "photometric_loss": .25,
            "clip_model": protocol.CLIP_MODEL, "clip_revision": protocol.CLIP_REVISION,
            "viga_revision": protocol.EVALUATOR_REVISION,
            "evaluator_sha256": protocol.EVALUATOR_SHA256, "device": "cuda:0",
            "torch_version": "2.8.0+cu128", "transformers_version": "4.55.4",
            "numpy_version": "2.4.6", "pillow_version": "12.3.0"}
        payload.update(changes)
        return payload

    def test_standalone_scoring_rejects_tampered_run_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self.write_run_manifest(root)
            manifest["protocol_sha256"] = "0" * 64
            protocol.write_manifest(root / "run-manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "protocol hash"):
                scoring.score_saved_run(root, root / "dataset", ["level1/camera1"], 10,
                                        root / "clip")

    def test_fake_path_executable_is_never_used_and_argv_is_canonical(self):
        import os, sys
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "python"
            sentinel = root / "executed"
            fake.write_text("#!/bin/sh\ntouch " + str(sentinel) + "\n")
            fake.chmod(0o755)
            image, target = root / "image.png", root / "target.png"
            image.write_bytes(b"candidate"); target.write_bytes(b"target")
            completed = SimpleNamespace(returncode=0, stdout=json.dumps(self.scorer_payload()), stderr="")
            with patch.dict(os.environ, {"PATH": str(root)}), \
                 patch.object(scoring, "verify_clip_snapshot", return_value={"verified": True}), \
                 patch.object(scoring.subprocess, "run", return_value=completed) as run:
                result = scoring.score_pair(image, target, root / "clip", {"preflight": True})
            argv = run.call_args.args[0]
            self.assertEqual(argv[:3], [str(Path(sys.executable).absolute()), "-I",
                                        str(scoring.CANONICAL_SCORER)])
            self.assertFalse(sentinel.exists())
            self.assertEqual(result["candidate_image_sha256"], dataset.sha256(image))
            self.assertEqual(result["target_image_sha256"], dataset.sha256(target))

    def test_clip_snapshot_manifest_rejects_extra_or_mutated_bytes(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); root = base / "snapshot"; root.mkdir()
            names = ["config.json", "merges.txt", "preprocessor_config.json", "pytorch_model.bin",
                     "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "vocab.json"]
            rows = []
            for name in names:
                payload = ("bytes-" + name).encode()
                (root / name).write_bytes(payload)
                rows.append({"path": name, "size": len(payload),
                             "sha256": __import__("hashlib").sha256(payload).hexdigest(), "symlink": False})
            manifest = base / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "repository": protocol.CLIP_MODEL,
                "revision": protocol.CLIP_REVISION, "files": rows}))
            with patch.object(scoring, "CLIP_SNAPSHOT_MANIFEST", manifest):
                self.assertEqual(len(scoring.verify_clip_snapshot(root)["files"]), 8)
                (root / "goal.py").write_text("pass")
                with self.assertRaisesRegex(ValueError, "allowlist"):
                    scoring.verify_clip_snapshot(root)
                (root / "goal.py").unlink()
                (root / "tokenizer.json").write_bytes(b"mutated")
                with self.assertRaisesRegex(ValueError, "bytes"):
                    scoring.verify_clip_snapshot(root)

    def test_score_pair_rejects_wrong_evaluator_or_clip_identity(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); image = root / "image"; target = root / "target"
            image.write_bytes(b"i"); target.write_bytes(b"t")
            completed = SimpleNamespace(returncode=0,
                stdout=json.dumps(self.scorer_payload(clip_revision="wrong")), stderr="")
            with patch.object(scoring, "verify_clip_snapshot", return_value={"verified": True}), \
                 patch.object(scoring.subprocess, "run", return_value=completed):
                with self.assertRaisesRegex(ValueError, "identity"):
                    scoring.score_pair(image, target, root / "clip", {"preflight": True})

    def test_runtime_preflight_rejects_wrong_python_and_extra_distribution(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        expected = dict(scoring._EXPECTED_RUNTIME)
        expected.update({"python_build": ["main", "Clang 22.1.3"],
            "lexical_executable": str(Path(sys.executable).absolute()),
            "resolved_executable": str(Path(sys.executable).resolve()),
            "prefix": str(Path(sys.executable).absolute().parent.parent), "base_prefix": "/base",
            "isolated": 1, "ignore_environment": 1, "no_user_site": 1, "safe_path": True,
            "cuda_available": True,
            "distributions": [[name, version] for name, version in scoring.locked_distributions().items()]})
        wrong = {**expected, "python_version": "3.13.14"}
        with patch.object(scoring.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(wrong), stderr="")):
            with self.assertRaisesRegex(ValueError, "3.13.15"):
                scoring.preflight_scorer_runtime()
        expected["distributions"].append(["unexpected", "1"])
        with patch.object(scoring.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(expected), stderr="")):
            with self.assertRaisesRegex(ValueError, "extras"):
                scoring.preflight_scorer_runtime()

    def test_standalone_scoring_verifies_dataset_before_reading_renders(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.write_run_manifest(root)
            with patch.object(scoring, "preflight_scorer_runtime", return_value={}), \
                 patch.object(scoring, "verify_clip_snapshot", return_value={}), \
                 patch.object(dataset, "verify", side_effect=ValueError("target hash mismatch")) as verify:
                with self.assertRaisesRegex(ValueError, "target hash mismatch"):
                    scoring.score_saved_run(root, root / "dataset", ["level1/camera1"], 10,
                                            root / "clip")
        verify.assert_called_once_with("level1/camera1", root / "dataset")

    def test_standalone_scoring_uses_independent_audit_render_not_model_png(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.write_run_manifest(root, rounds=1)
            task = root / "level1-camera1"
            (task / "work").mkdir(parents=True)
            (task / "work/iteration01.png").write_bytes(b"model-written")
            blend = task / "work/iteration01.blend"
            blend.write_bytes(b"independently-audited-blend")
            (task / "audit01").mkdir()
            audited = task / "audit01/verified.png"
            audited.write_bytes(b"independent-audit")
            audit_record = {"objects": {"Cube": {"location": [1, 0, 0]}}}
            (task / "audit01/metadata.json").write_text(json.dumps({
                "source": str(blend), "source_sha256": dataset.sha256(blend),
                "png": str(audited), "png_sha256": dataset.sha256(audited),
                "audit": audit_record, "runtime": {"returncode": 0},
                "evidence": {"blender_version": "5.2.1", "compute_device_type": "CUDA",
                    "scene_device": "GPU", "engine": "CYCLES",
                    "devices": [{"type": "CUDA", "use": True}]}}))
            (task / "audit-admission.json").write_text(json.dumps([{
                "round": 1, "source": str(blend), "source_sha256": dataset.sha256(blend),
                "image": str(audited), "image_sha256": dataset.sha256(audited),
                "audit_sha256": protocol.digest(audit_record),
                "meaningful_change_validated": True}]))
            target = root / "dataset/level1/camera1/target.png"
            target.parent.mkdir(parents=True); target.write_bytes(b"target")
            with patch.object(scoring, "preflight_scorer_runtime", return_value={}), \
                 patch.object(scoring, "verify_clip_snapshot", return_value={}), \
                 patch.object(dataset, "verify", return_value={"verified": True}), \
                 patch.object(scoring, "score_pair", return_value={
                     "clip_image_cosine_similarity": .5, "photometric_loss": .25}) as score:
                result = scoring.score_saved_run(root, root / "dataset", ["level1/camera1"], 1,
                                                 root / "clip")
            self.assertEqual(result["summary"]["completed_tasks"], 1)
            self.assertEqual(score.call_args.args[0], audited)

    def test_standalone_scoring_rejects_unverified_audit_image(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.write_run_manifest(root, rounds=1)
            audit = root / "level1-camera1/audit01"
            audit.mkdir(parents=True)
            (audit / "verified.png").write_bytes(b"unverified")
            with patch.object(scoring, "preflight_scorer_runtime", return_value={}), \
                 patch.object(scoring, "verify_clip_snapshot", return_value={}), \
                 patch.object(dataset, "verify", return_value={"verified": True}), \
                 patch.object(scoring, "score_pair", return_value={
                     "clip_image_cosine_similarity": .5, "photometric_loss": .25}) as score:
                result = scoring.score_saved_run(root, root / "dataset", ["level1/camera1"], 1,
                                                 root / "clip")
            self.assertEqual(result["summary"]["completed_tasks"], 0)
            score.assert_not_called()

    def test_standalone_scoring_rejects_admission_without_audit_metadata(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.write_run_manifest(root, rounds=1)
            task = root / "level1-camera1"
            blend = task / "work/iteration01.blend"
            blend.parent.mkdir(parents=True); blend.write_bytes(b"blend")
            audit = task / "audit01"; audit.mkdir()
            image = audit / "verified.png"; image.write_bytes(b"render")
            (task / "audit-admission.json").write_text(json.dumps([{
                "round": 1, "source": str(blend), "source_sha256": dataset.sha256(blend),
                "image": str(image), "image_sha256": dataset.sha256(image),
                "audit_sha256": "a" * 64, "meaningful_change_validated": True}]))
            target = root / "dataset/level1/camera1/target.png"
            target.parent.mkdir(parents=True); target.write_bytes(b"target")
            with patch.object(scoring, "preflight_scorer_runtime", return_value={}), \
                 patch.object(scoring, "verify_clip_snapshot", return_value={}), \
                 patch.object(dataset, "verify", return_value={"verified": True}), \
                 patch.object(scoring, "score_pair", return_value={
                     "clip_image_cosine_similarity": .5, "photometric_loss": .25}) as score:
                result = scoring.score_saved_run(root, root / "dataset", ["level1/camera1"], 1,
                                                 root / "clip")
            self.assertEqual(result["summary"]["completed_tasks"], 0)
            score.assert_not_called()

    def test_standalone_scoring_rejects_failed_or_non_cuda_audit_metadata(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.write_run_manifest(root, rounds=1)
            task = root / "level1-camera1"
            blend = task / "work/iteration01.blend"
            blend.parent.mkdir(parents=True); blend.write_bytes(b"blend")
            audit = task / "audit01"; audit.mkdir()
            image = audit / "verified.png"; image.write_bytes(b"render")
            audit_record = {"objects": {"Cube": {"location": [1, 0, 0]}}}
            (audit / "metadata.json").write_text(json.dumps({
                "source": str(blend), "source_sha256": dataset.sha256(blend),
                "png": str(image), "png_sha256": dataset.sha256(image), "audit": audit_record,
                "runtime": {"returncode": 0}, "evidence": {"blender_version": "5.2.1",
                    "compute_device_type": "CUDA", "scene_device": "GPU", "engine": "BLENDER_EEVEE_NEXT",
                    "devices": [{"type": "CUDA", "use": True}]}}))
            (task / "audit-admission.json").write_text(json.dumps([{
                "round": 1, "source": str(blend), "source_sha256": dataset.sha256(blend),
                "image": str(image), "image_sha256": dataset.sha256(image),
                "audit_sha256": protocol.digest(audit_record),
                "meaningful_change_validated": True}]))
            target = root / "dataset/level1/camera1/target.png"
            target.parent.mkdir(parents=True); target.write_bytes(b"target")
            with patch.object(scoring, "preflight_scorer_runtime", return_value={}), \
                 patch.object(scoring, "verify_clip_snapshot", return_value={}), \
                 patch.object(dataset, "verify", return_value={"verified": True}), \
                 patch.object(scoring, "score_pair", return_value={
                     "clip_image_cosine_similarity": .5, "photometric_loss": .25}) as score:
                result = scoring.score_saved_run(root, root / "dataset", ["level1/camera1"], 1,
                                                 root / "clip")
            self.assertEqual(result["summary"]["completed_tasks"], 0)
            score.assert_not_called()

    def test_standalone_aggregate_records_dataset_evaluator_and_scorer_provenance(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest = self.write_run_manifest(root, rounds=1)
            runtime = {"executable_sha256": "a" * 64, "scoring_lock_sha256":
                       manifest["protocol"]["source_identity"]["scoring_lock_sha256"]}
            snapshot = {"manifest_sha256": "b" * 64}
            with patch.object(scoring, "preflight_scorer_runtime", return_value=runtime), \
                 patch.object(scoring, "verify_clip_snapshot", return_value=snapshot), \
                 patch.object(dataset, "verify", return_value={"verified": True}):
                result = scoring.score_saved_run(root, root / "dataset", ["level1/camera1"], 1,
                                                 root / "clip")
        provenance = result["provenance"]
        self.assertEqual(provenance["dataset_revision"], dataset.DATASET_REVISION)
        self.assertEqual(provenance["evaluator_revision"], protocol.EVALUATOR_REVISION)
        self.assertEqual(provenance["evaluator_sha256"], protocol.EVALUATOR_SHA256)
        self.assertEqual(provenance["clip_revision"], protocol.CLIP_REVISION)
        self.assertEqual(provenance["clip_snapshot"], snapshot)
        self.assertEqual(provenance["scorer_sha256"], dataset.sha256(scoring.CANONICAL_SCORER))
        self.assertEqual(provenance["protocol_sha256"], manifest["protocol_sha256"])
        self.assertEqual(provenance["source_identity"], manifest["protocol"]["source_identity"])
        self.assertEqual(provenance["model"], "gpt-6-astra")
        self.assertEqual(provenance["versions"]["codex"], "codex-cli 0.154.0")
        self.assertEqual(provenance["scorer_runtime"], runtime)

    def test_aggregate_uses_nclip_and_earliest_round_ties_and_records_failures(self):
        rows = [
            {"task": "level1/camera1", "round": 1, "status": "complete", "clip_image_cosine_similarity": .8, "photometric_loss": .4},
            {"task": "level1/camera1", "round": 2, "status": "complete", "clip_image_cosine_similarity": .8, "photometric_loss": .2},
            {"task": "level1/camera2", "round": 1, "status": "failed", "error": "render failed"},
        ]
        result = scoring.aggregate(rows, expected_tasks=["level1/camera1", "level1/camera2", "level1/camera3"])
        best = result["tasks"][0]["best_round"]
        self.assertEqual(best["round"], 1)
        self.assertAlmostEqual(best["nclip"], .2)
        self.assertEqual(result["summary"]["completed_tasks"], 1)
        self.assertEqual(result["summary"]["failed_tasks"], 2)
        self.assertAlmostEqual(result["summary"]["mean_best_nclip_completed_tasks"], .2)
        self.assertAlmostEqual(result["summary"]["mean_best_photometric_loss_completed_tasks"], .4)
        self.assertEqual(result["tasks"][2]["status"], "missing")

    def test_csv_flattens_generation_and_scorer_runtime_provenance(self):
        provenance = {"protocol_sha256": "a" * 64, "model": "gpt-6-astra",
            "model_revision": None, "source_identity": {"git_revision": "b" * 40,
                "scoring_lock_sha256": "c" * 64},
            "versions": {"codex": "codex-cli 0.154.0", "bpy": "5.2.1"},
            "scorer_runtime": {"executable_sha256": "d" * 64,
                "implementation": "CPython", "python_version": "3.11.16"}}
        result = scoring.aggregate([], ["t"], provenance=provenance)
        with tempfile.TemporaryDirectory() as tmp:
            _, csv_path = scoring.write_outputs(result, Path(tmp))
            with csv_path.open(newline="") as stream:
                row = next(csv.DictReader(stream))
        self.assertEqual(row["protocol_sha256"], "a" * 64)
        self.assertEqual(row["scoring_lock_sha256"], "c" * 64)
        self.assertEqual(row["scorer_python_sha256"], "d" * 64)
        self.assertEqual(row["codex_version"], "codex-cli 0.154.0")
        self.assertEqual(row["bpy_version"], "5.2.1")

    def test_json_and_csv_outputs_are_machine_readable(self):
        provenance = {"dataset_revision": "dataset", "evaluator_revision": "evaluator",
                      "evaluator_sha256": "a" * 64, "clip_revision": "clip",
                      "scorer_sha256": "b" * 64}
        result = scoring.aggregate([{"task": "t", "round": 1, "status": "complete",
                                     "clip_image_cosine_similarity": .75, "photometric_loss": .5}],
                                   ["t"], provenance=provenance)
        with tempfile.TemporaryDirectory() as tmp:
            json_path, csv_path = scoring.write_outputs(result, Path(tmp))
            self.assertEqual(json.loads(json_path.read_text())["summary"]["completed_tasks"], 1)
            with csv_path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["best_round"], "1")
            self.assertEqual(rows[0]["best_nclip"], "0.25")
            for key, value in provenance.items():
                self.assertEqual(rows[0][key], value)


class SuiteProtocolTests(unittest.TestCase):
    def test_ci_uses_immutable_actions_python_and_hashed_scoring_lock(self):
        import re
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/blenderbench-portable.yml").read_text()
        refs = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)
        self.assertTrue(refs)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in refs))
        self.assertIn('runs-on: ubuntu-24.04', workflow)
        self.assertIn('python-version: "3.13"', workflow)
        self.assertIn('--require-hashes -r benchmarks/blenderbench_direct/requirements-fixture.lock', workflow)
        self.assertNotIn('requirements-scoring.lock', workflow)
        source = (root / "benchmarks/blenderbench_direct/requirements-scoring.in").read_text()
        self.assertNotIn("--extra-index-url", source)
        self.assertIn(
            "torch @ https://download.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp313-cp313-manylinux_2_28_x86_64.whl",
            source,
        )
        lock = (root / "benchmarks/blenderbench_direct/requirements-scoring.lock").read_text()
        for package in ("numpy", "pillow", "transformers",
                        "nvidia-cuda-nvrtc-cu12", "nvidia-cudnn-cu12", "triton"):
            self.assertRegex(lock, rf"(?m)^{package}==[^\s]+.*\\\n(?:\s+--hash=sha256:[0-9a-f]{{64}})")
        self.assertRegex(lock, r"(?m)^torch @ https://download\.pytorch\.org/[^\s]+.*\\\n(?:\s+--hash=sha256:[0-9a-f]{64})")
        self.assertIn("requests==2.34.2", lock)
        self.assertIn("setuptools==78.1.0", lock)
        self.assertIn("numpy==2.4.6", lock)
        self.assertIn("transformers==4.55.4", lock)
        self.assertIn("tqdm==4.70.0", lock)
        self.assertIn("urllib3==2.7.0", lock)

    def test_publication_result_manifests_are_small_complete_and_path_free(self):
        root = Path(__file__).resolve().parents[1] / "benchmarks/blenderbench_direct/publication"
        expected = {
            "results-summary.json": "073be2c44f1a763b66dea5138eda1b8959e8553fef6ce288f1d78ee92807d6e8",
            "reference-results.json": "1a50fc5ab89acf56bed4db49ec435a6bf1be866ba0213f1869a68891ba24cc0e",
            "artifact-manifest.json": "087f17b1c953373d80aedb6287a1ba79363e9ebf4b232c374bed5fb11a29c978",
        }
        for name, digest in expected.items():
            self.assertEqual(dataset.sha256(root / name), digest)
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("/home/", text)
            self.assertNotRegex(text, r"(?:token|password|secret|credential)\s*[:=]")
        summary = json.loads((root / "results-summary.json").read_text())
        reference = json.loads((root / "reference-results.json").read_text())
        artifacts = json.loads((root / "artifact-manifest.json").read_text())
        self.assertEqual((summary["task_count"], summary["round_count"]), (27, 270))
        self.assertEqual(len(reference["rows"]), 27)
        self.assertEqual(reference["experiment"]["scored_rounds"], 270)
        self.assertEqual(len(reference["scoring_runtime"]["packages"]), 41)
        self.assertEqual(reference["video"]["sha256"],
                         "04966320de680203baed6c01c3e211059350454139dc8a802c557cce24815c17")
        self.assertEqual(len(artifacts["tasks"]), 27)
        self.assertEqual(sum(len(task["rounds"]) for task in artifacts["tasks"].values()), 270)
        self.assertTrue(all(round_["blend_bytes"] > 0 for task in artifacts["tasks"].values()
                            for round_ in task["rounds"]))
        self.assertEqual(artifacts["compilation_video"]["sha256"],
                         "04966320de680203baed6c01c3e211059350454139dc8a802c557cce24815c17")

    def test_root_readme_pins_latest_video_and_metric_meaning(self):
        text = (Path(__file__).resolve().parents[1] / "readme.md").read_text(encoding="utf-8")
        immutable = ("https://huggingface.co/datasets/michaelgold/blenderbench-direct-results/resolve/"
                     "7c2c43be4517aee4d76d2b445578988de186970c/video/"
                     "blenderbench-complete-compilation.mp4")
        self.assertIn(f"[![BlenderBench result](https://bpy.dev/assets/blenderbench-result.webp)]({immutable})", text)
        self.assertIn("27 tasks / 270 rounds", text)
        self.assertIn("CLIP image-embedding cosine similarity, not literal accuracy", text)
        self.assertIn("04966320de680203baed6c01c3e211059350454139dc8a802c557cce24815c17", text)

    def test_generation_prompt_freezes_public_protocol_without_edit_bounds(self):
        from benchmarks.blenderbench_direct.suite import generation_prompt
        prompt = generation_prompt("task text", Path("/work").absolute(), 10)
        self.assertIn("exactly 10 meaningful", prompt)
        self.assertIn("Call get_render_as_image_for_cli after each saved round", prompt)
        self.assertIn("expected_output_blend", prompt)
        self.assertIn("No CLIP/scoring", prompt)
        self.assertIn("goal_code", prompt)
        for tool in protocol.TOOLS:
            self.assertIn(tool, prompt)
        for obsolete in ("allowed_transforms", "location_bounds", "allowed edit"):
            self.assertNotIn(obsolete, prompt)

    def test_committed_benchmark_runtime_and_docs_have_no_private_home_or_model_default(self):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        tracked = subprocess.check_output(["git", "ls-files", "benchmarks", "tools"],
                                          cwd=root, text=True).splitlines()
        untracked = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "benchmarks", "tools"],
            cwd=root, text=True).splitlines()
        files = sorted(set(tracked + untracked))
        offenders = {}
        for relative in files:
            path = root / relative
            if (relative.startswith("benchmarks/blenderbench_direct/publication/")
                    or not relative.endswith((".py", ".md", ".rst", ".toml", ".json"))
                    or not path.exists()):
                continue
            text = path.read_text(encoding="utf-8")
            found = [needle for needle in ("/home/mg", "gpt-6-astra", "configured-model",
                                           "configured-codex", "configured-bpy", "configured-version")
                     if needle in text]
            if found:
                offenders[relative] = found
        self.assertEqual(offenders, {})


class VideoTests(unittest.TestCase):
    def test_overlay_labels_metric_and_task_level_token_interpolation_explicitly(self):
        state = video.timeline_state(5.0, duration=10.0, rounds=[
            {"round": 1, "elapsed_seconds": 2.0, "image": "one.png", "clip_image_cosine_similarity": .5},
            {"round": 2, "elapsed_seconds": 8.0, "image": "two.png", "clip_image_cosine_similarity": .8},
        ], task_tokens=1000)
        self.assertEqual(state["round"], 1)
        self.assertEqual(state["task_tokens_interpolated"], 500)
        labels = video.overlay_labels("provider/model", "level1/camera1", state, 10)
        self.assertIn("CLIP image cosine similarity: 0.500000", labels)
        self.assertIn("Task-level tokens (linear interpolation): ~500", labels)
        unavailable = video.timeline_state(1, duration=10, rounds=[], task_tokens=None)
        self.assertIn("unavailable", video.overlay_labels("m", "t", unavailable, 10))


if __name__ == "__main__":
    unittest.main()
