"""Regressions at the actual provider boundary for isolated KEAT requests."""
import copy
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import agent
import keat_live as live
import test_keat_live


class ProviderRollbackTests(unittest.TestCase):
    setUp = test_keat_live.LiveTests.setUp

    def boundary(self, stack, current):
        stack.enter_context(patch.object(agent.run_context, 'current_run', return_value=current))
        stack.enter_context(patch.object(agent, '_runs', return_value=SimpleNamespace(store_result=lambda *a, **k: None)))
        stack.enter_context(patch.object(agent, '_run_status_gate'))
        stack.enter_context(patch.object(agent, '_run_event_strict'))
        stack.enter_context(patch.object(agent.frame_trace, 'metadata_for', return_value=None))
        stack.enter_context(patch.object(agent.frame_measure, 'measure', return_value=None))
        stack.enter_context(patch.object(agent.frame_serve, 'select', side_effect=lambda **k: (k['system'], None)))
        return stack.enter_context(patch.object(agent.llm, 'chat', return_value=SimpleNamespace(
            blocks=[], text='ok', stop_reason='end_turn')))

    def test_fallback_matrix_sends_canonical_without_reconstruction(self):
        failures = ('no_run', 'selector_exception', 'invalid_receipt', 'critical_material')
        for failure in failures:
            with self.subTest(failure=failure), ExitStack() as stack:
                state = live.capture_turn(self.ctx, 'candidate', [])
                canonical = [dict(role='user', content='canonical legacy')]
                chat = self.boundary(stack, None if failure == 'no_run' else SimpleNamespace(run_id='r'))
                if failure == 'selector_exception':
                    stack.enter_context(patch.object(live, 'select_provider', side_effect=RuntimeError('private')))
                elif failure == 'invalid_receipt':
                    stack.enter_context(patch.object(live, 'select_provider', return_value={'served': False}))
                elif failure == 'critical_material':
                    stack.enter_context(patch.object(agent, '_critical_secret_values_from_messages', return_value=('secret',)))
                expected = copy.deepcopy(canonical)
                with live.bind_turn(state, canonical):
                    agent._model_call('system', canonical, [])
                    self.assertEqual(chat.call_args.kwargs['messages'], expected)
                    self.assertEqual(canonical, expected)

    def test_corrupt_receipts_never_authorize_isolated_candidate(self):
        mutations = {
            'served_only': lambda r: {'served': True},
            'contradictory_status': lambda r: dict(r, status='fallback'),
            'truthy_served': lambda r: dict(r, served=1),
            'missing_checkpoint': lambda r: {k: v for k, v in r.items() if k != 'checkpoint'},
            'wrong_checkpoint': lambda r: dict(r, checkpoint='stale'),
            'wrong_call': lambda r: dict(r, binding=dict(r['binding'], call_id='stale')),
            'unknown_field': lambda r: dict(r, reference='invented'),
        }
        for name, mutate in mutations.items():
            with self.subTest(corruption=name), ExitStack() as stack:
                state = live.capture_turn(self.ctx, 'candidate', [])
                canonical = [dict(role='user', content='canonical')]
                expected = copy.deepcopy(canonical)
                chat = self.boundary(stack, SimpleNamespace(run_id='r'))
                original = live.select_provider

                def corrupt(*args, **kwargs):
                    receipt = original(*args, **kwargs)
                    self.assertTrue(live.valid_served_receipt(receipt, system=kwargs.get('system', args[0] if args else None),
                        messages=kwargs.get('messages', args[1] if len(args) > 1 else None),
                        tools=kwargs.get('tools', args[2] if len(args) > 2 else None),
                        run_id=kwargs.get('run_id', args[3] if len(args) > 3 else None),
                        call_id=kwargs.get('call_id', args[4] if len(args) > 4 else None)))
                    return mutate(receipt)

                stack.enter_context(patch.object(live, 'select_provider', side_effect=corrupt))
                with live.bind_turn(state, canonical):
                    agent._model_call('system', canonical, [])
                self.assertEqual(chat.call_args.kwargs['messages'], expected)
                self.assertEqual(canonical, expected)

    def test_stale_genuine_receipt_falls_back_to_canonical(self):
        state = live.capture_turn(self.ctx, 'candidate', [])
        first = [dict(role='user', content='candidate')]
        with live.bind_turn(state, first):
            provider = live.prepare_provider_messages('system', first, [])
            stale = live.select_provider('system', provider, [], 'r', 'old-call')
            self.assertIsNotNone(stale)
        with ExitStack() as stack:
            fresh = live.capture_turn(self.ctx, 'candidate', [])
            canonical = [dict(role='user', content='canonical')]
            chat = self.boundary(stack, SimpleNamespace(run_id='r'))
            stack.enter_context(patch.object(live, 'select_provider', return_value=stale))
            with live.bind_turn(fresh, canonical):
                agent._model_call('system', canonical, [])
            self.assertEqual(chat.call_args.kwargs['messages'], canonical)
            self.assertIsNone(fresh.head)

    def test_exact_authorized_path_uses_distinct_provider_identity(self):
        state = live.capture_turn(self.ctx, 'candidate', [])
        canonical = [dict(role='user', content='candidate')]
        with ExitStack() as stack:
            chat = self.boundary(stack, SimpleNamespace(run_id='r'))
            seen = {}
            original = live.prepare_provider_messages

            def prepare(system, messages, tools):
                result = original(system, messages, tools)
                seen['provider'] = result
                return result

            stack.enter_context(patch.object(live, 'prepare_provider_messages', side_effect=prepare))
            with live.bind_turn(state, canonical):
                agent._model_call('system', canonical, [])
            sent = chat.call_args.kwargs['messages']
            self.assertIs(sent, seen['provider'])
            self.assertIsNot(sent, canonical)
            self.assertEqual(sent, canonical)
            self.assertIsNotNone(state.head)

    def test_provider_mutation_cannot_modify_canonical(self):
        state = live.capture_turn(self.ctx, 'candidate', [])
        canonical = [dict(role='user', content='canonical')]
        expected = copy.deepcopy(canonical)
        with live.bind_turn(state, canonical):
            provider = live.prepare_provider_messages('system', canonical, [])
            provider[0]['content'] = 'arbitrary provider mutation'
            provider.insert(0, dict(role='assistant', content='inserted'))
            self.assertEqual(canonical, expected)
            self.assertIsNone(live.select_provider('system', provider, [], 'r', 'c'))
            self.assertFalse(live.fallback_bound(provider))
            self.assertEqual(provider[0]['content'], 'inserted')
            self.assertEqual(canonical, expected)


if __name__ == '__main__':
    unittest.main()
