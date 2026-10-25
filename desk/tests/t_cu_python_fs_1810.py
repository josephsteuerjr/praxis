"""Hélène's selected Windows CU file consumer, against disposable paths only.

NativeConsumer requires HELENE_CU_TEST_BODY_EXE, HELENE_CU_TEST_BRIDGE_EXE,
HELENE_CU_TEST_STAGED_FS_CLI and HELENE_CU_TEST_EDITION from a composed vNext
source candidate plus a separately staged desktop package. Never use installed
Hélène, SCM or Task Scheduler for this fixture.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid

DESK = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cu_fs_cli", DESK / "localharness" / "cu_fs_cli.py")
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)


@contextmanager
def _disposable_directory():
    """Windows can retain an ended child's cwd handle briefly after wait()."""
    root = Path(tempfile.mkdtemp(prefix="cu-fs-wire-"))
    try:
        yield root
    finally:
        deadline = time.monotonic() + 5
        while True:
            try:
                shutil.rmtree(root)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)


class FileContract(unittest.TestCase):
    def test_browse_read_hash_and_utf8_byte_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "ёж.txt"
            path.write_bytes("ёж".encode() + b"\xff\x00")
            stat = fs.dispatch("fs.stat", {"path": str(path)})
            self.assertEqual((stat["kind"], stat["size"]), ("file", 6))
            entry = fs.dispatch("fs.list", {"path": tmp, "offset": 0, "limit": 1})
            self.assertEqual((entry["total"], len(entry["entries"]), entry["eof"]), (1, 1, True))
            self.assertEqual(entry["entries"][0]["name"], "ёж.txt")
            read = fs.dispatch("fs.read", {"path": str(path), "offset": 2, "limit": 3})
            self.assertEqual((read["text"], read["next_offset"], read["lossy"]), ("ж�", 5, True))
            self.assertEqual(fs.dispatch("fs.hash", {"path": str(path)})["sha256"],
                             hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaisesRegex(fs.Refusal, "beyond file size"):
                fs.dispatch("fs.read", {"path": str(path), "offset": 7})

    def test_optimistic_write_replace_backup_and_precommit_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "word.txt"
            original = "старое"
            path.parent.mkdir()
            path.write_text(original, encoding="utf-8")
            digest = hashlib.sha256(original.encode()).hexdigest()
            with self.assertRaisesRegex(fs.Refusal, "hash conflict"):
                fs.dispatch("fs.write_atomic", {"path": str(path), "content": "wrong",
                                               "expected_sha256": "0" * 64})
            with self.assertRaisesRegex(fs.Refusal, "one match"):
                fs.dispatch("fs.replace", {"path": str(path), "old": "absent", "new": "wrong"})
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            written = fs.dispatch("fs.write_atomic", {"path": str(path), "content": "новое",
                        "expected_sha256": digest, "backup": True})
            self.assertEqual((written["before_sha256"], written["size"]), (digest, len("новое".encode())))
            self.assertEqual(Path(written["backup"]).read_text(encoding="utf-8"), original)
            replaced = fs.dispatch("fs.replace", {"path": str(path), "old": "новое", "new": "готово",
                                  "expected_sha256": written["after_sha256"]})
            self.assertEqual(path.read_text(encoding="utf-8"), "готово")
            self.assertEqual(replaced["before_sha256"], written["after_sha256"])
            self.assertFalse(list(path.parent.glob("*.praxis-part-*")))

    def test_replace_invalid_utf8_refuses_before_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.txt"
            path.write_bytes(b"\xff")
            with self.assertRaises(UnicodeDecodeError):
                fs.dispatch("fs.replace", {"path": str(path), "old": "x", "new": "y", "backup": True})
            self.assertEqual(path.read_bytes(), b"\xff")
            self.assertEqual(list(Path(tmp).glob("*.praxis-backup-*")), [])

    def test_falsey_nonboolean_backup_is_refused_before_write_or_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.txt"
            path.write_bytes(b"old")
            for value in (None, 0, "", [], {}):
                for capability, fields in (("fs.write_atomic", {"content": "new"}),
                                           ("fs.replace", {"old": "old", "new": "new"})):
                    with self.subTest(backup=value, capability=capability):
                        phase = {"effect": "none"}
                        with self.assertRaisesRegex(fs.Refusal, "backup must be a boolean"):
                            fs.dispatch(capability, {"path": str(path), "backup": value,
                                                     **fields}, phase=phase)
                        self.assertEqual(phase["effect"], "none")
                        self.assertEqual(path.read_bytes(), b"old")
                        self.assertEqual(list(Path(tmp).glob("*.praxis-backup-*")), [])

    def test_replace_preserves_mixed_newlines_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lines.txt"
            path.write_bytes(b"before\r\ntarget\nkeep\r\n")
            result = fs.dispatch("fs.replace", {"path": str(path),
                    "old": "target\n", "new": "changed\n"})
            self.assertTrue(result["ok"])
            self.assertEqual(path.read_bytes(), b"before\r\nchanged\nkeep\r\n")

    def test_list_link_metadata_does_not_follow_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.txt"
            target.write_bytes(b"actual content")
            link = root / "link.txt"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlink privilege unavailable: {error}")
            rows = {item["name"]: item for item in fs.dispatch("fs.list", {"path": tmp})["entries"]}
            self.assertEqual(rows["link.txt"]["kind"], "other")
            target.unlink()
            rows = {item["name"]: item for item in fs.dispatch("fs.list", {"path": tmp})["entries"]}
            self.assertIn("link.txt", rows)  # dangling target must not fail the listing
            self.assertEqual(rows["link.txt"]["kind"], "other")

    def test_mkdir_created_chain_and_partial_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "a" / "b"
            with self.assertRaisesRegex(fs.Refusal, "parents=true"):
                fs.dispatch("fs.mkdir", {"path": str(path), "parents": False})
            first = fs.dispatch("fs.mkdir", {"path": str(path), "unused": 3})
            self.assertTrue(first["complete"])
            self.assertEqual(first["created_count"], 2)
            self.assertEqual(first["ignored_args"], ["unused"])
            self.assertTrue(fs.dispatch("fs.mkdir", {"path": str(path)})["existed"])
            fail = root / "p" / "q"
            original = Path.mkdir
            def fault(current, *args, **kwargs):
                if current == fail:
                    raise OSError("fixture refused second level")
                return original(current, *args, **kwargs)
            with mock.patch.object(Path, "mkdir", fault):
                partial = fs.dispatch("fs.mkdir", {"path": str(fail)})
            self.assertFalse(partial["complete"])
            self.assertEqual((partial["created_count"], partial["failed_count"]), (1, 1))
            self.assertTrue(partial["summary"].startswith("PARTIAL:"))
            self.assertTrue((root / "p").is_dir())

    def test_controlled_failure_preserves_destination_and_discloses_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.txt"
            path.write_text("old")
            phase = {"effect": "none"}
            with mock.patch.object(fs, "_replace", side_effect=OSError("fixture fail before replace")):
                with self.assertRaisesRegex(OSError, "fixture"):
                    fs.dispatch("fs.write_atomic", {"path": str(path), "content": "new"}, phase=phase)
            self.assertEqual(path.read_text(), "old")
            self.assertEqual(phase["effect"], "precommit")
            self.assertFalse(list(Path(tmp).glob("*.praxis-part-*")))

    @unittest.skipUnless(os.name == "nt", "Windows runner selection only")
    def test_missing_cli_refusal_and_selected_paths(self):
        sys.path.insert(0, str(DESK / "localharness"))
        import body
        saved = dict(body.STATE)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for name in ("helene-body.exe", "helene-bridge.exe"):
                    (root / name).touch()
                (root / "data").mkdir()
                owner = body.Body(root, root / "data", {"computer": {"enabled": True, "python_fs": True}})
                self.assertFalse(owner.start())
                self.assertIn("missing", body.STATE["reason"])
                self.assertEqual(owner.children, [])
        finally:
            body.STATE.clear()
            body.STATE.update(saved)

    @unittest.skipUnless(os.name == "nt", "Windows runner selection only")
    def test_runner_selects_only_body_and_can_combine_read_and_fs(self):
        sys.path.insert(0, str(DESK / "localharness"))
        import body
        saved = dict(body.STATE)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for name in ("helene-body.exe", "helene-bridge.exe"):
                    (root / name).touch()
                python = root / "runtime" / "python.exe"
                python.parent.mkdir()
                python.touch()
                tree = root / "data"
                tree.mkdir()
                owner = body.Body(root, tree, {"computer": {"enabled": True,
                    "python_fs": True, "python_reads": True}})
                class IdleThread:
                    def __init__(self, **kwargs): pass
                    def start(self): pass
                with mock.patch.object(body, "pick_port", return_value=9489), \
                     mock.patch.object(body, "_make_job", return_value=None), \
                     mock.patch.object(body._Child, "spawn"), \
                     mock.patch.object(body.threading, "Thread", IdleThread), \
                     mock.patch.object(body.atexit, "register"), \
                     mock.patch.object(owner, "_write_state"), \
                     mock.patch.dict(os.environ, {"HELENE_CU_PYTHON_FS": "foreign",
                                              "PRAXIS_BODY_TOKEN": "foreign"}):
                    self.assertTrue(owner.start())
                bridge, executor = owner.children
                self.assertNotIn("HELENE_CU_PYTHON_FS", bridge.env)
                self.assertNotIn("PRAXIS_BODY_TOKEN", bridge.env)
                self.assertEqual(executor.env["HELENE_CU_PYTHON_FS"], "1")
                self.assertEqual(executor.env["HELENE_CU_PYTHON_READS"], "1")
                self.assertEqual(Path(executor.env["HELENE_CU_PYTHON_FS_SCRIPT"]).name, "cu_fs_cli.py")
                self.assertEqual(executor.env["HELENE_CU_PYTHON_EXE"], str(python))
                self.assertNotEqual(executor.env["PRAXIS_BODY_TOKEN"], "foreign")
        finally:
            body.STATE.clear()
            body.STATE.update(saved)

    def test_large_escaped_read_and_write_are_not_read_shim_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.txt"
            path.write_bytes(b"\x00" * (2 * 1024 * 1024))
            result = fs.dispatch("fs.read", {"path": str(path), "limit": 8 * 1024 * 1024})
            self.assertEqual(len(result["text"]), 2 * 1024 * 1024)
            self.assertGreater(len(json.dumps({"ok": True, "result": result})), 8 * 1024 * 1024)
            self.assertEqual(fs.MAX_REQUEST, 64 * 1024 * 1024)


CONSUMER_ENV = ("HELENE_CU_TEST_BODY_EXE", "HELENE_CU_TEST_BRIDGE_EXE",
                "HELENE_CU_TEST_STAGED_FS_CLI", "HELENE_CU_TEST_EDITION")


@unittest.skipUnless(os.name == "nt" and all(os.environ.get(key) for key in CONSUMER_ENV),
                     "requires disposable composed body/bridge, edition and staged desk CLI")
class NativeConsumer(unittest.TestCase):
    def test_selected_native_parity_journal_replay_and_unknown_effect(self):
        body_exe = Path(os.environ[CONSUMER_ENV[0]])
        bridge_exe = Path(os.environ[CONSUMER_ENV[1]])
        cli = Path(os.environ[CONSUMER_ENV[2]])
        edition_client = Path(os.environ[CONSUMER_ENV[3]]) / "body_client.py"
        self.assertEqual(cli.read_bytes(), (DESK / "localharness" / "cu_fs_cli.py").read_bytes())
        package = json.loads((cli.parent.parent / "desk.json").read_text("utf-8"))
        self.assertEqual(package["files"]["localharness/cu_fs_cli.py"],
                         hashlib.sha256(cli.read_bytes()).hexdigest())
        spec = importlib.util.spec_from_file_location("cu_body_client_fs", edition_client)
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
        with _disposable_directory() as root:
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
            with open(os.devnull, "wb") as bridge_log, open(os.devnull, "wb") as body_log:
                try:
                    env_bridge = os.environ.copy()
                    env_bridge.update(PRAXIS_BRIDGE_DEVICE_TOKEN=token,
                                      PRAXIS_BRIDGE_CONTROLLER_TOKEN=controller)
                    bridge = subprocess.Popen([str(bridge_exe), "--listen", f"127.0.0.1:{port}",
                        "--state-dir", str(root / "bridge-state")], cwd=root, env=env_bridge,
                        stdout=bridge_log, stderr=bridge_log, creationflags=0x08000000)
                    processes.append(bridge)
                    until = time.monotonic() + 20
                    while time.monotonic() < until:
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=1):
                                break
                        except OSError:
                            time.sleep(.1)
                    env_body = os.environ.copy()
                    env_body.update(PRAXIS_BODY_TOKEN=token, HELENE_CU_PYTHON_FS="1",
                        HELENE_CU_PYTHON_EXE=sys.executable, HELENE_CU_PYTHON_FS_SCRIPT=str(cli),
                        HELENE_CU_PYTHON_READS="1",
                        HELENE_CU_PYTHON_SCRIPT=str(cli.with_name("cu_read_cli.py")))
                    body = subprocess.Popen([str(body_exe), "connect", "--config", str(config)],
                        cwd=root, env=env_body, stdout=body_log, stderr=body_log,
                        creationflags=0x08000000)
                    processes.append(body)
                    client._settings = lambda: (f"http://127.0.0.1:{port}", controller, device)
                    until = time.monotonic() + 40
                    while time.monotonic() < until:
                        status = client.call("body.status", {}, timeout=3)
                        if status.get("ok") and isinstance(status.get("identity"), dict):
                            break
                        self.assertIsNone(body.poll(), str(status))
                        time.sleep(.2)
                    else:
                        self.fail(f"body did not connect: {status}")
                    self.assertIn("native-win32-with-python-reads",
                        [item["name"] for item in status["manifest"]["adapters"]])
                    self.assertTrue(client.call("desktop.status", {}, timeout=30)["ok"])
                    path = root / "file-é.txt"
                    rid, oid = "fs-" + uuid.uuid4().hex, "op-" + uuid.uuid4().hex
                    created = client.call("fs.write_atomic", {"path": str(path), "content": "one"},
                                          request_id=rid, operation_id=oid, timeout=55)
                    self.assertTrue(created["ok"], created)
                    self.assertEqual(path.read_text(), "one")
                    self.assertEqual(created["after_sha256"], hashlib.sha256(b"one").hexdigest())
                    # Same physical request cannot reapply even after outside change.
                    path.write_text("outside")
                    replay = client.call("fs.write_atomic", {"path": str(path), "content": "one"},
                                         request_id=rid, operation_id=oid, timeout=55)
                    self.assertEqual(replay["result"], created["result"])
                    self.assertEqual(path.read_text(), "outside")
                    conflict = client.call("fs.write_atomic", {"path": str(path), "content": "different"},
                                           request_id=rid, operation_id=oid, timeout=55)
                    self.assertFalse(conflict["ok"])
                    self.assertEqual(conflict["code"], "id_conflict")
                    larger = root / "escaped.bin"
                    larger.write_bytes(b"\x00" * (2 * 1024 * 1024))
                    escaped = client.call("fs.read", {"path": str(larger), "limit": 8 * 1024 * 1024}, timeout=55)
                    self.assertTrue(escaped["ok"], escaped.get("code"))
                    self.assertEqual(len(escaped["text"]), 2 * 1024 * 1024)
                    # The real body→bridge frame, not just the child pipe:
                    # 8 MiB of NUL becomes ~48 MiB of JSON on the wire.
                    larger.write_bytes(b"\x00" * (8 * 1024 * 1024))
                    large_id, large_op = "fs-" + uuid.uuid4().hex, "op-" + uuid.uuid4().hex
                    full = client.call("fs.read", {"path": str(larger), "limit": 8 * 1024 * 1024},
                                       request_id=large_id, operation_id=large_op, timeout=60)
                    self.assertTrue(full["ok"], full.get("code"))
                    self.assertEqual((len(full["text"]), full["text"].count("\x00"), full["eof"]),
                                     (8 * 1024 * 1024, 8 * 1024 * 1024, True))
                    del full
                    larger.write_bytes(b"changed")
                    replay_read = client.call("fs.read", {"path": str(larger), "limit": 8 * 1024 * 1024},
                                              request_id=large_id, operation_id=large_op, timeout=60)
                    self.assertTrue(replay_read["ok"], replay_read.get("code"))
                    self.assertEqual(len(replay_read["text"]), 8 * 1024 * 1024)
                    del replay_read
                    long_write = client.call("fs.write_atomic", {"path": str(root / "long.txt"),
                                                            "content": "x" * 100_000}, timeout=55)
                    self.assertTrue(long_write["ok"], long_write.get("code"))
                    self.assertEqual(long_write["size"], 100_000)
                    too_large = root / "http-limit.txt"
                    rejected = client.call("fs.write_atomic", {"path": str(too_large),
                        "content": "x" * (2 * 1024 * 1024 + 1)}, timeout=30)
                    self.assertFalse(rejected["ok"])
                    self.assertEqual(rejected["code"], "http_413")
                    self.assertFalse(too_large.exists())
                    for capability, args in (("fs.stat", {"path": str(path)}),
                        ("fs.list", {"path": str(root), "limit": 3}),
                        ("fs.read", {"path": str(path), "offset": 1}),
                        ("fs.hash", {"path": str(path)})):
                        selected = client.call(capability, args, timeout=55)
                        self.assertTrue(selected["ok"], selected)
                        native_env = os.environ.copy()
                        native_env["PRAXIS_BODY_TOKEN"] = token
                        for key in list(native_env):
                            if key.startswith("HELENE_CU_"):
                                native_env.pop(key)
                        native = subprocess.run([str(body_exe), "invoke-local", "--config", str(config),
                            "--capability", capability, "--args", json.dumps(args),
                            "--execution", "interactive"], cwd=root, env=native_env,
                            capture_output=True, timeout=30)
                        self.assertEqual(native.returncode, 0, native.stderr)
                        original = json.loads(native.stdout)["result"]
                        self.assertEqual(set(selected["result"]), set(original), capability)
                        for key in selected["result"]:
                            if key not in ("modified_unix_ms", "entries", "total"):
                                self.assertEqual(selected["result"][key], original[key], (capability, key))
                    links = root / "links"
                    links.mkdir()
                    linked_target = links / "target.txt"
                    linked_target.write_bytes(b"link destination")
                    link = links / "alias.txt"
                    link.symlink_to(linked_target)
                    for dangling in (False, True):
                        if dangling:
                            linked_target.unlink()
                        link_args = {"path": str(links), "limit": 5}
                        selected_links = client.call("fs.list", link_args, timeout=55)
                        self.assertTrue(selected_links["ok"], selected_links)
                        native = subprocess.run([str(body_exe), "invoke-local", "--config", str(config),
                            "--capability", "fs.list", "--args", json.dumps(link_args),
                            "--execution", "interactive"], cwd=root, env=native_env,
                            capture_output=True, timeout=30)
                        self.assertEqual(native.returncode, 0, native.stderr)
                        self.assertEqual(selected_links["result"]["entries"],
                                         json.loads(native.stdout)["result"]["entries"])
                    # A known pre-effect CAS mismatch must be Failed, not InDoubt.
                    bad = client.call("fs.replace", {"path": str(path), "old": "outside", "new": "x",
                        "expected_sha256": "0" * 64}, timeout=55)
                    self.assertFalse(bad["ok"])
                    self.assertEqual(bad["code"], "capability")
                    # Serde's native bool field rejects even falsey non-bools.
                    # The selected Python route must refuse before mutating too.
                    for capability, fields in (("fs.write_atomic", {"content": "unbacked"}),
                                               ("fs.replace", {"old": "outside", "new": "unbacked"})):
                        for value in (None, 0, ""):
                            with self.subTest(capability=capability, backup=value):
                                malformed = client.call(capability, {"path": str(path),
                                    "backup": value, **fields}, timeout=55)
                                self.assertFalse(malformed["ok"])
                                self.assertEqual(malformed["code"], "capability")
                                self.assertEqual(path.read_text(), "outside")
                    # This unprivileged disposable fixture has no SYSTEM helper;
                    # it proves the dispatch boundary, not the installed service.
                    system = client.call("fs.stat", {"path": str(path)}, execution="system", timeout=8)
                    self.assertFalse(system["ok"])
                    self.assertEqual(system["code"], "system_router")
                    # A child can commit then die without a receipt: the journal
                    # retains InDoubt, and exact replay neither runs it nor lies.
                    marker = root / "unknown.txt"
                    fake = root / "commit_then_die.py"
                    fake.write_text("import json,os,sys\n"
                        "r=json.loads(sys.stdin.buffer.read())\n"
                        "open(r['args']['path'],'w').write('effect')\n"
                        "os._exit(9)\n", encoding="utf-8")
                    # A new disposable body instance with same state/journal but
                    # different selected executor proves restart replay too.
                    body.terminate(); body.wait(timeout=6)
                    env_body["HELENE_CU_PYTHON_FS_SCRIPT"] = str(fake)
                    second = subprocess.Popen([str(body_exe), "connect", "--config", str(config)],
                        cwd=root, env=env_body, stdout=body_log, stderr=body_log,
                        creationflags=0x08000000)
                    processes.append(second)
                    until = time.monotonic() + 30
                    while time.monotonic() < until:
                        ready = client.call("body.status", {}, timeout=3)
                        if ready.get("ok"):
                            break
                        time.sleep(.2)
                    unknown_id, unknown_op = "fs-" + uuid.uuid4().hex, "op-" + uuid.uuid4().hex
                    unknown = client.call("fs.write_atomic", {"path": str(marker), "content": "effect"},
                        request_id=unknown_id, operation_id=unknown_op, timeout=55)
                    self.assertEqual(marker.read_text(), "effect")
                    self.assertFalse(unknown["ok"])
                    self.assertEqual((unknown["status"], unknown["code"], unknown["effect"]),
                                     ("in_doubt", "effect_in_doubt", "unknown"))
                    unknown_frame = client.response(unknown_id)["response"]
                    marker.write_text("untouched")
                    second.terminate(); second.wait(timeout=6)
                    env_body["HELENE_CU_PYTHON_FS_SCRIPT"] = str(cli)
                    third = subprocess.Popen([str(body_exe), "connect", "--config", str(config)],
                        cwd=root, env=env_body, stdout=body_log, stderr=body_log,
                        creationflags=0x08000000)
                    processes.append(third)
                    until = time.monotonic() + 30
                    while time.monotonic() < until:
                        ready = client.call("body.status", {}, timeout=3)
                        if ready.get("ok"):
                            break
                        time.sleep(.2)
                    replay_unknown = client.call("fs.write_atomic", {"path": str(marker), "content": "effect"},
                        request_id=unknown_id, operation_id=unknown_op, timeout=55)
                    self.assertEqual(replay_unknown["result"], unknown["result"])
                    self.assertEqual(replay_unknown["status"], "in_doubt")
                    self.assertEqual(client.response(unknown_id)["response"], unknown_frame)
                    self.assertEqual(marker.read_text(), "untouched")
                    # The bridge above can answer from its own durable cache.
                    # Open a NEW Runtime directly on the old body's journal to
                    # establish the body replay itself, not merely bridge replay.
                    envelope = {"protocol": "praxis.body.v1", "message_id": str(uuid.uuid4()),
                        "device_id": device, "seq": 1, "type": "invoke",
                        "request_id": unknown_id, "operation_id": unknown_op,
                        "execution": "interactive", "capability": "fs.write_atomic",
                        "args": {"path": str(marker), "content": "effect"}}
                    direct = subprocess.run([str(body_exe), "invoke-envelope", "--config", str(config)],
                        input=json.dumps(envelope).encode("utf-8"), cwd=root, env=env_body,
                        capture_output=True, timeout=30)
                    self.assertEqual(direct.returncode, 0, direct.stderr)
                    saved_frame = json.loads(direct.stdout)
                    self.assertEqual(saved_frame["type"], "result")
                    self.assertEqual(saved_frame["status"], "in_doubt")
                    self.assertEqual(saved_frame["result"], unknown_frame["result"])
                    self.assertEqual(marker.read_text(), "untouched")
                finally:
                    for proc in reversed(processes):
                        if proc.poll() is None:
                            proc.terminate()
                    for proc in reversed(processes):
                        try:
                            proc.wait(timeout=6)
                        except subprocess.TimeoutExpired:
                            proc.kill(); proc.wait(timeout=4)


if __name__ == "__main__":
    unittest.main()
