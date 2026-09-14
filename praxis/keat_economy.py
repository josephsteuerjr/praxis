"""Owner-DM renderer-local economy plan. No parsing of speaker text or source I/O.

Only exact source bodies present in the selected same-call E block are elided.
Headers, provenance, pointers, recall, runtime context and current input survive.
The plan is private/transient; receipts contain counts only.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import json

_COLLECT = ContextVar('keat_economy_collect', default=None)
_BOUND = ContextVar('keat_economy_bound', default=None)


@contextmanager
def collect():
    rows = {'sources': [], 'sections': []}
    token = _COLLECT.set(rows)
    try:
        yield rows
    finally:
        _COLLECT.reset(token)


def source(component, body, address=None):
    rows = _COLLECT.get()
    if (rows is not None and component in ('dossier', 'index', 'recap') and body
            and isinstance(address, str) and address.strip()):
        # Body equality is not source identity. The renderer-owned address is
        # never inferred from private text and distinguishes identical bodies.
        rows['sources'].append((component, address, body))


def section(body, block):
    import frame_layout
    rows = _COLLECT.get()
    if rows is None:
        return
    for component, address, raw in rows['sources']:
        if raw not in body:
            continue
        variants = [frame_layout.quote(raw.strip('\n'))]
        if frame_layout.plain_on():
            variants.append(frame_layout.quote(frame_layout.plain_body(raw).strip('\n')))
        for rendered in variants:
            if block.count(rendered) == 1:
                rows['sections'].append((component, address, raw, rendered, block))
                break


@contextmanager
def bind(ctx, current, evidence, rows):
    import frame_serve
    import frame_layout
    # Unsupported old/media adapters retain their exact legacy bytes.
    value = None
    if (frame_serve.enabled(ctx) and isinstance(current, str)
            and frame_layout.form_new()):
        value = (ctx, current, evidence, copy.deepcopy(rows))
    token = _BOUND.set(value)
    try:
        yield
    finally:
        _BOUND.reset(token)


def candidate(system, messages):
    import frame_serve
    import frame_layout
    bound = _BOUND.get()
    if bound is None or not frame_serve.enabled(bound[0]):
        return None
    _, current, evidence, rows = bound
    if not messages or messages[-1] != {'role': 'user', 'content': current}:
        return None
    coverage = frame_serve.economy_coverage(system)
    if not coverage:
        return None
    # Work on the exact builder evidence prefix, NEVER scan the current question.
    prefix = '<praxis_context_evidence>\n' + evidence.strip()
    if not current.startswith(prefix):
        return None
    changed = prefix
    removed = {}
    for ordinal, (component, address, body, rendered, section_block) in enumerate(rows['sections']):
        covered = coverage.get(component, {})
        # A rendered aggregate is not an identity proof: a different file can
        # contain both this address and the same body. Only renderer-owned maps.
        block = covered.get(address, '') if isinstance(covered, dict) else ''
        if component == 'dossier' and body != block:
            continue
        if (len(body) < 64 or body not in block or
                changed.count(section_block.strip()) != 1 or section_block.strip() not in evidence
                or changed.count(rendered) != 1 or evidence.count(rendered) != 1):
            continue
        marker = frame_layout.quote(
            f'[тело уже приведено в E; источник и граница раскрытия прежние; {component}:{ordinal}]')
        if len(marker) >= len(rendered):
            continue
        changed = changed.replace(rendered, marker, 1)
        removed[component] = removed.get(component, 0) + len(rendered.encode('utf-8')) - len(marker.encode('utf-8'))
    if changed == prefix:
        return None
    result = copy.deepcopy(messages)
    result[-1]['content'] = changed + current[len(prefix):]
    return result, removed


def size(value):
    n = len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'))
    return {'json_utf8_bytes': n, 'estimated_tokens': (n + 3) // 4}


def measurement(system, legacy, candidate, tools, removed):
    def parts(tape):
        content = tape[-1].get('content') if tape else None
        prefix, separator, suffix = (content.partition('\n</praxis_context_evidence>\n')
                                     if isinstance(content, str) else ('', '', ''))
        sections = ({'current_evidence': size(prefix), 'current_situation_and_message': size(suffix)}
                    if separator else {})
        return { **sections, 'system': size(system), 'history': size(tape[:-1]),
                 'current': size(tape[-1:]), 'messages': size(tape),
                 'tools': size(tools), 'envelope': size(dict(system=system, messages=tape, tools=tools)) }
    return dict(schema=1, method='local_json_utf8_bytes_div4_v1',
                provider_tokens=None, legacy=parts(legacy), candidate=parts(candidate),
                removed_utf8_bytes=removed,
                removed_estimated_tokens={key: (value + 3) // 4 for key, value in removed.items()})
