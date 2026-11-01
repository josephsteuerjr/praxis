"""Real complete-archive update and rollback on one disposable native Mac."""
from pathlib import Path
import hashlib,http.server,json,os,shutil,ssl,subprocess,sys,threading,time,urllib.error,zipfile
REPO=Path(__file__).resolve().parents[2];TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
OUT=REPO/'evidence-full-update';OUT.mkdir(exist_ok=True)
sys.path[:0]=[str(REPO/'desk/tests'),str(REPO/'desk/installer'),str(REPO/'desk')]
from build_python_macos_rc import unpack
from macos_distribution_qualification import digest

def write(name,value):(OUT/name).write_text(json.dumps(value,indent=2,ensure_ascii=True))
def prepare():
    outer=TEMP/'qualified-release-artifact.zip';assert digest(outer)==os.environ['ARTIFACT_SHA256']
    with zipfile.ZipFile(outer) as z:
        item=next(i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip')
        archive=TEMP/'full-release.zip';archive.write_bytes(z.read(item))
    assert digest(archive)==os.environ['CANDIDATE_SHA256']
    unpack(archive,TEMP/'full-update-driver')
    python=TEMP/'full-update-driver/Helene/runtime/bin/python3'
    os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),'subject'])

def subject():
    import psutil,urllib.request
    import rc18_macos_pipeline as native
    from localharness import posix_first_start as first,owner_stop,desktop_update_request as jobs
    from localharness.package_notice import read,write as store
    home=TEMP/'full-update-home';home.mkdir()
    os.environ.update(HOME=str(home),CFFIXED_USER_HOME=str(home),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    places=first.Places(home/'Helene')
    result={'archive_sha256':os.environ['CANDIDATE_SHA256'],'real_complete_release_archive':True,
        'synthetic_profile_only':True,'semantic_model_review_accepted':False,'steps':{}}
    recheck=os.environ.get('UPDATE_RECHECK')=='failed-start'
    if recheck:result['previous_real_https_clean_and_support_run']=41296826304
    write('FULL-UPDATE.json',result)
    provider_calls=[]
    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))));provider_calls.append(self.path)
            text='Disposable local CI response.'
            response={'id':'chatcmpl-fixture','object':'chat.completion','created':int(time.time()),'model':'fixture',
                'choices':[{'index':0,'message':{'role':'assistant','content':text},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
            if body.get('stream'):
                data=('data: '+json.dumps({'id':'chatcmpl-fixture','object':'chat.completion.chunk','choices':[{'index':0,'delta':{'role':'assistant','content':text},'finish_reason':None}]})+'\n\n'+
                      'data: '+json.dumps({'id':'chatcmpl-fixture','object':'chat.completion.chunk','choices':[{'index':0,'delta':{},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n').encode()
                mime='text/event-stream'
            else:data=json.dumps(response).encode();mime='application/json'
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    provider=http.server.ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=provider.serve_forever,daemon=True).start()
    archive=TEMP/'full-release.zip';unpack(archive,TEMP/'full-update-install')
    root=TEMP/'full-update-install/Helene';python=root/'runtime/bin/python3'
    first.prepare(root,places=places)
    first.create_agent(root,{'name':'Full archive fixture','owner':'Disposable CI','provider':'openai','model':'fixture',
        'key':'fixture-local-key','base_url':f'http://127.0.0.1:{provider.server_port}/v1'},places=places)
    owner_stop.change_pause(places.data,True,reason='native archive qualification',via='fixture')
    cfg=read(root/'helene.json');cfg['fixture_custom']={'preserve':'exact'};store(root/'helene.json',cfg)
    sentinels={'memory/groups/archive-fixture.jsonl':b'{"text":"preserve history"}\n',
        'memory/.state/devices.json':b'{"ci":"device"}', 'memory/.state/phone-pairs.json':b'{"ci":"pair"}',
        'workspace/archive-owner.txt':b'owner material'}
    for name,raw in sentinels.items():
        p=places.data/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    logs=[];processes=[]
    def launch():
        log=(OUT/('window-'+str(len(logs))+'.log')).open('wb');logs.append(log)
        p=subprocess.Popen([str(root/'Helene.app/Contents/MacOS/Helene'),'--root',str(root)],cwd=root,env=dict(os.environ),stdout=log,stderr=subprocess.STDOUT)
        processes.append(p);native.wait(lambda:native.control(root));return p
    def info():return native.invoke(root,'app_info')
    def data_exact():
        for name,raw in sentinels.items():assert (places.data/name).read_bytes()==raw,name
        assert read(root/'helene.json')['fixture_custom']=={'preserve':'exact'}
    def selected_code():
        cfg=read(root/'helene.json');p=Path(cfg.get('code','tree'));return (p if p.is_absolute() else root/p).resolve()
    def job_public():
        row=jobs.status(places.data)
        return {k:row.get(k) for k in ('id','state','phase','error','source_mode','staging','transferred_to') if k in row}
    def finish(identity,timeout=300,failed=False):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            row=jobs.status(places.data);write('current-job.json',job_public())
            if row.get('id')==identity:
                if not failed and row.get('state')=='completed':return row
                if row.get('state') in ('attention','cancelled','superseded'):
                    if failed:return row
                    raise RuntimeError('Full archive update refused: '+str(job_public()))
            time.sleep(.2)
        raise TimeoutError('Full archive update did not settle: '+str(job_public()))
    def screenshot(name):subprocess.run(['screencapture','-x',str(OUT/name)],check=True)
    cert=OUT/'localhost-cert.pem';key=TEMP/'localhost-key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost',
        '-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
    os.environ['SSL_CERT_FILE']=str(cert)
    serve=TEMP/'full-update-https';serve.mkdir()
    name='Helene-2.3.0-rc.18-macos-arm64.zip';shutil.copy2(archive,serve/name)
    class Archive(http.server.SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(serve),**kwargs)
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Archive)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'https://localhost:{server.server_port}/{name}'
    stop_watch=None
    try:
        launch();native.wait(lambda:info().get('version')=='2.3.0-rc.18');data_exact()
        if recheck:
            from localharness.program_state import directory
            manual=directory(root,'downloads')/name;shutil.copy2(archive,manual)
            assert digest(manual)==os.environ['CANDIDATE_SHA256']
            downloaded={'path':str(manual)}
        else:
            diag=root/'.helene/logs/archive-fixture.log';diag.parent.mkdir(parents=True,exist_ok=True)
            diag.write_text('native archive fixture api_key="fixture-local-key"\n')
            support=Path(native.invoke(root,'logs_bundle'))
            with zipfile.ZipFile(support) as z:
                assert z.testzip() is None
                text=b'\n'.join(z.read(n) for n in z.namelist())
                assert b'fixture-local-key' not in text and b'native archive fixture' in text
            shutil.copy2(support,OUT/'redacted-support.zip');result['steps']['actual_support_export_masks_model_key']=True
            try:native.invoke(root,'update_download',{'url':url,'sha256':'0'*64,'extensions':[]})
            except urllib.error.HTTPError:result['steps']['wrong_hash_refused_old_window_retained']=bool(info())
            else:raise AssertionError('Wrong archive SHA was accepted')
            downloaded=native.invoke(root,'update_download',{'url':url,'sha256':os.environ['CANDIDATE_SHA256'],'extensions':[]},timeout=300)
            assert downloaded['sha_ok'] and digest(downloaded['path'])==os.environ['CANDIDATE_SHA256']
            result['steps']['full_https_download_exact']=True;write('FULL-UPDATE.json',result)
            admission=native.invoke(root,'update_install',{'path':downloaded['path'],'sourceMode':'clean'})
            write('clean-admission.json',{k:v for k,v in admission.items() if k not in ('token','key')})
            identity=admission.get('id') or admission.get('job',{}).get('id')
            assert identity,admission
            finish(identity);native.wait(lambda:info().get('version')=='2.3.0-rc.18',120);data_exact()
            result['steps']['real_full_archive_clean_update_and_native_restart']=True;screenshot('after-clean.png');write('FULL-UPDATE.json',result)
        # Force only the newly launched, exact-path migration child to fail.
        # All archive bytes and updater logic remain the real distribution.
        code=selected_code();(code/'archive-owner-change.py').write_bytes(b'OWNER_CHANGE=1\n')
        before_cfg=(root/'helene.json').read_bytes();before_file=(code/'archive-owner-change.py').read_bytes()
        stop_watch=threading.Event();killed=[]
        existing_pid=native.control(root)['pid'];admission_started=time.time()
        def fail_new_start():
            while not stop_watch.is_set():
                for p in psutil.process_iter(['pid','cmdline','create_time']):
                    try:
                        args=p.info.get('cmdline') or []
                        if (p.pid!=existing_pid and p.info['create_time']>=admission_started-.05
                                and '--migration' in args and str(root/'app/python_app.py') in args and str(root) in args):
                            born=p.info['create_time']
                            if psutil.Process(p.pid).create_time()==born:
                                p.kill();killed.append({'pid':p.pid,'birth':born});return
                    except (psutil.NoSuchProcess,psutil.AccessDenied):pass
                time.sleep(.01)
        watcher=threading.Thread(target=fail_new_start,daemon=True);watcher.start()
        admission=native.invoke(root,'update_install',{'path':downloaded['path'],'sourceMode':'clean'})
        identity=admission.get('id') or admission.get('job',{}).get('id');assert identity
        finish(identity,failed=True);stop_watch.set();watcher.join(3)
        assert len(killed)==1,'No specifically observed new startup child was faulted'
        native.wait(lambda:info().get('version')=='2.3.0-rc.18',120)
        assert (code/'archive-owner-change.py').read_bytes()==before_file
        assert (root/'helene.json').read_bytes()==before_cfg
        data_exact();result['steps']['full_archive_failed_start_rolls_back_and_reopens_previous']=killed
        screenshot('after-rollback.png');write('FULL-UPDATE.json',result)
        result['provider_calls']=provider_calls;result['steps']['no_owner_account_or_live_provider']=True
        write('FULL-UPDATE.json',result)
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error);result['job']=job_public();result['provider_calls']=provider_calls
        write('FULL-UPDATE.json',result)
        for folder in (root/'.helene/logs',places.data/'memory/.control'):
            for p in folder.glob('*.log'):
                if p.stat().st_size<2*1024*1024:shutil.copy2(p,OUT/p.name)
        screenshot('failure.png');raise
    finally:
        if stop_watch is not None:stop_watch.set()
        try:
            if native.control(root):native.invoke(root,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
        except Exception:pass
        for p in processes:
            try:p.wait(15)
            except subprocess.TimeoutExpired:p.terminate();p.wait(10)
        for log in logs:log.close()
        server.shutdown();provider.shutdown()

if __name__=='__main__':{'prepare':prepare,'subject':subject}[sys.argv[1]]()
