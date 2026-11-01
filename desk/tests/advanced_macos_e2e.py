"""Actual user-facing native actions; all credentials/profile data synthetic."""
from pathlib import Path
import json,os,subprocess,sys,time,urllib.request
REPO=Path(__file__).resolve().parents[2];TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
sys.path[:0]=[str(Path(__file__).parent),str(REPO/'desk')]
import extended_macos as common

def wait(fn,timeout=90):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:value=fn()
        except (OSError,ValueError,KeyError):value=None
        if value:return value
        time.sleep(.2)
    raise TimeoutError('Native E2E condition was not observed')
def control(root):
    import psutil
    for p in (root/'.helene/windows').glob('.python-window-*.json'):
        try:
            row=json.loads(p.read_text());q=psutil.Process(row['pid'])
            if q.status()!=psutil.STATUS_ZOMBIE and row['root']==str(root):return row
        except (OSError,ValueError,psutil.Error):pass
def invoke(root,command,args=None):
    row=wait(lambda:control(root));body=json.dumps({'command':command,'args':args or {}}).encode()
    req=urllib.request.Request(row['origin']+'/__python__/invoke',data=body,headers={'Content-Type':'application/json','X-Helene-Window':row['token']})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=180) as r:return json.load(r)['result']
def save(name,value):common.write(name,value)

def ui():
    program,python=common.prepare()
    # The driver uses the verified runtime as well; its own process never moves.
    if sys.executable!=str(python):
        os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),'ui-subject'])
    return ui_subject(program,python)
def ui_subject(program=None,python=None):
    if program is None:program=TEMP/'base/Helene';python=program/'runtime/bin/python3'
    import native_ax as ax
    home=TEMP/'ui-home';home.mkdir();env=dict(os.environ,HOME=str(home),CFFIXED_USER_HOME=str(home),PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
    # Stop only this synthetic profile before creating it; no model/provider.
    subprocess.run([str(python),'-B','-c',"import sys;sys.path.insert(0,sys.argv[1]);from localharness import owner_stop;owner_stop.latch_client('native-ui-fixture')",str(program/'app')],env=env,check=True)
    log=(common.EVIDENCE/'native-ui.log').open('wb')
    p=subprocess.Popen([str(program/'Helene.app/Contents/MacOS/Helene'),'--root',str(program)],cwd=program,env=env,stdout=log,stderr=subprocess.STDOUT)
    result={'source_actions':'native AX focus/press and Quartz keyboard events, not window RPC','trusted_AX':bool(ax.AX.AXIsProcessTrusted()),'steps':{}}
    try:
        row=wait(lambda:control(program));pid=row['pid']
        wait(lambda:any(x['AXRole']=='AXTextField' for x in ax.walk(pid)))
        tree=ax.walk(pid);save('initial-ax.json',[{k:v for k,v in x.items() if k!='element'} for x in tree])
        fields=[x for x in tree if x['AXRole'] in ('AXTextField','AXSecureTextField')]
        assert len(fields)>=4,[(x['AXRole'],x['AXDescription']) for x in fields]
        for label,value in (('Имя агента','Native UI fixture'),('Твоё имя','Disposable CI'),('Модель','fixture-model'),('Ключ доступа','fixture-only-key')):
            ax.fill(ax.find(pid,label,'AXTextField'),value)
            if label!='Ключ доступа':wait(lambda:ax.text(ax.find(pid,label,'AXTextField'),'AXValue')==value,5)
        # Native form submission is the action under test. The synthetic owner
        # Stop prevents even the placeholder model from making a network call.
        ax.press(ax.find(pid,'Создать агента','AXButton'))
        wait(lambda:(home/'Helene/memory/.control/profile-identity.json').is_file())
        wait(lambda:invoke(program,'app_info').get('version')=='2.3.0-rc.18')
        identity=json.loads((home/'Helene/memory/.control/profile-identity.json').read_text())
        assert identity['agent_name']=='Native UI fixture'
        result['steps']['created_agent_via_native_form']=True
        time.sleep(6);row=control(program);pid=row['pid']
        save('window-ax.json',[{k:v for k,v in x.items() if k!='element'} for x in ax.walk(pid)])
        ax.press(ax.find(pid,'Настройки'))
        result['steps']['opened_settings_via_native_control']=True
        time.sleep(2);subprocess.run(['screencapture','-x',str(common.EVIDENCE/'native-settings.png')],check=True)
        # Read-only verification may use the existing local control endpoint.
        result['app_info']={k:v for k,v in invoke(program,'app_info').items() if k in ('version','platform','arch','root')}
        result['steps']['no_provider_credentials_or_generation']=True
        save('NATIVE-UI.json',result)
    except BaseException as e:
        result['error']=type(e).__name__+': '+str(e);save('NATIVE-UI.json',result)
        if p.poll() is None:
            save('failure-ax.json',[{k:v for k,v in x.items() if k!='element'} for x in ax.walk(p.pid)])
            subprocess.run(['screencapture','-x',str(common.EVIDENCE/'failure.png')],check=False)
        raise
    finally:
        if control(program):
            try:invoke(program,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
            except Exception:pass
        try:p.wait(20)
        except subprocess.TimeoutExpired:p.terminate();p.wait(10)
        log.close()

if __name__=='__main__':{'ui':ui,'ui-subject':ui_subject}[sys.argv[1]]()
