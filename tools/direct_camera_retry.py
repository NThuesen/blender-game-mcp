"""One fresh Camera3 retry after a normally finished suite; never preempt it.

Launch with nohup/setsid and retain stdout. A STOP in either output cancels the
queue. An interrupted/crashed predecessor is NOT equivalent to a finished suite.
The existing global flock is held through native admission, generation and score.
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

import direct_suite as suite
import direct_runtime as runtime


def suite_finished(output):
    if (output / 'STOP').exists():
        raise InterruptedError('predecessor STOP: retry cancelled')
    try:
        with (output / 'events.jsonl').open() as stream:
            last = None
            for line in stream:
                if line.strip():
                    last = json.loads(line)
        return bool(last and last.get('event') == 'dispatcher_finished')
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def try_lock(lock):
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--after-suite', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.absolute()
    predecessor = args.after_suite.resolve(strict=True)
    out.mkdir(parents=True, exist_ok=False)
    events = out / 'events.jsonl'
    process = {'pid': os.getpid(), 'argv': sys.argv, 'cwd': os.getcwd(),
               'after_suite': str(predecessor), 'task': 'level1/camera3'}
    (out / 'process.json').write_text(json.dumps(process, indent=2))
    def status(state, **extra):
        row = dict(process, status=state, **extra)
        temp = out / 'status.tmp'
        temp.write_text(json.dumps(row, indent=2))
        temp.replace(out / 'status.json')
        runtime.emit(events, state, **process, **extra)
    try:
        with (suite.ROOT / 'direct-suite.lock').open('a') as lock:
            while True:
                if (out / 'STOP').exists():
                    raise InterruptedError('retry STOP')
                finished = suite_finished(predecessor)
                if finished and try_lock(lock):
                    break
                status('waiting_for_suite', predecessor_finished=finished)
                time.sleep(10)
            # Recheck under the lock before any native GPU or model work.
            if not suite_finished(predecessor) or (out / 'STOP').exists():
                raise InterruptedError('admission cancelled')
            sys.path.insert(0, str(suite.ROOT / 'input-tools'))
            import dataset
            tasks = json.loads((suite.ROOT / 'input-tools/task-policies.json').read_text())['tasks']
            if set(tasks) != set(dataset.task_ids()):
                raise ValueError('canonical policy/manifest task mismatch')
            config = suite.direct.prepare_config(tomllib.loads(
                (suite.ROOT / 'mghavn-custom-bpy.toml').read_text()), approved=True)
            version = subprocess.check_output(['/home/mg/.local/bin/codex', '--version'], text=True).strip()
            if version != 'codex-cli 0.154.0':
                raise ValueError('unreviewed Codex version')
            status('running')
            suite.run_task('level1/camera3', tasks['level1/camera3'],
                           out / 'level1-camera3', config, dataset)
            status('complete')
    except BaseException as exc:
        status('failed', error=repr(exc))
        raise


if __name__ == '__main__':
    main()
