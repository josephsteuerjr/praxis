"""Native Windows console observation for the updater's exact launch policy."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness.update_process import interpreter_for, background_flags


@unittest.skipUnless(os.name == 'nt', 'native Windows process/console API')
class Windowless(unittest.TestCase):
    def test_packaged_interpreter_prefers_pythonw_and_accepts_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'runtime').mkdir()
            console = root / 'runtime/python.exe';console.touch()
            self.assertEqual(interpreter_for(root), console)
            windowless = root / 'runtime/pythonw.exe';windowless.touch()
            self.assertEqual(interpreter_for(root), windowless)

    def test_actual_python_and_pythonw_helpers_have_no_console_and_exit(self):
        probe = "import ctypes,json,os; k=ctypes.windll.kernel32; k.GetConsoleWindow.restype=ctypes.c_void_p; pids=(ctypes.c_ulong*16)(); n=k.GetConsoleProcessList(pids,16); print(json.dumps({'pid':os.getpid(),'console':k.GetConsoleWindow(),'console_processes':list(pids)[:n]}),flush=True)"
        for binary in (Path(sys.executable), Path(sys.executable).with_name('pythonw.exe')):
            with self.subTest(binary=binary.name), tempfile.TemporaryDirectory() as directory:
                self.assertTrue(binary.is_file())
                log = Path(directory) / 'helper.log'
                with log.open('wb') as out:
                    completed = subprocess.run([str(binary), '-c', probe], stdin=subprocess.DEVNULL,
                        stdout=out, stderr=subprocess.STDOUT, creationflags=background_flags(), timeout=15)
                self.assertEqual(completed.returncode, 0, log.read_text())
                state = json.loads(log.read_text())
                self.assertIsNone(state['console'])
                # CREATE_NO_WINDOW may give console-subsystem python.exe its
                # own invisible console. It must never share the parent's.
                self.assertLessEqual(set(state['console_processes']), {state['pid']})


if __name__ == '__main__':
    unittest.main()
