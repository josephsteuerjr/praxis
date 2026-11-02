"""External RC24 apparatus: exact frozen subject, retained binaries, narrow tests."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,importlib,json,os,plistlib,shutil,subprocess,sys,time,unittest,zipfile
import rc23_macos_candidate_qa as base

QA=Path(__file__).resolve();REPO=base.REPO;TEMP=base.TEMP
COMMIT='61d984508fad19e1b0f95b77f92fde42ae18da45';VERSION='2.3.0-rc.24'
OLD_SHA='1fe57769e3f8b4587f8c728c377b833286c092c2c2e38a26cdd93f93d5cd7816'
OUT=base.WORKSPACE/('evidence-rc24-macos-'+os.environ['RC24_LANE']);OUT.mkdir(exist_ok=True)
PACK=TEMP/'rc24-output';PROGRAM=PACK/'Helene';BASE=TEMP/'rc24-qualified/Helene'
OLD_ARCHIVE=TEMP/'qualified-current-rc23.zip'
base.OUT=OUT;base.PRODUCT_COMMIT=COMMIT;base.VERSION=VERSION;base.QA=QA
digest=base.digest;save=base.save;command=base.command
os.environ['PYTHONDONTWRITEBYTECODE']='1'

def inventory(root):
    return {p.relative_to(root).as_posix():{'sha256':digest(p),'mode':p.stat().st_mode&0o777}
        for p in sorted(root.rglob('*')) if p.is_file()}

def qualified():
    assert sys.platform=='darwin' and os.uname().machine=='arm64'
    base.subject_identity(materialize=True)
    from build_python_macos_rc import unpack
    from macos_archive_delta import restore
    outer=TEMP/'qualified-release-artifact.zip';assert digest(outer)==base.OUTER_SHA
    old18=TEMP/'qualified-rc18.zip'
    with zipfile.ZipFile(outer) as z:
        rows=[r for r in z.infolist() if Path(r.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip'];assert len(rows)==1
        old18.write_bytes(z.read(rows[0]))
    assert digest(old18)==base.BASE_SHA
    middle=TEMP/'qualified-intermediate-rc23.zip'
    chain=(('current-rc23-evidence.zip','8a91c005ca5bc1b3707c0bec9e61e57becb19bccbaed452e1f975b046af31381',
            'exact-archive-delta.zip',old18,middle,'15b56cad2c7df77372d8ea4253f00db8c2768d025a4020b4bbab55cf9e5ca0ae'),
           ('owner-ux-sealed-evidence.zip','b8d08334516fbbf57fe2efbd2b3ca9fd9d59be7d6aa61cc1a86b0d54ec4f3f00',
            'owner-ux-exact-archive-delta.zip',middle,OLD_ARCHIVE,OLD_SHA))
    for name,sha,member,prior,destination,wanted in chain:
        evidence=TEMP/name;assert digest(evidence)==sha
        patch=TEMP/(name+'.delta.zip')
        with zipfile.ZipFile(evidence) as z:
            assert z.testzip() is None;patch.write_bytes(z.read(member))
        restore(prior,patch,destination);assert digest(destination)==wanted
    unpack(OLD_ARCHIVE,BASE.parent)
    save('QUALIFIED-BASE.json',{'archive_sha256':OLD_SHA,'retained_rc23_scope':
        'Prior actual native15/26, first creation, warning, Cocoa lifecycle and changed74 contracts. No repeat of healthy broad/native builds.'})

def prepare():
    qualified();driver=TEMP/'rc24-driver-runtime';shutil.copytree(BASE/'runtime',driver)
    python=driver/'bin/python3';os.execv(str(python),[str(python),'-B',str(QA),'build'])

def focused():
    modules=('tests.t_source_battery_controls_0211','tests.t_source_update_parallel_2910',
        'tests.t_source_hand_custody_0211','tests.t_bundled_verification_executor_0111',
        'tests.t_verification_windows_0211')
    cases=[]
    for name in modules:
        module=importlib.import_module(name)
        for cls in vars(module).values():
            if isinstance(cls,type) and issubclass(cls,unittest.TestCase) and cls.__module__==name:
                cases.extend(t.id() for t in unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    cases.append('tests.t_source_update_hand_3010.HandTransport.test_actual_legacy_client_process_reuses_three_calls_without_cli_or_runtime_rechecks')
    assert len(cases)==len(set(cases))
    retained=[];previous=TEMP/'rc24-prior-focused.zip'
    if os.environ['RC24_LANE']=='15' and previous.is_file():
        assert digest(previous)=='f998db3b8c3edd9d4767528713daf30e7aee895d17cfe343d90db05da4de8705'
        with zipfile.ZipFile(previous) as z:
            assert z.testzip() is None
            subject=json.loads(z.read('SUBJECT-SOURCE.json'));current=json.loads((OUT/'SUBJECT-SOURCE.json').read_text())
            assert subject['commit']==current['commit']==COMMIT and subject['files']==current['files']
            report=json.loads(z.read('TARGETED-PYTHON.json'))
            assert {r['id'] for r in report['outcomes']}==set(cases)
            for row in report['outcomes']:
                if row['passed']:
                    (OUT/row['log']).write_bytes(z.read(row['log']));retained.append({**row,'retained_from_run':41387562900})
            (OUT/'PRIOR-TARGETED-PYTHON.json').write_bytes(z.read('TARGETED-PYTHON.json'))
        save('RETAINED-FOCUSED.json',{'exact_frozen_source_manifest':True,'prior_run':41387562900,
            'retained_passed':len(retained),'only_previous_failed_case_repeated':True,
            'prior_RED':'External runner omitted composed core from PYTHONPATH; command_process import failed before the actual child scenario.'})
    pending=[c for c in cases if c not in {r['id'] for r in retained}]
    def execute(case):
        work=TEMP/'rc24-targeted'/case;work.mkdir(parents=True)
        env=dict(os.environ,HOME=str(work),CFFIXED_USER_HOME=str(work),TMPDIR=str(work),
            PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',
            PYTHONPATH=os.pathsep.join(map(str,(REPO/'helene/core',REPO/'praxis'))))
        start=time.monotonic();log=OUT/(case+'.log')
        with log.open('wb') as stream:
            p=subprocess.run([sys.executable,'-B','-m','unittest',case,'-v'],cwd=REPO/'desk',env=env,
                stdout=stream,stderr=subprocess.STDOUT,timeout=180)
        text=log.read_text(errors='replace')
        return {'id':case,'exit':p.returncode,'seconds':time.monotonic()-start,'log':log.name,
            'passed':p.returncode==0 and 'Ran 1 test' in text and 'skipped' not in text.lower()}
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:rows=retained+list(pool.map(execute,pending))
    report={'source_commit':COMMIT,'platform':sys.platform,'macos':subprocess.check_output(['sw_vers','-productVersion'],text=True).strip(),
        'unique':len(cases),'workers':os.cpu_count(),'outcomes':rows,'passed':all(r['passed'] for r in rows),
        'scope':'Affected scheduler, Stop/Pause, source-hand custody/real legacy HTTP, executor layout/cache and packer units only; prior broad RED retained.'}
    save('TARGETED-PYTHON.json',report)
    assert report['passed'],[r for r in rows if not r['passed']]

def build():
    from portable_sources import compose_core,frontend_inputs,frontend_outputs
    from python_posix_bundle import assemble,normalize_output_modes
    from localharness import source_verification_bundle as bundle,source_verification_executor as executor,source_update_host
    from build_python_macos_rc import unpack
    base.subject_identity();focused()
    prior=frontend_inputs(BASE/'verification/repository');current=frontend_inputs(REPO)
    assert prior==current,'Frontend source inputs changed'
    retained={}
    for source,name in (('static','app'),('mobile','mobile'),('miniapp','miniapp')):
        shutil.copytree(BASE/'app'/source,REPO/'desk'/name/'dist')
        retained[name]=inventory(BASE/'app'/source)
    save('RETAINED-FRONTENDS.json',{'source_commit':COMMIT,'qualified_archive_sha256':OLD_SHA,
        'inputs':current,'inputs_all_exact_prior':True,'outputs':retained,'rebuilt_commands':[],
        'scope':'Retained actual qualified app/mobile/miniapp output bytes. No claim of new frontend builds.'})
    maps={name:inventory(BASE/name) for name in ('runtime','Helene.app','verification-tools')}
    for name in ('helene-body','helene-bridge'):
        maps[name]={'sha256':digest(BASE/name),'mode':(BASE/name).stat().st_mode&0o777}
    native=json.loads((BASE/'native-build.json').read_text());assert native['exit']==0 and native['platform']=='arm64'
    sources={}
    for prefix in ('praxis/body','helene/core/body'):
        for p in (REPO/prefix).rglob('*'):
            if p.is_file() and not any(x in p.parts for x in ('.git','target','__pycache__')):sources[p.relative_to(REPO/prefix).as_posix()]=digest(p)
    assert sources==native['source_files']
    core,origins=compose_core(REPO);edition=TEMP/'rc24-edition';edition.mkdir()
    assert {n:{'sha256':hashlib.sha256(r.data).hexdigest(),'mode':0o755 if r.mode=='100755' else 0o644} for n,r in core.items()}==inventory(BASE/'tree')
    for name,row in core.items():
        p=edition/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(row.data);p.chmod(0o755 if row.mode=='100755' else 0o644)
    tools=BASE/'verification-tools'
    lock=bundle.executor_lock(tools={'node':str(tools/'bin/node'),'npm':str(tools/'bin/npm')});lock['tools'].pop('cargo',None)
    verification=TEMP/'rc24-verification';bundle.build(REPO,verification,VERSION,{'darwin':lock})
    manifest=json.loads((verification/'manifest.json').read_text());manifest['bundled_executors']={'darwin':executor.describe(tools)}
    (verification/'manifest.json').write_bytes(bundle.encoded(manifest));bundle.load(verification,digest(verification/'manifest.json'),VERSION)
    downloads=TEMP/'rc24-component-downloads.json';components=json.loads((REPO/'desk/installer/mac-component-downloads-rc23.json').read_text())
    components['app_version']=VERSION;downloads.write_text(json.dumps(components,indent=2))
    PACK.mkdir();copytree=shutil.copytree;run=subprocess.run;imports=[]
    def transfer(src,dst,*a,**k):
        if Path(src).resolve() in ((BASE/'runtime').resolve(),tools.resolve()):
            assert not Path(dst).exists() and Path(dst).resolve().is_relative_to(PROGRAM.resolve())
            return shutil.move(str(src),str(dst))
        return copytree(src,dst,*a,**k)
    def readonly_import(argv,*a,**k):
        if isinstance(argv,(tuple,list)) and str(argv[0])==str(PROGRAM/'runtime/bin/python3'):
            assert '-I' in argv and '-c' in argv;argv=[argv[0],'-B',*argv[1:]];imports.append(list(map(str,argv)))
        return run(argv,*a,**k)
    shutil.copytree=transfer;subprocess.run=readonly_import
    try:initial=assemble(BASE/'runtime',edition,None,None,PROGRAM,version=VERSION,target='macos-arm64',downloads=downloads,
        body=BASE/'helene-body',bridge=BASE/'helene-bridge',verification=verification,verification_tools=tools)
    finally:shutil.copytree=copytree;subprocess.run=run
    initial.rename(TEMP/'rc24-before-cocoa.zip')
    shutil.copytree(BASE/'Helene.app',PROGRAM/'Helene.app')
    command(['codesign','--verify','--deep','--strict',PROGRAM/'Helene.app'],'codesign-verify.log')
    unpack(REPO/'desk/installer/rc18-native-license-texts.zip',PROGRAM)
    for name in ('native-build.json','runtime-script-portability.json','helene','Start Helene.command'):shutil.copy2(BASE/name,PROGRAM/name)
    shutil.copy2(REPO/'desk/installer/PYTHON-MACOS-START-HERE.md',PROGRAM/'START-HERE.md')
    normalize_output_modes(PROGRAM)
    for name in ('runtime','Helene.app','verification-tools'):assert maps[name]==inventory(PROGRAM/name),name
    for name in ('helene-body','helene-bridge'):assert maps[name]=={'sha256':digest(PROGRAM/name),'mode':(PROGRAM/name).stat().st_mode&0o777}
    for source,name in (('static','app'),('mobile','mobile'),('miniapp','miniapp')):assert inventory(PROGRAM/'app'/source)==retained[name]
    info=plistlib.loads((PROGRAM/'Helene.app/Contents/Info.plist').read_bytes())
    assert info['CFBundleShortVersionString']=='2.3.0' and info['CFBundleVersion']=='23023'
    save('QUALIFIED-REUSE.json',{'passed':True,'base_archive_sha256':OLD_SHA,'files':maps,
        'native_source_files_exact':len(sources),'core_files_exact':len(core),'readonly_imports':imports,'frontend_outputs_exact':True,
        'no_native_frontend_runtime_rebuild':True,'voice_downloads':0,'native_plist_identity':{'short':'2.3.0','build':'23023'}})
    (PROGRAM/'macos-inputs.json').write_text(json.dumps({'format':'helene-macos-inputs.v1','version':VERSION,'source_commit':COMMIT,
        'native_reused_from_archive':OLD_SHA,'core_origins':origins,'scope':'Affected source delta plus actual Cocoa lifecycle15/26. Prior native qualification retained.',
        'limits':'No physical TCC/microphone/accounts/Intel/notarization or whole broad semantic acceptance.'},indent=2))
    for file,key in (('python-app.json','version'),('helene-build.json','version'),('source-inputs.json','version'),
        ('app/desk.json','version'),('verification/manifest.json','version'),('app/component-downloads.json','app_version')):
        assert json.loads((PROGRAM/file).read_text())[key]==VERSION,(file,key)
    files={p.relative_to(PROGRAM).as_posix():digest(p) for p in PROGRAM.rglob('*') if p.is_file()
        and p.relative_to(PROGRAM).parts[0] not in ('tree','components','data','downloads') and p.name not in ('helene.json','python-package-files.json')}
    (PROGRAM/'python-package-files.json').write_text(json.dumps({'format':'helene-python-program-files.v1','version':VERSION,'files':files},indent=2))
    archive=PACK/f'Helene-{VERSION}-macos-arm64.zip'
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PROGRAM.rglob('*')):
            if p.is_file():z.write(p,'Helene/'+p.relative_to(PROGRAM).as_posix())
    save('RUNTIME-ADMISSION.json',source_update_host.verified_runtime(PROGRAM))
    candidate={'format':'helene-macos-rc24-candidate.v1','version':VERSION,'source_commit':COMMIT,'sha256':digest(archive),
        'size':archive.stat().st_size,'archive':archive.name,'reused_archive_sha256':OLD_SHA,'qualification_pending':True}
    (PACK/'candidate.json').write_text(json.dumps(candidate,indent=2));save('candidate.json',candidate)
    (PACK/(archive.name+'.sha256')).write_text(candidate['sha256']+'  '+archive.name+'\n')
    from macos_archive_delta import create,restore,versioned
    patch=OUT/'rc24-exact-archive-delta.zip';create(OLD_ARCHIVE,archive,patch)
    save('DELTA-ROUNDTRIP.json',restore(OLD_ARCHIVE,patch,TEMP/'rc24-roundtrip.zip'))
    save('MANUAL-ARCHIVE.json',versioned(archive,PACK/f'Helene-{VERSION}-macos-arm64-manual.zip',f'Helene-{VERSION}-macos-arm64'))
    native_check()
    print(json.dumps(candidate),flush=True)

def prepare26():
    qualified()
    from macos_archive_delta import restore
    sealed=TEMP/'rc24-sealed';candidate=json.loads((sealed/'candidate.json').read_text())
    assert candidate['source_commit']==COMMIT and candidate['version']==VERSION
    PACK.mkdir();archive=PACK/candidate['archive'];restore(OLD_ARCHIVE,sealed/'rc24-exact-archive-delta.zip',archive)
    assert digest(archive)==candidate['sha256'];(PACK/'candidate.json').write_text(json.dumps(candidate));save('candidate.json',candidate)
    python=BASE/'runtime/bin/python3';os.execv(str(python),[str(python),'-B',str(QA),'check26'])

def check26():base.subject_identity();focused();native_check()

def native_check():
    from build_python_macos_rc import unpack
    import rc18_macos_pipeline as helper
    import psutil
    candidate=json.loads((PACK/'candidate.json').read_text());archive=PACK/candidate['archive'];assert digest(archive)==candidate['sha256']
    unpack(archive,TEMP/'rc24-native-install');program=TEMP/'rc24-native-install/Helene'
    helper.EVIDENCE=OUT;helper.REPO=REPO
    home=TEMP/'rc24-native-home';home.mkdir();data=home/'Helene'
    env=dict(os.environ,HOME=str(home),CFFIXED_USER_HOME=str(home),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    # Set up and pause a task-only profile before launching the unchanged real Cocoa executable.
    code="""import sys;from pathlib import Path
