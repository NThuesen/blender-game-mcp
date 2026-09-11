"""Unrestricted saved edits; orphan cleanup alone is not a round."""
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


    def test_zero_user_unused_material_loss_is_accepted(self):
        before, after, policy = self.fixture()
        original = copy.deepcopy(before)
        direct.validate_edit(before, after, policy)
        self.assertEqual(before, original)

    def test_used_material_change_or_loss_is_accepted(self):
        for value in ('changed-hash', None):
            before, after, policy = self.fixture()
            if value is None:
                del after['audit']['preserve']['materials']['used']
            else:
                after['audit']['preserve']['materials']['used'] = value
            direct.validate_edit(before, after, policy)

    def test_material_edits_need_no_lifecycle_permission(self):
        for field, value in [('users', 1), ('use_fake_user', True),
                             ('use_extra_user', True), ('library', '//linked.blend'),
                             ('users', None)]:
            before, after, policy = self.fixture()
            before['material_lifecycle']['orphan'][field] = value
            direct.validate_edit(before, after, policy)
        before, after, policy = self.fixture()
        del before['material_lifecycle']
        direct.validate_edit(before, after, policy)

    def test_geometry_lighting_and_material_additions_are_accepted(self):
        for collection, name in [('meshes', 'mesh'), ('scenes', 'Scene'), ('materials', 'new')]:
            before, after, policy = self.fixture()
            after['audit']['preserve'][collection][name] = 'changed'
            direct.validate_edit(before, after, policy)

    def test_cleanup_alone_is_not_a_meaningful_round(self):
        before, after, policy = self.fixture()
        after['audit']['objects']['Camera']['lens'] = 50
        with self.assertRaisesRegex(ValueError, 'no meaningful scene change'):
            direct.validate_edit(before, after, policy)

    def test_present_orphan_content_may_change(self):
        before, after, policy = self.fixture()
        after['audit']['preserve']['materials']['orphan'] = 'changed'
        direct.validate_edit(before, after, policy)


if __name__ == '__main__':
    unittest.main()
