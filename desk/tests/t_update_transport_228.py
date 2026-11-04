"""Relay ownership and error transparency, without a live account/provider."""
from pathlib import Path
from types import SimpleNamespace
import copy,json,sys,tempfile,unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).parent))
from localharness import source_update_transport as transport
from localharness.package_notice import read,write


class ReviewTransport(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.agent={'tree':self.root/'profile','config':self.root/'helene.json'}
        self.spec=SimpleNamespace(port=5013,argv=('fixture-relay',),cwd=self.root,env={'RELAY_API_KEY':'private-fixture-key'})
        self.child=mock.Mock(pid=12345);self.child.poll.return_value=None

    def lease(self,**kw):return transport.RelayLease(self.agent,self.spec,self.root,kw.pop('stopped',lambda:False),**kw)

    def test_route_selection_never_changes_unmanaged_or_external_provider(self):
        cfg={'relay':{'enabled':True,'port':5013}}
        model={'roles':{'voice':{'framework':'openai'}},'frameworks':{'openai':{'base_url':'http://127.0.0.1:5013/v1'}}}
        before=copy.deepcopy(model);self.assertTrue(transport.uses_managed_relay(cfg,model));self.assertEqual(model,before)
        for url in ('https://api.example.invalid/v1','http://127.0.0.1:5014','http://user:secret@127.0.0.1:5013'):
            model['frameworks']['openai']['base_url']=url;self.assertFalse(transport.uses_managed_relay(cfg,model))
        cfg['relay']['enabled']=False;model['frameworks']['openai']['base_url']='http://127.0.0.1:5013'
        self.assertFalse(transport.uses_managed_relay(cfg,model))

    def test_shared_relay_spec_keeps_auth_proxy_and_platform_contract(self):
        from localharness import standalone_relay
        binary=self.root/'praxis-relay';binary.write_bytes(b'fixture only')
        cfg={'relay':{'enabled':True,'port':5013,'key':'private-key'},'model':{'key':'legacy-key'}}
        ambient={'PATH':'fixture-path','RELAY_API_KEY':'ambient-secret','HELENE_TOKEN':'channel-secret'}
        with mock.patch.object(standalone_relay,'executable',return_value=binary):
            spec=transport.supervisor.relay_spec(self.agent['config'],cfg,self.agent['tree'],Path(sys.executable),install_base=self.root,environ=ambient)
            self.assertEqual(spec.argv,(str(binary),'serve','--headless'))
            self.assertEqual(spec.port,5013);self.assertEqual(spec.env['RELAY_API_KEY'],'private-key')
            self.assertNotIn('HELENE_TOKEN',spec.env);self.assertNotIn('private-key',repr(spec.argv))
            self.assertEqual(spec.env['RELAY_AUTH_DIR'],str(self.agent['tree']/'relay/local_auth'))
            cfg={'relay':{'enabled':False},'images':{'enabled':True},'model':{'key':'other-provider-secret'}}
            spec=transport.supervisor.relay_spec(self.agent['config'],cfg,self.agent['tree'],Path(sys.executable),install_base=self.root,environ=ambient)
            self.assertNotIn('RELAY_API_KEY',spec.env);self.assertNotIn('HELENE_TOKEN',spec.env)

    def test_shared_relay_spec_preserves_legacy_windows_arguments(self):
        from localharness import standalone_relay
        binary=self.root/'helene-relay.exe';binary.write_bytes(b'fixture only')
        windows_os=SimpleNamespace(**{**vars(transport.supervisor.os),'name':'nt'})
        with mock.patch.object(standalone_relay,'executable',return_value=binary),mock.patch.object(transport.supervisor,'os',windows_os):
            spec=transport.supervisor.relay_spec(self.agent['config'],{'relay':{'enabled':True}},self.agent['tree'],Path(sys.executable),install_base=self.root,environ={})
            self.assertEqual(spec.argv,(str(binary),'--minimized','--start-api','--config',str(self.agent['config'].resolve())))

    def test_borrowed_relay_is_never_stopped_or_replaced(self):
        with mock.patch.object(transport.supervisor,'_port_busy',return_value=True),mock.patch.object(transport.RelayLease,'_ready',return_value=True),mock.patch.object(transport.supervisor.JobChild,'spawn') as spawn:
            with self.lease() as lease:self.assertFalse(lease.owned);lease.check()
        spawn.assert_not_called();self.assertEqual(read(self.root/'relay-transport.json')['state'],'borrowed-retained')

    def test_owned_relay_is_stopped_after_scope_and_secret_is_not_in_receipt(self):
        with mock.patch.object(transport.supervisor,'_port_busy',return_value=False),mock.patch.object(transport.RelayLease,'_ready',return_value=True),mock.patch.object(transport.supervisor.JobChild,'spawn',return_value=self.child):
            with self.lease() as lease:self.assertTrue(lease.owned);lease.check()
        self.child.stop.assert_called_once();raw=(self.root/'relay-transport.json').read_text();self.assertNotIn('private-fixture-key',raw);self.assertEqual(json.loads(raw)['state'],'stopped')

    def test_stop_prevents_admission(self):
        with mock.patch.object(transport.supervisor.JobChild,'spawn') as spawn:
            with self.assertRaisesRegex(RuntimeError,'Stop/Pause'):
                with self.lease(stopped=lambda:True):pass
        spawn.assert_not_called()

    def test_startup_failure_and_timeout_stop_only_owned_child(self):
        for code in (1,None):
            with self.subTest(code=code),mock.patch.object(transport.supervisor,'_port_busy',return_value=False),mock.patch.object(transport.RelayLease,'_ready',return_value=False),mock.patch.object(transport.supervisor.JobChild,'spawn',return_value=self.child):
                self.child.reset_mock();self.child.poll.return_value=code
                with self.assertRaises((RuntimeError,TimeoutError)):
                    with self.lease(timeout=0):pass
                self.child.stop.assert_called_once()

    def test_unconfirmed_cleanup_preserves_primary_error_and_custody(self):
        self.child.stop.side_effect=RuntimeError('custody still unknown')
        with mock.patch.object(transport.supervisor,'_port_busy',return_value=False),mock.patch.object(transport.RelayLease,'_ready',return_value=True),mock.patch.object(transport.supervisor.JobChild,'spawn',return_value=self.child):
            with self.assertRaisesRegex(RuntimeError,'primary failure.*custody still unknown'):
                with self.lease():raise ValueError('primary failure')
        self.assertEqual(read(self.root/'relay-transport.json')['state'],'cleanup-unconfirmed')

    def test_private_control_must_report_the_selected_api_port(self):
        write(self.agent['tree']/'relay/local_auth/control.json',{'port':5014,'token':'private-control'})
        with mock.patch.object(transport.supervisor,'_port_busy',return_value=True):
            with mock.patch.object(transport.python_relay,'control',return_value={'port':5014}) as control:self.assertFalse(self.lease()._ready())
            control.assert_called_once()
            with mock.patch.object(transport.python_relay,'control',return_value={'port':5013,'running':True}):self.assertTrue(self.lease()._ready())
            with mock.patch.object(transport.python_relay,'control',return_value={'port':5013,'running':False}):self.assertFalse(self.lease()._ready())
        with mock.patch.object(transport.supervisor,'_port_busy',return_value=False),mock.patch.object(transport.python_relay,'control') as control:
            self.assertFalse(self.lease()._ready());control.assert_not_called()

    def test_foreign_busy_port_is_not_killed_or_replaced(self):
        with mock.patch.object(transport.supervisor,'_port_busy',return_value=True),mock.patch.object(transport.RelayLease,'_ready',return_value=False),mock.patch.object(transport.supervisor.JobChild,'spawn') as spawn:
            with self.assertRaisesRegex(RuntimeError,'identity is unavailable'):
                with self.lease(timeout=0):pass
        spawn.assert_not_called()

    def test_same_port_profiles_take_distinct_authenticated_execution_turns(self):
        import socket, threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from localharness import standalone_relay
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        binary = self.root/'praxis-relay'; binary.write_bytes(b'fixture')
        entered = [threading.Event(), threading.Event()]; release = threading.Event()
        errors = []; profiles = []; starts = []
        for number in range(2):
            cfg = self.root/str(number)/'helene.json'
            write(cfg, {'relay': {'enabled': True, 'port': port, 'key': 'profile-'+str(number)}})
            profiles.append({'tree': cfg.parent/'data', 'config': cfg, 'value': read(cfg)})
        effective = {'roles': {'voice': {'framework': 'openai'}},
            'frameworks': {'openai': {'base_url': 'http://127.0.0.1:'+str(port)+'/v1'}}}
        def spawn(argv, *, cwd, env, log, stopped):
            api = socket.socket(); api.bind(('127.0.0.1', port)); api.listen()
            token = env['RELAY_API_KEY']; starts.append(token)
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args): pass
                def do_POST(self):
                    if self.headers.get('X-Relay-Control') != token:
                        self.send_error(403); return
                    self.rfile.read(int(self.headers['Content-Length']))
                    raw = json.dumps({'port': port, 'running': True}).encode()
                    self.send_response(200); self.send_header('Content-Length', str(len(raw)))
                    self.end_headers(); self.wfile.write(raw)
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever); thread.start()
            write(Path(env['RELAY_AUTH_DIR'])/'control.json', {'port': server.server_port, 'token': token})
            class Child:
                pid = 12345
                def poll(self): return None
                def stop(self):
                    server.shutdown(); server.server_close(); thread.join(3); api.close()
            return Child()
        def worker(number):
            try:
                with transport.review_transport(profiles[number], self.root/'program', self.root/('job-'+str(number)), effective, lambda: False) as lease:
                    self.assertTrue(lease.owned); self.assertFalse(lease.borrowed)
                    self.assertEqual(transport.python_relay.control(lease.agent, 'status')['port'], port)
                    entered[number].set()
                    if number == 0: self.assertTrue(release.wait(5))
            except BaseException as exc: errors.append(exc)
        threads = [threading.Thread(target=worker, args=(number,)) for number in range(2)]
        with mock.patch.object(standalone_relay, 'executable', return_value=binary), \
             mock.patch.object(transport.supervisor.JobChild, 'spawn', side_effect=spawn):
            try:
                threads[0].start(); self.assertTrue(entered[0].wait(5))
                threads[1].start(); self.assertFalse(entered[1].wait(.3))
            finally:
                release.set()
                for thread in threads:
                    if thread.ident is not None: thread.join(8)
        self.assertFalse(any(thread.is_alive() for thread in threads)); self.assertFalse(errors, errors)
        self.assertTrue(entered[1].is_set()); self.assertEqual(starts, ['profile-0', 'profile-1'])

    def test_worker_network_error_is_not_reported_as_stale_candidate(self):
        import t_source_update_pipeline_220 as fixtures
        f=fixtures.PipelineTests('runTest');f.setUp();self.addCleanup(f.doCleanups);f.tick();op=f.operation()
        unit=f.forge._unit_dir(op['task_id'],'agents',op['unit_id'])
        write(unit/'result.json',{'status':'error','error':'APIConnectionError: Connection error.'})
        with self.assertRaisesRegex(ValueError,'Source merger failed: APIConnectionError'):
            fixtures.pipeline._merge_tick(op,f.directory,f.forge)
        with self.assertRaises(ValueError):fixtures.pipeline.validate_ready(f.directory)


if __name__=='__main__':unittest.main()
