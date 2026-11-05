"""Install a verified optional XTTS bundle without pip or duplicate runtime copies.

The packaged runtime can execute in components/xtts/runtime. Legacy voice-xtts
installations remain selected. Model additions are resumable; existing differing
files are retained, never overwritten. Model licence stays with the model.
"""
from __future__ import annotations
import json
import os
import tempfile
import shutil
from pathlib import Path
if __package__:
    from .app_components import digest
else:
    from app_components import digest


def runtime_for(program):
    from localharness.component_store import path
    legacy=path(program,'voice-xtts')
    executable='python.exe' if os.name=='nt' else 'bin/python3'
    return legacy if (legacy/executable).is_file() else path(program,'components/xtts')/'runtime'


def install(tree: Path, bundle: Path | None = None, *, program: Path | None = None):
    program=Path(program or Path(__file__).resolve().parents[2])
    from localharness import component_store as store
    bundle=Path(bundle or store.path(program,'components/xtts'))
    note=json.loads((bundle/'xtts-bundle.json').read_text(encoding='utf-8'))
    if note.get('format')!='helene-xtts.v1' or not isinstance(note.get('files'),dict) or not note['files']:
        raise ValueError('invalid XTTS bundle')
    # Validate the supplied bundle before publishing either runtime or model.
    for relative, expected in note['files'].items():
        rel=Path(relative)
        if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.parts[0] not in ('runtime','model') or '\\' in relative or ':' in relative:
            raise ValueError('invalid XTTS bundle path')
        source=(bundle/rel).resolve()
        if not source.is_relative_to(bundle.resolve()) or not source.is_file() or digest(source)!=expected:
            raise ValueError('XTTS bundle changed: '+relative)
    existing = store.model_path(tree,'xtts')
    prior_ref=store._pins(tree)['pins'].get('models/xtts')
    prior_files=(json.loads((existing.parent/'receipt.json').read_text())['files'] if prior_ref else {})
    for relative, expected in note['files'].items():
        rel=Path(relative)
        if rel.parts[0]!='model':continue
        current=existing.joinpath(*rel.parts[1:])
        if current.exists():
            got=digest(current) if current.is_file() else None
            previous=prior_files.get(Path(*rel.parts[1:]).as_posix())
            if got!=expected and (not previous or got!=previous[1]):
                raise FileExistsError('XTTS model differs; existing selection retained')
    # Both a removable offline bundle and legacy installation become permanent
    # store objects; the agent holds a model-only pin shared across agents.
    reference, report = store.import_directory(bundle, program=program)
    model_source = store._reference(reference)/'model'
    runtime_ref, runtime_report = store.import_directory(store._reference(reference)/'runtime', program=program)
    # Retain personal sample files alongside unchanged weights. Assemble a new
    # immutable view; never add metadata into a model held by a running worker.
    with tempfile.TemporaryDirectory(prefix='.model-view-',dir=store.root()) as temporary:
        assembled=Path(temporary)/'model';assembled.mkdir()
        def link_or_copy(src,dst):
            try:os.link(Path(src).resolve(),dst)
            except OSError:shutil.copyfile(src,dst)
            return str(dst)
        def ignore_replaced(directory,names):
            relative=Path(directory).relative_to(existing)
            return [name for name in names if (model_source/relative/name).is_file()]
        if existing.is_dir():shutil.copytree(existing,assembled,dirs_exist_ok=True,copy_function=link_or_copy,ignore=ignore_replaced)
        for path in model_source.rglob('*'):
            if path.is_file():
                destination=assembled/path.relative_to(model_source)
                if not destination.exists():destination.parent.mkdir(parents=True,exist_ok=True);link_or_copy(path,destination)
        model_ref, model_report = store.import_directory(assembled)
    store.pin(program, 'components/xtts', reference)
    store.pin(program, 'voice-xtts', runtime_ref)
    store.pin(tree, 'models/xtts', model_ref)
    runtime=store.path(program,'voice-xtts')
    return {'ok':True,'state':'done','backend':'xtts',
        'python':str(runtime/('python.exe' if os.name=='nt' else 'bin/python3')),
        'restart_required':True,'copied_bytes':report['copied_bytes']+model_report['copied_bytes']+runtime_report['copied_bytes']}
