"""Native final package, source admission and real offline executor checks."""
from pathlib import Path
import json,os,shutil,subprocess,sys,zipfile
REPO=Path(__file__).resolve().parents[2];TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
sys.path[:0]=[str(REPO/'desk'),str(REPO/'desk/installer'),str(Path(__file__).parent)]
import extended_macos as common
OUT=REPO/'evidence-final';OUT.mkdir(exist_ok=True)
RUNTIME=TEMP/'final-runtime'
TOOLS=TEMP/'rc18-verification-tools'

def command(argv,name,**kwargs):
    with (OUT/name).open('wb') as output:
        subprocess.run(list(map(str,argv)),stdout=output,stderr=subprocess.STDOUT,check=True,start_new_session=True,**kwargs)

def prepare():
    # Reuse the exact qualified runtime, including its pinned dependency delta;
    # no repeat pip resolver or native runtime download for a packaging repair.
    from macos_distribution_qualification import digest
    from build_python_macos_rc import unpack
    outer=TEMP/'qualified-release-artifact.zip'
    assert digest(outer)=='ed89bd6a5d081f6f4f440e79c3f6b393534e255bbfbbec75e134c90249586b8a'
    archive=TEMP/'runtime-parent.zip'
    with zipfile.ZipFile(outer) as z:
        item=next(i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip')
        archive.write_bytes(z.read(item))
    assert digest(archive)=='43115b145684a98554d3adcbb129580ab9fd783502ac183dbc412576b3a67d88'
    unpack(archive,TEMP/'runtime-parent');program=TEMP/'runtime-parent/Helene'
    shutil.copytree(program/'runtime',RUNTIME,symlinks=False,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    python=RUNTIME/'bin/python3'
    from runtime_scripts import make_portable
    receipt=make_portable(RUNTIME)
    (TEMP/'runtime-script-portability.json').write_text(json.dumps(receipt,indent=2))
    os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),'build'])

