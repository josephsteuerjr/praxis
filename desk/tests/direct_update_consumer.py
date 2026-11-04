"""Disposable native direct-update consumer; no model/network/owner profile.

Pass --runtime pointing at an immutable released embedded Windows Python. Only
its small stdlib/interpreter/psutil closure is copied into the synthetic program.
The synthetic version-bound tests are tiny; this is not product full QA.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent),
             str(Path(__file__).resolve().parents[2]/'helene/core')]
from localharness import source_merge as merge,update_composition as comp,direct_update as direct
from localharness import source_update as pipeline,source_update_launcher as launcher
from localharness import source_verification_bundle as bundle
from localharness.package_notice import read,write


def put(root,name,content):
    path=Path(root)/name;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content,str) else content)
    return path


def setup(root,runtime,*,slow=False,clean=False):
    root=Path(root);new=root/'incoming/Helene';old=root/'old';old.mkdir(parents=True)
    source=Path(__file__).resolve().parents[1]
    shutil.copytree(source/'localharness',new/'app/localharness',ignore=shutil.ignore_patterns('__pycache__'))
    put(new,'app/feature.py','answer=1\n');put(old,'app/feature.py','answer=0\n')
    put(new,'app/localharness/fixture_value.py','answer=42\n')
    shutil.copytree(new/'app/localharness',old/'app/localharness')
    r=new/'runtime';r.mkdir()
    for file in Path(runtime).iterdir():
        if file.is_file() and file.suffix.lower() in ('.exe','.dll','.pyd','.zip'):
            shutil.copy2(file,r/file.name)
    for file in Path(runtime).glob('python*._pth'):
        put(r,file.name,'\n'.join([file.stem.replace('._pth','')+'.zip','.', 'Lib/site-packages','../app','../tree','import site','']))
    import psutil
    shutil.copytree(Path(psutil.__file__).parent,r/'Lib/site-packages/psutil',ignore=shutil.ignore_patterns('__pycache__'))
    # The exact released executable is used by the launcher AND test commands.
    write(new/'python-app.json',{'version':'9.0.0','delivery':'portable'})
    test='import unittest,os,time\nfrom pathlib import Path\nclass Tiny(unittest.TestCase):\n def test_candidate(self):\n  import feature\n  self.assertTrue(feature.AUTHORED in (10,20))\n'
    if slow:
        test='import unittest,subprocess,sys,time,os,json\nfrom pathlib import Path\nclass Tiny(unittest.TestCase):\n def test_stop(self):\n  child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(300)"])\n  (Path(os.environ["TEMP"])/"sleeping-child.json").write_text(json.dumps({"pid":child.pid}))\n  time.sleep(300)\n'
    base=root/'base';local=root/'core-0';peer=root/'core-1'
    for path,value in ((base,0),(local,10),(peer,20)):
        put(path,'feature.py',f'AUTHORED={value}\n')
        put(path,'praxis_test.py','# disposable hermetic bootstrap\n')
        put(path,'test_feature.py',test)
    shutil.copytree(base,new/'tree')
    put(new/'tree','upstream.py','NEW=True\n')
    core_source=Path(__file__).resolve().parents[2]/'helene/core'
    # This trusted incoming helper, not candidate code, owns all native children.
    shutil.copy2(core_source/'command_process.py',new/'tree/command_process.py')
    rows=[]
    for index,code in enumerate((local,peer)):
        data=root/f'data-{index}';put(data,'memory/sentinel.bin',b'KEEP\x00MEMORY')
        put(data,'settings.json','{"identity":"unchanged"}\n')
        configuration=old/('helene.json' if index==0 else 'agents/peer/helene.json')
        write(configuration,{'mode':'local','tree':str(data.resolve()),'code':str(code.resolve()),'setup_complete':False})
    from localharness import code_transition,authored_baseline
    shutil.copytree(base,old/'tree')
    write(old/'python-app.json',{'version':'8.0.0','delivery':'portable'})
    code_transition.seal(old);authored_baseline.seal(old)
    code_transition.seal(new);authored_baseline.seal(new)
    # Real authored app L differs from shipped A; shared M will resolve it once.
    put(old,'app/feature.py','answer=7\n')
    app=comp.app_inventory(new/'app')
    write(new/'source-inputs.json',{'files':{n:r['sha256'] for n,r in app.items()}})
    package={p.relative_to(new).as_posix():bundle.digest(p) for p in new.rglob('*') if p.is_file()}
    write(new/'python-package-files.json',{'files':package})
    # Build only protocol fixtures, not the project's hundreds of test modules.
    repo=root/'tiny-suite'
    put(repo,'praxis/praxis_test.py','# disposable bootstrap\n')
    put(repo,'praxis/test_feature.py',test)
    put(repo,'helene/core/feature.py','AUTHORED=0\n')
    put(repo,'desk/tests/t_tiny.py','import unittest\nclass Tiny(unittest.TestCase):\n def test_app(self):\n  from localharness import fixture_value\n  self.assertEqual(fixture_value.answer,42)\n')
    put(repo,'desk/app/test/tiny.mjs','import assert from "node:assert/strict";assert.equal(1,1);\n')
    lock=bundle.executor_lock(packages=[]);lock['tools']['python']={'sha256':bundle.digest(r/'python.exe')}
    verification=bundle.build(repo,new/'verification','9.0.0',{sys.platform:lock})
    package['verification/manifest.json']=verification['sha256'];write(new/'python-package-files.json',{'files':package})
    binding={'kind':'incoming-update-runtime.v1','program':str(new.resolve()),'test_battery':1,
        'verification_bundle_sha256':verification['sha256']}
    from localharness import update_source
    if clean:
        update_source.select(old,new,'clean',by='owner-words')
        return old,new,None,None,None
    update_source.select(old,new,'preserve',by='owner-words',require_review=True,resolution_runtime=new)
    rows=code_transition.preflight(old,new)['plans']
    directory=Path(rows[0]['directory']);plan=merge.validate_inputs(directory)
    cache=root/'npm-cache';cache.mkdir()
    operation={'id':directory.name,'directory':str(directory),'program':str(old.resolve()),
        'tree':rows[0]['tree'],'source':rows[0]['source'],'input_sha256':merge.input_signature(plan),
        'candidate_sha256':merge.candidate_signature(directory),'version':'9.0.0',
        'state':'waiting_agent','resolution_owner':'agent','continuation':pipeline.AUTOMATIC,
        'attempts':[],'coordinator':binding,'verification_bundle':verification,
        'verification_executor':{'python':str(r/'python.exe'),'npm_config_cache':str(cache),'parallel_lanes':2}}
    write(directory/'pipeline.json',operation);write(pipeline.path(operation['tree']),operation)
    for row in rows:comp.prepare(row['directory'],direct.operation(row['directory'],operation))
    comp.repair(directory,'app/feature.py','answer=42\n',expected=comp.signature(directory),reason='Resolve shared app once')
    from localharness.agent_source_update import command
    command(operation['tree'],'finish')
    operation=read(directory/'pipeline.json')
    assert operation['state']=='verification_requested',operation
    return old,new,directory,operation,rows


class DeterministicForge:
    """Actual child results through Service.tick; transport proof, NOT model QA."""
    SELF_REVIEW_API=1
    def __init__(self,root):self.root=Path(root)
    def _unit_dir(self,task,kind,unit):return self.root/task/kind/unit
    def _unit_state(self,directory):return read(Path(directory)/'state.json')
    def self_review_task(self,task,snapshot,sha,worker,parallel):
        self.snapshot,self.sha,self.worker=snapshot,sha,worker
    def agent(self,task,action,agent_id,**kwargs):
        import subprocess
        directory=self._unit_dir(task,'agents',agent_id);directory.mkdir(parents=True)
        write(directory/'request.json',{'brief':kwargs['brief']})
        script=directory/'deterministic_critic.py'
        script.write_text("from pathlib import Path\nimport sys,json\nsys.path.insert(0,"+repr(str(Path(__file__).resolve().parents[1]))+")\nfrom localharness.self_review import validate_snapshot\nfrom localharness.package_notice import write\nvalidate_snapshot("+repr(self.snapshot)+","+repr(self.sha)+")\n"+
            "write(Path(__file__).with_name('result.json'),{'status':'done','complete':True,'snapshot_verified':True,'subject_sha256':"+repr(self.sha)+
            ",'result':json.dumps({'verdict':'clear','subject_sha256':"+repr(self.sha)+",'summary':'Deterministic transport fixture, not model judgment','findings':[],'gaps':[]})})\nwrite(Path(__file__).with_name('state.json'),{'status':'done'})\n",encoding='utf8')
        subprocess.run([sys.executable,'-B',str(script)],check=True,timeout=30)
        return 'fixture subprocess completed'


def execute(root,runtime):
    old,new,directory,operation,rows=setup(root,runtime)
    before={r['tree']:{n:(Path(r['tree'])/n).read_bytes() for n in ('memory/sentinel.bin','settings.json')} for r in rows}
    # Import trusted helper from the exact incoming synthetic core.
    sys.path.insert(0,str(new/'tree'))
    assert launcher.tick(operation,directory,lambda:False) is None
    identity=dict(operation['test_launcher']);write(directory/'pipeline.json',operation)
    deadline=time.monotonic()+180
    while True:
        sources=launcher.tick(operation,directory,lambda:False)
        if sources is not None:break
        if time.monotonic()>deadline:
            launcher.cancel(operation,directory)
            raise TimeoutError('Synthetic native verification did not settle')
        time.sleep(.1)
    verified=launcher.validate(operation,directory)
    assert verified['cores']==2 and len(verified['executions'])==2,verified
    assert verified['execution_pass'],verified
    assert read(Path(identity['root'])/'process.json')['invocation']==identity['invocation']
    # Rejoin/collect, not another process after the initiating observer ends.
    launcher.collect(operation,directory)
    assert read(Path(identity['root'])/'process.json')['pid']==identity['pid']
    # Exercise the real frozen subject, worker results, aggregation and exact
    # apply gate. Scripted critics prove wiring only; configured Sol/Astra review
    # of the product changes is a separate required result of the author task.
    from localharness import self_review
    operation.update(resolution_owner='agent',continuation=pipeline.AUTOMATIC)
    service=self_review.Service(operation['tree'],operation['source'],old,
        DeterministicForge(Path(root)/'critic-units'),coordinator=operation['coordinator'])
    pipeline._review_request(operation,directory,service)
    write(directory/'pipeline.json',operation)
    service.tick(review_ids={operation['review_id']})
    record=service.status(operation['review_id'])
    pipeline._clear(service,record,operation['subject_sha256'])
    operation.update(state='ready_to_apply',review_clear=True)
    write(directory/'pipeline.json',operation)
    pipeline.validate_ready(directory)
    # Real mechanical programme apply/rollback through its canonical overlay.
    from localharness import update_overlay
    from localharness.python_source_updater import rollback
    from localharness import python_source_updater as updater,app_migration
    original_config=(old/'helene.json').read_bytes()
    prepared=updater.prepare_install(old,new)
    assert prepared['note']['copied_data']==[]
    assert prepared['note']['backup_bytes']==0 and prepared['note']['backup']==''
    note,recovery=updater.install(old,new)
    assert (old/'app/feature.py').read_text()=='answer=42\n'
    installed=app_migration.discover(old)['agents']
    assert len(installed)==2,installed
    assert {Path(r['code'],'feature.py').read_text() for r in installed}=={'AUTHORED=10\n','AUTHORED=20\n'}
    rollback(old,recovery);updater.active_update(old).unlink(missing_ok=True)
    assert (old/'app/feature.py').read_text()=='answer=7\n'
    assert (old/'helene.json').read_bytes()==original_config
    for data,files in before.items():
        for name,raw in files.items():assert (Path(data)/name).read_bytes()==raw
    # Secondary byte drift makes the ONE transition report stale.
    put(Path(rows[1]['directory'])/'result','feature.py','AUTHORED=21\n')
    try:launcher.validate(operation,directory)
    except (ValueError,FileNotFoundError):pass
    else:raise AssertionError('Secondary drift reused whole-transition tests')
    return {'launcher':identity,'verification':verified,'data_unchanged':True,'overlay_rollback':True,
        'secondary_drift_refused':True,'review_transport':'deterministic independent child + real canonical gate','root':str(root)}


def clean_consumer(root,runtime):
    old,new,*_=setup(root,runtime,clean=True)
    from localharness import python_source_updater as updater,app_migration
    profiles=app_migration.discover(old)['agents']
    before={r['tree']:{n:(Path(r['tree'])/n).read_bytes() for n in ('memory/sentinel.bin','settings.json')} for r in profiles}
    cfg=(old/'helene.json').read_bytes()
    prepared=updater.prepare_install(old,new)
    assert not prepared['note']['copied_data'] and prepared['note']['backup_bytes']==0 and prepared['note']['backup']==''
    note,recovery=updater.install(old,new)
    assert (old/'app/feature.py').read_text()=='answer=1\n'
    assert {Path(r['code'],'feature.py').read_text() for r in app_migration.discover(old)['agents']}=={'AUTHORED=0\n'}
    updater.rollback(old,recovery);updater.active_update(old).unlink(missing_ok=True)
    assert (old/'helene.json').read_bytes()==cfg and (old/'app/feature.py').read_text()=='answer=7\n'
    for tree,files in before.items():
        for name,raw in files.items():assert (Path(tree)/name).read_bytes()==raw
    return {'clean_apply_rollback':True,'same_data':True,'mandatory_backup':False}


def controls(root,runtime):
    import psutil
    results={}
    for mode in ('stop','crash'):
        base=Path(root)/mode
        old,new,directory,operation,rows=setup(base,runtime,slow=True)
        sys.path.insert(0,str(new/'tree'))
        launcher.tick(operation,directory,lambda:False)
        identity=operation['test_launcher'];write(directory/'pipeline.json',operation)
        deadline=time.monotonic()+120;child=None
        while time.monotonic()<deadline:
            for attempt in directory.glob('test-battery/*/attempt.json'):
                work=Path(read(attempt)['work'])
                for marker in work.glob('slot-*/tmp/sleeping-child.json'):
                    pid=read(marker)['pid'];child={'pid':pid,'birth':psutil.Process(pid).create_time()}
            if child:break
            time.sleep(.1)
        if not child:raise AssertionError('Native grandchild did not reach its owned command')
        if mode=='stop':launcher.cancel(operation,directory)
        else:
            process=launcher._PROCESSES[str(Path(identity['root']))]
            confirmed,why=process.stop_tree()
            assert confirmed,why
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            try:
                terminal=launcher._observe(Path(identity['root']))[1]
                if terminal is not None:break
            except RuntimeError:
                if mode=='crash':break
                raise
            time.sleep(.1)
        else:raise AssertionError('Native test owner did not settle')
        try:
            actual=psutil.Process(child['pid'])
            assert actual.create_time()!=child['birth'],'Native sleeping grandchild survived'
        except psutil.NoSuchProcess:pass
        # A cancelled report or an owner killed without one cannot be replayed.
        try:launcher.tick(operation,directory,lambda:mode=='stop')
        except (ValueError,RuntimeError,InterruptedError):pass
        else:raise AssertionError('Stopped/crashed verification restarted')
        assert read(Path(identity['root'])/'process.json')['invocation']==identity['invocation']
        results[mode]={'grandchild':child,'descendants_settled':True,'no_replay':True,'launcher':identity}
    return results


def evidence(root,output):
    import zipfile
    archive=output.with_suffix('.evidence.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for here,dirs,names in os.walk(root):
            dirs[:]=[n for n in dirs if n not in ('runtime','repository.git','__pycache__')]
            for name in names:
                path=Path(here)/name
                z.write(path,path.relative_to(root).as_posix())
    return {'path':str(archive),'sha256':bundle.digest(archive),'bytes':archive.stat().st_size}


def remove_fixture(root):
    import stat
    def readonly(function,name,failure):
        path=Path(name);info=path.lstat()
        if not path.resolve().is_relative_to(root) or path.is_symlink() or info.st_nlink!=1:raise failure[1]
        path.chmod(info.st_mode|stat.S_IWRITE);function(name)
    shutil.rmtree(root,onerror=readonly)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--controls',action='store_true');p.add_argument('--clean',action='store_true');a=p.parse_args()
    root=Path(tempfile.mkdtemp(prefix='helene-direct-native-'))
    try:
        result=({'controls':controls(root,a.runtime)} if a.controls else clean_consumer(root,a.runtime) if a.clean else execute(root,a.runtime))
        result['evidence']=evidence(root,a.output)
        result['status']='passed';write(a.output,result)
        remove_fixture(root)
    except BaseException as exc:
        write(a.output,{'status':'failed','root':str(root),'error':f'{type(exc).__name__}: {exc}',
            'evidence':evidence(root,a.output)})
        raise
    print('DIRECT_NATIVE_PASS',flush=True)


if __name__=='__main__':main()
