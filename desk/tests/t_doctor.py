# -*- coding: utf-8 -*-
"""Стенд доктора (уровень 0): чистые функции решений + CLI smoke.

Запуск:  python tests/t_doctor.py

Проверяется контракт, а не удобство:
- три нуля различаются (нет файла / бит / пусто) — разными словами в вердикте;
- чистые decide_* не трогают диск: им подаётся уже прочитанный факт;
- пороги — константы модуля (beat устарел -> sick; рестарт-петля -> sick);
- CLI: status на пустой папке не падает и код 0; explain на неизвестного
  агента — код 2 и жалоба в stderr; JSON status разбирается и несёт приговор.

Образец стиля — t_agents_cli.py: программа зовётся отдельным процессом,
PYTHONUTF8=1, судим по коду возврата.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESK = HERE.parent
sys.path.insert(0, str(DESK / "localharness"))

import doctor  # noqa: E402


def run_cli(*args: str, base: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-X", "utf8", str(DESK / "localharness" / "doctor.py"),
                           *args, "--base", str(base)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=120)


def lay(root: Path, files: dict) -> None:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=1)
        path.write_text(text, encoding="utf-8", newline="\n")


class PureDecisions(unittest.TestCase):
    """Чистые функции: без диска, только факты."""

    def test_three_zeros_speak_differently(self):
        now = 1000.0
        missing = doctor.decide_beat({"state": "missing"}, 15.0, now, "надзор")
        broken = doctor.decide_beat({"state": "broken", "why": "x"}, 15.0, now, "надзор")
        empty = doctor.decide_beat({"state": "empty"}, 15.0, now, "надзор")
        words = {missing["note"], broken["note"], empty["note"]}
        self.assertEqual(len(words), 3, "три нуля обязаны звучать тремя разными фразами")
        for note in (missing["note"], broken["note"], empty["note"]):
            self.assertNotEqual(note.strip(), "")

    def test_fresh_beat_is_healthy(self):
        now = 1000.0
        got = doctor.decide_beat({"state": "ok", "data": {"beat_epoch": now - 3}}, 15.0, now,
                                 "надзор")
        self.assertEqual(got["verdict"], doctor.VERDICT_HEALTHY)

    def test_stale_beat_is_watch_not_sick(self):
        # один замолчавший прибор — симптом, не приговор: пауза контейнера или
        # погашенный владелецем процесс выглядят так же; «болен» ставит паттерн
        now = 1_000_000.0
        got = doctor.decide_beat({"state": "ok", "data": {"beat_epoch": now - 60}}, 15.0, now,
                                 "надзор")
        self.assertEqual(got["verdict"], doctor.VERDICT_WATCH)
        self.assertIn("молчит", got["note"])

    def test_future_beat_is_watch_not_healthy(self):
        now = 1000.0
        got = doctor.decide_beat({"state": "ok", "data": {"beat_epoch": now + 3600}}, 15.0,
                                 now, "надзор")
        self.assertEqual(got["verdict"], doctor.VERDICT_WATCH)

    def test_beat_without_epoch_is_watch(self):
        got = doctor.decide_beat({"state": "ok", "data": {"kind": "x"}}, 15.0, 1000.0, "надзор")
        self.assertEqual(got["verdict"], doctor.VERDICT_WATCH)

    def test_reader_missing_is_watch_not_sick(self):
        got = doctor.decide_reader({"state": "missing"}, 1000.0)
        self.assertEqual(got["verdict"], doctor.VERDICT_WATCH)

    def test_reader_stale_is_watch_with_log_hint(self):
        now = 1_000_000.0
        got = doctor.decide_reader({"state": "ok", "data": {"at": now - 300}}, now)
        self.assertEqual(got["verdict"], doctor.VERDICT_WATCH)
        self.assertIn("runner.log", got["note"])

    def test_reader_alive_and_busy_mentions_run(self):
        now = 1000.0
        got = doctor.decide_reader(
            {"state": "ok", "data": {"at": now - 5, "busy": True, "run": "r42"}}, now)
        self.assertEqual(got["verdict"], doctor.VERDICT_HEALTHY)
        self.assertIn("r42", got["note"])

    def test_restart_loop_thresholds(self):
        self.assertEqual(doctor.decide_restarts(0)["verdict"], doctor.VERDICT_HEALTHY)
        self.assertEqual(doctor.decide_restarts(1)["verdict"], doctor.VERDICT_WATCH)
        self.assertEqual(doctor.decide_restarts(2)["verdict"], doctor.VERDICT_WATCH)
        got = doctor.decide_restarts(3)
        self.assertEqual(got["verdict"], doctor.VERDICT_SICK)
        self.assertIn("петля", got["note"])

    def test_quiet_deaf_with_debt_is_sick_long_silence_is_watch(self):
        deaf = doctor.decide_quiet(30.0, 5)
        self.assertEqual(deaf["verdict"], doctor.VERDICT_SICK)
        self.assertIn("недоставленн", deaf["note"])
        # тишина без долга — норма для спокойного агента: наблюдение, не болезнь
        quiet = doctor.decide_quiet(95.0, 0)
        self.assertEqual(quiet["verdict"], doctor.VERDICT_WATCH)
        self.assertNotIn("недоставленн", quiet["note"])

    def test_quiet_none_is_watch(self):
        got = doctor.decide_quiet(None, 0)
        self.assertEqual(got["verdict"], doctor.VERDICT_WATCH)

    def test_skip_storm(self):
        self.assertEqual(doctor.decide_skips(0)["verdict"], doctor.VERDICT_HEALTHY)
        self.assertEqual(doctor.decide_skips(10)["verdict"], doctor.VERDICT_WATCH)
        self.assertEqual(doctor.decide_skips(200)["verdict"], doctor.VERDICT_SICK)

    def test_sick_patterns_are_sick_but_silent_install_is_only_watch(self):
        # «болен» ставят только паттерны; приборная тишина без паттерна — watch.
        self.assertEqual(doctor.decide_restarts(3)["verdict"], doctor.VERDICT_SICK)
        self.assertEqual(doctor.decide_quiet(30.0, 5)["verdict"], doctor.VERDICT_SICK)
        self.assertEqual(doctor.decide_skips(200)["verdict"], doctor.VERDICT_SICK)
        stale = doctor.decide_beat(
            {"state": "ok", "data": {"beat_epoch": 1.0}}, 15.0, 1_000_000.0, "надзор")
        self.assertEqual(stale["verdict"], doctor.VERDICT_WATCH)

    def test_worst(self):
        self.assertEqual(doctor.worst("healthy", "watch", "sick"), "sick")
        self.assertEqual(doctor.worst("healthy", "watch"), "watch")
        self.assertEqual(doctor.worst(), "healthy")


class Probes(unittest.TestCase):
    """Чтение приборов с различением трёх нулей на настоящих файлах."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-doctor-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_read_probe_states(self):
        gone = self.root / "gone.json"
        self.assertEqual(doctor.read_probe(gone)["state"], "missing")
        empty = self.root / "empty.json"
        empty.write_text("", encoding="utf-8")
        self.assertEqual(doctor.read_probe(empty)["state"], "broken")
        junk = self.root / "junk.json"
        junk.write_text("{не json", encoding="utf-8")
        self.assertEqual(doctor.read_probe(junk)["state"], "broken")
        bom = self.root / "bom.json"
        bom.write_bytes(b"\xef\xbb\xbf" + json.dumps({"beat_epoch": 1}).encode("utf-8"))
        self.assertEqual(doctor.read_probe(bom)["state"], "ok")
        array = self.root / "array.json"
        array.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(doctor.read_probe(array)["state"], "broken")

    def test_supervisor_state_dir_not_control(self):
        # записка надзора лежит в .state, а НЕ в .control — проверяем, что врач
        # читает правильную папку
        tree = self.root / "data"
        (tree / "memory" / ".control").mkdir(parents=True)
        (tree / "memory" / ".control" / "supervisor.json").write_text(
            json.dumps({"beat_epoch": 1}), encoding="utf-8")
        self.assertEqual(doctor.read_supervisor(tree)["state"], "missing")
        (tree / "memory" / ".state").mkdir(parents=True)
        (tree / "memory" / ".state" / "supervisor.json").write_text(
            json.dumps({"beat_epoch": 1}), encoding="utf-8")
        self.assertEqual(doctor.read_supervisor(tree)["state"], "ok")

    def test_valid_json_without_known_fields_is_empty(self):
        tree = self.root / "data"
        (tree / "memory" / ".state").mkdir(parents=True)
        (tree / "memory" / ".state" / "supervisor.json").write_text(
            json.dumps({"hello": "world"}), encoding="utf-8")
        self.assertEqual(doctor.read_supervisor(tree)["state"], "empty")


