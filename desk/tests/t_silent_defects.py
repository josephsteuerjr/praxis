# -*- coding: utf-8 -*-
"""Точечные стенды трёх молчаливых дефектов (1.4.0, волна A).

Запуск:  python tests/t_silent_defects.py

Каждый дефект нашла разведка 06.10 (root 8b0e8faf); здесь
они прикрыты тестами того уровня, на котором живут:

  * (a) runner._handle_note: записка `/resume` при живом семенном стопе —
    owner_stop.resume() кидает RuntimeError, и прежде он убивал ГЛАВНЫЙ цикл
    движка. Теперь: движок жив, владелец видит записку-ошибку, записка помечена
    разобранной (replay не будет терзать её трижды);
  * (b) updates.py: описания рук TOOL и TOOL_DEСК обещали «промолчишь до срока —
    вернём прежнюю версию», а с 04.10 молчание оставляет новую (trial_tick →
    timeout → _success). Текст — часть протокола между агентом и установщиком;
    стенд держит его честным: оба текста говорят правду и не врут ни в одну
    сторону;
  * (c) updater.py: провал записи расписки в дерево агента молчал в журнал —
    окно и агент видели СТАРУЮ расписку о живом обновлении. Теперь вторая
    попытка: резервный файл рядом (в дереве), при новом провале — копия в дом
    исполнителя. Молчание исключено, зелёный лог не врёт.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness")]

import updates  # noqa: E402


class ResumeNoteUnderSeedStop(unittest.TestCase):
    """(a) /resume не роняет цикл и не теряет записку молча."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="helene-resume-")
        self.addCleanup(self._tmp.cleanup)
        self.processed = Path(self._tmp.name) / "processed"
        self.processed.mkdir(parents=True)

    def test_runtimeerror_becomes_an_error_note_and_a_done_mark(self):
        import runner  # noqa: PLC0415  (импорт после sys.path)
        said: list[str] = []

        def refuse(*_a, **_kw):
            raise RuntimeError("Use explicit native Resume to clear the seed stop first")

        desk = types.SimpleNamespace(deliver=lambda text, **_kw: said.append(text))
        note = self.processed / "123__to__window.md"
        note.write_text("/resume", encoding="utf-8")
        with mock.patch.object(runner.owner_stop, "resume", refuse), \
                mock.patch.object(runner, "_room", lambda _key: desk), \
                mock.patch.object(runner, "_SEALED", False):
            runner._handle_note(note, "/resume", self.processed)
        # движок не упал (мы дошли до сюда), владелец получил записку-ошибку…
        self.assertEqual(len(said), 1, "записка-ошибка не легла в окно")
        self.assertIn("не прошёл", said[0])
        self.assertIn("Resume", said[0])
        # …и записка помечена разобранной: replay не будет терзать её трижды
        marks = list(self.processed.glob("*.done"))
        self.assertEqual(len(marks), 1, "метка .done не легла")
        self.assertIn("refused", marks[0].read_text(encoding="utf-8"))

    def test_happy_resume_still_marks_done(self):
        import runner  # noqa: PLC0415
        called = []
        desk = types.SimpleNamespace(deliver=lambda *_a, **_kw: called.append("deliver"))
        note = self.processed / "456.md"
        note.write_text("/resume", encoding="utf-8")
        with mock.patch.object(runner.owner_stop, "resume", lambda: called.append("resume")), \
                mock.patch.object(runner, "_room", lambda _key: desk), \
                mock.patch.object(runner, "_SEALED", False):
            runner._handle_note(note, "/resume", self.processed)
        self.assertEqual(called, ["resume"])
        self.assertIn("resumed", (self.processed / "456.md.done").read_text(encoding="utf-8"))

    def test_empty_note_still_ignored(self):
        import runner  # noqa: PLC0415
        note = self.processed / "789.md"
        note.write_text("", encoding="utf-8")
        with mock.patch.object(runner, "_SEALED", False):
            runner._handle_note(note, "", self.processed)
        self.assertIn("empty", (self.processed / "789.md.done").read_text(encoding="utf-8"))


