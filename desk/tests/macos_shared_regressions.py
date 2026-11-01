"""Actual Mac consumers for shared observer Exit and session-owned installers."""
from pathlib import Path
import hashlib,http.server,io,json,os,shlex,shutil,ssl,subprocess,sys,threading,time,zipfile
from types import SimpleNamespace
REPO=Path(__file__).resolve().parents[2];TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
OUT=REPO/'evidence-shared-mac';OUT.mkdir(exist_ok=True)
sys.path[:0]=[str(REPO/'desk/tests'),str(REPO/'desk/installer')]
from build_python_macos_rc import unpack,digest
def write(name,value):(OUT/name).write_text(json.dumps(value,indent=2))
def prepare():
    outer=TEMP/'qualified-release-artifact.zip';assert digest(outer)==os.environ['ARTIFACT_SHA256']
    archive=TEMP/'shared-release.zip'
    with zipfile.ZipFile(outer) as z:
        row=next(i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip')
        archive.write_bytes(z.read(row))
    assert digest(archive)==os.environ['CANDIDATE_SHA256']
    # The operator already has the independently verified8f ZIP. Transfer a
    # small byte-exact delta instead of downloading unchanged native/runtime
    # payload again; native consumers still use the full original target ZIP.
    if os.environ.get('SKIP_DELTA_TRANSPORT')!='1':
        with zipfile.ZipFile(TEMP/'baseline-release-artifact.zip') as z:
            row=next(i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip')
            baseline=TEMP/'baseline-release.zip';baseline.write_bytes(z.read(row))
        assert digest(baseline)=='8f133b5238fbea7a300b1eb00c1f37adfc571f5ec4afa9f5f4c29046ff00926b'
        from macos_archive_delta import create,restore
        patch=OUT/'exact-archive-delta.zip';create(baseline,archive,patch)
        restored=TEMP/'delta-reconstructed.zip';receipt=restore(baseline,patch,restored)
        assert digest(restored)==os.environ['CANDIDATE_SHA256']
        write('DELTA-TRANSPORT.json',receipt)
    from macos_archive_delta import versioned
    manual=TEMP/'manual-release.zip'
    receipt=versioned(archive,manual,'Helene-2.3.0-rc.18-macos-arm64')
    if os.environ.get('EXPECTED_MANUAL_SHA256'):
        assert receipt['sha256']==os.environ['EXPECTED_MANUAL_SHA256']
    write('MANUAL-ARCHIVE.json',receipt)
    if os.environ.get('NATIVE_DITTO_EXTRACT')=='1':
        # The real OS extractor must preserve the shipped executable bits;
        # custom Python unpack() would repair modes and hide delivery defects.
        subprocess.run(['/usr/bin/ditto','-x','-k',str(manual),str(TEMP/'shared-install')],check=True)
        measured=0
        with zipfile.ZipFile(manual) as z:
            for row in z.infolist():
                target=TEMP/'shared-install'/row.filename
                if row.is_dir():continue
                assert target.is_file() and not target.is_symlink(),row.filename
                assert target.read_bytes()==z.read(row),row.filename
                assert target.stat().st_mode&0o777==(row.external_attr>>16)&0o777,row.filename
                measured+=1
        write('NATIVE-EXTRACTION.json',{'extractor':'/usr/bin/ditto -x -k','archive_sha256':receipt['sha256'],
            'actual_extracted_file_bytes_and_modes_exact':measured,'mode_repair_performed':False})
    else:unpack(manual,TEMP/'shared-install')
    program=TEMP/'shared-install/Helene-2.3.0-rc.18-macos-arm64'
    shutil.copytree(program/'runtime',TEMP/'shared-driver-runtime')
    python=TEMP/'shared-driver-runtime/bin/python3'
    os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),'subject'])
