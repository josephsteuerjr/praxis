"""The actual command entry must not turn codepage help into a modal failure."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ConsoleEntryTests(unittest.TestCase):
    def test_help_cp1251_without_starting_or_error_log(self):
        entry = Path(__file__).resolve().parents[1] / 'python_app.py'
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(entry), '--root', directory, '--help'],
                capture_output=True, timeout=20,
                env={**os.environ, 'PYTHONIOENCODING': 'cp1251:strict'})
            self.assertEqual(result.returncode, 0, result.stderr.decode('cp1251', errors='replace'))
            self.assertIn(b'--launch-intent', result.stdout)
            self.assertIn(b'--panic', result.stdout)
            self.assertFalse((Path(directory) / 'startup-error.log').exists())


if __name__ == '__main__':
    unittest.main()
