import asyncio,hashlib,hmac,json,os,tempfile,time,unittest
from pathlib import Path
from aiohttp.test_utils import TestClient,TestServer
from urllib.parse import urlencode

_TEMP=tempfile.TemporaryDirectory();TREE=Path(_TEMP.name)/'tree';TREE.mkdir()
os.environ.update(HELENE_TREE=str(TREE),HELENE_TOKEN='synthetic-owner',HELENE_DESK_STATE=str(Path(_TEMP.name)/'state'),HELENE_TG_OWNER_ID='42',HELENE_TG_BOT_TOKEN='synthetic-bot')
import deskapp as app
from deskd import forge_reader,phone_activity,forge_snapshot

class PhoneContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        forge_snapshot.invalidate();self.addCleanup(forge_snapshot.invalidate)
        app.TOKEN='synthetic-owner';app._DEVICES_CACHE={'stamp':None,'rows':[]}
        app._save_devices([{'id':'fixture','hash':app._key_hash('synthetic-phone')}])
        self.client=TestClient(TestServer(app.build_app()));await self.client.start_server()
        self.headers={'Authorization':'Bearer synthetic-phone'}
        self.task=TREE/'memory/.forge/tasks/code-fixture';self.unit=self.task/'agents/worker-fixture';self.unit.mkdir(parents=True,exist_ok=True)
        (self.task/'task.json').write_text(json.dumps({'id':'code-fixture','goal':'Проверить панель','status':'active'}))
        (self.unit/'request.json').write_text(json.dumps({'id':'worker-fixture','task_id':'code-fixture','status':'running','brief':'Проверить интерфейс','role':'worker'}))
        (self.unit/'result.json').write_text(json.dumps({'status':'done','result':'PRIVATE-PAYLOAD-CANARY'}))
        (self.unit/'progress.json').write_text(json.dumps({'phase':'model','generation':'g1'}))
    async def asyncTearDown(self):await self.client.close()
    async def test_no_key_cannot_read_phone_records(self):
        r=await self.client.get('/api/phone/forge/tasks');self.assertEqual(r.status,403)
    async def test_paired_phone_reads_real_server_layout_without_result_payload(self):
        r=await self.client.get('/api/phone/forge/tasks',headers=self.headers);self.assertEqual(r.status,200)
        text=await r.text();self.assertNotIn('PRIVATE-PAYLOAD-CANARY',text)
        page=json.loads(text);row=page['recent'][0];self.assertEqual(row['id'],'code-fixture');self.assertEqual(row['agents'][0]['status'],'done')
        ref=row['agents'][0]['result_ref']
        self.assertEqual(ref['kind'],'result');self.assertEqual(len(ref['sha256']),64)
        self.assertEqual(r.headers['Cache-Control'],'private, no-store')
        # The installed 4 November API exposes hash-addressed payloads to paired
        # devices, while the roster itself never embeds their private contents.
        payload=await self.client.get('/api/phone/forge/payload',params={'task_id':'code-fixture','unit_id':'worker-fixture','kind':'result','sha256':ref['sha256']},headers=self.headers)
        self.assertEqual(payload.status,200);self.assertIn('PRIVATE-PAYLOAD-CANARY',(await payload.json())['text'])
    async def test_missing_worker_does_not_become_a_false_empty_team(self):
        p=self.unit/'request.json';old=p.read_bytes();p.unlink()
        try:
            r=await self.client.get('/api/phone/forge/tasks',headers=self.headers);self.assertEqual(r.status,404)
        finally:p.write_bytes(old)
    async def test_traversal_and_symlink_are_refused(self):
        r=await self.client.get('/api/phone/forge/events?task_id=..&unit_id=worker-fixture',headers=self.headers);self.assertEqual(r.status,400)
        link=self.task.parent/'escape-fixture';link.symlink_to(TREE,target_is_directory=True)
        try:
            r=await self.client.get('/api/phone/forge/events?task_id=escape-fixture&unit_id=worker-fixture',headers=self.headers);self.assertIn(r.status,(403,404))
        finally:link.unlink()
    async def test_phone_does_not_get_control_payload_or_owner_config(self):
        for path in ('/api/forge/control','/api/forge/payload','/api/agent-config','/pair/devices'):
            r=await self.client.get(path,headers=self.headers);self.assertEqual(r.status,403,path)
        r=await self.client.post('/api/phone/forge/tasks',json={'action':'stop'},headers=self.headers);self.assertIn(r.status,(403,405))
    async def test_live_phase_is_not_process_liveness(self):
        row=forge_reader.task_detail(TREE,'code-fixture')['agents'][0];self.assertEqual(row['status'],'done');self.assertNotEqual(row['process_liveness'],'alive')
    async def test_media_handler_returns_bytes_instead_of_json_serializing_a_file(self):
        from unittest.mock import patch
        path=TREE/'synthetic.ogg';path.write_bytes(b'OggS-TEST')
        with patch.object(app.readers,'media_file',return_value=(path,'audio/ogg','')):
            r=await self.client.get('/api/media?path=synthetic.ogg',headers=self.headers)
            self.assertEqual(r.status,200);self.assertEqual(await r.read(),b'OggS-TEST');self.assertIn('audio/ogg',r.headers['Content-Type'])
    async def test_existing_room_post_requires_auth_and_accepts_no_cross_origin(self):
        r=await self.client.post('/api/say',json={'text':'do not send'},headers={**self.headers,'Origin':'https://untrusted.invalid'})
        self.assertEqual(r.status,403)
    async def test_telegram_signed_owner_login_still_works_and_other_user_is_refused(self):
        from unittest.mock import patch
        def signed(uid):
            data={'auth_date':str(int(time.time())),'user':json.dumps({'id':uid,'first_name':'Fixture'})}
            secret=hmac.new(b'WebAppData',b'synthetic-bot',hashlib.sha256).digest()
            data['hash']=hmac.new(secret,'\n'.join(k+'='+data[k] for k in sorted(data)).encode(),hashlib.sha256).hexdigest()
            return urlencode(data)
        with patch.object(app,'_tg_tokens',return_value=['synthetic-bot']),patch.object(app,'_tg_owner_id',return_value='42'):
            r=await self.client.post('/pair/telegram',json={'initData':signed(42)});self.assertEqual(r.status,200)
            body=await r.json();self.assertTrue(body['key'].startswith('dk-'))
            r=await self.client.post('/pair/telegram',json={'initData':signed(43)});self.assertEqual(r.status,403)

if __name__=='__main__':unittest.main()
