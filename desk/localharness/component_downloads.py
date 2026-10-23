"""Pinned optional downloads shared by the wizard, updater and late installs.

The small manifest is bundled in the verified main ZIP; heavy payloads may live
under an older immutable release. Cache bytes and installed payload are separate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import urllib.parse
import uuid
import zipfile

FORMAT = 'helene-component-downloads.v1'
MANIFEST = 'component-downloads.json'
EXTENSIONS = {'toolchain', 'xtts', 'whisper'}
MAX_MANIFEST = 65536


def validate(value):
    if not isinstance(value, dict) or value.get('format') != FORMAT:
        raise ValueError('Unsupported component download manifest')
    groups, artifacts = value.get('extensions'), value.get('artifacts')
    if not isinstance(groups, dict) or set(groups) != EXTENSIONS or not isinstance(artifacts, dict):
        raise ValueError('Incomplete component download manifest')
    names = set()
    for key, row in artifacts.items():
        if not isinstance(row, dict) or not isinstance(key, str):
            raise ValueError('Invalid component artifact')
        name, sha, size = row.get('name'), row.get('sha256'), row.get('size')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.zip', name) or name in names:
            raise ValueError('Invalid or duplicate component archive name')
        names.add(name)
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise ValueError('Component SHA256 is required')
        if type(size) is not int or not 0 < size <= 8_000_000_000:
            raise ValueError('Invalid component size')
        url = urllib.parse.urlsplit(str(row.get('url', '')))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError('Component requires an absolute credential-free HTTPS URL')
        if Path(urllib.parse.unquote(url.path)).name != name:
            raise ValueError('Component URL basename differs')
    for group, ids in groups.items():
        if not isinstance(ids, list) or not ids or any(not isinstance(key, str) or key not in artifacts for key in ids):
            raise ValueError('Unknown artifact in component selection')
    return value


def parse(raw):
    if len(raw) > MAX_MANIFEST:
        raise ValueError('Component manifest too large')
    return validate(json.loads(raw))


def load(program):
    return parse((Path(program) / 'app' / MANIFEST).read_bytes())


def incoming(archive):
    with zipfile.ZipFile(archive) as reader:
        try:
            entry = reader.getinfo('Helene/app/' + MANIFEST)
        except KeyError:
            # Explicit legacy support only: new-format packages must not hide
            # a missing manifest behind same-tag URL guessing.
            app = json.loads(reader.read('Helene/python-app.json'))
            version = tuple(int(x) for x in app['version'].split('.')[:3])
            if version >= (1, 9, 3):
                raise ValueError('Incoming package lacks component download manifest')
            return None
        if entry.file_size > MAX_MANIFEST:
            raise ValueError('Component manifest too large')
        return parse(reader.read(entry))


def select(manifest, extensions):
    validate(manifest)
    if not isinstance(extensions, list) or any(name not in EXTENSIONS for name in extensions):
        raise ValueError('Unknown optional component selection')
    ids = dict.fromkeys(key for name in extensions for key in manifest['extensions'][name])
    return [dict(manifest['artifacts'][key], id=key) for key in ids]


def cache_name(row):
    return row['sha256'][:12] + '-' + row['name']


def cache_dir(program):
    path = Path(program) / 'downloads'
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch(program, manifest, extensions, progress=None):
    from .python_updates import _download
    rows = []
    for artifact in select(manifest, extensions):
        target = cache_dir(program) / cache_name(artifact)
        if target.exists():
            with target.open('rb') as source:
                valid=target.stat().st_size==artifact['size'] and hashlib.file_digest(source,'sha256').hexdigest()==artifact['sha256']
            if not valid:
                # Keep the bad input for diagnosis but let an explicit retry heal
                # the cache rather than pinning every future install to it.
                target.rename(target.with_name(target.name+'.invalid-'+uuid.uuid4().hex))
        got = _download(artifact['url'], target, artifact['sha256'], progress)
        if got['bytes'] != artifact['size']:
            raise ValueError('Downloaded component size differs; file retained: ' + str(target))
        rows.append(dict(got, artifact=artifact['id'], name=artifact['name']))
    return rows


def preserve_cache(previous, current):
    """Copy retained archives between program folders without overwriting bytes."""
    source, target = Path(previous) / 'downloads', Path(current) / 'downloads'
    if not source.is_dir() or source.resolve() == target.resolve():
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.is_symlink() or not item.is_file() or item.suffix not in {'.zip', '.json', '.sha256'}:
            continue
        destination = target / item.name
        if destination.exists():
            with item.open('rb') as left, destination.open('rb') as right:
                if hashlib.file_digest(left, 'sha256').digest() != hashlib.file_digest(right, 'sha256').digest():
                    raise ValueError('Different cached archives retained in both folders: ' + item.name)
            continue
        temporary = target / ('.' + uuid.uuid4().hex + '.copy')
        try:
            shutil.copyfile(item, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def install(program, extensions, progress=None, *, tree=None):
    """Prepare exact payloads before overlay; preserve archive cache on failure."""
    import tempfile
    from .python_updates import _extract
    program = Path(program)
    if 'xtts' in extensions and tree is None:
        raise ValueError('Select an agent tree for XTTS installation')
    manifest = load(program)
    rows = fetch(program, manifest, extensions, progress)
    with tempfile.TemporaryDirectory(prefix='.component-stage-', dir=program) as temporary:
        stage = Path(temporary)
        for row in rows:
            _extract(row['path'], stage, progress)
        payload = stage / 'Helene'
        # These public archives carry only optional component/voice trees, never
        # configuration or agent code. Validate the whole layout before writes.
        for entry in payload.iterdir():
            if entry.name not in {'components', 'voice'} or not entry.is_dir():
                raise ValueError('Unexpected optional payload root: ' + entry.name)
        from .python_source_updater import apply_files, rollback
        recovery=program/('.component-recovery-'+uuid.uuid4().hex)
        apply_files(payload,program.resolve(),recovery)
        try:
            if 'xtts' in extensions:
                from .xtts_install import install as install_xtts
                install_xtts(Path(tree),program=program)
        except BaseException:
            rollback(program,recovery)
            raise
    return {'state': 'installed', 'extensions': extensions, 'archives': rows,
            'cache': str(cache_dir(program)), 'restart_required': True}


def status(program):
    """Passive optional capability list; no downloads or gigabyte hashing."""
    from .python_updates import installed_extensions
    try:
        manifest=load(program)
        installed=installed_extensions(program)
        return [{'id':name,'ready':True,'installed':name in installed,
                 'download_bytes':sum(row['size'] for row in select(manifest,[name]))}
                for name in ('toolchain','xtts','whisper')]
    except (OSError,ValueError,KeyError) as error:
        return [{'id':name,'ready':False,'installed':False,'error':str(error)} for name in ('toolchain','xtts','whisper')]


def prepare(program, extensions, *, offline=None, progress=None):
    """Wizard entry after its main ZIP was verified; use the very same parser."""
    from .python_updates import _extract, _download
    program=Path(program).resolve()
    manifest=load(program)
    rows=[]
    for artifact in select(manifest,extensions):
        target=cache_dir(program)/cache_name(artifact)
        if offline is not None:
            source=Path(offline)/artifact['name']
            with source.open('rb') as stream:
                if source.stat().st_size!=artifact['size'] or hashlib.file_digest(stream,'sha256').hexdigest()!=artifact['sha256']:
                    raise ValueError('Offline component differs: '+artifact['name'])
            if not target.exists():shutil.copyfile(source,target)
        rows.append(artifact)
    if offline is None:fetch(program,manifest,extensions,progress)
    for artifact in rows:
        archive=cache_dir(program)/cache_name(artifact)
        with archive.open('rb') as source:
            if archive.stat().st_size!=artifact['size'] or hashlib.file_digest(source,'sha256').hexdigest()!=artifact['sha256']:
                raise ValueError('Cached component changed: '+artifact['name'])
        _extract(archive,program.parent,progress)
    return {'state':'prepared','extensions':extensions,'cache':str(cache_dir(program))}


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepare',type=Path,required=True)
    parser.add_argument('--extensions',default='')
    parser.add_argument('--offline',type=Path)
    args=parser.parse_args()
    def progress(value):print(json.dumps(value),flush=True)
    print(json.dumps(prepare(args.prepare,[s for s in args.extensions.split(',') if s],offline=args.offline,progress=progress)),flush=True)


if __name__=='__main__':main()
