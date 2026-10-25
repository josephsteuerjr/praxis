"""The real headless native-window command reader reaches the shipped Python IO.

Run after: cargo build --offline --manifest-path desk/shell/Cargo.toml
           --no-default-features --features host --bin helene-host
No installer, service, registry, live agent or network endpoint is involved.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from deskd import local_files


class FileRules(unittest.TestCase):
    def test_known_folder_redirect_and_missing_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            redirected = Path(tmp) / "elsewhere"
            redirected.mkdir()
            with mock.patch.object(local_files, "_known_folder", side_effect=lambda guid: (
                    redirected if guid == "374DE290-123F-4565-9164-39C4925E467B" else None)), \
                 mock.patch.object(local_files.Path, "home", return_value=home):
                rows = local_files._listing("", home)
            self.assertEqual(rows["path"], str(redirected.resolve()))
            self.assertEqual(rows["locations"][0]["path"], str(redirected))

    def test_system_impersonation_does_not_show_system_known_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner = Path(tmp) / "owner"
            system = Path(tmp) / "system"
            (owner / "Downloads").mkdir(parents=True)
            (system / "Downloads").mkdir(parents=True)
            with mock.patch.object(local_files.Path, "home", return_value=system), \
                 mock.patch.object(local_files, "_known_folder", return_value=system / "Downloads") as known:
                places = local_files.locations(owner)
            known.assert_not_called()
            self.assertEqual(places[0]["path"], str(owner / "Downloads"))

    def test_native_default_chooses_desktop_when_it_is_the_only_special_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            desktop = home / "Desktop"
            desktop.mkdir()
            with mock.patch.object(local_files.Path, "home", return_value=home), \
                 mock.patch.object(local_files, "_known_folder", return_value=None):
                self.assertEqual(local_files._listing("", home, native=True)["path"], str(desktop.resolve()))
                self.assertEqual(local_files._listing("", home)["path"], str(home.resolve()))

    def test_precommit_failure_preserves_target_and_cleans_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "old.txt"
            target.write_bytes(b"old bytes")
            with mock.patch.object(local_files.os, "fsync", side_effect=OSError("disk failed")):
                with self.assertRaises(OSError):
                    local_files.save_preview(tmp, "old.txt", base64.b64encode(b"new").decode(), True)
            self.assertEqual(target.read_bytes(), b"old bytes")
            self.assertEqual(list(root.glob(".helene-save-*")), [])

    def test_precommit_validation_and_no_replace_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "report.txt"
            target.write_bytes(b"original")
            for name, data in (("CON.txt", "YQ=="), ("report.txt", "broken@@"),
                               ("../bad", "YQ==")):
                with self.assertRaises((ValueError, FileExistsError)):
                    local_files.save_preview(tmp, name, data)
            with self.assertRaises(FileExistsError):
                local_files.save_preview(tmp, "report.txt", "YQ==")
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(list(root.glob(".helene-save-*")), [])

    def test_native_mime_table_does_not_promote_unknown_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "document.html"
            path.write_bytes(b"<p>untrusted</p>")
            self.assertEqual(local_files.read_file(str(path), native=True)["mime"],
                             "application/octet-stream")
            self.assertEqual(local_files.read_file(str(path))["mime"], "text/html")

    def test_read_and_save_size_boundary_scaled_without_large_allocation(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(local_files, "MAX_FILE", 4):
            folder = Path(tmp)
            source = folder / "source.bin"
            source.write_bytes(b"abcd")
            self.assertEqual(base64.b64decode(local_files.read_file(str(source))["data"]), b"abcd")
            saved = local_files.save_preview(tmp, "copy.bin", base64.b64encode(b"abcd").decode())
            self.assertEqual(saved["bytes"], 4)
            source.write_bytes(b"abcde")
            with self.assertRaises(ValueError): local_files.read_file(str(source))
            with self.assertRaises(ValueError):
                local_files.save_preview(tmp, "too-big.bin", base64.b64encode(b"abcde").decode())
            self.assertFalse((folder / "too-big.bin").exists())


@unittest.skipUnless(sys.platform == "win32", "Windows native-host consumer")
class NativeHost(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory(prefix="paper-host-")
        self.addCleanup(self.home.cleanup)
        self.root = Path(self.home.name)
        (self.root / "app/localharness").mkdir(parents=True)
        (self.root / "app/deskd").mkdir(parents=True)
        (self.root / "runtime").mkdir()
        for relative in ("localharness/local_files_cli.py", "deskd/local_files.py", "deskd/__init__.py"):
            shutil.copy2(DESK / relative, self.root / "app" / relative)
        # Only a temporary hardlink to THIS test interpreter; PYTHONHOME locates
        # its stdlib. The production route selects runtime/python.exe itself.
        os.link(sys.executable, self.root / "runtime/python.exe")
        (self.root / "data").mkdir()
        (self.root / "helene.json").write_text(json.dumps({
            "mode": "remote", "base": "http://127.0.0.1:9", "key": "not-a-live-key",
            "setup_complete": True, "python": "runtime/python.exe", "tree": "data", "port": 8094,
        }), encoding="utf-8")
        binary = DESK / "shell/target/debug/helene-host.exe"
        if not binary.is_file():
            self.fail("build the source helene-host before this consumer test")
        self.events = queue.Queue()
        self.process = subprocess.Popen([str(binary), "--root", str(self.root)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        env={**os.environ, "PYTHONHOME": sys.prefix, "PYTHONUTF8": "1"},
                                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        self.addCleanup(self.stop)
        threading.Thread(target=lambda: [self.events.put(json.loads(row)) for row in self.process.stdout],
                         daemon=True).start()
        self.assertEqual(self.events.get(timeout=15).get("event"), "ready")
        self.seq = 0

    def stop(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write(b'{"id":999,"command":"host_shutdown"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=12)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.stderr.close()

    def command(self, name, args):
        self.seq += 1
        ordinal = self.seq
        self.process.stdin.write((json.dumps({"id": ordinal, "command": name, "args": args}) + "\n").encode())
        self.process.stdin.flush()
        while True:
            row = self.events.get(timeout=20)
            if row.get("id") == ordinal:
                return row

    def test_list_read_save_refusal_and_unknown_receipt(self):
        folder = self.root / "owner-files"
        folder.mkdir()
        original = folder / "отчёт.bin"
        original.write_bytes(b"old\x00\xff")
        listed = self.command("local_files_list", {"path": str(folder)})
        self.assertEqual(listed["result"]["entries"][0]["name"], original.name)
        read = self.command("local_files_read", {"path": str(original)})
        self.assertEqual(read["result"]["name"], original.name)
        self.assertEqual(base64.b64decode(read["result"]["data"]), original.read_bytes())
        html = folder / "page.html"
        html.write_bytes(b"<script>untrusted</script>")
        self.assertEqual(self.command("local_files_read", {"path": str(html)})["result"]["mime"],
                         "application/octet-stream")
        for invalid in ("not-base64", "YQ=="):
            refused = self.command("local_files_save", {"folder": str(folder), "name": original.name,
                                                        "data": invalid, "overwrite": False})
            self.assertIn("error", refused)
        self.assertEqual(original.read_bytes(), b"old\x00\xff")
        saved = self.command("local_files_save", {"folder": str(folder), "name": original.name,
                                                  "data": base64.b64encode(b"new\x00").decode(), "overwrite": True})
        self.assertEqual(saved["result"]["bytes"], 4)
        self.assertEqual(original.read_bytes(), b"new\x00")
        self.assertEqual(list(folder.glob(".helene-save-*")), [])

        # A missing script must not make Rust fall back to its removed file IO.
        script = self.root / "app/localharness/local_files_cli.py"
        script.rename(script.with_suffix(".held"))
        unavailable = self.command("local_files_save", {"folder": str(folder), "name": original.name,
                                                        "data": "YQ==", "overwrite": True})
        self.assertIn("Python", unavailable.get("error", ""))
        self.assertEqual(original.read_bytes(), b"new\x00")
        script.with_suffix(".held").rename(script)

        # The dispatched writer may have committed before stdout was lost.
        # Native response is unknown, not a retry; readback shows new bytes.
        script.write_text("import json,sys,pathlib,base64\n"
                          "row=json.load(sys.stdin)\n"
                          "pathlib.Path(row['folder'],row['name']).write_bytes(base64.b64decode(row['data']))\n"
                          "sys.stdout.write('broken receipt')\n", encoding="utf-8")
        unknown = self.command("local_files_save", {"folder": str(folder), "name": original.name,
                                                    "data": "Yg==", "overwrite": True})
        self.assertIn("перечитай файл", unknown.get("error", ""))
        self.assertEqual(original.read_bytes(), b"b")

    def test_forced_host_death_kills_dispatched_python_before_late_save(self):
        folder = self.root / "owner-files"
        folder.mkdir()
        target = folder / "keep.bin"
        target.write_bytes(b"old")
        marker, gate = self.root / "python-started", self.root / "allow-late-write"
        script = self.root / "app/localharness/local_files_cli.py"
        script.write_text(
            "import os,sys,json,time,pathlib,base64\n"
            "row=json.load(sys.stdin)\n"
            f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
            f"while not pathlib.Path({str(gate)!r}).exists():time.sleep(.05)\n"
            "pathlib.Path(row['folder'],row['name']).write_bytes(base64.b64decode(row['data']))\n",
            encoding="utf-8")
        self.process.stdin.write((json.dumps({"id": 1, "command": "local_files_save",
                                              "args": {"folder": str(folder), "name": target.name,
                                                       "data": "bmV3", "overwrite": True}}) + "\n").encode())
        self.process.stdin.flush()
        until = time.monotonic() + 10
        while not marker.is_file() and time.monotonic() < until:
            self.assertIsNone(self.process.poll(), "host ended before file child ran")
            time.sleep(.05)
        self.assertTrue(marker.is_file(), "file child did not enter its script")
        pid = int(marker.read_text(encoding="ascii"))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.WaitForSingleObject.restype = ctypes.c_uint32
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, 0, pid)
        self.assertTrue(handle, "cannot attest child process identity")
        try:
            self.assertEqual(kernel.WaitForSingleObject(handle, 0), 0x102,
                             "the file child died before the forced host exit")
            self.process.kill()
            self.process.wait(timeout=10)
            self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0,
                             "file child survived native host death")
            gate.touch()
            time.sleep(.2)
            self.assertEqual(target.read_bytes(), b"old", "orphan committed after host exit")
        finally:
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
