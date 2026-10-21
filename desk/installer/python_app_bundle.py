"""Build the broker-free Python application from a clean distribution stage.

The permanent application contains Python, UI, seed agent code, and relay binary
plus source. Models and the native rebuild toolchain are optional components;
none is needed to launch the window. This builder neither installs nor publishes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

DESK=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(DESK))
from localharness import app_components


def component(output, binary, source):
    base=output/'components/relay'
    (base/'binary').mkdir(parents=True)
    shutil.copy2(binary,base/'binary'/binary.name)
    shutil.copytree(source,base/'source',ignore=shutil.ignore_patterns('.git','target','local_auth','logs','__pycache__','*.pyc'))
    files={p.relative_to(base).as_posix():app_components.digest(p) for p in base.rglob('*') if p.is_file()}
    return {'files':files,'executable':'binary/'+binary.name,'install_name':binary.name,
            'shared_toolchain':True,'toolchain_optional':True}


def install_runtime_dependencies(runtime, wheels):
    """Only the two declared, hash-pinned wheels; never pip into the host Python."""
    wheels=Path(wheels);record=json.loads((wheels/'wheels.json').read_text(encoding='utf-8'))
    if set(record.get('packages',{}))!={'psutil','PySocks'}:raise ValueError('Expected the declared runtime wheel inventory')
    inputs=[]
    for entry in record['packages'].values():
        name=entry['filename']
        if Path(name).name!=name or not name.endswith('.whl'):raise ValueError('Invalid wheel filename')
        path=wheels/name
        with path.open('rb') as source:actual=hashlib.file_digest(source,'sha256').hexdigest()
        if actual!=entry['sha256']:raise ValueError('Runtime dependency wheel changed: '+name)
        inputs.append(str(path.resolve()))
    runtime=Path(runtime)
    subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--no-compile','--upgrade','--target',str(runtime.resolve()),*inputs],check=True)
    # pip records local wheel paths; publish the pinned source URLs/digests,
    # not this builder's private filesystem location.
    for distribution in ('psutil','PySocks'):
        for metadata in runtime.glob(distribution+'-*.dist-info/direct_url.json'):
            metadata.unlink()
    expected=[record['packages'][name]['version'] for name in ('psutil','PySocks')]
    subprocess.run([str(runtime/'python.exe'),'-s','-c',
        'import json,sys,psutil,socks;sys.exit([psutil.__version__,socks.__version__]!=json.loads(sys.argv[1]))',
        json.dumps(expected)],check=True)
    (Path(runtime)/'runtime-additions.json').write_text(json.dumps(record,indent=2),encoding='utf-8')


def assemble(stage, output, relay, relay_source, *, version, toolchain=None, runtime_wheels=None):
    stage,output,relay,relay_source=map(Path,(stage,output,relay,relay_source))
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('Expected a numeric release version')
    if output.exists():raise FileExistsError(output)
    if output.resolve().is_relative_to(stage.resolve()) or stage.resolve().is_relative_to(output.resolve()):
        raise ValueError('Package source and output must be separate')
    if relay.name.lower()!='helene-relay.exe':raise ValueError('Expected the Hélène relay executable')
    def available(module):return any((stage/'runtime'/base/module).exists() for base in ('','Lib/site-packages'))
    if runtime_wheels is None and not (available('psutil/__init__.py') and available('socks.py')):
        raise ValueError('Runtime needs psutil and PySocks; provide --runtime-wheels with its hash inventory')
    def ignored(directory,names):
        path=Path(directory)
        omitted={'.git','__pycache__','local_auth'}
        if path==stage:omitted|={'data','agents','backups','helene-host.exe','helene-svc.exe','owner-stop.json','python-migration.json','migration-start.log','install.log','helene.json','helene.json.bak','agent-default.json','local-build.json','.env'}
        if path==stage/'components':omitted|={'broker','relay','toolchain','xtts','whisper'}
        return [name for name in names if name in omitted or name.endswith('.pyc')]
    shutil.copytree(stage,output,ignore=ignored)
    shutil.copy2(DESK.parent/'LICENSE',output/'LICENSE')
    shutil.copy2(DESK/'installer/ЛИЦЕНЗИЯ.md',output/'ЛИЦЕНЗИЯ.md')
    shutil.copy2(DESK/'installer/THIRD-PARTY.md',output/'ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md')
    shutil.copy2(DESK/'installer/PYTHON-START-HERE.md',output/'START-HERE.md')
    # The ASR engine is a real optional ZIP, not hidden inside the base runtime.
    for site in (output/'runtime/Lib/site-packages',output/'runtime'):
        for name in ('faster_whisper','ctranslate2','onnxruntime','av','tokenizers'):
            for pattern in (name,name+'.libs',name+'-*.dist-info'):
                for path in site.glob(pattern):
                    if path.is_dir():shutil.rmtree(path)
    app=output/'app';app.mkdir(exist_ok=True)
    if runtime_wheels:install_runtime_dependencies(output/'runtime',runtime_wheels)
    for pth in (output/'runtime').glob('python*._pth'):
        text=pth.read_text(encoding='utf-8')
        for entry in ('..\\app','..\\app\\localharness'):
            if entry not in text.splitlines():text+='\n'+entry+'\n'
        pth.write_text(text,encoding='utf-8')
    for name in ('python_app.py','python_tray.py','python_migrate.py','deskapp.py'):shutil.copy2(DESK/name,app/name)
    for name in ('deskd','localharness'):
        shutil.copytree(DESK/name,app/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if (DESK/'server').is_dir():
        shutil.copytree(DESK/'server',output/'server',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if (app/'desk.json').is_file():
        desk_manifest=json.loads((app/'desk.json').read_text(encoding='utf-8'))
        desk_manifest['version']=version
        (app/'desk.json').write_text(json.dumps(desk_manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if (app/'static').exists():shutil.rmtree(app/'static')
    shutil.copytree(DESK/'app/dist',app/'static')
    shutil.copytree(DESK/'resources',app/'resources',dirs_exist_ok=True)
    shutil.copy2(DESK/'shell/icons/icon.ico',app/'resources/python-app.ico')
    for site in (output/'runtime/Lib/site-packages',output/'runtime'):
        for pattern in ('piper','piper_phonemize*','piper_tts*'):
            for old in site.glob(pattern):
                if old.is_dir():shutil.rmtree(old)
                elif old.is_file():old.unlink()
    for stale in ('helene-build.json','.helene-payload.json'):(output/stale).unlink(missing_ok=True)
    migration=output/'migration';migration.mkdir(exist_ok=True)
    legacy={p.relative_to(stage).as_posix():app_components.digest(p) for p in stage.rglob('*')
            if p.is_file() and p.relative_to(stage).parts[0] not in ('data','agents','backups','tree','components','.git')
            and p.name not in ('helene.json','helene.json.bak','owner-stop.json')}
    (migration/'legacy-package-files.json').write_text(json.dumps({'files':legacy},indent=2),encoding='utf-8')
    entry=component(output,relay,relay_source)
    shutil.copy2(relay,output/'helene-relay.exe')
    if toolchain:
        shared=output/'components/toolchain';shutil.copytree(toolchain,shared)
        inventory={p.relative_to(shared).as_posix():app_components.digest(p) for p in shared.rglob('*') if p.is_file()}
        (shared/'files.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')
    (output/'components/components.json').write_text(json.dumps({'format':'helene-components.v1','components':{'relay':entry}},indent=2),encoding='utf-8')
    (output/'Start Hélène.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\n"runtime\\python.exe" -s "app\\python_app.py" --root "%~dp0." %*\r\n',encoding='utf-8')
    manifest={'format':'helene-python-app.v1','version':version,'entry':'app/python_app.py','window':'pywebview',
        'data':'resolved from helene.json tree','components':'components/components.json',
        'extensions':{'xtts':{'default':True,'license':'CPML non-commercial','assets':['Helene-components-xtts-runtime.zip','Helene-components-xtts-model.zip']},
                      'whisper':{'default':False,'assets':['Helene-components-whisper.zip']},
                      'toolchain':{'default':False,'assets':['Helene-components-toolchain.zip']}}}
    (output/'python-app.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (output/'helene-build.json').write_text(json.dumps({'format':'helene-python-version.v1','product':'Hélène','version':version,
        'platform':'windows','version_source':'python-app.json'},indent=2),encoding='utf-8')
    files={p.relative_to(output).as_posix():app_components.digest(p) for p in output.rglob('*')
           if p.is_file() and p.relative_to(output).parts[0] not in ('data','agents','backups','tree','components')
           and p.relative_to(output).as_posix() not in ('helene.json','python-package-files.json','owner-stop.json','python-migration.json')}
    (output/'python-package-files.json').write_text(json.dumps({'format':'helene-python-program-files.v1','version':version,'files':files},indent=2),encoding='utf-8')
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','output','relay','relay-source'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--toolchain',type=Path,help='Optional offline compiler, never required by the app')
    parser.add_argument('--version',required=True)
    parser.add_argument('--runtime-wheels',type=Path,help='Hash-pinned private-target runtime additions')
    args=parser.parse_args()
    assemble(args.stage,args.output,args.relay,args.relay_source,version=args.version,toolchain=args.toolchain,runtime_wheels=args.runtime_wheels)
if __name__=='__main__':main()
