"""Language at actual memory/work helper producers, without rewriting canon."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import desires
import frame_language as fl
import graph
import people
import work_loop as wl

LANGS=('ru','en','fr','zh')
RAW='Authored Русский 中文 <value>'


class MemoryWorkLanguage(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name);self.cfg=self.base/'helene.json'
        self.enterContext(patch.dict(os.environ,HELENE_CONFIG=str(self.cfg),PRAXIS_WORK_LOOP='1',
                                     PRAXIS_WORK_CONTINUATIONS='5'))
        self.enterContext(patch.object(people,'PEOPLE_DIR',self.base/'people'))
        self.enterContext(patch.object(graph,'MEM_DIR',self.base/'memory'))
        self.enterContext(patch.object(graph,'GRAPH_MD',self.base/'memory/graph.md'))
        state=wl.snapshot();self.addCleanup(wl.restore,state);wl.reset()
        self.choose('ru','ru')

    def choose(self,ui,frame):
        self.cfg.write_text(json.dumps({'lang':ui,'agent':{'frame_language':frame}}),encoding='utf-8')

    def test_actual_graph_creation_read_and_forget_across16_axes(self):
        for ui in LANGS:
            for frame in LANGS:
                with self.subTest(ui=ui,frame=frame):
                    self.choose(ui,frame)
                    graph.GRAPH_MD.unlink(missing_ok=True)
                    answer=graph.add_edge('alpha','beta',RAW)
                    self.assertIn(RAW,answer)
                    saved=graph.GRAPH_MD.read_bytes()
                    self.assertIn(RAW.encode(),saved)
                    around=graph.neighbors_text('alpha')
                    self.assertIn(RAW,around)
                    self.assertEqual(saved,graph.GRAPH_MD.read_bytes())
                    self.assertIn('alpha',graph.path_text('alpha','beta'))
                    removed=graph.forget_connection('alpha','beta')
                    self.assertIn('alpha',removed)
                    self.assertEqual(graph.edges(),[])

    def test_existing_graph_link_does_not_claim_date_was_written(self):
        self.choose('fr','en')
        with patch.object(graph,'_find_edge',return_value={'fixture':True}), \
             patch.object(graph,'_touch_edge_date',return_value=False) as touch:
            result=graph.add_edge('alpha','beta',RAW)
        touch.assert_called_once()
        self.assertEqual(result,'Link alpha — beta already exists.')
        self.assertNotIn('updated',result)

    def test_canonical_dossier_parser_and_authored_file_remain_literal(self):
        body={people.WHO:RAW,people.NOW:RAW,people.CHARACTER:RAW,
              people.FACTS:'- '+RAW,people.LOOPS:'- [ ] '+RAW,people.ALIASES_KEY:RAW}
        people.write('fixture',RAW,body);path=people.path_for('fixture');saved=path.read_bytes()
        parsed=people.parse(saved.decode())
        self.assertEqual(parsed[1][people.ALIASES_KEY],RAW)
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame)
                portrait,loops=people.portrait_and_loops('fixture')
                self.assertIn(RAW,portrait);self.assertIn(RAW,loops)
                self.assertIn(fl.text('helper.people.section.who'),portrait)
                compact=people.compact_profile('fixture',max_chars=3000)
                self.assertIn(RAW,compact);self.assertIn('[legacy-unverified]',compact)
                clipped,meta=people.frame_view(saved.decode(),limit=20,path_name='fixture.md')
                self.assertTrue(meta['cut'])
                self.assertIn('memory/people/fixture.md',clipped)
                self.assertEqual(saved,path.read_bytes())
                self.assertEqual(people.parse(path.read_text()),parsed)

    def test_desire_projection_uses_language_but_ledger_fields_stay_canonical(self):
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame)
                ledger=desires.DesireLedger(self.base/(ui+'-'+frame))
                value=ledger.notice(RAW,source='run-fixture: observed result',
                    why_it_matters=RAW,next_move=RAW,evidence_refs=['run-fixture/events.jsonl'],
                    desire_id='desire-language')
                self.assertEqual(ledger.get('desire-language')['statement'],RAW)
                events=ledger.events();before=ledger.events_path.read_bytes()
                projection=ledger.projection_path.read_text()
                self.assertIn(fl.text('helper.desires._write_projection_locked.278'),projection)
                self.assertIn('statement:',projection);self.assertIn('status: latent',projection)
                self.assertIn(RAW,projection)
                ledger.rebuild_projection()
                self.assertEqual(projection,ledger.projection_path.read_text())
                self.assertEqual(before,ledger.events_path.read_bytes())
                self.assertEqual(events,ledger.events())
                with self.assertRaises(ValueError):ledger.choose('desire-language',note=RAW)
                self.assertEqual(before,ledger.events_path.read_bytes())

    def test_work_turn_policy_and_literals_do_not_depend_on_language(self):
        for ui in LANGS:
            for frame in LANGS:
                self.choose(ui,frame)
                intro=wl.announce('task_window')
                self.assertIn('task_control',intro);self.assertIn('done',intro);self.assertIn('5',intro)
                self.assertEqual(wl.announce('chat_turn'),'')
                self.assertTrue(wl.nudge(1,5))
                control={'action':'wait','wake_on':RAW,'summary':RAW}
                saved=copy.deepcopy(control)
                status,reason,detail=wl.closing(control,now=1000)
                self.assertEqual(status,'paused');self.assertIn(RAW,reason)
                self.assertEqual(detail['task_control'],control)
                self.assertEqual(detail['wake']['wake_on'],RAW)
                self.assertEqual(control,saved)
                self.assertIn(RAW,wl.mirror(RAW,('read_file',)))
                self.assertIn('read_file',wl.mirror(RAW,('read_file',)))
                self.assertIn(RAW,wl.chat_nudge(RAW,1,5))
                self.assertIn(RAW,wl.woken_note({'woken':{'wake_on':RAW,'parked_at':'UTC'}}))

    def test_loop_decision_codes_remain_canonical(self):
        self.choose('fr','zh')
        for action in ('done','blocked','wait'):
            status,reason,detail=wl.closing({'action':action,'summary':RAW},now=1000)
            self.assertEqual(detail['task_control']['action'],action)
            self.assertIn(RAW,reason)
        should,reason=wl.decide(kind='task_window',control={'action':'done'},spent=0)
        self.assertFalse(should);self.assertTrue(reason.startswith('work_loop:'))
