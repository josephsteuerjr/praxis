"""Content-free provider progress. Observability never changes provider input or output."""
from contextlib import contextmanager
from contextvars import ContextVar
import math
import re
import time

_OBSERVER = ContextVar('model_progress_observer', default=None)
_COUNTS = ('argument_deltas', 'argument_bytes', 'argument_chars',
           'outside_string_whitespace_chars', 'longest_whitespace_run',
           'max_call_argument_bytes', 'delta_span_ms', 'attempt_ms')


def project(value):
    if not isinstance(value, dict) or value.get('schema') != 'relay.generation.v1':return {}
    result = {'schema': 'relay.generation.v1'}
    attempt = value.get('attempt_id')
    if isinstance(attempt,str) and re.fullmatch('[0-9a-f]{32}',attempt):result['attempt_id']=attempt
    for key in _COUNTS:
        item=value.get(key)
        if type(item) is int and item>=0:result[key]=item
    ratio=value.get('outside_string_whitespace_ratio')
    if type(ratio) in (int,float) and 0<=ratio<=1 and math.isfinite(ratio):
        result['outside_string_whitespace_ratio']=ratio
    outcome=value.get('outcome')
    if outcome in ('streaming','interrupted','completed','upstream_failed','runaway_generation'):
        result['outcome']=outcome
    for key,allowed in [('limits',('argument_bytes','whitespace_run','attempt_ms')),
                        ('usage',('input_tokens','output_tokens','total_tokens','cached_tokens','reasoning_tokens'))]:
        data=value.get(key)
        if isinstance(data,dict):result[key]={k:data[k] for k in allowed if type(data.get(k)) is int and data[k]>=0}
    return result if any(k in result for k in _COUNTS) else {}


def packet_of(chunk):
    value=chunk.get('relay_generation') if isinstance(chunk,dict) else getattr(chunk,'relay_generation',None)
    if value is None:
        extra=getattr(chunk,'model_extra',None)
        if isinstance(extra,dict):value=extra.get('relay_generation')
    return project(value)


@contextmanager
def observe(callback):
    token=_OBSERVER.set({'callback':callback,'last':0.0,'attempt':None})
    try:yield
    finally:_OBSERVER.reset(token)


def emit(value):
    observer=_OBSERVER.get()
    if observer is None:return
    safe=project(value)
    if not safe:return
    now=time.monotonic();attempt=safe.get('attempt_id')
    terminal=safe.get('outcome') in ('completed','upstream_failed','runaway_generation')
    if observer['attempt']==attempt and now-observer['last']<5 and not terminal:return
    observer.update(last=now,attempt=attempt)
    try:observer['callback'](safe)
    except Exception:pass  # A failed diagnostic sink cannot retry/cancel an adapter.
