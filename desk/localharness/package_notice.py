"""A package upgrade has no installer trial, but the agent must still learn it."""
import hashlib
import json
import os
import re
from pathlib import Path
import time
import tempfile

def read(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}

def write(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            json.dump(value, target, ensure_ascii=False, indent=2); target.write('\n')
            target.flush(); os.fsync(target.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def version(value):
    text = value.strip() if isinstance(value, str) else ''
    return text if re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', text) else ''

def observe(tree: Path, program: Path, config: dict):
    manifest=read(program/'python-app.json')
    if manifest.get('format')=='helene-python-app.v1' and version(manifest.get('version')):
        # Python delivery records its identity without a legacy QA passport.
        passport={**manifest,'platform':'windows'}
    else:
        passport=read(program/'helene-build.json')
        if passport.get('complete') is not True: return
    current=version(passport.get('version'))
    if not current: return
    state=Path(tree)/'memory/.state'
    scope=hashlib.sha256(str(program.resolve()).encode()).hexdigest()
    previous=read(state/'package-version.json')
    installed=read(program/'helene-install.json')
    if previous and previous.get('scope')!=scope:
        old=''  # a carried home is not an upgrade of its former machine
    else:
        installed_previous = version(installed.get('previous_version')) if installed.get('version') == current else ''
        installed_config = config.get('installed')
        configured = version(installed_config.get('version')) if isinstance(installed_config, dict) else ''
        old=version(previous.get('version')) or installed_previous or (configured if configured != current else '')
    # Older Linux homes did not record installed.version. An established agent
    # still needs the first package inspection, without inventing its former version.
    established_without_version = (not version(previous.get('version')) and not old
        and previous.get('scope') in (None, '', scope) and (state/'born.json').is_file())
    if (old and old!=current) or established_without_version:
        receipt={'kind':'package','desktop':True,'platform':passport.get('platform'),'state':'installed','from_version':old,'to_version':current,
            'scope':scope,'id':hashlib.sha256(f'{scope}:{old}:{current}:{passport.get("commit") or ""}:{previous.get("at") or installed.get("installed_utc") or ""}'.encode()).hexdigest()[:24],
            'at':time.time()}
        write(state/'package-update.json',receipt)
    if previous.get('version')!=current or previous.get('scope')!=scope:
        write(state/'package-version.json',{'scope':scope,'version':current,'at':time.time()})

def pending(tree: Path):
    state=Path(tree)/'memory/.state'
    receipt=read(state/'package-update.json')
    if not receipt or receipt.get('scope')!=read(state/'package-version.json').get('scope'): return None
    mark=read(state/'package-update-reported.json')
    if mark.get('id')==receipt.get('id') and mark.get('done') is True: return None
    return receipt

def mark(tree: Path, receipt: dict, **how):
    write(Path(tree)/'memory/.state/package-update-reported.json',{'id':receipt['id'],'state':receipt['state'],**how})

def reported(tree: Path): return read(Path(tree)/'memory/.state/package-update-reported.json')
