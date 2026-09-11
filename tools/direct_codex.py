"""Direct Codex launch/config preflight; no benchmark controller or scoring."""
import copy

TOOLS = ('execute_blender_code_for_cli', 'get_render_as_image_for_cli',
         'get_runtime_python_api_docs_for_cli', 'search_api_docs')
CODEX_VERSION = 'codex-cli 0.154.0'
CODEX_SOURCE_REVISION = '6b9826e3aa83b1a5947db50f4332cb9c65f1b340'
CODEX_FEATURE_REGISTRY_URL = (
    'https://github.com/openai/codex/blob/' + CODEX_SOURCE_REVISION
    + '/codex-rs/features/src/lib.rs')
DISABLED_FEATURES = (
    'shell_tool', 'unified_exec', 'unified_exec_tty', 'shell_snapshot', 'view_image',
    'sleep_tool', 'code_mode', 'code_mode_host', 'code_mode_prewarm',
    'request_permissions_tool', 'multi_agent', 'multi_agent_v2', 'apps', 'enable_mcp_apps',
    'tool_suggest', 'plugins', 'hooks', 'in_app_browser', 'in_app_local_automation',
    'browser_use', 'browser_use_full_cdp_access', 'browser_use_external', 'computer_use',
    'remote_plugin', 'plugin_sharing', 'image_generation', 'standalone_web_search',
    'web_search_request', 'web_search_cached')


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
    # Codex 0.154 otherwise defers MCP tools through tool_search, whose dynamic
    # calls are not represented by exec JSONL. Force the four tools direct.
    server['omit_tools_from'] = ['deferred', 'code_mode']
    return result


def verify_generation_workdir(root):
    """Require the mounted generation root to contain only initialized runtime inputs."""
    import hashlib
    from pathlib import Path
    root = Path(root).absolute()
    allowed = {'initial.blend', 'initial_verified.png', 'audit.json', 'metadata.json',
               'runtime.log', 'worker.py', 'worker.json'}
    if root.is_symlink() or not root.is_dir():
        raise ValueError('generation runtime root must be a regular directory')
    entries = list(root.iterdir())
    unexpected = sorted(entry.name for entry in entries if entry.name not in allowed)
    if unexpected or any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise ValueError(f'generation runtime root contains undeclared paths: {unexpected}')
    initial = root / 'initial.blend'
    if not initial.is_file():
        raise ValueError('generation runtime root requires initial.blend')
    worker = root / 'worker.py'
    if worker.exists():
        try:
            from tools import direct_runtime
        except ModuleNotFoundError:  # direct script invocation adds tools/ to sys.path
            import direct_runtime
        expected = hashlib.sha256(direct_runtime.WORKER.encode()).hexdigest()
        if hashlib.sha256(worker.read_bytes()).hexdigest() != expected:
            raise ValueError('generation runtime worker.py differs from canonical runtime bytes')


def verify_preflight(events, nonce, python, bpy_version='5.2.1'):
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
                and str(payload.get('version', '')).startswith(bpy_version)):
            return
    raise ValueError(f'no successful standalone bpy {bpy_version} MCP preflight result')


