"""Real local session transport: disposable observers never own the host.

Uses an isolated deterministic Controller (no agent/model/network other than its
loopback control). Native process birth/exit and production Host HTTP are real.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import session_host as session, python_updates as updates
from localharness.package_notice import read, write


class ShutdownObservation(unittest.TestCase):
    def test_lost_exit_reply_accepts_only_confirmed_absence_without_replaying(self):
        client=object.__new__(session.SessionClient);client.descriptor={'pid':42,'birth':1}
        client._request=Mock(side_effect=OSError('connection refused'));client.close=Mock()
        with patch.object(session,'process_state',return_value='absent'):
            self.assertTrue(client.shutdown()['already_exited'])
        client._request.assert_called_once_with('exit',timeout=45);client.close.assert_called_once()

    def test_unknown_exit_custody_remains_an_error(self):
        client=object.__new__(session.SessionClient);client.descriptor={'pid':42,'birth':1}
        client._request=Mock(side_effect=OSError('connection refused'));client.close=Mock()
        with patch.object(session,'process_state',return_value='unknown'):
            with self.assertRaises(OSError):client.shutdown()
        client._request.assert_called_once();client.close.assert_not_called()


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='helene-session-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / 'program with spaces'
        self.tree = self.home / 'independent data'
        self.root.mkdir(); self.tree.mkdir()
        write(self.root/'python-app.json', {'version':'2.3.0'})
        write(self.root/'helene.json', {'tree':str(self.tree),'code':'tree','setup_complete':False})
        write(self.root/'agents/other/helene.json', {'tree':str(self.home/'other-data'),'code':'../../tree','setup_complete':False})
        self.process = None

    def start(self):
        app = Path(__file__).resolve().parents[1]
        script = r'''
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from localharness import session_host as session
from localharness.python_controller import Controller
from localharness.package_notice import read,write
session.owner_stop.stopped=lambda:False
class FixtureController(Controller):
    def start(self): pass
    def close(self): pass
    def status(self): return {'agents':{},'fixture':True}
    def readiness(self,**args): return {'identity':self.current().id}
    def invoke(self,command,args):
        if command in ('update_start','update_cancel'):
            return super().invoke(command,args)
        target=self.current().tree/'effects.json'
        if command=='effect':
            value=read(target);value['count']=value.get('count',0)+1;write(target,value);return value
        if command=='selected': return {'id':self.current().id}
        raise ValueError('Unsupported fixture command')
session.Host(Path(sys.argv[2]),controller_factory=FixtureController).run()
'''
        self.process = subprocess.Popen([sys.executable,'-B','-c',script,str(app),str(self.root)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.addCleanup(self.stop)
        deadline = time.monotonic()+10
        while time.monotonic()<deadline:
            descriptor = read(session.control_path(self.root))
            if descriptor.get('pid')==self.process.pid and descriptor.get('state')=='running':
                session._rpc(descriptor,'status');self.descriptor=descriptor;return
            if self.process.poll() is not None:
                self.fail('fixture host exited: '+self.process.stderr.read().decode('utf8','replace'))
            time.sleep(.03)
        self.fail('fixture host admission timeout')

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        if self.process and self.process.stderr:self.process.stderr.close()

    def client(self, agent='', browser_mode=False):
        client=session.SessionClient(self.root,self.descriptor,lambda *args:None,'http://127.0.0.1:39401',agent,browser_mode)
        self.addCleanup(client.close)
        return client

    def test_detach_rejoin_keeps_same_real_owner_and_effect_not_replayed(self):
        self.start();first=self.client()
        self.assertEqual(first.invoke('effect',{})['count'],1)
        first.close();self.assertIsNone(self.process.poll())
        second=self.client()
        self.assertEqual(second.descriptor['generation'],first.descriptor['generation'])
        self.assertEqual(read(self.tree/'effects.json')['count'],1)
        self.assertTrue(second.status()['fixture'])
        second.shutdown();self.process.wait(timeout=8)
        self.assertEqual(self.process.returncode,0)

    def test_explicit_rc3_recovery_exits_exact_failed_host_without_replaying_work(self):
        from localharness import migration_job,desktop_update_request as jobs
        self.start()
        value={'id':'c'*32,'kind':'migration','program':str(self.root),'tree':str(self.tree),
            'state':'attention','phase':'preparing','error':'TimeoutError: Runner ownership heartbeat unconfirmed',
            'legacy_handoff':{'old':str(self.root)},
            'admission':{k:self.descriptor[k] for k in ('pid','birth','generation')}}
        jobs._save(self.tree,value)
        self.assertEqual(migration_job.failed_session(self.root)['id'],value['id'])
        self.assertTrue(migration_job.release_failed_session(self.root,value['id'])['released'])
        self.process.wait(timeout=8)
        self.assertEqual(jobs.status(self.tree)['state'],'cancelled')
        self.assertFalse((self.tree/'effects.json').exists())

    def test_rc3_recovery_never_stops_running_work_or_mismatched_birth(self):
        from localharness import migration_job,desktop_update_request as jobs
        self.start()
        value={'id':'d'*32,'kind':'migration','program':str(self.root),'tree':str(self.tree),
            'state':'running','phase':'preparing','error':'TimeoutError: Runner ownership heartbeat unconfirmed',
            'legacy_handoff':{'old':str(self.root)},
            'admission':{k:self.descriptor[k] for k in ('pid','birth','generation')}}
        jobs._save(self.tree,value)
        self.assertIsNone(migration_job.failed_session(self.root))
        value['state']='attention';value['admission']['birth']+=1;jobs._save(self.tree,value)
        with self.assertRaises(ValueError):migration_job.release_failed_session(self.root,value['id'])
        self.assertIsNone(self.process.poll())

    def test_wrong_token_generation_or_origin_never_mutates(self):
        self.start()
        for key,value in [('token','wrong'),('generation','old')]:
            with self.assertRaises((RuntimeError, OSError)):
                session._rpc({**self.descriptor,key:value},'invoke',{'command':'effect','arguments':{}})
        self.assertFalse((self.tree/'effects.json').exists())
        with self.assertRaises(ValueError):session._origin('http://evil.example:1234')

    def test_two_observers_keep_agent_selection_independent_and_exact_cors(self):
        self.start();first=self.client();second=self.client('other')
        self.assertEqual(first.invoke('selected',{})['id'],'main')
        self.assertEqual(second.invoke('selected',{})['id'],'other')
        self.assertEqual(first.invoke('selected',{})['id'],'main')
        self.assertTrue(session.configured_origin(self.root/'helene.json','http://127.0.0.1:39401'))
        self.assertFalse(session.configured_origin(self.root/'helene.json','http://127.0.0.1:39402'))
        descriptor=read(session.control_path(self.root))
        write(session.control_path(self.root),{**descriptor,'tree':str(self.home/'foreign')})
        self.assertFalse(session.allowed_origin(self.tree,'http://127.0.0.1:39401'))
        write(session.control_path(self.root),descriptor)
        first.close();second.close()
        self.assertFalse(session.allowed_origin(self.tree,'http://127.0.0.1:39401'))

    def test_stale_process_birth_does_not_authorize_cors(self):
        self.start();client=self.client()
        descriptor=read(session.control_path(self.root))
        write(session.control_path(self.root),{**descriptor,'birth':descriptor['birth']-1})
        self.assertEqual(session.process_state(read(session.control_path(self.root))),'changed')
        self.assertFalse(session.allowed_origin(self.tree,'http://127.0.0.1:39401'))
        write(session.control_path(self.root),descriptor);client.close()

    def test_existing_update_url_is_the_only_agent_feed_store(self):
        from localharness.python_controller import Controller
        owner=Controller(self.root,lambda *args:None)
        value=read(self.root/'helene.json');value['update']={'url':'https://example.test/helene/rc.json'};write(self.root/'helene.json',value)
        self.assertEqual(updates.release_endpoint(owner),'https://example.test/helene/rc.json')
        with self.assertRaisesRegex(ValueError,'latest only'):updates.release_endpoint(owner,'2.3.1')
        value.pop('update');write(self.root/'helene.json',value)
        self.assertIn('/josephsteuerjr/praxis/releases/tags/v2.3.1',updates.release_endpoint(owner,'2.3.1'))
        with self.assertRaisesRegex(ValueError,'obsolete'):updates.update_install(owner,'not-executed.zip',forceExtensions=True)

    def test_update_admission_precedes_download_and_survives_detach(self):
        self.start();client=self.client()
        result=client.invoke('update_start',{'url':'https://example.test/Helene-2.3.0-rc.1.zip','sha256':'a'*64,'sourceMode':'clean'})
        self.assertEqual(result['state'],'admitted')
        identity=result['job']['id']
        from localharness.desktop_update_request import status
        self.assertEqual(status(self.tree)['state'],'confirmed')
        self.assertNotIn('archive',status(self.tree))
        self.assertEqual(status(self.tree)['admission']['pid'],self.process.pid)
        repeated=client.invoke('update_start',{'url':'https://example.test/Helene-2.3.0-rc.1.zip','sha256':'a'*64,'sourceMode':'clean'})
        self.assertEqual(repeated['job']['id'],identity)
        client.close();self.assertIsNone(self.process.poll())
        self.assertEqual(status(self.tree)['id'],identity)
        second=self.client();second.shutdown();self.process.wait(timeout=8)
        from localharness.desktop_update_request import cancelled
        self.assertTrue(cancelled(self.tree,identity))
        self.assertTrue(status(self.tree)['cancel_requested'])

    def test_update_retains_admitting_observer_presentation_after_detach(self):
        self.start();browser=self.client(browser_mode=True)
        result=browser.invoke('update_start',{'url':'https://example.test/Helene.zip','sha256':'a'*64})
        self.assertTrue(result['job']['browser_mode'])
        browser.close()
        from localharness.desktop_update_request import status
        self.assertTrue(status(self.tree)['browser_mode'])
        self.assertEqual(status(self.tree)['source_mode'],'clean')  # Legacy fixture has no authored baseline.
        with self.assertRaises(RuntimeError):
            session._rpc(self.descriptor,'invoke',{'observer':browser.observer,'command':'update_start',
                'arguments':{'url':'https://example.test/other.zip'}})

    def test_rc_version_order_and_direct_feed_checksum(self):
        from types import SimpleNamespace
        self.assertGreater(updates.parts('2.3.0-rc.2'),updates.parts('2.3.0-rc.1'))
        self.assertGreater(updates.parts('2.3.0'),updates.parts('2.3.0-rc.99'))
        self.assertGreater(updates.parts('2.3.0-rc.1'),updates.parts('2.2.17'))
        with patch.object(updates,'request_json',return_value={'version':'2.3.0-rc.1','url':'https://example.test/Helene-2.3.0-rc.1.zip','sha256':'a'*64}), \
             patch.object(updates,'version',return_value='2.2.17'), \
             patch('localharness.python_platform.target',return_value='windows-x86_64'), \
             patch('localharness.python_platform.distribution',return_value=None):
            result=updates.update_check(SimpleNamespace(root=self.root),'https://example.test/rc.json')
        self.assertEqual(result['sha256'],'a'*64);self.assertTrue(result['newer'])


if __name__=='__main__':unittest.main()
