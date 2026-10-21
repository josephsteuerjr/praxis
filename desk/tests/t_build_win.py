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

    def test_secret_guard_knows_documentation_ellipsis(self):
        # 05.10: docs/run_retention.md вёз пример «object_key": "runs/sha256/…"
        # и 256-е три цифры роняли выпуск. Многоточие — пример, не секрет;
        # настоящий цифровой секрет по-прежнему ловится.
        import build_dist
        self.assertEqual(build_dist._assign_secret('{"object_key": "runs/sha256/…"}'), "")
        self.assertEqual(build_dist._assign_secret('key = "hunter2pass123"')[:9], "присвоени")
        self.assertNotEqual(build_dist._assign_secret('token: "abc123def456"'), "")

    def test_foreign_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "not-a-tree"
            fake.mkdir()
            with patch.object(sys, "argv", ["build_win.py", "--tree", str(fake)]):
                with self.assertRaises(SystemExit):
                    build_win.main()

    def test_vnext_requires_exact_relay_selection_and_dry_shows_debug_boundary(self):
        with patch.object(sys, "argv", ["build_win.py", "--tree", TREE,
                                        "--vnext-head", "a" * 40, "--dry"]):
            with self.assertRaisesRegex(SystemExit, "both"):
                build_win.main()
        text = io.StringIO()
        with patch.object(sys, "argv", ["build_win.py", "--tree", TREE, "--dry",
                                        "--vnext-head", "a" * 40, "--relay-root", "relay"]):
            with contextlib.redirect_stdout(text):
                self.assertEqual(build_win.main(), 0)
        self.assertIn("relay", text.getvalue())
        self.assertIn("DEBUG: runtime/tests", text.getvalue())

    def test_full_dist_refuses_any_skipped_build_step(self):
        with patch.object(sys, "argv", ["build_win.py", "--tree", TREE, "--dry",
                                        "--full-dist", "--skip-body"]):
            with self.assertRaisesRegex(SystemExit, "cannot accompany"):
                build_win.main()

    def test_crates_pin_output_directory_even_under_redirected_cargo_env(self):
        calls = []
        class Finished:
            def wait(self):
                return 0
        def launched(name, argv, cwd, log_name):
            calls.append((name, argv, cwd, log_name))
            built = Path(cwd) / "target" / build_win.WINDOWS_TARGET / "release" / build_win.CRATE_PRODUCTS[name]
            built.parent.mkdir(parents=True, exist_ok=True)
            built.write_bytes(b"MZ-new-" + name.encode("ascii"))
            return Finished()
        with tempfile.TemporaryDirectory() as tmp:
            desk = Path(tmp) / "desk"
            for name, exe in build_win.CRATE_PRODUCTS.items():
                stale = desk / name / "target/release" / exe
                stale.parent.mkdir(parents=True, exist_ok=True)
                stale.write_bytes(b"MZ-old-same-version")
            with patch.object(build_win, "DESK", desk), \
                 patch.object(build_win, "BUILD", desk / "installer/build"), \
                 patch.object(build_win, "run", side_effect=launched), \
                 patch.object(sys, "argv", ["build_win.py", "--tree", TREE, "--skip-fronts",
                                           "--skip-body", "--skip-dist"]), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(build_win.main(), 0)
            for name, exe in build_win.CRATE_PRODUCTS.items():
                self.assertEqual((desk / name / "target/release" / exe).read_bytes(),
                                 b"MZ-new-" + name.encode("ascii"))
        self.assertEqual([row[0] for row in calls], ["shell", "svc", "setup"])
        for name, argv, _cwd, _log in calls:
            self.assertEqual(argv[-4:], ["--target", build_win.WINDOWS_TARGET,
                                         "--target-dir", str(desk / name / "target")])

    def test_windows_relay_flag_never_starts_local_docker(self):
        import relay_src
        import vnext_dist
        import build_dist
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(sys, "argv", ["build_win.py", "--tree", TREE, "--vnext-head", "a" * 40,
                                       "--relay-root", tmp, "--build-relay", "--skip-fronts",
                                       "--skip-crates", "--skip-body", "--skip-dist"]), \
             patch.object(vnext_dist, "verify_edition"), patch.object(vnext_dist, "verify_relay"), \
             patch.object(build_dist, "relay_provenance"), \
             patch.object(build_dist, "relay_linux_provenance"), \
             patch.object(relay_src, "build") as win, \
             patch.object(relay_src, "build_linux") as linux, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build_win.main(), 0)
        win.assert_called_once()
        linux.assert_not_called()


if __name__ == "__main__":
    unittest.main()
