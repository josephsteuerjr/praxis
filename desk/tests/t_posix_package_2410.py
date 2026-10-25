"""Focused offline POSIX package/migration consumers; no live agent or network."""
from pathlib import Path
import sys,os,json,tempfile,unittest,zipfile,stat,subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import python_platform as platform, component_downloads as downloads
from localharness.python_updates import _extract
from localharness.python_source_updater import apply_files,rollback
from localharness.profile_paths import relocate_config,prepare_offline

@unittest.skipIf(os.name=='nt','Real POSIX modes/path mapping consumer')
class Contracts(unittest.TestCase):
 def test_targets(self):
  self.assertEqual(platform.asset_name('1.9.4','linux-x86_64'),'Helene-1.9.4-linux-x86_64.zip')
  self.assertEqual(platform.asset_name('1.9.4','windows-x86_64'),'Helene-1.9.4.zip')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'python-app.json').write_text('{"target":"windows-x86_64"}')
   with self.assertRaises(ValueError):platform.require_runtime(p)
 def test_modes_extract_apply_rollback(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);z=p/'in.zip';source=p/'source';target=p/'target';target.mkdir()
   (target/'run').write_text('same');(target/'run').chmod(0o644)
   with zipfile.ZipFile(z,'w') as out:
    item=zipfile.ZipInfo('Helene/run');item.create_system=3;item.external_attr=(stat.S_IFREG|0o755)<<16;out.writestr(item,'same')
   _extract(z,source);self.assertEqual((source/'Helene/run').stat().st_mode&0o777,0o755)
   apply_files(source/'Helene',target,p/'recovery');self.assertEqual((target/'run').stat().st_mode&0o777,0o755)
   rollback(target,p/'recovery');self.assertEqual((target/'run').stat().st_mode&0o777,0o644)
 def test_unavailable_not_windows_download(self):
  m={'format':downloads.FORMAT,'extensions':{n:[] for n in downloads.EXTENSIONS},'artifacts':{},'unavailable':{n:'No qualified target payload' for n in downloads.EXTENSIONS}}
  downloads.validate(m)
  with self.assertRaises(ValueError):downloads.select(m,['xtts'])
  m['unavailable'].pop('xtts')
  with self.assertRaises(ValueError):downloads.validate(m)
 def test_default_voice_status_and_target_map(self):
  from localharness import voice
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'app').mkdir()
   m={'format':downloads.FORMAT,'target':platform.runtime_target(),'extensions':{n:[] for n in downloads.EXTENSIONS},'artifacts':{},'unavailable':{n:'No qualified target payload' for n in downloads.EXTENSIONS}}
   (root/'app/component-downloads.json').write_text(json.dumps(m));(root/'python-app.json').write_text(json.dumps({'version':'1.9.4','target':platform.runtime_target(),'extensions':{'whisper':{},'xtts':{}}}))
   with patch.object(voice,'install_root',return_value=root):
    record=voice.state(root,{})
    self.assertFalse(voice._engine_missing('engine','fallback')['downloadable'])
    self.assertFalse(voice.speech_library()['downloadable'])
    self.assertIsInstance(record,dict)
   legacy=dict(m);legacy.pop('target')
   with self.assertRaises(ValueError):downloads.for_target(legacy,platform.runtime_target())
   self.assertEqual(downloads.for_target(m,platform.runtime_target()),m)
 def test_explicit_windows_path_map(self):
  v={'tree':r'C:\Users\A\Data','code':r'C:\Program Files\Helene\tree','prompt':r'keep C:\words'}
  with self.assertRaises(ValueError):relocate_config(v,{})
  actual=relocate_config(v,{r'C:\Users\A\Data':'/copy/data',r'C:\Program Files\Helene':'/copy/program'})
  self.assertEqual(actual['tree'],'/copy/data');self.assertEqual(actual['code'],'/copy/program/tree');self.assertEqual(actual['prompt'],v['prompt'])
 def test_offline_profile_source_git_preferences_backup(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);old=p/'offline';new=p/'new';old.mkdir();new.mkdir();tree=old/'external/data';code=old/'code'
   (tree/'soul').mkdir(parents=True);(tree/'memory').mkdir();code.mkdir()
   (tree/'soul/identity.md').write_text('Jarvis authored identity');(tree/'memory/log.txt').write_text('history');(code/'agent.py').write_text('ORIGINAL=1')
   def git(*a):return subprocess.check_output(['git','-C',str(code),*a],stderr=subprocess.STDOUT)
   git('init');git('add','agent.py');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture')
   expected=git('rev-parse','HEAD');(code/'agent.py').write_text('SELF_EDIT=2');(code/'new.py').write_text('untracked')
   cfg={'tree':r'C:\Data','code':r'C:\App\code','python':r'C:\App\runtime\python.exe','app':r'C:\App\app\deskapp.py','runner':r'C:\App\app\localharness\runner.py','model':{'key':'synthetic-key'},'installed':{'service':True}}
   raw=json.dumps(cfg);(old/'helene.json').write_text(raw);(old/'window-preferences.json').write_text('{"scale":0.7}')
   (new/'helene.json').write_text('{"setup_complete":false}');(new/'python-app.json').write_text(json.dumps({'target':platform.runtime_target()}))
   aliases={r'C:\Data':str(tree),r'C:\App':str(old)}
   note=prepare_offline(old,new,p/'new-data',aliases)
   now=json.loads((new/'helene.json').read_text());copied=Path(now['code']);moved=Path(now['tree'])
   self.assertEqual((old/'helene.json').read_text(),raw);self.assertFalse(now['installed']['service'])
   self.assertEqual((copied/'agent.py').read_text(),'SELF_EDIT=2');self.assertEqual((copied/'new.py').read_text(),'untracked')
   self.assertEqual(subprocess.check_output(['git','-C',str(copied),'rev-parse','HEAD']),expected)
   self.assertEqual((moved/'soul/identity.md').read_text(),'Jarvis authored identity')
   self.assertEqual((new/'window-preferences.json').read_text(),'{"scale":0.7}')
   self.assertEqual(now['model']['key'],'synthetic-key');self.assertTrue(now['python'].endswith('runtime/bin/python3'))
   with zipfile.ZipFile(note['backup']) as z:self.assertEqual(z.read('data/soul/identity.md'),b'Jarvis authored identity')
   self.assertEqual(note['state'],'prepared');self.assertNotIn('startup_proof',note)
 def test_configured_backup_is_rehomed_and_actual_snapshot_leaves_offline_source_untouched(self):
  from localharness import essential_backup
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);old=p/'offline';new=p/'new';home=p/'new-data';(old/'data/memory').mkdir(parents=True);(old/'code').mkdir();new.mkdir()
   (old/'data/memory/one.txt').write_text('history');(old/'code/agent.py').write_text('SELF_EDIT=1')
   (old/'helene.json').write_text(json.dumps({'tree':r'C:\Data','code':r'C:\App\code','backup':{'dir':r'C:\App\backups'},'runner':''}))
   (new/'helene.json').write_text('{"setup_complete":false}');(new/'python-app.json').write_text(json.dumps({'target':platform.runtime_target()}))
   prepare_offline(old,new,home,{r'C:\Data':str(old/'data'),r'C:\App':str(old)})
   dest=essential_backup.destination(new)
   self.assertTrue(dest.is_relative_to(home));self.assertFalse(dest.is_relative_to(old))
   result=essential_backup.snapshot(new,dest,'fixture')
   self.assertTrue(Path(result['path']).is_file());self.assertFalse((old/'backups').exists())

 def test_missing_code_uses_real_default_and_refuses_outside_offline_copy(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);old=p/'offline';new=p/'new';(old/'data').mkdir(parents=True);(old/'tree').mkdir();new.mkdir()
   (old/'helene.json').write_text('{"tree":"data"}');(new/'python-app.json').write_text(json.dumps({'target':platform.runtime_target()}))
   with self.assertRaisesRegex(ValueError,'effective data/code'):prepare_offline(old,new,p/'home',{})
   self.assertFalse((new/'python-migration.json').exists())

 def test_failed_offline_copy_and_config_landing_resume_named_paths(self):
  from unittest.mock import patch
  from localharness import app_migration
  for fail_landing in (False,True):
   with self.subTest(fail_landing=fail_landing),tempfile.TemporaryDirectory() as d:
    p=Path(d);old=p/'offline';new=p/'new';home=p/'new-data';(old/'data').mkdir(parents=True);(old/'code').mkdir();new.mkdir()
    (old/'data/history').write_text('retained');(old/'code/agent.py').write_text('SELF_EDIT=1');(old/'helene.json').write_text('{"tree":"data","code":"code","runner":""}')
    (new/'helene.json').write_text('{"setup_complete":false}');(new/'python-app.json').write_text(json.dumps({'target':platform.runtime_target()}))
    original=app_migration.shutil.copyfile
    def copy(src,dst,*a,**kw):
     if Path(dst)==new/'helene.json':raise OSError('landing failed')
     return original(src,dst,*a,**kw)
    context=(patch.object(app_migration.shutil,'copyfile',side_effect=copy) if fail_landing else patch.object(app_migration,'preserve_source',side_effect=OSError('copy failed')))
    with context:
     with self.assertRaises(OSError):prepare_offline(old,new,home,{})
    first=json.loads((new/'python-migration.json').read_text());self.assertEqual(first['state'],'preserving')
    result=prepare_offline(old,new,home,{},resume=True)
    self.assertEqual(result['preserved_roots'],first['preserved_roots']);self.assertEqual(result['state'],'prepared')
    self.assertEqual((Path(result['data_paths'][0])/'history').read_text(),'retained')
    self.assertTrue((old/'data/history').is_file())

if __name__=='__main__':unittest.main(verbosity=2)

