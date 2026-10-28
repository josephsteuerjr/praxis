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
