"""An agent can resolve only the conflicts of an owner-requested source update."""
from pathlib import Path
import hashlib
import json
import time

from .package_notice import read, write
from . import source_merge as merge

TOOL = {'name': 'source_update', 'description':
    'Resolve a pending desktop source update in its isolated candidate. status lists conflicts; '
    'read returns base/local/incoming/result for one path; resolve supplies the complete UTF-8 '
    'file or explicit delete; finish checks all resolutions. Preserve both intents. Never edit '
    'the live source for this task. Finishing does not install or restart; tell the owner to retry Update.',
    'input_schema': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['status', 'read', 'resolve', 'finish']},
        'path': {'type': 'string'}, 'content': {'type': 'string'}, 'delete': {'type': 'boolean'},
        'side': {'type': 'string', 'enum': ['base', 'local', 'incoming']},
        'offset': {'type': 'integer', 'minimum': 0}}, 'required': ['action']}}


def path(tree):
    return Path(tree) / 'memory/.control/source-update.json'


def pending(tree):
    request = read(path(tree))
    return request if request.get('state') == 'pending' else None


def bound(tree):
    request = read(path(tree))
    program = Path(request['program']).resolve()
    directory = Path(request['directory']).resolve()
    expected = program / '.source-updates' / request['id']
    if directory != expected or len(request['id']) != 64:
        raise ValueError('Source update directory does not belong to this request')
    plan = read(directory / 'plan.json')
    if Path(plan['local_path']).resolve() != Path(request['source']).resolve():
        raise ValueError('Source update request identity differs')
    return request, directory, plan


def command(tree, action='status', **args):
    if not read(path(tree)):
        return {'state': 'none'}
    request, directory, plan = bound(tree)
    if action == 'status':
        return {'state': plan['state'], 'version': request['version'],
                'conflicts': plan['conflicts'], 'resolved': plan['resolved']}
    if merge.inventory(request['source']) != plan['files']['local']:
        request['state'] = 'stale'
        write(path(tree), request)
        raise ValueError('Live source changed; ask the owner to retry Update for a fresh merge')
    if action in ('read', 'resolve'):
        name = args.get('path', '')
        if name not in plan['conflicts']:
            raise ValueError('This path is not a requested conflict')
        if action == 'read':
            offset = max(0, int(args.get('offset', 0)))
            result = {}
            for side in ('base', 'local', 'incoming', 'result'):
                file = directory / side / name
                if not file.is_file():
                    result[side] = {'exists': False}
                    continue
                try:
                    text = file.read_text(encoding='utf-8')
                except UnicodeError:
                    result[side] = {'exists': True, 'binary': True}
                    continue
                result[side] = {'exists': True, 'text': text[offset:offset + 6000],
                                'offset': offset, 'total_chars': len(text)}
            return result
        merge.resolve(directory, name, args.get('content'), delete=args.get('delete') is True, side=args.get('side', ''))
    elif action == 'finish':
        merge.finish(directory, exe=merge.executable(request['program']))
        request['state'] = 'ready'
        write(path(tree), request)
    else:
        raise ValueError('Unknown source update action')
    return command(tree, 'status')


def install(agent, tree):
    def hand(action='status', **args):
        try:
            return json.dumps(command(tree, action, **args), ensure_ascii=False)
        except (OSError, ValueError, RuntimeError, SyntaxError, KeyError) as exc:
            return json.dumps({'error': str(exc)}, ensure_ascii=False)
    agent.TOOL_IMPL[TOOL['name']] = hand
    if not any(t.get('name') == TOOL['name'] for t in agent.BASE_TOOLS):
        agent.BASE_TOOLS.append(TOOL)


def notify_due(tree, turn):
    request = pending(tree)
    if not request or int(request.get('tries', 0)) >= 3 or time.time() < request.get('retry_at', 0):
        return
    request.update(tries=request.get('tries', 0) + 1, retry_at=time.time() + 120)
    write(path(tree), request)
    note = ('Системное сообщение: владелец начал обновление Hélène до ' + request['version'] +
            '. Есть пересечения с твоими правками кода. Используй source_update: status, '
            'read для трёх сторон, resolve для полного результата и finish. Сохрани смысл '
            'обеих сторон. Работай только этой рукой с подготовленной копией. Если закончишь, '
            'сообщи владельцу: можно снова нажать «Обновить». Если не можешь безопасно совместить '
            'изменения, объясни конкретную проблему; рабочий код остаётся прежним.')
    turn('source-update-' + request['id'], speaker='Hélène', origin_text=note)
