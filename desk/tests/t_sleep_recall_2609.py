# -*- coding: utf-8 -*-
"""Сон и обслуживание индекса памяти в издании (1.0.1).

До 1.0.1 её ночной цикл (`sleep.run_scheduled`) в издании не звал никто: его заводят
часы `mtproto_runner`, которого харнесс не запускает. А с её переработкой recall
(memory_fts v8) явный вопрос к памяти больше не пересобирает индекс сам — оставляет
заявку, и без обслуживания заявка висела бы до сна. Проверяется:
  * `runner._sleep_due` — выключатель, мозг, её `sleep.due` со стартом процесса,
    занятость на время сна и снятие занятости даже при исключении;
  * `runner._recall_care_once` — нет заявки и база есть → ничего; заявка или нет
    базы → `memory_index.build`; другой сборщик держит замок → «busy»; сбой не роняет;
  * замки и заявки `memory_fts` дерева на ЭТОЙ системе (на Windows — ветка msvcrt без
    rename/unlink открытого файла; осиротевшая заявка подбирается под замком сборщика).

Запуск:  python tests/t_sleep_recall_2609.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "localharness")]
import runner  # noqa: E402


def tree_source() -> Path | None:
    for var, sub in (("HELENE_TREE_SRC", ""), ("HELENE_BODY_DIR", "tree")):
        raw = os.environ.get(var)
        if raw:
            candidate = Path(raw) / sub if sub else Path(raw)
            if (candidate / "memory_fts.py").is_file():
                return candidate
    for name in ("port-2409", "live-fix"):
        candidate = ROOT.parent / name
        if (candidate / "memory_fts.py").is_file():
            return candidate
    return None


class SleepDue(unittest.TestCase):
    def setUp(self):
        self.calls: list = []
        fake = types.ModuleType("sleep")
        fake.due = lambda now, started: self.calls.append(("due", now, started)) or self.due
        fake.run_scheduled = self._run
        self.due = True
        self.busy_during: list = []
        self.raise_in_run = False
        patches = [
            mock.patch.dict(sys.modules, {"sleep": fake}),
            mock.patch.object(runner, "_agent", object()),
            mock.patch.object(runner, "_brain_ready", lambda: True),
            mock.patch.object(runner, "_tree", None),
            mock.patch.dict(os.environ, {"PRAXIS_SLEEP_CYCLE": "on"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self):
        self.busy_during.append((runner._busy["busy"], runner._busy["run"]))
        if self.raise_in_run:
            raise RuntimeError("сон упал")
        return "сон: слито 0"

    def test_due_runs_once_with_process_start_and_marks_busy(self):
        runner._sleep_due()
        self.assertEqual(self.calls, [("due", None, runner._STARTED_AT)])
        self.assertEqual(self.busy_during, [(True, "sleep")])
        self.assertFalse(runner._busy["busy"], "после сна занятость снята")

    def test_not_due_does_not_run(self):
        self.due = False
        runner._sleep_due()
        self.assertEqual(self.busy_during, [])

    def test_switch_off_and_no_brain(self):
        with mock.patch.dict(os.environ, {"PRAXIS_SLEEP_CYCLE": "off"}):
            runner._sleep_due()
        with mock.patch.object(runner, "_brain_ready", lambda: False):
            runner._sleep_due()
        self.assertEqual(self.calls, [], "выключенный сон и мозг без модели даже не спрашивают due")

    def test_busy_is_cleared_even_when_sleep_raises(self):
        self.raise_in_run = True
        with self.assertRaises(RuntimeError):
            runner._sleep_due()
        self.assertFalse(runner._busy["busy"])

    def test_edition_default_is_on(self):
        import boot
        self.assertEqual(boot.PORT_DEFAULTS.get("PRAXIS_SLEEP_CYCLE"), "on")


class RecallCare(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.mem = Path(tmp.name) / "memory"
        (self.mem / ".state").mkdir(parents=True)
        self.requested = False
        self.built = 0
        self.build_result: dict = {"fts": {"sources": 3, "chunks": 9}}
        self.build_raises = False
        fts = types.ModuleType("memory_fts")
        fts.refresh_requested = lambda memory_dir: self.requested
        idx = types.ModuleType("memory_index")
        idx.MEM_DIR = self.mem
        idx.build = self._build
        for p in (mock.patch.dict(sys.modules, {"memory_fts": fts, "memory_index": idx}),
                  mock.patch.object(runner, "_agent", object())):
            p.start()
            self.addCleanup(p.stop)

    def _build(self):
        self.built += 1
        if self.build_raises:
            raise OSError("диск занят")
        return self.build_result

    def _db(self):
        (self.mem / ".state" / "recall.sqlite3").write_bytes(b"")

    def test_nothing_to_do_when_database_exists_and_no_request(self):
        self._db()
        self.assertEqual(runner._recall_care_once(), "idle")
        self.assertEqual(self.built, 0)

    def test_request_or_missing_database_builds(self):
        self.assertEqual(runner._recall_care_once(), "built", "базы нет — строим")
        self._db()
        self.requested = True
        self.assertEqual(runner._recall_care_once(), "built", "заявка — строим")
        self.assertEqual(self.built, 2)

    def test_other_builder_and_failure_do_not_raise(self):
        self.requested = True
        self.build_result = {"fts": {}}
        self.assertEqual(runner._recall_care_once(), "busy")
        self.build_raises = True
        self.assertEqual(runner._recall_care_once(), "failed")

    def test_no_agent_is_idle(self):
        with mock.patch.object(runner, "_agent", None):
            self.assertEqual(runner._recall_care_once(), "idle")
        self.assertEqual(self.built, 0)


SCRIPT = textwrap.dedent(r'''
    import json, os, sys, tempfile
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    import memory_fts as m
    out = {"windows": m._WINDOWS}
    with tempfile.TemporaryDirectory() as td:
        mem = Path(td) / "memory"
        (mem / ".state").mkdir(parents=True)
        out["first_request"] = m.request_refresh(memory_dir=mem)
        out["second_request"] = m.request_refresh(memory_dir=mem)
        fd = m.acquire_builder_lock(memory_dir=mem)
        out["second_builder_refused"] = m.acquire_builder_lock(memory_dir=mem) is None
        claim = m._claim_refresh_request_locked(memory_dir=mem)
        out["claimed"] = bool(claim and claim.exists())
        m.complete_refresh_request(memory_dir=mem, claim=claim)
        out["completed"] = not m.refresh_requested(memory_dir=mem)
        m.release_builder_lock(fd)
        if m._WINDOWS:
            m.request_refresh(memory_dir=mem)
            fd = m.acquire_builder_lock(memory_dir=mem)
            orphan = m._claim_refresh_request_locked(memory_dir=mem)
            m._CLAIM_FDS.pop(str(orphan))  # его сборщик умер, не отпустив
            m.release_builder_lock(fd)
            fd = m.acquire_builder_lock(memory_dir=mem)
            fresh = m._claim_refresh_request_locked(memory_dir=mem)
            out["orphan_recovered"] = bool(fresh and fresh != orphan and not orphan.exists())
            m.restore_refresh_request(memory_dir=mem, claim=fresh)
            out["restored"] = (mem / ".state" / "recall_refresh.json").exists()
            m.release_builder_lock(fd)
        c = m.claim_refresh_request(memory_dir=mem) if m.refresh_requested(memory_dir=mem) else None
        if c is not None:
            out["public_claim_holds_builder"] = m.acquire_builder_lock(memory_dir=mem) is None
            m.complete_refresh_request(memory_dir=mem, claim=c)
        fd = m.acquire_builder_lock(memory_dir=mem)
        out["builder_free_at_end"] = fd is not None
        if fd is not None:
            m.release_builder_lock(fd)
    print(json.dumps(out))
''')


@unittest.skipIf(tree_source() is None, "дерева рядом нет (HELENE_TREE_SRC) — memory_fts не проверен")
class RecallLocksOnThisSystem(unittest.TestCase):
    def test_claims_and_builder_lock(self):
        tree = tree_source()
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "probe.py"
            script.write_text(SCRIPT, encoding="utf-8")
            env = dict(os.environ, PRAXIS_TEST="1", PYTHONIOENCODING="utf-8")
            proc = subprocess.run([sys.executable, str(script), str(tree)], capture_output=True,
                                  text=True, encoding="utf-8", env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(out["windows"], os.name == "nt")
        for key in ("first_request", "second_builder_refused", "claimed", "completed",
                    "builder_free_at_end"):
            self.assertTrue(out[key], (key, out))
        self.assertFalse(out["second_request"], "вторая заявка поверх живой — не новая")
        if os.name == "nt":
            self.assertTrue(out["orphan_recovered"], out)
            self.assertTrue(out["restored"], out)
            self.assertTrue(out["public_claim_holds_builder"], out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
