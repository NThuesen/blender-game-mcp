#!/usr/bin/env python3
"""Offline, read-only evidence reduction. Never invokes scorer/model/Blender.
Writes only round-report.{json,csv,md} in the requested control/report directory.
"""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def build(root, synthetic=False, scores_path=None):
    root = Path(root)
    evidence = {}
    warnings = []

    def snapshot(path):
        path = Path(path)
        data = path.read_bytes()
        evidence[str(path)] = hashlib.sha256(data).hexdigest()
        return data.decode()

    ledger = []
    lines = snapshot(root / 'round-ledger.jsonl').splitlines()
    for i, line in enumerate(lines):
        try:
            ledger.append(json.loads(line))
        except json.JSONDecodeError:
            if i != len(lines)-1:
                raise
            warnings.append('Ignored incomplete last ledger line in live snapshot')
    summary = json.loads(snapshot(root/'summary.json')) if (root/'summary.json').exists() else None
    provenance = json.loads(snapshot(root/'provenance.json')) if (root/'provenance.json').exists() else None
    score_path = Path(scores_path) if scores_path else root/'clip_scores.json'
    if not score_path.exists():
        score_path = root/'score-view/clip_scores.json'
    scores = json.loads(snapshot(score_path)) if score_path.exists() else None
    by_hash = {}
    if scores:
        for item in scores['rows']:
            # Actual old scorer key is (sha256, candidate), not filename.
            if item.get('candidate') is not True:
                continue
            if not all(finite(item.get(k)) for k in ('raw_nclip', 'raw_pl')):
                warnings.append('Invalid/nonfinite score row: '+str(item.get('path')))
                continue
            digest = item['sha256']
            if digest in by_hash and any(by_hash[digest][k] != item[k] for k in ('raw_nclip','raw_pl')):
                raise ValueError('Conflicting candidate scores for hash '+digest)
            by_hash[digest] = item
    rows = []
    best = None
    cumulative_calls = 0
    for n in range(1, 11):
        events = [e for e in ledger if e.get('round') == n]
        starts = [e for e in events if e['phase'] == 'round_start']
        candidates = [e for e in events if e['phase'] == 'candidate_checkpoint']
        if len(candidates) > 1:
            raise ValueError('Multiple verified candidates for round '+str(n))
        candidate = candidates[0] if candidates else None
        terminal = candidate or next((e for e in reversed(events) if e['phase'] == 'task_failed'), None)
        attempted = bool(starts)
        completed = candidate is not None
        finished = [e for e in events if e['phase'] == 'model_session_finished']
        observed_calls = 0
        measurable = attempted
        for event in finished:
            ep = Path(event['summary']).parent/'events.json'
            if not ep.exists():
                measurable = False
                continue
            raw = json.loads(snapshot(ep))
            calls = [e.get('item', {}) for e in raw if e.get('type') in ('item.started','item.updated','item.completed') and e.get('item',{}).get('type') == 'mcp_tool_call']
            if any(not c.get('id') for c in calls):
                measurable = False
            observed_calls += len({c['id'] for c in calls if c.get('id')})
        calls_complete = measurable and len(finished) == len(starts)
        total_calls = observed_calls if calls_complete else None
        if attempted:
            cumulative_calls = cumulative_calls + total_calls if cumulative_calls is not None and total_calls is not None else None
        row = dict(round=n, attempted=attempted, completed=completed,
                   status='completed' if completed else 'failed' if terminal else 'attempted_incomplete' if attempted else 'unreached',
                   attempts=len(starts) if attempted else None,
                   failed_attempts=sum(e['phase']=='attempt_failed' for e in events) if attempted else None,
                   failure_reasons='\n'.join(e.get('error','unknown failure') for e in events if e['phase'] in ('attempt_failed','task_failed')) or None,
                   start_elapsed_seconds=starts[0]['elapsed_seconds'] if starts else None,
                   end_elapsed_seconds=terminal['elapsed_seconds'] if terminal else None,
                   elapsed_seconds=terminal['elapsed_seconds']-starts[0]['elapsed_seconds'] if terminal and starts else None,
                   last_observed_elapsed_seconds=events[-1]['elapsed_seconds'] if events else None,
                   tool_calls=total_calls, observed_finished_session_tool_calls=observed_calls if measurable else None,
                   cumulative_tool_calls=cumulative_calls if attempted else None,
                   checkpoint_png=candidate['png'] if candidate else None,
                   checkpoint_blend=candidate.get('blend') if candidate else None,
                   checkpoint_blend_sha256=candidate.get('blend_sha256') if candidate else None,
                   checkpoint_sha256=candidate.get('png_sha256') if candidate else None,
                   score_status='unscored' if candidate else 'no_verified_checkpoint', raw_pl=None, raw_nclip=None,
                   score_source_path=None, best_round=None, best_checkpoint_png=None, best_checkpoint_sha256=None,
                   best_raw_nclip=None, best_paired_raw_pl=None)
        if candidate:
            path = Path(candidate['png'])
            if not path.exists() or sha(path) != candidate.get('png_sha256'):
                row['score_status'] = 'checkpoint_missing_or_hash_mismatch'
            else:
                match = by_hash.get(candidate['png_sha256'])
                if match:
                    row.update(raw_pl=match['raw_pl'], raw_nclip=match['raw_nclip'], score_status='scored', score_source_path=match['path'])
        if row['raw_nclip'] is not None and (best is None or row['raw_nclip'] < best['raw_nclip']):
            best = row.copy()
        # Carry forward only on reached rounds; unreached rows remain null.
        if attempted and best:
            row.update(best_round=best['round'], best_checkpoint_png=best['checkpoint_png'],
                       best_checkpoint_sha256=best['checkpoint_sha256'], best_raw_nclip=best['raw_nclip'], best_paired_raw_pl=best['raw_pl'])
        rows.append(row)
    return dict(schema_version=1, synthetic=synthetic, root=str(root), requested_rounds=10,
                score_available=scores is not None, scored_rounds=sum(r['score_status']=='scored' for r in rows),
                selection='Cumulative minimum raw NCLIP among verified scored rounds; paired PL from SAME checkpoint; earliest round wins ties. Unreached best fields null.',
                measurement='Elapsed spans first round_start through candidate_checkpoint/task_failed, including retries/verification. MCP calls are unique event IDs per finished session, not guaranteed successful executions. Missing session evidence gives null totals. Observed finished-session calls exclude unfinished sessions.',
                score_access='Offline postprocessing only; never passed to generation or model sessions.',
                task_status=summary.get('status') if summary else None,
                task_wall_seconds=summary.get('wall_seconds') if summary else None,
                original_provenance=provenance,
                scorer_provenance={k:v for k,v in scores.items() if k not in ('rows','best_clip_checkpoint')} if scores else None,
                evidence_sha256=evidence, report_source_sha256=sha(__file__), warnings=warnings, rows=rows)


