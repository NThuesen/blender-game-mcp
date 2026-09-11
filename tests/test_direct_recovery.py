import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import direct_suite as suite

class RecoveryTests(unittest.TestCase):
    def test_recovery_scores_existing_work_without_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'old'
            work = old / 'work'
            work.mkdir(parents=True)
            (work / 'initial.blend').write_bytes(b'original')
            baseline = {'blend_sha256': suite.runtime.sha(work / 'initial.blend'), 'audit': {}}
            (work / 'metadata.json').write_text(json.dumps(baseline))
            dataset = SimpleNamespace(verify=lambda *a: {'verified': True})
            with patch.object(suite, 'ROOT', root), patch.object(suite.direct, 'invoke', side_effect=AssertionError('regeneration')), patch.object(suite, 'score_checkpoints') as score:
                suite.recover_task('level2/attribute1', {}, root / 'new', old, dataset)
                self.assertEqual(score.call_args.args[2], work)
                self.assertTrue((root / 'new/recovery.json').is_file())

    def test_recovery_rejects_modified_initial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / 'old/work'
            work.mkdir(parents=True)
            (work / 'initial.blend').write_bytes(b'changed')
            (work / 'metadata.json').write_text(json.dumps({'blend_sha256': 'original'}))
            with self.assertRaisesRegex(ValueError, 'initial.blend'):
                suite.recover_task('level2/attribute1', {}, root / 'new', work.parent, SimpleNamespace(verify=lambda *a: {}))

if __name__ == '__main__': unittest.main()
