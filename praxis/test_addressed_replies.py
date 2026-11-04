"""Accepted-delivery boundary, burst coalescence, recovery and topic isolation."""
import asyncio
import contextlib
import json
import tempfile
import types
import unittest
from collections import defaultdict, deque
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import addressed_replies as receipts
import epoch_repair
import media
from test_group_wake_snapshot import runner, agent, _wake, _Client, _no_compact


class AddressedReplies(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        policy = self.root / "memory/policies/addressed-replies.json"
        policy.parent.mkdir(parents=True)
        policy.write_text('{"enabled": true}')
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(receipts, "BASE", self.root))
        self.stack.enter_context(patch.object(epoch_repair, "BASE", self.root))

    def entry(self, mid=100, **changes):
        return dict(dict(state="accepted", kind="text", purpose="tool:reply",
                         peer_id="-700", topic_id=8, key="run:reply:1",
                         receipt={"message_id": mid},
                         addressed_observed_message_ids=([10, 11, 12, 90, 99] if mid == 100 else [mid - 1])), **changes)

    def test_receipt_is_restart_safe_monotonic_and_exact_topic(self):
        self.assertTrue(receipts.record_outbox(self.entry()))
        receipts._CACHE.clear()
        self.assertTrue(receipts.covered("-700__topic__8", 99))
        for chat, mid in [("-700__topic__8", 101), ("-700__topic__9", 99),
                          ("-700", 99), ("-701__topic__8", 99)]:
            self.assertFalse(receipts.covered(chat, mid))
        self.assertTrue(receipts.record_outbox(self.entry(90)))
        self.assertTrue(receipts.covered("-700__topic__8", 89))
        self.assertFalse(receipts.covered("-700__topic__8", 88))
        self.assertFalse(receipts.record_outbox(self.entry()))
        self.assertEqual(len(receipts._path("-700__topic__8").read_text().splitlines()), 2)

    def test_pending_failed_narration_and_dm_are_not_answers(self):
        for changes in [dict(state="pending"), dict(state="dead_letter"),
                        dict(purpose="tool:narrate"), dict(purpose="tool:send_message"),
                        dict(kind="file"), dict(peer_id="700", topic_id=None),
                        dict(receipt={"message_id": None})]:
            self.assertFalse(receipts.record_outbox(self.entry(**changes)))
        self.assertEqual(receipts.watermark("-700__topic__8"), 0)

    def test_explicit_policy_can_disable_coverage_without_deleting_receipts(self):
        receipts.record_outbox(self.entry())
        (self.root / "memory/policies/addressed-replies.json").write_text('{"enabled": false}')
        self.assertFalse(receipts.covered("-700__topic__8", 99))
        self.assertTrue(receipts._path("-700__topic__8").exists())

    def test_burst_coalesces_and_late_old_ping_cannot_replace_new_ping(self):
        chat = "-700__topic__8"
        with patch.object(runner, "_group_wakes", {}), patch.object(runner, "_note_address_overwritten"):
            for mid in (10, 11, 12):
                self.assertTrue(runner._install_group_wake(chat, _wake(mid=mid, ts=mid)))
            self.assertEqual(runner._group_wakes[chat].message_id, 12)
            receipts.record_outbox(self.entry())
            self.assertTrue(runner._release_answered_wake(chat, _wake(mid=10, ts=10)))
            runner._install_group_wake(chat, _wake(mid=101, ts=101))
            self.assertFalse(runner._install_group_wake(chat, _wake(mid=99, ts=99)))
            self.assertEqual(runner._group_wakes[chat].message_id, 101)

    def test_repair_queue_is_closed_only_by_later_reply_in_same_topic(self):
        def park(chat, mid):
            wake = dict(mid=mid, message_ts=1, kind="reply", addressed=True,
                        query="question", name="A", sender_id=1, is_owner=False,
                        known=True, family=False)
            return epoch_repair.park(chat, wake, {}, "capacity")
        covered = park("-700__topic__8", 90)
        newer = park("-700__topic__8", 101)
        other = park("-700__topic__9", 90)
        receipts.record_outbox(self.entry())
        receipts._CACHE.clear()
        self.assertEqual({r["id"] for r in epoch_repair.pending()}, {newer, other})
        rows = epoch_repair._rows()
        self.assertTrue(any(r.get("event") == "parked" and r["id"] == covered for r in rows))
        self.assertTrue(any(r.get("event") == "covered_by_reply" and r["id"] == covered for r in rows))
        self.assertEqual({r["id"] for r in epoch_repair.pending()}, {newer, other})

    def corrupt_index(self):
        self.assertTrue(receipts.record_outbox(self.entry()))
        path = receipts._path("-700__topic__8")
        path.write_bytes(path.read_bytes() + b'{"torn":')
        return path

    def test_corrupted_index_does_not_suppress_wake_or_epoch_pending(self):
        chat = "-700__topic__8"
        path = self.corrupt_index()
        damaged = path.read_bytes()
        wake = dict(mid=90, message_ts=1, kind="reply", addressed=True,
                    query="question", name="A", sender_id=1, is_owner=False,
                    known=True, family=False)
        parked = epoch_repair.park(chat, wake, {}, "capacity")
        with patch.object(runner, "_group_wakes", {}), \
                patch.object(runner, "_note_address_overwritten"):
            self.assertTrue(runner._install_group_wake(chat, _wake(mid=90, ts=1)))
            self.assertFalse(runner._release_answered_wake(chat, _wake(mid=80, ts=1)))
            self.assertEqual(runner._group_wakes[chat].message_id, 90)
        self.assertEqual({row["id"] for row in epoch_repair.pending()}, {parked})
        self.assertFalse(any(row.get("event") == "covered_by_reply"
                             for row in epoch_repair._rows()))
        self.assertFalse(receipts.diagnostics(chat)["ok"])
        self.assertEqual(path.read_bytes(), damaged)

    async def test_corrupted_index_does_not_break_accepted_delivery_or_replay(self):
        path = self.corrupt_index()
        damaged = path.read_bytes()
        entry = self.entry(state="pending", receipt=None, random_id=123, run_id="r",
                           payload={"text": "answer"})
        accepted = self.entry()
        outbox = Mock(mark_accepted=Mock(return_value=accepted))
        with patch.object(runner, "_run_cancel_requested", return_value=False), \
                patch.object(agent, "direct_outbox_prepared", return_value=True), \
                patch.object(runner, "_direct_outbox", return_value=outbox), \
                patch.object(runner, "_send_message_idempotent", new_callable=AsyncMock,
                             return_value=(types.SimpleNamespace(id=100), 123)) as network:
            result = await runner._send_direct_outbox_entry(entry, entity=-700)
            self.assertEqual(result, accepted)
            outbox.mark_accepted.assert_called_once()
            network.assert_awaited_once()
            network.reset_mock()
            self.assertEqual(await runner._send_direct_outbox_entry(result), accepted)
            network.assert_not_called()
        self.assertFalse(receipts.covered("-700__topic__8", 90))
        self.assertEqual(path.read_bytes(), damaged)

    async def test_delayed_timer_does_not_invoke_model_for_covered_wake(self):
        chat = "-700__topic__8"
        receipts.record_outbox(self.entry())
        wakes = {chat: _wake(mid=90, ts=1)}
        with patch.object(runner, "_group_wakes", wakes), patch.object(runner, "_meta", {
                chat: dict(is_dm=False, peer_id="-700", topic_id=8)}), \
                patch.object(agent, "voice_turn_envelope") as model:
            await runner._run_pass(chat)
        model.assert_not_called()
        self.assertNotIn(chat, wakes)

    async def exercise_pass(self, *, fail=False, new_mid=22, silent=False, during_send=False):
        chat = "-702"
        first = _wake(mid=20, ts=900)
        second = _wake(mid=new_mid, ts=999)
        wakes = {chat: first}
        client = _Client()
        if fail:
            client.send_message = AsyncMock(side_effect=RuntimeError("offline"))
        if during_send:
            original_send = client.send_message
            async def send(*args, **kwargs):
                runner._install_group_wake(chat, second)
                return await original_send(*args, **kwargs)
            client.send_message = send
        arm = Mock()
        def voice(*args, **kwargs):
            # Simulate the real model request boundary for the initial snapshot.
            agent._TURN_ADDRESSED_INBOUND.get().presented()
            if not during_send:
                runner._install_group_wake(chat, second)
            return media.TurnEnvelope(text="" if silent else "answer")
        with contextlib.ExitStack() as stack:
            for name, value in dict(client=client, _meta={chat: dict(entity=-702,
                    is_dm=False, title="room", size=3, room_mode="normal", addressed=False)},
                    _group_wakes=wakes, _last_pass=defaultdict(float), _passing=set(),
                    _pending_media=defaultdict(lambda: deque(maxlen=16)),
                    _recent_msgs=defaultdict(lambda: deque(maxlen=12)), _missed={}).items():
                stack.enter_context(patch.object(runner, name, value))
            stack.enter_context(patch.object(runner, "_cooldown", return_value=0.0))
            stack.enter_context(patch.object(runner, "_maybe_compact", side_effect=_no_compact))
            stack.enter_context(patch.object(runner, "_arm", arm))
            stack.enter_context(patch.object(agent, "voice_turn_envelope", side_effect=voice))
            await runner._run_pass(chat)
            await asyncio.sleep(0)
        return wakes, arm, client

    async def test_success_preserves_unread_ping_and_rearms(self):
        wakes, arm, client = await self.exercise_pass()
        self.assertEqual(wakes["-702"].message_id, 22)
        arm.assert_called_once_with("-702")
        self.assertEqual(len(client.sent), 1)
        self.assertTrue(receipts.covered("-702", 20))
        self.assertFalse(receipts.covered("-702", 22))

    async def test_lower_unread_during_send_survives_acceptance_and_restart(self):
        wakes, arm, client = await self.exercise_pass(new_mid=19, during_send=True)
        self.assertEqual(wakes["-702"].message_id, 19)
        arm.assert_called_once_with("-702")
        self.assertEqual(len(client.sent), 1)
        receipts._CACHE.clear()
        self.assertTrue(receipts.covered("-702", 20))
        self.assertFalse(receipts.covered("-702", 19))
        with patch.object(runner, "_group_wakes", {}):
            self.assertTrue(runner._install_group_wake("-702", _wake(mid=18, ts=1000)))

    async def test_ping_after_reply_id_survives_even_before_tool_finishes(self):
        wakes, arm, _ = await self.exercise_pass(new_mid=1002)
        self.assertEqual(wakes["-702"].message_id, 1002)
        arm.assert_called_once_with("-702")

    async def test_failed_send_keeps_pending_ping(self):
        wakes, arm, _ = await self.exercise_pass(fail=True)
        self.assertEqual(wakes["-702"].message_id, 22)
        arm.assert_called_once_with("-702")
        self.assertEqual(receipts.watermark("-702"), 0)

    async def test_silence_is_not_a_delivery_receipt(self):
        wakes, arm, _ = await self.exercise_pass(silent=True)
        self.assertEqual(wakes["-702"].message_id, 22)
        arm.assert_called_once_with("-702")
        self.assertEqual(receipts.watermark("-702"), 0)

    async def test_direct_accepted_replay_records_without_network(self):
        with patch.object(runner, "_send_message_idempotent", new_callable=AsyncMock) as network:
            await runner._send_direct_outbox_entry(self.entry())
        network.assert_not_called()
        self.assertTrue(receipts.covered("-700__topic__8", 99))

    async def test_direct_reply_records_at_acceptance_before_slow_projection(self):
        entry = self.entry(state="pending", receipt=None, random_id=123, run_id="r",
                           payload={"text": "answer"})
        accepted = self.entry()
        outbox = Mock(mark_accepted=Mock(return_value=accepted))
        with patch.object(runner, "_run_cancel_requested", return_value=False), \
                patch.object(agent, "direct_outbox_prepared", return_value=True), \
                patch.object(runner, "_direct_outbox", return_value=outbox), \
                patch.object(runner, "_send_message_idempotent", new_callable=AsyncMock,
                             return_value=(types.SimpleNamespace(id=100), 123)):
            await runner._send_direct_outbox_entry(entry, entity=-700)
        self.assertTrue(receipts.covered("-700__topic__8", 99))

    def test_zero_addressed_cooldown_hot_reload_preserves_ambient(self):
        perception = runner.perception
        with patch.object(perception, "STATE_PATH", self.root / "perception.json"), \
                patch.object(perception, "_journal"), patch.object(perception, "_spine"):
            perception.set_knob("cooldown_group", 360, by="egor")
            perception.set_knob("cooldown_addressed", 360, by="egor")
            self.assertEqual(runner._cooldown(False, addressed=True), 360)
            perception.set_knob("cooldown_addressed", 0, by="egor")
            self.assertEqual(runner._cooldown(False, addressed=True), 0)
            self.assertEqual(runner._cooldown(False, "quiet", addressed=True), 0)
            self.assertEqual(runner._cooldown(False, addressed=False), 360)
