"""Content-free monotonic voice preparation telemetry; never artifact metadata.

Durations are non-overlapping. Preparation is bound only around the tool loop;
continuations get call-local durations, not stale turn preparation or tool time.
"""
import contextlib
import contextvars
import time

_PREPARATION = contextvars.ContextVar('pre_model_preparation', default=None)
STAGES = frozenset(('old_context', 'message_tools', 'shadow', 'call_setup',
                    'measure', 'canary', 'model_input_artifact'))


class Timer:
    def __init__(self):
        self.last = time.monotonic()
        self.stages = {}

    def mark(self, stage):
        if stage not in STAGES:
            raise ValueError('unknown timing stage')
        now = time.monotonic()
        self.stages[stage] = round(max(0, now - self.last) * 1000, 3)
        self.last = now


@contextlib.contextmanager
def bind(timer):
    token = _PREPARATION.set(dict(timer.stages))
    try:
        yield
    finally:
        _PREPARATION.reset(token)


def payload(timer):
    prep = _PREPARATION.get()
    _PREPARATION.set(None)
    stages = dict(prep or {}, **timer.stages)
    return {'schema': 'praxis.pre-model-timing.v1',
            'preparation_observed': prep is not None,
            'stages_ms': stages, 'measured_total_ms': round(sum(stages.values()), 3)}


def start():
    """Instrumentation construction is optional, including clock failures."""
    try:
        return Timer()
    except Exception:
        return None


def mark(timer, stage):
    try:
        timer.mark(stage)
    except Exception:
        pass


@contextlib.contextmanager
def safe_bind(timer):
    """Guard meter lifecycle, never the voice body.

    Own a separate context token so a broken bind entry/exit cannot leak or
    consume an outer preparation. Do not give the meter the body's exception:
    its __exit__ must neither suppress nor replace a voice/model failure.
    """
    token = _PREPARATION.set(None)
    manager = None
    entered = False
    try:
        try:
            manager = bind(timer)
            manager.__enter__()
            entered = True
        except Exception:
            _PREPARATION.set(None)
        try:
            yield
        finally:
            if entered:
                try:
                    manager.__exit__(None, None, None)
                except Exception:
                    pass
    finally:
        _PREPARATION.reset(token)
