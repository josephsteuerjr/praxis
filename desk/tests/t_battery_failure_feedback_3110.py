"""Real small Python/Node evidence; RED observations permit repair, never apply."""
from pathlib import Path
import sys,unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from localharness import source_update_battery as battery,source_verification_bundle as bundle,source_merge as merge
from localharness.package_notice import read
import t_source_update_battery_2810 as fixture

class FailureFeedback(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.Battery();self.addCleanup(self.fx.doCleanups);self.fx.setUp()
        self.material=bundle.build(self.fx.repo,self.fx.root/'python-node-material','2.3.0',
            {sys.platform:bundle.executor_lock()})
        self.fx.operation['verification_bundle']=self.material

    def run_case(self,body):
        self.fx.put(self.fx.core/'test_authored_failure.py',body)
        self.fx.operation['candidate_sha256']=merge.candidate_signature(self.fx.directory)
        self.fx.operation['composition_sha256']=self.fx.composition()['sha256']
        return self.fx.prepare()

    def test_actual_failed_assertion_returns_exact_red_feedback_and_stays_unacceptable(self):
        cache,report=self.run_case('import unittest\nclass Bad(unittest.TestCase):\n def test_authored(self):self.assertEqual("wrong-app", "expected-app")\n')
        self.assertFalse(report['full_pass']);before=(cache/'receipt.json').read_bytes()
        with self.assertRaises(ValueError):battery.validate(self.fx.operation,self.fx.directory)
        feedback=battery.failure_feedback(self.fx.operation,self.fx.directory)
        self.assertIsNotNone(feedback);self.assertIs(feedback['full_pass'],False)
        actual={case['id'] for row in report['results'] for case in row.get('observation',{}).get('outcomes',[]) if case.get('status') in ('failed','error')}
        self.assertEqual({row['case_id'] for row in feedback['findings']},actual)
        self.assertTrue(any('wrong-app' in row['detail'] and 'expected-app' in row['detail'] for row in feedback['findings']))
        self.assertEqual(before,(cache/'receipt.json').read_bytes())
        self.assertFalse(read(cache/'report.json')['full_pass'])
        with self.assertRaises(ValueError):battery.validate(self.fx.operation,self.fx.directory)

    def test_undeclared_skip_is_not_an_automatic_code_correction(self):
        _,report=self.run_case('import unittest\nclass Missing(unittest.TestCase):\n @unittest.skip("missing private SDK capability")\n def test_missing(self):pass\n')
        self.assertFalse(report['full_pass'])
        self.assertIsNone(battery.failure_feedback(self.fx.operation,self.fx.directory))

    def test_changed_raw_failure_log_refuses_even_repair_admission(self):
        cache,_=self.run_case('import unittest\nclass Bad(unittest.TestCase):\n def test_bad(self):self.fail("actual failure")\n')
        path=next(cache.glob('*.output'));path.write_bytes(path.read_bytes()+b'changed diagnostic')
        with self.assertRaisesRegex(ValueError,'raw evidence changed'):
            battery.failure_feedback(self.fx.operation,self.fx.directory)

    def test_missing_executor_import_is_not_an_automatic_source_repair(self):
        _,report=self.run_case('import missing_executor_sdk_fixture_3110\n')
        self.assertFalse(report['full_pass'])
        self.assertIsNone(battery.failure_feedback(self.fx.operation,self.fx.directory))

    def test_unconfirmed_child_custody_outlives_an_older_complete_failure_receipt(self):
        cache,_=self.run_case('import unittest\nclass Bad(unittest.TestCase):\n def test_bad(self):self.fail("actual failure")\n')
        (cache/'custody-unconfirmed.json').write_text('{}',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'custody remains unconfirmed'):
            battery.failure_feedback(self.fx.operation,self.fx.directory)
        with self.assertRaisesRegex(ValueError,'custody remains unconfirmed'):
            battery.validate(self.fx.operation,self.fx.directory)

    def test_node_import_pseudo_case_is_not_typed_python_assertion_feedback(self):
        self.fx.put(self.fx.repo/'desk/app/test/one.mjs',
            'import missing from "helene-missing-executor-fixture-3110";\n')
        material=bundle.build(self.fx.repo,self.fx.root/'node-import-material','2.3.0',
            {sys.platform:bundle.executor_lock()})
        self.fx.operation['verification_bundle']=material
        _,report=self.run_case('import unittest\nclass Fine(unittest.TestCase):\n def test_fine(self):self.assertTrue(True)\n')
        self.assertFalse(report['full_pass'])
        node=next(row for row in report['results'] if row['kind']=='node')
        self.assertEqual(node['status'],'failed')
        self.assertTrue(node['observation']['coverage_complete'])
        self.assertIsNone(battery.failure_feedback(self.fx.operation,self.fx.directory))

if __name__=='__main__':unittest.main()
