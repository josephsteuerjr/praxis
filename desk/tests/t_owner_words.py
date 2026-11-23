# -*- coding: utf-8 -*-
"""Имя владельца в текстах дерева — правила, установка на модуль и инвентарь живого дерева.

Запуск:  python tests/t_owner_words.py

Три слоя проверки:
  1. чистые правила `OwnerWords.say` — английский, русский с падежами, притяжательный,
     история без имени, пример алиаса, тождество для самого владельца;
  2. `owner_words.install` на поддельном модуле — схемы, словари, шаблоны, обёртки
     `frame_trace.mark` (только авторские отрезки) и `_presence_frame`, повтор безвреден;
  3. текущее ядро и английская проекция должны обращаться к владельцу без личного имени.
"""
from __future__ import annotations

import ast
import os
import re
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "localharness"))

import owner_words  # noqa: E402

def _tree_agent() -> Path:
    """`agent.py` дерева, которое РЕАЛЬНО едет в поставку: HELENE_TREE_SRC, дерево сборки
    (HELENE_BODY_DIR/tree), иначе слой репозитория. Ревью 25.09 (A9 F1): стенд сверял слой
    чекаута, а в Mac-архив 0.8.6 уехало дерево Windows-архива с прежней фразой."""
    for var, sub in (("HELENE_TREE_SRC", ""), ("HELENE_BODY_DIR", "tree")):
        raw = os.environ.get(var)
        if raw:
            candidate = (Path(raw) / sub if sub else Path(raw)) / "agent.py"
            if candidate.is_file():
                return candidate
    return ROOT / "helene" / "core" / "agent.py"


TREE_AGENT = _tree_agent()
NAME = re.compile(r"Yegor|Егор|Egor")

def inventory(path: Path) -> list[int]:
    """Source lines whose authored text contains an original owner name."""
    tree = ast.parse(path.read_bytes(), filename=str(path))
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Constant)
            and isinstance(node.value, str) and NAME.search(node.value)]


class Rules(unittest.TestCase):
    def test_english_name_and_possessive(self):
        w = owner_words.OwnerWords("Sergei")
        self.assertEqual(w.say("NOTHING is refused — Yegor trusts you."), "NOTHING is refused — Sergei trusts you.")
        self.assertEqual(w.say("outside Yegor's DM only"), "outside Sergei's DM only")
        self.assertEqual(w.say("only Egor may call this"), "only Sergei may call this")
        self.assertEqual(w.say("Egor's computer"), "Sergei's computer")

    def test_russian_cases(self):
        w = owner_words.OwnerWords("Сергей")
        self.assertEqual(w.say("Windows-компьютер Егора: файлы"), "Windows-компьютер владельца: файлы")
        self.assertEqual(w.say("(только Егору)"), "(только владельцу)")
        self.assertEqual(w.say("договор с Егором"), "договор с владельцем")
        self.assertEqual(w.say("память о Егоре"), "память о владельце")
        self.assertEqual(w.say("видят Егор и родные"), "видят Сергей и родные")
        # Часть другого слова — не имя.
        self.assertEqual(w.say("Егоровна пришла"), "Егоровна пришла")

    def test_generic_owner_when_the_wizard_left_the_name_empty(self):
        w = owner_words.OwnerWords("владелец")
        self.assertTrue(w.active)
        self.assertEqual(w.say("Yegor trusts you; Yegor's words"), "the owner trusts you; the owner's words")
        self.assertEqual(w.say("пока Егор не снимет"), "пока владелец не снимет")

    def test_history_keeps_the_fact_and_drops_the_name(self):
        w = owner_words.OwnerWords("Sergei")
        said = w.say("outside orientation because Yegor explicitly rejected that polluted diary as a blueprint; you may")
        self.assertEqual(said, "outside orientation because that polluted diary was rejected as a blueprint; you may")

    def test_alias_example_and_non_strings_are_left_alone(self):
        w = owner_words.OwnerWords("Sergei")
        alias = "Bind an alias ('Yegor' to yegor-kosyrev): recall"
        self.assertEqual(w.say(alias), alias)
        self.assertEqual(w.say(""), "")
        self.assertIsNone(w.say(None))
        self.assertEqual(w.say(42), 42)

    def test_the_owner_being_yegor_changes_nothing(self):
        for name in ("Yegor", "Егор", "egor", "Yegor Kosyrev", "Егор Косырев", ""):
            w = owner_words.OwnerWords(name)
            self.assertFalse(w.active, name)
            self.assertEqual(w.say("Yegor trusts you, Егор"), "Yegor trusts you, Егор")

    def test_idempotent(self):
        w = owner_words.OwnerWords("Mira")
        once = w.say("Yegor's Windows computer; только Егору; Egor asks; Егор видит")
        self.assertEqual(w.say(once), once)
        self.assertNotRegex(once, NAME)

    def test_marks_classification(self):
        self.assertTrue(owner_words.mark_is_owner_text("contract.owner_tools"))
        self.assertTrue(owner_words.mark_is_owner_text("contract.base"))
        self.assertTrue(owner_words.mark_is_owner_text("state.owner_place"))
        for name in ("memory.people", "state.journal", "history.turn", "", None):
            self.assertFalse(owner_words.mark_is_owner_text(name), name)


