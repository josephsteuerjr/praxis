"""Real process consumers for terminal-independent portable launch."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

DESK=Path(__file__).resolve().parents[1];sys.path.insert(0,str(DESK))
from localharness import desktop_entry as entry

def wait(path):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if path.is_file():return path.read_text()
        time.sleep(.03)
    raise AssertionError('Missing '+str(path))

class DesktopEntry(unittest.TestCase):
    def test_background_one_owner_repeated_click_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'Helene with spaces';(root/'runtime/bin').mkdir(parents=True);(root/'app/localharness').mkdir(parents=True)
            (root/'runtime/bin/python3').symlink_to(sys.executable)
            shutil.copy2(Path(entry.__file__),root/'app/localharness/desktop_entry.py')
            (root/'app/python_app.py').write_text("import os,time\nfrom pathlib import Path\np=Path(__file__).parents[1]\n(p/'started').write_text(str(os.getpid()))\nwhile not (p/'close').exists():time.sleep(.03)\n")
            env=dict(os.environ,XDG_STATE_HOME=str(Path(d)/'logs'))
            command=[sys.executable,str(root/'app/localharness/desktop_entry.py'),'--root',str(root)]
            first=subprocess.run(command,env=env,capture_output=True,timeout=8)
            self.assertEqual(first.returncode,0,first.stderr)
            pid=int(wait(root/'started'));self.assertEqual(os.getsid(pid),pid)
            try:
                second=subprocess.run(command,env=env,capture_output=True,timeout=8)
                self.assertEqual(second.returncode,0,second.stderr)
                self.assertEqual(int((root/'started').read_text()),pid)
                logs='\n'.join(p.read_text() for p in (Path(d)/'logs/helene/logs').glob('*.log'))
                self.assertIn('already open',logs)
            finally:
                (root/'close').touch()
                deadline=time.monotonic()+8
                import psutil
                while time.monotonic()<deadline:
                    try:
                        if psutil.Process(pid).status()==psutil.STATUS_ZOMBIE:break
                    except psutil.NoSuchProcess:break
                    time.sleep(.03)
            (root/'close').unlink();(root/'started').unlink()
            third=subprocess.run(command,env=env,capture_output=True,timeout=8)
            self.assertEqual(third.returncode,0,third.stderr)
            try:self.assertNotEqual(int(wait(root/'started')),pid)
            finally:(root/'close').touch();time.sleep(.2)

    def test_menu_entry_uses_library_aware_launcher_and_no_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'Helene with spaces % and $'
            with patch.dict(os.environ,{'XDG_DATA_HOME':d}):path=entry.install_desktop(root)
            text=path.read_text()
            self.assertIn('Terminal=false',text)
            self.assertIn('Start Helene.sh',text)
            self.assertIn('Path='+str(root),text)
            self.assertIn('Icon='+str(root/'app/resources/python-app.png'),text)
            self.assertIn('%%',text)

if __name__=='__main__':unittest.main(verbosity=2)
