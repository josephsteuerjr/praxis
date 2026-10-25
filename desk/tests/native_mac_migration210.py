"""Real Cocoa migration window, package-independent profile and restart proof."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,time,urllib.request,zipfile

def run(old,archive,repo,evidence):
    old=Path(old).resolve();evidence=Path(evidence).resolve();evidence.mkdir(exist_ok=True)
    workspace=old.parent/'Mac migration acceptance';workspace.mkdir()
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            p=workspace/item.filename
            if item.is_dir():p.mkdir(parents=True,exist_ok=True);continue
            p.parent.mkdir(parents=True,exist_ok=True)
            with z.open(item) as a,p.open('wb') as b:shutil.copyfileobj(a,b)
            p.chmod((item.external_attr>>16)&0o777 or 0o644)
    root=workspace/'Helene'
    for rel in ('python_app.py','python_migrate.py','mac_migrate.py','resources/mac-migration.html',
                'localharness/app_migration.py','localharness/mac_migration.py','localharness/parallel_files.py',
                'localharness/portable_profile.py','localharness/python_source_updater.py','localharness/code_transition.py'):
        shutil.copy2(repo/'desk'/rel,root/'app'/rel)
    for rel in ('localharness/component_downloads.py','localharness/xtts_install.py'):
        shutil.copy2(repo/'desk'/rel,root/'app'/rel)
    subprocess.run([str(root/'runtime/bin/python3'),str(repo/'desk/installer/python_mac_bundles.py'),str(root),'2.1.0'],check=True)
    shutil.copytree(repo/'desk/app/dist',root/'app/static',dirs_exist_ok=True)
    from native_mac_privacy210 import instrument,deny_network_prompts
    instrument(root,evidence)
    meta=json.loads((root/'python-app.json').read_text());meta.update(version='2.1.0',delivery='portable')
    (root/'python-app.json').write_text(json.dumps(meta))
    result={'root':str(root),'old':str(old),'steps':{}}
    def save():(evidence/'migration.json').write_text(json.dumps(result,indent=2))
    def wait(fn,seconds=180):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            value=fn()
            if value:return value
            time.sleep(.3)
        raise TimeoutError('Native migration condition not observed')
    def control():
        for p in root.glob('.python-window-*.json'):
            try:
                v=json.loads(p.read_text());os.kill(v['pid'],0)
                with urllib.request.urlopen(v['origin']+'/config.js',timeout=1) as r:
                    if r.status==200:return v
            except (OSError,ValueError):pass
    def invoke(name,args=None):
        v=wait(control)
        req=urllib.request.Request(v['origin']+'/__python__/invoke',data=json.dumps({'command':name,'args':args or {}}).encode(),headers={'Content-Type':'application/json','X-Helene-Window':v['token']})
        with urllib.request.urlopen(req,timeout=240) as r:return json.load(r)['result']
    def sha(p):
        with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
    def facts(program):
        cfg=json.loads((program/'helene.json').read_text());code=(program/cfg['code']).resolve();data=(program/cfg['tree']).resolve()
        return {'memory':sha(data/'memory/history.txt'),'identity':sha(data/'soul/identity.md'),'index':sha(code/'.git/index'),
            'head':subprocess.check_output(['git','-C',code,'rev-parse','HEAD'],text=True).strip(),
            'authored':sha(code/'fixture_author.py'),'untracked':sha(code/'fixture_untracked.py'),'custom':cfg['fixture_custom']}
    def screen(name):subprocess.run(['screencapture','-x',str(evidence/name)],check=True)
    save();before=facts(old)
    try:
        subprocess.run(['open','-n',root/'Migrate Helene.app'],check=True);wait(control);time.sleep(2)
        deny_network_prompts(evidence)
        screen('migration-offer.png');result['steps']['native_migration_window']=True;save()
        prepared=invoke('migration_prepare',{'old':str(old),'data':str(workspace/'Preserved profile'),'closed':True})
        assert prepared['state']=='prepared';assert facts(root)==before
        result['steps']['profile_git_exact']=True;screen('prepared.png');save()
        # Close the wizard before starting the target through its ordinary
        # Finder entry: no competing window descriptor or old controller.
        invoke('python_window',{'action':'exit','relaunch':False});wait(lambda:control() is None,40)
        subprocess.run(['open','-n',root/'Helene.app'],check=True);wait(control)
        wait(lambda:json.loads((root/'python-migration.json').read_text()).get('state')=='new_app_started')
        assert facts(root)==before and facts(old)==before
        result['steps']['new_native_profile_confirmed']=True;screen('migrated.png');save()
        invoke('python_window',{'action':'exit','relaunch':False});wait(lambda:control() is None,40)
    except BaseException as exc:
        result['error']=type(exc).__name__+': '+str(exc);save();screen('failure.png')
        for name in ('startup-error.log','migration-start.log','mac-migration-status.json'):
            if (root/name).is_file():shutil.copy2(root/name,evidence/name)
        raise
    return result
