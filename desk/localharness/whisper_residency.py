"""Retain a used Whisper instance for thirty idle minutes, including warmup.

Installed at the application seam so preserved authored recognition code stays
untouched. Active inference is never unloaded by the idle timer.
"""
from contextlib import contextmanager
from functools import wraps
import threading
import weakref

IDLE_SECONDS = 30 * 60


class Residency:
    def __init__(self, model, *, seconds=IDLE_SECONDS, timer=threading.Timer):
        self.model = weakref.ref(model)
        self.seconds = seconds
        self.timer_factory = timer
        self.timer = None
        self.lock = threading.RLock()
        self.active = 0
        self.epoch = 0

    @contextmanager
    def use(self):
        with self.lock:
            self.epoch += 1
            self.active += 1
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1
                if not self.active:
                    self.epoch += 1
                    epoch = self.epoch
                    self.timer = self.timer_factory(self.seconds, lambda: self.expire(epoch))
                    self.timer.daemon = True
                    self.timer.start()

    def expire(self, epoch):
        # Hold the seam lock through unloading: an arriving recording either
        # renews its lease first or starts after unloading has completed.
        with self.lock:
            if epoch != self.epoch or self.active:
                return
            self.timer = None
            model = self.model()
            if model is not None:
                model.clear_cache()


_INSTALL_LOCK = threading.Lock()


def install(module):
    cls = getattr(module, 'FasterWhisperSTT', None)
    if cls is None or not all(hasattr(cls, name) for name in ('_get_model', '_transcribe', 'clear_cache')):
        return False  # A custom authored recognizer owns its own lifecycle.
    with _INSTALL_LOCK:
        if getattr(cls, '_helene_resident', False):
            return True
        def wrap(method):
            @wraps(method)
            def resident(self, *args, **kwargs):
                with _INSTALL_LOCK:
                    state = getattr(self, '_helene_residency', None)
                    if state is None:
                        state = self._helene_residency = Residency(self)
                with state.use():
                    return method(self, *args, **kwargs)
            return resident
        cls._get_model = wrap(cls._get_model)
        cls._transcribe = wrap(cls._transcribe)
        cls._helene_resident = True
    return True
