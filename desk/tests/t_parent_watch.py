# -*- coding: utf-8 -*-
"""Стенд сторожа родителя: оболочка умерла — движок и канал уходят вслед.

Запуск:  python tests/t_parent_watch.py
         (живая проба — только на POSIX: на Windows детей держит job-объект
          оболочки, и сторож там не ставится по построению)

Чего боимся: осиротевший движок на macOS живёт дальше с замком на дереве и
открытой сессией Telegram, а следующее окно видит «этой памятью уже занят
другой». Проверяется не «поток стартовал», а вся дорога: родитель убит → ppid
сменился → SIGTERM себе → SystemExit в главном потоке → `finally` отработал.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
LOCALHARNESS = HERE.parent / "localharness"
sys.path.insert(0, str(LOCALHARNESS))

import boot  # noqa: E402

POSIX = os.name != "nt"

#: Ребёнок: сторож с частым тиком, главный поток спит; выход через SystemExit
#: оставляет след `soft` — доказательство мягкого пути, а не os._exit.
CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
import boot
boot.arm_soft_exit("проба")
boot.watch_parent("проба", every=0.2, grace=3.0)
marker = sys.argv[2]
open(marker + ".alive", "w").write("1")
try:
    while True:
        time.sleep(0.1)
finally:
    open(marker + ".soft", "w").write("1")
"""

#: Родитель: ставит HELENE_PARENT_PID = свой pid, поднимает ребёнка и ждёт, пока
#: его не убьют снаружи. pid ребёнка печатает стенду.
PARENT = r"""
import os, subprocess, sys, time
env = dict(os.environ, HELENE_PARENT_PID=str(os.getpid()), PYTHONUTF8="1")
child = subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2], sys.argv[3]], env=env)
print(child.pid, flush=True)
while True:
    time.sleep(0.2)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Knobs(unittest.TestCase):
    def test_parent_pid_is_read_and_checked(self):
        with patch.dict(os.environ, {"HELENE_PARENT_PID": "4242"}):
            self.assertEqual(boot.parent_pid(), 4242)
        with patch.dict(os.environ, {"HELENE_PARENT_PID": "не число"}):
            self.assertEqual(boot.parent_pid(), 0)
        with patch.dict(os.environ, {"HELENE_PARENT_PID": ""}):
            self.assertEqual(boot.parent_pid(), 0)

    def test_without_the_variable_nothing_is_armed(self):
        with patch.dict(os.environ, {"HELENE_PARENT_PID": ""}):
            self.assertEqual(boot.watch_parent("стенд"), 0)

    @unittest.skipUnless(os.name == "nt", "про Windows: там сторожа нет по построению")
    def test_windows_never_arms(self):
        with patch.dict(os.environ, {"HELENE_PARENT_PID": str(os.getppid())}):
            self.assertEqual(boot.watch_parent("стенд"), 0)
        self.assertFalse(boot.arm_soft_exit("стенд"))

    @unittest.skipUnless(POSIX, "про POSIX")
    def test_wrong_parent_is_not_watched(self):
        # Назван не тот, кто нас поднял, — судить о его жизни по ppid нельзя.
        with patch.dict(os.environ, {"HELENE_PARENT_PID": str(os.getpid())}):
            self.assertEqual(boot.watch_parent("стенд"), 0)


@unittest.skipUnless(POSIX, "живая проба сторожа — только на POSIX")
class Orphan(unittest.TestCase):
    def test_child_leaves_softly_after_the_parent_dies(self):
        with tempfile.TemporaryDirectory(prefix="helene-watch-") as tmp:
            marker = str(Path(tmp) / "след")
            parent = subprocess.Popen(
                [sys.executable, "-c", PARENT, CHILD, str(LOCALHARNESS), marker],
                stdout=subprocess.PIPE, text=True)
            self.addCleanup(lambda: parent.poll() is None and parent.kill())
            child_pid = int(parent.stdout.readline().strip())
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not Path(marker + ".alive").exists():
                time.sleep(0.1)
            self.assertTrue(Path(marker + ".alive").exists(), "ребёнок не поднялся")
            self.assertTrue(_alive(child_pid))
            # Родитель умирает грубо — как умирает упавшая оболочка.
            parent.kill()
            parent.wait(timeout=10)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and _alive(child_pid):
                time.sleep(0.1)
            self.assertFalse(_alive(child_pid), "сирота пережил родителя")
            self.assertTrue(Path(marker + ".soft").exists(),
                            "ребёнок ушёл не мягким путём: finally не отработал")

    def test_sigterm_is_a_soft_exit(self):
        # Тот же путь, что и у сторожа, только сигнал шлёт стенд: SystemExit,
        # а не мгновенная смерть без finally.
        with tempfile.TemporaryDirectory(prefix="helene-term-") as tmp:
            marker = str(Path(tmp) / "след")
            child = subprocess.Popen([sys.executable, "-c", CHILD, str(LOCALHARNESS), marker],
                                     env=dict(os.environ, HELENE_PARENT_PID=""))
            self.addCleanup(lambda: child.poll() is None and child.kill())
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not Path(marker + ".alive").exists():
                time.sleep(0.1)
            child.send_signal(signal.SIGTERM)
            code = child.wait(timeout=10)
            self.assertEqual(code, 128 + signal.SIGTERM)
            self.assertTrue(Path(marker + ".soft").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
