"""Real complete-archive update and rollback on one disposable native Mac."""
from pathlib import Path
import hashlib,http.server,json,os,re,shutil,ssl,subprocess,sys,threading,time,urllib.error,zipfile
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
    phase='legacy' if os.environ.get('UPDATE_SCENARIO')=='legacy' else 'subject'
    os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),phase])


def legacy():
    import datetime,psutil
    import rc18_macos_pipeline as native
    from localharness import posix_first_start as first,owner_stop
    from localharness.package_notice import read,write as store
    old_evidence=TEMP/'rc18-evidence.zip'
    assert digest(old_evidence)=='6eb05a4b19951fa9cc27caaca36de0024a5e73fcb5982503798f98959ddc1b91'
    with zipfile.ZipFile(old_evidence) as z:
        previous=TEMP/'actual-previous.zip';previous.write_bytes(z.read('Helene-2.1.0-macos-arm64.zip'))
    assert digest(previous)=='5e4fe949b0ed44c95d9cdd8dbd20885e3ac2f887eaf41901cdbda8b46ec5a921'
    unpack(previous,TEMP/'legacy-install');root=TEMP/'legacy-install/Helene'
    home=TEMP/'legacy-home';home.mkdir()
    os.environ.update(HOME=str(home),CFFIXED_USER_HOME=str(home),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    places=first.Places(home/'Helene')
    # Seed only a synthetic profile/config. No old application/runtime/source
    # byte is replaced before its own real update command runs.
    first.ensure_config(root,places=places,refresh_profile=True)
    first.common._write(first.common.receipt(root),{'format':first.common.FORMAT,'state':'completed',
        'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'data':str(places.data)})
    first.create_agent(root,{'name':'Actual previous 2.1','owner':'Disposable CI','provider':'openai',
        'model':'fixture','key':'fixture-unused-local-key','base_url':'http://127.0.0.1:9/v1'},places=places)
    owner_stop.change_pause(places.data,True,reason='legacy fixture stays paused',via='fixture')
    cfg=read(root/'helene.json');cfg['fixture_legacy_custom']={'keep':21};store(root/'helene.json',cfg)
    saved={'memory/groups/legacy-fixture.jsonl':b'{"text":"old history"}\n',
        'workspace/legacy-owner.txt':b'old owner data','memory/.state/devices.json':b'{"legacy":"device"}'}
    for name,raw in saved.items():
        p=places.data/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    archive=root.parent/'Helene-2.3.0-rc.18-macos-arm64.zip';shutil.copy2(TEMP/'full-release.zip',archive)
    result={'previous_version':'2.1.0','previous_sha256':digest(previous),'incoming_sha256':digest(archive),
        'profile_setup':'synthetic config only; unmodified actual old application and runtime', 'steps':{}}
    log=(OUT/'legacy-window.log').open('wb');p=None
    try:
        p=subprocess.Popen([str(root/'Helene.app/Contents/MacOS/Helene'),'--root',str(root)],cwd=root,
            env=dict(os.environ),stdout=log,stderr=subprocess.STDOUT)
        native.wait(lambda:native.invoke(root,'app_info').get('version')=='2.1.0',120)
        result['steps']['actual_previous_native_window']=True;write('LEGACY-UPDATE.json',result)
        admission=native.invoke(root,'update_install',{'path':str(archive)},timeout=300)
        write('legacy-admission.json',admission)
        native.wait(lambda:native.invoke(root,'app_info').get('version')=='2.3.0-rc.18',360)
        assert read(root/'helene.json')['fixture_legacy_custom']=={'keep':21}
        for name,raw in saved.items():assert (places.data/name).read_bytes()==raw,name
        current=psutil.Process(native.control(root)['pid'])
        assert str(root/'Helene.app/Contents/MacOS/Helene') in current.cmdline()
        result['steps']['real_previous_to_current_full_archive_native_update_data_exact']=True
        result['steps']['cocoa_identity_retained']=current.cmdline()
        native.wait(lambda:native.control(root) and native.control(root).get('ui_ready'),90);time.sleep(2)
        subprocess.run(['screencapture','-x',str(OUT/'after-legacy-upgrade.png')],check=True)
        write('LEGACY-UPDATE.json',result)
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error);write('LEGACY-UPDATE.json',result)
        for path in [*root.parent.glob('helene-update-*.log'),*(root/'.helene/logs').glob('*.log')]:
            if path.stat().st_size<2*1024*1024:shutil.copy2(path,OUT/path.name)
        subprocess.run(['screencapture','-x',str(OUT/'legacy-failure.png')]);raise
    finally:
        try:
            if native.control(root):native.invoke(root,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
        except Exception:pass
        if p is not None:
            try:p.wait(15)
            except subprocess.TimeoutExpired:p.terminate();p.wait(10)
        log.close()

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
    cold_only=os.environ.get('UPDATE_RECHECK')=='cold-only'
    recheck=os.environ.get('UPDATE_RECHECK') in ('remaining','cold-only')
    if recheck:result['previous_exact_archive_https_clean_support_pristine_preserve_run']=41308479060
    if cold_only:result['previous_preserve_cancellation_and_failed_start_rollback_run']=41302093575
    write('FULL-UPDATE.json',result)
    provider_calls=[];provider_users=[];provider_meta=[];status_tool={'issued':False,'observed':False}
    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))));provider_calls.append(self.path)
            users=[json.dumps(row.get('content'),ensure_ascii=False) for row in body.get('messages',[]) if row.get('role')=='user']
            if users:provider_users.append(users[-1])
            tool_reply=None;tool_name=None;tool_arguments={'action':'status'}
            if os.environ.get('REQUIRE_WORK_EVENT')=='1':
                from localharness import source_update as source_ops
                request=read(source_ops.path(places.data))
                expected='Событие текущего обновления '+str(request.get('id',''))[:8]
                offered=[row.get('function',{}).get('name') for row in body.get('tools',[])]
                catalogued='source_update' in json.dumps(body.get('messages',[]),ensure_ascii=False)
                provider_meta.append({'offered_tool_names':offered,'source_request_id':request.get('id'),
                    'new_event_in_last_user':bool(request.get('id') and users and expected in users[-1]),
                    'source_update_in_public_pointer_catalog':catalogued,
                    'tool_reply_ids':[row.get('tool_call_id') for row in body.get('messages',[]) if row.get('role')=='tool']})
                write('PROVIDER-METADATA.json',provider_meta)
                if (request.get('id') and users and expected in users[-1]
                        and ('source_update' in offered or ('call' in offered and catalogued)) and not status_tool['issued']):
                    tool_name='source_update' if 'source_update' in offered else 'call'
                    if tool_name=='call':tool_arguments={'name':'source_update','args_json':'{"action":"status"}'}
                    status_tool.update(issued=True,started=time.monotonic(),request_id=request['id'],public_entry_point=tool_name)
                replies=[row for row in body.get('messages',[]) if row.get('role')=='tool' and row.get('tool_call_id')=='call_ci_readonly_status']
                if replies:
                    raw=replies[-1]['content']
                    if isinstance(raw,list):raw='\n'.join(str(part.get('text','')) for part in raw if isinstance(part,dict))
                    try:tool_reply=json.JSONDecoder().raw_decode(raw)[0] if isinstance(raw,str) else raw
                    except ValueError:
                        # Model observations include ResultRef metadata and may
                        # externalize the middle of a large JSON status. Audit
                        # the exact immutable native result behind that marker.
                        match=re.search(r'\[ResultRef run=([^ ]+) id=([^ ]+) path=([^ ]+) sha256=([0-9a-f]{64}) size=(\d+)',raw)
                        if match:
                            assert re.fullmatch(r'results/[0-9]+-[A-Za-z0-9_.-]+\.(?:log|bin)',match[3])
                            runs=list((places.data/'memory/runs').glob('*/'+match[1]+'/manifest.json'))
                            assert len(runs)==1,'Model ResultRef does not identify one native run'
                            target=(runs[0].parent/match[3]).resolve()
                            assert target.is_relative_to(places.data/'memory/runs')
                            payload=target.read_bytes();assert len(payload)==int(match[5]) and hashlib.sha256(payload).hexdigest()==match[4]
                            tool_reply=json.loads(payload)
                            status_tool.update(native_durable_result_sha256=match[4],native_durable_result_size=len(payload),
                                model_observed_result_reference=True)
                        else:tool_reply={'error':'Non-JSON model tool observation without a native result reference'}
                    if not isinstance(tool_reply,dict):tool_reply={'error':'Non-object model tool observation'}
                    status_tool.update(observed=True,seconds=time.monotonic()-status_tool['started'],
                        returned_state=tool_reply.get('state'),error=tool_reply.get('error',''))
            calls=([{'id':'call_ci_readonly_status','type':'function','function':{'name':tool_name,'arguments':json.dumps(tool_arguments)}}] if tool_name else None)
            text='Disposable local CI response.'
            response={'id':'chatcmpl-fixture','object':'chat.completion','created':int(time.time()),'model':'fixture',
                'choices':[{'index':0,'message':{'role':'assistant','content':text},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
            if calls:response['choices'][0].update(message={'role':'assistant','content':None,'tool_calls':calls},finish_reason='tool_calls')
            if body.get('stream'):
                delta=({'role':'assistant','tool_calls':[{'index':0,**calls[0]}]} if calls else {'role':'assistant','content':text})
                data=('data: '+json.dumps({'id':'chatcmpl-fixture','object':'chat.completion.chunk','choices':[{'index':0,'delta':delta,'finish_reason':None}]})+'\n\n'+
                      'data: '+json.dumps({'id':'chatcmpl-fixture','object':'chat.completion.chunk','choices':[{'index':0,'delta':{},'finish_reason':'tool_calls' if calls else 'stop'}]})+'\n\ndata: [DONE]\n\n').encode()
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
                if failed and row.get('state')=='completed':
                    raise AssertionError('Fault injection/cancellation was not observed; the ordinary update completed')
                if row.get('state') in ('attention','cancelled','superseded'):
                    if failed:return row
                    raise RuntimeError('Full archive update refused: '+str(job_public()))
            time.sleep(.2)
        raise TimeoutError('Full archive update did not settle: '+str(job_public()))
    def screenshot(name,ready=True):
        if ready:
            native.wait(lambda:native.control(root) and native.control(root).get('ui_ready'),90);time.sleep(2)
        subprocess.run(['screencapture','-x',str(OUT/name)],check=True)
    def cocoa_identity():
        row=native.control(root);p=psutil.Process(row['pid'])
        args=p.cmdline()
        assert any(str(root/'Helene.app/Contents/MacOS/Helene')==a for a in args),args
        return {'pid':p.pid,'executable':p.exe(),'command':args}
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
            result['steps']['real_full_archive_clean_update_and_native_restart']=cocoa_identity();screenshot('after-clean.png');write('FULL-UPDATE.json',result)
        if not cold_only:
            # The ordinary pristine preserve path needs no invented semantic
            # clearance and must complete through the same real archive.
            if not recheck:
                owner_stop.change_pause(places.data,False,reason='explicit synthetic pristine preserve',via='fixture')
                admission=native.invoke(root,'update_install',{'path':downloaded['path'],'sourceMode':'preserve'})
                identity=admission.get('id') or admission.get('job',{}).get('id');assert identity
                finish(identity);native.wait(lambda:info().get('version')=='2.3.0-rc.18',120);data_exact()
                result['steps']['real_full_archive_pristine_preserve_update']=cocoa_identity()
                owner_stop.change_pause(places.data,True,reason='pristine preserve completed',via='fixture')
                screenshot('after-pristine-preserve.png');write('FULL-UPDATE.json',result)
            # Modified-preserve requires full semantic clearance even without Git
            # conflicts. A fixture provider cannot grant it. Prove the real gate
            # retains the old program and its own explicit cancellation settles.
            from localharness import source_update,python_source_updater as mechanical
            authored=selected_code()/'archive-preserved-owner.py';authored.write_bytes(b'OWNER_NONCONFLICT=23\n')
            before_preserve=(root/'helene.json').read_bytes();window_pid=native.control(root)['pid']
            prior_plans=set((places.data/'memory/.source-updates').glob('*/plan.json'))
            owner_stop.change_pause(places.data,False,reason='explicit synthetic preserve qualification',via='fixture')
            admission=native.invoke(root,'update_install',{'path':downloaded['path'],'sourceMode':'preserve'})
            identity=admission.get('id') or admission.get('job',{}).get('id');assert identity
            # Earlier pristine updates retain their plan. Do not treat that
            # already-completed plan as admission of this new asynchronous job.
            gated=native.wait(lambda:[p for p in (places.data/'memory/.source-updates').glob('*/plan.json')
                if p not in prior_plans and source_update.requires_gate(read(p),p.parent)],180)
            assert gated,'Authored source was not admitted to its new mandatory gate'
            assert native.control(root)['pid']==window_pid and authored.read_bytes()==b'OWNER_NONCONFLICT=23\n'
            assert (root/'helene.json').read_bytes()==before_preserve and not mechanical.active_update(root).exists()
            result['steps']['modified_preserve_requires_semantic_gate_old_program_retained']=True
            if os.environ.get('REQUIRE_WORK_EVENT')=='1':
                request=native.wait(lambda:read(source_update.path(places.data)),60)
                expected='Событие текущего обновления '+request['id'][:8]
                current=native.wait(lambda:next((text for text in provider_users if expected in text),None),120)
                proof={'request_id':request['id'],'event_prefix':expected,'present_in_actual_last_user_message':True,
                    'last_user_message_sha256':hashlib.sha256(current.encode()).hexdigest(),
                    'local_fixture_provider_only':True,'semantic_clearance_injected':False}
                write('MODEL-CURRENT-PROOF.json',proof)
                result['steps']['actual_model_current_contains_new_source_update_event']=proof
                native.wait(lambda:status_tool['observed'],90)
                write('READONLY-STATUS-TOOL.json',status_tool)
                assert not status_tool['error'] and status_tool['returned_state'] in ('waiting_agent','pending','needs_attention'),status_tool
                assert status_tool['seconds']<30,'Real source-tool status exceeded its30s transport budget'
                result['steps']['fixture_model_executes_real_readonly_status_tool']=dict(status_tool)
                if os.environ.get('REQUIRE_NATIVE_MONITOR')=='1':
                    import native_ax as ax
                    pid=native.control(root)['pid']
                    def header_button():
                        rows=ax.walk(pid)
                        write('NATIVE-MONITOR-BEFORE-AX.json',[{k:v for k,v in row.items() if k!='element'} for row in rows])
                        matches=[row for row in rows if row['AXRole']=='AXButton' and any('Открыть действия' in row[key]
                            for key in ('AXTitle','AXDescription','AXValue'))]
                        return matches[0]['element'] if len(matches)==1 else None
                    ax.press(native.wait(header_button,30))
                    def visible_actions():
                        rows=ax.walk(pid)
                        texts={row[key] for row in rows for key in ('AXTitle','AXDescription','AXValue')}
                        joined='\n'.join(texts)
                        return rows if 'ожидают завершения' in joined and 'ожидает исправлений от агента' in joined else None
                    rows=native.wait(visible_actions,30)
                    write('NATIVE-MONITOR-AX.json',[{k:v for k,v in row.items() if k!='element'} for row in rows])
                    screenshot('source-monitor-actions.png')
                    result['steps']['native_ax_header_badge_opens_actual_pending_actions']=True
            cancelled=native.invoke(root,'update_cancel');assert cancelled.get('ok')
            finish(identity,timeout=180,failed=True)
            native.wait(lambda:jobs.status(places.data).get('state')=='cancelled',90)
            assert native.control(root)['pid']==window_pid and authored.read_bytes()==b'OWNER_NONCONFLICT=23\n'
            data_exact();owner_stop.change_pause(places.data,True,reason='synthetic preserve cancelled',via='fixture')
            result['steps']['pending_preserve_explicit_cancel_retains_program_data_and_authored_code']=True
            screenshot('after-preserve-cancel.png');write('FULL-UPDATE.json',result)
            # Force only the newly launched, exact-path migration child to fail.
            # All archive bytes and updater logic remain the real distribution.
            code=selected_code();(code/'archive-owner-change.py').write_bytes(b'OWNER_CHANGE=1\n')
            before_cfg=(root/'helene.json').read_bytes();before_file=(code/'archive-owner-change.py').read_bytes()
            stop_watch=threading.Event();killed=[]
            existing_pid=native.control(root)['pid'];admission_started=time.time()
            def fail_new_start():
                while not stop_watch.is_set():
                    for candidate_pid in psutil.pids():
                        try:
                            p=psutil.Process(candidate_pid);born=p.create_time()
                            if p.pid==existing_pid or born<admission_started-.05:continue
                            args=p.cmdline()
                            if (p.pid!=existing_pid and born>=admission_started-.05
                                    and '--migration' in args and str(root) in args
                                    and any(a in args for a in (str(root/'app/python_app.py'),str(root/'Helene.app/Contents/MacOS/Helene')))):
                                if psutil.Process(p.pid).create_time()==born:
                                    p.kill();killed.append({'pid':p.pid,'birth':born});return
                        except (psutil.Error,SystemError,OSError,ValueError):pass
                    time.sleep(.01)
            watcher=threading.Thread(target=fail_new_start,daemon=True);watcher.start()
            admission=native.invoke(root,'update_install',{'path':downloaded['path'],'sourceMode':'clean'})
            identity=admission.get('id') or admission.get('job',{}).get('id');assert identity
            finish(identity,failed=True);stop_watch.set();watcher.join(3)
            assert len(killed)==1,'No specifically observed new startup child was faulted'
            native.wait(lambda:info().get('version')=='2.3.0-rc.18',120)
            assert (code/'archive-owner-change.py').read_bytes()==before_file
            assert (root/'helene.json').read_bytes()==before_cfg
            data_exact();result['steps']['full_archive_failed_start_rolls_back_and_reopens_previous']={'faulted':killed,'reopened':cocoa_identity()}
            screenshot('after-rollback.png');write('FULL-UPDATE.json',result)
            # A failed durable job is intentionally rejoined, not silently
            # replayed by another identical click. Explicitly cancel it before
            # admitting the separate interruption experiment.
            assert native.invoke(root,'update_cancel').get('ok')
            native.wait(lambda:jobs.status(places.data).get('state')=='cancelled',90)
        # Crash the exact admitted updater AFTER its real full overlay,
        # before startup commit. A later ordinary launch must consume the durable
        # recovery journal before importing the mixed application.
        from localharness import python_source_updater as mechanical,update_progress
        before_cfg=(root/'helene.json').read_bytes()
        code=selected_code();authored=code/'archive-cold-owner.py';authored.write_bytes(b'COLD_OWNER=1\n')
        before_authored=authored.read_bytes();interrupted=[];stop_watch=threading.Event();began=time.time()
        def interrupt_overlay():
            while not stop_watch.is_set():
                try:
                    state=update_progress.status(root)
                    if (mechanical.active_update(root).is_file() and state and state.get('stage')=='starting'
                            and state.get('updated_at',0)>=began):
                        for receipt in (root/'.helene/updates').glob('*/updater-admitted*.json'):
                            if receipt.stat().st_mtime<began:continue
                            admitted=read(receipt)
                            if admitted.get('target')!=str(root):continue
                            process=psutil.Process(admitted['pid'])
                            if process.create_time()!=admitted.get('birth') or str(root) not in process.cmdline():continue
                            process.suspend()
                            pending=read(mechanical.active_update(root))
                            if pending.get('committed'):
                                process.resume();continue
                            note=read(root/'python-migration.json')
                            if note.get('state') in ('new_app_started','completed'):
                                process.resume();continue
                            stopped_children=[]
                            for candidate_pid in psutil.pids():
                                try:
                                    child=psutil.Process(candidate_pid);birth=child.create_time()
                                    if birth<began:continue
                                    args=child.cmdline()
                                    if ('--migration' in args and str(root) in args
                                            and str(root/'Helene.app/Contents/MacOS/Helene') in args
                                            and psutil.Process(child.pid).create_time()==birth):
                                        child.kill();stopped_children.append({'pid':child.pid,'birth':birth})
                                except (psutil.Error,SystemError,OSError,ValueError):pass
                            process.kill()
                            interrupted.append({'pid':admitted['pid'],'birth':admitted['birth'],
                                'point':'full overlay finished, startup uncommitted','stage':state['stage'],
                                'new_startups_faulted':stopped_children})
                            return
                except (OSError,ValueError,psutil.Error):pass
                time.sleep(.005)
        watcher=threading.Thread(target=interrupt_overlay,daemon=True);watcher.start()
        admission=native.invoke(root,'update_install',{'path':downloaded['path'],'sourceMode':'clean'})
        native.wait(lambda:interrupted,300);stop_watch.set();watcher.join(3)
        assert mechanical.active_update(root).is_file(),'No interrupted full overlay journal'
        launch();native.wait(lambda:info().get('version')=='2.3.0-rc.18',180)
        assert not mechanical.active_update(root).exists()
        assert (root/'helene.json').read_bytes()==before_cfg
        assert authored.read_bytes()==before_authored
        data_exact();result['steps']['cold_start_recovers_real_interrupted_full_overlay']={'faulted':interrupted,'reopened':cocoa_identity()}
        screenshot('after-cold-recovery.png');write('FULL-UPDATE.json',result)
        result['provider_calls']=provider_calls;result['steps']['no_owner_account_or_live_provider']=True
        write('FULL-UPDATE.json',result)
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error);result['job']=job_public();result['provider_calls']=provider_calls
        write('READONLY-STATUS-TOOL.json',status_tool)
        write('FULL-UPDATE.json',result)
        from localharness import source_update as source_ops
        source_request=read(source_ops.path(places.data))
        if source_request.get('directory'):
            from localharness.source_update_hand import public_context
            directory=Path(source_request['directory']);operation=read(directory/'pipeline.json')
            write('SOURCE-TRANSPORT-STATE.json',{'id':source_request.get('id'),'state':source_request.get('state'),
                'operation':{k:operation.get(k) for k in ('state','error','paused','cancel_requested') if k in operation},
                'public_hand':public_context(directory)})
        runner_log=places.data/'runner.log'
        if runner_log.is_file():
            (OUT/'runner-tail.log').write_text(runner_log.read_text(errors='replace')[-40000:].replace('fixture-local-key','[fixture-key]'))
        for folder in (root/'.helene/logs',places.data/'memory/.control'):
            for p in folder.glob('*.log'):
                if p.stat().st_size<2*1024*1024:shutil.copy2(p,OUT/p.name)
        screenshot('failure.png',ready=False);raise
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

if __name__=='__main__':{'prepare':prepare,'subject':subject,'legacy':legacy}[sys.argv[1]]()
