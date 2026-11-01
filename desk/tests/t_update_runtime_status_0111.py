"""Editing admission is fast; runtime corruption still fails the actual gates."""
from pathlib import Path
import sys,threading,unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
import t_app_source_update_3110 as fixtures
from localharness import update_composition as composition
from localharness.package_notice import read,write

class RuntimeStatus(unittest.TestCase):
 def setUp(self):
  self.fx=fixtures.AppSourceUpdate();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
  self.fx.prepare_pair();self.directory=self.fx.directory
 def test_editing_identity_matches_full_identity_without_rehashing_runtime(self):
  full=composition.signature(self.directory);admission=composition.admit_runtime(self.directory)
  original=composition._hash;reads=[]
  def measured(path):
   if Path(path).is_relative_to(self.fx.new/'runtime'):reads.append(str(path))
   return original(path)
  with patch.object(composition,'_hash',side_effect=measured),composition.editing_runtime(admission):
   self.assertEqual(composition.signature(self.directory),full)
   self.assertEqual(composition.signature(self.directory),full)
  self.assertEqual(reads,[])
 def test_corruption_during_editing_is_not_a_runtime_approval(self):
  admission=composition.admit_runtime(self.directory);before=composition.signature(self.directory)
  (self.fx.new/'runtime/python.exe').write_bytes(b'changed runtime')
  with composition.editing_runtime(admission):self.assertEqual(composition.signature(self.directory),before)
  with self.assertRaisesRegex(ValueError,'runtime changed'):composition.signature(self.directory)
 def test_added_runtime_file_is_rejected_by_the_actual_gate(self):
  admission=composition.admit_runtime(self.directory)
  (self.fx.new/'runtime/undeclared.dll').write_bytes(b'not in passport')
  with composition.editing_runtime(admission):composition.signature(self.directory)
  with self.assertRaisesRegex(ValueError,'undeclared'):composition.signature(self.directory)
 def test_forged_cached_runtime_declaration_is_refused(self):
  admission=composition.admit_runtime(self.directory);target=self.directory/'composition.json'
  value=read(target);value['package_files']['runtime/python.exe']='0'*64;write(target,value)
  with composition.editing_runtime(admission):
   with self.assertRaisesRegex(ValueError,'another immutable input'):composition.signature(self.directory)
  with self.assertRaisesRegex(ValueError,'passport'):composition.signature(self.directory)
 def test_admission_does_not_leak_into_a_later_validation(self):
  admission=composition.admit_runtime(self.directory)
  with self.assertRaises(RuntimeError),composition.editing_runtime(admission):raise RuntimeError('fixture interruption')
  with patch.object(composition,'_runtime_inventory',side_effect=RuntimeError('actual gate entered')):
   with self.assertRaisesRegex(RuntimeError,'actual gate entered'):composition.signature(self.directory)
 def test_actual_runtime_readers_preserve_cooperative_cancellation(self):
  from localharness import validation_read
  caller=threading.get_ident();observed=[]
  def cancelled_in_reader():
   if threading.get_ident()!=caller:
    observed.append(True)
    raise validation_read.ReadCancelled()
  with validation_read.scope(cancelled_in_reader):
   with self.assertRaises(validation_read.ReadCancelled):composition.signature(self.directory)
  self.assertTrue(observed)
  self.assertIsNone(validation_read.current())

if __name__=='__main__':unittest.main()
