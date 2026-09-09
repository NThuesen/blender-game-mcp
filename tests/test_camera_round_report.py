"""SYNTHETIC unit fixtures only, not benchmark score evidence."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from benchmarks.blenderbench_camera.round_report import build, sha, write_report


class SyntheticReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-round-report-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = []
        self.scores = []

    def round(self, n, content, pl, nclip):
        p = self.root/f'{n}.png'
        p.write_bytes(content)  # Synthetic byte-hash fixture, not a rendered PNG.
        arm = self.root/f'arm-{n}'
        arm.mkdir()
        item = {'id':'same-id-across-sessions','type':'mcp_tool_call'}
        (arm/'events.json').write_text(json.dumps([{'type':kind,'item':item} for kind in ('item.started','item.updated','item.completed')]))
        self.ledger += [dict(round=n,phase='round_start',attempt=1,elapsed_seconds=n*10),dict(round=n,phase='model_session_finished',attempt=1,elapsed_seconds=n*10+1,summary=str(arm/'summary.json')),dict(round=n,phase='candidate_checkpoint',attempt=1,elapsed_seconds=n*10+5,png=str(p),png_sha256=sha(p))]
        self.scores.append(dict(path=str(p),candidate=True,sha256=sha(p),raw_pl=pl,raw_nclip=nclip))

    def save(self, scores=True):
        (self.root/'round-ledger.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in self.ledger))
        (self.root/'provenance.json').write_text(json.dumps({'synthetic':True,'pinned':'fixture'}))
        if scores:
            (self.root/'clip_scores.json').write_text(json.dumps({'rows':self.scores,'model_revision':'synthetic-revision'}))

    def test_paired_best_not_independent_pl_and_tie(self):
        self.round(1,b'a',.01,.5)
        self.round(2,b'b',.8,.2)
        self.round(3,b'c',.001,.2)
        self.save()
        result=build(self.root,True)
        rows=result['rows']
        self.assertEqual(len(rows),10)
        self.assertEqual((rows[2]['best_round'],rows[2]['best_paired_raw_pl']),(2,.8))
        self.assertEqual(rows[2]['cumulative_tool_calls'],3)
        self.assertEqual(rows[0]['elapsed_seconds'],5)
        self.assertIsNone(rows[3]['best_raw_nclip'])
        self.assertEqual(result['original_provenance']['pinned'],'fixture')

    def test_deduplicated_png_maps_by_candidate_hash(self):
        self.round(1,b'identical',.6,.4)
        self.round(2,b'identical',.6,.4)
        self.scores.pop()
        self.scores.insert(0,dict(path='initial',candidate=False,sha256=self.scores[0]['sha256'],raw_pl=0,raw_nclip=0))
        self.save()
        rows=build(self.root,True)['rows']
        self.assertEqual(rows[1]['raw_nclip'],.4)
        self.assertEqual(rows[1]['best_round'],1)
        self.assertEqual(rows[1]['score_source_path'],str(self.root/'1.png'))

    def test_missing_scores_failed_and_unreached(self):
        self.round(1,b'a',.1,.1)
        self.ledger += [dict(round=2,phase='round_start',attempt=1,elapsed_seconds=20),dict(round=2,phase='attempt_failed',elapsed_seconds=22),dict(round=2,phase='task_failed',elapsed_seconds=23)]
        self.save(False)
        result=write_report(self.root,synthetic=True)
        rows=result['rows']
        self.assertEqual(rows[0]['score_status'],'unscored')
        self.assertEqual(rows[1]['status'],'failed')
        self.assertEqual(rows[2]['status'],'unreached')
        self.assertIsNone(rows[1]['tool_calls'])
        self.assertTrue(all(r['raw_pl'] is None for r in rows))
        with (self.root/'round-report.csv').open() as f:
            csvrows=list(csv.DictReader(f))
        self.assertEqual(len(csvrows),10)
        self.assertEqual(csvrows[1]['raw_pl'],'null')
        self.assertIn('SYNTHETIC TEST FIXTURE',(self.root/'round-report.md').read_text())
        self.assertTrue(json.loads((self.root/'round-report.json').read_text())['synthetic'])

    def test_tamper_and_missing_events_null(self):
        self.round(1,b'a',.1,.1)
        self.save()
        (self.root/'1.png').write_bytes(b'tampered')
        (self.root/'arm-1/events.json').unlink()
        row=build(self.root,True)['rows'][0]
        self.assertEqual(row['score_status'],'checkpoint_missing_or_hash_mismatch')
        self.assertIsNone(row['raw_pl'])
        self.assertIsNone(row['tool_calls'])

    def test_partial_live_ledger_and_score_view_fallback(self):
        self.round(1,b'a',.1,.1)
        self.save()
        view=self.root/'score-view'
        view.mkdir()
        (self.root/'clip_scores.json').rename(view/'clip_scores.json')
        with (self.root/'round-ledger.jsonl').open('a') as f:
            f.write('{"round":')
        result=build(self.root,True)
        self.assertEqual(result['scored_rounds'],1)
        self.assertEqual(len(result['warnings']),1)

    def test_nonfinite_score_is_not_selected(self):
        self.round(1,b'a',.1,float('nan'))
        self.save()
        self.assertIsNone(build(self.root,True)['rows'][0]['raw_nclip'])


    def test_failed_round_carries_previous_best(self):
        self.round(1,b'a',.9,.1)
        self.ledger += [dict(round=2,phase='round_start',attempt=1,elapsed_seconds=20),dict(round=2,phase='task_failed',elapsed_seconds=23)]
        self.save()
        row=build(self.root,True)['rows'][1]
        self.assertEqual((row['best_round'],row['best_paired_raw_pl']),(1,.9))
        self.assertIsNone(row['raw_pl'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
