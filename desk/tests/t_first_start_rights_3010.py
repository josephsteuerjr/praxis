"""Scope, data custody and native helper checks in owned temporary trees."""
import os
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
from unittest.mock import MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import first_start as policy,windows_first_start as native

@unittest.skipUnless(os.name=='nt','Windows native retirement')
class Rights(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='first-rights-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.old=self.base/'old';self.old.mkdir()
        self.new=self.base/'new';self.new.mkdir();self.data=self.old/'data';self.data.mkdir()
        (self.data/'keep').write_bytes(b'private data');(self.old/'obsolete.py').write_text('old code')
    def request(self,action,source):
        request=dict(action=action,incoming=str(self.new),roots=[str(self.old)],protected=[str(self.data)],
                     source=str(source) if source is not None else '')
        request['custody']=native._capture_custody(request)
        return request
    def test_helper_prunes_code_but_never_removes_data_or_incoming(self):
        request=self.request('prune',self.old);request['keep']=[str(self.data)]
        native.privileged_action(request)
        self.assertFalse((self.old/'obsolete.py').exists());self.assertEqual((self.data/'keep').read_bytes(),b'private data')
        for path in (self.data,self.old,self.new,self.base/'outside'):
            with self.assertRaises(ValueError):native.privileged_action(self.request('remove',path))
    def test_helper_removes_exact_declared_old_file_and_not_a_new_process(self):
        native.privileged_action(self.request('remove',self.old/'obsolete.py'))
        self.assertFalse((self.old/'obsolete.py').exists())
        with patch.object(native,'owned',return_value=[]),patch.object(native,'terminate') as kill:
            request=self.request('terminate',None);request['source']='';request.update(pid=os.getpid(),birth=1.0)
            with self.assertRaises(ValueError):native.privileged_action(request)
            kill.assert_not_called()
    def test_reparse_cleanup_removes_link_only_and_preserves_target(self):
        import subprocess
        outside=self.base/'outside';outside.mkdir();(outside/'keep').write_bytes(b'unrelated')
        link=self.old/'linked'
        subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
        native.Backend(self.new).remove(link)
        self.assertFalse(link.exists());self.assertEqual((outside/'keep').read_bytes(),b'unrelated')
    def test_owner_stop_uses_canonical_data_without_recreating_local_placeholder(self):
        from localharness import owner_stop
        places=policy.Places(self.base,(),(),self.base/'Downloads')
        with patch.object(policy.Places,'current',return_value=places),patch.dict(os.environ,HELENE_USER_DATA=str(places.data)):
            self.assertEqual(owner_stop.client_file(),places.data/'owner-stop.json')
    def test_absolute_process_affinity_has_no_relative_cwd_accident(self):
        self.assertFalse(native._inside('data',[self.old]))
        self.assertTrue(native._inside(self.old/'app/runner.py',[self.old]))
        self.assertFalse(native._inside(str(self.old)+'-other/app/runner.py',[self.old]))
        self.assertTrue(native._inside('\\\\?\\'+str(self.old/'app/runner.py'),[self.old]))
    def test_termination_race_requires_actual_same_handle_exit(self):
        import ctypes
        for exited in (True,False):
            api=MagicMock();api.OpenProcess.return_value=123
            api.WaitForSingleObject.side_effect=[258,0 if exited else 258]
            def finished(*_):ctypes.set_last_error(5);return False
            api.TerminateProcess.side_effect=finished
            with patch.object(native,'kernel',return_value=api):
                if exited:native.terminate(999999,0)
                else:
                    with self.assertRaises(PermissionError):native.terminate(999999,0)
            api.CloseHandle.assert_called_once_with(123)
    def test_legacy_script_and_emergency_shortcuts_match_the_exact_programme(self):
        from localharness.migration_windows import link_belongs
        for name in ('Start Helene.vbs','Helene setup and recovery.vbs','helene.exe','app/windows_start.py'):
            self.assertTrue(link_belongs({'target':str(self.base/'wscript.exe'),'arguments':'"'+str(self.old/name)+'" --panic'},self.old))
            self.assertTrue(link_belongs({'target':str(self.base/'wscript.exe'),'arguments':'"'+name+'"','cwd':str(self.old)},self.old))
        self.assertFalse(link_belongs({'target':str(self.base/'editor.exe'),'arguments':'"'+str(self.old/'notes.md')+'"'},self.old))
        self.assertFalse(link_belongs({'target':str(self.base/'wscript.exe'),'arguments':'"'+str(self.base/'old-other/Start Helene.vbs')+'"'},self.old))

if __name__=='__main__':unittest.main()
