"""Live preview of the words a model call is generating, for the owner's window.

Tim (Helene 2.7.2, local Gemma via llama.cpp at ~6 tokens/s): the answer appeared only
after the call finished, and thousands of reasoning tokens went by blind. Unlike
model_progress (content-free relay counters), this carries text, so it never leaves the
run folder: the agent's sink rewrites one small file that the window reads while the call
runs. The durable record of what the model said is still model_output.

Three streams: `thinking` (reasoning the provider streams), `text` (plain model text — under
the reply-hand contract a note to self) and `reply` (the `text` argument of a reply call as
it is generated; that is what the person will receive).

Observability never changes provider input or output. Without an observer every feed is a
no-op, and a failing sink is ignored.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import re
import time

_OBSERVER = ContextVar('model_preview_observer', default=None)
KINDS = ('text', 'thinking', 'reply')
LIMIT = 4000         # characters of each stream kept in a snapshot (the tail)
RAW_LIMIT = 400_000  # raw reply arguments kept to decode the text field
INTERVAL = 0.75      # seconds between snapshots; the window polls every ~1.5 s
_FIELD = re.compile(r'"text"\s*:\s*"')
_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', '"': '"', '\\': '\\', '/': '/'}


@contextmanager
def observe(callback, *, interval: float = INTERVAL):
    state = None
    if callable(callback):
        state = {'callback': callback, 'interval': float(interval), 'last': 0.0}
        _reset(state)
    token = _OBSERVER.set(state)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


def active() -> bool:
    return _OBSERVER.get() is not None


def _reset(state: dict) -> None:
    state.update({'text': '', 'thinking': '', 'reply_args': '', 'text_chars': 0, 'thinking_chars': 0})


def restart() -> None:
    """A new provider attempt (retry, fallback) starts an empty preview."""
    state = _OBSERVER.get()
    if state is not None:
        _reset(state)


def partial_text_field(raw: str) -> str:
    """The `text` string of a JSON object that is still being generated, decoded so far."""
    match = _FIELD.search(raw or '')
    if not match:
        return ''
    out, i, n = [], match.end(), len(raw)
    while i < n:
        ch = raw[i]
        if ch == '"':
            break
        if ch == '\\':
            if i + 1 >= n:
                break
            code = raw[i + 1]
            if code == 'u':
                digits = raw[i + 2:i + 6]
                if len(digits) < 4 or not re.fullmatch('[0-9a-fA-F]{4}', digits):
                    break
                out.append(chr(int(digits, 16)))
                i += 6
                continue
            out.append(_ESCAPES.get(code, code))
            i += 2
            continue
        out.append(ch)
        i += 1
    return ''.join(out).encode('utf-16', 'surrogatepass').decode('utf-16', 'replace')


def feed(kind: str, fragment) -> None:
    """kind: text, thinking, or reply_args (raw JSON arguments of a reply call)."""
    state = _OBSERVER.get()
    if state is None or not isinstance(fragment, str) or not fragment:
        return
    if kind == 'reply_args':
        state['reply_args'] = (state['reply_args'] + fragment)[:RAW_LIMIT]
    elif kind in ('text', 'thinking'):
        state[kind] = (state[kind] + fragment)[-LIMIT:]
        state[kind + '_chars'] += len(fragment)
    else:
        return
    now = time.monotonic()
    if now - state['last'] >= state['interval']:
        state['last'] = now
        reply = partial_text_field(state['reply_args'])
        snapshot = {'text': state['text'], 'thinking': state['thinking'], 'reply': reply[-LIMIT:],
                    'text_chars': state['text_chars'], 'thinking_chars': state['thinking_chars'],
                    'reply_chars': len(reply)}
        try:
            state['callback'](snapshot)
        except Exception:
            pass  # a preview sink can never fail, retry or cancel the provider call
