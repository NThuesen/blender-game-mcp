"""User correction: edit validity is not a task allowlist."""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import direct_codex as direct

class UnrestrictedTests(unittest.TestCase):
    def test_all_scene_edits_ignore_local_policy(self):
        before = {'objects': {'Cube': {'location': [0, 0, 0]}},
                  'shape_keys': {'Key': {'Smile': 0}},
                  'preserve': {'materials': {'used': 'old'}, 'meshes': {'mesh': 'old'}}}
        mutations = [
            lambda a: a['objects'].update(New={'type': 'LIGHT', 'energy': 300}),
            lambda a: a['objects'].pop('Cube'),
            lambda a: a['objects']['Cube'].update(location=[999, 0, 0]),
            lambda a: a['objects'].update(Camera={'lens': 60}),
            lambda a: a['shape_keys']['Key'].update(Smile=1),
            lambda a: a['preserve']['materials'].update(used='new'),
            lambda a: a['preserve']['meshes'].update(mesh='new'),
        ]
        for mutate in mutations:
            after = copy.deepcopy(before)
            mutate(after)
            direct.validate_edit(before, after, {'unsupported_blocker': True,
                'location_bounds': {'Cube': [[-1, 1]] * 3}})

    def test_nonfinite_and_noop_still_invalid(self):
        before = {'objects': {'Cube': {'location': [0, 0, 0]}}}
        with self.assertRaisesRegex(ValueError, 'no meaningful scene change'):
            direct.validate_edit(before, before, {})
        after = copy.deepcopy(before)
        after['objects']['Cube']['location'][0] = float('nan')
        with self.assertRaisesRegex(ValueError, 'finite'):
            direct.validate_edit(before, after, {})
