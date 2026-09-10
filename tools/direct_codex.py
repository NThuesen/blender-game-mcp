"""Direct Codex launch/config preflight; no benchmark controller or scoring."""
import copy

TOOLS = ('execute_blender_code_for_cli', 'get_runtime_python_api_docs_for_cli',
         'search_api_docs', 'get_python_api_docs')


def prepare_config(config, *, approved):
    if not approved:
        raise ValueError('explicit approval required: --approve-blender-tools')
    if set(config) != {'mcp_servers'} or set(config['mcp_servers']) != {'blender'}:
        raise ValueError('config must contain only mcp_servers.blender')
    result = copy.deepcopy(config)
    server = result['mcp_servers']['blender']
    allowed = {'command', 'args', 'cwd', 'env', 'enabled_tools', 'required',
               'startup_timeout_sec', 'tool_timeout_sec'}
    if set(server) - allowed:
        raise ValueError('unsupported or conflicting MCP settings')
    if sorted(server.get('enabled_tools', [])) != sorted(TOOLS):
        raise ValueError('enabled_tools must be exactly the four standalone tools')
    env = server.get('env', {})
    from pathlib import Path
    if (env.get('BLENDER_MCP_CLI_BACKEND') != 'bpy'
            or not Path(env.get('BLENDER_MCP_BPY_PYTHON', '')).is_absolute()
            or not Path(server.get('command', '')).is_absolute()):
        raise ValueError('absolute server and standalone bpy interpreter required')
    server['tools'] = {tool: {'approval_mode': 'approve'} for tool in TOOLS}
    server['required'] = True
    return result


def verify_preflight(events, nonce, python):
    """Require a completed real MCP item, never trust the final assistant text."""
    import json
    for event in events:
        item = event.get('item', {})
        if (event.get('type') != 'item.completed' or item.get('type') != 'mcp_tool_call'
                or item.get('server') != 'blender' or item.get('tool') != TOOLS[0]):
            continue
        result = item.get('result') or {}
        if item.get('status') != 'completed' or item.get('error') or result.get('isError'):
            continue
        payload = result.get('structured_content')
        if payload is None:
            try:
                payload = json.loads(''.join(c.get('text', '') for c in result.get('content', [])))
            except (ValueError, TypeError):
                continue
        if (isinstance(payload, dict) and payload.get('preflight') == nonce
                and payload.get('python') == python
                and str(payload.get('version', '')).split()[:1] == ['5.2.1']):
            return
    raise ValueError('no successful standalone bpy 5.2.1 MCP preflight result')


def validate_edit(before, after, policy):
    """Compare independent RNA audits, masking only exact task allowances."""
    import math
    left = copy.deepcopy(before.get('audit', before))
    right = copy.deepcopy(after.get('audit', after))
    # Only disappearance of an independently evidenced, local, zero-user
    # material is normal save/reopen cleanup. Keep present material contents,
    # all additions and every object/slot/geometry/light/camera audit exact.
    materials = left.get('preserve', {}).get('materials', {})
    saved_materials = right.get('preserve', {}).get('materials', {})
    for name in set(materials) - set(saved_materials):
        lifecycle = before.get('material_lifecycle', {}).get(name, {})
        if (type(lifecycle.get('users')) is int and lifecycle['users'] == 0
                and lifecycle.get('use_fake_user') is False
                and lifecycle.get('use_extra_user') is False
                and 'library' in lifecycle and lifecycle['library'] is None):
            del materials[name]
    changed = False
    if policy.get('unsupported_blocker'):
        raise ValueError('unsupported task policy')

    def remove(a, b, field, width=None):
        nonlocal changed
        av, bv = a[field], b[field]
        for value in (av, bv):
            values = value if isinstance(value, list) else [value]
            if width is not None and len(values) != width:
                raise ValueError('invalid property dimensions')
            if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
                raise ValueError('nonfinite or nonnumeric allowed property')
        changed |= av != bv
        del a[field]
        del b[field]

    try:
        for name, fields in policy.get('allowed_transforms', {}).items():
            if not fields or len(set(fields)) != len(fields) or set(fields) - {'location', 'rotation_euler', 'scale'}:
                raise ValueError('forbidden transform policy')
            for field in fields:
                remove(left['objects'][name], right['objects'][name], field, 3)
        for name, fields in policy.get('allowed_camera_data', {}).items():
            if fields != ['lens']:
                raise ValueError('forbidden camera data policy')
            a, b = left['objects'][name], right['objects'][name]
            if a['type'] != 'CAMERA' or b['type'] != 'CAMERA':
                raise ValueError('lens allowance requires camera')
            remove(a, b, 'lens', 1)
        for name, fields in policy.get('allowed_light_data', {}).items():
            if not fields or len(set(fields)) != len(fields) or set(fields) - {'energy', 'color'}:
                raise ValueError('forbidden light policy')
            a, b = left['objects'][name], right['objects'][name]
            if a['type'] != 'LIGHT' or b['type'] != 'LIGHT':
                raise ValueError('light allowance requires light')
            for field in fields:
                remove(a['light_data'], b['light_data'], field, 3 if field == 'color' else 1)
        for name, fields in policy.get('allowed_shape_keys', {}).items():
            if not fields or len(set(fields)) != len(fields):
                raise ValueError('invalid shape key policy')
            for field in fields:
                remove(left['shape_keys'][name], right['shape_keys'][name], field, 1)
        for name, bounds in policy.get('location_bounds', {}).items():
            location = after.get('audit', after)['objects'][name]['location']
            if len(location) != 3 or len(bounds) != 3 or any(not lo <= x <= hi for x, (lo, hi) in zip(location, bounds)):
                raise ValueError('location outside bounds')
    except (KeyError, TypeError) as exc:
        raise ValueError('missing or malformed policy property') from exc
    if left != right:
        raise ValueError('forbidden scene edits outside task policy')
    if not changed:
        raise ValueError('no allowed scene change')


