"""Installation receipts retain failures, reject duplicates, and keep Job custody."""
from pathlib import Path
from types import SimpleNamespace
import json,sys,tempfile,threading,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import component_install_job as jobs,component_downloads as downloads,update_process

class ComponentJobs(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)/'Программа';self.root.mkdir();self.tree=Path(self.tmp.name)/'data'
 def row(self):return jobs.read(jobs.path(self.root,'whisper'))
 def test_success_and_progress_are_shared_by_both_settings_paths(self):
  def install(root,names,report,*,tree):
   self.assertEqual((root,names,tree),(self.root,['whisper'],self.tree))
   report({'stage':'unpacking','files':2,'total_files':3})
   self.assertEqual(self.row()['files'],2);return {'state':'installed'}
  with patch.object(downloads,'install',side_effect=install):jobs.inline(self.root,'whisper',self.tree)
  self.assertEqual(self.row()['state'],'completed');self.assertFalse(jobs.status(self.root,'whisper')['active'])
 def test_failed_install_is_terminal_and_explicit_retry_gets_a_new_identity(self):
  with patch.object(downloads,'install',side_effect=OSError('fixture download stopped')):
   with self.assertRaises(OSError):jobs.inline(self.root,'whisper',self.tree)
  row=self.row();self.assertEqual(row['state'],'failed');self.assertIn('download stopped',row['error'])
  retry=jobs.claim(self.root,'whisper',self.tree);self.assertNotEqual(retry['id'],row['id'])
 def test_duplicate_download_from_another_window_or_voice_path_is_refused(self):
  admitted=jobs.claim(self.root,'whisper',self.tree)
  with self.assertRaisesRegex(RuntimeError,'устанавливается'):jobs.inline(self.root,'whisper',self.tree)
  self.assertEqual(self.row()['id'],admitted['id'])
 def test_absent_worker_is_interrupted_and_retryable_but_unknown_custody_is_not(self):
  original=jobs.claim(self.root,'whisper',self.tree)
  with patch.object(jobs,'alive',return_value=False):
   self.assertEqual(jobs.status(self.root,'whisper')['state'],'interrupted')
   newer=jobs.claim(self.root,'whisper',self.tree)
  self.assertNotEqual(original['id'],newer['id'])
  with patch.object(jobs,'alive',return_value=None):
   with self.assertRaisesRegex(RuntimeError,'устанавливается'):jobs.claim(self.root,'whisper',self.tree)
 def test_late_callback_cannot_overwrite_a_new_attempt(self):
  original=jobs.claim(self.root,'whisper',self.tree)
  jobs.update(self.root,'whisper',original['id'],state='failed')
  newer=jobs.claim(self.root,'whisper',self.tree)
  with self.assertRaisesRegex(RuntimeError,'replaced'):jobs.update(self.root,'whisper',original['id'],state='completed')
  self.assertEqual(self.row()['id'],newer['id'])
 def owner(self):
  child=Mock(pid=123);child.handle=object();child.poll.return_value=None
  return SimpleNamespace(root=self.root,lock=threading.RLock(),exiting=False,stop=threading.Event(),
   current=lambda:SimpleNamespace(tree=self.tree),background=Mock(return_value=child)),child
 def start_context(self):
  self.enterContext(patch.object(downloads,'load',return_value={}))
  self.enterContext(patch.object(downloads,'select',return_value=[]))
  self.enterContext(patch.object(update_process,'interpreter_for',return_value=Path(sys.executable)))
 def test_start_is_async_owned_and_cannot_spawn_another_copy(self):
  self.start_context();owner,child=self.owner();own=jobs.identity()
  with patch.object(jobs,'identity',return_value=own),patch.object(downloads,'install',side_effect=AssertionError('Never extract on the request thread')):
   first=jobs.start(owner,'whisper');second=jobs.start(owner,'whisper')
  self.assertTrue(first['active']);self.assertEqual(first['id'],second['id'])
  owner.background.assert_called_once();self.assertIs(owner.component_jobs['whisper'],child)
  self.assertEqual(owner.background.call_args.args[1],self.tree/'component-download.log')
  owner.stop.set()
  with self.assertRaisesRegex(RuntimeError,'Exit'):jobs.start(owner,'xtts')
 def test_failed_worker_pin_stops_the_admitted_child_before_a_retry(self):
  self.start_context();owner,child=self.owner();own=jobs.identity()
  with patch.object(jobs,'identity',side_effect=[own,RuntimeError('fixture child vanished')]):
   with self.assertRaisesRegex(RuntimeError,'подтверждения'):jobs.start(owner,'whisper')
  child.stop.assert_called_once();self.assertEqual(self.row()['state'],'failed')

if __name__=='__main__':unittest.main()
