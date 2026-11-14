"""Owner control through the existing independent Docker controller."""
import asyncio
import json
import os
from pathlib import Path
import re
import secrets
import time

RUN_ID = re.compile(r'run-[A-Za-z0-9_-]{1,100}\Z')
_CHANGE = asyncio.Lock()


def read(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + secrets.token_hex(8))
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name != 'nt':
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


async def state(tree, docker):
    code, blob = await docker('/containers/praxis/json')
    if code != 200:
        return {'ok': False, 'available': False, 'note': 'Не удалось проверить контейнер Пракс.'}
    try:
        raw = json.loads(blob)
        current = raw['State']
        running = current['Running'] is True
    except (ValueError, KeyError, TypeError):
        return {'ok': False, 'available': False, 'note': 'Контроллер не получил состояние Пракс.'}
    panic = (Path(tree) / 'memory/.panic').exists()
    return {'ok': True, 'available': True, 'running': running,
        'stop_requested': panic, 'stopped': not running,
        'status': str(current.get('Status') or 'unknown'),
        'started_at': str(current.get('StartedAt') or ''),
        'note': 'Пракс остановлена.' if not running else
                'Стоп запрошен; проверяю завершение.' if panic else 'Пракс работает.'}


async def action(tree, docker, value):
    if not isinstance(value, dict) or set(value) != {'action'} or value['action'] not in ('stop', 'start'):
        return {'ok': False, 'note': 'Нужно действие stop или start.'}
    async with _CHANGE:
        return await _action(Path(tree), docker, value['action'])


async def _action(tree, docker, verb):
    sentinel = tree / 'memory/.panic'
    before = await state(tree, docker)
    if not before.get('available'):
        return before
    if verb == 'stop':
        # Persistent intent comes first: even an unsuccessful Docker request or
        # a later restart cannot silently re-enable the agent.
        write(sentinel, {'by': 'desk:owner', 'at': time.time(), 'reason': 'MiniApp emergency stop'})
        code, _ = await docker('/containers/praxis/stop?t=0', method='POST', timeout=15)
        after = await state(tree, docker)
        return {**after, 'ok': code in (204, 304) and after.get('stopped') is True,
                'accepted': True, 'action': 'stop'}
    sentinel.unlink(missing_ok=True)
    code, _ = await docker('/containers/praxis/start', method='POST', timeout=15)
    after = await state(tree, docker)
    if code not in (204, 304) or not after.get('running'):
        write(sentinel, {'by': 'desk:owner', 'at': time.time(), 'reason': 'start unconfirmed'})
        return {**after, 'ok': False, 'note': 'Запуск не подтверждён; стоп сохранён.'}
    return {**after, 'ok': True, 'action': 'start', 'accepted': True,
            'note': 'Пракс запускается. Готовность появится после восстановления.'}


async def cancel_run(tree, run_id):
    tree = Path(tree)
    if not RUN_ID.fullmatch(str(run_id)):
        return {'ok': False, 'note': 'Некорректный идентификатор хода.'}
    files = list((tree / 'memory/runs').glob('*/' + run_id + '/manifest.json'))
    if len(files) != 1 or files[0].is_symlink():
        return {'ok': False, 'note': 'Ход не найден однозначно.'}
    manifest = read(files[0])
    if not manifest:
        return {'ok': False, 'note': 'Не удалось прочитать состояние хода.'}
    # A terminal manifest alone does not prove that a previously cancelled
    # provider/process has exited. Always request the resource-owner receipt.
    path = tree / 'memory/.control/run-cancel' / (run_id + '.json')
    request = read(path)
    if request.get('run_id') != run_id or request.get('action') != 'cancel':
        request = {'id': secrets.token_hex(12), 'run_id': run_id, 'action': 'cancel',
                   'by': 'desk:owner', 'at': time.time(), 'reason': 'immediate reset from MiniApp'}
        write(path, request)
    receipt = path.with_suffix('.receipt')
    end = time.monotonic() + 2
    while time.monotonic() < end:
        result = read(receipt)
        if result.get('request_id') == request['id'] and result.get('ok') is True:
            return {'ok': result.get('ok') is True, 'accepted': True,
                'confirmed': result.get('ok') is True, 'receipt': result,
                'note': 'Исполнение хода прервано. Совершённые действия сохранены; '
                        'неизвестные исходы ждут сверки.' if result.get('ok') else
                        'Просьба записана, но завершение шага не подтверждено.'}
        await asyncio.sleep(.05)
    return {'ok': True, 'accepted': True, 'confirmed': False,
            'note': 'Сброс запрошен. Завершение шага пока не подтверждено.'}