def verify_generation_events(events, rounds=None, workdir=None):
    """Reject any generation operation outside the four approved MCP tools."""
    import json
    import re
    forbidden = re.compile(
        r'(?i)(goal(?:_code)?\.py|\bsolution(?:\.py)?\b|oracle|scores?\.json|scoring[/\\]|'
        r'aggregate\.json|ref_based_eval|evaluator|clipmodel|photometric_loss)')
    seen = {}
    completed = set()
    visual_checkpoints = []
    pending_checkpoint = None
    active_edit = None
    turn_completed = 0
    for index, event in enumerate(events):
        if event.get('type') in ('error', 'turn.failed'):
            raise ValueError('generation event stream reports failure')
        if event.get('type') == 'turn.completed':
            turn_completed += 1
            if index != len(events) - 1:
                raise ValueError('generation turn completion must terminate the event stream')
            continue
        if event.get('type') not in ('item.started', 'item.updated', 'item.completed'):
            continue
        item = event.get('item')
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            raise ValueError('malformed generation item event')
        ident, kind = item['id'], item.get('type')
        signature = (kind, item.get('server'), item.get('tool'))
        if ident in seen and seen[ident] != signature:
            raise ValueError('generation item identity changed in event stream')
        seen[ident] = signature
        if kind in ('reasoning', 'agent_message'):
            continue
        if kind != 'mcp_tool_call':
            raise ValueError(f'prohibited Codex generation operation: {kind!r}')
        if item.get('server') != 'blender' or item.get('tool') not in TOOLS:
            raise ValueError('generation used a tool outside the four approved Blender MCP tools')
        arguments = json.dumps(item.get('arguments', {}), sort_keys=True, default=str)
        if forbidden.search(arguments):
            raise ValueError('generation MCP arguments access prohibited goal/solution or score feedback')
        if event.get('type') == 'item.started' and item.get('tool') == 'execute_blender_code_for_cli':
            if active_edit is not None or pending_checkpoint is not None:
                raise ValueError('next edit dispatch preceded visual inspection of prior checkpoint')
            active_edit = ident
        if event.get('type') == 'item.completed':
            if ident in completed:
                raise ValueError('duplicate generation item completion')
            completed.add(ident)
            result = item.get('result') or {}
            if (item.get('status') != 'completed' or item.get('error')
                    or (isinstance(result, dict) and result.get('isError'))):
                raise ValueError('generation MCP tool call failed')
            if item.get('tool') == 'execute_blender_code_for_cli':
                if pending_checkpoint is not None or (active_edit is not None and active_edit != ident):
                    raise ValueError('next edit dispatch preceded visual inspection of prior checkpoint')
                structured = result.get('structured_content') if isinstance(result, dict) else None
                if isinstance(structured, dict) and isinstance(structured.get('_checkpoint'), dict):
                    pending_checkpoint = structured['_checkpoint']
                active_edit = None
            if item.get('tool') == 'get_render_as_image_for_cli':
                content = result.get('content', []) if isinstance(result, dict) else []
                if not any(isinstance(value, dict) and value.get('type') == 'image'
                           and value.get('mimeType') == 'image/png' and value.get('data')
                           for value in content):
                    raise ValueError('visual inspection tool did not return PNG image content')
                arguments = item.get('arguments')
                if not isinstance(arguments, dict) or not isinstance(arguments.get('blend_file'), str):
                    raise ValueError('visual inspection requires a saved checkpoint path')
                path = __import__('pathlib').Path(arguments['blend_file']).absolute()
                metadata = next((value.get('_meta', value.get('meta')) for value in content
                                 if isinstance(value, dict) and value.get('type') == 'image'), None)
                if workdir is not None:
                    expected_path = (__import__('pathlib').Path(workdir).resolve()
                                     / f'iteration{len(visual_checkpoints)+1:02}.blend')
                    if path != expected_path:
                        raise ValueError('visual inspection checkpoint path or source hash is invalid')
                    path = path.resolve(strict=True)
                    import hashlib
                    with path.open('rb') as stream:
                        actual_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
                    source_path = (__import__('pathlib').Path(workdir).resolve() / 'initial.blend'
                                   if not visual_checkpoints else
                                   __import__('pathlib').Path(workdir).resolve()
                                   / f'iteration{len(visual_checkpoints):02}.blend')
                    if not isinstance(pending_checkpoint, dict):
                        raise ValueError('visual inspection requires an attested checkpoint creation')
                    with source_path.open('rb') as stream:
                        source_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if (not isinstance(metadata, dict)
                            or metadata.get('source') != str(path)
                            or metadata.get('source_sha256') != actual_hash
                            or pending_checkpoint.get('path') != str(path)
                            or pending_checkpoint.get('source') != str(source_path)
                            or pending_checkpoint.get('source_sha256') != source_hash
                            or pending_checkpoint.get('output_sha256') != actual_hash
                            or pending_checkpoint.get('existed_before') is not False):
                        raise ValueError('visual inspection checkpoint path or source hash is invalid')
                visual_checkpoints.append(path.name)
                pending_checkpoint = None
    if any(ident not in completed and signature[0] == 'mcp_tool_call'
           for ident, signature in seen.items()):
        raise ValueError('generation event stream contains unfinished MCP tool call')
    if turn_completed != 1 or not completed:
        raise ValueError('generation event stream is empty or lacks a completed turn/tool call')
    if rounds is not None:
        expected = [f'iteration{number:02}.blend' for number in range(1, rounds + 1)]
        if visual_checkpoints != expected:
            raise ValueError(f'visual inspection checkpoint sequence must be exactly {expected}')
        if pending_checkpoint is not None:
            raise ValueError('generation created an uninspected checkpoint')


