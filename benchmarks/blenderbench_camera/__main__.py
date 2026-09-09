"""BlenderBench camera-only CLI. No model calls except explicitly opted-in run."""
from __future__ import annotations
import argparse
import fcntl
import json
from pathlib import Path
import sys
import time
from . import dataset, rounds, runtime


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    def runtime_options(q):
        q.add_argument('--blender',type=Path,required=True,help='Absolute GPU-capable Blender executable (independent verifier)')
        q.add_argument('--gpu',choices=['auto','METAL','CUDA','OPTIX'],default='auto')
    def task_options(q):
        q.add_argument('--dataset-dir',type=Path,required=True,help='External data cache, not the repository')
        q.add_argument('--tasks',nargs='+',choices=sorted(dataset.REVIEWED),default=['camera4'])
    q=sub.add_parser('download',help='Download only hash-pinned scene/start/task/target; never goal.py');task_options(q)
    q=sub.add_parser('initialize',help='Apply reviewed start camera and independently GPU-render');task_options(q);runtime_options(q)
    q=sub.add_parser('run',help='Paid generation, explicit unverified-isolation opt-in required');task_options(q);runtime_options(q)
    q.add_argument('--output',type=Path,required=True,help='Fresh external parent; creates one new root per task')
    q.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    q.add_argument('--model',required=True)
    q.add_argument('--runtime',choices=['blender','bpy'],default='blender')
    q.add_argument('--server-python',type=Path,required=True)
    q.add_argument('--bpy-python',type=Path)
    q.add_argument('--seconds',type=float,default=3600)
    q.add_argument('--model-seconds',type=float,default=180)
    q.add_argument('--max-tools',type=int,default=30,help='Reactive observed MCP call cap per model session, not rounds')
    q.add_argument('--resume-from',type=Path,help='Existing portable single-task root; old evidence retained, active budget subtracted')
    q.add_argument('--accept-unverified-isolation',action='store_true')
    q=sub.add_parser('render',help='No model: independently reopen/audit/render existing checkpoint');runtime_options(q)
    q.add_argument('--blend',type=Path,required=True);q.add_argument('--output',type=Path,required=True)
    q.add_argument('--runtime',choices=['blender','bpy'],default='blender')
    q.add_argument('--server-python',type=Path,help='Also render through enhanced backend; no model calls')
    q.add_argument('--bpy-python',type=Path)
    q.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    q=sub.add_parser('report',help='Read-only saved evidence to JSON/CSV/Markdown, no ML imports')
    q.add_argument('--root',type=Path,required=True);q.add_argument('--output',type=Path,required=True);q.add_argument('--scores',type=Path)
    q=sub.add_parser('score',help='Official pinned PL/NCLIP offline; no VIGA agent/judge')
    q.add_argument('--root',type=Path,required=True);q.add_argument('--input',type=Path,required=True);q.add_argument('--output',type=Path,required=True);q.add_argument('--viga',type=Path,required=True);q.add_argument('--pl-only',action='store_true')
    q=sub.add_parser('video',help='15s chronological TARGET/progress with exact observed calls and interpolated tokens')
    q.add_argument('--root',type=Path,required=True);q.add_argument('--input',type=Path,required=True);q.add_argument('--output',type=Path,required=True);q.add_argument('--model',required=True)
    q=sub.add_parser('verify-sources',help='Verify VIGA checkout and pinned camera metadata without downloading assets')
    q.add_argument('--viga',type=Path,required=True)
    q=sub.add_parser('cache-clip',help='Explicit network download of pinned evaluator weights, no model inference')
    return p


def external(path):
    repo=Path(__file__).resolve().parents[2]
    path=path.resolve()
    if path==repo or repo in path.parents: raise ValueError('Generated assets/evidence must live outside this repository')
    return path


