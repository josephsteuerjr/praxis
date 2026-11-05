"""Validated deployment identity; no command text or credentials are accepted."""
from pathlib import PurePosixPath
import re


def destination(raw):
    value = raw.get('deployment') or {}
    if not isinstance(value, dict): raise ValueError('Проверь параметры установки сервера')
    directory = str(value.get('directory') or '/opt/helene')
    project = str(value.get('project') or 'helene')
    container = str(value.get('container') or project)
    try: port = int(value.get('port') or 8094)
    except (ValueError, TypeError): raise ValueError('Порт канала должен быть числом') from None
    path = PurePosixPath(directory)
    if (not directory.startswith(('/opt/', '/srv/')) or str(path) != directory
            or any(p in {'.', '..'} for p in path.parts)
            or not re.fullmatch(r'/[A-Za-z0-9_./-]+', directory)
            or len(path.parts) < 3 or len(directory) > 180):
        raise ValueError('Выбери отдельную папку установки внутри /opt или /srv')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,47}', project):
        raise ValueError('Имя проекта: строчные латинские буквы, цифры, дефис')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,55}', container) or not 1024 <= port <= 65535:
        raise ValueError('Проверь имя контейнера и свободный порт канала (1024–65535)')
    return {'directory': directory, 'project': project, 'container': container, 'port': port}


def accepted(state, supervisor, panic, expected):
    """A live owner channel with a fresh server supervisor; Stop is success."""
    if not isinstance(state, dict) or not isinstance(supervisor, dict) or not isinstance(panic, dict): return False
    if not state.get('agent') or (state.get('desk') or {}).get('version') != expected: return False
    if supervisor.get('kind') != 'serverboot' or supervisor.get('alive') is not True: return False
    children = {r.get('id'): r for r in supervisor.get('children', []) if isinstance(r, dict)}
    if (children.get('channel') or {}).get('alive') is not True: return False
    if panic.get('stopped') is True:
        return all(r.get('alive') is False for k, r in children.items() if k != 'channel')
    return panic.get('stopped') is False and (state.get('runner') or {}).get('alive') is True