def subject():
    import psutil
    import rc18_macos_pipeline as native
    program=TEMP/'shared-install/Helene-2.3.0-rc.18-macos-arm64'
    sys.path[:0]=[str(program/'app'),str(program/'tree')]
    from localharness import posix_first_start as first,owner_stop,component_install_job as jobs,component_downloads as downloads
    from localharness.python_controller import Controller
    from localharness.package_notice import write as store
    home=TEMP/'shared-home';home.mkdir()
    os.environ.update(HOME=str(home),CFFIXED_USER_HOME=str(home),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    result={'archive_sha256':os.environ['CANDIDATE_SHA256'],'manual_archive_sha256':json.loads((OUT/'MANUAL-ARCHIVE.json').read_bytes())['sha256'],'actual_distributed_shared_code':True,
        'scope':'Disposable native Mac with synthetic tiny component archives and paused API profile. No model weights/account/microphone acceptance.', 'steps':{}}
    write('SHARED-MAC.json',result)
    fixture=TEMP/'component-program';(fixture/'runtime/bin').mkdir(parents=True)
    # A regular forwarding interpreter in a synthetic installer fixture, never
    # represented as a distributable runtime or a modified factory program.
    wrapper=fixture/'runtime/bin/python3'
    wrapper.write_text('#!/bin/sh\nexec '+shlex.quote(str(program/'runtime/bin/python3'))+' "$@"\n');wrapper.chmod(0o755)
    shutil.copytree(program/'app/localharness',fixture/'app/localharness')
    shutil.copy2(program/'app/python_migrate.py',fixture/'app/python_migrate.py')
    store(fixture/'python-app.json',{'format':'helene-python-app.v1','target':'macos-arm64','version':'2.3.0-rc.18'})
    tree=TEMP/'component-profile';tree.mkdir()
    store(fixture/'helene.json',{'tree':str(tree),'code':str(program/'tree'),'mode':'local','python':'runtime/bin/python3'})
    def owner():
        value=Controller(fixture,lambda *args:None);value.current=lambda:SimpleNamespace(tree=tree)
        return value
    def manifest(raw,*,url=None):
        row={'name':'fixture-whisper.zip','sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}
        if url:row['url']=url
        else:row['offline_only']=True
        value={'format':downloads.FORMAT,'target':'macos-arm64','extensions':{'whisper':['whisper'],'xtts':[]},
            'artifacts':{'whisper':row},'unavailable':{'xtts':'Synthetic fixture'}}
        downloads.validate(value);store(fixture/'app/component-downloads.json',value)
        if not url:(downloads.cache_dir(fixture)/downloads.cache_name(row)).write_bytes(raw)
        return row
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('Helene/voice/fixture.txt',b'component exact native fixture\n')
    valid=payload.getvalue();active_owner=None;release=threading.Event();server=None
    processes=[];logs=[]
    recheck_login=os.environ.get('RECHECK_MOVED_LOGIN')=='1'
    if recheck_login:
        result['previous_passed_installer_and_two_observer_run']=41308478947
        result['focus']='Only moved-programme login/teardown tail; two observers are its setup precondition.'
    os.environ['PYTHONFAULTHANDLER']='1'
    try:
        if not recheck_login:
            manifest(valid);active_owner=owner();admit=jobs.start(active_owner,'whisper')
            native.wait(lambda:jobs.status(fixture,'whisper').get('state') in ('completed','failed'),90)
            assert jobs.status(fixture,'whisper')['state']=='completed'
            assert (fixture/'voice/fixture.txt').read_bytes()==b'component exact native fixture\n'
            active_owner.close();active_owner=None
            result['steps']['actual_cached_tiny_install']=True;write('SHARED-MAC.json',result)
            manifest(b'not a ZIP');active_owner=owner();jobs.start(active_owner,'whisper')
            native.wait(lambda:jobs.status(fixture,'whisper').get('state')=='failed',90)
            failed=jobs.status(fixture,'whisper');assert failed.get('error_type')=='BadZipFile',failed
            active_owner.close();active_owner=None
            manifest(valid);active_owner=owner();jobs.start(active_owner,'whisper')
            native.wait(lambda:jobs.status(fixture,'whisper').get('state')=='completed',90)
            active_owner.close();active_owner=None
            result['steps']['actual_bad_zip_durable_error_and_explicit_retry']=True;write('SHARED-MAC.json',result)
            # Hold a real HTTPS transfer so close() must stop the exact background
            # installer rather than merely observe an already finished child.
            cert=OUT/'localhost-cert.pem';key=TEMP/'shared-localhost-key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost',
                '-addext','subjectAltName=DNS:localhost','-keyout',str(key),'-out',str(cert)],capture_output=True,check=True)
            os.environ['SSL_CERT_FILE']=str(cert)
            entered=threading.Event()
            raw=b'fixture download, not model weights\n'+b'x'*(2*1024*1024)
            class Slow(http.server.BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):
                    self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers()
                    try:self.wfile.write(raw[:65536]);self.wfile.flush();entered.set();release.wait(90)
                    except OSError:pass
            server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Slow)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
            server.socket=context.wrap_socket(server.socket,server_side=True)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            manifest(raw,url=f'https://localhost:{server.server_port}/fixture-whisper.zip')
            active_owner=owner();jobs.start(active_owner,'whisper');assert entered.wait(45)
            pin=jobs.status(fixture,'whisper')['worker'];assert jobs.alive(pin) is True
            active_owner.close();active_owner=None
            native.wait(lambda:jobs.alive(pin) is False,30)
            assert jobs.status(fixture,'whisper')['state']=='interrupted'
            result['steps']['actual_exit_stops_blocked_https_component_worker']=True;write('SHARED-MAC.json',result)
            release.set();server.shutdown();server=None
            moved=fixture.with_name('Moved installer Hélène');fixture.rename(moved)
            result['steps']['component_fixture_moves_after_exit']=True
        # Two actual Cocoa observers share one real paused profile owner.
        places=first.Places(home/'Helene');first.prepare(program,places=places)
        first.create_agent(program,{'name':'Two native observers','owner':'Disposable CI','provider':'openai',
            'model':'fixture','key':'fixture-unused-local-key','base_url':'http://127.0.0.1:9/v1'},places=places)
        owner_stop.change_pause(places.data,True,reason='native shared fixture stays paused',via='fixture')
        for number in range(2):
            log=(OUT/f'observer-{number}.log').open('wb');logs.append(log)
            p=subprocess.Popen([str(program/'Helene.app/Contents/MacOS/Helene'),'--root',str(program)],cwd=program,
                env=dict(os.environ),stdout=log,stderr=subprocess.STDOUT);processes.append(p)
            native.wait(lambda:native.control(program) and native.control(program).get('ui_ready'),120)
        def controls():
            rows=[]
            for path in (program/'.helene/windows').glob('.python-window-*.json'):
                row=json.loads(path.read_bytes())
                if psutil.pid_exists(row['pid']):rows.append(row)
            return rows
        native.wait(lambda:len(controls())==2 and all(row.get('ui_ready') for row in controls()),90);time.sleep(3)
        subprocess.run(['screencapture','-x',str(OUT/'two-native-observers.png')],check=True)
        descriptor=json.loads((places.data/'memory/.control/desktop-session.json').read_bytes())
        owned=psutil.Process(descriptor['pid'])
        tracked={(p.pid,p.create_time()) for p in [owned,*owned.children(recursive=True)]}
        tracked.update((p.pid,psutil.Process(p.pid).create_time()) for p in processes)
        native.invoke(program,'python_window',{'action':'exit','relaunch':False,'owner_exit':True})
        def all_gone():
            for pid,birth in tracked:
                try:
                    p=psutil.Process(pid)
                    if p.create_time()==birth and p.status() not in (psutil.STATUS_ZOMBIE,psutil.STATUS_DEAD):return False
                except psutil.NoSuchProcess:pass
            return True
        native.wait(all_gone,60)
        program.rename(program.with_name('Moved two observers Hélène'))
        program=program.with_name('Moved two observers Hélène')
        result['steps']['actual_owner_exit_closes_two_cocoa_observers_and_all_observed_children']=len(tracked)
        result['steps']['whole_program_folder_moves_after_two_observer_exit']=True
        write('SHARED-MAC.json',result)
        # The existing setter must bind the actual moved/versioned programme,
        # never a guessed ~/Applications/Helene path. No owner Dock is edited.
        log=(OUT/'moved-login-window.log').open('wb');logs.append(log)
        p=subprocess.Popen([str(program/'Helene.app/Contents/MacOS/Helene'),'--root',str(program)],cwd=program,
            env=dict(os.environ),stdout=log,stderr=subprocess.STDOUT);processes.append(p)
        native.wait(lambda:native.control(program) and native.control(program).get('ui_ready'),120)
        native.invoke(program,'autostart_set',{'on':True});assert native.invoke(program,'autostart_get') is True
        import plistlib
        plist=next((home/'Library/LaunchAgents').glob('app.helene.login.*.plist'))
        binding=plistlib.loads(plist.read_bytes())
        assert binding['WorkingDirectory']==str(program)
        assert binding['ProgramArguments'][0]==str(program/'Helene.app/Contents/MacOS/Helene')
        assert ['--root',str(program)]==binding['ProgramArguments'][1:3]
        subprocess.run(['plutil','-lint',str(plist)],capture_output=True,check=True)
        closing=time.monotonic()
        pin=(p.pid,psutil.Process(p.pid).create_time())
        def observe_slow_close():
            time.sleep(15)
            if p.poll() is not None:return
            try:
                if psutil.Process(pin[0]).create_time()!=pin[1]:return
                subprocess.run(['sample',str(pin[0]),'2','-file',str(OUT/'moved-close-sample.txt')],capture_output=True,timeout=15)
                from localharness.session_host import control_path
                value=json.loads(control_path(program).read_bytes())
                write('moved-close-owner.json',{k:value.get(k) for k in ('pid','birth','generation','program','tree')})
            except Exception as error:write('sample-error.json',{'error':str(error)})
        observer=threading.Thread(target=observe_slow_close,daemon=True);observer.start()
        native.invoke(program,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
        try:p.wait(75)  # SessionClient permits45s RPC +15s owner census, then UI cleanup.
        except subprocess.TimeoutExpired:
            import signal
            if psutil.Process(pin[0]).create_time()==pin[1]:p.send_signal(signal.SIGABRT)
            raise
        assert p.returncode==0,'Moved programme close failed; inspect faulthandler/window log'
        result['moved_close_elapsed_seconds']=time.monotonic()-closing
        write('SHARED-MAC.json',result)
        target=f'gui/{os.getuid()}/'+binding['Label']
        subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)],capture_output=True,check=True)
        try:
            native.wait(lambda:native.invoke(program,'app_info').get('root')==str(program),120)
            native.invoke(program,'autostart_set',{'on':False})
            native.invoke(program,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
            native.wait(lambda:native.control(program) is None,45)
        finally:subprocess.run(['launchctl','bootout',target],capture_output=True,check=True)
        if plist.exists():plist.unlink()  # exact synthetic-home entry only
        result['steps']['actual_launchagent_after_versioned_folder_move']=binding
        result['desktop_pins']='Owner Dock pins not modified/accepted; old-location pins require explicit re-pinning.'
        write('SHARED-MAC.json',result)
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error);write('SHARED-MAC.json',result)
        subprocess.run(['screencapture','-x',str(OUT/'failure.png')]);raise
    finally:
        release.set()
        if server is not None:server.shutdown()
        if active_owner is not None:active_owner.close()
        for p in processes:
            try:p.wait(10)
            except subprocess.TimeoutExpired:p.terminate();p.wait(10)
        for log in logs:log.close()
        if (tree/'component-download.log').exists():shutil.copy2(tree/'component-download.log',OUT/'component-download.log')
if __name__=='__main__':{'prepare':prepare,'subject':subject}[sys.argv[1]]()
