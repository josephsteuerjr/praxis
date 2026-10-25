"""Windows CU read adapter: Python OS reads and Hélène's selected launch seam.

Run with an explicit Python 3.14. The native composed-body/bridge consumer is
exercised by the separate disposable-source fixture, not by this unit suite.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cu_read_cli", DESK / "localharness" / "cu_read_cli.py")
cu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cu)


class PageAndContract(unittest.TestCase):
    def test_page_clamps_limit_and_discloses_cursor_without_fabricating_rows(self):
        self.assertEqual(cu._page([{"i": i} for i in range(4)], {"offset": 2, "limit": 1}), {
            "ok": True, "total": 4, "offset": 2, "limit": 1, "returned": 1,
            "next_offset": 3, "items": [{"i": 2}]})
        self.assertIsNone(cu._page([], {"offset": 5})["next_offset"])
        self.assertEqual(cu._page([], {"limit": 0})["limit"], 1)
        for invalid in (True, -1, "2", 0.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                cu._page([], {"offset": invalid})

    def test_only_three_exact_read_verbs_are_implemented(self):
        for name in ("body.status", "process.list", "desktop.window.activate",
                     "desktop.input.perform", "fs.stat"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "unsupported"):
                cu.dispatch(name, {})
        with self.assertRaisesRegex(ValueError, "object"):
            cu.dispatch("desktop.window.list", [])

    def test_dpi_refuses_non_v2_context(self):
        user = mock.Mock()
        user.GetThreadDpiAwarenessContext.return_value = 7
        user.AreDpiAwarenessContextsEqual.return_value = False
        with self.assertRaisesRegex(OSError, "per-monitor-v2"):
            cu._dpi(user)
        user.SetProcessDpiAwarenessContext.assert_called_once_with(cu.PER_MONITOR_V2)

    def test_failed_process_metadata_stays_unknown(self):
        kernel = mock.Mock()
        kernel.ProcessIdToSessionId.return_value = False
        kernel.OpenProcess.return_value = 0
        self.assertEqual(cu._process(kernel, 91), (None, None, None))

    def test_locked_or_headless_desktop_has_zero_foreground_handle(self):
        self.assertEqual(cu._hex(None), "0x0")
        self.assertEqual(cu._hex(0), "0x0")

    @unittest.skipUnless(os.name == "nt", "Windows OS only")
    def test_real_read_shapes_and_filters_are_compatible_with_body_client(self):
        status = cu.dispatch("desktop.status", {})
        self.assertTrue(status["ok"])
        self.assertIn("foreground", status)
        self.assertIn("virtual_screen", status)
        self.assertIn("session_id", status)
        self.assertIn("clipboard_sequence", status)
        windows = cu.dispatch("desktop.window.list", {"limit": 1})
        self.assertTrue(windows["ok"])
        self.assertIn("foreground_hwnd", windows)
        self.assertEqual(windows["returned"], len(windows["items"]))
        if windows["items"]:
            row = windows["items"][0]
            self.assertEqual(row["fingerprint"].split(":")[0], row["hwnd"])
            self.assertEqual(row["rect"]["width"], row["rect"]["right"] - row["rect"]["left"])
            by_pid = cu.dispatch("desktop.window.list", {"pid": row["pid"], "limit": 3})
            self.assertTrue(all(item["pid"] == row["pid"] for item in by_pid["items"]))
        processes = cu.dispatch("os.process.list", {"name_contains": "python", "limit": 2})
        self.assertTrue(processes["ok"])
        self.assertLessEqual(processes["returned"], 2)
        self.assertTrue(all("python" in (row["name"] + (row["path"] or "")).lower()
                            for row in processes["items"]))
        self.assertEqual([r["pid"] for r in processes["items"]],
                         sorted(r["pid"] for r in processes["items"]))
        with self.assertRaisesRegex(ValueError, "pid"):
            cu.dispatch("desktop.window.list", {"pid": -1})


@unittest.skipUnless(os.name == "nt", "Windows-only opt-in selection; POSIX keeps the native route")
class RunnerSelection(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(DESK / "localharness"))
        import body
        self.body = body
        self.saved_state = dict(body.STATE)

    def tearDown(self):
        self.body.STATE.clear()
        self.body.STATE.update(self.saved_state)

    def test_explicit_choice_refuses_absent_package_without_native_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helene-body.exe").touch()
            (root / "helene-bridge.exe").touch()
            target = root / "data"
            target.mkdir()
            owner = self.body.Body(root, target, {"computer": {"enabled": True, "python_reads": True}})
            self.assertFalse(owner.start())
            self.assertIn("missing", self.body.STATE["reason"])
            self.assertEqual(owner.children, [])
            self.assertFalse((target / "body" / "body.json").exists())

    def test_selected_paths_and_tokens_stay_in_separate_child_environments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helene-body.exe").touch()
            (root / "helene-bridge.exe").touch()
            python = root / "runtime" / "python.exe"
            python.parent.mkdir()
            python.touch()
            target = root / "data"
            target.mkdir()
            owner = self.body.Body(root, target, {"computer": {"enabled": True, "python_reads": True}})
            class IdleThread:
                def __init__(self, **kwargs): pass
                def start(self): pass
            with mock.patch.object(self.body, "pick_port", return_value=9489), \
                 mock.patch.object(self.body, "_make_job", return_value=None), \
                 mock.patch.object(self.body._Child, "spawn"), \
                 mock.patch.object(self.body.threading, "Thread", IdleThread), \
                 mock.patch.object(self.body.atexit, "register"), \
                 mock.patch.object(owner, "_write_state"), \
                 mock.patch.dict(os.environ, {"HELENE_CU_PYTHON_READS": "1",
                    "HELENE_CU_PYTHON_EXE": "foreign.exe", "PRAXIS_BODY_TOKEN": "foreign"}):
                self.assertTrue(owner.start())
            bridge, body = owner.children
            self.assertNotIn("HELENE_CU_PYTHON_READS", bridge.env)
            self.assertNotIn("PRAXIS_BODY_TOKEN", bridge.env)
            self.assertEqual(body.env["HELENE_CU_PYTHON_READS"], "1")
            self.assertEqual(body.env["HELENE_CU_PYTHON_EXE"], str(python))
            self.assertEqual(Path(body.env["HELENE_CU_PYTHON_SCRIPT"]).name, "cu_read_cli.py")
            self.assertNotEqual(body.env["PRAXIS_BODY_TOKEN"], "foreign")
            saved = json.loads((target / "body" / "body.json").read_text("utf-8"))
            self.assertNotIn("token", saved)
            self.assertNotIn("python_reads", saved)


@unittest.skipUnless(os.name == "nt" and all(os.environ.get(key) for key in (
    "HELENE_CU_TEST_BODY_EXE", "HELENE_CU_TEST_BRIDGE_EXE", "HELENE_CU_TEST_STAGED_CLI",
    "HELENE_CU_TEST_EDITION")),
    "set the four HELENE_CU_TEST_* paths to composed edition/body/bridge and staged desk CLI")
class NativeConsumer(unittest.TestCase):
    """The optional real controller/bridge/body path never addresses live services."""

    def test_native_refusal_token_scrub_timeout_and_parent_death(self):
        import ctypes
        import subprocess
        import sys
        import time
        body_exe = Path(os.environ["HELENE_CU_TEST_BODY_EXE"])
        with tempfile.TemporaryDirectory(prefix="cu-custody-") as tmp:
            root = Path(tmp)
            config = root / "body.json"
            config.write_text(json.dumps({"device_id": "cu-custody",
                "bridge_ws_url": "ws://127.0.0.1:59871",
                "artifact_base_url": "http://127.0.0.1:59871",
                "state_dir": str(root / "state")}), encoding="utf-8")
            env = os.environ.copy()
            env.update(PRAXIS_BODY_TOKEN="private-token", HELENE_CU_PYTHON_READS="1",
                       HELENE_CU_PYTHON_EXE=sys.executable,
                       HELENE_CU_PYTHON_SCRIPT=str(root / "missing.py"))
            command = [str(body_exe), "invoke-local", "--config", str(config),
                       "--capability", "desktop.status", "--args", "{}",
                       "--execution", "interactive"]
            def invoke():
                run = subprocess.run(command, env=env, cwd=root, capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                return json.loads(run.stdout)
            self.assertEqual(invoke()["type"], "error")  # no Rust fallback
            fake = root / "fake.py"
            fake.write_text("import sys,os,json\nsys.stdin.buffer.read()\n"
                "print(json.dumps({'ok':True,'result':{'ok':True,'token_seen': "
                "any(k.startswith(('PRAXIS_BODY','PRAXIS_BRIDGE','HELENE_CU_')) for k in os.environ)}}))\n",
                encoding="utf-8")
            env["HELENE_CU_PYTHON_SCRIPT"] = str(fake)
            self.assertIs(invoke()["result"]["token_seen"], False)

            kernel = ctypes.windll.kernel32
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel.WaitForSingleObject.restype = ctypes.c_uint32
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            def exited(pid, wait_ms=5000):
                handle = kernel.OpenProcess(0x00100000, False, pid)
                if not handle:
                    return True
                try:
                    return kernel.WaitForSingleObject(handle, wait_ms) == 0
                finally:
                    kernel.CloseHandle(handle)
            pid_file = root / "child.pid"
            fake.write_text("import sys,os,time\nsys.stdin.buffer.read()\n"
                f"open({str(pid_file)!r},'w').write(str(os.getpid()))\ntime.sleep(40)\n",
                encoding="utf-8")
            start = time.monotonic()
            timed = invoke()
            self.assertEqual(timed["type"], "error")
            self.assertIn("timed out", timed["message"])
            self.assertGreaterEqual(time.monotonic() - start, 19)
            self.assertTrue(exited(int(pid_file.read_text()), 0))

            pid_file.unlink()
            parent = subprocess.Popen(command, env=env, cwd=root, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, creationflags=0x08000000)
            try:
                until = time.monotonic() + 8
                while not pid_file.exists() and time.monotonic() < until:
                    time.sleep(.05)
                self.assertTrue(pid_file.exists(), "body did not admit child")
                pid = int(pid_file.read_text())
                parent.kill()
                parent.wait(timeout=5)
                self.assertTrue(exited(pid), "Python child survived forced body death")
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=3)

    def test_disposable_loopback_wire_result_and_native_default(self):
        import socket
        import subprocess
        import sys
        import time
        import importlib.util
        import uuid
        body_exe = Path(os.environ["HELENE_CU_TEST_BODY_EXE"])
        bridge_exe = Path(os.environ["HELENE_CU_TEST_BRIDGE_EXE"])
        cli = Path(os.environ["HELENE_CU_TEST_STAGED_CLI"])
        edition_client = Path(os.environ["HELENE_CU_TEST_EDITION"]) / "body_client.py"
        self.assertTrue(edition_client.is_file(), "composed edition client required")
        spec = importlib.util.spec_from_file_location("cu_body_client", edition_client)
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
        with tempfile.TemporaryDirectory(prefix="cu-consumer-") as tmp:
            root = Path(tmp)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            device = "cu-" + uuid.uuid4().hex[:16]
            token, controller = "dev-" + uuid.uuid4().hex, "ctl-" + uuid.uuid4().hex
            config = root / "body.json"
            config.write_text(json.dumps({"device_id": device,
                "bridge_ws_url": f"ws://127.0.0.1:{port}",
                "artifact_base_url": f"http://127.0.0.1:{port}",
                "state_dir": str(root / "body-state")}), encoding="utf-8")
            processes = []
            with (root / "bridge.log").open("wb") as bridge_log, \
                 (root / "body.log").open("wb") as body_log:
                try:
                    bridge_env = os.environ.copy()
                    bridge_env.update(PRAXIS_BRIDGE_DEVICE_TOKEN=token,
                                      PRAXIS_BRIDGE_CONTROLLER_TOKEN=controller)
                    bridge = subprocess.Popen([str(bridge_exe), "--listen", f"127.0.0.1:{port}",
                        "--state-dir", str(root / "bridge-state")], cwd=root, env=bridge_env,
                        stdout=bridge_log, stderr=bridge_log, creationflags=0x08000000)
                    processes.append(bridge)
                    until = time.monotonic() + 20
                    while time.monotonic() < until:
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=1):
                                break
                        except OSError:
                            time.sleep(.1)
                    body_env = os.environ.copy()
                    body_env.update(PRAXIS_BODY_TOKEN=token, HELENE_CU_PYTHON_READS="1",
                        HELENE_CU_PYTHON_EXE=sys.executable, HELENE_CU_PYTHON_SCRIPT=str(cli))
                    body = subprocess.Popen([str(body_exe), "connect", "--config", str(config)],
                        cwd=root, env=body_env, stdout=body_log, stderr=body_log,
                        creationflags=0x08000000)
                    processes.append(body)
                    client._settings = lambda: (f"http://127.0.0.1:{port}", controller, device)
                    until = time.monotonic() + 40
                    while time.monotonic() < until:
                        answer = client.call("body.status", {}, timeout=3)
                        if answer.get("ok") and isinstance(answer.get("identity"), dict):
                            break
                        self.assertIsNone(body.poll(), f"body exited: {answer}")
                        time.sleep(.2)
                    else:
                        self.fail(f"body never connected: {answer}")
                    self.assertEqual(answer["manifest"]["protocol"], "praxis.body.v1")
                    self.assertIn("native-win32-with-python-reads",
                        [a["name"] for a in answer["manifest"]["adapters"]])
                    # Bridge Invoke defaults omitted args to null; invoke-local sends it explicitly.
                    status_null = subprocess.run([str(body_exe), "invoke-local", "--config", str(config),
                        "--capability", "desktop.status", "--args", "null", "--execution", "interactive"],
                        cwd=root, env=body_env, capture_output=True, timeout=30)
                    self.assertEqual(status_null.returncode, 0, status_null.stderr)
                    self.assertTrue(json.loads(status_null.stdout)["result"]["ok"])
                    selected_results = {}
                    for cap, args in (("desktop.status", {}),
                                      ("desktop.window.list", {"limit": 2}),
                                      ("os.process.list", {"name_contains": "python", "limit": 2})):
                        result = client.call(cap, args, timeout=30)
                        selected_results[cap] = result["result"]
                        self.assertIs(result.get("transport_ok"), True, result)
                        self.assertIs(result.get("ok"), True, result)
                        self.assertEqual(result["status"], "succeeded")
                        if "items" in result:
                            self.assertEqual(result["returned"], len(result["items"]))
                    # A new local request without the opt-in reaches the same
                    # composed Rust binary's original native capability path.
                    native_env = os.environ.copy()
                    native_env["PRAXIS_BODY_TOKEN"] = token
                    for key in list(native_env):
                        if key.startswith("HELENE_CU_"):
                            native_env.pop(key)
                    default = subprocess.run([str(body_exe), "invoke-local", "--config", str(config),
                        "--capability", "desktop.status", "--args", "{}", "--execution", "interactive"],
                        cwd=root, env=native_env, capture_output=True, timeout=20)
                    self.assertEqual(default.returncode, 0, default.stderr)
                    result = json.loads(default.stdout)
                    self.assertEqual(result["type"], "result")
                    self.assertTrue(result["result"]["ok"])
                    self.assertEqual(set(result["result"]), set(client.call("desktop.status", {}, timeout=30)["result"]))
                    # Windows state changes between reads: compare shapes and
                    # pagination semantics, not volatile rows or total counts.
                    for cap, args in (("desktop.window.list", {"limit": 2}),
                                      ("os.process.list", {"name_contains": "python", "limit": 2})):
                        native = subprocess.run([str(body_exe), "invoke-local", "--config", str(config),
                            "--capability", cap, "--args", json.dumps(args), "--execution", "interactive"],
                            cwd=root, env=native_env, capture_output=True, timeout=20)
                        self.assertEqual(native.returncode, 0, native.stderr)
                        native_result = json.loads(native.stdout)["result"]
                        selected_result = selected_results[cap]
                        self.assertEqual(set(selected_result), set(native_result))
                        for page in (native_result, selected_result):
                            self.assertEqual(page["limit"], 2)
                            self.assertEqual(page["offset"], 0)
                            self.assertEqual(page["returned"], len(page["items"]))
                        if selected_result["items"] and native_result["items"]:
                            self.assertEqual(set(selected_result["items"][0]), set(native_result["items"][0]))
                    native_manifest = subprocess.run([str(body_exe), "invoke-local", "--config", str(config),
                        "--capability", "body.status", "--args", "{}", "--execution", "interactive"],
                        cwd=root, env=native_env, capture_output=True, timeout=20)
                    self.assertEqual(native_manifest.returncode, 0, native_manifest.stderr)
                    self.assertIn("native-win32-desktop", [a["name"] for a in
                        json.loads(native_manifest.stdout)["result"]["manifest"]["adapters"]])
                finally:
                    for proc in reversed(processes):
                        if proc.poll() is None:
                            proc.terminate()
                    for proc in reversed(processes):
                        try:
                            proc.wait(timeout=6)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=4)


if __name__ == "__main__":
    unittest.main()
