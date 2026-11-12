"""Root-side fresh snapshot of a transferred agent for its return. Removes nothing.

Uploaded together with the carry modules and run by the host python3 (3.9+),
never by the installed programme, which may predate this protocol. Input is one
JSON object on stdin; output is one JSON object on stdout.

  scope    read-only: configured roots, external project candidates, version.
  snapshot after transfer_recover fenced and stopped THIS deployment: under the
           installer flock, confirm the fence and that every owned container is
           still stopped, then archive data/code/application, the pristine code
           bases and the projects the owner chose. Host-bound paths are explicit
           exclusions with reasons; an unchosen external link is excluded with
           the owner's decision as its reason. Nothing is skipped silently.

The archive lives in <installation>.return-<id>/, outside every archived root.
A repeated snapshot returns the existing receipt while the fence still holds.
"""
from pathlib import Path
import fcntl
import json
import os
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from localharness import carry_git, carry_payload, carry_scope  # noqa: E402
from localharness.private_files import write_private  # noqa: E402
import transfer_recover as ownership  # noqa: E402

FORMAT = 'helene.return.v1'

#: Paths of the server data folder that belong to that host, not to the agent.
HOST_DATA = (
    ('.serverboot', 'ключ окна и надзор принадлежат серверу'),
    ('.channel', 'канал окна этого сервера'),
    ('.owner-stop', 'остановка владельцем относится к серверной копии'),
    ('body', 'тело компьютера живёт на своей машине'),
    ('models', 'модели голоса — снаряжение машины, скачиваются заново'),
    ('.helene-component-pins.json', 'закрепления компонентов этой машины'),
    ('relay/logs', 'журналы серверного реле'),
    ('memory/.state/harness.lock', 'замок дерева серверного процесса'),
    ('memory/.state/desk-token', 'ключ окна сервера'),
    ('memory/.state/body.json', 'привязка тела сервера'),
    ('memory/.state/phone-access', 'туннель и кнопка Mini App сервера'),
    ('memory/.state/server-transfer.json', 'квитанция переноса ведётся на компьютере'),
    ('memory/.state/server-transfer.lock', 'замок переноса'),
    ('memory/.state/server-transfer-history', 'история переносов ведётся на компьютере'),
    ('memory/.state/server-transfer-archives', 'архивы переносов ведутся на компьютере'),
)


def installation(row):
    root = ownership.canonical(row)
    if not (root / 'helene.json').is_file():
        raise ValueError('installation is missing')
    if not ownership.claimed(row, root):
        raise ValueError('installation ownership mismatch')
    return root


def version(root):
    try:
        return str(json.loads((root / 'helene-build.json').read_text(encoding='utf-8')).get('version') or '')
    except (OSError, ValueError):
        return ''


def scope(row):
    root = installation(row)
    found = carry_scope.discover(root / 'helene.json')
    for item in found['roots']:
        # Counts and problems only: listing every file name is neither needed nor private.
        item['links'] = [{'path': link['path'], 'target': link['target'], 'error': link['error']}
                         for link in item['links']]
        item['empty_directories'] = len(item['empty_directories'])
    return {'ok': True, 'format': FORMAT, 'version': version(root), 'scope': found,
            'host_exclusions': [{'path': path, 'reason': reason} for path, reason in HOST_DATA]}


def _project_name(index):
    return 'project-' + str(index)


def _plan_roots(root, plan, found):
    config = carry_scope.read_config(root / 'helene.json')
    standard = carry_scope.configured_roots(root / 'helene.json', config)
    roots = {'data': standard['data'], 'code': standard['code']}
    if standard['application'].is_dir():
        roots['app'] = standard['application']
    current = version(root)
    # Merge bases for code edits: the clean copy the server updater keeps.
    base = root / '.updater' / 'pristine' / current
    if current and base.is_dir() and not base.is_symlink():
        roots['base'] = base
    pristine = root / 'pristine'
    if pristine.is_dir() and not pristine.is_symlink():
        roots['pristine'] = pristine
    known = {os.path.normcase(str(Path(item['path']).resolve())): item for item in found['projects']}
    chosen = {}
    decisions = plan.get('projects') or []
    if not isinstance(decisions, list):
        raise ValueError('invalid project plan')
    for decision in decisions:
        if not isinstance(decision, dict) or not isinstance(decision.get('path'), str):
            raise ValueError('invalid project decision')
        key = os.path.normcase(str(Path(decision['path']).resolve()))
        if key not in known:
            raise ValueError('project is not part of the reviewed scope: ' + decision['path'])
        if decision.get('bring') is True:
            chosen[key] = Path(known[key]['path'])
    missing = [item['path'] for key, item in known.items() if not any(
        os.path.normcase(str(Path(d.get('path', '')).resolve())) == key for d in decisions if isinstance(d, dict))]
    if missing:
        raise ValueError('every external project needs the owner decision: ' + ', '.join(missing))
    for index, path in enumerate(sorted(chosen.values(), key=str), 1):
        roots[_project_name(index)] = path
    return config, roots, current


