# -*- coding: utf-8 -*-
"""Описание тула `computer`, указатель руки и блок владельца словами Linux — против ЖИВОГО дерева.

Запуск:  python tests/t_tool_text_linux.py

Та же забота, что у `t_tool_text_mac.py` (порт 28.09): словари подстрочных замен
`body.LINUX_*` мёртвые по построению — дерево живёт своей жизнью, и одной правки фразы
хватит, чтобы замена перестала находить свой кусок. Тогда модель на Linux читает про
PowerShell и UI Automation и зовёт несуществующее. Здесь: каждой паре есть что менять в
живом тексте, после правки слов Windows не осталось, правка идемпотентна. Разбор дерева —
те же функции, что у Mac-стенда (`ast`, без импорта дерева).
"""
from __future__ import annotations

import sys
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "localharness"))

import body  # noqa: E402
from t_tool_text_mac import (  # noqa: E402  — один разбор дерева на оба стенда
    TREE_AGENT, WINDOWS_WORDS, computer_descriptions, dict_entry, english_pointers_source,
    owner_block_text,
)


class LiveTree(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TREE_AGENT.is_file(), f"дерева нет там, где его ждут: {TREE_AGENT}")
        self.parts = computer_descriptions(TREE_AGENT)
        self.assertTrue(self.parts, "в дереве не нашлось тула `computer`")
        self.said = "\n".join(self.parts)

    def test_every_replacement_has_something_to_replace(self):
        dead = [old for old, _ in body.LINUX_TOOL_TEXT if old not in self.said]
        self.assertEqual(dead, [], "в описании тула `computer` нет этих подстрок — замены мёртвые:\n  "
                         + "\n  ".join(repr(d) for d in dead))

    def test_after_the_patch_no_windows_word_is_left(self):
        said = body.linux_tool_text(self.said)
        for word in WINDOWS_WORDS:
            self.assertNotIn(word, said, f"после правки в описании осталось «{word}»")

    def test_the_patch_says_the_linux_words_instead(self):
        said = body.linux_tool_text(self.said)
        for word in ("Use the connected computer (Linux)", "manage shell processes (bash)",
                     "native desktop hands (X11 and AT-SPI)", "AT-SPI accessibility tree"):
            self.assertIn(word, said, word)

    def test_the_patch_is_idempotent_and_differs_from_mac(self):
        once = body.linux_tool_text(self.said)
        self.assertEqual(body.linux_tool_text(once), once)
        self.assertNotEqual(once, body.mac_tool_text(self.said))


class LiveOwnerBlockAndPointer(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TREE_AGENT.is_file(), f"дерева нет там, где его ждут: {TREE_AGENT}")

    def test_owner_block_speaks_linux(self):
        block = owner_block_text(TREE_AGENT)
        self.assertTrue(block, "в дереве не нашлось блока владельца")
        dead = [old for old, _ in body.LINUX_OWNER_TEXT if old not in block]
        self.assertEqual(dead, [], "в блоке владельца нет этих подстрок:\n  " + "\n  ".join(map(repr, dead)))
        said = body.linux_owner_text(block)
        # «…subagents on Windows still goes through it» — сознательное
        # упоминание виндового keyhole: весь абзац — факт о НЁМ (нет мозга
        # машины): ядро добавило его позже словаря, вычленяем до запрета (06.10).
        said_plain = re.sub(r"`coding_session\(scope='windows'\)` is a deprecated keyhole.*?task store\.\s*", "", said)
        for word in ("Windows PC", "PowerShell", "on Windows", "The PC has"):
            self.assertNotIn(word, said_plain, f"после правки осталось «{word}»")
        self.assertIn("This Linux computer is your DIRECT body", said)
        self.assertEqual(body.linux_owner_text(said), said)

    def test_russian_pointer_speaks_linux(self):
        ru = dict_entry(TREE_AGENT, "HAND_PURPOSE", "computer")
        self.assertTrue(ru, "в дереве нет HAND_PURPOSE[\"computer\"]")
        said = body.linux_pointer_text(ru)
        for word in ("Windows", "PowerShell", "Егора"):
            self.assertNotIn(word, said, f"после правки в указателе осталось «{word}»")
        self.assertIn("bash", said)

    def test_english_pointer_speaks_linux(self):
        src = english_pointers_source()
        if src is None:
            self.skipTest("tool_text_en.py издания не найден — английский указатель НЕ сверен")
        en = dict_entry(src, "POINTER_PURPOSE", "computer")
        self.assertTrue(en)
        said = body.linux_pointer_text(en)
        for word in ("Windows", "PowerShell", "Yegor"):
            self.assertNotIn(word, said, f"после правки в указателе осталось «{word}»")


class ResultsAndInstall(unittest.TestCase):
    def test_result_lines_speak_linux(self):
        line = "Windows body online: desktop недоступен из Session 0; UI Automation tree"
        said = body.linux_result_text(line)
        self.assertNotIn("Windows", said)
        self.assertNotIn("Session 0", said)
        self.assertIn("Body (Linux)", said)
        self.assertIn("AT-SPI tree", said)

    def test_speak_linux_wraps_owner_block_and_pointers_once(self):
        class FrameTrace:
            def __init__(self):
                self.seen = []

            def mark(self, name, zone, kind, text, *args, **kwargs):
                self.seen.append(text)

        class Agent:
            HAND_PURPOSE = {"computer": "Windows-компьютер Егора: файлы, PowerShell"}
            frame_trace = FrameTrace()
            TOOL_IMPL: dict = {}

        agent = Agent()
        first = body.speak_linux(agent)
        second = body.speak_linux(agent)
        self.assertEqual(first["pointers"], 1)
        self.assertEqual(second["pointers"], 0, "правка указателя идемпотентна")
        self.assertTrue(first["owner"])
        self.assertFalse(second["owner"], "обёртка блока владельца не удваивается")
        agent.frame_trace.mark(body.OWNER_TOOLS_MARK, "z", "k", "The Windows PC is your DIRECT body")
        self.assertEqual(agent.frame_trace.seen[-1], "This Linux computer is your DIRECT body")
        agent.frame_trace.mark("other.segment", "z", "k", "The Windows PC is your DIRECT body")
        self.assertEqual(agent.frame_trace.seen[-1], "The Windows PC is your DIRECT body",
                         "чужой отрезок кадра не трогается")


if __name__ == "__main__":
    unittest.main(verbosity=2)