class UpdateHandTextTellsTheTruthAboutSilence(unittest.TestCase):
    """(b) Описания рук не обещают откат, которого молчание больше не делает."""

    def test_desk_text_does_not_promise_a_silent_rollback(self):
        text = updates.TOOL_DESK["description"]
        self.assertNotIn("если промолчишь", text, "старая ложь ещё в тексте руки")
        self.assertIn("ОСТАЕТСЯ работать", text)
        self.assertIn("не принудительный", text)

    def test_server_text_does_not_promise_a_silent_rollback(self):
        text = updates.TOOL["description"]
        self.assertNotIn("если промолчишь", text, "старая ложь ещё в тексте руки")
        self.assertIn("ОСТАЕТСЯ работать", text)
        self.assertIn("не принудительный", text)


class ReceiptFailureIsNeverSilent(unittest.TestCase):
    """(c) Провал записи расписки — вторая попытка рядом и копия исполнителю.

    `Shared` пишет через dir_fd/O_NOFOLLOW (Linux), поэтому обмен с деревом
    подменён моком: проверяется ПОРЯДОК попыток и следы, а не сисколлы —
    тексты рук и механика save() живут на обеих платформах.
    """

    def setUp(self):
        sys.path.insert(0, str(DESK / "server" / "updater"))
        import updater as up  # noqa: PLC0415
        self.up = up
        self._tmp = tempfile.TemporaryDirectory(prefix="helene-receipt-")
        self.addCleanup(self._tmp.cleanup)
        self.install = Path(self._tmp.name) / "opt" / "helene"
        (self.install / "data").mkdir(parents=True)
        self.u = up.Updater(up.Config(self.install), clock=lambda: 1000.0,
                            sleep=lambda _s: None)
        self.u.state = {"id": "t1", "state": "checking"}

    def test_failed_receipt_gets_a_second_life_next_door(self):
        # дерево не пишется совсем: и расписка, и резервный файл рядом — мимо
        broken = mock.Mock(wraps=self.u.shared)
        broken.write.side_effect = OSError("disk full (стенд)")
        self.u.shared = broken
        self.u.save()
        # вторая попытка была — резервным именем в ту же папку дерева…
        near = [c.args[-2] for c in broken.write.call_args_list
                if isinstance(c.args[-2], str) and ".failed-" in c.args[-2]]
        self.assertEqual(len(near), 1, "повторной записи рядом не было")
        self.assertTrue(near[0].startswith("update-plan.receipt"), near[0])
        # …и копия легла в дом исполнителя: молчания нет, след есть
        failed = list((self.install / ".updater").glob("receipt.failed-*.json"))
        self.assertEqual(len(failed), 1, "резервная копия расписки не легла к исполнителю")
        row = json.loads(failed[0].read_text(encoding="utf-8"))
        self.assertIn("disk full", row["error"])
        self.assertEqual(row["receipt"].get("id"), "t1")

    def test_retry_next_door_success_needs_no_home_copy(self):
        # провал мог быть привязан к имени: повтор рядом удался — домой не пишем
        calls = []

        class HalfBroken:
            def write(self, *args):
                calls.append(args)
                if isinstance(args[-2], str) and ".failed-" not in args[-2]:
                    raise OSError("имя занято (стенд)")

        self.u.shared = HalfBroken()
        self.u.save()
        self.assertEqual(len(calls), 2, "ровно две попытки: расписка и повтор рядом")
        self.assertEqual(list((self.install / ".updater").glob("receipt.failed-*.json")), [],
                         "повтор рядом удался — домой писать не нужно")

    def test_receipt_failure_does_not_spoil_the_state(self):
        # провал расписки — не провал состояния: state.json пишется своим путём
        # (save_error — про него), а след лежит в .failed-копии
        broken = mock.Mock(wraps=self.u.shared)
        broken.write.side_effect = OSError("read-only (стенд)")
        self.u.shared = broken
        self.u.save()
        self.assertEqual(self.u.save_error, "", "состояние записалось своим путём")
        failed = list((self.install / ".updater").glob("receipt.failed-*.json"))
        self.assertEqual(len(failed), 1)
        self.assertIn("read-only", failed[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
