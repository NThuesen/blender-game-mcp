"""Camera verification and ledger ported from the exercised ten-round controller."""
from __future__ import annotations
import hashlib,json,os,signal,subprocess,time
from pathlib import Path
BLENDER='blender'
RENDER_TIMEOUT=600.0
METAL_SETUP=''
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()


def dump(p,v):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp'); t.write_text(json.dumps(v,indent=2)+'\n'); t.replace(p)


def load(p): return json.loads(Path(p).read_text())


class Ledger:
    def __init__(self,root,started=None):
        self.root=root; self.started=time.monotonic() if started is None else started
        self.rows=[]; self.completed=0; self.active=None; self.attempt=0
    def emit(self,phase,**kw):
        row=dict(elapsed_seconds=time.monotonic()-self.started,phase=phase,round=self.active,attempt=self.attempt,completed_rounds=self.completed,**kw)
        self.rows.append(row)
        if self.root is not None:
            with (self.root/'round-ledger.jsonl').open('a') as f:
                f.write(json.dumps(row)+'\n'); f.flush(); os.fsync(f.fileno())
        return row
    def start(self,n,attempt):
        if n!=self.completed+1 or n>10 or attempt not in (1,2): raise ValueError('nonsequential round or unbounded retry')
        if self.active==n and attempt!=self.attempt+1: raise ValueError('duplicate attempt')
        self.active=n; self.attempt=attempt; self.emit('round_start')
    def candidate(self,before,after,blend,png):
        if self.active!=self.completed+1: raise ValueError('round not started')
        if before['camera']==after['camera']: raise ValueError('inspection/no-op is not a refinement')
        if before['preserve']!=after['preserve']: raise ValueError('non-camera preservation audit failed')
        self.emit('edit_verified',camera_before=before['camera'],camera_after=after['camera'])
        self.completed+=1
        self.emit('candidate_checkpoint',blend=str(blend),png=str(png),blend_sha256=sha(blend),png_sha256=sha(png))
    def complete(self): return self.completed==10


def budget(deadline,cap):
    remaining=deadline-time.monotonic()
    if remaining<=1: raise TimeoutError('3600-second task deadline')
    return min(cap,remaining-0.5)


AUDIT = r'''
import bpy,json,hashlib
from pathlib import Path
s=bpy.context.scene
c=bpy.data.objects['Camera1']
def props(x):
    out={}
    for p in x.bl_rna.properties:
        if p.identifier=='rna_type' or p.is_readonly or p.type in ('POINTER','COLLECTION'): continue
        try:
            v=getattr(x,p.identifier)
            if not isinstance(v,(str,int,float,bool,type(None))): v=list(v)
            out[p.identifier]=v
        except Exception: pass
    return out
def nodes(tree):
    if tree is None: return None
    return {'nodes':[(n.name,props(n),[(i.name,repr(i.default_value)) for i in n.inputs if hasattr(i,'default_value')]) for n in tree.nodes], 'links':[(l.from_node.name,l.from_socket.identifier,l.to_node.name,l.to_socket.identifier) for l in tree.links]}
def h(v): return hashlib.sha256(json.dumps(v,sort_keys=True,default=str).encode()).hexdigest()
settings={k:getattr(s.render,k) for k in ('engine','resolution_x','resolution_y','resolution_percentage')}
settings['samples']=s.cycles.samples
preserve={'settings':settings,'objects':[(o.name,o.type,[list(r) for r in o.matrix_world],o.hide_render,[(m.name,m.type,props(m)) for m in o.modifiers]) for o in bpy.data.objects if o.name!='Camera1'], 'meshes':[(m.name,h(([list(v.co) for v in m.vertices],[list(p.vertices) for p in m.polygons])),[x.name if x else None for x in m.materials]) for m in bpy.data.meshes], 'materials':[(m.name,props(m),nodes(m.node_tree)) for m in bpy.data.materials], 'lights':[(l.name,props(l),nodes(l.node_tree)) for l in bpy.data.lights], 'worlds':[(w.name,props(w),nodes(w.node_tree)) for w in bpy.data.worlds], 'images':[(i.name,hashlib.sha256(i.packed_file.data).hexdigest() if i.packed_file else i.filepath) for i in bpy.data.images if i.type!='RENDER_RESULT'], 'scene_camera':s.camera.name}
record={'camera':{'matrix':[list(r) for r in c.matrix_world],'lens':c.data.lens},'preserve':h(preserve),'settings':settings,'audit_exclusions':['animation data','mesh UV/color attributes','unpacked external file bytes','recursive node groups','non-lens camera data properties']}
Path(REPORT).write_text(json.dumps(record,indent=2))
if PNG:
    exec(METAL_SETUP)
    Path(REPORT).with_suffix('.device.json').write_text(json.dumps(_metal_evidence,indent=2))
    s.render.filepath=PNG
    s.render.image_settings.file_format='PNG'
    bpy.ops.render.render(write_still=True)
'''


def inspect_render(blend,report,png,deadline):
    script=report.with_suffix('.py')
    script.write_text('REPORT='+repr(str(report))+'\nPNG='+repr(str(png) if png else None)+'\nMETAL_SETUP='+repr(METAL_SETUP)+'\n'+AUDIT)
    with report.with_suffix('.log').open('x') as log:
        p=subprocess.Popen([BLENDER,'--background','--disable-autoexec',str(blend),'--python-exit-code','2','--python',str(script)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try: rc=p.wait(timeout=budget(deadline,RENDER_TIMEOUT))
        except BaseException:
            os.killpg(p.pid,signal.SIGKILL); p.wait(); raise
    if rc: raise RuntimeError('Independent Blender verification failed: '+str(report))
    record=load(report)
    if png:
        from PIL import Image
        with Image.open(png) as im: im.load(); size=list(im.size)
        settings=record['settings']
        expected=[settings[k]*settings['resolution_percentage']//100 for k in ('resolution_x','resolution_y')]
        if size!=expected: raise ValueError('render dimensions differ from task')
    return record


PROMPT='''BlenderBench camera4 camera alignment. This is explicit refinement round ROUND/10, attempt ATTEMPT. The attached first image is the current verified scene render; second image is the TARGET. Compare actual pixels and identify a concrete residual misalignment before editing.
Start from {start_blend}. Change only existing Camera1 position/rotation and lens to better match TARGET. Preserve all other scene contents and original renderer/resolution/samples. Do not load other scenes or discover solution files. Use only Blender MCP tools. Inspection or numeric optimization alone is NOT a round: you must APPLY a nonzero camera edit and save it to {blend_out}. Do not reset to the original input or create another camera. Save one candidate only. Do not render: the external harness independently reopens this exact saved blend, verifies camera change/preservation, renders it at original settings, checkpoints it, and attaches that real render to the next round. Render failures are retried under the same round number, never counted as rounds. Do not create videos or overlays. Return concise JSON with concrete visual defect, applied camera change, and blend_path. This round has MODEL_SECONDS seconds for model/MCP editing, with independent rendering afterward inside the overall 3600-second task deadline. Prior feedback: FEEDBACK
'''
