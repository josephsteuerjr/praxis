"""Admit one extracted native package and run changed contracts in an owned profile."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time


def workers():
    count=os.cpu_count() or 1
    try:
        quota,period=Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota!='max':
            count=min(count,max(1,int(quota)//int(period)))
    except (OSError,ValueError):
        pass
    return count


def copy_bound(source,target,rows):
    """Copy verified package/source inputs into the normal isolated work root."""
    target.mkdir(parents=True)
    result={}
    for name,row in sorted(rows.items()):
        original=(source/name).resolve();destination=target/name
        assert original.is_relative_to(source.resolve()) and original.is_file() and not (source/name).is_symlink()
        assert destination.resolve().is_relative_to(target.resolve())
        raw=original.read_bytes()
        assert hashlib.sha256(raw).hexdigest()==row['sha256'],name
        mode=int(row['mode'][-3:],8) if isinstance(row['mode'],str) else row['mode']
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(raw);destination.chmod(mode)
        assert hashlib.sha256(destination.read_bytes()).hexdigest()==row['sha256']
        assert destination.stat().st_mode&0o777==mode
        result[name]={'sha256':row['sha256'],'mode':mode,'size':len(raw)}
    return result


def provision_native_dependencies(program,metadata,verification,work):
    """Exercise the packaged update coordinator's ordinary automatic provision."""
    declared=verification.get('dependencies',{}).get(sys.platform)
    if not declared:
        return None,None
    import importlib.metadata
    import site
    from localharness import source_update,source_verification_bundle as bundle
    operation={'version':metadata['version'],
        'coordinator':{'program':str(program),
                       'verification_bundle_sha256':metadata['verification_bundle_sha256']},
        'verification_executor':{'python':sys.executable}}
    before=bundle.digest(sys.executable)
    source_update._verification_dependencies(operation,work,lambda:False)
    executor=operation['verification_executor']
    target=Path(executor['python_dependencies']).resolve()
    receipt=Path(executor['dependency_receipt']).resolve()
    assert target.is_relative_to(work) and receipt.is_relative_to(work)
    assert bundle.digest(receipt)==executor['dependency_receipt_sha256']
    evidence=json.loads(receipt.read_bytes())
    assert evidence['result']['status']=='passed'
    assert evidence['bundle_sha256']==metadata['verification_bundle_sha256']
    assert evidence['python_sha256']==before==bundle.digest(sys.executable)
    assert evidence['files']==bundle.inventory(target)
    log=target.with_name(target.name+'.install.log')
    assert bundle.digest(log)==evidence['log_sha256']
    runtime_paths=site.getsitepackages([sys.prefix])
    runtime={d.metadata['Name'].lower().replace('_','-'):d.version
             for d in importlib.metadata.distributions(path=runtime_paths) if d.metadata['Name']}
    installed={d.metadata['Name'].lower().replace('_','-'):d.version
               for d in importlib.metadata.distributions(path=[str(target)]) if d.metadata['Name']}
    expected=verification['executor_locks'][sys.platform]['python_packages']
    effective={**runtime,**installed}
    assert all(effective.get(name)==version for name,version in expected.items())
    assert installed=={name.lower().replace('_','-'):row['version'] for name,row in declared['packages'].items()}
    proof={'normal_autoprovision':True,
        'entrypoint':'localharness.source_update._verification_dependencies',
        'manifest_sha256':metadata['verification_bundle_sha256'],'platform':sys.platform,
        'declared':declared,'python_sha256':before,'python_version':sys.version,
        'dependency_target':str(target.relative_to(work)),
        'receipt':str(receipt.relative_to(work)),'receipt_sha256':bundle.digest(receipt),
        'install_log':str(log.relative_to(work)),'install_log_sha256':bundle.digest(log),
        'result':evidence['result'],'runtime_distribution_versions':runtime,
        'target_distribution_versions':installed,'locked_distribution_versions':expected,
        'effective_distribution_versions':{name:effective[name] for name in expected},
        'offline':True,'global_install':False,'executor':executor}
    (work/'verification-dependencies.json').write_text(json.dumps(proof,indent=2),encoding='utf8')
    return target,proof


