"""Synthetic read-only KEAT readiness tests; no serving imports or provider calls."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import keat_readiness


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'keat'
        self.policy = Path(self.tmp.name) / 'policy.json'

    def env(self):
        return {'PRAXIS_KEAT_CAPTURE': 'on', 'PRAXIS_KEAT': 'serve',
                'PRAXIS_KEAT_CAPTURE_POLICY': str(self.policy),
                'PRAXIS_KEAT_ROOT': str(self.root),
                'PRAXIS_KEAT_MODES': 'owner', 'PRAXIS_KEAT_STREAMS': '42'}

    def write_policy(self, **changes):
        value = {'schema': 'keat.capture-policy.v1', 'namespace': 'test',
                 'root': str(self.root), 'enrollments': [{
                    'stream': '42', 'mode': 'owner', 'capture': {
                        'issuer': 'admin', 'policy_revision': 'p1',
                        'audience': ['owner'], 'transfer': 'none',
                        'presence_hidden': False}}]}
        value.update(changes)
        self.policy.write_text(json.dumps(value), encoding='utf-8')

    def test_empty_environment_attests_exact_default_off_without_io(self):
        result = keat_readiness.receipt({})
        self.assertEqual(result['state'], 'default_off')
        self.assertTrue(result['checks']['default_off_exact'])
        self.assertFalse(result['ready'])
        self.assertFalse(result['capture_ready'])
        self.assertFalse(result['serve_ready'])
        self.assertFalse(self.root.exists())

    def test_exact_policy_root_and_enrollment_are_ready_and_public_safe(self):
        self.write_policy()
        result = keat_readiness.receipt(self.env())
        self.assertTrue(result['ready'])
        self.assertTrue(result['capture_ready'])
        self.assertTrue(result['serve_ready'])
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['enrollment_count'], 1)
        self.assertEqual(len(result['policy_sha256']), 64)
        self.assertNotIn(str(self.tmp.name), json.dumps(result))
        private = keat_readiness.receipt(self.env(), private=True)
        self.assertEqual(private['root'], str(self.root))
        self.assertEqual(private['streams'], ['42'])
        self.assertFalse(self.root.exists())

    def test_capture_only_soak_is_ready_for_capture_not_misconfigured(self):
        self.write_policy()
        for serving in ('', 'off'):
            with self.subTest(serving=serving):
                env = dict(self.env(), PRAXIS_KEAT=serving)
                result = keat_readiness.receipt(env)
                self.assertEqual(result['state'], 'capture_only_ready')
                self.assertTrue(result['capture_ready'])
                self.assertFalse(result['serve_ready'])
                self.assertFalse(result['ready'])  # v1 compatibility: serve readiness
                self.assertTrue(result['checks']['serve_off'])

    def test_capture_only_does_not_require_serving_selectors(self):
        self.write_policy()
        env = self.env()
        env['PRAXIS_KEAT'] = 'off'
        env.pop('PRAXIS_KEAT_MODES')
        env.pop('PRAXIS_KEAT_STREAMS')
        result = keat_readiness.receipt(env)
        self.assertEqual(result['state'], 'capture_only_ready')
        self.assertTrue(result['capture_ready'])
        self.assertFalse(result['serve_ready'])
        self.assertFalse(result['checks']['selectors_valid'])
        self.assertTrue(result['checks']['policy_enrollments_valid'])

    def test_capture_only_with_invalid_policy_still_fails_closed(self):
        self.policy.write_text('{bad', encoding='utf-8')
        result = keat_readiness.receipt(dict(self.env(), PRAXIS_KEAT='off'))
        self.assertEqual(result['state'], 'misconfigured')
        self.assertFalse(result['capture_ready'])
        self.assertFalse(result['serve_ready'])
        self.assertFalse(result['ready'])

    def test_observability_joins_readiness_and_call_receipts(self):
        self.write_policy()
        run = Path(self.tmp.name) / 'tree/memory/runs/day/run'
        run.mkdir(parents=True)
        events = [
            {'kind': 'model_input', 'call_id': 'a', 'metadata': {
                'keat': {'status': 'served'},
                'frame_measure': {'actual': {'estimated_tokens': 55}}}},
            {'kind': 'model_completed', 'call_id': 'a',
             'at': '2026-09-11T00:00:00Z',
             'usage': {'schema': 2, 'in': 4, 'cache_read': 6}},
        ]
        (run / 'events.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in events), encoding='utf-8')
        result = keat_readiness.observability(Path(self.tmp.name) / 'tree', self.env())
        self.assertTrue(result['readiness']['ready'])
        group = result['input_cost']['groups'][0]
        self.assertEqual(group['metrics']['input_estimated_tokens']['sum'], 55)
        self.assertEqual(group['metrics']['cache_read']['sum'], 6)
        self.assertNotIn(str(self.tmp.name), json.dumps(result))

    def test_policy_root_and_sparse_enrollment_must_match_exactly(self):
        self.write_policy(root=str(self.root / 'other'))
        result = keat_readiness.receipt(self.env())
        self.assertEqual(result['state'], 'misconfigured')
        self.assertFalse(result['checks']['policy_root_exact'])
        self.write_policy()
        env = dict(self.env(), PRAXIS_KEAT_PAIRS='[["owner","42"],["owner","43"]]')
        result = keat_readiness.receipt(env)
        self.assertFalse(result['checks']['enrollments_exact'])

    def test_sparse_pair_readiness_matches_policy_without_cartesian_widening(self):
        dm_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                      'audience': ['dm:101'], 'transfer': 'none',
                      'presence_hidden': False}
        wake_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                        'audience': ['scheduled:wake'], 'transfer': 'none',
                        'presence_hidden': False}
        self.write_policy(enrollments=[
            {'mode': 'dm', 'stream': '101', 'capture': dm_capture},
            {'mode': 'wake', 'stream': 'scheduler:wake', 'capture': wake_capture},
        ])
        env = dict(self.env(),
                   PRAXIS_KEAT_PAIRS='[["dm","101"],["wake","scheduler:wake"]]')
        result = keat_readiness.receipt(env, private=True)
        self.assertTrue(result['serve_ready'])
        self.assertTrue(result['checks']['serving_adapters_ready'])
        self.assertEqual(result['enrollment_count'], 2)
        self.assertEqual(result['pairs'], [['dm', '101'], ['wake', 'scheduler:wake']])
        self.assertEqual(result['selector_source'], 'pairs')

        # The crossed pair is neither runtime-selected nor readiness-enrolled.
        from keat_runtime import staged
        self.assertFalse(staged('dm', 'scheduler:wake', env))
        self.assertFalse(staged('wake', '101', env))

    def test_ordinary_dm_requires_exact_runtime_authority_contract(self):
        base = {'issuer': 'admin', 'policy_revision': 'p1',
                'audience': ['dm:101'], 'transfer': 'none',
                'presence_hidden': False}
        variants = (
            ('stream_zero', '0', base),
            ('stream_negative', '-1', base),
            ('stream_noncanonical', '001', base),
            ('stream_nonnumeric', 'chat-a', base),
            ('audience', '101', dict(base, audience=['dm:102'])),
            ('transfer', '101', dict(base, transfer='delegate')),
            ('presence', '101', dict(base, presence_hidden=True)),
        )
        for name, stream, capture in variants:
            with self.subTest(name=name):
                self.write_policy(enrollments=[
                    {'mode': 'dm', 'stream': stream, 'capture': capture}])
                env = dict(self.env(), PRAXIS_KEAT_PAIRS=json.dumps(
                    [['dm', stream]], separators=(',', ':')))
                result = keat_readiness.receipt(env)
                self.assertTrue(result['capture_ready'])
                self.assertTrue(result['checks']['selectors_valid'])
                self.assertTrue(result['checks']['enrollments_exact'])
                self.assertFalse(result['checks']['serving_adapters_ready'])
                self.assertFalse(result['serve_ready'])
                self.assertEqual(result['state'], 'misconfigured')

    def test_mixed_valid_dm_and_window_is_capture_valid_but_never_serve_ready(self):
        dm_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                      'audience': ['dm:101'], 'transfer': 'none',
                      'presence_hidden': False}
        window_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                          'audience': ['owner'], 'transfer': 'none',
                          'presence_hidden': False}
        self.write_policy(enrollments=[
            {'mode': 'dm', 'stream': '101', 'capture': dm_capture},
            {'mode': 'window', 'stream': 'durable-window', 'capture': window_capture},
        ])
        env = dict(self.env(), PRAXIS_KEAT_PAIRS=
                   '[["dm","101"],["window","durable-window"]]')
        result = keat_readiness.receipt(env)
        self.assertTrue(result['capture_ready'])
        self.assertTrue(result['checks']['selectors_valid'])
        self.assertTrue(result['checks']['enrollments_exact'])
        self.assertFalse(result['checks']['serving_adapters_ready'])
        self.assertFalse(result['serve_ready'])
        self.assertEqual(result['state'], 'misconfigured')

    def test_mixed_valid_dm_and_noncanonical_wake_cannot_claim_adapter_readiness(self):
        dm_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                      'audience': ['dm:101'], 'transfer': 'none',
                      'presence_hidden': False}
        wake_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                        'audience': ['owner'], 'transfer': 'none',
                        'presence_hidden': False}
        self.write_policy(enrollments=[
            {'mode': 'dm', 'stream': '101', 'capture': dm_capture},
            {'mode': 'wake', 'stream': 'wake-a', 'capture': wake_capture},
        ])
        env = dict(self.env(),
                   PRAXIS_KEAT_PAIRS='[["dm","101"],["wake","wake-a"]]')
        result = keat_readiness.receipt(env)
        self.assertTrue(result['capture_ready'])
        self.assertTrue(result['checks']['selectors_valid'])
        self.assertTrue(result['checks']['enrollments_exact'])
        self.assertFalse(result['checks']['serving_adapters_ready'])
        self.assertFalse(result['serve_ready'])
        self.assertEqual(result['state'], 'misconfigured')

        # Capture-only validity is deliberately separate from adapter readiness.
        capture_only = keat_readiness.receipt(dict(env, PRAXIS_KEAT='off'))
        self.assertTrue(capture_only['capture_ready'])
        self.assertFalse(capture_only['serve_ready'])
        self.assertEqual(capture_only['state'], 'capture_only_ready')

    def test_mixed_valid_dm_and_unsupported_group_is_not_serve_ready(self):
        dm_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                      'audience': ['dm:101'], 'transfer': 'none',
                      'presence_hidden': False}
        group_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                         'audience': ['group:202'], 'transfer': 'none',
                         'presence_hidden': False}
        self.write_policy(enrollments=[
            {'mode': 'dm', 'stream': '101', 'capture': dm_capture},
            {'mode': 'group', 'stream': '202', 'capture': group_capture},
        ])
        env = dict(self.env(),
                   PRAXIS_KEAT_PAIRS='[["dm","101"],["group","202"]]')
        result = keat_readiness.receipt(env)
        self.assertTrue(result['capture_ready'])
        self.assertTrue(result['checks']['selectors_valid'])
        self.assertTrue(result['checks']['enrollments_exact'])
        self.assertFalse(result['checks']['serving_adapters_ready'])
        self.assertFalse(result['serve_ready'])
        self.assertFalse(result['ready'])
        self.assertEqual(result['state'], 'misconfigured')

    def test_native_owner_and_dm_same_stream_is_capture_valid_but_not_serve_ready(self):
        owner_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                         'audience': ['owner'], 'transfer': 'none',
                         'presence_hidden': False}
        dm_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                      'audience': ['dm:42'], 'transfer': 'none',
                      'presence_hidden': False}
        self.write_policy(enrollments=[
            {'mode': 'dm', 'stream': '42', 'capture': dm_capture},
            {'mode': 'owner', 'stream': '42', 'capture': owner_capture},
        ])
        env = dict(self.env(),
                   PRAXIS_KEAT_PAIRS='[["dm","42"],["owner","42"]]')
        result = keat_readiness.receipt(env)

        # Both exact capture rows remain diagnosable; readiness merely refuses
        # to claim that native ingress can select one unique authority.
        self.assertTrue(result['capture_ready'])
        self.assertTrue(result['checks']['policy_enrollments_valid'])
        self.assertTrue(result['checks']['selectors_valid'])
        self.assertTrue(result['checks']['enrollments_exact'])
        self.assertFalse(result['checks']['native_dm_enrollments_unique'])
        self.assertFalse(result['checks']['serving_adapters_ready'])
        self.assertFalse(result['serve_ready'])
        self.assertFalse(result['ready'])
        self.assertEqual(result['state'], 'misconfigured')

    def test_native_owner_and_dm_distinct_streams_remain_serve_ready(self):
        owner_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                         'audience': ['owner'], 'transfer': 'none',
                         'presence_hidden': False}
        dm_capture = {'issuer': 'admin', 'policy_revision': 'p1',
                      'audience': ['dm:43'], 'transfer': 'none',
                      'presence_hidden': False}
        self.write_policy(enrollments=[
            {'mode': 'dm', 'stream': '43', 'capture': dm_capture},
            {'mode': 'owner', 'stream': '42', 'capture': owner_capture},
        ])
        env = dict(self.env(),
                   PRAXIS_KEAT_PAIRS='[["dm","43"],["owner","42"]]')
        result = keat_readiness.receipt(env)

        self.assertTrue(result['checks']['native_dm_enrollments_unique'])
        self.assertTrue(result['checks']['serving_adapters_ready'])
        self.assertTrue(result['capture_ready'])
        self.assertTrue(result['serve_ready'])
        self.assertTrue(result['ready'])
        self.assertEqual(result['state'], 'ready')

    def test_native_identity_readiness_matches_ingress_independent_of_row_order(self):
        from keat_candidate import ContractError
        from keat_live import _native_policy

        # The peer is resolved before its owner/ordinary-DM authority. Distinct
        # mode tuples must not conceal a shared Telegram principal. Conversely,
        # an exact two-principal canary must not be rejected just for two modes.
        for dm_stream in ('42', '43'):
            for reverse in (False, True):
                with self.subTest(dm_stream=dm_stream, reverse=reverse):
                    rows = [
                        {'mode': 'dm', 'stream': dm_stream, 'capture': {
                            'issuer': 'admin', 'policy_revision': 'p1',
                            'audience': ['dm:' + dm_stream], 'transfer': 'none',
                            'presence_hidden': False}},
                        {'mode': 'owner', 'stream': '42', 'capture': {
                            'issuer': 'admin', 'policy_revision': 'p1',
                            'audience': ['owner'], 'transfer': 'none',
                            'presence_hidden': False}},
                    ]
                    self.write_policy(enrollments=rows[::-1] if reverse else rows)
                    before = self.policy.read_bytes()
                    env = dict(self.env(), PRAXIS_KEAT_PAIRS=json.dumps(
                        [['dm', dm_stream], ['owner', '42']], separators=(',', ':')))
                    distinct = dm_stream != '42'
                    for private in (False, True):
                        result = keat_readiness.receipt(env, private=private)
                        self.assertEqual(result['serve_ready'], distinct)
                        self.assertEqual(result['ready'], distinct)
                        self.assertEqual(
                            result['checks']['native_dm_enrollments_unique'], distinct)
                    with patch.dict('os.environ', env, clear=True):
                        if distinct:
                            self.assertEqual(_native_policy(42)[1]['mode'], 'owner')
                            self.assertEqual(_native_policy(43)[1]['mode'], 'dm')
                        else:
                            with self.assertRaisesRegex(
                                    ContractError, 'ambiguous native DM enrollment'):
                                _native_policy(42)
                    self.assertEqual(self.policy.read_bytes(), before)
                    self.assertFalse(self.root.exists())

    def test_pairs_are_canonical_and_override_legacy_selectors(self):
        self.write_policy()
        invalid = ('', '[]', '[["owner", "42"]]', '[["owner","42"], ["dm","x"]]',
                   '[["owner","42"],["owner","42"]]', '[["owner"," 42"]]',
                   '[["wat","42"]]', '[["owner",42]]')
        for value in invalid:
            with self.subTest(value=value):
                env = dict(self.env(), PRAXIS_KEAT_PAIRS=value)
                result = keat_readiness.receipt(env)
                self.assertFalse(result['checks']['selectors_valid'])
                self.assertFalse(result['serve_ready'])
                self.assertEqual(result['state'], 'misconfigured')

        # Presence of a malformed new selector cannot fall back to valid legacy env.
        result = keat_readiness.receipt(dict(self.env(), PRAXIS_KEAT_PAIRS='bad'))
        self.assertFalse(result['serve_ready'])

    def test_canonical_policy_parser_rejects_duplicate_keys_like_runtime(self):
        self.write_policy()
        original = self.policy.read_text(encoding='utf-8')
        variants = (
            original.replace(
                '"schema": "keat.capture-policy.v1"',
                '"schema": "ignored", "schema": "keat.capture-policy.v1"'),
            original.replace(
                '"issuer": "admin"',
                '"issuer": "ignored", "issuer": "admin"'),
        )
        for index, raw in enumerate(variants):
            with self.subTest(depth=index):
                self.policy.write_text(raw, encoding='utf-8')
                result = keat_readiness.receipt(self.env())
                self.assertFalse(result['checks']['policy_schema'])
                self.assertFalse(result['ready'])
                self.assertEqual(result['state'], 'misconfigured')

    def test_policy_namespace_must_be_nonempty_by_runtime_text_rules(self):
        from keat_source import _text

        for namespace in ('', '   ', '\t\n'):
            with self.subTest(namespace=repr(namespace)):
                self.write_policy(namespace=namespace)
                self.assertFalse(_text(namespace))
                result = keat_readiness.receipt(self.env())
                self.assertFalse(result['checks']['policy_schema'])
                self.assertFalse(result['capture_ready'])
                self.assertFalse(result['serve_ready'])
                self.assertFalse(result['ready'])
                self.assertEqual(result['state'], 'misconfigured')

    def test_selector_grammar_is_literal_and_rejects_non_strings(self):
        from keat_runtime import staged

        self.write_policy()
        variants = (
            ('PRAXIS_KEAT_MODES', ' owner ', True),
            ('PRAXIS_KEAT_MODES', 'owner ', True),
            ('PRAXIS_KEAT_MODES', 'owner, wake', False),
            ('PRAXIS_KEAT_STREAMS', ' 42 ', True),
            ('PRAXIS_KEAT_STREAMS', '42 ', True),
            ('PRAXIS_KEAT_STREAMS', '42, 43', False),
            ('PRAXIS_KEAT_STREAMS', 42, False),
        )
        for name, value, selected_token_changed in variants:
            with self.subTest(selector=name, value=value):
                env = dict(self.env(), **{name: value})
                if selected_token_changed:
                    self.assertFalse(staged('owner', '42', env))
                result = keat_readiness.receipt(env)
                self.assertFalse(result['checks']['selectors_valid'])
                self.assertFalse(result['ready'])
                self.assertEqual(result['state'], 'misconfigured')

    def test_unknown_activation_values_are_explicitly_misconfigured(self):
        for name, value in (('PRAXIS_KEAT_CAPTURE', 'ON'),
                            ('PRAXIS_KEAT_CAPTURE', 'OFF'),
                            ('PRAXIS_KEAT', 'servee'),
                            ('PRAXIS_KEAT', 'OFF')):
            with self.subTest(name=name, value=value):
                result = keat_readiness.receipt({name: value})
                self.assertEqual(result['state'], 'misconfigured')
                self.assertFalse(result['checks']['default_off_exact'])
                check = ('capture_value_valid' if name.endswith('CAPTURE')
                         else 'serve_value_valid')
                self.assertFalse(result['checks'][check])

    def test_days_rejects_nonfinite_negative_and_excessive_values_via_argparse(self):
        for value in ('nan', 'inf', '-1', '3650.1'):
            with self.subTest(value=value), self.assertRaises(SystemExit) as raised, \
                    patch('sys.stderr', new=io.StringIO()) as stderr:
                keat_readiness.main(['--tree', self.tmp.name, '--days', value])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn('must be finite in the range 0..3650', stderr.getvalue())

    def test_duplicates_malformed_policy_and_partial_activation_fail_closed(self):
        self.policy.write_text('{bad', encoding='utf-8')
        env = dict(self.env(), PRAXIS_KEAT_MODES='owner,owner')
        result = keat_readiness.receipt(env)
        self.assertFalse(result['checks']['selectors_valid'])
        self.assertFalse(result['checks']['policy_schema'])
        self.assertFalse(result['ready'])
        self.assertEqual(result['state'], 'misconfigured')


if __name__ == '__main__':
    unittest.main()
