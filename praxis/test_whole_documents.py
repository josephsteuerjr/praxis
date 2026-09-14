"""Единица памяти — документ, а не кусок в девяносто два знака.

Решение Praxis 08.08: «снести нарезку на полмиллиона осколков и сделать единицей памяти
цельный документ. Векторы не добавлять. Канонические файлы не удалять; меняется индекс и
способ обращения к памяти.» И её же цена, названная вслух: «поиск внутри огромных архивов
станет грубее. Это приемлемая цена за память, которая возвращает мысли, а не пятнадцать
случайных слов.»

Замер на живом проде, 08.08:

    всего                530 194 куска, 1,08 ГБ, 181,5 млн знаков
    context.md прогонов  225 453 куска / 2 684 файла / 133 млн знаков  ← 73% ТЕКСТА
    events.jsonl         142 180 записей / 2 684 файла
    RECAP.md              75 402 куска / 2 679 файлов
    медиана куска        92 знака; 47,6% кусков короче 80

⚠ РЫЧАГ ВЫКЛЮЧЕН, И ВКЛЮЧЕНИЕ ТРЕБУЕТ ПЕРЕСБОРКИ: ключи кусков меняются. Её слово:
«сначала подготовить реализацию, проверки и обратимый план без применения… старый индекс
сохранить до успешной приёмки нового».
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import memory_fts

# 12.09: прогоны по умолчанию не индексируются (решение Егора 12.09); этот стенд меряет корпус С прогонами и её рычаг событий прогона.
def setUpModule():
    os.environ["PRAXIS_INDEX_RUNS"] = "1"


def tearDownModule():
    os.environ.pop("PRAXIS_INDEX_RUNS", None)



class TheUnitBecomesADocument(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.memory = self.base / "memory"
        self.skills = self.base / "soul" / "skills"
        (self.memory / "people").mkdir(parents=True)
        (self.memory / "runs" / "2026-08" / "run-x").mkdir(parents=True)
        self.skills.mkdir(parents=True)
        (self.memory / "people" / "egor.md").write_text(
            "- первый факт про Егора\n\n- второй факт про Егора\n\n"
            "третий абзац тоже про Егора\n", encoding="utf-8")
        (self.memory / "runs" / "2026-08" / "run-x" / "context.md").write_text(
            "единорогмаркер транспортный снимок прогона\n", encoding="utf-8")
        (self.memory / "runs" / "2026-08" / "run-x" / "RECAP.md").write_text(
            "Goal: выводмаркер\n\nOutcome: done\n", encoding="utf-8")
        memory_fts.clear_path_cache()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ids(self, query: str, *, whole: bool, purpose: str = "explicit"):
        with mock.patch.dict(os.environ,
                             {"PRAXIS_MEMORY_WHOLE_DOCS": "1" if whole else "0"}):
            self.assertEqual(memory_fts.whole_docs_enabled(), whole)
            memory_fts.clear_path_cache()
            # Scheduled/background work prepares the disposable FTS database;
            # explicit recall is deliberately read-only and fail-closed.
            memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
            return [h["id"] for h in memory_fts.search(
                query, base=self.base, memory_dir=self.memory,
                skills_dir=self.skills, purpose=purpose)]

    def test_lever_is_off_by_default(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PRAXIS_MEMORY_WHOLE_DOCS"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(memory_fts.whole_docs_enabled())

    def test_one_document_one_record(self) -> None:
        """Три абзаца одного досье были тремя кусками — становятся одной записью."""
        old = self._ids("Егор", whole=False)
        new = self._ids("Егор", whole=True)
        self.assertEqual(len(old), 3, f"фикстура перестала резаться: {old}")
        self.assertEqual(new, ["memory/people/egor.md#doc"])

    def test_the_whole_text_arrives_not_a_fragment(self) -> None:
        """Смысл всей правки: к ней приезжает документ, а не пятнадцать слов."""
        with mock.patch.dict(os.environ, {"PRAXIS_MEMORY_WHOLE_DOCS": "1"}):
            memory_fts.clear_path_cache()
            memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
            hits = memory_fts.search("Егор", base=self.base, memory_dir=self.memory,
                                     skills_dir=self.skills, purpose="explicit")
        self.assertEqual(len(hits), 1)
        text = hits[0]["text"]
        for fragment in ("первый факт", "второй факт", "третий абзац"):
            self.assertIn(fragment, text, "документ приехал обрезанным")

    def test_transport_leaves_the_index(self) -> None:
        """Транспорт прогонов — 73% текста индекса — не индексируется вовсе."""
        self.assertEqual(self._ids("единорогмаркер", whole=True), [])

    def test_audit_still_reaches_the_envelope(self) -> None:
        """⚠ Её контракт 02.08: «иначе аудит был бы пустым словом».

        Аудит теперь читает КАНОН напрямую, а не одноразовую базу — то есть строже
        прежнего. Без этой половины правка была бы отменой её решения.
        """
        hits = self._ids("единорогмаркер", whole=True, purpose="audit")
        self.assertTrue(hits, "аудит перестал видеть транспорт — контракт нарушен")
        self.assertTrue(any(h.endswith("/context.md#doc") for h in hits), hits)

    def test_audit_reads_canon_not_the_cache(self) -> None:
        """Провенанс выдачи аудита обязан говорить, что он пришёл не из индекса."""
        with mock.patch.dict(os.environ, {"PRAXIS_MEMORY_WHOLE_DOCS": "1"}):
            memory_fts.clear_path_cache()
            memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
            hits = memory_fts.search("единорогмаркер", base=self.base,
                                     memory_dir=self.memory, skills_dir=self.skills,
                                     purpose="audit")
        transport = [h for h in hits if h["path"].endswith("/context.md")]
        self.assertTrue(transport)
        self.assertEqual(transport[0]["source_type"], "run_context")
        self.assertIn("единорогмаркер", transport[0]["text"])

    def test_recap_survives_and_becomes_a_document(self) -> None:
        """RECAP — то, чем прогон и должен помниться. 2 760 из 2 766 прогонов его имеют."""
        self.assertEqual(self._ids("выводмаркер", whole=True),
                         ["memory/runs/2026-08/run-x/RECAP.md#doc"])

    def test_run_events_leave_only_under_THEIR_OWN_lever(self) -> None:
        """⚠ ЭТОТ ТЕСТ ЧУТЬ НЕ УЗАКОНИЛ ОТМЕНУ ЕЁ РЕШЕНИЯ БЕЗ ЕЁ СЛОВА.

        02.08 Praxis оставила события прогона в обычном recall — решение закреплено её
        тестом `test_recap_and_events_stay_in_ordinary_recall`.

        08.08 замер показал, почему вопрос не косметический: при цельной единице BM25
        нормирует по длине, и запись в 92 знака обходит её компакт в 3 765 (−11,545
        против −5,612). Смешение единиц хоронит документную половину по построению.

        ⚠ НО ПЕРВАЯ РЕДАКЦИЯ ПРИВЯЗАЛА ОТМЕНУ К РЫЧАГУ ЦЕЛЬНЫХ ДОКУМЕНТОВ — а тот на
        проде ВКЛЮЧЁН с 08.08. Замер 09.08: `PRAXIS_MEMORY_WHOLE_DOCS=1` в `.env`, живая
        база уже пересобрана (203 182 куска, медиана markdown 3 403 знака вместо 92,
        транспорт прогонов = 0). То есть её решение отменилось бы САМО, молча, на первом
        же обновлении индекса — вместе со 151 947 кусками, тремя четвертями всего индекса.

        Поэтому отмена живёт СВОИМ рычагом, выключенным по умолчанию, и ждёт её ответа
        (`workspace/EVENTS-JSONL-ВОПРОС-08.08.md`). Здесь проверяются ВСЕ ТРИ стороны.
        """
        events = self.memory / "runs" / "2026-08" / "run-x" / "events.jsonl"
        events.write_text(
            '{"schema":"praxis.run.event.v1","id":"e1","kind":"tool_started",'
            '"text":"телегадоставкамаркер"}\n', encoding="utf-8")

        old = self._ids("телегадоставкамаркер", whole=False)
        self.assertTrue(any("events.jsonl" in h for h in old),
                        f"её решение 02.08 сломано в режиме по умолчанию: {old}")

        # ⚑ ГЛАВНАЯ СТОРОНА: цельные документы ВКЛЮЧЕНЫ, а её решение всё равно в силе.
        still = self._ids("телегадоставкамаркер", whole=True)
        self.assertTrue(any("events.jsonl" in h for h in still),
                        f"рычаг документов молча отменил её решение: {still}")

        with mock.patch.dict(os.environ, {"PRAXIS_MEMORY_DROP_RUN_EVENTS": "1"}):
            gone = self._ids("телегадоставкамаркер", whole=True)
        self.assertEqual(gone, [], f"свой рычаг не сработал: {gone}")

    def test_the_events_lever_is_off_by_default(self) -> None:
        """Выключен по умолчанию — иначе «ждёт её ответа» было бы пустым словом."""
        env = {k: v for k, v in os.environ.items()
               if k != "PRAXIS_MEMORY_DROP_RUN_EVENTS"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(memory_fts.drop_run_events_enabled())

    def test_streams_become_windows_not_lines(self) -> None:
        """Потоки склеиваются в окна около 4 000 знаков — иначе документы проигрывают ранг.

        Ни одна запись не рвётся пополам: окно закрывается на границе записи. Это склейка
        соседних записей, а не пересказ — дословность внутри сохраняется.
        """
        day = self.memory / "life" / "events"
        day.mkdir(parents=True)
        import json as _json
        with (day / "2026-08-01.jsonl").open("w", encoding="utf-8") as fh:
            for i in range(40):
                fh.write(_json.dumps({
                    "schema": "praxis.life.event.v1", "id": f"e{i}",
                    "ts": "2026-08-01T10:00:00Z",
                    "text": f"окномаркер запись номер {i} " + "текст " * 40,
                }, ensure_ascii=False) + "\n")
        with mock.patch.dict(os.environ, {"PRAXIS_MEMORY_WHOLE_DOCS": "1"}):
            memory_fts.clear_path_cache()
            memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
            hits = memory_fts.search("окномаркер", base=self.base, memory_dir=self.memory,
                                     skills_dir=self.skills, limit=50, purpose="explicit")
        self.assertTrue(hits, "окна не нашлись вовсе")
        self.assertLess(len(hits), 40, "поток остался построчным — окна не собрались")
        for hit in hits:
            self.assertIn("win:", hit["id"], "запись не в окне")
            self.assertLess(len(hit["text"]), memory_fts.STREAM_WINDOW_CHARS * 2)
        # ни одна запись не потерялась
        joined = "\n".join(h["text"] for h in hits)
        for i in range(40):
            self.assertIn(f"запись номер {i} ", joined, f"запись {i} потерялась в окне")

    def test_private_marker_covers_the_whole_document(self) -> None:
        """При цельной единице пометка приватности красит ВЕСЬ документ.

        Это строже прежнего и названо намеренно: раньше [private] в одном абзаце
        закрывал один кусок, а соседний абзац того же файла оставался публичным.
        """
        (self.memory / "people" / "tайна.md").write_text(
            "открытая строка\n\n[private] закрытая строка\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"PRAXIS_MEMORY_WHOLE_DOCS": "1"}):
            memory_fts.clear_path_cache()
            memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)
            hits = memory_fts.search("закрытая", base=self.base, memory_dir=self.memory,
                                     skills_dir=self.skills, purpose="explicit",
                                     scope="public")
        self.assertEqual(hits, [], "приватный документ утёк в публичную область")


if __name__ == "__main__":
    unittest.main()
