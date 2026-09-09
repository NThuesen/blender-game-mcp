"""Portable orchestration of the exercised ten-round camera controller."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import sys
import time
from . import rounds as r
from . import runtime
from .dataset import entries, inspect_start, verify_asset


def configure(args):
    r.BLENDER=str(args.blender)
    r.METAL_SETUP=runtime.gpu_code(args.gpu)
    return r.METAL_SETUP


def initialize(args, task):
    inp=args.dataset_dir/task
    for rel,row in entries(task): verify_asset(inp/rel,row)
    inspect_start(task,inp/'start.py')
    if (inp/'initialized.json').exists():
        meta=r.load(inp/'initialized.json')
        for name,key in [('start_initialized.blend','blend_sha256'),('initial_verified.png','png_sha256')]:
            if r.sha(inp/name)!=meta[key]: raise ValueError('Initialized input drift')
        return inp
    scene=next(inp/rel for rel,_ in entries(task) if rel.endswith('.blend'))
    runtime.storage_gate(inp,scene.stat().st_size)
    script=inp/'initialize-camera.py'
    if script.exists(): raise FileExistsError('Partial initialization preserved; use a fresh dataset directory')
    script.write_text('import bpy\nfrom pathlib import Path\n'+r.METAL_SETUP+
        f'\nexec(compile(Path({str(inp/"start.py")!r}).read_text(),"reviewed_start.py","exec"))\n'+
        f'bpy.ops.wm.save_as_mainfile(filepath={str(inp/"start_initialized.blend")!r})\n')
    runtime.command([args.blender,'--background','--disable-autoexec',scene,'--python-exit-code','2','--python',script],inp/'initialize-camera.log',600)
    meta=r.inspect_render(inp/'start_initialized.blend',inp/'initialize-audit.json',inp/'initial_verified.png',time.monotonic()+600)
    settings=meta['settings']; dimensions=[settings[k]*settings['resolution_percentage']//100 for k in ('resolution_x','resolution_y')]
    r.dump(inp/'initialized.json',{'blend_sha256':r.sha(inp/'start_initialized.blend'),'png_sha256':r.sha(inp/'initial_verified.png'),'dimensions':dimensions,'render_settings':settings,'gpu_setup':r.sha(inp/'initialize-audit.device.json')})
    return inp


def recover(root, seconds=3600):
    """Read-only recovery; no fresh budget and no replay of ambiguous launches."""
    root=Path(root)
    rows=[json.loads(x) for x in (root/'round-ledger.jsonl').read_text().splitlines() if x.strip()]
    checks=[x for x in rows if x['phase']=='candidate_checkpoint']
    if not checks: raise ValueError('Resume requires at least one verified checkpoint')
    if [x['round'] for x in checks]!=list(range(1,len(checks)+1)): raise ValueError('Nonsequential checkpoints')
    for e in checks:
        for key in ('blend','png'):
            if r.sha(e[key])!=e[key+'_sha256']: raise ValueError('Resume checkpoint drift')
    summary=r.load(root/'summary.json') if (root/'summary.json').exists() else {}
    if not summary and rows[-1]['phase'] not in ('task_failed','candidate_checkpoint'):
        raise ValueError('Active elapsed is ambiguous after abrupt interruption; preserve evidence and reconcile before resume')
    prior=max([float(e['elapsed_seconds']) for e in rows]+[float(summary.get('wall_seconds') or 0)])
    if prior>=seconds: raise TimeoutError('Original active budget exhausted')
    n=len(checks)+1
    attempts=[e for e in rows if e.get('round')==n and e['phase']=='round_start']
    attempt=1; reuse=False
    if attempts:
        last=attempts[-1]; attempt=last['attempt']
        arm=root/f'round-{n:02d}-attempt-{attempt}'/'enhanced'
        finished=any(e['phase']=='model_session_finished' and e.get('round')==n and e.get('attempt')==attempt for e in rows)
        if (arm/'launch.json').exists() or finished:
            attempt+=1
            if attempt>2: raise ValueError('Round already used two launch attempts; refusing replay')
        else: reuse=True  # preparation-only interruption; original round_start retained
    sessions=summary.get('sessions',[])
    if not sessions:
        for e in rows:
            if e['phase']!='model_session_finished':continue
            s=r.load(e['summary'])
            # A persisted offset is needed for exact timeline alignment; never infer it.
            if 'session_offset_seconds' not in e:
                continue
            sessions.append({'round':e['round'],'attempt':e['attempt'],'offset_seconds':e['session_offset_seconds'],'summary':s})
    return {'rows':rows,'completed':len(checks),'last':checks[-1],'prior':prior,'next_attempt':attempt,'reuse_start':reuse,'sessions':sessions,'sessions_timeline_complete':len(sessions)==sum(e['phase']=='model_session_finished' for e in rows)}


def adapter(args, source, setup, dimensions):
    from benchmarks.token_efficiency import creative_smoke as smoke
    cfg=smoke.CONDITIONS['enhanced']
    cfg['source']=source
    cfg['env']={'BLENDER_MCP_CLI_BACKEND':args.runtime,'BLENDER_PATH':str(args.blender),'PYTHONDONTWRITEBYTECODE':'1'}
    if args.runtime=='bpy': cfg['env']['BLENDER_MCP_BPY_PYTHON']=str(args.bpy_python)
    original_build=smoke.build_argv
    original_prepare=smoke.prepare
    original_evidence=smoke.VideoEvidence
    def prepare(arm, condition, start_blend_override=None):
        if arm.exists(): raise FileExistsError(arm)
        runtime.storage_gate(arm.parent,Path(start_blend_override).stat().st_size)
        (arm/'input').mkdir(parents=True); (arm/'output').mkdir()
        (arm/'server-source').symlink_to(source,target_is_directory=True)
        runtime.clone(start_blend_override,arm/'input/start.blend')
        return {'condition':condition,'root':str(arm),'start_blend':str(arm/'input/start.blend')}
    def build(*a,**kw):
        argv,prompt=original_build(*a,**kw)
        for i,v in enumerate(argv):
            if v.startswith('mcp_servers.blender.command='):
                argv[i]='mcp_servers.blender.command='+json.dumps(str(args.server_python))
        argv[-1:-1]=['-c','mcp_servers.blender.tools.execute_blender_code_for_cli.approval_mode="approve"','-c','mcp_servers.blender.tool_timeout_sec=600']
        if argv.count('--image')!=2: raise ValueError('Both current and target images required')
        r.dump(a[0]/'launch.json',{'argv':argv,'model':args.model,'isolation':'unverified; explicitly accepted'})
        return argv,prompt
    class Evidence(original_evidence):
        def __init__(self,*a,**kw):
            kw['expected_size']=tuple(dimensions)
            super().__init__(*a,**kw)
    smoke.prepare=prepare; smoke.build_argv=build; smoke.VideoEvidence=Evidence
    def restore():
        smoke.prepare=original_prepare; smoke.build_argv=original_build; smoke.VideoEvidence=original_evidence
    return smoke,restore


def backend_probe(args, root, source, start, deadline):
    """Actually render a saved scene through the production enhanced helper."""
    png=root/'backend-probe.png'; script=root/'backend-probe.py'
    code=f'import bpy\nbpy.context.scene.render.filepath={str(png)!r}\nbpy.context.scene.render.image_settings.file_format="PNG"\nbpy.ops.render.render(write_still=True)\nresult=_metal_evidence'
    script.write_text('import json\nfrom pathlib import Path\nfrom blmcp.tools_helpers.blender_cli import run_blender_cli\n'+
        f'result=run_blender_cli({str(start)!r},{code!r})\n'+
        f'Path({str(root/"backend-probe.json")!r}).write_text(json.dumps(result))\n')
    env=dict(os.environ,PYTHONPATH=str(source/'mcp'),PYTHONDONTWRITEBYTECODE='1',BLENDER_MCP_CLI_BACKEND=args.runtime,BLENDER_PATH=str(args.blender))
    if args.runtime=='bpy':env['BLENDER_MCP_BPY_PYTHON']=str(args.bpy_python)
    digest=r.sha(start)
    runtime.command([args.server_python,script],root/'backend-probe.log',r.budget(deadline,600),env)
    from PIL import Image
    with Image.open(png) as im: im.load(); size=list(im.size)
    result=r.load(root/'backend-probe.json')
    if result.get('scene_device')!='GPU': raise ValueError('Backend probe did not report GPU')
    if r.sha(start)!=digest: raise ValueError('Backend probe changed source')
    return size


def run(args, task):
    if not args.accept_unverified_isolation: raise ValueError('Paid run requires --accept-unverified-isolation; this is not an enforced oracle sandbox')
    if args.runtime=='bpy' and not args.bpy_python: raise ValueError('--bpy-python required')
    inp=args.dataset_dir/task
    meta=r.load(inp/'initialized.json'); start=inp/'start_initialized.blend'; image=inp/'initial_verified.png'; target=inp/'renders/goal/render1.png'
    if r.sha(start)!=meta['blend_sha256'] or r.sha(image)!=meta['png_sha256']: raise ValueError('Input hash mismatch')
    for rel,row in entries(task): verify_asset(inp/rel,row)
    root=args.output/task
    recovered=recover(args.resume_from,args.seconds) if args.resume_from else None
    if recovered:
        original=args.resume_from
        prior_config=r.load(original/'provenance.json')
        if prior_config.get('portable_config'):
            expected={'model':args.model,'runtime':args.runtime,'gpu':args.gpu,'task':task,'seconds':args.seconds}
            if prior_config['portable_config']!=expected: raise ValueError('Continuation configuration differs from original')
        else:
            raise ValueError('Legacy scoring/report supported; legacy generation resume needs explicit reviewed source/config migration, not an automatic new protocol')
        source=runtime.verify_frozen(original)
        start=Path(recovered['last']['blend']);image=Path(recovered['last']['png'])
    runtime.storage_gate(root.parent,start.stat().st_size)
    root.mkdir(parents=True,exist_ok=False)
    probe=root/'cow-probe.blend'
    runtime.clone(start,probe)
    probe.unlink()  # Only our just-created disposable CoW probe, not evidence.
    setup=configure(args)
    if recovered:
        (root/'frozen').symlink_to(args.resume_from/'frozen',target_is_directory=True)
        r.dump(root/'source-provenance.json',r.load(args.resume_from/'source-provenance.json'))
        (root/'round-ledger.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in recovered['rows']))
    else: source=runtime.freeze(args.repo,root,setup)
    prior=recovered['prior'] if recovered else 0
    begun=time.monotonic()-prior; deadline=begun+args.seconds
    ledger=r.Ledger(root,begun)
    sessions=recovered['sessions'] if recovered else []
    if recovered:ledger.rows=recovered['rows'];ledger.completed=recovered['completed']
    r.dump(root/'provenance.json',{'dataset_revision':__import__('benchmarks.blenderbench_camera.dataset',fromlist=['SHA']).SHA,'portable_config':{'model':args.model,'runtime':args.runtime,'gpu':args.gpu,'task':task,'seconds':args.seconds},'continuation':bool(recovered),'original_root':str(args.resume_from) if recovered else None,'original_ledger_sha256':r.sha(args.resume_from/'round-ledger.jsonl') if recovered else None,'prior_active_seconds':prior,'downtime_excluded':bool(recovered),'requested_rounds':10,'render_timeout_seconds':600,'isolation':'unverified user-authorized exploratory','VIGA_agent_executed':False,'protocol_change':'Fresh native model session per candidate, independent verification, no evaluator feedback; not published agent equivalence','input':str(inp),'target_sha256':r.sha(target),'initial_sha256':meta['png_sha256']})
    smoke,restore=adapter(args,source,setup,meta['dimensions'])
    calls=0
    from benchmarks.token_efficiency.video_evidence import tool_timeline
    for e in ledger.rows:
        if e['phase']=='model_session_finished':
            events=r.load(Path(e['summary']).parent/'events.json'); t=tool_timeline(events); calls+=t[-1]['tool_calls'] if t else 0
    feedback='Inspect the actual current image and refine residual camera alignment.'
    def alarm(*unused): raise TimeoutError('Combined active task budget exhausted')
    old=signal.signal(signal.SIGALRM,alarm); signal.setitimer(signal.ITIMER_REAL,max(.001,deadline-time.monotonic()))
    status='incomplete'
    try:
        before=r.inspect_render(start,root/'initial-audit.json',None,deadline)
        repeat=r.inspect_render(start,root/'repeat-audit.json',None,deadline)
        if before!=repeat: raise ValueError('Nondeterministic preservation audit')
        if backend_probe(args,root,source,start,deadline)!=meta['dimensions']:raise ValueError('Backend render dimensions mismatch')
        r.inspect_render(start,root/'gpu-preflight-audit.json',root/'gpu-preflight.png',deadline)
        if recovered:ledger.emit('continuation_started',prior_active_seconds=prior,downtime_excluded=True)
        for n in range(ledger.completed+1,11):
            first=recovered['next_attempt'] if recovered and n==recovered['completed']+1 else 1
            for attempt in range(first,3):
                runtime.storage_gate(root,start.stat().st_size)
                if recovered and n==recovered['completed']+1 and attempt==first and recovered['reuse_start']:
                    ledger.active=n;ledger.attempt=attempt;ledger.emit('preparation_only_attempt_continued')
                else:ledger.start(n,attempt)
                seconds=min(args.model_seconds,max(1,r.budget(deadline,args.seconds)/(11-n)-100))
                arm=root/f'round-{n:02d}-attempt-{attempt}'/'enhanced'
                prompt=r.PROMPT.replace('camera4',task).replace('Astra',args.model).replace('ROUND',str(n)).replace('ATTEMPT',str(attempt)).replace('MODEL_SECONDS',str(int(seconds))).replace('FEEDBACK',feedback.replace('{','{{').replace('}','}}'))
                prompt+='\nDisable local blend save backups (do not save preferences). Save only the requested single candidate.\n'
                offset=time.monotonic()-begun
                summary=smoke.run_trial(arm,'enhanced',args.model,r.budget(deadline,seconds),33554432,args.max_tools,prompt,start,[image,target])
                # Camera mode intentionally asks only for a saved scene; legacy creative
                # success also requires a model-rendered PNG. Retain that raw field.
                sessions.append({'round':n,'attempt':attempt,'offset_seconds':offset,'summary':summary})
                timeline=tool_timeline(r.load(arm/'events.json'));calls+=timeline[-1]['tool_calls'] if timeline else 0
                ledger.emit('model_session_finished',calls=calls,summary=str(arm/'summary.json'),session_offset_seconds=offset)
                try:
                    candidate=root/'candidates'/f'round-{n:02d}-attempt-{attempt}';candidate.mkdir(parents=True)
                    checkpoint=candidate/'candidate.blend';runtime.clone(arm/'output/voxel_robot_pool_party_enhanced.blend',checkpoint)
                    digest=r.sha(checkpoint)
                    after=r.inspect_render(checkpoint,candidate/'audit.json',None,deadline)
                    if before['camera']==after['camera'] or before['preserve']!=after['preserve']:raise ValueError('No camera change or preservation failure')
                    ledger.emit('edit_applied',blend=str(checkpoint),sha256=digest)
                    png=candidate/'progress.png';ledger.emit('verification_start')
                    # Render-only retry stays in this attempt and never increments rounds.
                    for retry in range(2):
                        try:
                            after=r.inspect_render(checkpoint,candidate/f'render-audit-{retry}.json',png,deadline);break
                        except Exception as exc:
                            ledger.emit('render_retry_failed',retry=retry,error=str(exc))
                            if retry==1:raise
                    if r.sha(checkpoint)!=digest:raise ValueError('Independent render changed saved scene')
                    ledger.candidate(before,after,checkpoint,png)
                    start,image,before=checkpoint,png,after
                    feedback='Previous candidate independently verified; refine residual pixel mismatch.'
                    break
                except Exception as exc:
                    feedback=str(exc);ledger.emit('attempt_failed',error=feedback)
                    if attempt==2:raise
        status='ten_verified_rounds'
    except Exception as exc:ledger.emit('task_failed',error=str(exc))
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old);restore()
        r.dump(root/'summary.json',{'status':status,'verified_rounds':ledger.completed,'requested_rounds':10,'wall_seconds':time.monotonic()-begun,'deadline_seconds':args.seconds,'sessions':sessions,'calls':calls,'last_verified_blend':str(start),'last_verified_render':str(image),'success':ledger.complete(),'continuation':bool(recovered),'usage_note':'Native session telemetry; legacy creative success fields require a PNG not requested in camera mode. Camera acceptance is the independent verified-round ledger.'})
    return 0 if ledger.complete() else 1
