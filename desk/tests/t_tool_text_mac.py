# -*- coding: utf-8 -*-
"""Описание тула `computer` словами macOS — против ЖИВОГО дерева.

Запуск:  python tests/t_tool_text_mac.py

⚠ ЗАЧЕМ. `body.MAC_TOOL_TEXT` — словарь подстрочных замен: он переводит
описание тула `computer` с языка Windows на язык macOS прямо в загруженном
модуле дерева (`body.describe_for_mac`). Словарь этот МЁРТВЫЙ по построению:
дерево — чужой код (Праксис), оно живёт своей жизнью, и одной правки фразы в
`helene/core/agent.py` хватит, чтобы замена перестала находить свой кусок. И
тогда ничего не сломается ГРОМКО: модель просто получит описание, которое
говорит ей про PowerShell и UI Automation на машине, где нет ни того, ни
другого, — и пойдёт звать несуществующее.

Соседний стенд (`t_body.py`, класс Platform) проверяет ту же функцию на
ОБРАЗЦАХ: что замены не рвут соседние предложения и идемпотентны. Здесь
проверяется другое и непроверяемое образцами — что каждой замене есть что
заменять в ЖИВОМ тексте, и что после всех замен слов Windows не осталось.

Дерево не импортируется: `helene/core/agent.py` тянет за собой половину
Праксис. Схема разбирается по исходнику (`ast`) тем же правилом, каким ходит
`body._mac_walk`: найти словарь тула с `name == "computer"` и собрать ВСЕ
`description` внутри него, на любой глубине — и у самого тула, и у полей
`input_schema`.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                    # корень репозитория: рядом desk/ и helene/
sys.path.insert(0, str(HERE.parent / "localharness"))

import body  # noqa: E402  — только импорт: файл тела правит агент тела

#: Дерево агента, как оно уезжает в поставку Mac (`tree/` в архиве — это оно).
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

#: Слова Windows, которых в описании для macOS быть не должно. `.exe` здесь же:
#: имён с расширением на Mac не бывает ни у одного файла поставки.
WINDOWS_WORDS = ("Windows", "PowerShell", "UI Automation", "Win32", ".exe",
                 "WinForms", "Office COM", "wcode")


def computer_descriptions(path: Path) -> list[str]:
    """Все `description` схемы тула `computer` из исходника дерева.

    Тем же правилом, что `body._mac_walk`: словарь с `"name": "computer"` —
    корень, дальше вглубь по всем словарям. Строки, собранные из кусков в
    скобках, питон склеивает ещё при разборе, поэтому сравнивать можно с целыми
    фразами словаря замен.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        is_computer = any(
            isinstance(k, ast.Constant) and k.value == "name"
            and isinstance(v, ast.Constant) and v.value == "computer"
            for k, v in zip(node.keys, node.values)
        )
        if not is_computer:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Dict):
                continue
            for k, v in zip(sub.keys, sub.values):
                if isinstance(k, ast.Constant) and k.value == "description" \
                        and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    found.append(v.value)
    return found


class LiveTree(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TREE_AGENT.is_file(), f"дерева нет там, где его ждут: {TREE_AGENT}")
        self.parts = computer_descriptions(TREE_AGENT)
        self.assertTrue(self.parts, "в дереве не нашлось тула `computer` — разбор схемы отстал от дерева")
        self.said = "\n".join(self.parts)

    def test_the_tool_text_is_the_windows_one_before_the_patch(self):
        """Сторож самого стенда: если дерево уже не про Windows, проверять нечего."""
        self.assertIn("Use the connected Windows computer", self.said,
                      "описание тула в дереве перестало быть виндовым — словарь замен надо пересобрать")

    def test_every_replacement_has_something_to_replace(self):
        """Каждая пара словаря обязана НАЙТИ свой кусок в живом описании.

        Мёртвая пара — это не «лишняя строка», а дыра: фраза, ради которой её
        писали, осталась в описании как была, и модель на Mac читает про
        Windows. Краснеем ИМЕНЕМ подстроки, а не общим «что-то не так».
        """
        dead = [old for old, _ in body.MAC_TOOL_TEXT if old not in self.said]
        self.assertEqual(
            dead, [],
            "в описании тула `computer` дерева нет этих подстрок — замены мёртвые:\n  "
            + "\n  ".join(repr(d) for d in dead))

    def test_after_the_patch_no_windows_word_is_left(self):
        said = body.mac_tool_text(self.said)
        for word in WINDOWS_WORDS:
            self.assertNotIn(word, said, f"после правки в описании осталось «{word}»")

    def test_the_patch_says_the_mac_words_instead(self):
        said = body.mac_tool_text(self.said)
        for word in ("Use the connected computer (macOS)",
                     "manage shell processes (zsh)",
                     "native desktop hands (Accessibility)",
                     "Accessibility control tree"):
            self.assertIn(word, said, word)

    def test_the_patch_is_idempotent_on_the_live_text(self):
        once = body.mac_tool_text(self.said)
        self.assertEqual(body.mac_tool_text(once), once)


