"""Two real Settings writers, including a separate CLI process, must serialize CAS."""
import json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'desk'))
from deskd import agentcfg
from localharness import config_cli

class Transactions(unittest.TestCase):
    def test_exhausted_reader_refusal_preserves_settings_and_cleans_staging(self):
        from localharness import package_notice
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'helene.json';path.write_text('{"agent":{"name":"old"}}',encoding='utf8')
            before=path.read_bytes();seen=config_cli.mtime_ns(path)
            error=PermissionError(13,'controlled Windows refusal');error.winerror=5
            with patch.object(os,'replace',side_effect=error),patch.object(package_notice,'time') as clock:
                clock.monotonic.side_effect=(0.,10.)
                with self.assertRaises(PermissionError) as result:
                    config_cli.save(path,'{"agent":{"name":"new"}}',seen)
            self.assertIs(result.exception,error)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(config_cli.mtime_ns(path),seen)
            self.assertEqual(list(path.parent.glob('.tmp-helene-*')),[])

    @unittest.skipUnless(os.name=='nt','native Windows FILE_SHARE_DELETE semantics')
    def test_real_windows_reader_does_not_abort_a_settings_transaction(self):
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateFileW.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE)
        api.CreateFileW.restype=wintypes.HANDLE
        api.CloseHandle.argtypes=(wintypes.HANDLE,);api.CloseHandle.restype=wintypes.BOOL
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'helene.json';path.write_text('{"agent":{"name":"old"}}',encoding='utf8')
            seen=config_cli.mtime_ns(path)
            handle=api.CreateFileW(str(path),0x80000000,1|2,None,3,0x80,None)
            if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
            original=os.replace;collisions=[]
            def close_reader():
                nonlocal handle
                if handle is not None:
                    self.assertTrue(api.CloseHandle(handle));handle=None
            def replace(staged,target):
                try:return original(staged,target)
                except PermissionError as error:
                    if Path(target)==path:
                        collisions.append(error.winerror);close_reader()
                    raise
            try:
                with patch.object(os,'replace',side_effect=replace):
                    result=config_cli.save(path,'{"agent":{"name":"new"}}',seen)
            finally:close_reader()
            self.assertTrue(collisions)
            self.assertTrue(all(code in (5,32,33) for code in collisions))
            self.assertTrue(result['ok'])
            self.assertGreater(int(result['mtime_ns']),int(seen))
            self.assertEqual(json.loads(path.read_text(encoding='utf8'))['agent']['name'],'new')
            self.assertEqual(config_cli.save(path,'{"agent":{"name":"stale"}}',seen)['code'],'stale')
            self.assertEqual(list(path.parent.glob('.tmp-helene-*')),[])

    def test_equal_clock_ticks_never_restore_an_old_settings_receipt(self):
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'helene.json';path.write_text('{"agent":{"name":"old"}}',encoding='utf-8')
            original=path.stat().st_mtime_ns;seen=config_cli.mtime_ns(path)
            factory=config_cli.tempfile.NamedTemporaryFile
            @contextlib.contextmanager
            def same_tick(**kwargs):
                with factory(**kwargs) as output:yield output
                os.utime(output.name,ns=(original,original))
            with patch.object(config_cli.tempfile,'NamedTemporaryFile',side_effect=same_tick):
                first=config_cli.save(path,'{"agent":{"name":"one"}}',seen)
                second=config_cli.save(path,'{"agent":{"name":"two"}}',first['mtime_ns'])
            self.assertTrue(first['ok']);self.assertTrue(second['ok'])
            self.assertLess(int(seen),int(first['mtime_ns']));self.assertLess(int(first['mtime_ns']),int(second['mtime_ns']))
            stale=config_cli.save(path,'{"agent":{"name":"stale"}}',seen)
            self.assertEqual(stale['code'],'stale')
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['agent']['name'],'two')

    def test_channel_compare_blocks_real_cli_then_stale_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'helene.json'
            path.write_text(json.dumps({'agent':{'name':'old'},'tree':'KEEP','self_review':{'after_update':False}}),encoding='utf-8')
            seen=config_cli.mtime_ns(path)
            entered=threading.Event();release=threading.Event();answer=[]
            original=agentcfg._read_snapshot
            def held(p):
                value=original(p);entered.set()
                if not release.wait(10):raise AssertionError('barrier timed out')
                return value
            with patch.object(agentcfg.readers,'config_path',return_value=path),patch.object(agentcfg,'_read_snapshot',side_effect=held):
                thread=threading.Thread(target=lambda:answer.append(agentcfg.save({'self_review':{'after_update':True}},seen)))
                thread.start();self.assertTrue(entered.wait(5))
                env=dict(os.environ,PYTHONPATH=str(ROOT/'desk'),PYTHONIOENCODING='utf-8')
                cli=subprocess.Popen([sys.executable,'-B','-m','localharness.config_cli','--path',str(path),'--mtime-ns',seen],cwd=ROOT/'desk',env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
                try:
                    cli.stdin.write(json.dumps({'agent':{'name':'new'},'tree':'KEEP'}));cli.stdin.close();cli.stdin=None
                    # Child is live while channel's checked transaction remains held.
                    self.assertIsNone(cli.poll())
                    release.set();thread.join(10)
                    out,err=cli.communicate(timeout=20)
                finally:
                    release.set();thread.join(10)
                    if cli.poll() is None:cli.kill();cli.wait()
                self.assertEqual(cli.returncode,0,err)
                self.assertTrue(answer[0]['ok'],answer)
                reply=json.loads(out);self.assertFalse(reply['ok']);self.assertEqual(reply['code'],'stale')
                saved=json.loads(path.read_text(encoding='utf-8'))
                self.assertTrue(saved['self_review']['after_update']);self.assertEqual(saved['agent']['name'],'old')
                self.assertEqual(saved['tree'],'KEEP')

    def test_full_save_then_old_channel_receipt_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'helene.json';path.write_text('{"agent":{"name":"old"}}',encoding='utf-8')
            seen=config_cli.mtime_ns(path)
            self.assertTrue(config_cli.save(path,'{"agent":{"name":"new"}}',seen)['ok'])
            with patch.object(agentcfg.readers,'config_path',return_value=path):
                response=agentcfg.save({'self_review':{'after_update':True}},seen)
            self.assertEqual(response['code'],'conflict');self.assertFalse(response['ok'])
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['agent']['name'],'new')

if __name__=='__main__':unittest.main()
