"""Offline XTTS protocol, durable preparation, cancellation and runner acceptance.

All model tests use small injected engines; real weights are a separate server
acceptance lane. These tests never connect to Telegram or a model provider.
"""
from __future__ import annotations

import asyncio
import atexit
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import wave

os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault("PRAXIS_TEST", "1")
_SESSION = tempfile.mkdtemp(prefix="xtts-session-")
atexit.register(shutil.rmtree, _SESSION, True)
os.environ["TELEGRAM_SESSION"] = str(Path(_SESSION) / "test-session")

import media
import xtts_intent
from xtts_engine import XttsEngine, split_text
from xtts_protocol import INTENT_MIME, SCHEMA, XttsError, read_intent
from xtts_service import SpeechServer
from xtts_tts_client import XttsTTSClient

HEALTH = {"ready": True, "phase": "ready", "model_id": "a" * 64, "profile_id": "b" * 64}


def wav(path):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\0\0" * 2400)


class SpoolCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="xtts-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.spool = media.MediaSpool(self.root / "media")

    def intent(self, **kwargs):
        args = dict(target_chat_id="777", scope="owner", caption="caption",
                    run_id="run-fixture", health=HEALTH)
        args.update(kwargs)
        return xtts_intent.stage(self.spool, "Точная речь.", **args)

    def attachment(self):
        path = self.root / "voice.ogg"
        path.write_bytes(b"OggS" + b"A" * 80)
        return path

    def metadata(self, item):
        return {"request_id": item.queue_id, "model_id": HEALTH["model_id"],
                "profile_id": HEALTH["profile_id"], "generation": "test"}

    def test_stage_is_private_intent_and_does_not_enqueue(self):
        item = self.intent()
        self.assertTrue(xtts_intent.is_intent(item))
        self.assertTrue(item.voice_note)
        self.assertEqual(self.spool.pending(), ())
        self.assertEqual(read_intent(item.path)["text"], "Точная речь.")

    def test_no_synthesis_before_guard_admission(self):
        client = mock.Mock()
        with self.assertRaisesRegex(XttsError, "intent_not_admitted"):
            xtts_intent.prepare(self.spool, self.intent(), client)
        client.synthesize_intent.assert_not_called()

    def test_unselected_voice_is_not_silently_assigned(self):
        with self.assertRaisesRegex(XttsError, "awaiting_voice"):
            self.intent(health={"ready": False, "phase": "awaiting_voice"})
        self.assertEqual(list((self.root / "media").rglob("*speech-intent*")), [])

    def test_audience_and_identity_cannot_be_changed(self):
        item = self.intent()
        for changed in (replace(item, target_chat_id="888"), replace(item, scope="group"),
                        replace(item, queue_id="d" * 32), replace(item, run_id="other")):
            with self.subTest(changed=changed.queue_id):
                with self.assertRaises((media.MediaError, XttsError)):
                    self.spool.validate_outbound(changed)

    def test_mutated_intent_bytes_fail_integrity(self):
        item = self.intent()
        item.path.write_bytes(item.path.read_bytes().replace("Точная".encode(), "Чужая!".encode()))
        with self.assertRaises(media.MediaError):
            self.spool.validate_outbound(item)

    def test_pending_survives_restart_and_keeps_exact_text(self):
        item = self.intent()
        self.spool.enqueue(item)
        restored = media.MediaSpool(self.spool.root).pending()
        self.assertEqual(restored, (item,))
        self.assertEqual(read_intent(restored[0].path)["text"], "Точная речь.")

    def test_preparation_commits_into_same_outbox_without_mutating_intent(self):
        item = self.intent()
        self.spool.enqueue(item)
        original = item.path.read_bytes()
        self.assertTrue(xtts_intent.commit(self.spool, item, self.attachment(), self.metadata(item)))
        result = xtts_intent.prepared(media.MediaSpool(self.spool.root), item)
        self.assertEqual(result.mime, "audio/ogg")
        self.assertTrue(result.voice_note)
        self.assertEqual(result.queue_id, item.queue_id)
        self.assertEqual(self.spool.pending(), (item,))
        self.assertEqual(item.path.read_bytes(), original)

    def test_first_committed_audio_wins_and_no_second_synthesis(self):
        item = self.intent()
        self.spool.enqueue(item)
        path = self.attachment()
        xtts_intent.commit(self.spool, item, path, self.metadata(item))
        first = xtts_intent.prepared(self.spool, item)
        path.write_bytes(b"OggS" + b"B" * 80)
        self.assertFalse(xtts_intent.commit(self.spool, item, path, self.metadata(item)))
        client = mock.Mock()
        xtts_intent.prepare(self.spool, item, client)
        client.synthesize_intent.assert_not_called()
        self.assertEqual(xtts_intent.prepared(self.spool, item).sha256, first.sha256)

    def test_cancel_between_synthesis_and_commit_cannot_publish_audio(self):
        item = self.intent()
        self.spool.enqueue(item)
        with self.assertRaisesRegex(XttsError, "cancelled"):
            xtts_intent.commit(self.spool, item, self.attachment(), self.metadata(item), stopped=lambda: True)
        self.assertIsNone(xtts_intent.prepared(self.spool, item))

    def test_different_profile_cannot_commit(self):
        item = self.intent()
        self.spool.enqueue(item)
        metadata = {**self.metadata(item), "profile_id": "c" * 64}
        with self.assertRaisesRegex(XttsError, "profile_changed"):
            xtts_intent.commit(self.spool, item, self.attachment(), metadata)

    def test_cancelled_outbox_cannot_commit(self):
        item = self.intent()
        self.spool.enqueue(item)
        self.spool.fail(item.queue_id, reason="cancelled")
        with self.assertRaisesRegex(XttsError, "intent_no_longer_pending"):
            xtts_intent.commit(self.spool, item, self.attachment(), self.metadata(item))

    def test_prepared_corruption_is_not_resynthesized_or_sent(self):
        item = self.intent()
        self.spool.enqueue(item)
        xtts_intent.commit(self.spool, item, self.attachment(), self.metadata(item))
        actual = xtts_intent.prepared(self.spool, item)
        actual.path.write_bytes(b"OggS" + b"Z" * 80)
        client = mock.Mock()
        with self.assertRaises(media.MediaError):
            xtts_intent.prepare(self.spool, item, client)
        client.synthesize_intent.assert_not_called()