def owner_block_text(path: Path) -> str:
    """Текст блока владельца — 4-й аргумент `frame_trace.mark("contract.owner_tools", …)`.

    Литерал там склеен из соседних строк с одной f-строкой посередине, поэтому в
    дереве разбора это `JoinedStr`; берём его постоянные куски. Подстановки
    (`{trust_tool}`) в текст не входят — словарь замен их и не трогает.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "mark" and len(node.args) >= 4):
            continue
        head = node.args[0]
        if not (isinstance(head, ast.Constant) and head.value == body.OWNER_TOOLS_MARK):
            continue
        text = node.args[3]
        if isinstance(text, ast.Constant) and isinstance(text.value, str):
            return text.value
        if isinstance(text, ast.JoinedStr):
            return "".join(v.value for v in text.values
                           if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return ""


def dict_entry(path: Path, dict_name: str, key: str) -> str:
    """Значение `key` в словаре верхнего уровня `dict_name = {...}` исходника."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == dict_name for t in targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for k, v in zip(node.value.keys, node.value.values):
            if isinstance(k, ast.Constant) and k.value == key \
                    and isinstance(v, ast.Constant) and isinstance(v.value, str):
                return v.value
    return ""


def english_pointers_source() -> Path | None:
    """`tool_text_en.py` издания: HELENE_TREE_SRC, дерево сборки (HELENE_BODY_DIR/tree),
    иначе рабочая копия издания рядом с репозиторием. В самом репозитории его нет."""
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


class LiveOwnerBlockAndPointer(unittest.TestCase):
    """20.09 (Mac, 0.8.1): «на маке не реализовано» — модель читала указатель руки и
    блок владельца, а не схему. Те же правила, что у схемы: каждой паре есть что
    менять в ЖИВОМ дереве, после правки слов Windows нет, правка идемпотентна."""

    def setUp(self):
        self.assertTrue(TREE_AGENT.is_file(), f"дерева нет там, где его ждут: {TREE_AGENT}")

    def test_owner_block_pairs_are_alive_and_the_block_speaks_mac_afterwards(self):
        block = owner_block_text(TREE_AGENT)
        self.assertTrue(block, "в дереве не нашлось frame_trace.mark(\"contract.owner_tools\", …) — "
                               "разбор отстал от дерева")
        self.assertIn("The Windows PC is your DIRECT body", block,
                      "блок владельца перестал быть виндовым — словарь MAC_OWNER_TEXT надо пересобрать")
        dead = [old for old, _ in body.MAC_OWNER_TEXT if old not in block]
        self.assertEqual(dead, [], "в блоке владельца нет этих подстрок — замены мёртвые:\n  "
                         + "\n  ".join(repr(d) for d in dead))
        said = body.mac_owner_text(block)
        for word in ("Windows PC", "PowerShell", "on Windows", "The PC has"):
            self.assertNotIn(word, said, f"после правки в блоке владельца осталось «{word}»")
        self.assertIn("This Mac is your DIRECT body", said)
        self.assertEqual(body.mac_owner_text(said), said)

    def test_russian_pointer_pair_is_alive(self):
        ru = dict_entry(TREE_AGENT, "HAND_PURPOSE", "computer")
        self.assertTrue(ru, "в дереве нет HAND_PURPOSE[\"computer\"] — разбор отстал от дерева")
        self.assertIn("Windows-компьютер Егора", ru, "русский указатель уже не виндовый — пару надо пересобрать")
        said = body.mac_pointer_text(ru)
        for word in ("Windows", "PowerShell", "Егора"):
            self.assertNotIn(word, said, f"после правки в указателе осталось «{word}»")
        self.assertEqual(body.mac_pointer_text(said), said)

    def test_english_pointer_pair_is_alive(self):
        src = english_pointers_source()
        if src is None:
            self.skipTest("tool_text_en.py издания не найден (HELENE_TREE_SRC, HELENE_BODY_DIR/tree, "
                          "../live-fix) — английский указатель против живого текста НЕ сверен")
        en = dict_entry(src, "POINTER_PURPOSE", "computer")
        self.assertTrue(en, f"в {src} нет POINTER_PURPOSE[\"computer\"] — разбор отстал")
        self.assertIn("Yegor's Windows computer", en, "английский указатель уже не виндовый — пару надо пересобрать")
        said = body.mac_pointer_text(en)
        for word in ("Windows", "PowerShell", "Yegor"):
            self.assertNotIn(word, said, f"после правки в указателе осталось «{word}»")
        self.assertEqual(body.mac_pointer_text(said), said)


if __name__ == "__main__":
    unittest.main(verbosity=2)
