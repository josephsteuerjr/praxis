"""Exact-run reset: durable request, bounded wait for actual resource-owner proof."""
import asyncio
import json
from pathlib import Path
import re
import secrets
import time

RUN_ID = re.compile(r'run-[A-Za-z0-9_-]{1,100}\Z')


def read(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def request(tree, run_id, reason='immediate owner reset', *, known=False):
    if not RUN_ID.fullmatch(str(run_id)):raise ValueError('Некорректный идентификатор хода.')
    tree=Path(tree)
    if not known:
        manifests=list((tree/'memory/runs').glob('*/'+run_id+'/manifest.json'))
        if len(manifests)!=1 or manifests[0].is_symlink() or not read(manifests[0]):
            raise ValueError('Ход не найден однозначно.')
    path=tree/'memory/.control/run-cancel'/(run_id+'.json')
    from localharness.phone_tunnel.platform_support import acquire_file_lock, release_file_lock
    path.parent.mkdir(parents=True,exist_ok=True)
    lock=acquire_file_lock(path.with_suffix('.lock'),timeout_sec=2)
    try:return _publish(path,run_id,reason)
    finally:release_file_lock(lock)


def _publish(path, run_id, reason):
    current=read(path)
    if current.get('run_id')==run_id and current.get('action')=='cancel':return path,current
    value=dict(id=secrets.token_hex(12),run_id=run_id,action='cancel',by='desk:owner',at=time.time(),reason=reason)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp-'+secrets.token_hex(6))
    temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    temporary.replace(path)
    return path,value


def known_run(tree, run_id, reason):
    # Legacy/custom harness identifiers keep their existing cancellation path;
    # only canonical runs participate in the new resource-owner protocol.
    if RUN_ID.fullmatch(str(run_id)):
        return request(tree,run_id,reason,known=True)


async def cancel(tree,run_id):
    try:path,asked=await asyncio.to_thread(request,tree,run_id)
    except (ValueError,OSError) as exc:return dict(ok=False,accepted=False,confirmed=False,note=str(exc))
    deadline=time.monotonic()+2
    result={}
    while time.monotonic()<deadline:
        result=await asyncio.to_thread(read,path.with_suffix('.receipt'))
        if result.get('request_id')==asked['id'] and result.get('ok') is True:
            return dict(ok=True,accepted=True,confirmed=True,receipt=result,
                        note='Исполнение хода прервано. Совершённые действия и история сохранены; неизвестные исходы ждут сверки.')
        await asyncio.sleep(.05)
    return dict(ok=True,accepted=True,confirmed=False,
                receipt=result if result.get('request_id')==asked['id'] else {},
                note='Сброс запрошен. Завершение текущего действия пока не подтверждено.')
