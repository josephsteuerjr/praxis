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
from python_app_bundle import component
import deskpkg
DESK=Path(__file__).resolve().parents[1]


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


def assemble(runtime, edition, relay, relay_source, output, *, version, target, downloads=None, body=None, bridge=None, without_relay=False):
    runtime,edition,output=map(Path,(runtime,edition,output))
    relay=Path(relay) if relay else None
    relay_source=Path(relay_source) if relay_source else None
    if target not in ('linux-x86_64','macos-arm64'):raise ValueError('Expected a POSIX target')
    if target!=python_platform.runtime_target():raise ValueError('Assembly must run on the selected native OS/architecture')
    if output.exists():raise FileExistsError(output)
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('Expected numeric version')
    if without_relay:
        if relay or relay_source:raise ValueError('Explicit relay-unavailable assembly cannot contain relay inputs')
    elif relay is None or relay.name!='helene-relay' or not relay.is_file() or relay_source is None:
        raise ValueError('Expected exact POSIX relay input, or explicit --without-relay')
    for source in (runtime,edition,*((relay_source,) if relay_source else ())):
        if not source.is_dir() or output.resolve().is_relative_to(source.resolve()):raise ValueError('Invalid or overlapping package input')
    if not (edition/'agent.py').is_file() or not (edition/'llm.py').is_file():raise ValueError('Selected agent source is incomplete')
    if not (DESK/'app/dist/index.html').is_file():raise ValueError('Build the selected UI before assembly')
    provided=(component_downloads.for_target(component_downloads.parse(Path(downloads).read_bytes()),target) if downloads else None)
    output.mkdir(parents=True)
    ignore=shutil.ignore_patterns('__pycache__','*.pyc','.git','node_modules','target')
    shutil.copytree(runtime,output/'runtime',symlinks=False,ignore=ignore)
    prepare_qt_process(output)
    python=output/'runtime/bin/python3'
    gui='from PySide6 import QtCore,QtGui,QtWidgets,QtWebEngineWidgets' if target.startswith('linux-') else 'import Cocoa,WebKit'
    subprocess.run([str(python),'-I','-c','import psutil,socks,yaml,webview,aiohttp,telethon,paramiko; '+gui],check=True)
    app=output/'app';app.mkdir()
    for name in ('python_app.py','python_tray.py','python_migrate.py','deskapp.py'):shutil.copy2(DESK/name,app/name)
    for name in ('localharness','deskd','resources'):shutil.copytree(DESK/name,app/name,ignore=ignore)
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
    components={'relay':{'unavailable':'No target-qualified native relay input was supplied; direct API mode only'}}
    (output/'components').mkdir(exist_ok=True)
    if relay:
        components['relay']=component(output,relay,relay_source)
        shutil.copy2(relay,output/'helene-relay');(output/'helene-relay').chmod(0o755)
        (output/'components/relay/binary/helene-relay').chmod(0o755)
    (output/'components/components.json').write_text(json.dumps({'format':'helene-components.v1','components':components},indent=2),encoding='utf-8')
    for binary,name in ((body,'helene-body'),(bridge,'helene-bridge')):
        if binary:
            shutil.copy2(binary,output/name);(output/name).chmod(0o755)
    (output/'python-app.json').write_text(json.dumps({'format':'helene-python-app.v1','version':version,
        'entry':'app/python_app.py','window':'pywebview','target':target,'local_candidate':True,
        'native_inputs':{'relay':bool(relay),'body':bool(body),'bridge':bool(bridge)},
        'qualification':'Private candidate; native GUI/custody/update require platform consumer evidence',
        'extensions':{n:{'default':False,'available':n not in manifest.get('unavailable',{})} for n in sorted(component_downloads.EXTENSIONS)}},indent=2),encoding='utf-8')
    (output/'helene-build.json').write_text(json.dumps({'format':'helene-python-version.v1','product':'Hélène','version':version,'platform':target,'version_source':'python-app.json'}),encoding='utf-8')
    cfg={'mode':'local','tree':'data','code':'tree','python':'runtime/bin/python3','app':'app/deskapp.py',
         'runner':'app/localharness/runner.py','setup_complete':False,'port':8094,'relay':{'enabled':False},'voice':{'speak':False}}
    (output/'helene.json').write_text(json.dumps(cfg,indent=2),encoding='utf-8')
    launcher='#!/bin/sh\nset -eu\nhere=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$here/runtime/bin/python3" -s "$here/app/python_app.py" --root "$here" "$@"\n'
    (output/'helene').write_text(launcher,encoding='utf-8');(output/'helene').chmod(0o755)
    shutil.copy2(DESK.parent/'LICENSE',output/'LICENSE')
    shutil.copy2(DESK.parent/'NOTICE',output/'NOTICE')
    shutil.copy2(DESK/'installer/THIRD-PARTY.md',output/'THIRD-PARTY.md')
    shutil.copy2(DESK/'installer/PYTHON-START-HERE.md',output/'START-HERE.md')
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
    p.add_argument('--without-relay',action='store_true',help='Explicit direct-provider candidate; no bundled relay capability')
    p.add_argument('--version',required=True);p.add_argument('--target',required=True)
    p.add_argument('--downloads',type=Path);p.add_argument('--body',type=Path);p.add_argument('--bridge',type=Path)
    a=p.parse_args();print(assemble(a.runtime,a.edition,a.relay,a.relay_source,a.output,version=a.version,target=a.target,downloads=a.downloads,body=a.body,bridge=a.bridge,without_relay=a.without_relay))