def verify_checkpoints(root, rounds, policy=None):
    """Artifact gate only: independent scene/GPU/invariant audit still required."""
    import hashlib
    import json
    import math
    from PIL import Image
    try:
        rows = [json.loads(line) for line in (root / 'rounds.jsonl').read_text().splitlines()]
        if rounds < 1 or [r['round'] for r in rows] != list(range(1, rounds + 1)):
            raise ValueError('missing, duplicate, or unordered rounds')
        hashes, poses, evidence = set(), set(), []
        for n, row in enumerate(rows, 1):
            blend, png = root / f'iteration{n:02}.blend', root / f'iteration{n:02}.png'
            if row['blend'] != str(blend) or row['png'] != str(png):
                raise ValueError('checkpoint paths must match expected absolute paths')
            if blend.is_symlink() or png.is_symlink() or not blend.stat().st_size:
                raise ValueError('checkpoint must be a nonempty regular file, not a symlink')
            with blend.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if policy is None:
                pose = tuple(row['pose']['location'] + row['pose']['rotation'])
                if len(pose) != 6 or not all(type(v) in (int, float) and math.isfinite(v) for v in pose):
                    raise ValueError('pose must contain six finite numbers')
                if pose in poses:
                    raise ValueError('duplicate reported pose')
                poses.add(pose)
            elif not isinstance(row.get('rationale'), str) or not row['rationale'].strip():
                raise ValueError('task checkpoint requires rationale')
            if digest in hashes:
                raise ValueError('duplicate checkpoint bytes')
            hashes.add(digest)
            with Image.open(png) as image:
                if image.format != 'PNG':
                    raise ValueError('checkpoint image must be PNG')
                image.load()
                dimensions = list(image.size)
            evidence.append({'round': n, 'blend_sha256': digest, 'dimensions': dimensions})
        return evidence
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError(f'incomplete checkpoint artifacts: {exc}') from exc


