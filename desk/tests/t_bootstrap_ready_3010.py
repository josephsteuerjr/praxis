"""Real Windows kernel acknowledgements and actual compiled bootstrap process."""
from pathlib import Path
import ctypes,json,os,subprocess,sys,tempfile,time,unittest,uuid
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'installer')]
from localharness import bootstrap_ready as ready

@unittest.skipUnless(sys.platform=='win32','Windows native entry and kernel events')
class BootstrapReady(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ctypes import wintypes
        cls.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        cls.kernel.CreateEventW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR];cls.kernel.CreateEventW.restype=wintypes.HANDLE
        cls.kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];cls.kernel.WaitForSingleObject.restype=wintypes.DWORD
        cls.kernel.CloseHandle.argtypes=[wintypes.HANDLE]

    def setUp(self):
        self.previous=ready._name;ready._name=None;self.addCleanup(setattr,ready,'_name',self.previous)

    def test_real_ready_event_is_one_shot_and_not_inherited(self):
        name='Local\\HeleneBootstrap-'+uuid.uuid4().hex
        event=self.kernel.CreateEventW(None,True,False,name);self.assertTrue(event)
        self.addCleanup(self.kernel.CloseHandle,event)
        with patch.dict(os.environ,HELENE_BOOTSTRAP_EVENT=name):
            ready.attach();self.assertNotIn('HELENE_BOOTSTRAP_EVENT',os.environ)
            self.assertEqual(self.kernel.WaitForSingleObject(event,0),258)
            ready.signal();ready.signal()
        self.assertEqual(self.kernel.WaitForSingleObject(event,0),0)

    def test_failure_has_separate_event_and_invalid_names_cannot_signal(self):
        name='Local\\HeleneBootstrap-'+uuid.uuid4().hex
        event=self.kernel.CreateEventW(None,True,False,name+'-failed');self.assertTrue(event)
        self.addCleanup(self.kernel.CloseHandle,event)
        with patch.dict(os.environ,HELENE_BOOTSTRAP_EVENT=name):ready.signal(failed=True)
        self.assertEqual(self.kernel.WaitForSingleObject(event,0),0)
        with patch.dict(os.environ,HELENE_BOOTSTRAP_EVENT='Global\\another-app'):
            with self.assertRaisesRegex(ValueError,'identity'):ready.attach()

    def test_closed_launcher_does_not_break_late_application_start(self):
        with patch.dict(os.environ,HELENE_BOOTSTRAP_EVENT='Local\\HeleneBootstrap-'+uuid.uuid4().hex):ready.signal()
        self.assertIsNone(ready._name)

    def test_actual_bootstrap_waits_for_ready_and_returns_reported_failure(self):
        from windows_archive_entry import launcher
        with tempfile.TemporaryDirectory(prefix='helene-bootstrap-') as tmp:
            root=Path(tmp)/'Hélène новая';(root/'runtime').mkdir(parents=True)
            executable=launcher(root)
            fixture=root/'python_fixture.rs'
            fixture.write_text(r'''#![windows_subsystem = "windows"]
use std::{env,os::windows::ffi::OsStrExt};
#[link(name="kernel32")] extern "system" {fn OpenEventW(access:u32,inherit:i32,name:*const u16)->isize;fn SetEvent(handle:isize)->i32;fn CloseHandle(handle:isize)->i32;}
fn main(){std::thread::sleep(std::time::Duration::from_millis(150));let mut name=env::var("HELENE_BOOTSTRAP_EVENT").unwrap();if env::var("HELENE_FIXTURE_FAIL").is_ok(){name.push_str("-failed");}let wide:Vec<u16>=std::ffi::OsStr::new(&name).encode_wide().chain(Some(0)).collect();unsafe{let h=OpenEventW(2,0,wide.as_ptr());assert!(h!=0);assert!(SetEvent(h)!=0);CloseHandle(h);}}
''',encoding='utf-8')
            result=subprocess.run(['rustc','--edition=2021','--crate-name','fixture','-C','strip=symbols',str(fixture),'-o',str(root/'runtime/pythonw.exe')],capture_output=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            for fail in (False,True):
                environment=dict(os.environ);environment.pop('HELENE_FIXTURE_FAIL',None)
                if fail:environment['HELENE_FIXTURE_FAIL']='1'
                started=time.monotonic();result=subprocess.run([str(executable)],env=environment,capture_output=True,timeout=8)
                self.assertEqual(result.returncode,int(fail))
                self.assertGreater(time.monotonic()-started,.12)
            receipt=json.loads((root/'helene-bootstrap.json').read_text())
            self.assertEqual(len(receipt['icon_sha256']),64)

if __name__=='__main__':unittest.main()
