"""Profile and program contract; native consumers additionally run on macOS."""
from pathlib import Path
import json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import posix_first_start as first,first_start as common,python_posix

class FirstStart(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.root=self.base/'App folder';self.data=self.base/'Profile/data'
        self.places=first.Places(self.data)
        for name in ('runtime/bin/python3','app/python_app.py','app/deskapp.py','app/first_start_window.py',
                     'app/resources/first-start.html','tree/agent.py','tree/llm.py'):
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# public fixture\n')
        (self.root/'python-app.json').write_text('{"format":"helene-python-app.v1"}')
    def test_new_profile_and_api_creation_separate_data_and_code(self):
        self.assertFalse(first.prepare(self.root,places=self.places)['agent'])
        value=common.load(self.root/'helene.json')
        self.assertEqual(value['tree'],str(self.data));self.assertTrue(value['python'].endswith('runtime/bin/python3'))
        with patch('localharness.standalone_relay.install',side_effect=AssertionError('API must not download relay')):
            first.create_agent(self.root,dict(name='Fixture',provider='openai',model='fixture',key='fixture-only'),places=self.places)
        self.assertTrue(first.agent_exists(self.data));self.assertFalse((self.root/'data').exists())
        self.assertEqual(common.load(self.data/'memory/.control/profile-identity.json')['agent_name'],'Fixture')
    def test_subscription_install_failure_leaves_retryable_form_without_identity(self):
        first.prepare(self.root,places=self.places)
        with patch('localharness.standalone_relay.ensure_configured',side_effect=OSError('offline fixture')):
            with self.assertRaises(OSError):first.create_agent(self.root,dict(name='Fixture',provider='chatgpt',model='fixture'),places=self.places)
        self.assertFalse(first.agent_exists(self.data));self.assertFalse(common.load(self.root/'helene.json')['setup_complete'])
    def test_receipted_profile_is_ready_without_windows_folder_environment(self):
        first.prepare(self.root,places=self.places)
        with patch.dict(os.environ,{},clear=True):
            self.assertTrue(first.ready(self.root))
    def test_existing_profile_queues_devices_and_source_edits_stay_exact(self):
        files={'memory/groups/1.jsonl':'{"text":"history"}\n','memory/.state/queue.json':'{"queued":[1]}',
               'memory/.state/devices.json':'{"fixture":{}}','memory/.state/phone-pairs.json':'{}','workspace/note.txt':'owner work'}
        for name,body in files.items():
            p=self.data/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(body)
        (self.root/'tree/agent.py').write_text('# authored change\n')
        self.assertTrue(first.prepare(self.root,places=self.places)['agent'])
        for name,body in files.items():self.assertEqual((self.data/name).read_text(),body)
        self.assertEqual((self.root/'tree/agent.py').read_text(),'# authored change\n')
        self.assertEqual(first.Places.current(self.root).data,self.data)
    def test_overlap_and_program_profile_are_refused_without_cleanup(self):
        for selected in (self.root,self.root/'data',self.root.parent):
            with self.assertRaises(ValueError):first.prepare(self.root,places=first.Places(selected))
        (self.data/'app').mkdir(parents=True);(self.data/'app/python_app.py').write_text('retain');(self.data/'app/deskapp.py').write_text('retain')
        with self.assertRaises(ValueError):first.prepare(self.root,places=self.places)
        self.assertEqual((self.data/'app/python_app.py').read_text(),'retain')
    @unittest.skipUnless(sys.platform=='darwin','Actual macOS system aliases')
    def test_system_alias_is_accepted_but_user_link_is_still_refused(self):
        from localharness import program_state
        with tempfile.TemporaryDirectory() as raw:
            self.assertEqual(common.plain(raw),Path(raw).resolve())
            self.assertEqual(program_state._plain(raw),Path(raw).resolve())
            link=Path(raw)/'owner-link';link.symlink_to(self.root,target_is_directory=True)
            for guard in (common.plain,program_state._plain):
                with self.assertRaises(ValueError):guard(link/'new-file')
    @unittest.skipIf(os.name=='nt','POSIX native path spelling')
    def test_macos_login_item_is_per_user_owned_and_foreign_bytes_retained(self):
        from types import SimpleNamespace
        owner=SimpleNamespace(root=self.root)
        with patch.object(sys,'platform','darwin'),patch.object(Path,'home',return_value=self.base):
            path,text=python_posix.startup_binding(self.root)
            self.assertTrue(str(path).startswith(str(self.base/'Library/LaunchAgents')))
            python_posix.invoke(owner,'autostart_set',{'on':True})
            self.assertTrue(python_posix.invoke(owner,'autostart_get',{}))
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            path.write_text('foreign item')
            with self.assertRaises(RuntimeError):python_posix.invoke(owner,'autostart_set',{'on':False})
            self.assertEqual(path.read_text(),'foreign item')

if __name__=='__main__':unittest.main(verbosity=2)
