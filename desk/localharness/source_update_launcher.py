"""The ordinary, separately running test launcher and its one transition report.

The existing battery owns parallel commands and their descendant custody. This
adapter owns exactly one invocation (PID + birth + nonce), never a second agent
or polling scheduler. A lost process/result stays unknown, never auto-replayed.
"""
from pathlib import Path
import argparse
import hashlib
import os
import subprocess
import sys
import uuid
if __package__ in (None,''):
    program=Path(__file__).resolve().parents[2]
    sys.path[:0]=[str(program/'app'),str(program/'tree')]
    __package__='localharness'
from .package_notice import read,write
from . import source_merge as merge,direct_update

FORMAT='helene-transition-tests.v1'
_PROCESSES={}


def request(operation,directory):
    from . import update_composition
    return {'format':FORMAT,'composition_sha256':update_composition.signature(directory,operation),
        'cores':direct_update.core_rows(directory,fresh=True),
        'verification_request':operation.get('verification_request',{}),
        'executor':operation.get('verification_executor',{}),
        'bundle':operation.get('verification_bundle') or operation['coordinator'].get('verification_bundle_sha256'),
        'operation':operation}


def _paths(operation,directory):
    value=request(operation,directory)
    # Mutable lifecycle/result fields are not the request identity.
    bound={k:v for k,v in value.items() if k!='operation'}
    key=merge.signature(bound)
    root=Path(directory)/'verification'/key
    return root,value,bound


def _observe(root):
    from .session_host import process_state
    descriptor=read(root/'process.json')
    if not descriptor:return None,None
    process=_PROCESSES.get(str(root))
    if process is not None:
        code=process.poll()
        state='alive' if code is None else 'absent'
    else:state=process_state(descriptor)
    result=read(root/'result.json')
    if state=='alive':return descriptor,None
    if state=='unknown':raise RuntimeError('Test launcher process observation unknown; retained, not replayed')
    if not result or any(result.get(k)!=descriptor.get(k) for k in ('pid','birth','invocation')):
        raise RuntimeError('Test launcher ended without its bound report; retained results require observation')
    if result.get('shared_inputs',{}).get('retained_for_custody') is True:
        from .source_update_battery import CustodyUnconfirmed
        raise CustodyUnconfirmed('Test command descendants remain unconfirmed; retained work/report are not settlement')
    if process is not None:_PROCESSES.pop(str(root),None)
    return descriptor,result


def reference(operation,directory):
    """Read the one bound producer even for pre-fix interrupted linkage."""
    current=operation.get('test_launcher')
    if current:return current
    if not (Path(directory)/'verification').exists():return None
    root,_value,_bound=_paths(operation,directory);descriptor=read(root/'process.json')
    if descriptor:return {'root':str(root),**descriptor}
    if (root/'request.json').exists():raise RuntimeError('Launcher admission record is incomplete; no absence proof')
    return None


def _reference(operation,directory,root,descriptor):
    operation['test_launcher']={'root':str(root),**descriptor}
    from .source_update import _persist
    _persist(Path(directory),operation)


def tick(operation,directory,stopped):
    if stopped():
        cancel(operation,directory)
        raise InterruptedError('Owner stopped separate verification')
    existing=reference(operation,directory)
    if existing:
        _reference(operation,directory,existing['root'],{k:v for k,v in existing.items() if k!='root'})
        if _observe(Path(existing['root']))[1] is None:return None
    root,value,bound=_paths(operation,directory)
    descriptor,result=_observe(root)
    if result is not None:return collect(operation,directory)
    if descriptor is not None:
        _reference(operation,directory,root,descriptor)
        return None
    root.mkdir(parents=True,exist_ok=True)
    invocation=uuid.uuid4().hex
    write(root/'request.json',{'request':value,'binding':bound,'invocation':invocation})
    # Starting is durable before Popen. An ambiguous admission grants no replay.
    descriptor={'state':'starting','invocation':invocation}
    _reference(operation,directory,root,descriptor)
    write(root/'process.json',descriptor)
    from .update_process import interpreter_for,background_flags
    new=Path(operation['coordinator']['program'])
    command=[str(interpreter_for(new)),'-s','-B',str(new/'app/localharness/source_update_launcher.py'),
             '--root',str(root)]
    from command_process import CommandProcess
    with (root/'launcher.log').open('ab') as output:
        process=CommandProcess(command,cwd=new,stdin=subprocess.DEVNULL,stdout=output,
            stderr=subprocess.STDOUT,creationflags=background_flags(),start_new_session=os.name!='nt')
    import psutil
    descriptor={'state':'running','invocation':invocation,'pid':process.pid,'birth':psutil.Process(process.pid).create_time()}
    _PROCESSES[str(root)]=process
    write(root/'process.json',descriptor)
    _reference(operation,directory,root,descriptor)
    return None


def cancel(operation,directory):
    try:existing=reference(operation,directory)
    except (OSError,ValueError,KeyError,RuntimeError):return False
    if not existing:return True
    _reference(operation,directory,existing['root'],{k:v for k,v in existing.items() if k!='root'})
    root=Path(existing['root']);write(root/'stop.json',{'reason':'Owner Stop'})
    try:_descriptor,result=_observe(root)
    except RuntimeError as exc:
        operation['test_launcher_error']=str(exc)
        return False
    return result is not None


