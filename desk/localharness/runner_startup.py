"""Startup evidence is separate from the inbox reader's readiness heartbeat."""
from pathlib import Path
import os
import time

from .package_notice import read, write


def phase(tree, name):
    write(Path(tree) / 'memory/.state/runner-startup.json',
          {'pid': os.getpid(), 'phase': name, 'at': time.time()})


def observe(tree, pid):
    if not pid:
        return {'state': 'stopped', 'phase': '', 'elapsed': 0}
    reader = read(Path(tree) / 'memory/.control/desk_inbox/.reader.json')
    stamp = reader.get('at')
    if (reader.get('pid') == pid and type(stamp) in (int, float)
            and 0 <= time.time() - stamp < 15):
        return {'state': 'ready', 'phase': 'ready', 'elapsed': 0}
    startup = read(Path(tree) / 'memory/.state/runner-startup.json')
    name = startup.get('phase') if startup.get('pid') == pid else 'launch'
    if name not in ('launch', 'layout', 'code', 'sandbox', 'services', 'recovery', 'ready'):
        name = 'launch'
    stamp = startup.get('at') if startup.get('pid') == pid else None
    elapsed = max(0, int(time.time() - stamp)) if type(stamp) in (int, float) else 0
    # A stale ready reader is a loss of contact, never a fresh startup proof.
    return {'state': 'unconfirmed' if name == 'ready' else 'starting',
            'phase': name, 'elapsed': elapsed}
