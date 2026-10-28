"""Synthetic offline authority fixtures; no capture or permission inference."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from keat_candidate import ContractError, _digest
from keat_epoch import EvidencePins, EpochStore, _bytes, validate


def fixture():
    grant = dict(issuer='owner', policy_revision='policy:1', grant='g:1',
                 audience=['owner'], transfer='private', presence_hidden=False)
    messages = [dict(role='user', content='same'), dict(role='user', content='same'),
                dict(role='assistant', content=[dict(type='tool_use', id='call:1', name='x', input={'a': 1})]),
                dict(role='user', content=[dict(type='tool_result', tool_use_id='call:1',
                     content=[dict(type='image', source={'type': 'base64', 'data': 'AA==', 'media_type': 'image/png'})])])]
    receipts = [dict(event=f'event:{i}', key=f'chat:{i}:message:1', kind='message' if i < 2 else 'tool',
                     revision=0, parent=None, deleted=False, payload_digest=_digest(m), capture=copy.deepcopy(grant))
                for i, m in enumerate(messages)]
    source = dict(schema='keat.original.v1', namespace='original-store', receipts=receipts,
                  messages=messages, projection=[dict(index=i, message_digest=_digest(m), origins=[receipts[i]['event']],
                  transform='exact', issuer='projector:1') for i, m in enumerate(messages)], tools=[{'name': 'x', 'description': 'exact'}], historical_count=3)
    auth = dict(schema='keat.authorization.v1', namespace=source['namespace'], revision='auth:1',
                audience=['owner'], valid_from=10, valid_until=20, grants={'g:1': grant},
                heads={r['key']: _digest(r) for r in receipts})
    return source, auth


def arguments(source, auth):
    sb, ab = _bytes(source), _bytes(auth)
    return dict(source_bytes=sb, authority_bytes=ab,
                pins=EvidencePins(source['namespace'], hashlib.sha256(sb).hexdigest(),
                                  hashlib.sha256(ab).hexdigest(), auth['revision']),
                audience=('owner',), now=15)


class EpochTests(unittest.TestCase):
    def setUp(self):
        self.source, self.auth = fixture()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = EpochStore(self.tmp.name)

    def write(self, **overrides):
        args = dict(expected_parent=None, epoch='A:1', policy='epoch-policy:1', reason='initial',
                    **arguments(self.source, self.auth))
        args.update(overrides)
        return self.store.checkpoint(**args)

    def reopen(self, head, **overrides):
        args = arguments(self.source, self.auth)
        args.pop('source_bytes')
        args.update(overrides)
        return EpochStore(self.tmp.name).reopen(expected_head=head, **args)

    def test_restart_idempotent_mid_tool_resume(self):
        head = self.write()
        self.assertEqual(self.write(), head)
        first = self.reopen(head)
        self.assertEqual(first, self.reopen(head))
        self.assertEqual(first['historical_a'] + first['active_roles'], self.source['messages'])
        self.assertEqual(first['source']['projection'][0]['origins'], ['event:0'])
        self.assertNotEqual(first['source']['projection'][0]['origins'], first['source']['projection'][1]['origins'])
        first['historical_a'][0]['content'] = 'mutated'
        self.assertEqual(self.reopen(head)['historical_a'][0]['content'], 'same')

    def test_current_authorization_revalidated(self):
        head = self.write()
        for mutation in ('revoke', 'hidden', 'transfer', 'issuer', 'head', 'audience'):
            with self.subTest(mutation=mutation):
                auth = copy.deepcopy(self.auth)
                if mutation == 'revoke': auth['grants'].clear()
                elif mutation == 'head': auth['heads']['chat:0:message:1'] = '0' * 64
                elif mutation == 'audience': auth['audience'] = ['guest']
                else: auth['grants']['g:1'][{'hidden': 'presence_hidden'}.get(mutation, mutation)] = True
                args = arguments(self.source, auth)
                args.pop('source_bytes')
                with self.assertRaises(ContractError): self.reopen(head, **args)
        for now in (9, 20):
            with self.assertRaises(ContractError): self.reopen(head, now=now)

    def test_projection_and_completeness(self):
        for mutation in ('missing', 'reorder', 'duplicate', 'edit', 'no_receipt', 'legacy', 'namespace', 'bad_cut'):
            with self.subTest(mutation=mutation):
                source = copy.deepcopy(self.source)
                if mutation == 'missing': source['projection'].pop()
                elif mutation == 'reorder': source['receipts'].reverse()
                elif mutation == 'duplicate': source['projection'][1]['origins'] = ['event:0']
                elif mutation == 'edit': source['messages'][0]['content'] = 'edit'
                elif mutation == 'no_receipt': del source['receipts'][0]['capture']
                elif mutation == 'legacy': source['schema'] = 'praxis.run.v1'
                elif mutation == 'namespace': source['namespace'] = 'other'
                else: source['historical_count'] = True
                with self.assertRaises(ContractError): validate(**arguments(source, self.auth))

    def test_coalesced_and_synthetic_projection(self):
        source = self.source
        source['messages'][0]['content'] = 'same\nsame'
        source['messages'].pop(1)
        source['projection'][0].update(origins=['event:0', 'event:1'], transform='join-with-newline',
                                       message_digest=_digest(source['messages'][0]))
        source['projection'].pop(1)
        for i, row in enumerate(source['projection']): row['index'] = i
        source['receipts'][0]['kind'] = 'synthetic'
        self.auth['heads'][source['receipts'][0]['key']] = _digest(source['receipts'][0])
        self.assertEqual(validate(**arguments(source, self.auth)), source)

    def test_edit_delete_lineage(self):
        for deleted in (False, True):
            source, auth = fixture()
            old = source['receipts'][0]
            revised = dict(old, event='edit:1', revision=1, parent=_digest(old), deleted=deleted,
                           payload_digest=_digest('new'))
            source['receipts'].append(revised)
            auth['heads'][old['key']] = _digest(revised)
            # Old projected original cannot survive edit OR delete, even identical text.
            with self.assertRaises(ContractError): validate(**arguments(source, auth))
            source['messages'].pop(0)
            source['projection'].pop(0)
            source['historical_count'] = 2
            if not deleted:
                source['messages'].append({'role': 'user', 'content': 'new'})
                source['projection'].append(dict(index=0, message_digest=_digest(source['messages'][-1]),
                      origins=['edit:1'], transform='exact', issuer='projector:1'))
            for i, row in enumerate(source['projection']): row['index'] = i
            validate(**arguments(source, auth))
            revised['parent'] = None
            auth['heads'][old['key']] = _digest(revised)
            with self.assertRaises(ContractError): validate(**arguments(source, auth))

    def test_historical_json_numeric_types_require_new_epoch(self):
        # Python equality is weaker than canonical JSON identity. Reissue all
        # projection/source/auth pins so only the same-epoch invariant rejects.
        for before, after in ((True, 1), (1, 1.0), (False, 0), (0.0, -0.0)):
            for location in ('extra', 'tool_input'):
                with self.subTest(before=repr(before), after=repr(after), location=location):
                    self.source, self.auth = fixture()
                    index = 0 if location == 'extra' else 2
                    message = self.source['messages'][index]
                    target = message if location == 'extra' else message['content'][0]['input']
                    target['nested'] = {'values': [before]}
                    self.source['projection'][index]['message_digest'] = _digest(message)
                    with tempfile.TemporaryDirectory() as directory:
                        self.store = EpochStore(directory)
                        head = self.write()
                        old_messages = copy.deepcopy(self.source['messages'])
                        target['nested']['values'][0] = after
                        self.assertEqual(old_messages, self.source['messages'])
                        self.assertNotEqual(_digest(old_messages), _digest(self.source['messages']))
                        self.source['projection'][index]['message_digest'] = _digest(message)
                        self.auth['revision'] = 'auth:2'
                        validate(**arguments(self.source, self.auth))
                        with self.assertRaisesRegex(ContractError, 'A changed'):
                            self.write(expected_parent=head)
                        self.assertEqual((Path(directory) / 'HEAD').read_text(), head)
                        # A new epoch remains the explicit escape hatch; its
                        # source is separately validated with reissued pins.
                        self.write(expected_parent=head, epoch='A:2', reason='typed change')

    def test_receipt_numeric_type_aliases_reject_with_fresh_pins(self):
        for field, value in (('revision', False), ('revision', 0.0),
                             ('deleted', 0), ('deleted', 0.0),
                             ('presence_hidden', 0), ('presence_hidden', 0.0)):
            with self.subTest(field=field, value=repr(value)):
                source, auth = fixture()
                receipt = source['receipts'][0]
                target = receipt['capture'] if field == 'presence_hidden' else receipt
                old = copy.deepcopy(receipt)
                target[field] = value
                self.assertEqual(old, receipt)
                self.assertNotEqual(_digest(old), _digest(receipt))
                auth['heads'][receipt['key']] = _digest(receipt)
                if field == 'presence_hidden':
                    auth['grants']['g:1'][field] = value
                with self.assertRaises(ContractError):
                    validate(**arguments(source, auth))

    def test_stale_parent_and_explicit_flip(self):
        head = self.write()
        with self.assertRaises(ContractError): self.write(reason='different')
        self.source['historical_count'] = 1
        with self.assertRaises(ContractError): self.write(expected_parent=head)
        next_head = self.write(expected_parent=head, epoch='A:2', reason='explicit reset')
        self.assertNotEqual(head, next_head)
        with self.assertRaises(ContractError): self.reopen(head)
        self.reopen(next_head)

    def test_selected_grant_strict_capture_schema(self):
        for hidden, numeric in ((False, 0), (True, 1)):
            source, auth = fixture()
            for receipt in source['receipts']:
                receipt['capture']['presence_hidden'] = hidden
            auth['heads'] = {r['key']: _digest(r) for r in source['receipts']}
            auth['grants']['g:1']['presence_hidden'] = numeric
            with self.subTest(hidden=hidden), self.assertRaises(ContractError):
                validate(**arguments(source, auth))
        grant = self.auth['grants']['g:1']
        malformed = [None, [], dict(grant, extra='field')]
        for field in grant:
            missing = dict(grant)
            del missing[field]
            malformed.append(missing)
            for invalid in (None, 1, {}, []):
                malformed.append(dict(grant, **{field: invalid}))
        malformed += [dict(grant, audience=[1]), dict(grant, audience=['owner', 'owner'])]
        for value in malformed:
            auth = copy.deepcopy(self.auth)
            auth['grants']['g:1'] = value
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate(**arguments(self.source, auth))

    def test_post_head_replace_sync_failure_retry(self):
        from keat_epoch import _sync_directory
        calls = []
        def fail_head_sync(path):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('HEAD directory sync failed after replace')
            _sync_directory(path)
        with patch('keat_epoch._sync_directory', side_effect=fail_head_sync):
            with self.assertRaises(OSError): self.write()
        head = (self.store.directory / 'HEAD').read_text()
        self.store = EpochStore(self.tmp.name)
        with patch('keat_epoch.os.fsync', side_effect=OSError('still failing')) as sync:
            with self.assertRaises(OSError): self.write()
            self.assertGreater(sync.call_count, 0)
        with patch('keat_epoch._sync_directory', side_effect=OSError('directory still failing')):
            with self.assertRaises(OSError): self.write()
        self.assertEqual(self.write(), head)
        self.assertEqual(self.reopen(head)['checkpoint'], head)

    def test_nested_store_entries_synced_and_retry_after_parent_failure(self):
        from keat_epoch import _sync_directory
        self.store = EpochStore(Path(self.tmp.name) / 'new' / 'nested' / 'store')
        failed_parent = self.store.directory.parent.parent
        calls = []
        def fail_parent(path):
            calls.append(path)
            if path == failed_parent:
                raise OSError('parent entry not durable')
            _sync_directory(path)
        with patch('keat_epoch._sync_directory', side_effect=fail_parent):
            with self.assertRaises(OSError): self.write()
        self.assertIn(failed_parent, calls)
        self.store = EpochStore(self.store.directory)
        with patch('keat_epoch._sync_directory', side_effect=fail_parent):
            with self.assertRaises(OSError): self.write()
        with patch('keat_epoch._sync_directory', wraps=_sync_directory) as sync:
            head = self.write()
        self.assertEqual([c.args[0] for c in sync.call_args_list],
                         [self.store.directory, *self.store.directory.parents])
        self.assertEqual(self.write(), head)

    def test_existing_object_after_interrupted_directory_sync(self):
        from keat_epoch import _sync_file
        with patch('keat_epoch._sync_directory', side_effect=OSError('object publication sync')):
            with self.assertRaises(OSError): self.write()
        self.assertFalse((self.store.directory / 'HEAD').exists())
        objects = list(self.store.directory.glob('*.json'))
        self.assertEqual(len(objects), 1)
        with patch('keat_epoch._sync_file', side_effect=OSError('object still not durable')):
            with self.assertRaises(OSError): self.write()
        self.assertFalse((self.store.directory / 'HEAD').exists())
        with patch('keat_epoch._sync_file', wraps=_sync_file) as sync:
            head = self.write()
        self.assertEqual(sync.call_args_list[0].args[0], objects[0])
        self.assertEqual(head, objects[0].stem)
        self.assertEqual(self.write(), head)

    def test_parallel_writers_compare_and_swap(self):
        def write(reason):
            try: return self.write(reason=reason)
            except ContractError: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ['one', 'two']))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_interruption_before_head_then_retry(self):
        original = self.store._atomic
        def interrupted(name, raw):
            if name == 'HEAD': raise OSError('injected prepublication crash')
            original(name, raw)
        with patch.object(self.store, '_atomic', side_effect=interrupted):
            with self.assertRaises(OSError): self.write()
        self.assertIsNone(self.store._head())
        self.reopen(self.write())

    def test_corrupt_checkpoint_head_ancestor_and_pins(self):
        head = self.write()
        path = self.store.directory / (head + '.json')
        raw = path.read_bytes()
        path.write_bytes(raw + b' ')
        with self.assertRaises(ContractError): self.reopen(head)
        with self.assertRaises(ContractError): self.write()
        path.write_bytes(raw)
        next_head = self.write(expected_parent=head, epoch='A:2')
        path.unlink()
        with self.assertRaises(ContractError): self.reopen(next_head)
        (self.store.directory / 'HEAD').write_bytes(b'broken')
        with self.assertRaises(ContractError): self.reopen(next_head)
        args = arguments(self.source, self.auth)
        args['pins'] = replace(args['pins'], authority_revision='stale')
        with self.assertRaises(ContractError): validate(**args)
        args = arguments(self.source, self.auth)
        args['source_bytes'] += b' '
        with self.assertRaises(ContractError): validate(**args)

    def test_schema_identity_is_not_epoch_policy(self):
        head = self.write()
        self.source['tools'][0]['description'] = 'changed with same name'
        child = self.write(expected_parent=head, reason='new schema')
        self.assertEqual(self.reopen(child)['epoch'], 'A:1')
        self.assertNotEqual(self.store._load(head)['schema_digest'], self.store._load(child)['schema_digest'])


if __name__ == '__main__':
    unittest.main()
