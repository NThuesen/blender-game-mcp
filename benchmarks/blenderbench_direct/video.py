"""Portable BlenderBench compilation video with metric-accurate overlays."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
from typing import Iterable


def timeline_state(elapsed: float, *, duration: float, rounds: Iterable[dict],
                   task_tokens: int | None) -> dict:
    if not math.isfinite(elapsed) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("finite nonnegative elapsed and positive duration required")
    selected = None
    for row in sorted(rounds, key=lambda item: (item["elapsed_seconds"], item["round"])):
        if row["elapsed_seconds"] <= elapsed:
            selected = row
        else:
            break
    tokens = None if task_tokens is None else round(task_tokens * min(1.0, max(0.0, elapsed / duration)))
    return {
        "round": selected["round"] if selected else 0,
        "image": selected.get("image") if selected else None,
        "clip_image_cosine_similarity": selected.get("clip_image_cosine_similarity") if selected else None,
        "task_tokens_interpolated": tokens,
        "elapsed_seconds": elapsed,
    }


def overlay_labels(model: str, task: str, state: dict, rounds: int) -> str:
    similarity = state.get("clip_image_cosine_similarity")
    metric = ("CLIP image cosine similarity: unavailable" if similarity is None else
              f"CLIP image cosine similarity: {similarity:.6f}")
    tokens = state.get("task_tokens_interpolated")
    token_label = ("Task-level tokens (linear interpolation): unavailable" if tokens is None else
                   f"Task-level tokens (linear interpolation): ~{tokens:,}")
    return "\n".join((f"{model} | {task} | Round {state['round']}/{rounds}", metric, token_label))


def generate(spec_path: Path, output: Path, *, fps: int = 24, seconds_per_task: float = 3.0,
             ffmpeg: str | None = None) -> dict:
    """Generate an H.264 compilation from an explicit, machine-readable task spec.

    Each task record requires target, initial, duration_seconds, rounds, model and
    optional task_tokens. Round elapsed times are task-local. Token counts shown in
    frames are explicitly linear task-level interpolation, never event-level usage.
    """
    from PIL import Image, ImageDraw

    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    tasks = spec["tasks"]
    if not tasks:
        raise ValueError("video spec contains no tasks")
    if fps < 1 or seconds_per_task <= 0:
        raise ValueError("positive fps and seconds-per-task required")
    encoder = ffmpeg or shutil.which("ffmpeg")
    if not encoder:
        raise RuntimeError("ffmpeg not found; pass --ffmpeg")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    width, height = 1024, 600
    process = subprocess.Popen([encoder, "-hide_banner", "-loglevel", "error", "-n",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)],
        stdin=subprocess.PIPE)
    report = {"schema_version": 1, "output": str(output), "fps": fps,
              "seconds_per_task": seconds_per_task, "frames": [],
              "metric_label": "CLIP image cosine similarity",
              "token_label": "task-level linear interpolation"}
    try:
        frame_count = round(fps * seconds_per_task)
        for task in tasks:
            with Image.open(task["target"]) as image:
                target = image.convert("RGB").resize((512, 512))
            initial = Path(task["initial"])
            for index in range(frame_count):
                elapsed = task["duration_seconds"] * index / max(1, frame_count - 1)
                state = timeline_state(elapsed, duration=task["duration_seconds"],
                                       rounds=task["rounds"], task_tokens=task.get("task_tokens"))
                source = Path(state["image"]) if state["image"] else initial
                with Image.open(source) as image:
                    candidate = image.convert("RGB").resize((512, 512))
                frame = Image.new("RGB", (width, height), "black")
                frame.paste(target, (0, 88)); frame.paste(candidate, (512, 88))
                draw = ImageDraw.Draw(frame)
                draw.text((12, 12), "TARGET (static)", fill="white")
                labels = overlay_labels(task["model"], task["task"], state, len(task["rounds"]))
                draw.multiline_text((524, 8), labels, fill="white", spacing=3)
                if process.stdin is None:
                    raise RuntimeError("ffmpeg stdin unavailable")
                process.stdin.write(frame.tobytes())
                report["frames"].append({"task": task["task"], "task_frame": index, **state})
        if process.stdin is not None:
            process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError(f"ffmpeg exited {process.returncode}")
    except BaseException:
        process.kill(); process.wait()
        raise
    report_path = output.with_suffix(output.suffix + ".json")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report
