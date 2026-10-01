# -*- coding: utf-8 -*-
"""Оркестратор сборки: план в правильном порядке и валидация дерева. Без тяжёлой работы."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "installer")]
import build_win

TREE = str(Path(__file__).resolve().parents[2] / "helene" / "core")


class Plan(unittest.TestCase):
    def test_dry_prints_four_steps_in_order(self):
        out = io.StringIO()
        with patch.object(sys, "argv", ["build_win.py", "--tree", TREE, "--dry"]):
            with contextlib.redirect_stdout(out):
                code = build_win.main()
        self.assertEqual(code, 0)
        text = out.getvalue()
        for step in ("fronts", "crates", "body", "dist"):
            self.assertIn(step, text)
        # Порядок именно такой: фронт нужен крейтам, тело и упаковка — после.
        self.assertLess(text.index("fronts"), text.index("crates"))
        self.assertLess(text.index("crates"), text.index("body"))
        self.assertLess(text.index("body"), text.index("dist"))

    def test_foreign_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "not-a-tree"
            fake.mkdir()
            with patch.object(sys, "argv", ["build_win.py", "--tree", str(fake)]):
                with self.assertRaises(SystemExit):
                    build_win.main()


if __name__ == "__main__":
    unittest.main()