def write_report(root, output=None, synthetic=False, scores_path=None):
    report = build(root, synthetic, scores_path)
    output = Path(output) if output else Path(root)
    output.mkdir(parents=True, exist_ok=True)
    rows = report['rows']
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows({k: 'null' if v is None else v for k,v in row.items()} for row in rows)
    fields = ['round','status','score_status','raw_pl','raw_nclip','best_round','best_raw_nclip','best_paired_raw_pl','elapsed_seconds','tool_calls','cumulative_tool_calls']
    md = ['# Camera per-round best-so-far report', '', '**SYNTHETIC TEST FIXTURE**' if synthetic else 'Real saved evidence snapshot (not a live-process check).', '', report['selection'], '', report['measurement'], '', 'Unscored/failed/unreached measurements are `null`; no missing scores are fabricated.', '', '| '+' | '.join(fields)+' |', '| '+' | '.join(['---']*len(fields))+' |']
    for row in rows:
        md.append('| '+' | '.join('null' if row[k] is None else str(row[k]) for k in fields)+' |')
    md += ['', f"Task status: {report['task_status']}; task wall seconds: {report['task_wall_seconds']}; scored verified rounds: {report['scored_rounds']}/10."]
    if (report.get('original_provenance') or {}).get('continuation'):
        md += ['', 'Continuation: elapsed is combined active task time, excluding downtime. Original attempts remain in the ledger; see provenance for retry semantics and clock-alignment uncertainty.']
    for row in rows:
        if row['failure_reasons']:
            md += ['', f"## Round {row['round']} failures ({row['failed_attempts']} failed attempts)", '', '```text', row['failure_reasons'], '```']
    md += ['', 'Provenance and source SHA256 pins: see sibling JSON. Scores remain offline and withheld from model.']
    for suffix, text in [('json',json.dumps(report,indent=2,allow_nan=False)+'\n'),('csv',stream.getvalue()),('md','\n'.join(md)+'\n')]:
        dest = output/('round-report.'+suffix)
        tmp = dest.with_suffix(dest.suffix+'.tmp')
        tmp.write_text(text)
        tmp.replace(dest)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = write_report(args.root, args.output)
    print(json.dumps({'root':str(args.root), 'output':str(args.output or args.root), 'scored_rounds':report['scored_rounds'], 'score_available':report['score_available'], 'rows':len(report['rows'])}))
