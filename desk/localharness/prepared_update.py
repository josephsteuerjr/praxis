"""Prepare an authenticated release without admitting an install or stopping the agent."""
import hashlib
import shutil
import threading
import time
import uuid
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

from . import package_notice, program_state
from .config_transaction import transaction
from .windows_paths import Path, walk, wide

_guard = threading.RLock()
_states = {}
CHECK_SECONDS = 15 * 60


def _options(owner):
    from .python_updates import release_endpoint
    config = owner.current().config
    settings = package_notice.read(config).get('update') or {}
    return (str(Path(config).resolve()), release_endpoint(owner), settings.get('auto_prepare') is not False)


def _public(row):
    return {key: row[key] for key in ('state', 'latest', 'error', 'sha256') if key in row}


def monitor(owner, stop):
    # A newly opened window checks immediately. A tray-only session also checks.
    while not stop.wait(60):
        try:
            status(owner)
        except (OSError, ValueError, RuntimeError):
            continue  # The selected profile may be changing or still in setup.


def status(owner):
    """Start at most one worker per installation; polling only reads its state."""
    root = Path(owner.root).resolve()
    options = _options(owner)
    with _guard:
        previous = _states.get(root)
        if not options[2]:
            if previous:
                previous['cancel'].set()
            return {'state': 'disabled'}
        if previous and previous['thread'].is_alive():
            if previous['options'] != options:
                previous['cancel'].set()
                return {'state': 'checking'}
            return _public(previous)
        if previous and previous['options'] == options and not previous['cancel'].is_set() and time.monotonic() < previous['until']:
            if previous['state'] != 'ready' or _owns_cache(previous):
                return _public(previous)
        row = dict(state='checking', options=options, cancel=threading.Event(), until=0)
        worker = threading.Thread(target=_prepare, args=(root, row, previous),
                                  daemon=True, name='helene-prepare-update')
        row['thread'] = worker
        _states[root] = row
        worker.start()
        return _public(row)


def _prepare(root, row, previous):
    from . import python_updates as updates
    from .python_platform import target, distribution, asset_name, require_runtime
    try:
        release = updates.update_check(SimpleNamespace(root=root), row['options'][1])
        if not release['newer']:
            row['state'] = 'current'
            return
        latest, url = release['latest'], release['url']
        digest = release['sha256'].lower()
        name = asset_name(latest, target(root), distribution(root))
        if len(digest)!=64 or not url.startswith('https://') or unquote(urlsplit(url).path).split('/')[-1]!=name:
            raise ValueError('The release has no verified package for this platform')
        if previous and previous.get('state')=='ready' and previous.get('sha256')==digest and previous['options']==row['options'] and _owns_cache(previous):
            with _guard:
                row.update({key:previous[key] for key in ('state','latest','sha256','url','archives','proofs','cache','generation')})
            return
        if row['cancel'].is_set():return
        row['state'] = 'preparing'
        def still_wanted(_progress=None):
            if row['cancel'].is_set():
                raise InterruptedError('Automatic update preparation was disabled')
        download = updates.update_download(SimpleNamespace(root=root, prefetch_only=True, prefetch_progress=still_wanted), url, digest)
        cache = program_state.directory(root, 'prefetch') / 'incoming'
        archives = [download, *download['components']]
        generation = uuid.uuid4().hex
        # A second profile can have its own session host. A reader may only
        # consume the finished generation that its own worker verified.
        with transaction(cache):
            still_wanted()
            if cache.is_symlink() or getattr(cache,'is_junction',lambda:False)():raise ValueError('Linked update cache')
            if cache.exists():shutil.rmtree(wide(cache))
            cache.mkdir()
            proofs = {}
            for asset in archives:
                still_wanted()
                proofs.update(updates._extract(asset['path'], cache, still_wanted))
            package_notice.write(cache/'generation.json',{'generation':generation})
            package = cache / 'Helene'
            require_runtime(package, root)
            if updates.version(package)!=latest or not (package/'app/python_app.py').is_file():
                raise ValueError('Extracted package identity differs from release')
            if row['cancel'].is_set():return
            with _guard:
                row.update(state='ready', latest=latest, sha256=digest, url=url, cache=cache, generation=generation,
                           archives=[str(Path(a['path']).resolve()) for a in archives],
                           proofs={Path(path).relative_to(cache).as_posix():v['sha256'] for path,v in proofs.items()})
    except Exception as exc:
        row.update(state='error', error=str(exc))
    finally:
        row['until'] = time.monotonic() + (60 if row['state']=='error' else CHECK_SECONDS)


def _owns_cache(row):
    return package_notice.read(row['cache']/'generation.json').get('generation')==row['generation'] and (row['cache']/'Helene').is_dir()


def restart_args(owner, sha256):
    """Bind a click to the selected profile, configured feed and exact ready bytes."""
    options = _options(owner)
    with _guard:
        row = _states.get(Path(owner.root).resolve()) or {}
        if (not options[2] or row.get('options')!=options or row.get('state')!='ready'
                or row.get('sha256')!=sha256 or row['cancel'].is_set() or not _owns_cache(row)):
            raise ValueError('The prepared update is no longer current')
        return {'url':row['url'], 'sha256':sha256, 'sourceMode':'clean'}


def take(root, destination, archives, digest):
    """Use only this session's verified extraction. A modified cache cannot run."""
    with _guard:
        row = _states.get(Path(root).resolve())
        if (not row or row['state']!='ready' or row['cancel'].is_set() or row['sha256']!=digest
                or row['archives']!=[str(Path(a).resolve()) for a in archives]):
            return False
        cache, expected = row['cache'], row.pop('proofs')
        row.update(state='used', until=time.monotonic()+CHECK_SECONDS)
    with transaction(cache):
        if not _owns_cache(row):return False
        if cache.is_symlink() or getattr(cache,'is_junction',lambda:False)():raise ValueError('Linked update cache')
        source, claimed = cache/'Helene', destination/'Helene'
        if source.is_symlink() or getattr(source,'is_junction',lambda:False)():raise ValueError('Linked update package')
        source.rename(claimed)
    found = set()
    directories = {parent.as_posix() for name in expected for parent in Path(name).parents}
    def unreadable(exc):raise exc
    for directory, children, files in walk(claimed, onerror=unreadable):
        for name in children + files:
            path = Path(directory)/name
            relative = path.relative_to(destination).as_posix()
            if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('Linked prepared update entry')
            if name in children:
                if relative not in directories:raise ValueError('Unexpected prepared directory')
                continue
            if relative not in expected or not path.is_file():raise ValueError('Unexpected prepared file')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=expected[relative]:
                    raise ValueError('Prepared update file changed')
            found.add(relative)
    if found!=expected.keys():raise ValueError('Incomplete prepared update')
    return True
