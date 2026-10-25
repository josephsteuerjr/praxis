"""The complete desktop source package carries the exact native-window CLI chain.

Before running, build the first-party app/mobile/miniapp dist as for a normal
package. This test refuses a partial desk package; it never installs it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
import deskpkg


class DesktopPackageFileConsumer(unittest.TestCase):
    def test_full_package_imports_same_cli_and_file_owner(self):
        with tempfile.TemporaryDirectory(prefix="paper-package-") as tmp:
            base = Path(tmp)
            app = base / "app"
            manifest = deskpkg.build(app, deskpkg.WINDOWS, allow_partial=False, log=lambda _msg: None)
            self.assertEqual(manifest["skipped"], [])
            for rel in ("localharness/local_files_cli.py", "deskd/local_files.py"):
                original, staged = DESK / rel, app / rel
                self.assertEqual(staged.read_bytes(), original.read_bytes(), rel)
                self.assertEqual(manifest["files"][rel], hashlib.sha256(staged.read_bytes()).hexdigest())
            folder = base / "files"
            folder.mkdir()
            original = folder / "original.bin"
            original.write_bytes(b"package\x00\xff")
            script = app / "localharness/local_files_cli.py"
            env = {**os.environ, "PYTHONUTF8": "1"}

            def request(row):
                result = subprocess.run([sys.executable, str(script)], input=json.dumps(row).encode(),
                                        capture_output=True, timeout=15, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout.decode("utf-8"))

            seen = request({"action": "read", "path": str(original)})
            self.assertTrue(seen["ok"])
            self.assertEqual(base64.b64decode(seen["result"]["data"]), original.read_bytes())
            saved = request({"action": "save", "folder": str(folder), "name": "copy.bin",
                             "overwrite": False, "data": base64.b64encode(b"from package").decode()})
            self.assertTrue(saved["ok"])
            self.assertEqual((folder / "copy.bin").read_bytes(), b"from package")
            if sys.platform == "win32":
                binary = DESK / "shell/target/debug/helene-host.exe"
                self.assertTrue(binary.is_file(), "build source helene-host before the package test")
                (base / "runtime").mkdir()
                os.link(sys.executable, base / "runtime/python.exe")
                (base / "data").mkdir()
                (base / "helene.json").write_text(json.dumps({
                    "mode": "remote", "base": "http://127.0.0.1:9", "key": "not-live",
                    "setup_complete": True, "python": "runtime/python.exe", "tree": "data", "port": 8094,
                }), encoding="utf-8")
                env["PYTHONHOME"] = sys.prefix
                host = subprocess.Popen([str(binary), "--root", str(base)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, env=env,
                                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                rows = queue.Queue()
                threading.Thread(target=lambda: [rows.put(json.loads(line)) for line in host.stdout],
                                 daemon=True).start()
                try:
                    self.assertEqual(rows.get(timeout=15).get("event"), "ready")
                    host.stdin.write((json.dumps({"id": 1, "command": "local_files_read",
                                                      "args": {"path": str(folder / "copy.bin")}}) + "\n").encode())
                    host.stdin.flush()
                    response = rows.get(timeout=20)
                    self.assertEqual(response.get("id"), 1, response)
                    self.assertEqual(base64.b64decode(response["result"]["data"]), b"from package")
                finally:
                    if host.poll() is None:
                        host.stdin.write(b'{"id":999,"command":"host_shutdown"}\n')
                        host.stdin.flush()
                        try: host.wait(timeout=12)
                        except subprocess.TimeoutExpired: host.kill(); host.wait(timeout=5)
                    host.stdin.close()
                    host.stdout.close()
                    host.stderr.close()
            print("package_files=%d digest=%s" % (len(manifest["files"]), manifest["digest"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
