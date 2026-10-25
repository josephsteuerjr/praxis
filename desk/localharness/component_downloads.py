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
import sys
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = 'localharness'

FORMAT = 'helene-component-downloads.v1'
MANIFEST = 'component-downloads.json'
EXTENSIONS = {'xtts', 'whisper'}
LEGACY_EXTENSIONS = EXTENSIONS | {'toolchain'}
MAX_MANIFEST = 65536


def validate(value):
    if not isinstance(value, dict) or value.get('format') != FORMAT:
        raise ValueError('Unsupported component download manifest')
    groups, artifacts = value.get('extensions'), value.get('artifacts')
    if not isinstance(groups, dict) or not EXTENSIONS <= set(groups) <= LEGACY_EXTENSIONS or not isinstance(artifacts, dict):
        raise ValueError('Incomplete component download manifest')
    unavailable=value.get('unavailable',{})
    if (not isinstance(unavailable,dict) or any(k not in LEGACY_EXTENSIONS or not isinstance(v,str) or not v.strip() for k,v in unavailable.items())):
        raise ValueError('Invalid unavailable-component reasons')
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
        if row.get('offline_only') is True:
            if row.get('url'):raise ValueError('Offline-only component must not claim a download URL')
            continue
        if 'offline_only' in row and row['offline_only'] is not False:
            raise ValueError('Invalid offline-only component flag')
        url = urllib.parse.urlsplit(str(row.get('url', '')))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError('Component requires an absolute credential-free HTTPS URL')
        if Path(urllib.parse.unquote(url.path)).name != name:
            raise ValueError('Component URL basename differs')
    for group, ids in groups.items():
        if (not isinstance(ids,list) or (not ids and group not in unavailable) or
                (ids and group in unavailable) or any(not isinstance(key,str) or key not in artifacts for key in ids)):
            raise ValueError('Unknown artifact in component selection')
    return value


def parse(raw):
    if len(raw) > MAX_MANIFEST:
        raise ValueError('Component manifest too large')
    return validate(json.loads(raw))


def for_target(value, selected):
    # Original v1 maps described Windows bundles. Other runtimes must carry
    # an explicit target; a model-data archive travels inside its runtime group.
    if __package__:
        from .python_platform import TARGETS
    else:
        from python_platform import TARGETS
    target=value.get('target','windows-x86_64')
    if target not in TARGETS or target!=selected:
        raise ValueError('Optional component map is for another OS/architecture')
    return value


def load(program):
    program=Path(program)
    if __package__:
        from .python_platform import target
    else:
        from python_platform import target
    selected=target(program) if (program/'python-app.json').is_file() else 'windows-x86_64'
    return for_target(parse((program / 'app' / MANIFEST).read_bytes()),selected)


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
        application=json.loads(reader.read('Helene/python-app.json'))
        return for_target(parse(reader.read(entry)),application.get('target','windows-x86_64'))


def select(manifest, extensions):
    validate(manifest)
    if not isinstance(extensions, list) or any(name not in EXTENSIONS for name in extensions):
        raise ValueError('Unknown optional component selection')
    for name in extensions:
        if name in manifest.get('unavailable',{}):
            raise ValueError(name+': '+manifest['unavailable'][name])
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
        got = None
        if target.exists():
            with target.open('rb') as source:
                valid=target.stat().st_size==artifact['size'] and hashlib.file_digest(source,'sha256').hexdigest()==artifact['sha256']
            if not valid:
                # Keep the bad input for diagnosis but let an explicit retry heal
                # the cache rather than pinning every future install to it.
                target.rename(target.with_name(target.name+'.invalid-'+uuid.uuid4().hex))
            else:
                got={'path':str(target),'bytes':artifact['size'],'sha256':artifact['sha256'],'sha_ok':True,'reused':True}
                if progress:progress(dict(got,stage='downloading',asset=artifact['name'],total_bytes=artifact['size']))
        if got is not None:
            pass  # This operation has already verified the cached archive once.
        elif artifact.get('offline_only') is True:
            if not target.is_file():
                raise FileNotFoundError('Offline component required: '+artifact['name']+'; prepare the supplied extension pack with --offline first')
            got={'path':str(target),'bytes':target.stat().st_size,'sha256':artifact['sha256'],'reused':True}
        else:
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


def changed_extensions(program, manifest, extensions):
    """Normal updates keep installed payload when its pinned identity is equal.

    This is preservation, not an integrity/repair claim. Explicit component
    installation still checks the archives and reinstalls their exact bytes.
    """
    from .python_updates import installed_extensions
    try:previous=load(program)
    except (OSError,ValueError,KeyError):return list(extensions)
    installed=set(installed_extensions(program))
    def identity(value,name):
        return sorted((row['name'],row['sha256'],row['size']) for row in select(value,[name]))
    changed=[]
    for name in extensions:
        try:same=identity(previous,name)==identity(manifest,name)
        except ValueError:same=False
        if name not in installed or not same:changed.append(name)
    return changed


