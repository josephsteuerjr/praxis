"""Native uninstall precedes attachment; no parallel Python removal pipeline."""
from pathlib import Path
import os,sys,tempfile,unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
from localharness import migration_windows as windows,windows_installation as installation,migration_lifecycle as lifecycle
from localharness import source_update_host
from localharness.package_notice import read,write
from localharness.update_progress import Progress
import t_migration_clean_windows_2810 as clean_fixture


@unittest.skipUnless(os.name=='nt','Windows native uninstaller')
class NativeMigration(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='native-migration-');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.old,self.new=clean_fixture.CleanMigration().fixture(self.root)
        (self.old/'helene.exe').write_bytes(b'old application')
        (self.old/'helene-setup.exe').write_bytes(b'fixture uninstaller; never executed')
        self.command=[str(self.old/'helene-setup.exe'),'--uninstall','--quiet']
        self.data=self.old/'data';self.history=(self.data/'memory/history.jsonl').read_bytes()
        self.progress=Progress(self.root/'.helene-update-native.progress.json','native',self.new)
        # Lifecycle fixtures never enumerate the actual owner's desktop/pins.
        # The dedicated exact-target cases supply their own shortcut directory.
        shortcut_scope=patch.object(windows,'shortcut_folders',return_value=[])
        shortcut_scope.start();self.addCleanup(shortcut_scope.stop)
        empty_scope=patch.object(windows,'remove_empty_legacy_local',return_value={'state':'isolated-fixture'})
        empty_scope.start();self.addCleanup(empty_scope.stop)

    def run_clean(self):
        with patch.object(source_update_host,'verified_runtime',return_value={'kind':'test runtime custody'}):
            return installation.run(self.new,self.new,old=self.old,launch=False,progress=self.progress)

    def test_installed_native_removes_itself_before_same_data_is_attached(self):
        preferences=b'{"appearance":"paper"}'
        (self.old/'window-preferences.json').write_bytes(preferences)
        write(self.old/'agent-default.json',{'id':'base'})
        events=[]
        def uninstall(old,command,data,**kwargs):
            self.assertFalse((self.new/'helene.json').exists())
            self.assertEqual(data,[str(self.data)])
            (old/'helene.exe').unlink();(old/'code/agent.py').unlink()
            (old/'window-preferences.json').unlink();(old/'agent-default.json').unlink()
            events.append('native-uninstall')
            return {'ok':True,'method':'native_uninstaller','purge':False}
        with patch.object(windows,'registered_uninstall',return_value=self.command), \
             patch.object(windows,'uninstall_before_attach',side_effect=uninstall), \
             patch.object(lifecycle,'stop',side_effect=AssertionError('No preliminary Python stop')), \
             patch.object(lifecycle,'cleanup_installation',side_effect=AssertionError('Native uninstaller owns integrations')), \
             patch.object(lifecycle,'remove_program',side_effect=AssertionError('No second deletion walk')), \
             patch.object(lifecycle,'quiescent',side_effect=lambda *a:events.append('verified-dead')):
            note=self.run_clean()
        cfg=read(self.new/'helene.json')
        self.assertEqual(events[0],'native-uninstall')
        self.assertEqual(Path(cfg['tree']),self.data)
        self.assertEqual(note['retirement']['method'],'native_uninstaller')
        self.assertTrue(note['old_retired_before_attach']);self.assertEqual(note['copied_data'],[])
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),self.history)
        self.assertEqual((self.new/'window-preferences.json').read_bytes(),preferences)
        self.assertEqual(read(self.new/'agent-default.json'),{'id':'base'})
        self.assertEqual([p['stage'] for p in self.progress.value['stages']],['checking_programme','uninstalling_old','connecting_data','prepared'])

    def test_uninstall_failure_never_falls_back_to_python_deletion_or_attachment(self):
        with patch.object(windows,'registered_uninstall',return_value=self.command), \
             patch.object(windows,'uninstall_before_attach',side_effect=RuntimeError('uninstaller failed')), \
             patch.object(lifecycle,'stop',side_effect=AssertionError('No second stop')), \
             patch.object(lifecycle,'remove_program',side_effect=AssertionError('No second removal')):
            with self.assertRaisesRegex(RuntimeError,'uninstaller failed'):self.run_clean()
        self.assertFalse((self.new/'helene.json').exists())
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),self.history)

    def test_native_setup_is_used_when_its_registration_is_already_missing(self):
        with patch.object(windows,'registered_uninstall',return_value=None):
            self.assertEqual(windows.uninstaller_for(self.old),self.command)

    def test_no_uninstaller_is_a_portable_programme(self):
        (self.old/'helene-setup.exe').unlink()
        with patch.object(windows,'registered_uninstall',return_value=None):
            self.assertIsNone(windows.uninstaller_for(self.old))

    def test_purge_and_foreign_uninstaller_cannot_be_admitted(self):
        for command in ([*self.command,'--purge'],[str(self.new/'helene-setup.exe'),'--uninstall','--quiet']):
            with self.subTest(command=command),patch.object(windows,'uninstall_registered') as uninstall:
                with self.assertRaisesRegex(ValueError,'data-preserving'):windows.uninstall_before_attach(self.old,command,[str(self.data)])
                uninstall.assert_not_called()

    def test_nonstandard_in_program_data_is_refused_before_uninstall(self):
        custom=self.old/'custom-profile';custom.mkdir()
        with patch.object(windows,'uninstall_registered') as uninstall:
            with self.assertRaisesRegex(ValueError,'data directory'):windows.uninstall_before_attach(self.old,self.command,[str(custom)])
            uninstall.assert_not_called()

    def test_native_uninstall_leaving_external_descendant_is_not_success(self):
        row={'ProcessId':123,'Birth':4.0}
        with patch.object(windows,'owned_processes',return_value=[row]), \
             patch.object(windows,'uninstall_registered',return_value={'ok':True}), \
             patch('localharness.session_host.process_state',return_value='alive'), \
             patch.object(windows,'stop_generation',return_value=False), \
             patch.object(windows.time,'monotonic',side_effect=[0,601]):
            with self.assertRaisesRegex(RuntimeError,'descendant'):windows.uninstall_before_attach(self.old,self.command,[str(self.data)])

    def test_native_leftover_emergency_link_is_removed_by_target_only(self):
        links=self.root/'links';links.mkdir()
        emergency=links/'Helene — аварийный стоп.lnk'
        foreign=links/'Other Helene — аварийный стоп.lnk'
        windows.shortcut(emergency,self.old/'helene.exe',self.old,'--panic --notify')
        windows.shortcut(foreign,self.new/'helene.exe',self.new,'--panic --notify')
        def native_uninstall(*args,**kwargs):
            self.assertTrue(emergency.exists(),'Python must not clean before native uninstall')
            (self.old/'helene.exe').unlink()
            return {'ok':True,'purge':False}
        with patch.object(windows,'owned_processes',return_value=[]), \
             patch.object(windows,'uninstall_registered',side_effect=native_uninstall), \
             patch.object(windows,'quiescent'),patch.object(windows,'service',return_value=None), \
             patch.object(windows,'shortcut_folders',return_value=[links]):
            receipt=windows.uninstall_before_attach(self.old,self.command,[str(self.data)])
        self.assertEqual(receipt['shortcuts_removed'],[str(emergency)])
        self.assertFalse(emergency.exists());self.assertTrue(foreign.exists())
        self.assertEqual((self.data/'memory/history.jsonl').read_bytes(),self.history)

    def test_late_elevated_worker_is_joined_without_killing_acl_work(self):
        worker={'ProcessId':123,'Birth':4.0,'ExecutablePath':str(self.old/'helene-setup.exe')}
        (self.old/'helene.exe').unlink()
        states=iter(['alive','absent'])
        events=[]
        with patch.object(windows,'owned_processes',side_effect=[[],[worker],[]]), \
             patch.object(windows,'uninstall_registered',return_value={'ok':True}), \
             patch('localharness.session_host.process_state',side_effect=lambda row:next(states)), \
             patch.object(windows,'stop_generation',side_effect=AssertionError('Never kill an active native uninstaller')), \
             patch.object(windows.time,'monotonic',side_effect=[0,180]),patch.object(windows.time,'sleep'), \
             patch.object(windows,'quiescent'),patch.object(windows,'service',return_value=None), \
             patch.object(windows,'remove_shortcuts',return_value=[]):
            receipt=windows.uninstall_before_attach(self.old,self.command,[str(self.data)],progress=events.append)
        self.assertTrue(receipt['old_processes_confirmed_dead'])
        self.assertEqual(events[0]['processes'],[{'pid':123,'name':'helene-setup.exe'}])

    def test_existing_elevated_uninstall_is_joined_without_second_invocation(self):
        worker={'ProcessId':123,'Birth':4.0,'ExecutablePath':str(self.old/'helene-setup.exe')}
        (self.old/'helene.exe').unlink()
        with patch.object(windows,'owned_processes',side_effect=[[worker],[]]), \
             patch.object(windows,'uninstall_registered',side_effect=AssertionError('Do not start a duplicate uninstaller')), \
             patch('localharness.session_host.process_state',side_effect=['alive','absent']), \
             patch.object(windows,'stop_generation',side_effect=AssertionError('Do not kill the native worker')), \
             patch.object(windows,'quiescent'),patch.object(windows,'service',return_value=None), \
             patch.object(windows,'remove_shortcuts',return_value=[]):
            receipt=windows.uninstall_before_attach(self.old,self.command,[str(self.data)])
        self.assertEqual(receipt['joined_existing'],[{'pid':123,'birth':4.0}])


if __name__=='__main__':unittest.main()
