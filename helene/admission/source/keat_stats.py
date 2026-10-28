"""Read-only, stdlib-only run-event statistics (no serving imports).

Издание (26.09): это её `frame_stats.py` под другим именем. У издания свой `frame_stats.py`
(разрезы кадра 08.09, другой интерфейс); приёмная КЕАТ (`keat_readiness.observability`)
читает эту, её статистику — как в проде.

One observation is a model_completed receipt, NOT a logical step or backend
attempt. Joins require the same nonempty model call_id within one run; tool
call_id is a different namespace and is never joined by adjacency. No artifacts,
text, manifests or frame snapshots are read. Thin frame metadata is not a mode.

Usage counters are reported as recorded, not as money. llm.py currently normalizes
OpenAI/Responses input to fresh tokens and Anthropic input is already fresh, but
historical receipts do not attest that version. A cache ratio therefore requires
explicit usage.schema == 2. It excludes cache_creation (not a total billed-input
ratio). Missing cache counters, including omitted OpenAI zeroes, remain unknown.
Public output uses report-local category labels for arbitrary strings; these are
not stable identifiers. --private exposes selected IDs/labels, never raw text.
``frame_sections_recorded`` counts only the bounded section list preserved in a
model-input receipt; it is not a complete provider-input section count.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json
import math
from pathlib import Path

UNKNOWN = 'unknown'
AXES = ('role', 'model', 'framework', 'step', 'iteration', 'hand', 'frame_mode',
        'variant', 'stream', 'canary', 'keat_status', 'day')
DEFAULT_AXES = ('role', 'iteration', 'hand', 'frame_mode', 'variant')
METRICS = ('in', 'cache_read', 'cache_creation', 'out', 'duration_ms', 'tool_calls')
INPUT_METRICS = ('input_estimated_tokens', 'frame_sections_recorded', 'preparation_ms')
SUMMARY_METRICS = METRICS + INPUT_METRICS


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if math.isfinite(value) and value >= 0:
                return value
        except OverflowError:
            pass
    return None


def label(value):
    return value if isinstance(value, str) and value.strip() else UNKNOWN


def quantile(values, p):
    """Nearest rank: sorted[ceil(p*n)-1], p in (0,1]; empty => None."""
    if not 0 < p <= 1:
        raise ValueError('p must be in (0, 1]')
    values = sorted(values)
    return values[max(0, math.ceil(p * len(values)) - 1)] if values else None


def events(path, diagnostics):
    try:
        with path.open('rb') as source:
            for line in source:
                diagnostics['lines'] += 1
                if not line.strip():
                    diagnostics['blank_lines'] += 1
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    diagnostics['malformed_lines'] += 1
                    continue
                if not isinstance(row, dict) or not isinstance(row.get('kind'), str):
                    diagnostics['invalid_events'] += 1
                    continue
                yield row
    except (OSError, UnicodeError):
        diagnostics['unreadable_files'] += 1


def calls_from_run(run_dir, *, since=None, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else Counter()
    receipts = list(events(Path(run_dir) / 'events.jsonl', diagnostics))
    inputs = defaultdict(list)
    timings = defaultdict(list)
    for event in receipts:
        if event['kind'] == 'model_input' and label(event.get('call_id')) != UNKNOWN:
            meta = event.get('metadata')
            if not isinstance(meta, dict):
                result = event.get('result')
                meta = result.get('metadata') if isinstance(result, dict) else None
            if isinstance(meta, dict):
                inputs[event['call_id']].append(meta)
        if event['kind'] == 'model_preparation_timing' and label(event.get('call_id')) != UNKNOWN:
            timings[event['call_id']].append(event)
    rows = []
    for event in receipts:
        if event['kind'] != 'model_completed':
            continue
        diagnostics['completed_seen'] += 1
        at = timestamp(event.get('at'))
        if at is None:
            diagnostics['completed_timestamp_unknown'] += 1
        if since is not None:
            if at is None:
                diagnostics['excluded_timestamp_unknown'] += 1
                continue
            if at < since:
                diagnostics['excluded_before_cutoff'] += 1
                continue
        call = label(event.get('call_id'))
        candidates = inputs.get(call, []) if call != UNKNOWN else []
        # Conflicting duplicated inputs are not resolved by choosing nearest/latest.
        meta = candidates[0] if candidates and all(m == candidates[0] for m in candidates) else {}
        if candidates and not meta:
            diagnostics['ambiguous_input_metadata'] += 1
        usage = event.get('usage') if isinstance(event.get('usage'), dict) else {}
        row = {axis: UNKNOWN for axis in AXES}
        for axis in ('role', 'model', 'framework', 'stream', 'canary', 'variant'):
            row[axis] = label(event.get(axis))
        for axis in ('variant', 'stream', 'canary'):
            if row[axis] == UNKNOWN:
                row[axis] = label(meta.get(axis))
        row['frame_mode'] = label(meta.get('mode'))
        keat = meta.get('keat') if isinstance(meta.get('keat'), dict) else {}
        row['keat_status'] = label(keat.get('status'))
        row['step'] = label(event.get('step_id'))
        iteration = event.get('iteration')
        if isinstance(iteration, int) and not isinstance(iteration, bool) and iteration >= 0:
            row['iteration'] = str(iteration)
        # Current tool_started carries only its OWN call_id: no model link.
        # No step/tool attribution from call-id spelling or nearby events.
        schema = usage.get('schema')
        # Schema is a version, never an arbitrary payload (even in private output).
        schema = schema if type(schema) is int and schema >= 0 else None
        row.update(run=str(Path(run_dir)), call=call, frame=label(meta.get('frame_id')),
                   day=at.date().isoformat() if at else UNKNOWN,
                   stop=label(event.get('stop_reason')), usage_schema=schema)
        row.update({key: number(usage.get(key)) for key in METRICS[:4]})
        row.update({key: number(event.get(key)) for key in METRICS[4:]})
        measurement = meta.get('frame_measure') if isinstance(meta.get('frame_measure'), dict) else {}
        actual = measurement.get('actual') if isinstance(measurement.get('actual'), dict) else {}
        row['input_estimated_tokens'] = number(actual.get('estimated_tokens'))
        # model_input metadata contains only the frame recorder's bounded section
        # sample. It does not attest the provider request's complete section count.
        sections = meta.get('sections') if isinstance(meta.get('sections'), list) else None
        row['frame_sections_recorded'] = len(sections) if sections is not None else None
        timing_candidates = timings.get(call, []) if call != UNKNOWN else []
        timing = (timing_candidates[0] if timing_candidates and
                  all(t == timing_candidates[0] for t in timing_candidates) else {})
        if timing_candidates and not timing:
            diagnostics['ambiguous_preparation_timing'] += 1
        row['preparation_ms'] = number(timing.get('measured_total_ms'))
        rows.append(row)
    return rows


def collect(tree, *, since=None):
    if since is not None and since.tzinfo is None:
        raise ValueError('cutoff must be timezone-aware')
    diagnostics = Counter()
    rows = []
    root = Path(tree) / 'memory' / 'runs'
    for path in sorted(root.glob('*/*/events.jsonl')):
        diagnostics['files'] += 1
        rows.extend(calls_from_run(path.parent, since=since, diagnostics=diagnostics))
    return rows, dict(diagnostics)


def summarize(rows):
    total = len(rows)
    metrics = {}
    overflowed_sums = []
    for key in SUMMARY_METRICS:
        values = [r[key] for r in rows if r[key] is not None]
        # Accepted counters are finite, but their aggregate need not be.
        try:
            aggregate = number(sum(values)) if values else None
        except OverflowError:
            aggregate = None
        if values and aggregate is None:
            overflowed_sums.append(key)
        metrics[key] = dict(known=len(values), missing=total-len(values),
                            coverage=len(values)/total if total else None,
                            sum=aggregate,
                            p50=quantile(values, .5), p90=quantile(values, .9))
    paired = [r for r in rows if r['usage_schema'] == 2 and
              r['in'] is not None and r['cache_read'] is not None]
    # Exact rational accumulation avoids overflow in both per-row additions
    # and totals. Only the final bounded [0, 1] ratio becomes a float.
    fresh = sum((Fraction(r['in']) for r in paired), Fraction())
    read = sum((Fraction(r['cache_read']) for r in paired), Fraction())
    denominator = fresh + read
    stops = [r['stop'] for r in rows if r['stop'] != UNKNOWN]
    keat = Counter(r['keat_status'] if r['keat_status'] in ('served', 'fallback')
                   else UNKNOWN for r in rows)
    return dict(calls=total, metrics=metrics,
                keat_status=dict(served=keat['served'], fallback=keat['fallback'],
                                 unknown=keat[UNKNOWN]),
                diagnostics=dict(overflowed_sums=overflowed_sums),
                cuts=dict(known=len(stops), missing=total-len(stops),
                          count=stops.count('max_tokens') if stops else None),
                cache_read_over_fresh_plus_read=dict(
                    value=float(read/denominator) if denominator else None,
                    known=len(paired), missing=total-len(paired),
                    coverage=len(paired)/total if total else None),
                attribution={a: dict(known=sum(r[a] != UNKNOWN for r in rows),
                                     unknown=sum(r[a] == UNKNOWN for r in rows)) for a in AXES})


def report(rows, diagnostics, *, axes=DEFAULT_AXES, private=False):
    if not axes or any(a not in AXES for a in axes) or len(set(axes)) != len(axes):
        raise ValueError('choose distinct supported axes')
    buckets = defaultdict(list)
    # Public labels deliberately hide arbitrary model/role/tool/variant strings,
    # including malicious labels containing secrets. Unknown remains explicit.
    labels = {a: {v: f'category-{i+1}' for i, v in enumerate(sorted(
        {r[a] for r in rows if r[a] != UNKNOWN}))} for a in axes}
    for row in rows:
        key = tuple(row[a] if private or row[a] == UNKNOWN else labels[a][row[a]] for a in axes)
        buckets[key].append(row)
    result = dict(schema=1, privacy='private' if private else 'aggregate',
                  quantile='nearest-rank ceil(p*n)', observation='model_completed receipt',
                  cost=None, diagnostics=diagnostics, summary=summarize(rows), axes=list(axes),
                  groups=[dict(key=dict(zip(axes, key)), **summarize(value))
                          for key, value in sorted(buckets.items())])
    if private:
        result['rows'] = rows
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path, required=True)
    cutoff = parser.add_mutually_exclusive_group()
    cutoff.add_argument('--since', help='timezone-aware ISO timestamp or UTC date')
    cutoff.add_argument('--days', type=float)
    parser.add_argument('--by', default=','.join(DEFAULT_AXES))
    parser.add_argument('--private', action='store_true', help='expose selected raw IDs/labels')
    parser.add_argument('--json', type=Path, help='write report instead of stdout')
    args = parser.parse_args(argv)
    since = None
    if args.since:
        since = timestamp(args.since + 'T00:00:00Z' if len(args.since) == 10 else args.since)
        if since is None:
            parser.error('invalid or timezone-naive --since')
    if args.days is not None:
        if not math.isfinite(args.days) or args.days < 0:
            parser.error('--days must be finite and nonnegative')
        try:
            since = datetime.now(timezone.utc) - timedelta(days=args.days)
        except OverflowError:
            parser.error('--days cutoff is outside the supported datetime range')
    axes = tuple(args.by.split(','))
    if not axes or any(a not in AXES for a in axes) or len(set(axes)) != len(axes):
        parser.error('invalid --by axes')
    rows, diagnostics = collect(args.tree, since=since)
    result = report(rows, diagnostics, axes=axes, private=args.private)
    result['since'] = since.isoformat() if since else None
    text = json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False) + '\n'
    if args.json:
        args.json.write_text(text, encoding='utf-8')
    else:
        print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