def collect(operation,directory):
    root,_value,bound=_paths(operation,directory)
    descriptor,result=_observe(root)
    if result is None:raise ValueError('Separate test launcher has not completed')
    if result.get('binding')!=bound or result.get('status')!='ready_for_review':
        raise ValueError('Separate test launcher did not complete: '+str(result.get('error') or result.get('status')))
    if (root/'stop.json').exists():raise ValueError('Stopped test launcher is not review evidence')
    operation['transition_battery']={'root':str(root),'sha256':hashlib.sha256((root/'result.json').read_bytes()).hexdigest()}
    validate(operation,directory)
    sources={'battery':{'root':str(root),'files':{'report.md':'report.md'}}}
    from . import source_update_battery as battery
    for row in result['results']:
        core=next(r for r in _value['cores'] if r['key']==row['execution_core'])
        view=direct_update.operation(core['directory'],operation);view['battery']=row['battery']
        cache=Path(core['directory'])/'test-battery'/row['battery']['key']
        receipt=read(cache/'receipt.json');battery._sources(cache,receipt)
        sources['battery_'+row['core'].replace('-','_')]={'root':str(cache/'evidence'),
            'files':{n:n for n in receipt['files']}}
    return sources


def validate(operation,directory):
    from . import source_update_battery as battery
    root,value,bound=_paths(operation,directory)
    pointer=operation.get('transition_battery') or {}
    if pointer.get('root')!=str(root) or pointer.get('sha256')!=hashlib.sha256((root/'result.json').read_bytes()).hexdigest():
        raise ValueError('Whole-transition test report changed or is missing')
    _descriptor,result=_observe(root)
    if result is None or result.get('binding')!=bound or result.get('status')!='ready_for_review' or (root/'stop.json').exists():
        raise ValueError('Whole-transition verification is incomplete or stale')
    rows=result.get('results',[])
    if [r.get('core') for r in rows]!=[r['key'] for r in value['cores']]:
        raise ValueError('Whole-transition verification omitted/duplicated a selected core')
    checked={}
    for core,row in zip(value['cores'],rows):
        canonical=row.get('execution_core')
        selected=next((r for r in value['cores'] if r['key']==canonical),None)
        if selected is None or _equivalent(core,operation)!=_equivalent(selected,operation):
            raise ValueError('Test reuse does not cover this core and its complete delta policy')
        view=direct_update.operation(selected['directory'],operation)
        view['battery']=row['battery']
        if canonical not in checked:checked[canonical]=battery.validate_single(view,selected['directory'])
    return {'review_evidence_complete':True,'cores':len(rows),'executions':checked,
        'full_pass':all(r['full_pass'] for r in checked.values()),
        'execution_pass':all(r['execution_pass'] for r in checked.values())}


def _equivalent(core,operation):
    plan=merge.validate_inputs(core['directory'])
    return merge.signature({'result':core['files'],'sides':plan['files'],
        'request':operation.get('verification_request',{})})


def run(root):
    import psutil
    root=Path(root);saved=read(root/'request.json');value=saved['request'];operation=value['operation']
    directory=Path(operation['directory'])
    own={'pid':os.getpid(),'birth':psutil.Process().create_time(),'invocation':saved['invocation']}
    result={**own,'format':FORMAT,'binding':saved['binding'],'results':[]}
    # The nonce is present before parent publication: a fast child cannot race
    # an empty descriptor into authority. The parent rechecks these same bytes.
    write(root/'process.json',{**own,'state':'running'})
    from . import source_update_battery as battery,source_update
    stopped=lambda: ((root/'stop.json').exists() or (directory/'cancel.json').exists()
        or source_update._owner_blocked(operation['tree']) or source_update.job_blocked(operation))
    import tempfile
    shared={'work':str(Path(tempfile.mkdtemp(prefix='helene-update-tests-transition-')).resolve())}
    uncertain=False
    try:
        if request(operation,directory) != value:
            raise ValueError('Verification request changed before independent execution')
        executions={}
        for core in value['cores']:
            if stopped():raise InterruptedError('Owner stopped separate test launcher')
            key=_equivalent(core,operation)
            if key not in executions:
                view=direct_update.operation(core['directory'],operation)
                battery.prepare(view,core['directory'],{},stopped,shared=shared)
                battery.validate_single(view,core['directory'])
                executions[key]={'execution_core':core['key'],'battery':view['battery']}
            result['results'].append({'core':core['key'],**executions[key]})
        if stopped():raise InterruptedError('Owner stopped verification before report publication')
        result['status']='ready_for_review'
    except BaseException as exc:
        uncertain=isinstance(exc,battery.CustodyUnconfirmed)
        result.update(status='blocked',error=f'{type(exc).__name__}: {exc}')
    finally:
        if not uncertain:
            battery._cleanup_work(Path(shared['work']),Path(shared.get('cache',root)))
        result['shared_inputs']={'preparations':1 if shared.get('identity') else 0,'work':shared['work'],'retained_for_custody':uncertain}
    # Exact structured report is the ordinary report read by the independent panel.
    import json
    (root/'report.md').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    write(root/'result.json',result)
    return 0 if result['status']=='ready_for_review' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    return run(parser.parse_args().root)


if __name__=='__main__':
    raise SystemExit(main())
