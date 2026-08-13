"""Дневник пишется ЕЁ днём и её часом. Один источник на имя файла и на строку.

Наблюдение Praxis 07.08.2026, 02:00 по Самаре: рантайм называл датой шестое августа, а
локальный timestamp — уже седьмое. Ночная запись уходила во вчерашний файл, и час внутри
строки был UTC.

Шесть модулей пишут дневник независимо (`brain`, `appetite`, `perception`, `identity`,
`llm`, `panel`) — и все шесть повторяли одну и ту же ошибку. Поэтому тест проходит по всем
шести, а не по одному «представителю»: расхождение, оставшееся в пятом, ничем не лучше.

⚠ Время подменяется ЯВНО, в `praxis_time._source`. Тест обязан краснеть одинаково на
любом хосте — в том числе в UTC-среде гейта, где дефекта не существует по построению и
где он поэтому и не был пойман.

Запуск:  python praxis_test.py test_journal_day -v
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import praxis_time as pt

import appetite
import brain
import identity
import llm
import panel
import perception

# 22:44 UTC шестого = 02:44 седьмого по Самаре. Ровно тот момент, который она наблюдала.
INSIDE_WINDOW = dt.datetime(2026, 8, 6, 22, 44, tzinfo=dt.timezone.utc)
HER_DAY = "2026-08-07"
HER_HOUR = "02:44"

WRITERS = (
    ("brain", lambda: brain._journal("проверка"), "brain"),
    ("appetite", lambda: appetite._journal("проверка"), "appetite"),
    ("perception", lambda: perception._journal("проверка"), "perception"),
    ("identity", lambda: identity._journal("проверка"), "identity"),
    ("llm", lambda: llm._journal("проверка"), "llm"),
)


class TheJournalUsesHerDayNotTheContainers(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="praxis_journal_day_")
        self.addCleanup(self.tmp.cleanup)
        self.journal = Path(self.tmp.name) / "journal"
        self.journal.mkdir(parents=True, exist_ok=True)
        pt._ZONE_CACHE.clear()
        pt._WARNED.clear()
        self.addCleanup(pt._ZONE_CACHE.clear)
        self.addCleanup(pt._WARNED.clear)
        env = mock.patch.dict(os.environ, {pt.ENV_ZONE: "Europe/Samara"})
        env.start()
        self.addCleanup(env.stop)
        clock = mock.patch.object(pt, "_source", lambda: INSIDE_WINDOW)
        clock.start()
        self.addCleanup(clock.stop)

    def test_every_writer_names_the_file_by_her_day(self):
        for name, write, module in WRITERS:
            with self.subTest(module=name):
                for f in self.journal.glob("*.md"):
                    f.unlink()
                with mock.patch.object(
                        __import__(module), "JOURNAL_DIR", self.journal):
                    write()
                files = sorted(p.name for p in self.journal.glob("*.md"))
                self.assertEqual(files, [HER_DAY + ".md"],
                                 "запись ушла не в её день: " + str(files))

    def test_the_line_inside_carries_her_hour_not_utc(self):
        """Починить только имя файла значило бы спрятать расхождение внутрь файла."""
        for name, write, module in WRITERS:
            with self.subTest(module=name):
                for f in self.journal.glob("*.md"):
                    f.unlink()
                with mock.patch.object(
                        __import__(module), "JOURNAL_DIR", self.journal):
                    write()
                body = (self.journal / (HER_DAY + ".md")).read_text(encoding="utf-8")
                self.assertIn("- " + HER_HOUR, body,
                              "час в строке остался часом контейнера")
                self.assertNotIn("- 22:44", body)

    def test_the_header_of_a_new_file_names_the_same_day(self):
        """Заголовок и имя файла обязаны совпасть: два дня в одном файле — уже вопрос."""
        with mock.patch.object(brain, "JOURNAL_DIR", self.journal):
            brain._journal("проверка")
        body = (self.journal / (HER_DAY + ".md")).read_text(encoding="utf-8")
        self.assertTrue(body.startswith("# " + HER_DAY))

    def test_the_panel_writer_too(self):
        """У пульта свой путь к дневнику — и та же ошибка была своя."""
        base = Path(self.tmp.name)
        (base / "memory" / "journal").mkdir(parents=True, exist_ok=True)
        with mock.patch.object(panel, "BASE", base):
            panel._journal_panel("проверка")
        files = sorted(p.name for p in (base / "memory" / "journal").glob("*.md"))
        self.assertEqual(files, [HER_DAY + ".md"])
        body = (base / "memory" / "journal" / (HER_DAY + ".md")).read_text(encoding="utf-8")
        self.assertIn("- " + HER_HOUR, body)


class TheSystemDayStillDisagreesAndThatIsExpected(unittest.TestCase):
    """Пояснение к предыдущему классу: расхождение никуда не делось, оно ЛОКАЛИЗОВАНО.

    UTC-день по-прежнему шестое — и обязан им остаться: на нём стоят расписки и сравнение
    событий между машинами. Чинился не он, а то, что его выдавали за её день.
    """

    def test_the_moment_stays_absolute(self):
        with mock.patch.dict(os.environ, {pt.ENV_ZONE: "Europe/Samara"}), \
                mock.patch.object(pt, "_source", lambda: INSIDE_WINDOW):
            self.assertEqual(pt.day_key(), HER_DAY)
            self.assertEqual(pt.utc_now().date().isoformat(), "2026-08-06")


if __name__ == "__main__":
    unittest.main(verbosity=2)