def main(argv=None):
    if not __debug__: raise RuntimeError('Do not run with Python -O; asset/audit assertions are safety gates')
    args=parser().parse_args(argv)
    for key,value in vars(args).items():
        if isinstance(value,Path):
            # Resolving a venv's python symlink selects the base interpreter and
            # silently drops that environment's dependencies.
            setattr(args,key,value.expanduser().absolute() if key in ('server_python','bpy_python','blender') else value.expanduser().resolve())
    for key in ('output','dataset_dir'):
        if hasattr(args,key):external(getattr(args,key))
    if args.command=='download':
        for task in args.tasks:print(dataset.stage(task,args.dataset_dir))
    elif args.command=='initialize':
        from .controller import configure, initialize
        configure(args)
        for task in args.tasks:print(initialize(args,task))
    elif args.command=='run':
        from .controller import run
        if not 0<args.seconds<=3600 or not 0<args.model_seconds<=3600 or args.max_tools<1:raise ValueError('Positive budgets required; task seconds <=3600')
        if args.resume_from and len(args.tasks)!=1:raise ValueError('Resume exactly one task')
        if not args.accept_unverified_isolation:raise ValueError('--accept-unverified-isolation is required before any paid run')
        args.output.mkdir(parents=True,exist_ok=True)
        with (args.output/'.camera-run.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return max(run(args,task) for task in args.tasks)
    elif args.command=='render':
        rounds.BLENDER=str(args.blender);rounds.METAL_SETUP=runtime.gpu_code(args.gpu)
        runtime.storage_gate(args.output)  # Read-only render: no scene rewrites/clones.
        args.output.mkdir(parents=True,exist_ok=False)
        before=rounds.sha(args.blend)
        record=rounds.inspect_render(args.blend,args.output/'audit.json',args.output/'render.png',time.monotonic()+600)
        if args.server_python:
            from .controller import backend_probe
            if args.runtime=='bpy' and not args.bpy_python:raise ValueError('--bpy-python required')
            source=runtime.freeze(args.repo,args.output,rounds.METAL_SETUP)
            backend_probe(args,args.output,source,args.blend,time.monotonic()+600)
        if before!=rounds.sha(args.blend):raise ValueError('Render modified source')
        print(json.dumps({'source_unchanged':True,'blend_sha256':before,'render_sha256':rounds.sha(args.output/'render.png'),'audit':record}))
    elif args.command=='report':
        from .round_report import write_report
        result=write_report(args.root,args.output,scores_path=args.scores)
        print(json.dumps({'rows':len(result['rows']),'scored_rounds':result['scored_rounds'],'output':str(args.output)}))
    elif args.command=='score':
        from .score import score
        result=score(args.root,args.input,args.output,args.viga,args.pl_only)
        print(json.dumps({'scored_unique_checkpoints':len(result['rows']),'failures':result['failures'],'best_clip_checkpoint':result['best_clip_checkpoint']}))
        return int(bool(result['failures']) or not result['rows'])
    elif args.command=='video':
        from . import video
        video.INITIAL=args.input/'initial_verified.png';video.TARGET=args.input/'renders/goal/render1.png';video.MODEL=args.model
        video.compose(args.root,args.output)
        print(args.output/'camera-ten-round-15s.mp4')
    elif args.command=='verify-sources':
        from .score import evaluator_source,VIGA_REVISION,EVALUATOR_SHA256
        evaluator_source(args.viga)
        rows=[(t,rel,row) for t in dataset.REVIEWED for rel,row in dataset.entries(t)]
        print(json.dumps({'dataset_revision':dataset.SHA,'tasks':len(dataset.REVIEWED),'assets':len(rows),'viga_revision':VIGA_REVISION,'evaluator_sha256':EVALUATOR_SHA256,'source_metadata_sha256':rounds.sha(Path(dataset.__file__).with_name('assets-manifest.json')),'asset_bytes_downloaded':0}))
    elif args.command=='cache-clip':
        from huggingface_hub import snapshot_download
        from .score import CLIP_REVISION
        print(snapshot_download('openai/clip-vit-base-patch32',revision=CLIP_REVISION,allow_patterns=['config.json','preprocessor_config.json','tokenizer_config.json','special_tokens_map.json','vocab.json','merges.txt','tokenizer.json','pytorch_model.bin']))
    return 0


if __name__=='__main__':raise SystemExit(main())
