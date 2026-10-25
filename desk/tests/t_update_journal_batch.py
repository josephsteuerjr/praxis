"""Large runtime overlays keep one complete pre-effect recovery intent."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import python_source_updater as u

class Journal(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  root=Path(self.tmp.name);self.source=root/'source';self.target=root/'target';self.recovery=root/'recovery'
  self.source.mkdir();self.target.mkdir()
  for i in range(40):
   name=f'{i:03}.txt';(self.source/name).write_text('NEW')
   if i<30:(self.target/name).write_text('OLD')
 def unchanged(self):
  self.assertEqual({p.name:p.read_text() for p in self.target.iterdir()},{f'{i:03}.txt':'OLD' for i in range(30)})
 def test_complete_preimages_and_intent_precede_first_effect_and_map_writes_are_constant(self):
  real_replace=Path.replace;real_write=Path.write_text;records=[];effects=[]
  def write(path,*a,**kw):
   if path.name=='update-files.tmp':records.append(len(json.loads(a[0])))
   return real_write(path,*a,**kw)
  def replace(path,dest):
   if path.name.endswith('.update-part'):
    rows=json.loads((self.recovery/'update-files.json').read_text());self.assertEqual(len(rows),40)
    for i in range(30):self.assertEqual((self.recovery/f'{i:03}.txt').read_text(),'OLD')
    effects.append(str(dest))
   return real_replace(path,dest)
  with patch.object(Path,'write_text',write),patch.object(Path,'replace',replace):u.apply_files(self.source,self.target,self.recovery)
  self.assertEqual(records,[40,40]);self.assertEqual(len(effects),40)
  self.assertTrue(all(p.read_text()=='NEW' for p in self.target.iterdir()))
  u.rollback(self.target,self.recovery);self.unchanged()
 def test_second_replacement_failure_restores_changed_and_not_yet_changed_preimages(self):
  real=Path.replace;count=0
  def replace(path,dest):
   nonlocal count
   if path.name.endswith('.update-part'):
    count+=1
    if count==2:raise OSError('controlled write failure')
   return real(path,dest)
  with patch.object(Path,'replace',replace),self.assertRaisesRegex(OSError,'controlled'):u.apply_files(self.source,self.target,self.recovery)
  self.unchanged();self.assertEqual(len(json.loads((self.recovery/'update-files.json').read_text())),40)
 def test_full_intent_recovery_with_zero_or_partial_effects(self):
  for prefix in (0,31):
   with self.subTest(prefix=prefix):
    recovery=self.recovery/str(prefix);recovery.mkdir(parents=True)
    rows=[]
    for i in range(40):
     name=f'{i:03}.txt';exists=(self.target/name).exists()
     if exists:(recovery/name).write_bytes((self.target/name).read_bytes())
     import hashlib
     incoming=hashlib.sha256((self.source/name).read_bytes()).hexdigest()
     rows.append({'path':name,'existed':exists,'incoming':incoming})
     if i<prefix:(self.target/name).write_bytes((self.source/name).read_bytes())
    (recovery/'update-files.json').write_text(json.dumps(rows))
    u.rollback(self.target,recovery);self.unchanged()
 def test_preimage_failure_publishes_no_program_change(self):
  real=u.shutil.copy2;count=0
  def copy(source,dest,*a,**kw):
   nonlocal count
   if Path(source).parent==self.target:
    count+=1
    if count==2:raise OSError('preimage refused')
   return real(source,dest,*a,**kw)
  with patch.object(u.shutil,'copy2',copy),self.assertRaisesRegex(OSError,'preimage refused'):u.apply_files(self.source,self.target,self.recovery)
  self.unchanged()
 def test_failed_intent_publishes_no_program_change(self):
  real=Path.write_text
  def write(path,*a,**kw):
   if path.name=='update-files.tmp':raise OSError('intent refused')
   return real(path,*a,**kw)
  with patch.object(Path,'write_text',write),self.assertRaisesRegex(OSError,'intent refused'):u.apply_files(self.source,self.target,self.recovery)
  self.unchanged()

if __name__=='__main__':unittest.main(verbosity=2)
