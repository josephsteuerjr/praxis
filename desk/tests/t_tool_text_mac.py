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
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                    # корень репозитория: рядом desk/ и helene/
sys.path.insert(0, str(HERE.parent / "localharness"))

import body  # noqa: E402  — только импорт: файл тела правит агент тела

#: Дерево агента, как оно уезжает в поставку Mac (`tree/` в архиве — это оно).
TREE_AGENT = ROOT / "helene" / "core" / "agent.py"

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
