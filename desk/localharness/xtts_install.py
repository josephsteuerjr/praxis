"""Install a verified optional XTTS bundle without pip or duplicate runtime copies.

The packaged runtime can execute in components/xtts/runtime. Legacy voice-xtts
installations remain selected. Model additions are resumable; existing differing
files are retained, never overwritten. Model licence stays with the model.
"""
from __future__ import annotations
import json
import os
import shutil
import tempfile
from pathlib import Path
if __package__:
    from .app_components import digest
else:
    from app_components import digest


def runtime_for(program):
    legacy=Path(program)/'voice-xtts'
    executable='python.exe' if os.name=='nt' else 'bin/python3'
    return legacy if (legacy/executable).is_file() else Path(program)/'components/xtts/runtime'


def install(tree: Path, bundle: Path | None = None, *, program: Path | None = None):
    program=Path(program or Path(__file__).resolve().parents[2])
    bundle=Path(bundle or program/'components/xtts')
    note=json.loads((bundle/'xtts-bundle.json').read_text(encoding='utf-8'))
    if note.get('format')!='helene-xtts.v1' or not isinstance(note.get('files'),dict) or not note['files']:
        raise ValueError('invalid XTTS bundle')
    runtime=runtime_for(program)
    if runtime==program/'components/xtts/runtime' and bundle.resolve()!=(program/'components/xtts').resolve():
        runtime=program/'voice-xtts'  # An external removable bundle must not own execution.
    model=Path(tree)/'models/xtts'
    missing=[]
    for relative,expected in note['files'].items():
        rel=Path(relative)
        if rel.is_absolute() or '..' in rel.parts or rel.parts[0] not in ('runtime','model') or '\\' in relative or ':' in relative:
            raise ValueError('invalid XTTS bundle path')
        source=(bundle/rel).resolve()
        if not source.is_relative_to(bundle.resolve()) or not source.is_file() or digest(source)!=expected:
            raise ValueError('XTTS bundle changed: '+relative)
        destination=(runtime if rel.parts[0]=='runtime' else model).joinpath(*rel.parts[1:])
        if destination.exists():
            if destination.is_symlink() or not destination.is_file() or (not source.samefile(destination) and digest(destination)!=expected):
                raise FileExistsError('XTTS destination differs; retained: '+str(destination))
        else:missing.append((source,destination,expected,rel.parts[0]))
    program.mkdir(parents=True,exist_ok=True)
    for source,destination,expected,kind in missing:
        destination.parent.mkdir(parents=True,exist_ok=True)
        handle,raw=tempfile.mkstemp(prefix='.xtts-',dir=destination.parent);os.close(handle);temporary=Path(raw)
        try:
            if kind=='model' and source.suffix=='.pth' and source.stat().st_dev==temporary.stat().st_dev:
                temporary.unlink();os.link(source,temporary)
            else:shutil.copy2(source,temporary)
            if not source.samefile(temporary) and digest(temporary)!=expected:raise ValueError('XTTS copy changed')
            # Never overwrite a racing file; a retry verifies it before reuse.
            os.link(temporary,destination)
        finally:temporary.unlink(missing_ok=True)
    return {'ok':True,'state':'done','backend':'xtts','python':str(runtime/('python.exe' if os.name=='nt' else 'bin/python3')),'restart_required':True}
