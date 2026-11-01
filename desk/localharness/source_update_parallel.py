"""Twelve private battery slots; recipe coverage stays owned by the full gate."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import ast
import hashlib
import os
import queue
from pathlib import Path
import shutil
import stat
import threading
import time

from .package_notice import read,write


def live_progress(work,cache,slot,active):
    """Read only the current command's telemetry; it never grants approval."""
    if active.get('phase')!='running' or not active.get('lane'):return {}
    row=read(Path(work)/('slot-'+str(slot))/'test-progress.json')
    identity=hashlib.sha256(active['lane'].encode()).hexdigest()[:20]
    expected=str((Path(cache)/(identity+'.cases.json')).resolve())
    if row.get('format')!='helene-test-progress.v1' or row.get('report')!=expected:return {}
    completed,failed,collected=(row.get(key) for key in ('tests_completed','tests_failed','tests_collected'))
    if any(type(n) is not int or n<0 for n in (completed,failed)):return {}
    if collected is not None and (type(collected) is not int or collected<completed):return {}
    return {'tests_completed':completed,'tests_failed':failed,'tests_collected':collected,
            'current_test':row.get('current_test') if isinstance(row.get('current_test'),str) else '',
            'test_phase':row.get('phase')}


def policy(operation):
    workers=(operation.get('verification_executor') or {}).get('parallel_lanes',12)
    if type(workers) is not int or not 1<=workers<=12:
        raise ValueError('parallel_lanes must be an integer from 1 through 12')
    return {'version':3,'workers':workers,'isolation':'private-slot-source-home-dependencies-caches',
            'dispatch':'next-free-private-slot;ordered-build-chains',
            'quiescent':'after-all-parallel-command-groups-settle;fresh-private-copy',
            'core_sharding':'sha256-case-id','dependencies':'npm-chain;cargo-chain;declared-shared-native-quiescent',
            'scheduler_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def shared_native_session(binding):
    # The pure hands CLI has per-call base/receipt paths in each private slot.
    # Desktop/body/display inputs share a live session and remain serialized.
    env = binding['executor'].get('native_inputs', {}).get('native_env', {})
    return bool(set(env) - {'PRAXIS_HANDS','HELENE_HOST_BINARY'})


def native_scope(lane,env,native_inputs):
    """Unannotated commands have private CLI hands, never shared desktop inputs."""
    from .source_verification_bundle import SHARED_NATIVE_INPUTS
    requested=lane.get('native_inputs',[])
    if (not isinstance(requested,list) or any(not isinstance(key,str) or key not in SHARED_NATIVE_INPUTS for key in requested)
            or len(requested)!=len(set(requested))):raise ValueError('Invalid lane native capabilities')
    if 'HELENE_BODY_BIN' in requested and 'HELENE_BODY_DIR' not in requested:
        raise ValueError('Native body capability requires its declared directory')
    scoped=dict(env)
    for key in SHARED_NATIVE_INPUTS:scoped.pop(key,None)
    if native_inputs is None:
        if any(key in env for key in requested):raise ValueError('Shared native capability has no bound executor inputs')
        return scoped,None
    allowed={'PRAXIS_HANDS','HELENE_HOST_BINARY',*requested}
    selected={key:value for key,value in native_inputs.get('native_env',{}).items() if key in allowed}
    scoped.update(selected)
    keep={str(Path(selected[key]).resolve()) for key in ('PRAXIS_HANDS','HELENE_HOST_BINARY') if key in selected}
    if 'HELENE_BODY_DIR' in selected:
        base=Path(selected['HELENE_BODY_DIR']);suffix='.exe' if os.name=='nt' else ''
        keep.update(str((base/(prefix+'-'+kind+suffix)).resolve()) for prefix in ('helene','praxis') for kind in ('body','bridge'))
    pins={path:digest for path,digest in native_inputs.get('native_files',{}).items() if path in keep}
    return scoped,{'native_env':selected,'native_files':pins}


def _check(stopped):
    if stopped():raise InterruptedError('Owner stopped update battery')


def _copy(source,target,stopped,progress=None):
    """No links/shared writable inodes; cancellation is checked per chunk."""
    source,target=Path(source),Path(target)
    _check(stopped)
    info=source.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
        raise ValueError('Linked battery input root: '+str(source))
    for directory,dirs,names in os.walk(source):
        _check(stopped);here=Path(directory)
        for name in [*dirs,*names]:
            entry=here/name;info=entry.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('Linked battery input: '+str(entry))
        destination=target/here.relative_to(source);destination.mkdir(parents=True,exist_ok=True)
        for name in names:
            _check(stopped)
            copied=0
            with (here/name).open('rb') as src,(destination/name).open('xb') as dst:
                while chunk:=src.read(1024*1024):
                    _check(stopped);dst.write(chunk);copied+=len(chunk)
            shutil.copystat(here/name,destination/name)
            if progress:progress(copied,1)


def inventory(battery,root,stopped):
    rows={};root=Path(root)
    for directory,dirs,names in os.walk(root):
        _check(stopped);here=Path(directory)
        dirs[:]=sorted(name for name in dirs if name not in battery.bundle.EXCLUDED)
        for name in [*dirs,*sorted(names)]:
            _check(stopped);path=here/name;info=path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('Linked verification input')
            if not path.is_file():continue
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                while chunk:=stream.read(1024*1024):_check(stopped);digest.update(chunk)
            row={'sha256':digest.hexdigest(),'size':info.st_size,
                'mode':'100755' if info.st_mode&0o111 and os.name!='nt' else '100644'}
            if battery.bundle.python_framework(path)=='pytest':row['framework']='pytest'
            native=battery.bundle.python_native_inputs(path)
            if native is not None:row['native_inputs']=native
            rows[path.relative_to(root).as_posix()]=row
    return rows


def groups(lanes,workers,external_native=False):
    if len({row['id'] for row in lanes})!=len(lanes):raise ValueError('Duplicate battery lane identity')
    count=1 if external_native else workers
    result=[[] for _ in range(count)]
    turn=0
    for index,lane in enumerate(lanes):
        if lane.get('isolation')=='quiescent':continue
        # Each shard independently discovers the complete suite. Aggregation
        # verifies identical discovery plus an exact disjoint partition.
        if lane['kind']=='unittest' and count>1:
            for shard in range(count):
                result[shard].append((index,{**lane,'id':lane['id']+'/shard-'+str(shard),
                    '_shard_index':shard,'_shard_count':count}))
        else:
            slot=(0 if lane['kind'] in {'npm-prepare','npm-build','node'} else
                  min(1,count-1) if lane['kind'] in {'cargo','cargo-build'} else turn%count)
            result[slot].append((index,lane));turn+=1
    return [group for group in result if group]


def aggregate(lane,parts):
    if len(parts)==1 and '_shard_index' not in parts[0][0]:return parts[0][1]
    parts.sort(key=lambda pair:pair[0]['_shard_index'])
    rows=[row for _,row in parts];observations=[row.get('observation') or {} for row in rows]
    expected=observations[0].get('discovered')
    collected=[case for observed in observations for case in observed.get('collected',[])]
    complete=(expected is not None and all(observed.get('discovered')==expected for observed in observations)
        and len(parts)==parts[0][0]['_shard_count'] and Counter(collected)==Counter(expected)
        and all(observed.get('complete') and observed.get('coverage_complete') for observed in observations))
    observation={'collected':expected or collected,'complete':complete,'coverage_complete':complete,
        'tests_run':sum(row.get('tests_run',0) for row in observations)}
    for name in ('executed','skipped_before_start','outcomes'):
        observation[name]=[case for observed in observations for case in observed.get(name,[])]
    passed=complete and all(row.get('status')=='passed' and row.get('exit')==0 for row in rows)
    failure=next((row for row in rows if row.get('status')!='passed'),{})
    return {'id':lane['id'],'kind':lane['kind'],'status':'passed' if passed else failure.get('status','failed'),
        'reason':failure.get('reason','' if complete else 'Shard discovery/coverage differs'),
        'exit':0 if passed else 1,'observation':observation,'shards':rows,
        'logs':{name:digest for row in rows for name,digest in row.get('logs',{}).items()}}


def execution_lanes(lanes,repository,actual_files):
    """Read scheduling constraints from exact frozen test bytes, without
    changing the logical recipe schema understood by older update preparers.
    """
    result=[]
    for lane in lanes:
        name=lane.get('source','');value=dict(lane)
        from . import source_verification_bundle as bundle
        sources=list(dict.fromkeys([*lane.get('sources',[]),*([name] if name else [])]))
        for source in sources:
            if source not in actual_files:continue
            path=Path(repository)/source
            if path.suffix!='.py':continue
            body=path.read_bytes()
            if hashlib.sha256(body).hexdigest()!=actual_files[source]['sha256']:raise ValueError('Native capability source changed: '+source)
            native=bundle.python_native_inputs(path)
            if (native is not None)!=('native_inputs' in actual_files[source]) or native is not None and actual_files[source]['native_inputs']!=native:
                raise ValueError('Native capability metadata differs from frozen source: '+source)
        native=bundle.native_lane_inputs(actual_files,sources)
        if value.get('native_inputs',native)!=native:raise ValueError('Lane native capabilities differ from frozen source')
        if native:value.update(native_inputs=native,isolation='quiescent')
        if name.startswith('desk/tests/') and name.endswith('.py') and name in actual_files:
            body=(Path(repository)/name).read_bytes()
            if hashlib.sha256(body).hexdigest()!=actual_files[name]['sha256']:raise ValueError('Test isolation source changed: '+name)
            try:module=ast.parse(body.decode('utf-8-sig'))
            except (SyntaxError,UnicodeError):module=ast.Module(body=[],type_ignores=[])
            for node in module.body:
                if not isinstance(node,ast.Assign) or not any(isinstance(t,ast.Name) and t.id=='HELENE_TEST_ISOLATION' for t in node.targets):continue
                try:isolation=ast.literal_eval(node.value)
                except (ValueError,TypeError):raise ValueError('Test isolation must be a literal: '+name)
                if isolation!='quiescent' or value.get('isolation',isolation)!=isolation:raise ValueError('Test isolation differs from frozen source: '+name)
                value['isolation']=isolation
        result.append(value)
    return result


def run(battery,lanes,repository,actual_files,work,runner,tools,operation,cache,stopped,binding,errors):
    execution=execution_lanes(lanes,repository,actual_files)
    quiescent=[(i,lane) for i,lane in enumerate(execution) if lane.get('isolation')=='quiescent']
    scheduled=groups(execution,policy(operation)['workers']) or [[]]
    physical_total=sum(len(group) for group in scheduled)+len(quiescent)
    required_parts=Counter(index for group in scheduled for index,_ in group)
    required_parts.update(index for index,_ in quiescent)
    # Independent files use the next free private slot. Build chains and core
    # shards retain their explicit ordering and isolated native constraints.
    pending=queue.Queue()
    fixed=[]
    for group in scheduled:
        chain=[]
        for index,lane in group:
            if lane['kind'] in {'unittest-file','pytest-file','script','helper'}:
                pending.put((index,lane))
            else:chain.append((index,lane))
        fixed.append(chain)
    scheduled=fixed
    # Copies contain source/tests, not runtimes or models. Refuse insufficient
    # storage before making twelve dependency overlays; never silently share it.
    source_bytes=sum(row.get('size',0) for row in actual_files.values())
    dependency_bytes=sum(row.get('size',0) for row in binding['executor'].get('dependency_files',{}).values())
    executor=operation.get('verification_executor') or {}
    cache_bytes=0
    for group in scheduled:
        kinds={lane['kind'] for _,lane in group}
        needed=({'npm_config_cache'} if kinds&{'npm-prepare','npm-build','node'} else set())|({'CARGO_HOME','RUSTUP_HOME'} if kinds&{'cargo','cargo-build'} else set())
        for key in needed:
            source=Path(executor[key]) if executor.get(key) else None
            if source and source.is_dir():
                for directory,dirs,names in os.walk(source):
                    _check(stopped)
                    for name in names:cache_bytes+=(Path(directory)/name).lstat().st_size
    required=(source_bytes+dependency_bytes)*len(scheduled)+cache_bytes+256*1024*1024
    if shutil.disk_usage(work).free<required:
        raise OSError('Insufficient free space for isolated battery slots: '+str(required)+' bytes required')
    abort=threading.Event()
    def cancelled():return abort.is_set() or stopped()
    progress_lock=threading.RLock();finished_parts=Counter();active={};last_write=0.0
    observed_tests=0;observed_failed=0;copied_bytes=0;copied_files=0
    def publish(state='running',*,force=False,error=''):
        nonlocal last_write
        now=time.monotonic()
        if not force and now-last_write<.25:return
        running=[dict(slot=slot,**value,**live_progress(work,cache,slot,value)) for slot,value in sorted(active.items())]
        write(cache/'progress.json',{'state':state,'slots':len(scheduled),'updated_at':time.time(),
            'observation':'live scheduler; counters include only completed commands and observed tests',
            'completed':sum(finished_parts.values()),'total':physical_total,
            'completed_lanes':sum(finished_parts[i]==n for i,n in required_parts.items()),'total_lanes':len(lanes),
            'tests_completed':observed_tests+sum(row.get('tests_completed',0) for row in running),
            'tests_failed':observed_failed+sum(row.get('tests_failed',0) for row in running),'tests_total_known':False,
            'copied_bytes':copied_bytes,'copied_files':copied_files,
            'active':running,
            **({'error':error} if error else {})})
        last_write=now
    def copy_progress(size,files):
        nonlocal copied_bytes,copied_files
        with progress_lock:
            copied_bytes+=size;copied_files+=files;publish()
    def jobs(group):
        yield from group
        while not cancelled():
            try:yield pending.get_nowait()
            except queue.Empty:return
    with progress_lock:publish(force=True)
    def slot(number,group):
        nonlocal observed_tests,observed_failed
        _check(cancelled)
        private=work/('slot-'+str(number));repo=private/'repository'
        with progress_lock:
            active[number]={'phase':'preparing','started_at':time.time()};publish(force=True)
        _copy(repository,repo,cancelled,copy_progress)
        if inventory(battery,repo,cancelled)!=actual_files:raise ValueError('Private source copy differs')
        env=battery._environment(private/'home',repo/'live',repo/'desk',operation)
        kinds={lane['kind'] for _,lane in group}
        needed=({'npm_config_cache'} if kinds&{'npm-prepare','npm-build','node'} else set())|({'CARGO_HOME','RUSTUP_HOME'} if kinds&{'cargo','cargo-build'} else set())
        for key in ('CARGO_HOME','RUSTUP_HOME','npm_config_cache'):
            source=Path(env[key]);target=private/'inputs'/key
            if key in needed and source.is_dir():_copy(source,target,cancelled,copy_progress)
            env[key]=str(target)
        dependencies=(operation.get('verification_executor') or {}).get('python_dependencies')
        if dependencies:
            source=Path(dependencies).resolve();target=private/'dependencies'
            expected=binding['executor'].get('dependency_files')
            if expected is None:raise ValueError('Missing bound Python dependencies')
            _copy(source,target,cancelled,copy_progress)
            if inventory(battery,target,cancelled)!=expected:raise ValueError('Private Python dependencies differ')
            env['PYTHONPATH']=os.pathsep.join(str(target) if part==str(source) else part for part in env['PYTHONPATH'].split(os.pathsep))
            env['HELENE_VERIFICATION_DEPENDENCIES']=str(target)
        results=[]
        executed=[]
        for index,lane in jobs(group):
            _check(cancelled)
            with progress_lock:
                active[number]={'phase':'running','lane':lane['id'],'kind':lane['kind'],
                    'file':lane.get('source') or lane.get('root') or lane['id'],'started_at':time.time()}
                publish(force=True)
            reason=lane.get('platform_skip',{}).get(battery.sys.platform)
            if lane['kind']=='helper':row={'id':lane['id'],'status':'support-only','reason':lane['reason']}
            elif reason:row={'id':lane['id'],'status':'platform-skip','reason':reason}
            else:
                missing=errors.get(battery._lane_tool(lane))
                if lane['kind'] in {'npm-prepare','npm-build'}:missing=missing or errors.get('node')
                if not lane['sources']:missing='Full lane sources unavailable: '+lane['id']
                if missing:row={'id':lane['id'],'status':'unavailable','reason':missing}
                else:
                    try:row=battery._run_lane(lane,repo,runner,tools,env,cache,cancelled,binding['executor'].get('native_inputs'))
                    except InterruptedError:raise
                    except OSError as exc:row={'id':lane['id'],'status':'unavailable','reason':str(exc)}
            if dependencies and inventory(battery,target,cancelled)!=expected:
                raise ValueError('Tests changed private Python dependencies')
            # Each finished lane survives a later lane/cleanup failure.
            write(cache/(hashlib.sha256(lane['id'].encode()).hexdigest()[:20]+'.lane.json'),row)
            results.append((index,lane,row))
            executed.append(lane)
            with progress_lock:
                finished_parts[index]+=1
                observation=row.get('observation') or {}
                count=observation.get('tests_run')
                if type(count) is int and count>=0:observed_tests+=count
                observed_failed+=sum(value.get('status') not in {'passed','skipped'} for value in observation.get('outcomes',[]))
                active.pop(number,None)
                publish(force=True)
        changes,outputs=battery._test_copy_changes(repo,actual_files,executed,cancelled)
        with progress_lock:active.pop(number,None);publish(force=True)
        return results,[f'slot-{number}/{name}' for name in changes],[dict(row,slot=number) for row in outputs]
    results=[];changes=[];outputs=[];failures=[]
    with ThreadPoolExecutor(max_workers=policy(operation)['workers'],thread_name_prefix='helene-battery') as pool:
        pending_futures={pool.submit(slot,number,group) for number,group in enumerate(scheduled)}
        while pending_futures:
            completed,pending_futures=wait(pending_futures,timeout=2,return_when=FIRST_COMPLETED)
            with progress_lock:publish(force=True)
            for future in completed:
                try:
                    found,changed,built=future.result();results.extend(found);changes.extend(changed);outputs.extend(built)
                except BaseException as exc:abort.set();failures.append(exc)
    if failures:
        with progress_lock:publish('interrupted',force=True,error=str(failures[0]))
        # Every active child settles before cleanup; uncertain custody wins.
        raise next((exc for exc in failures if isinstance(exc,battery.CustodyUnconfirmed)),failures[0])
    _check(stopped)
    if quiescent:
        try:
            # Every parallel command and descendant has settled. Reclaim its
            # disposable copies before a fresh latency-measurement context, so
            # peak disk use does not grow to thirteen dependency overlays.
            cleanup=battery._cleanup_work(work,cache)
            if cleanup.get('state')!='removed':raise OSError('Could not retire parallel test copies before quiescent checks')
            Path(work).mkdir()
            found,changed,built=slot(0,quiescent)
            results.extend(found);changes.extend(changed);outputs.extend(built)
        except BaseException as exc:
            with progress_lock:publish('interrupted',force=True,error=str(exc))
            raise
    rows=[]
    for index,lane in enumerate(lanes):rows.append(aggregate(lane,[(part,row) for i,part,row in results if i==index]))
    with progress_lock:publish('completed',force=True)
    return rows,sorted(changes),outputs
