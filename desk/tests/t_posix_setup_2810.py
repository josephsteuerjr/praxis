"""Real isolated POSIX writer shutdown and data-preserving program retirement."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import migration_posix as lifecycle
from localharness.package_notice import write


@unittest.skipIf(os.name=='nt','POSIX lifecycle runs on the owned Linux stand')
class PosixSetup(unittest.TestCase):
    def test_installer_cwd_cannot_claim_foreign_inline_or_relative_python(self):
        from contextlib import chdir
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';other=root/'other';children=[]
            for folder in (old,other):
                folder.mkdir();(folder/'worker.py').write_text('import time\ntime.sleep(90)\n')
            try:
                children.append(subprocess.Popen([sys.executable,str(old/'worker.py')]))
                children.append(subprocess.Popen([sys.executable,'worker.py'],cwd=old))
                children.append(subprocess.Popen([sys.executable,'worker.py'],cwd=other))
                children.append(subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'],cwd=old))
                with chdir(old),patch.object(lifecycle,'service',return_value=None):lifecycle.stop(old)
                for child in children[:2]:self.assertIsNotNone(child.poll())
                for child in children[2:]:self.assertIsNone(child.poll())
                self.assertTrue(lifecycle.quiescent(old))
            finally:
                for child in children:
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=5)

    def test_exec_within_selected_program_is_rechecked_before_shutdown(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';old.mkdir();before=old/'before.py';after=old/'after.py'
            ready=root/'ready';go=root/'go';executed=root/'executed'
            after.write_text('from pathlib import Path\nimport time\nPath('+repr(str(executed))+').touch()\ntime.sleep(90)\n')
            before.write_text('from pathlib import Path\nimport os,sys,time\nPath('+repr(str(ready))+').touch()\nwhile not Path('+repr(str(go))+').exists():time.sleep(.01)\nos.execv(sys.executable,[sys.executable,'+repr(str(after))+'])\n')
            child=subprocess.Popen([sys.executable,str(before)])
            try:
                end=time.monotonic()+5
                while not ready.exists() and time.monotonic()<end:time.sleep(.02)
                self.assertTrue(ready.exists());snapshot=lifecycle._processes(old);original=lifecycle._processes;calls=[]
                self.assertTrue(any(p.pid==child.pid for p,_ in snapshot));go.touch()
                while not executed.exists() and time.monotonic()<end:time.sleep(.02)
                self.assertTrue(executed.exists())
                def discover(path):
                    calls.append(path)
                    return snapshot if len(calls)<=2 else original(path)
                with patch.object(lifecycle,'service',return_value=None),patch.object(lifecycle,'_processes',side_effect=discover):lifecycle.stop(old)
                self.assertIsNotNone(child.poll());self.assertTrue(lifecycle.quiescent(old))
            finally:
                if child.poll() is None:child.terminate()
                child.wait(timeout=5)

    def test_nonchild_zombie_is_quiescent_without_killing_its_parent(self):
        import psutil
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';old.mkdir();entry=old/'worker.py'
            entry.write_text('import time\ntime.sleep(90)\n')
            code='import subprocess,sys,time;p=subprocess.Popen([sys.executable,'+repr(str(entry))+']);print(p.pid,flush=True);time.sleep(90)'
            parent=subprocess.Popen([sys.executable,'-u','-c',code],stdout=subprocess.PIPE,text=True)
            try:
                child=psutil.Process(int(parent.stdout.readline()))
                with patch.object(lifecycle,'service',return_value=None):lifecycle.stop(old)
                self.assertIsNone(parent.poll())
                self.assertEqual(child.status(),psutil.STATUS_ZOMBIE)
                self.assertTrue(lifecycle.quiescent(old))
            finally:
                parent.terminate();parent.wait(timeout=5);parent.stdout.close()

    def test_desktop_items_are_owned_bindings_and_foreign_items_stay_untouched(self):
        for platform in ('linux','darwin'):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);desktop=root/'Desktop';desktop.mkdir();old=root/'old';new=root/'new'
                with patch.object(sys,'platform',platform),patch.dict(os.environ,{'HOME':str(root)}),patch.object(lifecycle,'service',return_value=None):
                    paths=lifecycle.install_shortcuts(old,desktop)
                    self.assertTrue(lifecycle.desktop_binding(Path(paths[0]),old))
                    lifecycle.cleanup_installation(old,new)
                    self.assertFalse(Path(paths[0]).exists())
                    paths=lifecycle.install_shortcuts(new,desktop)
                    target=Path(paths[0]);self.assertTrue(lifecycle.desktop_binding(target,new))
                    protected=target/'Contents/MacOS/Helene' if platform=='darwin' else target
                    protected.write_text('foreign desktop item')
                    with self.assertRaises(ValueError):lifecycle.install_shortcuts(old,desktop)
                    self.assertEqual(protected.read_text(),'foreign desktop item')

    def test_old_writer_stops_without_affecting_a_neighbour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';other=root/'old-other'
            children=[]
            try:
                for folder in (old,other):
                    (folder/'app').mkdir(parents=True)
                    entry=folder/'app/legacy_worker.py';entry.write_text('import time\ntime.sleep(90)\n')
                    children.append(subprocess.Popen([sys.executable,str(entry)]))
                with patch.object(lifecycle,'service',return_value=None):lifecycle.stop(old)
                self.assertIsNotNone(children[0].poll());self.assertIsNone(children[1].poll())
                self.assertTrue(lifecycle.quiescent(old))
            finally:
                for child in children:
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=5)

    def test_retirement_deletes_program_and_keeps_data_source_git_and_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';new=root/'new';new.mkdir()
            for name in ('app/python_app.py','runtime/bin/python3','app/old-profile/memory.jsonl','data/memory/history.jsonl','tree/agent.py','tree/.git/HEAD','helene.json'):
                path=old/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
            note={'startup_proof':True,'data_paths':[str(old/'data')],'old_data_paths':[str(old/'app/old-profile')],'source_paths':[{'old':str(old/'tree')} ]}
            result=lifecycle.remove_program(old,new,note)
            self.assertTrue(result['ok']);self.assertFalse((old/'app/python_app.py').exists())
            for name in ('app/old-profile/memory.jsonl','data/memory/history.jsonl','tree/agent.py','tree/.git/HEAD','helene.json'):self.assertEqual((old/name).read_text(),name)
            self.assertTrue((new/'old-program-retirement.json').is_file())


if __name__=='__main__':unittest.main()
