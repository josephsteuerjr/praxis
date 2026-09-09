# -*- coding: utf-8 -*-
"""Стенд границы хода в окне (ревью 06.09, п. 1.13).

Запуск:  python tests/t_window_word.py

Правило, которое здесь проверяется: после хода в окне владелец видит ЛИБО слово
агента, ЛИБО явную плашку («молчание по решению», «ход закрыт без реплики»).
Пустого окна после хода не бывает. Наблюдение 06.09: два хода из четырёх ушли
`telegram-delivery: silent` — весь отчёт лежал в заметке `end_turn(note=…)`, а
условие доставки считало отправленный файл словом.

Дерево здесь подменено заглушкой с теми же именами, что зовёт `_turn_in_window`
(`ChannelContext`, `voice_turn_envelope`, `run_delivery_*`); запись хода она
кладёт в `turns.jsonl` ДО возврата конверта — ровно как дерево. Файлы — во
временную папку.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import runner  # noqa: E402
import transport  # noqa: E402


class _Envelope:
    def __init__(self, run_id: str, text: str = "", outbound=(), deferred=False,
                 failed=False):
        self.run_id, self.text = run_id, text
        self.outbound, self.deferred, self.failed = list(outbound), deferred, failed


class _Media:
    def __init__(self, path: str):
        self.path, self.caption, self.kind = path, "снимок", "photo"
        self.target_chat_id, self.voice_note = "", False


class _Ctx:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _StubAgent:
    """Дерево: пишет запись хода и возвращает конверт; расписки доставки считает."""
    ChannelContext = _Ctx

    def __init__(self, tree: Path, row: dict, envelope: _Envelope):
        self.tree, self.row, self.envelope = tree, row, envelope
        self.receipts: list[tuple] = []

    def voice_turn_envelope(self, chat_id, convo, speaker, *, ctx, history,
                            current_text, orient):
        path = self.tree / "memory" / ".state" / "turns.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as sink:
            sink.write(json.dumps({"chat_id": chat_id, "run_id": self.envelope.run_id,
                                   **self.row}, ensure_ascii=False) + "\n")
        return self.envelope

    def run_delivery_started(self, run_id, **kw):
        self.receipts.append(("started", run_id))

    def run_delivery_text_accepted(self, run_id, *, text):
        self.receipts.append(("text", text))

    def run_delivery_finalize_recovered(self, run_id):
        self.receipts.append(("finalize", run_id))

    def run_delivery_completed(self, run_id, *, silent, silent_reason=""):
        self.receipts.append(("completed", silent, silent_reason))


class Boundary(unittest.TestCase):
    """`boundary_word` — чистое правило над записью хода."""

    def test_note_after_outcome_is_her_word(self):
        row = {"held": "unspoken",
               "note": "done: done: Проверила руку computer по шагам: всё удалось."}
        self.assertEqual(runner.boundary_word(row),
                         (runner.WORD, "Проверила руку computer по шагам: всё удалось."))

    def test_single_word_answer_is_not_a_machine_outcome(self):
        for note in ("Спасибо.", "42", "Готово"):
            self.assertEqual(runner.boundary_word({"held": "unspoken", "note": note}),
                             (runner.WORD, note))

    def test_bare_outcome_is_not_a_word(self):
        for note in ("done", "done: представилась", "", "wait:"):
            self.assertEqual(runner.boundary_word({"held": "unspoken", "note": note}),
                             (runner.BLANK, ""), note)

    def test_wait_and_blocked_keep_their_meaning(self):
        self.assertEqual(
            runner.boundary_word({"held": "unspoken", "note": "wait: пока Егор не пришлёт ключ"}),
            (runner.WORD, "Жду: пока Егор не пришлёт ключ"))
        self.assertEqual(
            runner.boundary_word({"held": "unspoken", "note": "blocked: нужен доступ к папке"}),
            (runner.WORD, "Препятствие: нужен доступ к папке"))

    def test_declared_silence(self):
        self.assertEqual(runner.boundary_word({"held": "voice", "why": "нечего  добавить"}),
                         (runner.SILENCE, "нечего добавить"))

    def test_other_holds_are_blank(self):
        for held in ("", "error", "empty", None):
            self.assertEqual(runner.boundary_word({"held": held, "note": "done: много слов тут"}),
                             (runner.BLANK, ""), str(held))


class WindowTurn(unittest.TestCase):
    """Сам шов: что легло в архив комнаты после хода."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        self.desks = transport.Desks(self.tree, "Егор", "Hélène",
                                     memory_life=None, agent_name="Мира")
        self.desk = self.desks.default
        self.saved = {k: getattr(runner, k) for k in
                      ("_tree", "_desk", "_desks", "_agent", "_life", "_agent_name",
                       "_deliver_unspoken", "_bot")}
        runner._tree, runner._desk, runner._desks = self.tree, self.desk, self.desks
        runner._life, runner._bot = None, None
        runner._agent_name, runner._deliver_unspoken = "Мира", True

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(runner, k, v)
        self.tmp.cleanup()

    def _turn(self, row: dict, envelope: _Envelope) -> tuple[str, list[dict], _StubAgent]:
        agent = _StubAgent(self.tree, row, envelope)
        runner._agent = agent
        self.desk.archive("Мира, проверь руку", outgoing=False)
        outcome = runner._turn_in_window("window-1", speaker="Егор")
        return outcome, self.desk.rows(), agent

    def test_report_in_end_turn_note_reaches_the_window(self):
        outcome, rows, agent = self._turn(
            {"held": "unspoken", "note": "done: Всё проверила: окно нашла, текст набран."},
            _Envelope("run-1"))
        self.assertEqual(outcome, "spoken")
        last = rows[-1]
        self.assertEqual(last["text"], "Всё проверила: окно нашла, текст набран.")
        self.assertTrue(last["outgoing"])
        self.assertNotIn("system", last, "слово агента — не плашка")
        self.assertIn(("text", "Всё проверила: окно нашла, текст набран."), agent.receipts)

    def test_short_authored_answer_reaches_archive_and_delivery_receipt(self):
        outcome, rows, agent = self._turn({"held": "unspoken", "note": "Спасибо."}, _Envelope("run-short"))
        self.assertEqual(outcome, "spoken")
        self.assertEqual(rows[-1]["text"], "Спасибо.")
        self.assertNotIn("system", rows[-1])
        self.assertIn(("text", "Спасибо."), agent.receipts)

    def test_media_does_not_swallow_the_word(self):
        # 06.09: снимок ушёл, отчёт остался в заметке — окно показало один «[файл]».
        shot = self.tree / "shot.png"
        shot.write_bytes(b"png")
        outcome, rows, _ = self._turn(
            {"held": "unspoken", "note": "done: Снимок сделала, строка в Блокноте верная."},
            _Envelope("run-2", outbound=[_Media(str(shot))]))
        self.assertEqual(outcome, "spoken")
        texts = [r["text"] for r in rows if r.get("outgoing")]
        self.assertTrue(any(t.startswith("[файл] shot.png") for t in texts), texts)
        self.assertIn("Снимок сделала, строка в Блокноте верная.", texts)

    def test_declared_silence_shows_a_grey_plaque(self):
        outcome, rows, agent = self._turn(
            {"held": "voice", "why": "владелец просил не отвечать"}, _Envelope("run-3"))
        self.assertEqual(outcome, "silent")
        last = rows[-1]
        self.assertTrue(last.get("system"))
        self.assertEqual(last.get("kind"), "silence")
        self.assertIn("молчание по решению Мира", last["text"])
        self.assertIn("владелец просил не отвечать", last["text"])
        self.assertIn(("completed", True, "agent chose silence"), agent.receipts)
        # Плашка — не её слово: в ленту модели не идёт.
        self.assertNotIn("молчание", "\n".join(self.desk.lines()))

    def test_bare_end_turn_shows_a_plaque_not_done(self):
        outcome, rows, _ = self._turn({"held": "unspoken", "note": "done"}, _Envelope("run-4"))
        self.assertEqual(outcome, "silent")
        last = rows[-1]
        self.assertTrue(last.get("system"))
        self.assertIn("без реплики", last["text"])
        self.assertNotEqual(last["text"].strip(), "done")

    def test_switched_off_delivery_still_leaves_a_plaque(self):
        runner._deliver_unspoken = False
        outcome, rows, _ = self._turn(
            {"held": "unspoken", "note": "done: Отчёт, который владелец выключил."},
            _Envelope("run-5"))
        self.assertEqual(outcome, "silent")
        self.assertTrue(rows[-1].get("system"))
        self.assertNotIn("Отчёт", rows[-1]["text"])

    def test_reply_hand_wins(self):
        # Рука сказала — заметка хода не дублируется плашкой или вторым словом.
        row = {"held": "", "note": "done: Сказала рукой, это заметка."}
        agent = _StubAgent(self.tree, row, _Envelope("run-6"))

        def speak(chat_id, convo, speaker, *, ctx, history, current_text, orient):
            self.desk.deliver("Ответила рукой.", label="Егор")
            return _StubAgent.voice_turn_envelope(agent, chat_id, convo, speaker, ctx=ctx,
                                                  history=history, current_text=current_text,
                                                  orient=orient)
        agent.voice_turn_envelope = speak
        runner._agent = agent
        self.desk.archive("привет", outgoing=False)
        outcome = runner._turn_in_window("window-6", speaker="Егор")
        self.assertEqual(outcome, "spoken")
        rows = self.desk.rows()
        self.assertEqual(rows[-1]["text"], "Ответила рукой.")
        self.assertEqual(sum(1 for r in rows if r.get("outgoing")), 1)


class AppendLock(unittest.TestCase):
    """п. 1.7: два потока в один архив — ни одной склеенной строки."""

    def test_two_writers_one_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "memory" / "groups" / "window.jsonl"
            errors: list[BaseException] = []

            def writer(tag: str) -> None:
                try:
                    for i in range(400):
                        transport._append_jsonl(archive, {"who": tag, "i": i,
                                                          "text": "строка " * 40})
                except BaseException as exc:      # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(t,)) for t in ("a", "b", "c")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertFalse(errors)
            lines = archive.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1200)
            rows = [json.loads(line) for line in lines]
            for tag in ("a", "b", "c"):
                self.assertEqual([r["i"] for r in rows if r["who"] == tag], list(range(400)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
