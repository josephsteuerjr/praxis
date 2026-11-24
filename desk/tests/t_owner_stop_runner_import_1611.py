"""The runner's bare owner_stop import must retain the packaged Windows latch."""
from pathlib import Path
import importlib.util
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DESK=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(DESK))
sys.path.insert(0,str(DESK/'localharness'))
from localharness import first_start,owner_stop


class RunnerOwnerStopImport(unittest.TestCase):
    def test_script_entry_resolves_its_package_without_an_ambient_pythonpath(self):
        with tempfile.TemporaryDirectory() as profile:
            result = subprocess.run([sys.executable, '-I', '-B', str(DESK/'localharness/runner.py'), '--help'],
                                    cwd=profile, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertIn(b'--config', result.stdout)

    def check_latch(self,module):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)/'Helene/data'
            windows=SimpleNamespace(name='nt',environ={'HELENE_USER_DATA':str(data)})
            with patch.object(module,'os',windows),patch.object(first_start.Places,'current',return_value=SimpleNamespace(data=data)):
                self.assertEqual(module.client_file(),data/'owner-stop.json')
                self.assertFalse((data/'owner-stop.json').exists())
                data.mkdir(parents=True)
                (data/'owner-stop.json').write_text('{}',encoding='utf8')
                with patch.object(module,'seed_file',return_value=None):
                    self.assertTrue(module.stopped())

    def test_packaged_import_reads_the_selected_user_latch(self):
        self.check_latch(owner_stop)

    def test_runner_bare_import_reads_the_same_latch(self):
        spec=importlib.util.spec_from_file_location('owner_stop',DESK/'localharness/owner_stop.py')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertFalse(module.__package__)
        self.check_latch(module)


if __name__=='__main__':unittest.main()
