"""External CI apparatus: package an explicit frozen subject, never this QA checkout."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,os,plistlib,shutil,subprocess,sys,time,zipfile
QA=Path(__file__).resolve();WORKSPACE=Path(os.environ['GITHUB_WORKSPACE']).resolve()
REPO=WORKSPACE/'subject';TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
OUT=WORKSPACE/'evidence-rc23-package';OUT.mkdir(exist_ok=True)
PRODUCT_COMMIT='2d6d8f63983747b6ef7ad300c2f0e9f08c4c1b51';VERSION='2.3.0-rc.23'
OUTER_SHA='a2c375d594aaf31e7cf0f57b598a70a16473ce26dc8acb492ae44afd606970ae'
BASE_SHA='baa533b05f4b63c69be3547122ae38fd21ca87012d68fea20445bd4b19da40f5'
BASE=TEMP/'qualified-base/Helene';PACK=TEMP/'rc23-output';PROGRAM=PACK/'Helene'
sys.path[:0]=[str(REPO/'desk'),str(REPO/'desk/installer'),str(REPO/'desk/tests')]

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def save(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')
def command(argv,name,**kw):
    with (OUT/name).open('wb') as stream:
        done=subprocess.run(list(map(str,argv)),stdout=stream,stderr=subprocess.STDOUT,timeout=kw.pop('timeout',600),**kw)
    if done.returncode:raise RuntimeError(f'{name}: exit {done.returncode}; inspect the retained log')
    return done
def subject_identity():
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()==PRODUCT_COMMIT
    assert not subprocess.check_output(['git','diff','--name-only'],cwd=REPO)
    rows={}
    for entry in subprocess.check_output(['git','ls-files','--stage','-z'],cwd=REPO).split(b'\0'):
        if not entry:continue
        head,name=entry.split(b'\t',1);mode,oid,stage=head.decode().split();name=name.decode()
        body=(REPO/name).read_bytes();assert stage=='0'
        assert hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()==oid,name
        rows[name]={'sha256':hashlib.sha256(body).hexdigest(),'mode':mode}
    save('SUBJECT-SOURCE.json',{'commit':PRODUCT_COMMIT,'files':rows,'QA_script_sha256':digest(QA),
        'instrumentation_external_to_subject':True,'version':VERSION})
def prepare():
    assert sys.platform=='darwin' and os.uname().machine=='arm64';subject_identity()
    from build_python_macos_rc import unpack
    outer=TEMP/'qualified-release-artifact.zip';assert digest(outer)==OUTER_SHA
    archive=TEMP/'qualified-base.zip'
    with zipfile.ZipFile(outer) as z:
        candidates=[i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip']
        assert len(candidates)==1;archive.write_bytes(z.read(candidates[0]))
    assert digest(archive)==BASE_SHA;unpack(archive,BASE.parent)
    driver=TEMP/'rc23-driver-runtime';shutil.copytree(BASE/'runtime',driver)
    python=driver/'bin/python3';os.execv(str(python),[str(python),'-B',str(QA),'build'])
def build():
    from portable_sources import compose_core,frontend_inputs,frontend_outputs,write_receipt
    from python_posix_bundle import assemble,normalize_output_modes
    from localharness import source_verification_bundle as bundle,source_verification_executor as executor,source_update_host
    from build_python_macos_rc import unpack
    subject_identity();command([sys.executable,'-m','pip','check'],'pip-check.log')
    old=bundle.load(BASE/'verification',digest(BASE/'verification/manifest.json'),'2.3.0-rc.18')
    tools=BASE/'verification-tools';selected=executor.provision(BASE,old,TEMP/'frontend-cache-operation')
    env=dict(os.environ,PATH=str(tools/'bin')+os.pathsep+os.environ.get('PATH',''),
        npm_config_offline='true',npm_config_cache=selected['npm_config_cache'],
        npm_config_userconfig=str(TEMP/'empty-user.rc'),npm_config_globalconfig=str(TEMP/'empty-global.rc'))
    names=('app','mobile','miniapp','remote','setup/ui');before=frontend_inputs(REPO)
    def install(name):command([selected['npm'],'ci','--offline','--ignore-scripts','--audit=false','--fund=false'],'npm-ci-'+name.replace('/','-')+'.log',cwd=REPO/'desk'/name,env=env)
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:list(pool.map(install,names))
    def front(name):command([selected['npm'],'run','build'],'frontend-'+name.replace('/','-')+'.log',cwd=REPO/'desk'/name,env=env)
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:list(pool.map(front,names))
    assert frontend_inputs(REPO)==before
    write_receipt(REPO,OUT/'frontend-receipt.json',[{'cwd':'desk/'+n,'argv':['npm','run','build'],'returncode':0} for n in ('app','mobile','miniapp')],before=before)
    save('FRONTENDS.json',{'passed':True,'projects':names,'inputs':before,'outputs':frontend_outputs(REPO),'all_offline':True})
    native=json.loads((BASE/'native-build.json').read_text());assert native['exit']==0 and native['platform']=='arm64'
    source={}
    for prefix in ('praxis/body','helene/core/body'):
        for p in (REPO/prefix).rglob('*'):
            if p.is_file() and not any(x in p.parts for x in ('.git','target','__pycache__')):source[p.relative_to(REPO/prefix).as_posix()]=digest(p)
    assert source==native['source_files']
    for name in ('body','bridge'):assert digest(BASE/('helene-'+name))==native[name+'_sha256']
    core,origins=compose_core(REPO);edition=TEMP/'selected-edition';edition.mkdir()
    for name,row in core.items():
        p=edition/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(row.data);p.chmod(0o755 if row.mode=='100755' else 0o644)
    lock=bundle.executor_lock(tools={'node':str(tools/'bin/node'),'npm':str(tools/'bin/npm')});lock['tools'].pop('cargo',None)
    verification=TEMP/'rc23-verification';bundle.build(REPO,verification,VERSION,{'darwin':lock})
    manifest=json.loads((verification/'manifest.json').read_text());manifest['bundled_executors']={'darwin':executor.describe(tools)}
    (verification/'manifest.json').write_bytes(bundle.encoded(manifest));bundle.load(verification,digest(verification/'manifest.json'),VERSION)
    PACK.mkdir();initial=assemble(BASE/'runtime',edition,None,None,PROGRAM,version=VERSION,target='macos-arm64',
        downloads=REPO/'desk/installer/mac-component-downloads-rc23.json',body=BASE/'helene-body',bridge=BASE/'helene-bridge',
        verification=verification,verification_tools=tools)
    initial.rename(TEMP/'rc23-assembly-before-cocoa.zip')
    shutil.copytree(BASE/'Helene.app',PROGRAM/'Helene.app')
    info=PROGRAM/'Helene.app/Contents/Info.plist';value=plistlib.loads(info.read_bytes())
    value.update(CFBundleShortVersionString='2.3.0',CFBundleVersion='23023');info.write_bytes(plistlib.dumps(value))
    command(['codesign','--force','--sign','-',PROGRAM/'Helene.app'],'codesign-sign.log')
    command(['codesign','--verify','--deep','--strict',PROGRAM/'Helene.app'],'codesign-verify.log')
    unpack(REPO/'desk/installer/rc18-native-license-texts.zip',PROGRAM)
    for a,b in ((REPO/'desk/installer/PYTHON-MACOS-START-HERE.md',PROGRAM/'START-HERE.md'),(BASE/'native-build.json',PROGRAM/'native-build.json'),
                (BASE/'runtime-script-portability.json',PROGRAM/'runtime-script-portability.json')):shutil.copy2(a,b)
    launcher='#!/bin/sh\nset -eu\nhere=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$here/Helene.app/Contents/MacOS/Helene" --root "$here" "$@"\n'
    for name in ('helene','Start Helene.command'):(PROGRAM/name).write_text(launcher);(PROGRAM/name).chmod(0o755)
    reused={'archive_sha256':BASE_SHA,'outer_sha256':OUTER_SHA,'run_id':41313241302,
        'launcher_sha256':digest(BASE/'Helene.app/Contents/MacOS/Helene'),'scope':'Exact qualified Runtime/Cocoa launcher/native organs and offline toolkit; new application and verification source assembled above.'}
    save('NATIVE-REUSE.json',{'selected_source_files_exact':len(source),'native':native,'reused':reused,'no_Rust_or_pip_resolution':True,'voice_downloads':0})
    (PROGRAM/'macos-inputs.json').write_text(json.dumps({'format':'helene-macos-inputs.v1','version':VERSION,'source_commit':PRODUCT_COMMIT,
        'base_public_commit':'f3e709ee39e87a0cfa2c25ca18e576a3d2d5d4e0','native_reused':reused,'native_built':native,
        'core_origins':origins,'native_qualification':'Separate current package receipts; physical TCC/mic/Intel/notarization not accepted'},indent=2))
    normalize_output_modes(PROGRAM)
    files={p.relative_to(PROGRAM).as_posix():digest(p) for p in PROGRAM.rglob('*') if p.is_file()
        and p.relative_to(PROGRAM).parts[0] not in ('tree','components','data','downloads') and p.name not in ('helene.json','python-package-files.json')}
    (PROGRAM/'python-package-files.json').write_text(json.dumps({'format':'helene-python-program-files.v1','version':VERSION,'files':files},indent=2))
    archive=PACK/f'Helene-{VERSION}-macos-arm64.zip'
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PROGRAM.rglob('*')):
            if p.is_file():z.write(p,'Helene/'+p.relative_to(PROGRAM).as_posix())
    binding=source_update_host.verified_runtime(PROGRAM)
    candidate={'format':'helene-macos-candidate.v1','version':VERSION,'target':'macos-arm64','source_commit':PRODUCT_COMMIT,
        'sha256':digest(archive),'size':archive.stat().st_size,'archive':archive.name,'files':len(files),'native_reused':reused,
        'qualification_pending':True,'QA_script_sha256':digest(QA)}
    (PACK/'candidate.json').write_text(json.dumps(candidate,indent=2));shutil.copy2(PACK/'candidate.json',OUT/'candidate.json')
    (PACK/(archive.name+'.sha256')).write_text(candidate['sha256']+'  '+archive.name+'\n')
    save('RUNTIME-ADMISSION.json',binding)
    from macos_archive_delta import create,restore,versioned
    patch=OUT/'exact-archive-delta.zip';plan=create(TEMP/'qualified-base.zip',archive,patch)
    reconstructed=TEMP/'rc23-roundtrip.zip';restored=restore(TEMP/'qualified-base.zip',patch,reconstructed)
    manual=PACK/f'Helene-{VERSION}-macos-arm64-manual.zip';manual_row=versioned(archive,manual,f'Helene-{VERSION}-macos-arm64')
    save('MANUAL-ARCHIVE.json',manual_row);save('DELTA-ROUNDTRIP.json',restored)
    with open(os.environ['GITHUB_OUTPUT'],'a') as output:output.write('sha256='+candidate['sha256']+'\n')
    print(json.dumps(candidate),flush=True)

def isolated(program):
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','DYLD_LIBRARY_PATH','SSL_CERT_FILE','SSL_CERT_DIR')}
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',DYLD_PRINT_LIBRARIES='1')
    code="""import sys,json,importlib,ssl;from pathlib import Path
