"""Assemble the broker-free Python app from explicit POSIX inputs.

The portable root is writable by its owner. Legacy service/package hooks are
never called. Runtime symlinks are materialized in this distribution stage so
all existing no-symlink extraction/migration consumers keep one policy.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import app_components,component_downloads,python_platform
sys.path.insert(0,str(Path(__file__).resolve().parent))
import deskpkg
DESK=Path(__file__).resolve().parents[1]


def launcher(target):
    # Qt/Python stay private; installed OS libraries keep their own driver and
    # GIO module ABI. The copied closure supplies only missing desktop libs.
    libraries=('if [ -d "$here/runtime/system-libs" ]; then\n'
               '    helene_libraries=""\n'
               '    for directory in "$here"/runtime/lib/python*/site-packages/PySide6/Qt/lib "$here/runtime/lib" /lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu /lib64 /usr/lib64 "$here/runtime/system-libs"; do\n'
               '        if [ -d "$directory" ]; then helene_libraries="${helene_libraries:+$helene_libraries:}$directory"; fi\n'
               '    done\n'
               '    export LD_LIBRARY_PATH="$helene_libraries${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
               'fi\n') if target.startswith('linux-') else ''
    return '#!/bin/sh\nset -eu\nhere=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'+libraries+'exec "$here/runtime/bin/python3" -s "$here/app/python_app.py" --root "$here" "$@"\n'


def prepare_qt_process(output):
    """Restore Qt's process entry in an owned runtime copy, never a linked stage.

    Import-only checks miss QtWebEngineProcess: wheels unpack it as 0644 on
    some preparations, but the real window must exec this ELF helper.
    """
    for helper in Path(output).glob('runtime/lib/python*/site-packages/PySide6/Qt/libexec/QtWebEngineProcess'):
        if helper.is_symlink():raise ValueError('Qt process entry must be a copied regular file')
        with helper.open('rb') as stream:
            if stream.read(4)!=b'\x7fELF':raise ValueError('Expected the Linux Qt process ELF')
        helper.chmod(0o755)


def normalize_output_modes(output):
    """Owned distribution copies must not inherit group/world-write bits."""
    for path in (Path(output), *Path(output).rglob('*')):
        if path.is_symlink():raise ValueError('Distribution stage retained a symlink')
        mode=stat.S_IMODE(path.stat().st_mode)
        safe=mode & 0o755
        if safe!=mode:path.chmod(safe)


def use_host_compiler_runtime(output):
    """Keep the desktop GPU driver's C++ ABI together with its host runtime.

    Bundling an older libstdc++ ahead of the host makes a newer Mesa driver
    fail to load (GLIBCXX/CXXABI symbols), even when Qt imports succeed.
    These base-OS libraries are dependencies of the supported desktop, not
    portable GUI additions. Only the owned distribution copy is changed.
    """
    output=Path(output)
    notice=output/'linux-libraries.json'
    if not notice.is_file():raise ValueError('Linux library provenance is missing')
    record=json.loads(notice.read_text(encoding='utf-8'))
    host={}
    for name in ('libstdc++.so.6','libgcc_s.so.1'):
        path=output/'runtime/system-libs'/name
        if path.is_symlink():raise ValueError('Linked compiler runtime in owned distribution')
        if path.is_file():path.unlink()
        donor=record.get('libraries',{}).pop(name,None)
        host[name]={'provider':'host operating system','reason':'Keep the host graphics-driver ABI',
                    'excluded_donor':donor}
    record['host_dependencies']=host
    notice.write_text(json.dumps(record,indent=2),encoding='utf-8')


def assemble(runtime, edition, relay, relay_source, output, *, version, target, downloads=None, body=None, bridge=None, without_relay=False, verification=None, verification_tools=None, legal=None):
    runtime,edition,output=map(Path,(runtime,edition,output))
    relay=Path(relay) if relay else None
    relay_source=Path(relay_source) if relay_source else None
    if target not in ('linux-x86_64','macos-arm64'):raise ValueError('Expected a POSIX target')
    if target!=python_platform.runtime_target():raise ValueError('Assembly must run on the selected native OS/architecture')
    if output.exists():raise FileExistsError(output)
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-rc\.\d+)?',version):raise ValueError('Expected release or numbered RC version')
    if relay or relay_source:raise ValueError('praxis-relay downloads separately; do not embed it in the base application')
    for source in (runtime,edition,*((relay_source,) if relay_source else ())):
        if not source.is_dir() or output.resolve().is_relative_to(source.resolve()):raise ValueError('Invalid or overlapping package input')
    if not (edition/'agent.py').is_file() or not (edition/'llm.py').is_file():raise ValueError('Selected agent source is incomplete')
    if not (DESK/'app/dist/index.html').is_file():raise ValueError('Build the selected UI before assembly')
    provided=(component_downloads.for_target(component_downloads.parse(Path(downloads).read_bytes()),target) if downloads else None)
    output.mkdir(parents=True)
    ignore=shutil.ignore_patterns('__pycache__','*.pyc','.git','node_modules','target')
    shutil.copytree(runtime,output/'runtime',symlinks=False,ignore=ignore)
    if target.startswith('linux-'):
        from runtime_scripts import make_portable
        console_receipt=make_portable(output/'runtime')
        (output/'runtime/portable-console-entries.json').write_text(json.dumps(console_receipt,indent=2),encoding='utf-8')
    prepare_qt_process(output)
    python=output/'runtime/bin/python3'
    gui='from PySide6 import QtCore,QtGui,QtWidgets,QtWebEngineWidgets' if target.startswith('linux-') else 'import Cocoa,WebKit'
    subprocess.run([str(python),'-I','-c','import psutil,socks,yaml,webview,aiohttp,telethon,paramiko; '+gui],check=True)
    app=output/'app';app.mkdir()
    for entry in DESK.glob('*.py'):
        if not entry.name.startswith(('test_','scratch_')) and entry.name not in ('deskpkg.py','layout.py'):
            shutil.copy2(entry,app/entry.name)
    for name in ('localharness','deskd','resources'):shutil.copytree(DESK/name,app/name,ignore=ignore)
    # Same canonical release engine as Windows; edition/tree may be older or edited.
    shutil.copy2(DESK.parent/'helene/core/xtts_tts_worker.py',app/'localharness/xtts_tts_worker.py')
    shutil.copy2(DESK/'shell/icons/python-app.png',app/'resources/python-app.png')
    shutil.copytree(DESK/'app/dist',app/'static')
    for frontend,dest in (('mobile','mobile'),('miniapp','miniapp')):
        shutil.copytree(DESK/frontend/'dist',app/dest)
    selected=edition.parent/'SELECTED-SOURCE.json'
    if selected.is_file():shutil.copy2(selected,output/'selected-source.json')
    shutil.copytree(DESK/'server',output/'server',ignore=ignore)
    # Root server recipe consumes the selected core's declaration, not a
    # nonexistent repository-root file. Channel dependencies retain their SSOT.
    channel=deskpkg.requirements(deskpkg.MACOS if target.startswith('macos-') else deskpkg.LINUX)
    (output/'requirements.txt').write_text((edition/'requirements.txt').read_text(encoding='utf-8')+'\n# Hélène channel/runtime\n'+'\n'.join(channel)+'\n',encoding='utf-8')
    (app/'requirements-desk.txt').write_text('\n'.join(channel)+'\n',encoding='utf-8')
    # The exact selected code is a code input, never the agent's soul or memory.
    def code_inputs(directory, names):
        omitted=set(ignore(directory,names))
        if Path(directory).resolve()==edition.resolve():
            for name in ('soul','memory','workspace','data','.env','AGENTS.md'):
                path=edition/name
                if path.is_file() or (path.is_dir() and any(p.is_file() for p in path.rglob('*'))):
                    raise ValueError('Personal state is not a code input: '+name)
                omitted.add(name)  # import-created empty directories are not payload
        return omitted
    shutil.copytree(edition,output/'tree',ignore=code_inputs)
    for forbidden in ('soul','memory','workspace','data','.env','AGENTS.md'):
        path=output/'tree'/forbidden
        if path.exists():raise ValueError('Private/identity material in code input: '+forbidden)
    if downloads:
        manifest=provided
    else:
        reason='A target-compatible payload has not been built and qualified for '+target
        manifest={'format':component_downloads.FORMAT,'target':target,'extensions':{n:[] for n in sorted(component_downloads.EXTENSIONS)},
                  'artifacts':{},'unavailable':{n:reason for n in sorted(component_downloads.EXTENSIONS)}}
        component_downloads.validate(manifest)
    (app/'component-downloads.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    components={}
    (output/'components').mkdir(exist_ok=True)
    (output/'components/components.json').write_text(json.dumps({'format':'helene-components.v1','components':components},indent=2),encoding='utf-8')
    for binary,name in ((body,'helene-body'),(bridge,'helene-bridge')):
        if binary:
            shutil.copy2(binary,output/name);(output/name).chmod(0o755)
    (output/'python-app.json').write_text(json.dumps({'format':'helene-python-app.v1','version':version,
        'entry':'app/python_app.py','window':'pywebview','target':target,'local_candidate':re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) is None,
        'native_inputs':{'relay':bool(relay),'body':bool(body),'bridge':bool(bridge)},
        'qualification':'Native GUI/custody/update require platform consumer evidence',
        'extensions':{n:{'default':False,'available':n not in manifest.get('unavailable',{})} for n in sorted(component_downloads.EXTENSIONS)}},indent=2),encoding='utf-8')
    (output/'helene-build.json').write_text(json.dumps({'format':'helene-python-version.v1','product':'Hélène','version':version,'platform':target,'version_source':'python-app.json'}),encoding='utf-8')
    cfg={'mode':'local','tree':'data','code':'tree','python':'runtime/bin/python3','app':'app/deskapp.py',
         'runner':'app/localharness/runner.py','setup_complete':False,'port':8094,'relay':{'enabled':False},'voice':{'speak':False}}
    (output/'helene.json').write_text(json.dumps(cfg,indent=2),encoding='utf-8')
    (output/'helene').write_text(launcher(target),encoding='utf-8');(output/'helene').chmod(0o755)
    shutil.copy2(DESK.parent/'LICENSE',output/'LICENSE')
    shutil.copy2(DESK.parent/'NOTICE',output/'NOTICE')
    shutil.copy2(DESK/'installer/THIRD-PARTY.md',output/'THIRD-PARTY.md')
    instructions='PYTHON-LINUX-START-HERE.md' if target.startswith('linux-') else 'PYTHON-START-HERE.md'
    shutil.copy2(DESK/'installer'/instructions,output/'START-HERE.md')
    if legal:
        legal=Path(legal).resolve()
        for name in ('licenses','THIRD-PARTY-LICENSES','THIRD-PARTY-LICENSES.txt','linux-libraries.json'):
            entry=legal/name
            if entry.is_symlink():raise ValueError('Linked native legal material')
            if entry.is_dir():shutil.copytree(entry,output/name,symlinks=False)
            elif entry.is_file():shutil.copy2(entry,output/name)
        if target.startswith('linux-') and not (output/'linux-libraries.json').is_file():
            raise ValueError('Linux system-library provenance is missing')
        if target.startswith('linux-'):use_host_compiler_runtime(output)
    # The incoming coordinator verifies actual application bytes before it may
    # import itself for an authored update. This is the same app-relative ABI
    # as Windows portable_refresh, including target-specific downloads.
    app_files={p.relative_to(app).as_posix():app_components.digest(p) for p in app.rglob('*')
               if p.is_file() and p.name!='desk.json'}
    static={n[7:]:h for n,h in app_files.items() if n.startswith('static/')}
    (output/'source-inputs.json').write_text(json.dumps({'version':version,'files':app_files},indent=2),encoding='utf-8')
    (app/'desk.json').write_text(json.dumps({'version':version,'files':app_files,
        'digest':hashlib.sha256('\n'.join(f'{n} {h}' for n,h in sorted(app_files.items())).encode()).hexdigest(),
        'static':hashlib.sha256('\n'.join(f'{n} {h}' for n,h in sorted(static.items())).encode()).hexdigest()},indent=2),encoding='utf-8')
    if verification:
        from localharness import source_verification_bundle
        verification=Path(verification).resolve()
        bundle_sha=source_verification_bundle.digest(verification/'manifest.json')
        source_verification_bundle.load(verification,bundle_sha,version)
        shutil.copytree(verification,output/'verification',symlinks=False)
        meta=json.loads((output/'python-app.json').read_text(encoding='utf-8'))
        meta['verification_bundle_sha256']=bundle_sha
        (output/'python-app.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
        if verification_tools:
            from localharness import source_verification_executor
            shutil.copytree(verification_tools,output/'verification-tools',symlinks=False)
            declared=source_verification_bundle.load(output/'verification',bundle_sha,version).get('bundled_executors',{}).get(sys.platform)
            if source_verification_executor.describe(output/'verification-tools')!=declared:
                raise ValueError('Native verification tools differ from bound bundle')
    elif verification_tools:raise ValueError('Native verification tools require a bound source bundle')
    normalize_output_modes(output)
    from localharness.code_transition import seal as seal_code
    from localharness.authored_baseline import seal as seal_authored_baseline
    seal_code(output);seal_authored_baseline(output)
    # Use the same byte inventory the updater preserves. Mutable configuration,
    # user data, component overlays and this inventory itself are not retired.
    files={p.relative_to(output).as_posix():app_components.digest(p) for p in output.rglob('*')
           if p.is_file() and p.relative_to(output).parts[0] not in ('tree','components','data','downloads')
           and p.name not in ('helene.json','python-package-files.json')}
    (output/'python-package-files.json').write_text(json.dumps({'format':'helene-python-program-files.v1','version':version,'files':files},indent=2),encoding='utf-8')
    normalize_output_modes(output)
    archive=output.parent/python_platform.asset_name(version,target)
    if archive.exists():raise FileExistsError(archive)
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(output.rglob('*')):
            if path.is_symlink():raise ValueError('Distribution stage retained a symlink')
            if path.is_file():z.write(path,'Helene/'+path.relative_to(output).as_posix())
    return archive


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('runtime','edition','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--relay',type=Path);p.add_argument('--relay-source',type=Path)
    p.add_argument('--without-relay',action='store_true',help='Compatibility flag: relay is always downloaded separately')
    p.add_argument('--version',required=True);p.add_argument('--target',required=True)
    p.add_argument('--downloads',type=Path);p.add_argument('--body',type=Path);p.add_argument('--bridge',type=Path)
    p.add_argument('--verification',type=Path,help='Exact version-bound full source verification bundle')
    a=p.parse_args();print(assemble(a.runtime,a.edition,a.relay,a.relay_source,a.output,version=a.version,target=a.target,downloads=a.downloads,body=a.body,bridge=a.bridge,without_relay=a.without_relay,verification=a.verification))