class ConditioningTests(unittest.TestCase):
    def engine(self, root):
        engine = XttsEngine(root, root / "profile.json")
        engine.model_id = "a" * 64
        engine.profile = {"speaker": "named", "language": "ru"}
        # Reverse dictionary order deliberately: never depend on .values().
        engine.engine = SimpleNamespace(speaker_manager=SimpleNamespace(speakers={
            "named": {"speaker_embedding": "speaker", "gpt_cond_latent": "conditioning"}}))
        engine.profile_id = engine._profile_identity()
        return engine

    def test_builtin_fields_are_named_and_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = self.engine(Path(temp))
            self.assertEqual(engine._conditioning(lambda: False), ("conditioning", "speaker"))
            self.assertEqual(engine._conditioning(lambda: False), ("conditioning", "speaker"))
            self.assertEqual(engine.conditioning_loads, 1)

    def test_reference_content_change_invalidates_same_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.engine(root)
            reference = root / "voice.wav"
            reference.write_bytes(b"first")
            engine.profile = {"reference": str(reference), "language": "ru"}
            engine.profile_id = engine._profile_identity()
            engine.engine.get_conditioning_latents = mock.Mock(return_value=("c", "s"))
            engine._conditioning(lambda: False)
            engine._conditioning(lambda: False)
            engine.engine.get_conditioning_latents.assert_called_once()
            reference.write_bytes(b"second")
            with self.assertRaisesRegex(XttsError, "profile_changed"):
                engine._conditioning(lambda: False)

    def test_runtime_change_requires_new_explicit_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = self.engine(Path(temp))
            engine._conditioning(lambda: False)
            engine.runtime_id = "new-runtime"
            with self.assertRaisesRegex(XttsError, "profile_changed"):
                engine._conditioning(lambda: False)

    def test_cancel_after_conditioning_cannot_cache_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.engine(root)
            reference = root / "voice.wav"
            reference.write_bytes(b"reference")
            engine.profile = {"reference": str(reference)}
            engine.profile_id = engine._profile_identity()
            cancelled = threading.Event()
            def condition(**kwargs):
                cancelled.set()
                return "c", "s"
            engine.engine.get_conditioning_latents = condition
            with self.assertRaisesRegex(XttsError, "cancelled"):
                engine._conditioning(cancelled.is_set)
            self.assertFalse(engine.cache)

    def test_reference_changed_during_conditioning_cannot_be_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = self.engine(root)
            reference = root / "voice.wav"
            reference.write_bytes(b"before")
            engine.profile = {"reference": str(reference)}
            engine.profile_id = engine._profile_identity()
            def condition(**kwargs):
                reference.write_bytes(b"after")
                return "c", "s"
            engine.engine.get_conditioning_latents = condition
            with self.assertRaisesRegex(XttsError, "profile_changed"):
                engine._conditioning(lambda: False)
            self.assertFalse(engine.cache)

    def test_long_text_keeps_words_and_respects_real_token_limit(self):
        tokenizer = SimpleNamespace(encode=lambda text, **kwargs: list(text))
        text = "Привет, мир! " + "Оченьдлинноеслово" * 20 + ".\n" + "Ещё речь. " * 30
        parts = list(split_text(text, tokenizer, "ru", 60))
        self.assertTrue(all(len(p) < 60 for p in parts))
        self.assertEqual("".join("".join(parts).split()), "".join(text.split()))


