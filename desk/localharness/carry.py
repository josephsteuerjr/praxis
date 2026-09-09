# -*- coding: utf-8 -*-
"""Перенос агента: один архив — и он живёт в другом месте.

Что такое «агент» для переноса: папка данных `data/` целиком (память,
конституция, навыки, рабочая папка, личный git `data/.git`, вход в ChatGPT
`data/relay/local_auth`, сессия Telegram `data/telegram`) плюс настройки
`helene.json` (имена, модель с ключом, Telegram, реле) и паспорт
`helene-carry.json` (кто, откуда, когда, какой версии, что внутри — и где
лежат секреты). Код (`tree/`, `app/`) и рантайм в архив НЕ входят: это
поставка, она есть в каждой установке своя.

Экспорт — `python carry.py export --config helene.json [--out путь.zip]`,
та же команда стоит за кнопкой «Экспорт агента» в Настройках и за
`helene-setup.exe --export`. Импорт — `python carry.py import --config
helene.json --archive путь.zip`: прежняя `data/` (если была) отходит в
`data.before-<штамп>/` и не удаляется, конфиг сливается по правилу ниже.
Обратный перенос сервер → ПК — тем же архивом.

⚠ Секреты в архиве. Ключ модели, токен бота, вход ChatGPT и сессия Telegram
едут внутри — иначе агент на новом месте нем. Паспорт перечисляет их
поимённо, а экспорт печатает об этом прямо. Архив — не для пересылки
посторонним.

Что из `data/` НЕ едет: `body/` (тело руки computer живёт на машине, не с
агентом), журналы (`*.log`), замок дерева и ключ окна (`memory/.state/
harness.lock`, `desk-token`, `body.json` — они принадлежат хосту), стыки
`workspace/mnt/` (это чужие папки владельца, а не его), `__pycache__`.

Правило слияния конфига при импорте: у АРХИВА берутся блоки про агента —
`agent`, `owner`, `model`, `telegram`, `relay`, `env`, `update`; у МЕСТА
остаются блоки про хост — `mode`, `python`, `app`, `runner`, `tree`, `code`,
`port`, `agent_mode`, `sandbox`, `service`, `computer`, `phone`, `installed`.
На сервере (см. `server/`) это значит: агент приезжает со своей моделью и
именем, а ограда, порт и раскладка папок — местные.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import socket
import stat as _stat
import subprocess
import sys
import zipfile
from pathlib import Path

SCHEMA = "helene.carry.v1"
PASSPORT = "helene-carry.json"
CONFIG_NAME = "helene.json"

#: Блоки конфига, которые едут с агентом (архив побеждает при импорте).
AGENT_KEYS = ("agent", "owner", "model", "telegram", "relay", "env", "update")
#: Блоки конфига, которые остаются за местом (архив их не трогает).
HOST_KEYS = ("mode", "python", "app", "runner", "tree", "code", "port", "agent_mode",
             "sandbox", "service", "computer", "phone", "installed", "setup_complete",
             "read_dotenv", "base", "key")

#: Что в `data/` не едет: относительные пути (с завершающим `/` — папка целиком)
#: и маски имён.
SKIP_PATHS = ("body/", "workspace/mnt/", "workspace/.fence/", "workspace/.tmp/",
              "relay/logs/", "memory/.state/harness.lock", "memory/.state/desk-token",
              "memory/.state/body.json")
SKIP_NAMES = ("__pycache__", ".pytest_cache")
SKIP_SUFFIXES = (".log", ".log.1", ".pyc")

#: Где в архиве лежат секреты — паспорт называет их поимённо.
SECRET_SPOTS = (
    (f"{CONFIG_NAME}: model.key", lambda cfg, data: bool(((cfg.get("model") or {}).get("key") or "").strip())),
    (f"{CONFIG_NAME}: telegram.bot_token", lambda cfg, data: bool(((cfg.get("telegram") or {}).get("bot_token") or "").strip())),
    ("data/relay/local_auth (вход ChatGPT)", lambda cfg, data: (data / "relay" / "local_auth").is_dir()),
    ("data/telegram (сессия аккаунта)", lambda cfg, data: (data / "telegram").is_dir()),
    ("data/memory/llm.json (выбор мозга с ключом)", lambda cfg, data: (data / "memory" / "llm.json").is_file()),
)


def _read_config(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        text = raw[3:].decode("utf-8")
    elif raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8")
    cfg = json.loads(text)
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: верхний уровень должен быть объектом")
    return cfg


def _write_config(path: Path, cfg: dict) -> None:
    tmp = path.with_name(".tmp-" + path.name)
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def tree_of(config_path: Path, cfg: dict) -> Path:
    raw = str(cfg.get("tree") or "data")
    tree = Path(raw)
    return tree if tree.is_absolute() else (config_path.parent / tree).resolve()


def _is_reparse(path: Path) -> bool:
    """Стык (junction) или символическая ссылка: внутрь не ходим."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if _stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & 0x400)      # FILE_ATTRIBUTE_REPARSE_POINT


