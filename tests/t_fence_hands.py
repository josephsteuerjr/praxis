# -*- coding: utf-8 -*-
"""Стенд «ограда поимённо»: правда о том, какая рука накрыта, а какая нет.

Запуск:  python tests/t_fence_hands.py

Что здесь проверяется и почему именно это. Дыра, ради которой отчёт заведён,
была не в ограде, а в РАССКАЗЕ о ней: `shell` вправду уходил в AppContainer, а
`run`/`run_tests`/`pip_install` исполнялись мимо — `workshop` держит свой
`import subprocess`, и подмена в модуле `agent` до него не достаёт. Владелец
видел одну строку «песочница: shell в контейнере» и читал её как «накрыто всё».

Поэтому стенд держит три вещи:
  1. приговор считается ИЗ РЕАЛЬНОСТИ — снял обёртку, и рука честно назвалась
     вне ограды; поставил шим в модуль руки — назвалась накрытой;
  2. умолчание fail-closed: рука, которой нет в карте, в отчёт не попадает, а
     рука из карты без шима и без обёртки называется вне ограды;
  3. карта не протухла в обе стороны: имя, которого в наборе рук больше нет,
     приезжает отдельным родом `unknown`, а не исчезает молча.

Ни AppContainer, ни один процесс здесь не поднимается: `hands_report` читает
только атрибуты реализаций и `sys.modules`.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))

import fence  # noqa: E402


#: Руки, машину не трогающие. В наборе их шестьдесят, здесь хватит двух: они
#: тут ради проверки, что отчёт их НЕ берёт.
QUIET = ("recall", "say")


def agent_with(*, without=()) -> types.SimpleNamespace:
    """Модуль агента с ПОЛНЫМ набором рук, минус названные.

    ⚠ Полный, а не «те, что нужны тесту». Отчёт называет `unknown` всякое имя из
    карты, которого в наборе нет, — и на огрызке из двух рук в `unknown`
    посыпалось бы всё остальное. Живой набор либо полон (дерево загрузилось),
    либо пуст (не загрузилось, и отчёт пуст целиком); середины нет, и стенд
    обязан стоять там же, где продукт.
    """
    names = [n for n in (*fence.MACHINE_HANDS, *QUIET) if n not in without]
    return types.SimpleNamespace(TOOL_IMPL={n: (lambda *a, **k: "") for n in names})


def verdict(rows, name: str) -> str:
    for row in rows:
        if row["name"] == name:
            return row["fence"]
    return "(нет в отчёте)"


def why(rows, name: str) -> str:
    for row in rows:
        if row["name"] == name:
            return row["why"]
    return ""


class Ground(unittest.TestCase):
    """Состояние ограды и `sys.modules` — глобальные; тест возвращает их как было.

    ⚠ `sys.modules` здесь не педантизм: отчёт спрашивает про шим ИМЕННО там
    (`_shim_in`), поэтому тесты кладут туда заглушки. Оставленная заглушка
    `agent` — это подмена настоящего модуля для всех, кто побежит следом в том
    же процессе, а прогон у продукта общий.
    """

    TOUCHED = ("agent", "workshop", "hands", "forge_process")

    def setUp(self):
        self._state = dict(fence.STATE)
        self._modules = {n: sys.modules.get(n) for n in self.TOUCHED}

    def tearDown(self):
        fence.STATE.clear()
        fence.STATE.update(self._state)
        for name, mod in self._modules.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


class Report(Ground):

    def test_a_hand_that_does_not_touch_the_machine_is_not_in_the_report(self):
        """`recall` и `say` в отчёт не попадают: ограде о них сказать нечего.

        Это не косметика. Отчёт читается глазами, и шестьдесят строк «—» утопили
        бы те семь, ради которых он заведён.
        """
        rows = fence.hands_report(agent_with())
        named = [r["name"] for r in rows]
        for quiet in QUIET:
            self.assertNotIn(quiet, named)
        self.assertIn("shell", named)
        self.assertNotIn("unknown", {r["fence"] for r in rows})

    def test_a_file_hand_is_fenced_only_while_the_wrapper_is_really_on_it(self):
        """Приговор снимается с ОБЁРТКИ, а не с имени руки."""
        agent = agent_with()
        rows = fence.hands_report(agent)
        self.assertEqual(verdict(rows, "fs_write"), "outside")

        agent.TOOL_IMPL["fs_write"]._helene_fenced = True
        rows = fence.hands_report(agent)
        self.assertEqual(verdict(rows, "fs_write"), "path")

    def test_shell_is_named_fenced_only_when_the_shim_and_the_container_are_both_up(self):
        """Два условия, и оба настоящие: шим в модуле руки И поднятый контейнер.

        Второе — не формальность: шим стоит ВСЕГДА (он же единственный декодер
        вывода), в том числе когда контейнер не поднялся. Сорвавшийся контейнер,
        названный оградой, — ровно та неправда, которую отчёт закрывает.
        """
        agent = agent_with()
        sys.modules["agent"] = agent

        agent.subprocess = object()
        fence.STATE["container"] = True
        self.assertEqual(verdict(fence.hands_report(agent), "shell"), "outside")

        agent.subprocess = fence._SubprocessShim(
            __import__("subprocess"), None, Path("."), Path("."))
        fence.STATE["container"] = True
        self.assertEqual(verdict(fence.hands_report(agent), "shell"), "container")

        fence.STATE["container"] = False
        fence.STATE["reason"] = "контейнер не поднялся: проба"
        rows = fence.hands_report(agent)
        self.assertEqual(verdict(rows, "shell"), "outside")
        self.assertIn("контейнер не поднялся", why(rows, "shell"))

    def test_workshop_hands_are_outside_and_the_reason_names_their_module(self):
        """Дыра, ради которой всё это: `run` исполняется НЕ в модуле `agent`.

        Отказ обязан называть причину устройством («команду исполняет workshop,
        шима ограды там нет»), а не общим «не огорожена»: по общему слову нельзя
        понять, чинится это ручкой в настройках или правкой продукта.
        """
        # Модуль ЗАГРУЖЕН и не огорожен — та самая раскладка, что жила в
        # продукте до 10.09. Выкинуть его из `sys.modules` было бы проверкой
        # другой ветки («не загружен»), и тест зеленел бы не о том.
        sys.modules["workshop"] = types.SimpleNamespace(subprocess=object())
        sys.modules.pop("hands", None)
        rows = fence.hands_report(agent_with())
        for name in ("run", "run_tests", "pip_install"):
            self.assertEqual(verdict(rows, name), "outside", name)
            self.assertIn("workshop", why(rows, name), name)

    def test_an_unfenced_floor_binary_keeps_the_workshop_hands_outside(self):
        """`workshop.run` сперва пробует «пол» (`hands.execute` → бинарь), и у
        `hands` свой `import subprocess`. Огородить один `workshop` и назвать
        руку накрытой значило бы оставить дыру и написать над ней «в контейнере».
        """
        shim = fence._SubprocessShim(__import__("subprocess"), None, Path("."), Path("."),
                                     route_all=True)
        sys.modules["workshop"] = types.SimpleNamespace(subprocess=shim)
        sys.modules["hands"] = types.SimpleNamespace(subprocess=object())
        fence.STATE["container"] = True
        rows = fence.hands_report(agent_with())
        self.assertEqual(verdict(rows, "run"), "outside")
        self.assertIn("hands", why(rows, "run"))

        sys.modules["hands"] = types.SimpleNamespace(subprocess=shim)
        rows = fence.hands_report(agent_with())
        self.assertEqual(verdict(rows, "run"), "container")

    def test_the_body_hand_is_outside_by_design_and_says_so(self):
        """`computer` вне ограды НЕ по недосмотру: тело живёт снаружи контейнера.

        Строка про него обязана отличаться от строки про `run` — иначе владелец
        прочитает границу как дыру и пойдёт чинить то, что чинить нечего.
        """
        rows = fence.hands_report(agent_with())
        self.assertEqual(verdict(rows, "computer"), "outside")
        self.assertIn("снаружи ограды", why(rows, "computer"))
        self.assertNotIn("шима ограды там нет", why(rows, "computer"))

    def test_a_hand_the_map_remembers_but_the_set_no_longer_offers_is_called_out(self):
        """Карта протухает и с этой стороны: имя, которого больше нет.

        Молча вычёркивать нельзя: тогда переименованная ею рука тихо исчезнет из
        отчёта, и «вне ограды» про неё никто больше не скажет.
        """
        rows = fence.hands_report(agent_with(without=("run",)))
        self.assertEqual(verdict(rows, "run"), "unknown")
        self.assertIn("нет", why(rows, "run"))

    def test_no_agent_module_means_an_empty_report_not_a_crash(self):
        """Снимок устройства пишется и до загрузки дерева — падать здесь нельзя."""
        self.assertEqual(fence.hands_report(None), [])
        self.assertEqual(fence.hands_report(types.SimpleNamespace()), [])


class Map(Ground):

    def test_every_named_hand_says_what_it_touches(self):
        """Пустое «трогает» выключило бы руку из отчёта совсем — тихо."""
        for name, (touches, module) in fence.MACHINE_HANDS.items():
            self.assertTrue(str(touches).strip(), name)
            self.assertTrue(module is None or str(module).strip(), name)

    def test_the_hands_that_run_commands_are_all_in_the_map(self):
        """Список исполняющих рук — тот же, что в шапке `fence.py` и в CHANGELOG.

        Если завтра руку добавят, а сюда не впишут, отчёт не соврёт (умолчание
        fail-closed — её просто не будет в списке трогающих машину), но и не
        скажет. Этот тест — то место, где о ней вспомнят.
        """
        running = {n for n, (touches, _) in fence.MACHINE_HANDS.items()
                   if "команд" in touches}
        self.assertEqual(
            running,
            {"shell", "run", "run_tests", "pip_install",
             "coding_run", "coding_process", "coding_agent", "coding_verify",
             "coding_swarm", "coding_checkpoint", "coding_session"})

    def test_the_whole_forge_family_is_accounted_for(self):
        """Forge — десять рук, и почти все трогают машину.

        Сверено 10.09 с её живым `TOOL_IMPL` (93 руки): семейство `coding_*`
        ровно такое. Умолчание fail-closed на нём не спасало — неразобранная
        рука в отчёт просто не попадает, то есть молчит; поэтому семейство
        перечислено целиком и держится этим тестом.
        """
        family = {n for n in fence.MACHINE_HANDS if n.startswith("coding_")}
        self.assertEqual(
            family,
            {"coding_run", "coding_process", "coding_agent", "coding_verify",
             "coding_swarm", "coding_checkpoint", "coding_session",
             "coding_edit", "coding_inspect", "coding_learn"})
        for name in family:
            self.assertIn(name, fence.OUTSIDE_BY_DESIGN, name)
            self.assertIn("worktree", fence.OUTSIDE_BY_DESIGN[name], name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
