"""Active-turn addressed input is one exact-topic item per model request."""
import contextlib
import unittest
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import Mock, patch

from test_group_wake_snapshot import runner, agent, _wake


class ActiveAddressedFIFO(unittest.TestCase):
    def setUp(self):
        self.chat = '-700__topic__8'
        self.inbox = runner._ActiveAddressedInbox(self.chat, _wake(mid=10, ts=10))
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(runner, '_active_addressed_inboxes', {self.chat: self.inbox}))
        self.stack.enter_context(patch.object(runner, '_group_wakes', {}))
        self.stack.enter_context(patch.object(runner.addressed_replies, 'covered', return_value=False))
        self.stack.enter_context(patch.object(runner, '_note_address_overwritten'))

    def incoming(self, mid, text='', chat=None, addressed=True):
        wake = replace(_wake(mid=mid, ts=mid), query=text, inbound_text=text, addressed=addressed)
        runner._install_group_wake(chat or self.chat, wake)
        return wake

    def test_fifo_exact_topic_only_addressed_and_deduplicated(self):
        self.incoming(11, 'first')
        self.incoming(12, 'ambient', addressed=False)
        self.incoming(13, 'foreign', chat='-700__topic__9')
        self.incoming(14, 'second')
        self.incoming(14, 'duplicate')
        self.assertIn('first', self.inbox.take())
        self.assertIsNone(self.inbox.observed)
        self.inbox.presented()
        self.assertEqual(self.inbox.observed, 11)
        self.assertIn('second', self.inbox.take())
        self.assertEqual(self.inbox.observed, 11)
        self.inbox.presented()
        self.assertEqual(self.inbox.observed, 14)
        self.assertIsNone(self.inbox.take())

    def test_model_calls_receive_one_each_and_not_system_authority(self):
        self.incoming(11, 'one')
        self.incoming(12, 'two')
        messages = [{'role': 'user', 'content': 'initial'}]
        seen = []
        def model(system, messages, tools):
            seen.append([dict(m) for m in messages])
            return 'ok'
        token = agent._TURN_ADDRESSED_INBOUND.set(self.inbox)
        try:
            with patch.object(agent, '_model_call_impl', side_effect=model), patch.object(agent.keat_live, 'discard_provider'):
                agent._model_call('system', messages)
                agent._model_call('system', messages)
        finally:
            agent._TURN_ADDRESSED_INBOUND.reset(token)
        self.assertIn('one', seen[0][-1]['content'])
        self.assertNotIn('two', str(seen[0]))
        self.assertIn('two', seen[1][-1]['content'])
        self.assertEqual(seen[1][-1]['role'], 'user')

    def test_arrival_after_last_model_boundary_stays_unread(self):
        self.inbox.presented()
        late = self.incoming(11, 'late')
        self.assertEqual(self.inbox.observed, 10)
        self.assertFalse(runner._release_answered_wake(self.chat, _wake(mid=10, ts=10)))
        self.assertEqual(runner._group_wakes[self.chat].message_id, late.message_id)

    def test_active_arrival_does_not_cancel_model_task(self):
        task = Mock()
        with patch.object(runner, '_debounce', {self.chat: task}), patch.object(runner.asyncio, 'create_task') as create:
            runner._arm(self.chat)
        task.cancel.assert_not_called()
        create.assert_not_called()

    def test_pending_edits_and_deletions(self):
        self.incoming(11, 'old')
        self.incoming(12, 'deleted')
        self.inbox.revise(11, query='new', author='revised')
        self.inbox.revise(12, deleted=True)
        text = self.inbox.take()
        self.assertIn('new', text)
        self.assertIn('revised', text)
        self.assertNotIn('old', text)
        self.assertIsNone(self.inbox.take())

    def test_unrendered_late_media_blocks_coverage_of_later_ids(self):
        import types
        wake = replace(_wake(mid=11, ts=11), query='photo',
                       media_snapshot=(types.SimpleNamespace(message_id=11),))
        self.inbox.put(wake)
        self.incoming(12, 'later text')
        self.assertIn('photo', self.inbox.take())
        self.inbox.presented()
        self.assertEqual(self.inbox.observed, 10)
        self.assertIn('later text', self.inbox.take())
        self.inbox.presented()
        self.assertEqual(self.inbox.observed, 10)

    def test_real_loop_drains_queue_even_after_terminal_model_response(self):
        messages = [{'role': 'user', 'content': 'initial'}]
        seen = []
        def model(system, messages, tools):
            seen.append(str(messages))
            if len(seen) == 1:
                self.incoming(11, 'first-arrival')
                self.incoming(12, 'second-arrival')
            return SimpleNamespace(stop_reason='end_turn', text='answer', blocks=[])
        token = agent._TURN_ADDRESSED_INBOUND.set(self.inbox)
        try:
            with patch.object(agent, '_model_call_impl', side_effect=model), \
                    patch.object(agent, '_run_status_gate'), \
                    patch.object(agent, '_arm_private_floor', return_value=0), \
                    patch.object(agent, '_work_loop_continue', return_value=False), \
                    patch.object(agent.keat_live, 'discard_provider'):
                result = agent._terminal_tool_loop(system='system', messages=messages, tools=[])
        finally:
            agent._TURN_ADDRESSED_INBOUND.reset(token)
        self.assertEqual(result, 'answer')
        self.assertEqual(len(seen), 3)
        self.assertIn('first-arrival', seen[1])
        self.assertNotIn('second-arrival', seen[1])
        self.assertIn('second-arrival', seen[2])

    def test_pending_lower_id_caps_coverage(self):
        self.incoming(13, 'first')
        self.incoming(11, 'second')
        self.inbox.take()
        self.inbox.presented()
        self.assertEqual(self.inbox.observed, 10)
        self.inbox.take()
        self.inbox.presented()
        self.assertEqual(self.inbox.observed, 13)

    def test_lower_arrival_after_provider_boundary_ignores_old_coverage(self):
        self.inbox.presented()
        with patch.object(runner.addressed_replies, 'covered', return_value=True):
            self.incoming(9, 'late lower')
            self.assertIn(self.chat, runner._group_wakes)
            self.assertFalse(runner._release_answered_wake(self.chat, _wake(mid=10, ts=10)))
            # A closed mailbox must not make the unread exemption disappear.
            runner._active_addressed_inboxes.clear()
            self.assertFalse(runner._wake_covered(self.chat, runner._group_wakes[self.chat]))

    def test_exact_read_ids_do_not_invent_older_messages(self):
        self.inbox.presented()
        self.assertEqual(self.inbox.read_ids(), [10])
        self.incoming(13, 'newer')
        self.incoming(11, 'older')
        self.inbox.take()
        self.inbox.presented()
        self.assertEqual(self.inbox.read_ids(), [10, 13])
        self.inbox.take()
        self.inbox.presented()
        self.assertEqual(self.inbox.read_ids(), [10, 11, 13])
