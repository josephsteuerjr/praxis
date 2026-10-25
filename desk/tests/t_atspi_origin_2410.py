"""The observed GTK4 zero-origin case never authorizes an ambiguous root."""
from pathlib import Path
import importlib.util,unittest
p=Path(__file__).resolve().parents[2]/'helene/core/body/crates/praxis-body/src/linux_desktop.py'
s=importlib.util.spec_from_file_location('desktop',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Binding(unittest.TestCase):
 bounds=dict(x=165,y=108,width=310,height=263)
 def test_observed_zero_origin_same_size(self):
  self.assertEqual(m.accessibility_window_binding([(1,dict(x=0,y=0,width=310,height=263))],self.bounds),(1,True))
 def test_near_corner_zero_origin_stays_unverified(self):
  near={**self.bounds,'x':20,'y':30}
  self.assertEqual(m.accessibility_window_binding([(1,dict(x=0,y=0,width=310,height=263))],near),(1,True))
 def test_screen_coordinates_stay_screen(self):
  self.assertEqual(m.accessibility_window_binding([(1,dict(self.bounds))],self.bounds),(1,False))
 def test_duplicates_and_size_mismatch_remain_refused(self):
  zero=dict(x=0,y=0,width=310,height=263)
  for rows in ([],[(1,zero),(2,zero)],[(1,{**zero,'width':311})]):
   with self.assertRaisesRegex(RuntimeError,'absent or ambiguous'):m.accessibility_window_binding(rows,self.bounds)
 def test_x11_peers_observe_only_identity_and_detect_duplicates(self):
  from unittest.mock import patch
  with patch.object(m,'command',return_value='0x10, 0x20'),patch.object(m,'pid_of',return_value=7),patch.object(m,'window_title',return_value='Same'),patch.object(m,'geometry',return_value=self.bounds):
   self.assertEqual(m.equivalent_x11_windows(7,'Same',self.bounds),[16,32])
if __name__=='__main__':unittest.main(verbosity=2)
