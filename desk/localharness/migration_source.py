"""Select pristine release code after preserving a legacy profile."""
from pathlib import Path
import tempfile
from . import source_merge as merge
from .package_notice import read, write


def verify(program):
    from .code_transition import baseline
    program = Path(program).resolve()
    upstream = read(program / 'code-upstream.json')
    actual = merge.inventory(program / 'tree')
    if actual != upstream.get('files'):
        raise ValueError('Clean migration requires verified pristine release code')
    with tempfile.TemporaryDirectory(prefix='migration-code-proof-', dir=program.parent) as tmp:
        if merge.inventory(baseline(program, Path(tmp))) != actual:
            raise ValueError('Pristine migration source archive differs')
    return actual


def prepare(old, new, note, *, preserve_previous=True):
    from .app_migration import code_path, preserve_source
    from .code_transition import baseline
    from .essential_backup import configs, load
    from .portable_profile import relative_config
    old, new = Path(old).resolve(), Path(new).resolve()
    actual = verify(new)
    targets = {}; previous_copies={}
    for config in configs(new):
        if not config.is_file(): continue
        value = load(config)
        if value.get('mode') == 'remote': continue
        previous = code_path(config, value)
        if previous not in targets:
            if preserve_previous and previous.is_dir():
                saved=previous
                if not previous.is_relative_to(new):
                    saved=new/'code-backup'/note['id']/str(len(targets))
                    preserve_source(previous,saved,source_only=True)
                previous_copies[previous]=saved
            target = new / 'code' / note['id'] / ('clean-' + str(len(targets)))
            target.mkdir(parents=True, exist_ok=False)
            baseline(new, target)
            exe = merge.executable(new, old); repo = target / '.git'
            merge.git(repo, 'init', '--quiet', exe=exe)
            merge.git(repo, 'config', 'core.worktree', '..', exe=exe)
            commit = merge.snapshot(repo, target, actual, exe=exe, label='Pristine migration source')
            merge.git(repo, 'update-ref', 'refs/heads/upstream', commit, exe=exe)
            merge.git(repo, 'symbolic-ref', 'HEAD', 'refs/heads/upstream', exe=exe)
            targets[previous] = target
        value.update(code=str(targets[previous]), app=str(new / 'app/deskapp.py'),
                     runner=str(new / 'app/localharness/runner.py'))
        write(config, relative_config(value, config, new) if note.get('portable') else value)
    for row in note['source_paths']:
        previous = Path(row['selected']).resolve()
        if previous in targets:
            if previous in previous_copies:row['preserved_previous'] = str(previous_copies[previous])
            else:row['previous_code_absent'] = True
            row['selected'] = str(targets[previous])
    note.update(source_policy='clean', state='prepared', pristine_source_sha256=read(new / 'code-upstream.json')['sha256'])
    write(new / 'python-migration.json', note)
    return note
