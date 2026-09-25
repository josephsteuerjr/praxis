# -*- coding: utf-8 -*-
"""Расширения владельца (25.09, поток K): манифест, версия API, загрузка, крючки, репетиция.

Инварианты (страница СВОИ-ТУЛЫ-ПЕРЕЖИВАЮТ-ОБНОВЛЕНИЕ-25.09, одобрена Егором 25.09):
  * major API равен, объявленный minor — минимум; без поля api — отказ словами;
  * отпечаток поверхности API записан и совпадает с живым (иначе — поднять версию);
  * тул расширения встаёт в список рук агента с пометкой; конфликт имени → namespace;
  * необъявленная capability, поздняя регистрация, переполненный бюджет — отказ словами;
  * сбой одного расширения не роняет остальные; крючок, упавший в ходе, не рвёт ход;
  * снимок состояния пишется; строка ориентира называет выключенное расширение;
  * репетиция (`check`) не трогает живой реестр и не требует агента;
  * requires.helene сверяется с версией программы.

Запуск:  python tests/t_extensions.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "localharness"))
import extensions  # noqa: E402


def fake_agent() -> types.ModuleType:
    mod = types.ModuleType("agent")
    mod.BASE_TOOLS = [{"name": "reply", "description": "ответить", "input_schema": {"type": "object"}}]
    mod.SHARED_CONTEXT_TOOLS = []
    mod.OWNER_TOOLS = []
    mod.PRAXIS_SELF_TOOLS = []
    mod.TOOL_IMPL = {"reply": lambda **kw: "ok"}
    mod.HAND_PURPOSE = {"reply": "ответить"}
    mod.journal_calls = []
    mod.tool_journal = lambda text, salience=2: mod.journal_calls.append(text) or "Записано."
    return mod


def write_ext(root: Path, name: str, manifest: dict, code: str) -> Path:
    d = root / "extensions" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "extension.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / f"{name}.py").write_text(textwrap.dedent(code), encoding="utf-8")
    return d


GOOD = {"name": "quota", "version": "1.2", "api": "helene.ext/1.0", "entry": "quota:register",
        "capabilities": ["fs.read"], "acceptance": "quota:check"}

GOOD_CODE = """
    QUOTA_TOOL = {"description": "остаток запросов", "input_schema": {"type": "object", "properties": {}}}
    CALLS = []

    def tool_quota():
        return "143"

    def register(api):
        api.register_tool("quota", QUOTA_TOOL, tool_quota, purpose="остаток", capabilities=("fs.read",))
        api.register_hook("after_turn", lambda chat_id, envelope: CALLS.append(("after", chat_id)))
        api.register_hook("on_boot", lambda api_: CALLS.append(("boot", api_.version()["api"])))

    def check(api):
        return True
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="helene_ext_"))
        self.tree = self.tmp / "data"
        self.tree.mkdir()
        self.agent = fake_agent()

    def install(self, host_version="0.8.7"):
        return extensions.install(self.agent, self.tree, {"agent": {"name": "Феофан"},
                                                          "model": {"key": "SECRET"}},
                                  host_version=host_version)


class Versions(unittest.TestCase):
    def test_surface_fingerprint_is_recorded(self):
        self.assertEqual(extensions.surface_fingerprint(), extensions.SURFACE_FINGERPRINT,
                         "поверхность PluginAPI изменилась — подними API_MINOR/API_MAJOR и перепиши отпечаток")

    def test_negotiation_rules(self):
        ok, word = extensions.negotiate({"api": "helene.ext/1.0"})
        self.assertTrue(ok, word)
        self.assertTrue(extensions.negotiate({"api": "helene.ext/1"})[0])
        ok, word = extensions.negotiate({"api": "helene.ext/2.0"})
        self.assertFalse(ok)
        self.assertIn("другой major", word)
        ok, word = extensions.negotiate({"api": "helene.ext/1.9"})
        self.assertFalse(ok)
        self.assertIn("обновить программу", word)
        ok, word = extensions.negotiate({})
        self.assertFalse(ok)
        self.assertIn("нет поля api", word)

    def test_requires(self):
        self.assertEqual(extensions.requires_ok({"requires": {"helene": ">=0.8.7"}}, "0.8.7"), (True, ""))
        self.assertEqual(extensions.requires_ok({"requires": {"helene": ">=0.8.7"}}, "0.8.6")[0], False)
        self.assertEqual(extensions.requires_ok({"requires": {"helene": "*"}}, ""), (True, ""))
        ok, note = extensions.requires_ok({"requires": {"helene": ">=0.9"}}, "")
        self.assertTrue(ok)
        self.assertIn("не проверено", note)

    def test_scrub_hides_secrets(self):
        out = extensions.scrub({"model": {"key": "abc", "base_url": "http://x"}, "relay": {"token": "t"},
                                "telegram": {"api_hash": "h", "phone": "+7", "owner_id": "1"}})
        self.assertEqual(out["model"]["key"], "•••")
        self.assertEqual(out["model"]["base_url"], "http://x")
        self.assertEqual(out["relay"]["token"], "•••")
        self.assertEqual((out["telegram"]["api_hash"], out["telegram"]["phone"]), ("•••", "•••"))
        self.assertEqual(out["telegram"]["owner_id"], "1")


