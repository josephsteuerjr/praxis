"""Сироты хоронятся, пока раннер жив, — и код выхода раннера при этом не теряется.

Второе важнее первого. `waitpid(-1)` снимает любого ребёнка, включая самого раннера,
а на его коде выхода висит вся логика отката: перепутать здесь значит откатывать
здоровый коммит или не откатывать сломанный.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap
import time
import unittest

import bootguard


@unittest.skipUnless(hasattr(os, "WNOHANG"), "зомби бывают только на POSIX")
class TheSupervisorBuriesOrphansWhileTheRunnerLives(unittest.TestCase):

    def setUp(self):
        self._spawned: list[subprocess.Popen] = []
        # Жнец снимает детей сам, и Popen об этом не знает: без этого его деструктор
        # сыплет ResourceWarning в чужой гейт. Шум в общем прогоне — тоже цена.
        self.addCleanup(self._quiet_spawned)

    def _quiet_spawned(self):
        for proc in self._spawned:
            if proc.returncode is None:
                proc.returncode = 0

    def _spawn(self, source: str) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", textwrap.dedent(source)])
        self._spawned.append(proc)
        return proc

    def test_the_runner_exit_code_survives_the_reaping(self):
        """Ради этого весь разбор: код выхода обязан дойти до решения об откате."""
        for expected in (0, 7, 42):
            with self.subTest(code=expected):
                proc = self._spawn(f"import sys; sys.exit({expected})")
                self.assertEqual(bootguard._wait_runner_reaping(proc, poll=0.01),
                                 expected)
                self.assertEqual(proc.returncode, expected,
                                 "Popen остался думать, что ребёнок жив")

    def test_an_orphan_is_reaped_before_the_runner_finishes(self):
        """Сирота — внук, чей родитель умер раньше. Он переезжает к нам, и мы обязаны
        его сжать НЕ ДОЖИДАЯСЬ конца раннера: именно этого и не делал прежний код."""
        # Внук живёт дольше своего родителя: родитель выходит сразу, внук — через миг.
        orphan_parent = self._spawn("""
            import subprocess, sys
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.2)"])
            sys.exit(0)
            """)
        orphan_parent.wait()
        # Раннер живёт дольше внука — значит сжать внука можно только во время ожидания.
        runner = self._spawn("import time; time.sleep(1.5); raise SystemExit(3)")
        code = bootguard._wait_runner_reaping(runner, poll=0.01)
        self.assertEqual(code, 3, "код выхода раннера потерян")
        # Ни одного несжатого ребёнка не осталось: следующий waitpid обязан сказать
        # «детей нет вовсе», а не отдать чей-то забытый статус.
        with self.assertRaises(ChildProcessError):
            os.waitpid(-1, os.WNOHANG)

    def test_a_runner_that_outlives_many_orphans_still_reports_its_code(self):
        for _ in range(5):
            self._spawn("import sys; sys.exit(0)")
        runner = self._spawn("import time; time.sleep(0.4); raise SystemExit(9)")
        self.assertEqual(bootguard._wait_runner_reaping(runner, poll=0.01), 9)
        with self.assertRaises(ChildProcessError):
            os.waitpid(-1, os.WNOHANG)

    def test_waiting_does_not_spin_the_processor(self):
        """Опрос обязан спать между кругами: PID 1 в горячем цикле — это та же беда,
        только другой стороной."""
        runner = self._spawn("import time; time.sleep(0.5)")
        started = time.process_time()
        bootguard._wait_runner_reaping(runner, poll=0.05)
        spent = time.process_time() - started
        self.assertLess(spent, 0.25, f"ожидание сожгло {spent:.2f} с процессорного времени")


class TheReaperIsStillCalledAfterTheRunnerDies(unittest.TestCase):
    """Прежний вызов `_reap_orphans()` после выхода раннера НЕ снят: он подбирает тех,
    кто осиротел в самый последний момент, и работает в ветках panic/preflight."""

    def test_the_old_hook_is_still_there(self):
        source = pathlib.Path(bootguard.__file__).read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("_reap_orphans()"), 2,
                                "жнец после выхода раннера пропал вместе с правкой")


if __name__ == "__main__":
    unittest.main()
