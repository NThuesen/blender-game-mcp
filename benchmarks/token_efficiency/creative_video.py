"""Programmatic two-up progress compositor for creative MCP smoke trials.

No model calls and no Blender execution. It consumes immutable camera receipts
and exact MCP event counts on a shared elapsed clock. Token interpolation is
presentation-only; measured usage remains separate. Hero previews are opt-in.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import json
import math
from pathlib import Path
import shutil
import subprocess

if __package__:
    from .video_evidence import interpolated_total, tool_timeline
else:
    from video_evidence import interpolated_total, tool_timeline

WIDTH, HEIGHT = 1600, 740
BG, PANEL, TEXT, MUTED = "#0b101b", "#141e2e", "#edf4ff", "#a7b8cc"
GOOD, WARN, BAD = "#79e0b1", "#ffd080", "#ff8f8f"


def font(size: int):
    from PIL import ImageFont

    for name in [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "DejaVuSans.ttf",
    ]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def clock(seconds: float | int | None) -> str:
    if not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        return "unknown"
    value = max(0, int(seconds))
    return f"{value // 60:02d}:{value % 60:02d}"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_object(path: Path) -> dict:
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def image_for(path: str, size=(752, 420)):
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        source.verify()
    with Image.open(path) as source:
        return ImageOps.pad(
            ImageOps.exif_transpose(source).convert("RGB"),
            size,
            color="#080c14",
            method=Image.Resampling.LANCZOS,
        )


def token_total(tokens: dict | None) -> int | None:
    if not isinstance(tokens, dict):
        return None
    raw_values = [tokens.get(key) for key in ["uncached_input", "cached_input", "output"]]
    values = []
    for value in raw_values:
        if type(value) is not int or value < 0:
            return None
        values.append(value)
    # Codex output_tokens already includes reasoning_output_tokens; do not add reasoning again.
    return sum(values)


def token_line(tokens: dict | None, *, interpolated: bool = False,
               final_tokens: dict | None = None) -> str:
    if not isinstance(tokens, dict) or all(value is None for value in tokens.values()):
        return "Tokens: not yet reported"
    if interpolated:
        current_total = token_total(tokens)
        final_total = token_total(final_tokens)
        if current_total is not None and final_total is not None:
            return f"Tokens: ~{current_total:,} / {final_total:,} interpolated"
    values = {key: (0 if tokens.get(key) is None else tokens[key])
              for key in ["uncached_input", "cached_input", "output", "reasoning"]}
    return "Tokens: uncached {uncached_input:,} | cached {cached_input:,} | output {output:,} | reasoning {reasoning:,}".format(**values)


def scaled_tokens(tokens: dict | None, fraction: float) -> dict | None:
    if not isinstance(tokens, dict) or all(value is None for value in tokens.values()):
        return None
    fraction = max(0.0, min(1.0, fraction))
    return {key: int(round(value * fraction)) if type(value) is int and value >= 0 else None
            for key, value in tokens.items()}


def usage_tokens(usage: dict) -> dict:
    total = usage.get("input_tokens") if type(usage.get("input_tokens")) is int else None
    cached = usage.get("cached_input_tokens") if type(usage.get("cached_input_tokens")) is int else None
    return {
        "uncached_input": total - cached if total is not None and cached is not None and cached <= total else None,
        "cached_input": cached,
        "output": usage.get("output_tokens") if type(usage.get("output_tokens")) is int else None,
        "reasoning": usage.get("reasoning_output_tokens") if type(usage.get("reasoning_output_tokens")) is int else None,
    }


def event_time(event: dict) -> float | None:
    value = event.get("elapsed_seconds")
    return value if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 else None


def trial_root(root: Path, condition: str) -> Path:
    direct = root / condition / "summary.json"
    nested = root / f"{condition}" / condition / "summary.json"
    if direct.exists():
        return root / condition
    if nested.exists():
        return root / condition / condition
    if (root / "summary.json").exists() and root.name == condition:
        return root
    raise FileNotFoundError(f"cannot find {condition} summary under {root}")


def collect_trial(root: Path, condition: str, title: str) -> dict:
    summary = load_object(root / "summary.json")
    events = load_json(root / "events.json") if (root / "events.json").exists() else []
    if not isinstance(events, list):
        events = []
    # Never invent receipt times from filenames or expose a final at t=0.
    evidence_path = summary.get("evidence_manifest")
    evidence = load_object(Path(evidence_path)) if evidence_path else {}
    frames = evidence.get("frames", [])
    events = evidence.get("events", events)
    import hashlib
    for frame in frames:
        path = Path(frame["path"])
        if path.resolve().is_relative_to(root.resolve()):
            raise ValueError("video requires immutable external snapshots")
        if hashlib.sha256(path.read_bytes()).hexdigest() != frame["sha256"]:
            raise ValueError("camera snapshot hash mismatch")
        image_for(str(path))
    frames = sorted(frames, key=lambda frame: frame["elapsed_seconds"])
    calls = tool_timeline(events)
    token_events = []
    cumulative = {"uncached_input": 0, "cached_input": 0, "output": 0, "reasoning": 0}
    for event in events:
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            supplied = usage_tokens(event["usage"])
            cumulative = {key: cumulative[key] + supplied[key]
                          if cumulative[key] is not None and supplied[key] is not None else None
                          for key in cumulative}
            token_events.append({"elapsed_seconds": event_time(event), "tokens": dict(cumulative),
                                 "supplied_usage": event["usage"]})

    wall = summary.get("wall_seconds")
    return {
        "title": title,
        "synthetic": evidence.get("identity", {}).get("synthetic", False),
        "condition": condition,
        "root": str(root),
        "status": "success" if summary.get("success") else "failed/incomplete",
        "success": bool(summary.get("success")),
        "wall_seconds": wall,
        "final_tool_counts": summary.get("telemetry", {}).get("tool_counts", {}),
        "final_tool_errors": summary.get("telemetry", {}).get("tool_errors"),
        "final_tokens": summary.get("telemetry", {}).get("tokens"),
        "frames": frames,
        "events": sorted(calls, key=lambda item: item["elapsed_seconds"] if item["elapsed_seconds"] is not None else math.inf),
        "token_events": sorted(token_events, key=lambda item: item["elapsed_seconds"] if item["elapsed_seconds"] is not None else math.inf),
    }


def frame_state(trial: dict, elapsed: float, norm: float, hook: bool) -> dict:
    frames = trial["frames"]
    selected = frames[-1] if hook and frames else None
    timed = [item for item in frames if item.get("elapsed_seconds") is not None]
    if not selected:
        if timed:
            times = [item["elapsed_seconds"] for item in timed]
            index = bisect_right(times, elapsed) - 1
            selected = timed[index] if index >= 0 else None

    events = [item for item in trial["events"] if item.get("elapsed_seconds") is not None
              and item["elapsed_seconds"] <= elapsed]
    token_events = [item for item in trial["token_events"] if item.get("elapsed_seconds") is not None
                    and item["elapsed_seconds"] <= elapsed]
    if events:
        calls = events[-1]["tool_calls"]
        errors = events[-1]["tool_errors"]
    else:
        calls = 0
        errors = 0
    tokens = token_events[-1]["tokens"] if token_events else None
    ended = isinstance(trial.get("wall_seconds"), (int, float)) and elapsed >= trial["wall_seconds"]
    checkpoints = trial["token_events"]
    # Terminal usage is the session endpoint, not an intermediate checkpoint.
    if checkpoints and token_total(checkpoints[-1]["tokens"]) == token_total(trial.get("final_tokens")):
        checkpoints = checkpoints[:-1]
    estimate = interpolated_total(elapsed, trial.get("wall_seconds"),
                                  token_total(trial.get("final_tokens")),
                                  [(item.get("elapsed_seconds"), token_total(item["tokens"]))
                                   for item in checkpoints])
    return {"frame": selected, "tool_calls": calls, "tool_errors": errors,
            "tokens": tokens, "measured_tokens": tokens,
            "interpolated_total": estimate, "tokens_interpolated": True, "ended": ended}


def draw_panel(canvas, draw, x: int, y: int, trial: dict, elapsed: float, norm: float, hook: bool):
    draw.rounded_rectangle((x + 10, y + 4, x + 790, y + 594), radius=14, fill=PANEL)
    draw.text((x + 24, y + 15), trial["title"], font=font(27), fill=TEXT)
    state = frame_state(trial, elapsed, norm, hook)
    status = trial["status"].upper() if state["ended"] or hook else "IN PROGRESS"
    color = GOOD if trial["success"] else BAD
    draw.text((x + 24, y + 49), status, font=font(19), fill=color)
    image_y = y + 78
    draw.rectangle((x + 24, image_y, x + 775, image_y + 419), fill="#080c14")
    frame = state["frame"]
    if frame:
        canvas.paste(image_for(frame["path"]), (x + 24, image_y))
        label = "FINAL PREVIEW (out of timeline)" if hook else "Latest observed stable camera render"
        draw.text((x + 36, image_y + 388), label, font=font(18), fill="white",
                  stroke_width=2, stroke_fill="black")
    else:
        draw.text((x + 400, image_y + 210), "Awaiting first progress render", anchor="mm",
                  font=font(27), fill=MUTED)
    wall = trial.get("wall_seconds")
    elapsed_display = min(elapsed, wall) if isinstance(wall, (int, float)) else elapsed
    draw.text((x + 24, y + 507),
              f"Elapsed: {clock(elapsed_display)} / {clock(wall)} | Tool calls: {state['tool_calls']} | Errors: {state['tool_errors']}",
              font=font(21), fill=TEXT)
    tokens = overlay_token_line(state["interpolated_total"])
    while draw.textlength(tokens, font=font(17)) > 748:
        tokens = tokens[:-4] + "..."
    draw.text((x + 24, y + 546), tokens, font=font(17), fill=MUTED)


def render(manifest: dict, index: int):
    from PIL import Image, ImageDraw

    fps = manifest["fps"]
    hook_frames = int(manifest["hook_seconds"] * fps)
    hook = index < hook_frames
    active = max(1, manifest["duration_seconds"] * fps - hook_frames - 1)
    norm = 0.0 if hook else (index - hook_frames) / active
    elapsed = norm * manifest["global_wall_seconds"]
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 15), manifest["title"], font=font(30), fill=TEXT)
    subtitle = "FIRST FRAME HOOK: final results first, then actual tool-call build timeline" if hook else (
        f"Normalized shared time {norm * 100:05.1f}% | shared elapsed {clock(elapsed)} / {clock(manifest['global_wall_seconds'])}")
    draw.text((24, 57), subtitle, font=font(22), fill=MUTED)
    draw.rectangle((24, 93, 1576, 99), fill="#2b364a")
    draw.rectangle((24, 93, 24 + int(1552 * norm), 99), fill="#6ebdff")
    draw_panel(canvas, draw, 0, 110, manifest["trials"][0], elapsed, norm, hook)
    draw_panel(canvas, draw, 800, 110, manifest["trials"][1], elapsed, norm, hook)
    return canvas


def overlay_token_line(total):
    return "Tokens (interpolated): " + (f"~{total:,}" if total is not None else "unavailable")


def build(root: Path, output: Path | None = None, *, duration_seconds: int = 15, fps: int = 24,
          hook_seconds: float = 0.0) -> dict:
    original_root = trial_root(root, "original")
    enhanced_root = trial_root(root, "enhanced")
    manifest = {
        "title": "MCP REGULAR VS ENHANCED | CAMERA PROGRESS",
        "duration_seconds": duration_seconds,
        "fps": fps,
        "hook_seconds": hook_seconds,
        "trials": [
            collect_trial(original_root, "original", "REGULAR / ORIGINAL MCP"),
            collect_trial(enhanced_root, "enhanced", "ENHANCED MCP"),
        ],
    }
    walls = [item.get("wall_seconds") for item in manifest["trials"]
             if isinstance(item.get("wall_seconds"), (int, float)) and item["wall_seconds"] > 0]
    if not walls:
        raise ValueError("at least one trial must have positive wall_seconds")
    if any(trial["synthetic"] for trial in manifest["trials"]):
        manifest["title"] = "SYNTHETIC TEST ONLY | NOT BENCHMARK RESULTS"
        manifest["synthetic"] = True
    # Include final post-exit stability receipts and a shared endpoint hold.
    receipts = [frame["elapsed_seconds"] for trial in manifest["trials"] for frame in trial["frames"]]
    manifest["global_wall_seconds"] = max(walls + receipts) + 0.5
    output = output or root / "creative-progress-two-up.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe required on PATH; no auto-install")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(fps), "-i", "-",
           "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt",
           "yuv420p", "-movflags", "+faststart", str(output)]
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for index in range(duration_seconds * fps):
            process.stdin.write(render(manifest, index).tobytes())
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("ffmpeg encoding failed")
    except BaseException:
        process.kill()
        process.wait()
        raise
    probe = json.loads(subprocess.check_output([
        ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,nb_read_frames,duration:format=duration",
        "-of", "json", str(output),
    ], text=True))
    report = {"output_mp4": str(output), "manifest": manifest, "probe": probe,
              "frames": duration_seconds * fps, "fps": fps, "duration_seconds": duration_seconds,
              "width": WIDTH, "height": HEIGHT}
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    output.with_suffix(".validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="creative_smoke root containing original/ and enhanced/")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--duration-seconds", type=int, default=15)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--hook-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)
    report = build(args.root.resolve(), args.output, duration_seconds=args.duration_seconds,
                   fps=args.fps, hook_seconds=args.hook_seconds)
    print(json.dumps({key: value for key, value in report.items() if key != "manifest"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
