"""Direct app-worker boot in an isolated interpreter, not an imported test module."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

DESK = Path(__file__).resolve().parents[1]


class SpeechBootstrap(unittest.TestCase):
    def test_direct_worker_cli_bootstraps_app_siblings_without_argv_directory(self):
        # -I removes cwd and the script directory just like embedded ._pth.
        # --help proves the CLI reaches its argument parser, no model is loaded.
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-B',
                str(DESK / 'localharness/speech_worker.py'), '--help'],
                cwd=directory, capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ('--home', '--tree', '--generation', '--model'):
            self.assertIn(flag, result.stdout)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
