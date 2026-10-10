"""A package upgrade has no installer trial, but the agent must still learn it."""
import hashlib
import json
import os
from pathlib import Path
import time

def read(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}

def write(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temporary,path)

def observe(tree: Path, program: Path, config: dict):
    passport=read(program/'helene-build.json')
    current=str(passport.get('version') or '').strip()
    if not current or passport.get('complete') is not True: return
    state=Path(tree)/'memory/.state'
    scope=hashlib.sha256(str(program.resolve()).encode()).hexdigest()
    previous=read(state/'package-version.json')
    if previous and previous.get('scope')!=scope:
        old=''  # a carried home is not an upgrade of its former machine
    else:
        old=str(previous.get('version') or (config.get('installed') or {}).get('version') or '').strip()
    # Older Linux homes did not record installed.version. An established agent
    # still needs the first package inspection, without inventing its former version.
    established_linux = not previous and not old and passport.get('platform') == 'linux' and (state/'born.json').is_file()
    if (old and old!=current) or established_linux:
        receipt={'kind':'package','desktop':True,'state':'installed','from_version':old,'to_version':current,
            'scope':scope,'id':hashlib.sha256(f'{scope}:{old}:{current}:{passport.get("commit") or ""}'.encode()).hexdigest()[:24],
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
