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

    def test_generation_exposes_visual_inspection_with_exactly_four_tools(self):
        m = self.load()
        self.assertEqual(len(m.TOOLS), 4)
        self.assertIn('get_render_as_image_for_cli', m.TOOLS)

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
        self.assertEqual(server['omit_tools_from'], ['deferred', 'code_mode'])

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

    def test_generation_stream_allows_blender_resolution_properties(self):
        m = self.load()
        events = [
            {'type': 'item.completed', 'item': {'id': 'edit', 'type': 'mcp_tool_call',
                'server': 'blender', 'tool': 'execute_blender_code_for_cli', 'status': 'completed',
                'error': None, 'arguments': {'code': 'bpy.context.scene.render.resolution_x = 512'},
                'result': {'structured_content': {'saved': True}}}},
            {'type': 'turn.completed'},
        ]
        m.verify_generation_events(events)

    def test_generation_stream_requires_one_successful_image_inspection_per_round(self):
        m = self.load()
        events = [
            {'type': 'item.completed', 'item': {'id': 'edit', 'type': 'mcp_tool_call',
                'server': 'blender', 'tool': 'execute_blender_code_for_cli', 'status': 'completed',
                'error': None, 'result': {'structured_content': {'saved': True}}}},
            {'type': 'turn.completed'},
        ]
        with self.assertRaisesRegex(ValueError, 'visual inspection'):
            m.verify_generation_events(events, rounds=1)
        events.insert(1, {'type': 'item.completed', 'item': {'id': 'inspect',
            'type': 'mcp_tool_call', 'server': 'blender', 'tool': 'get_render_as_image_for_cli',
            'status': 'completed', 'error': None,
            'arguments': {'blend_file': '/work/iteration01.blend'},
            'result': {'content': [{'type': 'image', 'mimeType': 'image/png', 'data': 'AA=='}]}}})
        m.verify_generation_events(events, rounds=1)

    def test_generation_stream_binds_visual_inspection_to_each_round_checkpoint(self):
        m = self.load()
        events = [
            {'type': 'item.completed', 'item': {'id': 'edit', 'type': 'mcp_tool_call',
                'server': 'blender', 'tool': 'execute_blender_code_for_cli', 'status': 'completed',
                'error': None, 'arguments': {'code': 'edit'}, 'result': {'structured_content': {}}}},
            {'type': 'item.completed', 'item': {'id': 'inspect', 'type': 'mcp_tool_call',
                'server': 'blender', 'tool': 'get_render_as_image_for_cli', 'status': 'completed',
                'error': None, 'arguments': {'blend_file': '/work/iteration02.blend'},
                'result': {'content': [{'type': 'image', 'mimeType': 'image/png', 'data': 'AA=='}]}}},
            {'type': 'turn.completed'},
        ]
        with self.assertRaisesRegex(ValueError, 'checkpoint sequence'):
            m.verify_generation_events(events, rounds=1)

    def test_generation_stream_rejects_visual_inspection_from_wrong_directory(self):
        import hashlib, tempfile
        m = self.load()
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / 'work'; work.mkdir()
            checkpoint = work / 'iteration01.blend'; checkpoint.write_bytes(b'checkpoint')
            wrong = Path(tmp) / 'wrong' / checkpoint.name
            events = [
                {'type': 'item.completed', 'item': {'id': 'edit', 'type': 'mcp_tool_call',
                    'server': 'blender', 'tool': 'execute_blender_code_for_cli', 'status': 'completed',
                    'error': None, 'arguments': {'code': 'edit'}, 'result': {'structured_content': {}}}},
                {'type': 'item.completed', 'item': {'id': 'inspect', 'type': 'mcp_tool_call',
                    'server': 'blender', 'tool': 'get_render_as_image_for_cli', 'status': 'completed',
                    'error': None, 'arguments': {'blend_file': str(wrong)},
                    'result': {'content': [{'type': 'image', 'mimeType': 'image/png', 'data': 'AA==',
                        '_meta': {'source_sha256': hashlib.sha256(b'checkpoint').hexdigest()}}]}}},
                {'type': 'turn.completed'},
            ]
            with self.assertRaisesRegex(ValueError, 'checkpoint path'):
                m.verify_generation_events(events, rounds=1, workdir=work)

    def test_generation_stream_rejects_unattested_precomputed_checkpoint(self):
        import hashlib, tempfile
        m = self.load()
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp); initial = work / 'initial.blend'; initial.write_bytes(b'initial')
            checkpoint = work / 'iteration01.blend'
            checkpoint.write_bytes(b'precomputed')
            digest = hashlib.sha256(b'precomputed').hexdigest()
            events = [
                {'type': 'item.completed', 'item': {'id': 'noop', 'type': 'mcp_tool_call',
                    'server': 'blender', 'tool': 'execute_blender_code_for_cli', 'status': 'completed',
                    'error': None, 'arguments': {'blend_file': str(work / 'initial.blend'), 'code': 'result={}'},
                    'result': {'structured_content': {}}}},
                {'type': 'item.completed', 'item': {'id': 'inspect', 'type': 'mcp_tool_call',
                    'server': 'blender', 'tool': 'get_render_as_image_for_cli', 'status': 'completed',
                    'error': None, 'arguments': {'blend_file': str(checkpoint)},
                    'result': {'content': [{'type': 'image', 'mimeType': 'image/png', 'data': 'AA==',
                        '_meta': {'source': str(checkpoint), 'source_sha256': digest}}]}}},
                {'type': 'turn.completed'},
            ]
            with self.assertRaisesRegex(ValueError, 'attested checkpoint'):
                m.verify_generation_events(events, rounds=1, workdir=work)
            events[0]['item']['result']['structured_content'] = {'_checkpoint': {
                'path': str(checkpoint), 'source': str(initial),
                'source_sha256': hashlib.sha256(b'initial').hexdigest(),
                'output_sha256': digest, 'existed_before': False}}
            m.verify_generation_events(events, rounds=1, workdir=work)

    def test_generation_stream_rejects_next_edit_dispatched_before_prior_inspection(self):
        m = self.load()
        checkpoint = lambda n: {'path': f'/work/iteration{n:02}.blend'}
        image = lambda n: {'type': 'item.completed', 'item': {'id': f'i{n}',
            'type': 'mcp_tool_call', 'server': 'blender', 'tool': 'get_render_as_image_for_cli',
            'status': 'completed', 'error': None,
            'arguments': {'blend_file': f'/work/iteration{n:02}.blend'},
            'result': {'content': [{'type': 'image', 'mimeType': 'image/png', 'data': 'AA=='}]}}}
        edit = lambda n, kind='item.completed': {'type': kind, 'item': {'id': f'e{n}',
            'type': 'mcp_tool_call', 'server': 'blender', 'tool': 'execute_blender_code_for_cli',
            'status': 'completed', 'error': None,
            'arguments': {'expected_output_blend': f'/work/iteration{n:02}.blend'},
            'result': {'structured_content': {'_checkpoint': checkpoint(n)}}}}
        events = [edit(1, 'item.started'), edit(2, 'item.started'), edit(1), image(1),
                  edit(2), image(2), {'type': 'turn.completed'}]
        with self.assertRaisesRegex(ValueError, 'dispatch'):
            m.verify_generation_events(events, rounds=2)

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

    def test_checkpoint_ancestors_may_not_be_symlinks(self):
        m = self.load()
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real = base / 'real'
            real.mkdir()
            linked = base / 'linked'
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                m.verify_checkpoints(linked, 1)

    def test_generation_workdir_is_an_exact_noncontaminated_surface(self):
        m = self.load()
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'initial.blend').write_bytes(b'blend')
            m.verify_generation_workdir(root)
            (root / 'obfuscated.bin').write_bytes(b'hidden payload')
            with self.assertRaisesRegex(ValueError, 'runtime root'):
                m.verify_generation_workdir(root)

    def test_generation_workdir_accepts_canonical_worker_when_imported_as_tools_module(self):
        m = self.load()
        from tools import direct_runtime
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'initial.blend').write_bytes(b'blend')
            (root / 'worker.py').write_text(direct_runtime.WORKER)
            m.verify_generation_workdir(root)

    def test_cli_denied_preflight_stops_run_even_with_exit_zero(self):
        m = self.load()
        self.assertTrue(hasattr(m, 'main'), 'CLI missing')
        import tempfile, json, subprocess, sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / 'work'
            work.mkdir()
            fake = root / 'codex'
            fake.write_text('#!' + sys.executable + '\nimport sys\n'
                            'print("codex-cli 0.154.0" if "--version" in sys.argv else '
                            '\'{"type":"turn.completed"}\')\n')
            fake.chmod(0o755)
            (work / 'initial.blend').write_bytes(b'fixture')
            (root / 'prompt.txt').write_text('fixture generation task')
            (root / 'target.png').write_bytes(b'fixture')
            config = root / 'server.toml'
            config.write_text('[mcp_servers.blender]\ncommand="/server"\n'
                'enabled_tools=' + json.dumps(list(m.TOOLS)) + '\n'
                '[mcp_servers.blender.env]\nBLENDER_MCP_CLI_BACKEND="bpy"\n'
                'BLENDER_MCP_BPY_PYTHON="/venv/bin/python"\n')
            args = [sys.executable, str(SCRIPT), 'run', '--config', str(config),
                    '--codex', str(fake), '--codex-version', 'codex-cli 0.154.0',
                    '--bpy-version', '5.2.1', '--workdir', str(work),
                    '--evidence', str(root / 'logs'),
                    '--model', 'test-model', '--approve-blender-tools',
                    '--prompt', str(root / 'prompt.txt'), '--target', str(root / 'target.png')]
            proc = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn('no successful', proc.stderr)
            self.assertFalse((root / 'logs' / 'run.jsonl').exists())
            argv = json.loads((root / 'logs' / 'preflight.argv.json').read_text())
            self.assertIn('--strict-config', argv)
            self.assertIn('read-only', argv)
            for feature in m.DISABLED_FEATURES:
                self.assertIn(feature, argv)
                self.assertIn('--disable', argv)
            self.assertTrue(any('experimental_request_user_input=false' in a for a in argv))
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
                '{"id":"fixture-call","type":"mcp_tool_call","server":"blender","tool":"execute_blender_code_for_cli",'
                '"status":"completed","error":None,"result":{"structured_content":payload}}}))\n')
            args[args.index('--evidence') + 1] = str(root / 'logs2')
            proc = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn('generation event stream is empty or lacks', proc.stderr)
            self.assertTrue((root / 'logs2' / 'run.jsonl').is_file())

    def test_task_policy_is_ignored_but_noop_is_rejected(self):
        import copy
        m = self.load()
        self.assertTrue(hasattr(m, 'validate_edit'), 'task policy validator missing')
        before = {'objects': {'Cube': {'type': 'MESH', 'location': [0, 0, 0],
                  'scale': [1, 1, 1]}}, 'preserve': {'mesh': 'fixed'}}
        after = copy.deepcopy(before)
        after['objects']['Cube']['location'] = [1, 0, 0]
        policy = {'allowed_transforms': {'Cube': ['location']},
                  'location_bounds': {'Cube': [[-2, 2]] * 3}}
        m.validate_edit(before, after, policy)
        with self.assertRaises(ValueError):
            m.validate_edit(before, before, policy)
        m.validate_edit(before, {**after, 'preserve': {'mesh': 'changed'}}, policy)
        after['objects']['Cube']['location'] = [3, 0, 0]
        m.validate_edit(before, after, policy)

    def test_all_property_families_and_geometry_are_editable(self):
        import copy
        m = self.load()
        before = {'objects': {'Cam': {'type': 'CAMERA', 'lens': 50},
            'Lamp': {'type': 'LIGHT', 'light_data': {'energy': 20, 'color': [1, 1, 1]}}},
            'shape_keys': {'Keys': {'Smile': 0, 'Basis': 0}}, 'preserve': {'mesh': 'fixed'}}
        for policy, mutate in [
            ({'allowed_camera_data': {'Cam': ['lens']}}, lambda x: x['objects']['Cam'].update(lens=60)),
            ({'allowed_light_data': {'Lamp': ['energy', 'color']}}, lambda x: x['objects']['Lamp']['light_data'].update(energy=30, color=[1, 0, 1])),
            ({'allowed_shape_keys': {'Keys': ['Smile']}}, lambda x: x['shape_keys']['Keys'].update(Smile=0.5))]:
            after = copy.deepcopy(before)
            mutate(after)
            m.validate_edit(before, after, policy)
            after['preserve']['mesh'] = 'changed'
            m.validate_edit(before, after, policy)
        after = copy.deepcopy(before)
        after['objects']['Cam']['lens'] = float('nan')
        with self.assertRaises(ValueError):
            m.validate_edit(before, after, {'allowed_camera_data': {'Cam': ['lens']}})
        with self.assertRaises(ValueError):
            m.validate_edit(before, before, {'allowed_camera_data': {'Cam': ['sensor_width']}})
        with self.assertRaises(ValueError):
            m.validate_edit(before, before, {'allowed_light_data': {'Lamp': ['shadow_soft_size']}})

    def test_non_camera_ledger_needs_no_fake_pose(self):
        import tempfile, json
        from PIL import Image
        m = self.load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blend, png = root / 'iteration01.blend', root / 'iteration01.png'
            blend.write_bytes(b'BLENDERfixture')
            Image.new('RGB', (2, 2)).save(png)
            (root / 'rounds.jsonl').write_text(json.dumps({'round': 1,
                'blend': str(blend), 'png': str(png), 'rationale': 'move cube'}))
            self.assertEqual(len(m.verify_checkpoints(root, 1, policy={'allowed_transforms': {'Cube': ['location']}})), 1)

    def test_toml_roundtrip_preserves_keys_booleans_and_paths(self):
        m = self.load()
        import tomllib
        value = {'literal.dot': {'env': {'PATH': '/space dir/bin'}, 'required': True,
                               'tools': list(m.TOOLS)}}
        self.assertEqual(tomllib.loads('mcp_servers=' + m.toml_value(value))['mcp_servers'], value)

    def test_generation_stream_rejects_empty_or_incomplete_turns(self):
        m = self.load()
        for events in ([], [
            {'type': 'item.completed', 'item': {'id': 'call', 'type': 'mcp_tool_call',
                'server': 'blender', 'tool': m.TOOLS[0], 'status': 'completed',
                'arguments': {}, 'result': {'structured_content': {'ok': True}}}},
        ], [{'type': 'turn.completed'}]):
            with self.subTest(events=events), self.assertRaises(ValueError):
                m.verify_generation_events(events)

    def test_generation_stream_fails_closed_on_nonapproved_operations_and_feedback(self):
        m = self.load()
        allowed = [
            {'type': 'item.completed', 'item': {'id': 'reason', 'type': 'reasoning'}},
            {'type': 'item.completed', 'item': {'id': 'call', 'type': 'mcp_tool_call',
                'server': 'blender', 'tool': m.TOOLS[0], 'status': 'completed',
                'arguments': {'code': "import bpy; bpy.context.scene.render.filepath='iteration01.png'"},
                'result': {'structured_content': {'ok': True}}}},
            {'type': 'turn.completed'},
        ]
        m.verify_generation_events(allowed)
        prohibited = [
            {'type': 'item.completed', 'item': {'id': 'shell', 'type': 'command_execution',
                                                'command': 'python edit_scene.py'}},
            {'type': 'item.completed', 'item': {'id': 'write', 'type': 'file_change',
                                                'path': 'scene.blend'}},
            {'type': 'item.completed', 'item': {'id': 'goal', 'type': 'mcp_tool_call',
                'server': 'blender', 'tool': m.TOOLS[0], 'status': 'completed',
                'arguments': {'code': "open('../goal.py').read()"}}},
            {'type': 'item.completed', 'item': {'id': 'score', 'type': 'mcp_tool_call',
                'server': 'blender', 'tool': m.TOOLS[0], 'status': 'completed',
                'arguments': {'code': "json.load(open('scoring/aggregate.json'))"}}},
        ]
        for event in prohibited:
            with self.subTest(item=event['item']['id']), self.assertRaises(ValueError):
                m.verify_generation_events([event])


if __name__ == '__main__':
    unittest.main()
