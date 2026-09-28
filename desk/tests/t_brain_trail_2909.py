# -*- coding: utf-8 -*-
"""Модель в кадре и кто её менял (1.2.5, слово Егора 29.09: «провенанс нарушен»).

Джарвис на ПК Егора: лимит подписки кончился, Егор переключил модель в Настройках на
glm-5.3 — а агент модели своей в кадре не видел и решил, что «конфиг стёрло обновление»,
дважды спросив, вернуть ли прежнюю. Теперь каждая смена голоса ложится в след с тем, кто
её сделал, а в кадре каждого хода — на чём агент сейчас и кто менял последним.

Запуск:  python tests/t_brain_trail_2909.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))

import brain_trail as bt  # noqa: E402


def llm(tree: Path, model: str, host: str = "https://api.z.ai/api/anthropic", fw: str = "anthropic"):
    path = tree / "memory" / "llm.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"frameworks": {fw: {"base_url": host, "api_key": "SECRET-KEY"}},
                                "roles": {"voice": {"framework": fw, "model": model}}}), "utf-8")


class Trail(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="brain-trail-"))
        self.install = self.tmp / "Helene"
        self.tree = self.install / "data"
        self.cfg_path = self.install / "helene.json"
        self.install.mkdir()
        self.cfg_path.write_text("{}", "utf-8")
        llm(self.tree, "gpt-6-astra", "http://127.0.0.1:5011", "openai")
        bt.reconcile(self.tree)                     # точка отсчёта

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def owner_switches_in_settings(self, model="glm-5.3"):
        time.sleep(0.02)
        self.cfg_path.write_text(json.dumps({"model": {"model": model}}), "utf-8")
        return bt.project(self.tree, self.cfg_path, {}, lambda tree, cfg: llm(tree, model) or "проекция")

    def test_the_frame_names_the_model_and_the_owner_who_changed_it(self):
        self.owner_switches_in_settings()
        line = bt.orient_line(self.tree)
        self.assertIn("Твой мозг сейчас: glm-5.3 (z.ai, протокол anthropic)", line)
        self.assertIn("владелец в Настройках", line)
        self.assertIn("было gpt-6-astra", line)
        self.assertNotIn("SECRET", json.dumps(bt.snapshot(self.tree)) + line)

    def test_the_installer_is_named_when_it_wrote_the_settings(self):
        marker = self.install / bt.INSTALL_MARKER
        marker.write_text(json.dumps({"version": "1.2.5"}), "utf-8")
        self.owner_switches_in_settings()
        self.assertIn("установщик при обновлении до 1.2.5", bt.last(self.tree)["by"])

    def test_the_agents_own_switch_carries_its_why(self):
        def switch_brain(action="switch", role="voice", model="", why=""):
            llm(self.tree, model, "http://127.0.0.1:5011", "openai")
            return "рукопожатие прошло"
        agent = types.SimpleNamespace(TOOL_IMPL={"switch_brain": switch_brain})
        self.assertTrue(bt.install_switch_hook(agent, self.tree))
        agent.TOOL_IMPL["switch_brain"](action="switch", model="gpt-6-sol", why="Егор попросил sol")
        row = bt.last(self.tree)
        self.assertEqual((row["by"], row["to"]["model"], row["why"]), (bt.BY_AGENT, "gpt-6-sol", "Егор попросил sol"))
        self.assertIn("подписка через реле", bt.orient_line(self.tree))

    def test_a_change_by_no_known_path_is_named_so(self):
        llm(self.tree, "glm-5.3")                   # llm.json правили руками или shell
        self.assertIn("никто из известных путей", bt.orient_line(self.tree))

    def test_nothing_changed_nothing_written(self):
        bt.project(self.tree, self.cfg_path, {}, lambda tree, cfg: "не трогаю")
        rows = (self.tree / "memory" / ".state" / "brain-trail.jsonl").read_text("utf-8").splitlines()
        self.assertEqual(len(rows), 1, "только точка отсчёта")
        self.assertNotIn("Последняя смена", bt.orient_line(self.tree))

    def test_an_old_change_is_not_repeated_forever(self):
        self.owner_switches_in_settings()
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=bt.FRESH_DAYS + 1)
        line = bt.orient_line(self.tree, now=later)
        self.assertIn("glm-5.3", line)
        self.assertNotIn("Последняя смена", line)

    def test_pinned_start_is_the_pin_not_the_owner(self):
        # helene.json не менялся с прошлой проекции, но закрепление вернуло выбор владельца
        receipt = self.tree / "memory" / ".state" / "brain_projection.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text("{}", "utf-8")
        future = time.time() + 5
        os.utime(receipt, (future, future))
        bt.project(self.tree, self.cfg_path, {"model": {"pinned": True}},
                   lambda tree, cfg: llm(tree, "glm-5.3") or "проекция")
        self.assertEqual(bt.last(self.tree)["by"], bt.BY_PIN)


class Wiring(unittest.TestCase):
    def test_runner_puts_the_line_into_every_turn_and_projects_with_the_trail(self):
        src = (DESK / "localharness" / "runner.py").read_text("utf-8")
        self.assertIn("brain_trail.orient_line(_tree)", src)
        self.assertEqual(src.count("brain_trail.project(tree, config_path, cfg, boot.project_brain)"), 2)
        self.assertIn("brain_trail.install_switch_hook(agent, tree)", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
