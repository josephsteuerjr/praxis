"""Read-only live host capability probe. Never records token/channel/page content.

Usage (bundled Python): tests/probe_loaded_reload.py path/to/.python-window-PID.json
An old host serves its in-memory bridge even after disk updates. A document
reload cannot upgrade that Python code. Exit 2 means restart/activation needed,
NOT permission to perform one. No keyboard input or navigation is sent.
"""
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen


def probe(control):
    config = json.loads(Path(control).read_text(encoding='utf-8'))
    origin = config['origin']
    parsed = urlsplit(origin)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path not in ('', '/'):
        raise ValueError('Expected local Python window origin')
    with urlopen(origin, timeout=5) as response:
        page = response.read(2_000_000)
    return {'pid': config['pid'], 'bytes': len(page), 'sha256': hashlib.sha256(page).hexdigest(),
            'injected': b'<head><script>window.DESK_CONFIG_OVERRIDE' in page,
            'dom_reload_handler': b"window.addEventListener('keydown'" in page,
            'safe_native_bridge': b"reloadCapability='safe-renderer-v2'" in page}


if __name__ == '__main__':
    facts = probe(sys.argv[1])
    print(json.dumps(facts, indent=2))
    sys.exit(0 if facts['safe_native_bridge'] else 2)
