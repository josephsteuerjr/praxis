"""Return a transferred agent from its server: fresh memory, code, projects and Git.

`server_transfer.recover_local` only stops the server copy; the local memory then
is the snapshot from before the move. This module brings the CURRENT agent back:

  started    -> fenced      transfer_recover stops THIS deployment and writes the
                            durable server fence (a late installer cannot start it)
  fenced     -> snapshot    transfer_return archives data/code/app/projects under
                            the installer lock while every owned container is stopped
  snapshot   -> downloaded  SFTP download, resumable after a broken connection,
                            outer SHA-256 equal to the server receipt
  downloaded -> staged      carry_payload.stage into a NEW permanent home beside the
                            retained data; carry_git makes worktrees independent
  staged     -> activating  the server version must not be newer than this programme
  activating -> done        code edits merged three-way onto the configured local
                            code; the receipt moves into the new data with phase
                            server-stopped; config.tree switches to the new data;
                            the old data keeps a retired receipt and stays fenced

The receipt row (server_transfer.receipt_path) records every phase before the
next side effect, so a retry continues instead of starting over. Nothing is
deleted: the server installation, the previous local data, the downloaded archive
and a copy of the local code before the merge all remain. The local agent stays
stopped; the owner resumes it separately after looking at the result.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import tempfile
import time
import zipfile

from . import carry_git, carry_payload, carry_scope
from . import server_transfer as transfer
from .carry_inventory import file_sha256
from .private_files import write_private

PHASES = ('started', 'fenced', 'snapshot', 'downloaded', 'staged', 'activating', 'done')
RETURNABLE = ('installing', 'connecting', 'ready', 'server-stopped')
HELPER = (('server', 'transfer_return.py', 'transfer_return.py'),
          ('server', 'transfer_recover.py', 'transfer_recover.py'),
          ('localharness', 'carry_inventory.py', 'localharness/carry_inventory.py'),
          ('localharness', 'carry_scope.py', 'localharness/carry_scope.py'),
          ('localharness', 'carry_payload.py', 'localharness/carry_payload.py'),
          ('localharness', 'carry_git.py', 'localharness/carry_git.py'),
          ('localharness', 'private_files.py', 'localharness/private_files.py'))


class ReturnError(transfer.TransferError):
    pass


# --- local layout ---------------------------------------------------------------------

def _config(config_path):
    return carry_scope.read_config(config_path)


def _data(config_path):
    return carry_scope.configured_roots(config_path, _config(config_path))['data']


def places(config_path, identity, attempt=1):
    """Beside the retained data: the download folder and the new permanent home."""
    data = _data(config_path)
    stem = data.name + '-r' + identity[:8]
    home = data.parent / (stem if attempt == 1 else stem + '-' + str(attempt))
    return {'payload': data.parent / (stem + '.payload'), 'home': home}


def _version(program):
    try:
        return str(json.loads((Path(program) / 'helene-build.json').read_text(encoding='utf-8-sig')).get('version') or '')
    except (OSError, ValueError):
        return ''


def _version_key(value):
    match = re.fullmatch(r'\s*v?(\d+(?:\.\d+)*)(?:[-+].*)?\s*', str(value or ''))
    return tuple(int(part) for part in match.group(1).split('.')) if match else None


def compatible(server_version, local_version):
    """Newer server memory needs a programme at least as new; never guess."""
    server, local = _version_key(server_version), _version_key(local_version)
    if server is None or local is None:
        return server_version == local_version and bool(server_version)
    width = max(len(server), len(local))
    return server + (0,) * (width - len(server)) <= local + (0,) * (width - len(local))


def _save(config_path, row):
    transfer.save_transfer(config_path, row)


def _record(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, (json.dumps(row, ensure_ascii=False) + '\n').encode())


# --- remote steps ---------------------------------------------------------------------

def _source(kind, name):
    here = Path(__file__).resolve()
    places_ = ((here.parent / name,) if kind == 'localharness' else
               (here.parents[1] / 'server' / name, here.parents[2] / 'server' / name))
    for path in places_:
        if path.is_file():
            return path
    raise ReturnError('В поставке нет помощника возврата: ' + name)


def remote_stage(row, identity):
    return str(row['deployment']['directory']) + '.return-' + identity


def upload_helper(client, stage):
    helper = stage + '/helper'
    transfer.execute(client, 'umask 077; mkdir -p -m 700 ' + shlex.quote(helper + '/localharness')
                     + '; test ! -L ' + shlex.quote(stage) + ' && test ! -L ' + shlex.quote(helper))
    expected = {}
    with client.open_sftp() as sftp:
        for kind, name, target in HELPER:
            path = _source(kind, name)
            sftp.put(str(path), helper + '/' + target)
            expected[helper + '/' + target] = hashlib.sha256(path.read_bytes()).hexdigest()
    reported = transfer.execute(client, 'sha256sum ' + ' '.join(shlex.quote(p) for p in expected))[1]
    seen = {}
    for line in reported.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            seen[parts[1].strip()] = parts[0]
    if seen != expected:
        raise ReturnError('Помощник возврата повредился при передаче. Сервер не изменён; повтори.')
    code, value, _ = transfer.execute(client, 'python3 -c ' + shlex.quote('import sys;print(sys.version_info[:2]>=(3,9))'), check=False)
    if code or value.strip() != 'True':
        raise ReturnError('Для возврата на сервере нужен python3 версии 3.9 или новее.')
    return helper


def helper_call(client, helper, request, *, timeout=3600):
    command = 'cd ' + shlex.quote(helper) + ' && python3 -I transfer_return.py'
    _, value, _ = transfer.execute(client, command, timeout=timeout, check=False,
                                   input_data=json.dumps(request, ensure_ascii=False))
    try:
        reply = json.loads(value.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ReturnError('Сервер не ответил на шаг возврата понятным результатом. Данные на месте; повтори.') from None
    if reply.get('ok') is not True:
        raise ReturnError('Сервер отказал в шаге возврата: ' + str(reply.get('message') or 'причина не сообщена'))
    return reply


def fence(client, helper, row):
    command = 'python3 -I ' + shlex.quote(helper + '/transfer_recover.py') + ' ' + shlex.quote(json.dumps(row))
    _, value, _ = transfer.execute(client, command, timeout=240, check=False)
    try:
        reply = json.loads(value.strip().splitlines()[-1])
    except (ValueError, IndexError):
        reply = {}
    if reply.get('ok') is not True:
        raise ReturnError(reply.get('message') or 'Сервер не подтвердил остановку своей копии. Местный Stop сохранён.')
    return reply


def download(client, remote, size, digest, folder, *, progress=None):
    """Resume a partial file by offset; accept only the exact server bytes."""
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    final, part = folder / 'payload.zip', folder / 'payload.zip.part'
    if final.is_file():
        with final.open('rb') as stream:
            if file_sha256(stream) == digest:
                return final
        final.rename(folder / ('payload-mismatch-' + secrets.token_hex(4) + '.zip'))
    with client.open_sftp() as sftp:
        if sftp.stat(remote).st_size != size:
            raise ReturnError('Размер архива на сервере не совпал с его квитанцией. Повтори снимок.')
        offset = part.stat().st_size if part.is_file() else 0
        if offset > size:
            part.rename(folder / ('payload-oversized-' + secrets.token_hex(4) + '.part'))
            offset = 0
        if not part.exists():
            write_private(part, b'')
        last = 0.0
        with sftp.open(remote, 'rb') as source, part.open('r+b') as target:
            target.seek(offset)
            source.seek(offset)
            if offset < size:
                # Bounded read-ahead: an unbounded prefetch queues every 32 KiB
                # request of a multi-gigabyte archive at once.
                source.prefetch(size, max_concurrent_requests=64)
            while offset < size:
                block = source.read(min(4 * 1024 * 1024, size - offset))
                if not block:
                    raise ReturnError('Связь оборвалась при скачивании. Повтори: загрузка продолжится с места обрыва.')
                target.write(block)
                offset += len(block)
                if progress and time.monotonic() - last > 1:
                    target.flush()
                    os.fsync(target.fileno())
                    progress(offset, size)
                    last = time.monotonic()
            target.flush()
            os.fsync(target.fileno())
    with part.open('rb') as stream:
        received = file_sha256(stream)
    if received != digest:
        # Windows cannot rename an open file; the stream is closed above.
        part.rename(folder / ('payload-corrupt-' + secrets.token_hex(4) + '.part'))
        raise ReturnError('Скачанный архив не совпал с сервером. Повтори возврат: он скачается заново.')
    os.replace(part, final)
    return final


# --- local steps ----------------------------------------------------------------------

def stage(config_path, state, archive, *, git=None):
    """Use the recorded home if it is ready for this archive, else a fresh one."""
    attempt = int(state.get('attempt') or 1)
    while True:
        home = places(config_path, state['id'], attempt)['home']
        ready = home / '.payload-ready.json'
        if ready.is_file():
            receipt = json.loads(ready.read_text(encoding='utf-8'))
            if receipt.get('sha256') == state['remote']['sha256'] and not receipt.get('activated'):
                break
        if not home.exists() and not home.is_symlink():
            state['attempt'] = attempt
            state['home'] = str(home)
            carry_payload.stage(archive, home, expected_sha256=state['remote']['sha256'])
            break
        attempt += 1
    state['attempt'] = attempt
    state['home'] = str(home)
    meta = metadata(archive)  # stage() verified the outer SHA and every member
    records = meta.get('git') or []
    if records:
        carry_git.rebind(home, records, git=git)
    return home, meta


def metadata(archive):
    """The plan of an archive already verified when it was staged into the home."""
    with zipfile.ZipFile(archive) as zf:
        return json.loads(zf.read(carry_payload.META))['metadata']


def _forward_edits(row):
    """Code edits as they left this computer: {path: bytes|None}, version."""
    archive = Path(str(row.get('archive') or ''))
    if not archive.is_file():
        return {}, ''
    try:
        with zipfile.ZipFile(archive) as zf:
            manifest = json.loads(zf.read('code/manifest.json').decode('utf-8'))
            if manifest.get('whole'):
                return {}, str(manifest.get('version') or '')
            names = set(zf.namelist())
            edits = {}
            for path in manifest.get('edited') or []:
                name = 'code/mine/' + str(path)
                edits[str(path)] = zf.read(name) if name in names else None
            return edits, str(manifest.get('version') or '')
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}, ''


def _base(cc, home, meta, row, program, work):
    """Code as it was when the agent left, in server-version terms.

    The clean copy of the server version, plus the edits that travelled with the
    agent (when the server still runs that version). Then a revert made on the
    server is a change too, and edits made here meanwhile are preserved.
    -> (directory or None, hash snapshot or None, kind)
    """
    version = str(meta.get('version') or '')
    roots = home / 'roots'
    if (roots / 'departure' / 'tree').is_dir():
        # The verified transfer kit: exactly the code that left this computer.
        base = work / 'base'
        shutil.copytree(roots / 'departure', base, ignore=shutil.ignore_patterns('__pycache__', '.kit-signature'))
        return base, None, 'departure'
    base = None
    if (roots / 'base' / 'tree').is_dir():
        base = work / 'base'
        shutil.copytree(roots / 'base', base, ignore=shutil.ignore_patterns('__pycache__'))
    else:
        for candidate in (roots / 'pristine' / (version + '.zip'), cc.pristine_path(program, version)):
            if version and candidate.is_file():
                opened = cc.open_pristine(candidate, work / 'opened')
                if opened is not None:
                    base = work / 'base'
                    shutil.copytree(opened, base)
                    break
    edits, left = _forward_edits(row)
    overlay = edits if left == version else {}
    if base is not None:
        for path, blob in overlay.items():
            target = base / path
            if blob is None:
                if target.is_file():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
        return base, None, 'pristine+departure' if overlay else 'pristine'
    prints = cc.shipped_prints(version, cc.flavor_of(program)) if version else None
    if not prints:
        return None, None, ''
    table = dict(prints)
    for path, blob in overlay.items():
        if blob is None:
            table.pop(path, None)
        else:
            table[path] = hashlib.sha256(blob).hexdigest()
    return None, table, 'prints+departure' if overlay else 'prints'


def merge_code(config_path, program, row, home, meta, new_data, folder):
    """Server edits -> the configured local code, three-way; full copy stays in home."""
    cc = transfer.carry._codecarry()
    roots = home / 'roots'
    cfg = _config(config_path)
    local = carry_scope.configured_roots(config_path, cfg)
    targets = (('tree', roots / 'code', local['code']), ('app', roots / 'app', local['application']))
    if cc is None:
        return {'note': 'переносчика правок кода нет в этой установке; серверный код целиком в ' + str(roots)}
    backup = folder / 'local-code-before'
    if not backup.exists():
        temporary = folder / ('local-code-before.part-' + secrets.token_hex(4))
        for name, _, target in targets:
            if target.is_dir():
                shutil.copytree(target, temporary / name, symlinks=True,
                                ignore=shutil.ignore_patterns('__pycache__'))
        temporary.mkdir(parents=True, exist_ok=True)
        temporary.rename(backup)
    version = str(meta.get('version') or '')
    here = _version(program)
    work = Path(tempfile.mkdtemp(prefix='helene-return-code-'))
    try:
        base, prints, kind = _base(cc, home, meta, row, program, work)
        report = {'base': kind, 'edited': [], 'carried': [], 'merged': [], 'conflicts': [], 'skipped': [],
                  'server_code': str(roots / 'code'), 'server_app': str(roots / 'app'), 'local_before': str(backup)}
        if not kind:
            report['summary'] = ('сравнить серверный код было не с чем: местный код не менялся, '
                                 'серверный код целиком сохранён в ' + str(roots))
            return report
        materials, diffs, budget = [], [], [cc.MAX_MATERIALS]
        labels = ('агент на сервере (' + version + ')', 'при переезде (' + version + ')', 'здесь ' + (here or '?'))
        for name, mine, target in targets:
            if not mine.is_dir() or not target.is_dir():
                report['skipped'].append({'path': name, 'why': 'папки кода нет на одной из сторон'})
                continue
            rep = cc.carry_code(base / name if base is not None else None, mine, target,
                                work=work / ('merge-' + name), labels=labels, prefix=name, budget=budget,
                                base_snap=cc.hashes_snapshot(prints, name) if prints else None,
                                skip=tuple(cc.DESK_SKIP[name]))
            for key in ('edited', 'carried', 'merged', 'conflicts', 'skipped'):
                report[key].extend(rep[key])
            materials.extend(rep['materials'])
            diffs.extend(rep['diff'])
        if report['edited']:
            target = cc.materials_folder(new_data, version or '?', tag='return')
            readme = cc.materials_readme(report, version, here or 'эту установку').replace('# Обновление', '# Возвращение', 1)
            cc._put(target, ['README.md'], (readme + '\n\nПолный серверный код: `' + str(roots / 'code') + '` и `'
                                            + str(roots / 'app') + '`.\n').encode('utf-8'))
            cc._put(target, ['edits.diff'], ''.join(diffs).encode('utf-8')[:cc.MAX_DIFF])
            for row_ in materials:
                parts = row_['path'].split('/')
                for side in ('mine', 'base', 'theirs', 'merged'):
                    blob = row_.get(side)
                    if blob is not None:
                        cc._put(target, parts[:-1] + [parts[-1] + '.' + side], blob)
            report['folder'] = 'workspace/' + target.name
        report['summary'] = ('правок агента в коде: ' + str(len(report['edited'])) + '; перенесено '
                             + str(len(report['carried'])) + ', слито ' + str(len(report['merged']))
                             + ', не легло ' + str(len(report['conflicts'])))
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def activate(config_path, program, row, state, home, meta):
    """Commit point: config.tree -> the returned data. Retries converge."""
    server_version = str(meta.get('version') or '')
    local_version = _version(program)
    if not compatible(server_version, local_version):
        raise ReturnError('Агент на сервере работал на версии ' + (server_version or 'неизвестной')
                          + ', а здесь ' + (local_version or 'неизвестная') + '. Сначала обнови программу на '
                          'этом компьютере; скачанный возврат сохранён и продолжится после обновления.')
    old_receipt = transfer.receipt_path(config_path)
    folder = Path(places(config_path, state['id'])['payload'])
    new_data = home / 'roots' / 'data'
    if not new_data.is_dir():
        raise ReturnError('В подготовленной копии нет данных агента. Ничего не переключено.')
    state['phase'] = 'activating'
    row['return'] = state
    _save(config_path, row)
    code = merge_code(config_path, program, row, home, meta, new_data, folder)
    local = _config(config_path)
    before = folder / 'config-before.json'
    if not before.exists():
        _record(before, local)
    returned = {'id': state['id'], 'at': time.time(), 'from': row.get('destination'), 'version': server_version,
                'home': str(home), 'previous_data': str(_data(config_path)), 'previous_receipt': str(old_receipt),
                'previous_config': str(before), 'archive': str(folder / 'payload.zip'),
                'sha256': state['remote']['sha256'], 'code': code,
                'exclusions': state['remote'].get('exclusions', []), 'projects': state.get('projects', [])}
    fresh = {key: value for key, value in row.items() if key != 'return'}
    fresh.update(phase='server-stopped', returned=returned)
    _record(new_data / 'memory/.state/server-transfer.json', fresh)
    merged = transfer.carry.merge_config(local, meta.get('config') or {})
    merged['tree'] = str(new_data)
    # The window watched the server copy; the agent now lives here again.
    merged['mode'] = 'local'
    for key in ('base', 'key', 'server_locator'):
        merged.pop(key, None)
    transfer.carry._write_config(Path(config_path), merged)
    retire(old_receipt, row, state)
    return {'ok': True, 'phase': 'done', 'home': str(home), 'data': str(new_data), 'code': code,
            'previous_data': returned['previous_data'], 'restart': 'application',
            'message': 'Агент вернулся: память, код и проекты с сервера на месте. Прежняя копия и архив '
                       'сохранены. Перезапусти программу и возобнови агента, когда проверишь.'}


def retire(path, row, state):
    """The pre-move data stays readable but can never start as the live agent."""
    state['phase'] = 'done'
    retired = {**row, 'phase': 'returned-retired', 'return': state}
    _record(Path(path), retired)


# --- orchestration --------------------------------------------------------------------

def _connect(raw, row):
    # The host key pinned at the move is required; a different key never authenticates.
    return transfer.connect({**raw, 'fingerprint': row['fingerprint']})


def _require(raw, config_path):
    row = transfer.saved_transfer(config_path)
    if not row:
        raise ReturnError('Этот агент не переезжал на сервер из этой программы.')
    if row.get('returned'):
        raise ReturnError('Агент уже вернулся с этого сервера. Прежние данные и архив сохранены.')
    if not transfer.same_destination(row, {**raw, 'fingerprint': raw.get('fingerprint') or row.get('fingerprint')}):
        raise ReturnError('Укажи тот же сервер, порт, логин и параметры установки, что при переезде.')
    if row.get('phase') == 'archive':
        raise ReturnError('Агент не успел переехать: на сервере его нет. Останови серверную копию для возврата — '
                          'местная память и так актуальна.')
    if row.get('phase') not in RETURNABLE and not row.get('return'):
        raise ReturnError('Состояние переезда не позволяет возврат: ' + str(row.get('phase')))
    return row


def scope(raw, config_path, program):
    """Read-only review before anything stops: roots, projects, exclusions."""
    with transfer.custody(config_path):
        row = _require(raw, config_path)
        identity = (row.get('return') or {}).get('id') or secrets.token_hex(8)
        with contextlib.closing(_connect(raw, row)) as client:
            helper = upload_helper(client, remote_stage(row, identity))
            reply = helper_call(client, helper, {'action': 'scope', 'row': _row_for_server(row)}, timeout=600)
        reply['return_id'] = identity
        reply['local_version'] = _version(program)
        reply['compatible'] = compatible(reply.get('version'), reply['local_version'])
        return reply


def _row_for_server(row):
    return {key: row[key] for key in ('id', 'archive_sha', 'phase', 'deployment', 'program_sha') if key in row}


def run(raw, config_path, program, *, projects=(), progress=None):
    config_path, program = Path(config_path), Path(program)
    with transfer.custody(config_path):
        row = _require(raw, config_path)
        if not transfer.owner_stop.stopped():
            raise ReturnError('Сначала включи Stop в приложении: возврат не запускает агента сам.')
        projects = [dict(item) for item in projects]
        state = row.get('return') or {}
        if not state:
            state = {'id': secrets.token_hex(8), 'phase': 'started', 'projects': projects}
        elif state.get('projects') != projects:
            if PHASES.index(state['phase']) >= PHASES.index('downloaded'):
                raise ReturnError('Возврат уже скачан с прежним выбором проектов. Заверши его или отмени и начни заново.')
            state['projects'] = projects
            if state['phase'] == 'snapshot':
                state['phase'] = 'fenced'
        row['return'] = state
        _save(config_path, row)

        def note(phase, **extra):
            state.update(phase=phase, **extra)
            row['return'] = state
            _save(config_path, row)

        def moved(done, total):
            state['progress'] = {'done': done, 'total': total, 'at': time.time()}
            row['return'] = state
            _save(config_path, row)
            if progress:
                progress(done, total)

        if PHASES.index(state['phase']) < PHASES.index('downloaded'):
            with contextlib.closing(_connect(raw, row)) as client:
                stage_dir = remote_stage(row, state['id'])
                helper = upload_helper(client, stage_dir)
                if state['phase'] == 'started':
                    fence(client, helper, _row_for_server(row))
                    note('fenced')
                if state['phase'] == 'fenced':
                    reply = helper_call(client, helper, {'action': 'snapshot', 'row': _row_for_server(row),
                                                         'plan': {'return_id': state['id'], 'projects': projects}})
                    free = shutil.disk_usage(_data(config_path).parent).free
                    if free < reply['bytes'] * 3:
                        raise ReturnError('Для возврата нужно около ' + str(reply['bytes'] * 3 // 2 ** 20)
                                          + ' МБ свободного места рядом с данными агента. Сервер остановлен, данные на месте.')
                    note('snapshot', remote={k: reply[k] for k in ('archive', 'sha256', 'bytes', 'version', 'exclusions',
                                                                  'roots', 'links')})
                if state['phase'] == 'snapshot':
                    remote = state['remote']
                    try:
                        download(client, remote['archive'], remote['bytes'], remote['sha256'],
                                 places(config_path, state['id'])['payload'], progress=moved)
                    except (OSError, EOFError, transfer.paramiko.SSHException):
                        raise ReturnError('Связь с сервером оборвалась при скачивании. Повтори возврат: '
                                          'загрузка продолжится с места обрыва.') from None
                    state.pop('progress', None)
                    note('downloaded')
        archive = places(config_path, state['id'])['payload'] / 'payload.zip'
        cc = transfer.carry._codecarry()
        git = cc._bundled_git(program) if cc is not None else None
        if state['phase'] == 'downloaded':
            home, meta = stage(config_path, state, archive, git=shutil.which(git) if git else None)
            note('staged')
        home = Path(state['home'])
        meta = metadata(archive)
        if state['phase'] in ('staged', 'activating'):
            return activate(config_path, program, row, state, home, meta)
        raise ReturnError('Неизвестное состояние возврата: ' + str(state['phase']))


def cancel(config_path):
    """Forget an unfinished return before activation; server stays fenced, bytes stay."""
    with transfer.custody(config_path):
        row = transfer.saved_transfer(config_path)
        state = row.get('return') or {}
        if not state:
            return {'ok': True, 'cancelled': False}
        if PHASES.index(state['phase']) >= PHASES.index('activating'):
            raise ReturnError('Переключение уже началось; его можно только завершить.')
        history = transfer.receipt_path(config_path).parent / 'server-return-history'
        _record(history / (state['id'] + '.json'), state)
        row.pop('return', None)
        _save(config_path, row)
        return {'ok': True, 'cancelled': True,
                'message': 'Незавершённый возврат отменён. Серверная копия остаётся остановленной; '
                           'скачанное сохранено.' if state['phase'] != 'started' else 'Возврат отменён.'}


def status(config_path):
    row = transfer.saved_transfer(config_path)
    state = row.get('return') or {}
    if row.get('returned'):
        return {'ok': True, 'returned': True, 'details': row['returned']}
    return {'ok': True, 'returned': False, 'phase': state.get('phase'), 'progress': state.get('progress'),
            'projects': state.get('projects', []), 'id': state.get('id')}
