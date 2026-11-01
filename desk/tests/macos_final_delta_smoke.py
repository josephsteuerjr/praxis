"""Fresh native GUI consumer after a shared UI/runtime-hand-only source delta."""
from pathlib import Path
import json,os,shutil,subprocess,sys,time,zipfile

def prepare():
    repo=Path(__file__).resolve().parents[2];temp=Path(os.environ['RUNNER_TEMP']).resolve()
    sys.path[:0]=[str(repo/'desk/tests'),str(repo/'desk/installer')]
    from build_python_macos_rc import unpack,digest
    outer=temp/'qualified-release-artifact.zip';assert digest(outer)==os.environ['ARTIFACT_SHA256']
    with zipfile.ZipFile(outer) as z:
        row=next(i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip')
        archive=temp/'delta-smoke.zip';archive.write_bytes(z.read(row))
    assert digest(archive)==os.environ['CANDIDATE_SHA256']
    unpack(archive,temp/'delta-smoke-install');program=temp/'delta-smoke-install/Helene'
    shutil.copytree(program/'runtime',temp/'delta-smoke-driver-runtime')
    python=temp/'delta-smoke-driver-runtime/bin/python3'
    os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),'subject'])

def smoke(program):
    import psutil
    import rc18_macos_pipeline as native
    program=Path(program).resolve();temp=Path(os.environ['RUNNER_TEMP']);out=native.EVIDENCE
    home=temp/'shared-delta-smoke-home';home.mkdir()
    env=dict(os.environ,HOME=str(home),CFFIXED_USER_HOME=str(home),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    processes=[];logs=[];roots=[program];data=home/'Helene'
    result={'archive_source':subprocess.check_output(['git','rev-parse','HEAD'],cwd=native.REPO,text=True).strip(),
        'scope':'Actual new package GUI/Exit/move. Frozen native/body/runtime/import/10-module gates retained from run41305717386, not re-executed.',
        'retained_qualification_run':41305717386,'steps':{},'physical_owner_acceptance':False}
    def save():native.save('native-shared-delta.json',result)
    def launch(root):
        log=(out/('delta-window-'+str(len(logs))+'.log')).open('wb');logs.append(log)
        p=subprocess.Popen([str(root/'Helene.app/Contents/MacOS/Helene'),'--root',str(root)],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
        processes.append(p);return p
    def ready(root):
        # First-start publishes ui_ready too. Wait for the NEW ordinary RPC
        # endpoint after its Cocoa exec, not the disappearing form receipt.
        native.wait(lambda:native.invoke(root,'app_info').get('root')==str(root),120)
        native.wait(lambda:native.control(root) and native.control(root).get('ui_ready'),120)
    def snapshot(name):time.sleep(2);native.snapshot(name)
    def close(root):
        if native.control(root):native.invoke(root,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
        native.wait(lambda:not native.control(root),75)
    try:
        p=launch(program);native.wait(lambda:native.invoke(program,'first_status').get('stage')=='create',120)
        snapshot('delta-first-start.png');result['steps']['fresh_first_start']=True;save()
        native.invoke(program,'first_create',{'name':'Shared delta Mac fixture','owner':'Disposable CI',
            'provider':'chatgpt','model':'fixture-model'},timeout=240)
        native.invoke(program,'first_open');ready(program)
        info=native.invoke(program,'app_info');assert info['root']==str(program)
        result['steps']['new_native_cocoa_renderer_ready']=True;snapshot('delta-native-window.png');save()
        rows={}
        for name,raw in {'memory/groups/delta-fixture.jsonl':b'{"text":"history retained"}\n',
            'memory/.state/queue.json':b'{"fixture":"queue"}', 'memory/.state/devices.json':b'{"fixture":"device"}',
            'memory/.state/phone-pairs.json':b'{"fixture":"phone"}','workspace/delta.txt':b'owner work'}.items():
            target=data/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);rows[name]=raw
        authored=program/'tree/delta-owner.py';authored.write_bytes(b'OWNER_DELTA=1\n')
        desc=json.loads((data/'memory/.control/desktop-session.json').read_bytes());owner=psutil.Process(desc['pid'])
        tracked=[(q.pid,q.create_time()) for q in [owner,*owner.children(recursive=True)]]
        close(program);native.wait(lambda:p.poll() is not None,75)
        def gone():
            for pid,birth in tracked:
                try:
                    q=psutil.Process(pid)
                    if q.create_time()==birth and q.status()!=psutil.STATUS_ZOMBIE:return False
                except psutil.NoSuchProcess:pass
            return True
        native.wait(gone,75);result['steps']['actual_exit_all_observed_pid_births_absent']=len(tracked);save()
        moved=program.parent/'Moved final Hélène';program.rename(moved);roots.append(moved)
        p=launch(moved);ready(moved);assert native.invoke(moved,'app_info')['root']==str(moved)
        for name,raw in rows.items():assert (data/name).read_bytes()==raw,name
        assert (moved/'tree/delta-owner.py').read_bytes()==b'OWNER_DELTA=1\n'
        result['steps']['unicode_whole_folder_move_profile_history_queue_devices_phone_code_exact']=True
        snapshot('delta-moved-window.png');close(moved);native.wait(lambda:p.poll() is not None,75);save()
    except BaseException as exc:
        result['error']=type(exc).__name__+': '+str(exc);save();native.snapshot('delta-failure.png');raise
    finally:
        for root in roots:
            if root.exists():
                try:close(root)
                except Exception:pass
        for p in processes:
            try:p.wait(15)
            except subprocess.TimeoutExpired:p.terminate();p.wait(10)
        for log in logs:log.close()

if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    else:
        repo=Path(__file__).resolve().parents[2]
        sys.path[:0]=[str(repo/'desk/tests'),str(repo/'desk/installer')]
        smoke(Path(os.environ['RUNNER_TEMP']).resolve()/'delta-smoke-install/Helene')
