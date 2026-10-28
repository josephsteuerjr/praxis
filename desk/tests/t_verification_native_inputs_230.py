"""Explicit owned native executor inputs; fixture binaries are never executed."""
from pathlib import Path
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[2] / 'helene/core')]
from localharness import source_update_battery as battery
from localharness import source_verification_bundle as bundle
import t_source_update_battery_2810 as protocol


class NativeInputs(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='verification-native-input-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.program = self.root / 'own-program'
        self.program.mkdir()
        suffix = '.exe' if os.name == 'nt' else ''
        self.body = self.program / ('helene-body' + suffix)
        self.bridge = self.program / ('helene-bridge' + suffix)
        self.body.write_bytes(b'OWN synthetic body; not executable')
        self.bridge.write_bytes(b'OWN synthetic bridge; not executable')
        self.executor = {'native_env': {'HELENE_BODY_DIR': str(self.program)},
                         'native_files': {str(p): bundle.digest(p) for p in (self.body, self.bridge)}}

    def environment(self, executor=None):
        return battery._environment(self.root / 'home', self.root / 'core', self.root / 'repository/desk',
                                    {'verification_executor': executor or {}})

    def run_environment_probe(self, env, native_inputs=None):
        repository = self.root / 'repository'
        source = repository / 'desk/tests/t_probe.py'
        source.parent.mkdir(parents=True, exist_ok=True)
        keys = ['HELENE_BODY_DIR', 'HELENE_BODY_BIN', 'DISPLAY', 'DBUS_SESSION_BUS_ADDRESS',
                'XDG_RUNTIME_DIR', 'PRAXIS_HANDS', 'OPENAI_API_KEY', 'HTTPS_PROXY', 'HELENE_HOST_BINARY']
        source.write_text('import os,json\nprint(json.dumps({k:os.environ[k] for k in ' + repr(keys) +
                          ' if k in os.environ},sort_keys=True))\n', encoding='utf8')
        cache = self.root / 'probe-cache'; cache.mkdir(exist_ok=True)
        lane = {'id': 'owned-env-probe', 'kind': 'script', 'root': 'desk',
                'source': 'desk/tests/t_probe.py', 'argv': []}
        args = [lane, repository, None, {'python': sys.executable}, env, cache, lambda: False]
        if native_inputs is not None:
            args.append(native_inputs)
        row = battery._run_lane(*args)
        self.assertEqual(row['status'], 'passed', row)
        return json.loads(next(cache.glob('*.output')).read_text(encoding='utf8'))

    def test_ambient_native_paths_display_and_credentials_never_reach_actual_child(self):
        ambient = {key: 'UNRELATED production input' for key in (
            'HELENE_BODY_DIR', 'HELENE_BODY_BIN', 'DISPLAY', 'DBUS_SESSION_BUS_ADDRESS',
            'XDG_RUNTIME_DIR', 'PRAXIS_HANDS', 'OPENAI_API_KEY', 'HTTPS_PROXY', 'HELENE_HOST_BINARY')}
        with mock.patch.dict(os.environ, ambient):
            self.assertEqual(self.run_environment_probe(self.environment()), {})

    def test_declared_pinned_body_pair_reaches_actual_child_not_ambient(self):
        with mock.patch.dict(os.environ, {'HELENE_BODY_DIR': 'UNRELATED production input',
                                         'OPENAI_API_KEY': 'fixture-secret'}):
            env = self.environment(self.executor)
            observed = self.run_environment_probe(env)
        self.assertEqual(observed, {'HELENE_BODY_DIR': str(self.program)})

    def test_unknown_env_keys_and_bad_values_are_refused_not_ignored(self):
        for env in ({'OPENAI_API_KEY': 'secret'}, {'PRAXIS_HANDS': 'off'},
                    {'HELENE_HOST_BIN': 'invented'}, {'DISPLAY': ''}, {'DISPLAY': 42},
                    {'DISPLAY': 'bad\x00value'}, [], None):
            with self.subTest(env=env), self.assertRaises(ValueError):
                self.environment({'native_env': env})

    def test_missing_wrong_or_unpinned_native_files_refuse_environment(self):
        cases = []
        missing_pin = copy.deepcopy(self.executor); missing_pin['native_files'].pop(str(self.bridge)); cases.append(missing_pin)
        wrong_hash = copy.deepcopy(self.executor); wrong_hash['native_files'][str(self.body)] = '0' * 64; cases.append(wrong_hash)
        bad_hash = copy.deepcopy(self.executor); bad_hash['native_files'][str(self.body)] = 'not-sha'; cases.append(bad_hash)
        orphan = copy.deepcopy(self.executor); orphan.pop('native_env'); cases.append(orphan)
        extra = self.root / 'unrelated-file'; extra.write_bytes(b'unrelated')
        extra_pin = copy.deepcopy(self.executor); extra_pin['native_files'][str(extra)] = bundle.digest(extra); cases.append(extra_pin)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.environment(value)
        self.bridge.unlink()
        with self.assertRaises(ValueError):
            self.environment(self.executor)

    def test_actual_selected_binary_and_session_values_are_bound_in_executor_identity(self):
        self.executor['native_env'].update(HELENE_BODY_BIN=str(self.body), DISPLAY=':97',
                                          DBUS_SESSION_BUS_ADDRESS='unix:path=/owned/session',
                                          XDG_RUNTIME_DIR=str(self.root))
        operation = {'verification_executor': self.executor}
        manifest = {'executor_locks': {sys.platform: bundle.executor_lock()}}
        first = battery._tools(operation, manifest)[1]
        self.assertEqual(first['native_inputs']['native_files'], self.executor['native_files'])
        self.assertEqual(self.run_environment_probe(self.environment(self.executor)), self.executor['native_env'])
        self.executor['native_env']['DISPLAY'] = ':98'
        self.assertNotEqual(first, battery._tools(operation, manifest)[1])
        self.executor['native_env']['HELENE_BODY_BIN'] = str(self.bridge)
        with self.assertRaises(ValueError):
            self.environment(self.executor)

    def test_changed_file_between_environment_and_launch_refuses_before_child_starts(self):
        native = battery._native_inputs(self.executor)
        env = self.environment(self.executor)
        self.body.write_bytes(b'changed after environment')
        with self.assertRaisesRegex(ValueError, 'native.*(changed|match)|Native.*(changed|match)'):
            self.run_environment_probe(env, native)
        self.assertFalse(list((self.root / 'probe-cache').glob('*.output')))

    def test_missing_file_between_environment_and_launch_refuses_before_child_starts(self):
        native = battery._native_inputs(self.executor)
        env = self.environment(self.executor)
        self.bridge.unlink()
        with self.assertRaises(ValueError):
            self.run_environment_probe(env, native)
        self.assertFalse(list((self.root / 'probe-cache').glob('*.output')))

    def test_prepare_and_validate_recheck_files_outside_composition_runtime(self):
        fixture = protocol.Battery('test_exact_whole_composition_cache_and_changed_candidate_full_rerun')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.operation['verification_executor'].update(self.executor)
        _, report, verified = fixture.green_fixture()
        self.assertTrue(verified['full_pass'])  # Stubbed native protocol only, not real body execution.
        self.assertEqual(report['binding']['executor']['native_inputs']['native_env'], self.executor['native_env'])
        self.bridge.write_bytes(b'changed outside runtime composition')
        for action in (lambda: fixture.prepare(), lambda: battery.validate(fixture.operation, fixture.directory)):
            with self.assertRaises(ValueError):
                action()


if __name__ == '__main__':
    unittest.main(verbosity=2)
