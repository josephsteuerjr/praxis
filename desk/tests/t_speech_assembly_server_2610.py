"""Actual small assembly + server launch/client contracts; no model or subprocess.

Only native-runtime import probes and Popen are substituted. Assemblers, their
file inventories, packaged engine resolution, launch argv, IPC worker and client
are production consumers. Native OS/process/model qualification stays separate.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
import wave

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / 'server')]
from installer import python_app_bundle, python_posix_bundle
from localharness import speech_client, speech_protocol as ipc, voice, owner_stop
import serverboot


def put(root, name, content=b'# fixture\n'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def load_worker(path):
    spec = importlib.util.spec_from_file_location('localharness._assembly_worker_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpeechAssemblyServer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.desk = self.root / 'release/desk'
        for name in ('python_app.py', 'python_tray.py', 'python_migrate.py', 'deskapp.py',
                     'deskd/__init__.py', 'localharness/__init__.py', 'app/dist/index.html',
                     'mobile/dist/index.html', 'miniapp/dist/index.html', 'resources/entry',
                     'shell/icons/icon.ico', 'shell/icons/python-app.png',
                     'installer/ЛИЦЕНЗИЯ.md', 'installer/THIRD-PARTY.md',
                     'installer/PYTHON-START-HERE.md'):
            put(self.desk, name)
        for name in ('speech_worker.py', 'speech_protocol.py', 'speech_encode.py'):
            put(self.desk, 'localharness/' + name, (DESK / 'localharness' / name).read_bytes())
        self.canonical = (DESK.parent / 'helene/core/xtts_tts_worker.py').read_bytes()
        put(self.desk.parent, 'helene/core/xtts_tts_worker.py', self.canonical)
        for name in ('LICENSE', 'NOTICE'):
            put(self.desk.parent, name)
        for name in ('serverboot.py', 'supervisor_policy.py', 'emergency_stop.py'):
            put(self.desk, 'server/' + name, (DESK / 'server' / name).read_bytes())
        put(self.desk, 'component-downloads.json', (DESK / 'component-downloads.json').read_bytes())
        self.edition = self.root / 'edition'
        for name in ('agent.py', 'llm.py', 'requirements.txt'):
            put(self.edition, name)
        self.authored = b'raise RuntimeError("authored worker must never load")\n'
        put(self.edition, 'xtts_tts_worker.py', self.authored)

    def windows(self, name='windows'):
        stage = self.root / (name + '-stage')
        put(stage, 'runtime/psutil/__init__.py')
        put(stage, 'runtime/socks.py')
        put(stage, 'runtime/python314._pth', b'.\nimport site\n')
        shutil.copytree(self.edition, stage / 'tree')
        output = self.root / name
        with patch.object(python_app_bundle, 'DESK', self.desk):
            python_app_bundle.assemble(stage, output, version='2.2.0')
        return output

    def assert_engine(self, output):
        path = output / 'app/localharness/xtts_tts_worker.py'
        self.assertEqual(path.read_bytes(), self.canonical)
        module = load_worker(output / 'app/localharness/speech_worker.py')
        engine = module.engine_module()
        self.assertEqual(Path(engine.__file__), path)
        self.assertTrue(callable(engine.load_engine))
        self.assertTrue(callable(engine.synthesize_wav))
        self.assertEqual((output / 'tree/xtts_tts_worker.py').read_bytes(), self.authored)
        inventory = json.loads((output / 'python-package-files.json').read_text())['files']
        self.assertEqual(inventory['app/localharness/xtts_tts_worker.py'],
                         hashlib.sha256(self.canonical).hexdigest())
        # This is the exact old assembly failure; an authored copy is no fallback.
        path.unlink()
        try:
            with self.assertRaisesRegex(RuntimeError, 'Application XTTS engine missing'):
                module.engine_module()
        finally:
            path.write_bytes(self.canonical)
        return module

    def test_windows_actual_assembly_engine_import_and_missing_sibling_control(self):
        output = self.windows()
        self.assert_engine(output)
        # Server image recipe consumes this app closure, not tree's legacy engine.
        recipe = (output / 'server/serverboot.py').read_text(encoding='utf-8')
        self.assertIn('localharness/speech_worker.py', recipe)
        self.assertIn('COPY app/ ./app/', (DESK / 'server/Dockerfile').read_text(encoding='utf-8'))

    def test_both_posix_target_assemblies_ship_exact_release_engine_not_selected_tree(self):
        for target in ('linux-x86_64', 'macos-arm64'):
            with self.subTest(target=target):
                runtime = self.root / (target + '-runtime')
                put(runtime, 'bin/python3')
                output = self.root / target
                with patch.object(python_posix_bundle, 'DESK', self.desk), patch.object(
                        python_posix_bundle.python_platform, 'runtime_target', return_value=target), patch.object(
                        python_posix_bundle.subprocess, 'run') as imports:
                    archive = python_posix_bundle.assemble(runtime, self.edition, None, None,
                        output, version='2.2.0', target=target)
                self.assertTrue(archive.is_file())
                imports.assert_called_once()
                self.assert_engine(output)

    def child(self, output):
        tree = output / 'data'
        tree.mkdir(exist_ok=True)
        model = tree / 'models/xtts'
        for name in ('config.json', 'model.pth', 'vocab.json', 'speakers_xtts.pth'):
            put(model, name)
        python = put(output, 'voice-python')
        cfg = {'code': 'tree', 'voice': {'speak': True, 'tts_speaker': 'A', 'tts_language': 'en'}}
        child = serverboot.xtts_child(output, cfg, tree, {
            'HELENE_XTTS_PYTHON': str(python), 'HELENE_TOKEN': 'fixture-secret',
            'RELAY_API_KEY': 'fixture-relay', 'PATH': os.environ.get('PATH', '')}, user=10001, group=10000)
        self.assertIsNotNone(child)
        return child, tree, cfg

    def test_server_app_launcher_and_client_exchange_pcm_and_refresh_generation_per_spawn(self):
        output = self.windows()
        worker = self.assert_engine(output)
        child, tree, cfg = self.child(output)
        home = tree / 'memory/.state/xtts'
        self.assertEqual(Path(child.argv[2]), output / 'app/localharness/speech_worker.py')
        self.assertEqual(child.user, 10001)
        self.assertEqual(child.group, 10000)
        self.assertNotIn('HELENE_TOKEN', child.env)
        self.assertNotIn('RELAY_API_KEY', child.env)
        self.assertEqual(child.env['HELENE_SUPERVISOR'], 'serverboot')
        self.assertEqual(child.env['HELENE_TREE'], str(tree))
        launches, errors, threads = [], [], []
        canonical = worker.engine_module()
        engine = types.SimpleNamespace(speaker_manager=types.SimpleNamespace(speakers={'A': {}}),
                                       config=types.SimpleNamespace(languages=['en']))

        def synthetic_pcm(engine, request, path, stopped):
            self.assertEqual(request['speaker'], 'A')
            self.assertFalse(stopped())
            with wave.open(str(path), 'wb') as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
                stream.writeframes(b'\x01\x00' * 2400)

        def launch(argv, **kwargs):
            launches.append(list(argv))
            def run():
                try:
                    # Execute the CLI selected by serverboot, substituting only
                    # model loading/PCM and OS signals (this is a thread, not PID).
                    with patch.object(sys, 'argv', argv[2:]), patch.object(worker.signal, 'signal'), patch.object(
                            worker, 'engine_module', return_value=canonical), patch.object(
                            canonical, 'load_engine', return_value=engine), patch.object(
                            canonical, 'synthesize_wav', side_effect=synthetic_pcm):
                        worker.main()
                except BaseException as exc:
                    errors.append(exc)
            thread = threading.Thread(target=run)
            threads.append(thread); thread.start()
            return types.SimpleNamespace(pid=os.getpid(), poll=lambda: None)

        def blocked(selected_tree):
            return (selected_tree / '.owner-stop/stop.json').exists() or (
                selected_tree / 'memory/.control/autonomy-paused.json').exists()

        def command(pid):
            self.assertEqual(pid, os.getpid())
            return types.SimpleNamespace(cmdline=lambda: launches[-1])

        def finish():
            put(home, 'stop', b'')
            for thread in threads:
                thread.join(3)
                self.assertFalse(thread.is_alive())
            self.assertFalse((home / 'ready.json').exists())
            self.assertEqual(errors, [])
        self.addCleanup(finish)
        generations = []
        with patch.object(serverboot.subprocess, 'Popen', side_effect=launch), patch.object(
                ipc, 'blocked', side_effect=blocked), patch('psutil.Process', side_effect=command):
            for attempt in range(2):
                if attempt:
                    put(home, 'stop', b''); threads[-1].join(3)
                    self.assertFalse(threads[-1].is_alive())
                    (home / 'stop').unlink()
                child.spawn()
                audio = speech_client.synthesize('synthetic server fixture', tree, output / 'audio',
                    configuration=lambda: cfg, format='wav', timeout=3)
                audio.check()
                with wave.open(str(audio.path), 'rb') as stream:
                    self.assertEqual(stream.getnframes(), 2400)
                generations.append(audio.generation)
                self.assertTrue(ipc.identity(audio.generation))
                self.assertEqual(voice.worker_live(home, ipc.read(home / 'ready.json')), True)
                audio.path.unlink()
                for name in ('memory/.control/autonomy-paused.json', '.owner-stop/stop.json'):
                    latch = put(tree, name, b'{}')
                    with self.assertRaises(InterruptedError):
                        speech_client.synthesize('refused', tree, output / 'audio',
                            configuration=lambda: cfg, format='wav', timeout=.1)
                    latch.unlink()
                self.assertEqual(list((home / 'output').iterdir()), [])
            finish()
        self.assertNotEqual(*generations)
        self.assertNotIn('--generation', child.argv)
        self.assertTrue(all(argv.count('--generation') == 1 for argv in launches))

    def test_legacy_ready_record_is_not_accepted_by_current_client(self):
        tree = self.root / 'legacy-data'
        home = tree / 'memory/.state/xtts'
        ipc.write(home / 'ready.json', {'pid': os.getpid(), 'ready': True})
        with patch.object(ipc, 'blocked', return_value=False):
            with self.assertRaisesRegex(TimeoutError, 'not ready'):
                speech_client.synthesize('legacy failure control', tree, self.root / 'audio',
                    configuration=lambda: {'voice': {'speak': True}}, format='wav', timeout=.05)
        self.assertFalse((home / 'inbox').exists())

    def test_sanitized_server_environment_preserves_actual_stop_and_pause_readers(self):
        output = self.windows()
        child, tree, cfg = self.child(output)
        # Exercise the actual Linux owner_stop branch without changing host os.name
        # or reading this machine's real owner latch.
        with patch.object(owner_stop, 'os', types.SimpleNamespace(name='posix', environ=child.env)):
            self.assertEqual(owner_stop.seed_file(), tree / '.owner-stop/stop.json')
            self.assertFalse(ipc.blocked(tree))
            for name in ('.owner-stop/stop.json', 'memory/.control/autonomy-paused.json'):
                latch = put(tree, name, b'{}')
                self.assertTrue(ipc.blocked(tree))
                with self.assertRaises(InterruptedError):
                    speech_client.synthesize('refused', tree, output / 'audio',
                        configuration=lambda: cfg, format='wav', timeout=.1)
                latch.unlink()
                self.assertFalse(ipc.blocked(tree))
        self.assertFalse((tree / 'memory/.state/xtts/inbox').exists())

    def test_server_refuses_missing_app_engine_even_when_authored_worker_exists(self):
        output = self.windows()
        child, tree, cfg = self.child(output)
        (output / 'app/localharness/xtts_tts_worker.py').unlink()
        self.assertTrue((output / 'tree/xtts_tts_worker.py').is_file())
        with patch('builtins.print'):
            self.assertIsNone(serverboot.xtts_child(output, cfg, tree, child.env))


if __name__ == '__main__':
    unittest.main(verbosity=2)
