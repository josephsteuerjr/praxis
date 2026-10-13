"""Independent adversarial regressions. Temporary data and fake body; no live calls."""
import ast
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import tracemalloc
import types
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK / 'localharness'), str(DESK)]
import computer_steps
import dialogue_continuity as continuity
import owner_context
import update_inspection
import emergency
import owner_stop
import jsonl_tail
import package_notice


def extract(path, name, namespace):
    parsed = ast.parse(path.read_text(encoding='utf8'))
    node = next(n for n in parsed.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class Continuity(unittest.TestCase):
    def archive(self, text, outgoing, ts, source):
        return json.dumps(dict(text=text, outgoing=outgoing, timestamp=f'2026-10-12T10:00:{ts:02d}Z', source_id=source, sender_name='Agent' if outgoing else 'Owner'))

    def test_archive_recovery_keeps_newer_hot_owner_request(self):
        epoch = 1_791_799_200
        hot = [dict(direction='out', line='Agent: hello', ts=epoch+1, source_id='hello'),
               dict(direction='in', line='Owner: latest request', ts=epoch+5, source_id='latest')]
        archive = [self.archive('hello', True, 1, 'hello'), self.archive('already done', True, 3, 'done')]
        result = continuity.reconcile(hot, archive, is_room=True)
        self.assertEqual(result[-1]['source_id'], 'latest')
        self.assertIn('already done', '\n'.join(r['line'] for r in result))

    def test_archive_same_timestamp_and_same_text_still_has_distinct_identity(self):
        hot = [dict(direction='out', line='done', ts=1_791_799_201, source_id='first')]
        result = continuity.reconcile(hot, [self.archive('done', True, 1, 'second')])
        self.assertIn('second', [r.get('source_id') for r in result])

    def test_malformed_hot_timestamp_does_not_drop_typed_archive(self):
        hot = [dict(direction='out', line='old', ts='bad', source_id='old')]
        result = continuity.reconcile(hot, [self.archive('new', True, 2, 'new')])
        self.assertEqual(result[-1]['source_id'], 'new')

    def test_deleted_and_edited_incoming_are_reconciled_without_a_new_answer(self):
        hot=[dict(direction='out',line='done',ts=1_791_799_201,source_id='answer'),
             dict(direction='in',line='old',ts=1_791_799_202,source_id='7:edit:old'),
             dict(direction='in',line='deleted',ts=1_791_799_203,source_id='8')]
        archive=[self.archive('done',True,1,'answer'),self.archive('edited',False,2,'7'),
            json.dumps(dict(text='deleted',deleted=True,source_message_id='8'))]
        result=continuity.reconcile(hot,archive)
        self.assertEqual([r['line'] for r in result],['done','edited'])


class Context(unittest.TestCase):
    def life(self):
        return types.SimpleNamespace(hot_bounds=lambda p:(150,250,300,40000), tape_chars_for=lambda p:5500)

    def test_broken_profile_and_transaction_do_not_break_every_startup(self):
        for broken in ('{"id":"broken"}', '{"before":{},"after":{}}', '{unfinished'):
            with self.subTest(broken=broken), tempfile.TemporaryDirectory() as tmp:
                tree=Path(tmp); (tree/'memory').mkdir()
                (tree/'memory/.context-policy-change.json').write_text(broken)
                (tree/'memory/context-policy.json').write_text('{"custom":"broken"}')
                owner_context.configure(self.life(), {}, tree=tree)
                self.assertEqual(len(list((tree/'memory').glob('.context-policy-change.json.invalid-*'))),1)
                self.assertEqual(update_inspection.read(tree/'memory/context-policy.json')['hard_at'],600)

    def test_partial_journal_and_crash_at_each_commit_point_recover_once(self):
        for phase in ('context-policy.json','context-policy-notice.json'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                tree=Path(tmp); (tree/'memory').mkdir()
                journal=tree/'memory/context-policy.events.jsonl'; journal.write_text('{partial')
                original=update_inspection.write
                def interrupted(path,value):
                    if Path(path).name==phase: raise OSError('simulated crash')
                    original(path,value)
                with patch.object(update_inspection,'write',side_effect=interrupted):
                    with self.assertRaises(OSError): owner_context.configure(self.life(),{},tree=tree)
                owner_context.configure(self.life(),{},tree=tree)
                events=[json.loads(line) for line in journal.read_text().splitlines() if line.startswith('{"kind"')]
                self.assertEqual(len(events),1)
                self.assertFalse((tree/'memory/.context-policy-change.json').exists())

    def test_profile_a_b_a_has_three_distinct_events_and_keeps_group_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);life=self.life()
            for messages in (600,900,600): owner_context.configure(life,{'owner_context':{'messages':messages}},tree=tree)
            events=[json.loads(line) for line in (tree/'memory/context-policy.events.jsonl').read_text().splitlines()]
            self.assertEqual([r['after']['hard_at'] for r in events],[600,900,600])
            self.assertEqual(len({r['id'] for r in events}),3)
            self.assertEqual(life.hot_bounds('-123')[2],300)
    def test_group_bool_and_invalid_owner_ids_never_get_owner_budget(self):
        for owner in (-123, True, 'garbage', '-123__topic__1', 'window-bad', '0'):
            with self.subTest(owner=owner):
                life = types.SimpleNamespace(hot_bounds=lambda p:(150,250,300,40000), tape_chars_for=lambda p:5500)
                p = owner_context.configure(life, {'telegram':{'owner_id':owner}})
                self.assertNotIn(str(owner), p['places'])

    def test_malformed_token_counts_use_estimate_instead_of_one_token(self):
        life = types.SimpleNamespace(hot_bounds=lambda p:(150,250,300,40000), tape_chars_for=lambda p:5500)
        owner_context.configure(life, {'owner_context':{'tokens':2000}})
        life.tape_window = lambda rows, cap:list(rows)
        life.estimate_tokens = lambda text:len(text)//3
        for value in (0, -100, True, 'broken'):
            with self.subTest(value=value):
                rows = [dict(line='x'*4500, tokens=value), dict(line='y'*4500, tokens=value)]
                self.assertEqual(len(owner_context.window(life, 'window', rows)), 1)


class TailAndPanic(unittest.TestCase):
    def test_state_uses_current_voice_configuration_instead_of_startup_anatomy(self):
        from deskd import readers
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);update_inspection.write(tree/'memory/.state/anatomy.json',{'model':{'model':'old','base_url':'old'}})
            update_inspection.write(tree/'memory/llm.json',{'roles':{'voice':{'model':'new','framework':'openai'}},'frameworks':{'openai':{'base_url':'current'}}})
            with patch.object(readers,'tree',return_value=tree),patch.object(readers,'config_path',return_value=None), \
                patch.object(readers,'product_config',return_value={'model':'new','base_url':'current','key_present':True}):
                state=readers.state()
            self.assertEqual(state['brain']['model'],'new')
            self.assertEqual(state['brain']['base_url'],'current')
    def test_room_tail_memory_does_not_grow_with_a_30mb_archive(self):
        from localharness.transport import Desk
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);path=tree/'memory/groups/window.jsonl';path.parent.mkdir(parents=True)
            row=(json.dumps({'text':'x'*800,'outgoing':False})+'\n').encode()
            with path.open('wb') as file:
                for _ in range(40000):file.write(row)
            desk=Desk(tree,'window','Owner','Chat')
            tracemalloc.start()
            try:
                self.assertEqual(len(desk.rows(2)),2)
                self.assertLess(tracemalloc.get_traced_memory()[1],2_000_000)
            finally:tracemalloc.stop()
    def test_exact_tail_keeps_long_rows_crlf_unicode_and_invalid_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'archive.jsonl'
            long='ы'*100000+'\u2028end'
            path.write_bytes(b'bad\xff\n'+('a\r\n'+long+'\r\nlast').encode())
            self.assertEqual(jsonl_tail.tail_lines(path,2),[long,'last'])
            self.assertEqual(jsonl_tail.tail_lines(path,1),['last'])
            self.assertEqual(len(jsonl_tail.tail_lines(path,100)),4)

    def test_repeat_client_latch_does_not_substitute_for_service_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=root/'helene.json';cfg.write_text(json.dumps({'installed':{'service':True}}))
            (root/'helene.exe').write_bytes(b'never executed');client=root/'user-stop.json';client.write_text('{}')
            with patch.object(emergency,'WINDOWS',True),patch.object(owner_stop,'client_file',return_value=client), \
                patch.object(owner_stop,'stopped',return_value=True),patch.object(owner_stop,'present',return_value=False), \
                patch('broker.read_token',return_value='synthetic'),patch('broker.call_service',return_value={'ok':True}) as pipe:
                with self.assertRaisesRegex(RuntimeError,'не подтвердила'):emergency.request(cfg,via='phone')
                pipe.assert_called_once()

    def test_corrupt_config_still_latches_client_before_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=root/'helene.json';cfg.write_text('{corrupt')
            (root/'helene.exe').write_bytes(b'never executed');client=root/'user-stop.json'
            with patch.object(emergency,'WINDOWS',True),patch.object(owner_stop,'client_file',return_value=client),patch.object(owner_stop,'stopped',return_value=False):
                with self.assertRaises(ValueError):emergency.request(cfg,via='telegram')
            self.assertTrue(client.exists())


