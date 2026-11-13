"""Versioned multi-file replacements. Caller holds its root and shared mutation locks.

All preflight checks precede publication. Each file is published and read back separately;
there is deliberately no multi-file atomicity claim and no automatic rollback.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import hands

FILES_SCHEMA = {
    'type': 'array', 'minItems': 1,
    'description': 'Versioned files; do not mix with single-file arguments.',
    'items': {'type': 'object', 'additionalProperties': False,
              'required': ['path', 'expected_sha256', 'edits'], 'properties': {
        'path': {'type': 'string', 'description': 'Existing UTF-8 file.'},
        'expected_sha256': {'type': 'string', 'description': 'Read version: 64 or 16 hex.'},
        'edits': {'type': 'array', 'minItems': 1, 'description': 'Unique replacements.',
                  'items': {'type': 'object', 'additionalProperties': False, 'required': ['old', 'new'], 'properties': {
                      'old': {'type': 'string', 'minLength': 1, 'description': 'Unique exact text.'},
                      'new': {'type': 'string', 'description': 'Replacement; empty deletes.'},
                      'all': {'type': 'boolean', 'description': 'Replace all occurrences; overlapping matches still refuse.'}}}}}}
}
MODE_SCHEMA = {'type': 'string', 'enum': ['snapshot', 'sequential'],
               'description': 'snapshot (default): original text, no overlap. sequential: chained replacements.'}
DESCRIPTION = (' Batch uses files, each with its read SHA. All-file preflight before writing. '
               'snapshot matches old in the original (once, or every occurrence with all=true), refusing overlaps; sequential chains edits. '
               'One publication/readback per file. Reply lists every file and before/after SHA: '
               'confirmed/refused/in_doubt; partial means mixed confirmed/refused, in_doubt takes precedence. '
               'Stops on failure. No auto rollback: reread and edit with fresh versions. '
               'Remote host/Windows batch refuses without sending edits.')


def replacements(raw: bytes, edits, mode: str = 'snapshot'):
    data, error, _ = replacement_plan(raw, edits, mode)
    return data, error


def replacement_plan(raw: bytes, edits, mode: str = 'snapshot'):
    if not isinstance(mode, str) or mode not in {'snapshot', 'sequential'}:
        return None, 'mode must be snapshot or sequential.', 0
    if not isinstance(edits, list) or not edits:
        return None, 'edits must be a nonempty list.', 0
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return None, 'File is not UTF-8; no bytes changed.', 0
    spans, count = [], 0
    for index, edit in enumerate(edits):
        if (not isinstance(edit, dict) or not {'old', 'new'} <= set(edit)
                or set(edit) - {'old', 'new', 'all'} or not isinstance(edit.get('all', False), bool)):
            return None, f'edit {index}: expected old, new and optional boolean all.', 0
        old, new = edit['old'], edit['new']
        if not isinstance(old, str) or not old or not isinstance(new, str):
            return None, f'edit {index}: old must be nonempty text and new must be text.', 0
        start = text.find(old)
        # str.count misses overlapping occurrences, e.g. aa in aaa.
        if start < 0 or (not edit.get('all', False) and text.find(old, start + 1) >= 0):
            return None, f'edit {index}: old must occur exactly once unless all=true; reread and widen the context.', 0
        matches = []
        while start >= 0:
            matches.append((start, start + len(old), new))
            start = text.find(old, start + 1) if edit.get('all', False) else -1
        if any(a[1] > b[0] for a, b in zip(matches, matches[1:])):
            return None, f'edit {index}: overlapping matches are ambiguous even with all=true.', 0
        count += len(matches)
        if mode == 'sequential':
            for start, end, replacement in reversed(matches):
                text = text[:start] + replacement + text[end:]
        else:
            spans.extend(matches)
    if mode == 'snapshot':
        spans.sort()
        if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
            return None, 'Overlapping replacements in the original snapshot; no bytes changed.', 0
        for start, end, new in reversed(spans):
            text = text[:start] + new + text[end:]
    try:
        return text.encode('utf-8'), '', count
    except UnicodeEncodeError:
        return None, 'Replacement is not encodable as UTF-8.', 0


def result(rows, message=''):
    states = {r['status'] for r in rows}
    status = ('in_doubt' if 'in_doubt' in states else
              'confirmed' if states == {'confirmed'} else
              'partial' if 'confirmed' in states else 'refused')
    return {'status': status, 'message': message, 'files': rows,
            'before_sha256': None, 'after_sha256': None}


def refused(files, reason):
    rows = [dict(path=f.get('path', ''), **hands.outcome('refused', reason))
            for f in files if isinstance(f, dict)] if isinstance(files, list) else []
    return result(rows, reason)


def uncertain(value, reason):
    rows = [dict(r) for r in value['files']]
    for r in rows:
        if r['status'] == 'confirmed':
            r.update(status='in_doubt', message=reason)
    return result(rows, reason)


def render(value):
    return 'batch: ' + json.dumps(value, ensure_ascii=False)


def apply(files, resolve, mode='snapshot'):
    """resolve(path) -> (canonical target, refusal). No locks acquired here."""
    if not isinstance(files, list) or not files:
        return refused(files, 'files must be a nonempty list.')
    plans, rows, seen = [], [], set()
    for item in files:
        reason, before, data, target, count = '', None, None, None, 0
        path = item.get('path', '') if isinstance(item, dict) else ''
        if (not isinstance(item, dict) or set(item) != {'path', 'expected_sha256', 'edits'}
                or not isinstance(path, str) or not path
                or not isinstance(item.get('expected_sha256'), str) or not item['expected_sha256']):
            reason = 'Each file requires path, expected_sha256 and edits, with no extra fields.'
        else:
            try:
                target, reason = resolve(path)
                if target is not None and not reason:
                    target = Path(target)
                    key = os.path.normcase(str(target.resolve()))
                    if key in seen:
                        reason = 'Duplicate resolved file path; combine its edits in one entry.'
                    seen.add(key)
                    raw, before = hands.snapshot(target)
                    reason = reason or hands.version_conflict(item['expected_sha256'], before)
                    if not reason:
                        if raw is None:
                            reason = 'File does not exist; batch only edits existing files.'
                        else:
                            data, reason, count = replacement_plan(raw, item['edits'], mode)
                else:
                    reason = reason or 'Path refused.'
            except (OSError, ValueError, RuntimeError) as exc:
                reason = f'Preflight failed: {type(exc).__name__}.'
        rows.append(dict(path=path, **hands.outcome('refused', reason or 'Not published.', before)))
        plans.append((target, data, before, path, reason, count))
    if any(p[4] for p in plans):
        return result(rows, 'Preflight refused; no files written.')
    for i, (target, data, before, path, _, count) in enumerate(plans):
        def recheck():
            again, reason = resolve(path)
            return reason or ('' if again == target else 'Path changed after preflight.')
        try:
            value = hands.publish(target, data, before, recheck=recheck)
        except Exception as exc:
            # The publisher may already have acted. Preserve all earlier receipts.
            value = hands.outcome('in_doubt', f'Publisher raised {type(exc).__name__}; reread before retry.', before)
        rows[i].update(value)
        if value['status'] == 'confirmed':
            rows[i]['replacements'] = count
        if value['status'] != 'confirmed':
            for remaining in rows[i+1:]:
                remaining['message'] = 'Not attempted after an earlier publication failed.'
            break
    return result(rows, 'Per-file results; no automatic rollback.')
