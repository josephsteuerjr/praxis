"""Content-free monotonic voice preparation telemetry; never artifact metadata.

Durations are non-overlapping. Preparation is bound only around the tool loop;
continuations get call-local durations, not stale turn preparation or tool time.

12.09: ``span(name)`` — подстадии внутри `old_context`. Стадия `old_context` = один
вызов `_build_prompt_parts`, и по прибору она держала медиану 18–22 с без единой метки
внутри. Подстадии пишутся в отдельный словарь `substages_ms` и НЕ входят в
`measured_total_ms`: стадии остаются неперекрывающимися, а подстадии — вложенными
(внешняя включает внутреннюю). Вне таймера хода `span` ничего не пишет.
"""
import contextlib
import contextvars
import time

_PREPARATION = contextvars.ContextVar('pre_model_preparation', default=None)
_SUBSTAGES = contextvars.ContextVar('pre_model_substages', default=None)
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
    out = {'schema': 'praxis.pre-model-timing.v1',
           'preparation_observed': prep is not None,
           'stages_ms': stages, 'measured_total_ms': round(sum(stages.values()), 3)}
    sub = _SUBSTAGES.get()
    if sub:
        out['substages_ms'] = dict(sub)
    _SUBSTAGES.set(None)
    return out


def start():
    """Instrumentation construction is optional, including clock failures."""
    try:
        timer = Timer()
    except Exception:
        return None
    try:
        _SUBSTAGES.set({})
    except Exception:
        pass
    return timer


def mark(timer, stage):
    try:
        timer.mark(stage)
    except Exception:
        pass


@contextlib.contextmanager
def span(name):
    """Подстадия сборки: сколько миллисекунд занял обёрнутый блок.

    Повторное имя суммируется (один тир может собираться в несколько заходов).
    Вне хода (нет `start()`) — пустая операция; ошибка прибора не смеет стоить хода,
    а исключение тела проходит наружу нетронутым.
    """
    sub = _SUBSTAGES.get()
    if not isinstance(sub, dict) or not isinstance(name, str) or not name:
        yield
        return
    t0 = time.monotonic()
    try:
        yield
    finally:
        try:
            sub[name] = round(sub.get(name, 0.0) + max(0.0, time.monotonic() - t0) * 1000, 3)
        except Exception:
            pass


def add(name, ms):
    """Прибавить уже измеренные миллисекунды к подстадии (для циклов, где `with` неудобен)."""
    sub = _SUBSTAGES.get()
    if not isinstance(sub, dict) or not isinstance(name, str) or not name:
        return
    try:
        sub[name] = round(sub.get(name, 0.0) + max(0.0, float(ms)), 3)
    except Exception:
        pass


def substages():
    """Снимок подстадий текущего хода (только для тестов и приборов)."""
    sub = _SUBSTAGES.get()
    return dict(sub) if isinstance(sub, dict) else {}


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
