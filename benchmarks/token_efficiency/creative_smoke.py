"""Repeatable Codex+Blender MCP creative smoke trials.

This is an exploratory harness, not a publication benchmark. It gives Codex one
fresh trial directory and keeps validators/oracles outside that directory.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .codex_runner import ExecEvents, capture
from .models import ORIGINAL_REVISION
from .telemetry import digest, encoded, redact
from .video_evidence import VideoEvidence

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE_TOOLS = ["search_api_docs", "get_python_api_docs", "execute_blender_code_for_cli"]
CONDITIONS = {
    "original": {
        "revision": ORIGINAL_REVISION,
        "source": Path("/tmp/task11-codex-original-evidence-01/control/server-source"),
        "tools": BASE_TOOLS,
        "env": {"BLENDER_PATH": "/Applications/Blender.app/Contents/MacOS/Blender"},
        "label": "official original MCP + Blender CLI/static docs",
    },
    "enhanced": {
        "revision": "working-tree snapshot",
        "source": Path("/tmp/task11-codex-enhanced-evidence-01/control/server-source"),
        "tools": BASE_TOOLS + ["get_runtime_python_api_docs_for_cli"],
        "env": {
            "BLENDER_MCP_CLI_BACKEND": "bpy",
            "BLENDER_MCP_BPY_PYTHON": "/tmp/blender-mcp-bpy-5.2.1/bin/python3",
        },
        "label": "enhanced MCP + standalone bpy/runtime docs",
    },
}


class TimedExecEvents(ExecEvents):
    """ExecEvents variant that preserves observer-relative event times."""

    def __init__(self, *args, evidence=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.evidence = evidence
        self.started = time.monotonic()

    def observe(self, event):
        event = dict(event)
        event["elapsed_seconds"] = time.monotonic() - self.started
        if self.evidence is not None:
            event = self.evidence.event(event)
        if event.get("type") == "error" and str(event.get("message", "")).startswith("Reconnecting..."):
            self.events.append(redact(event))
            return None
        return super().observe(event)

PROMPT = """Create a repeatable high-resolution voxel-style Blender scene: a chrome robot grilling cheeseburgers at a futuristic neon pool party in tropical paradise.

