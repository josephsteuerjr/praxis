"""Deterministic durable attachment pause and newer-trigger regressions."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Reuse the test-only Telegram import/session boundary, never live credentials.
from test_direct_telegram_outbox import runner, agent
import media


class AttachmentZenPauseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "spool"
        self.spool = media.MediaSpool(self.root)
        source = Path(self.temp.name) / "report.txt"
        source.write_text("verified result\n")
        self.item = self.spool.resolve_outbound(
            source, kind="document", target_chat_id="777", scope="owner")
        self.ctx = agent.ChannelContext(chat_id="777", is_dm=True, owner=True)

    def stage(self):
        with patch.object(runner, "_MEDIA_SPOOL", self.spool), patch.object(media.time, "time", return_value=1000.0):
            runner._stage_authorized_media((self.item,), reply_to=42)
        self.item = self.spool.pending()[0]

    async def test_before_exact_threshold_restart_and_no_duplicate(self):
        self.stage()
        send = AsyncMock(return_value={"message_id": 91, "random_id": 777, "accepted_at": 1030.0})
        with patch.object(runner, "_MEDIA_SPOOL", self.spool), patch.object(runner, "_MEDIA_SENDING", set()), patch.object(runner, "_MEDIA_ACCEPTED", {}), patch.object(runner, "_send_turn_media", send):
            for now in (1000.0, 1029.999):
                with patch.object(media.time, "time", return_value=now):
                    self.assertFalse(await runner._attempt_queued_media("entity", self.item, ctx=self.ctx))
            send.assert_not_awaited()
            restarted = media.MediaSpool(self.root)
            restored = restarted.pending()[0]
            self.assertEqual(restored.queue_id, self.item.queue_id)
            self.assertEqual(restored.target_chat_id, "777")
            self.assertEqual(restored.reply_to_message_id, 42)
            with patch.object(runner, "_MEDIA_SPOOL", restarted), patch.object(media.time, "time", return_value=1030.0):
                self.assertTrue(await runner._attempt_queued_media("entity", restored, ctx=self.ctx))
                self.assertFalse(await runner._attempt_queued_media("entity", restored, ctx=self.ctx))
            send.assert_awaited_once()
            again = media.MediaSpool(self.root)
            self.assertEqual(again.pending(), ())
            self.assertFalse(again.ready(restored.queue_id, now=1100.0))
            self.assertEqual(len(again.outbox_results("delivered")), 1)

    def test_newer_trigger_does_not_cancel_media_bearing_delivery(self):
        self.stage()
        manager = unittest.mock.Mock()
        manager.manifest.return_value = {"status": "running"}
        manager.iter_events.return_value = iter([{
            "kind": "tool_started", "tool": "telegram.deliver", "call_id": "delivery:run-test",
            "args": {"media_count": 1, "media_queue_ids": [self.item.queue_id]},
        }])
        with patch.object(agent, "_runs", return_value=manager):
            self.assertFalse(agent.run_delivery_superseded("run-test", reason="newer trigger"))
        manager.transition.assert_not_called()
        self.assertEqual(self.spool.pending(), (self.item,))
        # Restaging the same authorized intent does not restart its pause.
        with patch.object(runner, "_MEDIA_SPOOL", self.spool), patch.object(media.time, "time", return_value=1020.0):
            runner._stage_authorized_media((self.item,), reply_to=999)
        self.assertFalse(self.spool.ready(self.item.queue_id, now=1029.999))
        self.assertTrue(self.spool.ready(self.item.queue_id, now=1030.0))
        self.assertEqual(self.spool.pending()[0].reply_to_message_id, 42)

    async def test_explicit_cancellation_still_prevents_upload(self):
        self.stage()
        self.assertTrue(self.spool.fail(self.item.queue_id, reason="explicit owner cancellation"))
        send = AsyncMock()
        with patch.object(runner, "_MEDIA_SPOOL", self.spool), patch.object(runner, "_send_turn_media", send), patch.object(media.time, "time", return_value=1100.0):
            self.assertFalse(await runner._attempt_queued_media("entity", self.item, ctx=self.ctx))
        send.assert_not_awaited()

    def test_legacy_pending_pause_is_migrated_once_and_corrupt_time_fails_closed(self):
        import json
        self.stage()
        path = self.spool._record_path(self.item.queue_id)
        row = json.loads(path.read_text())
        row.pop("not_before")
        path.write_text(json.dumps(row))
        self.assertFalse(self.spool.ready(self.item.queue_id, now=2000.0))
        restarted = media.MediaSpool(self.root)
        self.assertFalse(restarted.ready(self.item.queue_id, now=2029.999))
        self.assertTrue(restarted.ready(self.item.queue_id, now=2030.0))
        row = json.loads(path.read_text())
        self.assertEqual(row["not_before"], 2030.0)
        row["not_before"] = "NaN"
        path.write_text(json.dumps(row))
        with self.assertRaises(media.MediaValidationError):
            restarted.ready(self.item.queue_id, now=5000.0)

    def test_malformed_or_too_early_pause_never_authorizes_upload(self):
        import json
        self.stage()
        path = self.spool._record_path(self.item.queue_id)
        original = json.loads(path.read_text())
        for invalid in (True, False, "1030", None, {}, float("nan"), float("inf"), 0, 1000, 1029.999):
            with self.subTest(not_before=invalid):
                row = dict(original, not_before=invalid)
                path.write_text(json.dumps(row))
                with self.assertRaises(media.MediaValidationError):
                    self.spool.ready(self.item.queue_id, now=5000.0)


class DirectAttachmentZenPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_gate_threshold_restart_retry_and_no_sleep(self):
        import types
        import telegram_outbox
        from test_direct_telegram_outbox import MutableClock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "direct"
            clock = MutableClock()
            outbox = telegram_outbox.TelegramOutbox(root, clock=clock)
            source = Path(tmp) / "result.txt"
            source.write_text("result")
            entry = outbox.prepare_file("attachment-decision", peer_id=777,
                source=source, visible_filename="result.txt", mime="text/plain",
                caption="", run_id="run-test", call_id="call-test", purpose="tool:send_file")
            send = AsyncMock(return_value=(types.SimpleNamespace(id=94), entry["random_id"]))
            resolve = AsyncMock(return_value="entity")
            with patch.object(runner, "_DIRECT_OUTBOX", outbox), patch.object(agent, "direct_outbox_prepared", return_value=True), patch.object(runner, "_send_file_idempotent", send), patch.object(runner, "_resolve_entity", resolve):
                for now in (1000.0, 1029.999):
                    clock.value = now
                    self.assertFalse(outbox.pending(due_only=True))
                    with self.assertRaises(agent.DurableSideEffectPending):
                        await runner._send_direct_outbox_entry(entry)
                send.assert_not_awaited()
                resolve.assert_not_awaited()
                self.assertEqual(outbox.get(entry["key"])["attempts"], 0)
                restarted = telegram_outbox.TelegramOutbox(root, clock=clock)
                self.assertEqual(restarted.get(entry["key"])["not_before"], 1030.0)
                with patch.object(runner, "_DIRECT_OUTBOX", restarted):
                    clock.value = 1030.0
                    self.assertTrue(restarted.pending(due_only=True))
                    restarted.record_retry(entry["key"], "offline", now=clock.value)
                    self.assertFalse(restarted.ready(entry["key"]))
                    clock.value = 1035.0
                    accepted = await runner._send_direct_outbox_entry(restarted.get(entry["key"]))
                    await runner._send_direct_outbox_entry(accepted)
                self.assertEqual(send.await_count, 1)
                self.assertEqual(accepted["state"], "accepted")
