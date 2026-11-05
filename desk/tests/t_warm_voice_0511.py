"""Warm recordings avoid rescanning/reloading and idle expiry cannot interrupt use."""
from pathlib import Path
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from localharness import recognition, voice, whisper_residency


class Timer:
    made = []
    def __init__(self, delay, callback):
        self.delay, self.callback, self.cancelled = delay, callback, False
        self.made.append(self)
    def start(self): pass
    def cancel(self): self.cancelled = True


class WarmVoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = self.root / 'helene.json'
        self.cfg.write_text('{}', encoding='utf8')
        Timer.made.clear()

    def test_all_available_cores_and_retention_even_with_old_saved_thread_limit(self):
        for available in (1, 4, 32):
            with self.subTest(available=available), patch.object(os, 'process_cpu_count', return_value=available, create=True):
                env = voice._stt_env(self.root, {'voice': {'threads': 2, 'keep_loaded': False}},
                    {'ready': True, 'installed': {'path': 'local'}})
            self.assertEqual(env['PRAXIS_STT_CPU_THREADS'], str(available))
            self.assertEqual(env['PRAXIS_STT_KEEP_LOADED'], '1')

    def test_idle_is_thirty_minutes_after_last_use_and_never_during_inference(self):
        model = types.SimpleNamespace(clear_cache=Mock())
        class Model:
            clear_cache = model.clear_cache
        obj = Model()
        state = whisper_residency.Residency(obj, timer=Timer)
        with state.use(): pass
        old = Timer.made[-1]
        self.assertEqual(old.delay, 1800)
        with state.use():
            old.callback()  # A cancelled callback that was already dispatched.
            model.clear_cache.assert_not_called()
        self.assertTrue(old.cancelled)
        old.callback(); model.clear_cache.assert_not_called()
        Timer.made[-1].callback(); model.clear_cache.assert_called_once()

    def test_actual_whisper_adapter_reuses_warm_instance_until_idle_expiry(self):
        spec = importlib.util.spec_from_file_location('_whisper_250', DESK.parent/'helene/core/media_audio.py')
        module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None); spec.loader.exec_module(module)
        factory = Mock(return_value=types.SimpleNamespace(transcribe=Mock(return_value=([types.SimpleNamespace(text='hello')], None))))
        config = module.AudioConfig.from_env({'PRAXIS_STT_KEEP_LOADED': '1'})
        adapter = module.FasterWhisperSTT(config, model_factory=factory)
        state = whisper_residency.Residency(adapter, timer=Timer)
        adapter._helene_residency = state
        whisper_residency.install(module)
        source = self.root/'voice.wav'; source.write_bytes(b'input identity; decoder is controlled')
        adapter._get_model()  # Existing warmup.
        warmed = adapter._model
        self.assertEqual(adapter.transcribe(source), 'hello')
        self.assertEqual(adapter.transcribe(source), 'hello')
        self.assertIs(adapter._model, warmed); factory.assert_called_once()
        Timer.made[-1].callback(); self.assertIsNone(adapter._model)
        adapter.transcribe(source); self.assertEqual(factory.call_count, 2)

    def test_warm_path_skips_configuration_and_library_scans_until_revision(self):
        read = Mock(return_value='{}')
        ready = {'ready': True, 'dir': str(self.root/'models/whisper')}
        module = types.SimpleNamespace(transcribe=Mock(return_value='said'), set_default_backend=Mock())
        with patch.object(voice, 'apply_stt', return_value=ready) as apply, patch.object(voice, 'install_root', return_value=self.root), \
                patch.object(recognition.importlib, 'import_module', return_value=module):
            engine = recognition.Recognition(self.root, self.cfg, read)
            engine.current(); engine.transcribe('first'); engine.transcribe('second')
            self.assertEqual(read.call_count, 1); self.assertEqual(apply.call_count, 1)
            module.set_default_backend.assert_not_called()  # Keep already warm boot instance.
            self.cfg.write_text('{"changed": true}', encoding='utf8')
            engine.transcribe('third'); self.assertEqual(apply.call_count, 2)
            pins = self.root/'.helene-component-pins.json'; pins.write_text('{}', encoding='utf8')
            engine.transcribe('fourth'); self.assertEqual(apply.call_count, 3)

    def test_configuration_change_resets_backend_once_not_every_recording(self):
        module = types.SimpleNamespace(transcribe=Mock(return_value='said'), set_default_backend=Mock())
        def apply(*args):
            os.environ['PRAXIS_STT_MODEL'] = self.cfg.read_text(encoding='utf8')
            return {'ready': True}
        with patch.dict(os.environ), patch.object(voice, 'apply_stt', side_effect=apply), \
                patch.object(voice, 'install_root', return_value=self.root), patch.object(recognition.importlib, 'import_module', return_value=module):
            engine = recognition.Recognition(self.root, self.cfg, lambda path: path.read_text(encoding='utf8'))
            engine.transcribe('a'); engine.transcribe('b')
            self.cfg.write_text('{"voice":{}}', encoding='utf8'); engine.transcribe('c'); engine.transcribe('d')
        self.assertEqual(module.set_default_backend.call_count, 2)


if __name__ == '__main__': unittest.main()
