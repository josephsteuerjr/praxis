"""Persistent, content-addressed voice dependencies; release/agent pin files.

Published objects are never updated in place or garbage-collected here. Old
programs and running processes retain their names, bytes and rollback pins.
Only legacy import hashes/copies payloads; reuse reads a small pin and receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import uuid

from . import speech_protocol as ipc

FORMAT = 'helene-component-pins.v1'
PINS = '.helene-component-pins.json'
ROOTS = ('components/xtts', 'components/whisper', 'voice', 'voice-xtts')
MODELS = ('xtts', 'whisper')


def _plain(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('Linked component-store path')
    return path


def root():
    # Deliberate install-independent home, including for portable releases.
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Helene'
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support/Helene'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'helene'
    return _plain(base / 'component-store-v1')


def pin_file(owner):
    selected = os.environ.get('HELENE_COMPONENT_PROGRAM')
    if selected and Path(owner).absolute() == Path(selected).absolute():
        return _plain(Path(selected) / 'component-state' / PINS)
    return _plain(Path(owner) / PINS)


def _pins(owner):
    file = pin_file(owner)
    if not file.exists(): return {'format': FORMAT, 'pins': {}}
    value = ipc.read(file)
    if value.get('format') != FORMAT or not isinstance(value.get('pins'), dict):
        raise ValueError('Invalid component pins')
    return value


def _reference(ref):
    if not isinstance(ref, dict) or not isinstance(ref.get('identity'), str) or len(ref['identity']) != 64:
        raise ValueError('Invalid component reference')
    if any(c not in '0123456789abcdef' for c in ref['identity']):
        raise ValueError('Invalid component identity')
    store = _plain(Path(ref['store']))
    selected = _plain(store / 'objects' / ref['identity'] / 'payload')
    receipt = ipc.read(selected.parent / 'receipt.json')
    if (receipt.get('identity') != ref['identity'] or receipt.get('compatibility') != ref.get('compatibility')
            or not selected.is_dir()):
        raise ValueError('Pinned component unavailable')
    return selected


def export_references(owner):
    value = _pins(owner)
    for name, ref in value['pins'].items():
        if name not in ROOTS and name not in ('models/xtts', 'models/whisper'):
            raise ValueError('Unknown pinned component')
        _reference(ref)
    return value


def path(program, relative):
    relative = str(relative)
    if relative not in ROOTS: raise ValueError('Unknown component root')
    ref = _pins(program)['pins'].get(relative)
    if not ref:return Path(program)/relative
    expected=compatibility(program);actual=ref.get('compatibility') or {}
    if (actual.get('target')!=expected['target']
            or (actual.get('distribution') and expected['distribution'] and actual['distribution']!=expected['distribution'])
            or (relative in ('voice','components/whisper') and actual.get('abi')!=expected['abi'])):
        raise ValueError('Pinned component is incompatible with this runtime')
    return _reference(ref)


def model_path(tree, name):
    if name not in MODELS: raise ValueError('Unknown voice model')
    ref = _pins(tree)['pins'].get('models/' + name)
    return _reference(ref) if ref else Path(tree) / 'models' / name


def reference_path(tree, value, *, program=None):
    selected=Path(value).expanduser()
    if selected.is_absolute():
        if not selected.is_relative_to(Path(tree).absolute()):return selected
        selected=selected.relative_to(Path(tree).absolute())
    if len(selected.parts)>=3 and selected.parts[0]=='models' and selected.parts[1] in MODELS:
        return model_path(tree,selected.parts[1]).joinpath(*selected.parts[2:])
    candidate=Path(tree)/selected
    # Accepted legacy settings could store a programme-relative WAV. Current
    # agent-relative/model references take priority; only a migration supplies
    # the exact former config directory for this bounded compatibility path.
    if program is not None and not candidate.is_file() and (Path(program)/selected).is_file():
        return Path(program)/selected
    return candidate


def compatibility(program=None):
    from .python_platform import runtime_target
    app = ipc.read(Path(program) / 'python-app.json') if program else {}
    distribution = app.get('distribution') or ''
    if not distribution and sys.platform.startswith('linux'):
        import platform
        try: distribution = platform.freedesktop_os_release().get('ID', '')
        except OSError: pass
    return {'target': app.get('target') or runtime_target(),
            'distribution': distribution, 'abi': sys.implementation.cache_tag}


def _inventory(source):
    rows = {}
    for directory, dirs, files in os.walk(source, followlinks=False):
        base = Path(directory)
        for name in dirs:
            item = base / name
            if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
                raise ValueError('Linked component directory')
        for name in files:
            item = base / name; resolved = item.resolve()
            # HF snapshot files may be internal relative links to immutable blobs.
            if (not resolved.is_relative_to(source.resolve()) or not resolved.is_file()
                    or getattr(item, 'is_junction', lambda: False)()):
                raise ValueError('Component file leaves its payload')
            with item.open('rb') as stream:
                rows[item.relative_to(source).as_posix()] = [item.stat().st_size,
                    hashlib.file_digest(stream, 'sha256').hexdigest(),
                    (stat.S_IMODE(item.stat().st_mode) & 0o111) if os.name == 'posix' else 0]
    if not rows: raise ValueError('Empty component')
    return rows


def import_directory(source, *, program=None, store=None):
    """Import once, preserving source names; return reference + actual copy bytes.

    Same-volume hardlinks are solely the one-time import optimization. All
    subsequent consumers resolve the persistent directory without per-file work.
    """
    source = _plain(source); store = _plain(store or root())
    if not source.is_dir(): raise FileNotFoundError(source)
    if store.is_relative_to(source): raise ValueError('Component store overlaps source')
    metadata = {'compatibility': compatibility(program), 'files': _inventory(source)}
    installed=ipc.read(source/'installed.json')
    recorded=installed.get('python')
    if isinstance(recorded,str):
        parts=recorded.split('.')
        if len(parts)>=2 and all(v.isdigit() for v in parts[:2]):
            metadata['compatibility']['abi']='cpython-'+''.join(parts[:2])
    identity = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    ref = {'store': str(store), 'identity': identity, 'compatibility': metadata['compatibility']}
    objects = _plain(store / 'objects'); objects.mkdir(parents=True, exist_ok=True)
    blobs=_plain(store/'blobs');blobs.mkdir(exist_ok=True)
    destination = objects / identity
    copied = 0
    with ipc._queue_lock(store):
        if destination.exists():
            _reference(ref)
            # Explicit install/import verifies reuse. A legacy hardlinked name
            # may have been edited since first import. Repair publishes another
            # object; never overwrite the one an active process may still hold.
            valid=True
            for relative, (size, expected, executable) in metadata['files'].items():
                candidate=destination/'payload'/relative
                if (not candidate.is_file() or candidate.stat().st_size!=size
                        or (os.name == 'posix' and candidate.stat().st_mode & 0o111 != executable)):
                    valid=False;break
                with candidate.open('rb') as stream:
                    if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:
                        valid=False;break
            if valid:return ref, {'copied_bytes': 0, 'reused': True}
            metadata.update(repair_of=identity,instance=uuid.uuid4().hex)
            identity=hashlib.sha256(json.dumps(metadata,sort_keys=True).encode()).hexdigest()
            ref=dict(ref,identity=identity);destination=objects/identity
        stage = objects / ('.import-' + uuid.uuid4().hex)
        payload = stage / 'payload'; payload.mkdir(parents=True)
        try:
            for relative, (size, expected, executable) in metadata['files'].items():
                src = source / relative; dst = payload / relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                # Different agents may have different installed.json/sample
                # views of identical weights. Deduplicate the actual bytes,
                # independently of that small per-agent object identity.
                blob=blobs/(expected + ('.x' + format(executable,'03o') if executable else ''))
                if blob.exists():
                    with blob.open('rb') as stream:
                        valid_blob=(blob.stat().st_size==size and hashlib.file_digest(stream,'sha256').hexdigest()==expected
                            and (os.name!='posix' or blob.stat().st_mode & 0o111 == executable))
                    if not valid_blob:blob=blobs/(expected+'-'+uuid.uuid4().hex)
                if not blob.exists():
                    try:os.link(src.resolve(),blob)
                    except OSError:shutil.copy2(src,blob);copied+=size
                try:os.link(blob,dst)
                except OSError:shutil.copy2(blob,dst);copied+=size
                # Verify the imported name before publishing its immutable identity.
                with dst.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                        raise ValueError('Component changed during import')
            ipc.write(stage / 'receipt.json', dict(metadata, identity=identity))
            stage.replace(destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    return ref, {'copied_bytes': copied, 'reused': False}


def pin(owner, relative, ref):
    if relative not in ROOTS and relative not in ('models/xtts', 'models/whisper'):
        raise ValueError('Unknown pin')
    _reference(ref)
    owner = _plain(owner); owner.mkdir(parents=True, exist_ok=True)
    selected = pin_file(owner)
    selected.parent.mkdir(parents=True, exist_ok=True)
    with ipc._queue_lock(selected.parent):
        value = _pins(owner)
        previous = value['pins'].get(relative)
        if previous and previous != ref:
            # Keep rollback dependency identities even after an explicit install.
            value.setdefault('retained', []).append({'root': relative, 'reference': previous})
        value['pins'][relative] = ref
        ipc.write(pin_file(owner), value)


def preserve(previous, current):
    results = []
    for relative in ROOTS:
        ref = _pins(previous)['pins'].get(relative)
        if ref:
            _reference(ref); result = {'copied_bytes': 0, 'reused': True}
        else:
            source = Path(previous) / relative
            if not source.is_dir(): continue
            ref, result = import_directory(source, program=previous)
            pin(previous, relative, ref)
        # Never overwrite a new release's explicitly installed component pin.
        if relative not in _pins(current)['pins'] and not (Path(current) / relative).exists():
            pin(current, relative, ref)
        results.append(dict(result, root=relative, identity=ref['identity']))
    return results


def prepare_update(previous, current):
    """Stage pins without changing the running programme's selection.

    Used by update_profile before overlay preparation. Published candidate
    component pins win, then incoming physical payload, then previous pins.
    The overlay journals the small marker itself for rollback.
    """
    from .component_downloads import preserve_cache
    preserve_cache(previous,current)
    report=[]
    for relative in ROOTS:
        if relative in _pins(current)['pins']:
            _reference(_pins(current)['pins'][relative]);continue
        incoming=Path(current)/relative
        ref=_pins(previous)['pins'].get(relative)
        if incoming.is_dir():ref,result=import_directory(incoming,program=current)
        elif ref:
            _reference(ref);result={'copied_bytes':0,'reused':True}
        elif (Path(previous)/relative).is_dir():
            ref,result=import_directory(Path(previous)/relative,program=previous)
        else:continue
        pin(current,relative,ref);report.append(dict(result,root=relative))
    return report


def prepare_package(package, current, extensions):
    """Pin only explicitly selected optional payloads for a fresh programme."""
    selected=set(extensions);published=[]
    for relative in ROOTS:
        extension='whisper' if relative in ('voice','components/whisper') else 'xtts'
        if extension not in selected:continue
        ref=_pins(package)['pins'].get(relative)
        if ref:_reference(ref)
        elif (Path(package)/relative).is_dir():ref,_=import_directory(Path(package)/relative,program=package)
        else:continue
        pin(current,relative,ref);published.append(relative)
    return set(published)


def preserve_models(previous, current):
    results = []
    for name in MODELS:
        relative = 'models/' + name
        ref = _pins(previous)['pins'].get(relative)
        if ref:
            _reference(ref); result = {'copied_bytes': 0, 'reused': True}
        else:
            source = Path(previous) / relative
            if not source.is_dir() or not any(source.iterdir()): continue
            ref, result = import_directory(source)
            pin(previous, relative, ref)
        pin(current, relative, ref)
        results.append(dict(result, root=relative, identity=ref['identity']))
    return results


def cache():
    destination = _plain(root() / 'archives')
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def work_area(kind, identity):
    if kind not in ('voice','whisper') or not isinstance(identity,str) or not identity or any(c not in '0123456789abcdef' for c in identity):
        raise ValueError('Invalid component work area')
    destination=_plain(root()/'downloads'/kind/identity)
    destination.mkdir(parents=True,exist_ok=True)
    return destination
