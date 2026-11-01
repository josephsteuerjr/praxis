"""Native final package, source admission and real offline executor checks."""
from pathlib import Path
import json,os,shutil,subprocess,sys
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
    program,python=common.prepare()
    shutil.copytree(program/'runtime',RUNTIME,symlinks=False,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    python=RUNTIME/'bin/python3'
    command([python,'-m','pip','install','--disable-pip-version-check','--no-compile','pytest==8.4.2','aiogram','soundfile','cryptography>=43,<49'],'runtime-dependencies.log',timeout=900)
    os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),'build'])

def build():
    from rc18_macos_pipeline import fronts
    from localharness import source_verification_bundle as bundle,source_verification_executor as executor,source_update_host
    from build_python_macos_rc import build as package
    fronts()
    command([sys.executable,'-m','pip','check'],'pip-check.log',timeout=120)
    # Other72 cases passed and3 exact Windows cases skipped on source0a388b1/run41295075384;
    # Only native custody cases affected by the bounded exit observation repeat.
    command([sys.executable,'-B','-m','unittest','tests.t_source_update_battery_2810.Battery.test_output_limit_has_no_passing_coverage','test_command_process_2810.CommandCustody','-v'],
        'verification-contracts.log',cwd=REPO/'desk',timeout=600)
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
        verification=verification,verification_tools=TOOLS,runtime=RUNTIME)
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
    native.native()

if __name__=='__main__':{'prepare':prepare,'build':build}[sys.argv[1]]()
