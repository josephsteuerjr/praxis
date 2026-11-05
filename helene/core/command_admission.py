"""POSIX deferred exec gate. Parent EOF before admission never executes a command."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main(directory, descriptor):
    directory = Path(directory)
    request = json.loads((directory/'request.json').read_text(encoding='utf-8'))
    expected = request['nonce'].encode('ascii')
    (directory/'ready').write_text(request['nonce'], encoding='utf-8')
    received = b''
    with os.fdopen(int(descriptor), 'rb', buffering=0) as gate:
        while len(received) < len(expected):
            block = gate.read(len(expected) - len(received))
            if not block:
                break
            received += block
    if received != expected:
        receipt = {'nonce': request['nonce'], 'settled': True, 'stopped': True,
                   'exit_code': 127, 'note': 'Parent ended before command admission'}
        temporary = directory/'settled.tmp'
        temporary.write_text(json.dumps(receipt), encoding='utf-8')
        temporary.replace(directory/'settled.json')
        return 127
    command = request['command']
    args = [command] if isinstance(command, str) else command
    if request['shell']:
        args = [request.get('executable') or '/bin/sh', '-c', *args]
    executable = request.get('executable') or args[0]
    os.execvpe(executable, args, os.environ)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
