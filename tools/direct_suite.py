"""External sequential dispatcher: native admission, direct Codex, post-run audit/score.
No model reasoning loop, score feedback, VLM judge, or upstream script execution.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib
import uuid
from types import SimpleNamespace

import direct_codex as direct
import direct_runtime as runtime

ROOT = Path('/home/mg/blenderbench-direct')
FIELDS = ('allowed_transforms', 'allowed_camera_data', 'allowed_light_data',
          'allowed_shape_keys', 'location_bounds')


def task_plan(tasks):
    plan = [t for t in tasks if t != 'level1/camera1']
    if len(tasks) != 27 or len(plan) != 26:
        raise ValueError('expected 27 canonical tasks, 26 remaining')
    return plan


def run_task(task, policy, out, config, dataset):
    inp = ROOT / 'dataset' / task
    verification = dataset.verify(task, inp)
    policy = dict(policy, task=task, pin_verified=True, stop_file=str(out.parent / 'STOP'))
    if any(field not in policy for field in FIELDS):
        raise ValueError('missing exact task policy fields')
    out.mkdir(parents=True, exist_ok=False)
    (out / 'input-verification.json').write_text(json.dumps(verification, indent=2))
    events = out / 'events.jsonl'
    runtime.emit(events, 'initializing', task=task)
    initial = runtime.initialize(inp, policy, runtime.CUSTOM_BPY_PYTHON, out / 'work', events)
    work = out / 'work'
    blend = work / 'initial.blend'
    Path(initial['blend']).rename(blend)
    baseline = runtime.inspect_render(blend, policy, runtime.CUSTOM_BPY_PYTHON, out / 'admission', events)
    reopened = runtime.inspect_render(blend, policy, runtime.CUSTOM_BPY_PYTHON, out / 'reopen', events, render=False)
    if initial['audit'] != baseline['audit'] or baseline['audit'] != reopened['audit']:
        raise ValueError('initialization/reopen independent invariant mismatch')
    initial_hash = runtime.sha(blend)
    runtime.emit(events, 'native_admission_passed', task=task, source_sha256=initial_hash)
    args = SimpleNamespace(codex='/home/mg/.local/bin/codex', model='gpt-6-astra', workdir=work)
    evidence = out / 'codex'
    evidence.mkdir(mode=0o700)
    (evidence / 'config.json').write_text(json.dumps(config, indent=2))
    nonce = uuid.uuid4().hex
    code = 'import bpy,sys; result=' + repr({'preflight': nonce})
    code += '; result.update(python=sys.executable,version=bpy.app.version_string)'
    prompt = ('Call only execute_blender_code_for_cli once with blend_file=' + str(blend)
              + ' and code=' + code + '. Do not mutate/save. No shell or other tools.')
    preflight = direct.invoke(args, config, evidence, 'preflight', prompt)
    direct.verify_preflight(preflight, nonce, runtime.CUSTOM_BPY_PYTHON)
    runtime.emit(events, 'preflight_passed', task=task)
    contract = ('Solve this BlenderBench task by direct use of the four Blender MCP tools.\n'
        + policy['task_description'] + '\nTarget image is attached. Initial scene: ' + str(blend)
        + '\nExact mutable-property policy (all unlisted scene data must remain unchanged):\n'
        + json.dumps({f: policy[f] for f in FIELDS}, indent=2)
        + '\nPerform exactly 10 meaningful edit/render/visual-inspection rounds sequentially. '
        'Inspect initial scene and image, reason yourself about edits, render and inspect each checkpoint '
        'using view_image. Do not delegate reasoning to scripts/controllers. No CLIP/scoring, VLM judge, '
        'oracle/goal .blend access, or score feedback. Do not read audit or evaluation files. '
        'Use only execute_blender_code_for_cli, get_runtime_python_api_docs_for_cli, search_api_docs, '
        'get_python_api_docs for scene work; no shell scene operations. '
        'The backend code-call deadline remains 120 seconds; the MCP client deadline remains 86400 seconds. '
        'Use CUDA rendering, enable CUDA devices only and disable CPU; retain engine, samples, resolution '
        'and all other saved scene settings. Save scene BEFORE transient PNG output setting changes; '
        'never save the render filepath/encoding overrides. Disable backup save_version. '
        'Never alter initial.blend. Do not create/delete objects or alter unlisted properties. '
        'Every round must be a distinct allowed edit, not inspection/no-op or a repeat of any earlier scene state. '
        'Save absolute paths ' + str(work / 'iteration01.blend') + ' and '
        + str(work / 'iteration01.png') + ' through iteration10.blend and iteration10.png. '
        'Append rounds.jsonl in this work directory via MCP, one JSON object per completed round: '
        'round integer 1..10, blend absolute path, png absolute path, rationale explaining the visual '
        'hypothesis and actual edit. Do not invent camera poses for non-camera tasks. '
        'Each checkpoint must remain directly openable with dependencies intact. '
        'Stop only after all ten rounds and their images and ledger rows are written.')
    runtime.emit(events, 'generation_started', task=task)
    direct.invoke(args, config, evidence, 'run', contract, inp / 'target.png')
    if runtime.sha(blend) != initial_hash:
        raise ValueError('Codex modified initial.blend')
    artifacts = direct.verify_checkpoints(work, 10, policy=policy)
    (out / 'artifact-check.json').write_text(json.dumps(artifacts, indent=2))
    runtime.emit(events, 'generation_finished', task=task)
    # All external scoring occurs only AFTER the full direct generation session.
    previous = baseline
    seen = {json.dumps(baseline['audit'], sort_keys=True)}
    rows = []
    for n in range(1, 11):
        audit = runtime.inspect_render(work / f'iteration{n:02}.blend', policy,
            runtime.CUSTOM_BPY_PYTHON, out / f'audit{n:02}', events)
        direct.validate_edit(baseline, audit, policy)
        direct.validate_edit(previous, audit, policy)
        state = json.dumps(audit['audit'], sort_keys=True)
        if state in seen:
            raise ValueError('repeated allowed scene state')
        seen.add(state)
        previous = audit
        score_dir = out / f'score{n:02}'
        score_dir.mkdir()
        runtime.run_process([ROOT / 'score-venv/bin/python', ROOT / 'blenderbench-score-pair.py',
            audit['png'], inp / 'target.png'], score_dir / 'score.json', events, policy)
        score = json.loads((score_dir / 'score.json').read_text())
        rows.append(dict(round=n, **score, blend=audit['blend'], blend_sha256=audit['blend_sha256']))
        (out / 'scores.json').write_text(json.dumps(rows, indent=2))
    best = min(rows, key=lambda row: (row['raw_nclip'], row['round']))
    (out / 'result.json').write_text(json.dumps({'status': 'complete', 'task': task,
        'rounds': rows, 'best': best, 'feedback_to_generation': False, 'vlm_judge': False}, indent=2))
    runtime.emit(events, 'task_complete', task=task, rounds=len(rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.absolute()
    out.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'direct-suite.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sys.path.insert(0, str(ROOT / 'input-tools'))
        import dataset
        tasks = json.loads((ROOT / 'input-tools/task-policies.json').read_text())['tasks']
        plan = task_plan(tasks)
        if set(tasks) != set(dataset.task_ids()):
            raise ValueError('canonical policy/manifest task mismatch')
        config = direct.prepare_config(tomllib.loads((ROOT / 'mghavn-custom-bpy.toml').read_text()), approved=True)
        version = subprocess.check_output(['/home/mg/.local/bin/codex', '--version'], text=True).strip()
        if version != 'codex-cli 0.154.0':
            raise ValueError('unreviewed Codex version')
        runtime.emit(out / 'events.jsonl', 'dispatcher_started', pid=os.getpid(), tasks=plan,
                     backend_deadline_seconds=120, client_deadline_seconds=86400)
        (out / 'process.json').write_text(json.dumps({'pid': os.getpid(), 'argv': sys.argv,
            'cwd': os.getcwd(), 'tasks': plan, 'model': 'gpt-6-astra'}, indent=2))
        states = {}
        def status(task, state, **extra):
            states[task] = dict(status=state, **extra)
            temp = out / 'status.tmp'
            temp.write_text(json.dumps(states, indent=2))
            temp.replace(out / 'status.json')
            runtime.emit(out / 'events.jsonl', state, task=task, **extra)
        for task in plan:
            if (out / 'STOP').exists():
                break
            task_out = out / task.replace('/', '-')
            if task_out.exists():
                raise ValueError('fresh task output required; automatic restart forbidden')
            # Do not duplicate the active downloader. Wait for its verified checkpoint.
            checkpoint = ROOT / 'dataset' / task / 'checkpoint.json'
            while True:
                if (out / 'STOP').exists():
                    raise InterruptedError('manual STOP')
                try:
                    staging = json.loads(checkpoint.read_text())
                except (FileNotFoundError, json.JSONDecodeError):
                    staging = {}
                if staging.get('status') == 'verified':
                    break
                if staging.get('status') == 'failed':
                    status(task, 'input_failed', error=staging.get('error'))
                    break
                status(task, 'waiting_for_staging')
                time.sleep(10)
            if staging.get('status') != 'verified':
                continue
            status(task, 'running', output=str(task_out))
            try:
                run_task(task, tasks[task], task_out, config, dataset)
            except Exception as exc:
                status(task, 'failed', error=repr(exc), output=str(task_out))
            else:
                status(task, 'complete', output=str(task_out))
        runtime.emit(out / 'events.jsonl', 'dispatcher_finished', states=states)


if __name__ == '__main__':
    main()
