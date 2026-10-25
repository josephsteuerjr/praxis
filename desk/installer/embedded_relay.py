"""Pin the embedded Hélène relay independently of the universal CLI checkout.

The descriptor and Linux recipe belong to the integration HEAD. The supplied
hotfix source belongs to its own commit. Neither claims an offline toolchain.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import re
import shutil
import tempfile

import relay_src
import vnext_candidate

DESCRIPTOR = 'desk/installer/embedded-relay.json'
RECIPE = 'desk/installer/embedded-relay.Dockerfile'
KIND = 'vnext_embedded_relay'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _authority(repo, head):
    if vnext_candidate._git(repo, 'rev-parse', 'HEAD').decode().strip() != head:
        raise ValueError('embedded relay integration HEAD moved')
    blobs = {}
    for name in (DESCRIPTOR, RECIPE):
        data = vnext_candidate._git(repo, 'show', f'{head}:{name}')
        if (repo / name).is_symlink() or (repo / name).read_bytes() != data:
            raise ValueError('embedded relay integration source moved: ' + name)
        blobs[name] = data
    note = json.loads(blobs[DESCRIPTOR])
    files = note.get('source_files')
    if (note.get('product') != 'embedded-helene-relay' or not isinstance(files, dict)
            or re.fullmatch(r'[0-9a-f]{40}', str(note.get('source_commit', ''))) is None
            or not {'Cargo.toml', 'Cargo.lock', 'LICENSE', 'NOTICE'}.issubset(files)):
        raise ValueError('invalid embedded relay descriptor')
    for name, digest in files.items():
        if (not isinstance(name, str) or '\\' in name or ':' in name
                or name.startswith('/') or '..' in name.split('/')
                or name == 'Dockerfile' or not isinstance(digest, str)
                or re.fullmatch(r'[0-9a-f]{64}', digest) is None):
            raise ValueError('invalid embedded relay source row')
    expected = {**files, 'Dockerfile': _sha(blobs[RECIPE])}
    identity = {'kind': KIND, 'head': head, 'product': note['product'],
                'source_commit': note['source_commit'],
                'descriptor_sha256': _sha(blobs[DESCRIPTOR]),
                'recipe_sha256': _sha(blobs[RECIPE]),
                'source_files_sha256': relay_src.relay_rebuild_kit._digest(files)}
    return expected, identity, blobs[RECIPE]


def verify(repo: Path, head: str, staged: Path) -> dict:
    expected, identity, _ = _authority(repo, head)
    actual = relay_src.relay_rebuild_kit.inventory(staged, source=True, private_mirror=True)
    if actual != expected:
        raise ValueError('embedded relay staged inventory differs from descriptor/recipe')
    digest, count = relay_src.build_inputs(staged)
    legacy, legacy_files = relay_src.digest(staged)
    identity.update(input_digest_kind=relay_src.INPUT_DIGEST_KIND,
                    input_sha256=digest, input_files=count)
    stamp = relay_src.stamp(staged)
    if stamp != {**identity, 'digest': legacy, 'files': len(legacy_files)}:
        raise ValueError('embedded relay source receipt differs from selected bytes')
    return identity


def stage(repo: Path, head: str, source: Path, destination: Path) -> dict:
    repo = Path(repo).resolve()
    source = Path(os.path.abspath(source))
    destination = Path(destination).resolve()
    if destination.exists() or destination == repo or destination.is_relative_to(repo):
        raise ValueError('embedded relay destination occupied or inside checkout')
    expected, identity, recipe = _authority(repo, head)
    supplied = {name: digest for name, digest in expected.items() if name != 'Dockerfile'}
    inventory = relay_src.relay_rebuild_kit.inventory(source, source=True, private_mirror=True)
    if inventory != supplied:
        raise ValueError('embedded relay input differs from exact hotfix source map')
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.embedded-relay-', dir=destination.parent))
    try:
        for name in supplied:
            content = (source / name).read_bytes()
            if _sha(content) != supplied[name]:
                raise ValueError('embedded relay source moved during capture: ' + name)
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (work / 'Dockerfile').write_bytes(recipe)
        digest, count = relay_src.build_inputs(work)
        legacy, files = relay_src.digest(work)
        identity.update(input_digest_kind=relay_src.INPUT_DIGEST_KIND,
                        input_sha256=digest, input_files=count)
        (work / relay_src.STAMP).write_text(json.dumps({**identity, 'digest': legacy,
            'files': len(files)}, sort_keys=True) + '\n', encoding='utf-8')
        verify(repo, head, work)
        if destination.exists():
            raise ValueError('embedded relay destination appeared')
        os.rename(work, destination)
        return verify(repo, head, destination)
    finally:
        if work.exists():
            shutil.rmtree(work)
