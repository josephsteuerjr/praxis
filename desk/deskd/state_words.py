"""Closed display recipes for current service state, independent of frame locale."""
from __future__ import annotations
import math

FIELDS={
 'reader_failed':(), 'not_running':(), 'quota_fallback':('until',), 'quota_no_fallback':('until',),
 'key_missing':(), 'model_missing':(), 'relay_signin':(), 'sleeping':(), 'thinking':(),
 'wake_wait':('at',), 'online':(), 'paused':(),
 'error.auth':(), 'error.quota':(), 'error.timeout':(), 'error.network':(), 'error.address':(),
 'error.parameter':(), 'error.context':(), 'error.policy':(), 'error.request':(), 'error.server':(), 'error.unknown':(),
 'action.restart':(), 'action.settings':(), 'action.key':(), 'action.configure':(), 'action.signin':(),
 'alarm.reader_state':(), 'alarm.reader_pulse':(), 'alarm.restart_loop':('count',),
 'alarm.debt':('minutes','count'), 'alarm.long_silence':('minutes',), 'alarm.defer_storm':('count',),
}


def make(kind,**values):
    if (type(kind) is not str or kind not in FIELDS or set(values)!=set(FIELDS[kind])
            or any(type(value) not in (int,float) or not math.isfinite(value) or value<0 for value in values.values())):
        raise ValueError('Invalid agent state display recipe')
    return {'schema':'helene.agent-state.v1','kind':kind,'values':dict(values)}


def error_hint(kind,legacy,owned):
    return make('error.'+kind) if owned else legacy
