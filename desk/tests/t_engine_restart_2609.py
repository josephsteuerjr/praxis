# -*- coding: utf-8 -*-
"""Стенд «перезапуск движка по просьбе владельца» (26.09).

Запуск:  python tests/t_engine_restart_2609.py

Живой случай: у мамы Егора голос включился только после ручного перезапуска всей
программы, а под службой кнопка «Перезапустить» окна была нулевым действием — окно
детей службы не держит. Теперь движок сам бьётся в записке надзора (`deskd/control.py`),
берёт просьбу `/api/supervisor/restart` и выходит между ходами кодом 42; поднимает его
тот, кто держит, — окно или служба.

Проверяется то, что здесь может соврать:
  * пока движок бьётся, окно видит «управление доступно» — иначе просьба не ляжет;
  * просьба «runner»/«all» взводит выход и получает расписку «сделано»;
  * «relay» движок не берёт — расписка честно говорит, кто держит реле;
  * приоритет процесса на время расшифровки — ниже обычного и возвращается назад;
  * потоков whisper — не больше «ядер минус одно».
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness")]
from deskd import control  # noqa: E402
import runner  # noqa: E402
import voice  # noqa: E402


class EngineAnswersTheRestartRequest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="helene-restart-")
        self.addCleanup(self._tmp.cleanup)
        self.tree = Path(self._tmp.name)
        runner._restart_wanted[0] = False
        self.addCleanup(lambda: runner._restart_wanted.__setitem__(0, False))

    def test_beat_makes_control_available_and_names_the_engine(self):
        self.assertFalse(control.supervisor_state(self.tree)["control"]["available"])
        self.assertFalse(runner._supervisor_tick(self.tree, control))
        state = control.supervisor_state(self.tree)
        self.assertTrue(state["control"]["available"], state["control"])
        self.assertEqual(state["kind"], "engine")
        self.assertEqual(state["children"][0]["role"], "runner")

    def test_restart_all_arms_the_exit_and_leaves_a_receipt(self):
        runner._supervisor_tick(self.tree, control)      # записка — иначе просьба не ляжет
        asked = control.ask(self.tree, "all")
        self.assertTrue(asked["ok"], asked)
        self.assertTrue(runner._supervisor_tick(self.tree, control))
        self.assertTrue(runner._restart_wanted[0])
        receipt = control.supervisor_state(self.tree)["receipt"]
        self.assertTrue(receipt["done"])
        self.assertEqual(receipt["id"], asked["request"]["id"])
        self.assertIn("между ходами", receipt["note"])
        # Просьба снята со стола: второй тик её не найдёт.
        self.assertIsNone(control.supervisor_state(self.tree)["pending"])

    def test_relay_is_not_the_engines_to_restart(self):
        runner._supervisor_tick(self.tree, control)
        control.ask(self.tree, "relay")
        self.assertFalse(runner._supervisor_tick(self.tree, control))
        self.assertFalse(runner._restart_wanted[0])
        receipt = control.supervisor_state(self.tree)["receipt"]
        self.assertFalse(receipt["done"])
        self.assertIn("реле", receipt["note"])

    def test_exit_code_is_the_one_the_supervisors_know(self):
        self.assertEqual(runner.RESTART_EXIT_CODE, 42)
        for crate in ("shell", "svc"):
            source = (DESK / crate / "src" / "main.rs").read_text(encoding="utf-8")
            self.assertIn("const RESTART_EXIT: i32 = 42;", source, crate)


class TranscriptionYieldsTheProcessor(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "приоритет процесса — только Windows")
    def test_priority_drops_inside_and_comes_back(self):
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
        kernel.GetPriorityClass.restype = ctypes.c_uint32
        handle = kernel.GetCurrentProcess()
        before = kernel.GetPriorityClass(handle)
        self.assertNotEqual(before, 0)
        with runner._low_priority():
            self.assertEqual(kernel.GetPriorityClass(handle), 0x4000)
            with runner._low_priority():                 # вложенный вход не возвращает раньше времени
                self.assertEqual(kernel.GetPriorityClass(handle), 0x4000)
            self.assertEqual(kernel.GetPriorityClass(handle), 0x4000)
        self.assertEqual(kernel.GetPriorityClass(handle), before)

    def test_whisper_leaves_a_core_to_the_owner(self):
        ready = {"ready": True, "installed": {"path": "C:/models/turbo"}, "why": ""}
        original = voice.state
        voice.state = lambda tree, cfg: ready
        self.addCleanup(lambda: setattr(voice, "state", original))
        cores = os.cpu_count() or 4
        env = voice.env_for(Path("."), {"voice": {"threads": 64}})
        self.assertEqual(int(env["PRAXIS_STT_CPU_THREADS"]), max(1, cores - 1))
        env = voice.env_for(Path("."), {"voice": {"threads": 1}})
        self.assertEqual(env["PRAXIS_STT_CPU_THREADS"], "1")


if __name__ == "__main__":
    unittest.main()
