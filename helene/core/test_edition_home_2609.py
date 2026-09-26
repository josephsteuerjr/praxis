"""Издание, 26.09: агент стартует у себя дома, а рука без аргумента отвечает словами.

Живой случай у первого стороннего человека (1.0.3): busybox из поставки на `bash -l`
уходил в профиль владельца, и первый ход «осмотрись» ушёл на поиски своей папки по
чужим адресам (135 с, семь команд, `/app`); `fs_search` без `pattern` падал TypeError
из потока — трейс в журнал, «[tool_error TypeError]» модели.

Запуск:  python praxis_test.py test_edition_home_2609 -v
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

_fa = types.ModuleType("anthropic")
_fa.Anthropic = lambda **kw: None
sys.modules.setdefault("anthropic", _fa)
_fd = types.ModuleType("dotenv")
_fd.load_dotenv = lambda *a, **k: None
sys.modules.setdefault("dotenv", _fd)

import agent  # noqa: E402


class ShellStartsAtHome(unittest.TestCase):
    def test_cd_home_comes_first_posix_slashes(self):
        home = Path("C:/Users/Мама/AppData/Local/Programs/Helene/data/workspace")
        self.assertEqual(agent._shell_at_home("pwd", home),
                         "cd 'C:/Users/Мама/AppData/Local/Programs/Helene/data/workspace' && pwd")

    def test_own_quotes_in_the_path_are_escaped(self):
        self.assertEqual(agent._shell_at_home("ls", Path("/home/o'neil/x")),
                         "cd '/home/o'\\''neil/x' && ls")

    def test_multiline_command_keeps_its_lines(self):
        out = agent._shell_at_home("echo a\necho b", Path("/h"))
        self.assertEqual(out, "cd '/h' && echo a\necho b")


class HandsNameTheMissingArgument(unittest.TestCase):
    def test_missing_required_argument_is_named(self):
        def fs_search(pattern, path="."):
            return "never"
        self.assertIn("pattern", agent._tool_args_mismatch(fs_search, {}))
        self.assertEqual(agent._tool_args_mismatch(fs_search, {"pattern": "x"}), "")
        self.assertIn("unexpected", agent._tool_args_mismatch(fs_search, {"pattern": "x", "extra": 1}))

    def test_unreadable_signature_is_not_judged(self):
        class Opaque:
            def __call__(self, *args, **kwargs):
                return "ok"

            @property
            def __signature__(self):
                raise ValueError("сигнатуры нет")

        self.assertEqual(agent._tool_args_mismatch(Opaque(), {"whatever": 1}), "")

    def test_call_with_ceiling_answers_in_words_not_traceback(self):
        def fs_search(pattern, path="."):
            raise AssertionError("рука не должна быть позвана")
        out = agent._call_tool_with_ceiling("fs_search", fs_search, {})
        self.assertIn("не позвана", out)
        self.assertIn("pattern", out)


if __name__ == "__main__":
    unittest.main()
