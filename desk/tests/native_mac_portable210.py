"""Actual macOS bundle baseline in a disposable CI home, without model calls."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys,time,urllib.request,zipfile

repo=Path(__file__).resolve().parents[2]
evidence=repo/'evidence210';evidence.mkdir(exist_ok=True)
work=Path(os.environ['RUNNER_TEMP'])/'Helene native audit'
work.mkdir(exist_ok=True)
archive=work/'public.zip'
url='https://github.com/josephsteuerjr/praxis/releases/download/v2.0.0/Helene-2.0.0-macos-arm64.zip'
urllib.request.urlretrieve(url,archive)
with archive.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
assert sha=='c237f4d29584662d49c6f7c126453139dbd0171dce7ac153cb90d29794525a74'
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    for item in z.infolist():
        path=work/item.filename
        if item.is_dir():path.mkdir(parents=True,exist_ok=True);continue
        path.parent.mkdir(parents=True,exist_ok=True)
        with z.open(item) as a,path.open('wb') as b:shutil.copyfileobj(a,b)
        path.chmod((item.external_attr>>16)&0o777 or 0o644)
root=work/'Helene';runtime=root/'runtime/bin/python3'
for relative in ('python_app.py','python_migrate.py','mac_migrate.py','resources/mac-migration.html',
                 'localharness/python_source_updater.py','localharness/python_updates.py',
                 'localharness/app_migration.py','localharness/portable_profile.py',
                 'localharness/parallel_files.py','localharness/code_transition.py',
                 'localharness/component_downloads.py','localharness/xtts_install.py'):
    shutil.copy2(repo/'desk'/relative,root/'app'/relative)
metadata=json.loads((root/'python-app.json').read_text());metadata.update(version='2.1.0',delivery='portable')
(root/'python-app.json').write_text(json.dumps(metadata,indent=2))
subprocess.run([sys.executable,str(repo/'desk/installer/python_mac_bundles.py'),str(root),'2.1.0'],check=True)
shutil.copytree(repo/'desk/app/dist',root/'app/static',dirs_exist_ok=True)
from native_mac_privacy210 import instrument,deny_network_prompts
instrument(root,evidence)
result={'public_sha256':sha,'platform':os.uname().machine,'root':str(root),'steps':{}}
def save():(evidence/'native.json').write_text(json.dumps(result,indent=2))
def wait(fn,seconds=100):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(.3)
    raise TimeoutError('Native condition not observed')
def control():
    for path in root.glob('.python-window-*.json'):
        try:
            value=json.loads(path.read_text());os.kill(value['pid'],0)
            if value['root']==str(root):return value
        except (OSError,ValueError):pass
def invoke(name,args=None):
    row=wait(control)
    req=urllib.request.Request(row['origin']+'/__python__/invoke',data=json.dumps({'command':name,'args':args or {}}).encode(),headers={'Content-Type':'application/json','X-Helene-Window':row['token']})
    with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)['result']
save()
try:
    tests=['tests.t_python_native_update_2410','tests.t_python_migration','tests.t_posix_owner_2410','tests.t_component_downloads_193','tests.t_linux_extensions_2410']
    with (evidence/'unit.log').open('w') as log:
        done=subprocess.run([str(runtime),'-m','unittest',*tests,'-v'],cwd=repo/'desk',stdout=log,stderr=subprocess.STDOUT,timeout=600)
    result['steps']['focused_tests']=done.returncode;save()
    subprocess.run(['open','-n',str(root/'Helene.app')],check=True)
    row=wait(control);result['steps']['native_window']=True
    result['app_info']=invoke('app_info');save()
    time.sleep(4)
    deny_network_prompts(evidence)
    subprocess.run(['screencapture','-x',str(evidence/'fresh-window.png')],check=True)
    subprocess.run(['open',str(root/'Helene.app')],check=True)
    time.sleep(2)
    live=[]
    for p in root.glob('.python-window-*.json'):
        value=json.loads(p.read_text())
        try:os.kill(value['pid'],0);live.append(value['pid'])
        except OSError:pass
    result['steps']['repeat_live_windows']=len(set(live));save()
    invoke('python_window',{'action':'exit','relaunch':False})
    wait(lambda:control() is None,40);result['steps']['explicit_exit']=True;save()
    subprocess.run(['open','-n',str(root/'Helene.app')],check=True)
    wait(control);result['steps']['reopen']=True;save()
    invoke('python_window',{'action':'exit','relaunch':False});wait(lambda:control() is None,40)
    assert done.returncode==0,'Focused test failure; see unit.log'
    assert result['steps']['repeat_live_windows']==1,'Repeated launch created more than one native window'
    from native_portable_update210 import run as native_update
    result['native_update']=native_update(root,evidence/'update');save()
    deny_network_prompts(evidence)
    from native_mac_migration210 import run as native_migration
    result['native_migration']=native_migration(root,archive,repo,evidence/'migration');save()
except BaseException as exc:
    result['error']=type(exc).__name__+': '+str(exc);save()
    subprocess.run(['screencapture','-x',str(evidence/'failure.png')],check=False)
    for name in ('startup-error.log','python-app.log'):
        if (root/name).is_file():shutil.copy2(root/name,evidence/name)
    raise
