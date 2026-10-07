"""Run only inside disposable Docker/VM: these tests create process trees."""
import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
import process_scope as scopes


class ScopeTests(unittest.TestCase):
    def test_normal_output(self):
        with scopes.bind(uuid.uuid4().hex):
            result = scopes.run([sys.executable, '-c', 'print(123)'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.stdout.strip(), '123')
        self.assertEqual(result.returncode, 0)

    def test_timeout_descendant_holding_pipe(self):
        with scopes.bind(uuid.uuid4().hex):
            t = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                scopes.run([sys.executable, '-c', "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"], capture_output=True, timeout=.3)
            self.assertLess(time.monotonic() - t, 6)

    def test_cancel_and_closed_admission(self):
        name = uuid.uuid4().hex
        with scopes.bind(name):
            proc = scopes.spawn([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.PIPE)
        try:
            receipt = scopes.cancel(name)
            self.assertEqual(receipt['requested'], 1)
            self.assertEqual(receipt['failures'], [])
            proc.communicate(timeout=5)
            with scopes.bind(name), self.assertRaises(RuntimeError):
                scopes.spawn([sys.executable, '-c', 'raise SystemExit(99)'])
        finally:
            scopes.release(proc)

    def test_unrelated_sentinel_survives(self):
        sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            self.assertFalse(scopes.stop_owned(sentinel.pid))
            scopes.cancel(uuid.uuid4().hex)
            self.assertIsNone(sentinel.poll())
        finally:
            sentinel.kill(); sentinel.wait()

    def test_cancel_spawn_race(self):
        name = uuid.uuid4().hex
        barrier = threading.Barrier(2)
        children = []
        def start():
            with scopes.bind(name):
                barrier.wait()
                try:
                    children.append(scopes.spawn([sys.executable, '-c', 'import time; time.sleep(60)']))
                except RuntimeError:
                    pass
        thread = threading.Thread(target=start)
        thread.start(); barrier.wait(); scopes.cancel(name); thread.join(5)
        self.assertFalse(thread.is_alive())
        for p in children:
            p.wait(timeout=5); scopes.release(p)


if __name__ == '__main__':
    unittest.main()


class StepIsolation(unittest.TestCase):
    def test_interrupt_step_keeps_forge_and_future_step(self):
        root = uuid.uuid4().hex
        with scopes.bind(root + '/forge'):
            child = scopes.spawn([sys.executable, '-c', 'import time; time.sleep(60)'])
        with scopes.bind(root + '/step/one'):
            step = scopes.spawn([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            scopes.cancel(root + '/step/one')
            step.wait(timeout=5)
            self.assertIsNone(child.poll())
            with scopes.bind(root + '/step/two'):
                self.assertEqual(scopes.run([sys.executable, '-c', 'pass'],timeout=5).returncode,0)
        finally:
            scopes.cancel(root)
            child.wait(timeout=5)
            scopes.release(child); scopes.release(step)

    def test_normal_exit_cleans_redirected_grandchild(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp, scopes.bind(uuid.uuid4().hex):
            pidfile = Path(tmp)/'pid'
            code = "import subprocess,sys; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);Path(sys.argv[1]).write_text(str(p.pid))"
            self.assertEqual(scopes.run([sys.executable,'-c',code,str(pidfile)],timeout=5).returncode,0)
            pid=int(pidfile.read_text())
            if os.name=='posix':
                p=Path('/proc')/str(pid)/'stat'
                deadline=time.monotonic()+2
                while p.exists() and p.read_text().split()[2]!='Z' and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertTrue(not p.exists() or p.read_text().split()[2]=='Z')
