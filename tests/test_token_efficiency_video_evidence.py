"""SYNTHETIC offline evidence tests. No model, Blender, network or dataset calls."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from benchmarks.token_efficiency.codex_runner import capture, ExecEvents, CodexSubprocessAdapter
from benchmarks.token_efficiency.creative_smoke import TimedExecEvents
from benchmarks.token_efficiency.creative_video import (
    build, collect_trial, frame_state, overlay_token_line, render, token_total,
)
from benchmarks.token_efficiency.video_evidence import VideoEvidence, interpolated_total, tool_timeline


def png(color="red"):
    image = Image.new("RGB", (320, 180), color)
    ImageDraw.Draw(image).text((12, 70), "SYNTHETIC - NOT BENCHMARK EVIDENCE", fill="white")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def event(kind="item.completed", ident="SYNTHETIC-call", **changes):
    item = dict(id=ident, type="mcp_tool_call", server="blender", tool="allowed",
                status="completed", result=None, error=None)
    item.update(changes)
    return dict(type=kind, item=item)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="SYNTHETIC-video-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        (self.workspace / "output").mkdir(parents=True)
        self.now = 0.0

    def evidence(self, **kwargs):
        return VideoEvidence(self.workspace, self.root / "control", identity={"synthetic": True},
                             monotonic=lambda: self.now, **kwargs)

    def tick(self, evidence):
        self.now += 0.2
        evidence.poll()

    def test_partial_invalid_overwrite_and_immutable_snapshot(self):
        evidence = self.evidence()
        path = self.workspace / "output/progress_001.png"
        data = png()
        path.write_bytes(data[:40])
        self.tick(evidence)
        self.tick(evidence)
        self.assertEqual(evidence.frames, [])
        path.write_bytes(data)
        self.tick(evidence)
        self.assertEqual(evidence.frames, [])
        self.tick(evidence)
        first = evidence.frames[0]
        snapshot = Path(first["path"])
        self.assertEqual(snapshot.read_bytes(), data)
        self.assertEqual(snapshot.stat().st_mode & 0o222, 0)
        self.assertFalse(snapshot.is_relative_to(self.workspace))
        self.assertEqual(first["sha256"], hashlib.sha256(data).hexdigest())
        self.assertTrue(first["observed_utc"].endswith("+00:00"))
        self.assertGreaterEqual(first["stability_seconds"], 0.1)
        self.assertIsNone(first["render_completion_latency_seconds"])
        self.assertFalse(first["resolution_compliant"])
        path.write_bytes(png("blue"))
        self.tick(evidence)
        self.tick(evidence)
        self.assertEqual(len(evidence.frames), 2)
        self.assertEqual(snapshot.read_bytes(), data)
        self.tick(evidence)
        self.assertEqual(len(evidence.frames), 2)

    def test_excluded_textures_nested_paths_and_symlinks(self):
        evidence = self.evidence(final_names=("final.png", "explicit_first_frame.png"))
        out = self.workspace / "output"
        for name in ("atlas.png", "texture.png", "progress_diffuse.png", "unlisted_first_frame.png"):
            (out / name).write_bytes(png())
        (out / "textures").mkdir()
        (out / "textures/progress_001.png").write_bytes(png())
        (out / "progress_003.png").symlink_to(out / "atlas.png")
        (out / "final.png").write_bytes(png())
        (out / "explicit_first_frame.png").write_bytes(png())
        self.tick(evidence)
        self.tick(evidence)
        self.assertEqual({Path(f["source_path"]).name for f in evidence.frames},
                         {"final.png", "explicit_first_frame.png"})

    def test_full_decode_not_only_header_or_verify(self):
        evidence = self.evidence()
        path = self.workspace / "output/final.png"
        path.write_bytes(png())
        self.tick(evidence)
        with patch("PIL.PngImagePlugin.PngImageFile.load", side_effect=OSError("SYNTHETIC pixel decode failure")):
            self.tick(evidence)
        self.assertEqual(evidence.frames, [])
        self.tick(evidence)
        self.assertEqual(len(evidence.frames), 1)

    def test_caps_and_disjoint_roots(self):
        for destination in (self.workspace / "control", self.root):
            with self.assertRaisesRegex(ValueError, "disjoint"):
                VideoEvidence(self.workspace, destination, identity={})
        evidence = self.evidence(max_frames=1)
        for number in range(3):
            (self.workspace / f"output/progress_{number:03}.png").write_bytes(png())
        self.tick(evidence)
        self.tick(evidence)
        self.assertEqual(len(evidence.frames), 1)
        self.assertTrue(evidence.cap_reported)

    def test_byte_cap_and_oversize_file(self):
        evidence = self.evidence(max_bytes=1, max_png_bytes=10000)
        (self.workspace / "output/final.png").write_bytes(png())
        self.tick(evidence)
        self.tick(evidence)
        self.assertEqual(evidence.frames, [])
        self.assertTrue(evidence.cap_reported)

    def test_durable_raw_journal_before_finish_and_malformed_bytes(self):
        evidence = self.evidence()
        raw = b'{"type":"turn.started"}\n\xffTRUNCATED'
        evidence.raw("stdout", raw)
        observer = TimedExecEvents(evidence=evidence)
        observer.feed(raw)
        rows = [json.loads(line) for line in evidence.journal.read_text().splitlines()]
        transports = [row for row in rows if row["kind"] == "transport"]
        self.assertEqual(base64.b64decode(transports[0]["data"]), raw)
        self.assertEqual([row["event"]["type"] for row in rows if row["kind"] == "event"], ["turn.started"])
        self.assertIn("truncated_event", observer.finish()["errors"])

    def test_oversize_png_and_storage_failure(self):
        evidence = self.evidence(max_png_bytes=1)
        (self.workspace / "output/final.png").write_bytes(png())
        self.tick(evidence)
        self.tick(evidence)
        self.assertEqual(evidence.frames, [])
        evidence.max_png_bytes = 10000
        with patch.object(evidence, "record", side_effect=OSError("SYNTHETIC disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.tick(evidence)

    def test_no_scene_scoring_camera_or_source_writes(self):
        evidence = self.evidence()
        paths = [self.workspace / "start.blend", self.workspace / "output/final.blend",
                 self.workspace / "output/render1.png", self.workspace / "output/render2.png",
                 self.workspace / "output/final.png"]
        for path in paths:
            path.write_bytes(png() if path.suffix == ".png" else b"SYNTHETIC .blend sentinel, not a Blender file")
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.tick(evidence)
        self.tick(evidence)
        self.assertEqual(before, {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
        self.assertEqual(len(evidence.frames), 1)

    def test_cumulative_supplied_checkpoints_and_hash_rejection(self):
        evidence = self.evidence()
        observer = TimedExecEvents(["allowed"], max_turns=2, evidence=evidence)
        observer.observe(dict(type="thread.started", thread_id="SYNTHETIC"))
        for when, amount in [(2, 60), (9, 40)]:
            self.now = when
            observer.observe(dict(type="turn.started"))
            observer.observe(dict(type="turn.completed", usage={
                "input_tokens": amount, "cached_input_tokens": 0, "output_tokens": 0}))
        (self.workspace / "output/final.png").write_bytes(png())
        self.tick(evidence)
        self.tick(evidence)
        telemetry = observer.finish()
        manifest = evidence.finish(dict(wall_seconds=10), telemetry)
        (self.workspace / "summary.json").write_text(json.dumps(dict(
            evidence_manifest=str(manifest), wall_seconds=10, success=True, telemetry=telemetry)))
        trial = collect_trial(self.workspace, "original", "SYNTHETIC")
        state = frame_state(trial, 6, 0.6, False)
        self.assertEqual(state["interpolated_total"], 80)
        self.assertEqual(token_total(state["measured_tokens"]), 60)
        snapshot = Path(evidence.frames[0]["path"])
        snapshot.chmod(0o644)
        snapshot.write_bytes(png("blue"))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            collect_trial(self.workspace, "original", "SYNTHETIC")

    def test_tool_counts_completion_only_duplicates_and_errors(self):
        events = [event("item.started"), event("item.updated"), event(), event(),
                  event(ident="SYNTHETIC-fail", result={"isError": True}),
                  event(ident="SYNTHETIC-fail", result={"isError": True})]
        for index, item in enumerate(events):
            item["elapsed_seconds"] = index
        timeline = tool_timeline(events)
        self.assertEqual([row["tool_calls"] for row in timeline], [1, 1, 1, 1, 2, 2])
        self.assertEqual([row["tool_errors"] for row in timeline], [0, 0, 0, 0, 1, 1])
        observer = ExecEvents(["allowed"])
        for item in events:
            observer.observe(item)
        self.assertEqual(observer.tool_errors, 1)
        self.assertIn("duplicate_item_completion", observer.errors)

    def test_interpolation_checkpoints_final_only_unknown_and_reasoning(self):
        self.assertEqual(interpolated_total(5, 10, 100), 50)
        self.assertEqual(interpolated_total(1, 10, 100, [(2, 60)]), 30)
        self.assertEqual(interpolated_total(6, 10, 100, [(2, 60)]), 80)
        self.assertEqual(interpolated_total(99, 10, 100), 100)
        self.assertEqual(interpolated_total(5, 10, 0), 0)
        self.assertIsNone(interpolated_total(5, 10, None, [(2, 60)]))
        self.assertIsNone(interpolated_total(5, 10, 100, [(2, 80), (4, 20)]))
        self.assertEqual(token_total(dict(uncached_input=60, cached_input=40, output=20, reasoning=10)), 120)
        self.assertEqual(overlay_token_line(None), "Tokens (interpolated): unavailable")
        self.assertEqual(overlay_token_line(50), "Tokens (interpolated): ~50")

    def test_final_only_receipt_is_not_intermediate_and_no_future_frames(self):
        tokens = dict(uncached_input=60, cached_input=20, output=20)
        trial = dict(frames=[{"elapsed_seconds": 8, "path": "SYNTHETIC-final.png"}],
                     events=[dict(elapsed_seconds=2, tool_calls=1, tool_errors=0)],
                     token_events=[dict(elapsed_seconds=9, tokens=tokens)], final_tokens=tokens,
                     wall_seconds=10)
        state = frame_state(trial, 5, 0.5, False)
        self.assertEqual(state["interpolated_total"], 50)
        self.assertIsNone(state["measured_tokens"])
        self.assertIsNone(state["frame"])
        self.assertEqual(state["tool_calls"], 1)
        end = frame_state(trial, 11, 1, False)
        self.assertTrue(end["ended"])
        self.assertEqual(end["interpolated_total"], 100)
        self.assertIsNotNone(end["frame"])
        trial["final_tokens"] = None
        self.assertIsNone(frame_state(trial, 11, 1, False)["interpolated_total"])

    def test_capture_child_final_snapshot_and_raw_flush(self):
        evidence = VideoEvidence(self.workspace, self.root / "capture-control", identity={"synthetic": True}, poll_seconds=0.02)
        observer = TimedExecEvents(evidence=evidence)
        payload = b'{"type":"thread.started","thread_id":"SYNTHETIC"}\n{"type":"turn.started"}\n{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":2}}\n'
        # Only a local Python fixture process is launched, never Codex/Blender.
        script = "import os,pathlib; pathlib.Path('output/final.png').write_bytes(" + repr(png()) + "); os.write(1," + repr(payload) + ")"
        result = capture([sys.executable, "-c", script], self.workspace, observer, evidence=evidence)
        self.assertEqual(result["returncode"], 0)
        telemetry = observer.finish()
        manifest = evidence.finish(result, telemetry)
        self.assertEqual(len(evidence.frames), 1)
        rows = [json.loads(line) for line in evidence.journal.read_text().splitlines()]
        self.assertEqual(b"".join(base64.b64decode(row["data"]) for row in rows
                                   if row["kind"] == "transport" and row["stream"] == "stdout"), payload)
        self.assertEqual(json.loads(manifest.read_text())["measured_telemetry"]["tokens"]["output"], 2)

    def test_capture_deadline_cap_and_launch_refusal(self):
        for index, (script, bounds, reason) in enumerate([
            ("import time; time.sleep(10)", {"seconds": 0.05}, "deadline"),
            ("import os; os.write(1,b'x'*1000)", {"output_bytes": 64}, "output_cap")]):
            evidence = VideoEvidence(self.workspace, self.root / f"control-{index}", identity={"synthetic": True})
            observer = TimedExecEvents(evidence=evidence)
            result = capture([sys.executable, "-c", script], self.workspace, observer, evidence=evidence, **bounds)
            self.assertEqual(result["stop_reason"], reason)
            self.assertTrue(evidence.journal.is_file())
        with self.assertRaisesRegex(ValueError, "live launch blocked"):
            CodexSubprocessAdapter().run({"launch_blockers": ["SYNTHETIC refusal"]})


class SyntheticVideoArtifactTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "offline encoder unavailable; no install")
    def test_synthetic_two_up_encoded_and_decoded(self):
        # Explicit opt-in retains test artifacts; defaults to temporary cleanup.
        parent = os.environ.get("SYNTHETIC_VIDEO_ARTIFACT_ROOT")
        if parent:
            root = Path(tempfile.mkdtemp(prefix="SYNTHETIC-not-benchmark-", dir=parent))
        else:
            temporary = tempfile.TemporaryDirectory(prefix="SYNTHETIC-not-benchmark-")
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
        for label in ("original", "enhanced"):
            workspace = root / label
            (workspace / "output").mkdir(parents=True)
            evidence = VideoEvidence(workspace, root / f"{label}-control", identity={"synthetic": True, "model": "NO_MODEL"}, poll_seconds=0.001)
            evidence.started = time.monotonic()
            (workspace / "output/progress_001.png").write_bytes(png("#225599"))
            evidence.poll()
            time.sleep(0.002)
            evidence.poll()
            observer = TimedExecEvents(["allowed"], evidence=evidence)
            for item in [dict(type="thread.started", thread_id="SYNTHETIC"), dict(type="turn.started"),
                         event("item.started"), event(), dict(type="turn.completed", usage={
                             "input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10})]:
                observer.observe(item)
            (workspace / "output/final.png").write_bytes(png("#227755"))
            result = dict(wall_seconds=1.0, returncode=0, stop_reason=None)
            telemetry = observer.finish()
            manifest = evidence.finish(result, telemetry)
            (workspace / "summary.json").write_text(json.dumps(dict(evidence_manifest=str(manifest),
                wall_seconds=1.0, success=True, telemetry=telemetry, synthetic=True)))
            trial = collect_trial(workspace, label, "SYNTHETIC / NO MODEL")
            self.assertEqual(len(trial["frames"]), 2)
        output = root / "SYNTHETIC-not-benchmark.mp4"
        report = build(root, output, duration_seconds=2, fps=4)
        self.assertEqual(report["probe"]["streams"][0]["nb_read_frames"], "8")
        self.assertAlmostEqual(float(report["probe"]["format"]["duration"]), 2.0, places=2)
        decoded = root / "SYNTHETIC-decoded-final.png"
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-i", str(output),
                        "-vf", "select=eq(n\\,7)", "-frames:v", "1", str(decoded)], check=True)
        with Image.open(decoded) as image:
            image.load()
            self.assertEqual(image.size, (1600, 740))
        report["synthetic"] = True
        report["test_only_not_benchmark"] = True
        (root / "SYNTHETIC-test-report.json").write_text(json.dumps(report, indent=2))
        print("SYNTHETIC_ONLY_ARTIFACTS=" + str(root))


if __name__ == "__main__":
    unittest.main()
