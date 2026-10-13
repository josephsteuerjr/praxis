"""Selected-agent pause: durable state, explicit commands and worker receipts."""
import datetime as dt
import json
import os
import secrets
import time
from pathlib import Path

def _read(path):
    try:
        value=json.loads(path.read_text(encoding='utf8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def _write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name('.tmp-'+path.name+'-'+secrets.token_hex(6))
    temp.write_text(json.dumps(value,ensure_ascii=False)+'\n',encoding='utf8')
    os.replace(temp,path)

def state(tree):
    ctl=Path(tree)/'memory/.control';reader=_read(ctl/'desk_inbox/.reader.json');worker=reader.get('autonomy')
    try:fresh=type(reader.get('pid')) is int and reader['pid']>0 and 0<=time.time()-float(reader.get('at'))<15
    except (TypeError,ValueError,OverflowError):fresh=False
    supported=isinstance(worker,dict) and worker.get('protocol')==1 and bool(reader.get('started'))
    paused=(ctl/'autonomy-paused.json').exists()
    return {'supported':supported,'available':fresh and supported,'paused':paused,
            'stopped':bool(worker.get('stopped')) if supported and fresh else None,
            'pid':reader.get('pid') if fresh else None,'started':reader.get('started') if fresh else None,
            'pending':_read(ctl/'autonomy-request.json') or _read(ctl/'autonomy.processing.json') or None,
            'receipt':_read(ctl/'autonomy-receipt.json') or None,
            'note':('Агент на паузе; Telegram принимается без новых ответов.' if paused else 'Агент работает.') if fresh and supported
                   else 'Этот движок не поддерживает управление паузой. Серверный Hélène harness нужно обновить до 1.6.1.' if not supported else 'Движок пока не отвечает; состояние не подтверждено.'}

def request(tree,action,by='owner'):
    if action not in ('pause','resume'):return {'ok':False,'note':'Нужна команда pause или resume.'}
    observed=state(tree)
    if not observed['available']:return {'ok':False,'note':observed['note']}
    if action=='resume' and observed['stopped']:return {'ok':False,'note':'Включён аварийный стоп. Сначала сними его штатной командой владельца.'}
    value={'id':secrets.token_hex(12),'action':action,'by':str(by)[:40],
           'at':dt.datetime.now(dt.timezone.utc).isoformat(),'pid':observed['pid'],'started':observed['started']}
    _write(Path(tree)/'memory/.control/autonomy-request.json',value)
    return {'ok':True,'request':value,'note':'Пауза запрошена; жду подтверждения.' if action=='pause' else 'Продолжение запрошено; жду подтверждения.'}

def request_and_wait(tree,action,by='telegram-owner',wait=3):
    answer=request(tree,action,by)
    if not answer['ok']:return answer
    deadline=time.monotonic()+wait
    while time.monotonic()<deadline:
        receipt=_read(Path(tree)/'memory/.control/autonomy-receipt.json')
        if receipt.get('id')==answer['request']['id'] and receipt.get('phase')!='resume_prepared':
            return {**answer,'ok':bool(receipt.get('done')),'note':receipt.get('note') or 'Команда не подтверждена.','receipt':receipt}
        time.sleep(.1)
    return answer

def consume(tree,manager,statuses,started,before_resume=None):
    ctl=Path(tree)/'memory/.control';claimed=ctl/'autonomy.processing.json'
    if not claimed.exists():
        try:os.replace(ctl/'autonomy-request.json',claimed)
        except FileNotFoundError:return None
    asked=_read(claimed);receipt={'id':asked.get('id'),'action':asked.get('action'),'pid':os.getpid(),'started':started,'done':False,'failed':[]}
    import owner_stop,control_watch,process_scope
    try:
        if not asked.get('id') or asked.get('pid')!=os.getpid() or asked.get('started')!=started:
            raise ValueError('Движок изменился; повтори команду для текущего агента.')
        owner_stop.configure(tree)
        if asked['action']=='pause':
            cutoff=control_watch._instant(asked['at']);owner_stop.pause('owner pause')
            for row in manager.list_runs(statuses=tuple(statuses)):
                rid=str(row.get('run_id') or row.get('id') or '')
                if not rid:continue
                try:
                    if control_watch._instant(row.get('created_at'))+dt.timedelta(milliseconds=1)>cutoff:continue
                    with control_watch._BOUNDARY_LOCK:
                        processes=process_scope.cancel(rid)
                        if processes.get('failures'):receipt['failed'].append({'run_id':rid,'process_failures':processes['failures']})
                        manager.request_cancel(rid,actor='desk:'+str(asked.get('by') or 'owner'),reason='owner pause')
                except Exception as exc:receipt['failed'].append({'run_id':rid,'error':type(exc).__name__})
            receipt.update(done=True,note='Пауза включена. Telegram сохраняется; новые ответы и фоновые ходы остановлены.')
            if receipt['failed']:receipt['note']+=' Остановка некоторых текущих действий не подтверждена; подробности в расписке управления.'
        elif asked['action']=='resume':
            with owner_stop.LOCK:
                if owner_stop.stopped():raise RuntimeError('Аварийный стоп снимается отдельно владельцем.')
                newer=_read(ctl/'autonomy-request.json')
                pause=_read(ctl/'autonomy-paused.json')
                cutoff=control_watch._instant(asked['at']).timestamp()
                if (newer.get('action')=='pause' or float(pause.get('at') or 0)>cutoff):
                    raise RuntimeError('Есть более новая команда паузы; старое продолжение отменено.')
                prepared=_read(ctl/'autonomy-receipt.json')
                if not (prepared.get('id')==asked['id'] and prepared.get('phase')=='resume_prepared' and prepared.get('started')==started):
                    if before_resume:before_resume()
                    _write(ctl/'autonomy-receipt.json',{**receipt,'phase':'resume_prepared'})
                owner_stop.resume()
            receipt.update(done=True,note='Агент продолжает работу. Накопившиеся обращения Telegram сохранены без отдельных ответов.')
        else:raise ValueError('Неизвестная команда автономии.')
    except Exception as exc:receipt.update(note=str(exc)[:240],error=type(exc).__name__)
    receipt.update(at=dt.datetime.now(dt.timezone.utc).isoformat(),paused=(ctl/'autonomy-paused.json').exists())
    # If receipt persistence fails, retain the claim. No UI may certify success.
    _write(ctl/'autonomy-receipt.json',receipt);claimed.unlink()
    return receipt
