"""Local Forge search effect, shared by direct and owned process routes.

The parent retains task/root authority and output projection. No agent import,
live client, mutation locks, new search caps or retry policy belongs here.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
import sys


def execute(request, *, runner=None):
    root, p = Path(request['root']), Path(request['path'])
    query, glob = request['query'], request['glob']
    rg = request.get('rg')
    if rg:
        cmd = [rg, '--line-number', '--no-heading', '--color', 'never', '-g', glob or '**/*',
               '--', query, str(p if p.is_dir() else p.parent)]
        try:
            if runner:r = runner(cmd, timeout=40)
            else:
                raw = subprocess.run(cmd, capture_output=True, timeout=40)
                decode = lambda value:(value or b'').decode('utf-8','replace').replace('\r\n','\n')
                r = subprocess.CompletedProcess(raw.args,raw.returncode,decode(raw.stdout),decode(raw.stderr))
            out = r.stdout or ''
            if r.returncode not in (0, 1):out += r.stderr or ''
            return {'content':out.strip() or 'Ничего не найдено.', 'cap':20000,
                    'outcome':'ok' if r.returncode in (0, 1) else 'failed'}
        except Exception as exc:
            return {'content':f'Поиск упал: {type(exc).__name__}: {exc}', 'outcome':'failed'}
    try:rx = re.compile(query)
    except re.error as exc:return {'content':f'Плохой regex: {exc}', 'outcome':'refused'}
    hits = []
    base = p if p.is_dir() else p.parent
    for file in base.glob(glob or '**/*'):
        if not file.is_file() or any(x in request['skip_dirs'] for x in file.parts):continue
        try:
            for no, line in enumerate(file.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
                if rx.search(line):
                    hits.append(f'{file.relative_to(root)}:{no}: {line[:220]}')
                    if len(hits) >= 200:break
        except OSError:pass
        if len(hits) >= 200:break
    return {'content':'\n'.join(hits) or 'Ничего не найдено.', 'outcome':'ok'}


if __name__ == '__main__':
    request = json.load(sys.stdin)
    sys.stdout.write(json.dumps(execute(request), ensure_ascii=False))
