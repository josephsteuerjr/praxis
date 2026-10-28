"""Real pytest-function consumer and source-bound recipe selection."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_verification_bundle as bundle,source_update_battery as battery


class PytestCoverage(unittest.TestCase):
    def test_canonical_three_function_suite_is_collected_with_its_real_tmp_path_fixture(self):
        desk=Path(__file__).resolve().parents[1]
        source=desk/'tests/test_python_package_version.py'
        self.assertEqual(bundle.python_framework(source),'pytest')
        with tempfile.TemporaryDirectory(prefix='helene-pytest-consumer-') as tmp:
            work=Path(tmp);report=work/'cases.json'
            done=subprocess.run([sys.executable,'-B',str(desk/'localharness/source_verification_runner.py'),
                '--root',str(desk),'--source',str(source),'--framework','pytest','--report',str(report),'--sandbox',str(work)],
                capture_output=True,text=True,encoding='utf8',timeout=60,
                env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.assertEqual(done.returncode,0,done.stdout+'\n'+done.stderr)
            value=json.loads(report.read_text(encoding='utf8'))
        self.assertEqual(len(value['collected']),3)
        self.assertEqual(value['collected'],value['executed'])
        self.assertTrue(value['coverage_complete']);self.assertTrue(value['successful'])
        self.assertTrue(all(row['status']=='passed' for row in value['outcomes']))

    def test_recipe_keeps_unittest_and_adds_actual_pytest_sources(self):
        files={'live/praxis_test.py':{},'live/test_core.py':{},
            'live/test_functions.py':{'framework':'pytest'},
            'desk/tests/t_classes.py':{},'desk/tests/test_functions.py':{'framework':'pytest'},
            'desk/app/test/ui.test.mjs':{}}
        lanes=bundle.recipe(files)
        self.assertEqual(next(row for row in lanes if row['id']=='core')['kind'],'unittest')
        self.assertEqual(next(row for row in lanes if row['id']=='desk/tests/test_functions.py')['kind'],'pytest-file')
        self.assertEqual(next(row for row in lanes if row['id']=='pytest:live/test_functions.py')['kind'],'pytest-file')
        self.assertEqual(next(row for row in lanes if row['id']=='desk/tests/t_classes.py')['kind'],'unittest-file')
        self.assertIn('python',battery._required_tools(lanes))

    def test_cross_tree_source_locator_is_isolated_composed_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/'repository/desk';core=root/'repository/live'
            env=battery._environment(root/'home',core,app)
            self.assertEqual(env['HELENE_TREE_SRC'],str(core))
            self.assertEqual(env['HELENE_TEST_SOURCE_ROOT'],str(app.parent))


if __name__=='__main__':unittest.main()