root=Path(sys.argv[1]).resolve();sys.path[:0]=[str(root/'app'),str(root/'tree')]
names=('python_app','webview','Quartz','Cocoa','WebKit','anthropic','openai','httpx','aiohttp','telethon','paramiko','cryptography','psutil','yaml','socks','pytest','aiogram','soundfile')
rows={}
for name in names:
 m=importlib.import_module(name);p=Path(m.__file__).resolve() if getattr(m,'__file__',None) else None
 if p is not None:assert p.is_relative_to(root),(name,str(p))
 rows[name]=str(p)
from localharness.tls_context import default_context
ctx=default_context();assert ctx.check_hostname and ctx.verify_mode==ssl.CERT_REQUIRED and ctx.get_ca_certs()
print(json.dumps({'imports':rows,'python':sys.version,'trusted_roots':len(ctx.get_ca_certs())}))
"""
    command([program/'runtime/bin/python3','-I','-B','-c',code,program],'isolated-runtime-imports.log',env=env)
    loaded=(OUT/'isolated-runtime-imports.log').read_text()
    assert not any(p in loaded for p in ('/opt/homebrew/','/usr/local/','/Library/Frameworks/Python'))
    command(['codesign','--verify','--deep','--strict',program/'Helene.app'],'distribution-codesign.log')
    for i,p in enumerate((program/'runtime/bin/python3',program/'Helene.app/Contents/MacOS/Helene',program/'helene-body',program/'helene-bridge',program/'verification-tools/bin/node')):
        command(['file',p],f'binary-{i}.log');assert 'arm64' in (OUT/f'binary-{i}.log').read_text()
        command(['otool','-L',p],f'dylibs-{i}.log')
    result=subprocess.run(['spctl','--assess','--type','execute','--verbose=4',str(program/'Helene.app')],capture_output=True,text=True)
    (OUT/'gatekeeper.log').write_text(result.stdout+result.stderr)
    save('DISTRIBUTION.json',{'source_commit':PRODUCT_COMMIT,'isolated_imports':True,'codesign_valid':True,
        'platform':subprocess.check_output(['sw_vers'],text=True),'arch':os.uname().machine,
        'gatekeeper_assessment_exit':result.returncode,'notarized':False,'physical_owner_acceptance':False})

def native(program):
    from localharness import owner_stop
    import rc18_macos_pipeline as helper
    from macos_final_delta_smoke import smoke
    isolated(program);helper.EVIDENCE=OUT/'native';helper.EVIDENCE.mkdir()
    original=helper.invoke;body_checked=[]
    def invoke(root,name,args=None,timeout=90):
        if name=='first_create':args={'name':'RC23 native fixture','owner':'Disposable CI','provider':'openai','model':'fixture','key':'fixture-unused','base_url':'http://127.0.0.1:9/v1'}
        if name=='first_open':owner_stop.change_pause(TEMP/'shared-delta-smoke-home/Helene',True,reason='Native fixture stays paused',via='fixture')
        result=original(root,name,args,timeout)
        if name=='app_info':assert result['version']==VERSION
        if name=='first_open' and not body_checked:
            helper.wait(lambda:original(root,'app_info').get('version')==VERSION,120)
            command([sys.executable,'-B','-m','unittest','tests.t_body_macos.Contract','-v'],'body-contract.log',cwd=REPO/'desk',env=dict(os.environ,HELENE_BODY_DIR=str(root)))
            body_checked.append(True)
        return result
    helper.invoke=invoke;os.environ['COCOA_EXIT_REPETITIONS']='3'
    try:smoke(program)
    finally:helper.invoke=original
    assert body_checked
    save('CURRENT-NATIVE-PASS.json',{'source_commit':PRODUCT_COMMIT,'new_archive_GUI_Exit_move':True,'body_contract':True,
        'paused_loopback_profile':True,'native_unit_evidence':[41359869672,41360169871],'full_broad_green':False,
        'physical_TCC_mic_accounts_Intel_notarization':False})

def qualify():
    assert json.loads((PROGRAM/'macos-inputs.json').read_text())['source_commit']==PRODUCT_COMMIT
    native(PROGRAM)
def upgrade():
    """Real old18 -> new23 clean update and current23 failed-start rollback."""
    import http.server,ssl,threading,urllib.error,psutil
    import rc18_macos_pipeline as helper
    from localharness import owner_stop,desktop_update_request as jobs
    from localharness.package_notice import read,write
    home=TEMP/'upgrade-home';home.mkdir();data=home/'Helene';root=BASE
    result={'old_archive_sha256':BASE_SHA,'source_commit':PRODUCT_COMMIT,'steps':{},'semantic_review_accepted':False}
    def receipt():save('FULL-ARCHIVE-UPDATE.json',result)
    cert=TEMP/'loopback-cert.pem';key=TEMP/'loopback-key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost',
        '-addext','subjectAltName=DNS:localhost','-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
    serve=TEMP/'archive-https';serve.mkdir();archive=next(PACK.glob('Helene-*-macos-arm64.zip'));shutil.copy2(archive,serve/archive.name)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self,*a,**k):super().__init__(*a,directory=str(serve),**k)
        def log_message(self,*a):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key);server.socket=context.wrap_socket(server.socket,server_side=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    env=dict(os.environ,HOME=str(home),CFFIXED_USER_HOME=str(home),SSL_CERT_FILE=str(cert),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    process=None;watch=None;stop=threading.Event();log=(OUT/'upgrade-window.log').open('wb')
    def info():return helper.invoke(root,'app_info')
    def finish(identity,failed=False):
        end=time.monotonic()+360
        while time.monotonic()<end:
            row=jobs.status(data)
            if row.get('id')==identity:
                if row.get('state')=='completed':
                    assert not failed,'Fault injection did not stop the actual new startup';return row
                if row.get('state') in ('attention','cancelled','superseded'):
                    assert failed,row;return row
            time.sleep(.2)
        raise TimeoutError('Update did not settle: '+str(jobs.status(data)))
    saved={'memory/groups/rc23-upgrade.jsonl':b'{"text":"old history retained"}\n',
        'memory/.state/devices.json':b'{"fixture":"device"}','memory/.state/phone-pairs.json':b'{"fixture":"phone"}',
        'workspace/rc23-upgrade.txt':b'owner material'}
    def data_exact():
        for name,raw in saved.items():assert (data/name).read_bytes()==raw,name
        assert read(root/'helene.json')['rc23_fixture_custom']=={'preserve':'exact'}
    try:
        process=subprocess.Popen([str(root/'Helene.app/Contents/MacOS/Helene'),'--root',str(root)],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
        helper.wait(lambda:helper.invoke(root,'first_status').get('stage')=='create',120)
        helper.invoke(root,'first_create',{'name':'Previous18 fixture','owner':'Disposable CI','provider':'openai',
            'model':'fixture','key':'fixture-unused','base_url':'http://127.0.0.1:9/v1'})
        owner_stop.change_pause(data,True,reason='Old/native update fixture remains paused',via='fixture')
        for name,raw in saved.items():
            path=data/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        cfg=read(root/'helene.json');cfg['rc23_fixture_custom']={'preserve':'exact'};write(root/'helene.json',cfg)
        helper.invoke(root,'first_open');helper.wait(lambda:info().get('version')=='2.3.0-rc.18',120)
        result['steps']['actual_unmodified_old18_application_runtime_opened']=True;receipt()
        url=f'https://localhost:{server.server_port}/{archive.name}'
        try:helper.invoke(root,'update_download',{'url':url,'sha256':'0'*64,'extensions':[]},timeout=300)
        except urllib.error.HTTPError:assert info()['version']=='2.3.0-rc.18';result['steps']['wrong_hash_refused_old_window_preserved']=True
        else:raise AssertionError('Wrong archive SHA accepted')
        download=helper.invoke(root,'update_download',{'url':url,'sha256':digest(archive),'extensions':[]},timeout=300)
        assert download['sha_ok'] and digest(download['path'])==digest(archive);result['steps']['actual_full_HTTPS_download_exact']=True;receipt()
        admission=helper.invoke(root,'update_install',{'path':download['path'],'sourceMode':'clean'})
        identity=admission.get('id') or admission.get('job',{}).get('id');assert identity;finish(identity)
        helper.wait(lambda:info().get('version')==VERSION,150);data_exact()
        assert str(root/'Helene.app/Contents/MacOS/Helene') in psutil.Process(helper.control(root)['pid']).cmdline()
        result['steps']['actual_old18_to_new23_clean_update_native_Cocoa_data_and_unknown_config_preserved']=True;receipt()
        subprocess.run(['screencapture','-x',str(OUT/'after-old18-to-new23.png')],check=True)
        cfg=read(root/'helene.json');code=Path(cfg.get('code','tree'));code=(code if code.is_absolute() else root/code).resolve()
        authored=code/'rc23-rollback-owner.py';authored.write_bytes(b'OWNER_CHANGE=23\n');before_cfg=(root/'helene.json').read_bytes()
        before_file=authored.read_bytes();oldpid=helper.control(root)['pid'];started=time.time();killed=[]
        def fail_new_start():
            while not stop.is_set():
                for pid in psutil.pids():
                    try:
                        p=psutil.Process(pid);born=p.create_time();args=p.cmdline()
                        if pid!=oldpid and born>=started-.05 and '--migration' in args and str(root) in args and any(a in args for a in (str(root/'app/python_app.py'),str(root/'Helene.app/Contents/MacOS/Helene'))):
                            if psutil.Process(pid).create_time()==born:p.kill();killed.append({'pid':pid,'birth':born});return
                    except (psutil.Error,OSError,ValueError,SystemError):pass
                time.sleep(.01)
        watch=threading.Thread(target=fail_new_start,daemon=True);watch.start()
        admission=helper.invoke(root,'update_install',{'path':download['path'],'sourceMode':'clean'})
        identity=admission.get('id') or admission.get('job',{}).get('id');assert identity;finish(identity,failed=True)
        stop.set();watch.join(3);assert len(killed)==1
        helper.wait(lambda:info().get('version')==VERSION,150)
        assert authored.read_bytes()==before_file and (root/'helene.json').read_bytes()==before_cfg;data_exact()
        result['steps']['actual_failed_new_start_rollback_owner_edit_config_data_and_Cocoa_reopened']={'faulted':killed};receipt()
        subprocess.run(['screencapture','-x',str(OUT/'after-failed-start-rollback.png')],check=True)
        assert helper.invoke(root,'update_cancel').get('ok');helper.wait(lambda:jobs.status(data).get('state')=='cancelled',90)
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error);receipt()
        subprocess.run(['screencapture','-x',str(OUT/'upgrade-failure.png')]);raise
    finally:
        stop.set()
        if watch:watch.join(3)
        try:
            if helper.control(root):helper.invoke(root,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
        except Exception:pass
        if process:
            try:process.wait(15)
            except subprocess.TimeoutExpired:process.terminate();process.wait(10)
        server.shutdown();server.server_close();log.close()
def matrix_prepare():
    from build_python_macos_rc import unpack
    archive=next((WORKSPACE/'candidate').glob('Helene-*-macos-arm64.zip'))
    assert digest(archive)==os.environ['CANDIDATE_SHA256'];unpack(archive,TEMP/'rc23-output')
    driver=TEMP/'matrix-driver-runtime';shutil.copytree(PROGRAM/'runtime',driver)
    python=driver/'bin/python3';os.execv(str(python),[str(python),'-B',str(QA),'qualify'])
if __name__=='__main__':globals()[sys.argv[1]]()