def build():
    from rc18_macos_pipeline import fronts
    from localharness import source_verification_bundle as bundle,source_verification_executor as executor,source_update_host
    from build_python_macos_rc import build as package,digest as digest_native
    fronts()
    organs=TEMP/'native-organs'
    assert digest_native(TEMP/'native-organs.zip')=='3338c7d903bf6595ee5d1e9a7227101e63e9a0657860c296ec490f430aff2cec'
    from build_python_macos_rc import unpack
    unpack(TEMP/'native-organs.zip',organs)
    command([sys.executable,'-m','pip','check'],'pip-check.log',timeout=120)
    shared_delta=os.environ.get('FINAL_SHARED_DELTA')=='1'
    cases=(['tests.t_update_runtime_status_0111','tests.t_source_update_hand_3010','tests.t_update_activity_3010',
        'tests.t_python_app.PythonApplicationTests.test_pending_mac_profile_migration_reaches_recovery_before_first_start',
        'tests.t_app_source_update_3110'] if shared_delta else ['tests.t_component_jobs_0111','tests.t_voice_download_paths_0111',
        'tests.t_observer_exit_0111','tests.t_operation_diagnostics_0111','tests.t_python_app','tests.t_source_work_event_0111'])
    command([sys.executable,'-B','-m','unittest',*cases,'-v'],
        'shared-regressions.log',cwd=REPO/'desk',timeout=600)
    node_env=dict(os.environ,HELENE_TEST_PYTHON=sys.executable,PATH=str(TOOLS/'bin')+os.pathsep+os.environ.get('PATH',''))
    node_cases=(('work-activity-3010','update-monitor-0111','panel-density-2910','panel-widths') if shared_delta else
        ('contract','pending-voice','speech-status-3010','voice-refresh','voice-session-210','voice-shortcut','session','tray','settings-firstrun'))
    command([TOOLS/'bin/node','--test',*[REPO/'desk/app/test'/(n+'.test.mjs') for n in node_cases]],
        'shared-node-regressions.log',cwd=REPO/'desk/app',env=node_env,timeout=300)
    # Prior contract/custody gates passed; only updater callsites changed now.
    if not shared_delta:
        command([sys.executable,'-B','-m','unittest','tests.t_python_native_update_2410','-v'],
            'verification-contracts.log',cwd=REPO/'desk',timeout=600)
    env=dict(os.environ,PATH='/usr/bin:/bin:/usr/sbin:/sbin',PYTHONNOUSERSITE='1')
    for name,args in (('pytest',['--version']),('trafilatura',['--version']),('bottle',['--version']),('pip',['--version'])):
        command([RUNTIME/'bin'/name,*args],'portable-console-'+name+'.log',cwd=TEMP,env=env,timeout=60)
    # Contract tests above include native output-limit custody diagnostics.
    # Materialize only public, immutable cache/tool inputs. The npm cache in
    # each operation will be a separate owned mutable copy.
    for p in TOOLS.rglob('*'):
        if p.is_symlink():raise ValueError('Linked cached executor input')
        p.chmod(p.stat().st_mode & 0o755)
    lock=bundle.executor_lock(tools={'node':str(TOOLS/'bin/node'),'npm':str(TOOLS/'bin/npm')})
    lock['tools'].pop('cargo',None)  # Native organs unchanged; no compiler claim.
    verification=TEMP/'final-verification'
    result=bundle.build(REPO,verification,'2.3.0-rc.18',{'darwin':lock})
    manifest=json.loads((verification/'manifest.json').read_text())
    manifest['bundled_executors']={'darwin':executor.describe(TOOLS)}
    (verification/'manifest.json').write_bytes(bundle.encoded(manifest))
    bundle_sha=bundle.digest(verification/'manifest.json')
    bundle.load(verification,bundle_sha,'2.3.0-rc.18')
    program=package(TEMP/'rc18-evidence.zip',TEMP/'final-build-work',TEMP/'final-output',
        verification=verification,verification_tools=TOOLS,runtime=RUNTIME,runtime_scripts=TEMP/'runtime-script-portability.json',native_organs=organs)
    binding=source_update_host.verified_runtime(program)
    operation=TEMP/'native-executor-probe';operation.mkdir()
    selected=executor.provision(program,manifest,operation)
    # The exact selected npm must install all locked projects with networking
    # disabled by npm itself. This uses no user/system/global package cache.
    source=operation/'source'
    shutil.copytree(REPO,source,ignore=shutil.ignore_patterns('.git','node_modules','__pycache__','*.pyc','evidence-*'))
    env=dict(os.environ,PATH=str(Path(selected['node']).parent)+os.pathsep+os.environ.get('PATH',''),
        npm_config_offline='true',npm_config_userconfig=str(operation/'empty.rc'),npm_config_globalconfig=str(operation/'global.rc'))
    for name in ('app','mobile','miniapp','remote'):
        command([selected['npm'],'ci','--ignore-scripts','--offline','--cache',selected['npm_config_cache'],'--audit=false','--fund=false'],
            'offline-'+name+'.log',cwd=source/'desk'/name,env=env,timeout=300)
    shutil.copy2(TEMP/'final-output/candidate.json',OUT/'candidate.json')
    shutil.copy2(verification/'manifest.json',OUT/'verification-manifest.json')
    (OUT/'ADMISSION.json').write_text(json.dumps({'runtime':binding,'executor':{k:str(v) for k,v in selected.items()},
        'offline_all4_npm_projects':True,'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()},indent=2))
    # Keep a native handoff/GUI/lifecycle smoke on the actual new archive after
    # its final bytes were sealed; further update/matrix jobs reuse this SHA.
    import rc18_macos_pipeline as native
    native.PROGRAM=program
    if shared_delta:
        from macos_final_delta_smoke import smoke
        smoke(program)
    else:native.native()

if __name__=='__main__':{'prepare':prepare,'build':build}[sys.argv[1]]()
