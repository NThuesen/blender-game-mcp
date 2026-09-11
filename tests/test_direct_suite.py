"""Synthetic orchestration contracts, never benchmark evidence."""
import importlib.util
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))

class SuiteTests(unittest.TestCase):
    def gpu_setup(self, active_engine='CYCLES'):
        from types import SimpleNamespace as NS
        import direct_runtime
        active = NS(render=NS(engine=active_engine), cycles=NS(device='CPU'))
        inactive = NS(render=NS(engine='BLENDER_EEVEE'), cycles=NS(device='CPU'))
        devices = [NS(type='CUDA', use=False), NS(type='CPU', use=True)]
        prefs = NS(compute_device_type='NONE', refresh_devices=lambda: None, devices=devices)
        bpy = NS(context=NS(scene=active, preferences=NS(addons={'cycles': NS(preferences=prefs)})),
                 data=NS(scenes=[inactive, active]))
        # Exercise the actual worker setup without importing native bpy locally.
        setup = direct_runtime.WORKER.split('# GPU setup', 1)[1].split("assert s.camera", 1)[0]
        exec('# GPU setup' + setup, {'bpy': bpy})
        return active, inactive, devices

    def test_inactive_eevee_scene_is_preserved_with_active_cycles(self):
        active, inactive, devices = self.gpu_setup()
        self.assertEqual(active.render.engine, 'CYCLES')
        self.assertEqual(active.cycles.device, 'GPU')
        self.assertEqual(inactive.render.engine, 'BLENDER_EEVEE')
        self.assertEqual(inactive.cycles.device, 'CPU')
        self.assertEqual([d.use for d in devices], [True, False])

    def test_active_eevee_still_fails_closed_without_conversion(self):
        with self.assertRaisesRegex(AssertionError, 'unsupported non-Cycles'):
            self.gpu_setup('BLENDER_EEVEE')

    def test_rna_shared_graph_is_linear_and_preserves_leaves(self):
        import ast
        from types import SimpleNamespace
        import direct_runtime
        tree = ast.parse(direct_runtime.WORKER)
        names = {'scalar', 'ref', 'collection_props', 'props'}
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        class ID: pass
        class ShapeKey: pass
        ns = {'bpy': SimpleNamespace(types=SimpleNamespace(ID=ID, ShapeKey=ShapeKey)), 'SKIP': set()}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), 'worker-test', 'exec'), ns)
        pointer = lambda key: SimpleNamespace(identifier=key, type='POINTER', is_readonly=False)
        value = SimpleNamespace(identifier='value', type='INT', is_readonly=False)
        class Node:
            calls = 0
            bl_rna = SimpleNamespace(identifier='Node', properties=[pointer('left'), pointer('right'), value])
            def __init__(self, child=None): self.left = self.right = child; self.value = 1
            def as_pointer(self):
                Node.calls += 1
                return id(self)
        leaf = Node()
        node = leaf
        for _ in range(12): node = Node(node)
        a = ns['props'](node)
        self.assertLess(Node.calls, 40, 'shared graph expands exponentially')
        leaf.value = 2
        self.assertNotEqual(a, ns['props'](node), 'leaf content must remain audited')

    def test_heterogeneous_rna_collection_uses_each_schema(self):
        import ast
        from types import SimpleNamespace
        import direct_runtime
        tree = ast.parse(direct_runtime.WORKER)
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'collection_props']
        class ID: pass
        class ShapeKey: pass
        ns = {'bpy': SimpleNamespace(types=SimpleNamespace(ID=ID, ShapeKey=ShapeKey)),
              'SKIP': set(), 'props': lambda x, **kw: {'type': x.bl_rna.identifier} if x else None}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), 'worker-test', 'exec'), ns)
        field = SimpleNamespace(identifier='text', type='POINTER', is_readonly=False)
        a = SimpleNamespace(bl_rna=SimpleNamespace(identifier='NodeA', properties=[field]), text=None)
        b = SimpleNamespace(bl_rna=SimpleNamespace(identifier='NodeB', properties=[]))
        class Values(list):
            def foreach_get(self, *args): pass
        got = ns['collection_props'](Values([a, b] * 70), ())
        self.assertEqual(len(got), 140)
        self.assertEqual(got[1], {'type': 'NodeB'})

    def test_nonfunctional_legacy_retry_and_stale_validation_docs_are_removed(self):
        self.assertFalse((TOOLS / 'direct_camera_retry.py').exists())
        self.assertFalse((TOOLS / 'direct_camera_retry.md').exists())
        self.assertFalse((TOOLS / 'direct_saved_validation.md').exists())


if __name__ == '__main__':
    unittest.main()
