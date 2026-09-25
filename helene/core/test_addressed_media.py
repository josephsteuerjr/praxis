"""Картинка такому-то приходит картинкой, и расписка называет кому и что.

ЖИВОЙ СЛУЧАЙ 13.08.2026, ЛИЧКА ЕГОРА. Он попросил переслать Насте скрин. Фото ушло ему
самому, а она отчиталась «пересылка успешно ушла Насте, message_id=2049». Обе половины
были неправдой, и каждая по своей причине:

  * у `send_media` НЕ БЫЛО адресата вообще — она молча писала адресом текущий чат,
    поэтому для задачи «фото такому-то» исполнителя не существовало;
  * `message_id=2049` принадлежал ТЕКСТОВОМУ сообщению: расписка не различала видов
    вложения, и «что-то ушло» читалось как «ушла картинка».

Её собственный разбор (десант `code-eeb2f170`, 5 агентов): «это не транспортная поломка,
а дырка в интерфейсе». Её же воркер назвал минимальный цельный патч — он здесь и
воспроизведён; патч погиб вместе с worktree, см. test_reconcile_spares_live_work.py.

⚠ ЧТО ОХРАНЯЕТСЯ. Тип вложения — часть НАМЕРЕНИЯ, а не решение транспорта: он записан
до сети и переживает падение. Иначе повтор после краха доставил бы то же фото документом.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="praxis-addressed-media-session-"))
atexit.register(shutil.rmtree, _SESSION_DIR, True)
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ["TELEGRAM_SESSION"] = str(_SESSION_DIR / "telethon")
os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import mtproto_runner as runner  # noqa: E402
import telegram_outbox  # noqa: E402
import workshop  # noqa: E402


class TheHandNowHasAnAddress(unittest.TestCase):
    def test_send_media_declares_to_in_its_schema(self):
        """Без параметра в схеме рука не существует для неё, сколько бы кода ни лежало."""
        spec = next(t for t in agent.WORKSHOP_TOOLS if t["name"] == "send_media")
        self.assertIn("to", spec["input_schema"]["properties"])

    def test_an_addressed_photo_reaches_the_typed_durable_bridge(self):
        home = Path(tempfile.mkdtemp(prefix="praxis-media-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        picture = home / "screen.png"
        picture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        seen: list[tuple] = []
        with (
            patch.object(workshop, "_resolve_read", return_value=picture),
            patch.dict(agent._TELETHON,
                       {"send_file": lambda *a: seen.append(a) or "sent"}, clear=True),
        ):
            result = agent.tool_send_media(str(picture), "photo", caption="вот",
                                           to="@nastya_bayu")
        self.assertEqual(result, "sent")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][2], "@nastya_bayu")
        self.assertEqual(seen[0][3], "photo")

    def test_without_to_the_old_staged_path_is_untouched(self):
        home = Path(tempfile.mkdtemp(prefix="praxis-media-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        picture = home / "screen.png"
        picture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        staged: list[dict] = []
        with (
            patch.object(workshop, "_resolve_read", return_value=picture),
            patch.object(agent, "_stage_turn_media",
                         side_effect=lambda p, **kw: staged.append(kw) or "staged"),
            patch.dict(agent._TELETHON, {"send_file": _forbidden}, clear=True),
        ):
            result = agent.tool_send_media(str(picture), "photo", caption="вот")
        self.assertEqual(result, "staged")
        self.assertEqual(staged[0]["kind"], "photo")

    def test_an_addressed_photo_through_send_file_stays_a_photo(self):
        """Раньше явный `to` уводил фото на документный путь — молча и без следа."""
        home = Path(tempfile.mkdtemp(prefix="praxis-media-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        picture = home / "shot.jpg"
        picture.write_bytes(bytes.fromhex("ffd8ffe0") + b"0" * 64)
        seen: list[tuple] = []
        with (
            patch.object(workshop, "_resolve_read", return_value=picture),
            patch.dict(agent._TELETHON,
                       {"send_file": lambda *a: seen.append(a) or "sent"}, clear=True),
        ):
            workshop.send_file(str(picture), "подпись", to="@someone")
        self.assertEqual(seen[0][3], "photo")


def _forbidden(*_a, **_kw):
    raise AssertionError("отправка без адресата не должна идти прямым мостом")


class TheIntentRemembersWhatKindItWas(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="praxis-addressed-media-")
        self.old_outbox = runner._DIRECT_OUTBOX
        self.old_reconciled = runner._DIRECT_OUTBOX_RECONCILED
        runner._DIRECT_OUTBOX = telegram_outbox.TelegramOutbox(
            Path(self.tempdir.name) / "outbox")
        runner._DIRECT_OUTBOX_RECONCILED = set()

    async def asyncTearDown(self):
        runner._DIRECT_OUTBOX = self.old_outbox
        runner._DIRECT_OUTBOX_RECONCILED = self.old_reconciled
        self.tempdir.cleanup()

    def _voice(self) -> Path:
        source = Path(self.tempdir.name) / "note.ogg"
        source.write_bytes(b"OggS" + b"0" * 128)
        return source

    async def test_a_voice_note_survives_the_durable_round_trip(self):
        entry = runner._direct_outbox().prepare_file(
            "telegram-outbox:run-v:tool:call-v",
            peer_id=5034280146,
            source=self._voice(),
            visible_filename="note.ogg",
            mime="audio/ogg",
            caption="послушай",
            run_id="run-v",
            call_id="call-v",
            purpose="tool:send_file",
            media_kind="audio",
            voice_note=True,
        )
        self.assertEqual(entry["payload"]["media_kind"], "audio")
        self.assertTrue(entry["payload"]["voice_note"])

        sent: list = []

        async def _send(entity, item, **kwargs):
            sent.append(item)
            return types.SimpleNamespace(id=555), kwargs["random_id"]

        with (
            patch.object(agent, "direct_outbox_prepared", return_value=True),
            patch.object(runner, "_resolve_entity",
                         AsyncMock(return_value=types.SimpleNamespace(id=5034280146))),
            patch.object(runner, "_send_file_idempotent", AsyncMock(side_effect=_send)),
        ):
            accepted = await runner._send_direct_outbox_entry(entry)

        self.assertEqual(sent[0].kind, "audio")
        self.assertTrue(sent[0].voice_note)
        self.assertEqual(accepted["receipt"]["media_kind"], "audio")
        self.assertEqual(accepted["receipt"]["peer_id"], 5034280146)

    async def test_the_receipt_proves_address_and_kind_not_just_that_something_left(self):
        source = Path(self.tempdir.name) / "screen.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        entry = runner._direct_outbox().prepare_file(
            "telegram-outbox:run-p:tool:call-p",
            peer_id=-1001240718803,
            topic_id=98591,
            reply_to=98591,
            source=source,
            visible_filename="screen.png",
            mime="image/png",
            caption="",
            run_id="run-p",
            call_id="call-p",
            purpose="tool:send_file",
            media_kind="photo",
        )
        accepted = runner._direct_outbox().mark_accepted(entry["key"], message_id=2049)
        receipt = accepted["receipt"]
        self.assertEqual(receipt["peer_id"], -1001240718803)
        self.assertEqual(receipt["topic_id"], 98591)
        self.assertEqual(receipt["media_kind"], "photo")
        self.assertEqual(receipt["sha256"], entry["payload"]["sha256"])

    async def test_the_words_she_reads_name_the_kind(self):
        source = Path(self.tempdir.name) / "screen.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        entry = runner._direct_outbox().prepare_file(
            "telegram-outbox:run-w:tool:call-w",
            peer_id=5034280146,
            source=source,
            visible_filename="screen.png",
            mime="image/png",
            caption="",
            run_id="run-w",
            call_id="call-w",
            purpose="tool:send_file",
            media_kind="photo",
        )
        accepted = runner._direct_outbox().mark_accepted(entry["key"], message_id=2049)
        words = runner._direct_outbox_result(accepted, label="Настя")
        self.assertIn("Отправлено: фото", words)
        self.assertIn("Настя", words)

    async def test_the_same_key_may_not_change_the_kind_underneath(self):
        source = Path(self.tempdir.name) / "screen.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        common = dict(
            peer_id=42, source=source, visible_filename="screen.png",
            mime="image/png", caption="", run_id="run-c", call_id="call-c",
            purpose="tool:send_file",
        )
        runner._direct_outbox().prepare_file(
            "telegram-outbox:run-c:tool:call-c", media_kind="photo", **common)
        with self.assertRaises(telegram_outbox.TelegramOutboxConflict):
            runner._direct_outbox().prepare_file(
                "telegram-outbox:run-c:tool:call-c", media_kind="document", **common)


class OlderEntriesStillMeanDocument(unittest.TestCase):
    """Записи до 13.08 не знали слова «тип» — и означали ровно документ."""

    def test_a_payload_without_a_kind_reads_as_a_document(self):
        filled = telegram_outbox._file_payload_with_kind(
            {"mime": "application/pdf", "sha256": "0" * 64})
        self.assertEqual(filled["media_kind"], "document")
        self.assertFalse(filled["voice_note"])

    def test_an_unknown_kind_is_refused_rather_than_guessed(self):
        with self.assertRaises(telegram_outbox.TelegramOutboxValidationError):
            telegram_outbox._media_kind("sticker")


if __name__ == "__main__":
    unittest.main()
