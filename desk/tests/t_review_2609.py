# -*- coding: utf-8 -*-
"""Адверсарное ревью 26.09 — движок издания (W4 S3 и соседи).

* пауза фона (`appetite.background_hold`, рука «умерь фон») гасит ВХОЖДЕНИЕ повторяющегося
  намерения без хода модели — её правило с сервера; разовое пробуждение идёт как шло;
* без паузы повторяющееся намерение поднимает ход, как прежде.

Запуск:  python tests/t_review_2609.py
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "localharness")]
import runner  # noqa: E402


class FakeTasks:
    def __init__(self, due):
        self._due = list(due)
        self.fired: list[str] = []

    def due(self):
        return list(self._due)

    def after_run_holds(self):
        return []

    def mark_fired(self, task_id):
        self.fired.append(task_id)


class FakeAlarms:
    def __init__(self, tasks):
        self.tasks = tasks
        self.raised: list[str] = []

    def reconcile_claims(self):
        pass

    def fire(self, task, invoke):
        self.raised.append(task["id"])
        return True


class BackgroundHoldStopsScheduledWakes(unittest.TestCase):
    def run_tick(self, due, hold):
        tasks = FakeTasks(due)
        alarms = FakeAlarms(tasks)
        appetite = types.ModuleType("appetite")
        appetite.background_hold = lambda: hold
        with mock.patch.object(runner, "_desk", object()), \
                mock.patch.object(runner, "_life", object()), \
                mock.patch.object(runner, "_alarms", alarms), \
                mock.patch.object(runner, "_brain_ready", lambda: True), \
                mock.patch.object(runner, "_ALARM_FIRED", []), \
                mock.patch.dict(sys.modules, {"appetite": appetite}):
            runner._fire_due_tasks()
        return tasks, alarms

    def test_recurring_intent_on_pause_is_consumed_without_a_turn(self):
        tasks, alarms = self.run_tick(
            [{"id": "r1", "kind": "wake", "recur": "every 1h"}],
            hold="фон остановлен по слову владельца")
        self.assertEqual(alarms.raised, [], "хода модели нет")
        self.assertEqual(tasks.fired, ["r1"], "вхождение погашено — сдвинуто на следующее")

    def test_one_off_wake_on_pause_still_fires(self):
        tasks, alarms = self.run_tick(
            [{"id": "w1", "kind": "wake"}], hold="фон остановлен")
        self.assertEqual(alarms.raised, ["w1"])
        self.assertEqual(tasks.fired, [])

    def test_without_pause_recurring_intent_fires(self):
        tasks, alarms = self.run_tick(
            [{"id": "r2", "kind": "wake", "recur": "daily 09:00"}], hold=None)
        self.assertEqual(alarms.raised, ["r2"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main(verbosity=2)
