"""Разбор свёрток места помнит себя между ходами — и не переживает своё основание.

Свёртка неизменяема: `_write_compact` кладёт файл один раз. Но `_parse_place_compacts`
перечитывал и перепроверял ВСЕ файлы места заново на каждый вызов, а зовут его оба графа,
то есть каждая сборка кадра. Замер 16.09 в боевом контейнере на `-1001240718803`
(2080 файлов, 13 МБ): 1,25 с на вызов, и стадия кадра `old_context.summary` платила это
каждый ход.

Здесь проверяется не скорость, а честность памяти разбора — то, из-за чего кэш имеет
право существовать:

  * второй вопрос о том же месте не идёт в разбор заново;
  * дописанная свёртка видна сразу;
  * ПЕРЕПИСАННЫЙ файл перечитывается — отпечаток жизни это ловит;
  * удалённый файл уходит и из ответа, и из памяти;
  * наружу едут копии: прежний разбор отдавал свежие объекты, и `rebuild_state` кладёт
    эти шапки в состояние места — кэш, отдающий свой словарь, раздавал бы чужую правку;
  * отказ помнится как отказ, но починенный файл принимается;
  * записи прежнего корня дерева не копятся, когда корень сменился.

Запуск:  python praxis_test.py test_compacts_parse_cache_1609 -v
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import memory_life as ml
import telegram_routes as tr

ROOM = "-1001240718803"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_cparse_"))
        self._orig = {name: getattr(ml, name) for name in
                      ("BASE", "MEM_DIR", "LIFE_DIR", "EVENTS_DIR", "COMPACTS_DIR",
                       "EPISODES_DIR", "CLAIMS_DIR", "PATCHES_DIR", "REFLECTIONS_DIR",
                       "STATE_DIR", "LEGACY_SUMMARIES_DIR", "DIALOGUES_DIR")}
        ml.BASE = self.tmp
        ml.MEM_DIR = self.tmp / "memory"
        ml.LIFE_DIR = ml.MEM_DIR / "life"
        ml.EVENTS_DIR = ml.LIFE_DIR / "events"
        ml.COMPACTS_DIR = ml.LIFE_DIR / "compacts"
        ml.EPISODES_DIR = ml.LIFE_DIR / "episodes"
        ml.CLAIMS_DIR = ml.LIFE_DIR / "claims"
        ml.PATCHES_DIR = ml.LIFE_DIR / "patches"
        ml.REFLECTIONS_DIR = ml.LIFE_DIR / "reflections"
        ml.STATE_DIR = ml.MEM_DIR / ".state" / "life"
        ml.LEGACY_SUMMARIES_DIR = ml.MEM_DIR / ".summaries"
        ml.DIALOGUES_DIR = ml.MEM_DIR / "dialogues"
        self._routes = tr.DIR
        tr.DIR = self.tmp / "memory" / ".state" / "group_context"
        ml._COMPACT_PARSE_CACHE.clear()

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(ml, name, value)
        tr.DIR = self._routes
        ml._COMPACT_PARSE_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ помощь
    def _msg(self, mid: int, text: str, *, chat: str = ROOM):
        return ml.record_message(chat, f"Николай: {text}", actor="Николай",
                                 direction="in", source_id=str(mid), ts=float(mid),
                                 dedupe_key=f"telegram:{chat}:{mid}:in")

    def _compact(self, rows, *, chat: str = ROOM, summary: str = "старая сводка"):
        return ml._write_compact(
            chat, {"summary": summary, "open_threads": [], "claims": [], "episodes": []},
            tier=1, depth=1, source_events=[r["id"] for r in rows], source_compacts=[],
            event_count=len(rows), continued=False,
            first_ts=min(str(r["ts"]) for r in rows),
            last_ts=max(str(r["ts"]) for r in rows))

    def _count_reads(self):
        """Обёртка-счётчик вокруг разбора одного файла. Возвращает (список, снять)."""
        calls: list[str] = []
        original = ml._read_compact_candidate

        def counting(path, chat_id):
            calls.append(Path(path).name)
            return original(path, chat_id)

        ml._read_compact_candidate = counting
        self.addCleanup(setattr, ml, "_read_compact_candidate", original)
        return calls


class TestTheParseRemembersItself(Base):
    def test_a_second_question_about_the_same_place_does_not_parse_again(self):
        self._compact([self._msg(1, "раз")], summary="первая")
        self._compact([self._msg(2, "два")], summary="вторая")
        calls = self._count_reads()

        first, _ = ml._parse_place_compacts(ROOM)
        self.assertEqual(len(first), 2, "обе свёртки места видны")
        self.assertEqual(len(calls), 2, "первый заход честно читает оба файла")

        second, _ = ml._parse_place_compacts(ROOM)
        self.assertEqual(len(calls), 2, "второй заход не пошёл в разбор заново")
        self.assertEqual({cid: item[0]["id"] for cid, item in second.items()},
                         {cid: item[0]["id"] for cid, item in first.items()})
        self.assertEqual({item[1] for item in second.values()},
                         {item[1] for item in first.values()},
                         "тело recap из памяти — то же самое")

    def test_a_compact_written_after_the_first_read_is_seen(self):
        self._compact([self._msg(1, "раз")], summary="первая")
        seen_before, _ = ml._parse_place_compacts(ROOM)
        self.assertEqual(len(seen_before), 1)

        self._compact([self._msg(2, "два")], summary="вторая")
        seen_after, _ = ml._parse_place_compacts(ROOM)
        self.assertEqual(len(seen_after), 2, "новая свёртка видна без просьбы")

    def test_a_rewritten_compact_is_read_again(self):
        meta = self._compact([self._msg(1, "раз")], summary="прежняя суть")
        path = ml.BASE / meta["path"]
        before, _ = ml._parse_place_compacts(ROOM)
        self.assertIn("прежняя суть", before[meta["id"]][1])

        # Переписываем ТЕЛО, шапку не трогаем: меняется длина и хвост файла.
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("прежняя суть",
                                     "переписанная суть, заметно длиннее прежней"),
                        encoding="utf-8")

        after, _ = ml._parse_place_compacts(ROOM)
        self.assertIn("переписанная суть", after[meta["id"]][1],
                      "правка файла обязана сбросить его разбор")
        self.assertNotIn("прежняя суть", after[meta["id"]][1])

    def test_a_removed_compact_leaves_both_the_answer_and_the_memory(self):
        meta = self._compact([self._msg(1, "раз")], summary="первая")
        self._compact([self._msg(2, "два")], summary="вторая")
        path = ml.BASE / meta["path"]
        ml._parse_place_compacts(ROOM)
        self.assertIn(path.as_posix(), ml._COMPACT_PARSE_CACHE)

        path.unlink()
        left, _ = ml._parse_place_compacts(ROOM)
        self.assertNotIn(meta["id"], left, "удалённой свёртки нет в ответе")
        self.assertNotIn(path.as_posix(), ml._COMPACT_PARSE_CACHE,
                         "кэш не переживает своё основание")

    def test_the_parse_hands_out_copies_not_its_own_memory(self):
        meta = self._compact([self._msg(1, "раз")], summary="первая")
        first, _ = ml._parse_place_compacts(ROOM)
        first[meta["id"]][0]["tier"] = 999
        first[meta["id"]][0]["source_event_ids"].append("evt-подделка")

        second, _ = ml._parse_place_compacts(ROOM)
        self.assertEqual(second[meta["id"]][0]["tier"], 1,
                         "правка снаружи не доехала до памяти разбора")
        self.assertNotIn("evt-подделка", second[meta["id"]][0]["source_event_ids"],
                         "списки шапки тоже копии, а не общая память")

    def test_a_refused_compact_stays_refused_and_is_accepted_once_repaired(self):
        meta = self._compact([self._msg(1, "раз")], summary="первая")
        path = ml.BASE / meta["path"]
        good = path.read_text(encoding="utf-8")
        path.write_text("# не свёртка, а просто текст\n", encoding="utf-8")

        calls = self._count_reads()
        refused, _ = ml._parse_place_compacts(ROOM)
        self.assertEqual(refused, {}, "битый файл в канон не попадает")
        self.assertEqual(len(calls), 1)

        ml._parse_place_compacts(ROOM)
        self.assertEqual(len(calls), 1,
                         "отказ помнится: битый файл не перепроверяется каждый ход")

        path.write_text(good, encoding="utf-8")
        repaired, _ = ml._parse_place_compacts(ROOM)
        self.assertIn(meta["id"], repaired, "починенный файл принимается")

    def test_the_memory_does_not_outlive_the_tree_root(self):
        self._compact([self._msg(1, "раз")], summary="первая")
        ml._parse_place_compacts(ROOM)
        self.assertTrue(ml._COMPACT_PARSE_CACHE)
        stale_key = next(iter(ml._COMPACT_PARSE_CACHE))

        # Стенды уводят BASE в одноразовый каталог; записи прежнего корня обязаны уйти.
        other = Path(tempfile.mkdtemp(prefix="praxis_cparse_other_"))
        self.addCleanup(shutil.rmtree, other, True)
        ml.BASE = other
        ml.MEM_DIR = other / "memory"
        ml.LIFE_DIR = ml.MEM_DIR / "life"
        ml.EVENTS_DIR = ml.LIFE_DIR / "events"
        ml.COMPACTS_DIR = ml.LIFE_DIR / "compacts"
        ml.STATE_DIR = ml.MEM_DIR / ".state" / "life"
        tr.DIR = other / "memory" / ".state" / "group_context"
        self._compact([self._msg(7, "в другом дереве")], summary="другая")

        ml._parse_place_compacts(ROOM)
        self.assertNotIn(stale_key, ml._COMPACT_PARSE_CACHE,
                         "записи прежнего корня не копятся до конца прогона")


if __name__ == "__main__":       # pragma: no cover
    unittest.main(verbosity=2)
