# -*- coding: utf-8 -*-
"""Имя владельца в текстах дерева — правила, установка на модуль и инвентарь живого дерева.

Запуск:  python tests/t_owner_words.py

Три слоя проверки:
  1. чистые правила `OwnerWords.say` — английский, русский с падежами, притяжательный,
     история без имени, пример алиаса, тождество для самого Егора;
  2. `owner_words.install` на поддельном модуле — схемы, словари, шаблоны, обёртки
     `frame_trace.mark` (только авторские отрезки) и `_presence_frame`, повтор безвреден;
  3. ИНВЕНТАРЬ ЖИВОГО ДЕРЕВА (`helene/core/agent.py`, `tool_text_en.py` издания): каждая
     строка с именем владельца обязана быть либо накрыта установкой (константа схемы,
     словарь, шаблон, авторская метка кадра, обёрнутая функция), либо стоять в ЯВНОМ
     списке остатка `KNOWN_REMAINING`. Новая строка с именем в новом месте — красный стенд
     по имени функции: молча она в кадр не уедет.
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
    import os  # noqa: PLC0415
    for var, sub in (("HELENE_TREE_SRC", ""), ("HELENE_BODY_DIR", "tree")):
        raw = os.environ.get(var)
        if raw:
            candidate = (Path(raw) / sub if sub else Path(raw)) / "agent.py"
            if candidate.is_file():
                return candidate
    return ROOT / "helene" / "core" / "agent.py"


TREE_AGENT = _tree_agent()
NAME = re.compile(r"Yegor|Егор|Egor")

#: Функции дерева, где имя владельца ОСТАЁТСЯ (тексты внутри рук, механизмы, которых в
#: издании нет). Список явный: убрал имя из функции — убери её отсюда, добавил в новую —
#: реши, накрывать или сюда.
KNOWN_REMAINING = frozenset({
    "my_sends_today_digest", "tool_freeze_contact", "tool_recent_turns",
    "tool_mail_draft_reply", "tool_rest", "tool_send_message", "tool_narrate",
    "tool_manage_identity", "tool_home_note", "_who", "outbound_privacy_frame",
    "_mailbox_frame_block", "task_window", "_repl",
})
#: Единственный литерал в сборке промпта вне меток: заголовок домашнего слоя памяти.
KNOWN_REMAINING_LITERALS = ("Дом (общий слой: Егор и родные)",)


def inventory(path: Path) -> list[dict]:
    """Все строковые литералы с именем: строка, докстринг ли, функция, метка кадра, цель присваивания."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # noqa: SLF001
    rows = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and NAME.search(node.value)):
            continue
        fn = mark = assign = None
        docstring = False
        prev, cur = node, getattr(node, "_parent", None)
        while cur is not None:
            if isinstance(cur, ast.Expr) and prev is node:
                docstring = True
            if isinstance(cur, ast.FunctionDef) and fn is None:
                fn = cur.name
            if (isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute)
                    and cur.func.attr == "mark" and cur.args
                    and isinstance(cur.args[0], ast.Constant) and mark is None):
                mark = cur.args[0].value
            if isinstance(cur, (ast.Assign, ast.AnnAssign)) and assign is None:
                target = cur.targets[0] if isinstance(cur, ast.Assign) else cur.target
                assign = getattr(target, "id", None) or ast.unparse(target)
            prev, cur = cur, getattr(cur, "_parent", None)
        rows.append({"line": node.lineno, "doc": docstring, "fn": fn, "mark": mark,
                     "assign": assign, "text": node.value})
    return rows


