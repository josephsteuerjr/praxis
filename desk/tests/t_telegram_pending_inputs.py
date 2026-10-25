"""Real Telegram ingress + real core checkpoint admission, without networks/models."""
from contextlib import contextmanager
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
DESK = HERE.parent
sys.path[:0] = [str(HERE), str(DESK), str(DESK / "localharness"),
               os.environ.get("HELENE_TREE_SRC", str(DESK.parent / "helene/core"))]

from t_owner_input_boundary import OwnerInputBoundary
import agent
import botapi
import mtproto
import runner
import run_context
import telegram_inputs as incoming
import voice_admission


class TelegramInputs(unittest.TestCase):
    drain = OwnerInputBoundary.drain
    store = OwnerInputBoundary.store

    def setUp(self):
        OwnerInputBoundary.setUp(self)
        self.current = run_context.RunContext.create(
            kind="chat_turn", goal="telegram input", principal_id="11",
            scope="owner", origin_chat_id="123", delivery_chat_id="123")
        self.calls = []
        self.failed = set()
        self.keep(patch("owner_stop.paused", return_value=False))
        self.keep(patch.object(runner, "_tree", self.root))
        self.keep(patch.object(runner, "_agent", agent))
        self.bot = self.make_bot()
        self.keep(patch.object(runner, "_bot", self.bot))
        self.keep(patch.object(runner, "_transcribe_path", side_effect=self.transcribe))
        self.keep(patch("telegram_media.persist", side_effect=self.persist))

    def keep(self, context):
        value = context.start()
        self.addCleanup(context.stop)
        return value

    def make_bot(self, cls=botapi.BotTransport):
        value = cls.__new__(cls)
        botapi.BotTransport.__init__(
            value, SimpleNamespace(), self.root, None,
            {"agent": {"name": "Helene"},
             "telegram": {"bot_token": "fixture", "owner_id": "11",
                          "allow_from": "listed", "allowed_ids": ["22"]}})
        value.me = {"id": 99}
        value.client = Mock()
        return value

    def persist(self, client, tree, peer, message):
        path = tree / "memory/telegram-media" / (str(message["message_id"]) + ".ogg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("voice-" + str(message["message_id"])).encode())
        return {"media_path": path.relative_to(tree).as_posix(),
                "media_kind": "audio", "media_mime": "audio/ogg"}

    def transcribe(self, source):
        def decode(path):
            self.calls.append(path.name)
            if path.name in self.failed:
                raise RuntimeError("synthetic recognition failure")
            return "heard-" + path.stem
        return voice_admission.resolve(source, decode, signature="test")

    def feed(self, mid, text="", *, voice=False, sender=11, room=123, **update):
        message = {
            "message_id": mid, "date": time.time(), "text": text,
            "chat": {"id": room, "type": "private"},
            "from": {"id": sender, "first_name": "Sender-" + str(sender)},
        }
        if voice:
            message.pop("text")
            message["voice"] = {"file_id": "fixture-" + str(mid), "duration": 1}
        self.bot._ingest({"message": message, **update})

    def bound(self, batch):
        for item in batch:
            self.bot.inputs.bind(item, self.current)

    def test_voice_voice_and_outgoing_do_not_replace_pending_identity(self):
        self.feed(1, voice=True)
        self.feed(2, voice=True)
        self.feed(3, "own answer", sender=99, _outgoing=True)
        batch = incoming.select(runner, "123")
        self.assertEqual([p["record"]["message_id"] for p in batch], ["1", "2"])
        self.bound(batch)
        self.drain(runner._drain_owner_inputs, [])
        contents = "\n".join(m["content"] for m in self.saved["messages"])
        self.assertIn("heard-1", contents)
        self.assertIn("heard-2", contents)
        self.assertEqual(self.bot.inputs.items, {})
        self.assertEqual(self.events, ["checkpoint"])

    def test_failed_voice_does_not_block_later_independent_text(self):
        self.failed.add("1.ogg")
        self.feed(1, voice=True)
        self.feed(2, "independent text")
        batch = incoming.select(runner, "123")
        self.assertEqual([p["record"]["message_id"] for p in batch], ["2"])
        self.bound(batch)
        self.drain(runner._drain_owner_inputs, [])
        self.assertIn("independent text", self.saved["messages"][0]["content"])
        self.assertNotIn("synthetic", json.dumps(self.saved["messages"]))
        self.assertEqual(set(self.bot.inputs.items), {"123:1"})
        incoming.select(runner, "123")
        self.assertEqual(self.calls, ["1.ogg"])  # error receipt suppresses inference

    def test_checkpoint_failure_never_acknowledges_exact_ids(self):
        self.feed(1, voice=True)
        batch = incoming.select(runner, "123")
        self.bound(batch)
        with self.assertRaisesRegex(RuntimeError, "storage unavailable"):
            self.drain(runner._drain_owner_inputs, [], fail_checkpoint=True)
        self.assertIsNotNone(self.bot.inputs.get("123:1"))
        self.assertEqual(self.events, ["checkpoint-refused"])

    def test_restart_unknown_ack_reuses_checkpoint_without_asr(self):
        self.feed(1, voice=True)
        batch = incoming.select(runner, "123")
        self.bound(batch)
        original_ack = self.bot.inputs.ack
        with patch.object(self.bot.inputs, "ack", side_effect=OSError("unknown ACK")):
            with self.assertRaisesRegex(OSError, "unknown ACK"):
                self.drain(runner._drain_owner_inputs, [])
        durable = list(self.saved["messages"])
        self.bot.inputs = incoming.PendingInputs(self.root)
        with patch.object(runner, "_transcribe_path", side_effect=AssertionError("no ASR")):
            additions, ack = incoming.collect(runner, self.current, durable)
            self.assertEqual(additions, [])
            ack()
        self.assertEqual(self.bot.inputs.items, {})
        self.assertEqual(self.calls, ["1.ogg"])

    def test_active_same_principal_only_and_new_input_not_in_prior_ack(self):
        self.feed(1, "same principal")
        self.feed(2, "other principal", sender=22)
        self.feed(3, voice=True, room=456)
        additions, ack = incoming.collect(runner, self.current, [])
        self.feed(4, "arrived after snapshot")
        self.assertEqual(len(additions), 1)
        self.assertIn("same principal", additions[0]["content"])
        ack()
        self.assertEqual(set(self.bot.inputs.items), {"123:2", "456:3", "123:4"})

    def test_partial_multi_item_ack_keeps_exact_remaining_binding(self):
        self.feed(1, voice=True)
        self.feed(2, voice=True)
        self.bound(incoming.select(runner, "123"))
        real_ack = self.bot.inputs.ack
        def interrupted(key, nonce, run_id):
            if key == "123:2":
                raise OSError("second ACK interrupted")
            return real_ack(key, nonce, run_id)
        with patch.object(self.bot.inputs, "ack", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.drain(runner._drain_owner_inputs, [])
        self.assertEqual(set(self.bot.inputs.items), {"123:2"})
        self.bot.inputs = incoming.PendingInputs(self.root)
        additions, ack = incoming.collect(runner, self.current, self.saved["messages"])
        self.assertEqual(additions, [])
        ack()
        self.assertEqual(self.bot.inputs.items, {})

    def test_capture_before_archive_crash_is_repaired_without_new_identity(self):
        with patch("botapi._append_jsonl", side_effect=OSError("archive unavailable")):
            with self.assertRaises(OSError):
                self.feed(1, voice=True)
        first = self.bot.inputs.get("123:1")
        self.assertFalse(first["published"])
        restored = incoming.PendingInputs(self.root)
        rooms = botapi.Rooms(self.root, None, "Helene")
        restored.recover_archive(rooms)
        after = restored.get("123:1")
        self.assertEqual(first["nonce"], after["nonce"])
        self.assertTrue(after["published"])
        rows = (self.root / "memory/groups/123.jsonl").read_text("utf-8").splitlines()
        self.assertEqual(len(rows), 1)

    def test_archive_before_cursor_redelivery_does_not_recreate_admitted_input(self):
        self.feed(1, "one")
        self.bound(incoming.select(runner, "123"))
        self.drain(runner._drain_owner_inputs, [])
        self.feed(1, "one")  # Same Telegram ID, e.g. offset save was interrupted.
        self.assertEqual(self.bot.inputs.items, {})
        self.assertEqual(incoming.select(runner, "123"), [])

    def test_media_recovery_updates_only_matching_original(self):
        self.feed(1, voice=True)
        value = self.bot.inputs.get("123:1")
        expected = value["row"]["media_source"]
        self.assertIsNone(self.bot.inputs.media_update(
            "123", "1", {"different": True}, {"media_path": "wrong"}))
        finish = self.bot.inputs.media_update(
            "123", "1", expected, {"media_error": "waiting"})
        self.assertIsNotNone(finish)
        self.assertFalse(self.bot.inputs.get("123:1")["published"])
        finish()
        self.assertTrue(self.bot.inputs.get("123:1")["published"])

    def test_real_retry_route_rejects_archive_only_audio_and_accepts_exact_pending_original(self):
        import deskapp
        self.feed(1, voice=True)
        source = self.root / "memory/telegram-media/1.ogg"
        voice_admission._write(source, {"status": "error", "error": "offline"})
        relative = "memory/telegram-media/1.ogg"
        with patch.object(deskapp.readers, "tree", return_value=self.root):
            status = asyncio.run(deskapp._r_voice_attachment(
                SimpleNamespace(body={}, query={"path": relative})))
            self.assertTrue(status["can_retry"])
            status = asyncio.run(deskapp._r_voice_attachment(
                SimpleNamespace(body={"path": relative}, query={})))
            self.assertTrue(status["retry_requested"])
            other = source.with_name("historical.ogg")
            other.write_bytes(b"old")
            with self.assertRaises(ValueError):
                asyncio.run(deskapp._r_voice_attachment(SimpleNamespace(
                    body={}, query={"path": "memory/telegram-media/historical.ogg"})))

    def test_passive_last_voice_never_runs_asr_and_uses_bounded_tail(self):
        self.feed(1, voice=True)
        spool = SimpleNamespace(max_turn_media=4)
        with patch.object(runner, "_agent", SimpleNamespace(_media_spool=lambda: spool)), \
             patch.object(runner, "_last_n", return_value=10), \
             patch.object(runner, "_transcribe_path", side_effect=AssertionError("historical ASR")), \
             patch("jsonl_tail.tail_lines", wraps=__import__("jsonl_tail").tail_lines) as tail:
            refs, notes = runner._telegram_media_refs(
                "123", SimpleNamespace(scope="owner", chat_id="123"))
        self.assertEqual((refs, notes), ([], []))
        tail.assert_called_once()

    def test_shared_mtproto_gates_and_no_history_edit_outgoing_unallowed_capture(self):
        self.bot = self.make_bot(mtproto.MtprotoTransport)
        self.keep(patch.object(runner, "_bot", self.bot))
        self.feed(1, voice=True, _history=True)
        self.feed(2, voice=True, _catchup=True)
        self.feed(3, voice=True, _edited=True)
        self.feed(4, voice=True, sender=99, _outgoing=True)
        self.feed(5, voice=True, sender=33)
        self.assertEqual(self.bot.inputs.items, {})
        self.feed(6, voice=True)
        self.assertEqual(set(self.bot.inputs.items), {"123:6"})

    def test_pause_discard_keeps_archive_and_existing_run_but_no_old_automatic_wake(self):
        self.feed(1, "already bound")
        self.bound(incoming.select(runner, "123"))
        self.feed(2, "not yet bound")
        self.bot.discard_pending()
        self.assertEqual(set(self.bot.inputs.items), {"123:1"})
        self.assertTrue((self.root / "memory/groups/123.jsonl").is_file())

    def test_failed_head_over_batch_limit_cannot_starve_later_input(self):
        for mid in range(1, incoming.LIMIT + 2):
            self.failed.add(str(mid) + ".ogg")
            self.feed(mid, voice=True)
        self.feed(incoming.LIMIT + 2, "later independent text")
        found = []
        for _ in range(4):
            found.extend(incoming.select(runner, "123"))
        self.assertTrue(any("later independent text" in p["message"]["content"] for p in found))
        self.assertGreater(len(self.bot.inputs.items), incoming.LIMIT)


if __name__ == "__main__":
    unittest.main()
