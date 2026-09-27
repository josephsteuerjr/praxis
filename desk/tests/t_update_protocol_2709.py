# -*- coding: utf-8 -*-
"""Стенд протокола обновления на сервере (27.09): `deskd/control.py`, раздел «обновление».

Запуск:  python tests/t_update_protocol_2709.py

Жалоба Дмитрия К: агент не мог обновить свой контейнер изнутри. Теперь агент или окно
кладут план, исполнитель снаружи сверяет его и ждёт «да» человека. Здесь проверяется
то, что может соврать на стороне канала и руки:
  * план — только из закрытого списка полей: версия, копия, проверки; мусор не проходит,
    обязательные проверки из плана не убрать;
  * без живого исполнителя план не ложится, и отказ говорит, как его поднять;
  * пока идёт обновление, второй план не ложится; ждущий «да» — заменяется;
  * «да» принимается только на расписку «жду подтверждения» и только с её ключом;
  * итог, о котором агент уже рассказал, второй раз не всплывает.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from deskd import control  # noqa: E402


def ctl(tree: Path) -> Path:
    return tree / "memory" / ".control"


def put(tree: Path, name: str, data: dict) -> None:
    path = ctl(tree) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def beat(tree: Path, *, ok: bool = True, age: float = 1.0, **extra) -> None:
    put(tree, control.UPDATER_BEAT, {"beat_epoch": time.time() - age, "ok": ok,
                                     "why": "" if ok else "не вижу установку",
                                     "current_version": "1.1.0",
                                     "latest": {"version": "1.1.1"}, **extra})


class PlanFields(unittest.TestCase):
    def test_чистый_план_и_обязательные_проверки(self):
        plan, why = control.validate_plan({"id": "ab12cd34ef", "version": "v1.2.3",
                                           "checks": ["brain"], "junk": "rm -rf /",
                                           "image": "evil:latest"})
        self.assertEqual(why, "")
        self.assertEqual(plan["version"], "1.2.3")
        self.assertEqual(plan["backup"], "full")
        for name in control.UPDATE_MANDATORY:
            self.assertIn(name, plan["checks"])
        self.assertIn("brain", plan["checks"])
        self.assertNotIn("junk", plan)
        self.assertNotIn("image", plan)

    def test_отказы_с_причиной(self):
        cases = [
            ({"version": "1.2.3"}, "id"),
            ({"id": "ab12cd34ef", "version": "../../etc"}, "номер"),
            ({"id": "ab12cd34ef", "backup": "none"}, "копия"),
            ({"id": "ab12cd34ef", "checks": ["docker exec"]}, "проверок нет"),
            ({"id": "ab12cd34ef", "checks": "running"}, "список"),
            ("не объект", "объект"),
        ]
        for raw, word in cases:
            plan, why = control.validate_plan(raw)
            self.assertIsNone(plan, raw)
            self.assertIn(word, why, raw)

    def test_латест_и_предел_ожидания(self):
        plan, _ = control.validate_plan({"id": "ab12cd34ef", "wait_min": 999})
        self.assertEqual(plan["version"], "latest")
        self.assertEqual(plan["wait_min"], control.UPDATE_WAIT_MIN[1])

    def test_версии(self):
        self.assertEqual(control.version_tuple("v1.10.2"), (1, 10, 2))
        self.assertIsNone(control.version_tuple("1.1"))
        self.assertIsNone(control.version_tuple("latest"))
        self.assertGreater(control.version_tuple("1.10.0"), control.version_tuple("1.9.9"))


class PlanAndConfirm(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="helene-update-")
        self.addCleanup(self._tmp.cleanup)
        self.tree = Path(self._tmp.name)

    def test_без_исполнителя_план_не_ложится_и_сказано_как_поднять(self):
        got = control.update_plan(self.tree, "1.1.1")
        self.assertFalse(got["ok"])
        self.assertIn(control.UPDATER_COMMAND, got["note"])
        self.assertFalse((ctl(self.tree) / control.UPDATE_PLAN).exists())

    def test_молчащий_и_больной_исполнитель(self):
        beat(self.tree, age=120)
        got = control.update_plan(self.tree)
        self.assertFalse(got["ok"])
        self.assertIn("молчит", got["note"])
        beat(self.tree, ok=False)
        got = control.update_plan(self.tree)
        self.assertFalse(got["ok"])
        self.assertIn("не вижу установку", got["note"])

    def test_план_ложится_и_состояние_видно(self):
        beat(self.tree)
        got = control.update_plan(self.tree, "1.1.1", "code", "починка голоса", by="agent",
                                  chat="Людмила (123)")
        self.assertTrue(got["ok"], got)
        plan = json.loads((ctl(self.tree) / control.UPDATE_PLAN).read_text("utf-8"))
        self.assertEqual((plan["version"], plan["backup"], plan["asked_by"]), ("1.1.1", "code", "agent"))
        self.assertEqual(plan["chat"], "Людмила (123)")
        state = control.update_state(self.tree)
        self.assertTrue(state["updater"]["ok"])
        self.assertTrue(state["updater"]["newer"])
        self.assertEqual(state["plan"]["id"], plan["id"])

    def test_пока_идёт_второй_план_не_ложится(self):
        beat(self.tree)
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "running",
                                                "step": "собираю образ"})
        got = control.update_plan(self.tree)
        self.assertFalse(got["ok"])
        self.assertIn("собираю образ", got["note"])
        # ждущий «да» — заменяется новым планом (исполнитель пометит прежний «заменён»)
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "awaiting"})
        self.assertTrue(control.update_plan(self.tree)["ok"])

    def test_да_только_на_показанную_расписку_и_с_её_ключом(self):
        got = control.update_confirm(self.tree, "aaaa1111", "k", "yes")
        self.assertFalse(got["ok"])
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "awaiting",
                                                "nonce": "n0nce", "from_version": "1.1.0",
                                                "to_version": "1.1.1"})
        self.assertIn("сменился", control.update_confirm(self.tree, "bbbb2222", "n0nce", "yes")["note"])
        self.assertIn("ключ", control.update_confirm(self.tree, "aaaa1111", "чужой", "yes")["note"])
        self.assertFalse(control.update_confirm(self.tree, "aaaa1111", "n0nce", "может быть")["ok"])
        self.assertFalse((ctl(self.tree) / control.UPDATE_CONFIRM).exists())
        got = control.update_confirm(self.tree, "aaaa1111", "n0nce", "yes", by="window",
                                     words="  да,   обновляй ")
        self.assertTrue(got["ok"])
        self.assertIn("1.1.0 → 1.1.1", got["note"])
        confirm = json.loads((ctl(self.tree) / control.UPDATE_CONFIRM).read_text("utf-8"))
        self.assertEqual((confirm["decision"], confirm["by"], confirm["words"]),
                         ("yes", "window", "да, обновляй"))

    def test_итог_рассказывается_один_раз(self):
        self.assertIsNone(control.update_unreported(self.tree))
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "running"})
        self.assertIsNone(control.update_unreported(self.tree))
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "done",
                                                "finished_epoch": time.time()})
        self.assertEqual(control.update_unreported(self.tree)["id"], "aaaa1111")
        control.update_mark_reported(self.tree, "aaaa1111")
        self.assertIsNone(control.update_unreported(self.tree))
        # давний итог не всплывает никогда
        put(self.tree, control.UPDATE_RECEIPT, {"id": "bbbb2222", "state": "rolled_back",
                                                "finished_epoch": time.time() - 10 * 86400})
        self.assertIsNone(control.update_unreported(self.tree))
        # отказ плана — не событие для отчёта (агент видит его в руке сразу)
        put(self.tree, control.UPDATE_RECEIPT, {"id": "cccc3333", "state": "refused",
                                                "finished_epoch": time.time()})
        self.assertIsNone(control.update_unreported(self.tree))

    def test_срок_испытания(self):
        plan, _ = control.validate_plan({"id": "ab12cd34ef"})
        self.assertEqual(plan["trial_min"], control.UPDATE_TRIAL_DEFAULT)
        plan, _ = control.validate_plan({"id": "ab12cd34ef", "trial_min": 1})
        self.assertEqual(plan["trial_min"], control.UPDATE_TRIAL_MIN[0])
        beat(self.tree)
        self.assertEqual(control.update_plan(self.tree, trial_min=45)["plan"]["trial_min"], 45)

    def test_слово_на_испытании(self):
        self.assertIn("испытания сейчас нет",
                      control.update_verdict(self.tree, "aaaa1111", "k", "accept")["note"])
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "trial",
                                                "trial": {"key": "k3y"}, "from_version": "1.1.1",
                                                "to_version": "1.1.2"})
        self.assertFalse(control.update_verdict(self.tree, "aaaa1111", "k3y", "может быть")["ok"])
        self.assertIn("другой план", control.update_verdict(self.tree, "bbbb2222", "k3y", "accept")["note"])
        self.assertIn("ключ", control.update_verdict(self.tree, "aaaa1111", "чужой", "accept")["note"])
        self.assertFalse((ctl(self.tree) / control.UPDATE_VERDICT).exists())
        got = control.update_verdict(self.tree, "aaaa1111", "k3y", "reject", by="agent",
                                     words="  рука shell  молчит ")
        self.assertTrue(got["ok"])
        self.assertIn("1.1.1", got["note"])
        self.assertIn("память", got["note"])
        row = json.loads((ctl(self.tree) / control.UPDATE_VERDICT).read_text("utf-8"))
        self.assertEqual((row["verdict"], row["by"], row["words"]), ("reject", "agent", "рука shell молчит"))
        # во время испытания второй план не ложится
        beat(self.tree)
        self.assertIn("идёт другое обновление", control.update_plan(self.tree)["note"])

    def test_слово_сказано_испытания_больше_нет(self):
        # исполнитель взял слово: расписка уходит из испытания ДО отката (ревью 27.09) —
        # «Принять» посреди отката записалось бы ложным «прошло»
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "running", "phase": "rollback",
                                                "step": "откатываю", "trial": {"key": "k3y"}})
        got = control.update_verdict(self.tree, "aaaa1111", "k3y", "accept", by="window")
        self.assertFalse(got["ok"])
        self.assertIn("откатываю", got["note"])
        # расписка исполнителя прежней версии: «trial», но фаза уже не испытание
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "trial", "phase": "rollback",
                                                "trial": {"key": "k3y"}})
        self.assertFalse(control.update_verdict(self.tree, "aaaa1111", "k3y", "accept")["ok"])
        self.assertFalse((ctl(self.tree) / control.UPDATE_VERDICT).exists())

    def test_согласие_вместе_с_планом(self):
        beat(self.tree)
        got = control.update_plan(self.tree, "1.1.1", consent="window")
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["plan"]["consent"], "window")
        self.assertIn("начнёт сам", got["note"])
        # согласия не из закрытого списка не бывает; слова без согласия не едут
        plan, _ = control.validate_plan({"id": "ab12cd34ef", "consent": "agent", "consent_words": "да"})
        self.assertEqual((plan["consent"], plan["consent_words"]), ("", ""))
        plan, _ = control.validate_plan({"id": "ab12cd34ef", "consent": "OWNER-WORDS",
                                         "consent_words": "  обновись \n сейчас "})
        self.assertEqual((plan["consent"], plan["consent_words"]), ("owner-words", "обновись сейчас"))
        self.assertEqual(control.UPDATER_COMMAND, "sh server/install.sh")

    def test_несостоявшийся_ход_по_записке_повторяется(self):
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "done",
                                                "finished_epoch": time.time()})
        control.update_mark_reported(self.tree, "aaaa1111", "done", done=False, tries=1, noted=True)
        self.assertEqual(control.update_unreported(self.tree)["id"], "aaaa1111")
        self.assertEqual(control.update_report_mark(self.tree)["tries"], 1)
        control.update_mark_reported(self.tree, "aaaa1111", "done", done=True, tries=2)
        self.assertIsNone(control.update_unreported(self.tree))
        # отметка прежнего вида (без done) — «рассказано»
        put(self.tree, control.UPDATE_REPORTED, {"id": "aaaa1111", "state": "done"})
        self.assertIsNone(control.update_unreported(self.tree))

    def test_испытание_и_итог_рассказываются_по_отдельности(self):
        now = time.time()
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "trial",
                                                "trial": {"key": "k", "since_epoch": now}})
        self.assertEqual(control.update_unreported(self.tree)["state"], "trial")
        control.update_mark_reported(self.tree, "aaaa1111", "trial")
        self.assertIsNone(control.update_unreported(self.tree))
        put(self.tree, control.UPDATE_RECEIPT, {"id": "aaaa1111", "state": "rolled_back",
                                                "finished_epoch": now,
                                                "trial": {"verdict": {"verdict": "timeout"}}})
        self.assertEqual(control.update_unreported(self.tree)["state"], "rolled_back")
        # итог «принято», сказанный самим агентом, второй раз не рассказывается
        put(self.tree, control.UPDATE_RECEIPT, {"id": "cccc3333", "state": "done", "finished_epoch": now,
                                                "trial": {"verdict": {"verdict": "accept", "by": "agent"}}})
        self.assertIsNone(control.update_unreported(self.tree))
        put(self.tree, control.UPDATE_RECEIPT, {"id": "dddd4444", "state": "done", "finished_epoch": now,
                                                "trial": {"verdict": {"verdict": "accept", "by": "window"}}})
        self.assertEqual(control.update_unreported(self.tree)["id"], "dddd4444")

    def test_история_читается_с_хвоста(self):
        path = ctl(self.tree) / control.UPDATE_HISTORY
        path.parent.mkdir(parents=True)
        path.write_text("\n".join(json.dumps({"id": str(i), "state": "done"}) for i in range(9))
                        + "\nне json\n", encoding="utf-8")
        rows = control.update_history(self.tree, limit=3)
        self.assertEqual([r["id"] for r in rows], ["7", "8"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
