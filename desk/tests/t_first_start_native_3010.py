"""Real Windows termination, respawning guardians and exclusive file handles.

Only own temporary paths and captured synthetic children; no owner installation.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import windows_first_start as native


@unittest.skipUnless(os.name=='nt','native Windows process/Restart Manager contracts')
class NativeFirstStart(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='helene-native-first-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.old=self.base/'Old Helene';self.old.mkdir()
        self.backend=native.Backend(self.base/'New Helene')
        # This suite owns child processes and files only. It has no authority
        # over the host SCM, task scheduler or autoruns. Hook planning/effects
        # have a separate hermetic suite with fake inventories.
        self.hooks=patch.object(native.Backend,'hooks',autospec=True)
        self.hooks.start();self.addCleanup(self.hooks.stop)
    def launch(self,script,env=None):
        path=self.base/('fixture-'+str(len(list(self.base.glob('fixture-*.py'))))+'.py')
        path.write_text(script,encoding='utf8')
        process=subprocess.Popen([sys.executable,'-B',str(path)],env={**os.environ,**(env or {})},
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def close():
            if process.poll() is None:process.terminate();process.wait(timeout=5)
        self.addCleanup(close);return process
    def wait(self,path,process):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if path.is_file():return
            if process.poll() is not None:self.fail('fixture exited before opening its native resource')
            time.sleep(.02)
        self.fail('fixture readiness not confirmed')
    def test_system_python_guardian_stops_before_its_respawning_bridge_and_body(self):
        self.guardian(data_only=False)
    def test_orphan_data_owner_stops_even_without_a_surviving_programme_folder(self):
        self.guardian(data_only=True)
    def guardian(self,*,data_only):
        import psutil
        app=self.old/'app/localharness';app.mkdir(parents=True)
        script=app/'session_host.py';ready=self.base/'guardian-ready.json'
        script.write_text('''import json,os,subprocess,sys,time
from pathlib import Path
children=[]
def child():return subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(60)'])
children=[child(),child()]
Path(os.environ['FIRST_READY']).write_text(json.dumps({'guardian':os.getpid(),'organs':[p.pid for p in children]}))
while True:
 for i,p in enumerate(children):
  if p.poll() is not None:children[i]=child()
 time.sleep(.02)
''',encoding='utf8')
        process=subprocess.Popen([sys.executable,'-B',str(script)],env={**os.environ,'FIRST_READY':str(ready)},
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        observed={}
        try:
            self.wait(ready,process);identity=json.loads(ready.read_text())
            observed={pid:psutil.Process(pid).create_time() for pid in [identity['guardian'],*identity['organs']]}
            roots=[self.old];protected=[]
            if data_only:
                data=self.base/'agent-data';control=data/'memory/.control';control.mkdir(parents=True)
                (control/'desktop-session.json').write_text(json.dumps({'format':'helene-session-host.v1',
                    'program':str(self.base/'removed-programme'),'pid':process.pid,'birth':observed[process.pid],'tree':str(data)}))
                roots=[];protected=[data]
            rows=native.owned(roots,protected);self.assertEqual(rows[0]['pid'],process.pid)
            self.assertTrue(set(observed).issubset({row['pid'] for row in rows}))
            self.backend.stop(roots,protected);process.wait(timeout=5)
            self.assertEqual(native.owned(roots,protected),[])
            for pid,birth in observed.items():
                if psutil.pid_exists(pid):self.assertNotEqual(psutil.Process(pid).create_time(),birth)
        finally:
            for pid,birth in observed.items():
                try:native.terminate(pid,birth)
                except (OSError,RuntimeError):pass
            if process.poll() is None:process.terminate();process.wait(timeout=5)
    def test_restart_manager_finds_and_releases_an_actual_exclusive_handle(self):
        file=self.old/'held.bin';file.write_bytes(b'owned fixture');ready=self.base/'held-ready'
        process=self.launch('''import ctypes,os,time
from ctypes import wintypes
from pathlib import Path
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
k.CreateFileW.restype=wintypes.HANDLE
h=k.CreateFileW(os.environ['FIRST_FILE'],0x80000000,0,None,3,0x80,None)
if h==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
Path(os.environ['FIRST_READY']).write_text('held')
time.sleep(60)
''',{'FIRST_FILE':str(file),'FIRST_READY':str(ready)})
        self.wait(ready,process)
        with self.assertRaises(PermissionError):file.unlink()
        self.assertFalse(any(row['pid']==process.pid for row in native.owned([self.old])))
        self.assertIn(process.pid,[row['pid'] for row in native.restart_holders([file])])
        self.backend.release(file);process.wait(timeout=5);file.unlink();self.assertFalse(file.exists())
    def test_process_generation_guard_refuses_to_terminate_the_wrong_birth(self):
        import psutil
        process=self.launch('import time;time.sleep(60)');birth=psutil.Process(process.pid).create_time()
        with self.assertRaisesRegex(RuntimeError,'generation'):native.terminate(process.pid,birth+1)
        self.assertIsNone(process.poll())
    def test_kernel_single_instance_has_no_lock_files_and_excludes_another_thread(self):
        target=self.base/'data'
        def other():
            with self.assertRaisesRegex(RuntimeError,'другом окне'):
                with native.Backend(self.base/'Other').exclusive(target):pass
        before=set(self.base.iterdir())
        with self.backend.exclusive(target):
            with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(other).result(timeout=5)
        self.assertEqual(set(self.base.iterdir()),before)


if __name__=='__main__':unittest.main()
