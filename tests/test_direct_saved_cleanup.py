"""Saved delivery audits tolerate only evidenced orphan material removal."""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import direct_codex as direct


class SavedCleanupTests(unittest.TestCase):
    def fixture(self):
        before = {'audit': {'objects': {'Camera': {'type': 'CAMERA', 'lens': 50}},
                  'preserve': {'materials': {'orphan': 'orphan-hash', 'used': 'used-hash'},
                               'meshes': {'mesh': 'geometry-hash'},
                               'scenes': {'Scene': 'lighting-hash'}}},
                  'material_lifecycle': {'orphan': {'users': 0, 'use_fake_user': False,
                                         'use_extra_user': False, 'library': None},
                                         'used': {'users': 1, 'use_fake_user': False,
                                         'use_extra_user': False, 'library': None}}}
        after = copy.deepcopy(before)
        after['audit']['objects']['Camera']['lens'] = 55
        del after['audit']['preserve']['materials']['orphan']
        del after['material_lifecycle']['orphan']
        return before, after, {'allowed_camera_data': {'Camera': ['lens']}}

    def test_worker_records_material_lifecycle_without_mutating(self):
        import ast
        from types import SimpleNamespace as NS
        import direct_runtime
        functions = [n for n in ast.parse(direct_runtime.WORKER).body
                     if isinstance(n, ast.FunctionDef) and n.name == 'material_lifecycle']
        self.assertEqual(len(functions), 1)
        material = NS(name='orphan', users=0, use_fake_user=False,
                      use_extra_user=False, library=None)
        ns = {'bpy': NS(data=NS(materials=[material]))}
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'worker', 'exec'), ns)
        self.assertEqual(ns['material_lifecycle']()['orphan'],
                         self.fixture()[0]['material_lifecycle']['orphan'])
        self.assertFalse(material.use_fake_user)

    def test_generation_starts_without_independent_reopen_gate(self):
        import tempfile
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        import direct_suite as suite
        class GenerationReached(Exception): pass
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def initialize(inp, policy, python, output, events):
                output.mkdir()
                blend = output / 'start_initialized.blend'
                blend.write_bytes(b'fixture')
                return {'blend': str(blend), **self.fixture()[0]}
            def invoke(args, config, evidence, label, prompt, target=None):
                if label == 'run':
                    self.assertIn('previous saved checkpoint', prompt)
                    raise GenerationReached()
                return []
            policy = {f: {} for f in suite.FIELDS}
            policy['task_description'] = 'test'
            with patch.object(suite, 'ROOT', root), \
                 patch.object(suite.runtime, 'initialize', initialize), \
                 patch.object(suite.runtime, 'inspect_render', side_effect=AssertionError('pre-generation reopen')), \
                 patch.object(suite.direct, 'invoke', invoke), \
                 patch.object(suite.direct, 'verify_preflight'):
                with self.assertRaises(GenerationReached):
                    suite.run_task('level2/attribute1', policy, root / 'task', {},
                                   NS(verify=lambda *a: {}))

    def test_zero_user_unused_material_loss_is_accepted(self):
        before, after, policy = self.fixture()
        original = copy.deepcopy(before)
        direct.validate_edit(before, after, policy)
        self.assertEqual(before, original)

    def test_used_material_change_or_loss_is_rejected(self):
        for value in ('changed-hash', None):
            before, after, policy = self.fixture()
            if value is None:
                del after['audit']['preserve']['materials']['used']
            else:
                after['audit']['preserve']['materials']['used'] = value
            with self.assertRaisesRegex(ValueError, 'forbidden scene edits'):
                direct.validate_edit(before, after, policy)

    def test_missing_lifecycle_and_protected_orphans_fail_closed(self):
        for field, value in [('users', 1), ('use_fake_user', True),
                             ('use_extra_user', True), ('library', '//linked.blend'),
                             ('users', None)]:
            before, after, policy = self.fixture()
            before['material_lifecycle']['orphan'][field] = value
            with self.assertRaisesRegex(ValueError, 'forbidden scene edits'):
                direct.validate_edit(before, after, policy)
        before, after, policy = self.fixture()
        del before['material_lifecycle']
        with self.assertRaisesRegex(ValueError, 'forbidden scene edits'):
            direct.validate_edit(before, after, policy)

    def test_geometry_lighting_and_material_additions_still_fail(self):
        for collection, name in [('meshes', 'mesh'), ('scenes', 'Scene'), ('materials', 'new')]:
            before, after, policy = self.fixture()
            after['audit']['preserve'][collection][name] = 'changed'
            with self.assertRaisesRegex(ValueError, 'forbidden scene edits'):
                direct.validate_edit(before, after, policy)

    def test_cleanup_alone_is_not_a_meaningful_round(self):
        before, after, policy = self.fixture()
        after['audit']['objects']['Camera']['lens'] = 50
        with self.assertRaisesRegex(ValueError, 'no allowed scene change'):
            direct.validate_edit(before, after, policy)

    def test_present_orphan_content_remains_audited(self):
        before, after, policy = self.fixture()
        after['audit']['preserve']['materials']['orphan'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'forbidden scene edits'):
            direct.validate_edit(before, after, policy)


if __name__ == '__main__':
    unittest.main()
