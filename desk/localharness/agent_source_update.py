"""The selected agent resolves isolated source; independent review still accepts it."""
from pathlib import Path
import difflib,json,time
from . import source_merge as merge
from .package_notice import read,write

class AgentResolutionPending(RuntimeError):pass

class AgentResolutionUnavailable(RuntimeError):pass


def require_patch_tool(program):
    """Inspect the running app's tool contract without importing its code."""
    import ast
    path=Path(program)/'app/localharness/source_update.py'
    try:
        module=ast.parse(path.read_text(encoding='utf-8-sig'))
        tool=next(ast.literal_eval(n.value) for n in reversed(module.body)
                  if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TOOL' for t in n.targets))
        actions=tool['input_schema']['properties']['action']['enum']
        protocol=next((ast.literal_eval(n.value) for n in reversed(module.body)
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CONTINUATION_PROTOCOL' for t in n.targets)),0)
        if {'diff','patch','finish','verify','review','apply'}<=set(actions) and protocol==2:return
    except (OSError,ValueError,SyntaxError,StopIteration,KeyError,TypeError):pass
    raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Прежний агент не умеет точечно исправлять конфликт обновления. Можно установить чистый код, сохранив текущую папку данных. Автоматического архива кода нет.')

def marker(directory):return Path(directory)/'agent-resolution.json'

def _carry(tree,directory):
    from .source_update import path
    request=read(path(tree));prior=Path(request.get('directory') or '.')
    if not request.get('resolution_owner')=='agent' or prior.resolve()==Path(directory).resolve():return
    current=merge.validate_inputs(directory)
    valid_roots={Path(request.get('program') or '.')/'.source-updates', Path(tree).resolve()/'memory/.source-updates'}
    if prior.parent not in valid_roots or prior.name!=request.get('id'):return
    old=merge.validate_inputs(prior)
    if (old['state']!='ready' or old['files']!=current['files'] or old['local_path']!=current['local_path']
            or merge.inventory(prior/'result')!=old.get('result')):return
    candidate=merge.inventory(directory/'result')
    for name in sorted(set(candidate)|set(old['result'])):
        if name not in current['conflicts'] and candidate.get(name)==old['result'].get(name):continue
        target=prior/'result'/name
        merge.resolve(directory,name,target.read_text(encoding='utf8') if target.is_file() else None,
            delete=not target.is_file(),reason='Carry selected-agent resolution of identical frozen A/L/B inputs',
            expected_candidate=merge.candidate_signature(directory))
    merge.finish(directory,exe=merge.executable(request['program']))
    write(Path(directory)/'agent-resolution-carry.json',{'from':str(prior),'input_sha256':merge.input_signature(current),
        'candidate_sha256':merge.candidate_signature(directory),'previous_merged_commit':old['merged_commit']})

def request(tree,program,source,directory,version):
    from . import source_update as pipeline
    directory=Path(directory);plan=merge.validate_inputs(directory)
    if not marker(directory).exists():
        _carry(tree,directory)
        write(marker(directory),{'format':'helene-agent-resolution.v1','input_sha256':merge.input_signature(plan),
            'source':str(Path(source).resolve()),'owner_update_requested':True,'continuation':pipeline.AUTOMATIC})
    pipeline.ensure(tree,program,source,directory,version)
    plan=merge.validate_inputs(directory)
    operation=read(directory/'pipeline.json')
    value={'id':directory.name,'program':str(Path(program).resolve()),'directory':str(directory.resolve()),
        'source':str(Path(source).resolve()),'tree':str(Path(tree).resolve()),'version':version,
        'resolution_owner':'agent','input_sha256':operation['input_sha256'],
        'state':'pending' if operation.get('state')!='ready_to_apply' else 'ready'}
    # Old2.1.1's existing hand/notifier understands pending and ready.
    previous=read(pipeline.path(tree))
    if previous.get('id')==value['id']:
        value.update({k:previous[k] for k in ('tries','retry_at','followthrough') if k in previous})
    write(pipeline.path(tree),value)
    return operation

def command(tree, action, **args):
    from .source_update import applied_status
    terminal=applied_status(tree)
    if terminal:
        if action!='status':raise ValueError('This source update is already installed')
        return terminal
    from .source_update_hand import try_request
    handled,value=try_request(tree,action,args)
    if handled:return value
    from .source_update import path, _bound
    from .config_transaction import transaction
    if action == 'status':
        return _command(tree, action, **args)
    directory, _ = _bound(read(path(tree)),tree=tree)
    with transaction(directory / 'pipeline.json'):
        return _command(tree, action, **args)


def _command(tree,action,**args):
    from . import source_update as pipeline,owner_stop
    request=read(pipeline.path(tree));directory,plan=pipeline._bound(request,tree=tree)
    proof=read(marker(directory))
    if proof.get('input_sha256')!=merge.input_signature(plan):raise ValueError('No selected-agent resolution authority for these inputs')
    operation=read(directory/'pipeline.json')
    if action=='status':
        from .source_update_hand import public_context
        from .runtime_identity import current as worker_identity
        state=operation.get('state');ready=plan.get('state')=='ready'
        if state=='waiting_agent':
            next_action=('Inspect the exact core/app composition and findings. Repair then finish; request verify for the full applicable battery, review after full PASS, and apply after clear exact review. No new owner confirmation. '
                         + ('Current independent review is clear; apply can now request the mechanical switch.' if operation.get('review_clear') else ''))
            if operation.get('continuation')==pipeline.AUTOMATIC:
                next_action='Repair the exact candidate and finish. The incoming coordinator owns full tests, independent review and the authorized switch. Handle concrete findings; do not manually replay mechanical phases.'
        elif state in ('queued','merging','reviewing','verification_requested','review_requested'):
            next_action='The incoming host is executing the exact verification/review you requested. Inspect status; do not use ordinary live self_review as an update approval.'
        elif state=='applied':
            next_action='This exact update was installed and its new startup was confirmed. Continue the owner task using installed_program; this old update candidate needs no further edits or reminders.'
        elif state=='ready_to_apply':
            next_action='Exact candidate passed independent review. The authorized updater continues automatically; verify installed behavior and report evidence after restart.'
        else:
            next_action='Read the exact review_evidence/error. Do not revive a cancelled plan or bypass review. Repair the cause and use update_request for a fresh authorized preserve update; explicit Stop remains binding.'
        from . import update_composition
        composition = update_composition.signature(directory, operation) if (directory/'composition.json').is_file() else None
        shared = update_composition.for_plan(directory, operation)
        if not shared['consistent']:
            next_action='Reconcile the differing shared_application app paths with each selected peer, using app repair and finish, then a new full verify and independent review for every changed composition. Old receipts cannot approve peer app bytes.'
        compound=read(directory/'composition.json');app_plan=read(directory/'app-merge/plan.json')
        app_paths=set(compound.get('base_app',{}))
        app_paths.update((compound.get('app_source') or {}).get('binding',{}).get('local',{}))
        return {**operation,'version':request['version'],'conflicts':plan['conflicts'],'resolved':plan['resolved'],
                'app_conflicts':app_plan.get('conflicts',[]),'app_resolved':app_plan.get('resolved',{}),
                'coordinator_context':public_context(directory),
                'worker_context':worker_identity(tree),
                'candidate_state':plan['state'],'next_action':next_action,'acceptance_owner':'selected-agent',
                'evidence_handling':'Frozen snapshots and incoming files are read-only evidence. Never import or execute them in place: Python may write __pycache__. Use AST or copy files to a separate temporary directory and run python -B there.',
                'candidate_sha256':merge.candidate_signature(directory), 'composition_sha256':composition,
                'compound_paths':['app/'+name for name in sorted(app_paths)],
                'shared_application': shared}
    pipeline._fresh(operation,directory)
    if owner_stop.stopped() or owner_stop.paused() or operation.get('paused') or operation.get('cancel_requested'):
        raise ValueError('Owner Stop/Pause forbids source resolution')
    if operation.get('state')=='needs_attention' and operation.get('review_id'):
        store='.source-update-reviews' if (operation.get('coordinator') or {}).get('review_store')=='isolated-v1' else '.self-review'
        record=read(Path(operation['tree'])/'memory'/store/operation['review_id']/'request.json')
        terminal={'done','error','cancelled'}
        if record.get('execution') not in terminal or any(c.get('execution') not in terminal for c in record.get('critics',[])):
            raise ValueError('Existing reviewer producer is live or unknown; observe its exact result before editing or replaying')
    if operation.get('state') == 'ready_to_apply' and action in ('resolve','patch'):
        from . import update_composition
        if not update_composition.for_plan(directory, operation)['consistent']:
            _invalidate(directory, operation)
    if operation.get('state') not in ('waiting_agent','queued','needs_attention'):raise ValueError('Agent candidate is frozen for verification or independent review')
    if action in ('verify','review','apply'):
        from .app_source_update import require_ready
        require_ready(directory)
        from . import update_composition, source_update_battery
        composition = update_composition.signature(directory, operation)
        if args.get('expected_composition_sha256') != composition:
            raise ValueError('Use the exact current expected_composition_sha256 from status')
        if not update_composition.for_plan(directory, operation)['consistent']:
            raise ValueError('Final shared application differs; reconcile peer app candidates before verify/review/apply')
        if plan.get('state') != 'ready' or merge.inventory(directory/'result') != plan.get('result'):
            raise ValueError('Finish the complete current candidate first')
        operation['candidate_sha256'] = merge.candidate_signature(directory)
        operation['composition_sha256'] = composition
        if action == 'verify':
            operation.update(state='verification_requested', review_clear=False)
            operation.pop('verification_error', None)
        elif action == 'review':
            source_update_battery.validate(operation,directory)
            operation.update(state='review_requested', review_clear=False)
        else:
            if not operation.get('review_clear'):
                raise ValueError('Current independent update review is not clear')
            source_update_battery.validate(operation,directory)
            operation['state']='ready_to_apply'
        pipeline._persist(directory,operation)
        if action == 'apply':
            try:
                pipeline.validate_ready(directory)
            except BaseException:
                operation['state']='waiting_agent';pipeline._persist(directory,operation)
                raise
        request['state']=operation['state'];write(pipeline.path(tree),request)
        return _command(tree,'status')
    if action in ('read','diff','resolve','patch'):
        name=args.get('path','');merge.relative(name)
        if name.startswith('app/') and (directory/'composition.json').is_file():
            from . import update_composition
            relative=name.removeprefix('app/')
            target=directory/'compound/app'/relative
            if action in ('read','diff'):
                app_plan=read(directory/'app-merge/plan.json')
                side=args.get('side') or 'result'
                before=directory/'compound/base-app'/relative
                selected=target
                if app_plan:
                    if side not in ('base','local','incoming','result'):raise ValueError('Unknown application side')
                    before=directory/'app-merge/base'/relative
                    selected=directory/'app-merge'/side/relative if side!='result' else target
                def text(path,published=False):
                    if not path.is_file():return ''
                    raw=path.read_bytes()
                    try:value=raw.decode('utf-8-sig')
                    except UnicodeDecodeError:return None
                    from .self_review import source_credential,PRIVATE_NAMES
                    if any(p.lower() in PRIVATE_NAMES for p in path.relative_to(directory).parts) or source_credential(value,published=published):
                        raise ValueError('Private or credential-like authored application content is withheld')
                    return value
                original=text(before,True);current=text(selected,side in ('base','incoming'))
                if original is None or current is None:
                    return {'binary':True,'path':name,'side':side,'exists':selected.is_file(),
                        'sha256':update_composition._hash(selected) if selected.is_file() else None,
                        'next_action':'Use resolve with side=base/local/incoming to select exact binary bytes.'}
                text=(''.join(difflib.unified_diff(original.splitlines(True),current.splitlines(True),fromfile='release/'+name,tofile='result/'+name))
                      if action=='diff' else original if not app_plan and args.get('side')=='incoming' else current)
                offset=max(0,int(args.get('offset',0)));end=min(len(text),offset+8000)
                return {'text':text[offset:end],'offset':offset,'total_chars':len(text),'next_offset':end if end<len(text) else None}
            if action=='resolve' and args.get('side') in ('base','local','incoming') and (directory/'app-merge/plan.json').is_file():
                from .app_source_update import resolve_side
                resolve_side(directory,relative,args['side'],expected=args.get('expected_composition_sha256',''),reason=args.get('reason',''))
                _invalidate(directory,operation)
                return _command(tree,'status')
            content=args.get('content')
            if action=='patch':
                text=target.read_text(encoding='utf8');before=args.get('before');after=args.get('after')
                if not isinstance(before,str) or not before or not isinstance(after,str) or text.count(before)!=1:
                    raise ValueError('Patch must match one exact nonempty compound fragment')
                content=text.replace(before,after,1)
            update_composition.repair(directory,name,content,delete=args.get('delete') is True,
                expected=args.get('expected_composition_sha256',''),reason=args.get('reason',''))
            _invalidate(directory,operation)
            return _command(tree,'status')
        declared=set().union(*(set(v) for v in plan['files'].values()))
        if name not in declared:raise ValueError('Not a declared source input')
        if action in ('read','diff'):
            from .self_review import source_credential,PRIVATE_NAMES
            texts={};metadata={}
            for side in ('base','local','incoming','result'):
                file=directory/side/name
                if not file.is_file():metadata[side]={'exists':False};continue
                text=file.read_text(encoding='utf-8-sig');published=side in ('base','incoming') or any(
                    merge.inventory(directory/side).get(name)==plan['files'][s].get(name) for s in ('base','incoming'))
                if any(p.lower() in PRIVATE_NAMES for p in Path(name).parts) or source_credential(text,published=published):
                    metadata[side]={'withheld':'private or possible credential'};continue
                texts[side]=text;metadata[side]={'exists':True,'total_chars':len(text)}
            if action=='diff':
                text=''.join(''.join(difflib.unified_diff(texts.get('base','').splitlines(True),texts.get(side,'').splitlines(True),fromfile='base/'+name,tofile=side+'/'+name)) for side in ('local','incoming','result'))
            else:text=texts.get(args.get('side') or 'result','')
            offset=max(0,int(args.get('offset',0)));end=min(len(text),offset+8000)
            return {'sides':metadata,'text':text[offset:end],'offset':offset,'next_offset':end if end<len(text) else None,'total_chars':len(text)}
        if action=='patch':
            path=directory/'result'/name;text=path.read_text(encoding='utf8');before=args.get('before');after=args.get('after')
            if not isinstance(before,str) or not before or not isinstance(after,str) or text.count(before)!=1:
                raise ValueError('Patch must match one exact nonempty candidate fragment')
            args={**args,'content':text.replace(before,after,1),'side':''}
        if not args.get('expected_candidate_sha256'):raise ValueError('Every write requires the current expected_candidate_sha256 from status')
        if plan.get('state') == 'ready':
            merge.reopen(directory)
        merge.resolve(directory,name,args.get('content'),delete=args.get('delete') is True,side=args.get('side',''),
            reason=args.get('reason',''),expected_candidate=args.get('expected_candidate_sha256',''))
        _invalidate(directory,operation)
    elif action=='finish':
        if operation.get('continuation')==pipeline.AUTOMATIC:
            operation['finish_intent']={'input_sha256':operation['input_sha256'],
                'candidate_sha256':merge.candidate_signature(directory),'at':time.time()}
            pipeline._persist(directory,operation)
            pipeline.finish_for_verification(operation,directory)
            request['state']=operation['state']
        else:
            from .app_source_update import finish as finish_app
            finish_app(directory)
            merge.finish(directory,exe=merge.executable(request['program']))
            request['state']='pending'
        write(pipeline.path(tree),request)
    else:raise ValueError('Unknown selected-agent source action')
    return _command(tree,'status')


def _invalidate(directory, operation):
    """Retain old evidence as history, never reuse it to authorize new bytes."""
    from .source_update import _persist
    history={key:operation[key] for key in ('candidate_sha256','composition_sha256','battery','review_id','subject_sha256','review_evidence') if key in operation}
    if history:operation.setdefault('evidence_history',[]).append(history)
    for key in ('battery','review_id','subject_sha256','review_clear','verification_sources','finish_intent','finish_error'):
        operation.pop(key,None)
    operation['state']='waiting_agent'
    _persist(directory,operation)

def notify(tree,turn):
    from . import source_update as pipeline,owner_stop
    from .session_host import process_state
    request=read(pipeline.path(tree))
    from .update_supersession import blocked
    if blocked(request,protocol='source',current_tree=tree):return
    if (request.get('resolution_owner')!='agent'
            or owner_stop.stopped() or owner_stop.paused()):return
    directory=pipeline.locate(request);plan=read(directory/'plan.json')
    operation=read(directory/'pipeline.json')
    if blocked(operation,protocol='source',current_tree=tree):return
    state=operation.get('state')
    if (state not in ('waiting_agent','needs_attention','verification_requested','review_requested')
            or operation.get('cancel_requested') or operation.get('paused')
            or pipeline._cancelled(directory) or pipeline.job_blocked(operation)):return
    automatic=operation.get('continuation')==pipeline.AUTOMATIC
    hand=read(directory/'coordinator-hand.json')
    if automatic and state=='waiting_agent' and hand.get('state')!='ready':return
    producer=read(directory/'coordinator-process.json')
    observed=process_state(producer) if producer else 'absent'
    # notify_due is called by the idle runner, never during its active turn.
    # A long, genuinely running verifier/reviewer is not agent inactivity.
    if state in ('verification_requested','review_requested') and observed in ('alive','unknown'):return
    result=read(directory/'coordinator-result.json') if producer else {}
    if operation.get('continuation')==pipeline.AUTOMATIC and state in ('verification_requested','review_requested'):
        control=(operation.get('coordinator') or {}).get('job_control') or {}
        parent=read(control['path']) if control.get('path') else {}
        failed=(result.get('invocation')==producer.get('invocation') and result.get('returncode') not in (None,0,75))
        if not failed and parent.get('state')!='attention':return
    if state in ('verification_requested','review_requested'):
        next_step='Проверяющий процесс завершился без перехода состояния. Прочитай coordinator-result.json, verification_error и сохранённые результаты. Устрани причину через штатный путь; не повторяй сломанный прогон и не подменяй квитанции.'
    elif operation.get('finish_error'):
        next_step='Подготовка кандидата завершилась ошибкой: '+operation['finish_error']+'. Прочитай status и исправь точную причину, затем finish.'
    elif operation.get('verification_error'):
        next_step='Разбери verification_error и сохранившиеся результаты проверки. Исправь причину, затем запроси verify точного результата; отсутствие отчёта не является PASS.'
    elif operation.get('review_clear'):
        next_step='Текущая независимая панель clear: проверь status и вызови apply с текущим expected_composition_sha256. Затем проверь запуск установленной версии.'
    elif plan.get('state')=='ready':
        next_step='Кандидат подготовлен. Проверь status: если полный verify этого состава прошёл, вызови review; после clear вызови apply. Если точной батареи нет, вызови verify.'
    else:
        next_step='Прочитай status и все конфликты, доведи resolve/patch и finish. Не закрывай поручение обещанием или ожиданием нового разрешения владельца.'
    if automatic and state=='waiting_agent':
        next_step='Прочитай status и точные замечания/ошибку. Исправь кандидат или причину сбоя, затем finish. Тесты, независимое ревью и разрешённое переключение продолжает обновлятор; не повторяй сломанный прогон без устранения причины.'
    progress=merge.signature({'state':state,'candidate':operation.get('candidate_sha256'),
        'composition':operation.get('composition_sha256'),'review':operation.get('review_id'),
        'clear':operation.get('review_clear'),'error':operation.get('verification_error') or operation.get('finish_error') or operation.get('error'),
        'plan_state':plan.get('state'),'resolved':plan.get('resolved'),'result':plan.get('result')})
    notice_path=directory/'agent-notice.json'
    reminder=read(notice_path) or request.get('followthrough') or {}
    now=time.time()
    if reminder.get('progress')==progress:
        if reminder.get('state')!='failed' or now<reminder.get('retry_at',0):return
    # Revalidate complete frozen inputs only for a genuinely new delivered
    # event. Idle polling is a metadata observation, never an apply gate.
    pipeline._bound(request)
    notice={'progress':progress,'at':now,'kind':'measured-update-event','state':'reserved'}
    write(notice_path,notice)
    # Time passing is not a new event. Notify for actual new work/results only.
    # Save only the current identity; superseded/cancelled plans never reappear.
    current=read(pipeline.path(tree))
    latest=read(directory/'pipeline.json')
    if (current.get('id')!=request['id'] or current.get('state')=='cancelled'
            or latest!=operation or pipeline.job_blocked(latest)
            or pipeline._cancelled(directory) or owner_stop.stopped() or owner_stop.paused()):return
    continuation=('После finish обновлятор сам выполнит полные тесты, независимое ревью и переключение после clear. '
        'Исправляй конкретные findings и снова finish; механические этапы не требуют новых ходов.' if automatic else
        'У этого прежнего задания ручной протокол: после finish запроси verify, после точного PASS — review, после clear — apply.')
    text=('Событие текущего обновления '+request['id'][:8]+'. Следующий шаг: '+next_step+' '
        'source_update status показывает кандидат, исходную установку и контекст проверяющего процесса. '
        'Исправляй только изолированный кандидат через read/diff/patch/resolve; сохраняй оба намерения. '+continuation+' '
        'Действующее поручение владельца уже разрешает эти этапы. Живой код, замороженные входы и квитанции не меняй. '
        'Stop/Pause обязательны. После переключения проверь фактически запущенную версию.')
    try:
        result=turn('source-update-'+request['id']+'-'+progress[:16],speaker='Hélène',origin_text=text)
    except BaseException:
        write(notice_path,{**notice,'state':'failed','retry_at':now+60})
        raise
    write(notice_path,{**notice,'state':'failed' if result=='failed' else 'delivered',
        **({'retry_at':now+60} if result=='failed' else {})})
    return result