def main():
    parser=argparse.ArgumentParser()
    for name in ('program','source','equipment','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--prior-regressions',type=Path)
    parser.add_argument('--prior-regressions-proof',type=Path)
    parser.add_argument('--prior-native-evidence',type=Path)
    parser.add_argument('--prior-native-proof',type=Path)
    parser.add_argument('--version',required=True)
    args=parser.parse_args()
    program=args.program.resolve();source=args.source.resolve();out=args.out.resolve()
    assert not out.exists() and not out.is_relative_to(program)
    out.mkdir(parents=True)
    sys.path[:0]=[str(program/'app'),str(program/'tree'),str(source/'desk/installer')]
    from localharness.source_update_host import verified_runtime
    from localharness.source_verification_bundle import load
    metadata=json.loads((program/'python-app.json').read_text(encoding='utf8'))
    assert metadata['version']==args.version and metadata['local_candidate'] is False
    verified_runtime(program)
    verification=load(program/'verification',metadata['verification_bundle_sha256'],args.version)
    frozen=json.loads((args.equipment/'SOURCE-FROZEN.json').read_bytes())
    # Some real source consumers create owned Git/IPC fixtures beside the test
    # module. Keep the immutable /source mount intact and run them from an exact
    # hash-bound disposable copy, as the product's source battery does.
    regression_inputs=dict(frozen['files'])
    for receipt in ('FRONTENDS.json','EXTRA-FRONTENDS.json'):
        regression_inputs.update(json.loads((args.equipment/receipt).read_bytes())['outputs'])
    regression_source=out/'regression-source'
    copy_bound(source,regression_source,regression_inputs)
    source=regression_source
    from localharness import standalone_relay
    binary=standalone_relay.executable(program)
    if binary.is_file():
        assert binary.parent==program/'relay-product'
        relay=json.loads((program/'relay-product/BUILD-PASSPORT.json').read_text(encoding='utf8'))
        assert hashlib.sha256(binary.read_bytes()).hexdigest()==relay['binary']['sha256']
        assert relay['version'] in subprocess.check_output([str(binary),'--version'],text=True)
    else:
        # Fresh Intel packages follow the product's separately installed relay
        # contract; older qualified donors retain an already installed relay.
        from localharness import python_platform
        from unittest.mock import patch
        assert python_platform.target(program)=='macos-x86_64'
        assert metadata['native_inputs']['relay'] is False
        pin=standalone_relay.PIN['platforms'][python_platform.runtime_target()]
        fixture=args.equipment/'relay-component.tar.gz'
        assert fixture.stat().st_size==pin['size']
        assert hashlib.sha256(fixture.read_bytes()).hexdigest()==pin['sha256']
        optional=out/'optional-relay-program';optional.mkdir()
        calls=[]
        def local_download(url,target,expected,progress=None):
            assert url==pin['url'] and expected==pin['sha256']
            shutil.copyfile(fixture,target);calls.append(url)
            return {'bytes':fixture.stat().st_size}
        with patch('localharness.python_updates._download',side_effect=local_download):
            installed=standalone_relay.install(optional)
        assert installed['installed'] and calls==[pin['url']]
        binary=standalone_relay.executable(optional)
        assert standalone_relay.PIN['version'] in subprocess.check_output([str(binary),'--version'],text=True)
        assert not standalone_relay.executable(program).exists()
        relay={'version':standalone_relay.PIN['version'],'optional_component':True,
            'archive_sha256':pin['sha256'],'native_installer_executed':True,
            'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'network_download_substituted_with_exact_existing_local_release':True}
    from qualify_relay_device import qualify as check_relay_device
    # 2.8.5: desktop packages bundle the 2.8 relay (8ba23b8, quota failover) built on the pinned 1.2.0 base.
    pin=standalone_relay.PIN
    assert relay['version']==pin['version'] and (relay.get('source_commit')==pin['source_commit'] or (
        relay.get('candidate') is True and relay.get('base_commit')==pin['source_commit']
        and relay.get('source_commit')=='8ba23b89fcbcfe8a3d0decd18ed457bc7cc7c1cd'))
    check_relay_device(binary,out/'relay-device')
    from portable_sources import compose_core
    edition=out/'edition';edition.mkdir()
    for name,entry in compose_core(source)[0].items():
        path=edition/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(entry.data);path.chmod(int(entry.mode[-3:],8))
    home=out/'test-home';home.mkdir();temporary=home/'tmp';temporary.mkdir()
    env=dict(os.environ,HOME=str(home.resolve()),USERPROFILE=str(home.resolve()),
        TMPDIR=str(temporary.resolve()),TEMP=str(temporary.resolve()),TMP=str(temporary.resolve()),
        PYTHONPATH=os.pathsep.join(map(str,[source,source/'desk',source/'desk/localharness',source/'desk/tests',edition])),
        HELENE_TREE_SRC=str(edition),PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',
        NO_PROXY='127.0.0.1,localhost,::1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',
        PATH=os.pathsep.join([str(program/'runtime/bin'),str(program/'verification-tools/bin'),os.environ.get('PATH','')]))
    selection=json.loads((args.equipment/'CASESET.json').read_text(encoding='utf8'))
    names=selection['macos' if sys.platform=='darwin' else 'linux']
    assert names and len(set(names))==len(names)
    handoff=json.loads((args.equipment/'HANDOFF-TESTS.json').read_bytes())
    assert handoff['source_commit']==frozen['commit'] and handoff['source_tree_sha256']==frozen['source_tree_sha256']
    assert handoff['modules'] and set(handoff['modules'])<=set(names)
    for name,row in handoff['files'].items():
        assert hashlib.sha256((source/name).read_bytes()).hexdigest()==row['sha256']
    from portable_sources import application
    packaged_origins={origin:name for name,origin in application(source)[1].items()}
    application_bindings={}
    for name,row in json.loads((args.equipment/'HANDOFF-APIS.json').read_bytes()).items():
        assert name.startswith('desk/') and not name.startswith('desk/tests/') and '..' not in Path(name).parts
        packaged=program/packaged_origins[name]
        assert packaged.is_file() and hashlib.sha256(packaged.read_bytes()).hexdigest()==row['sha256']
        assert hashlib.sha256((source/name).read_bytes()).hexdigest()==row['sha256']
        application_bindings[name]={'packaged_path':str(packaged),'source_path':str(source/name),
                                    'sha256':row['sha256'],'packaged_and_executed_source_equal':True}
    for name in names:
        assert re.fullmatch(r't_[a-z0-9_]+',name) and (source/'desk/tests'/(name+'.py')).is_file()
    def one(name):
        started=time.monotonic()
        with (out/(name+'.log')).open('wb') as log:
            result=subprocess.run([sys.executable,'-B',str(source/'desk/tests'/(name+'.py'))],
                cwd=source/'desk',env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1200,start_new_session=True)
        text=(out/(name+'.log')).read_text(encoding='utf8',errors='replace')
        return {'module':name,'exit':result.returncode,'seconds':round(time.monotonic()-started,2),
            'cases':re.findall(r'Ran (\d+) tests?',text)}
    rows=[];reuse=None
    assert bool(args.prior_regressions)==bool(args.prior_regressions_proof)
    if args.prior_regressions:
        reuse=json.loads(args.prior_regressions_proof.read_bytes())
        assert reuse['platform']==sys.platform and reuse['source_commit']==frozen['commit']
        assert reuse['source_tree_sha256']==frozen['source_tree_sha256']
        assert hashlib.sha256(args.prior_regressions.read_bytes()).hexdigest()==reuse['regressions_sha256']
        rows=json.loads(args.prior_regressions.read_bytes())
        assert Counter(names)==Counter(row['module'] for row in rows)
        (out/'regressions.json').write_text(json.dumps(rows,indent=2),encoding='utf8')
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            for future in as_completed([pool.submit(one,name) for name in names]):
                rows.append(future.result());(out/'regressions.json').write_text(json.dumps(rows,indent=2),encoding='utf8')
    assert not any(row['exit'] for row in rows),'Read native regression logs'
    core_names=selection['native_core']['macos' if sys.platform=='darwin' else 'linux']
    assert core_names and len(core_names)==len(set(core_names))
    assert all(re.fullmatch(r'test_[a-z0-9_]+',name) and (program/'tree'/(name+'.py')).is_file()
               for name in core_names)
    core_work=out/'core-work';core_work.mkdir()
    (core_work/'home').mkdir()
    dependencies,dependency_proof=provision_native_dependencies(program,metadata,verification,core_work)
    source_rows=dict(frozen['files'])
    source_rows.update(json.loads((args.equipment/'FRONTENDS.json').read_bytes())['outputs'])
    extra=args.equipment/'EXTRA-FRONTENDS.json'
    if extra.is_file():source_rows.update(json.loads(extra.read_bytes())['outputs'])
    package_rows={}
    for path in sorted((program/'tree').rglob('*')):
        assert not path.is_symlink()
        if path.is_file():package_rows[path.relative_to(program/'tree').as_posix()]={
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mode':path.stat().st_mode&0o777}
    core_tree=core_work/'tree';core_source=core_work/'source'
    copied={'packaged_tree':copy_bound(program/'tree',core_tree,package_rows),
            'source':copy_bound(source,core_source,source_rows)}
    (core_work/'copied-inputs.json').write_text(json.dumps(copied,indent=2),encoding='utf8')
    modules=core_work/'modules.json'
    modules.write_text(json.dumps(['tree/'+name+'.py' for name in core_names]),encoding='utf8')
    lock=core_work/'executor-lock.json'
    lock.write_text(json.dumps(verification['executor_locks'][sys.platform]),encoding='utf8')
    observation=core_work/'observation.json'
    core_env={**env,'HELENE_TEST_SOURCE_ROOT':str(core_source),'HELENE_TREE_SRC':str(core_tree),
              'HOME':str(core_work/'home'),'USERPROFILE':str(core_work/'home'),
              'PYTHONPATH':os.pathsep.join(map(str,[core_tree,core_source,core_source/'desk',
                  core_source/'desk/localharness',core_source/'desk/tests']))}
    core_env.pop('PRAXIS_BASE',None)
    core_env.pop('PYTHONHOME',None)
    core_reuse=None
    assert bool(args.prior_native_evidence)==bool(args.prior_native_proof)
    if args.prior_native_evidence:
        core_reuse=json.loads(args.prior_native_proof.read_bytes())
        assert core_reuse['platform']==sys.platform and core_reuse['source_commit']==frozen['commit']
        assert core_reuse['source_tree_sha256']==frozen['source_tree_sha256']
        prior={}
        for key,row in core_reuse['native_core_evidence'].items():
            path=args.prior_native_evidence/row['path']
            assert path.resolve().is_relative_to(args.prior_native_evidence.resolve())
            assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
            prior[key]=json.loads(path.read_bytes())
        assert prior['copied_inputs']==copied
        assert prior['executor_lock']==verification['executor_locks'][sys.platform]
        assert Counter(prior['modules'])==Counter(json.loads(modules.read_bytes()))
        observed=prior['observation']
        observation.write_text(json.dumps(observed,indent=2),encoding='utf8')
    else:
        dependency_args=['--dependencies',str(dependencies)] if dependencies else []
        with (out/'native-core.log').open('wb') as log:
            core_result=subprocess.run([sys.executable,'-I','-S','-B',
                str(program/'verification/runner/source_verification_runner.py'),'--root',str(core_tree),
                '--modules-file',str(modules),'--report',str(observation),'--sandbox',str(core_work),'--lock',str(lock),
                *dependency_args],
                cwd=core_tree,env=core_env,stdout=log,stderr=subprocess.STDOUT,timeout=1200,start_new_session=True)
        assert core_result.returncode==0,'Read native deferred-admission/core custody log'
        observed=json.loads(observation.read_text(encoding='utf8'))
    if dependency_proof:
        dependency_proof['runner_lock_matched']=True
        dependency_proof['executor_lock_sha256']=hashlib.sha256(lock.read_bytes()).hexdigest()
        (core_work/'verification-dependencies.json').write_text(json.dumps(dependency_proof,indent=2),encoding='utf8')
    assert observed['complete'] and observed['successful'] and observed['coverage_complete']
    assert Counter(observed['collected'])==Counter(observed['executed']+observed.get('skipped_before_start',[]))
    assert Counter(observed['collected'])==Counter(row['id'] for row in observed['outcomes'])
    from source_declared_skips import collect
    declared=collect(program/'tree')
    assert declared==verification['source_declared_command_skips']
    prefix=declared['module']+'.'
    assert Counter(row['id'] for row in declared['cases'])==Counter(
        name for name in observed['collected'] if name.startswith(prefix))
    permitted=verification['allowed_test_skips'][sys.platform]
    for row in observed['outcomes']:
        if row['status']=='skipped':
            assert permitted.get('core::'+row['id'])==row['reason'],'Undeclared native core skip'
        else:
            assert row['status']=='passed','Native core contract did not pass'
    gui_env={key:value for key,value in env.items() if key not in ('PYTHONPATH','PYTHONHOME','PRAXIS_BASE','HELENE_TREE_SRC')}
    with (out/'native-gui.log').open('wb') as log:
        result=subprocess.run([sys.executable,'-B',str(args.equipment/'native_gui.py'),str(program),str(out/'native-gui')],
            env=gui_env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
    assert result.returncode==0,'Read native GUI log'
    proof=json.loads((args.equipment/'SOURCE-FROZEN.json').read_text(encoding='utf8'))
    with (out/'computer-deadline.log').open('wb') as log:
        native_deadline=subprocess.run([sys.executable,'-B',str(args.equipment/'native_deadline_observer.py'),
            '--body',str(program/'helene-body'),'--output',str(out/'computer-deadline')],
            env=env,stdout=log,stderr=subprocess.STDOUT,timeout=90)
    assert native_deadline.returncode==0,'Read computer-deadline.log'
    deadline_proof=json.loads((out/'computer-deadline/PASS.json').read_bytes())
    assert deadline_proof['passed']
    receipt={'computer_deadline':deadline_proof,'success':True,'source_commit':proof['commit'],'source_tree_sha256':proof['source_tree_sha256'],
        'uid':os.getuid(),'version':args.version,'python':sys.version,'cpu_workers':workers(),
        'runtime_admitted':True,'regressions':rows,'relay':relay,'native_core':core_names,
        'regressions_reused':reuse,
        'native_core_reused':core_reuse,
        'verification_dependencies':dependency_proof,
        'native_core_cases':len(observed['collected']),'native_core_outcomes':dict(Counter(row['status'] for row in observed['outcomes'])),
        'source_declared_command_skips':declared,
        'handoff_regressions':{'declaration':handoff,'declaration_sha256':hashlib.sha256((args.equipment/'HANDOFF-TESTS.json').read_bytes()).hexdigest(),
            'results':[row for row in rows if row['module'] in handoff['modules']],
            'fresh_execution':reuse is None,'application_bindings':application_bindings,
            'execution_api_root':'Frozen source files byte-equivalent to the actual packaged application APIs'},
        'native_gui':json.loads((out/'native-gui/native-gui.json').read_text(encoding='utf8')),
        'paid_provider':False,'physical_devices':False,'owner_installation_changed':False}
    (out/'native-final.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':
    main()
