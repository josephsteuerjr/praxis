"""Local selection and actual first-input cache; no provider/process spawning."""
from __future__ import annotations
import ast
import copy
import importlib.util
import json
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'helene/core'), str(ROOT/'praxis'), str(ROOT/'desk'), str(ROOT/'desk/localharness')]
import frame_continuation as fc


def row(i, text, outgoing=False):
    return {'id': f'event-{i}', 'source_id': f'local-{i}', 'actor': 'agent' if outgoing else 'owner',
            'direction': 'out' if outgoing else 'in', 'line': text, 'ts': i, 'meta': {}}


class Continuation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.rows = [row(1, 'old input'), row(2, 'accepted old answer', True), row(3, 'question')]
        self.source = {'chat':'window', 'source':str(self.root), 'generation':{'frontier':[]},
                       'cache_root':str(self.root/'cache'), 'rows':self.rows}
        self.ctx = types.SimpleNamespace(chat_id='window', room_chat_id='window', owner=True, owner_audience=True, is_dm=True)
        self.patch = patch.dict('os.environ', {'PRAXIS_FRAME_CONTINUATION':'on','PRAXIS_KEAT':'off','PRAXIS_KEAT_CAPTURE':'off'})
        self.patch.start(); self.addCleanup(self.patch.stop)

    def prepare(self, **changes):
        history, current = fc.dialogue(self.rows)
        self.messages = copy.deepcopy(history) + [{'role':'user','content':current+' [fresh evidence]'}]
        args = dict(ctx=self.ctx, history=history, user_msg=current, messages=self.messages,
                    system='stable system',tools=[{'name':'read'}],model_key='fixture',prefix_messages=[],
                    hands=False,render=lambda rows, **kwargs:copy.deepcopy(rows))
        args.update(changes)
        with fc.bind_source(self.source, lambda:copy.deepcopy(self.source)):
            return fc.prepare(**args)

    def observe(self, plan):
        with fc.bind(plan):
            fc.observe(system='stable system',messages=plan.messages,tools=[{'name':'read'}])

    def test_exact_prefix_no_private_tools_or_undelivered_draft(self):
        first = self.prepare(); saved = copy.deepcopy(first.messages)
        self.observe(first)
        first.messages += [{'role':'assistant','content':'PRIVATE TOOL TRACE / UNDELIVERED DRAFT'}]
        self.rows += [row(4,'accepted delivered answer',True),row(5,'next question')]
        second = self.prepare()
        self.assertEqual(second.messages[:len(saved)],saved)
        self.assertGreater(second.reused,0)
        self.assertIn('accepted delivered answer',json.dumps(second.messages))
        self.assertNotIn('PRIVATE TOOL TRACE',json.dumps(second.messages))
        self.observe(second)
        # Later iterations cannot overwrite the first checkpoint.
        self.assertEqual(len(fc._load(second.path)['messages']),len(second.messages))

    def test_repeated_large_evidence_never_resets_successful_first_anchor(self):
        previous=[]
        for turn in range(15):
            history,current=fc.dialogue(self.rows)
            normal=history+[{'role':'user','content':current+'\nMEMORY EVIDENCE '+('x'*100000)}]
            plan=self.prepare(messages=normal)
            self.assertEqual(plan.messages[:len(previous)],previous)
            self.assertIn('old input',fc._json(plan.messages))
            if turn:self.assertGreater(plan.reused,0)
            self.observe(plan)
            previous=copy.deepcopy(plan.messages)
            self.rows += [row(4+turn*2,'accepted '+str(turn),True),row(5+turn*2,'next '+str(turn))]
        self.assertGreater(len(fc._json(plan.messages)),2*len(fc._json(normal)))

    def test_edits_delete_reorder_frontier_and_audience_reset(self):
        first=self.prepare();self.observe(first)
        original=copy.deepcopy(self.rows)
        for mode in ('edit','delete','reorder','frontier','audience','system','tools','model'):
            self.rows[:]=copy.deepcopy(original)+[row(4,'answer',True),row(5,'next')]
            self.source['generation']={'frontier':[]};self.ctx.owner_audience=True
            options={}
            if mode=='edit': self.rows[0]['line']='edited'
            if mode=='delete': self.rows.pop(0)
            if mode=='reorder': self.rows[0],self.rows[1]=self.rows[1],self.rows[0]
            if mode=='frontier': self.source['generation']={'frontier':['fold']}
            if mode=='audience': self.ctx.owner_audience=False
            if mode=='system': options['system']='other'
            if mode=='tools': options['tools']=[]
            if mode=='model': options['model_key']='other'
            with self.subTest(mode=mode): self.assertEqual(self.prepare(**options).reused,0)

    def test_changed_before_first_send_and_stale_source_do_not_checkpoint(self):
        plan=self.prepare();plan.messages.append({'role':'user','content':'late inbox'})
        self.observe(plan);self.assertFalse(plan.path.exists())
        plan=self.prepare();self.rows[0]['line']='changed during call'
        self.observe(plan);self.assertFalse(plan.path.exists())

    def test_actual_fallback_provider_change_is_explicit_invalidation(self):
        first=self.prepare(expected_provider=['primary','model-A'])
        with fc.bind(first):
            fc.observe(system='stable system',messages=first.messages,tools=[{'name':'read'}],provider_route=['fallback','model-B'])
        self.rows += [row(4,'accepted fallback answer',True),row(5,'next')]
        second=self.prepare(expected_provider=['primary','model-A'])
        self.assertEqual(second.reason,'provider_route_changed')
        self.assertEqual(second.reused,0)

    def test_no_identity_media_and_shortened_history_decline(self):
        self.assertIsNone(self.prepare(user_msg=[]))
        self.assertIsNone(self.prepare(history=[]))
        self.rows[0].pop('id');self.rows[0].pop('source_id')
        self.assertIsNone(self.prepare())

    def test_real_runner_selection_matches_renderer_and_keeps_130_roles(self):
        import owner_context
        source=ast.parse((ROOT/'desk/localharness/runner.py').read_text(encoding='utf-8'))
        nodes=[node for node in source.body if isinstance(node,ast.FunctionDef) and node.name in {'_dialogue','_continuation_snapshot'}]
        self.rows[:]=[row(i+1,f'line-{i}',i%2==1) for i in range(130)]+[row(131,'question')]
        life=types.SimpleNamespace(hot_records=lambda *a:copy.deepcopy(self.rows),hot_bounds=lambda c:(100,150,200,200000),
            tape_chars_for=lambda c:600000,tape_window=lambda rows,n:rows,_load_state=lambda c:{'frontier':[]},helene_owner_context={})
        namespace={'Path':Path,'_life':life,'_tree':self.root,'_tail_lines':lambda *a:[], '_last_n':lambda c:200,
                   'log':logging.getLogger(),'transport':types.SimpleNamespace(is_room=lambda c:True)}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'runner.py','exec'),namespace)
        proof={};history,current=namespace['_dialogue']('window',continuation=proof)
        self.assertEqual(len(history),130)
        self.assertEqual(fc.dialogue(proof['rows']),(history,current))
        with fc.bind_source(proof,lambda:namespace['_continuation_snapshot']('window')):
            plan=fc.prepare(ctx=self.ctx,history=history,user_msg=current,messages=history+[{'role':'user','content':current}],
                system='stable system',tools=[{'name':'read'}],model_key='fixture',prefix_messages=[],hands=False,
                render=lambda rows,**kw:rows)
            self.assertEqual(plan.messages[0]['content'],'line-0')
            self.observe(plan)
        self.assertTrue(plan.path.is_file())


if __name__=='__main__':unittest.main()
