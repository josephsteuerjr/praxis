"""Installation-wide emergency stop; independent of the agent's turn or UI.

Windows uses the authenticated service pipe, with native CLI as a fallback.
POSIX writes the same durable latch read by the native supervisor. A stop
request never releases an existing latch and never offers a remote resume.
"""
from pathlib import Path
import json
import os
import subprocess
import time

import owner_stop
WINDOWS = os.name == 'nt'


def installation(config_path: Path) -> Path:
    for root in (config_path.parent, *config_path.parents):
        if (root / 'helene-svc.exe').is_file():
            return root
    raise RuntimeError('Не найден нативный стоп-кран рядом с установкой Hélène.')


def request(config_path: Path, *, via: str, reason: str = '') -> dict:
    via = via if via in {'agent', 'telegram', 'phone', 'window', 'cli'} else 'cli'
    if owner_stop.stopped():
        return {'ok': True, 'latched': True, 'note': 'Стоп уже включён; автоматического запуска не будет.'}
    if not WINDOWS:
        path = owner_stop.seed_file()
        if path is None:
            raise RuntimeError('Не определён дом владельца для стоп-крана.')
        if os.environ.get('HELENE_SUPERVISOR') == 'serverboot':
            import secrets
            tree=Path(os.environ['HELENE_TREE'])
            folder=tree/'memory/.control';folder.mkdir(parents=True,exist_ok=True)
            pending=folder/'panic.json'
            temp=folder/('panic-'+secrets.token_hex(6)+'.tmp')
            try:
                with temp.open('x',encoding='utf-8') as target:
                    json.dump({'via':via,'at':time.time()},target)
                    target.flush();os.fsync(target.fileno())
                os.replace(temp,pending)
            finally:temp.unlink(missing_ok=True)
            deadline=time.monotonic()+6
            while not owner_stop.stopped() and time.monotonic()<deadline:
                time.sleep(.05)
            if not owner_stop.stopped():raise RuntimeError('Надзор сервера ещё не подтвердил аварийный стоп.')
            return {'ok':True,'latched':True,'note':'Аварийный стоп сохранён на сервере. Перезапуск контейнера не возобновит агента; запуск — явной командой владельца на сервере.'}
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('x', encoding='utf-8') as target:
                json.dump({'at': time.time(), 'via': via,
                           'by': 'agent' if via == 'agent' else 'owner', 'reason': reason[:200]}, target)
                target.flush()
                os.fsync(target.fileno())
        except FileExistsError:
            pass
    else:
        import broker
        root = installation(Path(config_path))
        cfg = json.loads((root / 'helene.json').read_text(encoding='utf-8-sig'))
        tree = Path(cfg.get('tree') or 'data')
        if not tree.is_absolute():
            tree = root / tree
        token = broker.read_token(tree)
        accepted = False
        if token:
            try:
                reply = broker.call_service(broker.pipe_name(root), {
                    'v': broker.V, 'token': token, 'op': 'panic', 'args': [via],
                    'why': 'Аварийная остановка Hélène', 'timeout_sec': 5}, 6)
                accepted = reply.get('ok') is True
            except OSError:
                pass
        if not accepted:
            done = subprocess.run([str(root / 'helene-svc.exe'), 'panic', '--via', via],
                                  capture_output=True, timeout=10, creationflags=0x08000000)
            if done.returncode:
                raise RuntimeError('Служба не приняла стоп. Используй ярлык «Hélène — аварийный стоп» или helene.exe --panic.')
    if not owner_stop.stopped():
        raise RuntimeError('Стоп-флаг ещё не подтверждён; прекращение работы не объявлено.')
    return {'ok': True, 'latched': True,
            'note': 'Аварийный стоп включён. Надзор завершает все агентские процессы; запуск возможен только явным действием владельца.'}


def state() -> dict:
    return {'stopped': owner_stop.stopped(), 'resume_remote': False}