class SelectionTests(unittest.TestCase):
    def test_disabled_and_missing_keep_existing_backend(self):
        from xtts_tts_client import selected
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "selection.json"
            with mock.patch.dict(os.environ, {"PRAXIS_XTTS_SELECTION_FILE": str(path)}):
                self.assertIsNone(selected())
                path.write_text(json.dumps({"schema": "praxis.xtts.selection.v1", "enabled": False}))
                self.assertIsNone(selected())

    def test_enable_requires_exact_model_and_profile_identity(self):
        from xtts_tts_client import selected
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "selection.json"
            with mock.patch.dict(os.environ, {"PRAXIS_XTTS_SELECTION_FILE": str(path)}):
                value = {"schema": "praxis.xtts.selection.v1", "enabled": True,
                         "model_id": "a" * 64, "profile_id": "b" * 64}
                path.write_text(json.dumps(value))
                self.assertEqual(selected(), value)
                path.write_text(json.dumps({**value, "enabled": "false"}))
                with self.assertRaisesRegex(XttsError, "invalid_selection"):
                    selected()

    def test_malformed_option_does_not_silently_select_another_voice(self):
        from xtts_tts_client import selected
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "selection.json"
            path.write_text("{truncated")
            with mock.patch.dict(os.environ, {"PRAXIS_XTTS_SELECTION_FILE": str(path)}):
                with self.assertRaisesRegex(XttsError, "invalid_selection"):
                    selected()

    def test_configuration_has_intent_and_applied_receipts(self):
        from xtts_control import write_config
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "selection.json"
            value = {"schema": "praxis.xtts.selection.v1", "enabled": False}
            write_config(path, value, actor="owner")
            stored=json.loads(path.read_text())
            self.assertEqual({k:stored[k] for k in value}, value)
            self.assertRegex(stored["revision"], r"^[0-9a-f]{32}$")
            rows = [json.loads(line) for line in (path.parent / "config-events.jsonl").read_text().splitlines()]
            self.assertEqual([r["kind"] for r in rows], ["config_intent", "config_applied"])
            self.assertTrue(all(r["actor"] == "owner" for r in rows))


