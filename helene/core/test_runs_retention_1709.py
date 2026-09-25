# -*- coding: utf-8 -*-
"""Ретенция прогонов в издании: она вправду снимает снимки и вправду молчит, когда не сняла.

ПОЧЕМУ ЭТОТ СТЕНД НОВЫЙ, А НЕ ПЕРЕНЕСЁННЫЙ. Ядерный `test_runs_prune.py` проверяет
правило (что удаляем и с какого возраста), но не проверяет двух вещей, из-за которых
в издании ретенция была бы обещанием без дела:

1. ⚠ НА WINDOWS ОНА НЕ ДЕЛАЛА НИЧЕГО. `run_retention.discover_open_evidence_runs`
   стоит на POSIX `openat` (`O_DIRECTORY|O_NOFOLLOW`, `dir_fd=`). Этих флагов в
   Windows нет; ядро честно отвечает «compatibility: …», `protected_runs` возвращает
   `None`, и `prune` выходит со строкой «open-evidence discovery failed; nothing
   pruned». Ни исключения, ни красного — диск просто растёт. Здесь проверяется, что
   издание в этом случае читает открытую работу СВОИМ путём и проход состоится.

2. ⚠ ОТЧЁТ ВРАЛ В СВОЮ ПОЛЬЗУ. Байты засчитывались ДО `rmtree`, а `OSError` уходил в
   `errors`. На Windows файл, который держит открытым живой процесс, не удаляется —
   и отчёт рапортовал «снято N мегабайт» при нуле снятого.

Запуск: python praxis_test.py test_runs_retention_1709 -v
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

import runs_prune

# ⚠ ЭТИ СЛУЧАИ ПРОВЕРЯЮТ ПУТЬ ИЗДАНИЯ, А НЕ ПУТЬ СИСТЕМЫ. На Linux (гейт идёт в докере)
# POSIX `openat` есть, и `run_retention` читает открытую работу сам — своим строгим
# разбором, которому нужен полный ядерный формат карточки. Проверять здесь надо другое:
# что издание делает, когда системного примитива НЕТ. Поэтому ядерный обход заменяется
# его честным ответом с Windows — «compatibility: …», — и дальше работает наш путь.
class _NoOpenat:
    """Ответ ядерного обхода на системе без POSIX-примитивов."""

    @staticmethod
    def discover_open_evidence_runs(base):
        return set(), ["compatibility: open-evidence discovery requires POSIX openat support"]


def _tree(root: Path) -> Path:
    (root / "memory" / "runs").mkdir(parents=True, exist_ok=True)
    return root


def _run(base: Path, name: str, *, status: str = "done", results: int = 3) -> Path:
    month = name[4:10]
    run_dir = base / "memory" / "runs" / month / name
    (run_dir / "results").mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    (run_dir / "events.jsonl").write_text('{"kind":"started"}\n', encoding="utf-8")
    for i in range(results):
        (run_dir / "results" / f"{i}.json").write_text("x" * 1024, encoding="utf-8")
    return run_dir


def _old_name(days: int, tail: str = "0000dead") -> str:
    stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    return "run-" + stamp.strftime("%Y%m%dT%H%M%S") + "123456Z-" + tail


class RetentionWorksHere(unittest.TestCase):
    """Проход состоится и на системе без POSIX-примитивов."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = _tree(Path(self.tmp.name))
        self.addCleanup(self.tmp.cleanup)

    def test_old_results_are_gone_and_manifest_stays(self):
        run = _run(self.base, _old_name(30))
        report = runs_prune.prune(self.base, apply=True)
        self.assertFalse((run / "results").exists(), "снимки старого прогона остались на диске")
        self.assertTrue((run / "manifest.json").exists(), "манифест снесли — история «что было» потеряна")
        self.assertTrue((run / "events.jsonl").exists(), "события снесли при умолчании 0 дней")
        self.assertGreater(report["results_runs"], 0)
        self.assertNotIn("open-evidence discovery failed; nothing pruned", report["errors"],
                         "на этой системе ретенция вышла, ничего не тронув")

    def test_a_young_run_is_untouched(self):
        run = _run(self.base, _old_name(1))
        runs_prune.prune(self.base, apply=True)
        self.assertTrue((run / "results").exists(), "снимки свежего прогона сняли")

    def test_a_live_run_is_untouched(self):
        run = _run(self.base, _old_name(30, "0000l1ve"), status="running")
        report = runs_prune.prune(self.base, apply=True)
        self.assertTrue((run / "results").exists(), "сняли снимки у прогона, который ещё идёт")
        self.assertEqual(report["skipped_live"], 1)

    def test_a_run_held_by_open_work_is_untouched(self):
        """Карточка открытой работы держит свой прогон — даже старый."""
        name = _old_name(30, "0000ab01")
        run = _run(self.base, name)
        card = self.base / "memory" / "work" / "tasks" / "t-1"
        card.mkdir(parents=True)
        (card / "TASK.md").write_text(
            "---\nstatus: \"doing\"\nrun_ids: [\"%s\"]\n---\n\nработа идёт\n" % name,
            encoding="utf-8")
        with mock.patch.dict(sys.modules, {"run_retention": _NoOpenat}):
            report = runs_prune.prune(self.base, apply=True)
        self.assertTrue((run / "results").exists(), "сняли снимки, на которые ссылается открытая работа")
        self.assertEqual(report["skipped_protected"], 1)

    def test_a_closed_card_does_not_hold_its_run(self):
        name = _old_name(30, "0000dd0e")
        run = _run(self.base, name)
        card = self.base / "memory" / "work" / "tasks" / "t-2"
        card.mkdir(parents=True)
        (card / "TASK.md").write_text(
            "---\nstatus: \"done\"\nrun_ids: [\"%s\"]\n---\n\nсделано\n" % name, encoding="utf-8")
        with mock.patch.dict(sys.modules, {"run_retention": _NoOpenat}):
            runs_prune.prune(self.base, apply=True)
        self.assertFalse((run / "results").exists(), "закрытая карточка держит прогон вечно")

    def test_an_open_desire_holds_its_run(self):
        name = _old_name(30, "0000c0fe")
        run = _run(self.base, name)
        desires = self.base / "memory" / "desires"
        desires.mkdir(parents=True)
        (desires / "events.jsonl").write_text(
            json.dumps({"desire_id": "d-1", "state": {"status": "open", "run_ids": [name]}}) + "\n",
            encoding="utf-8")
        with mock.patch.dict(sys.modules, {"run_retention": _NoOpenat}):
            report = runs_prune.prune(self.base, apply=True)
        self.assertTrue((run / "results").exists(), "сняли снимки, нужные незакрытому желанию")
        self.assertEqual(report["skipped_protected"], 1)

    def test_an_unreadable_source_stops_the_whole_pass(self):
        """Молчание источника — не разрешение: не прочли работу — не трогаем ничего."""
        run = _run(self.base, _old_name(30))
        tasks = self.base / "memory" / "work" / "tasks" / "t-3"
        tasks.mkdir(parents=True)
        (tasks / "TASK.md").write_bytes(b"\xff\xfe\x00broken")
        with mock.patch.dict(sys.modules, {"run_retention": _NoOpenat}):
            report = runs_prune.prune(self.base, apply=True)
        self.assertTrue((run / "results").exists(), "сняли снимки, не сумев прочитать открытую работу")
        self.assertTrue(report["errors"], "проход промолчал о том, что источник не прочитан")


class TheReportDoesNotLie(unittest.TestCase):
    """Отчёт считает то, что вправду снято."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = _tree(Path(self.tmp.name))
        self.addCleanup(self.tmp.cleanup)

    def test_dry_run_counts_but_keeps_everything(self):
        run = _run(self.base, _old_name(30))
        report = runs_prune.prune(self.base, apply=False)
        self.assertTrue((run / "results").exists())
        self.assertGreater(report["results_bytes"], 0, "счёт вхолостую не назвал ни байта")

    @unittest.skipUnless(os.name == "nt", "держать файл от удаления умеет Windows")
    def test_what_could_not_be_deleted_is_not_reported_as_freed(self):
        run = _run(self.base, _old_name(30))
        held = run / "results" / "0.json"
        with open(held, "r", encoding="utf-8"):
            report = runs_prune.prune(self.base, apply=True)
        self.assertEqual(report["results_bytes"], 0,
                         "отчёт назвал снятыми байты файла, который остался на диске")
        self.assertTrue(report["errors"], "не снялось — и об этом не сказано ни слова")
        self.assertTrue(held.exists())


if __name__ == "__main__":
    unittest.main()
