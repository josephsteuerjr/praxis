"""Real edition agent -> work_loop -> durable checkpoint -> inbox ACK.

Run in copied Linux with HELENE_TREE_SRC pointing to the candidate payload.
No model, Telegram, installed data, or Forge worker is started.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [os.environ.get('HELENE_TREE_SRC', str(DESK.parent / 'helene/core')),
               str(DESK), str(DESK / 'localharness'), str(DESK.parent / 'praxis')]
import agent
import run_context
import work_loop
import runner
import turn_inbox


class OwnerInputBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = run_context.RunContext.create(
            kind='chat_turn', goal='owner boundary', principal_id='test', scope='owner',
            delivery_chat_id='window')
        self.events = []
        self.saved = None
        self.manager = SimpleNamespace(store_result=self.store,
                                       path=lambda _: self.root / 'runs' / self.current.run_id)
        self.addCleanup(work_loop.release, self.current.run_id)

    def store(self, run_id, payload, **kwargs):
        self.events.append('checkpoint')
        self.saved = json.loads(payload)

    def drain(self, callback, messages, prior=None, fail_checkpoint=False):
        original = agent._persist_tool_loop_checkpoint
        def checkpoint(**kwargs):
            if fail_checkpoint:
                self.events.append('checkpoint-refused')
                raise RuntimeError('storage unavailable')
            return original(**kwargs)
        with run_context.bind_run(self.current), \
             patch.object(agent, 'OWNER_INPUT_DRAIN', callback, create=True), \
             patch.object(agent, '_runs', return_value=self.manager), \
             patch.object(agent, '_append_captured_messages', side_effect=lambda m, b: m.extend(b)), \
             patch.object(agent, '_persist_tool_loop_checkpoint', side_effect=checkpoint), \
             patch('process_scope.read_boundary'):
            return agent._drain_owner_input('system', messages, [], 1, prior)

    def test_new_input_reopens_closure_and_checkpoints_before_ack_without_resetting_budget(self):
        with run_context.bind_run(self.current):
            work_loop.spend(); work_loop.spend()
            work_loop.note_sent('already delivered')
            work_loop.note_finished('old closure')
            work_loop.claim('done', why='old closure')
        batch = [{'role': 'user', 'content': 'one more thing'}]
        messages = []
        self.assertTrue(self.drain(lambda *_: (batch, lambda: self.events.append('ack')), messages))
        self.assertEqual(self.events, ['checkpoint', 'ack'])
        self.assertEqual(self.saved['messages'], messages)
        state = self.saved['work_loop']
        self.assertEqual(state['used'], 2)
        self.assertEqual(state['sent'], 1)
        self.assertFalse(state['finished'])
        self.assertIsNone(state['control'])
        with run_context.bind_run(self.current):
            self.assertEqual(work_loop.spoken(), ('already delivered',))

    def test_initial_files_enter_first_model_input_and_source_survives(self):
        source = self.root / 'memory/.control/desk_inbox/attachments/message/readme.txt'
        source.parent.mkdir(parents=True); source.write_bytes(b'actual attachment')
        initial = runner._INITIAL_FILES.set({'room': 'window', 'source_id': 'message',
                                             'paths': ['attachments/message/readme.txt']})
        self.addCleanup(runner._INITIAL_FILES.reset, initial)
        messages = []
        with patch.object(runner, '_tree', self.root), patch.object(runner, '_agent', agent), \
             patch('turn_inbox.collect', return_value=([], lambda: self.events.append('ack'))):
            self.assertTrue(self.drain(runner._drain_owner_inputs, messages))
        target = self.manager.path(self.current.run_id) / 'files/readme.txt'
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertIn(str(target), self.saved['messages'][0]['content'])
        self.assertTrue(source.is_file())
        self.assertEqual(self.events, ['checkpoint', 'ack'])

    def test_storage_failure_never_acknowledges_incoming_batch(self):
        with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
            self.drain(lambda *_: ([{'role': 'user', 'content': 'owner'}],
                                   lambda: self.events.append('ack')), [], fail_checkpoint=True)
        self.assertEqual(self.events, ['checkpoint-refused'])

    def test_real_storage_failure_stops_run_before_ack(self):
        transitions = []
        self.manager.manifest = lambda _: {'status': 'running'}
        def transition(run_id, status, **kwargs):
            transitions.append((run_id, status))
            return {'status': status}
        self.manager.transition = transition
        def refused(*args, **kwargs):
            self.events.append('storage-refused')
            raise OSError('disk unavailable')
        self.manager.store_result = refused
        with self.assertRaises(agent.RunStopped):
            self.drain(lambda *_: ([{'role': 'user', 'content': 'owner followup'}],
                                   lambda: self.events.append('ack')), [])
        self.assertEqual(self.events, ['storage-refused'])
        self.assertEqual(transitions, [(self.current.run_id, 'paused')])

    def test_prior_model_response_is_retained_before_new_owner_word(self):
        prior = {'role': 'assistant', 'content': 'response before new word'}
        batch = [{'role': 'user', 'content': 'new word'}]
        self.drain(lambda *_: (batch, lambda: None), [], prior)
        self.assertEqual(self.saved['messages'], [prior, *batch])

    def test_empty_batch_does_not_reopen_or_spend(self):
        with run_context.bind_run(self.current):
            work_loop.claim('done', why='done')
        self.assertFalse(self.drain(lambda *_: ([], lambda: self.events.append('ack')), []))
        self.assertEqual(self.events, ['ack'])
        with run_context.bind_run(self.current):
            self.assertEqual(work_loop.taken()['action'], 'done')

    def test_other_run_closure_is_not_modified(self):
        other = run_context.RunContext.create(kind='chat_turn', goal='other', principal_id='test', scope='owner')
        self.addCleanup(work_loop.release, other.run_id)
        with run_context.bind_run(other):
            work_loop.claim('done', why='other'); work_loop.note_finished('other')
        self.drain(lambda *_: ([{'role': 'user', 'content': 'owner'}], lambda: None), [])
        with run_context.bind_run(other):
            self.assertTrue(work_loop.finished())
            self.assertEqual(work_loop.taken()['action'], 'done')

    def test_real_inbox_source_is_only_done_after_the_checkpoint(self):
        inbox = self.root / 'memory/.control/desk_inbox'; inbox.mkdir(parents=True)
        (inbox / 'one.md').write_text('owner followup', encoding='utf-8')
        archived = []
        desk = SimpleNamespace(archive=lambda *a, **kw: archived.append(kw), life=lambda *a, **kw: None)
        fake = SimpleNamespace(_tree=self.root, _speaker='owner', transport=SimpleNamespace(is_room=lambda _: True),
            _inbox_target=lambda _: 'window', _note_bytes=lambda p: p.read_bytes(),
            _seal_claim=lambda *a, **kw: (True, ''), _message_text=lambda b: b.decode(),
            _split_attachments=lambda t: (t, []), _room=lambda _: desk, _now=lambda: None,
            _mark_done=lambda p, n, w: (self.events.append('ack'), (p / (n + '.done')).write_text(w)))
        self.drain(lambda c, m: turn_inbox.collect(fake, c, m), [])
        self.assertEqual(self.events, ['checkpoint', 'ack'])
        self.assertTrue((inbox / 'processed/one.md').is_file())
        self.assertTrue((inbox / 'processed/one.md.done').is_file())
        self.assertEqual(archived[0]['source_id'], 'note:one')


if __name__ == '__main__':
    unittest.main()
