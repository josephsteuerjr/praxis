"""A real partially-initialized stdlib import must not race desktop dataclasses."""
from pathlib import Path
import subprocess
import sys
import unittest

DESK=Path(__file__).resolve().parents[1]
SCRIPT=r'''
import sys,threading,time,importlib,importlib.machinery,importlib.util
sys.path.insert(0,sys.argv[1])
assert 'typing' not in sys.modules
entered=threading.Event();release=threading.Event();errors=[]
original=importlib.machinery.PathFinder.find_spec('typing')
loader=original.loader
class Paused:
 def create_module(self,spec):return loader.create_module(spec)
 def exec_module(self,module):
  entered.set()
  if not release.wait(10):raise TimeoutError('fixture import gate')
  loader.exec_module(module)
class Finder:
 def find_spec(self,name,path=None,target=None):
  if name=='typing':original.loader=Paused();return original
sys.meta_path.insert(0,Finder())
def load(name):
 try:importlib.import_module(name)
 except BaseException as error:errors.append(type(error).__name__+': '+str(error))
typing_thread=threading.Thread(target=load,args=('typing',));typing_thread.start()
assert entered.wait(5)
desktop_thread=threading.Thread(target=load,args=('localharness.python_controller',));desktop_thread.start()
time.sleep(.3);waited=desktop_thread.is_alive()
release.set();typing_thread.join(10);desktop_thread.join(10)
assert not typing_thread.is_alive() and not desktop_thread.is_alive()
assert waited and not errors,{'waited_for_complete_typing':waited,'errors':errors}
print('COLD_TYPING_IMPORT_SERIALIZED')
'''

class ControllerStartup(unittest.TestCase):
    def test_real_typing_initialization_finishes_before_desktop_dataclasses(self):
        done=subprocess.run([sys.executable,'-S','-c',SCRIPT,str(DESK)],capture_output=True,text=True,timeout=30)
        self.assertEqual(done.returncode,0,done.stdout+done.stderr)
        self.assertIn('COLD_TYPING_IMPORT_SERIALIZED',done.stdout)

if __name__=='__main__':unittest.main()
