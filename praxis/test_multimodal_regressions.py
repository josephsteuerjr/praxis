"""Hermetic regressions for guarded multimodal delivery.

Run with:  python praxis_test.py test_multimodal_regressions -v
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import types
import unittest
import wave
from collections import defaultdict, deque
from pathlib import Path
from unittest.mock import Mock, patch

import agent
import media
import media_audio
import workshop


class _ImportOnlyTelegramClient:
    """Enough of Telethon for runner decorators; never opens a session/network."""

    def __init__(self, *args, **kwargs):
        pass

    def on(self, *args, **kwargs):
        return lambda fn: fn


class _ImportOnlyEvents:
    @staticmethod
    def NewMessage(*args, **kwargs):
        return object()

    @staticmethod
    def ChatAction(*args, **kwargs):
        return object()


if "mtproto_runner" not in sys.modules:
    _prior_telethon = sys.modules.get("telethon")
    _fake_telethon = types.ModuleType("telethon")
    _fake_telethon.TelegramClient = _ImportOnlyTelegramClient
    _fake_telethon.events = _ImportOnlyEvents
    sys.modules["telethon"] = _fake_telethon
    try:
        import mtproto_runner as runner
    finally:
        if _prior_telethon is None:
            sys.modules.pop("telethon", None)
        else:
            sys.modules["telethon"] = _prior_telethon
else:
    import mtproto_runner as runner


JPEG = b"\xff\xd8\xff\xe0" + b"regression-jpeg"
OGG = b"OggS" + b"regression-audio"


class _FakeAudioBackend:
    def __init__(self, root: Path):
        self.root = root

    def synthesize(self, text: str) -> Path:
        path = self.root / "spoken.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(b"\x00\x00" * 80)
        return path

    def transcribe(self, path) -> str:
        return "local transcript"


class RegressionBase(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="praxis_multi_regression_")
        self.root = Path(self.td.name)
        (self.root / "workspace").mkdir(parents=True)
        self.spool = media.MediaSpool(
            self.root / "workspace" / "media",
            photo_max_bytes=4096,
            audio_max_bytes=4096,
            max_total_bytes=32768,
            ttl_seconds=3600,
            max_queue=8,
            max_turn_media=8,
        )
        self._old_agent_spool = agent._MEDIA_SPOOL
        self._old_runner_spool = runner._MEDIA_SPOOL
        self._old_workshop = (workshop.BASE, workshop.REPO)
        agent._MEDIA_SPOOL = self.spool
        runner._MEDIA_SPOOL = self.spool
        workshop.BASE = self.root
        workshop.REPO = self.root
        media_audio.set_default_backend(_FakeAudioBackend(self.root))

    def tearDown(self) -> None:
        agent._MEDIA_SPOOL = self._old_agent_spool
        runner._MEDIA_SPOOL = self._old_runner_spool
        workshop.BASE, workshop.REPO = self._old_workshop
        media_audio.set_default_backend(None)
        self.td.cleanup()

    @property
    def owner_ctx(self) -> agent.ChannelContext:
        return agent.ChannelContext(chat_id="777", is_dm=True, owner=True)

    def photo_path(self, name: str = "photo.jpg") -> Path:
        path = self.root / "workspace" / name
        path.write_bytes(JPEG)
        return path

    def audio_path(self, name: str = "voice.ogg") -> Path:
        path = self.root / "workspace" / name
        path.write_bytes(OGG)
        return path


class TestAgentGuardRegressions(RegressionBase):
    def test_sequential_send_media_and_send_file_keep_both_queue_ids(self) -> None:
        """Sequential tool calls retain both attachments with inspectable speech."""
        audio = self.audio_path("instrumental.ogg")
        midi = self.root / "workspace" / "score.mid"
        midi.write_bytes(b"MThd" + b"\x00" * 32)
        outbound = []
        channel_token = agent._TURN_CHANNEL.set(self.owner_ctx)
        outbound_token = agent._TURN_OUTBOUND.set(outbound)
        try:
            before_audio = agent._tool_outbound_snapshot()
            result = agent._call_tool_with_ceiling(
                "send_media", agent.tool_send_media,
                {"path": str(audio), "kind": "audio"},
            )
            self.assertIn("Подготовила audio", result)
            audio_metadata = agent._tool_result_metadata(before_audio)
            before_file = agent._tool_outbound_snapshot()
            result = agent._call_tool_with_ceiling(
                "send_file", workshop.send_file, {"path": str(midi)},
            )
            self.assertIn("Подготовила document", result)
            file_metadata = agent._tool_result_metadata(before_file)
            queue_ids = [item.queue_id for item in outbound]
            self.assertEqual(len(set(queue_ids)), 2)
            self.assertEqual(
                [item["queue_id"] for item in
                 audio_metadata["outbound"] + file_metadata["outbound"]], queue_ids,
            )
            self.assertEqual(
                [item["queue_id"] for item in agent._tool_outbound_snapshot()], queue_ids,
            )
            with patch.object(media_audio, "transcribe", return_value="Recognized owner speech"):
                kept, context, images, discriminator = agent._prepare_outbound_guard(
                    outbound, {}, self.owner_ctx,
                )
            self.assertEqual([item.queue_id for item in kept], queue_ids)
            self.assertEqual(discriminator, "|".join(queue_ids))
            self.assertIn("Recognized owner speech", context)
            self.assertIn("document score.mid", context)
            self.assertEqual(images, ())
            self.assertTrue(all(item.path.is_file() for item in kept))
        finally:
            agent._TURN_OUTBOUND.reset(outbound_token)
            agent._TURN_CHANNEL.reset(channel_token)

    def _sequential_group_attachments(self, transcript: str) -> None:
        ctx = agent.ChannelContext(chat_id="779", is_dm=False, known=True)
        self.assertEqual(ctx.scope, "group")
        audio = self.audio_path("group-audio.ogg")
        midi = self.root / "workspace" / "group-score.mid"
        midi.write_bytes(b"MThd" + b"\x00" * 32)
        outbound = []
        channel_token = agent._TURN_CHANNEL.set(ctx)
        outbound_token = agent._TURN_OUTBOUND.set(outbound)
        try:
            before_audio = agent._tool_outbound_snapshot()
            agent._call_tool_with_ceiling(
                "send_media", agent.tool_send_media,
                {"path": str(audio), "kind": "audio"},
            )
            audio_metadata = agent._tool_result_metadata(before_audio)
            before_file = agent._tool_outbound_snapshot()
            agent._call_tool_with_ceiling(
                "send_file", workshop.send_file, {"path": str(midi)},
            )
            file_metadata = agent._tool_result_metadata(before_file)
            queue_ids = [item.queue_id for item in outbound]
            self.assertEqual(len(set(queue_ids)), 2)
            self.assertEqual(
                [item["queue_id"] for item in
                 audio_metadata["outbound"] + file_metadata["outbound"]], queue_ids,
            )
            self.assertEqual(
                [item["queue_id"] for item in agent._tool_outbound_snapshot()], queue_ids,
            )
            with (patch.object(media_audio, "transcribe", return_value=transcript),
                  patch.object(agent, "_run_event") as event):
                if transcript:
                    kept, context, images, discriminator = agent._prepare_outbound_guard(
                        outbound, {}, ctx,
                    )
                    event.assert_not_called()
                    self.assertIn(transcript, context)
                else:
                    with self.assertLogs(agent.log, level="WARNING") as logs:
                        kept, context, images, discriminator = agent._prepare_outbound_guard(
                            outbound, {}, ctx,
                        )
                    event.assert_called_once_with(
                        "outbound_media_preparation_rejected",
                        queue_id=queue_ids[0], reason="audio_empty_transcript",
                    )
                    self.assertEqual(len(logs.output), 1)
                    self.assertIn("queue_id=" + queue_ids[0], logs.output[0])
                    self.assertIn("reason=audio_empty_transcript", logs.output[0])
                    self.assertNotIn(str(self.root), logs.output[0])
                    self.assertFalse(outbound[0].path.exists())
                expected_ids = queue_ids if transcript else queue_ids[1:]
                self.assertEqual([item.queue_id for item in kept], expected_ids)
                self.assertEqual(discriminator, "|".join(expected_ids))
                self.assertIn("document group-score.mid", context)
                self.assertEqual(images, ())
                self.assertTrue(all(item.path.is_file() for item in kept))
        finally:
            agent._TURN_OUTBOUND.reset(outbound_token)
            agent._TURN_CHANNEL.reset(channel_token)

    def test_group_sequential_audio_and_midi_with_transcript_keep_both(self) -> None:
        """Group preparation preserves both sequential queue IDs with inspectable speech."""
        self._sequential_group_attachments("Recognized speech for the group")

    def test_group_sequential_empty_stt_rejects_audio_but_keeps_midi(self) -> None:
        """Historical group case is preparation rejection, not staged queue loss."""
        self._sequential_group_attachments("")

    def test_empty_audio_transcript_rejected_for_every_audience(self) -> None:
        contexts = [
            self.owner_ctx,
            agent.ChannelContext(chat_id="777", is_dm=True, known=True,
                                 principal_id=agent.PRAXIS_SELF_PRINCIPAL,
                                 _scope_override="owner"),
            agent.ChannelContext(chat_id="778", is_dm=True, known=True),
            agent.ChannelContext(chat_id="779", is_dm=False, owner=True),
            agent.ChannelContext(chat_id="780", is_dm=True, _scope_override="owner"),
        ]
        for index, ctx in enumerate(contexts):
            with self.subTest(ctx=ctx):
                item = self.spool.resolve_outbound(
                    self.audio_path(f"empty-{index}.ogg"), kind="audio",
                    target_chat_id=ctx.chat_id, scope=ctx.scope)
                with (
                    patch.object(media_audio, "transcribe", return_value="  "),
                    patch.object(agent, "_run_event") as event,
                    self.assertLogs(agent.log, level="WARNING") as logs,
                ):
                    result = agent._prepare_outbound_guard([item], {}, ctx)
                self.assertEqual(result, ([], "", (), ""))
                event.assert_called_once_with(
                    "outbound_media_preparation_rejected",
                    queue_id=item.queue_id, reason="audio_empty_transcript",
                )
                self.assertIn("queue_id=" + item.queue_id, "\n".join(logs.output))
                self.assertIn("reason=audio_empty_transcript", "\n".join(logs.output))
                self.assertNotIn(str(item.path), "\n".join(logs.output))
                self.assertFalse(item.path.exists())

    def test_audio_backend_failure_has_safe_rejection_reason(self) -> None:
        item = self.spool.resolve_outbound(
            self.audio_path(), kind="audio", target_chat_id="777", scope="owner")
        with (
            patch.object(media_audio, "transcribe",
                         side_effect=media_audio.AudioProcessingError(
                             f"backend failed at {item.path}: PRIVATE-TRANSCRIPT")),
            self.assertLogs(agent.log, level="WARNING") as logs,
        ):
            result = agent._prepare_outbound_guard([item], {}, self.owner_ctx)
        self.assertEqual(result, ([], "", (), ""))
        self.assertIn("reason=audio_transcription_failed", "\n".join(logs.output))
        self.assertNotIn("backend failed", "\n".join(logs.output))
        self.assertNotIn(str(item.path), "\n".join(logs.output))
        self.assertNotIn("PRIVATE-TRANSCRIPT", "\n".join(logs.output))
        self.assertIn("queue_id=" + item.queue_id, "\n".join(logs.output))
        self.assertFalse(item.path.exists())

    def test_invalid_queue_id_is_redacted_from_logs_and_rejection_events(self) -> None:
        item = self.spool.resolve_outbound(
            self.audio_path(), kind="audio", target_chat_id="777", scope="owner")
        invalid_queue_id = f"{item.path}\nPRIVATE-QUEUE-PAYLOAD"
        item = media.OutboundMedia(
            kind=item.kind, path=item.path, mime=item.mime, size=item.size,
            target_chat_id=item.target_chat_id, scope=item.scope,
            sha256=item.sha256, queue_id=invalid_queue_id,
        )
        with (
            patch.object(media_audio, "transcribe") as stt,
            patch.object(agent, "_run_event") as event,
            self.assertLogs(agent.log, level="WARNING") as logs,
        ):
            result = agent._prepare_outbound_guard([item], {}, self.owner_ctx)
        self.assertEqual(result, ([], "", (), ""))
        stt.assert_not_called()
        event.assert_called_once_with(
            "outbound_media_preparation_rejected",
            queue_id="[redacted-invalid-queue-id]", reason="validation_failed",
        )
        diagnostics = ("\n".join(logs.output), repr(event.call_args_list))
        for diagnostic in diagnostics:
            self.assertNotIn(invalid_queue_id, diagnostic)
            self.assertNotIn(str(item.path), diagnostic)
            self.assertNotIn("PRIVATE-QUEUE-PAYLOAD", diagnostic)
            self.assertIn("[redacted-invalid-queue-id]", diagnostic)

    def test_audio_preparation_does_not_bypass_validation(self) -> None:
        for invalid in ("chat", "scope", "bytes"):
            with self.subTest(invalid=invalid):
                item = self.spool.resolve_outbound(
                    self.audio_path(f"invalid-{invalid}.ogg"), kind="audio",
                    target_chat_id="778" if invalid == "chat" else "777",
                    scope="known" if invalid == "scope" else "owner")
                if invalid == "bytes":
                    item.path.write_bytes(OGG + b"tampered")
                with (
                    patch.object(media_audio, "transcribe", return_value="") as stt,
                    self.assertLogs(agent.log, level="WARNING"),
                ):
                    result = agent._prepare_outbound_guard([item], {}, self.owner_ctx)
                self.assertEqual(result, ([], "", (), ""))
                stt.assert_not_called()

    def test_audio_guard_still_rejects_oversized_transcript(self) -> None:
        item = self.spool.resolve_outbound(
            self.audio_path(), kind="audio", target_chat_id="777", scope="owner")
        with (
            patch.object(media_audio, "transcribe",
                         return_value="x" * (agent._OUTBOUND_AUDIO_GUARD_CHARS + 1)),
            self.assertLogs(agent.log, level="WARNING") as logs,
        ):
            result = agent._prepare_outbound_guard([item], {}, self.owner_ctx)
        self.assertEqual(result, ([], "", (), ""))
        self.assertIn("reason=audio_transcript_too_long", "\n".join(logs.output))

    def test_outbound_document_guard_uses_exact_metadata_without_contents(self) -> None:
        source = self.root / "workspace" / "build report.txt"
        secret_contents = b"PRIVATE-CONTENTS-MUST-NOT-BE-IN-THE-GUARD"
        source.write_bytes(secret_contents)
        item = self.spool.resolve_outbound(
            source, kind="document", target_chat_id="777", scope="owner",
            caption="requested build report",
        )

        kept, context, images, discriminator = agent._prepare_outbound_guard(
            [item], {}, self.owner_ctx,
        )

        self.assertEqual(kept, [item])
        self.assertIn("document build_report.txt", context)
        self.assertIn("application/octet-stream", context)
        self.assertIn(f"({len(secret_contents)} bytes)", context)
        self.assertIn(f"sha256={item.sha256}", context)
        self.assertIn("caption: requested build report", context)
        self.assertNotIn(secret_contents.decode("ascii"), context)
        self.assertEqual(images, ())
        self.assertEqual(discriminator, item.queue_id)

    def test_outbound_tts_full_text_reaches_guard_context(self) -> None:
        spoken = "Начало " + ("очень-длинный-текст " * 20) + " КОНЕЦ_НЕ_ОБРЕЗАН"
        seen: dict = {}

        def fake_voice(*args, **kwargs):
            agent.tool_speak(spoken, caption="voice")
            return "Готово."

        def fake_guard(reply, *args, **kwargs):
            seen.update(kwargs)
            return reply

        with (
            patch.object(agent.llm, "configured", return_value=True),
            patch.object(agent, "_voice", side_effect=fake_voice),
            patch.object(agent, "guard_outbound_reply", side_effect=fake_guard),
            patch.object(agent, "_presence_frame", return_value=""),
            patch.object(agent, "build_state_block", return_value=""),
            patch.object(agent, "recent_journal", return_value=""),
            patch.object(agent.turns, "begin", return_value={"kind": "chat"}),
        ):
            envelope = agent.voice_turn_envelope(
                "777", "Егор: ответь голосом", "Егор", ctx=self.owner_ctx)

        self.assertEqual(envelope.text, "Готово.")
        self.assertEqual(len(envelope.outbound), 1)
        self.assertFalse((self.root / "spoken.wav").exists(),
                         "synthesized temp WAV must move into the TTL-managed spool")
        context = seen["outbound_context"]
        self.assertIn("Точный текст синтезированной речи", context)
        self.assertIn(spoken, context)
        self.assertIn("КОНЕЦ_НЕ_ОБРЕЗАН", context)

    def test_owner_audience_media_bypasses_evaluator_for_praxis_self(self) -> None:
        source = self.photo_path()
        owner_audience = agent.ChannelContext(
            chat_id="777", principal_id=agent.PRAXIS_SELF_PRINCIPAL,
            is_dm=True, owner=False, known=True, _scope_override="owner",
        )

        def fake_voice(*_args, **_kwargs):
            agent._stage_turn_media(source, kind="photo", caption="owner-only diagram")
            return "Вот файл, который я решила прислать."

        with (
            patch.object(agent.llm, "configured", return_value=True),
            patch.object(agent, "_voice", side_effect=fake_voice),
            patch.object(
                agent, "evaluate_reply",
                side_effect=AssertionError("owner-audience media must not reach evaluator"),
            ),
            patch.object(agent, "_presence_frame", return_value=""),
            patch.object(agent, "build_state_block", return_value=""),
            patch.object(agent, "recent_journal", return_value=""),
            patch.object(agent.notes, "append", return_value=None),
            patch.object(agent.turns, "begin", return_value={"kind": "chat"}),
            patch.object(agent.turns, "record", return_value=None),
        ):
            envelope = agent.voice_turn_envelope(
                "777", "[self-trigger]", "Praxis", ctx=owner_audience)

        self.assertEqual(envelope.text, "Вот файл, который я решила прислать.")
        self.assertEqual(len(envelope.outbound), 1)
        self.assertEqual(envelope.outbound[0].caption, "owner-only diagram")

    def test_outbound_image_is_passed_to_evaluator(self) -> None:
        source = self.photo_path()
        seen: dict = {}
        known_dm = agent.ChannelContext(chat_id="778", is_dm=True, owner=False, known=True)

        def fake_voice(*args, **kwargs):
            agent._stage_turn_media(source, kind="photo", caption="diagram")
            return "Вот изображение."

        def fake_evaluate(*args, **kwargs):
            seen.update(kwargs)
            return "ok", ""

        with (
            patch.object(agent.llm, "configured", return_value=True),
            patch.object(agent, "_voice", side_effect=fake_voice),
            patch.object(agent, "evaluate_reply", side_effect=fake_evaluate),
            patch.object(agent, "_presence_frame", return_value=""),
            patch.object(agent, "build_state_block", return_value=""),
            patch.object(agent, "recent_journal", return_value=""),
            patch.object(agent.notes, "append", return_value=None),
            patch.object(agent.turns, "begin", return_value={"kind": "chat"}),
            patch.object(agent.turns, "record", return_value=None),
            patch.object(agent.turns, "tail_block", return_value=""),
        ):
            envelope = agent.voice_turn_envelope(
                "778", "Гость: пришли картинку", "Гость", ctx=known_dm)

        self.assertEqual(envelope.text, "Вот изображение.")
        self.assertEqual(len(envelope.outbound), 1)
        images = seen["outbound_images"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["type"], "image")
        self.assertEqual(images[0]["mime"], "image/jpeg")
        self.assertEqual(Path(images[0]["path"]), envelope.outbound[0].path)

    def _turn_with_staged_photo(self, judge, *, caption="diagram"):
        """Ход с одним вложением и заданным поведением судьи -> (конверт, staged-копии)."""
        source = self.photo_path()
        staged: list[media.OutboundMedia] = []
        known_dm = agent.ChannelContext(chat_id="778", is_dm=True, owner=False, known=True)

        def fake_voice(*args, **kwargs):
            agent._stage_turn_media(source, kind="photo", caption=caption)
            staged.extend(agent._TURN_OUTBOUND.get() or ())
            return "Safe-looking wrapper."

        with (
            patch.object(agent.llm, "configured", return_value=True),
            patch.object(agent.llm, "chat", **judge),
            patch.object(agent, "_voice", side_effect=fake_voice),
            patch.object(agent, "_presence_frame", return_value=""),
            patch.object(agent, "build_state_block", return_value=""),
            patch.object(agent, "recent_journal", return_value=""),
            patch.object(agent, "tool_journal", return_value=""),
            patch.object(agent, "_held_self_wake", return_value=None),
            patch.object(agent.notes, "append", return_value=None),
            patch.object(agent.turns, "begin", return_value={"kind": "chat"}),
            patch.object(agent.turns, "record", return_value=None),
            patch.object(agent.turns, "tail_block", return_value=""),
        ):
            envelope = agent.voice_turn_envelope(
                "778", "Гость: отправь", "Гость", ctx=known_dm)
        self.assertEqual(len(staged), 1)
        return envelope, staged

    def test_evaluator_failure_neither_holds_her_word_nor_eats_her_attachment(self) -> None:
        # ⚠ Тест назывался `..._holds_and_deletes_staged_media` и закреплял fail-closed:
        # судья упал -> её текст придержан, её вложение УДАЛЕНО. Решением Егора 27.07
        # недоступность судьи перестала быть классом отказа вообще: нет ответа — нет и
        # совета, а её работа не платит за чужой сбой.
        envelope, staged = self._turn_with_staged_photo(
            {"side_effect": RuntimeError("evaluator unavailable")})

        self.assertEqual(envelope.text, "Safe-looking wrapper.")
        self.assertEqual(len(envelope.outbound), 1)
        self.assertTrue(staged[0].path.exists(),
                        "вложение не выбрасывается из-за того, что судья не ответил")

    def test_unassessable_media_is_advice_not_a_confiscation(self) -> None:
        # «Я не смогла посмотреть» — признание судьи о себе, а не находка о приватности.
        class _Resp:
            text = "PRIVACY_HOLD_UNASSESSABLE_MEDIA"
            stop_reason = "end_turn"

        envelope, staged = self._turn_with_staged_photo({"return_value": _Resp()})

        self.assertEqual(envelope.text, "Safe-looking wrapper.")
        self.assertEqual(len(envelope.outbound), 1)
        self.assertTrue(staged[0].path.exists())

    def test_staged_image_credential_still_stops_and_removes_the_outbound_copy(self) -> None:
        # Единственный код судьи, который остался стопом: пиксели механический кред-пол
        # не читает, а ключ на скриншоте — тот же ключ (адверсарка 23.07).
        class _Resp:
            text = "PRIVACY_HOLD_CREDENTIAL"
            stop_reason = "end_turn"

        source = self.photo_path()
        envelope, staged = self._turn_with_staged_photo({"return_value": _Resp()})

        self.assertEqual(envelope.text, "")
        self.assertEqual(envelope.outbound, ())
        self.assertFalse(staged[0].path.exists(), "копия в исходящем спуле убрана")
        self.assertTrue(source.exists(),
                        "исходный файл в её рабочем дереве не трогаем — это её работа")

    def test_authored_repetition_is_never_silently_suppressed(self) -> None:
        ctx = agent.ChannelContext(chat_id="-100", is_dm=False, owner=False, known=True)

        with (
            patch.object(agent, "evaluate_reply", return_value=("ok", "")),
            patch.object(agent.notes, "append", return_value=None),
            patch.object(agent.turns, "tail_block", return_value=""),
        ):
            first = agent.guard_outbound_reply(
                "Готово", "вася: пришли первое", ctx=ctx,
                repeat_discriminator="media-one")
            second = agent.guard_outbound_reply(
                "Готово", "вася: пришли второе", ctx=ctx,
                repeat_discriminator="media-two")
            duplicate = agent.guard_outbound_reply(
                "Готово", "вася: повтор", ctx=ctx,
                repeat_discriminator="media-two")

        self.assertEqual(first, "Готово")
        self.assertEqual(second, "Готово")
        self.assertEqual(duplicate, "Готово")


class TestRunnerRetryRegressions(RegressionBase):
    def test_cancelled_voice_wait_returns_authored_envelope_after_thread_finishes(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def blocking_turn(*args, **kwargs):
            entered.set()
            release.wait(timeout=5)
            return media.TurnEnvelope(text="authored", run_id="run-cancelled")

        async def exercise():
            with patch.object(agent, "voice_turn_envelope", side_effect=blocking_turn):
                task = asyncio.create_task(
                    runner._await_despite_cancellation(
                        runner._voice_turn_offloaded("777", "hello", "Егор")
                    )
                )
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                task.cancel()
                await asyncio.sleep(0.05)
                self.assertFalse(task.done())
                release.set()
                envelope, cancellation_seen = await task
                self.assertEqual(envelope.run_id, "run-cancelled")
                self.assertTrue(cancellation_seen)

        asyncio.run(exercise())

    def test_pending_inbound_is_retained_for_retry_then_consumed_terminally(self) -> None:
        chat_id = "777"
        ref = self.spool.ingest_bytes(
            JPEG, kind="photo", filename="in.jpg", chat_id=chat_id,
            message_id=1, scope="owner")
        later_ref = self.spool.ingest_bytes(
            JPEG + b"later", kind="photo", filename="later.jpg", chat_id=chat_id,
            message_id=2, scope="owner")
        pending = defaultdict(lambda: deque(maxlen=16))
        pending[chat_id].append(ref)
        meta = {
            chat_id: {
                "entity": 777,
                "is_dm": False,
                "is_owner": True,
                "known": True,
                "family": False,
                "name": "Егор",
                "title": "room",
                "size": 2,
                # Rolling metadata has already moved to an unrelated later speaker.
                "addressed": False,
                "addressed_mid": None,
                "room_mode": "normal",
            }
        }
        wake = runner.GroupWake(
            message_id=1, message_ts=100.0, kind="reply", speaker="Егор",
            sender_id=123, owner=True, known=True, family=False,
            context_snapshot="Егор: фото", reply_targets_snapshot=((1, "Егор", "фото"),),
            media_snapshot=(ref,))
        wakes = {chat_id: wake}
        # Медиа из более позднего фонового сообщения остаётся за frozen-границей.
        pending[chat_id].append(later_ref)
        calls: list[tuple[media.MediaRef, ...]] = []
        arm = Mock()

        def fake_turn(*args, **kwargs):
            calls.append(tuple(kwargs.get("media_refs") or ()))
            if len(calls) == 1:
                return media.TurnEnvelope(retry_media=True)
            return media.TurnEnvelope()

        async def fake_compact(chat):
            return None

        async def exercise():
            await runner._run_pass(chat_id)
            self.assertEqual(tuple(pending[chat_id]), (ref, later_ref))
            self.assertIs(wakes[chat_id], wake, "media retry обязан сохранить тот же wake")
            await runner._run_pass(chat_id)
            await asyncio.sleep(0)

        with (
            patch.object(runner, "_pending_media", pending),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_last_pass", defaultdict(float)),
            patch.object(runner, "_passing", set()),
            patch.object(runner, "_recent_msgs", defaultdict(lambda: deque(maxlen=12))),
            patch.object(runner, "_missed", {}),
            patch.object(runner, "_cooldown", return_value=0.0),
            patch.object(runner, "_last_n_text",
                         side_effect=AssertionError("group wake must not fetch moving live history")),
            patch.object(runner, "_maybe_compact", side_effect=fake_compact),
            patch.object(runner, "_arm", arm),
            patch.object(agent, "voice_turn_envelope", side_effect=fake_turn),
        ):
            asyncio.run(exercise())

        self.assertEqual(calls, [(ref,), (ref,)])
        arm.assert_called_once_with(chat_id)
        self.assertEqual(tuple(pending[chat_id]), (later_ref,))
        self.assertNotIn(chat_id, wakes)

    def test_failed_upload_stays_queued_and_retry_acknowledges_it(self) -> None:
        source = self.photo_path("retry.jpg")
        item = self.spool.resolve_outbound(
            source, kind="photo", target_chat_id="777", scope="owner")
        attempts: list[str] = []

        class FlakyClient:
            async def send_file(self, *args, **kwargs):
                attempts.append(str(args[1]))
                if len(attempts) == 1:
                    raise RuntimeError("temporary Telegram failure")

        meta = {
            "777": {
                "entity": 777,
                "is_dm": True,
                "is_owner": True,
                "known": True,
                "family": False,
                "name": "Егор",
            }
        }

        async def exercise():
            ctx = agent.ChannelContext(chat_id="777", is_dm=True, owner=True)
            first = await runner._queue_and_send_media(777, item, ctx=ctx)
            self.assertFalse(first)
            self.assertEqual(self.spool.pending(), (item,))
            self.assertTrue(item.path.exists())
            self.assertEqual(attempts, [], "staging must not attempt an early upload")
            clock.return_value = 1029.999
            await runner._media_cleanup_once()
            self.assertEqual(attempts, [], "the entire 30 second pause is mandatory")
            clock.return_value = 1030.0
            await runner._media_cleanup_once()
            self.assertEqual(len(attempts), 1)
            self.assertEqual(self.spool.pending(), (item,))
            self.assertTrue(item.path.exists(), "failed upload must remain retryable")
            await runner._media_cleanup_once()

        with (
            patch.object(media.time, "time", return_value=1000.0) as clock,
            patch.object(runner, "client", FlakyClient()),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_MEDIA_SENDING", set()),
            patch.object(runner, "_buf_push", return_value=None),
        ):
            asyncio.run(exercise())

        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.spool.pending(), ())
        self.assertFalse(item.path.exists())


class TestWorkshopRoutingRegression(RegressionBase):
    def test_send_file_routes_photo_and_audio_through_guarded_media_tool(self) -> None:
        photo = self.photo_path("route.jpg")
        audio = self.audio_path("route.ogg")
        routed: list[tuple[str, str, str]] = []

        def guarded(path, kind, caption="", **kwargs):
            routed.append((str(path), str(kind), str(caption)))
            return f"guarded:{kind}"

        def immediate_bridge(*args, **kwargs):
            raise AssertionError("photo/audio must not use the immediate send_file bridge")

        channel_token = agent._TURN_CHANNEL.set(self.owner_ctx)
        outbound_token = agent._TURN_OUTBOUND.set([])
        try:
            with (
                patch.object(agent, "tool_send_media", side_effect=guarded),
                patch.dict(agent._TELETHON, {"send_file": immediate_bridge}, clear=True),
            ):
                photo_result = workshop.send_file("workspace/route.jpg", "photo caption")
                audio_result = workshop.send_file("workspace/route.ogg", "audio caption")
        finally:
            agent._TURN_OUTBOUND.reset(outbound_token)
            agent._TURN_CHANNEL.reset(channel_token)

        self.assertEqual(photo_result, "guarded:photo")
        self.assertEqual(audio_result, "guarded:audio")
        self.assertEqual(
            routed,
            [
                (str(photo.resolve()), "photo", "photo caption"),
                (str(audio.resolve()), "audio", "audio caption"),
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
