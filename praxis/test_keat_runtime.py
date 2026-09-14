"""Transactional runtime regression tests: synthetic evidence, no network."""
import copy
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

from keat_candidate import ContractError
from keat_runtime import ResumeBinding, RuntimeStore, staged
from test_keat_epoch import fixture, arguments


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source, self.auth = fixture()
        self.store = RuntimeStore(self.tmp.name)
        self.binding = ResumeBinding('run:1', 'call:1', 'owner', 'dm-owner', ('owner',))
        self.request = dict(system='exact\n system', messages=self.source['messages'],
                            tools=self.source['tools'])

    def write(self, **kwargs):
        args = dict(binding=self.binding, expected_parent=None, epoch='A:1',
                    policy='p:1', reason='first', **arguments(self.source, self.auth),
                    **self.request)
        args.pop('audience')
        args.update(kwargs)
        return self.store.checkpoint(**args)

    def reopen(self, head, **kwargs):
        args = arguments(self.source, self.auth)
        args.pop('source_bytes')
        args.pop('audience')
        args.update(expected_head=head, binding=self.binding, **self.request)
        args.update(kwargs)
        return RuntimeStore(self.tmp.name).reopen(**args)

    def test_structured_system_blocks_exact_after_reopen(self):
        blocks = [{'type': 'text', 'text': 'constitution',
                   'cache_control': {'type': 'ephemeral'}},
                  {'type': 'text', 'text': 'current transport'}]
        head = self.write(system=blocks)
        self.reopen(head, system=copy.deepcopy(blocks))
        with self.assertRaises(ContractError):
            self.reopen(head, system=str(blocks))
        changed = copy.deepcopy(blocks)
        changed[0].pop('cache_control')
        with self.assertRaises(ContractError):
            self.reopen(head, system=changed)

    def test_staging_uses_sparse_exact_pairs_and_preserves_owner_env(self):
        for mode in ('owner', 'dm', 'group', 'wake', 'window'):
            self.assertFalse(staged(mode, 'x', {}))
            env = {'PRAXIS_KEAT': 'serve',
                   'PRAXIS_KEAT_PAIRS': f'[["{mode}","x"]]'}
            self.assertTrue(staged(mode, 'x', env))
            self.assertFalse(staged(mode, 'y', env))
            self.assertFalse(staged('legacy', 'x', env))
            env['PRAXIS_KEAT'] = 'off'
            self.assertFalse(staged(mode, 'x', env))

        # Existing owner deployment remains valid without the new variable.
        legacy = {'PRAXIS_KEAT': 'serve', 'PRAXIS_KEAT_MODES': 'owner',
                  'PRAXIS_KEAT_STREAMS': 'x,y'}
        self.assertTrue(staged('owner', 'x', legacy))
        self.assertTrue(staged('owner', 'y', legacy))
        # Legacy Cartesian widening is deliberately removed.
        legacy['PRAXIS_KEAT_MODES'] = 'owner,group'
        self.assertFalse(staged('owner', 'x', legacy))
        self.assertFalse(staged('group', 'x', legacy))

    def test_sparse_pairs_do_not_create_cartesian_authority(self):
        env = {'PRAXIS_KEAT': 'serve',
               'PRAXIS_KEAT_PAIRS': '[["dm","chat-a"],["group","chat-b"]]',
               # PAIRS is authoritative when present; legacy values cannot widen it.
               'PRAXIS_KEAT_MODES': 'owner', 'PRAXIS_KEAT_STREAMS': 'owner-chat'}
        self.assertTrue(staged('dm', 'chat-a', env))
        self.assertTrue(staged('group', 'chat-b', env))
        self.assertFalse(staged('dm', 'chat-b', env))
        self.assertFalse(staged('group', 'chat-a', env))
        self.assertFalse(staged('owner', 'owner-chat', env))

    def test_malformed_noncanonical_and_duplicate_pairs_fail_closed(self):
        invalid = (None, '', '[]', '{}', '[["dm","x"], ["group","y"]]',
                   '[["group","y"],["dm","x"]]', '[ ["dm","x"] ]',
                   '[["dm","x"],["dm","x"]]', '[["dm"," x"]]',
                   '[["unknown","x"]]', '[["dm",1]]', '[["dm","x","y"]]')
        for value in invalid:
            with self.subTest(value=value):
                env = {'PRAXIS_KEAT': 'serve', 'PRAXIS_KEAT_PAIRS': value,
                       'PRAXIS_KEAT_MODES': 'owner', 'PRAXIS_KEAT_STREAMS': 'x'}
                self.assertFalse(staged('dm', 'x', env))

    def test_atomic_binding_exact_resume_and_retry(self):
        before = copy.deepcopy(self.request)
        head = self.write()
        self.assertEqual(head, self.write())
        reopened = self.reopen(head)
        self.assertEqual(reopened['historical_a'] + reopened['active_roles'], before['messages'])
        self.assertEqual(before, self.request)
        self.assertEqual(reopened, self.reopen(head))
        # A cuts across tool use/result; preserve exact original roles, not text.
        self.assertEqual(reopened['historical_a'][-1]['role'], 'assistant')
        self.assertEqual(reopened['active_roles'][0]['content'][0]['type'], 'tool_result')

    def test_request_identity_and_system_never_rebound_on_resume(self):
        head = self.write()
        for field, value in [('run_id', 'other'), ('call_id', 'other'), ('mode', 'group'),
                             ('stream', 'other'), ('audience', ('guest',))]:
            with self.subTest(field=field), self.assertRaises(ContractError):
                self.reopen(head, binding=replace(self.binding, **{field: value}))
        for change in [dict(system='different'), dict(tools=None),
                       dict(messages=list(reversed(self.source['messages'])))]:
            with self.assertRaises(ContractError):
                self.reopen(head, **change)

    def test_current_revocation_and_expiry(self):
        head = self.write()
        self.auth['grants'].clear()
        with self.assertRaises(ContractError):
            self.reopen(head)
        self.source, self.auth = fixture()
        with self.assertRaises(ContractError):
            self.reopen(head, now=20)

    def test_source_cannot_substitute_provider_tape_or_tools(self):
        for change in [dict(tools=[]), dict(messages=self.source['messages'][:-1])]:
            with self.assertRaises(ContractError):
                self.write(**change)
        self.assertFalse((self.store.epochs.directory / 'HEAD').exists())

    def test_publication_failure_and_exact_retry(self):
        original = self.store.epochs._atomic
        def fail(name, raw):
            if name == 'HEAD':
                raise OSError('injected')
            return original(name, raw)
        with patch.object(self.store.epochs, '_atomic', side_effect=fail):
            with self.assertRaises(OSError):
                self.write()
        self.assertFalse((self.store.epochs.directory / 'HEAD').exists())
        head = self.write()
        self.reopen(head)

    def test_stale_cas_corruption_and_no_legacy_reopen(self):
        head = self.write()
        with self.assertRaises(ContractError):
            self.write(reason='competing')
        successor = self.write(expected_parent=head, reason='next')
        with self.assertRaises(ContractError):
            self.reopen(head)
        path = self.store.epochs.directory / (head + '.json')
        path.write_bytes(b'{}')
        with self.assertRaises(ContractError):
            self.reopen(successor)

    def test_real_capture_transaction_and_current_narrowing(self):
        from keat_capture import CaptureLedger, NarrowingReader
        ledger = CaptureLedger(self.tmp.name + '/capture', 'live:1')
        reader = NarrowingReader(self.tmp.name + '/capture', 'live:1')
        grant = dict(self.auth['grants']['g:1'])
        message = dict(role='user', content='original')
        receipt = ledger.issue(occurrence_id='ingress:1', key='chat:1/message:1',
                               kind='message', payload=message, capture=grant)
        source_bytes = ledger.project(messages=[message], origins=[[receipt['event']]],
                                      transforms=['exact'], issuer='projector:1', tools=None,
                                      historical_count=1)
        request = dict(binding=self.binding, now=15, system='exact', messages=[message], tools=None)
        head = self.store.checkpoint_capture(ledger=ledger, reader=reader, source_bytes=source_bytes,
                                            expected_parent=None, epoch='A:1', policy='p:1',
                                            reason='initial', **request)
        request['now'] += 60  # independent fresh authorization after real time elapses
        reopened = self.store.reopen_capture(ledger=ledger, reader=reader,
                                             expected_head=head, **request)
        self.assertEqual(reopened['historical_a'], [message])
        reader.narrow('g:1')
        with self.assertRaises(ContractError):
            self.store.reopen_capture(ledger=ledger, reader=reader, expected_head=head, **request)

    def test_mismatched_reader_lock_rejected(self):
        from keat_capture import CaptureLedger, NarrowingReader
        ledger = CaptureLedger(self.tmp.name + '/a', 'live:1')
        reader = NarrowingReader(self.tmp.name + '/b', 'live:1')
        with self.assertRaises(ContractError):
            self.store.checkpoint_capture(ledger=ledger, reader=reader, source_bytes=b'{}')


if __name__ == '__main__':
    unittest.main()
