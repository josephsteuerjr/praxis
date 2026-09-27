"""Описание руки `shell` — словами этого компьютера (1.2.3).

27.09 агент на чистой установке прочёл в описании руки «контейнер, /app», получил от
busybox `whoami` → root и решил, что живёт в Linux. Стенд держит: во всех списках рук
и в SHELL_TOOL описание переписано, в нём дом установки и нет «/app» как дома; схема
входа не тронута.
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESK / "localharness"))

import shell_words  # noqa: E402

SERVER = ("Твои руки в твоём доме. Полный shell в контейнере. Твой дом — /app: душа в "
          "/app/soul, память в /app/memory.")


class ShellWordsTest(unittest.TestCase):
    def test_every_shell_schema_speaks_of_this_computer(self):
        shared = {"name": "shell", "description": SERVER, "input_schema": {"type": "object"}}
        mod = types.SimpleNamespace(
            BASE_TOOLS=[shared, {"name": "reply", "description": "x"}],
            TOOLS=[shared],
            SHELL_TOOL=shared,
            OWNER_TOOLS=[{"name": "shell", "description": SERVER}],
        )
        tree = Path("C:/Program Files/Helene/data")
        n = shell_words.install(mod, tree, Path("C:/Program Files/Helene"))
        self.assertEqual(n, 2)  # общий словарь — один раз, копия владельца — второй
        for tool in (mod.BASE_TOOLS[0], mod.OWNER_TOOLS[0], mod.SHELL_TOOL):
            words = tool["description"]
            self.assertNotIn("Полный shell в контейнере", words)
            self.assertIn("C:/Program Files/Helene/data/workspace", words)
            self.assertIn("не контейнер", words)
        self.assertEqual(mod.BASE_TOOLS[1]["description"], "x")
        self.assertEqual(shared["input_schema"], {"type": "object"})

    def test_english_overlay_is_rewritten_too(self):
        fake = types.ModuleType("tool_text_en")
        fake.EN = {"shell": {"d": "Your hands in your own home. A full shell in the container.",
                             "p": {"command": "the shell command"}}}
        sys.modules["tool_text_en"] = fake
        try:
            mod = types.SimpleNamespace(BASE_TOOLS=[{"name": "shell", "description": SERVER}])
            shell_words.install(mod, Path("C:/H/data"), Path("C:/H"))
            words = fake.EN["shell"]["d"]
            self.assertNotIn("in the container", words)
            self.assertIn("C:/H/data/workspace", words)
            self.assertEqual(fake.EN["shell"]["p"], {"command": "the shell command"})
        finally:
            del sys.modules["tool_text_en"]

    def test_windows_words_explain_busybox_root(self):
        words = shell_words.text(Path("C:/H/data"), Path("C:/H"), platform="win32")
        self.assertIn("busybox", words)
        self.assertIn("root", words)
        self.assertIn("это не Linux", words)

    def test_mac_words_have_no_windows_talk(self):
        words = shell_words.text(Path("/Users/a/Applications/Helene/data"), Path("/Users/a/Applications/Helene"),
                                 platform="darwin")
        self.assertNotIn("busybox", words)
        self.assertNotIn("Windows", words)
        self.assertIn("/Users/a/Applications/Helene/data/workspace", words)


if __name__ == "__main__":
    unittest.main()
