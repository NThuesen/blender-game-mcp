"""Synthetic orchestration contracts, never benchmark evidence."""
import importlib.util
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))

class SuiteTests(unittest.TestCase):
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

    def test_plan_excludes_only_completed_camera1(self):
        script = TOOLS / 'direct_suite.py'
        self.assertTrue(script.exists(), 'suite dispatcher missing')
        spec = importlib.util.spec_from_file_location('suite', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tasks = {'level1/camera1': {}}
        tasks.update({f'level2/task{i}': {} for i in range(26)})
        plan = module.task_plan(tasks)
        self.assertEqual(len(plan), 26)
        self.assertNotIn('level1/camera1', plan)
        with self.assertRaises(ValueError):
            module.task_plan({'level1/camera1': {}})

if __name__ == '__main__':
    unittest.main()
