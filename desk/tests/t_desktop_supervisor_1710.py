# -*- coding: utf-8 -*-
"""Python-owned desktop supervisor source and isolated Windows consumer fixture.

Run from desk/: python tests/t_desktop_supervisor_1710.py
No installed Hélène, global Stop markers or scheduled tasks are changed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / "localharness"))
if sys.platform == "win32":
    import msvcrt
    from desktop_supervisor import Coordinator, Spec, _config, _desktop_status, _plans, _relay_login_generation, _specs, _tree_hold, run  # noqa: E402
    from desktop_process import CustodyFault, JobCreationRefused, JobChild  # noqa: E402
    import desktop_process  # noqa: E402
    import desktop_supervisor  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "Windows Job policy")
class Policy(unittest.TestCase):
    def setUp(self):
        self.engine = Coordinator({"main:channel": "channel", "main:runner": "runner",
                                   "main:relay": "relay"})
        self.seq = 0

    def tick(self, now, **statuses):
        self.seq += 1
        children = {key: {"state": statuses.get(key, "absent")} for key in self.engine.children}
        for key, value in statuses.items():
            children[key] = value if isinstance(value, dict) else {"state": value}
        return self.engine.tick({"seq": self.seq, "now_ms": now, "children": children})

    def test_initial_spawn_once_and_crash_backoff(self):
        self.assertEqual({row["id"] for row in self.tick(0)}, set(self.engine.children))
        self.assertEqual(self.tick(1, **{key: "alive" for key in self.engine.children}), [])
        self.assertEqual(self.tick(100, **{"main:runner": {"state": "exited", "code": 1},
                                         "main:channel": "alive", "main:relay": "alive"}), [])
        self.assertEqual(self.tick(1099, **{"main:channel": "alive", "main:relay": "alive"}), [])
        self.assertEqual(self.tick(1100, **{"main:channel": "alive", "main:relay": "alive"}),
                         [{"id": "main:runner", "action": "spawn"}])

    def test_runner_restart_replaces_channel_and_halts_bad_configuration(self):
        self.tick(0)
        self.tick(1, **{key: "alive" for key in self.engine.children})
        actions = self.tick(2, **{"main:runner": {"state": "exited", "code": 42},
                                  "main:channel": "alive", "main:relay": "alive"})
        self.assertIn({"id": "main:channel", "action": "stop"}, actions)
        self.assertIn({"id": "main:runner", "action": "spawn"}, actions)
        self.tick(3, **{"main:runner": "alive", "main:relay": "alive"})
        self.assertEqual(self.tick(4, **{"main:runner": {"state": "exited", "code": 3},
                                        "main:channel": "alive", "main:relay": "alive"}), [])
        self.assertEqual(self.tick(99999, **{"main:channel": "alive", "main:relay": "alive"}), [])

    def test_busy_tree_six_bounded_retries(self):
        for n in range(1, 8):
            now = n * 11000
            actions = self.tick(now, **{"main:runner": {"state": "exited", "code": 4},
                                         "main:channel": "alive", "main:relay": "alive"})
            self.assertNotIn({"id": "main:runner", "action": "spawn"}, actions)
            actions = self.tick(now + 10000, **{"main:channel": "alive", "main:relay": "alive"})
            self.assertEqual({"id": "main:runner", "action": "spawn"} in actions, n <= 6)

    def test_relay_port_wait_not_a_crash(self):
        self.tick(0)
        self.tick(1, **{"main:channel": "alive", "main:runner": "alive", "main:relay": "waiting"})
        self.assertEqual(self.engine.children["main:relay"].falls, [])
        self.assertEqual(self.tick(20000, **{"main:channel": "alive", "main:runner": "alive"}), [])
        self.assertEqual(self.tick(20001, **{"main:channel": "alive", "main:runner": "alive"}),
                         [{"id": "main:relay", "action": "spawn"}])

    def test_reject_incomplete_and_stale_snapshots(self):
        with self.assertRaises(ValueError):
            self.engine.tick({"seq": 1, "now_ms": 0, "children": {}})
        self.tick(100)
        with self.assertRaises(ValueError):
            self.engine.tick({"seq": 1, "now_ms": 101,
                              "children": {key: {"state": "absent"} for key in self.engine.children}})

    def test_config_utf16_and_corrupt_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "helene.json"
            path.write_bytes('{"mode":"local"}'.encode("utf-16"))
            self.assertEqual(_config(path)["mode"], "local")
            path.write_bytes(b'{"mode":')
            with self.assertRaises(ValueError):
                _config(path)

    def test_status_replace_sharing_denial_retries_without_false_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            nonce = "a" * 32
            path = tree / ".python-desktop-status"
            _desktop_status(tree, nonce, 1, None, {"main": (tree, {})}, {})
            first = path.read_bytes()
            original_replace = os.replace
            attempts = 0

            def sharing_race(source, target):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError(13, "reader briefly holds target")
                return original_replace(source, target)

            with patch.object(desktop_supervisor.os, "replace", side_effect=sharing_race):
                _desktop_status(tree, nonce, 2, "refresh-2", {"main": (tree, {})}, {})
            self.assertEqual(attempts, 3)
            self.assertEqual(json.loads(path.read_bytes())["ack"], "refresh-2")
            with patch.object(desktop_supervisor.os, "replace",
                              side_effect=PermissionError(13, "persistent denial")):
                with self.assertRaises(PermissionError):
                    _desktop_status(tree, nonce, 3, "never-ack", {"main": (tree, {})}, {})
            self.assertNotEqual(path.read_bytes(), first)
            self.assertEqual(json.loads(path.read_bytes())["ack"], "refresh-2")
            self.assertEqual(list(tree.glob(".python-desktop-status.*.tmp")), [])

    def test_relay_plan_keeps_key_out_of_arguments_and_names_one_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "app").mkdir()
            (base / "helene-relay.exe").write_bytes(b"fixture only")
            (base / "app/deskapp.py").write_text("# fixture", encoding="utf-8")
            cfg = base / "helene.json"
            cfg.write_text(json.dumps({"mode": "local", "app": "app/deskapp.py",
                                       "python": sys.executable, "port": 8094,
                                       "relay": {"enabled": True, "key": "private-fixture-key"}}),
                           encoding="utf-8")
            tree, specs = _specs(cfg)
            self.assertEqual(set(specs), {"main:channel", "main:relay"})
            self.assertEqual(specs["main:relay"].env["RELAY_API_KEY"], "private-fixture-key")
            self.assertNotIn("private-fixture-key", repr(specs["main:relay"].argv))
            self.assertEqual(specs["main:channel"].env["HELENE_CONFIG"], str(cfg))
            cfg.write_text(json.dumps({"mode": "local", "app": "app/deskapp.py",
                                       "python": sys.executable, "relay": {"enabled": True, "key": "   "},
                                       "model": {"key": "legacy-fixture-key"}}), encoding="utf-8")
            _, fallback_specs = _specs(cfg)
            self.assertEqual(fallback_specs["main:relay"].env["RELAY_API_KEY"],
                             "legacy-fixture-key", "whitespace relay key must use text legacy fallback")
            cfg.write_text(json.dumps({"mode": "local", "app": "app/deskapp.py", "python": "python",
                                       "port": 8094, "relay": {"enabled": False},
                                       "images": {"enabled": True},
                                       "model": {"key": "other-provider-secret"}}), encoding="utf-8")
            with patch.dict(os.environ, {"RELAY_API_KEY": "ambient-relay-secret",
                                         "HELENE_TOKEN": "ambient-channel-secret"}):
                _, image_specs = _specs(cfg)
            self.assertEqual(image_specs["main:channel"].argv[0], sys.executable)
            self.assertIn("main:relay", image_specs)
            self.assertNotIn("RELAY_API_KEY", image_specs["main:relay"].env,
                             "image-only relay borrowed ambient or model credential")
            self.assertNotIn("HELENE_TOKEN", image_specs["main:relay"].env)

    def test_precreate_log_error_closes_job_before_retry(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(desktop_process, "ChildJob") as job, \
                patch.object(Path, "mkdir", side_effect=PermissionError("log denied")):
            with self.assertRaises(PermissionError):
                JobChild.spawn([sys.executable, "-c", "pass"], cwd=Path(tmp), env={},
                               log=Path(tmp) / "nested/child.log", stopped=lambda: False)
            job.return_value.close.assert_called_once()

    def test_job_creation_refusal_proves_no_process_was_started(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(desktop_process, "ChildJob", side_effect=OSError("no Job")), \
                patch.object(desktop_process._winapi, "CreateProcess") as create:
            with self.assertRaises(JobCreationRefused):
                JobChild.spawn([sys.executable, "-c", "pass"], cwd=Path(tmp), env={},
                               log=Path(tmp) / "child.log", stopped=lambda: False)
            create.assert_not_called()

    def test_planted_lock_symlink_never_overwrites_its_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            tree.mkdir()
            target = Path(tmp) / "other-file"
            target.write_bytes(b"original bytes")
            try:
                (tree / ".desktop-owner.lock").symlink_to(target)
            except OSError as exc:
                self.skipTest(f"Windows symlink creation unavailable: {exc}")
            with self.assertRaises((RuntimeError, OSError)):
                with _tree_hold(tree): pass
            self.assertEqual(target.read_bytes(), b"original bytes")

    def test_desktop_package_contains_importable_owner_and_captured_bytes(self):
        import hashlib
        sys.path.insert(0, str(DESK))
        import deskpkg
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "app"
            manifest = deskpkg.build(stage, deskpkg.WINDOWS, allow_partial=True, log=lambda _: None)
            for name in ("desktop_supervisor.py", "desktop_process.py", "agents.py", "owner_stop.py"):
                source = DESK / "localharness" / name
                target = stage / "localharness" / name
                self.assertEqual(source.read_bytes(), target.read_bytes())
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                                 manifest["files"][f"localharness/{name}"])
            imported = subprocess.run([sys.executable, "-c", "import desktop_supervisor,desktop_process,agents,owner_stop"],
                                      cwd=stage / "localharness", capture_output=True, timeout=15)
            self.assertEqual(imported.returncode, 0, imported.stderr.decode("utf-8", "replace"))

    def test_session_roster_keeps_tree_port_token_and_remote_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "app").mkdir()
            (base / "app/deskapp.py").write_text("# fixture", encoding="utf-8")
            (base / "app/runner.py").write_text("# fixture", encoding="utf-8")
            (base / "helene-relay.exe").write_bytes(b"only an inventory fixture")
            (base / "helene.json").write_text(json.dumps({"mode": "local", "python": sys.executable,
                "app": "app/deskapp.py", "runner": "app/runner.py", "tree": "data", "port": 8094,
                "installed": {"service": True}}), encoding="utf-8")
            neighbor = base / "agents/neighbor"
            neighbor.mkdir(parents=True)
            (neighbor / "helene.json").write_text(json.dumps({"mode": "local", "python": sys.executable,
                "app": "../../app/deskapp.py", "runner": "../../app/runner.py", "tree": "data",
                "port": 8095, "setup_complete": True, "relay": {"enabled": True, "key": "fixture-key"}}), encoding="utf-8")
            remote = base / "agents/remote"
            remote.mkdir(parents=True)
            (remote / "helene.json").write_text(json.dumps({"mode": "remote", "port": 8096}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "remote mode"):
                _plans(base / "helene.json", session_host=True)
            (remote / "helene.json").write_text(json.dumps({"mode": "remote", "port": 8096,
                                                              "enabled": False}), encoding="utf-8")
            plans = _plans(base / "helene.json", session_host=True)
            self.assertEqual(set(plans), {"main", "neighbor"})
            main, peer = plans["main"][1], plans["neighbor"][1]
            self.assertEqual(peer["neighbor:runner"].env["HELENE_CONFIG"], str(neighbor / "helene.json"))
            self.assertNotEqual(main["main:runner"].env["HELENE_TOKEN"],
                                peer["neighbor:runner"].env["HELENE_TOKEN"])
            self.assertEqual(peer["neighbor:channel"].port, 8095)
            self.assertEqual(peer["neighbor:relay"].argv[0], str(base / "helene-relay.exe"))
            peer_cfg = neighbor / "helene.json"
            changed = json.loads(peer_cfg.read_text(encoding="utf-8"))
            changed["relay"]["enabled"] = False
            peer_cfg.write_text(json.dumps(changed), encoding="utf-8")
            self.assertNotIn("neighbor:relay", _plans(base / "helene.json", session_host=True)["neighbor"][1])
            changed["tree"] = "../../data"  # a different port cannot justify sharing main's data
            peer_cfg.write_text(json.dumps(changed), encoding="utf-8")
            self.assertNotIn("neighbor", _plans(base / "helene.json", session_host=True))
            changed["tree"] = "data"
            changed["agent"] = "malformed nested config"
            peer_cfg.write_text(json.dumps(changed), encoding="utf-8")
            self.assertEqual(set(_plans(base / "helene.json", session_host=True)), {"main"},
                             "one malformed neighbor must not kill the base owner")
            changed.pop("agent")
            changed["telegram"] = 42
            peer_cfg.write_text(json.dumps(changed), encoding="utf-8")
            self.assertEqual(set(_plans(base / "helene.json", session_host=True)), {"main"})

    def test_desktop_roster_keeps_local_neighbors_but_refuses_service_and_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "app").mkdir()
            (base / "app/deskapp.py").write_text("# fixture", encoding="utf-8")
            (base / "helene.json").write_text(json.dumps({
                "mode": "local", "app": "app/deskapp.py", "python": sys.executable,
                "tree": "data", "installed": {"service": False}}), encoding="utf-8")
            peer = base / "agents/peer"
            peer.mkdir(parents=True)
            config = peer / "helene.json"
            data = {"mode": "local", "app": "../../app/deskapp.py", "python": sys.executable,
                    "tree": "data", "port": 8095}
            config.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(set(_plans(base / "helene.json", session_host=False)), {"main"})
            self.assertEqual(set(_plans(base / "helene.json", session_host=False,
                                         desktop_roster=True)), {"main", "peer"})
            data["mode"] = "remote"
            config.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "remote mode"):
                _plans(base / "helene.json", session_host=False, desktop_roster=True)
            data["enabled"] = False
            config.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(set(_plans(base / "helene.json", session_host=False,
                                         desktop_roster=True)), {"main"})
            root = json.loads((base / "helene.json").read_text(encoding="utf-8"))
            root["installed"]["service"] = True
            (base / "helene.json").write_text(json.dumps(root), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "service installation"):
                _plans(base / "helene.json", session_host=False, desktop_roster=True)

    def test_relay_login_generation_is_mtime_and_content_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = Spec("relay", ("fake-relay", "serve"), root, {}, root / "log", 5011)
            self.assertIsNone(_relay_login_generation(spec))
            marker = root / "local_auth/login-generation"
            marker.parent.mkdir()
            marker.write_bytes(b"account one")
            first = _relay_login_generation(spec)
            self.assertEqual(first[2], b"account one")
            marker.write_bytes(b"account two")
            self.assertNotEqual(_relay_login_generation(spec), first)
            marker.write_bytes(b"x" * 1025)
            with self.assertRaisesRegex(ValueError, "too large"):
                _relay_login_generation(spec)


@unittest.skipUnless(sys.platform == "win32", "Windows Job fixture")
class WindowsForeground(unittest.TestCase):
    """The OS consumer, with no installed-service/global-marker mutations."""

    def setUp(self):
        try:
            import psutil
        except ImportError:
            self.skipTest("psutil needed to attest descendant death")
        self.psutil = psutil
        self.home = tempfile.TemporaryDirectory(prefix="helene-python-owner-")
        self.base = Path(self.home.name)
        shutil.copytree(DESK / "localharness", self.base / "app/localharness",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.tree = self.base / "data"
        self.tree.mkdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        (self.base / "helene.json").write_text(json.dumps({
            "mode": "local", "python": sys.executable,
            "app": "app/deskapp.py", "runner": "app/runner.py", "tree": "data",
            "port": self.port, "relay": {"enabled": False}, "installed": {"service": False},
        }), encoding="utf-8")
        (self.base / "app/deskapp.py").write_text(
            "import http.server, json, os, pathlib, sys, time\n"
            "tree=pathlib.Path(os.environ['HELENE_TREE'])\n"
            "if os.environ.get('FAKE_CHANNEL_FAIL_ONCE'):\n"
            " marker=tree/'failed-first-channel'\n"
            " if not marker.exists(): marker.write_text('first launch failed');sys.exit(2)\n"
            "class H(http.server.BaseHTTPRequestHandler):\n"
            " def do_GET(self):\n"
            "  if not self.path.startswith('/api/home'): self.send_error(404); return\n"
            "  body=json.dumps({'tree':str(tree)}).encode()\n"
            "  self.send_response(200); self.send_header('Content-Type','application/json')\n"
            "  self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)\n"
            " def log_message(self,*args): pass\n"
            "time.sleep(float(os.environ.get('FAKE_CHANNEL_BIND_DELAY','0')))\n"
            "if os.environ.get('FAKE_CHANNEL_DELAY_AFTER_FIRST'):\n"
            " marker=tree/'channel-first-started'\n"
            " if marker.exists(): time.sleep(4)\n"
            " else: marker.write_text('first launch')\n"
            "class Server(http.server.ThreadingHTTPServer): allow_reuse_address=False\n"
            "server=Server(('127.0.0.1',int(sys.argv[1])),H)\n"
            "(tree/'channel.pid').write_text(str(os.getpid()))\n"
            "server.serve_forever()\n", encoding="utf-8")
        (self.base / "app/runner.py").write_text(
            "import os, pathlib, subprocess, sys, time\n"
            "tree=pathlib.Path(os.environ['HELENE_TREE'])\n"
            "child=subprocess.Popen([sys.executable,'-c',"
            "'import os,pathlib,time,sys;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(120)',"
            "str(tree/'grandchild.pid')],stdin=subprocess.DEVNULL)\n"
            "(tree/'runner.pid').write_text(str(os.getpid()))\n"
            "while True: time.sleep(.1)\n", encoding="utf-8")
        self.owner = None
        self.script = self.base / "app/localharness/desktop_supervisor.py"

    def tearDown(self):
        if self.owner is not None:
            if self.owner.poll() is None:
                self.owner.kill()
            self.owner.communicate(timeout=10)
        self.home.cleanup()

    def wait_for(self, predicate, seconds=12):
        limit = time.monotonic() + seconds
        while time.monotonic() < limit:
            if predicate(): return
            if self.owner.poll() is not None: break
            time.sleep(.1)
        self.fail(f"fixture did not converge; exit={self.owner.poll()} stderr={self.owner.stderr.read(2000) if self.owner.poll() is not None else 'running'}")

    def start(self, *extra):
        self.owner = subprocess.Popen([sys.executable, "-u", str(self.script),
                                       "--config", str(self.base / "helene.json"), *extra],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

    def add_neighbor(self, agent_id="neighbor", *, mode="local"):
        agent = self.base / "agents" / agent_id
        agent.mkdir(parents=True)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        (agent / "helene.json").write_text(json.dumps({
            "mode": mode, "python": sys.executable,
            "app": "../../app/deskapp.py", "runner": "../../app/runner.py",
            "tree": "data", "port": port, "setup_complete": True,
        }), encoding="utf-8")
        return agent / "data", port

    def start_native(self):
        binary = DESK / "svc/target/debug/helene-svc.exe"
        if not binary.is_file():
            self.skipTest("build desk/svc with cargo build --offline")
        self.owner = subprocess.Popen([str(binary), "foreground", "--config",
                                       str(self.base / "helene.json"), "--python-supervisor"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

    def other_config(self):
        # A second channel port on the SAME tree defeats every port-only gate.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            other_port = probe.getsockname()[1]
        cfg = json.loads((self.base / "helene.json").read_text(encoding="utf-8"))
        cfg["port"] = other_port
        path = self.base / "other-helene.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def legacy(self, config):
        binary = DESK / "svc/target/debug/helene-svc.exe"
        if not binary.is_file(): self.skipTest("build desk/svc with cargo build --offline")
        return subprocess.Popen([str(binary), "foreground", "--config", str(config)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

    def identities(self):
        paths = [self.tree / name for name in ("channel.pid", "runner.pid", "grandchild.pid")]
        self.wait_for(lambda: all(p.is_file() for p in paths))
        return [int(p.read_text()) for p in paths] + [self.owner.pid]

    def desktop_status(self, nonce):
        status = self.tree / ".python-desktop-status"
        def read():
            # The Windows rename replacing this status can deny one open even
            # though neither generation nor content is bad. Match the bounded
            # production reader, never mask a persistent failure.
            until = time.monotonic() + .3
            while True:
                try: return json.loads(status.read_text(encoding="utf-8"))
                except (PermissionError, FileNotFoundError):
                    if time.monotonic() >= until: raise
                    time.sleep(.02)
        self.wait_for(lambda: status.is_file() and read().get("nonce") == nonce)
        return read()

    def start_roster(self, nonce="a" * 32):
        self.owner = subprocess.Popen([sys.executable, "-u", str(self.script),
                                       "--config", str(self.base / "helene.json"),
                                       "--desktop-roster", "--status-nonce", nonce,
                                       "--control-stdin"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        return nonce

    def refresh_roster(self, nonce, command_id):
        self.owner.stdin.write((json.dumps({"action": "refresh", "nonce": nonce,
                                           "id": command_id}) + "\n").encode())
        self.owner.stdin.flush()
        self.wait_for(lambda: self.desktop_status(nonce).get("ack") == command_id)
        return self.desktop_status(nonce)

    def test_desktop_roster_generation_status_disable_and_restart(self):
        neighbor_tree, _ = self.add_neighbor()
        nonce = self.start_roster()
        base_ids = self.identities()[:3]
        self.wait_for(lambda: all((neighbor_tree / name).is_file()
                        for name in ("channel.pid", "runner.pid", "grandchild.pid")))
        peer_ids = [int((neighbor_tree / name).read_text()) for name in
                    ("channel.pid", "runner.pid", "grandchild.pid")]
        status = self.desktop_status(nonce)
        self.assertTrue(status["agents"]["main"]["raised"])
        self.assertTrue(status["agents"]["neighbor"]["raised"])
        peer = self.base / "agents/neighbor/helene.json"
        cfg = json.loads(peer.read_text(encoding="utf-8"))
        cfg["enabled"] = False
        peer.write_text(json.dumps(cfg), encoding="utf-8")
        stopped = self.refresh_roster(nonce, "b" * 32)
        self.assertNotIn("neighbor", stopped["agents"])
        self.assert_dead(peer_ids)
        self.assertEqual((neighbor_tree / ".desktop-owner.lock").read_bytes(), b"clean\n")
        self.assertTrue(all(self.psutil.pid_exists(pid) for pid in base_ids))
        cfg["enabled"] = True
        peer.write_text(json.dumps(cfg), encoding="utf-8")
        restarted = self.refresh_roster(nonce, "c" * 32)
        self.assertTrue(restarted["agents"]["neighbor"]["raised"])
        self.owner.stdin.write(b"stop\n")
        self.owner.stdin.flush()
        self.owner.wait(timeout=8)
        self.assert_dead(base_ids)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"clean\n")

    def test_desktop_roster_remote_refuses_before_child_launch(self):
        self.add_neighbor(mode="remote")
        self.start_roster()
        self.owner.wait(timeout=8)
        self.assertNotEqual(self.owner.returncode, 0)
        self.assertFalse((self.tree / "channel.pid").exists())
        self.assertFalse((self.tree / ".python-desktop-status").exists())

    def test_source_built_shell_host_python_owner_status_stop_and_refusals(self):
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file():
            self.skipTest("build shell --no-default-features --features host --bin helene-host")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        neighbor_tree, _ = self.add_neighbor()
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base), "--python-supervisor"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        ids = self.identities()
        self.wait_for(lambda: (neighbor_tree / "channel.pid").is_file())
        peer_ids = [int((neighbor_tree / name).read_text()) for name in
                    ("channel.pid", "runner.pid", "grandchild.pid")]
        status_path = self.tree / ".python-desktop-status"
        self.wait_for(status_path.is_file)
        original_nonce = json.loads(status_path.read_text(encoding="utf-8"))["nonce"]
        self.assertTrue(json.loads(status_path.read_text(encoding="utf-8"))["agents"]["neighbor"]["raised"])
        from queue import Queue
        from threading import Thread
        replies = Queue()
        def read_responses():
            for line in self.owner.stdout:
                row = json.loads(line)
                if "id" in row: replies.put(row)
        Thread(target=read_responses, daemon=True).start()
        def command(ordinal, name, args=None):
            self.owner.stdin.write((json.dumps({"id": ordinal, "command": name,
                                               "args": args or {}}) + "\n").encode())
            self.owner.stdin.flush()
            response = replies.get(timeout=15)
            self.assertEqual(response["id"], ordinal, response)
            self.assertNotIn("error", response, response)
            return response["result"]
        before = command(1, "agents_list")
        self.assertTrue(next(row for row in before["agents"] if row["id"] == "neighbor")["raised"])
        # A service install/removal would introduce a second owner. The
        # command must refuse before touching the shared registry or SCM.
        for ordinal, operation in ((101, "install_service"), (102, "remove_service")):
            self.owner.stdin.write((json.dumps({"id": ordinal, "command": operation,
                                               "args": {}}) + "\n").encode())
            self.owner.stdin.flush()
            refused = replies.get(timeout=15)
            self.assertEqual(refused["id"], ordinal)
            self.assertIn("Python desktop owner is selected", refused.get("error", ""))
        self.assertTrue(command(2, "agent_enabled_set", {"id": "neighbor", "enabled": False})["ok"])
        self.wait_for(lambda: not any(self.psutil.pid_exists(pid) for pid in peer_ids))
        self.assertTrue(all(self.psutil.pid_exists(pid) for pid in ids[:3]))
        after = command(3, "agents_list")
        self.assertFalse(next(row for row in after["agents"] if row["id"] == "neighbor")["raised"])
        self.assertTrue(command(4, "agent_enabled_set", {"id": "neighbor", "enabled": True})["ok"])
        self.wait_for(lambda: (neighbor_tree / "channel.pid").read_text() != str(peer_ids[0]))
        self.wait_for(lambda: (neighbor_tree / "runner.pid").read_text() != str(peer_ids[1]))
        self.wait_for(lambda: (neighbor_tree / "grandchild.pid").read_text() != str(peer_ids[2]))
        revived = [int((neighbor_tree / name).read_text()) for name in
                   ("channel.pid", "runner.pid", "grandchild.pid")]
        # Outside this window, the config is disabled while its Jobs still
        # exist. Remove must command Python's measured retirement first, not
        # mistake channel-only `raised` for an empty process tree.
        peer_config = self.base / "agents/neighbor/helene.json"
        peer = json.loads(peer_config.read_text(encoding="utf-8"))
        peer["enabled"] = False
        peer_config.write_text(json.dumps(peer), encoding="utf-8")
        self.assertTrue(command(6, "agent_remove", {"id": "neighbor"})["ok"])
        self.assert_dead(revived)
        self.owner.stdin.write((json.dumps({"id": 7, "command": "host_shutdown", "args": {}}) + "\n").encode())
        self.owner.stdin.flush()
        self.owner.wait(timeout=15)
        self.assertEqual(self.owner.returncode, 0)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"clean\n")
        self.assert_dead(ids + peer_ids)
        self.assertEqual(json.loads(status_path.read_text(encoding="utf-8"))["nonce"], original_nonce)

    def test_source_host_restart_retires_renderer_before_stopping_jobs(self):
        from queue import Queue
        from threading import Thread
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file(): self.skipTest("build source helene-host first")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base), "--python-supervisor"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        events = Queue()
        live = []
        def read_events():
            for line in self.owner.stdout:
                row = json.loads(line)
                if row.get("event") == "retire-window":
                    live.append(all(self.psutil.pid_exists(pid) for pid in ids[:3]))
                    ack = {"id": 98, "command": "window_retire_ack", "args": {"retireId": row["data"]["id"]}}
                    self.owner.stdin.write((json.dumps(ack) + "\n").encode())
                    self.owner.stdin.flush()
                events.put(row)
        Thread(target=read_events, daemon=True).start()
        self.assertEqual(events.get(timeout=16).get("event"), "ready")
        ids = self.identities()
        self.owner.stdin.write((json.dumps({"id": 1, "command": "restart_self"}) + "\n").encode())
        self.owner.stdin.flush()
        retiring = events.get(timeout=8)
        self.assertEqual(retiring.get("event"), "retire-window", retiring)
        next_event = events.get(timeout=12)
        if next_event.get("id") == 98: next_event = events.get(timeout=12)
        self.assertEqual(next_event.get("event"), "relaunch", next_event)
        self.assertEqual(live, [True], "Job tree stopped before renderer retirement ACK")
        self.assert_dead(ids[:3])
        self.owner.stdin.write((json.dumps({"id": 2, "command": "host_shutdown"}) + "\n").encode())
        self.owner.stdin.flush()
        self.owner.wait(timeout=12)

    def test_source_host_restart_without_renderer_ack_never_stops_jobs_first(self):
        from queue import Queue
        from threading import Thread
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file(): self.skipTest("build source helene-host first")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base), "--python-supervisor"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        events = Queue()
        def read_events():
            for line in self.owner.stdout: events.put(json.loads(line))
        Thread(target=read_events, daemon=True).start()
        self.assertEqual(events.get(timeout=16).get("event"), "ready")
        ids = self.identities()
        self.owner.stdin.write((json.dumps({"id": 1, "command": "restart_self"}) + "\n").encode())
        self.owner.stdin.flush()
        retiring = events.get(timeout=8)
        self.assertEqual(retiring.get("event"), "retire-window", retiring)
        failed = events.get(timeout=8)
        self.assertEqual(failed.get("event"), "exit", failed)
        self.assertTrue(all(self.psutil.pid_exists(pid) for pid in ids[:3]),
                        "restart stopped the Python Jobs before physical renderer ACK")
        self.owner.stdin.write((json.dumps({"id": 2, "command": "host_shutdown"}) + "\n").encode())
        self.owner.stdin.flush()
        self.owner.wait(timeout=12)
        self.assert_dead(ids[:3])

    def test_python_first_channel_failure_recovers_before_window_address(self):
        """The first status may say no channel while Python is retrying."""
        from queue import Queue
        from threading import Thread
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file(): self.skipTest("build source helene-host first")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base), "--python-supervisor"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE,
                                      env=dict(os.environ, FAKE_CHANNEL_FAIL_ONCE="1"),
                                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        events = Queue()
        def read_events():
            for line in self.owner.stdout: events.put(json.loads(line))
        Thread(target=read_events, daemon=True).start()
        ready = events.get(timeout=16)
        self.assertEqual(ready.get("event"), "ready", ready)
        self.assertTrue((self.tree / "failed-first-channel").is_file())
        self.assertNotIn("python-owner-refused", ready["data"]["script"])
        self.identities()
        self.owner.stdin.write((json.dumps({"id": 1, "command": "host_shutdown"}) + "\n").encode())
        self.owner.stdin.flush()
        self.owner.wait(timeout=12)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"clean\n")

    def test_python_live_job_never_attaches_window_to_foreign_port(self):
        """The child's active Job is not a socket identity or a UI address."""
        import http.server
        from queue import Queue
        from threading import Thread
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file(): self.skipTest("build source helene-host first")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        env = dict(os.environ, FAKE_CHANNEL_BIND_DELAY="5")
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base), "--python-supervisor"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=env,
                                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        # Python has already recorded its spawned child, but that child has
        # not yet bound. Place a different listener into that window.
        self.wait_for(lambda: (self.tree / ".python-desktop-status").is_file(), seconds=6)
        foreign = str(self.base / "foreign")
        class Other(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"tree": foreign}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass
        class ExclusiveServer(http.server.ThreadingHTTPServer):
            allow_reuse_address = False
        listener = ExclusiveServer(("127.0.0.1", self.port), Other)
        Thread(target=listener.serve_forever, daemon=True).start()
        try:
            events = Queue()
            def read_events():
                for line in self.owner.stdout:
                    events.put(json.loads(line))
            Thread(target=read_events, daemon=True).start()
            ready = events.get(timeout=18)
            self.assertEqual(ready.get("event"), "ready", ready)
            script = ready["data"]["script"]
            self.assertIn("python-owner-refused", script)
            self.assertNotIn(f"http://127.0.0.1:{self.port}", script)
            self.owner.stdin.write((json.dumps({"id": 1, "command": "host_shutdown"}) + "\n").encode())
            self.owner.stdin.flush()
            self.owner.wait(timeout=12)
            self.assertNotEqual(script.find("python-owner-refused"), -1)
        finally:
            listener.shutdown()
            listener.server_close()

    def test_source_built_shell_host_refuses_installed_without_spawning(self):
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file(): self.skipTest("build source helene-host first")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        root["installed"]["service"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base), "--python-supervisor"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        output, errors = self.owner.communicate((json.dumps({"id": 1, "command": "host_shutdown"}) + "\n").encode(), timeout=15)
        self.assertFalse((self.tree / "channel.pid").exists())
        self.assertFalse((self.tree / ".python-desktop-status").exists())
        self.assertNotIn("Python owns child tree", errors.decode("utf-8", "replace"))

    def test_source_built_shell_host_remote_refuses_before_token_or_children(self):
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file(): self.skipTest("build source helene-host first")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        neighbor_tree, _ = self.add_neighbor(mode="remote")
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base), "--python-supervisor"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        output, errors = self.owner.communicate((json.dumps({"id": 1, "command": "host_shutdown"}) + "\n").encode(), timeout=15)
        self.assertFalse((self.tree / "channel.pid").exists())
        self.assertFalse((self.tree / "memory/.state/desk-token").exists())
        self.assertFalse((neighbor_tree / ".desktop-owner.lock").exists())
        self.assertFalse((self.tree / ".python-desktop-status").exists())

    def test_source_built_shell_host_default_keeps_native_children(self):
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file(): self.skipTest("build source helene-host first")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        # The legacy route still launches the two native-managed children;
        # unlike Python mode, this source probe does not attest that the fake
        # Python fixtures ran (native Job admission is tested separately).
        self.wait_for(lambda: len([p for p in self.psutil.Process(self.owner.pid).children()
                                   if "python" in p.name().lower()]) >= 2)
        ids = [p.pid for p in self.psutil.Process(self.owner.pid).children(recursive=True)]
        self.assertFalse((self.tree / ".python-desktop-status").exists())
        self.owner.stdin.write((json.dumps({"id": 1, "command": "host_shutdown"}) + "\n").encode())
        self.owner.stdin.flush()
        self.owner.wait(timeout=15)
        self.assertEqual(self.owner.returncode, 0)
        self.assert_dead(ids)

    def test_source_built_shell_host_python_death_cannot_lift_rust_children(self):
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file(): self.skipTest("build source helene-host first")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base), "--python-supervisor"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        ids = self.identities()[:3]
        python_owner = next(p for p in self.psutil.Process(self.owner.pid).children()
                            if "desktop_supervisor.py" in " ".join(p.cmdline()))
        python_owner.kill()
        self.wait_for(lambda: not any(self.psutil.pid_exists(pid) for pid in ids))
        time.sleep(16)  # native missing-agent watcher would have retried here
        self.assertEqual((self.tree / "channel.pid").read_text(), str(ids[0]))
        self.assertFalse(any("deskapp.py" in " ".join(p.cmdline())
                             for p in self.psutil.Process(self.owner.pid).children(recursive=True)))
        from queue import Queue
        from threading import Thread
        events = Queue()
        def read_events():
            for line in self.owner.stdout: events.put(json.loads(line))
        Thread(target=read_events, daemon=True).start()
        first = events.get(timeout=5)
        self.assertEqual(first.get("event"), "ready", first)
        retiring = events.get(timeout=8)
        self.assertEqual(retiring.get("event"), "retire-window", retiring)
        # Deliberately withhold Electron's physical-destroy ACK. The host
        # must request process exit rather than retain or replace the URL.
        blocked = events.get(timeout=8)
        self.assertEqual(blocked.get("event"), "exit", blocked)
        self.owner.stdin.write((json.dumps({"id": 1, "command": "host_shutdown"}) + "\n").encode())
        self.owner.stdin.flush()
        self.owner.wait(timeout=12)

    def test_open_window_retires_after_channel_loss_and_foreign_port(self):
        """A channel PID/old URL does not remain an address after loss."""
        import http.server
        from queue import Queue
        from threading import Thread
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file(): self.skipTest("build source helene-host first")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base), "--python-supervisor"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE,
                                      env=dict(os.environ, FAKE_CHANNEL_DELAY_AFTER_FIRST="1"),
                                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        events = Queue()
        def read_events():
            for line in self.owner.stdout:
                row = json.loads(line)
                if row.get("event") == "retire-window":
                    # Emulate Electron's physical destroy ACK on this host
                    # fixture only. Withholding it is tested separately.
                    self.owner.stdin.write((json.dumps({"id": 98, "command": "window_retire_ack",
                                                        "args": {"retireId": row["data"]["id"]}}) + "\n").encode())
                    self.owner.stdin.flush()
                events.put(row)
        Thread(target=read_events, daemon=True).start()
        ready = events.get(timeout=16)
        self.assertEqual(ready.get("event"), "ready", ready)
        ids = self.identities()
        self.psutil.Process(ids[0]).kill()
        self.wait_for(lambda: not self.psutil.pid_exists(ids[0]), seconds=5)
        foreign = str(self.base / "foreign")
        class Other(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"tree": foreign}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass
        class ExclusiveServer(http.server.ThreadingHTTPServer):
            allow_reuse_address = False
        listener = None
        until = time.monotonic() + 2
        while listener is None and time.monotonic() < until:
            try: listener = ExclusiveServer(("127.0.0.1", self.port), Other)
            except OSError: time.sleep(.05)
        self.assertIsNotNone(listener, "fixture could not take the port after channel death")
        Thread(target=listener.serve_forever, daemon=True).start()
        try:
            retiring = events.get(timeout=12)
            self.assertEqual(retiring.get("event"), "retire-window", retiring)
            blocked = events.get(timeout=12)
            if blocked.get("id") == 98: blocked = events.get(timeout=12)
            self.assertEqual(blocked.get("event"), "replace-window", blocked)
            self.assertIn("python-owner-refused", blocked["data"]["script"])
            self.assertNotIn(f"http://127.0.0.1:{self.port}", blocked["data"]["script"])
            self.owner.stdin.write((json.dumps({"id": 1, "command": "host_shutdown"}) + "\n").encode())
            self.owner.stdin.flush()
            self.owner.wait(timeout=12)
        finally:
            listener.shutdown()
            listener.server_close()

    def test_source_built_shell_refuses_stale_status_before_config_write(self):
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file(): self.skipTest("build source helene-host first")
        config = self.base / "helene.json"
        root = json.loads(config.read_text(encoding="utf-8"))
        root["setup_complete"] = True
        config.write_text(json.dumps(root), encoding="utf-8")
        self.add_neighbor()
        peer = self.base / "agents/neighbor/helene.json"
        self.owner = subprocess.Popen([str(binary), "--root", str(self.base), "--python-supervisor"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        self.identities()
        self.wait_for(lambda: (self.tree / ".python-desktop-status").is_file())
        python_owner = next(p for p in self.psutil.Process(self.owner.pid).children()
                            if "desktop_supervisor.py" in " ".join(p.cmdline()))
        python_owner.suspend()
        try:
            time.sleep(3.5)
            self.owner.stdin.write((json.dumps({"id": 1, "command": "agent_enabled_set",
                                                "args": {"id": "neighbor", "enabled": False}}) + "\n").encode())
            self.owner.stdin.flush()
            from queue import Queue
            from threading import Thread
            replies = Queue()
            def reader():
                for line in self.owner.stdout:
                    row = json.loads(line)
                    if row.get("id") == 1: replies.put(row)
            Thread(target=reader, daemon=True).start()
            receipt = replies.get(timeout=8)
            self.assertIn("Python", receipt["error"], receipt)
            self.assertNotEqual(json.loads(peer.read_text(encoding="utf-8")).get("enabled"), False)
        finally:
            # The watcher may already have closed the suspended owner and its
            # Job; resuming a dead PID would turn an expected refusal red.
            try: python_owner.resume()
            except self.psutil.NoSuchProcess: pass
            self.owner.stdin.write((json.dumps({"id": 2, "command": "host_shutdown"}) + "\n").encode())
            self.owner.stdin.flush()
            self.owner.wait(timeout=12)

    def assert_dead(self, ids):
        limit = time.monotonic() + 8
        while time.monotonic() < limit and any(self.psutil.pid_exists(pid) for pid in ids):
            time.sleep(.1)
        self.assertFalse([pid for pid in ids if self.psutil.pid_exists(pid)],
                         "fixture Job retained descendants")

    def test_python_supervisor_death_closes_job_and_grandchild(self):
        self.start()
        channel, runner, grandchild, coordinator = self.identities()
        self.owner.kill()
        self.owner.wait(timeout=8)
        self.assert_dead((channel, runner, grandchild, coordinator))

    def test_local_ctrl_break_stops_owned_tree(self):
        self.start()
        ids = self.identities()
        self.owner.send_signal(signal.CTRL_BREAK_EVENT)
        self.owner.wait(timeout=8)
        self.assertEqual(self.owner.returncode, 0, self.owner.stderr.read().decode("utf-8", "replace"))
        self.assert_dead(ids)

    def test_competing_supervisor_and_service_owner_refused(self):
        self.start()
        self.identities()
        second = subprocess.run([sys.executable, "-u", str(self.script), "--config",
                                 str(self.base / "helene.json")], capture_output=True, timeout=8)
        self.assertNotEqual(second.returncode, 0)
        self.assertIsNone(self.owner.poll())
        self.owner.send_signal(signal.CTRL_BREAK_EVENT)
        self.owner.wait(timeout=8)
        cfg = json.loads((self.base / "helene.json").read_text(encoding="utf-8"))
        cfg["installed"]["service"] = True
        (self.base / "helene.json").write_text(json.dumps(cfg), encoding="utf-8")
        refused = subprocess.run([sys.executable, str(self.script), "--config",
                                  str(self.base / "helene.json")], capture_output=True, timeout=8)
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(b"pid" in refused.stdout)

    def test_python_then_native_legacy_refuses_even_with_different_port(self):
        self.start()
        ids = self.identities()
        rival = self.legacy(self.other_config())
        try:
            log = self.tree / "service.log"
            try:
                self.wait_for(lambda: log.exists() and
                              "desktop tree owned or lock unavailable" in log.read_text(encoding="utf-8", errors="replace"))
            except AssertionError as exc:
                self.fail(f"{exc}; rival={rival.poll()}; log={log.read_text(encoding='utf-8', errors='replace') if log.exists() else 'absent'}")
            self.assertIsNone(self.owner.poll())
            self.assertFalse((self.tree / "other-channel.pid").exists())
            with self.assertRaises(PermissionError):
                (self.tree / ".desktop-owner.lock").read_bytes()  # live exclusive handle
        finally:
            rival.send_signal(signal.CTRL_BREAK_EVENT)
            rival.communicate(timeout=10)
        self.owner.send_signal(signal.CTRL_BREAK_EVENT)
        self.owner.wait(timeout=8)
        self.assert_dead(ids)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"clean\n")

    def test_native_legacy_then_python_refuses_and_normal_stop_releases(self):
        config = self.base / "helene.json"
        self.owner = self.legacy(config)
        ids = self.identities()
        contender = subprocess.run([sys.executable, "-u", str(self.script),
                                    "--config", str(self.other_config())],
                                   capture_output=True, timeout=8)
        self.assertNotEqual(contender.returncode, 0)
        self.assertIn(b"desktop tree owned", contender.stderr)
        self.assertIsNone(self.owner.poll())
        self.owner.send_signal(signal.CTRL_BREAK_EVENT)
        self.owner.wait(timeout=8)
        self.assert_dead(ids)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"clean\n")
        self.owner.communicate(timeout=2)  # close native launcher's captured pipes
        for name in ("channel.pid", "runner.pid", "grandchild.pid"):
            (self.tree / name).unlink()  # next identities() must see fresh generation
        self.owner = subprocess.Popen([sys.executable, "-u", str(self.script), "--config",
                                       str(config), "--control-stdin"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        new_ids = self.identities()
        self.owner.stdin.write(b"stop\n")
        self.owner.stdin.flush()
        self.owner.wait(timeout=8)
        self.assert_dead(new_ids)

    def test_native_owner_crash_allows_python_recovery(self):
        self.owner = self.legacy(self.base / "helene.json")
        ids = self.identities()
        self.owner.kill()
        self.owner.wait(timeout=8)
        self.assert_dead(ids)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"active\n")
        self.owner.communicate(timeout=2)
        for name in ("channel.pid", "runner.pid", "grandchild.pid"):
            (self.tree / name).unlink()
        self.start()
        new_ids = self.identities()
        self.owner.send_signal(signal.CTRL_BREAK_EVENT)
        self.owner.wait(timeout=8)
        self.assert_dead(new_ids)

    def test_crashed_owner_recovers_after_os_lock_releases(self):
        self.start()
        ids = self.identities()
        self.owner.kill()
        self.owner.wait(timeout=8)
        self.assert_dead(ids)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"active\n")
        self.owner.communicate(timeout=2)
        for name in ("channel.pid", "runner.pid", "grandchild.pid"):
            (self.tree / name).unlink()
        self.start()
        new_ids = self.identities()
        self.owner.send_signal(signal.CTRL_BREAK_EVENT)
        self.owner.wait(timeout=8)
        self.assert_dead(new_ids)

    def test_childless_job_refusal_does_not_strand_tree_record(self):
        from threading import Event
        stop = Event()
        def no_job(*_args, **_kwargs):
            stop.set()
            raise JobCreationRefused("no Job before CreateProcess")
        with patch.object(desktop_supervisor.JobChild, "spawn", side_effect=no_job):
            self.assertEqual(run(self.base / "helene.json", stop=stop), 0)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"clean\n")

    def test_native_failed_first_spawn_cleans_on_confirmed_empty_job(self):
        config = self.base / "helene.json"
        cfg = json.loads(config.read_text(encoding="utf-8"))
        cfg["python"] = str(self.base / "app")  # existing directory, not an executable
        config.write_text(json.dumps(cfg), encoding="utf-8")
        self.owner = self.legacy(config)
        log = self.tree / "service.log"
        self.wait_for(lambda: log.exists() and "не поднялся" in
                      log.read_text(encoding="utf-8", errors="replace"))
        self.assertFalse((self.tree / "channel.pid").exists())
        self.owner.send_signal(signal.CTRL_BREAK_EVENT)
        self.owner.wait(timeout=8)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"clean\n")
        self.owner.communicate(timeout=2)
        cfg["python"] = sys.executable
        config.write_text(json.dumps(cfg), encoding="utf-8")
        self.owner = subprocess.Popen([sys.executable, "-u", str(self.script), "--config",
                                       str(config), "--control-stdin"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        ids = self.identities()
        self.owner.stdin.write(b"stop\n")
        self.owner.stdin.flush()
        self.owner.wait(timeout=8)
        self.assert_dead(ids)

    def test_leader_exit_kills_old_descendant_before_retry(self):
        self.start()
        channel, runner, grandchild, coordinator = self.identities()
        self.psutil.Process(runner).kill()
        self.wait_for(lambda: not self.psutil.pid_exists(grandchild))
        self.wait_for(lambda: (self.tree / "runner.pid").read_text() != str(runner))
        new_runner = int((self.tree / "runner.pid").read_text())
        self.assertNotEqual(new_runner, runner)
        self.assertIsNone(self.owner.poll())

    def test_session_host_two_agent_jobs_and_dynamic_disable(self):
        cfg = self.base / "helene.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        data["installed"] = {"service": True}
        cfg.write_text(json.dumps(data), encoding="utf-8")
        self.start("--session-host")
        self.identities()
        neighbor_tree, _ = self.add_neighbor()
        self.wait_for(lambda: all((neighbor_tree / name).is_file()
                        for name in ("channel.pid", "runner.pid", "grandchild.pid")))
        others = [int((neighbor_tree / name).read_text()) for name in
                  ("channel.pid", "runner.pid", "grandchild.pid")]
        self.assertTrue(all(self.psutil.pid_exists(pid) for pid in others))
        self.assertIsNone(self.owner.poll())
        neighbor_cfg = self.base / "agents/neighbor/helene.json"
        new = json.loads(neighbor_cfg.read_text(encoding="utf-8"))
        new["enabled"] = False
        neighbor_cfg.write_text(json.dumps(new), encoding="utf-8")
        self.wait_for(lambda: all(not self.psutil.pid_exists(pid) for pid in others))
        self.assertEqual((neighbor_tree / ".desktop-owner.lock").read_bytes(), b"clean\n")
        self.assertTrue(self.psutil.pid_exists(int((self.tree / "channel.pid").read_text())))
        self.owner.send_signal(signal.CTRL_BREAK_EVENT)
        self.owner.wait(timeout=8)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"clean\n")

    def test_session_host_base_config_change_replaces_jobs_not_owner(self):
        cfg = self.base / "helene.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        data["installed"] = {"service": True}
        cfg.write_text(json.dumps(data), encoding="utf-8")
        self.start("--session-host")
        old = self.identities()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            data["port"] = probe.getsockname()[1]
        cfg.write_text(json.dumps(data), encoding="utf-8")
        self.wait_for(lambda: (self.tree / "channel.pid").read_text() != str(old[0]))
        self.assert_dead(old[:3])
        self.assertIsNone(self.owner.poll())
        self.owner.send_signal(signal.CTRL_BREAK_EVENT)
        self.owner.wait(timeout=8)

    def test_session_host_neighbor_crash_then_remote_switch_refuses_owner(self):
        cfg = self.base / "helene.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        data["installed"] = {"service": True}
        cfg.write_text(json.dumps(data), encoding="utf-8")
        neighbor_tree, _ = self.add_neighbor()
        self.start("--session-host")
        base_ids = self.identities()[:3]
        self.wait_for(lambda: all((neighbor_tree / name).is_file()
                        for name in ("channel.pid", "runner.pid", "grandchild.pid")))
        runner = int((neighbor_tree / "runner.pid").read_text())
        grandchild = int((neighbor_tree / "grandchild.pid").read_text())
        self.psutil.Process(runner).kill()
        self.wait_for(lambda: not self.psutil.pid_exists(grandchild))
        self.wait_for(lambda: (neighbor_tree / "runner.pid").read_text() != str(runner))
        self.assertTrue(all(self.psutil.pid_exists(pid) for pid in base_ids))
        peer_cfg = self.base / "agents/neighbor/helene.json"
        new = json.loads(peer_cfg.read_text(encoding="utf-8"))
        new["mode"] = "remote"
        peer_cfg.write_text(json.dumps(new), encoding="utf-8")
        next_runner = int((neighbor_tree / "runner.pid").read_text())
        self.wait_for(lambda: self.owner.poll() is not None)
        self.assertNotEqual(self.owner.returncode, 0)
        self.assert_dead([next_runner, *base_ids])
        self.assertEqual((neighbor_tree / ".desktop-owner.lock").read_bytes(), b"clean\n")
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"clean\n")

    def test_stop_after_job_assignment_prevents_first_child_instruction(self):
        marker = self.tree / "should-not-run"
        calls = [0]
        def stopped():
            calls[0] += 1
            return calls[0] >= 2
        with self.assertRaisesRegex(RuntimeError, "owner stop before child resume"):
            JobChild.spawn([sys.executable, "-c", f"open({str(marker)!r},'w').write('ran')"],
                           cwd=self.base, env=dict(os.environ), log=self.tree / "refused.log",
                           stopped=stopped)
        self.assertFalse(marker.exists(), "suspended process ran before Job/Stop admission")

    def test_job_admission_failure_refuses_without_retry_or_orphan(self):
        created = []
        real_create = desktop_process._winapi.CreateProcess
        def capture(*args):
            receipt = real_create(*args)
            created.append(receipt[2])
            return receipt
        marker = self.tree / "should-not-run"
        with patch.object(desktop_process._winapi, "CreateProcess", side_effect=capture), \
             patch.object(desktop_process.ChildJob, "assign", side_effect=OSError("test Job refusal")):
            with self.assertRaises(CustodyFault):
                JobChild.spawn([sys.executable, "-c", f"open({str(marker)!r},'w').write('ran')"],
                               cwd=self.base, env=dict(os.environ), log=self.tree / "refused.log",
                               stopped=lambda: False)
        self.assertEqual(len(created), 1)
        self.assert_dead(created)
        self.assertFalse(marker.exists())

    def test_unrelated_inheritable_handle_is_not_sent_to_child(self):
        sentinel = self.tree / "foreign-handle"
        child = None
        with sentinel.open("wb") as source:
            handle = msvcrt.get_osfhandle(source.fileno())
            os.set_handle_inheritable(handle, True)
            try:
                child = JobChild.spawn([sys.executable, "-c", "import time;time.sleep(30)"],
                                       cwd=self.base, env=dict(os.environ),
                                       log=self.tree / "stdio.log", stopped=lambda: False)
            finally:
                os.set_handle_inheritable(handle, False)
        try:
            sentinel.unlink()  # Windows refuses while a child holds this handle.
        finally:
            if child is not None: child.stop()

    def test_native_foreground_launches_one_python_owner_and_stops_tree(self):
        self.start_native()
        ids = self.identities()
        self.wait_for(lambda: (self.tree / "service.log").is_file())
        log = (self.tree / "service.log").read_text(encoding="utf-8", errors="replace")
        match = re.search(r"Python owns child tree; launcher watches pid (\d+)", log)
        self.assertIsNotNone(match, log)
        coordinator = int(match.group(1))
        self.assertTrue(self.psutil.pid_exists(coordinator))
        self.owner.send_signal(signal.CTRL_BREAK_EVENT)
        self.owner.wait(timeout=8)
        self.assertEqual(self.owner.returncode, 0,
                         (self.tree / "service.log").read_text(encoding="utf-8", errors="replace"))
        self.assert_dead(ids + [coordinator])
        native_log = (self.tree / "service.log").read_text(encoding="utf-8", errors="replace")
        python_log = (self.tree / "desktop_supervisor.log").read_text(encoding="utf-8", errors="replace")
        self.assertIn("Stop handed to Python", native_log)
        self.assertIn("[python-supervisor] child Jobs empty", python_log)

    def test_native_launcher_handles_python_owner_death_without_fallback(self):
        self.start_native()
        ids = self.identities()
        self.wait_for(lambda: (self.tree / "service.log").is_file())
        log = (self.tree / "service.log").read_text(encoding="utf-8", errors="replace")
        match = re.search(r"launcher watches pid (\d+)", log)
        self.assertIsNotNone(match, log)
        coordinator = int(match.group(1))
        self.psutil.Process(coordinator).kill()
        self.owner.wait(timeout=8)
        self.assertNotEqual(self.owner.returncode, 0)
        self.assert_dead(ids + [coordinator])

    def test_native_launcher_crash_closes_outer_job(self):
        self.start_native()
        ids = self.identities()
        log = (self.tree / "service.log").read_text(encoding="utf-8", errors="replace")
        match = re.search(r"launcher watches pid (\d+)", log)
        self.assertIsNotNone(match, log)
        coordinator = int(match.group(1))
        self.owner.kill()
        self.owner.wait(timeout=8)
        self.assert_dead(ids + [coordinator])

    def test_native_session_host_opt_in_outer_fuse_with_service_config(self):
        binary = DESK / "svc/target/debug/helene-svc.exe"
        if not binary.is_file(): self.skipTest("build desk/svc first")
        cfg = self.base / "helene.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        data["installed"] = {"service": True}
        data["service"] = {"python_supervisor": True}
        cfg.write_text(json.dumps(data), encoding="utf-8")
        neighbor_tree, _ = self.add_neighbor()
        self.owner = subprocess.Popen([str(binary), "session-host", "--config", str(cfg),
                                       "--python-supervisor"], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        ids = self.identities()
        self.wait_for(lambda: all((neighbor_tree / name).is_file()
                        for name in ("channel.pid", "runner.pid", "grandchild.pid")))
        ids += [int((neighbor_tree / name).read_text()) for name in
                ("channel.pid", "runner.pid", "grandchild.pid")]
        # session-host detaches from the console: CTRL_BREAK does not reach it.
        # The scheduler's actual /End is not invoked in this isolated fixture;
        # kill the native owner and assert its outer Job plus Python Jobs die.
        self.owner.kill()
        self.owner.wait(timeout=8)
        self.assert_dead(ids)
        self.assertEqual((self.tree / ".desktop-owner.lock").read_bytes(), b"active\n")
        self.assertEqual((neighbor_tree / ".desktop-owner.lock").read_bytes(), b"active\n")

    def test_session_host_cooperative_stop_then_same_tree_restart(self):
        binary = DESK / "svc/target/debug/helene-svc.exe"
        if not binary.is_file(): self.skipTest("build desk/svc first")
        cfg = self.base / "helene.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        data["installed"] = {"service": True}
        data["service"] = {"python_supervisor": True}
        cfg.write_text(json.dumps(data), encoding="utf-8")
        neighbor_tree, _ = self.add_neighbor()
        for ordinal in range(2):
            self.owner = subprocess.Popen([str(binary), "session-host", "--config", str(cfg),
                                           "--python-supervisor"], stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            ids = self.identities()
            self.wait_for(lambda: all((neighbor_tree / name).is_file()
                            for name in ("channel.pid", "runner.pid", "grandchild.pid")))
            ids += [int((neighbor_tree / name).read_text()) for name in
                    ("channel.pid", "runner.pid", "grandchild.pid")]
            marker = self.tree / ".python-session-owner"
            self.wait_for(marker.is_file)
            nonce = marker.read_text(encoding="utf-8") + "\n" + format(ordinal + 1, "032x")
            (self.tree / ".python-session-request").write_bytes(nonce.encode("ascii"))
            try:
                self.owner.wait(timeout=12)
            except subprocess.TimeoutExpired:
                native = (self.tree / "service.log").read_text(encoding="utf-8", errors="replace")
                python = (self.tree / "desktop_supervisor.log").read_text(encoding="utf-8", errors="replace")
                self.fail(f"session Stop timed out; request={nonce!r}; native={native[-1200:]}; python={python[-1200:]}")
            self.assertEqual(self.owner.returncode, 0,
                             (self.tree / "service.log").read_text(encoding="utf-8", errors="replace"))
            self.assert_dead(ids)
            self.assertEqual((self.tree / ".python-session-ack").read_text(encoding="utf-8"), nonce)
            self.assertFalse(marker.exists())
            for tree in (self.tree, neighbor_tree):
                self.assertEqual((tree / ".desktop-owner.lock").read_bytes(), b"clean\n")
            refused = subprocess.run([str(binary), "session-host", "--config", str(cfg),
                                      "--python-supervisor"], capture_output=True, timeout=8)
            self.assertNotEqual(refused.returncode, 0,
                                "unacknowledged service Stop allowed a replacement generation")
            # The service keeps request across /End, then cleans only a matched
            # ack. Simulate this final housekeeping without changing real tasks.
            (self.tree / ".python-session-request").unlink()
            (self.tree / ".python-session-ack").unlink()
            self.owner.communicate(timeout=2)
            for tree in (self.tree, neighbor_tree):
                for name in ("channel.pid", "runner.pid", "grandchild.pid"):
                    (tree / name).unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
