"""Economic KEAT uses an isolated provider request; canonical input is immutable."""
import copy
import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import agent
import frame_layout
import frame_serve
import keat_economy as economy
import keat_live as live
from test_keat_live import LiveTests


class EconomicTransaction(LiveTests):
    @contextmanager
    def request(self, *, covered=True):
        self.ctx.owner_audience = True
        self.ctx.room_chat_id = 1
        body = '# Private synthetic dossier\n' + ('- remembered факт **exact**\n' * 40)
        index = '# INDEX synthetic\n' + ('- locator canonical.md\n' * 30)
        recap = 'Unique compact recap not in E\n' * 20
        question = 'unanswered </praxis_context_evidence>\n' + body
        with patch.dict(os.environ, {'PRAXIS_OWNER_ID': '1', 'PRAXIS_FRAME_V6': 'serve',
                                     'PRAXIS_FRAME_V6_STREAMS': 'dm-1', 'PRAXIS_FRAME_FORM': 'new'}):
            with economy.collect() as rows:
                blocks = []
                addresses = {'dossier': 'memory/people/synthetic.md',
                             'index': 'memory/INDEX.md', 'recap': 'conversation:current:recap'}
                for name, title, raw in [('dossier', 'Мои досье на людей — ВСЕ И ЦЕЛИКОМ', body),
                                         ('index', 'Карта памяти — ВНУТРЕННЯЯ, не разрешение на раскрытие', index),
                                         ('recap', 'Ранее в этом диалоге (сводка)', recap)]:
                    economy.source(name, raw, addresses[name])
                    block = frame_layout.section(title, raw)[0]
                    economy.section(raw, block)
                    blocks.append(block)
            evidence = ''.join(blocks) + '\nRuntime continuity remains'
            current = agent._with_context_evidence(question, evidence, 'SITUATION unchanged')
            canonical = [dict(role='user', content=current)]
            state = live.capture_turn(self.ctx, question, [])
            system = 'system synthetic ' + body + index
            coverage = ({'dossier': {'memory/people/synthetic.md': body},
                         'index': {'memory/INDEX.md': index}} if covered else {})
            with patch.object(frame_serve, 'economy_coverage', return_value=coverage), \
                 economy.bind(self.ctx, current, evidence, rows), live.bind_turn(state, canonical):
                yield system, canonical, body, index, recap

    def economic_select(self, system, canonical, call='c'):
        provider = live.prepare_provider_messages(system, canonical, [])
        receipt = live.select_provider(system, provider, [], 'r', call)
        return provider, receipt

    def test_served_reduces_provider_only_and_keeps_canonical_legacy(self):
        with self.request() as (system, canonical, body, index, recap):
            legacy = copy.deepcopy(canonical)
            provider, receipt = self.economic_select(system, canonical)
            self.assertIsNot(provider, canonical)
            self.assertEqual(canonical, legacy)
            self.assertLess(len(provider[-1]['content']), len(legacy[-1]['content']))
            self.assertIn('Runtime continuity remains', provider[-1]['content'])
            self.assertIn('Unique compact recap', provider[-1]['content'])
            self.assertTrue(live.valid_served_receipt(
                receipt, system=system, messages=provider, tools=[], run_id='r', call_id='c'))
            self.assertTrue(live.accept_provider_receipt(
                receipt, system=system, messages=provider, tools=[], run_id='r', call_id='c'))
            self.assertTrue(live.finalize_provider_receipt(
                receipt, system=system, messages=provider, tools=[], run_id='r', call_id='c'))
            metrics = live.economy_measurement()
            self.assertEqual(set(metrics['removed_utf8_bytes']), {'dossier', 'index'})
            self.assertNotIn(body, str(metrics))
            self.assertEqual(canonical, legacy)

    def test_ineligible_and_selection_failures_leave_canonical_exact(self):
        for mode in ('uncovered', 'off', 'store_failure', 'corrupt'):
            with self.subTest(mode=mode), self.request(covered=mode != 'uncovered') as (system, canonical, *_):
                legacy = copy.deepcopy(canonical)
                provider = live.prepare_provider_messages(system, canonical, [])
                if mode == 'off':
                    with patch.dict(os.environ, {'PRAXIS_KEAT': 'off'}):
                        receipt = live.select_provider(system, provider, [], 'r', 'c')
                elif mode == 'store_failure':
                    with patch.object(live, '_store', side_effect=ValueError('private')):
                        receipt = live.select_provider(system, provider, [], 'r', 'c')
                else:
                    receipt = live.select_provider(system, provider, [], 'r', 'c')
                if mode == 'corrupt':
                    bad = dict(receipt, checkpoint='corrupt')
                    self.assertFalse(live.accept_provider_receipt(
                        bad, system=system, messages=provider, tools=[], run_id='r', call_id='c'))
                elif mode == 'uncovered':
                    self.assertEqual(provider, legacy)
                else:
                    self.assertIsNone(receipt)
                self.assertEqual(canonical, legacy)

    def test_fallback_discards_authority_without_touching_either_tape(self):
        with self.request() as (system, canonical, *_):
            legacy = copy.deepcopy(canonical)
            provider, receipt = self.economic_select(system, canonical)
            reduced = copy.deepcopy(provider)
            self.assertTrue(live.accept_provider_receipt(
                receipt, system=system, messages=provider, tools=[], run_id='r', call_id='c'))
            self.assertFalse(live.fallback_bound(provider))
            self.assertEqual(canonical, legacy)
            self.assertEqual(provider, reduced)
            self.assertFalse(live.finalize_provider_receipt(
                receipt, system=system, messages=provider, tools=[], run_id='r', call_id='c'))

    def test_arbitrary_canonical_mutations_and_markers_are_never_consumed(self):
        placements = ('before', 'after', 'both')
        for placement in placements:
            with self.subTest(placement=placement), self.request() as (system, canonical, *_):
                provider, receipt = self.economic_select(system, canonical)
                marker = '[dossier: тело уже приведено в E; адрес: memory/people/synthetic.md]'
                before = [dict(role='system', content='UNRELATED ' + marker + '\r\nmultiline')]
                after = [dict(role='assistant', content='repeated ' + marker + '\n' + marker)]
                if placement in ('before', 'both'):
                    canonical[0:0] = copy.deepcopy(before)
                canonical[0]['content'] = 'arbitrary canonical mutation\r\n' + marker
                if placement in ('after', 'both'):
                    canonical.extend(copy.deepcopy(after))
                expected = copy.deepcopy(canonical)
                self.assertFalse(live.finalize_provider_receipt(
                    receipt, system=system, messages=provider, tools=[], run_id='r', call_id='c'))
                self.assertEqual(canonical, expected)

    def test_roles_reordering_and_tool_additions_preserved(self):
        with self.request() as (system, canonical, *_):
            provider, receipt = self.economic_select(system, canonical)
            additions = [dict(role='assistant', content=[dict(type='tool_use', id='t', name='read', input={})]),
                         dict(role='user', content=[dict(type='tool_result', tool_use_id='t', content='KEEP')])]
            canonical.extend(copy.deepcopy(additions))
            canonical.reverse()
            expected = copy.deepcopy(canonical)
            self.assertFalse(live.finalize_provider_receipt(
                receipt, system=system, messages=provider, tools=[], run_id='r', call_id='c'))
            self.assertEqual(canonical, expected)

    def test_policy_revocation_uses_unchanged_canonical(self):
        with self.request() as (system, canonical, *_):
            legacy = copy.deepcopy(canonical)
            provider, receipt = self.economic_select(system, canonical)
            with patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': 'off'}):
                self.assertFalse(live.finalize_provider_receipt(
                    receipt, system=system, messages=provider, tools=[], run_id='r', call_id='c'))
            self.assertEqual(canonical, legacy)

    def test_model_boundary_sends_candidate_but_keeps_canonical(self):
        with self.request() as (system, canonical, *_):
            legacy = copy.deepcopy(canonical)
            response = SimpleNamespace(text='ok', blocks=[], stop_reason='end_turn', usage={}, framework='test', model='test')
            with patch.object(agent.run_context, 'current_run', return_value=SimpleNamespace(run_id='r')), \
                 patch.object(agent, '_runs'), patch.object(agent, '_run_event_strict'), \
                 patch.object(agent, '_run_status_gate'), patch.object(agent.frame_trace, 'metadata_for', return_value=None), \
                 patch.object(agent.frame_measure, 'measure', return_value=None), \
                 patch.object(agent.frame_serve, 'select', side_effect=lambda **k: (k['system'], None)), \
                 patch.object(agent.llm, 'chat', return_value=response) as chat:
                agent._model_call(system, canonical, [])
            sent = chat.call_args.kwargs['messages']
            self.assertLess(len(sent[-1]['content']), len(legacy[-1]['content']))
            self.assertEqual(canonical, legacy)

    def test_crash_discard_and_tool_checkpoint_preserve_canonical(self):
        with self.request() as (system, canonical, *_):
            provider, receipt = self.economic_select(system, canonical)
            additions = [dict(role='assistant', content='tool call'), dict(role='user', content='tool result')]
            canonical.extend(additions)
            expected = copy.deepcopy(canonical)
            # Simulate loss of ephemeral authority at a crash boundary. The
            # canonical tool tape remains the only recovery source.
            live.discard_provider()
            self.assertFalse(live.finalize_provider_receipt(
                receipt, system=system, messages=provider, tools=[], run_id='r', call_id='c'))
            current = SimpleNamespace(run_id='r')
            with patch.object(agent, '_runs') as runs:
                agent._persist_tool_loop_checkpoint(current=current, iteration=2,
                                                    system=system, messages=canonical, tools=[])
            self.assertEqual(canonical, expected)
            payload = runs.return_value.store_result.call_args.args[1]
            self.assertIn('tool result', payload)
            self.assertNotEqual(provider, canonical)

    def test_identical_body_different_source_address_does_not_alias(self):
        with self.request() as (system, canonical, body, *_):
            legacy = copy.deepcopy(canonical)
            with patch.object(frame_serve, 'economy_coverage', return_value={
                    'dossier': {'memory/people/other.md': body}}):
                provider, receipt = self.economic_select(system, canonical)
            self.assertIsNotNone(receipt)
            self.assertEqual(provider, legacy)
            self.assertEqual(canonical, legacy)