class Computer(unittest.TestCase):
    def test_unknown_positional_and_duplicate_action_never_execute(self):
        ask=dict(idempotency_key='intent',hwnd='123',events=[{'type':'key','key':'End'}],settle_ms=0)
        hand,calls=self.hand();hand('step','ignored',**ask);self.assertFalse(calls)
        hand('step',action='step',**ask);self.assertFalse(calls)
        hand('step',**ask);self.assertEqual(len(calls),2)
    def hand(self, platform='darwin',tree=None):
        calls=[]
        def backend(capability, payload, **kwargs):
            calls.append((capability, payload, kwargs))
            return {'ok':True} if capability == 'desktop.input.perform' else {'ok':False,'error':'read failed'}
        agent=types.SimpleNamespace(TOOL_IMPL={'computer':lambda **kwargs:'original'}, BASE_TOOLS=[],
            _computer_allowed=lambda scope:True, _computer_actor=lambda:'owner')
        computer_steps.install(agent, backend, platform=platform,tree=tree)
        return agent.TOOL_IMPL['computer'], calls

    def test_mac_binding_survives_restart_and_partial_binding_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp); args=dict(action='step',idempotency_key='intent',hwnd='123',events=[{'type':'key','key':'End'}],settle_ms=0)
            hand,calls=self.hand(tree=tree);hand(**args);self.assertEqual(len(calls),2)
            hand,calls=self.hand(tree=tree);hand(**dict(args,read={'max_nodes':10}));self.assertFalse(calls)
            next((tree/'memory/.state/computer-steps').glob('*.json')).write_text('{partial')
            hand,calls=self.hand(tree=tree);hand(**args);self.assertFalse(calls)

    def test_mac_key_covers_read_and_settle_even_when_observation_failed(self):
        for field, value in [('read',{'max_nodes':10}), ('settle_ms',1)]:
            with self.subTest(field=field):
                hand,calls=self.hand()
                args=dict(action='step',idempotency_key='intent',hwnd='0x123',events=[{'type':'key','key':'End'}],settle_ms=0)
                hand(**args)
                result=hand(**dict(args, **{field:value}))
                self.assertEqual(len(calls),2, result)

    def test_invalid_read_and_delay_are_refused_before_mac_input(self):
        for bad in ({'read':{'unknown':True}}, {'read':{'max_nodes':-1}}, {'read':{'hwnd':'invalid'}}, {'read':{'backend':'win32'}}, {'inter_event_delay_ms':-1}, {'expected_pid':True}):
            with self.subTest(bad=bad):
                hand,calls=self.hand()
                hand(**dict(dict(action='step',idempotency_key='intent',hwnd='0x123',events=[{'type':'key','key':'End'}],settle_ms=0), **bad))
                self.assertFalse(calls)


