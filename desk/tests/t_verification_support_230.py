"""Actual composition preserves only frozen public test support, not live .env."""
from pathlib import Path
import hashlib
import tempfile
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import source_update_battery as battery
from localharness import source_verification_bundle as bundle


class VerificationSupport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='verification-support-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repository = self.root / 'repository'
        self.core = self.root / 'M'
        self.app = self.root / 'app'
        for directory in (self.core, self.app, self.repository / 'live', self.repository / 'praxis', self.repository / 'helene/core', self.repository / 'desk'):
            directory.mkdir(parents=True, exist_ok=True)
        (self.core / 'module.py').write_bytes(b'current=1\n')
        (self.app / 'feature.py').write_bytes(b'app=1\n')
        self.support = {'.env.example': b'# PRAXIS_SNAPSHOT_WRITE=v2; not .deploy.env\n',
                        'docker-compose.deploy.yml': b'praxis:\n  env_file: .env\nmailbot:\n'}
        files = {}
        for name, data in {**self.support, '.env': b'never-copy=private', '.arbitrary-example': b'not-allowed'}.items():
            for tree in ('live', 'praxis', 'helene/core'):
                path = self.repository / tree / name
                path.write_bytes(data)
                files[tree + '/' + name] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'mode': '100644'}
        self.manifest = {'files': files}
        self.value = {'core': {'root': str(self.core), 'files': bundle.inventory(self.core)},
                      'app': {'root': str(self.app), 'files': bundle.inventory(self.app)}}

    def test_public_support_survives_actual_M_replacement_and_private_env_does_not(self):
        core, _ = battery._copy_composition(self.value, self.repository, self.manifest)
        for name, data in self.support.items():
            self.assertEqual((core / name).read_bytes(), data)
            for tree in ('praxis', 'helene/core'):
                self.assertEqual((self.repository / tree / name).read_bytes(), data)
        self.assertFalse((core / '.env').exists())
        self.assertFalse((core / '.arbitrary-example').exists())
        self.assertEqual(set(self.value['core']['files']), {'module.py'}, 'support is verification-only, not installable M')

    def test_changed_frozen_support_is_not_blessed_or_replaced_from_owner_files(self):
        (self.repository / 'helene/core/.env.example').write_bytes(b'tampered public support')
        with self.assertRaisesRegex(ValueError, 'Required full test source unavailable'):
            battery._copy_composition(self.value, self.repository, self.manifest)


class DeclaredBuildOutputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='private-test-build-')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        for name in ('desk/mobile/dist/sw.js', 'desk/mobile/src/main.ts',
                     'desk/mobile/package.json', 'desk/mobile/package-lock.json', 'live/agent.py'):
            self.put(name, b'original')
        self.before = bundle.inventory(self.repo)
        self.lanes = [{'id': 'desk/mobile/build', 'root': 'desk/mobile', 'kind': 'npm-build'}]

    def put(self, name, data):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_declared_dist_is_recorded_before_after_not_applied_as_M(self):
        self.put('desk/mobile/dist/sw.js', b'generated new worker')
        self.put('desk/mobile/dist/asset-new.js', b'generated new asset')
        changes, observations = battery._test_copy_changes(self.repo, self.before, self.lanes)
        self.assertEqual(changes, [])
        self.assertEqual(len(observations), 1)
        output = observations[0]
        self.assertEqual(output['root'], 'desk/mobile/dist')
        self.assertEqual(output['before']['desk/mobile/dist/sw.js'], self.before['desk/mobile/dist/sw.js'])
        self.assertEqual(output['after']['desk/mobile/dist/sw.js']['sha256'], hashlib.sha256(b'generated new worker').hexdigest())
        self.assertEqual(output['changed_paths'], ['desk/mobile/dist/asset-new.js', 'desk/mobile/dist/sw.js'])
        self.assertEqual((self.repo / 'live/agent.py').read_bytes(), b'original')

    def test_authored_TS_package_and_lock_changes_remain_rejected(self):
        for name in ('desk/mobile/src/main.ts', 'desk/mobile/package.json', 'desk/mobile/package-lock.json'):
            with self.subTest(name=name):
                self.put(name, b'mutated authored input')
                changes, _ = battery._test_copy_changes(self.repo, self.before, self.lanes)
                self.assertIn(name, changes)
                self.put(name, b'original')

    def test_live_source_and_new_untracked_source_remain_rejected(self):
        self.put('live/agent.py', b'mutated runtime source')
        self.put('live/injected.py', b'new unauthorized source')
        changes, _ = battery._test_copy_changes(self.repo, self.before, self.lanes)
        self.assertEqual(changes, ['live/agent.py', 'live/injected.py'])

    def test_undeclared_dist_root_is_not_a_generic_generated_exception(self):
        self.put('desk/remote/dist/new.js', b'not a declared build output')
        self.put('desk/mobile/dist/sw.js', b'changed')
        for lanes in ([], [{'id': 'desk/mobile/test', 'root': 'desk/mobile', 'kind': 'npm-build'}],
                      [{'id': 'desk/mobile/build', 'root': 'desk/mobile', 'kind': 'node'}]):
            with self.subTest(lanes=lanes):
                changes, observations = battery._test_copy_changes(self.repo, self.before, lanes)
                self.assertIn('desk/mobile/dist/sw.js', changes)
                self.assertIn('desk/remote/dist/new.js', changes)
                self.assertEqual(observations, [])


if __name__ == '__main__':
    unittest.main()