def fake_tree():
    agent = types.ModuleType("agent")
    agent.PANIC_TOOL = {"name": "panic", "description": "встать и не перезапускаться, пока Егор не снимет"}
    agent.BASE_TOOLS = [
        {"name": "manage_identity", "description": "Ревизия применяется сразу, Егор видит постфактум",
         "input_schema": {"type": "object", "properties": {
             "reason": {"type": "string", "description": "Yegor's words if any"},
             "kind": {"type": "string", "enum": ["Yegor"]}}}},
        {"name": "add_alias", "description": "alias ('Yegor' to yegor-kosyrev): recall"},
    ]
    agent.OWNER_TOOLS = agent.BASE_TOOLS + [agent.PANIC_TOOL]
    agent.HAND_PURPOSE = {"computer": "Windows-компьютер Егора: файлы", "shell": "мои руки"}
    agent.tool_text_en = types.ModuleType("tool_text_en")
    agent.tool_text_en.POINTER_PURPOSE = {"computer": "Yegor's Windows computer: files", "shell": "hands"}
    agent.tool_text_en.EN = {"manage_identity": {"d": "Yegor sees it afterwards", "p": {"reason": "Yegor's words"}}}
    agent._HEARTBEAT_FRAME = "--- think of something worth writing to Yegor"
    agent._REST_WINDOW_FRAME = "ни даже Егор — подождёт"
    agent._TASK_WINDOW_BODY = "no name here"
    agent.frame_trace = types.ModuleType("frame_trace")
    agent.frame_trace.mark = lambda name, zone, kind, text, **kw: text
    agent._presence_frame = lambda ctx: f"PRIVATE conversation with Yegor — your person ({ctx})"
    return agent


