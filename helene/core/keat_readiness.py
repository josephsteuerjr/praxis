"""Read-only KEAT activation readiness receipt.

This module never creates capture/epoch directories and never imports serving code.
It validates the administrator's exact environment, canonical policy bytes, configured
root and bidirectional mode/stream enrollment.  Public receipts contain counts and
digests only; ``private=True`` additionally exposes configured paths/stream names.

``capture_ready`` attests configuration validity while capture is exactly ``on``.
``serve_ready`` additionally requires serving to be exactly ``serve`` and every
selected enrollment to have a proven serving adapter and its mode-specific
authority. The legacy ``ready`` field is retained as an alias for
``serve_ready``; it never means that a capture-only soak is misconfigured or that
live deployment was proved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

from keat_source import _json

MODES = frozenset(('owner', 'dm', 'group', 'wake', 'window'))


def _csv(value):
    """Parse the legacy literal comma grammar used by the owner compatibility path."""
    if type(value) is not str:
        return None
    rows = value.split(',')
    return (rows if all(rows) and all(part == part.strip() for part in rows) and
            len(rows) == len(set(rows)) else None)


def _canonical_pairs(value):
    """Parse the canonical sparse grammar consumed by ``keat_runtime.staged``."""
    if type(value) is not str:
        return None
    try:
        rows = json.loads(value)
        if (type(rows) is not list or not rows or
                any(type(row) is not list or len(row) != 2 for row in rows)):
            return None
        pairs = []
        for mode, stream in rows:
            if (type(mode) is not str or mode not in MODES or
                    type(stream) is not str or not stream or stream != stream.strip()):
                return None
            pairs.append((mode, stream))
        if len(pairs) != len(set(pairs)) or pairs != sorted(pairs):
            return None
        canonical = json.dumps(rows, ensure_ascii=True, allow_nan=False,
                               separators=(',', ':'))
        return pairs if value == canonical else None
    except (TypeError, ValueError, RecursionError):
        return None


def _configured_pairs(env):
    """Parse sparse pairs, with a narrow compatibility path for owner-only env."""
    if 'PRAXIS_KEAT_PAIRS' in env:
        return _canonical_pairs(env.get('PRAXIS_KEAT_PAIRS')), 'pairs'
    modes = _csv(env.get('PRAXIS_KEAT_MODES'))
    streams = _csv(env.get('PRAXIS_KEAT_STREAMS'))
    # One historical mode is an unambiguous exact set of pairs. New dm and every
    # multi-mode stage require PRAXIS_KEAT_PAIRS.
    if (not streams or modes is None or len(modes) != 1 or
            modes[0] not in MODES - {'dm', 'group'}):
        return None, 'legacy_single_mode'
    return [(modes[0], stream) for stream in streams], 'legacy_single_mode'


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _serving_adapter_ready(row):
    """Mirror the mode/authority admission of an actual serving adapter."""
    mode = row['mode']
    if mode == 'owner':
        return True
    if mode == 'wake':
        # issue_scheduled_wake constructs this context itself; no other wake
        # stream can ever be issued or adopted by the runtime adapter.
        return row['stream'] == 'scheduler:wake'
    if mode == 'group':
        stream = row['stream']
        capture = row['capture']
        return bool(
            stream == '-1001240718803' and
            capture['audience'] == ['group:' + stream] and
            capture['transfer'] == 'none' and
            capture['presence_hidden'] is False)
    if mode != 'dm':
        # Window remains capture-only until it has a proved serving adapter.
        return False
    stream = row['stream']
    capture = row['capture']
    try:
        canonical_positive_peer = (
            stream.isascii() and stream.isdecimal() and
            str(int(stream)) == stream and int(stream) > 0)
    except ValueError:
        # Match runtime rejection, including Python's bounded integer parsing.
        canonical_positive_peer = False
    return bool(
        canonical_positive_peer and
        capture['audience'] == ['dm:' + stream] and
        capture['transfer'] == 'none' and
        capture['presence_hidden'] is False
    )


def receipt(env=None, *, private=False):
    """Return a content-free readiness receipt; malformed state never raises."""
    env = os.environ if env is None else env
    result = {
        'schema': 'keat.readiness.v1', 'read_only': True,
        'serving_changed': False, 'ready': False,
        'capture_ready': False, 'serve_ready': False,
        'state': 'default_off', 'checks': {}, 'enrollment_count': 0,
        'policy_sha256': None,
    }
    checks = result['checks']
    capture = env.get('PRAXIS_KEAT_CAPTURE', '')
    serving = env.get('PRAXIS_KEAT', '')
    selected_pairs, selector_source = _configured_pairs(env)
    checks['default_off_exact'] = capture in ('', 'off') and serving in ('', 'off')
    checks['capture_value_valid'] = capture in ('', 'off', 'on')
    checks['serve_value_valid'] = serving in ('', 'off', 'serve')
    checks['capture_on'] = capture == 'on'
    checks['serve_on'] = serving == 'serve'
    checks['serve_off'] = serving in ('', 'off')
    checks['selectors_valid'] = selected_pairs is not None
    policy_name = env.get('PRAXIS_KEAT_CAPTURE_POLICY', '')
    root_name = env.get('PRAXIS_KEAT_ROOT', '')
    policy_path, expected_root = Path(policy_name), Path(root_name)
    checks['policy_path_absolute'] = bool(policy_name) and policy_path.is_absolute()
    checks['root_absolute'] = bool(root_name) and expected_root.is_absolute()
    if private:
        result['policy_path'] = policy_name or None
        result['root'] = root_name or None
        result['pairs'] = ([list(pair) for pair in selected_pairs]
                           if selected_pairs is not None else None)
        result['selector_source'] = selector_source
        # Retain existing trusted-host owner-canary receipt fields while migrating.
        result['modes'] = _csv(env.get('PRAXIS_KEAT_MODES'))
        result['streams'] = _csv(env.get('PRAXIS_KEAT_STREAMS'))
    policy = None
    try:
        raw = policy_path.read_bytes() if checks['policy_path_absolute'] else b''
        policy = _json(raw)
        result['policy_sha256'] = _digest(raw)
        checks['policy_schema'] = (type(policy) is dict and set(policy) == {
            'schema', 'namespace', 'root', 'enrollments'} and
            policy['schema'] == 'keat.capture-policy.v1' and
            type(policy['namespace']) is str and bool(policy['namespace'].strip()) and
            type(policy['enrollments']) is list)
    except (OSError, ValueError, UnicodeError, TypeError):
        checks['policy_schema'] = False
    checks['policy_root_exact'] = False
    checks['policy_enrollments_valid'] = False
    checks['enrollments_exact'] = False
    checks['native_dm_enrollments_unique'] = False
    checks['serving_adapters_ready'] = False
    if checks['policy_schema']:
        try:
            policy_root = Path(policy['root'])
            checks['policy_root_exact'] = (checks['root_absolute'] and
                policy_root.is_absolute() and policy_root == expected_root)
            pairs = []
            for row in policy['enrollments']:
                if type(row) is not dict or set(row) != {'stream', 'mode', 'capture'}:
                    raise ValueError
                if type(row['stream']) is not str or not row['stream'] or row['mode'] not in MODES:
                    raise ValueError
                capture_row = row['capture']
                if type(capture_row) is not dict or set(capture_row) != {
                        'issuer', 'policy_revision', 'audience', 'transfer', 'presence_hidden'}:
                    raise ValueError
                if (not all(type(capture_row[name]) is str and capture_row[name].strip()
                            for name in ('issuer', 'policy_revision', 'transfer')) or
                        type(capture_row['audience']) is not list or
                        not capture_row['audience'] or
                        not all(type(item) is str and item.strip()
                                for item in capture_row['audience']) or
                        len(capture_row['audience']) != len(set(capture_row['audience'])) or
                        type(capture_row['presence_hidden']) is not bool):
                    raise ValueError
                pairs.append((row['mode'], row['stream']))
            actual = set(pairs)
            checks['policy_enrollments_valid'] = bool(pairs) and len(pairs) == len(actual)
            native_streams = [stream for mode, stream in pairs
                              if mode in ('owner', 'dm', 'group')]
            # Native ingress resolves a Telegram peer before it knows whether
            # owner or ordinary-DM authority applies. Mirror _native_policy's
            # cross-row uniqueness: a stream cannot carry both authorities.
            checks['native_dm_enrollments_unique'] = (
                len(native_streams) == len(set(native_streams)))
            selected = set(selected_pairs or ())
            checks['enrollments_exact'] = (checks['policy_enrollments_valid'] and
                                           actual == selected)
            checks['serving_adapters_ready'] = (
                checks['policy_enrollments_valid'] and
                checks['native_dm_enrollments_unique'] and
                all(_serving_adapter_ready(row) for row in policy['enrollments']))
            result['enrollment_count'] = len(pairs)
        except (KeyError, TypeError, ValueError):
            pass
    capture_checks = ('capture_on', 'policy_path_absolute', 'root_absolute',
                      'policy_schema', 'policy_root_exact', 'policy_enrollments_valid')
    result['capture_ready'] = all(checks.get(name) is True for name in capture_checks)
    result['serve_ready'] = (result['capture_ready'] and checks['serve_on'] and
                             checks['selectors_valid'] and checks['enrollments_exact'] and
                             checks['serving_adapters_ready'])
    # Compatibility: readiness.v1 ``ready`` has always meant ready to serve.
    result['ready'] = result['serve_ready']
    if result['serve_ready']:
        result['state'] = 'ready'
    elif result['capture_ready'] and checks['serve_off']:
        result['state'] = 'capture_only_ready'
    elif (not checks['capture_value_valid'] or not checks['serve_value_valid'] or
          capture == 'on' or serving == 'serve'):
        result['state'] = 'misconfigured'
    return result


def observability(tree, env=None, *, days=None, private=False):
    """Join configured readiness with durable call receipts, never request content."""
    import keat_stats as frame_stats  # издание: её frame_stats под этим именем
    since = None
    if days is not None:
        from datetime import datetime, timedelta, timezone
        since = datetime.now(timezone.utc) - timedelta(days=days)
    rows, diagnostics = frame_stats.collect(tree, since=since)
    return {'schema': 'keat.observability.v1',
            'readiness': receipt(env, private=private),
            'input_cost': frame_stats.report(
                rows, diagnostics, axes=('keat_status', 'frame_mode'), private=private),
            'since': since.isoformat() if since else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private', action='store_true',
                        help='include configured paths/selectors and per-call metric rows')
    parser.add_argument('--tree', type=Path,
                        help='also join durable model_input/model_completed receipts')
    parser.add_argument('--days', type=float, help='optional lookback for --tree')
    args = parser.parse_args(argv)
    if args.days is not None and (not args.tree or not math.isfinite(args.days) or
                                  not 0 <= args.days <= 3650):
        parser.error('--days requires --tree and must be finite in the range 0..3650')
    value = (observability(args.tree, days=args.days, private=args.private)
             if args.tree else receipt(private=args.private))
    print(json.dumps(value, indent=2, sort_keys=True,
                     ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
