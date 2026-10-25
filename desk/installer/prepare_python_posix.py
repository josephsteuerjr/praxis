"""Prepare a private Linux runtime using existing publisher pins, not legacy main.

Run on a disposable Linux builder. It neither installs on the host nor includes
voice/toolchain payloads. The resolver's exact freeze travels with the runtime.
"""
from pathlib import Path
import argparse
import json
import subprocess
import sys
import tarfile
import ast
import hashlib
import urllib.request
import uuid
import os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import deskpkg


def literal(path, name):
    # Legacy builders perform release-version admission at import. Their pinned
    # data is reusable, that executable admission is not the Python app's gate.
    rows=[n.value for n in ast.parse(path.read_text(encoding='utf-8')).body
          if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)]
    if len(rows)!=1:raise ValueError('Ambiguous canonical build input: '+name)
    return ast.literal_eval(rows[0])


def named_digest(path, mapping, key):
    """Read one existing data-only publisher pin without importing legacy gates."""
    nodes=[n.value for n in ast.parse(path.read_text(encoding='utf-8')).body
           if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==mapping for t in n.targets)]
    if len(nodes)!=1 or not isinstance(nodes[0],ast.Dict):raise ValueError('Publisher pin map unavailable')
    values=[ast.literal_eval(v) for k,v in zip(nodes[0].keys,nodes[0].values)
            if isinstance(k,ast.Name) and k.id==key]
    if len(values)!=1:raise ValueError('Publisher pin identity ambiguous')
    return values[0]


def runtime_recipe(target):
    here=Path(__file__).resolve().parent
    version=literal(here/'build_mac.py','PY_VERSION');tag=literal(here/'build_mac.py','PBS_TAG')
    if target == 'linux-x86_64':
        suffix='x86_64-unknown-linux-gnu'
        expected=literal(here/'build_linux.py','PBS_SHA256')
        flavor=deskpkg.LINUX
        gui=['PySide6','qtpy']
    elif target == 'macos-arm64':
        suffix='aarch64-apple-darwin'
        # Existing publisher pin, independently rechecked 24 October 2026.
        expected=named_digest(here/'build_mac.py','SHA256','PBS_URL')
        flavor=deskpkg.MACOS
        gui=['pyobjc-core==12.2.2','pyobjc-framework-Cocoa==12.2.2',
             'pyobjc-framework-Quartz==12.2.2','pyobjc-framework-WebKit==12.2.2']
    else:
        raise ValueError('No native Python recipe for '+target)
    name=f'cpython-{version}+{tag}-{suffix}-install_only.tar.gz'
    deps=list(literal(here/'build_dist.py','TREE_DEPS'))+deskpkg.requirements(flavor)
    deps+=['psutil','PySocks','PyYAML',*gui]
    return {'version':version,'name':name,'sha256':expected,
            'url':f'https://github.com/astral-sh/python-build-standalone/releases/download/{tag}/{name}',
            'dependencies':deps}


def prepare(output, cache, *, resume=False, target=None):
    output,cache=Path(output),Path(cache)
    if output.exists() and not resume:raise FileExistsError(output)
    if (output/'runtime-input.json').exists():raise ValueError('Prepared runtime is immutable; choose another output')
    output.mkdir(parents=True,exist_ok=resume);cache.mkdir(parents=True,exist_ok=True)
    from localharness.python_platform import runtime_target
    target=target or runtime_target()
    if target != runtime_target():raise ValueError('Runtime preparation requires the selected native host')
    recipe=runtime_recipe(target)
    version,name,expected=recipe['version'],recipe['name'],recipe['sha256']
    archive=cache/name
    if not archive.exists():
        partial=archive.with_name(archive.name+'.download-'+uuid.uuid4().hex)
        with urllib.request.urlopen(recipe['url'],timeout=120) as source, partial.open('xb') as dest:
            import shutil
            shutil.copyfileobj(source,dest)
        with partial.open('rb') as source:
            if hashlib.file_digest(source,'sha256').hexdigest()!=expected:
                raise ValueError('Publisher runtime SHA mismatch; partial input retained')
        partial.replace(archive)
    with archive.open('rb') as source:actual=hashlib.file_digest(source,'sha256').hexdigest()
    if actual!=expected:raise ValueError('Publisher runtime SHA mismatch; input retained')
    runtime=output/'python';python=runtime/'bin/python3'
    extracted=runtime/'.publisher-extracted.json'
    if runtime.exists() and not extracted.is_file():
        if not resume:raise ValueError('Unfinished runtime retained; explicit --resume required')
        runtime.rename(output/('.interrupted-python-'+uuid.uuid4().hex))
    if not runtime.exists():
        stage=output/('.extract-'+uuid.uuid4().hex);stage.mkdir()
        with tarfile.open(archive) as source:source.extractall(stage,filter='tar')
        candidate=stage/'python'
        if version not in subprocess.check_output([str(candidate/'bin/python3'),'--version'],text=True):
            raise ValueError('Extracted interpreter identity differs; stage retained')
        subprocess.run([str(candidate/'bin/python3'),'-I','-c','import ssl,json,tarfile'],check=True)
        (candidate/'.publisher-extracted.json').write_text(json.dumps({'sha256':actual,'target':target}),encoding='utf-8')
        candidate.rename(runtime)
    record=json.loads(extracted.read_text(encoding='utf-8'))
    if record!={'sha256':actual,'target':target}:raise ValueError('Extracted runtime belongs to another publisher input')
    if version not in subprocess.check_output([str(python),'--version'],text=True):
        raise ValueError('Incomplete runtime identity differs')
    # Keep the product's canonical tree/channel requirements; optional speech is
    # NOT pulled in through the old builder's deps() which includes VOICE_DEPS.
    deps=recipe['dependencies']
    # This credential-free disposable builder may build legitimate source-only
    # pure-Python dependencies (pyaes/proxy_tools). pip prefers published wheels;
    # the legacy all-binary policy is not a capability boundary for this product.
    subprocess.run([str(python),'-m','pip','install','setuptools','wheel'],check=True)
    subprocess.run([str(python),'-m','pip','install',*deps],check=True)
    freeze=subprocess.check_output([str(python),'-m','pip','freeze'],text=True)
    (runtime/'requirements-resolved.txt').write_text(freeze,encoding='utf-8')
    gui='from PySide6 import QtCore,QtGui,QtWidgets,QtWebEngineWidgets' if target.startswith('linux-') else 'import Cocoa,WebKit'
    subprocess.run([str(python),'-c','import psutil,socks,yaml,webview,aiohttp,telethon,paramiko,anthropic,openai; '+gui],check=True)
    (output/'runtime-input.json').write_text(json.dumps({'python':version,'target':target,'publisher_archive':archive.name,
        'publisher_sha256':actual,'requested_dependencies':deps,
        'qualification':'imports only; native desktop and final package consumers remain separate'},indent=2),encoding='utf-8')
    return runtime


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--resume',action='store_true',help='Continue one unfinished runtime, never a prepared one')
    p.add_argument('--target',choices=('linux-x86_64','macos-arm64'))
    a=p.parse_args();print(prepare(a.output,a.cache,resume=a.resume,target=a.target))
