#!/usr/bin/env python3
"""Исходник реле для поставки: сверить с живым и обновить зеркало.

Реле в поставке (`helene-relay.exe`) собирается из `_relay_prod_src/` рядом с
репозиториями — это КОПИЯ живого исходника с сервера (`/opt/relay/Code`), и до
10.09 она обновлялась руками и без следа. Класс ошибки уже стрелял: 09.09 в
поставке лежал бинарь от 03.09, а починка ссылок была от 09.09 — узнали об этом
случайно, потому что сверять было нечем.

Здесь два действия и один отпечаток:

    python installer/relay_src.py --check --host <адрес>   # зеркало = живой?
    python installer/relay_src.py --pull  --host <адрес>   # обновить зеркало
    python installer/relay_src.py --digest                 # отпечаток зеркала

Отпечаток считается по содержимому с одной нормой переводов строк (LF): копия
на Windows иначе расходилась бы с сервером на каждом файле. Его же пишет в
паспорт сборка (`helene-build.json` -> `relay`), а рядом с зеркалом лежит
`RELAY-SOURCE.json`: откуда, какой коммит, когда снято.

Пароль — в `PRAXIS_DEPLOY_PASSWORD` (или ключом ssh-agent). Нужен `paramiko`.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

DESK = Path(__file__).resolve().parent.parent
ROOT = DESK.parent
sys.path.insert(0, str(DESK))
import layout  # noqa: E402 — где на диске лежат соседи раскладки

MIRROR = layout.relay_mirror()
STAMP = "RELAY-SOURCE.json"
BUILT = "RELAY-BUILD.json"
REMOTE_DEFAULT = "/opt/relay/Code"

# Что считается исходником реле: то, из чего собирается бинарь. Каталог target/
# и логи не в счёт — это следы сборки, а не она сама.
PATTERNS = ("src/**/*", "Cargo.toml", "Cargo.lock")


def _files(root: Path) -> list[Path]:
    seen: list[Path] = []
    for pat in PATTERNS:
        seen += [p for p in root.glob(pat) if p.is_file()]
    return sorted(set(seen))


def digest(root: Path) -> tuple[str, dict[str, str]]:
    """Отпечаток исходника -> (общий sha256, файл -> sha256).

    Содержимое приводится к LF: зеркало на Windows и оригинал на сервере
    отличались бы каждым файлом, и сверка не значила бы ничего.
    """
    files = {}
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        files[rel] = hashlib.sha256(
            path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    total = hashlib.sha256(
        "\n".join(f"{rel} {sha}" for rel, sha in sorted(files.items())).encode("utf-8")
    ).hexdigest()
    return total, files


def newest_mtime(root: Path = MIRROR) -> float:
    """Когда исходник трогали в последний раз — чтобы сборка увидела, что
    бинарь старше своих же исходников (09.09 в поставку уехало реле от 03.09)."""
    return max((p.stat().st_mtime for p in _files(root)), default=0.0)


def stamp(root: Path = MIRROR) -> dict:
    """Что записано о происхождении зеркала; пусто — не записано ничего."""
    return _read(root / STAMP)


def built(root: Path = MIRROR) -> dict:
    """Чем собран лежащий рядом бинарь; пусто — сборка ничего о себе не сказала."""
    return _read(root / BUILT)


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def build(root: Path = MIRROR) -> dict:
    """Собрать реле и записать, ИЗ ЧЕГО оно собрано.

    Отпечаток исходника в момент сборки — единственный честный ответ на вопрос
    «этот ли бинарь у меня в поставке». По времени файлов судить нельзя: любое
    обновление зеркала переписывает mtime, ничего не меняя по существу.
    """
    total, files = digest(root)
    print(f"cargo build --release в {root} ({len(files)} файлов исходника)…")
    r = subprocess.run(["cargo", "build", "--release"], cwd=root,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("реле не собралось")
    exe = next((p for p in (root / "target" / "release").glob("*.exe")
                if "codex" in p.name or "relay" in p.name), None)
    if exe is None:
        raise SystemExit(f"после сборки не нашёлся бинарь в {root / 'target' / 'release'}")
    note = {
        "digest": total,
        "exe": exe.name,
        "exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        "built_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (root / BUILT).write_text(json.dumps(note, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8", newline="\n")
    print(f"собрано: {exe} ({exe.stat().st_size / 1e6:.1f} МБ)")
    print(f"записано: {root / BUILT}")
    return note


# --- сервер -------------------------------------------------------------------

_PROBE = r"""
import hashlib, json, pathlib, subprocess
root = pathlib.Path(%r)
pats = %r
files = {}
seen = []
for pat in pats:
    seen += [p for p in root.glob(pat) if p.is_file()]
for path in sorted(set(seen)):
    files[path.relative_to(root).as_posix()] = hashlib.sha256(
        path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                      capture_output=True, text=True).stdout.strip()
dirty = bool(subprocess.run(['git', '-C', str(root), 'status', '--porcelain'],
                            capture_output=True, text=True).stdout.strip())
