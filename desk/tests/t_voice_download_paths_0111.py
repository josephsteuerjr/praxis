"""Both settings paths share qualified payloads and publish terminal states."""
from pathlib import Path
import json,os,sys,tempfile,threading,types,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import voice,component_downloads,xtts_install,component_install_job,component_store

class Downloads(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);base=Path(self.tmp.name)
  self.root=base/'Программа';self.root.mkdir();self.tree=base/'Profile'
  self.enterContext(patch.object(component_store,'root',return_value=base/'store'))
  self.enterContext(patch.object(voice,'install_root',return_value=self.root))
 def prepared_bundle(self):
  p=self.root/'components/xtts/xtts-bundle.json';p.parent.mkdir(parents=True);p.write_text('{}')
  python=xtts_install.runtime_for(self.root)/('python.exe' if os.name=='nt' else 'bin/python3')
  python.parent.mkdir(parents=True,exist_ok=True);python.write_bytes(b'not executed')
 def progress(self):return json.loads((self.tree/'memory/.state'/voice.SPEECH_PROGRESS).read_text())
 def test_voice_page_prepares_the_same_optional_component_before_install(self):
  def prepare(program,extensions,progress,*,tree):
   self.assertEqual(program,self.root);self.assertEqual(extensions,['xtts']);self.assertEqual(tree,self.tree)
   progress({'stage':'downloading','bytes':3,'total_bytes':10});return {'state':'installed'}
  with patch.object(component_downloads,'install',side_effect=prepare) as fetch,patch.object(xtts_install,'install',side_effect=AssertionError('Do not install and hash the same large payload twice')):
   voice.fetch_voice(self.tree,'xtts')
  fetch.assert_called_once();self.assertEqual(self.progress()['state'],'done')
 def test_prepared_component_is_reused_without_a_network_request(self):
  self.prepared_bundle()
  with (patch.object(component_downloads,'install',side_effect=AssertionError('Cached component must not be fetched')),
        patch.object(xtts_install,'install',return_value={'state':'done'}) as install):
   voice.fetch_voice(self.tree,'xtts')
  install.assert_called_once_with(self.tree,program=self.root);self.assertEqual(self.progress()['state'],'done')
 def test_unexpected_installer_failure_cannot_leave_running_progress(self):
  self.prepared_bundle()
  with patch.object(xtts_install,'install',side_effect=RuntimeError('fixture installer stopped')):
   with self.assertRaises(RuntimeError):voice.fetch_voice(self.tree,'xtts')
  self.assertEqual(self.progress()['state'],'failed');self.assertIn('fixture installer stopped',self.progress()['error'])
 def test_model_watcher_cannot_overwrite_a_terminal_download_error(self):
  caller=threading.get_ident();entered=threading.Event();release=threading.Event()
  original=voice._write
  def size(_path):
   if threading.get_ident()!=caller:
    entered.set();self.assertTrue(release.wait(10))
   return 0
  def write(path,value):
   original(path,value)
   if value.get('state')=='failed':release.set()
  def download(*args,**kwargs):
   self.assertTrue(entered.wait(10));raise OSError('fixture network failure')
  parent=types.ModuleType('faster_whisper');parent.__path__=[]
  child=types.ModuleType('faster_whisper.utils');child.download_model=download
  with (patch.dict(sys.modules,{'faster_whisper':parent,'faster_whisper.utils':child}),
        patch.object(voice,'library',return_value={'present':True}),patch.object(voice,'dir_size',side_effect=size),
        patch.object(voice,'_write',side_effect=write)):
   with self.assertRaises(SystemExit):voice.fetch(self.tree,voice.DEFAULT_MODEL,quiet=True)
  value=json.loads((self.tree/'memory/.state'/voice.PROGRESS).read_text())
  self.assertEqual(value['state'],'failed');self.assertIn('fixture network failure',value['note'])
 def test_dead_worker_is_reported_interrupted_immediately(self):
  target=self.tree/'memory/.state'/voice.PROGRESS
  voice._write(target,{'state':'running','note':'old download','worker':{'pid':123,'birth':1}})
  with patch.object(component_install_job,'alive',return_value=False):
   self.assertEqual(voice.download_progress(target)['state'],'failed')
  with patch.object(component_install_job,'alive',return_value=True):
   result=voice.download_progress(target);self.assertEqual(result['state'],'running');self.assertTrue(result['active'])

if __name__=='__main__':unittest.main()
