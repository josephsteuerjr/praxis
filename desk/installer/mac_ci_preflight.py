"""Check release metadata before starting the expensive macOS compilation."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from urllib.parse import quote


def check_release(release: dict, tag: str) -> str:
    match = re.fullmatch(r'[vV]?(\d+\.\d+\.\d+)', tag)
    if not match:
        raise ValueError('Укажи тег выпуска вида v1.4.1.')
    if release.get('tag_name') != tag:
        raise ValueError('Метаданные относятся к другому выпуску.')
    name = f'Helene-{match.group(1)}.zip'
    assets = release.get('assets')
    if not isinstance(assets, list):
        raise ValueError('В ответе GitHub нет списка файлов выпуска.')
    for required in (name, name + '.sha256'):
        found = [row for row in assets if isinstance(row, dict) and row.get('name') == required]
        if len(found) != 1 or found[0].get('state') != 'uploaded' or not isinstance(found[0].get('size'), int) or found[0]['size'] <= 0:
            raise ValueError(f'В выпуске {tag} нужен готовый {required}. Сначала подготовь Windows-выпуск с тем же номером.')
    return name


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', ''))
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo):
        parser.error('Укажи --repo owner/repository.')
    if not re.fullmatch(r'[vV]?\d+\.\d+\.\d+', args.tag):
        parser.error('Укажи --tag v1.4.1.')
    try:
        result = subprocess.run(
            ['gh', 'api', f'repos/{args.repo}/releases/tags/{quote(args.tag, safe="")}'],
            check=True, text=True, encoding='utf-8', capture_output=True, timeout=30,
        )
        asset = check_release(json.loads(result.stdout), args.tag)
    except subprocess.CalledProcessError:
        print(f'macOS: не удалось прочитать выпуск {args.tag}. Проверь, что он существует и доступен токену CI.', file=sys.stderr)
        return 1
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        print(f'macOS: {exc}', file=sys.stderr)
        return 1
    print(f'macOS: {asset} и его контрольная сумма готовы. Содержимое и паспорт проверит build_mac.py при загрузке.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
