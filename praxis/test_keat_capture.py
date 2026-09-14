import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from keat_candidate import ContractError, _digest
from keat_capture import CaptureLedger, NarrowingReader
from keat_epoch import validate


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = CaptureLedger(self.temp.name, 'telegram:account:test')
        self.reader = NarrowingReader(self.temp.name, 'telegram:account:test')

    def capture(self, event='e1', key='telegram:chat:1:topic:0:message:1', **kwargs):
        grant = dict(issuer='capture:test', policy_revision='p1', grant='grant:' + event,
                     audience=['owner'], transfer='none', presence_hidden=False)
        return self.ledger.issue(occurrence_id=event, key=key, kind='message',
                                 payload={'role': 'user', 'content': 'same'}, capture=grant, **kwargs)

    def source(self, events=None, keys=None):
        events = events or ['e1']
        return self.ledger.project(messages=[{'role': 'user', 'content': 'same'} for _ in events],
                                   origins=[[e] for e in events], transforms=['identity'] * len(events),
                                   issuer='project:test', tools=None, historical_count=1, keys=keys)

    def read(self, source):
        return self.reader.read(source_bytes=source, audience=('owner',), now=12)

    def test_durable_equal_content_distinct_occurrences_and_retry(self):
        one = self.capture()
        self.assertEqual(one, self.capture())
        self.capture('e2', 'telegram:chat:1:topic:0:message:2')
        source = self.source(['e1', 'e2'])
        authority, pins = NarrowingReader(self.temp.name, self.ledger.namespace).read(
            source_bytes=source, audience=('owner',), now=12)
        result = validate(source_bytes=source, authority_bytes=authority, pins=pins,
                          audience=('owner',), now=12)
        self.assertEqual(len(result['receipts']), 2)
        self.assertEqual([x['origins'] for x in result['projection']], [['e1'], ['e2']])
        with self.assertRaises(ContractError):
            self.capture('e1', 'changed-key')

    def test_no_fabricated_historical_grants(self):
        self.capture()
        source = json.loads(self.source())
        source['receipts'][0]['capture']['transfer'] = 'invented-current-room-transfer'
        with self.assertRaises(ContractError):
            self.read(json.dumps(source).encode())

    def test_revoke_current_independent_of_old_source(self):
        self.capture()
        source = self.source()
        self.read(source)
        self.reader.narrow('grant:e1')
        self.reader.narrow('grant:e1')
        with self.assertRaises(ContractError):
            self.read(source)
        with self.assertRaises(ContractError):
            self.reader.narrow('grant:e1', self.ledger.snapshot()['receipts'][0]['capture'])

    def test_order_and_missing_selection_fail_closed(self):
        self.capture()
        self.capture('e2', 'second')
        for events in [['e2', 'e1'], ['e1'], ['e1', 'e1']]:
            with self.assertRaises(ContractError):
                self.read(self.source(events))
        with self.assertRaises(ContractError):
            self.source(keys=['absent'])

    def test_edit_and_delete_make_old_source_stale(self):
        one = self.capture()
        old = self.source()
        two = self.capture('e2', expected_parent=_digest(one))
        self.read(self.source(['e2']))
        with self.assertRaises(ContractError):
            self.read(old)
        self.capture('e3', expected_parent=_digest(two), deleted=True)
        with self.assertRaises(ContractError):
            self.read(self.source(['e2']))
        with self.assertRaises(ContractError):
            self.capture('e4', expected_parent=_digest(two))

    def test_corruption_and_namespace_fail_closed(self):
        self.capture()
        with self.assertRaises(ContractError):
            CaptureLedger(self.temp.name, 'other').snapshot()
        path = Path(self.temp.name) / 'capture.jsonl'
        path.write_bytes(path.read_bytes() + b'{')
        with self.assertRaises(ContractError):
            self.ledger.snapshot()

    def test_retry_resync_after_lost_ack(self):
        with patch('keat_capture._sync_directory', side_effect=OSError('sync')):
            with self.assertRaises(OSError):
                self.capture()
            with self.assertRaises(OSError):
                self.capture()
        self.capture()
        self.assertEqual(len(self.ledger.snapshot()['receipts']), 1)

    def test_reentrant_shared_guard_and_strict_types(self):
        with self.ledger.guard(), CaptureLedger(self.temp.name, self.ledger.namespace).guard():
            self.capture()
            self.read(self.source())
        capture = self.ledger.snapshot()['receipts'][0]['capture']
        capture['presence_hidden'] = 0
        with self.assertRaises(ContractError):
            self.reader.narrow('grant:e1', capture)

    def test_current_narrowing_never_relabels_history(self):
        capture = dict(issuer='capture:test', policy_revision='p1', grant='g',
                       audience=['owner', 'guest'], transfer='none', presence_hidden=False)
        self.ledger.issue(occurrence_id='e1', key='k', kind='message', payload='x', capture=capture)
        source = self.source()
        self.reader.read(source_bytes=source, audience=('owner', 'guest'), now=12)
        capture['audience'] = ['owner']
        self.reader.narrow('g', capture)
        for audience in [('owner',), ('owner', 'guest')]:
            with self.assertRaises(ContractError):
                self.reader.read(source_bytes=source, audience=audience, now=12)
        capture['audience'] = ['owner', 'guest']
        with self.assertRaises(ContractError):
            self.reader.narrow('g', capture)

    def test_competing_capture_parents(self):
        from concurrent.futures import ThreadPoolExecutor
        one = self.capture()
        def attempt(event):
            try:
                return self.capture(event, expected_parent=_digest(one))
            except ContractError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ['e2', 'e3']))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(self.ledger.snapshot()['receipts']), 2)

    def test_exact_tool_media_coalescing(self):
        self.capture()
        self.capture('e2', 'second')
        messages = [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 't', 'name': 'x', 'input': {'n': True}},
                                                     {'type': 'image', 'source': {'data': 'opaque'}}]}]
        tools = [{'name': 'x', 'input_schema': {'type': 'object'}}]
        source = self.ledger.project(messages=messages, origins=[['e1', 'e2']], transforms=['coalesce'],
                                     issuer='projection:test', tools=tools, historical_count=0)
        self.read(source)
        self.assertEqual(json.loads(source)['messages'], messages)
        self.assertEqual(json.loads(source)['tools'], tools)


if __name__ == '__main__':
    unittest.main()
