"""Private GPU setup, bounded subprocesses and validated copy-on-write staging."""
from __future__ import annotations
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
from .rounds import sha, dump


def gpu_code(device='auto', system=None):
    system = system or platform.system()
    if device == 'auto':
        device = 'METAL' if system == 'Darwin' else 'CUDA'
    if (system == 'Darwin' and device != 'METAL') or (system == 'Linux' and device not in ('CUDA', 'OPTIX')):
        raise ValueError('GPU must be METAL on macOS or CUDA/OPTIX on Linux')
    if system not in ('Darwin', 'Linux'):
        raise ValueError('Only macOS and Linux are supported')
    return '''import bpy, json
_gpu_prefs=bpy.context.preferences.addons['cycles'].preferences
_gpu_prefs.compute_device_type=DEVICE
_gpu_prefs.refresh_devices()
if not any(d.type==DEVICE for d in _gpu_prefs.devices):
    raise RuntimeError('Required GPU unavailable; CPU fallback forbidden: '+DEVICE)
for d in _gpu_prefs.devices: d.use=(d.type==DEVICE)
for s in bpy.data.scenes:
    if s.render.engine!='CYCLES': raise RuntimeError('Camera port currently requires Cycles; engine not changed')
    s.cycles.device='GPU'
bpy.context.preferences.filepaths.save_version=0
_metal_evidence={'compute_device_type':_gpu_prefs.compute_device_type,
 'devices':[{'name':d.name,'type':d.type,'use':d.use,'id':d.id} for d in _gpu_prefs.devices],
 'scene_device':bpy.context.scene.cycles.device,'engine':bpy.context.scene.render.engine,
 'samples':bpy.context.scene.cycles.samples,
 'resolution':[bpy.context.scene.render.resolution_x,bpy.context.scene.render.resolution_y,bpy.context.scene.render.resolution_percentage],
 'blender_version':bpy.app.version_string}
print('__TRIAL_GPU__'+json.dumps(_metal_evidence),flush=True)
'''.replace('DEVICE', repr(device))


def clone(src, dst):
    src, dst = Path(src), Path(dst)
    if dst.exists(): raise FileExistsError(dst)
    if platform.system() == 'Darwin':
        lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
        fn=lib.clonefile; fn.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int]
        if fn(os.fsencode(src),os.fsencode(dst),0): raise OSError(ctypes.get_errno(),'clonefile failed',str(dst))
    elif platform.system() == 'Linux':
        try:
            with src.open('rb') as source, dst.open('xb') as target:
                fcntl.ioctl(target.fileno(),0x40049409,source.fileno())  # Linux FICLONE
        except BaseException:
            dst.unlink(missing_ok=True)
            raise
    else: raise RuntimeError('No copy-on-write implementation for this OS')
    if src.stat().st_ino == dst.stat().st_ino or sha(src) != sha(dst):
        raise ValueError('CoW clone validation failed')
    return str(dst)


def storage_gate(directory, scene_bytes=0, reserve=1024**3):
    directory=Path(directory)
    while not directory.exists(): directory=directory.parent
    free=shutil.disk_usage(directory).free
    required=6*scene_bytes+reserve
    if free < required: raise OSError(f'Insufficient free disk: {free} < {required}')
    return {'free_bytes':free,'required_bytes':required,'method':'CoW only; no hardlink/full-copy fallback'}


def command(argv, log, timeout, env=None):
    with Path(log).open('x') as stream:
        p=subprocess.Popen(list(map(str,argv)),stdout=stream,stderr=subprocess.STDOUT,env=env,start_new_session=True)
        try:
            rc=p.wait(timeout=timeout)
        except BaseException:
            os.killpg(p.pid,signal.SIGKILL); p.wait(); raise
    if rc: raise RuntimeError(f'Process exit {rc}; see {log}')


def freeze(repo, root, setup):
    """Snapshot source code once, never environments, assets or vendored evaluators."""
    repo,root=Path(repo),Path(root)
    dest=root/'frozen/server-source'
    dest.mkdir(parents=True)
    original={}
    for p in sorted((repo/'mcp/blmcp').rglob('*')):
        if not p.is_file() or p.suffix not in ('.py','.json','.toml','.md','.rst','.txt','.yaml','.yml'): continue
        rel=p.relative_to(repo)
        q=dest/rel; q.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,q)
        original[str(rel)]=sha(p)
    backend=dest/'mcp/blmcp/tools_helpers/blender_cli.py'
    text=backend.read_text()
    if text.count('_CLI_TIMEOUT = 120.0')!=1: raise ValueError('Reviewed backend timeout patch no longer matches')
    anchor='    config = _resolve_cli_backend()\n'
    if text.count(anchor)!=1: raise ValueError('Reviewed GPU injection no longer matches')
    text=text.replace('_CLI_TIMEOUT = 120.0','_CLI_TIMEOUT = 600.0').replace(anchor,anchor+'    code = '+repr(setup)+' + "\\n" + code\n')
    compile(text,str(backend),'exec'); backend.write_text(text)
    hashes={str(p.relative_to(dest)):sha(p) for p in dest.rglob('*') if p.is_file()}
    harness={str(p.relative_to(repo)):sha(p) for folder in ('benchmarks/blenderbench_camera','benchmarks/token_efficiency') for p in (repo/folder).glob('*.py')}
    dump(root/'source-provenance.json',{'original_sha256':original,'frozen_sha256':hashes,'harness_sha256':harness,'gpu_setup_sha256':hashlib.sha256(setup.encode()).hexdigest(),'patch':'600-second backend timeout; subprocess-local GPU setup after scene open'})
    return dest


def verify_frozen(root):
    p=Path(root)/'source-provenance.json'
    if not p.exists(): raise ValueError('Legacy run lacks portable source provenance; use report/score, not automatic continuation')
    record=json.loads(p.read_text()); source=Path(root)/'frozen/server-source'
    repo=Path(__file__).resolve().parents[2]
    for rel,h in record.get('harness_sha256',{}).items():
        if sha(repo/rel)!=h:raise ValueError('Harness drift prevents automatic continuation: '+rel)
    for rel,h in record['frozen_sha256'].items():
        if sha(source/rel)!=h: raise ValueError('Frozen source drift: '+rel)
    return source
