#!/usr/bin/env python3
"""Pinned GPU image-pair scorer using VIGA's exact metric functions.

Place the pinned VIGA ``ref_based_eval.py`` beside this script. This wrapper
hash-verifies it, extracts only three metric functions, and never runs VIGA's
agent or judge. Model weights must exist in the byte-verified offline snapshot supplied by the
parent runner. No Hub lookup is performed.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
from PIL import Image
import torch
from transformers import CLIPModel, CLIPProcessor

VIGA_REVISION = "69cb8ef0651bf68124815682df4a0f8e8b57c141"
EVALUATOR_SHA256 = "9ee08818aa858dccfff3efbb36cb9a98e8887f14e6168ad1a23731351cdeb3b5"
CLIP_REVISION = "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"
EVALUATOR_SOURCE_URL = ("https://github.com/Fugtemypt123/VIGA-release/blob/"
                        + VIGA_REVISION + "/evaluators/blenderbench/ref_based_eval.py")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit("usage: score_pair.py IMAGE TARGET")
    source = Path(__file__).with_name("ref_based_eval.py")
    if hashlib.sha256(source.read_bytes()).hexdigest() != EVALUATOR_SHA256:
        raise ValueError(f"evaluator bytes do not match pinned VIGA source: {EVALUATOR_SOURCE_URL}")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable; publication scorer has no CPU fallback")
    snapshot = os.environ.get("BLENDERBENCH_CLIP_SNAPSHOT")
    if not snapshot or not Path(snapshot).is_dir():
        raise ValueError("verified BLENDERBENCH_CLIP_SNAPSHOT is required")
    model = CLIPModel.from_pretrained(snapshot, local_files_only=True,
        use_safetensors=False, weights_only=True).to("cuda").eval()
    processor = CLIPProcessor.from_pretrained(snapshot, local_files_only=True, use_fast=False)

    def cuda_processor(*positional, **keywords):
        return processor(*positional, **keywords).to("cuda")

    namespace = {"np": np, "torch": torch, "Image": Image, "GLOBAL_CLIP_MODEL": model,
                 "GLOBAL_CLIP_PROCESSOR": cuda_processor}
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for name in ("ensure_clip_loaded", "clip_similarity", "photometric_loss"):
        node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    with Image.open(args[0]) as image:
        candidate = image.convert("RGB"); candidate.load()
    with Image.open(args[1]) as image:
        target = image.convert("RGB"); target.load()
    cosine = float(namespace["clip_similarity"](candidate, target))
    loss = float(namespace["photometric_loss"](candidate, target))
    if not np.isfinite(cosine) or not np.isfinite(loss):
        raise ValueError("nonfinite evaluator output")
    print(json.dumps({"clip_image_cosine_similarity": cosine, "nclip": 1.0 - cosine,
        "photometric_loss": loss, "clip_model": "openai/clip-vit-base-patch32",
        "clip_revision": CLIP_REVISION, "viga_revision": VIGA_REVISION,
        "evaluator_sha256": EVALUATOR_SHA256, "device": str(next(model.parameters()).device),
        "gpu": torch.cuda.get_device_name(0), "torch_version": torch.__version__,
        "transformers_version": __import__("transformers").__version__,
        "numpy_version": np.__version__, "pillow_version": __import__("PIL").__version__}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
