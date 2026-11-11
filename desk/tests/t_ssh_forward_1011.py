"""Real Windows no-console child and exclusive forwarding-worker lease."""
import ctypes
import json
import os
from pathlib import Path
import sys
import socket
import subprocess
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import ssh_forward


@unittest.skipUnless(os.name == 'nt', 'Windows logon worker')
class ForwardWorker(unittest.TestCase):
    def test_kernel_lease_excludes_duplicates_and_recovers_after_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'worker.lock'
            first = ssh_forward.Lease(path)
            try:
                with self.assertRaises(OSError):ssh_forward.Lease(path)
            finally:first.close()
            second = ssh_forward.Lease(path)
            second.close()

    def test_real_child_has_no_console_and_is_stopped_with_owned_job(self):
        from localharness.desktop_process import JobChild
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            probe = folder / 'probe.py'
            result = folder / 'console.json'
            probe.write_text('import ctypes,json,time\nfrom pathlib import Path\n'
                'Path("console.json").write_text(json.dumps({"console":int(ctypes.windll.kernel32.GetConsoleWindow())}))\n'
                'time.sleep(60)\n', encoding='utf-8')
            child = JobChild.spawn([sys.executable, str(probe)], cwd=folder,
                env=dict(os.environ), log=folder / 'out.log', stopped=lambda:False)
            try:
                end = time.monotonic() + 10
                while not result.exists() and time.monotonic() < end:time.sleep(.05)
                self.assertEqual(json.loads(result.read_text())['console'], 0)
                self.assertIsNone(child.poll())
            finally:child.stop()
            self.assertIsNone(child.handle)
            self.assertFalse(child.job.handle)

    def test_strict_pins_loopback_only_and_optional_socks(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'existing'
            file.write_text('fixture')
            cfg = dict(ssh=str(file), identity=str(file), known_hosts=str(file),
                       user='root', host='192.0.2.1', listen_port=18888, target_port=8888,
                       socks_port=18889)
            argv = ssh_forward.command(cfg)
            for value in ('StrictHostKeyChecking=yes', 'HostKeyAlgorithms=ssh-ed25519',
                          'ExitOnForwardFailure=yes', 'BatchMode=yes',
                          '127.0.0.1:18888:127.0.0.1:8888', '127.0.0.1:18889'):
                self.assertIn(value, argv)
            self.assertEqual(argv[-1], 'root@192.0.2.1')
            for key,value in [('listen_port',0),('listen_port',True),('socks_port',65536),
                              ('host','-oProxyCommand=bad'),('user','root@other')]:
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    ssh_forward.command(dict(cfg, **{key:value}))

    def test_status_replaces_previous_record_without_partial_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            ssh_forward.status(folder,'connecting',attempt=1)
            ssh_forward.status(folder,'retrying',exit_code=255)
            self.assertEqual(json.loads((folder/'status.json').read_text())['exit_code'],255)
            self.assertEqual(list(folder.glob('*.tmp')),[])

    def test_missing_network_at_startup_keeps_windowless_worker_retrying(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as unavailable:
            folder=Path(tmp)
            # Bound but not listening: reliably refused, without touching the
            # real network or borrowing an owner's SSH key.
            unavailable.bind(('127.0.0.1',0))
            fixture=folder/'fixture';fixture.write_text('not a credential')
            cfg=dict(ssh=str(Path(os.environ['WINDIR'])/'System32/OpenSSH/ssh.exe'),
                identity=str(fixture),known_hosts=str(fixture),host='127.0.0.1',user='fixture',
                port=unavailable.getsockname()[1],listen_port=18890,target_port=8888)
            config=folder/'config.json';config.write_text(json.dumps(cfg))
            pythonw=Path(sys.executable).with_name('pythonw.exe')
            if not pythonw.exists():self.skipTest('pythonw unavailable in this runtime')
            worker=subprocess.Popen([str(pythonw),str(Path(ssh_forward.__file__)),
                '--config',str(config)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                end=time.monotonic()+20;state={}
                while time.monotonic()<end:
                    path=folder/'status.json'
                    if path.exists():state=json.loads(path.read_text())
                    if state.get('attempt',0)>=2 and state.get('state')=='retrying':break
                    if worker.poll() is not None:break
                    time.sleep(.1)
                self.assertIsNone(worker.poll(),state)
                self.assertEqual(state.get('state'),'retrying')
                self.assertGreaterEqual(state.get('attempt',0),2)
            finally:
                worker.terminate();worker.wait(10)


if __name__ == '__main__':unittest.main()