Requirements:
- Use many blocky voxel elements: robot chef, grill, cheeseburgers, turquoise pool, palm trees, party guests, neon lights, sunset/paradise backdrop.
- Make the FIRST FRAME a polished final hero shot that hooks the viewer: robot, grill, burgers, pool and party readable immediately.
- Make the scene visibly take form from tool calls: after every scene-changing MCP tool call, save a stable PNG progress snapshot named progress_001.png, progress_002.png, etc. in the output directory. Do not wait until the end to produce the only image.
- Save the Blender scene to {blend_out}.
- Save the final hero first-frame PNG to {first_frame_out}.
- Render at 1280x720 or higher, with reasonable settings so the trial completes.
- Start from {start_blend}; preserve it and write only to the requested output directory.
- Use only the Blender MCP tools. Do not make the comparison/compositor video. Return final JSON with keys blend_path, first_frame_path, progress_frame_paths, object_count, short_description.
"""


def _run_blender_blank(path: Path) -> None:
    script = path.parent / "make_blank.py"
    script.write_text(
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "bpy.ops.mesh.primitive_cube_add(size=1, location=(0,0,0))\n"
        "bpy.context.object.name='blank_start_cube_delete_or_replace'\n"
        f"bpy.ops.wm.save_as_mainfile(filepath={str(path)!r})\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["/Applications/Blender.app/Contents/MacOS/Blender", "--background", "--python", str(script)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
    )


def prepare(root: Path, condition: str, start_blend_override: Path | None = None) -> dict:
    cfg = CONDITIONS[condition]
    if not cfg["source"].exists():
        raise FileNotFoundError(f"missing frozen source snapshot: {cfg['source']}")
    if root.exists():
        shutil.rmtree(root)
    (root / "input").mkdir(parents=True)
    (root / "output").mkdir()
    shutil.copytree(cfg["source"], root / "server-source")
    start = root / "input" / "start.blend"
    if start_blend_override is None:
        _run_blender_blank(start)
    else:
        if not start_blend_override.is_file():
            raise FileNotFoundError(f"missing start blend override: {start_blend_override}")
        shutil.copy2(start_blend_override, start)
    return {"condition": condition, "root": str(root), "start_blend": str(start)}


def build_argv(root: Path, condition: str, model: str, prompt_template: str,
               image_paths: list[Path] | None = None) -> tuple[list[str], str]:
    cfg = CONDITIONS[condition]
    blend_out = root / "output" / f"voxel_robot_pool_party_{condition}.blend"
    first_out = root / "output" / f"voxel_robot_pool_party_{condition}_first_frame.png"
    prompt = prompt_template.format(
        start_blend=root / "input" / "start.blend",
        blend_out=blend_out,
        first_frame_out=first_out,
        output_dir=root / "output",
        condition=condition,
    )
    overrides = {
        "mcp_servers.blender.command": "/tmp/blender-mcp-doc-server-env/bin/python3",
        "mcp_servers.blender.args": ["-c", "import sys; from blmcp import main; sys.exit(main())"],
        "mcp_servers.blender.cwd": str(root / "server-source" / "mcp"),
        "mcp_servers.blender.required": True,
        "mcp_servers.blender.enabled_tools": cfg["tools"],
        "features.shell_tool": False,
        "web_search": "disabled",
        "developer_instructions": (
            "Use only the allowlisted Blender MCP tools. Do not use shell, file editing, web, "
            "or other integrations. Do not inspect files outside the current trial directory. "
            "Only manage Blender scene creation/progress/final stills. The comparison video is built by external deterministic code."
        ),
        "mcp_servers.blender.env.PYTHONPATH": str(root / "server-source" / "mcp"),
    }
    for key, value in cfg["env"].items():
        overrides[f"mcp_servers.blender.env.{key}"] = value
    argv = [
        "codex", "--ask-for-approval", "on-request", "exec",
        "--approve-for-me", "--ignore-user-config", "--strict-config",
        "--ephemeral", "--json", "--color", "never", "--skip-git-repo-check",
        "--cd", str(root), "--model", model, "-o", str(root / "result.txt"),
    ]
    for image_path in image_paths or []:
        argv += ["--image", str(image_path)]
    for key, value in overrides.items():
        argv += ["-c", key + "=" + json.dumps(value, ensure_ascii=False)]
    argv.append(prompt)
    (root / "prompt.txt").write_text(prompt, encoding="utf-8")
    (root / "launch.json").write_bytes(encoded({"argv": argv, "condition": condition, "model": model, "label": cfg["label"]}))
    return argv, prompt


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _progress_frames(root: Path, events: list[dict]) -> list[dict]:
    seen = set()
    frames = []
    root_real = root.resolve()
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
            continue
        elapsed = event.get("elapsed_seconds")
        for text in _strings(item.get("result")):
            if ".png" not in text:
                continue
            for token in text.replace("'", '"').replace(",", " ").split():
                token = token.strip('"{}[]()')
                if not token.endswith(".png"):
                    continue
                path = Path(token)
                if not path.is_absolute():
                    path = root / path
                try:
                    resolved = path.resolve()
                    resolved.relative_to(root_real)
                except ValueError:
                    continue
                if resolved.is_file() and str(resolved) not in seen:
                    seen.add(str(resolved))
                    frames.append({"path": str(resolved), "elapsed_seconds": elapsed})
    return frames


def summarize(root: Path, condition: str, model: str, result: dict, telemetry: dict,
              events: list[dict], evidence: VideoEvidence | None = None) -> dict:
    outputs = sorted(str(p.relative_to(root)) for p in (root / "output").glob("*"))
    files = {name: digest((root / name).read_bytes()) for name in outputs
             if (root / name).is_file()}
    required = [
        f"output/voxel_robot_pool_party_{condition}.blend",
        f"output/voxel_robot_pool_party_{condition}_first_frame.png",
    ]
    summary = {
        "condition": condition,
        "model": model,
        "root": str(root),
        "outputs": outputs,
        "output_hashes": files,
        "progress_frames": evidence.frames if evidence is not None else [],
        "evidence_manifest": str(evidence.destination / "manifest.json") if evidence is not None else None,
        "returncode": result.get("returncode"),
        "stop_reason": result.get("stop_reason"),
        "wall_seconds": result.get("wall_seconds"),
        "telemetry": telemetry,
        "required_outputs_present": {name: (root / name).is_file() for name in required},
        "success": result.get("returncode") == 0 and all((root / name).is_file() for name in required),
    }
    (root / "summary.json").write_bytes(encoded(summary))
    return summary


def run_trial(root: Path, condition: str, model: str, seconds: float, output_bytes: int,
              max_tools: int, prompt_template: str, start_blend_override: Path | None = None,
              image_paths: list[Path] | None = None) -> dict:
    from PIL import Image  # require the decoder before preparation or model launch

    prepare(root, condition, start_blend_override)
    argv, _ = build_argv(root, condition, model, prompt_template, image_paths)
    # A unique sibling control directory survives workspace rewrites/reruns.
    control = Path(tempfile.mkdtemp(prefix=f"{root.name}-video-evidence-", dir=root.parent))
    evidence = VideoEvidence(root, control / "capture", identity={
        "run": str(root.resolve()), "task": "creative_smoke_custom" if prompt_template != PROMPT else "voxel_robot_pool_party",
        "prompt_sha256": digest(prompt_template.encode()), "condition": condition, "model": model,
        "synthetic": False, "comparison_eligible": False},
        final_names=("final.png", f"voxel_robot_pool_party_{condition}_first_frame.png"))
    observer = TimedExecEvents(CONDITIONS[condition]["tools"], max_tools=max_tools, max_turns=1, evidence=evidence)
    try:
        result = capture(argv, root, observer, seconds=seconds, output_bytes=output_bytes, evidence=evidence)
        if result["returncode"] != 0:
            observer.fail("process_exit")
    except BaseException as exc:  # keep a durable record for exploratory runs
        result = {"returncode": None, "stop_reason": "capture_exception", "wall_seconds": None, "error": str(exc)[:2000]}
        observer.fail("capture_exception")
    telemetry = observer.finish()
    evidence.finish(result, telemetry)
    (root / "events.json").write_bytes(encoded(observer.events))
    (root / "capture.json").write_bytes(encoded({k: (v.decode("utf-8", "replace") if isinstance(v, bytes) else v) for k, v in result.items()}))
    return summarize(root, condition, model, result, telemetry, observer.events, evidence)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/tmp/voxel-video-mcp-trial")
    parser.add_argument("--condition", choices=["original", "enhanced", "both"], default="both")
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--output-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--max-tools", type=int, default=80)
    parser.add_argument("--prompt-file", type=Path,
                        help="Prompt template file. May use {start_blend}, {blend_out}, {first_frame_out}, {output_dir}, and {condition}.")
    parser.add_argument("--compose-video", action="store_true",
                        help="After both conditions run, programmatically render the two-up progress video from saved events and progress frames.")
    parser.add_argument("--video-output", type=Path,
                        help="Optional path for --compose-video output.")
    parser.add_argument("--start-original", type=Path,
                        help="Optional existing .blend to use as the original condition input instead of a blank scene.")
    parser.add_argument("--start-enhanced", type=Path,
                        help="Optional existing .blend to use as the enhanced condition input instead of a blank scene.")
    parser.add_argument("--image", action="append", type=Path, default=[],
                        help="Attach an image to the Codex exec session. Repeat for multiple images.")
    args = parser.parse_args(argv)
    prompt_template = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else PROMPT
    root = Path(args.root).resolve()
    labels = ["original", "enhanced"] if args.condition == "both" else [args.condition]
    summaries = []
    for label in labels:
        start_override = args.start_original if label == "original" else args.start_enhanced
        summaries.append(run_trial(root / label, label, args.model, args.seconds,
                                   args.output_bytes, args.max_tools, prompt_template,
                                   start_override, args.image))
    result = {"root": str(root), "summaries": summaries}
    if args.compose_video and labels == ["original", "enhanced"]:
        cmd = [sys.executable, str(HERE / "creative_video.py"), str(root)]
        if args.video_output:
            cmd += ["--output", str(args.video_output)]
        completed = subprocess.run(cmd, check=False, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        if completed.returncode:
            result["video_error"] = {
                "returncode": completed.returncode,
                "command": cmd,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        else:
            result["video_report"] = json.loads(completed.stdout)
    (root / "summary.json").write_bytes(encoded(result))
    print(json.dumps(result, indent=2))
    return 0 if all(item["success"] for item in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
