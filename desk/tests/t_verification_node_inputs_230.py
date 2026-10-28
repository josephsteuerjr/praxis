"""Source-bound offline npm prerequisites; tiny real dependency, no registry calls.

The remote import fixture is causal consumer evidence, not the product's native
build or full battery. Package/lock inputs are synthetic and never edit UI locks.
"""
from pathlib import Path
import base64
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[2] / 'helene/core')]
from localharness import source_update_battery as battery
from localharness import source_verification_bundle as bundle

PROJECTS = ('desk/app', 'desk/mobile', 'desk/miniapp', 'desk/remote')
BASE = {'live/praxis_test.py': {}, 'live/test_core.py': {},
        'desk/tests/t_actual.py': {}, 'desk/app/test/import.test.mjs': {}}


class NodeInputs(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='verification-node-input-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.repository = self.work / 'repository'
        self.cache = self.work / 'npm-cache'; self.cache.mkdir()
        self.logs = self.work / 'evidence'; self.logs.mkdir()
        self.tools = {name: shutil.which(name) for name in ('node', 'npm')}

    def put(self, relative, text):
        path = self.repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf8', newline='')
        return path

    def files(self):
        return {**BASE, **bundle.inventory(self.repository)}

    def projects(self):
        for root in PROJECTS:
            package = {'name': root.replace('/', '-'), 'version': '1.0.0', 'private': True,
                       'type': 'module', 'scripts': {'build': 'node -e "console.log(1)"'}}
            self.put(root + '/package.json', json.dumps(package))
            self.put(root + '/package-lock.json', json.dumps({
                'name': package['name'], 'version': '1.0.0', 'lockfileVersion': 3,
                'packages': {'': {'name': package['name'], 'version': '1.0.0'}}}))

    def environment(self, cache=True):
        operation = {'verification_executor': {'npm_config_cache': str(self.cache)}} if cache else {}
        return battery._environment(self.work / 'home', self.repository / 'live', self.repository / 'desk', operation)

    def run_lane(self, lane, env=None):
        return battery._run_lane(lane, self.repository, None, self.tools, env or self.environment(),
                                 self.logs, lambda: False)

    def test_actual_four_locked_projects_precede_every_suite_and_same_three_builds(self):
        self.projects()
        lanes = bundle.recipe(self.files(), rebuild=PROJECTS[:3])
        preps = [row for row in lanes if row['kind'] == 'npm-prepare']
        self.assertEqual([row['root'] for row in preps], list(PROJECTS))
        self.assertEqual(lanes[:4], preps)
        self.assertEqual([row['root'] for row in lanes if row['kind'] == 'npm-build'], list(PROJECTS[:3]))
        for lane in preps:
            expected = {name: self.files()[name]['sha256'] for name in lane['sources']}
            self.assertEqual(lane['input_sha256'], expected)
            self.assertEqual(set(expected), {lane['root'] + '/package.json', lane['root'] + '/package-lock.json'})
        self.assertIn('node', battery._required_tools(preps))
        self.assertIn('npm', battery._required_tools(preps))
        self.assertEqual(battery._lane_tool(preps[0]), 'npm')

    def test_missing_project_lock_refuses_instead_of_borrowing_node_modules(self):
        self.projects()
        (self.repository / 'desk/remote/package-lock.json').unlink()
        with self.assertRaisesRegex(ValueError, 'locked npm project|package-lock'):
            bundle.recipe(self.files())

    def test_current_product_import_and_all_four_locks_are_the_declared_inputs(self):
        source = Path(__file__).resolve().parents[2]
        actual = dict(BASE)
        for root in PROJECTS:
            for filename in ('package.json', 'package-lock.json'):
                path = source / root / filename
                self.assertTrue(path.is_file(), str(path))
                actual[root + '/' + filename] = {'sha256': bundle.digest(path)}
        self.assertIn('../../remote/src/settings-server',
                      (source / 'desk/app/src/settings-agent.ts').read_text(encoding='utf8'))
        self.assertIn('qrcode', json.loads((source / 'desk/remote/package.json').read_text())['dependencies'])
        self.assertEqual(len([r for r in bundle.recipe(actual) if r['kind'] == 'npm-prepare']), 4)

    def test_cache_selection_is_explicit_and_ambient_user_config_is_not_inherited(self):
        with mock.patch.dict(os.environ, {'npm_config_cache': 'UNRELATED',
                                         'NPM_CONFIG_USERCONFIG': 'PRIVATE', 'NPM_TOKEN': 'PRIVATE'}):
            env = self.environment()
            isolated = self.environment(cache=False)
        self.assertEqual(env['npm_config_cache'], str(self.cache))
        self.assertNotEqual(isolated['npm_config_cache'], 'UNRELATED')
        self.assertNotIn('NPM_TOKEN', env)
        self.assertNotIn('NPM_CONFIG_USERCONFIG', env)
        self.assertNotEqual(env['npm_config_userconfig'], 'PRIVATE')
        manifest = {'executor_locks': {sys.platform: bundle.executor_lock()}}
        missing = battery._tools({}, manifest)
        self.assertIn('npm', missing[2])
        selected = battery._tools({'verification_executor': {'npm_config_cache': str(self.cache)}}, manifest)
        self.assertNotIn('npm', selected[2])
        self.assertEqual(selected[1]['npm_cache'], str(self.cache))
        other = self.work / 'other-cache'; other.mkdir()
        changed = battery._tools({'verification_executor': {'npm_config_cache': str(other)}}, manifest)
        self.assertNotEqual(selected[1], changed[1])

    def test_build_is_one_command_after_preparation_not_second_ci(self):
        self.projects()
        lane = next(r for r in bundle.recipe(self.files(), rebuild=['desk/app']) if r['kind'] == 'npm-build')
        def execute(command, cwd, env, output, stopped, native_inputs):
            output.write_text('synthetic command protocol only')
            return {'status': 'passed', 'exit': 0}
        with mock.patch.object(battery, '_execute', side_effect=execute):
            row = self.run_lane(lane)
        self.assertEqual(len(row['commands']), 1)
        self.assertEqual(row['commands'][0][1:3], ['run', 'build'])
        self.assertEqual(row['observation']['checks_run'], 1)
        self.assertNotIn('tests_run', row['observation'])
        battery._check_results({'lanes': [lane], 'results': [row]}, {})

    def test_changed_lock_refuses_before_any_process(self):
        self.projects()
        lane = next(r for r in bundle.recipe(self.files()) if r['kind'] == 'npm-prepare')
        self.put(lane['root'] + '/package-lock.json', '{}')
        with mock.patch.object(battery, '_execute', side_effect=AssertionError('must not launch')):
            with self.assertRaisesRegex(ValueError, 'npm.*changed|NPM.*changed'):
                self.run_lane(lane)

    def dependency(self, fill_cache):
        self.projects()
        payload = {'package/package.json': json.dumps({'name': 'owned-node-fixture', 'version': '1.0.0',
                                                     'type': 'module', 'exports': './index.js'}).encode(),
                   'package/index.js': b'export const value = "REMOTE_DEPENDENCY_READY";\n'}
        tar = self.work / 'owned-node-fixture.tgz'
        with tarfile.open(tar, 'w:gz') as archive:
            for name, data in payload.items():
                info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
        integrity = 'sha512-' + base64.b64encode(hashlib.sha512(tar.read_bytes()).digest()).decode()
        if fill_cache:
            done = subprocess.run([self.tools['npm'], 'cache', 'add', str(tar), '--offline', '--ignore-scripts',
                                   '--cache', str(self.cache)], cwd=self.work, env=self.environment(),
                                  capture_output=True, text=True, encoding='utf8', timeout=30,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        tar.unlink()  # ci can now use only the selected content-addressed cache.
        package = json.loads((self.repository / 'desk/remote/package.json').read_text())
        package['dependencies'] = {'owned-node-fixture': '1.0.0'}
        self.put('desk/remote/package.json', json.dumps(package))
        self.put('desk/remote/package-lock.json', json.dumps({
            'name': package['name'], 'version': '1.0.0', 'lockfileVersion': 3,
            'packages': {'': {'name': package['name'], 'version': '1.0.0', 'dependencies': package['dependencies']},
                         'node_modules/owned-node-fixture': {'version': '1.0.0',
                            'resolved': 'https://fixture.invalid/owned-node-fixture-1.0.0.tgz', 'integrity': integrity}}}))
        self.put('desk/remote/src/settings-server.js', 'export {value} from "owned-node-fixture";\n')
        self.put('desk/app/test/import.test.mjs',
                 'import {value} from "../../remote/src/settings-server.js";\n'
                 'import assert from "node:assert/strict";\n'
                 'assert.equal(value,"REMOTE_DEPENDENCY_READY");\n')

    def test_real_offline_remote_import_old_order_red_new_order_pass(self):
        self.assertTrue(all(self.tools.values()), 'The explicit Node/npm executor is required for this consumer')
        self.dependency(fill_cache=True)
        lanes = bundle.recipe(self.files())
        node = next(r for r in lanes if r['kind'] == 'node')
        old = self.run_lane(node)
        self.assertEqual(old['status'], 'failed')
        self.assertIn('ERR_MODULE_NOT_FOUND', '\n'.join((self.logs / n).read_text() for n in old['logs']))
        before = {n: bundle.digest(self.repository / n) for n in self.files() if n.endswith(('package.json', 'package-lock.json'))}
        for lane in (r for r in lanes if r['kind'] == 'npm-prepare'):
            row = self.run_lane(lane)
            self.assertEqual(row['status'], 'passed', row)
            self.assertEqual(row['observation']['checks_run'], 1)
            self.assertNotIn('tests_run', row['observation'])
            detail = row['command_results'][0]
            self.assertEqual(detail['cwd'], str(self.repository / lane['root']))
            self.assertEqual(detail['input_sha256'], lane['input_sha256'])
            self.assertEqual(detail['npm_cache'], str(self.cache))
            self.assertEqual(set(detail['tools']), {'node', 'npm'})
            self.assertIn('--offline', detail['argv'])
            battery._check_results({'lanes': [lane], 'results': [row]}, {})
        self.assertEqual(before, {n: bundle.digest(self.repository / n) for n in before})
        new = self.run_lane(node)
        self.assertEqual(new['status'], 'passed', new)
        self.assertGreater(new['observation']['tests_run'], 0)
        battery._check_results({'lanes': [node], 'results': [new]}, {})

    def test_unknown_cache_is_failed_command_evidence_not_fake_zero_test_pass(self):
        self.dependency(fill_cache=False)
        lane = next(r for r in bundle.recipe(self.files()) if r.get('root') == 'desk/remote')
        row = self.run_lane(lane)
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['observation']['checks_run'], 1)
        self.assertNotIn('tests_run', row['observation'])
        text = '\n'.join((self.logs / n).read_text() for n in row['logs'])
        self.assertIn('ENOTCACHED', text)
        self.assertEqual(row['command_results'][0]['status'], 'failed')
        with self.assertRaisesRegex(ValueError, 'did not pass'):
            battery._check_results({'lanes': [lane], 'results': [row]}, {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
