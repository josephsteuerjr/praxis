"""Exercise the real headless executable and its process groups on Linux only.

HELENE_HOST_BINARY=/path/to/helene-host python tests/t_host_linux.py
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

BINARY = os.environ.get("HELENE_HOST_BINARY", "")

class Pipe:
    def __init__(self, root: Path):
        self.proc = subprocess.Popen([BINARY, "--root", str(root)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            env={**os.environ, "HOME": str(root), "XDG_CONFIG_HOME": str(root / "xdg"),
                 "HELENE_LAUNCHER": str(root / "launcher")})
        self.seq = 0
        self.messages = queue.Queue()
        def read():
            for line in self.proc.stdout:
                self.messages.put(json.loads(line))
        threading.Thread(target=read, daemon=True).start()
        self.until(lambda m: m.get("event") == "ready")

    def until(self, predicate):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            message = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            if predicate(message): return message
        raise AssertionError("host pipe timeout")

    def request(self, command, **args):
        self.seq += 1
        self.proc.stdin.write(json.dumps({"id": self.seq, "command": command, "args": args}) + "\n")
        self.proc.stdin.flush()
        return self.until(lambda m: m.get("id") == self.seq)

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.wait(timeout=12)
        if not self.proc.stdin.closed: self.proc.stdin.close()
        self.proc.stdout.close()

@unittest.skipUnless(sys.platform == "linux" and BINARY, "requires real Linux host binary")
class HostContract(unittest.TestCase):
    def fixture(self, root, local=False):
        cfg = {"mode": "remote", "base": "http://127.0.0.1:9", "key": "fixture", "setup_complete": True}
        if local:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
            cfg = {"mode": "local", "setup_complete": True, "python": sys.executable,
                   "app": "fixture.py", "tree": "data", "code": "code", "port": port,
                   "relay": {"enabled": False}}
            (root / "data").mkdir(); (root / "code").mkdir()
            (root / "fixture.py").write_text(
                "import os,subprocess,sys,time\n"
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\n"
                "open('pids','w').write(str(os.getpid())+' '+str(p.pid))\n"
                "time.sleep(120)\n")
        (root / "helene.json").write_text(json.dumps(cfg))
        (root / "launcher").write_text("#!/bin/sh\nexit 0\n")
        return cfg

    def test_rpc_reads_and_preserves_stale_or_broken_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.fixture(root); host = Pipe(root)
            try:
                info = host.request("app_info")["result"]
                self.assertEqual(info["root"], str(root)); self.assertEqual(info["platform"], "linux")
                loaded = host.request("config_load")["result"]
                self.assertEqual(loaded["config"], cfg)
                self.assertIn("error", host.request("config_save", config="{"))
                self.assertEqual(json.loads((root / "helene.json").read_text()), cfg)
                changed = {**cfg, "lang": "ru"}
                saved = host.request("config_save", config=json.dumps(changed), mtimeNs=loaded["mtime_ns"])["result"]
                self.assertTrue(saved["ok"])
                stale = host.request("config_save", config=json.dumps(cfg), mtimeNs=loaded["mtime_ns"])["result"]
                self.assertFalse(stale["ok"]); self.assertEqual(stale["code"], "stale")
                self.assertEqual(json.loads((root / "helene.json").read_text()), changed)
                self.assertIn("error", host.request("not_a_command"))
                self.assertIn("error", host.request("firewall_allow", port=-1))
                self.assertIn("error", host.request("autostart_set", on="false"))
                self.assertTrue(host.request("autostart_set", on=True).get("error") is None)
                self.assertTrue(host.request("autostart_get")["result"])
                host.request("autostart_set", on=False)
                self.assertFalse(host.request("autostart_get")["result"])
            finally: host.close()

    def test_eof_shutdown_and_sigterm_reap_child_process_groups(self):
        for action in ("eof", "shutdown", "sigterm"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); self.fixture(root, local=True); host = Pipe(root)
                try:
                    deadline = time.monotonic() + 8; pids = root / "pids"
                    while not pids.exists() and time.monotonic() < deadline: time.sleep(.03)
                    self.assertTrue(pids.exists(), "real supervised child never started")
                    children = [int(p) for p in pids.read_text().split()]
                    if action == "shutdown": host.request("host_shutdown")
                    elif action == "sigterm": host.proc.send_signal(signal.SIGTERM)
                    else: host.proc.stdin.close()
                    host.proc.wait(timeout=12)
                    for pid in children:
                        status = Path(f"/proc/{pid}/status")
                        if status.exists(): self.assertIn("State:\tZ", status.read_text(), f"orphan process {pid}")
                finally: host.close()

    def test_owner_stop_resume_and_restart_have_real_pid_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = self.fixture(root, local=True)
            cfg["runner"] = "runner.py"
            (root / "helene.json").write_text(json.dumps(cfg))
            (root / "runner.py").write_text(
                "import json,os,subprocess,sys,time\nfrom pathlib import Path\n"
                "tree=Path('data'); receipt=tree/'memory/.control/desk_inbox/.reader.json'\n"
                "receipt.parent.mkdir(parents=True,exist_ok=True)\n"
                "receipt.write_text(json.dumps({'pid':os.getpid(),'at':time.time()}))\n"
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\n"
                "(tree/'grandchild.pid').write_text(str(p.pid))\n"
                "ask=tree/'memory/.state/supervisor-request.json'\n"
                "while True:\n"
                " if ask.exists():\n"
                "  ask.unlink(); sys.exit(6)\n"
                " time.sleep(.05)\n")
            host = Pipe(root)
            def wait_for(predicate):
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    value = host.request("owner_state")["result"]
                    if predicate(value): return value
                    time.sleep(.1)
                self.fail("owner state never confirmed lifecycle operation")
            try:
                before = wait_for(lambda s: s["runner_alive"] is True)
                pid = before["pid"]
                restarted = host.request("engine_restart")
                self.assertNotIn("error", restarted)
                after = wait_for(lambda s: s["runner_alive"] is True and s["pid"] != pid)
                self.assertNotEqual(pid, after["pid"])
                grandchild = int((root / "data/grandchild.pid").read_text())
                self.assertNotIn("error", host.request("owner_control", action="panic"))
                stopped = wait_for(lambda s: s["stopped"] and s["runner_alive"] is False)
                status = Path(f"/proc/{grandchild}/status")
                if status.exists(): self.assertIn("State:\tZ", status.read_text(), "stop left a running descendant")
                self.assertIn("error", host.request("engine_restart"))
                self.assertNotIn("error", host.request("owner_control", action="resume"))
                resumed = wait_for(lambda s: not s["stopped"] and s["runner_alive"] is True)
                self.assertNotEqual(stopped["pid"], resumed["pid"])
            finally: host.close()

    def test_parent_crash_reaps_host_and_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.fixture(root, local=True)
            code = ("import json,subprocess,sys,time\n"
                "p=subprocess.Popen(sys.argv[1:],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)\n"
                "for line in p.stdout:\n"
                " if json.loads(line).get('event')=='ready': break\n"
                "print(p.pid,flush=True)\n"
                "time.sleep(120)\n")
            parent = subprocess.Popen([sys.executable, "-u", "-c", code, BINARY, "--root", str(root)],
                stdout=subprocess.PIPE, text=True)
            try:
                pid = int(parent.stdout.readline())
                pids = root / "pids"; deadline = time.monotonic() + 8
                while not pids.exists() and time.monotonic() < deadline: time.sleep(.03)
                self.assertTrue(pids.exists())
                children = [int(p) for p in pids.read_text().split()]
                parent.kill(); parent.wait(timeout=5)
                deadline = time.monotonic() + 12
                def alive(pid):
                    status = Path(f"/proc/{pid}/status")
                    return status.exists() and "State:\tZ" not in status.read_text()
                while any(alive(p) for p in [pid, *children]) and time.monotonic() < deadline: time.sleep(.1)
                self.assertFalse(any(alive(p) for p in [pid, *children]), "crashed window left descendants")
            finally:
                if parent.poll() is None: parent.kill(); parent.wait(timeout=5)
                parent.stdout.close()

if __name__ == "__main__": unittest.main(verbosity=2)