def toml_value(value):
    """Encode nested inline TOML, preserving literal dotted keys and booleans."""
    import json
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k) + '=' + toml_value(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    return json.dumps(value, allow_nan=False)


def invoke(args, config, evidence, label, prompt, target=None):
    import json
    import subprocess
    command = [args.codex, 'exec', '--ignore-user-config', '--strict-config',
               '--skip-git-repo-check', '--sandbox', 'workspace-write', '--model', args.model,
               '--json', '-C', str(args.workdir), '-c', 'approval_policy="never"',
               '-c', 'mcp_servers=' + toml_value(config['mcp_servers']),
               '-o', str(evidence / (label + '.final.txt'))]
    if target:
        command += ['-i', str(target)]
    command += ['-']
    (evidence / (label + '.argv.json')).write_text(json.dumps(command, indent=2))
    (evidence / (label + '.prompt.txt')).write_text(prompt)
    with (evidence / (label + '.jsonl')).open('x') as out, (evidence / (label + '.stderr')).open('x') as err:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err, cwd=args.workdir)
        (evidence / (label + '.process.json')).write_text(json.dumps({'pid': proc.pid}))
        proc.communicate(prompt.encode())
    (evidence / (label + '.exit-code.txt')).write_text(str(proc.returncode))
    if proc.returncode:
        raise ValueError(f'{label}: Codex exited {proc.returncode}; inspect retained logs')
    return [json.loads(line) for line in (evidence / (label + '.jsonl')).read_text().splitlines()]


def main(argv=None):
    import argparse
    import json
    import subprocess
    import sys
    import tomllib
    import uuid
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['preflight', 'run'])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True, help='fresh private evidence directory')
    parser.add_argument('--model', required=True)
    parser.add_argument('--approve-blender-tools', action='store_true',
                        help='explicitly preapprove arbitrary code and docs on the configured MCP server')
    parser.add_argument('--prompt', type=Path)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--rounds', type=int, default=10)
    args = parser.parse_args(argv)
    prompt = ''
    try:
        config = prepare_config(tomllib.loads(args.config.read_text()), approved=args.approve_blender_tools)
        args.workdir = args.workdir.resolve(strict=True)
        args.evidence = args.evidence.absolute()
        if not (args.workdir / 'initial.blend').is_file():
            raise ValueError('workdir requires initial.blend')
        if args.mode == 'run':
            if not args.prompt or not args.target or args.rounds < 1:
                raise ValueError('run requires --prompt, --target, and positive --rounds')
            prompt = args.prompt.read_text()
            args.target = args.target.resolve(strict=True)
            if (args.workdir / 'rounds.jsonl').exists() or any(args.workdir.glob('iteration*')):
                raise ValueError('run requires fresh checkpoint paths; resume is unsupported')
        version = subprocess.check_output([args.codex, '--version'], text=True).strip()
        if version != 'codex-cli 0.154.0':
            raise ValueError(f'unsupported Codex version {version!r}; review schema before updating pin')
        args.evidence.mkdir(parents=True, exist_ok=False, mode=0o700)
        (args.evidence / 'config.json').write_text(json.dumps(config, indent=2))
        (args.evidence / 'codex-version.txt').write_text(version)
        nonce = uuid.uuid4().hex
        code = ('import bpy,sys; result={"preflight":' + repr(nonce)
                + ',"python":sys.executable,"version":bpy.app.version_string}')
        preflight = ('Call only execute_blender_code_for_cli once with blend_file='
                     + str(args.workdir / 'initial.blend') + ' and code=' + code
                     + '. Do not mutate or save the scene. Do not use shell or other tools.')
        events = invoke(args, config, args.evidence, 'preflight', preflight)
        verify_preflight(events, nonce, config['mcp_servers']['blender']['env']['BLENDER_MCP_BPY_PYTHON'])
        if args.mode == 'run':
            contract = ('\nUse only these MCP tools for scene operations: ' + ', '.join(TOOLS)
                        + '. No shell scene operations, benchmark controller, VLM judge, scoring, or publication. '
                        'Use view_image for image inspection. Write rounds.jsonl via MCP with one row per '
                        'completed round: round (integer), pose={location:[x,y,z],rotation:[x,y,z]}, '
                        'blend and png (absolute paths), rationale. Save iteration01.blend/iteration01.png '
                        f'through iteration{args.rounds:02}.blend/iteration{args.rounds:02}.png. '
                        'Inspection/no-op calls are not rounds. Never alter initial.blend.')
            invoke(args, config, args.evidence, 'run', prompt + contract, args.target)
            checkpoints = verify_checkpoints(args.workdir, args.rounds)
            (args.evidence / 'artifact-check.json').write_text(json.dumps({
                'status': 'artifacts_complete_not_scene_audited', 'checkpoints': checkpoints}, indent=2))
            print('Checkpoint artifacts complete; independent scene/GPU/invariant audit and scoring remain required.')
        else:
            print('Standalone bpy 5.2.1 MCP preflight passed; generation not run.')
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
