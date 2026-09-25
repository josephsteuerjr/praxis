"""Native Telegram provenance: admission, durable life rows and positional projection."""
import os
from unittest.mock import patch

os.environ.setdefault('PRAXIS_TEST', '1')
import memory_life as ml
import mtproto_runner as runner
import keat_live
from test_life_places import Base


class NativeLifeTests(Base):
    def setUp(self):
        super().setUp()
        self.guard = patch.object(runner, '_persisted_life_sources', set())
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def record(self, mid, line, direction='in', capture_live=True):
        return runner._record_life_message(
            '42', line, actor='Praxis' if direction == 'out' else 'Owner',
            direction=direction, source_id=mid, is_dm=True, ts=1700000000 + mid,
            dedupe_key=f'capture:{mid}', capture_live=capture_live)

    def test_admission_metadata_survives_rebuild_and_legacy_is_not_reissued(self):
        ref = {'namespace': 'n', 'event': 'e', 'key': 'k', 'payload_digest': 'd'}
        with patch.object(keat_live, 'capture_ingress', return_value=ref, create=True) as capture:
            self.assertTrue(self.record(1, 'Owner: hello'))
            self.assertTrue(self.record(1, 'Owner: hello'))
            self.assertTrue(self.record(2, 'Owner: old', capture_live=False))
            capture.assert_called_once_with('42', is_dm=True, source_id='1', direction='in',
                                           payload={'role': 'user', 'content': 'Owner: hello', 'actor': 'Owner'})
        events = list(ml.iter_events(chat_id='42', kinds={'conversation_message'}))
        self.assertEqual(events[0]['meta']['keat_occurrence'], ref)
        rows = ml.hot_records('42')
        self.assertEqual(rows[0]['meta']['keat_occurrence'], ref)
        self.assertEqual(rows[0]['id'], events[0]['id'])
        ml.rebuild_state('42')
        self.assertEqual(ml.hot_records('42')[0]['meta']['keat_occurrence'], ref)
        self.assertNotIn('keat_occurrence', ml.hot_records('42')[1]['meta'])

    def test_real_native_capture_rebuild_projection_checkpoint(self):
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root:
            policy = Path(root) / 'policy.json'
            policy.write_text(json.dumps(dict(
                schema='keat.capture-policy.v1', namespace='native-test', root=root,
                enrollments=[dict(mode='owner', stream='42', capture=dict(
                    issuer='approved', policy_revision='p1', audience=['owner'],
                    transfer='none', presence_hidden=False))])))
            with patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': 'on',
                    'PRAXIS_KEAT_CAPTURE_POLICY': str(policy), 'PRAXIS_KEAT': 'serve',
                    'PRAXIS_KEAT_MODES': 'owner', 'PRAXIS_KEAT_STREAMS': '42'}):
                self.record(1, 'Owner: same')
                self.record(2, 'Owner: same')
                self.record(3, 'Praxis: answer', 'out')
                self.record(4, 'Owner: next')
                ml.rebuild_state('42')
                sidecar = {}
                history, current = runner._dm_dialogue('42', occurrence_sidecar=sidecar)
                state = keat_live.adopt_projection(
                    SimpleNamespace(chat_id='42'), history, current, sidecar)
                self.assertIsNotNone(state)
                tape = history + [dict(role='user', content=current)]
                system = [dict(type='text', text='constitution')]
                with keat_live.bind_turn(state, tape):
                    provider = keat_live.prepare_provider_messages(system, tape, [])
                    receipt = keat_live.select(system, provider, [], 'run-native', 'call-native')
                self.assertEqual(receipt['status'], 'served')
                saved = dict(system=system, messages=tape, tools=[], keat=receipt)
                with keat_live.bind_resume(saved, 'run-native', 'call-native') as reopened:
                    self.assertIsNotNone(reopened)
                    self.assertEqual(reopened.historical_count, 2)
                ml.note_message_revision('42', 1, 'Owner: edited', actor='Owner')
                with keat_live.bind_resume(saved, 'run-native', 'call-native') as revoked:
                    self.assertIsNone(revoked)
                history[0]['content'] = 'forged'
                self.assertIsNone(keat_live.adopt_projection(
                    SimpleNamespace(chat_id='42'), history, current, sidecar))

    def test_split_outgoing_chunks_are_individual_revocable_occurrences(self):
        """A Telegram split is two originals, never ``17,18`` as one source id."""
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as root:
            policy = Path(root) / 'policy.json'
            policy.write_text(json.dumps(dict(
                schema='keat.capture-policy.v1', namespace='chunk-test', root=root,
                enrollments=[dict(mode='owner', stream='42', capture=dict(
                    issuer='approved', policy_revision='p1', audience=['owner'],
                    transfer='none', presence_hidden=False))])))
            env = {'PRAXIS_KEAT_CAPTURE': 'on', 'PRAXIS_KEAT_CAPTURE_POLICY': str(policy),
                   'PRAXIS_KEAT': 'serve', 'PRAXIS_KEAT_MODES': 'owner',
                   'PRAXIS_KEAT_STREAMS': '42'}
            with patch.dict(os.environ, env):
                self.record(17, 'Praxis: first chunk', 'out')
                self.record(18, 'Praxis: second chunk', 'out')
                self.record(19, 'Owner: next')
                ml.rebuild_state('42')
                sidecar = {}
                history, current = runner._dm_dialogue('42', occurrence_sidecar=sidecar)
                self.assertEqual([ref['key'].split(':')[3] for ref in sidecar['history'][0]],
                                 ['17', '18'])
                self.assertTrue(all(',' not in ref['key'] for group in sidecar['history']
                                    for ref in group))
                state = keat_live.adopt_projection(SimpleNamespace(chat_id='42'), history,
                                                    current, sidecar)
                tape = history + [dict(role='user', content=current)]
                system = [dict(type='text', text='constitution')]
                with keat_live.bind_turn(state, tape):
                    provider = keat_live.prepare_provider_messages(system, tape, [])
                    receipt = keat_live.select(system, provider, [], 'run-chunks', 'call-chunks')
                saved = dict(system=system, messages=tape, tools=[], keat=receipt)
                with keat_live.bind_resume(saved, 'run-chunks', 'call-chunks') as reopened:
                    self.assertIsNotNone(reopened)
                # Both actual Telegram IDs independently revoke this composite
                # rendered assistant history; no composite grant can survive.
                for message_id in (17, 18):
                    ml.note_message_revision('42', message_id, 'Praxis: edited', actor='Praxis')
                    with keat_live.bind_resume(saved, 'run-chunks', 'call-chunks') as reopened:
                        self.assertIsNone(reopened)

    def test_partially_captured_dialogue_is_not_narrowed_to_the_captured_tail(self):
        """26.09: тёплая лента, чьи старые строки старше захвата, остаётся ЦЕЛИКОМ.

        Прежний стенд закреплял обратное: «хвост» после последней незахваченной строки
        обслуживался один, а `legacy answer` из вызова выпадал. В живой личке её ответы
        не захватываются (рука `reply`), и этот хвост — всегда строки владельца после её
        последнего ответа: замер 21–25.09 — 21 обслуженный ход из 21 нёс в модель одну
        реплику при ленте в 49–78 сообщений. Проекция отдаётся только при полном захвате;
        иначе ход идёт прежним путём — с историей.
        """
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as root:
            policy = Path(root) / 'policy.json'
            policy.write_text(json.dumps(dict(
                schema='keat.capture-policy.v1', namespace='native-suffix-test', root=root,
                enrollments=[dict(mode='owner', stream='42', capture=dict(
                    issuer='approved', policy_revision='p1', audience=['owner'],
                    transfer='none', presence_hidden=False))])))
            env = {'PRAXIS_KEAT_CAPTURE': 'on', 'PRAXIS_KEAT_CAPTURE_POLICY': str(policy),
                   'PRAXIS_KEAT': 'serve', 'PRAXIS_KEAT_MODES': 'owner',
                   'PRAXIS_KEAT_STREAMS': '42'}
            with patch.dict(os.environ, env):
                self.record(3948, 'Owner: legacy question', capture_live=False)
                self.record(3949, 'Praxis: legacy answer', 'out', capture_live=False)
                self.record(3950, 'Owner: first buffered message')
                self.record(3951, 'Owner: second buffered message')
                ml.rebuild_state('42')
                sidecar = {'stale': True}
                history, current = runner._dm_dialogue('42', occurrence_sidecar=sidecar)
                self.assertEqual(history[-1], {'role': 'assistant', 'content': 'legacy answer'})
                self.assertEqual(current, 'first buffered message\nsecond buffered message')
                self.assertEqual(sidecar, {}, 'захваченный хвост не сужает ленту хода')

    def test_failed_capture_does_not_lose_live_message(self):
        with patch.object(keat_live, 'capture_ingress', side_effect=ValueError('no policy'), create=True):
            self.assertTrue(self.record(1, 'Owner: hello'))
        self.assertNotIn('keat_occurrence', ml.hot_records('42')[0]['meta'])

    def test_positional_coalescing_of_identical_occurrences(self):
        refs = [dict(event=f'e{i}') for i in range(5)]
        with patch.object(keat_live, 'capture_ingress', side_effect=refs, create=True):
            for i, direction in enumerate(['in', 'in', 'out', 'in', 'in']):
                self.record(i + 1, ('Praxis' if direction == 'out' else 'Owner') + ': same', direction)
        ml.rebuild_state('42')
        sidecar = {}
        with patch.dict(os.environ, {'PRAXIS_DIALOGUE_ROLES': '1'}):
            history, current = runner._dm_dialogue('42', occurrence_sidecar=sidecar)
            self.assertEqual(history, [{'role': 'user', 'content': 'same\n\nsame'},
                                       {'role': 'assistant', 'content': 'same'}])
            self.assertEqual(current, 'same\nsame')
            self.assertEqual(sidecar, {'history': [refs[:2], refs[2:3]], 'current': refs[3:]})
            # A live revision without a fresh issuance must invalidate old metadata.
            ml.record_message('42', 'Owner: edited', actor='Owner', source='telegram',
                              source_id='5:edit:1700000010:abc', direction='in', ts=1700000010)
            ml.note_message_revision('42', 5, 'Owner: edited', actor='Owner')
            runner._dm_dialogue('42', occurrence_sidecar=sidecar)
            self.assertEqual(sidecar, {})

    def test_legacy_or_first_turn_uses_unchanged_fallback(self):
        with patch.object(keat_live, 'capture_ingress', return_value={'event': 'e'}, create=True):
            self.record(1, 'Owner: first')
        with patch.dict(os.environ, {'PRAXIS_DIALOGUE_ROLES': '1'}):
            sidecar = {}
            self.assertEqual(runner._dm_dialogue('42', occurrence_sidecar=sidecar), ([], ''))
            self.assertEqual(sidecar, {})
            self.record(2, 'Praxis: legacy', 'out', capture_live=False)
            self.record(3, 'Owner: next', capture_live=False)
            self.assertEqual(runner._dm_dialogue('42', occurrence_sidecar=sidecar)[1], 'next')
            self.assertEqual(sidecar, {})

