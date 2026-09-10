"""То, что она записала СЕБЕ, можно найти. И прибор расхода показывает то, что расходуется.

02.08.2026. Её четвёртый шаг доказательства обучения — «извлечение в точке выбора» — стоял,
и мы думали, что дело в аллоулисте автоматического recall. Оказалось хуже: пути
`memory/notes/events.jsonl` не было в списке индексируемых ВООБЩЕ. Её собственные уроки
(`praxis.authored_note.event.v1`) не находил ни автоматический recall, ни явное «вспомни».
Урок написан — и невидим.

Граница здесь важнее самой правки: заметки становятся находимыми ЯВНО, но в автоматический
канон НЕ входят. У jsonl-видов `automatic_eligible` по умолчанию True, поэтому без явного
исключения новый вид молча попал бы к ней в кадр на каждом ходе — а что стоит в кадре,
решают Егор и она.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import llm
import memory_fts

NOTE = {
    "schema": "praxis.authored_note.event.v1",
    "event_type": "written", "kind": "note",
    "note_id": "note-20260728T210706-8bf9c75b2e",
    "run_id": "run-20260728T210650Z-aaaaaaaa",
    "scope": "chat", "ts": "2026-07-28T21:07:06Z",
    "text": "Урокмаркер: подготовленное вспоминается как доставленное — перед словом "
            "«сделано» искать квитанцию, а не память.",
}


class HerNotesAreFindable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="praxis_notes_")
        self.base = Path(self.tmp.name)
        self.mem = self.base / "memory"
        self.skills = self.base / "soul" / "skills"
        self.skills.mkdir(parents=True)
        (self.mem / "notes").mkdir(parents=True)
        (self.mem / "notes" / "events.jsonl").write_text(
            json.dumps(NOTE, ensure_ascii=False) + "\n", encoding="utf-8")
        memory_fts.rebuild(base=self.base, memory_dir=self.mem, skills_dir=self.skills)
        self.addCleanup(self.tmp.cleanup)

    def _rows(self, query, purpose="explicit"):
        return memory_fts.search(query, base=self.base, memory_dir=self.mem,
                                 skills_dir=self.skills, scope="owner",
                                 purpose=purpose, limit=20)

    def test_the_path_is_indexed_at_all(self):
        rels = {s.rel for s in memory_fts.iter_sources(base=self.base, memory_dir=self.mem)}
        self.assertIn("memory/notes/events.jsonl", rels)

    def test_explicit_recall_finds_her_lesson(self):
        rows = self._rows("Урокмаркер")
        self.assertTrue(rows, "её собственный урок должен находиться явным recall")
        self.assertEqual(rows[0]["source_type"], "authored_note")

    def test_it_does_not_enter_the_automatic_canon(self):
        """Граница 👤: что стоит в кадре на каждом ходе — не наше решение."""
        self.assertEqual(self._rows("Урокмаркер", purpose="automatic"), [])

    def test_kind_is_typed_not_lumped_into_self_event(self):
        kind, visibility = memory_fts._selected_jsonl(
            self.mem / "notes" / "events.jsonl", self.mem)
        self.assertEqual((kind, visibility), ("authored_note", "owner"))


class SpendMeterShowsWhatIsSpent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="praxis_meter_")
        self.orig = llm.USAGE_PATH
        llm.USAGE_PATH = Path(self.tmp.name) / "usage.json"
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: setattr(llm, "USAGE_PATH", self.orig))

    def test_line_names_the_cache_share_and_the_fresh_input(self):
        """Те же 310 139 токенов промпта и 264 192 из кэша — но `in` уже
        нормализован (схема 2), поэтому доля берётся от полного знаменателя.
        Число прежнее: 264192 / (264192 + 45947) = 85%."""
        llm._usage_add("voice", {"in": 45947, "out": 2000, "cache_read": 264192},
                       model="gpt-5.6-sol")
        line = llm.usage_line()
        self.assertIn("кэш 85%", line)
        self.assertIn("свежего входа 45.9к", line)

    def test_silence_when_the_provider_says_nothing(self):
        """Отсутствие поля — не ноль-как-факт: строка про кэш просто не появляется."""
        llm._usage_add("voice", {"in": 1000, "out": 10}, model="gpt-5.6-sol")
        self.assertNotIn("кэш", llm.usage_line())

    def test_no_division_by_zero_on_an_empty_day(self):
        llm._usage_add("voice", {"in": 0, "out": 0, "cache_read": 0}, model="x")
        llm.usage_line()


class OneBucketMustNotHoldTwoMeanings(unittest.TestCase):
    """27.08.2026. `in` значил разное у разных фреймворков, и это лежало в ОДНОМ ведре.

    anthropic отдаёт вход уже без кэша; openai — весь промпт вместе с кэшем. Пока обе
    величины звались `in`, дневная сумма переставала быть величиной, а отношение поверх
    неё — измерением. Замер 26.08 на живом: у glm `cache_read` 13 205 312 при `in`
    849 137 — кэш в пятнадцать раз больше «входа», чего при одной семантике не бывает.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="praxis_meaning_")
        self.orig = llm.USAGE_PATH
        llm.USAGE_PATH = Path(self.tmp.name) / "usage.json"
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: setattr(llm, "USAGE_PATH", self.orig))

    # ---- нормализация на шве openai

    def test_openai_input_stops_counting_the_cached_prefix_twice(self):
        usage = {"prompt_tokens": 56425, "completion_tokens": 262,
                 "prompt_tokens_details": {"cached_tokens": 17792}}
        self.assertEqual(llm._openai_fresh_in(usage, 17792), 56425 - 17792)

    def test_a_provider_without_the_detail_reports_everything_as_fresh(self):
        """Молчание про кэш — не ноль-как-факт, но и не повод выдумать попадание."""
        self.assertEqual(llm._openai_fresh_in({"prompt_tokens": 1000}, 0), 1000)

    def test_spend_never_goes_negative_if_a_provider_counts_otherwise(self):
        self.assertEqual(llm._openai_fresh_in({"prompt_tokens": 10}, 99), 0)

    # ---- обе семантики сходятся к одной

    def test_both_frameworks_describe_the_same_turn_with_the_same_numbers(self):
        """Один и тот же ход: 100 000 промпта, 90 000 из кэша. Anthropic такой ход
        описывает как in=10 000 + cache_read=90 000; openai — как prompt=100 000 с
        cached=90 000. После нормализации записи обязаны совпасть."""
        oai = llm._openai_fresh_in({"prompt_tokens": 100_000}, 90_000)
        anth_in = 10_000
        self.assertEqual(oai, anth_in)

    def test_the_share_is_taken_from_the_whole_prompt_not_from_the_fresh_part(self):
        llm._usage_add("voice", {"in": 10_000, "out": 100, "cache_read": 90_000},
                       model="glm-5.3")
        self.assertIn("кэш 90%", llm.usage_line())

    def test_the_fresh_input_is_no_longer_understated(self):
        """Суть поломки в деньгах: старая строка звала свежим `in − cache_read`, и на
        anthropic-записи это давало ноль — при девяноста тысячах реально свежих."""
        llm._usage_add("voice", {"in": 90_000, "out": 100, "cache_read": 10_000},
                       model="glm-5.3")
        line = llm.usage_line()
        self.assertIn("свежего входа 90.0к", line)
        self.assertIn("кэш 10%", line)

    # ---- честность про прошлое

    def test_a_record_started_before_the_fix_shows_tokens_but_not_a_rate(self):
        """Сутки, начатые старым кодом, внутри себя уже смешаны. Пересчитать их нечем,
        поэтому доля не показывается вовсе — «не знаю» честнее выдуманного числа."""
        llm._usage_add("voice", {"in": 1, "out": 1}, model="gpt-5.6-sol")
        data = json.loads(llm.USAGE_PATH.read_text(encoding="utf-8"))
        day = next(iter(data))
        del data[day]["voice"]["schema"]          # запись из прошлой эпохи учёта
        data[day]["voice"]["cache_read"] = 264192
        data[day]["voice"]["in"] = 310139
        llm.USAGE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        line = llm.usage_line()
        self.assertNotIn("кэш", line)
        self.assertIn("310.1к", line, "токены обязаны остаться видимыми")

    def test_a_fresh_record_is_stamped_so_the_meaning_is_knowable(self):
        llm._usage_add("voice", {"in": 5, "out": 5, "cache_read": 5}, model="glm-5.3")
        data = json.loads(llm.USAGE_PATH.read_text(encoding="utf-8"))
        rec = data[next(iter(data))]["voice"]
        self.assertEqual(rec["schema"], llm.USAGE_SCHEMA)
        self.assertEqual(rec["models"]["glm-5.3"]["schema"], llm.USAGE_SCHEMA)

    def test_the_stamp_hides_inside_the_role_record_not_beside_it(self):
        """Скаляр рядом с ролями уронил бы appetite и brain: они идут по `.values()`
        дня и зовут `.get`. Там общий except — падение стало бы тихим нулём."""
        llm._usage_add("voice", {"in": 5, "out": 5}, model="glm-5.3")
        day = json.loads(llm.USAGE_PATH.read_text(encoding="utf-8"))[
            next(iter(json.loads(llm.USAGE_PATH.read_text(encoding="utf-8"))))]
        for key, value in day.items():
            self.assertIsInstance(value, dict, f"скаляр {key!r} рядом с ролями дня")


if __name__ == "__main__":
    unittest.main()
