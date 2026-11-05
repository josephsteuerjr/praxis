"""Cheap warm-path recognition admission; settings/pins invalidate explicitly."""
import importlib
import json
import os
from pathlib import Path
import threading
from . import voice, whisper_residency


def signature():
    return json.dumps({k:v for k,v in os.environ.items()
        if k.startswith('PRAXIS_STT_') or k == 'PRAXIS_AUDIO_MODEL_DIR'}, sort_keys=True)


def stamp(path):
    try:
        value = path.stat()
        return (str(path), value.st_ino, value.st_mtime_ns, value.st_size)
    except FileNotFoundError:
        return (str(path), None)


class Recognition:
    def __init__(self, tree, config, read_config):
        self.tree, self.config = Path(tree), Path(config)
        self.read_config = read_config
        self.token = None
        self.state = {}
        self.signature = None
        self.applied = signature()
        self.model_manifest = self.tree / 'models/whisper/installed.json'
        self.lock = threading.RLock()

    def _token(self):
        from .component_store import pin_file
        root = voice.install_root()
        return tuple(stamp(path) for path in (self.config, pin_file(self.tree), pin_file(root),
            self.model_manifest, root / 'voice/installed.json', root / 'helene-build.json'))

    def current(self):
        with self.lock:
            token = self._token()
            if token != self.token or not self.state.get('ready'):
                cfg = json.loads(self.read_config(self.config))
                self.state = voice.apply_stt(self.tree, cfg)
                self.signature = signature()
                if self.state.get('dir'):
                    self.model_manifest = Path(self.state['dir']) / 'installed.json'
                self.token = self._token()
            return self.state, self.signature

    def transcribe(self, path):
        with self.lock:
            state, selected = self.current()
            if not state.get('ready'):
                raise RuntimeError(state.get('why') or 'Распознавание не настроено: Настройки → Голос')
            module = importlib.import_module('media_audio')
            whisper_residency.install(module)
            if self.applied != selected:
                module.set_default_backend(None)
                self.applied = selected
            return module.transcribe(path)