class LogicalSendRegressionTests(Base):
    """Execute the actual _run_pass transport/persistence block, no provider/network.

    AST extraction excludes unrelated model/wake machinery; all send receipt,
    partial-failure, buffer and canonical persistence statements are production.
    """
    def send(self, chunks, ids, fail_at=None):
        import ast
        import asyncio
        import inspect
        import textwrap
        from types import SimpleNamespace
        tree = ast.parse(textwrap.dedent(inspect.getsource(runner._run_pass)))
        fn = tree.body[0]
        # Locate the real contiguous send block nested in _run_pass's try.
        body = next(node.body for node in ast.walk(fn) if isinstance(node, ast.Try)
                    and any(isinstance(n, ast.AnnAssign) and
                            isinstance(n.target, ast.Name) and n.target.id == 'sent_chunks'
                            for n in node.body))
        start = next(i for i, n in enumerate(body) if isinstance(n, ast.AnnAssign)
                     and isinstance(n.target, ast.Name) and n.target.id == 'sent_chunks')
        end = next(i for i in range(start, len(body)) if isinstance(body[i], ast.If)
                   and isinstance(body[i].test, ast.Name) and body[i].test.id == 'sent_reply')
        statements = body[start:end + 1]
        # The remainder of this if only logs/raises/marks delivery; stop after
        # the production persistence call, preserving the actual send loop.
        last = statements[-1]
        stop = next(i for i, n in enumerate(last.body) if isinstance(n, ast.Expr)
                    and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
                    and n.value.func.id == '_persist_sent_reply')
        last.body = last.body[:stop + 1]
        wrapper = ast.AsyncFunctionDef(name='send_block', args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=statements, decorator_list=[])
        namespace = dict(vars(runner), chunks=chunks, delivery_run_id='', wake=None,
                         reply_to=None, route=SimpleNamespace(topic_id=None),
                         chat_id='42', addressed_mid=1, reply=''.join(chunks),
                         entity=object(), is_dm=True, envelope=SimpleNamespace(boundary=False), delivery_message_ids=[])
        calls = []
        async def transport(entity, chunk, **kwargs):
            index = len(calls)
            calls.append(chunk)
            if index == fail_at:
                raise RuntimeError('transport failed')
            return SimpleNamespace(id=ids[index]), 123
        namespace['_send_message_idempotent'] = transport
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                     '<production _run_pass send block>', 'exec'), namespace)
        asyncio.run(namespace['send_block']())

    def exercise(self, mode, partial=False):
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from collections import defaultdict, deque
        with tempfile.TemporaryDirectory() as root:
            policy = Path(root) / 'policy.json'
            policy.write_text(json.dumps(dict(
                schema='keat.capture-policy.v1', namespace='send-test', root=root,
                enrollments=[dict(mode='owner', stream='42', capture=dict(
                    issuer='approved', policy_revision='p1', audience=['owner'],
                    transfer='none', presence_hidden=False))])))
            env = {'PRAXIS_KEAT_CAPTURE': 'off' if mode == 'capture-off' else 'on',
                   'PRAXIS_KEAT_CAPTURE_POLICY': str(policy),
                   'PRAXIS_KEAT': 'off' if mode == 'serve-off' else 'serve',
                   'PRAXIS_KEAT_MODES': 'owner', 'PRAXIS_KEAT_STREAMS': '42',
                   'PRAXIS_DIALOGUE_ROLES': '1'}
            with patch.dict(os.environ, env), \
                    patch.object(runner, '_under_tests', return_value=False), \
                    patch.object(runner, '_persisted_life_sources', set()), \
                    patch.object(runner, '_buf', defaultdict(lambda: deque(maxlen=60))), \
                    patch.object(runner, '_buffer_message_ids', defaultdict(deque)), \
                    patch.object(runner.bufstore, 'meta_update'):
                chunks = ['first chunk ', '\n', ' second chunk']
                self.send(chunks + (['unsent'] if partial else []), [17, 18, 19],
                          fail_at=3 if partial else None)
                self.send(['independent'], [20])
                self.assertEqual(list(runner._buf['42']),
                                 ['Praxis: ' + ''.join(chunks), 'Praxis: independent'])
                runner._record_life_message('42', 'Owner: next', actor='Owner',
                    direction='in', source_id=21, is_dm=True, ts=None,
                    dedupe_key='telegram:42:21:in', capture_live=mode != 'ineligible')
                ml.rebuild_state('42')
                rows = ml.hot_records('42')
                self.assertEqual([str(r['source_id']) for r in rows],
                                 ['17', '18', '19', '20', '21'])
                sidecar = {}
                history, current = runner._dm_dialogue('42', occurrence_sidecar=sidecar)
                legacy = runner._turns_to_dialogue([(True, ''.join(chunks)),
                                                   (True, 'independent'), (False, 'next')])
                self.assertEqual((history, current), legacy)
                state = keat_live.adopt_projection(SimpleNamespace(chat_id='42'),
                                                   history, current, sidecar)
                tape = history + [dict(role='user', content=current)]
                system = [dict(type='text', text='constitution')]
                with keat_live.bind_turn(state, tape):
                    provider = keat_live.prepare_provider_messages(system, tape, [])
                    receipt = keat_live.select(system, provider, [], 'run-send', 'call-send')
                self.assertEqual(tape, legacy[0] + [dict(role='user', content=legacy[1])])
                if mode in ('capture-off', 'ineligible'):
                    self.assertIsNone(state)
                if mode == 'eligible':
                    self.assertIsNotNone(state)
                    self.assertEqual(receipt['status'], 'served')
                    refs = sidecar['history'][0]
                    self.assertEqual(len(refs), 4)
                    # Canonical display metadata cannot silently change the
                    # issuer-bound projection separators.
                    import copy
                    changed = copy.deepcopy(rows)
                    changed[1]['meta']['logical_send']['index'] = 9
                    with patch.object(ml, 'hot_records', return_value=changed):
                        altered = {}
                        h, c = runner._dm_dialogue('42', occurrence_sidecar=altered)
                    self.assertIsNone(keat_live.adopt_projection(
                        SimpleNamespace(chat_id='42'), h, c, altered))
                    saved = dict(system=system, messages=tape, tools=[], keat=receipt)
                    with keat_live.bind_resume(saved, 'run-send', 'call-send') as reopened:
                        self.assertIsNotNone(reopened)
                    # Revoke one interior whitespace chunk, not a composite ID.
                    ml.record_message('42', 'Praxis: edited', actor='Praxis', direction='out',
                                      source='telegram', source_id='18:edit:1800000000:abc',
                                      ts=1800000000)
                    ml.note_message_revision('42', 18, 'Praxis: edited', actor='Praxis')
                    with keat_live.bind_resume(saved, 'run-send', 'call-send') as reopened:
                        self.assertIsNone(reopened)
                    ml.rebuild_state('42')
                    runner._dm_dialogue('42', occurrence_sidecar=sidecar)
                    self.assertEqual(sidecar, {})
                else:
                    self.assertNotEqual((receipt or {}).get('status'), 'served')

    def test_capture_disabled(self):
        self.exercise('capture-off')

    def test_serving_disabled(self):
        self.exercise('serve-off')

    def test_ineligible_fallback(self):
        self.exercise('ineligible')

    def test_eligible_projection_and_individual_edit(self):
        self.exercise('eligible')

    def test_partial_send_preserves_only_accepted_prefix(self):
        self.exercise('eligible', partial=True)

    def test_batch_order_is_explicit_not_inferred_from_text(self):
        from logical_send import batch_groups
        batch = [dict(id='a', index=i, count=3) for i in range(3)]
        self.assertEqual(batch_groups(batch), [[0, 1, 2]])
        self.assertEqual(batch_groups(batch[::-1]), [[0], [1], [2]])
        self.assertEqual(batch_groups(batch[:2]), [[0], [1]])
        self.assertEqual(batch_groups(batch + [dict(id='b', index=0, count=1)]),
                         [[0, 1, 2], [3]])

    def test_failure_before_first_acceptance_persists_nothing(self):
        with patch.object(runner, '_persist_sent_reply') as persist:
            self.send(['not accepted'], [], fail_at=0)
        persist.assert_not_called()
