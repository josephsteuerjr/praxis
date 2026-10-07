"""Run inside Docker/VM only (AGENTS.md): Forge entry points in isolated Python."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class IsolatedEntryPoints(unittest.TestCase):
    def test_sibling_imports_without_script_path_or_pythonpath(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as cwd:
            env = dict(os.environ, PYTHONPATH=str(Path(cwd) / 'absent'))
            for name in ('forge_worker.py', 'forge_process.py', 'forge_verify.py'):
                with self.subTest(name=name):
                    proc = subprocess.run([sys.executable, '-I', str(root / name), '--help'],
                                          cwd=cwd, env=env, capture_output=True,
                                          text=True, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertIn('--request', proc.stdout)


if __name__ == '__main__':
    unittest.main()
