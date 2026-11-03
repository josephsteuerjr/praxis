"""Explicit portable Resume must work after the native installation is gone."""
from pathlib import Path
from types import SimpleNamespace
import json,os,sys,tempfile,threading,unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import owner_stop,python_desktop_commands as commands


class Resume(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='explicit-owner-resume-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.data=self.root/'profile/Helene/data';self.data.mkdir(parents=True)
        self.cfg=self.root/'helene.json';self.cfg.write_text('{"mode":"local","relay":{"enabled":true}}',encoding='utf-8')
        self.agent=SimpleNamespace(id='main',tree=self.data,config=self.cfg,enabled=True)
        self.owner=SimpleNamespace(root=self.root,current=lambda:self.agent,transfer_lock=threading.Lock(),
            resume_lock=threading.Lock(),exiting=False,start=Mock(),readiness=Mock(return_value={'runner_pid':42}),
            status=Mock(return_value={'agents':{'main':{'roles':['channel','runner','relay'],'raised':True,'pids':{'relay':43}}}}))
        self.machine=self.root/'old-system-state/stop.json';self.machine.parent.mkdir();self.machine.write_text('{"by":"owner"}',encoding='utf-8')
        self.client=self.data/'owner-stop.json';self.client.write_text('{"by":"owner"}',encoding='utf-8')
        self.legacy=self.root/'old-local-state/owner-stop.json'
        self.addCleanup(patch.stopall)
        patch.object(owner_stop,'seed_file',return_value=self.machine).start()
        patch.object(owner_stop,'client_file',return_value=self.client).start()
        # Added legacy helper is patched once it exists in the implementation.
        if hasattr(owner_stop,'legacy_client_file'):
            patch.object(owner_stop,'legacy_client_file',return_value=self.legacy).start()
        if os.name!='nt':
            from localharness import owner_resume
            class WindowsProtocol:
                name='nt'
                def __getattr__(self,name):return getattr(os,name)
            patch.object(owner_resume,'os',WindowsProtocol()).start()

    def test_resume_clears_old_system_stop_and_starts_the_actual_owner_path(self):
        result=commands.invoke(self.owner,'owner_control',{'action':'resume'})
        self.assertFalse(self.machine.exists());self.assertFalse(self.client.exists())
        self.owner.start.assert_called_once()
        self.owner.readiness.assert_called_once()
        self.assertTrue(result)

    def test_autonomy_pause_is_preserved_when_emergency_stop_is_resumed(self):
        pause=self.data/'memory/.control/autonomy-paused.json';pause.parent.mkdir(parents=True);pause.write_bytes(b'owner pause')
        commands.invoke(self.owner,'owner_control',{'action':'resume'})
        self.assertEqual(pause.read_bytes(),b'owner pause')
        self.owner.start.assert_called_once()

    def test_denied_machine_permission_keeps_stop_and_never_starts_children(self):
        from localharness import owner_resume
        real=owner_resume._remove
        def denied(path,expected):
            if path==self.machine:raise PermissionError('machine stop owned by old service')
            return real(path,expected)
        with patch.object(owner_resume,'_remove',side_effect=denied),patch.object(owner_resume,'_clear_machine',side_effect=RuntimeError('permission refused')):
            with self.assertRaises(RuntimeError):commands.invoke(self.owner,'owner_control',{'action':'resume'})
        self.owner.start.assert_not_called()
        self.assertTrue(self.machine.exists());self.assertTrue(self.client.exists())

    def test_new_panic_during_permission_wait_cancels_resume(self):
        from localharness import owner_resume
        real=owner_resume._remove
        def denied(path,expected):
            if path==self.machine:raise PermissionError('needs Windows permission')
            return real(path,expected)
        def permission(expected):
            owner_stop.latch_client('shortcut','fresh explicit emergency stop')
            self.machine.unlink()
        with patch.object(owner_resume,'_remove',side_effect=denied),patch.object(owner_resume,'_clear_machine',side_effect=permission):
            with self.assertRaises(RuntimeError):commands.invoke(self.owner,'owner_control',{'action':'resume'})
        self.owner.start.assert_not_called()
        self.assertEqual(json.loads(self.client.read_text(encoding='utf-8'))['reason'],'fresh explicit emergency stop')

    def test_new_stop_at_claim_boundary_is_restored(self):
        from localharness import owner_resume
        expected=owner_resume._snapshot(self.client)
        owner_stop.latch_client('shortcut','newer owner stop')
        with self.assertRaises(RuntimeError):owner_resume._remove(self.client,expected)
        self.assertEqual(json.loads(self.client.read_text(encoding='utf-8'))['reason'],'newer owner stop')

    def test_success_clears_legacy_user_marker_without_touching_other_files(self):
        self.legacy.parent.mkdir();self.legacy.write_bytes(b'old user stop')
        keep=self.legacy.parent/'keep';keep.write_bytes(b'owner data')
        commands.invoke(self.owner,'owner_control',{'action':'resume'})
        self.assertFalse(self.legacy.exists());self.assertEqual(keep.read_bytes(),b'owner data')

    def test_programme_exit_during_resume_keeps_admission_closed(self):
        self.owner.exiting=True
        with self.assertRaises(InterruptedError):commands.invoke(self.owner,'owner_control',{'action':'resume'})
        self.owner.start.assert_not_called();self.assertTrue(self.client.exists())


class PanicPath(unittest.TestCase):
    def test_panic_cli_selects_canonical_profile_data_before_latching(self):
        import python_app
        from localharness import first_start,bootstrap_ready
        with tempfile.TemporaryDirectory(prefix='panic-profile-') as t:
            data=Path(t)/'profile/Helene/data';observed=[]
            with (patch.object(python_app.sys,'platform','win32'),
                  patch.object(first_start.Places,'current',return_value=SimpleNamespace(data=data)),
                  patch.object(bootstrap_ready,'signal'),
                  patch.dict(os.environ,{'HELENE_USER_DATA':''}),
                  patch.object(owner_stop,'latch_client',side_effect=lambda *_:observed.append(os.environ.get('HELENE_USER_DATA')))):
                self.assertEqual(python_app._main(['--root',t,'--panic']),0)
            self.assertEqual(observed,[str(data)])


if __name__=='__main__':unittest.main()
