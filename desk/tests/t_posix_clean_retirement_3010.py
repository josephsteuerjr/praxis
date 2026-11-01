"""Real POSIX file retirement: clean code, retained data, cancellation and links."""
import json,os,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_posix as migration

@unittest.skipIf(os.name=='nt','POSIX retirement consumer; Windows adapter has separate tests')
class Retirement(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='posix-retirement-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.old=self.root/'old';self.new=self.root/'new';self.new.mkdir()
        self.note={'source_policy':'clean','old_quiescent':{'old':str(self.old)},
                   'data_paths':[str(self.old/'data')],'old_data_paths':[],'source_paths':[]}
        self.put('data/memory/history.jsonl',b'history')
    def put(self,name,value):
        p=self.old/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(value);return p
    def retire(self,**kwargs):
        with patch.object(migration,'quiescent'):
            return migration.remove_program(self.old,self.new,self.note,before_start=True,**kwargs)
    def test_clean_removes_internal_code_and_optional_runtime_but_retains_data_and_external_source(self):
        old_code=self.put('code/authored/agent.py',b'old authored code')
        runtime=self.put('voice-xtts/bin/python',b'old optional runtime')
        self.put('code/authored/.git/objects/fixture',b'old Git object')
        nested=self.put('code/authored/memory/history.jsonl',b'nested private history')
        model=self.put('components/xtts/model/weights.pth',b'model data')
        personal=self.put('personal.txt',b'unknown personal file')
        external=self.root/'external';external.mkdir();(external/'agent.py').write_bytes(b'external authored code')
        self.note['source_paths']=[{'old':str(external),'selected':str(self.new/'tree')}]
        self.assertTrue(self.retire()['ok'])
        self.assertFalse(old_code.exists());self.assertFalse(runtime.exists())
        self.assertEqual(nested.read_bytes(),b'nested private history');self.assertEqual(model.read_bytes(),b'model data')
        self.assertEqual(personal.read_bytes(),b'unknown personal file')
        self.assertEqual((external/'agent.py').read_bytes(),b'external authored code')
        self.assertFalse((self.new/'code-backup').exists())
    def test_event_cancellation_is_checked_before_retirement_effects(self):
        source=self.put('app/main.py',b'old');cancelled=threading.Event();cancelled.set()
        with self.assertRaises(InterruptedError):self.retire(cancelled=cancelled)
        self.assertEqual(source.read_bytes(),b'old');self.assertFalse((self.new/'old-program-retirement.json').exists())
    def test_linked_code_directory_is_not_followed(self):
        outside=self.root/'outside';outside.mkdir();(outside/'agent.py').write_bytes(b'external')
        (self.old/'code').symlink_to(outside,target_is_directory=True)
        self.retire()
        self.assertEqual((outside/'agent.py').read_bytes(),b'external');self.assertTrue((self.old/'code').is_symlink())
    def test_changed_file_is_refused_against_saved_retirement_inventory(self):
        source=self.put('app/a.py',b'old')
        def progress(value):
            if value.get('files')==0:source.write_bytes(b'changed after inventory')
        with self.assertRaisesRegex(RuntimeError,'changed'):self.retire(progress=progress)
        self.assertEqual(source.read_bytes(),b'changed after inventory')
        self.assertEqual((self.old/'data/memory/history.jsonl').read_bytes(),b'history')

    def test_parent_swapped_to_external_link_after_inventory_cannot_redirect_delete(self):
        source=self.put('app/main.py',b'identical payload')
        outside=self.root/'outside';outside.mkdir();foreign=outside/'main.py';foreign.write_bytes(source.read_bytes())
        def progress(value):
            if value.get('files')==0:
                (self.old/'app').rename(self.old/'moved-app')
                (self.old/'app').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'changed|linked'):self.retire(progress=progress)
        self.assertEqual(foreign.read_bytes(),b'identical payload')
        self.assertEqual((self.old/'moved-app/main.py').read_bytes(),b'identical payload')
        self.assertEqual((self.old/'data/memory/history.jsonl').read_bytes(),b'history')

if __name__=='__main__':unittest.main()
