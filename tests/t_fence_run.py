# -*- coding: utf-8 -*-
"""Стенд «исполняющие руки в ограде»: куда на самом деле уходит команда.

Запуск:  python tests/t_fence_run.py

Дыра, которую этот стенд стережёт, держалась не на замысле, а на арифметике
пространств имён: шим ограды подменял `subprocess` в модуле `agent`, а
`run`/`run_tests`/`pip_install` живут в `workshop`, у которого СВОЙ
`import subprocess`. Подмена в чужом модуле до них не доставала никогда — при
том что снимок устройства бодро писал «shell в AppContainer».

Поэтому проверяется не «есть ли шим», а маршрут:
  * что уходит в контейнер, а что мимо, и по какому признаку;
  * что рабочая папка вызывающего сохраняется (иначе `run` выполнит команду не
    в папке проекта, а в workspace — тихо и не там);
  * что сорвавшийся контейнер немедленно правит STATE, а не оставляет над
    неогороженной командой слово «AppContainer».

AppContainer здесь не поднимается: `Container` подменён счётчиком вызовов.
"""
from __future__ import annotations

import subprocess as real_subprocess
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import fence  # noqa: E402


class FakeContainer:
    """Контейнер, который ничего не запускает и всё запоминает."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[list[str], str, float]] = []
        self.fail = fail

    def run(self, argv, cwd, timeout):
        if self.fail:
            raise OSError("проба: CreateProcess не удался")
        self.calls.append(([str(a) for a in argv], str(cwd), timeout))
        return "вывод", 0, False


def shim(container, *, route_all: bool, root="C:/Helene", workspace="C:/Helene/data/workspace"):
    return fence._SubprocessShim(real_subprocess, container, Path(workspace),
                                 Path(root), route_all=route_all)


class Ground(unittest.TestCase):

    def setUp(self):
        self._state = dict(fence.STATE)
        self._workshop = sys.modules.get("workshop")

    def tearDown(self):
        fence.STATE.clear()
        fence.STATE.update(self._state)
        if self._workshop is None:
            sys.modules.pop("workshop", None)
        else:
            sys.modules["workshop"] = self._workshop


class AgentShim(Ground):
    """Модуль агента: в контейнер уходит рука `shell`, и только она."""

    def test_the_shell_triple_goes_into_the_container_from_the_workspace(self):
        c = FakeContainer()
        done = shim(c, route_all=False).run(["bash", "-lc", "echo привет"], timeout=7)
        self.assertEqual(len(c.calls), 1)
        argv, cwd, timeout = c.calls[0]
        self.assertEqual(argv[1:], ["-lc", "echo привет"])
        self.assertTrue(cwd.replace("\\", "/").endswith("data/workspace"))
        self.assertEqual(timeout, 7.0)
        self.assertEqual(done.stdout, "вывод")

    def test_anything_else_from_the_agent_module_stays_out_of_the_container(self):
        """Шире здесь нельзя: тем же `subprocess` модуль агента поднимает своё
        хозяйство, и увести его в AppContainer значило бы чинить дыру, ломая
        продукт."""
        c = FakeContainer()
        done = shim(c, route_all=False).run(
            [sys.executable, "-c", "print(42)"], capture_output=True, timeout=30)
        self.assertEqual(c.calls, [])
        # Насквозь — значит НАСКВОЗЬ: и вывод приезжает такой же, как у
        # настоящего subprocess (байты, раз `text` не просили). Свой декодер шим
        # ставит только на том пути, который сам и ведёт.
        self.assertIn(b"42", done.stdout)


class WorkshopShim(Ground):
    """Мастерская: в контейнер уходит ВСЁ, что она исполняет."""

    def test_a_shell_string_keeps_its_own_interpreter_in_the_container(self):
        """`run` зовёт строкой при shell=True — прежний шим такой вызов не узнавал.

        ⚠ И интерпретатор обязан остаться ТОТ ЖЕ. На Windows `shell=True` — это
        `cmd.exe /c`; завернув команду в busybox bash, ограда починила бы дыру и
        заодно поменяла язык, на котором агент эту команду написал. Дело ограды —
        где команда исполняется, а не что она значит.
        """
        import os
        c = FakeContainer()
        shim(c, route_all=True).run("pytest -q", shell=True,
                                    cwd="C:/Helene/data/workspace/projects/сайт",
                                    capture_output=True, text=True, timeout=120)
        self.assertEqual(len(c.calls), 1)
        argv, cwd, timeout = c.calls[0]
        self.assertEqual(argv[1:], ["/c", "pytest -q"] if os.name == "nt"
                         else ["-c", "pytest -q"])
        self.assertNotIn("bash", Path(argv[0]).stem.lower())
        self.assertEqual(timeout, 120.0)

    def test_the_shell_hand_still_speaks_bash(self):
        """Рука `shell` как была: busybox поставки, тройка `-lc`."""
        c = FakeContainer()
        shim(c, route_all=False).run(["bash", "-lc", "ls -la /app"], timeout=30)
        self.assertEqual(c.calls[0][0][1:], ["-lc", "ls -la /app"])

    def test_the_working_directory_of_the_caller_is_kept(self):
        """Папка проекта, а не workspace.

        Не косметика: `run` работает В ПРОЕКТЕ мастерской, и подмена рабочей
        папки выполнила бы команду агента не там, где он её задумал, — молча и
        с правдоподобным выводом.
        """
        c = FakeContainer()
        shim(c, route_all=True).run("ls", shell=True,
                                    cwd="C:/Helene/data/workspace/projects/сайт", timeout=10)
        self.assertTrue(c.calls[0][1].replace("\\", "/").endswith("projects/сайт"))

    def test_an_argv_list_goes_into_the_container_too(self):
        """`run_tests` и `pip_install` зовут списком, без shell."""
        c = FakeContainer()
        shim(c, route_all=True).run(
            ["C:/p/.venv/Scripts/python.exe", "-m", "pytest", "-q"],
            cwd="C:/Helene/data/workspace/projects/сайт", capture_output=True, timeout=120)
        argv, cwd, _ = c.calls[0]
        self.assertEqual(argv, ["C:/p/.venv/Scripts/python.exe", "-m", "pytest", "-q"])
        self.assertTrue(cwd.replace("\\", "/").endswith("projects/сайт"))

    def test_without_a_cwd_the_command_runs_in_the_home(self):
        c = FakeContainer()
        shim(c, route_all=True).run(["git", "status"], timeout=60)
        self.assertTrue(c.calls[0][1].replace("\\", "/").endswith("data/workspace"))


class WhenTheContainerIsNotThere(Ground):

    def test_no_container_means_a_plain_run_not_a_refusal(self):
        """Ограда не поднялась — руки обязаны работать, а не отказывать."""
        done = shim(None, route_all=True).run(
            [sys.executable, "-c", "print(7)"], capture_output=True, timeout=30)
        self.assertIn("7", done.stdout)

    def test_a_container_that_fails_to_start_the_command_corrects_the_state_at_once(self):
        """Иначе анатомия продолжила бы писать «AppContainer» над голой командой."""
        fence.STATE["container"] = True
        fence.STATE["reason"] = "shell в AppContainer S-1-15-2-…"
        done = shim(FakeContainer(fail=True), route_all=True).run(
            [sys.executable, "-c", "print(9)"], capture_output=True, timeout=30)
        self.assertIn("9", done.stdout)
        self.assertFalse(fence.STATE["container"])
        self.assertIn("контейнер не запустил команду", fence.STATE["reason"])


class Installed(Ground):
    """Шим доезжает до ЖИВОГО модуля мастерской, а не до второй его копии."""

    def test_the_shim_lands_on_the_module_that_is_already_loaded(self):
        loaded = types.SimpleNamespace(subprocess=real_subprocess)
        sys.modules["workshop"] = loaded
        fence._shim_workshop(FakeContainer(), Path("C:/H/data/workspace"), Path("C:/H"))
        self.assertIsInstance(loaded.subprocess, fence._SubprocessShim)
        self.assertTrue(loaded.subprocess._route_all)

    def test_a_second_install_does_not_wrap_the_shim_in_a_shim(self):
        loaded = types.SimpleNamespace(subprocess=real_subprocess)
        sys.modules["workshop"] = loaded
        fence._shim_workshop(FakeContainer(), Path("C:/H/data/workspace"), Path("C:/H"))
        first = loaded.subprocess
        fence._shim_workshop(FakeContainer(), Path("C:/H/data/workspace"), Path("C:/H"))
        self.assertIs(loaded.subprocess, first)

    def test_no_workshop_module_is_not_a_crash(self):
        """Дерево могло не загрузиться — ограда обязана встать всё равно."""
        sys.modules.pop("workshop", None)
        fence._shim_workshop(FakeContainer(), Path("C:/H/data/workspace"), Path("C:/H"))

    def test_installing_the_fence_really_reaches_the_workshop(self):
        """Проводка, а не только деталь.

        Ступень чинится в `_shim_workshop`, но пользы от неё ноль, если
        `fence.install` туда не заходит. Ограда здесь выключена намеренно: шим
        обязан вставать и без контейнера — он же единственный декодер вывода, и
        руки не должны менять поведение от того, поднялся AppContainer или нет.
        """
        import tempfile

        loaded = types.SimpleNamespace(subprocess=real_subprocess)
        sys.modules["workshop"] = loaded
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "data"
            (tree / "workspace").mkdir(parents=True)
            agent = types.SimpleNamespace(TOOL_IMPL={}, BASE_TOOLS=[], BASE=str(tree),
                                          subprocess=real_subprocess)
            fence.install(agent, tree, {"agent_mode": "interactive",
                                        "sandbox": {"enabled": False}})
        self.assertIsInstance(loaded.subprocess, fence._SubprocessShim)
        self.assertTrue(loaded.subprocess._route_all)

    def test_the_report_calls_the_workshop_hands_fenced_only_after_the_shim(self):
        """Связка ступеней: отчёт обязан меняться вместе с маршрутом."""
        agent = types.SimpleNamespace(
            TOOL_IMPL={n: (lambda *a, **k: "") for n in fence.MACHINE_HANDS})
        sys.modules["workshop"] = types.SimpleNamespace(subprocess=real_subprocess)
        fence.STATE["container"] = True
        before = {r["name"]: r["fence"] for r in fence.hands_report(agent)}
        self.assertEqual(before["run"], "outside")

        fence._shim_workshop(FakeContainer(), Path("C:/H/data/workspace"), Path("C:/H"))
        after = {r["name"]: r["fence"] for r in fence.hands_report(agent)}
        self.assertEqual(after["run"], "container")
        self.assertEqual(after["pip_install"], "container")

    def test_run_tests_carries_the_half_that_is_still_outside(self):
        """`run_tests("self")` идёт через selfdev в worktree предложения, а он
        контейнеру на запись не выдан. «В ограде» без оговорки было бы неправдой."""
        agent = types.SimpleNamespace(
            TOOL_IMPL={n: (lambda *a, **k: "") for n in fence.MACHINE_HANDS})
        sys.modules["workshop"] = types.SimpleNamespace(subprocess=real_subprocess)
        fence._shim_workshop(FakeContainer(), Path("C:/H/data/workspace"), Path("C:/H"))
        fence.STATE["container"] = True
        why = {r["name"]: r["why"] for r in fence.hands_report(agent)}["run_tests"]
        self.assertIn("selfdev", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
