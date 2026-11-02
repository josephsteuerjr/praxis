"""External apparatus for the owner-UX delta, with an immutable product checkout."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,importlib,json,os,re,shutil,subprocess,sys,time,unittest,zipfile
import rc23_macos_candidate_qa as base

QA=Path(__file__).resolve();REPO=base.REPO;TEMP=base.TEMP
COMMIT='d299ac425a21980ce63f6f590cea831d8793b4a2'
VERSION='2.3.0-rc.23'
OLD_SHA='15b56cad2c7df77372d8ea4253f00db8c2768d025a4020b4bbab55cf9e5ca0ae'
OLD_OUTER='8a91c005ca5bc1b3707c0bec9e61e57becb19bccbaed452e1f975b046af31381'
OUT=base.WORKSPACE/'evidence-rc23-owner-ux';OUT.mkdir(exist_ok=True)
PACK=TEMP/'owner-ux-output';PROGRAM=PACK/'Helene';BASE=TEMP/'owner-ux-qualified/Helene'
OLD_ARCHIVE=TEMP/'qualified-current-rc23.zip'
base.OUT=OUT;base.PRODUCT_COMMIT=COMMIT;base.QA=QA
digest=base.digest;save=base.save;command=base.command
os.environ['PYTHONDONTWRITEBYTECODE']='1'

def prepare():
    assert sys.platform=='darwin' and os.uname().machine=='arm64'
    base.subject_identity(materialize=True)
    prior=TEMP/'owner-ux-prior-checks.zip'
    assert digest(prior)=='504302dc7e73b1cc3373235c71357a71536cac6a4224da398a55beb629abfd27'
    with zipfile.ZipFile(prior) as z:
        assert z.testzip() is None
        expected=json.loads(z.read('SUBJECT-SOURCE.json'))
        current=json.loads((OUT/'SUBJECT-SOURCE.json').read_text())
        assert expected['commit']==current['commit']==COMMIT and expected['files']==current['files']
        checked=json.loads(z.read('TARGETED-PYTHON.json'))
        assert checked['passed'] and checked['unique']==checked['ran'] and all(r['status']=='passed' for r in checked['outcomes'])
        for name in ('TARGETED-PYTHON.json','targeted-python.log','targeted-node.log'):(OUT/name).write_bytes(z.read(name))
        assert 'ℹ fail 0' in z.read('targeted-node.log').decode()
    save('RETAINED-CHANGED-CONTRACTS.json',{'source_commit':COMMIT,'prior_run':41371366915,
        'prior_evidence_outer_sha256':digest(prior),'full_source_manifest_exact':True,
        'python_unique_passed':checked['unique'],'node_log':'targeted-node.log','no_healthy_contract_repeat':True})
    from build_python_macos_rc import unpack
    from macos_archive_delta import restore
    outer=TEMP/'qualified-release-artifact.zip';assert digest(outer)==base.OUTER_SHA
    old18=TEMP/'qualified-rc18.zip'
    with zipfile.ZipFile(outer) as z:
        rows=[r for r in z.infolist() if Path(r.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip'];assert len(rows)==1
        old18.write_bytes(z.read(rows[0]))
    assert digest(old18)==base.BASE_SHA
    evidence=TEMP/'current-rc23-evidence.zip';assert digest(evidence)==OLD_OUTER
    patch=TEMP/'qualified-current-delta.zip'
    with zipfile.ZipFile(evidence) as z:
        rows=[r for r in z.infolist() if Path(r.filename).name=='exact-archive-delta.zip'];assert len(rows)==1
        patch.write_bytes(z.read(rows[0]))
    restore(old18,patch,OLD_ARCHIVE);assert digest(OLD_ARCHIVE)==OLD_SHA
    unpack(OLD_ARCHIVE,BASE.parent)
    driver=TEMP/'owner-ux-driver-runtime';shutil.copytree(BASE/'runtime',driver)
    python=driver/'bin/python3';os.execv(str(python),[str(python),'-B',str(QA),'build'])

def focused():
    names=('tests.t_first_birth_handoff_0211','tests.t_first_constitution_0211',
        'tests.t_posix_first_start_0111','tests.t_first_start_3010','tests.t_first_start_relay_3110',
        'tests.t_incoming_cancel_stage_0211')
    cases=[]
    for name in names:
        module=importlib.import_module(name)
        for value in vars(module).values():
            if isinstance(value,type) and issubclass(value,unittest.TestCase) and value.__module__==name:
                cases.extend(unittest.defaultTestLoader.loadTestsFromTestCase(value))
    assert len(cases)==len({t.id() for t in cases})
    class Observed(unittest.TextTestResult):
        def __init__(self,*a,**k):super().__init__(*a,**k);self.rows=[]
        def addSuccess(self,t):super().addSuccess(t);self.rows.append({'id':t.id(),'status':'passed'})
        def addSkip(self,t,r):super().addSkip(t,r);self.rows.append({'id':t.id(),'status':'skipped','reason':r})
        def addFailure(self,t,e):super().addFailure(t,e);self.rows.append({'id':t.id(),'status':'failed','error':self._exc_info_to_string(e,t)})
        def addError(self,t,e):super().addError(t,e);self.rows.append({'id':t.id(),'status':'error','error':self._exc_info_to_string(e,t)})
    start=time.monotonic()
    with (OUT/'targeted-python.log').open('w') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Observed).run(unittest.TestSuite(cases))
    save('TARGETED-PYTHON.json',{'passed':result.wasSuccessful(),'unique':len(cases),'ran':result.testsRun,
        'outcomes':result.rows,'seconds':time.monotonic()-start,'source_commit':COMMIT,
        'scope':'Only changed birth/constitution/first-start/cancellation contracts on actual Darwin. Prior broad REDs are retained.'})
    assert result.wasSuccessful()

def frontend_closure(before,after):
    changed=sorted(n for n in set(before)|set(after) if before.get(n)!=after.get(n))
    assert changed==['desk/ui-kit/window/views/settings-frame.ts'],changed
    # A conservative graph includes type-only, re-export and dynamic relative
    # imports. Package/config/public assets are additionally exact in before.
    patterns=(r'\b(?:from\s*|import\s*)[\'\"]([^\'\"]+)[\'\"]',r'\bimport\s*\(\s*[\'\"]([^\'\"]+)[\'\"]')
    closures={}
    for name in ('mobile','miniapp'):
        pending=[(REPO/'desk'/name/'src/main.ts').resolve(),(REPO/'desk'/name/'vite.config.ts').resolve()];seen=set();external=set()
        while pending:
            p=pending.pop()
            if p in seen:continue
            assert p.is_relative_to(REPO) and p.is_file(),str(p)
            seen.add(p)
            if p.suffix not in ('.ts','.tsx','.js','.mjs','.css'):continue
            text=p.read_text()
            for pattern in patterns:
                for spec in re.findall(pattern,text):
                    if not spec.startswith('.'):
                        external.add(spec);continue
                    raw=(p.parent/spec).resolve()
                    selected=next((q for q in (raw,*[Path(str(raw)+s) for s in ('.ts','.tsx','.js','.mjs','.css')],raw/'index.ts',raw/'index.js') if q.is_file()),None)
                    assert selected is not None,(p,spec)
                    pending.append(selected)
        rows=sorted(p.relative_to(REPO).as_posix() for p in seen)
        assert not set(changed)&set(rows)
        assert all(before.get(n)==after.get(n) for n in rows)
        closures[name]={'relative_import_closure':rows,'external_package_imports':sorted(external),
            'all_inputs_and_package_locks_unchanged':True,'changed_window_module_unreachable':True}
    return {'changed_global_inputs':changed,'retained_closures':closures}

def build():
    from portable_sources import compose_core,frontend_inputs,frontend_outputs
    from python_posix_bundle import assemble,normalize_output_modes
    from localharness import source_verification_bundle as bundle,source_verification_executor as executor,source_update_host
    base.subject_identity()
    command([sys.executable,'-m','pip','check'],'pip-check.log')
    old=bundle.load(BASE/'verification',digest(BASE/'verification/manifest.json'),VERSION)
    tools=BASE/'verification-tools';selected=executor.provision(BASE,old,TEMP/'owner-ux-frontend-operation')
    before=frontend_inputs(REPO);old_inputs=frontend_inputs(BASE/'verification/repository')
    closure=frontend_closure(old_inputs,before)
    retained={}
    for name in ('mobile','miniapp'):
        shutil.copytree(BASE/'app'/name,REPO/'desk'/name/'dist')
        retained[name]={p.relative_to(BASE/'app'/name).as_posix():digest(p) for p in (BASE/'app'/name).rglob('*') if p.is_file()}
    env=dict(os.environ,PATH=str(tools/'bin')+os.pathsep+os.environ.get('PATH',''),npm_config_offline='true',
        npm_config_cache=selected['npm_config_cache'],npm_config_userconfig=str(TEMP/'empty-user.rc'),npm_config_globalconfig=str(TEMP/'empty-global.rc'))
    names=('app','remote')
    def install(n):command([selected['npm'],'ci','--offline','--ignore-scripts','--audit=false','--fund=false'],'npm-ci-'+n+'.log',cwd=REPO/'desk'/n,env=env)
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:list(pool.map(install,names))
    def build_front(n):command([selected['npm'],'run','build'],'frontend-'+n+'.log',cwd=REPO/'desk'/n,env=env)
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:list(pool.map(build_front,names))
    assert frontend_inputs(REPO)==before
    save('FRONTEND-DELTA-PROVENANCE.json',{'format':'helene-observed-and-retained-frontends.v1','source_commit':COMMIT,
        'base_archive_sha256':OLD_SHA,'rebuilt_commands':[{'cwd':'desk/'+n,'argv':['npm','run','build'],'returncode':0} for n in names],
        'retained_outputs':retained,'closure':closure,'inputs':before,'outputs':frontend_outputs(REPO),
        'no_claim_of_rebuilding_mobile_or_miniapp':True,'offline':True})
    with zipfile.ZipFile(OUT/'observed-frontends.zip','x',zipfile.ZIP_DEFLATED) as z:
        for name in (*names,'mobile','miniapp'):
            for p in sorted((REPO/'desk'/name/'dist').rglob('*')):
                if p.is_file():z.write(p,p.relative_to(REPO).as_posix())
    native=json.loads((BASE/'native-build.json').read_text());assert native['exit']==0 and native['platform']=='arm64'
    sources={}
    for prefix in ('praxis/body','helene/core/body'):
        for p in (REPO/prefix).rglob('*'):
            if p.is_file() and not any(x in p.parts for x in ('.git','target','__pycache__')):sources[p.relative_to(REPO/prefix).as_posix()]=digest(p)
    assert sources==native['source_files']
    for name in ('body','bridge'):assert digest(BASE/('helene-'+name))==native[name+'_sha256']
    core,origins=compose_core(REPO);edition=TEMP/'owner-ux-selected-edition';edition.mkdir()
    for name,row in core.items():
        p=edition/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(row.data);p.chmod(0o755 if row.mode=='100755' else 0o644)
    lock=bundle.executor_lock(tools={'node':str(tools/'bin/node'),'npm':str(tools/'bin/npm')});lock['tools'].pop('cargo',None)
    verification=TEMP/'owner-ux-verification';bundle.build(REPO,verification,VERSION,{'darwin':lock})
    manifest=json.loads((verification/'manifest.json').read_text());manifest['bundled_executors']={'darwin':executor.describe(tools)}
    (verification/'manifest.json').write_bytes(bundle.encoded(manifest));bundle.load(verification,digest(verification/'manifest.json'),VERSION)
    runtime_rows={p.relative_to(BASE/'runtime').as_posix():digest(p) for p in (BASE/'runtime').rglob('*') if p.is_file()}
    # Both directories are disposable task-owned inputs, never owner installs.
    # Preserve exact qualified Runtime/toolkit bytes, including their pycache.
    copytree=shutil.copytree
    def owned_transfer(src,dst,*a,**k):
        if Path(src).resolve() in ((BASE/'runtime').resolve(),tools.resolve()):
            assert not Path(dst).exists() and Path(dst).resolve().is_relative_to(PROGRAM.resolve())
            return shutil.move(str(src),str(dst))
        return copytree(src,dst,*a,**k)
    # The assembler's isolated import smoke uses -I, which ignores the
    # environment's PYTHONDONTWRITEBYTECODE. -B preserves the qualified cache
    # bytes while executing the same native import check.
    run=subprocess.run;import_checks=[]
    def native_readonly_import(argv,*a,**k):
        if isinstance(argv,(tuple,list)) and str(argv[0])==str(PROGRAM/'runtime/bin/python3'):
            assert '-I' in argv and '-c' in argv
            argv=[argv[0],'-B',*argv[1:]];import_checks.append(list(map(str,argv)))
        return run(argv,*a,**k)
    PACK.mkdir();shutil.copytree=owned_transfer;subprocess.run=native_readonly_import
    try:
        initial=assemble(BASE/'runtime',edition,None,None,PROGRAM,version=VERSION,target='macos-arm64',
            downloads=REPO/'desk/installer/mac-component-downloads-rc23.json',body=BASE/'helene-body',bridge=BASE/'helene-bridge',
            verification=verification,verification_tools=tools)
    finally:shutil.copytree=copytree;subprocess.run=run
    initial.rename(TEMP/'owner-ux-assembly-before-cocoa.zip')
    actual_runtime={p.relative_to(PROGRAM/'runtime').as_posix():digest(p) for p in (PROGRAM/'runtime').rglob('*') if p.is_file()}
    changed={n:{'before':runtime_rows.get(n),'after':actual_runtime.get(n)} for n in set(runtime_rows)|set(actual_runtime) if runtime_rows.get(n)!=actual_runtime.get(n)}
    save('RUNTIME-EXACT-CHECK.json',{'passed':not changed,'files':len(runtime_rows),'changes':changed,'actual_isolated_import_invocations':import_checks})
    assert not changed and import_checks
    shutil.copytree(BASE/'Helene.app',PROGRAM/'Helene.app')
    command(['codesign','--verify','--deep','--strict',PROGRAM/'Helene.app'],'codesign-verify.log')
    from build_python_macos_rc import unpack
    unpack(REPO/'desk/installer/rc18-native-license-texts.zip',PROGRAM)
    for name in ('START-HERE.md','native-build.json','runtime-script-portability.json','helene','Start Helene.command'):shutil.copy2(BASE/name,PROGRAM/name)
    reused={'archive_sha256':OLD_SHA,'native15_26_qualification_run':41360743312,'rollback_renderer_run':41361818225,
        'runtime_all_files_byte_exact':len(runtime_rows),'native_source_files_exact':len(sources),
        'cocoa_bundle_all_files_exact':{p.relative_to(BASE/'Helene.app').as_posix():digest(p) for p in (BASE/'Helene.app').rglob('*') if p.is_file()},
        'scope':'Exact Runtime, signed Cocoa bundle, body, bridge and toolkit; affected app/remote rebuilt, unchanged phone outputs retained with source closure.'}
    save('QUALIFIED-REUSE.json',reused)
    (PROGRAM/'macos-inputs.json').write_text(json.dumps({'format':'helene-macos-inputs.v1','version':VERSION,'source_commit':COMMIT,
        'native_reused':reused,'native_built':native,'core_origins':origins,'owner_ux_delta':True,
        'limits':'No physical TCC/mic/accounts/Intel/notarization or whole broad semantic-review acceptance.'},indent=2))
    normalize_output_modes(PROGRAM)
    files={p.relative_to(PROGRAM).as_posix():digest(p) for p in PROGRAM.rglob('*') if p.is_file()
        and p.relative_to(PROGRAM).parts[0] not in ('tree','components','data','downloads') and p.name not in ('helene.json','python-package-files.json')}
    (PROGRAM/'python-package-files.json').write_text(json.dumps({'format':'helene-python-program-files.v1','version':VERSION,'files':files},indent=2))
    archive=PACK/f'Helene-{VERSION}-macos-arm64.zip'
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PROGRAM.rglob('*')):
            if p.is_file():z.write(p,'Helene/'+p.relative_to(PROGRAM).as_posix())
    save('RUNTIME-ADMISSION.json',source_update_host.verified_runtime(PROGRAM))
    candidate={'format':'helene-macos-owner-ux-candidate.v1','version':VERSION,'source_commit':COMMIT,'sha256':digest(archive),
        'size':archive.stat().st_size,'archive':archive.name,'reused_archive_sha256':OLD_SHA,'qualification_pending':True}
    (PACK/'candidate.json').write_text(json.dumps(candidate,indent=2));save('candidate.json',candidate)
    (PACK/(archive.name+'.sha256')).write_text(candidate['sha256']+'  '+archive.name+'\n')
    from macos_archive_delta import create,restore,versioned
    patch=OUT/'owner-ux-exact-archive-delta.zip';create(OLD_ARCHIVE,archive,patch)
    restored=restore(OLD_ARCHIVE,patch,TEMP/'owner-ux-roundtrip.zip');save('DELTA-ROUNDTRIP.json',restored)
    save('MANUAL-ARCHIVE.json',versioned(archive,PACK/f'Helene-{VERSION}-macos-arm64-manual.zip',f'Helene-{VERSION}-macos-arm64'))
    with open(os.environ['GITHUB_OUTPUT'],'a') as output:output.write('sha256='+candidate['sha256']+'\n')
    print(json.dumps(candidate),flush=True)

def prepare_warning():
    """Only the missing rendered warning, using exact already sealed bytes."""
    base.subject_identity(materialize=True)
    from build_python_macos_rc import unpack
    from macos_archive_delta import restore
    outer=TEMP/'qualified-release-artifact.zip';assert digest(outer)==base.OUTER_SHA
    old18=TEMP/'qualified-rc18.zip'
    with zipfile.ZipFile(outer) as z:
        row=next(r for r in z.infolist() if Path(r.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip');old18.write_bytes(z.read(row))
    assert digest(old18)==base.BASE_SHA
    prior=TEMP/'current-rc23-evidence.zip';assert digest(prior)==OLD_OUTER
    patch=TEMP/'old23-exact-delta.zip'
    with zipfile.ZipFile(prior) as z:patch.write_bytes(z.read('exact-archive-delta.zip'))
    restore(old18,patch,OLD_ARCHIVE);assert digest(OLD_ARCHIVE)==OLD_SHA
    latest=TEMP/'owner-ux-sealed-evidence.zip'
    assert digest(latest)=='b8d08334516fbbf57fe2efbd2b3ca9fd9d59be7d6aa61cc1a86b0d54ec4f3f00'
    PACK.mkdir();patch=TEMP/'owner-ux-sealed-delta.zip'
    with zipfile.ZipFile(latest) as z:
        patch.write_bytes(z.read('owner-ux-exact-archive-delta.zip'))
        for name in ('candidate.json','NATIVE-OWNER-UX.json','NATIVE-FIRST-CREATE.json'):
            (OUT/('PRIOR-'+name)).write_bytes(z.read(name))
        candidate=json.loads(z.read('candidate.json'));assert candidate['source_commit']==COMMIT
        (PACK/'candidate.json').write_bytes(z.read('candidate.json'))
    archive=PACK/candidate['archive'];restore(OLD_ARCHIVE,patch,archive);assert digest(archive)==candidate['sha256']
    unpack(archive,TEMP/'owner-ux-warning-driver-install')
    save('SEALED-OWNER-UX-REUSED.json',{'source_commit':COMMIT,'archive_sha256':candidate['sha256'],
        'retained_build_first_create_and_exit_run':41371759119,'no_front_runtime_native_or_product_rebuild':True,
        'scope':'Only missing Programme-group warning visibility and normal exit. Original wrong-tab QA timeout stays RED.'})
    python=TEMP/'owner-ux-warning-driver-install/Helene/runtime/bin/python3'
    os.execv(str(python),[str(python),'-B',str(QA),'warning'])

def warning():return native(warning_only=True)

def native(*,warning_only=False):
    from build_python_macos_rc import unpack
    import rc18_macos_pipeline as helper
    import psutil
    archive=PACK/f'Helene-{VERSION}-macos-arm64.zip';assert digest(archive)==json.loads((PACK/'candidate.json').read_text())['sha256']
    unpack(archive,TEMP/'owner-ux-native-install');program=TEMP/'owner-ux-native-install/Helene'
    helper.EVIDENCE=OUT;helper.REPO=REPO
    home=TEMP/'owner-ux-native-home';home.mkdir()
    env=dict(os.environ,HOME=str(home),CFFIXED_USER_HOME=str(home),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    data=home/'Helene';python=program/'runtime/bin/python3';children=[];logs=[]
    result={'source_commit':COMMIT,'archive_sha256':digest(archive),'steps':{},'scope':'Actual installed bytes, custom form to SOUL to ordinary Cocoa plus pre-update warning. Prior unchanged mechanical lanes retained.'}
    def record():save('NATIVE-OWNER-UX.json',result)
    def launch(mode):
        log=(OUT/('native-'+mode+'.log')).open('wb');logs.append(log)
        p=subprocess.Popen([str(python),'-B',str(QA.with_name('rc23_macos_owner_ux_window.py')),mode,str(program),str(data),str(OUT)],cwd=program,env=env,stdout=log,stderr=subprocess.STDOUT)
        children.append(p);return p
    def close(p):
        descriptor=json.loads((data/'memory/.control/desktop-session.json').read_text())
        owner=psutil.Process(descriptor['pid']);tracked=[(q.pid,q.create_time()) for q in (owner,*owner.children(recursive=True))]
        helper.invoke(program,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
        p.wait(75);assert p.returncode==0
        helper.wait(lambda:not helper.control(program),75)
        def gone():
            for pid,birth in tracked:
                try:
                    q=psutil.Process(pid)
                    if q.create_time()==birth and q.status()!=psutil.STATUS_ZOMBIE:return False
                except psutil.NoSuchProcess:pass
            return True
        helper.wait(gone,75)
        result.setdefault('observed_exit_generations_absent',[]).append(len(tracked));record()
    try:
        if not warning_only:
            p=launch('create');helper.wait(lambda:(OUT/'NATIVE-FIRST-CREATE.json').is_file(),150)
            helper.wait(lambda:helper.invoke(program,'app_info').get('root')==str(program),150)
            helper.wait(lambda:helper.control(program) and helper.control(program).get('ui_ready'),150)
            created=json.loads((OUT/'NATIVE-FIRST-CREATE.json').read_text());assert created['actual_SOUL_exact'] and created['real_birth_authority_consumed']
            soul=(data/'soul/SOUL.md').read_bytes();identity=(data/'memory/.control/profile-identity.json').read_bytes()
            result['steps']['actual_custom_form_file_editable_text_birth_and_native_Cocoa_open']=created;record()
            time.sleep(2);helper.snapshot('owner-ux-ordinary-cocoa.png');close(p)
        p=launch('warning');helper.wait(lambda:(OUT/'NATIVE-UPDATE-WARNING.json').is_file(),150)
        warning=json.loads((OUT/'NATIVE-UPDATE-WARNING.json').read_text());assert warning['visible'] and not warning['update_writes_before_warning']
        if warning_only:soul=(data/'soul/SOUL.md').read_bytes();identity=(data/'memory/.control/profile-identity.json').read_bytes()
        result['steps']['actual_visible_warning_before_any_update_action']=warning;record();close(p)
        assert (data/'soul/SOUL.md').read_bytes()==soul and (data/'memory/.control/profile-identity.json').read_bytes()==identity
        result['steps']['SOUL_and_identity_exact_after_reopen']=True;result['passed']=True;record()
    except BaseException as e:
        result['error']=type(e).__name__+': '+str(e);record();helper.snapshot('owner-ux-failure.png');raise
    finally:
        if helper.control(program):
            try:helper.invoke(program,'python_window',{'action':'exit','relaunch':False,'owner_exit':False})
            except Exception:pass
        for p in children:
            try:p.wait(15)
            except subprocess.TimeoutExpired:p.terminate();p.wait(15)
        for log in logs:log.close()

if __name__=='__main__':globals()[sys.argv[1]]()
