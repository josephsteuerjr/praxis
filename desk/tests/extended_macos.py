"""Extended public native RC qualification; disposable runner/profile only."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import ast,hashlib,importlib.metadata,json,os,shutil,subprocess,sys,time,zipfile

REPO=Path(__file__).resolve().parents[2];TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
EVIDENCE=REPO/'evidence-extended';EVIDENCE.mkdir(exist_ok=True)
sys.path[:0]=[str(REPO/'desk'),str(REPO/'desk/installer')]
from build_python_macos_rc import unpack

def write(name,value):
    p=EVIDENCE/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,indent=2,ensure_ascii=True),encoding='utf-8')

def prepare():
    archive=TEMP/'rc18-qualified-input.zip'
    with archive.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='91d198b1b226ed2c630c57c6602cab963f45f7a678eee314bb06ae32c7dfcf58'
    with zipfile.ZipFile(archive) as z:
        i=next(i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip')
        candidate=TEMP/'base-candidate.zip';candidate.write_bytes(z.read(i))
    with candidate.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='79ce64094f84499c1225feeb498b5310131d9122dfa37f2766279d8d9de66255'
    unpack(candidate,TEMP/'base')
    program=TEMP/'base/Helene';python=program/'runtime/bin/python3'
    write('machine.json',{'os':subprocess.check_output(['sw_vers'],text=True),'arch':os.uname().machine,'cpus':os.cpu_count(),
        'base_sha256':'79ce64094f84499c1225feeb498b5310131d9122dfa37f2766279d8d9de66255','source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()})
    return program,python

def execute(command,log,*,cwd=REPO,env=None,timeout=300):
    p=EVIDENCE/log;p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('wb') as f:
        try:
            r=subprocess.run(list(map(str,command)),cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout,start_new_session=True)
            return r.returncode
        except subprocess.TimeoutExpired:return 124

def broad():
    program,python=prepare()
    from rc18_macos_pipeline import fronts
    fronts()
    # Test dependencies live in this one task's auxiliary directory. Never
    # install into the candidate, system Python or a publisher's runtime.
    deps=TEMP/'qa-deps'
    assert execute([python,'-m','pip','install','--disable-pip-version-check','--no-compile','--target',deps,'pytest==8.4.2','aiogram','soundfile'],
        'qa-dependencies.log',timeout=600)==0
    from localharness.source_verification_bundle import python_framework,executor_lock
    core=TEMP/'composed-core';core.mkdir()
    for folder in ('praxis','helene/core'):
        for p in (REPO/folder).rglob('*'):
            rel=p.relative_to(REPO/folder)
            if not p.is_file() or any(n in ('.git','__pycache__','node_modules','memory','soul','workspace','data') for n in rel.parts):continue
            q=core/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
    runner=REPO/'desk/localharness/source_verification_runner.py'
    lanes=[]
    for shard in range(os.cpu_count() or 3):lanes.append({'id':f'core-{shard}','root':core,'shard':shard,'count':os.cpu_count() or 3})
    for p in sorted((REPO/'desk/tests').glob('*.py')):
        if not p.name.startswith(('t_','test')):continue
        if p.name=='t_owner_stop_seed.py':lanes.append({'id':p.stem,'script':p});continue
        lanes.append({'id':p.stem,'root':REPO/'desk','source':p,'framework':python_framework(p)})
    for p in core.glob('test*.py'):
        if python_framework(p)=='pytest':lanes.append({'id':'pytest-'+p.stem,'root':core,'source':p,'framework':'pytest'})
    write('collection.json',[{k:str(v) for k,v in row.items()} for row in lanes])
    def run(row):
        identity=row['id'];home=TEMP/'broad-homes'/identity;home.mkdir(parents=True)
        # Several canonical suites create temp fixtures beside their source.
        # Each lane therefore owns its complete clean source copy INSIDE the
        # write fence. Sharing a read-only checkout was an invalid test setup.
        source=home/'source'
        shutil.copytree(REPO,source,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc','node_modules','evidence-*'))
        localcore=source/'live';shutil.copytree(core,localcore)
        localdesk=source/'desk'
        env=dict(os.environ,HOME=str(home),CFFIXED_USER_HOME=str(home),USERPROFILE=str(home),TMPDIR=str(home),TMP=str(home),TEMP=str(home),
            PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PRAXIS_TEST='1',HELENE_BODY_DIR=str(program),
            HELENE_TEST_SOURCE_ROOT=str(source),HELENE_TREE_SRC=str(localcore),PRAXIS_TEST_FORGE_PROCESS='1',
            PYTHONPATH=os.pathsep.join([str(localcore),str(localdesk),str(localdesk/'localharness')]))
        result=EVIDENCE/'reports'/(identity+'.json');result.parent.mkdir(exist_ok=True)
        if row.get('script'):command=[python,'-B',source/row['script'].relative_to(REPO)]
        else:
            root=localcore if row['root']==core else localdesk
            command=[python,'-I','-X','utf8','-S','-B',runner,'--root',root,'--report',result,'--sandbox',home,'--dependencies',deps]
            if row.get('source'):
                original=row['source'];relative=original.relative_to(core) if original.is_relative_to(core) else original.relative_to(REPO/'desk')
                command+=['--source',root/relative,'--framework',row['framework']]
            if 'shard' in row:command+=['--shard-index',row['shard'],'--shard-count',row['count']]
        code=execute(command,'logs/'+identity+'.log',cwd=localcore if identity.startswith(('core-','pytest-')) else localdesk,env=env,timeout=900 if identity.startswith('core-') else 240)
        report=json.loads(result.read_text()) if result.exists() else {}
        shutil.rmtree(source)
        return identity,{'exit':code,'report':str(result.relative_to(EVIDENCE)) if result.exists() else None,
            'tests':report.get('tests_run',0),'collected':len(report.get('collected',[])),'complete':report.get('complete'),
            'failures':[x for x in report.get('outcomes',[]) if x['status'] not in ('passed','skipped','expected-failure')],
            'skips':[x for x in report.get('outcomes',[]) if x['status']=='skipped'],'error':report.get('error')}
    results={}
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 3) as pool:
        futures=[pool.submit(run,lane) for lane in lanes]
        for f in as_completed(futures):
            name,row=f.result();results[name]=row;write('broad-progress.json',{'completed':len(results),'total':len(lanes),'results':results})
            print(json.dumps({'completed':len(results),'total':len(lanes),'lane':name,'exit':row['exit'],'tests':row['tests']}),flush=True)
    result={'complete':len(results)==len(lanes),'results':results,'tests':sum(r['tests'] for r in results.values()),
            'failed_lanes':[n for n,r in results.items() if r['exit']], 'zero_case_lanes':[n for n,r in results.items() if not r['tests']]}
    write('BROAD.json',result)
    # This discovery run records ALL failures for audit; it never narrows the
    # collection to turn a red complete sweep into a green product gate.
    assert not result['failed_lanes'],'Broad native sweep found failures; inspect BROAD.json'

if __name__=='__main__':{'broad':broad}[sys.argv[1]]()
