"""Direct launcher tests; fixtures are synthetic, not benchmark evidence."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'direct_codex.py'

class DirectCodexTests(unittest.TestCase):
    def load(self):
        self.assertTrue(SCRIPT.is_file(), 'repo-backed launcher is missing')
        spec = importlib.util.spec_from_file_location('direct_codex', SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_explicit_scoped_approval_required(self):
        m = self.load()
        config = {'mcp_servers': {'blender': {'command': '/server/bin/blender-mcp',
            'enabled_tools': list(m.TOOLS), 'env': {'BLENDER_MCP_CLI_BACKEND': 'bpy',
            'BLENDER_MCP_BPY_PYTHON': '/runtime/bin/python'}}}}
        with self.assertRaisesRegex(ValueError, 'explicit approval'):
            m.prepare_config(config, approved=False)
        result = m.prepare_config(config, approved=True)
        server = result['mcp_servers']['blender']
        self.assertEqual(server['tools'], {t: {'approval_mode': 'approve'} for t in m.TOOLS})
        self.assertNotIn('default_tools_approval_mode', server)
        self.assertNotIn('tools', config['mcp_servers']['blender'])
        self.assertTrue(server['required'])

    def test_rejects_extra_tools_and_conflicting_policy(self):
        m = self.load()
        base = {'mcp_servers': {'blender': {'command': '/server',
            'enabled_tools': list(m.TOOLS), 'env': {'BLENDER_MCP_CLI_BACKEND': 'bpy',
            'BLENDER_MCP_BPY_PYTHON': '/venv/bin/python'}}}}
        import copy
        for key, value in [('enabled_tools', list(m.TOOLS) + ['execute_blender_code']),
                           ('default_tools_approval_mode', 'auto'),
                           ('tools', {m.TOOLS[0]: {'approval_mode': 'prompt'}}),
                           ('enabled', False)]:
            config = copy.deepcopy(base)
            config['mcp_servers']['blender'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.prepare_config(config, approved=True)
        for extra in [{'approval_policy': 'never'}, {'mcp_servers': {'other': {}}}]:
            config = copy.deepcopy(base)
            config.update(extra)
            with self.assertRaises(ValueError):
                m.prepare_config(config, approved=True)

    def test_preflight_requires_successful_mcp_result_not_exit_zero(self):
        m = self.load()
        self.assertTrue(hasattr(m, 'verify_preflight'), 'result verification missing')
        import copy
        event = {'type': 'item.completed', 'item': {'type': 'mcp_tool_call',
            'server': 'blender', 'tool': m.TOOLS[0], 'status': 'completed', 'error': None,
            'result': {'structured_content': {'preflight': 'nonce',
                       'python': '/venv/bin/python', 'version': '5.2.1 LTS'}}}}
        m.verify_preflight([event], 'nonce', '/venv/bin/python')
        for events in [[], [{'type': 'turn.completed'}]]:
            with self.assertRaises(ValueError):
                m.verify_preflight(events, 'nonce', '/venv/bin/python')
        for field, value in [('error', {'message': 'MCP tool call requires approval, but approval policy is never'}),
                             ('status', 'failed'), ('result', {'isError': True}),
                             ('result', {'structured_content': {'preflight': 'nonce', 'python': '/wrong'}})]:
            broken = copy.deepcopy(event)
            broken['item'][field] = value
            with self.assertRaises(ValueError):
                m.verify_preflight([broken], 'nonce', '/venv/bin/python')

    def test_checkpoints_required_and_pngs_decoded(self):
        m = self.load()
        self.assertTrue(hasattr(m, 'verify_checkpoints'), 'checkpoint verifier missing')
        import tempfile, json
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                m.verify_checkpoints(root, 2)
            rows = []
            for n in (1, 2):
                blend, png = root / f'iteration{n:02}.blend', root / f'iteration{n:02}.png'
                blend.write_bytes(b'BLENDER' + bytes([n]))
                Image.new('RGB', (2, 2)).save(png)
                rows.append({'round': n, 'pose': {'location': [n, 0, 0], 'rotation': [0, 0, 0]},
                             'blend': str(blend), 'png': str(png)})
            (root / 'rounds.jsonl').write_text('\n'.join(map(json.dumps, rows)))
            self.assertEqual(len(m.verify_checkpoints(root, 2)), 2)
            (root / 'iteration02.png').write_bytes(b'not a PNG')
            with self.assertRaises(ValueError):
                m.verify_checkpoints(root, 2)

    def test_cli_denied_preflight_stops_run_even_with_exit_zero(self):
        m = self.load()
        self.assertTrue(hasattr(m, 'main'), 'CLI missing')
        import tempfile, json, subprocess, sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / 'codex'
            fake.write_text('#!' + sys.executable + '\nimport sys\n'
                            'print("codex-cli 0.154.0" if "--version" in sys.argv else '
                            '\'{"type":"turn.completed"}\')\n')
            fake.chmod(0o755)
            (root / 'initial.blend').write_bytes(b'fixture')
            (root / 'prompt.txt').write_text('fixture generation task')
            (root / 'target.png').write_bytes(b'fixture')
            config = root / 'server.toml'
            config.write_text('[mcp_servers.blender]\ncommand="/server"\n'
                'enabled_tools=' + json.dumps(list(m.TOOLS)) + '\n'
                '[mcp_servers.blender.env]\nBLENDER_MCP_CLI_BACKEND="bpy"\n'
                'BLENDER_MCP_BPY_PYTHON="/venv/bin/python"\n')
            args = [sys.executable, str(SCRIPT), 'run', '--config', str(config),
                    '--codex', str(fake), '--workdir', str(root), '--evidence', str(root / 'logs'),
                    '--model', 'test-model', '--approve-blender-tools',
                    '--prompt', str(root / 'prompt.txt'), '--target', str(root / 'target.png')]
            proc = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn('no successful', proc.stderr)
            self.assertFalse((root / 'logs' / 'run.jsonl').exists())
            argv = json.loads((root / 'logs' / 'preflight.argv.json').read_text())
            self.assertIn('--strict-config', argv)
            self.assertIn('workspace-write', argv)
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', argv)
            self.assertTrue(any('approval_mode' in a and 'approve' in a for a in argv))
            # A successful preflight followed by exit 0 and zero rounds still fails.
            fake.write_text('#!' + sys.executable + '\nimport sys,json,re\n'
                'if "--version" in sys.argv:\n print("codex-cli 0.154.0"); sys.exit()\n'
                'prompt=sys.stdin.read()\n'
                'nonce=re.search(r"[a-f0-9]{32}", prompt)\n'
                'payload={"preflight":nonce.group() if nonce else "",'
                '"python":"/venv/bin/python","version":"5.2.1 LTS"}\n'
                'print(json.dumps({"type":"item.completed","item":'
                '{"type":"mcp_tool_call","server":"blender","tool":"execute_blender_code_for_cli",'
                '"status":"completed","error":None,"result":{"structured_content":payload}}}))\n')
            args[args.index('--evidence') + 1] = str(root / 'logs2')
            proc = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn('incomplete checkpoint artifacts', proc.stderr)
            self.assertTrue((root / 'logs2' / 'run.jsonl').is_file())

    def test_toml_roundtrip_preserves_keys_booleans_and_paths(self):
        m = self.load()
        import tomllib
        value = {'literal.dot': {'env': {'PATH': '/space dir/bin'}, 'required': True,
                               'tools': list(m.TOOLS)}}
        self.assertEqual(tomllib.loads('mcp_servers=' + m.toml_value(value))['mcp_servers'], value)


if __name__ == '__main__':
    unittest.main()