def english_source() -> Path | None:
    roots = []
    for var, sub in (("HELENE_TREE_SRC", ""), ("HELENE_BODY_DIR", "tree")):
        raw = os.environ.get(var)
        if raw:
            roots.append(Path(raw) / sub if sub else Path(raw))
    roots.append(ROOT.parent / "live-fix")
    for root in roots:
        candidate = root / "tool_text_en.py"
        if candidate.is_file():
            return candidate
    return None


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
    """Инвентарь живого дерева: каждая строка с именем — накрыта или названа."""

    def setUp(self):
        self.assertTrue(TREE_AGENT.is_file(), f"дерева нет там, где его ждут: {TREE_AGENT}")
        self.rows = inventory(TREE_AGENT)
        self.assertTrue(self.rows, "в дереве не нашлось ни одной строки с именем владельца — инвентарь отстал")

    @staticmethod
    def _covered(row: dict) -> bool:
        assign = row["assign"] or ""
        if row["fn"] is None:
            return (assign in owner_words.TOOL_LISTS or assign.endswith("_TOOL")
                    or assign == "HAND_PURPOSE" or assign in owner_words.WINDOW_TEMPLATES)
        if row["mark"] and owner_words.mark_is_owner_text(row["mark"]):
            return True
        if assign == "owner_place":          # уезжает меткой state.owner_place
            return True
        return row["fn"] in owner_words.WRAPPED_FUNCTIONS

    def test_every_name_in_the_tree_is_covered_or_named(self):
        loose = []
        for row in self.rows:
            if row["doc"] or self._covered(row):
                continue
            if row["fn"] in KNOWN_REMAINING:
                continue
            if row["fn"] == "_build_prompt_parts" and any(
                    row["text"].startswith(lit) for lit in KNOWN_REMAINING_LITERALS):
                continue
            snippet = re.sub(r"\s+", " ", row["text"])
            m = NAME.search(snippet)
            start = max(0, m.start() - 40)
            loose.append(f"строка {row['line']}, {row['fn'] or '<модуль>'}"
                         f"{' [' + row['mark'] + ']' if row['mark'] else ''}: …{snippet[start:start + 90]}")
        self.assertEqual(loose, [], "имя владельца в дереве там, где издание его не переводит и не назвало:\n  "
                         + "\n  ".join(loose))

    def test_the_remaining_list_is_not_stale(self):
        """Функция из списка остатка обязана всё ещё содержать имя — иначе список врёт."""
        present = {row["fn"] for row in self.rows if row["fn"] and not row["doc"]}
        stale = sorted(KNOWN_REMAINING - present)
        self.assertEqual(stale, [], f"в этих функциях имени больше нет — вычеркнуть из KNOWN_REMAINING: {stale}")
        literals = [row["text"] for row in self.rows if row["fn"] == "_build_prompt_parts" and not row["mark"]]
        for lit in KNOWN_REMAINING_LITERALS:
            self.assertTrue(any(t.startswith(lit) for t in literals), f"литерала больше нет: {lit!r}")

    def test_history_pairs_and_marks_are_alive(self):
        src = TREE_AGENT.read_text(encoding="utf-8")
        for old, _ in owner_words.HISTORY_TEXT:
            self.assertIn(old, src, f"пара истории мёртвая — в дереве нет: {old!r}")
        for name in ("contract.base", "contract.appetite", "contract.owner_tools",
                     "contract.unknown_authority", "state.owner_place"):
            self.assertIn(f'"{name}"', src, f"метки {name} в дереве больше нет")
        for fname in owner_words.WRAPPED_FUNCTIONS:
            self.assertIn(f"def {fname}(", src, fname)
        for attr in owner_words.WINDOW_TEMPLATES:
            self.assertRegex(src, rf"(?m)^{re.escape(attr)} = ", attr)

    def test_the_covered_texts_speak_the_owner_afterwards(self):
        """Прогон правил по накрытым литералам дерева: имени не остаётся."""
        w = owner_words.OwnerWords("Sergei")
        for row in self.rows:
            if row["doc"] or not self._covered(row) or "yegor-kosyrev" in row["text"]:
                continue
            said = w.say(row["text"])
            self.assertNotRegex(said, NAME, f"строка {row['line']}: {said[:120]!r}")

    def test_english_projection_names_are_covered(self):
        src = english_source()
        if src is None:
            self.skipTest("tool_text_en.py издания не найден (HELENE_TREE_SRC, HELENE_BODY_DIR/tree, "
                          "../live-fix) — английская проекция против живого текста НЕ сверена")
        loose = [f"строка {row['line']} ({row['assign']})" for row in inventory(src)
                 if not row["doc"] and row["assign"] not in ("POINTER_PURPOSE", "EN")]
        self.assertEqual(loose, [], "имя владельца в tool_text_en вне POINTER_PURPOSE/EN:\n  " + "\n  ".join(loose))


if __name__ == "__main__":
    unittest.main(verbosity=2)