class Loading(Base):
    def test_good_extension_registers_tool_and_hooks(self):
        write_ext(self.tree, "quota", GOOD, GOOD_CODE)
        loaded = self.install()
        self.assertEqual([(e.name, e.state) for e in loaded], [("quota", "loaded")])
        names = [t["name"] for t in self.agent.BASE_TOOLS]
        self.assertIn("quota", names)
        schema = next(t for t in self.agent.BASE_TOOLS if t["name"] == "quota")
        self.assertIn("(расширение quota 1.2)", schema["description"])
        self.assertEqual(self.agent.TOOL_IMPL["quota"](), "143")
        self.assertEqual(self.agent.HAND_PURPOSE["quota"], "остаток")
        self.assertEqual(loaded[0].hooks.keys() & {"after_turn", "on_boot"}, {"after_turn", "on_boot"})
        module = sys.modules["helene_ext_quota__quota"]
        self.assertIn(("boot", "helene.ext/1.0"), module.CALLS)
        extensions.run_hook("after_turn", "777", None)
        self.assertIn(("after", "777"), module.CALLS)
        snap = json.loads((self.tree / "memory" / ".state" / "extensions.json").read_text(encoding="utf-8"))
        self.assertEqual(snap["items"][0]["state"], "loaded")
        self.assertIn("quota 1.2 — подключено", extensions.state_line())

    def test_name_conflict_gets_namespace_without_dots(self):
        code = GOOD_CODE.replace('api.register_tool("quota"', 'api.register_tool("reply"')
        write_ext(self.tree, "quota", GOOD, code)
        loaded = self.install()
        self.assertEqual(loaded[0].state, "loaded", loaded[0].reason)
        self.assertEqual(loaded[0].tools, ["ext_quota_reply"])
        self.assertRegex(loaded[0].tools[0], extensions.TOOL_NAME_RE, "имя годится провайдеру")
        self.assertIn("ext_quota_reply", self.agent.TOOL_IMPL)
        self.assertEqual(self.agent.TOOL_IMPL["reply"](), "ok", "штатная рука не подменена")

    def test_budget_is_about_the_extension_not_the_tree(self):
        # У дерева схемы всех рук ~125 000 знаков: расширение обязано подключаться и рядом с ними.
        self.agent.OWNER_TOOLS = [{"name": f"big{i}", "description": "x" * 2000,
                                   "input_schema": {"type": "object"}} for i in range(60)]
        write_ext(self.tree, "quota", GOOD, GOOD_CODE)
        loaded = self.install()
        self.assertEqual(loaded[0].state, "loaded", loaded[0].reason)
        self.assertIn("quota", self.agent.TOOL_IMPL)

    def test_failed_acceptance_rolls_the_tool_back(self):
        code = GOOD_CODE.replace("def check(api):\n        return True", "def check(api):\n        return 'самопроверка не прошла'")
        write_ext(self.tree, "quota", GOOD, code)
        loaded = self.install()
        self.assertEqual(loaded[0].state, "error")
        self.assertIn("самопроверка", loaded[0].reason)
        self.assertNotIn("quota", self.agent.TOOL_IMPL, "тул отказавшего расширения снят")
        self.assertFalse([t for t in self.agent.BASE_TOOLS if t["name"] == "quota"])
        self.assertEqual(loaded[0].tools, [])

    def test_on_boot_gets_the_live_api(self):
        code = GOOD_CODE.replace('CALLS.append(("boot", api_.version()["api"]))',
                                 'CALLS.append(("boot", api_.tree() is not None, api_.agent() is not None, bool(api_.config())))')
        write_ext(self.tree, "quota", GOOD, code)
        self.install()
        self.assertIn(("boot", True, True, True), sys.modules["helene_ext_quota__quota"].CALLS)

    def test_incompatible_major_is_named_not_silent(self):
        write_ext(self.tree, "quota", dict(GOOD, api="helene.ext/2.0"), GOOD_CODE)
        loaded = self.install()
        self.assertEqual(loaded[0].state, "incompatible")
        self.assertIn("другой major", loaded[0].reason)
        self.assertNotIn("quota", self.agent.TOOL_IMPL)
        self.assertIn("quota — не загружено: расширение написано под helene.ext/2.0", extensions.state_line())

    def test_requires_helene_refuses_old_program(self):
        write_ext(self.tree, "quota", dict(GOOD, requires={"helene": ">=0.9.0"}), GOOD_CODE)
        loaded = self.install(host_version="0.8.7")
        self.assertEqual(loaded[0].state, "incompatible")
        self.assertIn("требует helene >=0.9.0", loaded[0].reason)

    def test_undeclared_capability_and_late_registration_refused(self):
        code = GOOD_CODE.replace('capabilities=("fs.read",)', 'capabilities=("http",)')
        write_ext(self.tree, "quota", GOOD, code)
        loaded = self.install()
        self.assertEqual(loaded[0].state, "error")
        self.assertIn("не объявлена в манифесте", loaded[0].reason)
        late = """
            SAVED = {}
            def register(api):
                SAVED["api"] = api
            def check(api):
                try:
                    SAVED["api"].register_tool("x", {"input_schema": {}}, lambda: "")
                except Exception as exc:
                    return str(exc)
                return "поздняя регистрация прошла"
        """
        write_ext(self.tree, "late", {"name": "late", "version": "1", "api": "helene.ext/1.0",
                                      "entry": "late:register", "acceptance": "late:check"}, late)
        loaded = {e.name: e for e in self.install()}
        self.assertEqual(loaded["late"].state, "error")
        self.assertIn("регистрация закрыта", loaded["late"].reason)

    def test_budget_overflow_refuses_tool_with_words(self):
        big = GOOD_CODE.replace('"description": "остаток запросов"', '"description": "x" * 40000')
        write_ext(self.tree, "quota", GOOD, big)
        loaded = self.install()
        self.assertEqual(loaded[0].state, "error")
        self.assertIn("потолок", loaded[0].reason)
        self.assertNotIn("quota", self.agent.TOOL_IMPL)
        # и на репетиции тот же потолок — отчёт не скажет «ok» тому, что не подключится
        report = extensions.check(self.tree, host_version="0.8.7")
        self.assertFalse(report["ok"])

    def test_one_broken_extension_does_not_sink_the_other(self):
        write_ext(self.tree, "quota", GOOD, GOOD_CODE)
        write_ext(self.tree, "broken", {"name": "broken", "version": "1", "api": "helene.ext/1.0",
                                        "entry": "broken:register"}, "def register(api):\n    raise RuntimeError('бах')\n")
        states = {e.name: e.state for e in self.install()}
        self.assertEqual(states, {"broken": "error", "quota": "loaded"})

    def test_failing_hook_is_logged_not_raised(self):
        code = GOOD_CODE.replace("lambda chat_id, envelope: CALLS.append((\"after\", chat_id))",
                                 "lambda chat_id, envelope: 1 / 0")
        write_ext(self.tree, "quota", GOOD, code)
        loaded = self.install()
        out = extensions.run_hook("after_turn", "777", None)
        self.assertEqual(out[0][1], False)
        self.assertIn("ZeroDivisionError", out[0][2])
        self.assertIn("ZeroDivisionError", loaded[0].last_error)

    def test_manifest_name_must_match_folder(self):
        write_ext(self.tree, "quota", dict(GOOD, name="other"), GOOD_CODE)
        loaded = self.install()
        self.assertEqual(loaded[0].state, "error")
        self.assertIn("не совпадает с папкой", loaded[0].reason)

    def test_config_given_to_extension_has_no_secrets(self):
        code = """
            SEEN = {}
            def register(api):
                SEEN.update(api.config())
            def check(api):
                return True
        """
        write_ext(self.tree, "peek", {"name": "peek", "version": "1", "api": "helene.ext/1.0",
                                      "entry": "peek:register", "acceptance": "peek:check"}, code)
        self.install()
        self.assertEqual(sys.modules["helene_ext_peek__peek"].SEEN["model"]["key"], "•••")


class Rehearsal(Base):
    def test_check_reports_without_agent_and_keeps_live_registry(self):
        write_ext(self.tree, "quota", GOOD, GOOD_CODE)
        write_ext(self.tree, "old", dict(GOOD, name="old", entry="old:register", api="helene.ext/2.0"),
                  GOOD_CODE)
        live = self.install()
        before = [e.name for e in extensions.loaded()]
        report = extensions.check(self.tree, host_version="0.8.7")
        self.assertFalse(report["ok"])
        self.assertIn("old — расширение написано под helene.ext/2.0", report["summary"])
        states = {r["name"]: r["state"] for r in report["items"]}
        self.assertEqual(states, {"old": "incompatible", "quota": "loaded"})
        self.assertEqual([e.name for e in extensions.loaded()], before, "репетиция не трогает живой реестр")
        self.assertEqual(len(live), 2)

    def test_check_with_no_extensions(self):
        report = extensions.check(self.tree)
        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"], "расширений нет")


if __name__ == "__main__":
    unittest.main(verbosity=1)