def _skip(rel: str, name: str) -> bool:
    posix = rel.replace("\\", "/")
    for p in SKIP_PATHS:
        if p.endswith("/"):
            if posix == p.rstrip("/") or posix.startswith(p):
                return True
        elif posix == p:
            return True
    if name in SKIP_NAMES:
        return True
    return any(name.endswith(s) for s in SKIP_SUFFIXES)


def _walk(data: Path):
    """Файлы data/ для архива: (абсолютный путь, путь внутри архива)."""
    for root, dirs, files in os.walk(data):
        root_p = Path(root)
        rel_root = root_p.relative_to(data).as_posix()
        rel_root = "" if rel_root == "." else rel_root
        keep = []
        for d in dirs:
            rel = f"{rel_root}/{d}" if rel_root else d
            if _skip(rel + "/", d) or _is_reparse(root_p / d):
                continue
            keep.append(d)
        dirs[:] = keep
        for f in files:
            rel = f"{rel_root}/{f}" if rel_root else f
            if _skip(rel, f) or _is_reparse(root_p / f):
                continue
            yield root_p / f, f"data/{rel}"


def _git_head(data: Path) -> str:
    git = shutil.which("git")
    if not git or not (data / ".git").exists():
        return ""
    try:
        out = subprocess.run([git, "-C", str(data), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%dT%H%M%S")


def export(config_path: Path, out: Path | None = None) -> dict:
    """Собрать архив переноса. -> паспорт (он же лежит в архиве)."""
    config_path = Path(config_path).resolve()
    cfg = _read_config(config_path)
    data = tree_of(config_path, cfg)
    if not data.is_dir():
        raise SystemExit(f"папки данных нет: {data}")
    agent = str((cfg.get("agent") or {}).get("name") or "agent").strip() or "agent"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in agent).strip("-") or "agent"
    if out is None:
        out = config_path.parent / f"helene-{safe}-{_stamp()}.zip"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Ключи и вход едут внутри: без них агент на новом месте нем. Паспорт
    # говорит об этом поимённо, а не «в архиве могут быть секреты».
    secrets = [name for name, has in SECRET_SPOTS if has(cfg, data)]
    passport = {
        "schema": SCHEMA,
        "agent": agent,
        "owner": str((cfg.get("owner") or {}).get("name") or ""),
        "version": str((cfg.get("installed") or {}).get("version") or ""),
        "exported_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "agent_mode": str(cfg.get("agent_mode") or ""),
        "data_git_head": _git_head(data),
        "secrets": secrets,
        "skipped": list(SKIP_PATHS),
        "files": 0,
        "bytes": 0,
    }
    exported_cfg = {k: v for k, v in cfg.items()}
    files = 0
    total = 0
    tmp = out.with_suffix(out.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src, arc in _walk(data):
            try:
                zf.write(src, arc)
                files += 1
                total += src.stat().st_size
            except OSError as exc:
                print(f"  ⚠ не поехал {arc}: {exc}", file=sys.stderr)
        passport["files"] = files
        passport["bytes"] = total
        zf.writestr(CONFIG_NAME, json.dumps(exported_cfg, ensure_ascii=False, indent=2) + "\n")
        zf.writestr(PASSPORT, json.dumps(passport, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, out)
    passport["archive"] = str(out)
    passport["archive_bytes"] = out.stat().st_size
    return passport


def read_passport(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        if PASSPORT not in names:
            raise SystemExit(f"{archive}: это не архив переноса Hélène — нет {PASSPORT}")
        passport = json.loads(zf.read(PASSPORT).decode("utf-8"))
    if passport.get("schema") != SCHEMA:
        raise SystemExit(f"{archive}: схема {passport.get('schema')!r}, ждали {SCHEMA}")
    return passport


def merge_config(local: dict, carried: dict) -> dict:
    """Слить: у архива — блоки агента, у места — блоки хоста (см. шапку)."""
    out = dict(local)
    for key in AGENT_KEYS:
        if key in carried:
            out[key] = carried[key]
    for key in HOST_KEYS:
        if key in local:
            out[key] = local[key]
    # Всё, чего место не знает и что не про хост, доезжает из архива.
    for key, value in carried.items():
        if key not in out and key not in HOST_KEYS:
            out[key] = value
    return out


def _safe_member(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    return bool(parts) and ".." not in parts and not name.startswith("/") and ":" not in parts[0]


def import_(config_path: Path, archive: Path, *, keep_config: bool = False) -> dict:
    """Развернуть архив в место, на которое указывает `config_path`."""
    config_path = Path(config_path).resolve()
    archive = Path(archive).resolve()
    passport = read_passport(archive)
    if config_path.is_file():
        local = _read_config(config_path)
    else:
        local = {"mode": "local", "tree": "data", "code": "tree", "port": 8094}
    data = tree_of(config_path, local)
    receipt = {"passport": passport, "data": str(data), "backup": "", "config": str(config_path)}
    if data.exists() and any(data.iterdir()):
        backup = data.with_name(f"{data.name}.before-{_stamp()}")
        os.rename(data, backup)
        receipt["backup"] = str(backup)
    data.mkdir(parents=True, exist_ok=True)
    carried_cfg: dict = {}
    written = 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename
            if not _safe_member(name):
                raise SystemExit(f"подозрительный путь в архиве: {name!r}")
            if name == CONFIG_NAME:
                carried_cfg = json.loads(zf.read(info).decode("utf-8"))
                continue
            if name == PASSPORT or info.is_dir():
                continue
            if not name.startswith("data/"):
                continue
            target = data / name[len("data/"):]
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            written += 1
    receipt["files"] = written
    if not keep_config and carried_cfg:
        merged = merge_config(local, carried_cfg)
        _write_config(config_path, merged)
        receipt["config_merged"] = True
        receipt["agent"] = str((merged.get("agent") or {}).get("name") or "")
    else:
        receipt["config_merged"] = False
    return receipt


def _print_passport(p: dict) -> None:
    print(f"агент: {p.get('agent')} (владелец {p.get('owner') or '—'}), версия {p.get('version') or '—'}")
    print(f"откуда: {p.get('host')} · {p.get('platform')} · режим {p.get('agent_mode') or '—'}")
    print(f"снимок данных: git {p.get('data_git_head') or 'нет'} · файлов {p.get('files')} · "
          f"{(p.get('bytes') or 0) / 1e6:.1f} МБ")
    if p.get("secrets"):
        print("⚠ в архиве секреты: " + "; ".join(p["secrets"]))
        print("  архив — не для пересылки посторонним")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="перенос агента Hélène одним архивом")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="собрать архив из этой установки")
    ex.add_argument("--config", required=True)
    ex.add_argument("--out", default="")
    im = sub.add_parser("import", help="развернуть архив в эту установку")
    im.add_argument("--config", required=True)
    im.add_argument("--archive", required=True)
    im.add_argument("--keep-config", action="store_true",
                    help="не сливать helene.json из архива (только данные)")
    sh = sub.add_parser("show", help="паспорт архива")
    sh.add_argument("archive")
    args = parser.parse_args(argv)
    if args.cmd == "export":
        passport = export(Path(args.config), Path(args.out) if args.out else None)
        print(f"архив: {passport['archive']} ({passport['archive_bytes'] / 1e6:.1f} МБ)")
        _print_passport(passport)
        return 0
    if args.cmd == "show":
        _print_passport(read_passport(Path(args.archive)))
        return 0
    receipt = import_(Path(args.config), Path(args.archive), keep_config=args.keep_config)
    _print_passport(receipt["passport"])
    print(f"развёрнуто в {receipt['data']}: файлов {receipt['files']}")
    if receipt["backup"]:
        print(f"прежние данные: {receipt['backup']} (не удалены)")
    print("конфиг: " + ("слит — блоки агента из архива, блоки хоста местные"
                        if receipt["config_merged"] else "не тронут"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