class FakeEngine:
    def __init__(self):
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.wait = False
        self.cancelled = False

    def health(self):
        return HEALTH

    def synthesize(self, text, path, *, model_id, profile_id, stopped):
        if (model_id, profile_id) != (HEALTH["model_id"], HEALTH["profile_id"]):
            raise XttsError("profile_changed")
        self.calls += 1
        self.started.set()
        while self.wait and not self.release.wait(0.01):
            if stopped():
                self.cancelled = True
                raise XttsError("cancelled")
        wav(path)
        return {"model_id": model_id, "profile_id": profile_id}


@unittest.skipUnless(hasattr(__import__("socket"), "AF_UNIX") and os.name != "nt", "Linux socket stand")
class SocketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xtts-s-")
        self.root = Path(self.temp.name)
        self.token = self.root / "token"
        self.token.write_text("secret-" * 8)
        self.engine = FakeEngine()
        self.server = SpeechServer(self.root / "s", self.token.read_text(), self.engine,
                                    scratch=self.root / "scratch",
                                    encoder=lambda source, destination, stopped: destination.write_bytes(b"OggS" + b"OpusHead" + b"O" * 100))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = XttsTTSClient(self.root / "out", socket_path=self.root / "s", token_file=self.token, timeout=2)

    def tearDown(self):
        self.engine.release.set()
        self.server.close_requests()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def intent(self):
        return {"request_id": "d" * 32, "text": "hello", **{k: HEALTH[k] for k in ("model_id", "profile_id")}}

    def test_health_requires_authentication_and_never_echoes_token(self):
        self.assertTrue(self.client.health()["ready"])
        self.token.write_text("wrong-" * 8)
        with self.assertRaisesRegex(XttsError, "unauthorized") as caught:
            self.client.health()
        self.assertNotIn("wrong", str(caught.exception))

    def test_complete_audio_is_valid_ogg_and_scratch_is_removed(self):
        path, metadata = self.client.synthesize_intent(self.intent())
        self.assertEqual(path.read_bytes()[:4], b"OggS")
        self.assertEqual(metadata["request_id"], "d" * 32)
        self.assertFalse(list((self.root / "out").glob("*.wav")))
        deadline = time.monotonic() + 1
        while list((self.root / "scratch").glob("*.wav")) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(list((self.root / "scratch").glob("*.wav")))

    def test_cancel_before_synthesis_is_not_lost(self):
        self.client.cancel("d" * 32)
        with self.assertRaisesRegex(XttsError, "cancelled"):
            self.client.synthesize_intent(self.intent())
        self.assertEqual(self.engine.calls, 0)

    def test_hung_inference_has_a_bounded_service_recovery_deadline(self):
        event = threading.Event()
        self.server.active["a"] = (event, 100, 0)
        self.assertFalse(self.server.needs_restart(now=109))
        self.assertTrue(self.server.needs_restart(now=111))
        self.server.active.clear()

    def test_profile_change_fails_before_audio(self):
        with self.assertRaisesRegex(XttsError, "profile_changed"):
            self.client.synthesize_intent({**self.intent(), "profile_id": "c" * 64})
        self.assertFalse(list((self.root / "out").glob("*.ogg")))

    def test_cancel_and_health_do_not_wait_for_inference(self):
        self.engine.wait = True
        errors = []
        stop = threading.Event()
        def request():
            try:
                self.client.synthesize_intent(self.intent(), stopped=stop.is_set)
            except XttsError as exc:
                errors.append(exc.code)
        thread = threading.Thread(target=request)
        thread.start()
        self.assertTrue(self.engine.started.wait(2))
        self.assertTrue(self.client.health()["busy"])
        start = time.monotonic()
        self.client.cancel("d" * 32)
        stop.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertTrue(self.engine.cancelled)
        self.assertTrue(errors)

    def test_busy_duplicate_does_not_cancel_active_same_intent(self):
        self.engine.wait = True
        failures = []
        def first():
            try:
                self.client.synthesize_intent(self.intent())
            except XttsError as exc:
                failures.append(exc.code)
        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(self.engine.started.wait(2))
        try:
            with self.assertRaisesRegex(XttsError, "busy"):
                self.client.synthesize_intent(self.intent())
            self.assertFalse(self.engine.cancelled)
        finally:
            self.engine.release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures)


