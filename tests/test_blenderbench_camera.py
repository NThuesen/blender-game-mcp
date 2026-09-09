"""Offline synthetic protocol tests; not model benchmark evidence."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from benchmarks.blenderbench_camera import dataset,runtime,rounds
from benchmarks.blenderbench_camera.controller import recover,adapter
from benchmarks.blenderbench_camera.__main__ import main,parser


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_cli_help_all_commands(self):
        for cmd in ([],['download'],['run'],['score'],['report'],['render'],['video'],['initialize'],['verify-sources'],['cache-clip']):
            result=subprocess.run([sys.executable,'-m','benchmarks.blenderbench_camera',*cmd,'--help'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('/Users/mg',result.stdout)
    def test_manifest_no_solution(self):
        rows=[rel for t in dataset.REVIEWED for rel,_ in dataset.entries(t)]
        self.assertEqual(len(rows),32);self.assertFalse(any('goal.py' in x for x in rows))
        with self.assertRaises(ValueError):dataset.entries('camera1')
    def test_git_blob_hash_and_download_offline(self):
        b=b'synthetic bytes';row={'size':len(b),'oid':hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest(),'path':'data/blenderbench/level1/camera4/task.txt'}
        with patch.object(dataset.urllib.request,'urlopen',return_value=io.BytesIO(b)) as fetch:
            result=dataset.download(self.root/'asset',row)
            self.assertEqual(result['sha256'],hashlib.sha256(b).hexdigest())
            self.assertIn(dataset.SHA,fetch.call_args.args[0])
        self.assertTrue(dataset.download(self.root/'asset',row)['cached'])
    def test_lfs_tamper(self):
        p=self.root/'asset';p.write_bytes(b'xx');row={'size':2,'lfs':{'size':2,'oid':hashlib.sha256(b'xx').hexdigest()}}
        dataset.verify_asset(p,row);p.write_bytes(b'yy')
        with self.assertRaises(AssertionError):dataset.verify_asset(p,row)
    def test_reviewed_start_ast(self):
        p=self.root/'start.py'
        for task,(loc,rot,lens) in dataset.REVIEWED.items():
            s=f'import bpy\nbpy.data.objects["Camera1"].location={loc!r}\nbpy.data.objects["Camera1"].rotation_euler={rot!r}\n'
            if lens is not None:s+=f'bpy.data.objects["Camera1"].data.lens={lens!r}\n'
            p.write_text(s);dataset.inspect_start(task,p)
            p.write_text(s+'print("bad")\n')
            with self.assertRaises(AssertionError):dataset.inspect_start(task,p)
    def test_gpu_defaults_and_reject_cpu(self):
        self.assertIn("'METAL'",runtime.gpu_code(system='Darwin'))
        self.assertIn("'CUDA'",runtime.gpu_code(system='Linux'))
        for system,device in [('Darwin','CUDA'),('Linux','METAL'),('Linux','CPU')]:
            with self.assertRaises(ValueError):runtime.gpu_code(device,system)
    def test_gpu_setup_executes_and_disables_cpu(self):
        for system,device in [('Darwin','METAL'),('Linux','CUDA'),('Linux','OPTIX')]:
            devices=[NS(type=device,name='synthetic GPU',use=False,id='gpu'),NS(type='CPU',name='cpu',use=True,id='cpu')]
            prefs=NS(refresh_devices=lambda:None,devices=devices)
            scene=NS(render=NS(engine='CYCLES',resolution_x=512,resolution_y=512,resolution_percentage=100),cycles=NS(device='CPU',samples=16))
            bpy=NS(context=NS(preferences=NS(addons={'cycles':NS(preferences=prefs)},filepaths=NS(save_version=1)),scene=scene),data=NS(scenes=[scene]),app=NS(version_string='synthetic'))
            with patch.dict(sys.modules,{'bpy':bpy}):exec(runtime.gpu_code(device,system),{})
            self.assertTrue(devices[0].use);self.assertFalse(devices[1].use);self.assertEqual(scene.cycles.device,'GPU')
            prefs.devices=devices[1:]
            with patch.dict(sys.modules,{'bpy':bpy}),self.assertRaises(RuntimeError):exec(runtime.gpu_code(device,system),{})
    def test_disk_guard(self):
        with patch.object(runtime.shutil,'disk_usage',return_value=NS(free=100)),self.assertRaises(OSError):runtime.storage_gate(self.root,100)
    def test_clone_no_fullcopy_linux(self):
        p=self.root/'source';p.write_bytes(b'x')
        with patch.object(runtime.platform,'system',return_value='Linux'),patch.object(runtime.fcntl,'ioctl',side_effect=OSError('no reflinks')),self.assertRaises(OSError):runtime.clone(p,self.root/'dst')
        self.assertFalse((self.root/'dst').exists())
    def test_ledger_bound_noops_and_preservation(self):
        l=rounds.Ledger(None);l.start(1,1)
        with self.assertRaises(ValueError):l.candidate({'camera':1,'preserve':2},{'camera':1,'preserve':2},__file__,__file__)
        with self.assertRaises(ValueError):l.candidate({'camera':1,'preserve':2},{'camera':2,'preserve':3},__file__,__file__)
        l.start(1,2)
        for n in range(1,11):
            if n>1:l.start(n,1)
            l.candidate({'camera':n,'preserve':2},{'camera':n+1,'preserve':2},__file__,__file__)
        self.assertTrue(l.complete())
        with self.assertRaises(ValueError):l.start(11,1)
    def saved(self):
        b=self.root/'candidate.blend';b.write_bytes(b'synthetic blend')
        p=self.root/'progress.png';p.write_bytes(b'synthetic png')
        rows=[{'phase':'round_start','round':1,'attempt':1,'elapsed_seconds':0}, {'phase':'candidate_checkpoint','round':1,'attempt':1,'elapsed_seconds':100,'blend':str(b),'blend_sha256':rounds.sha(b),'png':str(p),'png_sha256':rounds.sha(p)}, {'phase':'round_start','round':2,'attempt':2,'elapsed_seconds':101}, {'phase':'task_failed','round':2,'attempt':2,'elapsed_seconds':110}]
        (self.root/'round-ledger.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
        return rows
    def test_resume_active_budget_and_prep_attempt(self):
        self.saved();r=recover(self.root)
        self.assertEqual(r['prior'],110);self.assertEqual(r['completed'],1);self.assertEqual(r['next_attempt'],2);self.assertTrue(r['reuse_start'])
        with self.assertRaises(TimeoutError):recover(self.root,100)
    def test_resume_rejects_launched_second_attempt(self):
        self.saved();arm=self.root/'round-02-attempt-2/enhanced';arm.mkdir(parents=True);(arm/'launch.json').write_text('{}')
        with self.assertRaises(ValueError):recover(self.root)
    def test_resume_tamper(self):
        self.saved();(self.root/'candidate.blend').write_bytes(b'changed')
        with self.assertRaises(ValueError):recover(self.root)
    def test_run_refuses_without_optin(self):
        with self.assertRaises(ValueError):main(['run','--dataset-dir',str(self.root),'--output',str(self.root/'out'),'--model','synthetic','--server-python',sys.executable,'--blender','/fixture/blender'])
        self.assertFalse((self.root/'out').exists())
    def test_video_calls_tokens(self):
        from benchmarks.blenderbench_camera.video import timeline_state
        event={'elapsed_seconds':1,'type':'item.started','item':{'id':'same','type':'mcp_tool_call'}}
        rounds.dump(self.root/'events.json',[event,event])
        sessions=[{'offset_seconds':0,'summary':{'root':str(self.root),'wall_seconds':10,'telemetry':{'usage_stream_complete':True,'raw_usage_redacted':[{'input_tokens':100,'output_tokens':20,'cached_input_tokens':50}]}}}]
        state=timeline_state(5,[],sessions);self.assertEqual(state[3:],(1,60))
        sessions[0]['summary']['telemetry']['usage_stream_complete']=False
        self.assertIsNone(timeline_state(5,[],sessions)[4])
    def test_adapter_paths_and_images(self):
        args=NS(runtime='blender',blender=Path('/fixture/blender'),server_python=Path('/fixture/python'),model='synthetic')
        smoke,restore=adapter(args,self.root,'', [512,512]);self.addCleanup(restore)
        arm=self.root/'arm';arm.mkdir()
        argv,prompt=smoke.build_argv(arm,'enhanced','synthetic',rounds.PROMPT,[self.root/'a.png',self.root/'b.png'])
        self.assertEqual(argv.count('--image'),2)
        self.assertIn('mcp_servers.blender.command="/fixture/python"',argv)
        self.assertNotIn('/tmp/blender-mcp-doc-server-env',str(argv))
        self.assertNotIn('{start_blend}',prompt)
    def test_freeze_has_only_source_and_gpu_patch(self):
        repo=self.root/'repo';p=repo/'mcp/blmcp/tools_helpers/blender_cli.py';p.parent.mkdir(parents=True)
        p.write_text('_CLI_TIMEOUT = 120.0\ndef run():\n    config = _resolve_cli_backend()\n')
        (p.parent/'bad.blend').write_bytes(b'not source')
        out=self.root/'out';source=runtime.freeze(repo,out,runtime.gpu_code('CUDA','Linux'))
        self.assertFalse((source/'mcp/blmcp/tools_helpers/bad.blend').exists())
        self.assertIn('_CLI_TIMEOUT = 600.0',(source/'mcp/blmcp/tools_helpers/blender_cli.py').read_text())
        self.assertEqual(runtime.verify_frozen(out),source)

    def test_full_controller_ten_rounds_mocked_no_model(self):
        from benchmarks.blenderbench_camera.controller import run
        inp=self.root/'data/camera4';inp.mkdir(parents=True)
        (inp/'start_initialized.blend').write_bytes(b'0');(inp/'initial_verified.png').write_bytes(b'synthetic')
        (inp/'renders/goal').mkdir(parents=True);(inp/'renders/goal/render1.png').write_bytes(b'target')
        rounds.dump(inp/'initialized.json',{'blend_sha256':rounds.sha(inp/'start_initialized.blend'),'png_sha256':rounds.sha(inp/'initial_verified.png'),'dimensions':[512,512]})
        args=NS(accept_unverified_isolation=True,runtime='blender',bpy_python=None,dataset_dir=self.root/'data',output=self.root/'out',resume_from=None,seconds=3600,blender=Path('/synthetic/blender'),gpu='METAL' if sys.platform=='darwin' else 'CUDA',repo=self.root,model='SYNTHETIC',model_seconds=180,max_tools=30)
        def inspect(blend,report,png,deadline):
            if png:png.write_bytes(b'SYNTHETIC PNG '+Path(blend).read_bytes())
            return {'camera':int(Path(blend).read_bytes()),'preserve':'same'}
        def trial(arm,*a):
            (arm/'output').mkdir(parents=True);n=int(arm.parent.name.split('-')[1])
            (arm/'output/voxel_robot_pool_party_enhanced.blend').write_bytes(str(n).encode())
            rounds.dump(arm/'events.json',[])
            s={'root':str(arm),'wall_seconds':1,'telemetry':{'usage_stream_complete':False}}
            rounds.dump(arm/'summary.json',s);return s
        def clone(a,b):Path(b).write_bytes(Path(a).read_bytes())
        with patch('benchmarks.blenderbench_camera.controller.entries',return_value=[]),patch.object(runtime,'freeze',return_value=self.root/'source'),patch('benchmarks.blenderbench_camera.controller.adapter',return_value=(NS(run_trial=trial),lambda:None)),patch('benchmarks.blenderbench_camera.controller.backend_probe',return_value=[512,512]),patch.object(rounds,'inspect_render',side_effect=inspect),patch.object(runtime,'clone',side_effect=clone):
            self.assertEqual(run(args,'camera4'),0)
        summary=rounds.load(args.output/'camera4/summary.json');self.assertEqual(summary['verified_rounds'],10);self.assertEqual(len(summary['sessions']),10)
    def test_venv_executable_symlink_preserved(self):
        exe=self.root/'venv/bin/python';exe.parent.mkdir(parents=True);exe.symlink_to(sys.executable)
        with patch('benchmarks.blenderbench_camera.controller.run',return_value=0) as run:
            main(['run','--dataset-dir',str(self.root/'data'),'--output',str(self.root/'out'),'--model','synthetic','--server-python',str(exe),'--blender','/fixture/blender','--accept-unverified-isolation'])
        self.assertEqual(run.call_args.args[0].server_python,exe)

if __name__=='__main__':unittest.main()
