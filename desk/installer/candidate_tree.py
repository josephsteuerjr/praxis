"""Stage the exact published-payload inventory from the committed core + overlay.

Allows platform CI before a public release exists. Refuses missing or different
bytes and never reads a private live tree or its Git history.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def stage(destination, root=ROOT):
    destination = Path(destination)
    manifest = json.loads((root/'HELENE-SOURCE.json').read_text(encoding='utf8'))
    if manifest.get('tree_inventory_scope') != 'published_payload': raise ValueError('inclusive inventory required')
    files = manifest.get('frozen_tree_files')
    if not isinstance(files, dict) or not files: raise ValueError('missing frozen inventory')
    if destination.exists() and any(destination.iterdir()): raise ValueError('candidate output must be empty')
    staged = {}
    for relative, expected in files.items():
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or ':' in relative: raise ValueError('invalid inventory path')
        source = next((root/base/path for base in ('helene/core','praxis') if (root/base/path).is_file()), None)
        if relative == 'LICENSE':
            text = (root/'desk/installer/ЛИЦЕНЗИЯ.md').read_text(encoding='utf8').replace('\r\n','\n')
            content = ((text.split('\n---\n',1)[1].strip() if '\n---\n' in text else text) + '\n').encode('utf8')
        elif relative == 'NOTICE':
            content = (root/'desk/installer/NOTICE').read_bytes().replace(b'\r\n', b'\n')
        elif source:
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                content = content.replace(b'\r\n', b'\n')
        else: raise ValueError('missing source: ' + relative)
        if hashlib.sha256(content).hexdigest() != expected: raise ValueError('source differs from frozen inventory: ' + relative)
        staged[relative] = content
    destination.mkdir(parents=True, exist_ok=True)
    for relative, content in staged.items():
        path = destination/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    return {'version': manifest['version'], 'files':len(staged), 'frozen_tree': manifest['frozen_tree']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--out', required=True)
    print(json.dumps(stage(parser.parse_args().out)))
