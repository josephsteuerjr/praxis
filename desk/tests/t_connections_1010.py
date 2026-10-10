"""Onboarding network checks, trusted discovery and resumable transfer: no live services."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock
import urllib.error

DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK),str(DESK/'localharness')]
from deskd import connection_checks
import remote_locator as locator
import server_transfer as transfer
import server_prepare

class Checks(unittest.TestCase):
    def check(self,replies,fields=None):
        requests=[]
        def open_request(req,timeout):
            requests.append(req)
            value=replies.pop(0)
            if isinstance(value,Exception):raise value
            return contextlib.closing(io.BytesIO(json.dumps(value).encode()))
        cfg={'telegram':{'bot_token':'test-bot-token','owner_id':'123','proxy':{'enabled':True,'url':'https://gateway.test/tg','key':'test-gateway-key'}}}
        original=json.dumps(cfg,sort_keys=True)
        with mock.patch.object(connection_checks.urllib.request,'build_opener',return_value=types.SimpleNamespace(open=open_request)):
            result=connection_checks.check_telegram(cfg,fields)
        self.assertEqual(json.dumps(cfg,sort_keys=True),original,'checking must never write settings')
        self.assertNotIn('test-bot-token',json.dumps(result));self.assertNotIn('test-gateway-key',json.dumps(result))
        self.assertFalse(any('getUpdates' in r.full_url or 'sendMessage' in r.full_url for r in requests))
        return result,requests

    def test_bot_and_files_are_independent(self):
        result,requests=self.check([{'ok':True},{'ok':True,'result':{'is_bot':True,'username':'my_bot'}},{'ok':True,'result':{'id':123,'type':'private'}}])
        self.assertFalse(result['ok'])
        rows={r['name']:r for r in result['checks']}
        self.assertTrue(rows['Бот']['ok']);self.assertFalse(rows['Фото и стикеры']['ok'])
        self.assertEqual(len(requests),3)
        self.assertEqual(result['bot_url'],'https://t.me/my_bot')

    def test_modern_gateway_and_invalid_owner(self):
        result,_=self.check([{'ok':True,'capabilities':['file']},{'ok':True,'result':{'is_bot':True}}],{'owner_id':'0'})
        self.assertFalse(result['ok']);self.assertFalse(result['checks'][-1]['ok'])

    def test_invalid_health_and_wrong_key_are_human_errors(self):
        for reply in ([],{'ok':False},urllib.error.HTTPError('private-url',403,'test-gateway-key',None,None)):
            result,_=self.check([reply])
            self.assertFalse(result['ok']);self.assertTrue(result['checks'])
            self.assertNotIn('private-url',json.dumps(result))

    def test_redirect_is_refused(self):
        self.assertIsNone(connection_checks.NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.test'))
    def test_unstarted_owner_chat_gets_the_next_step(self):
        result,_=self.check([{'ok':True,'capabilities':['file']},{'ok':True,'result':{'is_bot':True,'username':'my_bot'}},urllib.error.HTTPError('private-url',400,'chat not found',None,None)])
        self.assertFalse(result['ok']);self.assertIn('нажми «Начать»',result['checks'][-1]['message'])

class Discovery(unittest.TestCase):
    def config(self):return {'key':'owner-test-key','telegram':{'bot_token':'bot-test-token','owner_id':123}}
    def test_rotated_menu_origin_is_proven_before_owner_auth(self):
        for origin in ('https://first.test','https://changed.test'):
            requests=[]
            def read(req):
                requests.append(req)
                if 'getChatMenuButton' in req.full_url:return {'ok':True,'result':{'type':'web_app','web_app':{'url':origin+'/tg/'}}}
                challenge=req.full_url.split('challenge=')[1]
                self.assertIsNone(req.get_header('Authorization'))
                self.assertNotIn('owner-test-key',req.full_url)
                return {'instance':'real-instance','challenge':challenge,'proof':locator.signature('owner-test-key',challenge,'real-instance')}
            with mock.patch.object(locator,'read',side_effect=read):self.assertEqual(locator.locate(self.config()),origin)
            self.assertEqual(len(requests),2)

    def test_wrong_agent_or_replayed_challenge_never_gets_the_key(self):
        for wrong_challenge in (False,True):
            requests=[]
            def read(req):
                requests.append(req)
                if len(requests)==1:return {'ok':True,'result':{'type':'web_app','web_app':{'url':'https://wrong.test/tg/'}}}
                challenge='old' if wrong_challenge else req.full_url.split('challenge=')[1]
                return {'challenge':challenge,'instance':'other','proof':locator.signature('someone-elses-key',challenge,'other')}
            with mock.patch.object(locator,'read',side_effect=read),self.assertRaises(ValueError):locator.locate(self.config())
            self.assertTrue(all(r.get_header('Authorization') is None for r in requests))

    def test_wrong_menu_url_does_not_receive_a_probe(self):
        for target in ('http://unsafe.test/tg/','https://user:pass@unsafe.test/tg/','https://safe.test/tg/?key=x','https://safe.test/other'):
            with mock.patch.object(locator,'read',return_value={'ok':True,'result':{'type':'web_app','web_app':{'url':target}}}) as read,self.assertRaises(ValueError):locator.locate(self.config())
            self.assertEqual(read.call_count,1)

class Transfer(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.program=self.root/'program';self.program.mkdir()
        for name in ('app','tree','server'):(self.program/name).mkdir()
        for name in ('app/code.py','tree/agent.py','server/install.sh','requirements.txt','helene-relay'):(self.program/name).write_text('test')
        (self.program/'helene-build.json').write_text(json.dumps({'version':'2.7.9'}))
        self.config=self.root/'helene.json';self.config.write_text(json.dumps({'mode':'local','tree':'data','telegram':{'bot_token':'test-token','owner_id':'123'}}))
        self.raw={'host':'server.test','login':'root','fingerprint':'SHA256:tested','confirmed_host':True,'password':'never-in-argv'}
        self.files={};self.commands=[];self.failed=False;self.fail_install=False;self.corrupt=False
        self.client=types.SimpleNamespace(close=lambda:None,open_sftp=self.sftp)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(transfer,'connect',return_value=self.client).start()
        mock.patch.object(transfer,'execute',side_effect=self.execute).start()
        mock.patch.object(transfer,'probe',side_effect=self.probe).start()
        mock.patch.object(transfer.carry,'export',side_effect=self.export).start()
        mock.patch.object(transfer.remote_locator,'locate',return_value='https://verified.test').start()
        mock.patch.object(transfer.remote_locator,'read',return_value={'runner':{'alive':True}}).start()
    def probe(self,*args):return {'ok':True,'docker':True,'fingerprint':'SHA256:tested','existing':transfer.MARKER_PATH in self.files,'resumable':transfer.MARKER_PATH in self.files}
    def export(self,config,path):path.write_bytes(b'cold-memory-archive');return {'archive':str(path)}
    @contextlib.contextmanager
    def sftp(self):
        owner=self
        class File(io.StringIO):
            def __exit__(self,*args):owner.files[self.name]=self.getvalue().encode();self.close()
        def file(name,*args):f=File();f.name=name;return f
        yield types.SimpleNamespace(put=lambda local,remote:self.files.update({remote:Path(local).read_bytes()}),file=file,chmod=lambda *args:None)
    def execute(self,client,command,**kwargs):
        self.commands.append(command);self.assertNotIn('never-in-argv',command)
        if command.startswith('sha256sum '):
            blob=self.files[command.split()[1]];return 0,('bad-hash' if self.corrupt else hashlib.sha256(blob).hexdigest())+'  file',''
        if 'sh server/install.sh' in command and self.fail_install and not self.failed:
            self.failed=True;raise transfer.TransferError('test interrupted setup')
        if command=='cat '+transfer.TOKEN_PATH:return 0,'new-owner-key',''
        if command=='cat '+transfer.TARGET+'/helene-build.json':return 0,'{"version":"2.7.9"}',''
        return 0,'',''
    def test_success_keeps_archive_and_secret_free_receipt(self):
        result=transfer.run(self.raw,self.config,self.program)
        self.assertTrue(result['ok']);self.assertEqual(result['key'],'new-owner-key')
        self.assertTrue(Path(result['archive']).is_file())
        row=transfer.saved_transfer(self.config);self.assertEqual(row['phase'],'ready')
        self.assertNotIn('never-in-argv',json.dumps(row));self.assertNotIn('new-owner-key',json.dumps(row))
    def test_interrupted_install_continues_the_same_archive(self):
        self.fail_install=True
        with self.assertRaises(transfer.TransferError):transfer.run(self.raw,self.config,self.program)
        row=transfer.saved_transfer(self.config);original=row['archive'];original_id=row['id']
        result=transfer.run(self.raw,self.config,self.program)
        self.assertEqual(result['archive'],original);self.assertEqual(transfer.saved_transfer(self.config)['id'],original_id)
        self.assertEqual(transfer.carry.export.call_count,1,'resume must not re-export a different snapshot')
    def test_corrupt_sftp_payload_stops_before_install(self):
        self.corrupt=True
        with self.assertRaises(transfer.TransferError):transfer.run(self.raw,self.config,self.program)
        self.assertFalse(any('sh server/install.sh' in c for c in self.commands))
        self.assertNotIn(transfer.MARKER_PATH,self.files)
    def test_existing_unrelated_agent_is_never_replaced(self):
        transfer.probe.side_effect=lambda *args:{'ok':True,'existing':True,'resumable':False,'fingerprint':'SHA256:tested'}
        with self.assertRaises(transfer.TransferError):transfer.run(self.raw,self.config,self.program)
        self.assertEqual(self.commands,[]);transfer.carry.export.assert_not_called()
    def test_live_local_agent_blocks_export_and_ssh(self):
        with mock.patch.object(transfer.boot,'_lock_is_live',return_value=True),self.assertRaises(transfer.TransferError):transfer.run(self.raw,self.config,self.program)
        transfer.probe.assert_not_called();transfer.carry.export.assert_not_called();self.assertEqual(self.commands,[])
    def test_kit_excludes_private_root_config_and_git(self):
        (self.program/'helene.json').write_text('private-config');(self.program/'tree/.git').mkdir();(self.program/'tree/.git/secret').write_text('private-history')
        path=self.root/'kit.tar.gz';transfer.make_program(self.program,path)
        with tarfile.open(path) as kit:
            self.assertNotIn('helene.json',kit.getnames());self.assertFalse(any('.git' in name for name in kit.getnames()))

class HostTrust(unittest.TestCase):
    def test_helper_starts_with_isolated_python_without_a_running_local_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);config=root/'helene.json';config.write_text('{}')
            result=subprocess.run([sys.executable,'-I',str(Path(transfer.__file__)),'status','--config',str(config),'--program',str(root)],input='{}',text=True,capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),{'ok':True,'pending':False,'phase':None,'host':'','archive':None,'message':''})
    def test_no_credentials_sent_until_explicit_confirmation(self):
        key=types.SimpleNamespace(asbytes=lambda:b'server-public-key')
        transport=types.SimpleNamespace(start_client=lambda **kwargs:None,get_remote_server_key=lambda:key,close=lambda:None)
        known=types.SimpleNamespace(load=lambda *a:None,lookup=lambda *a:None)
        with mock.patch.object(transfer.paramiko,'Transport',return_value=transport),mock.patch.object(transfer.paramiko,'HostKeys',return_value=known),mock.patch.object(transfer,'connect') as connect:
            raw={'host':'server.test','password':'never-before-trust'}
            result=transfer.probe(raw);self.assertTrue(result['trust_needed']);connect.assert_not_called()
            result=transfer.probe({**raw,'fingerprint':result['fingerprint']});self.assertTrue(result['trust_needed']);connect.assert_not_called()

class VoicePreparation(unittest.TestCase):
    def test_guided_move_enables_its_own_phone_endpoint_preserving_agent_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'helene.json';config.write_text('{"telegram":{"bot_token":"test-private"},"phone":{"enabled":false,"external":"http://old.test"}}')
            with mock.patch.object(server_prepare.voice,'state',return_value={'ready':False}),mock.patch.object(server_prepare.voice,'speech_state',return_value={'ready':False}):
                server_prepare.prepare(config,connect=True)
            value=json.loads(config.read_text());self.assertEqual(value['telegram']['bot_token'],'test-private')
            self.assertEqual(value['phone'],{'enabled':True,'mode':'automatic'})
    def test_disabled_voice_downloads_nothing_and_enabled_voice_must_be_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'helene.json';config.write_text('{"tree":"data","voice":{"enabled":false,"speak":false}}')
            with mock.patch.object(server_prepare.voice,'state',return_value={'ready':False}),mock.patch.object(server_prepare.voice,'speech_state',return_value={'ready':False}),mock.patch.object(server_prepare.voice,'fetch') as hearing,mock.patch.object(server_prepare.voice,'fetch_voice') as speech:
                server_prepare.prepare(config);hearing.assert_not_called();speech.assert_not_called()
                config.write_text('{"tree":"data","voice":{"enabled":true,"speak":true}}')
                with self.assertRaises(RuntimeError):server_prepare.prepare(config)
                hearing.assert_called_once();speech.assert_called_once()

if __name__=='__main__':unittest.main(verbosity=2)
