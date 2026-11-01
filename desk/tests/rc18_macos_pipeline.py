"""Build/qualify RC18 only on a disposable standard Apple Silicon runner."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,os,shutil,subprocess,sys,time,urllib.request,zipfile

REPO=Path(__file__).resolve().parents[2]
EVIDENCE=REPO/'evidence-rc18';EVIDENCE.mkdir(exist_ok=True)
TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
PROGRAM=TEMP/'rc18-output/Helene'

def save(name,value):
    (EVIDENCE/name).write_text(json.dumps(value,indent=2,ensure_ascii=True),encoding='utf-8')

def run(command,log,*,cwd=REPO,env=None,timeout=600):
    with (EVIDENCE/log).open('wb') as f:
        p=subprocess.run(list(map(str,command)),cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
    return p.returncode

def fronts():
    assert os.uname().machine=='arm64'
    outputs={'app':'app/dist','mobile':'mobile/dist','miniapp':'miniapp/dist','remote':'remote/dist','setup/ui':'setup/dist'}
    names=tuple(outputs);jobs=os.cpu_count() or 3
    sys.path.insert(0,str(REPO/'desk/installer'))
    from portable_sources import frontend_inputs,frontend_outputs
    before=frontend_inputs(REPO)
    cache=TEMP/'rc18-fronts'
    if (cache/'receipt.json').is_file():
        receipt=json.loads((cache/'receipt.json').read_text())
        assert receipt['inputs']==before,'Cached frontend source differs'
        for name in names:shutil.copytree(cache/name,REPO/'desk'/outputs[name])
        assert frontend_outputs(REPO)==receipt['outputs'],'Cached frontend bytes differ'
        save('frontend-build.json',dict(receipt,reused_exact=True));return
    def install(name):return name,run(['npm','ci','--prefix',REPO/'desk'/name],'npm-ci-'+name.replace('/','-')+'.log')
    with ThreadPoolExecutor(max_workers=jobs) as pool:installed=dict(pool.map(install,names))
    save('frontend-install.json',installed);assert not any(installed.values()),installed
    def build(name):return name,run(['npm','run','build','--prefix',REPO/'desk'/name],'build-'+name.replace('/','-')+'.log')
    with ThreadPoolExecutor(max_workers=jobs) as pool:built=dict(pool.map(build,names))
    save('frontend-commands.json',built)
    assert not any(built.values()),built
    assert before==frontend_inputs(REPO),'Frontend inputs changed during build'
    receipt={'cpu_count':jobs,'commands':built,'inputs':before,'outputs':frontend_outputs(REPO)}
    save('frontend-build.json',receipt)
    cache.mkdir()
    for name in names:shutil.copytree(REPO/'desk'/outputs[name],cache/name)
    (cache/'receipt.json').write_text(json.dumps(receipt),encoding='utf-8')

def key():
    sys.path.insert(0,str(REPO/'desk/installer'))
    from portable_sources import frontend_inputs
    # Include the fifth setup frontend's complete tracked compiler input too.
    rows=frontend_inputs(REPO)
    for p in (REPO/'desk/setup/ui').rglob('*'):
        if p.is_file() and not any(x in ('node_modules','dist') for x in p.relative_to(REPO).parts):
            rows[p.relative_to(REPO).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    print('key='+hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest())

def wait(fn,seconds=90):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        try:value=fn()
        except (OSError,ValueError,KeyError):value=None
        if value:return value
        time.sleep(.15)
    raise TimeoutError('Native consumer condition was not observed')

def control(root):
    import psutil
    for p in [*(root/'.helene/windows').glob('.python-window-*.json'),*root.glob('.python-window-*.json')]:
        try:
            value=json.loads(p.read_text())
            proc=psutil.Process(value['pid'])
            if proc.status()!=psutil.STATUS_ZOMBIE and value['root']==str(root):return value
        except (OSError,ValueError,psutil.Error):pass

def invoke(root,name,args=None,timeout=90):
    value=wait(lambda:control(root))
    request=urllib.request.Request(value['origin']+'/__python__/invoke',data=json.dumps({'command':name,'args':args or {}}).encode(),
        headers={'Content-Type':'application/json','X-Helene-Window':value['token']})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=timeout) as r:return json.load(r)['result']

def snapshot(name):
    subprocess.run(['screencapture','-x',str(EVIDENCE/name)],check=True)

def deny_optional_prompts():
    script='''tell application "System Events"
repeat with p in (processes whose name is "UserNotificationCenter" or name is "CoreServicesUIAgent")
repeat with w in windows of p
repeat with b in (buttons of w whose name is "Don't Allow")
click b
end repeat
end repeat
end repeat
end tell'''
    p=subprocess.run(['osascript','-e',script],capture_output=True,text=True,timeout=12)
    save('privacy-denial.json',{'returncode':p.returncode})

def native():
    assert sys.platform=='darwin' and os.uname().machine=='arm64'
    import psutil
    home=TEMP/'rc18-qualification-home';home.mkdir()
    for folder in ('Desktop','Library','Downloads'): (home/folder).mkdir()
    baseenv=dict(os.environ,HOME=str(home),CFFIXED_USER_HOME=str(home),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    python=PROGRAM/'runtime/bin/python3'
    result={'target':'macos-arm64','version':'2.3.0-rc.18','cpu_count':os.cpu_count(),'steps':{},
        'physical_acceptance':{'TCC':'not owner accepted','microphone':'not tested','owner_Mac':'not tested'}}
    save('native.json',result)
    modules=['t_posix_first_start_0111','t_darwin_owner_2410','t_python_native_update_2410',
             't_update_modes_229','t_authored_baseline_3110','t_app_source_update_3110',
             't_pwa_continuity_0111','t_phone_settings_0111','t_phone_surface_0111','t_phone_stable_0111']
    def test(name):
        testhome=home/name;testhome.mkdir()
        env=dict(baseenv,HOME=str(testhome),CFFIXED_USER_HOME=str(testhome))
        return name,run([python,'-B','-m','unittest','tests.'+name,'-v'],name+'.log',cwd=REPO/'desk',env=env)
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 3) as pool:tests=dict(pool.map(test,modules))
    result['focused_tests']=tests;save('native.json',result)
    # Always collect real GUI evidence even if a source-focused lane has a
    # regression; final success still requires every intended test to pass.
    logs=[];children=[];roots=[PROGRAM]
    def launch(root):
        log=(EVIDENCE/('window-'+str(len(logs))+'.log')).open('wb');logs.append(log)
        p=subprocess.Popen([str(root/'Helene.app/Contents/MacOS/Helene'),'--root',str(root)],env=baseenv,cwd=root,stdout=log,stderr=subprocess.STDOUT)
        children.append(p);return p
    def exit(root):
        value=control(root)
        if value:
            invoke(root,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
            wait(lambda:control(root) is None,45)
    try:
        p=launch(PROGRAM);wait(lambda:control(PROGRAM))
        wait(lambda:invoke(PROGRAM,'first_status').get('stage')=='create')
        time.sleep(2);deny_optional_prompts();snapshot('01-first-start.png')
        result['steps']['fresh_first_start']=True;save('native.json',result)
        # The real subscription path installs the pinned optional Mac relay;
        # no account/login/provider request is performed by this consumer.
        invoke(PROGRAM,'first_create',{'name':'RC18 Mac fixture','owner':'Disposable CI','provider':'chatgpt','model':'fixture-model'},timeout=240)
        result['steps']['subscription_component_installed']=(PROGRAM/'relay-product/praxis-relay-headless').is_file()
        invoke(PROGRAM,'first_open')
        wait(lambda:control(PROGRAM) and control(PROGRAM).get('ui_ready'))
        wait(lambda:invoke(PROGRAM,'app_info').get('version')=='2.3.0-rc.18')
        time.sleep(5);deny_optional_prompts();snapshot('02-agent-window.png')
        info=invoke(PROGRAM,'app_info');result['app_info']={k:v for k,v in info.items() if k in ('version','platform','arch','root','exe_dir')}
        result['steps']['native_window']=True;save('native.json',result)
        # Body/bridge use the actual product's native launch/shutdown route.
        result['body_contract']=run([python,'-B','-m','unittest','tests.t_body_macos.Contract','-v'],'body-contract.log',
            cwd=REPO/'desk',env=dict(baseenv,HELENE_BODY_DIR=str(PROGRAM)),timeout=300)
        data=home/'Helene'
        preserved={}
        for name,body in {'memory/groups/fixture.jsonl':'{"text":"preserve conversation"}\n',
            'memory/.state/queue.json':'{"queued":["owner work"]}','memory/.state/devices.json':'{"fixture":{}}',
            'memory/.state/phone-pairs.json':'{"fixture":{}}','workspace/custom.txt':'owner work'}.items():
            f=data/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(body);preserved[name]=f.read_bytes()
        custom=PROGRAM/'tree/rc18-owner-edit.py';custom.write_text('# owner edit survives folder switches\n')
        # Exit is measured against actual PID birth identities, not only a
        # disappeared receipt or a returned HTTP reply.
        desc=json.loads((data/'memory/.control/desktop-session.json').read_text());owner=psutil.Process(desc['pid'])
        tracked=[(x.pid,x.create_time()) for x in [owner,*owner.children(recursive=True)]]
        exit(PROGRAM);wait(lambda:p.poll() is not None,45)
        def gone():
            for pid,birth in tracked:
                try:
                    q=psutil.Process(pid)
                    if q.create_time()==birth and q.status()!=psutil.STATUS_ZOMBIE:return False
                except psutil.NoSuchProcess:pass
            return True
        wait(gone,45);result['steps']['explicit_exit_all_observed_descendants']=True
        # Move the entire portable code folder; data and durable phone state
        # remain external. Reopening checks its exact candidate passport.
        moved=PROGRAM.parent/'Moved Hélène';PROGRAM.rename(moved);roots.append(moved)
        p=launch(moved);wait(lambda:invoke(moved,'app_info').get('root')==str(moved))
        for name,body in preserved.items():assert (data/name).read_bytes()==body,name
        assert (moved/'tree/rc18-owner-edit.py').read_text().startswith('# owner edit')
        result['steps']['folder_move_profile_queue_pairing_code_exact']=True
        time.sleep(2);snapshot('03-moved-window.png');exit(moved);wait(lambda:p.poll() is not None,45)
        # Full overlay/recovery consumers run above. This adds an independent
        # real-filesystem rollback with the shipped runtime and a later edit.
        sys.path.insert(0,str(moved/'app'))
        from localharness import python_source_updater as updater
        old=TEMP/'rollback-old';new=TEMP/'rollback-new';recovery=TEMP/'rollback-recovery'
        old.mkdir();new.mkdir();(old/'file.py').write_text('previous\n');(new/'file.py').write_text('incoming\n')
        updater.apply_files(new,old,recovery);assert (old/'file.py').read_text()=='incoming\n'
        (old/'file.py').write_text('later owner edit\n');updater.rollback(old,recovery)
        assert (old/'file.py').read_text()=='previous\n'
        assert (recovery/'later-self-edits/file.py').read_text()=='later owner edit\n'
        result['steps']['actual_overlay_and_rollback_retains_later_edit']=True
        result['steps']['no_provider_login_or_owner_data']=True
        save('native.json',result)
        assert not any(tests.values()),'Focused regressions; see individual logs'
        assert result['body_contract']==0,'Native body/bridge contract failed'
    except BaseException as exc:
        result['error']=type(exc).__name__+': '+str(exc);save('native.json',result);snapshot('failure.png')
        raise
    finally:
        for root in roots:
            if root.exists():
                try:exit(root)
                except Exception:pass
        for p in children:
            try:p.wait(15)
            except subprocess.TimeoutExpired:p.terminate();p.wait(10)
        for log in logs:log.close()

if __name__=='__main__':
    {'fronts':fronts,'native':native,'key':key}[sys.argv[1]]()
