"""Exercise actual subprocess battery evidence, reuse and panel handoff."""
from pathlib import Path
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import source_merge as merge, source_update, source_update_battery as battery


class Battery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / 'plan'
        self.core = self.directory / 'result'
        self.core.mkdir(parents=True)
        (self.core / 'praxis_test.py').write_text('import unittest\nunittest.main(module=None)\n')
        self.test = self.core / 'test_fixture.py'
        self.test.write_text('import unittest, os\nclass T(unittest.TestCase):\n'
            ' def test_bad(self): self.assertEqual(1, 2)\n'
            ' def test_env(self): self.assertNotIn("PRIVATE_OWNER_VALUE", os.environ)\n')
        self.app = self.root / 'app'
        self.app.mkdir()
        (self.app / 'seam.py').write_text('VALUE=1\n')
        self.harness = {'harness': {'root': str(self.app), 'merge_files': merge.inventory(self.app)}}
        self.plan = {'files': {'local': {}}, 'result': merge.inventory(self.core)}
        self.operation = {'candidate_sha256': merge.signature(self.plan['result']),
                          'coordinator': {'app_sha256': merge.signature(merge.inventory(self.app))}}
        self.patch = mock.patch.object(merge, 'validate_inputs', return_value=self.plan)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.sig = mock.patch.object(merge, 'candidate_signature',
                                    side_effect=lambda _: merge.signature(merge.inventory(self.core)))
        self.sig.start()
        self.addCleanup(self.sig.stop)

    def run_battery(self, stopped=lambda: False):
        with mock.patch.dict(os.environ, {'PRIVATE_OWNER_VALUE': 'must-not-reach-test'}):
            sources = battery.prepare(self.operation, self.directory, self.harness, stopped)
        path = Path(sources['battery']['root'])
        return sources, path, json.loads((path / 'report.md').read_text(encoding='utf8'))

    def test_actual_red_test_reaches_report_and_unchanged_input_reuses_it(self):
        before = merge.inventory(self.core)
        sources, path, report = self.run_battery()
        self.assertTrue(report['complete'])
        self.assertEqual(report['results'][0]['status'], 'failed')
        self.assertEqual(report['results'][0]['tests_run'], 2)
        self.assertIn('1 != 2', (path / report['results'][0]['excerpt']).read_text())
        self.assertEqual(merge.inventory(self.core), before)
        with mock.patch.object(battery, '_execute', side_effect=AssertionError('duplicate run')):
            self.assertEqual(self.run_battery()[0], sources)
        self.test.write_text(self.test.read_text().replace('assertEqual(1, 2)', 'assertEqual(1, 1)'))
        self.plan['result'] = merge.inventory(self.core)
        self.operation['candidate_sha256'] = merge.signature(self.plan['result'])
        _, new, current = self.run_battery()
        self.assertNotEqual(path, new)
        self.assertEqual(current['results'][0]['status'], 'passed')

    def test_modified_cached_evidence_is_not_reused(self):
        _, path, _ = self.run_battery()
        (path / 'report.md').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.run_battery()

    def test_stop_interrupts_actual_child_and_writes_no_complete_receipt(self):
        self.test.write_text('import time\ntime.sleep(60)\n')
        self.plan['result'] = merge.inventory(self.core)
        self.operation['candidate_sha256'] = merge.signature(self.plan['result'])
        calls = 0
        def stopped():
            nonlocal calls
            calls += 1
            return calls >= 4
        with self.assertRaises(InterruptedError):
            self.run_battery(stopped)
        self.assertFalse(list((self.directory / 'test-battery').glob('*/receipt.json')))

    def test_missing_suite_is_explicit_not_a_pass(self):
        self.test.unlink()
        (self.core / 'llm.py').write_text('VALUE=1\n')
        self.plan['result'] = merge.inventory(self.core)
        self.operation['candidate_sha256'] = merge.signature(self.plan['result'])
        _, _, report = self.run_battery()
        self.assertTrue(report['results'])
        self.assertTrue(all(row['status'] == 'unavailable' for row in report['results']))

    def test_unsettled_child_preserves_test_copy(self):
        captured = []
        def unsettled(command, cwd, *args):
            captured.append(Path(cwd).parent)
            raise battery.CustodyUnconfirmed('fixture custody unknown')
        with mock.patch.object(battery, '_execute', side_effect=unsettled):
            with self.assertRaises(battery.CustodyUnconfirmed):
                self.run_battery()
        work = captured[0]
        self.assertTrue(work.is_dir())
        # This fixture never spawned a child; its preserved directory is ours.
        self.addCleanup(battery.shutil.rmtree, work)
        self.assertFalse(list((self.directory / 'test-battery').glob('*/receipt.json')))

    def test_stop_is_honored_before_cached_evidence(self):
        self.run_battery()
        with self.assertRaises(InterruptedError):
            self.run_battery(stopped=lambda: True)

    def test_output_limit_is_recorded_as_attention_material(self):
        self.test.write_text('print("fixture output" * 1000)\n')
        self.plan['result'] = merge.inventory(self.core)
        self.operation['candidate_sha256'] = merge.signature(self.plan['result'])
        with mock.patch.object(battery, 'MAX_OUTPUT', 1000):
            _, _, report = self.run_battery()
        self.assertEqual(report['results'][0]['status'], 'output-limit')

    def test_red_evidence_enters_frozen_panel_before_review_without_verdict_override(self):
        sources, _, _ = self.run_battery()
        operation = {**self.operation, 'id': 'fixture', 'input_sha256': 'input',
                     'tree': str(self.root), 'coordinator': {'test_battery': 1}}
        plan = {**self.plan, 'conflicts': []}
        service = types.SimpleNamespace(request=mock.Mock(return_value={'id': 'review', 'subject_sha256': 'subject'}))
        with mock.patch.object(source_update, '_fresh', return_value=plan), \
             mock.patch.object(source_update, '_materials', return_value={}), \
             mock.patch.object(source_update, '_harness_material', return_value=self.harness), \
             mock.patch.object(battery, 'prepare', return_value=sources) as prepare:
            source_update._review_request(operation, self.directory, service)
        prepare.assert_called_once()
        self.assertEqual(service.request.call_args.kwargs['sources']['battery'], sources['battery'])
        self.assertEqual(operation['state'], 'reviewing')
        self.assertNotIn('verdict', operation)
        from localharness import self_review
        actual = self_review.Service(self.root / 'home', self.core, self.root, types.SimpleNamespace())
        record = actual.request(operation_id='battery-fixture', sources=sources)
        self.assertFalse(record['gaps'])
        self.assertIn('battery/report.md', [row['path'] for row in
            self_review.validate_snapshot(record['snapshot'], record['subject_sha256'])['manifest']])
        self.assertIn('attention cues', actual._brief(record, record['critics'][0]))


if __name__ == '__main__':
    unittest.main()
