"""Authenticated owner intents, exact acknowledgements and damaged-history views."""
import asyncio,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from aiohttp.test_utils import TestClient,TestServer


class RecoveryAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        global fixture,forge_control,forge_reader,forge_recovery,forge_snapshot
        import test_praxis_phone as fixture
        from deskd import forge_control,forge_reader,forge_recovery,forge_snapshot
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.tree=Path(tmp.name)
        self.enterContext(patch.object(fixture.app.readers,'tree',return_value=self.tree))
        fixture.app.TOKEN='synthetic-owner';fixture.app._DEVICES_CACHE={'stamp':None,'rows':[]}
        fixture.app._save_devices([{'id':'fixture','hash':fixture.app._key_hash('synthetic-phone')}])
        self.headers={'Authorization':'Bearer synthetic-phone'};forge_snapshot.invalidate();self.addCleanup(forge_snapshot.invalidate)
        self.task=self.tree/'memory/.forge/tasks/code-history';self.unit=self.task/'agents/agent-old';self.unit.mkdir(parents=True)
        self.put(self.task/'task.json',{'id':'code-history','goal':'Synthetic recovery fixture','status':'stalled','scope':'self','root':str(self.tree/'workspace')})
        self.put(self.unit/'request.json',{'id':'agent-old','task_id':'code-history','status':'running','scope':'self','root':str(self.tree/'workspace'),'created':'2000-01-01T00:00:00+00:00'})
        self.put(self.unit/'progress.json',{'generation':'g1','phase':'waiting'})
        self.put(self.unit/'stop.json',{'state':'unresolved','note':'unknown original outcome'})
        self.raw=b'{"kind":"agent_spawned","agent_id":"agent-old","at":"2000-01-01T00:00:00+00:00"}\n'+b'{"kind":broken\n'
        (self.task/'events.jsonl').write_bytes(self.raw)
        self.client=TestClient(TestServer(fixture.app.build_app()));await self.client.start_server()

    async def asyncTearDown(self):await self.client.close()
    def put(self,path,value):path.write_text(json.dumps(value),encoding='utf8')
    def body(self,**kw):return {'command_id':'a'*32,'task_id':'code-history','agent_id':'agent-old','expected_generation':'g1','action':'prepare_recovery','host_binding':'verified-local-execution-host','reason':'owner verified original execution host',**kw}
    async def post(self,body):return await self.client.post('/api/phone/forge/control',json=body,headers=self.headers)

    async def test_no_auth_cannot_read_or_publish_control(self):
        for method in ('get','post'):
            response=await getattr(self.client,method)('/api/phone/forge/control',**({'json':self.body()} if method=='post' else {}))
            self.assertEqual(response.status,403)

    async def test_explicit_confirmation_is_required_before_queue_write(self):
        for fields in ({'host_binding':''},{'reason':''}):
            self.assertEqual((await self.post(self.body(**fields))).status,400)
        self.assertFalse((self.tree/'memory/.control/forge/commands').exists())
        self.assertEqual((self.task/'events.jsonl').read_bytes(),self.raw)

    async def test_queue_ack_then_actual_receipt_and_reload_history(self):
        response=await self.post(self.body());self.assertEqual(response.status,200)
        self.assertEqual((await response.json())['state'],'queued')
        observed=[]
        def callback(task,action,**fields):
            observed.append((task,action,fields));return {'status':'prepared','reason':'synthetic callback observation'}
        forge_control.consume_pending(self.tree,callback)
        self.assertEqual(observed[0][2]['host_binding'],'verified-local-execution-host')
        self.assertEqual(observed[0][2]['reason'],self.body()['reason'])
        response=await self.client.get('/api/phone/forge/control?command_id='+'a'*32,headers=self.headers)
        self.assertEqual(response.status,200);self.assertEqual((await response.json())['state'],'observed')
        response=await self.client.get('/api/phone/forge/control?task_id=code-history',headers=self.headers)
        self.assertEqual((await response.json())['commands'][0]['command_id'],'a'*32)
        self.assertEqual((self.task/'events.jsonl').read_bytes(),self.raw)

    async def test_same_id_changed_host_attestation_conflicts_without_overwrite(self):
        self.assertEqual((await self.post(self.body())).status,200)
        path=self.tree/'memory/.control/forge/commands'/('a'*32+'.json');before=path.read_bytes()
        self.assertEqual((await self.post(self.body(reason='another claim'))).status,409)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual((await self.post(self.body())).status,200)
        self.assertEqual(len(list(path.parent.glob('*.json'))),1)

    async def test_claimed_unknown_effect_is_never_replayed(self):
        await self.post(self.body());root=self.tree/'memory/.control/forge';(root/'processing').mkdir()
        (root/'commands'/('a'*32+'.json')).replace(root/'processing'/('a'*32+'.json'))
        callback=Mock(side_effect=AssertionError('cannot replay claimed effect'))
        self.assertEqual(forge_control.consume_pending(self.tree,callback)[0]['state'],'unknown')
        callback.assert_not_called()
        response=await self.post(self.body());self.assertEqual((await response.json())['state'],'unknown')
        self.assertEqual((self.task/'events.jsonl').read_bytes(),self.raw)

    async def test_generation_change_is_rejected_before_callback(self):
        await self.post(self.body());self.put(self.unit/'progress.json',{'generation':'g2'})
        callback=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('stale generation cannot run'))
        self.assertEqual(forge_control.consume_pending(self.tree,callback)[0]['state'],'rejected')

    async def test_corrupt_journal_is_visible_without_exposing_or_changing_bytes(self):
        response=await self.client.get('/api/phone/forge/tasks',headers=self.headers)
        self.assertEqual(response.status,200);page=await response.json()
        rows=page['active']+page['recent'];unit=next(u for t in rows for u in t['agents'] if u['id']=='agent-old')
        diagnostic=unit['history_diagnostic'];self.assertEqual(diagnostic['code'],'forge_history_corrupt')
        self.assertEqual(diagnostic['line'],2);self.assertEqual(diagnostic['offset'],self.raw.index(b'\n')+1)
        self.assertEqual(diagnostic['journal_sha256'],hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(unit['status'],'stop_pending');self.assertFalse(unit['recovery']['proven'])
        self.assertEqual((self.task/'events.jsonl').read_bytes(),self.raw)

    async def test_invalid_prior_proof_stays_unknown_and_is_preserved(self):
        original=b'{invalid original proof';(self.unit/forge_recovery.HISTORY_PROOF).write_bytes(original)
        unit=forge_reader.task_detail(self.tree,'code-history')['agents'][0]
        self.assertEqual(unit['status'],'stop_pending');self.assertFalse(unit['recovery']['proven'])
        self.assertEqual(unit['history_diagnostic']['code'],'forge_history_proof_changed')
        self.assertEqual((self.unit/forge_recovery.HISTORY_PROOF).read_bytes(),original)

    async def test_unrelated_legacy_owner_routes_still_refuse_phone(self):
        self.assertEqual((await self.post(self.body(action='resume'))).status,400)
        self.assertEqual((await self.client.get('/api/forge/control',headers=self.headers)).status,403)

    async def test_process_only_unknown_task_is_visible_and_can_queue_group_recovery(self):
        import shutil
        shutil.rmtree(self.task/'agents')
        process=self.task/'processes/process-old';process.mkdir(parents=True)
        self.put(process/'request.json',{'id':'process-old','task_id':'code-history','root':str(self.tree/'workspace'),'status':'running'})
        self.put(process/'stop.json',{'state':'unresolved'})
        response=await self.client.get('/api/phone/forge/tasks',headers=self.headers)
        self.assertEqual(response.status,200);page=await response.json();row=page['active'][0]
        self.assertEqual(row['agents'],[]);self.assertEqual(row['recovery_units'][0]['id'],'process-old')
        self.assertEqual(row['recovery_units'][0]['history_diagnostic']['code'],'forge_history_corrupt')
        response=await self.post(self.body(agent_id='',expected_generation=''))
        self.assertEqual(response.status,200);self.assertEqual((await response.json())['state'],'queued')