print(json.dumps({'files': files, 'head': head, 'dirty': dirty}))
"""


def _connect(host: str, user: str):
    try:
        import paramiko  # noqa: PLC0415 — зависимость только этой утилиты
    except ImportError:
        raise SystemExit("нужен paramiko: python -m pip install paramiko")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user,
                   password=os.environ.get("PRAXIS_DEPLOY_PASSWORD") or None, timeout=30)
    return client


def remote_state(client, remote: str) -> dict:
    # Через base64: код многострочный, и любая попытка передать его кавычками
    # ломается о разбор оболочки — переводы строк доезжают до python3 уже
    # обратной косой с буквой, и он падает на первой же строке.
    code = base64.b64encode((_PROBE % (remote, PATTERNS)).encode("utf-8")).decode("ascii")
    cmd = f"python3 -c \"import base64;exec(base64.b64decode('{code}'))\""
    _in, out, err = client.exec_command(cmd, timeout=120)
    body = out.read().decode("utf-8", "replace").strip()
    if out.channel.recv_exit_status() != 0 or not body:
        raise SystemExit("не прочитался исходник на сервере: "
                         + err.read().decode("utf-8", "replace")[-500:])
    return json.loads(body)


def compare(mine: dict[str, str], theirs: dict[str, str]) -> list[str]:
    lines = []
    for rel in sorted(set(mine) | set(theirs)):
        a, b = theirs.get(rel), mine.get(rel)
        if a == b:
            continue
        lines.append(f"  {'нет в зеркале' if b is None else 'нет на сервере' if a is None else 'различается'}: {rel}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="исходник реле: сверка и обновление зеркала")
    ap.add_argument("--host", default=os.environ.get("PRAXIS_DEPLOY_HOST", ""))
    ap.add_argument("--user", default=os.environ.get("PRAXIS_DEPLOY_USER", "root"))
    ap.add_argument("--remote", default=REMOTE_DEFAULT, help="исходник реле на сервере")
    ap.add_argument("--mirror", default=str(MIRROR), help="зеркало рядом с репозиториями")
    ap.add_argument("--check", action="store_true", help="сверить зеркало с живым")
    ap.add_argument("--pull", action="store_true", help="обновить зеркало живым")
    ap.add_argument("--digest", action="store_true", help="напечатать отпечаток зеркала")
    ap.add_argument("--build", action="store_true",
                    help="собрать реле и записать, из какого исходника")
    args = ap.parse_args()

    mirror = Path(args.mirror)
    if args.build:
        build(mirror)
        if not (args.check or args.pull):
            return 0
    if args.digest or not (args.check or args.pull):
        total, files = digest(mirror)
        note = stamp(mirror)
        made = built(mirror)
        print(f"зеркало: {mirror}")
        print(f"файлов: {len(files)}, отпечаток {total[:16]}")
        print("происхождение: " + (json.dumps(note, ensure_ascii=False)
                                   if note else f"НЕ ЗАПИСАНО (нет {STAMP})"))
        if not made:
            print(f"бинарь: о себе ничего не сказано (нет {BUILT}) — собери "
                  "installer/relay_src.py --build")
        elif made.get("digest") == total:
            print(f"бинарь: собран из этого же исходника ({made.get('built_utc')})")
        else:
            print(f"бинарь: собран ИЗ ДРУГОГО исходника ({made.get('digest', '')[:16]}) — пересобрать")
        return 0

    if not args.host:
        raise SystemExit("нужен --host (или PRAXIS_DEPLOY_HOST)")
    client = _connect(args.host, args.user)
    try:
        state = remote_state(client, args.remote)
        _total, mine = digest(mirror)
        diff = compare(mine, state["files"])
        head = state["head"][:7] + ("-dirty" if state["dirty"] else "")
        print(f"живой исходник: {args.remote} @ {head}, файлов {len(state['files'])}")
        if args.check:
            if not diff:
                print("зеркало сходится с живым исходником — поставка соберётся из него")
                return 0
            print("зеркало РАСХОДИТСЯ с живым:")
            print("\n".join(diff))
            print("обновить: installer/relay_src.py --pull --host " + args.host)
            return 1

        # --pull: тянем ровно то, что считается исходником, и ничего больше.
        sftp = client.open_sftp()
        pulled = 0
        for rel in sorted(state["files"]):
            target = mirror / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with sftp.open(f"{args.remote.rstrip('/')}/{rel}", "rb") as src:
                data = src.read()
            target.write_bytes(data)
            pulled += 1
        sftp.close()
        for rel in sorted(set(mine) - set(state["files"])):
            print(f"  лишний файл в зеркале (не трогаю): {rel}")
        total, _files = digest(mirror)
        note = {
            "from": f"{args.user}@{args.host}:{args.remote}",
            "commit": state["head"],
            "dirty": state["dirty"],
            "pulled_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "digest": total,
            "files": pulled,
        }
        (mirror / STAMP).write_text(json.dumps(note, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8", newline="\n")
        print(f"взято файлов: {pulled}, отпечаток {total[:16]}")
        print(f"записано: {mirror / STAMP}")
        print("пересобрать реле: cargo build --release в " + str(mirror))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
