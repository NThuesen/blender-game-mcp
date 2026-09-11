"""Independent custom-bpy 5.2.1 audits and CUDA renders (never Blender CLI).

Initialization trust contract: parent verifies the dataset pin and reviews start.py,
then supplies policy pin_verified=True, start_sha256, and reviewed literal
initialization operations. Upstream source is hashed but NEVER executed.
Output directories must not exist. STOP in the output, or policy stop_file, cancels.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def emit(path, event, **fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {'event': event, 'time_unix': time.time(), **fields}
    with path.open('a') as stream:
        stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return row


def _disk(directory, policy):
    floor = int(policy.get('disk_floor_bytes', 1024**3))
    if floor < 0:
        raise ValueError('negative disk floor')
    free = shutil.disk_usage(directory).free
    if free < floor:
        raise OSError(f'disk floor safety: {free} < {floor}')
    return free


def run_process(argv, log, event_path, policy):
    """No deadline: retain native stdout/stderr; report liveness, not fake progress."""
    log = Path(log)
    _disk(log.parent, policy)
    stop = Path(policy.get('stop_file', log.parent / 'STOP'))
    if stop.exists():
        raise InterruptedError('manual stop file: ' + str(stop))
    start = time.monotonic()
    with log.open('xb') as stream:
        process = subprocess.Popen(list(map(str, argv)), stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        common = {'pid': process.pid, 'parent_pid': os.getpid(), 'task': policy.get('task'),
                  'argv': list(map(str, argv)), 'log': str(log.absolute())}
        last_size = 0
        last_activity = start
        last_slow = start
        try:
            emit(event_path, 'process_started', **common)
            while process.poll() is None:
                if stop.exists():
                    raise InterruptedError('manual stop file: ' + str(stop))
                free = _disk(log.parent, policy)
                size = log.stat().st_size
                now = time.monotonic()
                activity = {}
                try:
                    activity['proc_stat'] = Path(f'/proc/{process.pid}/stat').read_text()
                    activity['proc_io'] = Path(f'/proc/{process.pid}/io').read_text()
                except (FileNotFoundError, PermissionError):
                    pass
                if size != last_size:
                    last_activity = now
                emit(event_path, 'heartbeat', **common, elapsed_seconds=now-start,
                     stdout_bytes=size, new_stdout_bytes=size-last_size, free_bytes=free,
                     seconds_since_stdout=now-last_activity, activity=activity)
                if now-last_slow >= float(policy.get('slow_event_seconds', 60)):
                    emit(event_path, 'slow_operation', **common, elapsed_seconds=now-start,
                         stdout_bytes=size, activity=activity, action='continue_without_timeout')
                    last_slow = now
                last_size = size
                time.sleep(float(policy.get('heartbeat_seconds', 2)))
        except BaseException as exc:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            emit(event_path, 'process_cancelled', **common, reason=str(exc), returncode=process.returncode)
            raise
        stream.flush()
        os.fsync(stream.fileno())
    result = {**common, 'returncode': process.returncode, 'elapsed_seconds': time.monotonic()-start}
    emit(event_path, 'process_exited', **result)
    if process.returncode:
        raise RuntimeError(f'custom bpy process exited {process.returncode}; retained log: {log}')
    return result


# This source is executed exclusively by the explicitly configured bpy Python.
WORKER = r'''
import bpy, hashlib, json, sys, os
from pathlib import Path
cfg=json.loads(Path(sys.argv[1]).read_text())
assert bpy.app.version_string.startswith(cfg['bpy_version']), ('configured bpy version required', bpy.app.version_string)
bpy.context.preferences.filepaths.use_scripts_auto_execute=False
bpy.ops.wm.open_mainfile(filepath=cfg['source'], use_scripts=False)
assert not bpy.app.autoexec_fail, 'automatic script dependency rejected'
for op in cfg.get('initialization', []):
    if op['kind']=='object':
        obj=bpy.data.objects[op['object']]
        field=op['property']
        if field in ('location','rotation_euler','scale'):
            setattr(obj,field,op['value'])
        elif field in ('data.lens','data.energy','data.color'):
            setattr(obj.data,field.split('.')[1],op['value'])
        else: raise ValueError('unreviewed initialization property: '+field)
    elif op['kind']=='shape_key' and op['property']=='value':
        bpy.data.shape_keys[op['datablock']].key_blocks[op['key']].value=op['value']
    else: raise ValueError('unreviewed initialization operation')

# ID references are stable names, never memory-address reprs. All editable scalar
# RNA properties are included; nested embedded structs and collections recurse.
# Unsupported cyclic embedded structures fail closed instead of disappearing.
SKIP={'rna_type','is_updated','is_updated_data','is_updated_transform','tag','users',
      'use_fake_user','use_extra_user','is_embedded_data','is_runtime_data',
      'session_uid','original','id_data','library','override_library','preview','depsgraph',
      'animation_data_clear','name_full'}
def scalar(v):
    if isinstance(v,(str,bool,int,float,type(None))): return v
    if isinstance(v,bpy.types.ID): return ref(v)
    if hasattr(v,'items'): return {k:scalar(value) for k,value in v.items()}
    if isinstance(v,set): return sorted(v)
    return [scalar(i) for i in v]
def ref(v):
    return {'id_type':v.bl_rna.identifier,'name':v.name,
            'library':v.library.filepath if v.library else None}
def collection_props(values, stack):
    # RNA foreach_get avoids millions of Python per-vertex/per-component calls.
    # Every writable primitive field is retained; variable/non-numeric fields
    # and nested pointers still use the general serializer.
    if len(values)<128 or not hasattr(values,'foreach_get'):
        return [ref(i) if isinstance(i,bpy.types.ID) else props(i,stack=stack) for i in values]
    from array import array
    first=values[0]
    if isinstance(first,bpy.types.ID): return [ref(i) for i in values]
    if any(i.bl_rna.identifier != first.bl_rna.identifier for i in values):
        return [ref(i) if isinstance(i,bpy.types.ID) else props(i,stack=stack) for i in values]
    result={'count':len(values),'rna_type':first.bl_rna.identifier,'fields':{}}
    for p in first.bl_rna.properties:
        k=p.identifier
        if k in SKIP: continue
        if p.type not in ('POINTER','COLLECTION') and p.is_readonly: continue
        if isinstance(first,bpy.types.ShapeKey) and k=='value': continue
        if p.type in ('FLOAT','INT','BOOLEAN') and (not p.is_array or p.array_length):
            width=p.array_length if p.is_array else 1
            typecode={'FLOAT':'f','INT':'i','BOOLEAN':'b'}[p.type]
            data=array(typecode,[0])*(len(values)*width)
            try:
                values.foreach_get(k,data)
                result['fields'][k]={'width':width,'type':typecode,'sha256':hashlib.sha256(data.tobytes()).hexdigest()}
                continue
            except (AttributeError,TypeError,RuntimeError):
                # Some legacy UV collections expose a different indexed RNA
                # subtype from their iterator. Never reuse that field schema.
                return [props(i,stack=stack) for i in values]
        if p.type=='POINTER':
            result['fields'][k]=[ref(v) if isinstance(v,bpy.types.ID) else props(v,stack=stack) for v in (getattr(i,k) for i in values)]
        elif p.type=='COLLECTION':
            result['fields'][k]=[collection_props(getattr(i,k),stack) for i in values]
        else:
            result['fields'][k]=[scalar(getattr(i,k)) for i in values]
    return result

def props(x, omit=(), stack=()):
    # An RNA graph is not a tree (armature children/parents form dense graphs).
    # Serialize each embedded node once per root with deterministic traversal
    # references, retaining ALL node content without exponential duplication.
    global _rna_seen
    if not stack: _rna_seen={}
    if x is None: return None
    address=(x.bl_rna.identifier,x.as_pointer())
    if address in _rna_seen: return {'rna_ref':_rna_seen[address]}
    _rna_seen[address]=len(_rna_seen)
    stack=stack+(address,)
    out={}
    for p in x.bl_rna.properties:
        k=p.identifier
        if k in SKIP or k in omit or (isinstance(x,bpy.types.ShapeKey) and k=='value'): continue
        if p.type not in ('POINTER','COLLECTION') and p.is_readonly: continue
        v=getattr(x,k)
        if p.type=='POINTER':
            out[k]=ref(v) if isinstance(v,bpy.types.ID) else props(v,stack=stack)
        elif p.type=='COLLECTION':
            out[k]=collection_props(v,stack)
        else:
            if p.type=='STRING' and (p.subtype in ('FILE_PATH','DIR_PATH') or k=='filepath') and v.startswith('//') and v!='//':
                v=os.path.normpath(bpy.path.abspath(v))
            out[k]=scalar(v)
    if isinstance(x,bpy.types.ID):
        out['custom_properties']={k:scalar(v.to_list() if hasattr(v,'to_list') else v) for k,v in x.items() if k!='_RNA_UI'}
    return out

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()
def tree(t):
    if t is None: return None
    return {'properties':props(t), 'nodes':[{ 'properties':props(n),
            'inputs':[props(i) for i in n.inputs], 'outputs':[props(i) for i in n.outputs]} for n in t.nodes],
            'links':[(l.from_node.name,l.from_socket.identifier,l.to_node.name,l.to_socket.identifier) for l in t.links]}
def material_lifecycle():
    # Evidence for normal save cleanup, never add fake users just for auditing.
    return {m.name: {'users':m.users, 'use_fake_user':bool(m.use_fake_user),
                     'use_extra_user':bool(m.use_extra_user),
                     'library':m.library.filepath if m.library else None}
            for m in bpy.data.materials}

def audit():
    objects={}
    for o in bpy.data.objects:
        print('__FULL_AUDIT_OBJECT__'+o.name,flush=True)
        obj={'type':o.type,'location':list(o.location),'rotation_euler':list(o.rotation_euler),'scale':list(o.scale),
             'properties':props(o,('location','rotation_euler','scale','matrix_world','matrix_local','matrix_basis','dimensions')),
             'visibility':{s.name:{vl.name:o.hide_get(view_layer=vl) for vl in s.view_layers if o.name in vl.objects} for s in bpy.data.scenes}}
        if o.type=='CAMERA': obj['lens']=o.data.lens
        if o.type=='LIGHT': obj['light_data']={'energy':o.data.energy,'color':list(o.data.color)}
        objects[o.name]=obj
    preserve={}
    for collection in ('meshes','curves','metaballs','armatures','lattices','materials','lights','worlds','cameras','node_groups','actions','collections','scenes','particles','textures','speakers','volumes','pointclouds','grease_pencils'):
        items=getattr(bpy.data,collection,())
        values={}
        for item in items:
            print('__FULL_AUDIT_ITEM__'+json.dumps({'collection':collection,'name':item.name}),flush=True)
            bound=any(o.data==item for o in bpy.data.objects) if collection in ('cameras','lights') else False
            omissions=('lens','angle','angle_x','angle_y') if collection=='cameras' and bound else (('energy','color') if collection=='lights' and bound else ())
            value=props(item,omissions)
            if hasattr(item,'node_tree'): value['node_tree_contents']=tree(item.node_tree)
            if collection=='node_groups': value['contents']=tree(item)
            values[item.name]=digest(value)
        preserve[collection]=values
    images={}
    for image in bpy.data.images:
        if image.type in ('RENDER_RESULT','COMPOSITING'): continue
        data={'properties':props(image)}
        if image.packed_file: data['bytes']=hashlib.sha256(image.packed_file.data).hexdigest()
        elif image.source=='FILE':
            path=Path(bpy.path.abspath(image.filepath,library=image.library))
            if not path.is_file(): raise RuntimeError('unavailable image dependency: '+str(path))
            data['bytes']=hashlib.sha256(path.read_bytes()).hexdigest()
        images[image.name]=digest(data)
    preserve['images']=images
    for lib in bpy.data.libraries:
        path=Path(bpy.path.abspath(lib.filepath))
        preserve.setdefault('linked_libraries',{})[lib.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    preserve['shape_keys']={key.name:digest(props(key)) for key in bpy.data.shape_keys}
    shape_keys={key.name:{block.name:block.value for block in key.key_blocks} for key in bpy.data.shape_keys}
    return {'objects':objects,'shape_keys':shape_keys,'preserve':preserve}

# GPU setup is the only runtime-local device override. Engine, samples and
# resolution are never altered. Audit after setup so source CPU flags normalize.
prefs=bpy.context.preferences.addons['cycles'].preferences
prefs.compute_device_type='CUDA'
prefs.refresh_devices()
assert any(d.type=='CUDA' for d in prefs.devices), 'CUDA unavailable; CPU fallback forbidden'
for d in prefs.devices: d.use=(d.type=='CUDA')
# Only the active scene is rendered. Inactive scenes can use different engines;
# leave their engine AND device settings intact, retaining them in full audits.
s=bpy.context.scene
assert s.render.engine=='CYCLES', 'unsupported non-Cycles engine; not silently changed'
s.cycles.device='GPU'
assert s.camera is not None, 'scene has no active camera'
evidence={'compute_device_type':prefs.compute_device_type,
          'devices':[{'name':d.name,'type':d.type,'use':bool(d.use),'id':d.id} for d in prefs.devices],
          'scene_device':s.cycles.device,'engine':s.render.engine,'samples':s.cycles.samples,
          'resolution':[s.render.resolution_x,s.render.resolution_y,s.render.resolution_percentage],
          'blender_version':bpy.app.version_string,'blender_version_tuple':list(bpy.app.version),
          'build_hash':bpy.app.build_hash.decode(errors='replace'),'python_executable':sys.executable,
          'pid':os.getpid()}
if cfg.get('save'):
    bpy.context.preferences.filepaths.save_version=0
    bpy.ops.wm.save_as_mainfile(filepath=cfg['save'], relative_remap=True)
record={'audit':audit(),'evidence':evidence,'material_lifecycle':material_lifecycle(),
        'settings':{'engine':s.render.engine,'samples':s.cycles.samples,
                    'resolution_x':s.render.resolution_x,'resolution_y':s.render.resolution_y,
                    'resolution_percentage':s.render.resolution_percentage}}
Path(cfg['report']).write_text(json.dumps(record,sort_keys=True,allow_nan=False))
print('__FULL_AUDIT_READY__',flush=True)
if cfg.get('png'):
    # Output encoding and destination are transient and are never saved.
    s.render.filepath=cfg['png']
    s.render.image_settings.file_format='PNG'
    s.render.use_file_extension=True
    bpy.ops.render.render(write_still=True)
    print('__FULL_RENDER_FINISHED__',flush=True)
'''


def _execute(source, policy, bpy_python, bpy_version, output, event_path, render, start=None):
    # Preserve the venv executable path: resolving its symlink loses venv imports.
    python = Path(bpy_python).absolute()
    if not python.is_file():
        raise FileNotFoundError(python)
    if not bpy_version:
        raise ValueError('explicit bpy version pin required')
    source = Path(source).resolve(strict=True)
    if source.suffix != '.blend':
        raise ValueError('source must be a saved .blend')
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    _disk(output, policy)
    original = sha(source)
    png = output / ('initial_verified.png' if start else 'verified.png') if render else None
    saved = output / 'start_initialized.blend' if start else None
    config = {'source': str(source), 'report': str(output / 'audit.json'), 'bpy_version': bpy_version,
              'save': str(saved) if saved else None, 'png': str(png) if png else None}
    if start:
        config['initialization'] = policy['initialization']
        if sha(start) != policy['start_sha256']:
            raise ValueError('verified start.py hash drift')
    script = output / 'worker.py'
    script.write_text(WORKER)
    config_path = output / 'worker.json'
    config_path.write_text(json.dumps(config))
    try:
        process = run_process([str(python), '-u', str(script), str(config_path)], output / 'runtime.log', event_path, policy)
    finally:
        if sha(source) != original:
            emit(event_path, 'source_changed', source=str(source), task=policy.get('task'))
            raise ValueError('source blend changed during independent verification')
    result = json.loads(Path(config['report']).read_text())
    evidence = result['evidence']
    if (not evidence['blender_version'].startswith(bpy_version) or evidence['compute_device_type'] != 'CUDA'
            or evidence['scene_device'] != 'GPU' or not any(d['type']=='CUDA' and d['use'] for d in evidence['devices'])
            or any(d['use'] and d['type']!='CUDA' for d in evidence['devices'])):
        raise ValueError('custom bpy CUDA evidence failed')
    result.update(task=policy.get('task'), source=str(source), source_sha256=original,
                  blend=str(saved or source), blend_sha256=sha(saved or source),
                  runtime=process, output=str(output), audit_path=config['report'],
                  png=str(png) if png else None)
    if png:
        from PIL import Image
        with Image.open(png) as image:
            if image.format != 'PNG': raise ValueError('render is not PNG')
            image.load()
            dimensions = list(image.size)
        settings = result['settings']
        expected = [max(1, settings[k]*settings['resolution_percentage']//100) for k in ('resolution_x','resolution_y')]
        if dimensions != expected:
            raise ValueError(f'decoded PNG dimensions {dimensions} != task {expected}')
        result.update(png_dimensions=dimensions, png_sha256=sha(png))
        emit(event_path, 'render_completed', task=policy.get('task'), png=str(png),
             png_sha256=result['png_sha256'], png_dimensions=dimensions,
             blend=result['blend'], blend_sha256=result['blend_sha256'],
             runtime=process, evidence=evidence, source_sha256=original)
    (output / 'metadata.json').write_text(json.dumps(result, indent=2, sort_keys=True))
    return result


def inspect_render(blend, task_policy, bpy_python, bpy_version, output, event_path, render=True):
    """Independently open/audit/render an unchanged saved blend via custom bpy."""
    return _execute(blend, task_policy, bpy_python, bpy_version, output, event_path, render)


def initialize(inp, task_policy, bpy_python, bpy_version, output, event_path):
    """Apply parent-reviewed literal initialization; save and render the state.

    inp is a directory with exactly one top-level .blend and start.py, or a mapping
    with explicit blend/start_py paths. No goal/oracle files are inspected.
    """
    if task_policy.get('pin_verified') is not True or not task_policy.get('start_sha256') or not isinstance(task_policy.get('initialization'), list):
        raise ValueError('initialization requires parent-verified pin/policy, start_sha256 and literal initialization')
    if task_policy.get('reviewed') is False:
        raise ValueError('unsupported or unreviewed initialization policy')
    if isinstance(inp, dict):
        source, start = Path(inp['blend']), Path(inp['start_py'])
    else:
        inp = Path(inp)
        sources = list(inp.glob('*.blend'))
        if len(sources) != 1:
            raise ValueError('expected exactly one safe top-level input blend')
        source, start = sources[0], inp / 'start.py'
    if start.name != 'start.py' or sha(start) != task_policy['start_sha256']:
        raise ValueError('parent-verified start.py hash mismatch')
    return _execute(source, task_policy, bpy_python, bpy_version, output, event_path, True, start.resolve())