def _exclusions(roots):
    rows = [{'root': 'data', 'path': path, 'reason': reason} for path, reason in HOST_DATA]
    selected = {name: path.resolve() for name, path in roots.items()}

    def excluded(name, rel):
        return any(row['root'] == name and (rel == row['path'] or rel.startswith(row['path'] + '/')) for row in rows)

    def placement(target):
        # The deepest selected root wins, exactly as carry_payload resolves links.
        for name, path in sorted(selected.items(), key=lambda item: len(item[1].parts), reverse=True):
            if target.is_relative_to(path):
                return name, target.relative_to(path).as_posix()
        return None
    links = []
    for name, path in roots.items():
        links.extend((name, link) for link in carry_scope.inventory(path)['links'])
    for name, link in links:
        if excluded(name, link['path']):
            continue
        if not link['target']:
            rows.append({'root': name, 'path': link['path'], 'reason': 'ссылка на сервере никуда не ведёт'})
            continue
        target = Path(link['target'])
        where = placement(target)
        if where is None:
            rows.append({'root': name, 'path': link['path'],
                         'reason': 'ссылка ведёт за пределы выбранного: проект оставлен на сервере владельцем'
                         if target.is_dir() else 'ссылка на файл вне выбранных папок'})
        elif excluded(*where):
            rows.append({'root': name, 'path': link['path'], 'reason': 'ссылка ведёт в служебную папку сервера'})
    return rows


def _stage(root, plan):
    identity = str(plan.get('return_id') or '')
    if not re.fullmatch(r'[0-9a-f]{16}', identity):
        raise ValueError('invalid return id')
    stage = Path(str(root) + '.return-' + identity)
    if stage.is_symlink() or (stage.exists() and stage.resolve() != stage):
        raise ValueError('linked return folder refused')
    stage.mkdir(mode=0o700, exist_ok=True)
    os.chmod(stage, 0o700)
    return stage


def snapshot(row, plan):
    root = installation(row)
    stage = _stage(root, plan)
    receipt_path = stage / 'snapshot.json'
    fd = os.open(root / '.install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not ownership.fenced(row, root):
            raise ValueError('the server copy is not fenced for return')
        identities = ownership.owned(row, root)
        if not ownership.stopped(identities):
            raise ValueError('the server copy is running')
        decisions = plan.get('projects') or []
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            archive = Path(receipt['archive'])
            if receipt.get('projects') == decisions and archive.is_file():
                with archive.open('rb') as stream:
                    if carry_payload.checks.file_sha256(stream) == receipt['sha256']:
                        return {**receipt, 'ok': True, 'reused': True}
        found = carry_scope.discover(root / 'helene.json')
        config, roots, current = _plan_roots(root, plan, found)
        exclusions = _exclusions(roots)
        git = carry_git.plan(roots)
        metadata = {'format': FORMAT, 'transfer_id': row['id'], 'return_id': plan['return_id'],
                    'version': current, 'config': config, 'git': git['worktrees'],
                    'roots': {name: str(path) for name, path in git['roots'].items()},
                    'unavailable': found['unavailable']}
        result = carry_payload.create(stage / 'payload.zip', git['roots'], exclusions=exclusions, metadata=metadata)
        receipt = {'format': FORMAT, 'archive': result['archive'], 'sha256': result['sha256'],
                   'bytes': (stage / 'payload.zip').stat().st_size, 'integrity': result['integrity'],
                   'roots': result['roots'], 'links': result['links'], 'exclusions': exclusions,
                   'version': current, 'stopped': identities, 'projects': decisions}
        write_private(receipt_path, (json.dumps(receipt, ensure_ascii=False) + '\n').encode())
        return {**receipt, 'ok': True, 'reused': False}
    finally:
        os.close(fd)


def main():
    request = json.loads(sys.stdin.read(1024 * 1024) or '{}')
    action = request.get('action')
    try:
        if sys.version_info < (3, 9):
            raise ValueError('python3 3.9+ is required on the server')
        if action == 'scope':
            result = scope(request['row'])
        elif action == 'snapshot':
            result = snapshot(request['row'], request.get('plan') or {})
        else:
            raise ValueError('unknown action')
    except Exception as error:  # the GUI shows the reason; nothing was changed by a failure
        result = {'ok': False, 'message': str(error) or type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
