import hashlib,json,os,tempfile,unittest
from pathlib import Path
from aiohttp.test_utils import TestClient,TestServer

_TEMP=tempfile.TemporaryDirectory();TREE=Path(_TEMP.name)/'tree';TREE.mkdir()
os.environ.update(HELENE_TREE=str(TREE),HELENE_TOKEN='synthetic-owner',HELENE_DESK_STATE=str(Path(_TEMP.name)/'state'),HELENE_TG_OWNER_ID='42',HELENE_TG_BOT_TOKENS='synthetic-bot')
import deskapp as app
from deskd import praxis_window

RUN='run-20261123T120000000000Z-abcdef12'
SECRET='SYNTHETIC-OWNER-CANARY'

def put(path,text):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')

class WindowInstruments(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import patch
        self.enterContext(patch.dict(os.environ,{'HELENE_TREE':str(TREE)}))
        app.TOKEN='synthetic-owner';app._DEVICES_CACHE={'stamp':None,'rows':[]}
        app._save_devices([{'id':'fixture','hash':app._key_hash('synthetic-phone')}])
        self.client=TestClient(TestServer(app.build_app()));await self.client.start_server()
        self.owner={'Authorization':'Bearer synthetic-owner'};self.phone={'Authorization':'Bearer synthetic-phone'}
        run=TREE/'memory/runs/2026-11'/RUN
        frame={'system':[{'type':'text','text':'PERSONA '+SECRET}], 'messages':[{'role':'user','content':'привет'},{'role':'assistant','content':[{'type':'text','text':'да'}]}], 'tools':[{'name':'reply','description':'say','input_schema':{}}]}
        raw=json.dumps(frame,ensure_ascii=False).encode();digest=hashlib.sha256(raw).hexdigest()
        (run/'results').mkdir(parents=True,exist_ok=True);(run/'results/model-input.json').write_bytes(raw)
        events=[
          {'kind':'run_created','seq':1,'at':'2026-11-23T12:00:00Z','context_snapshot':{'path':'context.md','sha256':'a'*64,'size':10}},
          {'kind':'status_changed','seq':2,'at':'2026-11-23T12:00:00Z','from_status':'pending','to_status':'running'},
          {'kind':'model_started','seq':3,'at':'2026-11-23T12:00:01Z','call_id':'model-1','role':'voice','message_count':2,'tool_count':1},
          {'kind':'model_input','seq':4,'at':'2026-11-23T12:00:01Z','call_id':'model-1','name':'model-input','result':{'result_id':'result-0001','path':'results/model-input.json','sha256':digest,'size':len(raw),'inline':{'truncated':True}},
           'metadata':{'basis':'episodic','mode':'dm','frame_id':'frame-fixture','reused':False,'budget':{'limit':0,'unit':'chars','used_start':100,'used_final':250,'excludes':['x']},
                       'counts':{'tiers_offered':8,'tiers_included':4,'tiers_dropped':0},'caps':{'sections_dropped':0},'honesty':{'ok':True},'sections':[{'name':'persona','chars':40},{'name':'desires','chars':60}],'covers':['c1']}},
          {'kind':'model_preparation_timing','seq':5,'at':'2026-11-23T12:00:01Z','call_id':'model-1','preparation_observed':True,'measured_total_ms':120.5,'stages_ms':{'old_context':100,'call_setup':20},'substages_ms':{'old_context.persona':40}},
          {'kind':'model_progress','seq':6,'at':'2026-11-23T12:00:02Z','call_id':'model-1','metrics':{'schema':'relay.generation.v1','argument_bytes':5000,'longest_whitespace_run':12,'outcome':'completed','secret':'never-carried'}},
          {'kind':'tool_started','seq':7,'at':'2026-11-23T12:00:03Z','call_id':'call-1','tool':'reply','args':{}},
          {'kind':'tool_result','seq':8,'at':'2026-11-23T12:00:03Z','call_id':'call-1','name':'reply','result':{}},
          {'kind':'model_completed','seq':9,'at':'2026-11-23T12:00:04Z','call_id':'model-1','role':'voice','duration_ms':3000,'stop_reason':'tool_use','framework':'anthropic','model':'synthetic','usage':{'in':10,'out':2},'tool_calls':1,'text_chars':0},
          {'kind':'direct_outbox_intent','seq':10,'at':'2026-11-23T12:00:04Z','call_id':'call-1','result':{'result_id':'result-0002','path':'results/intent.json','size':30,'inline':{'truncated':False}}},
          {'kind':'delivery_skipped','seq':11,'at':'2026-11-23T12:00:05Z','reason':'Praxis chose silence','receipt_key':'k'},
          {'kind':'status_changed','seq':12,'at':'2026-11-23T12:00:05Z','from_status':'running','to_status':'done'},
          {'kind':'recap_written','seq':13,'at':'2026-11-23T12:00:05Z','path':'RECAP.md','size':7,'sha256':'b'*64},
        ]
        put(run/'events.jsonl',''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in events))
        put(run/'manifest.json',json.dumps({'created_at':'2026-11-23T12:00:00Z','status':'done','event_seq':13,'context':{'kind':'chat_turn','goal':'ответить'}}))
        put(run/'context.md','контекст хода');put(run/'RECAP.md','рекап')
        state=TREE/'memory/.state'
        put(state/'denials.jsonl',''.join(json.dumps({'ts':1700000000+i,'rail':'hands_floor','op':'fs_read','detail':'отказ '*200})+'\n' for i in range(5)))
        put(state/'envelope_shadow.jsonl',json.dumps({'ts':'2026-11-23T12:00:00Z','run_id':RUN,'note':'chat_turn','envelope':{'trigger_kind':'reply'}})+'\n')
        put(state/'telegram_contacts.json',json.dumps({'contacts':'never-listed'}))
    async def asyncTearDown(self):await self.client.close()

    async def test_only_the_owner_key_opens_the_instruments(self):
        for path in ('/api/window/journals','/api/window/journal/denials','/api/window/run/'+RUN,'/api/window/run/'+RUN+'/frame?call=model-1','/api/window/run/'+RUN+'/doc/recap'):
            self.assertEqual((await self.client.get(path)).status,403,path)
            self.assertEqual((await self.client.get(path,headers=self.phone)).status,403,path)
        self.assertEqual((await self.client.get('/api/window/journals',headers=self.owner)).status,200)

    async def test_journals_are_a_fixed_list_without_contacts_or_credentials(self):
        listed=await (await self.client.get('/api/window/journals',headers=self.owner)).json()
        ids={row['id'] for row in listed}
        self.assertEqual(ids,{row[0] for row in praxis_window.JOURNALS})
        denials=next(row for row in listed if row['id']=='denials')
        self.assertEqual((denials['exists'],denials['lines']),(True,5))
        self.assertFalse(next(row for row in listed if row['id']=='hands')['exists'])
        self.assertEqual((await self.client.get('/api/window/journal/telegram_contacts',headers=self.owner)).status,404)
        self.assertEqual((await self.client.get('/api/window/journal/..%2Fdeskd%2F__init__',headers=self.owner)).status,404)

    async def test_journal_rows_are_the_newest_first_page_cut_to_size(self):
        body=await (await self.client.get('/api/window/journal/denials?limit=2',headers=self.owner)).json()
        self.assertEqual([row['ts'] for row in body['rows']],[1700000003,1700000004]);self.assertEqual(body['lines'],5)
        self.assertLessEqual(len(body['rows'][0]['detail']),praxis_window._TEXT+1);self.assertTrue(body['rows'][0]['detail'].endswith('…'))
        self.assertEqual(body['columns'],['ts','rail','op','detail'])
        every=await (await self.client.get('/api/window/journal/denials?limit=999999',headers=self.owner)).json()
        self.assertEqual(len(every['rows']),5)

    async def test_timeline_groups_what_the_harness_journaled(self):
        body=await (await self.client.get('/api/window/run/'+RUN,headers=self.owner)).json()
        self.assertEqual((body['status'],body['kind'],body['goal']),('done','chat_turn','ответить'))
        call=body['calls'][0]
        self.assertEqual(call['call_id'],'model-1');self.assertEqual(call['state'],'completed')
        self.assertEqual(call['frame']['basis'],'episodic');self.assertFalse(call['frame']['reused'])
        self.assertEqual(call['frame']['budget']['used_final'],250);self.assertEqual(call['frame']['counts']['tiers_included'],4)
        self.assertEqual([section['name'] for section in call['frame']['sections']],['persona','desires'])
        self.assertEqual(call['preparation']['total_ms'],120.5);self.assertEqual(call['preparation']['stages_ms']['old_context'],100)
        self.assertEqual(call['generation']['outcome'],'completed');self.assertEqual(call['generation']['longest_whitespace_run'],12)
        self.assertNotIn('secret',json.dumps(call['generation']));self.assertNotIn('never-carried',json.dumps(body))
        self.assertEqual(call['completed']['usage'],{'in':10,'out':2});self.assertEqual(call['completed']['stop_reason'],'tool_use')
        self.assertEqual(body['tools'],{'reply':1})
        self.assertEqual([(row['from'],row['to']) for row in body['statuses']],[('pending','running'),('running','done')])
        self.assertEqual([row['kind'] for row in body['outgoing']],['direct_outbox_intent','delivery_skipped'])
        self.assertEqual(body['outgoing'][0]['result_id'],'result-0002');self.assertEqual(body['outgoing'][1]['reason'],'Praxis chose silence')
        self.assertEqual(body['recap']['size'],7);self.assertEqual(body['context_snapshot']['path'],'context.md')
        self.assertEqual(body['documents'],{'context':True,'recap':True})
        self.assertEqual(body['envelope']['envelope'],{'trigger_kind':'reply'})
        self.assertIsNone(body['turn'])
        self.assertEqual((await self.client.get('/api/window/run/run-19990101T000000000000Z-00000000',headers=self.owner)).status,404)

    async def test_frame_is_verified_against_the_recorded_digest(self):
        body=await (await self.client.get('/api/window/run/'+RUN+'/frame?call=model-1',headers=self.owner)).json()
        self.assertTrue(body['available']);self.assertTrue(body['verified'])
        self.assertEqual(body['messages'][0],{'role':'user','content':'привет'});self.assertEqual([tool['name'] for tool in body['tools']],['reply'])
        self.assertEqual(body['frame']['mode'],'dm');self.assertIn(SECRET,body['system'][0]['text'])  # the owner sees the real persona
        # a call the harness never recorded is refused in words, like every missing record of the channel
        self.assertEqual((await self.client.get('/api/window/run/'+RUN+'/frame?call=model-none',headers=self.owner)).status,404)
        # the same call with a tampered record: reported, not shown
        (TREE/'memory/runs/2026-11'/RUN/'results/model-input.json').write_bytes(b'{"messages":[],"system":"tampered"}')
        tampered=await (await self.client.get('/api/window/run/'+RUN+'/frame?call=model-1',headers=self.owner)).json()
        self.assertFalse(tampered['available']);self.assertEqual(tampered['reason'],'integrity');self.assertNotIn('system',tampered)

    async def test_documents_of_a_run(self):
        for name,text in (('context','контекст хода'),('recap','рекап')):
            body=await (await self.client.get(f'/api/window/run/{RUN}/doc/{name}',headers=self.owner)).json()
            self.assertEqual(body['text'],text);self.assertFalse(body['truncated'])
        self.assertEqual((await self.client.get(f'/api/window/run/{RUN}/doc/manifest.json',headers=self.owner)).status,404)

if __name__=='__main__':unittest.main()
