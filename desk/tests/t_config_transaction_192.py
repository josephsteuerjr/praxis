"""Two real Settings writers, including a separate CLI process, must serialize CAS."""
import json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'desk'))
from deskd import agentcfg
from localharness import config_cli

class Transactions(unittest.TestCase):
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