root=Path(sys.argv[1]);data=Path(sys.argv[2]);sys.path[:0]=[str(root/'app'),str(root/'tree')]
from localharness import posix_first_start as p,owner_stop
places=p.Places(data);p.prepare(root,places=places)
p.create_agent(root,{'name':'RC24 disposable fixture','owner':'CI','provider':'openai','model':'fixture-unused',
 'key':'fixture-unused','base_url':'http://127.0.0.1:9/v1','soul':{'kind':'text','text':'# RC24 fixture\\n'}},places=places)
owner_stop.change_pause(data,True,reason='Task-only Cocoa version and Exit check',via='fixture')
"""
    command([program/'runtime/bin/python3','-B','-c',code,program,data],'native-private-profile.log',env=env,cwd=program)
    result={'source_commit':COMMIT,'archive_sha256':candidate['sha256'],'platform':subprocess.check_output(['sw_vers','-productVersion'],text=True).strip(),
        'scope':'Brief actual unchanged Cocoa launcher open, current app_info RC24 and ordinary Exit; task-only profile paused before launch.'}
    p=None;log=(OUT/'native-cocoa.log').open('wb')
    try:
        p=subprocess.Popen([str(program/'Helene.app/Contents/MacOS/Helene'),'--root',str(program)],cwd=program,env=env,stdout=log,stderr=subprocess.STDOUT)
        info=helper.wait(lambda:(v if (v:=helper.invoke(program,'app_info')).get('version')==VERSION and v.get('root')==str(program) else None),150)
        window=helper.wait(lambda:(v if (v:=helper.control(program)) and v.get('ui_ready') else None),150)
        descriptor=json.loads((data/'memory/.control/desktop-session.json').read_text());owner=psutil.Process(descriptor['pid'])
        tracked={(q.pid,q.create_time()) for q in (owner,*owner.children(recursive=True),psutil.Process(p.pid))}
        result['app_info']={k:v for k,v in info.items() if k in ('version','platform','arch','root','exe_dir')}
        result['native_window_ready']={k:v for k,v in window.items() if k in ('pid','root','ui_ready','window_id')}
        save('NATIVE-CHECK.json',result);time.sleep(2);helper.snapshot('rc24-ordinary-cocoa.png')
        helper.invoke(program,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
        p.wait(75);assert p.returncode==0;helper.wait(lambda:not helper.control(program),75)
        def gone():
            for pid,birth in tracked:
                try:
                    q=psutil.Process(pid)
                    if q.create_time()==birth and q.status()!=psutil.STATUS_ZOMBIE:return False
                except psutil.NoSuchProcess:pass
            return True
        helper.wait(gone,75);result['observed_PID_births_absent']=len(tracked);result['passed']=True;save('NATIVE-CHECK.json',result)
    except BaseException as e:
        result['error']=type(e).__name__+': '+str(e);save('NATIVE-CHECK.json',result);helper.snapshot('rc24-failure.png');raise
    finally:
        if helper.control(program):
            try:helper.invoke(program,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
            except Exception:pass
        if p:
            try:p.wait(15)
            except subprocess.TimeoutExpired:p.terminate();p.wait(15)
        log.close()

if __name__=='__main__':globals()[sys.argv[1]]()
