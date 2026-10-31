from pathlib import Path
from types import SimpleNamespace,ModuleType
import sys,json,unittest,subprocess
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
import t_source_update_pipeline_220 as fixtures
BOTH=fixtures.BOTH
from localharness import legacy_update_tool as bridge,source_update,code_transition,update_source
from localharness.agent_source_update import require_patch_tool
from localharness.package_notice import read

class LegacyTool(unittest.TestCase):
    def test_old_runner_identity_is_reported_separately_from_incoming_disk_version(self):
        from localharness import package_notice,runtime_identity
        module=ModuleType('test_legacy_identity');module.__package__='localharness';module.__file__=str(self.target)
        self.agent.__file__=str(self.f.code/'agent.py')
        exec(compile(self.original+bridge.shim(self.f.new,sys.executable,source_update.TOOL,'identity').encode(),str(self.target),'exec'),module.__dict__)
        module.install(self.agent,self.f.tree)
        package_notice.write(self.f.tree/'memory/.control/desk_inbox/.reader.json',{'pid':__import__('os').getpid()})
        value=runtime_identity.current(self.f.tree)
        self.assertTrue(value['available']);self.assertEqual(value['version_at_start'],'2.1.1')
        self.assertEqual(value['program'],str(self.f.old));self.assertNotEqual(value['program'],str(self.f.new))
    def test_legacy_events_ignore_clock_replay_and_old_three_turn_limit(self):
        from unittest.mock import Mock
        from localharness import package_notice
        f=self.f;f.selected_automatic_candidate()
        # Build the actual appended module, including its self-contained client.
        module=ModuleType('test_legacy_events');module.__package__='localharness'
        exec(compile(self.original+bridge.shim(f.new,sys.executable,source_update.TOOL,'fixture').encode(),str(self.target),'exec'),module.__dict__)
        package_notice.write(f.directory/'coordinator-hand.json',{'state':'ready'})
        before=source_update.path(f.tree).read_bytes();turn=Mock(return_value='spoken')
        with patch('localharness.owner_stop.stopped',return_value=False),patch('localharness.owner_stop.paused',return_value=False):
            with patch.object(module.time,'time',return_value=1000):module.notify_due(f.tree,turn)
            with patch.object(module.time,'time',return_value=9000):module.notify_due(f.tree,turn)
            self.assertEqual(turn.call_count,1)
            operation=package_notice.read(f.directory/'pipeline.json');operation['finish_error']='actual new failure'
            package_notice.write(f.directory/'pipeline.json',operation);module.notify_due(f.tree,turn)
            self.assertEqual(turn.call_count,2)
        self.assertEqual(source_update.path(f.tree).read_bytes(),before)
        self.assertNotEqual(turn.call_args_list[0].args[0],turn.call_args_list[1].args[0])
    def setUp(self):
        self.f=fixtures.PipelineTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        helper=self.f.new/'app/localharness/legacy_update_tool.py';helper.parent.mkdir(parents=True,exist_ok=True)
        helper.write_bytes(Path(bridge.__file__).read_bytes())
        code_transition.seal(self.f.new)
        self.target=self.f.old/'app/localharness/source_update.py';self.target.parent.mkdir(parents=True)
        self.original=b"from pathlib import Path\nimport json,time\nfrom .package_notice import read,write\nTOOL={'input_schema':{'properties':{'action':{'enum':['read','resolve','finish']}}}}\ndef path(tree):return Path(tree)/'memory/.control/source-update.json'\n"
        self.target.write_bytes(self.original)
        self.agent=SimpleNamespace(TOOL_IMPL={},BASE_TOOLS=[]);self.restarts=[]
        ready=patch.object(bridge,'ensure_agent_window',return_value=True);ready.start();self.addCleanup(ready.stop)
    def restart(self,old):
        self.restarts.append(str(old))
        if self.target.read_bytes()!=self.original:
            module=ModuleType('test_legacy_hand');module.__package__='localharness'
            exec(compile(self.target.read_bytes(),str(self.target),'exec'),module.__dict__)
            module.install(self.agent,self.f.tree)
    def test_same_agent_gets_new_diff_patch_without_merger_and_restores_old_hand(self):
        f=self.f
        original_run=subprocess.run
        launched=[]
        # The hand deliberately pins the incoming packaged interpreter, not
        # the test runner's Python. This fixture substitutes only that CLI.
        packaged_python=f.new/('runtime/python.exe' if sys.platform=='win32' else 'runtime/bin/python3')
        def invoke(argv,**kw):
            launched.append(str(argv[0]))
            if str(argv[0])!=str(packaged_python):return original_run(argv,**kw)
            args=json.loads(kw['input']);action=args.pop('action')
            result=source_update.command(f.tree,action,**args)
            return SimpleNamespace(returncode=0,stdout=json.dumps(result).encode(),stderr=b'')
        with patch('localharness.owner_stop.stopped',return_value=False),patch('localharness.owner_stop.paused',return_value=False),patch('localharness.source_update_host.verified_runtime'),patch('localharness.update_process.interpreter_for',return_value=packaged_python),patch.object(bridge,'restart_engine',side_effect=self.restart):
            with bridge.provide(f.old,f.new):
                require_patch_tool(f.old)
                update_source.select(f.old,f.new,'preserve',by='window',require_review=True)
                result=code_transition.preflight(f.old,f.new,notify=False);f.directory=Path(result['plans'][0]['directory'])
                source_update.ensure(f.tree,f.old,f.code,f.directory,'2.2.0',coordinator=f.coordinator)
                hand=self.agent.TOOL_IMPL['source_update']
                with patch('subprocess.run',side_effect=invoke):
                    status=json.loads(hand());diff=json.loads(hand('diff',path='module.py'))
                    self.assertIn('text',diff,{'reply':diff,'launched':launched,'expected_python':str(packaged_python)})
                    self.assertIn('result/module.py',diff['text'])
                    hand('patch',path='module.py',before=(f.directory/'result/module.py').read_text(),after=BOTH,expected_candidate_sha256=status['candidate_sha256'])
                    status=json.loads(hand('finish'));self.assertIn('candidate_state',status,status);self.assertEqual(status['candidate_state'],'ready')
                self.assertFalse(f.spawns)
            self.assertEqual(self.target.read_bytes(),self.original);self.assertEqual(len(self.restarts),2)
    def test_failure_restores_exact_legacy_bytes_and_restarts(self):
        with patch('localharness.owner_stop.stopped',return_value=False),patch('localharness.owner_stop.paused',return_value=False),patch('localharness.source_update_host.verified_runtime'),patch('localharness.update_process.interpreter_for',return_value=Path(sys.executable)),patch.object(bridge,'restart_engine',side_effect=self.restart):
            with self.assertRaisesRegex(RuntimeError,'cancel fixture'):
                with bridge.provide(self.f.old,self.f.new):raise RuntimeError('cancel fixture')
        self.assertEqual(self.target.read_bytes(),self.original);self.assertEqual(len(self.restarts),2)
    def test_owner_stop_never_bootstraps_or_restarts(self):
        from localharness.agent_source_update import AgentResolutionUnavailable
        with patch('localharness.owner_stop.stopped',return_value=True),patch.object(bridge,'restart_engine') as restart:
            with self.assertRaises(AgentResolutionUnavailable):
                with bridge.provide(self.f.old,self.f.new):self.fail('Stop bypassed')
        self.assertEqual(self.target.read_bytes(),self.original);restart.assert_not_called()
    def test_protected_legacy_app_offers_clean_without_restart_or_partial_write(self):
        from localharness.agent_source_update import AgentResolutionUnavailable
        with patch('localharness.owner_stop.stopped',return_value=False),patch('localharness.owner_stop.paused',return_value=False),patch('localharness.source_update_host.verified_runtime'),patch('localharness.update_process.interpreter_for',return_value=Path(sys.executable)),patch.object(bridge,'replace_owned',side_effect=PermissionError(13,'protected')),patch.object(bridge,'restart_engine') as restart:
            with self.assertRaises(AgentResolutionUnavailable):
                with bridge.provide(self.f.old,self.f.new):self.fail('Read-only app accepted')
        self.assertEqual(self.target.read_bytes(),self.original);restart.assert_not_called()

class ControllerProtocol(unittest.TestCase):
    def test_stale_record_skipped_and_real_response_envelope_used(self):
        import tempfile,threading,http.server
        commands=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.assert_token=self.headers.get('X-Helene-Window')=='fixture-token'
                if not self.assert_token:self.send_error(403);return
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));commands.append(request['command'])
                result={'agents':[{'raised':True}]} if request['command']=='agents_list' else {'ok':True}
                payload=json.dumps({'result':result}).encode();self.send_response(200);self.end_headers();self.wfile.write(payload)
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                old=Path(folder)
                (old/'.python-window-1.json').write_text(json.dumps({'root':str(old),'origin':'http://127.0.0.1:0','token':'stale'}))
                (old/'.python-window-2.json').write_text(json.dumps({'root':str(old),'origin':f'http://127.0.0.1:{server.server_port}','token':'fixture-token'}))
                bridge.restart_engine(old)
                self.assertEqual(commands,['agents_list','engine_restart'])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
