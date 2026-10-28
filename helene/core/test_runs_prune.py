"""Ретенция прогонов (12.09): снимки старше недели уходят, события — навсегда (рычаг),
манифесты и живые прогоны — никогда; без ответа об открытой работе — ничего.

Запуск: python praxis_test.py test_runs_prune -v
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import runs_prune  # noqa: E402

NOW = _dt.datetime(2026, 9, 12, 20, 0, tzinfo=_dt.timezone.utc)


def _run(base: Path, days_old: float, status: str = "done", *, results=True, events=True,
         artifacts=False, suffix: str = "aa") -> Path:
    stamp = (NOW - _dt.timedelta(days=days_old)).strftime("%Y%m%dT%H%M%S")
    run_dir = base / "memory" / "runs" / f"{stamp[:4]}-{stamp[4:6]}" / f"run-{stamp}000000Z-{suffix}"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    if results:
        (run_dir / "results").mkdir()
        (run_dir / "results" / "0001-model-input.log").write_text("x" * 1000, encoding="utf-8")
        (run_dir / "results" / "0002-model-output.log").write_text("y" * 500, encoding="utf-8")
    if events:
        (run_dir / "events.jsonl").write_text('{"a":1}\n' * 20, encoding="utf-8")
    if artifacts:
        (run_dir / "artifacts").mkdir()
        (run_dir / "artifacts" / "blob").write_bytes(b"z" * 300)
    return run_dir


class RunsPruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_runs_prune_"))
        self.protect = mock.patch.object(runs_prune, "protected_runs", return_value=set())
        self.protect.start()
        self.addCleanup(self.protect.stop)
        self.env = mock.patch.dict(os.environ, {"PRAXIS_RUNS_RETENTION": "on",
                                                "PRAXIS_RUNS_RAW_DAYS": "7",
                                                "PRAXIS_RUNS_EVENTS_DAYS": "0",
                                                "PRAXIS_RUNS_ARTIFACTS_DAYS": "60"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_young_runs_are_untouched(self):
        run_dir = _run(self.tmp, 2)
        out = runs_prune.prune(self.tmp, now=NOW, apply=True)
        self.assertEqual(out["young"], 1)
        self.assertTrue((run_dir / "results" / "0001-model-input.log").exists())
        self.assertEqual(out["results_runs"], 0)

    def test_old_terminal_run_loses_results_but_keeps_manifest_and_events(self):
        run_dir = _run(self.tmp, 10)
        out = runs_prune.prune(self.tmp, now=NOW, apply=True)
        self.assertEqual(out["results_runs"], 1)
        self.assertEqual(out["results_bytes"], 1500)
        self.assertFalse((run_dir / "results").exists())
        self.assertTrue((run_dir / "manifest.json").exists())
        self.assertTrue((run_dir / "events.jsonl").exists(), "события живут 60 дней")

    def test_very_old_run_keeps_events_forever_but_loses_artifacts(self):
        """13.09: события читают list_active_runs и сверка исходящих — не удаляются никогда
        по умолчанию; первый проход с 60 днями снёс их у 65 июльских прогонов и оба
        читателя падали при каждом старте."""
        run_dir = _run(self.tmp, 70, artifacts=True)
        out = runs_prune.prune(self.tmp, now=NOW, apply=True)
        self.assertEqual(out["events_runs"], 0)
        self.assertTrue((run_dir / "events.jsonl").exists(), "события — навсегда")
        self.assertFalse((run_dir / "artifacts").exists(), "артефакты — 60 дней")
        self.assertTrue((run_dir / "manifest.json").exists(), "манифест — навсегда")

    def test_events_lever_is_explicit_and_never_below_raw_days(self):
        run_dir = _run(self.tmp, 70, artifacts=True)
        with mock.patch.dict(os.environ, {"PRAXIS_RUNS_EVENTS_DAYS": "60"}):
            self.assertEqual(runs_prune.events_days(), 60.0)
            out = runs_prune.prune(self.tmp, now=NOW, apply=True)
        self.assertEqual(out["events_runs"], 1)
        self.assertFalse((run_dir / "events.jsonl").exists())
        with mock.patch.dict(os.environ, {"PRAXIS_RUNS_EVENTS_DAYS": "3", "PRAXIS_RUNS_RAW_DAYS": "7"}):
            self.assertEqual(runs_prune.events_days(), 7.0, "события не моложе снимков")
        with mock.patch.dict(os.environ, {"PRAXIS_RUNS_EVENTS_DAYS": "0"}):
            self.assertEqual(runs_prune.events_days(), 0.0)

    def test_live_and_protected_runs_are_skipped(self):
        live = _run(self.tmp, 30, status="running", suffix="live")
        kept = _run(self.tmp, 30, status="done", suffix="keep")
        with mock.patch.object(runs_prune, "protected_runs", return_value={kept.name}):
            out = runs_prune.prune(self.tmp, now=NOW, apply=True)
        self.assertEqual(out["skipped_live"], 1)
        self.assertEqual(out["skipped_protected"], 1)
        self.assertTrue((live / "results").exists())
        self.assertTrue((kept / "results").exists())

    def test_unknown_open_work_means_nothing_is_pruned(self):
        run_dir = _run(self.tmp, 30)
        with mock.patch.object(runs_prune, "protected_runs", return_value=None):
            out = runs_prune.prune(self.tmp, now=NOW, apply=True)
        self.assertTrue(out["errors"])
        self.assertTrue((run_dir / "results").exists())

    def test_dry_run_counts_without_deleting(self):
        run_dir = _run(self.tmp, 10)
        out = runs_prune.prune(self.tmp, now=NOW, apply=False)
        self.assertEqual(out["results_bytes"], 1500)
        self.assertTrue((run_dir / "results").exists())
        self.assertIn("к снятию", runs_prune.report_line(out))

    def test_lever_off_does_nothing(self):
        run_dir = _run(self.tmp, 30)
        with mock.patch.dict(os.environ, {"PRAXIS_RUNS_RETENTION": "off"}):
            out = runs_prune.prune(self.tmp, now=NOW, apply=True)
        self.assertTrue((run_dir / "results").exists())
        self.assertIn("off", out["errors"][0])

    def test_time_budget_stops_the_pass_honestly(self):
        for i in range(3):
            _run(self.tmp, 10 + i, suffix=f"b{i}")
        with mock.patch.object(runs_prune.time, "monotonic", side_effect=[0.0, 0.0, 100.0, 100.0, 100.0]):
            out = runs_prune.prune(self.tmp, now=NOW, apply=False, budget_seconds=1.0)
        self.assertTrue(out["stopped_by_budget"])
        self.assertIn("бюджету", runs_prune.report_line(out))


if __name__ == "__main__":
    unittest.main()
