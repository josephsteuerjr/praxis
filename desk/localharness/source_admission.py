"""Materialize real incoming source after a legacy updater's admission gate.

The shipped compatibility view is explicitly separate from pristine upstream B.
It admits custody only. No source merge or review is accepted by this step.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import zipfile
import uuid
from contextlib import nullcontext

from . import source_merge as merge
from .package_notice import read, write

FORMAT = 'helene-source-admission.v1'


def _runtime_bytecode(root, path):
    """Recognize only regular interpreter caches paired with allowed source.

    Python imports sitecustomize before the helper entry can suppress bytecode.
    Such caches never belong to the accepted source inventory and are retained
    with the previous view rather than promoted or deleted.
    """
    if path.is_dir() and path.name == '__pycache__':
        parent = path.parent
        return parent == root or merge.allowed(parent.relative_to(root).as_posix())
    if not path.is_file() or path.parent.name != '__pycache__':
        return False
    try:
        source = Path(importlib.util.source_from_cache(str(path)))
        relative = source.relative_to(root).as_posix()
        if source.suffix != '.py' or not merge.allowed(relative) or not source.is_file():
            return False
        with path.open('rb') as stream:
            return stream.read(4) == importlib.util.MAGIC_NUMBER
    except (OSError, ValueError):
        return False


def _inventory(root, *, allow_bytecode=True):
    """A delivery view is sealed; excluded profile/cache data must not vanish."""
    files = merge.inventory(root)
    for path in Path(root).rglob('*'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Linked source admission path')
        if allow_bytecode and _runtime_bytecode(Path(root), path):
            continue
        relative = path.relative_to(root).as_posix()
        if not merge.allowed(relative) or (path.is_file() and relative not in files):
            raise ValueError('Source admission contains undeclared data; refusing replacement: ' + relative)
    return files


def _upstream(program, declaration):
    protected = declaration.get('pristine')
    manifest = program / ('source-admission-upstream.json' if protected else 'code-upstream.json')
    archive = program / ('source-admission-upstream.zip' if protected else 'code-upstream.zip')
    if protected and hashlib.sha256(manifest.read_bytes()).hexdigest() != protected['manifest_sha256']:
        raise ValueError('Protected incoming source manifest changed')
    upstream = read(manifest)
    if (upstream.get('sha256') != declaration['upstream_sha256']
            or hashlib.sha256(archive.read_bytes()).hexdigest() != declaration['upstream_sha256']
            or read(program / 'python-app.json').get('version') != declaration['target_version']):
        raise ValueError('Incoming source admission identity changed')
    _verify_archive(archive, upstream['files'])
    if protected:
        # Older installed updaters seal their custody view during prepare().
        # Accept only that exact declared view, never an arbitrary replacement.
        current = read(program / 'code-upstream.json')
        current_archive = program / 'code-upstream.zip'
        current_sha = hashlib.sha256(current_archive.read_bytes()).hexdigest()
        if current_sha != declaration['upstream_sha256']:
            if current.get('files') != declaration['view_files'] or current.get('sha256') != current_sha:
                raise ValueError('Legacy source reseal differs from declared custody view')
            _verify_archive(current_archive, declaration['view_files'])
    return upstream, archive


def _verify_archive(archive, files):
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if len(names) != len(set(names)) or set(names) != set(files):
            raise ValueError('Incoming pristine source inventory differs')
        for name in names:
            if not merge.allowed(name):
                raise ValueError('Private or invalid incoming source path')
            if hashlib.sha256(source.read(name)).hexdigest() != files[name]['sha256']:
                raise ValueError('Incoming pristine source changed: ' + name)


def activate(program, *, lease_held=False):
    """Once-only, recoverable promotion; later authored tree edits are retained."""
    program = Path(program).resolve()
    declaration = read(program / 'source-admission.json')
    if not declaration:
        return False
    if declaration.get('format') != FORMAT:
        raise ValueError('Unknown source admission format')
    identity = merge.signature(declaration)
    state_path = program / 'source-admission-state.json'
    state = read(state_path)
    if state.get('identity') == identity and state.get('state') == 'ready':
        return False
    from python_migrate import installation_lock
    with (nullcontext() if lease_held else installation_lock(program, timeout_sec=1)):
        state = read(state_path)
        if state.get('identity') == identity and state.get('state') == 'ready':
            return False
        if state and state.get('identity') != identity:
            raise ValueError('Source admission journal belongs to another incoming release')
        upstream, archive = _upstream(program, declaration)
        baseline, target = declaration['view_files'], upstream['files']
        for name in baseline:
            if not merge.allowed(name):raise ValueError('Invalid admission view path')
        tree = program / 'tree'
        bucket = program.parent / ('.helene-source-admission-' + hashlib.sha256(str(program).encode()).hexdigest()[:20])
        if (bucket.resolve() != bucket or bucket.is_symlink()
                or (hasattr(bucket, 'is_junction') and bucket.is_junction())):
            raise ValueError('Source admission recovery leaves its program parent')
        bucket.mkdir(exist_ok=True)
        previous, staged = bucket / 'previous', bucket / 'next'
        for path in (tree, previous, staged):
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('Linked source admission path')
        current = _inventory(tree) if tree.exists() else None
        prior = _inventory(previous) if previous.exists() else None
        if current not in (baseline, target, None) or prior not in (baseline, None):
            raise ValueError('Admission view was authored or recovery contents changed; refusing replacement')
        if current is None and prior != baseline:
            raise ValueError('Source admission recovery is missing its verified previous tree')
        if current == target and current != baseline and not state:
            # A target tree before any durable intent is not proof of activation.
            raise ValueError('Incoming source was replaced without an admission journal')
        if not state:
            write(state_path, {'format': FORMAT, 'identity': identity, 'state': 'preparing'})
        if declaration.get('pristine'):
            # Both immutable inputs and the still-unmodified custody tree have
            # been checked before restoring legacy updater's derived files.
            for source,name in ((archive,'code-upstream.zip'),
                                (program/'source-admission-upstream.json','code-upstream.json')):
                restored = program / ('.'+name+'.restore-part')
                shutil.copyfile(source, restored)
                restored.replace(program / name)
        if current != target:
            if staged.exists():
                # Retain an interrupted extraction, including any later edit.
                if staged.resolve().parent != bucket:raise ValueError('Invalid source admission staging')
                staged.replace(bucket / ('retained-' + uuid.uuid4().hex))
            staged.mkdir()
            with zipfile.ZipFile(archive) as source:
                for name, entry in target.items():
                    path = staged / name; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(source.read(name))
                    if os.name != 'nt':path.chmod(0o755 if entry['mode'] == '100755' else 0o644)
            if _inventory(staged) != target:
                raise ValueError('Materialized incoming source differs from pristine upstream')
            write(state_path, {'format': FORMAT, 'identity': identity, 'state': 'prepared'})
            if tree.exists():
                if previous.exists():
                    raise ValueError('Both admission view and recovery tree exist; refusing ambiguous replacement')
                tree.replace(previous)
            staged.replace(tree)
        if _inventory(tree) != target:
            raise ValueError('Incoming source promotion is incomplete')
        # Derive program passports from verified B; source-inputs/app and the
        # immutable pristine ZIP remain unchanged. Cached extraction proofs for
        # these derived paths must not be reused by the overlay preparer.
        manifest = read(program / 'python-package-files.json')
        if manifest:
            files = {n: h for n, h in manifest['files'].items() if not n.startswith('tree/')}
            files.update({'tree/' + n: row['sha256'] for n, row in target.items()})
            manifest.update(files=files, source_admission={'identity': identity, 'materialized': True})
            write(program / 'python-package-files.json', manifest)
        for name in ('edition-inventory.json', 'selected-source.json'):
            passport = read(program / name)
            if passport:
                passport.update(files=target, current_tree_scope='materialized pristine incoming source')
                write(program / name, passport)
        write(state_path, {'format': FORMAT, 'identity': identity, 'state': 'ready'})
        # Previous is a byte-verified shipped view, never authored L. Keep any
        # uncertain/locked recovery rather than deleting a changed source tree.
        if previous.exists():
            try:
                if _inventory(previous, allow_bytecode=False) == baseline:shutil.rmtree(previous)
            except (OSError, ValueError):pass
        return True


def current_proofs(verified, program):
    """Invalidate only bytes derived by admission materialization."""
    if not verified:return verified
    program = Path(program).resolve()
    changed = {'python-package-files.json', 'edition-inventory.json', 'selected-source.json',
               'source-admission-state.json', 'code-upstream.json', 'code-upstream.zip'}
    result = {}
    for name, proof in verified.items():
        path = Path(name).resolve()
        if path.is_relative_to(program):
            relative = path.relative_to(program).as_posix()
            if relative.startswith('tree/') or relative in changed:continue
        result[name] = proof
    return result


def copy_exclusions(package, target):
    """A retry of this delivery must retain its already materialized source.

    The packaged admission view is a one-time custody view. Copying it again
    after activation would replace B/self-edits while retaining the ready marker.
    Preserve the derived passports with the source they describe.
    """
    declaration = read(Path(package) / 'source-admission.json')
    if not declaration:
        return set()
    state = read(Path(target) / 'source-admission-state.json')
    if state.get('state') == 'ready' and state.get('identity') == merge.signature(declaration):
        return {'tree', 'python-package-files.json', 'edition-inventory.json',
                'selected-source.json', 'source-admission-state.json'}
    return set()
