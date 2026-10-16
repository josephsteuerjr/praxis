"""Historical personal scan reads every Git blob without a subprocess per file."""
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCANNER = Path(__file__).resolve().parents[1] / "installer" / "personal_scan.py"


class HistoricalScanner(unittest.TestCase):
    def test_only_the_named_synthetic_room_values_are_exempt(self):
        old = sys.argv
        try:
            sys.argv = [str(SCANNER), ".", "HEAD"]
            found = runpy.run_path(str(SCANNER))["found"]
            known = found({"test.py": "-100999999999999999 -100999999999999998"})
            self.assertNotIn("чат -100…", known)
            synthetic = '-100' + '987654321'  # scanner input, never a committed room value
            another = found({"test.py": synthetic})
            self.assertIn(synthetic, another["чат -100…"])
        finally:
            sys.argv = old

    def test_batch_matches_git_show_including_export_ignored_and_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args],
                                      capture_output=True, check=True).stdout
            git("init", "-q")
            git("config", "core.autocrlf", "false")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@invalid")
            samples = {
                ".gitattributes": b"hidden.txt export-ignore\n",
                "hidden.txt": b"private-shaped synthetic fixture 1234567890\n",
                "a folder/other.txt": "line one\r\nIris and text\n".encode(),
                "byte.bin": b"\xff\x00\x80\n",
                "unicode-\u00e9.txt": b"a third file\n",
            }
            for name, data in samples.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            git("add", "--", ".")
            git("commit", "-qm", "synthetic scan source")
            old = sys.argv
            try:
                sys.argv = [str(SCANNER), str(root), "HEAD"]
                texts = runpy.run_path(str(SCANNER))["texts"]
                observed = texts("HEAD")
                expected = {name: git("show", "HEAD:" + name).decode("utf-8", "replace")
                            for name in samples}
                self.assertEqual(observed, expected)
                self.assertIn("hidden.txt", observed, "export-ignore must not hide tracked bytes")
                self.assertEqual(texts(None), {name: (root / name).read_text(encoding="utf-8", errors="replace")
                                               for name in samples})
                (root / "hidden.txt").unlink()
                with self.assertRaises(OSError):
                    texts(None)
                with self.assertRaises(subprocess.CalledProcessError):
                    texts("does-not-exist")
            finally:
                sys.argv = old


if __name__ == "__main__":
    unittest.main()