class Install(unittest.TestCase):
    def test_install_patches_the_authored_texts_and_nothing_else(self):
        agent = fake_tree()
        report = owner_words.install(agent, {"owner": {"name": "Sergei"}})
        self.assertTrue(report["active"])
        self.assertEqual(report["owner"], "Sergei")
        # Схемы: описания на любой глубине; enum и пример алиаса — нет.
        self.assertEqual(agent.PANIC_TOOL["description"], "встать и не перезапускаться, пока Sergei не снимет")
        tool = agent.BASE_TOOLS[0]
        self.assertEqual(tool["description"], "Ревизия применяется сразу, Sergei видит постфактум")
        self.assertEqual(tool["input_schema"]["properties"]["reason"]["description"], "Sergei's words if any")
        self.assertEqual(tool["input_schema"]["properties"]["kind"]["enum"], ["Yegor"])
        self.assertEqual(agent.BASE_TOOLS[1]["description"], "alias ('Yegor' to yegor-kosyrev): recall")
        self.assertEqual(report["schemas"], 3)
        # Словари: оба указателя и английская проекция.
        self.assertEqual(agent.HAND_PURPOSE["computer"], "Windows-компьютер владельца: файлы")
        self.assertEqual(agent.HAND_PURPOSE["shell"], "мои руки")
        self.assertEqual(agent.tool_text_en.POINTER_PURPOSE["computer"], "Sergei's Windows computer: files")
        self.assertEqual(agent.tool_text_en.EN["manage_identity"]["d"], "Sergei sees it afterwards")
        self.assertEqual(agent.tool_text_en.EN["manage_identity"]["p"]["reason"], "Sergei's words")
        self.assertEqual(report["dicts"], 4)
        # Шаблоны окон.
        self.assertEqual(agent._HEARTBEAT_FRAME, "--- think of something worth writing to Sergei")
        self.assertEqual(agent._REST_WINDOW_FRAME, "ни даже Sergei — подождёт")
        self.assertEqual(agent._TASK_WINDOW_BODY, "no name here")
        self.assertEqual(report["templates"], 2)
        # Метки кадра: контракты — да, содержимое — тем же объектом.
        self.assertTrue(report["marks"])
        got = agent.frame_trace.mark("contract.owner_tools", "dynamic", "text", "Yegor trusts you", label="x")
        self.assertEqual(got, "Sergei trusts you")
        got = agent.frame_trace.mark("state.owner_place", "dynamic", "text", "channel with Yegor.")
        self.assertEqual(got, "channel with Sergei.")
        dossier = "Егор — сосед владельца, звонил во вторник"
        self.assertIs(agent.frame_trace.mark("memory.people", "dynamic", "text", dossier), dossier)
        # Рамка присутствия обёрнута.
        self.assertEqual(report["functions"], 1)
        self.assertEqual(agent._presence_frame("dm"), "PRIVATE conversation with Sergei — your person (dm)")

    def test_contract_constants_and_speaker_authority_speak_the_owner(self):
        """1.0.0: контракты рук/аппетита — константы модуля, полномочия говорящего — словарь
        frame_layout (строка «говорит» под стабильной головой комнаты)."""
        agent = fake_tree()
        agent._OWNER_TOOLS_CONTRACT = "NOTHING is refused — Yegor trusts you."
        agent._APPETITE_CONTRACT = "your thinking costs Yegor money"
        agent._ROOM_TOOLS_CONTRACT_STABLE = "NOTHING is refused — Yegor trusts you. (owner-only at call time)"
        agent.frame_layout = types.ModuleType("frame_layout")
        agent.frame_layout.AUTHORITY = {
            "owner": "полномочия владельца", "unknown": "впустить в «свои» может только Егор"}
        owner_words.install(agent, {"owner": {"name": "Sergei"}})
        self.assertEqual(agent._OWNER_TOOLS_CONTRACT, "NOTHING is refused — Sergei trusts you.")
        self.assertEqual(agent._APPETITE_CONTRACT, "your thinking costs Sergei money")
        self.assertIn("Sergei trusts you", agent._ROOM_TOOLS_CONTRACT_STABLE)
        self.assertEqual(agent.frame_layout.AUTHORITY["unknown"], "впустить в «свои» может только Sergei")

    def test_install_twice_changes_nothing_more(self):
        agent = fake_tree()
        owner_words.install(agent, {"owner": {"name": "Sergei"}})
        again = owner_words.install(agent, {"owner": {"name": "Sergei"}})
        self.assertEqual((again["schemas"], again["dicts"], again["templates"], again["marks"], again["functions"]),
                         (0, 0, 0, False, 0))
        self.assertTrue(getattr(agent.frame_trace.mark, "_helene_owner", False))
        self.assertFalse(getattr(agent.frame_trace.mark.__wrapped__, "_helene_owner", False))

    def test_install_for_yegor_himself_is_a_no_op(self):
        agent = fake_tree()
        before = (dict(agent.HAND_PURPOSE), agent._HEARTBEAT_FRAME, agent.frame_trace.mark, agent._presence_frame)
        report = owner_words.install(agent, {"owner": {"name": "Егор"}})
        self.assertFalse(report["active"])
        self.assertEqual((dict(agent.HAND_PURPOSE), agent._HEARTBEAT_FRAME, agent.frame_trace.mark,
                          agent._presence_frame), before)

    def test_install_survives_a_bare_module_and_an_empty_config(self):
        report = owner_words.install(types.ModuleType("bare"), {})
        # Без имени владельца — «владелец»: активно, но править нечего.
        self.assertTrue(report["active"])
        self.assertEqual((report["schemas"], report["dicts"], report["templates"], report["marks"], report["functions"]),
                         (0, 0, 0, False, 0))


class LiveTree(unittest.TestCase):
    def test_current_prompts_are_neutral(self):
        self.assertTrue(TREE_AGENT.is_file())
        self.assertEqual(inventory(TREE_AGENT), [])

    def test_current_tool_projection_is_neutral(self):
        projection = TREE_AGENT.with_name("tool_text_en.py")
        self.assertTrue(projection.is_file())
        self.assertEqual(inventory(projection), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