def validate_edit(before, after, policy):
    """Require finite, meaningful saved changes; local edit policy is ignored."""
    import math
    left = copy.deepcopy(before.get('audit', before))
    right = copy.deepcopy(after.get('audit', after))
    # Evidenced orphan cleanup alone is not a meaningful editing round.
    materials = left.get('preserve', {}).get('materials', {})
    saved_materials = right.get('preserve', {}).get('materials', {})
    for name in set(materials) - set(saved_materials):
        lifecycle = before.get('material_lifecycle', {}).get(name, {})
        if (type(lifecycle.get('users')) is int and lifecycle['users'] == 0
                and lifecycle.get('use_fake_user') is False
                and lifecycle.get('use_extra_user') is False
                and 'library' in lifecycle and lifecycle['library'] is None):
            del materials[name]
    def finite(value):
        if isinstance(value, dict):
            return all(finite(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return all(finite(v) for v in value)
        return not isinstance(value, float) or math.isfinite(value)

    if not finite(left) or not finite(right):
        raise ValueError('nonfinite scene property')
    if left == right:
        raise ValueError('no meaningful scene change')


def verify_checkpoints(root, rounds, policy=None):
    """Artifact gate only: independent scene/GPU/invariant audit still required."""
    import hashlib
    import json
    import math
    from pathlib import Path
    from PIL import Image
    root = Path(root).expanduser().absolute()
    for component in (root, *root.parents):
        if component.is_symlink():
            raise ValueError(f'symlink forbidden in checkpoint path: {component}')
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
    command = [args.codex]
    for feature in DISABLED_FEATURES:
        command += ['--disable', feature]
    command += ['exec', '--ignore-user-config', '--strict-config',
               '--skip-git-repo-check', '--sandbox', 'read-only', '--model', args.model,
               '--json', '-C', str(args.workdir), '-c', 'approval_policy="never"',
               '-c', 'web_search="disabled"',
               '-c', 'tools.experimental_request_user_input=false',
               '-c', 'tools.update_plan=false',
               '-c', 'mcp_servers=' + toml_value(config['mcp_servers']),
               '-o', str(evidence / (label + '.final.txt'))]
    if target:
        import hashlib
        target = target.resolve(strict=True)
        (evidence / (label + '.visible-input.json')).write_text(json.dumps({
            'mechanism': 'Codex exec -i bootstrap attachment; Codex 0.154 JSONL does not emit ImageView',
            'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))
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
    parser.add_argument('--codex', required=True)
    parser.add_argument('--codex-version', required=True)
    parser.add_argument('--bpy-version', required=True)
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
        verify_generation_workdir(args.workdir)
        if args.mode == 'run':
            if not args.prompt or not args.target or args.rounds < 1:
                raise ValueError('run requires --prompt, --target, and positive --rounds')
            prompt = args.prompt.read_text()
            args.target = args.target.resolve(strict=True)
            if (args.workdir / 'rounds.jsonl').exists() or any(args.workdir.glob('iteration*')):
                raise ValueError('run requires fresh checkpoint paths; resume is unsupported')
        version = subprocess.check_output([args.codex, '--version'], text=True).strip()
        if args.codex_version != CODEX_VERSION or version != CODEX_VERSION:
            raise ValueError(f'publication protocol requires exactly {CODEX_VERSION!r}; got {version!r}')
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
        verify_preflight(events, nonce, config['mcp_servers']['blender']['env']['BLENDER_MCP_BPY_PYTHON'], args.bpy_version)
        if args.mode == 'run':
            contract = ('\nUse only these MCP tools for scene operations: ' + ', '.join(TOOLS)
                        + '. No shell scene operations, benchmark controller, VLM judge, scoring, or publication. '
                        'After every saved round call get_render_as_image_for_cli on that round checkpoint and '
                        'visually inspect its returned PNG content; built-in view_image is disabled. Write rounds.jsonl via MCP with one row per '
                        'completed round: round (integer), pose={location:[x,y,z],rotation:[x,y,z]}, '
                        'blend and png (absolute paths), rationale. Save iteration01.blend/iteration01.png '
                        f'through iteration{args.rounds:02}.blend/iteration{args.rounds:02}.png. '
                        'For each edit set execute_blender_code_for_cli blend_file to initial.blend or the preceding '
                        'iteration and expected_output_blend to the new iteration blend. '
                        'Inspection/no-op calls are not rounds. Never alter initial.blend.')
            events = invoke(args, config, args.evidence, 'run', prompt + contract, args.target)
            verify_generation_events(events, rounds=args.rounds, workdir=args.workdir)
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
