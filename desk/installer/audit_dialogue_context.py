"""Read-only audit of persisted model inputs; emits structure/hashes, never text.

Receipts precede provider calls and are scrubbed by the runtime: this is not a
network capture. Nonterminal or changing runs are excluded from the audit.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def audit_run(folder: Path):
    manifest_bytes = (folder / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get('status') not in {'done', 'failed', 'cancelled'}:
        return None
    event_bytes = (folder / 'events.jsonl').read_bytes()
    events = [json.loads(line) for line in event_bytes.splitlines() if line.strip()]
    inputs, first, previous = [], None, None
    for event in events:
        if event.get('kind') != 'model_input':
            continue
        ref = event['result']
        path = (folder / ref['path']).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError('result outside run')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ref['sha256']:
            raise ValueError('result hash mismatch')
        request = json.loads(data)
        messages = request['messages']
        if first is None:
            first = messages
        inputs.append({
            'result_id': ref['result_id'], 'sha256': ref['sha256'],
            'messages': len(messages), 'roles': dict(Counter(m['role'] for m in messages)),
            'system_sha256': digest(request['system']),
            'initial_prefix_preserved': messages[:len(first)] == first,
            'previous_prefix_preserved': previous is None or messages[:len(previous)] == previous,
        })
        previous = messages
    if ((folder / 'manifest.json').read_bytes() != manifest_bytes
            or (folder / 'events.jsonl').read_bytes() != event_bytes):
        raise ValueError('run changed during audit')
    return {'run': folder.name, 'status': manifest['status'],
            'events_sha256': hashlib.sha256(event_bytes).hexdigest(),
            'event_counts': dict(Counter(e.get('kind') for e in events)), 'inputs': inputs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path)
    parser.add_argument('--limit', type=int, default=30, help='newest runs; 0 audits all')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    manifests = sorted(args.runs.glob('*/run-*/manifest.json'),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True)
    if args.limit:
        manifests = manifests[:args.limit]
    report = {'schema': 'helene.context-audit.v1', 'runs': [], 'excluded': []}
    for manifest in manifests:
        try:
            result = audit_run(manifest.parent)
            if result is not None:
                report['runs'].append(result)
            else:
                report['excluded'].append({'run': manifest.parent.name, 'reason': 'nonterminal'})
        except (OSError, ValueError, KeyError, TypeError):
            # Do not include exception text: malformed JSON can contain private input.
            report['excluded'].append({'run': manifest.parent.name, 'reason': 'unreadable or changed'})
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + '\n', encoding='utf-8')
    else:
        print(text)
    print(json.dumps({'runs': len(report['runs']), 'excluded': len(report['excluded']),
                      'inputs': sum(len(r['inputs']) for r in report['runs']),
                      'prefix_changes': sum(not i['previous_prefix_preserved']
                                            for r in report['runs'] for i in r['inputs'])}))


if __name__ == '__main__':
    main()
