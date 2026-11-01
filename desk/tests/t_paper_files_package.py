"""The complete desktop package carries the actual Python-window file consumer.

Before running, build the first-party app/mobile/miniapp dist as for a normal
package. This test refuses a partial desk package; it never installs it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
            (base/'data').mkdir()
            (base/'helene.json').write_text(json.dumps({'mode':'local','tree':'data',
                'code':'tree','setup_complete':False}),encoding='utf8')
            # The current window delegates to Controller, not the retired Rust
            # host. Isolated import proves this complete package carries its
            # real dependency closure; no source PYTHONPATH or model is used.
            code="""import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from localharness.python_controller import Controller
owner=Controller(Path(sys.argv[2]),lambda *unused:None)
try:result=owner.invoke('local_files_read',{'path':sys.argv[3]})
finally:owner.close()
print(json.dumps(result))
"""
            response=subprocess.run([sys.executable,'-I','-c',code,str(app),str(base),str(folder/'copy.bin')],
                                    capture_output=True,text=True,timeout=20,env=env)
            self.assertEqual(response.returncode,0,response.stderr)
            self.assertEqual(base64.b64decode(json.loads(response.stdout)['data']),b'from package')
            print("package_files=%d digest=%s" % (len(manifest["files"]), manifest["digest"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
