"""Cold-runner readiness and failure custody, without an owner profile/model."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_job as migration, desktop_update_request as jobs
from localharness.python_controller import Controller
from localharness.package_notice import read, write


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='helene-rc4-')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'old';self.root.mkdir()
        self.data=self.base/'data';self.data.mkdir();self.code=self.base/'authored';self.code.mkdir()
        write(self.root/'helene.json',{'tree':str(self.data),'code':str(self.code),'setup_complete':True})

    def test_cold_runner_first_heartbeat_after_old_deadline_still_requires_exact_identity(self):
        owner=Controller(self.root,lambda *a:None);clock=[1000.]
        state={'agents':{'main':{'raised':True,'pids':{'channel':41,'runner':42}}}}
        def open_url(url,**kw):
            return io.BytesIO(json.dumps({'product':'Hélène','root':str(self.root),'pid':41}
                if '/api/who' in url else {'tree':str(self.data)}).encode())
        http=SimpleNamespace(open=open_url)
        def load(path):
            if path==self.root/'helene.json':return {'code':str(self.code),'setup_complete':True}
            return {'pid':42 if clock[0]>=1036 else 99,'at':clock[0],'code':str(self.code)}
        with patch('urllib.request.build_opener',return_value=http),patch.object(owner,'status',return_value=state),\
             patch('localharness.python_controller.load',side_effect=load),\
             patch('localharness.desktop_supervisor._token',return_value='fixture'),\
             patch('localharness.desktop_supervisor.runner_required',return_value=True),\
             patch('localharness.python_controller.time.monotonic',side_effect=lambda:clock[0]),\
             patch('localharness.python_controller.time.time',side_effect=lambda:clock[0]),\
             patch('localharness.python_controller.time.sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+4)):
            with self.assertRaisesRegex(TimeoutError,'heartbeat'):
                owner.readiness(timeout=30,require_runner=True)
            clock[0]=1000
            self.assertEqual(owner.readiness(timeout=180,require_runner=True)['runner_pid'],42)
            self.assertGreaterEqual(clock[0],1036)
            with self.assertRaises(InterruptedError):
                owner.readiness(timeout=180,require_runner=True,cancelled=lambda:True)

    def failed_bootstrap(self,close_error=None):
        package=self.base/'.helene-update-fixture'/'Helene';package.mkdir(parents=True)
        value={'format':jobs.FORMAT if hasattr(jobs,'FORMAT') else 'helene-desktop-update-request.v2',
            'id':'a'*32,'kind':'migration','program':str(self.root),'tree':str(self.data),
            'state':'running','phase':'preparing','migration':{'old':str(self.root),'target':str(self.root),
            'package':str(package),'source_mode':'preserve','fresh':False,'remove_old':False}}
        jobs._save(self.data,value)
        owner=SimpleNamespace(root=self.root,stop=threading.Event(),event=Mock(),start=Mock(),close=Mock(side_effect=close_error),
            readiness=Mock(side_effect=TimeoutError('Runner ownership heartbeat unconfirmed')))
        host=SimpleNamespace(stop=threading.Event())
        from localharness import migration_lifecycle,source_update_host
        with patch.object(jobs,'bind_incoming'),patch.object(migration,'open_observer'),\
             patch.object(migration.owner_stop,'stopped',return_value=False),\
             patch.object(source_update_host,'verified_runtime',return_value={}),\
             patch.object(migration_lifecycle,'service',return_value=None),\
             patch.object(migration_lifecycle,'stop',return_value={}),\
             patch.object(migration_lifecycle,'quiescent'),\
             patch.object(migration_lifecycle,'restore_service',return_value=False):
            migration._execute(host,owner,value)
        return owner,host,jobs.status(self.data)

    def test_failed_bootstrap_releases_legacy_owner_and_retains_exact_job(self):
        owner,host,saved=self.failed_bootstrap()
        owner.close.assert_called_once();self.assertTrue(host.stop.is_set())
        self.assertEqual(saved['id'],'a'*32);self.assertEqual(saved['state'],'attention')
        self.assertTrue(saved['legacy_recovery']['released'])
        self.assertIn('heartbeat',saved['error'])

    def test_uncertain_cleanup_never_claims_release_or_forces_another_owner(self):
        owner,host,saved=self.failed_bootstrap(RuntimeError('child custody unconfirmed'))
        owner.close.assert_called_once();self.assertFalse(host.stop.is_set())
        self.assertFalse(saved['legacy_recovery']['released'])
        self.assertIn('custody unconfirmed',saved['legacy_recovery']['error'])

    def test_observer_program_requires_exact_host_birth_job_and_incoming_path(self):
        import psutil
        from localharness import session_host
        package=self.base/'incoming';package.mkdir()
        descriptor={'program':str(self.root),'tree':str(self.data),'pid':os.getpid(),
            'birth':psutil.Process().create_time(),'generation':'fixture'}
        write(session_host.control_path(self.root),descriptor)
        value={'id':'b'*32,'kind':'migration','program':str(self.root),'tree':str(self.data),
            'migration':{'package':str(package)},'admission':{k:descriptor[k] for k in ('pid','birth','generation')}}
        jobs._save(self.data,value)
        self.assertEqual(migration.observer_program(self.root,package,value['id']),package)
        with self.assertRaises(ValueError):migration.observer_program(self.root,self.base/'foreign',value['id'])
        value['admission']['birth']+=1;jobs._save(self.data,value)
        with self.assertRaises(ValueError):migration.observer_program(self.root,package,value['id'])

    def test_incoming_observer_never_rebinds_or_rewrites_legacy_configuration(self):
        import python_app
        from localharness import portable_profile,session_host
        package=self.base/'incoming';(package/'app/static').mkdir(parents=True)
        (package/'app/static/index.html').write_text('<html></html>')
        before=(self.root/'helene.json').read_bytes()
        with patch.object(migration,'observer_program',return_value=package),\
             patch.object(portable_profile,'bind_program') as bind,\
             patch.object(python_app,'Application',return_value=SimpleNamespace(event=Mock(),origin='http://127.0.0.1:1')),\
             patch.object(session_host,'attach',side_effect=RuntimeError('fixture stops before attach')):
            with self.assertRaisesRegex(RuntimeError,'fixture stops'):
                python_app._main(['--root',str(self.root),'--program',str(package),'--session-job','c'*32])
        bind.assert_not_called();self.assertEqual((self.root/'helene.json').read_bytes(),before)


if __name__=='__main__':unittest.main()