def preserve_installed(previous, current):
    """First migration retains optional payloads without fetching them again."""
    from .parallel_files import copytree
    previous,current=Path(previous),Path(current)
    from .standalone_relay import preserve
    preserve(previous,current)
    for relative in ('components/toolchain','components/xtts','components/whisper','voice','voice-xtts'):
        source=previous/relative;target=current/relative
        if source.is_dir() and not target.exists():
            copytree(source,target,symlinks=False)


def _overlay(program, rows, progress=None, *, xtts_tree=None):
    """Apply validated optional roots to the exact program, regardless of its name."""
    if not rows:
        return  # Base-only migration deliberately selects no optional payload.
    import tempfile
    from .python_updates import _extract
    with tempfile.TemporaryDirectory(prefix='.component-stage-', dir=program) as temporary:
        stage = Path(temporary)
        verified = {}
        for row in rows:
            verified.update(_extract(row['path'], stage, progress) or {})
        payload = stage / 'Helene'
        # These public archives carry only optional component/voice trees, never
        # configuration or agent code. Validate the whole layout before writes.
        for entry in payload.iterdir():
            if entry.name not in {'components', 'voice'} or not entry.is_dir():
                raise ValueError('Unexpected optional payload root: ' + entry.name)
        from .python_source_updater import apply_files, rollback
        recovery=program/('.component-recovery-'+uuid.uuid4().hex)
        # The stage lives on this same volume. Move each fully extracted file
        # into place after journalling preimages instead of copying GB again.
        apply_files(payload,program.resolve(),recovery,consume=True,verified=verified)
        try:
            if xtts_tree is not None:
                from .xtts_install import install as install_xtts
                install_xtts(Path(xtts_tree),program=program)
        except BaseException:
            rollback(program,recovery)
            raise


def install(program, extensions, progress=None, *, tree=None):
    """Prepare exact payloads before overlay; preserve archive cache on failure."""
    program = Path(program).resolve()
    if 'xtts' in extensions and tree is None:
        raise ValueError('Select an agent tree for XTTS installation')
    rows = fetch(program, load(program), extensions, progress)
    _overlay(program, rows, progress, xtts_tree=tree if 'xtts' in extensions else None)
    return {'state': 'installed', 'extensions': extensions, 'archives': rows,
            'cache': str(cache_dir(program)), 'restart_required': True}


def status(program):
    """Passive optional capability list; no downloads or gigabyte hashing."""
    from .python_updates import installed_extensions
    try:
        manifest=load(program)
        installed=installed_extensions(program)
        return [({'id':name,'ready':False,'installed':name in installed,
                  'error':manifest['unavailable'][name]} if name in manifest.get('unavailable',{}) else
                 {'id':name,'ready':True,'installed':name in installed,
                  'download_bytes':sum(row['size'] for row in select(manifest,[name]))})
                for name in ('xtts','whisper')]
    except (OSError,ValueError,KeyError) as error:
        return [{'id':name,'ready':False,'installed':False,'error':str(error)} for name in ('xtts','whisper')]


def prepare(program, extensions, *, offline=None, progress=None):
    """Wizard entry after its main ZIP was verified; use the very same parser."""
    program=Path(program).resolve()
    manifest=load(program)
    if offline is None:
        archives=fetch(program,manifest,extensions,progress)
    else:
        archives=[]
        for artifact in select(manifest,extensions):
            target=cache_dir(program)/cache_name(artifact)
            source=Path(offline)/artifact['name']
            if target.exists():
                with target.open('rb') as stream:
                    valid=target.stat().st_size==artifact['size'] and hashlib.file_digest(stream,'sha256').hexdigest()==artifact['sha256']
                if not valid:target.rename(target.with_name(target.name+'.invalid-'+uuid.uuid4().hex))
            if not target.exists():
                temporary=target.with_name('.'+uuid.uuid4().hex+'.copy')
                try:
                    digest=hashlib.sha256();size=0
                    with source.open('rb') as stream,temporary.open('xb') as output:
                        while chunk:=stream.read(1024*1024):
                            size+=len(chunk);digest.update(chunk);output.write(chunk)
                    if size!=artifact['size'] or digest.hexdigest()!=artifact['sha256']:
                        raise ValueError('Offline component differs: '+artifact['name'])
                    temporary.replace(target)
                finally:temporary.unlink(missing_ok=True)
            archives.append({'path':str(target),'sha256':artifact['sha256'],'bytes':artifact['size']})
    _overlay(program, archives, progress)
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