class Diagnosis(unittest.TestCase):
    """Сборка диагноза на дереве с приборами."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-doctor-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_restart_loop_in_journal_makes_agent_sick(self):
        import datetime as dt
        tree = self.root / "data"
        journal = tree / "memory" / "journal"
        journal.mkdir(parents=True)
        now = time.time()
        today = dt.datetime.now()
        rows = []
        for minute in (2, 6, 10):  # три рестарта за последние минуты
            when = dt.datetime.now() - dt.timedelta(minutes=minute)
            rows.append(f"- {when:%H:%M} (s3) [restart] перезапускаюсь")
        (journal / f"{today:%Y-%m-%d}.md").write_text(
            "\n".join(rows) + "\n", encoding="utf-8", newline="\n")
        # свежий mtime обязателен: нетронутый час файл не несёт свежего рестарта
        os.utime(journal / f"{today:%Y-%m-%d}.md", (now, now))
        got = doctor.diagnose_agent(tree, now=now)
        self.assertEqual(got["verdict"], doctor.VERDICT_SICK)
        self.assertEqual(got["checks"]["restarts"]["verdict"], doctor.VERDICT_SICK)
        self.assertEqual(got["measures"]["restarts_20m"], 3)

    def test_healthy_tree_needs_alive_instruments(self):
        tree = self.root / "data"
        now = time.time()
        lay(tree, {
            "memory/.state/supervisor.json": {"beat_epoch": now - 2, "kind": "serverboot"},
            "memory/.control/updater.json": {"beat_epoch": now - 4, "phase": "idle"},
            "memory/.control/desk_inbox/.reader.json": {"at": now - 8, "pid": 123,
                                                        "busy": False},
            "memory/.state/llm_calls.jsonl": json.dumps({"ts": now - 60}) + "\n",
        })
        got = doctor.diagnose_agent(tree, now=now)
        # рестартов нет, вызов модели минуту назад — приговор зависит от бит,
        # но не от журналов: живые биты => healthy
        self.assertEqual(got["verdict"], doctor.VERDICT_HEALTHY)
        # у здорового хвост раннера не показывается
        self.assertNotIn("runner_tail", got)

    def test_sick_tree_gets_runner_tail(self):
        tree = self.root / "data"
        now = time.time()
        lay(tree, {
            "memory/.state/supervisor.json": {"beat_epoch": now - 2, "kind": "serverboot"},
            "runner.log": "2026-10-06Traceback (most recent call last)\nboom\n",
        })
        # upаdater/reader отсутствуют -> watch; supervisor жив; но reader missing =>
        # watch overall, хвост раннера показывается
        got = doctor.diagnose_agent(tree, now=now)
        self.assertNotEqual(got["verdict"], doctor.VERDICT_HEALTHY)
        self.assertIn("runner_tail", got)
        self.assertIn("boom", got["runner_tail"])


class Cli(unittest.TestCase):
    """CLI smoke: отдельный процесс, как зовёт владелец."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="helene-doctor-cli-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "install"
        self.root.mkdir()
        lay(self.root, {"helene.json": {"agent": {"name": "Hélène"}, "port": 8094}})

    def test_status_on_empty_install_exits_zero(self):
        done = run_cli("status", base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report["schema"], "helene.doctor.v1")
        self.assertEqual(len(report["agents"]), 1)
        self.assertEqual(report["agents"][0]["id"], "main")
        # приборов нет — это наблюдение, а не болезнь
        self.assertEqual(report["agents"][0]["verdict"], "watch")

    def test_explain_unknown_agent_exits_two(self):
        done = run_cli("explain", "--agent", "ghost", base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertIn("ghost", done.stderr)

    def test_status_unknown_agent_is_error_json(self):
        done = run_cli("status", "--agent", "ghost", base=self.root)
        self.assertEqual(done.returncode, 2)
        self.assertTrue(done.stderr.strip())

    def test_explain_speaks_russian_words(self):
        done = run_cli("explain", base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Hélène", done.stdout)
        self.assertIn("наблюдение", done.stdout)
        self.assertIn("Итог установки", done.stdout)

    def test_second_agent_in_roster_is_diagnosed(self):
        lay(self.root, {
            "agents/mira/helene.json": {"agent": {"name": "Мира"}, "port": 8095,
                                        "tree": "data"},
        })
        done = run_cli("status", base=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual({a["id"] for a in report["agents"]}, {"main", "mira"})
        # выбор одного агента
        one = run_cli("status", "--agent", "mira", base=self.root)
        self.assertEqual(one.returncode, 0, one.stderr)
        self.assertEqual({a["id"] for a in json.loads(one.stdout)["agents"]}, {"mira"})

    def test_conflict_agent_is_sick_without_probes(self):
        lay(self.root, {
            "agents/mira/helene.json": {"agent": {"name": "Мира"}, "port": 8094},
        })
        done = run_cli("status", base=self.root)
        report = json.loads(done.stdout)
        mira = [a for a in report["agents"] if a["id"] == "mira"][0]
        self.assertEqual(mira["verdict"], "sick")
        self.assertIn("занят", mira.get("note", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
