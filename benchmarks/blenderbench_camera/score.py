"""Offline pinned official metric functions, never VIGA agent or judge imports."""
from __future__ import annotations
import ast
import json
from pathlib import Path
import subprocess
from .rounds import sha, dump, load
VIGA_REVISION='69cb8ef0651bf68124815682df4a0f8e8b57c141'
EVALUATOR_SHA256='9ee08818aa858dccfff3efbb36cb9a98e8887f14e6168ad1a23731351cdeb3b5'
CLIP_REVISION='3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268'


def evaluator_source(viga):
    viga=Path(viga).resolve()
    head=subprocess.check_output(['git','-C',str(viga),'rev-parse','HEAD'],text=True).strip()
    if head!=VIGA_REVISION: raise ValueError('VIGA HEAD must equal '+VIGA_REVISION)
    source=viga/'evaluators/blenderbench/ref_based_eval.py'
    if sha(source)!=EVALUATOR_SHA256: raise ValueError('VIGA evaluator bytes differ from pinned upstream')
    committed=subprocess.check_output(['git','-C',str(viga),'show',VIGA_REVISION+':evaluators/blenderbench/ref_based_eval.py'])
    if committed!=source.read_bytes(): raise ValueError('Evaluator working tree drift')
    return source


def score(root, inp, output, viga, pl_only=False):
    import numpy as np
    from PIL import Image
    source=evaluator_source(viga)
    ns={'np':np,'Image':Image}
    names=['photometric_loss']
    if not pl_only:
        import torch
        from transformers import CLIPModel, CLIPProcessor
        model=CLIPModel.from_pretrained('openai/clip-vit-base-patch32',revision=CLIP_REVISION,local_files_only=True,use_safetensors=False,weights_only=True).eval()
        processor=CLIPProcessor.from_pretrained('openai/clip-vit-base-patch32',revision=CLIP_REVISION,local_files_only=True,use_fast=False)
        ns.update(torch=torch,GLOBAL_CLIP_MODEL=model,GLOBAL_CLIP_PROCESSOR=processor)
        names=['ensure_clip_loaded','clip_similarity','photometric_loss']
    tree=ast.parse(source.read_text())
    for name in names:
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
    target_path=Path(inp)/'renders/goal/render1.png'
    with Image.open(target_path) as im: target=im.convert('RGB')
    expected=load(Path(inp)/'initialized.json')['dimensions']
    ledger=Path(root)/'round-ledger.jsonl'
    checks=[json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]
    rows=[]; seen=set(); failures=[]
    for e in checks:
        if e['phase']!='candidate_checkpoint': continue
        path=Path(e['png'])
        try:
            digest=sha(path)
            if digest!=e['png_sha256']: raise ValueError('Checkpoint hash mismatch')
            if digest in seen: continue
            with Image.open(path) as im: im.load(); image=im.convert('RGB')
            if list(image.size)!=expected: raise ValueError('Decoded dimensions differ from task')
            row={'path':str(path),'sha256':digest,'candidate':True,'role':'verified_round','round':e['round'],'dimensions':list(image.size),'raw_pl':float(ns['photometric_loss'](image,target)), 'raw_nclip':None if pl_only else float(1-ns['clip_similarity'](image,target))}
            rows.append(row); seen.add(digest)
        except Exception as exc: failures.append({'path':str(path),'error':str(exc)})
    best=min(rows,key=lambda r:r['raw_nclip']) if rows and not pl_only else None
    result={'rows':rows,'failures':failures,'best_clip_checkpoint':best,'selection':'Minimum NCLIP, paired PL from same checkpoint, earliest tie; no selection in PL-only mode','viga_revision':VIGA_REVISION,'source_sha256':sha(source),'model_revision':None if pl_only else CLIP_REVISION,'target_sha256':sha(target_path),'ledger_sha256':sha(ledger),'device':'cpu (evaluation only; rendering requires GPU)','pl_only':pl_only,'scope':'BlenderBench camera subset; raw units; no VLM judge or paper scaling'}
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    dest=output/('pl_scores.json' if pl_only else 'clip_scores.json')
    if dest.exists(): raise FileExistsError(dest)
    dump(dest,result)
    return result
