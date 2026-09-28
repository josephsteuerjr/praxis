"""Модель, закреплённая владельцем (1.2.4).

28.09 агент Дмитрия К трижды сам уходил на gpt-6-sol «по просьбе хозяина», которой не было,
и вернуть рабочий мозг мог только человек правкой llm.json. Стенд держит: с галочкой
`model.pinned` смена модели и профиля агентом отказывает словами и оставляет строку в
журнале с её «зачем»; без галочки — проходит как раньше; снятая галочка действует сразу;
на старте проекция возвращает модель владельца, даже когда helene.json не менялся.
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "localharness"))
sys.path.insert(0, str(ROOT))

import brain_pin  # noqa: E402
from localharness import boot  # noqa: E402


def _fake_brain():
    mod = types.ModuleType("brain")
    mod.calls = []
    mod.journal = []
    mod.switch = lambda role, model, *, why="", by="praxis": mod.calls.append(("switch", role, model)) or {"ok": True}
    mod.apply_profile = lambda name, *, why="", by="praxis": mod.calls.append(("profile", name)) or {"ok": True}
    mod._journal = mod.journal.append
    return mod


class BrainPinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "helene.json"
        self.brain = _fake_brain()
        sys.modules["brain"] = self.brain

    def tearDown(self):
        sys.modules.pop("brain", None)
        self.tmp.cleanup()

    def _pin(self, value: bool):
        self.cfg.write_text(json.dumps({"model": {"model": "gpt-5", "pinned": value}}), encoding="utf-8")

    def test_pinned_model_refuses_the_agent_in_words(self):
        self._pin(True)
        self.assertTrue(brain_pin.install(self.cfg))
        out = self.brain.switch("voice", "gpt-6-sol", why="Owner request: stronger model")
        self.assertFalse(out["ok"])
        self.assertTrue(out["pinned"])
        self.assertIn("закреплена владельцем", out["error"])
        self.assertEqual(self.brain.calls, [])
        self.assertIn("Owner request", self.brain.journal[-1])
        self.assertFalse(self.brain.apply_profile("deep", why="x")["ok"])
        self.assertEqual(self.brain.calls, [])

    def test_unpinned_passes_and_unpinning_acts_at_once(self):
        self._pin(True)
        brain_pin.install(self.cfg)
        self.assertFalse(self.brain.switch("voice", "gpt-6-sol", why="x")["ok"])
        self._pin(False)
        self.assertTrue(self.brain.switch("voice", "gpt-6-sol", why="x")["ok"])
        self.assertEqual(self.brain.calls, [("switch", "voice", "gpt-6-sol")])

    def test_install_twice_wraps_once(self):
        self._pin(False)
        brain_pin.install(self.cfg)
        first = self.brain.switch
        brain_pin.install(self.cfg)
        self.assertIs(self.brain.switch, first)


class PinAtStartTest(unittest.TestCase):
    def test_projection_returns_the_owner_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "llm.json"
            built = {"roles": {"voice": {"framework": "openai", "model": "gpt-5"}}}
            target.write_text(json.dumps({"roles": {"voice": {"framework": "openai", "model": "gpt-6-sol"}}}),
                              encoding="utf-8")
            self.assertTrue(boot._pin_holds(target, {"model": {"pinned": False}}, built),
                            "без галочки её выбор не трогаем")
            self.assertFalse(boot._pin_holds(target, {"model": {"pinned": True}}, built),
                             "с галочкой ушедшая модель возвращается к выбору владельца")
            target.write_text(json.dumps({"roles": {"voice": {"framework": "openai", "model": "gpt-5"}}}),
                              encoding="utf-8")
            self.assertTrue(boot._pin_holds(target, {"model": {"pinned": True}}, built))


if __name__ == "__main__":
    unittest.main()
