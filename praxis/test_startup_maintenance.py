"""Restart maintenance must leave transport and live turns responsive."""
import asyncio
import unittest
from unittest import mock

import memory_life as life
import mtproto_runner as runner


class StartupMaintenance(unittest.IsolatedAsyncioTestCase):
    async def test_only_one_place_in_flight_and_other_loop_work_can_run(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def check(chat):
            calls.append(chat)
            if chat == 'first':
                entered.set()
                await release.wait()

        with mock.patch.object(runner, '_maybe_compact', side_effect=check):
            task = asyncio.create_task(runner._startup_memory_maintenance(('first', 'second')))
            await entered.wait()
            await asyncio.sleep(0)
            self.assertEqual(calls, ['first'])
            release.set()
            await task
        self.assertEqual(calls, ['first', 'second'])

    async def test_failed_place_does_not_drop_following_places(self):
        with mock.patch.object(runner, '_maybe_compact', side_effect=[RuntimeError('broken'), None]) as check:
            await runner._startup_memory_maintenance(('broken', 'next'))
        self.assertEqual(check.call_args_list, [mock.call('broken'), mock.call('next')])

    async def test_shutdown_cancels_remaining_checks(self):
        with mock.patch.object(runner, '_maybe_compact', side_effect=asyncio.CancelledError) as check:
            with self.assertRaises(asyncio.CancelledError):
                await runner._startup_memory_maintenance(('first', 'second'))
        self.assertEqual(check.call_count, 1)


class RefreshMembership(unittest.TestCase):
    def test_membership_per_distinct_key_and_new_bindings_seen_next_snapshot(self):
        events = {f'event-{i}': {'id': f'event-{i}', 'kind': 'conversation_message',
                              'chat_id': 'alias' if i % 2 else 'unrelated'}
                  for i in range(400)}
        bound = {'alias': 'room'}
        original = life._same_conversation
        with mock.patch.object(life, 'bindings', side_effect=lambda: dict(bound)), \
             mock.patch.object(life, '_same_conversation', wraps=original) as membership:
            result = life._refresh_current_targets(events, list(events), 'room')
            self.assertEqual(len(result), 200)
            self.assertEqual(membership.call_count, 2)
            bound['unrelated'] = 'room'
            self.assertEqual(len(life._refresh_current_targets(events, list(events), 'room')), 400)
            self.assertEqual(membership.call_count, 4)

    def test_peer_lineage_still_accepts_event_without_place_binding(self):
        row = {'id': 'event-one', 'kind': 'conversation_message', 'chat_id': 'foreign'}
        with mock.patch.object(life, '_refresh_logical_target', return_value=('logical', 'room')), \
             mock.patch.object(life, '_same_conversation', side_effect=AssertionError('unnecessary lookup')):
            self.assertEqual(life._refresh_current_targets({'e': row}, ['e'], 'room'), {'logical': row})

    def test_identical_conversation_does_not_need_bindings_file(self):
        with mock.patch.object(life, 'bindings', side_effect=AssertionError('unnecessary read')):
            self.assertTrue(life._same_conversation('room', 'room'))
