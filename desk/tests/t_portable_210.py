"""Custody and rollback checks for the real portable migration consumers."""
from pathlib import Path
import json,os,subprocess,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import app_migration as migration, python_source_updater as updater
from localharness import installed_packages as packages
from localharness.portable_profile import digest

def put(root,name,text):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);return path

class Portable(unittest.TestCase):
    def fixture(self,base):
        old=base/'installed-profile';new=base/'new program';external=base/'external authored';data=base/'external memory'
        put(data,'soul/identity','identity');put(data,'memory/history','history');put(data,'telegram/account.session','session')
        put(external,'agent.py','stock');put(external,'self.py','authored')
        def git(*args):return subprocess.check_output(['git','-C',str(external),*args],stderr=subprocess.STDOUT)
        git('init');git('add','.');git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','base')
        put(external,'self.py','staged');git('add','self.py');put(external,'self.py','unstaged');put(external,'untracked','untracked')
        put(old,'helene.json',json.dumps({'tree':str(data),'code':str(external),'runner':'','backup':{'dir':str(base/'old backup')}}))
        put(new,'python-app.json','{"target":"linux-x86_64","delivery":"portable","version":"2.1.0"}')
        put(new,'tree/agent.py','stock');put(new,'tree/self.py','authored')
        return old,new,external,data,git

    def test_complete_external_profile_and_dirty_git_are_independent_and_movable(self):
        with tempfile.TemporaryDirectory() as d:
            old,new,code,data,git=self.fixture(Path(d))
            head=git('rev-parse','HEAD');index=(code/'.git/index').read_bytes()
            note=migration.prepare(old,new,new/'profile',independent=True,preserve_voice=False)
            cfg=json.loads((new/'helene.json').read_text())
            self.assertFalse(Path(cfg['tree']).is_absolute());self.assertFalse(Path(cfg['code']).is_absolute())
            self.assertEqual((new/cfg['tree']/'telegram/account.session').read_text(),'session')
            selected=new/cfg['code']
            self.assertEqual((selected/'.git/index').read_bytes(),index)
            self.assertEqual(subprocess.check_output(['git','-C',str(selected),'rev-parse','HEAD']),head)
            self.assertEqual((selected/'self.py').read_text(),'unstaged');self.assertEqual((selected/'untracked').read_text(),'untracked')
            old.rename(old.with_name('retired-profile'));code.rename(code.with_name('retired-code'));data.rename(data.with_name('retired-memory'))
            moved=new.with_name('Moved portable');new.rename(moved)
            self.assertEqual((moved/cfg['tree']/'memory/history').read_text(),'history')
            self.assertEqual((moved/cfg['code']/'agent.py').read_text(),'stock')
            self.assertTrue(all(Path(p).is_relative_to(new/'profile') for p in note['data_paths']))

    def test_portable_update_then_rollback_keeps_original_data_and_working_folder(self):
        with tempfile.TemporaryDirectory() as d:
            old,new,code,data,git=self.fixture(Path(d))
            put(old,'shipped-code.json',json.dumps({'files':{'agent.py':digest(code/'agent.py'),'self.py':digest(code/'self.py')}}))
            # The authored file has no incoming difference from its recorded baseline.
            put(new,'tree/self.py','unstaged');put(new,'tree/agent.py','incoming')
            before=(old/'helene.json').read_bytes()
            note,recovery=updater.install(old,new)
            cfg=json.loads((old/'helene.json').read_text())
            self.assertEqual((old/cfg['code']/'agent.py').read_text(),'incoming')
            self.assertEqual((old/cfg['tree']/'memory/history').read_text(),'history')
            self.assertTrue(all(Path(p).is_relative_to(old/'profile') for p in note['data_paths']))
            self.assertEqual(Path(note['new_root']),old)
            updater.rollback(old,recovery)
            self.assertEqual((old/'helene.json').read_bytes(),before)
            self.assertEqual((data/'memory/history').read_text(),'history')
            self.assertEqual((code/'agent.py').read_text(),'stock')

    def test_package_removal_requires_new_app_proof_and_detached_profile(self):
        with tempfile.TemporaryDirectory() as d:
            old,new,code,data,git=self.fixture(Path(d))
            note=migration.prepare(old,new,new/'profile',independent=True,preserve_voice=False)
            note['installed_package']={'name':'helene','manager':'deb'};migration.write_note(new,note)
            with patch.object(packages,'privileged') as remove:
                with self.assertRaisesRegex(RuntimeError,'not confirmed'):packages.retire(new)
                remove.assert_not_called()
            note.update(state='new_app_started',startup_proof={'test':True},data_paths=[str(data)]);migration.write_note(new,note)
            with self.assertRaisesRegex(RuntimeError,'outside'):packages.retire(new)

    def test_removed_package_recovery_is_idempotent_and_elevation_cancel_is_retryable(self):
        with tempfile.TemporaryDirectory() as d:
            old,new,code,data,git=self.fixture(Path(d))
            note=migration.prepare(old,new,new/'profile',independent=True,preserve_voice=False)
            note.update(installed_package={'name':'helene','manager':'deb'},state='new_app_started',startup_proof={'test':True});migration.write_note(new,note)
            with patch.object(packages,'still_installed',return_value=True),patch.object(packages.pwd,'getpwall',return_value=[]),patch.object(packages,'privileged',side_effect=RuntimeError('cancelled')):
                result=packages.retire(new)
            self.assertEqual(result['state'],'retirement_incomplete');self.assertIn('cancelled',result['retirement_error'])
            with patch.object(packages,'still_installed',return_value=False),patch.object(packages,'privileged') as remove:
                result=packages.retire(new);again=packages.retire(new);remove.assert_not_called()
            self.assertTrue(result['package_removed']);self.assertEqual(again,result)

    def test_installed_package_without_user_profile_waits_for_fresh_startup(self):
        with tempfile.TemporaryDirectory() as d:
            new=Path(d)/'fresh';put(new,'helene.json','{"setup_complete":false,"tree":"data","code":"tree"}')
            put(new,'tree/agent.py','stock')
            package={'name':'helene-python','manager':'deb','program':'/usr/lib/helene-python/seed','profile':None}
            with patch.object(packages,'still_installed',return_value=True),patch('localharness.python_platform.require_runtime'):
                note=packages.prepare(package,new)
            self.assertTrue(note['no_prior_profile']);self.assertEqual(note['state'],'prepared')
            with patch.object(packages,'privileged') as remove:
                with self.assertRaisesRegex(RuntimeError,'not confirmed'):packages.retire(new)
                remove.assert_not_called()
            note.update(state='new_app_started',startup_proof={'tree':str(new/'data'),'channel_pid':1});migration.write_note(new,note)
            with patch.object(packages,'still_installed',return_value=False):result=packages.retire(new)
            self.assertEqual(result['state'],'completed')

if __name__=='__main__':unittest.main(verbosity=2)
