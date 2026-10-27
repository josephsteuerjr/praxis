"""The selected agent resolves isolated source; independent review still accepts it."""
from pathlib import Path
import difflib,json,time
from . import source_merge as merge
from .package_notice import read,write

class AgentResolutionPending(RuntimeError):pass

def marker(directory):return Path(directory)/'agent-resolution.json'

def _carry(tree,directory):
    from .source_update import path
    request=read(path(tree));prior=Path(request.get('directory') or '.')
    if not request.get('resolution_owner')=='agent' or prior.resolve()==Path(directory).resolve():return
    current=merge.validate_inputs(directory)
    if prior.parent!=Path(request.get('program') or '.')/'.source-updates' or prior.name!=request.get('id'):return
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
            'source':str(Path(source).resolve()),'owner_update_requested':True})
    pipeline.ensure(tree,program,source,directory,version)
    plan=merge.validate_inputs(directory)
    operation=read(directory/'pipeline.json')
    value={'id':directory.name,'program':str(Path(program).resolve()),'directory':str(directory.resolve()),
        'source':str(Path(source).resolve()),'tree':str(Path(tree).resolve()),'version':version,
        'resolution_owner':'agent','input_sha256':operation['input_sha256'],
        'state':'ready' if plan['state']=='ready' else 'pending'}
    # Old2.1.1's existing hand/notifier understands pending and ready.
    previous=read(pipeline.path(tree))
    if previous.get('id')==value['id']:
        value.update({k:previous[k] for k in ('tries','retry_at') if k in previous})
    write(pipeline.path(tree),value)
    return operation

def command(tree,action,**args):
    from . import source_update as pipeline,owner_stop
    request=read(pipeline.path(tree));directory,plan=pipeline._bound(request)
    proof=read(marker(directory))
    if proof.get('input_sha256')!=merge.input_signature(plan):raise ValueError('No selected-agent resolution authority for these inputs')
    operation=read(directory/'pipeline.json')
    if action=='status':
        state=operation.get('state');ready=plan.get('state')=='ready'
        if state=='waiting_agent' and not ready:
            next_action='Read review_evidence/findings, resolve and patch the isolated candidate, then finish. Acceptance is delegated to you; no new owner confirmation.'
        elif state in ('waiting_agent','queued','merging','reviewing'):
            next_action='The incoming coordinator owns independent review and automatic continuation. Inspect status; do not start the ordinary live self_review panel or ask the owner to launch review.'
        elif state=='ready_to_apply':
            next_action='Exact candidate passed independent review. The authorized updater continues automatically; verify installed behavior and report evidence after restart.'
        else:
            next_action='Read the exact review_evidence/error. Do not revive a cancelled plan or bypass review. Repair the cause and use update_request for a fresh authorized preserve update; explicit Stop remains binding.'
        return {**operation,'version':request['version'],'conflicts':plan['conflicts'],'resolved':plan['resolved'],
                'candidate_state':plan['state'],'next_action':next_action,'acceptance_owner':'selected-agent',
                'evidence_handling':'Frozen snapshots and incoming files are read-only evidence. Never import or execute them in place: Python may write __pycache__. Use AST or copy files to a separate temporary directory and run python -B there.',
                'candidate_sha256':merge.candidate_signature(directory)}
    pipeline._fresh(operation,directory)
    if owner_stop.stopped() or operation.get('cancel_requested'):raise ValueError('Owner Stop cancelled source resolution')
    if operation.get('state') not in ('waiting_agent','queued'):raise ValueError('Agent candidate is frozen for independent review')
    if action in ('read','diff','resolve','patch'):
        name=args.get('path','');merge.relative(name)
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
                text=''.join(''.join(difflib.unified_diff(texts.get('base','').splitlines(True),texts.get(side,'').splitlines(True),fromfile='base/'+name,tofile=side+'/'+name)) for side in ('local','incoming'))
            else:text=texts.get(args.get('side') or 'result','')
            offset=max(0,int(args.get('offset',0)));end=min(len(text),offset+8000)
            return {'sides':metadata,'text':text[offset:end],'offset':offset,'next_offset':end if end<len(text) else None,'total_chars':len(text)}
        if action=='patch':
            path=directory/'result'/name;text=path.read_text(encoding='utf8');before=args.get('before');after=args.get('after')
            if not isinstance(before,str) or not before or not isinstance(after,str) or text.count(before)!=1:
                raise ValueError('Patch must match one exact nonempty candidate fragment')
            args={**args,'content':text.replace(before,after,1),'side':''}
        if not args.get('expected_candidate_sha256'):raise ValueError('Every write requires the current expected_candidate_sha256 from status')
        merge.resolve(directory,name,args.get('content'),delete=args.get('delete') is True,side=args.get('side',''),
            reason=args.get('reason',''),expected_candidate=args.get('expected_candidate_sha256',''))
    elif action=='finish':
        merge.finish(directory,exe=merge.executable(request['program']))
        request['state']='ready';write(pipeline.path(tree),request)
    else:raise ValueError('Unknown selected-agent source action')
    return command(tree,'status')

def notify(tree,turn):
    from . import source_update as pipeline,owner_stop
    request=read(pipeline.path(tree))
    if (request.get('resolution_owner')!='agent' or request.get('state') not in ('pending','waiting_agent')
            or int(request.get('tries',0))>=3 or time.time()<request.get('retry_at',0)
            or owner_stop.stopped() or owner_stop.paused()):return
    request.update(tries=int(request.get('tries',0))+1,retry_at=time.time()+120);write(pipeline.path(tree),request)
    return turn('source-update-'+request['id'],speaker='Hélène',origin_text=
        'Владелец начал обновление с сохранением правок. Разреши пересечения в изолированном кандидате: '
        'source_update status, diff/read (с next_offset), resolve/patch и finish. '
        'Сохрани обе стороны намерений, проверь связи автоматически совмещённых файлов; '
        'семантический ремонт любого объявленного файла требует reason и текущий expected_candidate_sha256. '
        'Живой код и настройки не меняй. Приёмка передана тебе: сам прочитай замечания независимой панели, '
        'исправь кандидата и повтори finish. После finish проверенный входящий координатор запускает панель '
        'и автоматически продолжает разрешённое обновление; обычный self_review старого Forge для этого не нужен. '
        'Не перекладывай запуск или результат ревью на владельца и не проси повторное согласие на уже порученный этап. '
        'Замороженные снимки и incoming только читай: не импортируй и не исполняй их на месте, иначе Python создаёт __pycache__. '
        'Для пробы скопируй нужные файлы в отдельный временный каталог и запускай python -B там; для статической проверки используй AST. '
        'Смотри candidate_state, review_evidence и next_action в status; clear подтверждает только точные исходники, '
        'после перезапуска сам проверь установленное поведение и сообщи доказательства. '
        'Пройди пользовательский путь сам, в том числе headless при доступном браузере. '
        'Различай запрос модели, фактическое исполнение харнессом и вернувшийся модели результат; '
        'прохождение исходных тестов не заменяет проверку установленного приложения. Stop/отмена остаются обязательны.')
