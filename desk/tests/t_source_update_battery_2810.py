"""Native isolated verification consumers; no production battery/models/network.

Python/Node fixture cases really execute. Stubbed native build results below test
ONLY receipt validation, never establish a platform or toolchain PASS.
"""
from pathlib import Path
from collections import Counter
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[2] / 'helene/core')]
from localharness import source_merge as merge, source_update_battery as battery
from localharness import source_verification_bundle as bundle
from localharness.package_notice import read, write

TEST = '''import unittest, os
class T(unittest.TestCase):
 def test_value(self):
  self.assertNotIn("PRIVATE_OWNER_VALUE", os.environ)
  self.assertEqual(1, 1)
'''


class Battery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='verification-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'source'
        for name, text in {
            'praxis/praxis_test.py': '# canonical hermetic bootstrap fixture\n',
            'praxis/test_first.py': TEST,
            'praxis/test_second.py': TEST,
            'helene/core/test_second.py': TEST.replace('test_value', 'test_overlay'),
            'desk/tests/t_one.py': TEST,
            'desk/tests/test_two.py': TEST,
            'desk/app/test/one.mjs': 'import assert from "node:assert/strict"; assert.equal(1,1);',
        }.items():
            self.put(self.repo / name, text)
        for root in bundle.NATIVE:
            prefix = 'praxis/' + root[5:] if root.startswith('live/') else root
            self.put(self.repo / prefix / 'Cargo.toml', '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n')
            self.put(self.repo / prefix / 'Cargo.lock', '# fixture lock\n')
            self.put(self.repo / prefix / 'src/lib.rs', '#[test] fn fixture() {}\n')
        for root in bundle.NPM_PROJECTS:
            self.put(self.repo / root / 'package.json', '{"scripts":{"build":"fixture"}}')
            self.put(self.repo / root / 'package-lock.json', '{"lockfileVersion":3,"packages":{}}')
        self.lock = bundle.executor_lock()
        self.bundle = self.root / 'verification'
        self.binding = bundle.build(self.repo, self.bundle, '2.3.0', {sys.platform: self.lock}, native=bundle.NATIVE)
        self.manifest = read(self.bundle / 'manifest.json')
        self.directory = self.root / 'plan'
        self.core = self.directory / 'result'
        # Real packaged code normally omits tests; the bundle supplies all tests.
        self.put(self.core / 'praxis_test.py', '# canonical hermetic bootstrap fixture\n')
        self.put(self.core / 'logic.py', 'VALUE = 1\n')
        for name in ('body', 'relay', 'hands'):
            shutil.copytree(self.bundle / 'repository/live' / name, self.core / name)
        self.app = self.root / 'app'
        self.put(self.app / 'seam.py', 'VALUE = 1\n')
        self.runtime = self.root / 'runtime'
        self.put(self.runtime / 'runtime.lock', 'pinned-runtime-fixture\n')
        self.operation = {'candidate_sha256': merge.candidate_signature(self.directory),
            'input_sha256': 'input-fixture', 'version': '2.3.0',
            'verification_bundle': self.binding}
        npm_cache = self.root / 'owned-empty-npm-cache'; npm_cache.mkdir()
        self.operation['verification_executor'] = {'npm_config_cache': str(npm_cache)}
        self.patch = mock.patch.object(merge, 'validate_inputs', side_effect=lambda *a: {'files': {'incoming': bundle.inventory(self.core)}})
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.comp = mock.patch.object(battery, 'composition', side_effect=self.composition)
        self.comp.start(); self.addCleanup(self.comp.stop)

    def put(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf8', newline='')

    def composition(self, *args):
        value = {name: {'root': str(root), 'files': bundle.inventory(root)}
                 for name, root in [('core', self.core), ('app', self.app), ('runtime', self.runtime)]}
        value['manifests'] = {'package': 'fixture-package'}
        # Explicit fixture runtime membership, not a product runtime claim.
        value['runtime']['files']['python.exe'] = {'sha256': bundle.digest(sys.executable), 'mode': '100755'}
        value['runtime']['root'] = str(Path(sys.executable).parent)
        value['sha256'] = merge.signature({k: v for k, v in value.items()})
        return value

    def prepare(self):
        with mock.patch.dict(os.environ, {'PRIVATE_OWNER_VALUE': 'not-for-test'}):
            sources = battery.prepare(self.operation, self.directory, {}, lambda: False)
        cache = Path(sources['battery']['root']).parent
        return cache, read(cache / 'report.json')

    def fake_native(self, lane, *args):
        if lane['kind'] in {'unittest', 'unittest-file'}:
            return self.real_lane(lane, *args)
        unit = lane['id'] + ':stubbed-native-unit'
        return {'id': lane['id'], 'kind': lane['kind'], 'status': 'passed', 'exit': 0,
            'observation': {'collected': [unit], 'executed': [unit], 'complete': True,
                'coverage_complete': True, 'tests_run': 1,
                'outcomes': [{'id': unit, 'status': 'passed'}]}}

    def green_fixture(self):
        # A Python suite is real. This synthetic native executor only exercises
        # protocol validation and must never be mistaken for product full QA.
        tools, observed, _ = battery._tools(self.operation, self.manifest)
        self.real_lane = battery._run_lane
        with mock.patch.object(battery, '_tools', return_value=(tools, observed, {})), \
             mock.patch.object(battery, '_run_lane', side_effect=self.fake_native):
            cache, report = self.prepare()
            verified = battery.validate(self.operation, self.directory)
        return cache, report, verified

    def test_full_recipe_is_derived_from_both_core_layers_and_all_desk_suites(self):
        self.assertIn('live/test_first.py', next(r for r in self.manifest['lanes'] if r['id'] == 'core')['sources'])
        self.assertIn('live/test_second.py', next(r for r in self.manifest['lanes'] if r['id'] == 'core')['sources'])
        self.assertIn('test_overlay', (self.bundle / 'repository/live/test_second.py').read_text())
        ids = [r['id'] for r in self.manifest['lanes']]
        self.assertIn('desk/tests/t_one.py', ids)
        self.assertIn('desk/tests/test_two.py', ids)
        self.assertTrue(set(bundle.NATIVE) <= set(ids))
        self.assertNotIn('selected', self.manifest['reuse'])
        bundle.load(self.bundle, self.binding['sha256'], '2.3.0')

    def test_actual_full_python_node_and_missing_native_fail_honestly(self):
        cache, report = self.prepare()
        core = next(r for r in report['results'] if r['id'] == 'core')
        self.assertEqual(core['status'], 'passed', core)
        self.assertEqual(core['observation']['tests_run'], 2)
        self.assertTrue(report['complete'])
        self.assertFalse(report['full_pass'])
        self.assertIn('battery', self.operation)
        native = next(r for r in report['results'] if r['id'] == 'live/body')
        self.assertNotEqual(native['status'], 'platform-skip')
        with self.assertRaises(ValueError):
            battery.validate(self.operation, self.directory)
        self.assertTrue(list(cache.glob('*.output')))

    def test_exact_whole_composition_cache_and_changed_candidate_full_rerun(self):
        cache, report, verified = self.green_fixture()
        self.assertTrue(verified['full_pass'])
        self.assertEqual(next(r for r in report['results'] if r['id'] == 'core')['observation']['tests_run'], 2)
        with mock.patch.object(battery, '_execute', side_effect=AssertionError('duplicate execution')):
            self.assertEqual(self.prepare()[0], cache)
        self.put(self.core / 'test_added.py', TEST)
        self.operation['candidate_sha256'] = merge.candidate_signature(self.directory)
        with self.assertRaisesRegex(ValueError, 'exact current composition'):
            with mock.patch.object(battery, '_tools', return_value=battery._tools(self.operation, self.manifest)[:2] + ({},)):
                battery.validate(self.operation, self.directory)
        next_cache, next_report, _ = self.green_fixture()
        self.assertNotEqual(cache, next_cache)
        self.assertEqual(next(r for r in next_report['results'] if r['id'] == 'core')['observation']['tests_run'], 3)

    def test_app_and_runtime_changes_invalidate_receipt_not_only_core(self):
        cache, _, _ = self.green_fixture()
        for path in (self.app / 'seam.py', self.runtime / 'runtime.lock'):
            original = path.read_bytes()
            path.write_bytes(original + b'# changed\n')
            with self.assertRaises(ValueError):
                battery.validate(self.operation, self.directory)
            path.write_bytes(original)
        self.assertTrue(cache.is_dir())

    def test_mutated_raw_or_frozen_evidence_never_reused(self):
        cache, _, _ = self.green_fixture()
        report = cache / 'evidence/report.md'
        report.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.prepare()

    def test_stop_before_cache_and_owner_cancellation(self):
        self.green_fixture()
        with self.assertRaises(InterruptedError):
            battery.prepare(self.operation, self.directory, {}, lambda: True)
        self.put(self.directory / 'cancel.json', '{}')
        with self.assertRaisesRegex(ValueError, 'Stopped/paused'):
            battery.validate(self.operation, self.directory)

    def test_tampered_bundle_or_lane_list_rejected_before_execution(self):
        manifest = read(self.bundle / 'manifest.json')
        manifest['lanes'].pop(0)
        write(self.bundle / 'manifest.json', manifest)
        self.operation['verification_bundle']['sha256'] = bundle.digest(self.bundle / 'manifest.json')
        with self.assertRaisesRegex(ValueError, 'omits a full discovery lane'):
            self.prepare()

    def test_zero_collected_not_promoted_to_full_by_complete_flag(self):
        row = {'id': 'fixture', 'kind': 'unittest', 'status': 'passed', 'exit': 0,
               'observation': {'complete': True, 'coverage_complete': True, 'collected': [], 'executed': []}}
        with self.assertRaisesRegex(ValueError, 'Nonzero exact'):
            battery._check_results({'lanes': [{'id': 'fixture', 'kind': 'unittest'}], 'results': [row]}, {})

    def test_exact_platform_skip_allowlist_not_missing_dependency_skip(self):
        lane = {'id': 'fixture', 'kind': 'unittest'}
        case = {'id': 'test.T.test_native', 'status': 'skipped', 'reason': 'Windows only'}
        row = {'id': 'fixture', 'status': 'passed', 'exit': 0, 'observation': {
            'complete': True, 'coverage_complete': True, 'collected': [case['id']],
            'executed': [case['id']], 'tests_run': 1, 'outcomes': [case]}}
        report = {'lanes': [lane], 'results': [row]}
        with self.assertRaisesRegex(ValueError, 'Undeclared test skip'):
            battery._check_results(report, {})
        policy = {'allowed_test_skips': {sys.platform: {'fixture::' + case['id']: 'Windows only'}}}
        battery._check_results(report, policy)
        case['reason'] = 'Dependency not installed'
        with self.assertRaisesRegex(ValueError, 'Undeclared test skip'):
            battery._check_results(report, policy)

    def test_failed_case_and_partial_execution_refuse_full(self):
        _, report, _ = self.green_fixture()
        current = copy.deepcopy(report)
        next(r for r in current['results'] if r['id'] == 'core')['observation']['executed'].pop()
        with self.assertRaisesRegex(ValueError, 'collected/executed'):
            battery._check_results(current, self.manifest)
        current = copy.deepcopy(report)
        next(r for r in current['results'] if r['id'] == 'core')['observation']['outcomes'][0]['status'] = 'failed'
        with self.assertRaisesRegex(ValueError, 'not passed'):
            battery._check_results(current, self.manifest)

    def test_actual_test_failure_remains_readable_and_no_gate_pass(self):
        self.put(self.core / 'test_changed.py', TEST.replace('assertEqual(1, 1)', 'assertEqual(1, 2)'))
        self.operation['candidate_sha256'] = merge.candidate_signature(self.directory)
        _, report = self.prepare()
        self.assertEqual(next(r for r in report['results'] if r['id'] == 'core')['status'], 'failed')
        self.assertFalse(report['full_pass'])
        self.assertTrue(any(row['status'] == 'failed' for row in next(r for r in report['results'] if r['id'] == 'core')['observation']['outcomes']))

    def test_unconfirmed_custody_keeps_work_but_never_complete(self):
        captured = []
        def broken(lane, repository, *args):
            captured.append(repository.parent)
            raise battery.CustodyUnconfirmed('fixture custody unknown')
        with mock.patch.object(battery, '_run_lane', side_effect=broken):
            with self.assertRaises(battery.CustodyUnconfirmed):
                self.prepare()
        self.assertTrue(captured[0].is_dir())
        self.addCleanup(shutil.rmtree, captured[0])
        self.assertFalse(list((self.directory / 'test-battery').glob('*/receipt.json')))
        self.assertTrue(list((self.directory / 'test-battery').glob('*/custody-unconfirmed.json')))

    def test_actual_stop_terminates_synthetic_child_without_complete_receipt(self):
        self.put(self.core / 'test_sleep.py', 'import time\ntime.sleep(60)\n')
        self.operation['candidate_sha256'] = merge.candidate_signature(self.directory)
        calls = 0
        def stopped():
            nonlocal calls
            calls += 1
            return calls > 10
        with self.assertRaises(InterruptedError):
            battery.prepare(self.operation, self.directory, {}, stopped)
        self.assertFalse(list((self.directory / 'test-battery').glob('*/receipt.json')))

    def test_output_limit_has_no_passing_coverage(self):
        self.put(self.core / 'test_loud.py', 'print("large-output" * 10000)\n')
        self.operation['candidate_sha256'] = merge.candidate_signature(self.directory)
        with mock.patch.object(battery, 'MAX_OUTPUT', 1000):
            _, report = self.prepare()
        self.assertEqual(next(r for r in report['results'] if r['id'] == 'core')['status'], 'output-limit')
        self.assertFalse(report['full_pass'])


    def test_explicit_script_contract_runs_supported_cli_not_zero_unittest_cases(self):
        repository = self.root / 'script-repository'
        script = repository / 'desk/tests/t_cli.py'
        self.put(script, 'import argparse\np=argparse.ArgumentParser()\np.add_argument("--value",required=True)\na=p.parse_args()\nassert a.value == "checked"\nprint("SCRIPT_CONSUMER_CHECKED")\n')
        files = dict(self.manifest['files'])
        files['desk/tests/t_cli.py'] = {'sha256': bundle.digest(script)}
        scripts = {'desk/tests/t_cli.py': {'kind': 'script', 'argv': ['--value', 'checked']}}
        lane = next(r for r in bundle.recipe(files, scripts=scripts) if r['id'] == 'desk/tests/t_cli.py')
        cache = self.root / 'script-cache'; cache.mkdir()
        env = battery._environment(self.root / 'script-home', self.core, repository / 'desk')
        row = battery._run_lane(lane, repository, None, {'python': sys.executable}, env, cache, lambda: False)
        self.assertEqual(row['status'], 'passed')
        self.assertEqual(row['observation']['checks_run'], 1)
        self.assertNotIn('tests_run', row['observation'])
        battery._check_results({'lanes': [lane], 'results': [row]}, {})
        self.assertIn('SCRIPT_CONSUMER_CHECKED', next(cache.glob('*.output')).read_text())
        with self.assertRaisesRegex(ValueError, 'coverage identity'):
            bundle.recipe(files, scripts={'desk/tests/t_cli.py': {'kind': 'helper', 'id': 'not-the-full-suite', 'reason': 'bad'}})

    def test_pytest_lane_executes_with_python_when_cargo_is_unavailable(self):
        self.put(self.repo / 'desk/tests/test_functions.py',
                 'def test_actual_fixture(tmp_path):\n assert tmp_path.is_dir()\n')
        self.bundle = self.root / 'pytest-verification'
        self.binding = bundle.build(self.repo, self.bundle, '2.3.0', {sys.platform: self.lock})
        self.manifest = read(self.bundle / 'manifest.json')
        self.operation['verification_bundle'] = self.binding
        tools, observed, errors = battery._tools(self.operation, self.manifest)
        errors = {**errors, 'cargo': 'No Cargo supplied for this Python-only lane',
                  'node': 'Node deliberately unavailable in this focused Python fixture'}
        with mock.patch.object(battery, '_tools', return_value=(tools, observed, errors)):
            _, report = self.prepare()
        row = next(r for r in report['results'] if r['id'] == 'desk/tests/test_functions.py')
        self.assertEqual(row['status'], 'passed', row)
        self.assertEqual(len(row['observation']['collected']), 1)
        self.assertEqual(row['observation']['collected'], row['observation']['executed'])
        self.assertFalse(report['full_pass'])  # The unrelated missing Node is not waived.

    def test_current_python_product_has_no_obsolete_rust_prerequisite(self):
        lanes = bundle.recipe(self.manifest['files'])
        ids = {r['id'] for r in lanes}
        self.assertFalse(any(n in ids for n in ('desk/shell', 'desk/svc', 'desk/setup')))
        self.assertFalse(any(r['kind'] == 'cargo' for r in lanes))
        self.assertFalse(any(r['kind'] == 'npm-build' for r in lanes))
        before = bundle.inventory(self.core)
        self.put(self.core / 'body/src/lib.rs', '#[test] fn changed_native() {}\n')
        value = self.composition()
        with mock.patch.object(merge, 'validate_inputs', return_value={'files': {'incoming': before}}):
            native, rebuild = battery._obligations(self.directory, value, {'native_lanes': [], 'build_lanes': []})
        self.assertEqual(native, ['live/body'])
        self.assertEqual(rebuild, [])

    def test_deleted_composed_app_file_does_not_execute_old_bundle_copy(self):
        repository = self.root / 'isolated-repository'
        bundle.copy_files(self.bundle / 'repository', self.manifest['files'], repository)
        self.put(repository / 'desk/deleted.py', 'raise AssertionError("old app leaked")\n')
        base = {'deleted.py': {'sha256': bundle.digest(repository / 'desk/deleted.py')}}
        value = self.composition()
        battery._copy_composition(value, repository, self.manifest, base)
        self.assertFalse((repository / 'desk/deleted.py').exists())
        self.assertNotIn('desk/deleted.py', battery._verification_files(value, self.manifest, base))

    def test_pinned_offline_wheel_provisions_private_target_and_is_rechecked(self):
        wheels = self.root / 'wheels'
        wheels.mkdir()
        wheel = wheels / 'tiny_verification_fixture-1.0-py3-none-any.whl'
        dist = 'tiny_verification_fixture-1.0.dist-info/'
        with zipfile.ZipFile(wheel, 'w') as z:
            z.writestr('tiny_verification_fixture.py', 'VALUE = "offline fixture"\n')
            z.writestr(dist + 'METADATA', 'Metadata-Version: 2.1\nName: tiny-verification-fixture\nVersion: 1.0\n')
            z.writestr(dist + 'WHEEL', 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            z.writestr(dist + 'RECORD', '')
        package = {'version': '1.0', 'filename': wheel.name, 'sha256': bundle.digest(wheel)}
        self.put(wheels / 'wheels.json', json.dumps({'packages': {'tiny-verification-fixture': package}}))
        lock = copy.deepcopy(self.lock)
        lock['python_packages']['tiny-verification-fixture'] = '1.0'
        target_bundle = self.root / 'offline-bundle'
        binding = bundle.build(self.repo, target_bundle, '2.3.0', {sys.platform: lock}, wheelhouses={sys.platform: wheels})
        target = self.root / 'offline-site'
        executor = bundle.provision(target_bundle, binding['sha256'], '2.3.0', sys.executable, target)
        self.assertTrue((target / 'tiny_verification_fixture.py').is_file())
        operation = {**self.operation, 'verification_bundle': binding, 'verification_executor': executor}
        manifest = read(target_bundle / 'manifest.json')
        self.assertNotIn('python', battery._tools(operation, manifest)[2])
        (target / 'tiny_verification_fixture.py').write_text('CHANGED = True\n')
        self.assertIn('python', battery._tools(operation, manifest)[2])


if __name__ == '__main__':
    unittest.main()
