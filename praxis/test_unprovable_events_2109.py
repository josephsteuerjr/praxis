"""Недоказуемое событие не попадает в свёртку, а новое пишется уже каноничным.

Conversation text сохраняет исходные пробелы и принимается индексом без потери
разделителей native chunks. Неидентифицируемые или malformed события остаются
видимыми ghosts, но не идут в свёртку; остальные поля проверяются как прежде.

Запуск:  python praxis_test.py test_unprovable_events_2109 -v
"""

from __future__ import annotations

import unittest

import memory_life as ml
import memory_provenance as mp

NBSP = " "


def event_row(text: str, *, event_id: str = "evt-20260921T070000000000Z-1189e90c",
              ts: str = "2026-09-21T07:00:00.000Z") -> dict:
    return {
        "schema": "praxis.life.event.v1", "id": event_id, "ts": ts,
        "kind": "conversation_message", "stream": "-100370", "chat_id": "-100370",
        "actor": "Dmitry", "direction": "in", "text": text, "source": "telegram",
        "source_id": "34828", "salience": 2, "refs": [], "meta": {},
        "dedupe_key": "telegram:-100370:34828:in",
    }


class TheIndexIsAskedBeforeTheModel(unittest.TestCase):
    def test_conversation_whitespace_is_exact_content_not_an_identifier(self):
        raw = " привет" + NBSP + " \n"
        row = event_row(raw)
        self.assertTrue(mp.event_row_indexable(row))
        self.assertEqual(row['text'], raw)
        self.assertTrue(mp.event_row_indexable(event_row("привет")))

    def test_other_fields_and_event_kinds_remain_canonical(self):
        for field, value in (('actor', ' Dmitry'), ('chat_id', ' -100370'),
                             ('id', 'bad'), ('meta', []), ('text', ' \n\u00a0')):
            with self.subTest(field=field):
                self.assertFalse(mp.event_row_indexable(dict(event_row('valid'), **{field: value})))
        self.assertFalse(mp.event_row_indexable(dict(event_row(' valid '), kind='run_episode')))

    def test_an_empty_or_broken_row_is_not_indexable(self):
        self.assertFalse(mp.event_row_indexable(event_row("")))
        self.assertFalse(mp.event_row_indexable({}))
        self.assertFalse(mp.event_row_indexable(None))

    def test_the_answer_matches_the_index_rule_itself(self):
        """Одно правило на двоих: сборка индекса и вопрос перед свёрткой."""
        import pathlib
        row = event_row("привет" + NBSP)
        self.assertEqual(mp.event_row_indexable(row),
                         mp._valid_event(row, pathlib.Path("2026-09-21.jsonl")))


class TheHotRingKeepsOnlyProvableRows(unittest.TestCase):
    def test_unprovable_rows_are_separated_not_folded(self):
        good = event_row("нормальная строка")
        ghost = event_row("хвост с пробелом ",
                          event_id="evt-20260921T070001000000Z-6c3d18d5",
                          ts="2026-09-21T07:00:01.000Z")
        ghost['actor'] = ' invalid actor '
        foldable, unprovable = ml._foldable_hot_rows(
            [good, ghost], {good["id"], ghost["id"]}, set())
        self.assertEqual([r["id"] for r in foldable], [good["id"]])
        self.assertEqual([r["id"] for r in unprovable], [ghost["id"]])

    def test_covered_and_stale_rows_are_still_dropped(self):
        good = event_row("нормальная строка")
        covered = event_row("уже свёрнута",
                            event_id="evt-20260921T070002000000Z-aaaa1111",
                            ts="2026-09-21T07:00:02.000Z")
        stale = event_row("старая ревизия",
                          event_id="evt-20260921T070003000000Z-bbbb2222",
                          ts="2026-09-21T07:00:03.000Z")
        foldable, unprovable = ml._foldable_hot_rows(
            [good, covered, stale], {good["id"], covered["id"]}, {covered["id"]})
        self.assertEqual([r["id"] for r in foldable], [good["id"]])
        self.assertEqual(unprovable, [])


class TheWriterMakesCanonicalRows(unittest.TestCase):
    def setUp(self) -> None:
        self._append = ml._append_record
        self.written: list[dict] = []
        ml._append_record = self.written.append

    def tearDown(self) -> None:
        ml._append_record = self._append

    def test_text_is_written_stripped(self):
        ml.append_event("conversation_message", chat_id="-100370", direction="in",
                        text="  Dmitry: держи ссылку" + NBSP + " \n",
                        source="telegram", source_id="34828")
        self.assertEqual(self.written[0]["text"], "Dmitry: держи ссылку")

    def test_the_written_row_is_indexable(self):
        ml.append_event("conversation_message", chat_id="-100370", direction="in",
                        text="строка" + NBSP, source="telegram", source_id="34829")
        self.assertTrue(mp.event_row_indexable(self.written[0]))

    def test_an_empty_text_stays_empty(self):
        """Пустой текст — отдельная болезнь; здесь мы её не лечим и не прячем."""
        ml.append_event("conversation_message", chat_id="-100370", direction="in",
                        text="   ", source="telegram", source_id="34830")
        self.assertEqual(self.written[0]["text"], "")

    def test_outgoing_text_is_kept_verbatim(self):
        """У исходящего пробел по краям несёт смысл: разбитый ответ склеивается по нему.

        Первая попытка канонизировать ВСЁ уронила test_keat_native_ingress восемью
        красными: «first chunk » + «second chunk» склеились без шва.
        """
        ml.append_event("conversation_message", chat_id="-100370", direction="out",
                        text="first chunk ", source="telegram", source_id="34831")
        self.assertEqual(self.written[0]["text"], "first chunk ")

    def test_internal_text_is_kept_verbatim_too(self):
        ml.append_event("run_episode", chat_id=None, direction="internal",
                        text=" отчёт ", source="runtime", source_id="run-1")
        self.assertEqual(self.written[0]["text"], " отчёт ")


if __name__ == "__main__":
    unittest.main()