class RunnerTests(SpoolCase):
    def setUp(self):
        super().setUp()
        import agent
        import mtproto_runner
        import run_context
        import run_manager
        self.agent, self.runner = agent, mtproto_runner
        self.manager = run_manager.RunManager(self.root / "runs")
        self.run = self.manager.create(run_context.RunContext.create(
            kind="chat", goal="XTTS fixture", principal_id="owner", scope="owner",
            origin_chat_id="777", delivery_chat_id="777"), "fixture")
        self.manager.transition(self.run.run_id, "running")
        self.ctx = agent.ChannelContext(chat_id="777", is_dm=True, owner=True)
        patches = [mock.patch.object(agent, "_RUN_MANAGER", self.manager),
                   mock.patch.object(agent, "_media_spool", return_value=self.spool),
                   mock.patch.object(mtproto_runner, "_media_spool", return_value=self.spool)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        self.voice_selection=mock.patch('xtts_tts_client.selected',return_value=HEALTH)
        self.voice_selection.start()
        self.addCleanup(self.voice_selection.stop)

    def test_tool_stages_exact_intent_without_synthesis_and_guard_reads_its_text(self):
        import run_context
        audio = SimpleNamespace(config=SimpleNamespace(tts_backend="xtts"), tts=mock.Mock())
        audio.tts.health.return_value = HEALTH
        outgoing = []
        tokens = [(self.agent._TURN_CHANNEL, self.agent._TURN_CHANNEL.set(self.ctx)),
                  (self.agent._TURN_OUTBOUND, self.agent._TURN_OUTBOUND.set(outgoing)),
                  (self.agent._TURN_MEDIA_GUARD, self.agent._TURN_MEDIA_GUARD.set({}))]
        try:
            with run_context.bind_run(self.run), mock.patch.object(self.agent.media_audio, "get_default_backend", return_value=audio):
                result = self.agent.tool_voice_message("Точно эти слова.")
            self.assertIn("в фоне", result)
            self.assertEqual(len(outgoing), 1)
            audio.tts.synthesize.assert_not_called()
            self.assertEqual(self.spool.pending(), ())
            with mock.patch.object(self.agent.media_audio, "transcribe") as stt:
                kept, context, _, _ = self.agent._prepare_outbound_guard(outgoing, {}, self.ctx)
            self.assertEqual(kept, outgoing)
            self.assertIn("Точно эти слова.", context)
            stt.assert_not_called()
        finally:
            for variable, token in reversed(tokens):
                variable.reset(token)

    def test_new_frames_expose_voice_message_and_old_checkpoint_dispatch_survives(self):
        names=[tool['name'] for tool in self.agent.TOOLS]
        self.assertTrue({'generate_voice', 'voice_generation', 'send_voice'} <= set(names))
        self.assertNotIn('voice_message', names)
        self.assertNotIn('speak',names)
        self.assertIs(self.agent.TOOL_IMPL['speak'],self.agent.TOOL_IMPL['voice_message'])

    def test_owner_off_parks_admitted_speech_without_rewriting_it(self):
        item=self.intent(run_id=self.run.run_id)
        self.spool.enqueue(item)
        self.agent.run_delivery_started(item.run_id,chat_id="777",text_chars=0,media_count=1,media_queue_ids=[item.queue_id])
        with mock.patch('xtts_tts_client.selected',return_value=None):
            self.assertFalse(self.agent.run_speech_preparation_allowed(item.run_id,item.queue_id))
        self.assertTrue(self.agent.run_speech_preparation_allowed(item.run_id,item.queue_id))
        self.assertEqual(self.spool.pending()[0].sha256,item.sha256)

    def test_queued_speech_requires_canonical_delivery_and_honors_pause(self):
        item = self.intent(run_id=self.run.run_id)
        self.spool.enqueue(item)
        self.assertFalse(self.agent.run_speech_preparation_allowed(item.run_id, item.queue_id))
        self.agent.run_delivery_started(item.run_id, chat_id="777", text_chars=0,
                                       media_count=1, media_queue_ids=[item.queue_id])
        self.assertTrue(self.agent.run_speech_preparation_allowed(item.run_id, item.queue_id))
        self.manager.transition(item.run_id, "paused")
        self.assertFalse(self.agent.run_speech_preparation_allowed(item.run_id, item.queue_id))

    def test_intent_restores_through_real_checkpoint_without_queue_side_effects(self):
        import run_resume
        item = self.intent(run_id=self.run.run_id)
        value = self.agent._outbound_descriptor_payload(item)
        descriptors = run_resume.restore_outbound_descriptors(
            {"outbound": [value]}, run_id=item.run_id, allowed_roots=[self.spool.root])
        plan = SimpleNamespace(run_id=item.run_id, outbound=descriptors)
        self.assertEqual(self.agent._resume_outbound_items(plan, self.ctx), [item])
        self.assertFalse(self.spool.pending())

    def test_intent_cannot_be_sent_through_direct_media_sender(self):
        with self.assertRaises(media.MediaError):
            asyncio.run(self.runner._send_turn_media(None, self.intent(), ctx=self.ctx))
        with self.assertRaises(media.MediaError):
            asyncio.run(self.runner._send_file_idempotent(None, self.intent()))

    def test_audio_configuration_failure_keeps_the_existing_tool_error_contract(self):
        token = self.agent._TURN_CHANNEL.set(self.ctx)
        outbound_token = self.agent._TURN_OUTBOUND.set([])
        try:
            with mock.patch.object(self.agent.media_audio, "get_default_backend",
                                   side_effect=self.agent.media_audio.AudioConfigurationError("fixture")):
                self.assertIn("Озвучка недоступна", self.agent.tool_voice_message("hello"))
        finally:
            self.agent._TURN_OUTBOUND.reset(outbound_token)
            self.agent._TURN_CHANNEL.reset(token)

    def test_first_attempt_returns_while_preparation_is_waiting(self):
        item = self.intent(run_id=self.run.run_id)
        self.spool.enqueue(item)
        async def scenario():
            gate = asyncio.Event()
            entered = asyncio.Event()
            async def background(*args, **kwargs):
                entered.set()
                await gate.wait()
            with (mock.patch.object(self.agent, "run_speech_preparation_allowed", return_value=True),
                  mock.patch.object(self.runner, "_prepare_speech_in_background", side_effect=background),
                  mock.patch.object(self.runner, "_send_turn_media", new_callable=mock.AsyncMock) as send):
                before = time.monotonic()
                self.assertFalse(await self.runner._attempt_queued_media(None, item, ctx=self.ctx))
                self.assertLess(time.monotonic() - before, 0.5)
                await asyncio.wait_for(entered.wait(), 1)
                self.assertFalse(self.runner._SPEECH_PREPARING[item.queue_id].done())
                send.assert_not_called()
                gate.set()
                await self.runner._SPEECH_PREPARING[item.queue_id]
                self.runner._SPEECH_PREPARING.pop(item.queue_id, None)
        asyncio.run(scenario())

    def test_prepared_speech_uses_same_delivery_identity_and_acceptance_tombstone(self):
        item = self.intent(run_id=self.run.run_id)
        self.spool.enqueue(item)
        self.agent.run_delivery_started(item.run_id, chat_id="777", text_chars=0,
                                       media_count=1, media_queue_ids=[item.queue_id])
        xtts_intent.commit(self.spool, item, self.attachment(), self.metadata(item))
        async def scenario():
            send = mock.AsyncMock(return_value={"message_id": 991, "random_id": 123})
            with (mock.patch.object(self.spool, "ready", return_value=True),
                  mock.patch.object(self.runner, "_send_turn_media", send),
                  mock.patch.object(self.agent, "run_delivery_finalize_recovered", return_value=False)):
                self.assertTrue(await self.runner._attempt_queued_media(None, item, ctx=self.ctx))
                actual = send.call_args.args[1]
                self.assertEqual(actual.mime, "audio/ogg")
                self.assertEqual(actual.queue_id, item.queue_id)
                self.assertTrue(actual.voice_note)
                self.assertFalse(await self.runner._attempt_queued_media(None, item, ctx=self.ctx))
                send.assert_awaited_once()
        asyncio.run(scenario())
        self.assertEqual(self.spool.outbox_results("delivered")[0]["queue_id"], item.queue_id)
        self.assertFalse(self.spool.pending())
        rows = [row for row in self.manager.iter_events(item.run_id) if row.get("kind") == "speech_prepared"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["intent_sha256"], item.sha256)
        retained = self.manager.path(item.run_id) / rows[0]["artifact"]["path"]
        self.assertTrue(retained.is_file())
        self.assertEqual(retained.read_bytes()[:4], b"OggS")

    def test_concurrent_attempts_schedule_one_background_preparation(self):
        item = self.intent(run_id=self.run.run_id)
        self.spool.enqueue(item)
        async def scenario():
            barrier = threading.Barrier(2)
            gate = asyncio.Event()
            async def background(*args, **kwargs):
                await gate.wait()
            def ready(*args):
                barrier.wait(timeout=2)
                return True
            with (mock.patch.object(self.agent, "run_speech_preparation_allowed", return_value=True),
                  mock.patch.object(xtts_intent, "retry_ready", side_effect=ready),
                  mock.patch.object(self.runner, "_prepare_speech_in_background", side_effect=background) as prepare):
                await asyncio.gather(*(self.runner._attempt_queued_media(None, item, ctx=self.ctx) for _ in range(2)))
                self.assertEqual(prepare.call_count, 1)
                gate.set()
                await self.runner._SPEECH_PREPARING[item.queue_id]
                self.runner._SPEECH_PREPARING.pop(item.queue_id, None)
        asyncio.run(scenario())

    def test_background_completes_full_prepare_archive_and_send_after_return(self):
        item = self.intent(run_id=self.run.run_id)
        self.spool.enqueue(item)
        self.agent.run_delivery_started(item.run_id, chat_id="777", text_chars=0,
                                       media_count=1, media_queue_ids=[item.queue_id])
        async def scenario():
            entered, release = threading.Event(), threading.Event()
            def synthesize(request, *, stopped):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("fixture release was not set")
                return self.attachment(), self.metadata(item)
            client = mock.Mock(synthesize_intent=mock.Mock(side_effect=synthesize))
            with (mock.patch("xtts_tts_client.XttsTTSClient", return_value=client),
                  mock.patch.object(self.spool, "ready", return_value=True),
                  mock.patch.object(self.runner, "_send_turn_media", new_callable=mock.AsyncMock,
                                    return_value={"message_id": 992}) as send,
                  mock.patch.object(self.agent, "run_delivery_finalize_recovered", return_value=False)):
                self.assertFalse(await self.runner._attempt_queued_media(None, item, ctx=self.ctx))
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set())
                send.assert_not_called()
                task = self.runner._SPEECH_PREPARING[item.queue_id]
                release.set()
                await asyncio.wait_for(task, 3)
                send.assert_awaited_once()
                self.assertFalse(self.spool.pending())
                self.assertEqual(len(self.spool.outbox_results("delivered")), 1)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
