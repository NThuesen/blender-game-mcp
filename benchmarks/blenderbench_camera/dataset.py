"""Pinned camera-only asset downloader; solution scripts are never selected."""
import ast, hashlib,json,os,time,urllib.request
from pathlib import Path
SHA = '203e4d325e9438ca55b29bdfc4f6a90842d74e68'


REVIEWED = {
 'camera2': ((-3.4813,4.5001,0.5115),(1.6678,0.0000,3.8693),None),
 'camera3': ((6.7303,-4.8700,3.7385),(1.2811,-0.0000,0.9355),50),
 'camera4': ((1.1400,0.8500,0.5300),(2.4435,1.1170,2.6354),None),
 'camera5': ((-1.2250,0.7100,-2.8300),(-0.4363,3.3161,0.0349),None),
 'camera6': ((2.4689,-2.6258,1.3083),(1.4060,0.0000,0.7626),None),
 'camera7': ((1.3353,1.4740,1.5357),(-0.7679,0.2094,0.0000),None),
 'camera8': ((7.3589,-6.9258,4.9583),(1.2664,0.0000,0.7800),None),
 'camera9': ((0.1104,-2.6639,9.8843),(0.3142,0,0),None),
}


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,indent=2)+'\n'); tmp.replace(path)


def load(path): return json.loads(path.read_text())


def hash_file(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def verify_asset(path,row):
    assert path.stat().st_size==row['size'], f'size mismatch {path}'
    sha=hash_file(path)
    if row.get('lfs'):
        assert row['lfs']['size']==row['size']
        assert sha==row['lfs']['oid'].removeprefix('sha256:'), f'LFS hash mismatch {path}'
    else:
        h=hashlib.sha1(f'blob {path.stat().st_size}\0'.encode())
        with path.open('rb') as f:
            for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
        assert h.hexdigest()==row['oid'], f'git blob mismatch {path}'
    return sha


def download(path,row):
    from .runtime import storage_gate
    storage_gate(path.parent, reserve=row['size']+1024**3)
    url=f"https://huggingface.co/datasets/DietCoke4671/BlenderBench/resolve/{SHA}/{row['path']}"
    if path.exists(): return {'url':url,'sha256':verify_asset(path,row),'bytes':path.stat().st_size,'cached':True}
    path.parent.mkdir(parents=True,exist_ok=True)
    part=path.with_suffix(path.suffix+'.part')
    # Stream to disk, hash during transport, and verify cached LFS/git metadata.
    for attempt in range(3):
        try:
            h=hashlib.sha256(); n=0
            with urllib.request.urlopen(url,timeout=120) as r, part.open('wb') as f:
                while True:
                    b=r.read(1024*1024)
                    if not b: break
                    f.write(b); h.update(b); n+=len(b)
                f.flush(); os.fsync(f.fileno())
            assert n==row['size'],f'truncated download: {path}: {n}'
            actual=verify_asset(part,row); assert actual==h.hexdigest()
            part.replace(path)
            return {'url':url,'sha256':actual,'bytes':n,'cached':False}
        except Exception:
            if attempt==2: raise
            time.sleep(2**attempt)


def inspect_start(task,path):
    tree=ast.parse(path.read_text()); loc,rot,lens=REVIEWED[task]
    assert isinstance(tree.body[0],ast.Import) and [(a.name,a.asname) for a in tree.body[0].names]==[('bpy',None)]
    expected=[('bpy.data.objects["Camera1"].location',loc),('bpy.data.objects["Camera1"].rotation_euler',rot)]
    if lens is not None: expected.append(('bpy.data.objects["Camera1"].data.lens',lens))
    assert len(tree.body)==len(expected)+1, 'Unreviewed extra Python statements'
    for node,(target,value) in zip(tree.body[1:],expected):
        assert isinstance(node,ast.Assign) and len(node.targets)==1
        assert ast.dump(node.targets[0])==ast.dump(ast.parse(target+' = 0').body[0].targets[0])
        assert ast.literal_eval(node.value)==value, 'Pinned start camera differs from inspected script'
    return {'sha256':hash_file(path),'review':'Full script inspected; exact camera-only AST and numeric values verified','source':path.read_text()}


def entries(task):
    if task not in REVIEWED: raise ValueError('Supported tasks: camera2 through camera9 only')
    manifest=load(Path(__file__).with_name('assets-manifest.json'))
    if manifest['revision']!=SHA: raise ValueError('Dataset revision drift')
    prefix=f'data/blenderbench/level1/{task}/'
    rows=[]
    for row in manifest['entries']:
        if not row['path'].startswith(prefix): continue
        rel=row['path'][len(prefix):]
        if Path(rel).is_absolute() or '..' in Path(rel).parts: raise ValueError('Unsafe asset path')
        if not (rel in ('start.py','task.txt','renders/goal/render1.png') or (rel.endswith('.blend') and 'goal' not in rel.lower())):
            raise ValueError('Unexpected asset; solution scripts forbidden')
        rows.append((rel,row))
    if len(rows)!=4 or sum(rel.endswith('.blend') for rel,_ in rows)!=1: raise ValueError('Incomplete task manifest')
    return rows


def stage(task, directory):
    inp=Path(directory)/task; inp.mkdir(parents=True,exist_ok=True)
    manifest={'task':task,'dataset':'DietCoke4671/BlenderBench','dataset_revision':SHA,'license':'CC-BY-4.0; retain upstream/per-asset attribution','files':{}}
    for rel,row in sorted(entries(task),key=lambda item:item[0]!='start.py'):
        manifest['files'][rel]=download(inp/rel,row)
        if rel=='start.py': manifest['start_review']=inspect_start(task,inp/rel)
    dump(inp/'manifest.json',manifest)
    return inp
