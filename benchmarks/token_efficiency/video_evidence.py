"""Local camera evidence, not an evaluator or model loop.

Raw journal records are private, unredacted base64 transport bytes plus parsed
receipt events. Keep this control directory outside the model workspace. Read-only
snapshots protect against accidental overwrite, NOT a security sandbox.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import time


class VideoEvidence:
    def __init__(self, workspace, destination, *, identity, poll_seconds=0.1,
                 max_frames=1000, max_bytes=256 * 1024 * 1024,
                 max_png_bytes=32 * 1024 * 1024, expected_size=(1280, 720),
                 final_names=("final.png",), monotonic=time.monotonic):
        from PIL import Image  # fail before launch if decoder is unavailable

        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("positive finite poll interval required")
        if any(type(n) is not int or n <= 0 for n in (max_frames, max_bytes, max_png_bytes)):
            raise ValueError("positive integer evidence caps required")
        self.workspace = Path(workspace).resolve()
        self.output = self.workspace / "output"
        self.destination = Path(destination).resolve()
        if self.destination.is_relative_to(self.workspace) or self.workspace.is_relative_to(self.destination):
            raise ValueError("evidence must be disjoint from model workspace")
        self.destination.mkdir(parents=True, exist_ok=False)
        self.destination.chmod(0o700)
        self.journal = self.destination / "raw.jsonl"
        self.journal.touch(mode=0o600, exist_ok=False)
        self.identity = dict(identity)
        self.poll_seconds = poll_seconds
        self.max_frames, self.max_bytes, self.max_png_bytes = max_frames, max_bytes, max_png_bytes
        self.expected_size = tuple(expected_size)
        self.final_names = set(final_names)
        self.monotonic = monotonic
        self.started = monotonic()
        self.last_poll = None
        self.candidates, self.captured = {}, {}
        self.captured_signatures = {}
        self.frames, self.events = [], []
        self.bytes = 0
        self.cap_reported = False
        self.record("capture.created", identity=self.identity,
                    poll_interval_seconds=poll_seconds,
                    timing_note="Receipt times, not render completion; latency before first observation unknown.")

    def stamp(self):
        return {"observed_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": self.monotonic() - self.started}

    def record(self, kind, **fields):
        row = {"kind": kind, **self.stamp(), **fields}
        with self.journal.open("ab") as handle:
            handle.write(json.dumps(row, ensure_ascii=True, allow_nan=False).encode() + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return row

    def start(self, started):
        self.started = started
        self.record("session.started", identity=self.identity)

    def raw(self, stream, chunk):
        self.record("transport", stream=stream, encoding="base64",
                    data=base64.b64encode(chunk).decode("ascii"))

    def event(self, event):
        stamped = {**event, **self.stamp()}
        self.record("event", event=stamped)
        self.events.append(stamped)
        return stamped

    def allowed(self, path):
        # Explicit direct output camera names only; no recursive texture discovery.
        return (path.name in self.final_names or
                re.fullmatch(r"progress_[0-9]+\.png", path.name) is not None)

    def poll(self):
        now = self.monotonic() - self.started
        if self.last_poll is not None and now - self.last_poll < self.poll_seconds:
            return
        previous_poll = self.last_poll
        self.last_poll = now
        if self.output.is_symlink() or not self.output.is_dir():
            return
        present = set()
        for path in sorted(self.output.iterdir()):
            if not self.allowed(path) or path.is_symlink() or not path.is_file():
                continue
            present.add(path.name)
            writing = False
            try:
                before = path.stat()
                signature = (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                if self.captured_signatures.get(path.name) == signature:
                    continue  # do not re-decode every old immutable frame each poll
                candidate = self.candidates.get(path.name)
                if candidate is None or candidate[0] != signature:
                    self.candidates[path.name] = (signature, now, previous_poll)
                    continue
                if before.st_size > self.max_png_bytes:
                    continue
                # Read a bounded byte snapshot; decode those exact bytes, then check
                # that the source did not change while being read/decoded.
                with path.open("rb") as handle:
                    data = handle.read(self.max_png_bytes + 1)
                if len(data) > self.max_png_bytes:
                    continue
                from PIL import Image
                with Image.open(io.BytesIO(data)) as image:
                    if image.format != "PNG":
                        continue
                    if image.width * image.height > 16_777_216:
                        continue
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    size = image.size
                after = path.stat()
                if signature != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    self.candidates.pop(path.name, None)
                    continue
                sha = hashlib.sha256(data).hexdigest()
                if self.captured.get(path.name) == sha:
                    self.captured_signatures[path.name] = signature
                    continue
                if len(self.frames) >= self.max_frames or self.bytes + len(data) > self.max_bytes:
                    if not self.cap_reported:
                        writing = True
                        self.record("snapshot.cap", max_frames=self.max_frames, max_bytes=self.max_bytes)
                        self.cap_reported = True
                    continue
                destination = self.destination / f"camera-{len(self.frames):06d}-{sha}.png"
                writing = True
                with destination.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                destination.chmod(0o444)
                frame = self.record(
                    "snapshot", path=str(destination), source_path=str(path), sha256=sha,
                    width=size[0], height=size[1], identity=self.identity,
                    resolution_compliant=size[0] >= self.expected_size[0] and size[1] >= self.expected_size[1],
                    first_observed_elapsed_seconds=candidate[1],
                    previous_poll_elapsed_seconds=candidate[2],
                    stability_seconds=now - candidate[1], poll_interval_seconds=self.poll_seconds,
                    actual_poll_gap_seconds=None if previous_poll is None else now - previous_poll,
                    render_completion_latency_seconds=None)
                self.frames.append(frame)
                self.captured[path.name] = sha
                self.captured_signatures[path.name] = signature
                self.bytes += len(data)
            except (OSError, ValueError, SyntaxError):
                if writing:
                    raise  # evidence storage failure is not a partial source PNG
                # Partially written or invalid PNGs are retried, never snapshotted.
                continue
        for name in set(self.candidates) - present:
            self.candidates.pop(name, None)

    def finish(self, result, telemetry):
        # A final file first seen at process exit still needs two observations.
        self.last_poll = None
        self.poll()
        time.sleep(self.poll_seconds)
        self.poll()
        self.record("session.finished", result={key: result.get(key) for key in
                    ("returncode", "stop_reason", "wall_seconds")}, measured_telemetry=telemetry)
        manifest = {"schema": 1, "identity": self.identity, "raw_journal": str(self.journal),
                    "frames": self.frames, "events": self.events,
                    "measured_telemetry": telemetry,
                    "snapshot_cap_reached": self.cap_reported,
                    "token_overlay": "Tokens (interpolated); presentation estimate, not measured per-frame usage"}
        path = self.destination / "manifest.json"
        with path.open("x") as handle:
            json.dump(manifest, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        return path


def tool_timeline(events):
    """Count unique observed MCP IDs, including completion-only streams.

    These are observed calls, not proof execution or edits occurred. Duplicate
    lifecycle records never increment totals; parser errors stay in telemetry.
    """
    seen, completed, failed = set(), set(), set()
    timeline = []
    for event in events:
        kind, item = event.get("type"), event.get("item")
        if kind not in ("item.started", "item.updated", "item.completed") or not isinstance(item, dict):
            continue
        ident = item.get("id")
        if item.get("type") != "mcp_tool_call" or not isinstance(ident, str):
            continue
        seen.add(ident)
        if kind == "item.completed" and ident not in completed:
            completed.add(ident)
            result = item.get("result")
            if bool(item.get("error")) or item.get("status") == "failed" or (
                    isinstance(result, dict) and result.get("isError") is True):
                failed.add(ident)
        timeline.append({"elapsed_seconds": event.get("elapsed_seconds"),
                         "tool_calls": len(seen), "tool_errors": len(failed)})
    return timeline


def interpolated_total(elapsed, wall, final_total, checkpoints=()):
    """Presentation-only piecewise interpolation of cumulative supplied usage.

    A complete final total is mandatory. Final-only telemetry uses zero -> final
    over session elapsed, NOT the receipt time of the final usage event.
    """
    if type(final_total) is not int or final_total < 0 or not isinstance(wall, (int, float)) or not math.isfinite(wall) or wall <= 0:
        return None
    points = {0.0: 0, float(wall): final_total}
    for when, total in checkpoints:
        if (type(total) is int and 0 <= total <= final_total and isinstance(when, (int, float))
                and math.isfinite(when) and 0 < when < wall):
            points[float(when)] = total
    ordered = sorted(points.items())
    if any(b[1] < a[1] for a, b in zip(ordered, ordered[1:])):
        return None  # contradictory cumulative checkpoints, not an invented curve
    elapsed = max(0.0, min(float(elapsed), wall))
    for (left, low), (right, high) in zip(ordered, ordered[1:]):
        if elapsed <= right:
            return round(low + (high - low) * (elapsed - left) / (right - left))
    return final_total
