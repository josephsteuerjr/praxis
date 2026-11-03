"""Actual composed voice -> model-call -> provider adapter with an offline SDK.

Each turn is a fresh native interpreter over one private synthetic profile. No
model, Telegram account or installed Jarvis is contacted. The SDK captures final
provider messages, not merely helper output; its usage values are synthetic.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def row(i, text, outgoing=False):
    return {'id': 'event-' + str(i), 'source_id': 'source-' + str(i),
            'actor': 'agent' if outgoing else 'person', 'direction': 'out' if outgoing else 'in',
            'line': text, 'ts': i, 'meta': {}}


def consumer(profile, input_path, output_path):
    # Must precede importing the edition: imports retain BASE and journal roots.
    os.environ.update(PRAXIS_BASE=str(profile), PRAXIS_FRAME_CONTINUATION='on',
                      PRAXIS_KEAT='off', PRAXIS_KEAT_CAPTURE='off',
                      PRAXIS_FRAME_SHADOW='off', PRAXIS_FRAME_MEASURE='off',
                      PRAXIS_FRAME_SERVE='off')
    sys.path[:0] = [str(ROOT/'helene/core'), str(ROOT/'praxis'), str(ROOT/'desk/localharness'), str(ROOT/'desk')]
    import types
    from contextlib import ExitStack
    from unittest.mock import patch
    import agent
    import frame_continuation as fc
    import frame_layout
    import frame_dossiers
    import llm
    value = json.loads(Path(input_path).read_text(encoding='utf-8'))
    llm.CONFIG_PATH = Path(profile)/'llm.json'
    llm.JOURNAL_DIR = Path(profile)/'journal'
    llm._CACHE.update(mtime=None, cfg=None)
    if not llm.CONFIG_PATH.exists():
        config = llm._from_env()
        config['frameworks']['anthropic']['api_key'] = 'synthetic-offline-key'
        config['roles']['voice'].update(framework='anthropic', model='synthetic-offline-model')
        llm.save_config(config)
    calls = []
    def create(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if value.get('with_tool_iteration') and len(calls) == 1:
            return types.SimpleNamespace(stop_reason='tool_use', model='synthetic-offline-model',
                content=[types.SimpleNamespace(type='tool_use', id='synthetic-call', name='synthetic_read_only_probe', input={})],
                usage=types.SimpleNamespace(input_tokens=7, output_tokens=2))
        return types.SimpleNamespace(stop_reason='end_turn', model='synthetic-offline-model',
            content=[types.SimpleNamespace(type='text', text='offline accepted candidate')],
            usage=types.SimpleNamespace(input_tokens=7, output_tokens=2))
    llm.use_test_client(types.SimpleNamespace(messages=types.SimpleNamespace(create=create)), 'anthropic')
    source = {'chat': value['chat'], 'source': str(profile), 'generation': value.get('generation', {'frontier': []}),
              'cache_root': str(Path(profile)/'cache'), 'rows': value['rows'],
              'admission': value.get('admission', {'owner': 'installation-1'})}
    history, current = fc.dialogue(source['rows'])
    ctx = agent.ChannelContext(chat_id=value['chat'], is_dm=value.get('is_dm', True),
                               owner=value.get('owner', True), known=True,
                               principal_id='1', addressed=True)
    receipts = []
    def build(*args, **kwargs):
        frame_layout.begin()
        for name, body in value.get('dossiers', []):
            frame_dossiers.collect(name, body)
        # Varying current evidence belongs after, never instead of, old FIRST.
        return 'stable persona', 'stable contract', value.get('evidence', 'new evidence')
    original_model = agent._model_call_impl
    def observed(*args, **kwargs):
        receipts.append(fc.receipt())
        return original_model(*args, **kwargs)
    with ExitStack() as stack:
        if value.get('full_builder'):
            # Keep the production builder; replace only its external/live
            # source observations with synthetic deterministic readings.
            stack.enter_context(patch.object(agent, '_persona_text', return_value='synthetic persona'))
            stack.enter_context(patch.object(agent, 'build_state_block', return_value=value.get('evidence', 'state')))
            stack.enter_context(patch.object(agent, 'build_state_evidence_block', return_value=''))
            stack.enter_context(patch.object(agent, '_active_desires_block', return_value=''))
        else:
            stack.enter_context(patch.object(agent, '_build_prompt_parts', build))
        stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('network forbidden in offline consumer')))
        stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('network forbidden in offline consumer')))
        stack.enter_context(patch.object(agent, '_model_call_impl', observed))
        stack.enter_context(patch.object(agent, '_work_loop_continue', return_value=False))
        stack.enter_context(patch.object(agent, '_arm_private_floor', return_value=0))
        if value.get('with_tool_iteration'):
            stack.enter_context(patch.dict(agent.TOOL_IMPL, {'synthetic_read_only_probe': lambda: 'synthetic observed result'}))
        stack.enter_context(fc.bind_source(source, lambda: copy.deepcopy(source)))
        run_material = ''
        if value.get('envelope') or value.get('runner'):
            from run_manager import RunManager
            manager = RunManager(Path(profile).resolve())
            stack.enter_context(patch.object(agent, '_runs', return_value=manager))
            stack.enter_context(patch.object(agent.work_loop, 'reply_hand_enabled', return_value=True))
            stack.enter_context(patch.object(agent, '_pre_shell_dirt', return_value=set()))
            stack.enter_context(patch.object(agent, '_autocommit_self_edit'))
            if value.get('runner'):
                import runner
                import memory_life
                for item in value['rows']:
                    memory_life.record_message(value['chat'], item['line'], actor=item['actor'],
                        direction=item['direction'], source='window', source_id=item['source_id'],
                        is_dm=value.get('is_dm', True), ts=item['ts'], dedupe_key='fixture:' + item['source_id'])
                stack.enter_context(patch.multiple(runner, _tree=Path(profile), _agent=agent,
                                                   _life=memory_life, _bot=None, _continuity=None))
                stack.enter_context(patch.object(runner, '_orient', return_value='synthetic runtime'))
                stack.enter_context(patch.object(runner, '_ext_hook'))
                envelope = runner._run_turn(value['chat'], '\n'.join(r['line'] for r in value['rows']), 'Synthetic person', ctx)
                source = runner._continuation_snapshot(value['chat'])
            else:
                envelope = agent.voice_turn_envelope(value['chat'], '\n'.join(r['line'] for r in value['rows']),
                    'Synthetic person', ctx=ctx, history=history, current_text=current)
            if envelope.failed or envelope.deferred:
                raise AssertionError('actual envelope did not complete its offline model phase')
            answer = envelope.text
            folder = manager.path(envelope.run_id)
            run_material = '\n'.join(p.read_text(encoding='utf-8') for p in folder.rglob('*')
                                      if p.is_file() and p.suffix in {'.md', '.json', '.jsonl'})
        else:
            override = ([{'name': 'synthetic_read_only_probe', 'description': 'Offline test probe',
                          'input_schema': {'type': 'object', 'properties': {}}}]
                        if value.get('with_tool_iteration') else [])
            answer = agent._voice_impl(current, history, 'Synthetic person', ctx=ctx,
                                       tools_override=override, no_tools=True,
                                       max_iters=2 if value.get('with_tool_iteration') else 1)
    checkpoint = fc._load(Path(source['cache_root'])/(fc._sha(source['chat'])+'.json'))
    Path(output_path).write_text(json.dumps({'calls': calls, 'checkpoint': checkpoint,
                                          'answer': answer, 'receipts': receipts,
                                          'run_material': run_material}, ensure_ascii=False), encoding='utf-8')


class ActualCalls(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='helene-context-230-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = self.root/'profile'; self.profile.mkdir()
        self.rows = [row(1, 'first source text')]
        self.counter = 0

    def call(self, **changes):
        self.counter += 1
        value = {'chat': 'window', 'rows': copy.deepcopy(self.rows)}
        value.update(changes)
        incoming, outgoing = self.root/f'in-{self.counter}.json', self.root/f'out-{self.counter}.json'
        incoming.write_text(json.dumps(value), encoding='utf-8')
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--consumer',
                                 str(self.profile), str(incoming), str(outgoing)],
                                cwd=ROOT, capture_output=True, encoding='utf-8', errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = json.loads(outgoing.read_text(encoding='utf-8'))
        self.assertEqual(len(result['calls']), 2 if value.get('with_tool_iteration') else 1)
        self.assertIsNotNone(result['checkpoint'], result)
        return result

    def test_three_actual_provider_requests_keep_entire_first_input_across_restart(self):
        first = self.call(evidence='FIRST PRIVATE EVIDENCE '+('x'*120000))
        self.rows += [row(2, 'first accepted canonical answer', True), row(3, 'second input')]
        second = self.call(evidence='second current evidence')
        self.rows += [row(4, 'second accepted canonical answer', True), row(5, 'third input')]
        third = self.call(evidence='third current evidence')
        for old, new in ((first, second), (second, third)):
            old_wire, new_wire = old['calls'][0], new['calls'][0]
            self.assertEqual(new_wire['messages'][:len(old_wire['messages'])], old_wire['messages'])
            self.assertEqual(old_wire['system'], new_wire['system'])
            self.assertEqual(new['checkpoint']['messages'][:len(old['checkpoint']['messages'])], old['checkpoint']['messages'])
            self.assertGreater(new['receipts'][0]['reused_messages'], 0)
        self.assertNotIn('tool_result', json.dumps(third['checkpoint']))

    def test_actual_runner_source_binding_reaches_actual_envelope_and_provider(self):
        first = self.call(runner=True)
        self.rows += [row(2, 'accepted actual runner answer', True), row(3, 'next actual runner input')]
        second = self.call(runner=True)
        self.assertEqual(second['calls'][0]['messages'][:len(first['calls'][0]['messages'])], first['calls'][0]['messages'])
        self.assertGreater(second['receipts'][0]['reused_messages'], 0)

    def test_actual_envelope_keeps_the_first_turn_and_re_admits_durable_origin(self):
        self.rows[0]['line'] = 'PRIVATE-ORIGIN /private/origin-ref'
        first = self.call(envelope=True)
        self.rows += [row(2, 'accepted answer', True), row(3, 'next input')]
        second = self.call(envelope=True)
        self.assertEqual(second['calls'][0]['messages'][:len(first['calls'][0]['messages'])], first['calls'][0]['messages'])
        self.rows += [row(4, 'public input')]
        public = self.call(envelope=True, owner=False, is_dm=False)
        self.assertNotIn('PRIVATE-ORIGIN', json.dumps(public['calls']))
        self.assertNotIn('PRIVATE-ORIGIN', public['run_material'])
        self.assertNotIn('/private/origin-ref', public['run_material'])

    def test_real_builder_moves_volatile_owner_state_to_append_only_current_packet(self):
        first = self.call(full_builder=True, evidence='STATE-ONE')
        self.rows += [row(2, 'accepted answer', True), row(3, 'next question')]
        second = self.call(full_builder=True, evidence='STATE-TWO')
        old, new = first['calls'][0], second['calls'][0]
        self.assertEqual(old['system'], new['system'])
        self.assertEqual(new['messages'][:len(old['messages'])], old['messages'])
        self.assertIn('STATE-ONE', json.dumps(new['messages']))
        self.assertIn('STATE-TWO', json.dumps(new['messages']))
        self.assertGreater(second['receipts'][0]['reused_messages'], 0)

    def test_telegram_dm_and_topic_use_the_same_actual_append_contract(self):
        for chat, is_dm in [('12345', True), ('-100123__topic__7', False)]:
            with self.subTest(chat=chat):
                self.rows = [row(1, 'first source text')]
                first = self.call(chat=chat, is_dm=is_dm)
                self.rows += [row(2, 'accepted one', True), row(3, 'second')]
                second = self.call(chat=chat, is_dm=is_dm)
                self.rows += [row(4, 'accepted two', True), row(5, 'third')]
                third = self.call(chat=chat, is_dm=is_dm)
                for old, new in ((first, second), (second, third)):
                    self.assertEqual(new['calls'][0]['messages'][:len(old['calls'][0]['messages'])], old['calls'][0]['messages'])
                    self.assertGreater(new['receipts'][0]['reused_messages'], 0)

    def test_real_tool_iteration_does_not_replace_first_checkpoint(self):
        first = self.call(with_tool_iteration=True)
        self.assertNotIn('tool_result', json.dumps(first['checkpoint']['messages']))
        self.assertIn('tool_result', json.dumps(first['calls'][1]['messages']))
        self.rows += [row(2, 'accepted canonical answer', True), row(3, 'new turn')]
        second = self.call(with_tool_iteration=True)
        self.assertEqual(second['calls'][0]['messages'][:len(first['calls'][0]['messages'])], first['calls'][0]['messages'])
        self.assertNotIn('tool_result', json.dumps(second['calls'][0]['messages']))

    def test_silent_or_failed_delivery_keeps_first_input_without_inventing_assistant(self):
        first = self.call(evidence='first evidence')
        self.rows += [row(2, 'new input after no accepted reply')]
        second = self.call(evidence='fresh evidence')
        self.assertEqual(second['calls'][0]['messages'][:len(first['calls'][0]['messages'])], first['calls'][0]['messages'])
        self.assertEqual(json.dumps(second['calls']).count('first source text'), 1)
        self.assertNotIn('assistant', [m['role'] for m in second['checkpoint']['messages']])
        self.assertGreater(second['receipts'][0]['reused_messages'], 0)

    def test_deleted_dossier_revokes_old_prefix_but_keeps_private_snapshot_evidence(self):
        source = self.profile/'memory/people/a.md'
        source.parent.mkdir(parents=True); source.write_text('source dossier', encoding='utf-8')
        self.call(dossiers=[['memory/people/a.md', 'DELETED-DOSSIER snapshot']])
        source.unlink()
        self.rows += [row(2, 'accepted', True), row(3, 'next')]
        second = self.call()
        self.assertNotIn('DELETED-DOSSIER', json.dumps(second['calls']))
        self.assertIn('dossier_revocations', second['receipts'][0]['status'])
        self.assertTrue(any('DELETED-DOSSIER' in p.read_text(encoding='utf-8') for p in (self.profile/'cache').glob('*.dossiers.*.json')))

    def test_restart_into_other_audience_re_admits_no_old_private_prefix_or_locator(self):
        self.rows[0]['line'] = 'PRIVATE-RAW /private/message-ref'
        first = self.call(evidence='PRIVATE-NOTE /private/result-ref', dossiers=[['memory/people/a.md', 'PRIVATE-DOSSIER /private/dossier-ref']])
        private_reply = row(2, 'PRIVATE-ANSWER /private/outgoing-ref', True)
        private_reply['meta'] = {'source_audience': {'chat': 'window', 'is_dm': True, 'owner': True}}
        self.rows += [private_reply, row(3, 'public input')]
        public = self.call(owner=False, is_dm=False, evidence='public admitted evidence')
        wire = json.dumps(public['calls'], ensure_ascii=False)
        self.assertNotIn('PRIVATE-NOTE', wire)
        self.assertNotIn('PRIVATE-RAW', wire)
        self.assertNotIn('PRIVATE-ANSWER', wire)
        self.assertNotIn('PRIVATE-DOSSIER', wire)
        self.assertNotIn('/private/', wire)
        self.assertIn('binding_changed:', public['receipts'][0]['status'])
        self.assertEqual(public['receipts'][0]['reused_messages'], 0)

    def test_source_edit_delete_fold_and_rights_invalidate_after_restart(self):
        self.call(evidence='old derived anchor')
        self.rows += [row(2, 'accepted', True), row(3, 'new')]
        edited = copy.deepcopy(self.rows); edited[0]['line'] = 'edited source'
        result = self.call(rows=edited)
        self.assertNotIn('old derived anchor', json.dumps(result['calls']))
        self.assertEqual(result['receipts'][0]['status'], 'source_edited_deleted_or_reordered')
        result = self.call(rows=edited[1:], generation={'frontier': ['authored-fold-1']})
        self.assertEqual(result['receipts'][0]['status'], 'source_generation_changed')
        result = self.call(rows=edited[1:]+[row(4, 'new owner admitted input')], generation={'frontier': ['authored-fold-1']}, admission={'owner': 'replacement-owner'})
        self.assertIn('admission', result['receipts'][0]['status'])

    def test_dossier_versions_survive_recent_roster_and_restart(self):
        self.call(dossiers=[['memory/people/a.md', 'admitted version one']])
        self.rows += [row(2, 'accepted', True), row(3, 'next')]
        second = self.call()
        self.assertEqual(json.dumps(second['calls']).count('admitted version one'), 1)
        self.rows += [row(4, 'accepted again', True), row(5, 'third')]
        third = self.call(dossiers=[['memory/people/a.md', 'admitted version two']])
        text = json.dumps(third['calls'])
        self.assertEqual(text.count('admitted version one'), 1)
        self.assertEqual(text.count('admitted version two'), 1)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--consumer':
        consumer(*sys.argv[2:])
    else:
        unittest.main()