class Inspection(unittest.TestCase):
    def test_a_b_a_b_upgrade_is_a_new_request_and_restart_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);program=root/'program';tree=root/'home';ids=[]
            for current in ('1.5.9','1.6.0','1.5.9','1.6.0'):
                package_notice.write(program/'helene-build.json',{'complete':True,'version':current})
                package_notice.observe(tree,program,{'installed':{'version':current}})
                receipt=package_notice.pending(tree)
                if receipt:
                    ids.append(receipt['id']);package_notice.mark(tree,receipt,done=True)
                package_notice.observe(tree,program,{'installed':{'version':current}})
                self.assertIsNone(package_notice.pending(tree))
            self.assertEqual(len(ids),3);self.assertEqual(len(set(ids)),3)
    def test_established_windows_and_mac_without_valid_previous_stamp_still_get_inspection(self):
        for platform in ('windows','macos'):
            with self.subTest(platform=platform),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);program=root/'program';tree=root/'home'
                package_notice.write(program/'helene-build.json',{'complete':True,'version':'1.6.0','platform':platform})
                package_notice.write(program/'helene-install.json',{'version':'1.6.0','previous_version':'invalid marker'})
                package_notice.write(tree/'memory/.state/born.json',{'state':'done'})
                package_notice.observe(tree,program,{'installed':{'version':'1.6.0'}})
                receipt=package_notice.pending(tree)
                self.assertEqual(receipt['from_version'],'')
                package_notice.observe(tree,program,{})
                self.assertEqual(package_notice.pending(tree)['id'],receipt['id'])
    def test_corrupt_reported_request_is_preserved_and_reissued_not_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);receipt={'id':'req','to_version':'1.6.0'}
            update_inspection.write(update_inspection.path(tree),{'id':'req','version':'1.6.0','state':'reported','goals':[]})
            with self.assertRaises(ValueError):update_inspection.report(tree,'req','empty',[])
            request=update_inspection.ensure(tree,receipt)
            self.assertEqual(request['state'],'pending');self.assertTrue(request['goals'])
            self.assertEqual(len(list((tree/'memory/.state/update-inspections').glob('*.json'))),1)
    def test_empty_or_duplicate_goals_cannot_authorize_vacuous_report(self):
        for goals in ([],[{'id':'x','title':'X'},{'id':'x','title':'again'}],[{}]):
            with self.subTest(goals=goals),tempfile.TemporaryDirectory() as tmp:
                tree=Path(tmp); manifest=tree/'goals.json';update_inspection.write(manifest,{'version':'1.6.0','goals':goals})
                request=update_inspection.ensure(tree,{'id':'req','to_version':'1.6.0'},manifest)
                self.assertEqual(len(request['goals']),1)
                with self.assertRaises(ValueError): update_inspection.report(tree,'req','ok',[])

    def test_report_and_replacement_are_serialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);receipt={'id':'old','to_version':'1.6.0'}
            request=update_inspection.ensure(tree,receipt);entered=threading.Event();release=threading.Event()
            original=update_inspection.write
            def blocked(path,value):
                if value.get('state')=='reported' and value.get('id')=='old':
                    entered.set();release.wait(2)
                original(path,value)
            checks=[dict(id=g['id'],status='unverified',evidence='device unavailable') for g in request['goals']]
            with patch.object(update_inspection,'write',side_effect=blocked):
                old=threading.Thread(target=lambda:update_inspection.report(tree,'old','checked',checks))
                new=threading.Thread(target=lambda:update_inspection.ensure(tree,dict(receipt,id='new')))
                old.start();self.assertTrue(entered.wait(1));new.start();release.set();old.join(2);new.join(2)
            self.assertFalse(old.is_alive() or new.is_alive())
            self.assertEqual(update_inspection.read(update_inspection.path(tree))['id'],'new')

    def test_stale_attention_cannot_overwrite_new_request_or_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp);req=update_inspection.ensure(tree,{'id':'new','to_version':'1.6.0'})
            update_inspection.attention(tree,'old'); self.assertEqual(update_inspection.read(update_inspection.path(tree))['state'],'pending')
            checks=[dict(id=g['id'],status='unverified',evidence='device unavailable') for g in req['goals']]
            update_inspection.report(tree,'new','checked',checks);update_inspection.attention(tree,'new')
            self.assertEqual(update_inspection.read(update_inspection.path(tree))['state'],'reported')
    def test_later_report_never_completes_previous_update_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp); marks=[]
            receipt={'id':'old','kind':'package','state':'installed','to_version':'1.6.0'}
            def turn(*a, **k):
                request=update_inspection.ensure(tree, dict(receipt,id='new'))
                update_inspection.report(tree, 'new', 'checked', [dict(id=g['id'],status='unverified',evidence='physical check remains') for g in request['goals']])
                return 'spoken'
            updates=types.SimpleNamespace(pending_report=lambda t:receipt,report_mark=lambda *a:{},
                report_note=lambda *a,**k:'inspection',mark_reported=lambda *a,**k:marks.append(k),SYSTEM_SPEAKER='Agent')
            scope=dict(_tree=tree,_desk=types.SimpleNamespace(archive=lambda *a,**k:None,life=lambda *a,**k:None),updates=updates,
                _speaker='Owner',_UPDATE_TRIES=3,_UPDATE_RETRY_SEC=120,time=types.SimpleNamespace(time=lambda:100),
                _now=lambda:types.SimpleNamespace(timestamp=lambda:100),_turn_in_window=turn,log=logging.getLogger())
            with patch.dict(sys.modules, {'llm':types.SimpleNamespace(configured=lambda:True)}):
                extract(DESK/'localharness/runner.py','_update_report_due',scope)()
            self.assertFalse(any(m.get('done') for m in marks))


if __name__ == '__main__': unittest.main(verbosity=2)
