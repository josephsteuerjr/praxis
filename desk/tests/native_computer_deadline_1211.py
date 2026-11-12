"""Actual Body run/poll/stop and descendant cleanup, isolated from owner sessions.

Run outside the Windows inline runner (Body deliberately detaches supervisors).
The public hand and Body client are real; only HTTP transport is replaced with
Body's supported invoke-local entry so no installed bridge or owner token is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from unittest import mock

import psutil
from t_computer_deadline_1211 import public_hand, body_client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--body', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = output / 'body.json'
    config.write_text(json.dumps({'device_id': 'helene-270-deadline-fixture', 'token': uuid.uuid4().hex,
        'bridge_ws_url': 'ws://127.0.0.1:1/body', 'artifact_base_url': 'http://127.0.0.1:1',
        'state_dir': str(output / 'state'),
        'system_router_pipe': 'helene-270-unused-' + uuid.uuid4().hex,
        'interactive_router_pipe': 'helene-270-unused-' + uuid.uuid4().hex}))
    body = args.body.resolve()
    calls, started, outcomes = [], [], []

    def call(capability, payload, **options):
        operation = options.get('operation_id') or 'deadline-' + uuid.uuid4().hex
        # Windows detached supervisors can inherit pipe handles: communicate()
        # then waits for their EOF even though invoke-local returned immediately.
        # Files + waiting for the actual CLI process measure asynchronous start.
        stdout = output / ('call-' + str(len(calls)) + '.json')
        stderr = stdout.with_suffix('.stderr.log')
        beginning = time.monotonic()
        with stdout.open('wb') as out, stderr.open('wb') as err:
            proc = subprocess.run([str(body), 'invoke-local', '--config', str(config),
                '--capability', capability, '--args', json.dumps(payload), '--operation-id', operation],
                stdout=out, stderr=err, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        calls.append({'capability': capability, 'returncode': proc.returncode,
                      'elapsed': time.monotonic() - beginning})
        assert proc.returncode == 0, stderr.read_text(encoding='utf-8', errors='replace')
        value = json.loads(stdout.read_bytes())
        # invoke-local returns a protocol result envelope.
        if value.get('type') == 'result':
            value = {**value, **value.get('result', {})}
        if capability == 'process.start':
            started.append(value.get('operation_id', operation))
        return value

    def poll(operation):
        return json.loads(hand('poll', operation_id=operation))

    def terminal(operation):
        end = time.monotonic() + 20
        while time.monotonic() < end:
            row = poll(operation)
            if row.get('status') in ('succeeded', 'failed', 'cancelled', 'timed_out', 'in_doubt'):
                return row
            time.sleep(.1)
        raise AssertionError('operation did not settle: ' + operation)

    def owned_tree(kind):
        marker = output / (kind + '-pids.json')
        script = output / (kind + '.py')
        script.write_text('import subprocess,sys,time,os,json\nfrom pathlib import Path\n'
            'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\n'
            + 'Path(' + repr(str(marker)) + ').write_text(json.dumps([os.getpid(),p.pid]))\n'
            + 'time.sleep(60)\n')
        if os.name == 'nt':
            quote = lambda text: "'" + str(text).replace("'", "''") + "'"
            command = '& ' + quote(sys.executable) + ' ' + quote(script)
        else:
            import shlex
            command = shlex.join([sys.executable, str(script)])
        receipt = json.loads(hand('run', command=command, timeout_ms=5000 if kind == 'deadline' else 0))
        operation = receipt['operation_id']
        end = time.monotonic() + 4
        while not marker.exists() and time.monotonic() < end:
            time.sleep(.05)
        assert marker.exists(), receipt
        children = [psutil.Process(pid) for pid in json.loads(marker.read_text())]
        identities = [(process.pid, process.create_time()) for process in children]
        if kind == 'stop':
            assert poll(operation)['status'] in ('starting', 'running')
            cancelled = json.loads(hand('stop', operation_id=operation))
            assert cancelled['status'] == 'cancelling', cancelled
        final = terminal(operation)
        assert final['status'] == ('timed_out' if kind == 'deadline' else 'cancelled'), final
        assert final.get('job_note') is None, final
        gone, alive = psutil.wait_procs(children, timeout=5)
        assert not alive, [(process.pid, process.create_time()) for process in alive]
        result = final.get('result') or {}
        if kind == 'deadline':
            assert 4900 <= result['duration_ms'] < 10000, final
        outcomes.append({'case': kind, 'receipt': receipt, 'final': final,
                         'descendants': identities, 'descendants_gone': True})

    hand = public_hand()
    try:
        with mock.patch.object(body_client, 'call', call):
            owned_tree('deadline')
            owned_tree('stop')
            command = 'Write-Output deadline-fixture-success' if os.name == 'nt' else 'echo deadline-fixture-success'
            receipt = json.loads(hand('run', command=command))
            final = terminal(receipt['operation_id'])
            assert final['status'] == 'succeeded', final
            outcomes.append({'case': 'async-success', 'receipt': receipt, 'final': final})
        result = {'passed': True, 'body_sha256': hashlib.sha256(body.read_bytes()).hexdigest(),
                  'source': str(Path(__file__).resolve()), 'outcomes': outcomes, 'calls': calls}
        (output / 'PASS.json').write_text(json.dumps(result, indent=2))
        print('PASS: actual Body deadline, cancellation, async success and child-tree cleanup', flush=True)
    finally:
        for operation in started:
            try:
                call('process.cancel', {'operation_id': operation})
            except Exception:
                pass


if __name__ == '__main__':
    main()
