"""Paired-device HTTP boundaries for the real PWA settings and monitoring."""
import copy,hashlib,json,secrets,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
from aiohttp.test_utils import TestClient,TestServer
import deskapp
from deskd import phone_settings,phone_activity
from localharness import config_cli


class Surface(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.tree=self.root/'data';self.tree.mkdir()
        self.path=self.root/'helene.json';self.secret=secrets.token_urlsafe(32)
        self.config={'mode':'local','tree':str(self.tree),'port':31234,'agent':{'name':'Fixture'},
            'model':{'framework':'openai','model':'chosen','key':self.secret,'base_url':'https://example.invalid/v1'},
            'phone':{'enabled':False,'mode':'automatic'},'relay':{'enabled':False}}
        self.path.write_text(json.dumps(self.config),encoding='utf8')
        self.patches=[patch.object(deskapp,'TOKEN',secrets.token_urlsafe(32)),
            patch.object(deskapp.readers,'tree',return_value=self.tree),
            patch.object(deskapp.readers,'config_path',return_value=self.path),
            patch.dict(deskapp.os.environ,{'HELENE_DESK_STATE':str(self.root/'channel')})]
        for item in self.patches:item.start();self.addCleanup(item.stop)
        deskapp._DEVICES_CACHE.update(stamp=None,rows=[]);deskapp._PAIRS.clear();deskapp._PAIR_CACHE.update(path=None,stamp=None)
        pair=deskapp._new_pair();self.key=deskapp._redeem(pair['token'],'Fixture','127.0.0.1')['key']
        self.old_phone=deskapp._PHONE_ACCESS
        self.client=TestClient(TestServer(deskapp.build_app(port=31234)));await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addCleanup(setattr,deskapp,'_PHONE_ACCESS',self.old_phone)
        self.origin=str(self.client.make_url('')).rstrip('/')
    def headers(self,key=None):return {'Authorization':'Bearer '+(key or self.key),'Origin':self.origin}

    async def test_unpaired_browser_cannot_read_settings(self):
        response=await self.client.get('/api/phone/settings')
        self.assertEqual(response.status,403)

    async def test_paired_read_is_useful_but_never_returns_stored_keys(self):
        response=await self.client.get('/api/phone/settings',headers=self.headers())
        self.assertEqual(response.status,200);value=await response.json()
        self.assertNotIn(self.secret,json.dumps(value))
        self.assertIn('model.model',[field['path'] for field in value['fields']])

    async def test_runtime_paths_and_wrong_profile_are_rejected_before_rpc(self):
        state=await (await self.client.get('/api/phone/settings',headers=self.headers())).json()
        with patch.object(phone_settings,'_desktop_commit') as commit:
            for changes,scope in (({'tree':'elsewhere'},state['scope_id']),({'agent.name':'Changed'},uuid.uuid4().hex)):
                response=await self.client.post('/api/phone/settings',headers=self.headers(),json={
                    'changes':changes,'mtime_ns':state['mtime_ns'],'scope_id':scope,'operation':uuid.uuid4().hex})
                self.assertIn(response.status,(400,409))
        commit.assert_not_called();self.assertEqual(json.loads(self.path.read_text()),self.config)

    async def test_normal_setting_uses_actual_config_writer_and_keeps_private_state(self):
        state=await (await self.client.get('/api/phone/settings',headers=self.headers())).json()
        def commit(path,tree,value,seen,operation):return config_cli.save(path,json.dumps(value),seen)
        with patch.object(phone_settings,'_desktop_commit',side_effect=commit):
            response=await self.client.post('/api/phone/settings',headers=self.headers(),json={
                'changes':{'agent.name':'Changed'},'mtime_ns':state['mtime_ns'],'scope_id':state['scope_id'],'operation':uuid.uuid4().hex})
        self.assertEqual(response.status,200)
        actual=json.loads(self.path.read_text());self.assertEqual(actual['agent']['name'],'Changed')
        self.assertEqual(actual['model']['key'],self.secret);self.assertEqual(actual['tree'],str(self.tree))

    async def test_phone_monitoring_does_not_grant_forge_payload_or_control(self):
        for path,method in (('/api/forge/payload','get'),('/api/forge/events','get'),('/api/forge/control','post')):
            response=await getattr(self.client,method)(path,headers=self.headers())
            self.assertEqual(response.status,403)

    async def test_phone_event_projection_preserves_cursor_and_exact_refs_without_inline_payload(self):
        raw={'events':[{'seq':1,'kind':'tool_dispatch','tool':'inspect','input_ref':{'sha256':'a'*64,'private':self.secret},
            'receipt':{'handler':'python.inspect','status':'returned','secret':self.secret},'arbitrary':self.secret}],
            'next_offset':37,'next_after_seq':1,'generation':'fixture','has_more':False}
        with patch.object(phone_activity.forge_reader,'events',return_value=raw):
            response=await self.client.get('/api/phone/forge/events?task_id=code-fixture&unit_id=unit-fixture',headers=self.headers())
            self.assertEqual(response.status,200);result=await response.json()
        self.assertNotIn(self.secret,json.dumps(result));self.assertEqual(result['next_offset'],raw['next_offset'])
        self.assertEqual(result['events'][0]['receipt'],{'handler':'python.inspect','status':'returned'})
        self.assertEqual(result['events'][0]['input_ref'],{'sha256':'a'*64,'kind':'payload'})

    async def test_paired_payload_reads_verify_identity_hash_and_auth(self):
        unit=self.tree/'memory/.forge/tasks/code-fixture/agents/unit-fixture';unit.mkdir(parents=True)
        (unit/'request.json').write_text(json.dumps({'id':'unit-fixture','task_id':'code-fixture'}))
        raw='Observed result 🐍'.encode('utf8');(unit/'result.json').write_bytes(raw)
        query={'task_id':'code-fixture','unit_id':'unit-fixture','kind':'result','sha256':hashlib.sha256(raw).hexdigest()}
        url='/api/phone/forge/payload'
        response=await self.client.get(url,params=query,headers=self.headers())
        self.assertEqual(response.status,200);self.assertEqual((await response.json())['text'],raw.decode('utf8'))
        response=await self.client.get(url,params={**query,'sha256':'0'*64},headers=self.headers())
        self.assertEqual(response.status,409)
        response=await self.client.get(url,params={**query,'task_id':'../outside'},headers=self.headers())
        self.assertEqual(response.status,400)
        response=await self.client.get(url,params=query)
        self.assertEqual(response.status,403)

    async def test_subscription_rejects_arbitrary_commands_without_touching_a_relay(self):
        response=await self.client.post('/api/phone/subscription',headers=self.headers(),json={'action':'shell','command':'untrusted'})
        self.assertEqual(response.status,400)


if __name__=='__main__':unittest.main()
