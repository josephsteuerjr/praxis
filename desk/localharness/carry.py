# -*- coding: utf-8 -*-
"""Перенос агента: один архив — и он живёт в другом месте.

Что такое «агент» для переноса: папка данных `data/` целиком (память,
конституция, навыки, рабочая папка, личный git `data/.git`, вход в ChatGPT
`data/relay/local_auth`, сессия Telegram `data/telegram`) плюс настройки
`helene.json` (имена, модель с ключом, Telegram, реле) и паспорт
`helene-carry.json` (кто, откуда, когда, какой версии, что внутри — и где
лежат секреты). Код (`tree/`, `app/`) и рантайм целиком в архив НЕ входят: это
поставка, она есть в каждой установке своя. Но ПРАВКИ агента в своём коде — его, и
с 1.2.5 они едут (`code/` в архиве): «Йоно может вернуться… или её дом переедет с
ней?» (Егор, 28.09). Правкой считается отличие от чистой версии, с которой агент
начинал (`pristine/<версия>.zip` у установки на ПК, чистый исходник исполнителя на
сервере, отпечатки выпуска); на новом месте они ложатся на его код тем же
переносчиком, что при обновлении (`server/updater/codecarry.py`), а что не легло —
агенту в `data/workspace/carry-<версия>/`. Сравнить не с чем — едет весь прежний код,
и агент получает его папкой.

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
`workspace/mnt/` (это чужие папки владельца, а не его), `models/` (модели
голоса: 0,5–1,6 ГБ снаряжения машины, качаются заново одной кнопкой),
`__pycache__`.

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
import hashlib
import json
import os
import platform
import shutil
import socket
import stat as _stat
import subprocess
import sys
import tempfile
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
              "memory/.state/body.json",
              # Модели голоса (`models/whisper`, 0,5–1,6 ГБ) — не агент, а
              # снаряжение машины: они скачиваются заново одной кнопкой, а в
              # архиве превратили бы перенос памяти в перенос гигабайтов.
              "models/")
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


# --- правки агента в своём коде (1.2.5) ---------------------------------------------------

CODE_PREFIX = "code/"
CODE_MANIFEST = "code/manifest.json"


def _codecarry():
    """Переносчик правок кода — рядом с исполнителем обновлений (он же у установщика ПК)."""
    here = Path(__file__).resolve()
    for cand in (here.parents[1] / "server" / "updater", here.parents[2] / "server" / "updater"):
        if (cand / "codecarry.py").is_file():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            import codecarry  # noqa: PLC0415
            return codecarry
    return None


def _install_version(root: Path, cfg: dict) -> str:
    version = str((cfg.get("installed") or {}).get("version") or "")
    if version:
        return version
    try:
        return str(json.loads((root / "helene-build.json").read_text("utf-8-sig")).get("version") or "")
    except (OSError, ValueError):
        return ""


def _code_base(cc, root: Path, version: str, work: Path):
    """Чистая версия, с которой агент начинал: (папка, отпечатки, откуда) — что нашлось."""
    base = cc.open_pristine(cc.pristine_path(root, version), work) if version else None
    if base is not None:
        return base, None, "pristine"
    server = root / ".updater" / "pristine" / version
    if version and all((server / n).is_dir() for n in cc.CODE_DIRS):
        return server, None, "pristine"
    prints = cc.shipped_prints(version, cc.flavor_of(root)) if version else None
    return None, prints, ("prints" if prints else "")


def _export_code(zf: zipfile.ZipFile, root: Path, cfg: dict) -> dict:
    """Правки агента в `tree/` и `app/` — в архив. -> строка для паспорта."""
    cc = _codecarry()
    if cc is None:
        return {"note": "переносчика правок кода нет в этой установке — код не поехал"}
    if not (root / "tree" / "agent.py").is_file():
        return {"note": "кода агента рядом с настройками нет — везти нечего"}
    version = _install_version(root, cfg)
    work = Path(tempfile.mkdtemp(prefix="helene-carry-code-"))
    try:
        base_root, prints, base_kind = _code_base(cc, root, version, work)
        manifest = {"version": version, "flavor": cc.flavor_of(root), "base": base_kind,
                    "edited": [], "prints": {}, "whole": False}
        if not base_kind:
            # Сравнить не с чем — весь прежний код: пусть агент разберёт сам (как на сервере).
            manifest["whole"] = True
            for name in cc.CODE_DIRS:
                for rel, kind in cc.snapshot(root / name, tuple(cc.DESK_SKIP[name])).items():
                    blob = cc.read_plain(root / name / rel) if kind[0] == "file" else None
                    if blob is not None:
                        zf.writestr(f"{CODE_PREFIX}whole/{name}/{rel}", blob)
            zf.writestr(CODE_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
            return {"base": "", "whole": True, "version": version}
        for name in cc.CODE_DIRS:
            skip = tuple(cc.DESK_SKIP[name])
            snap = cc.hashes_snapshot(prints, name) if base_root is None else None
            base_dir = base_root / name if base_root is not None else None
            for rel in cc.code_edits(base_dir, root / name, skip=skip, base_snap=snap):
                path = f"{name}/{rel}"
                manifest["edited"].append(path)
                mine = cc.read_plain(root / name / rel)
                if mine is not None:
                    zf.writestr(f"{CODE_PREFIX}mine/{path}", mine)
                if base_dir is not None:
                    blob = cc.read_plain(base_dir / rel)
                    if blob is not None:
                        zf.writestr(f"{CODE_PREFIX}base/{path}", blob)
                elif (prints or {}).get(path):
                    manifest["prints"][path] = prints[path]
        zf.writestr(CODE_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
        return {"base": base_kind, "edited": len(manifest["edited"]), "version": version}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _import_code(zf: zipfile.ZipFile, root: Path, data: Path) -> dict:
    """Правки агента из архива — на код этой установки. -> отчёт для расписки."""
    names = set(zf.namelist())
    if CODE_MANIFEST not in names:
        return {}
    cc = _codecarry()
    if cc is None:
        return {"note": "переносчика правок кода нет в этой установке — правки остались в архиве (code/)"}
    manifest = json.loads(zf.read(CODE_MANIFEST).decode("utf-8"))
    version = str(manifest.get("version") or "")
    here = _install_version(root, _read_config(root / CONFIG_NAME)) if (root / CONFIG_NAME).is_file() else ""
    work = Path(tempfile.mkdtemp(prefix="helene-carry-code-"))
    try:
        for name in names:
            if name.startswith(CODE_PREFIX) and not name.endswith("/") and _safe_member(name) \
                    and name != CODE_MANIFEST:
                target = work / name[len(CODE_PREFIX):]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
        if manifest.get("whole"):
            folder = cc.materials_folder(data, version or "?", tag="carry")
            shutil.copytree(work / "whole", folder / "old-code")
            (folder / "README.md").write_text(
                f"# Переезд: твой прежний код ({version or 'версия неизвестна'})\n\n"
                "Сравнить его с чистой версией там, откуда ты переехал, было не с чем — поэтому "
                "здесь, в `old-code/`, он целиком. Сравни с нынешним `tree/` и `app/` сам и "
                "перенеси своё.\n", "utf-8")
            return {"whole": True, "folder": f"workspace/{folder.name}",
                    "summary": "сравнить код было не с чем — прежний код агенту папкой"}
        report = {"edited": [], "carried": [], "merged": [], "conflicts": [], "skipped": []}
        materials, diffs = [], []
        labels = (f"агент ({version})", f"чистая {version}", f"здесь {here or '?'}")
        budget = [cc.MAX_MATERIALS]
        prints = manifest.get("prints") or {}
        edited = [str(p) for p in manifest.get("edited") or []]
        for name in cc.CODE_DIRS:
            base_dir = work / "base" / name
            # Снимок базы — только правленые пути: по содержимому, где оно приехало, иначе по отпечатку.
            snap = {}
            head = name + "/"
            for path in edited:
                if not path.startswith(head):
                    continue
                rel = path[len(head):]
                blob = cc.read_plain(base_dir / rel)
                if blob is not None:
                    snap[rel] = ("file", hashlib.sha256(blob).hexdigest(), None)
                elif prints.get(path):
                    snap[rel] = ("file", str(prints[path]), None)
            rep = cc.carry_code(base_dir if base_dir.is_dir() else None, work / "mine" / name, root / name,
                                work=work / ".merge", labels=labels, prefix=name, budget=budget,
                                base_snap=snap, skip=tuple(cc.DESK_SKIP[name]))
            for key in report:
                report[key].extend(rep[key])
            materials.extend(rep["materials"])
            diffs.extend(rep["diff"])
        if report["edited"]:
            folder = cc.materials_folder(data, version or "?", tag="carry")
            readme = cc.materials_readme(report, version, here or "эту установку").replace(
                "# Обновление", "# Переезд", 1)
            cc._put(folder, ["README.md"], readme.encode("utf-8"))
            cc._put(folder, ["edits.diff"], "".join(diffs).encode("utf-8")[:cc.MAX_DIFF])
            for row in materials:
                parts = row["path"].split("/")
                for side in ("mine", "base", "theirs", "merged"):
                    blob = row.get(side)
                    if blob is not None:
                        cc._put(folder, parts[:-1] + [f"{parts[-1]}.{side}"], blob)
            report["folder"] = f"workspace/{folder.name}"
        report["summary"] = (f"правок агента в коде: {len(report['edited'])}; легло "
                             f"{len(report['carried']) + len(report['merged'])}, не легло "
                             f"{len(report['conflicts'])}")
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


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
        try:
            passport["code"] = _export_code(zf, config_path.parent, cfg)
        except Exception as exc:                  # правки кода — не повод терять перенос памяти
            passport["code"] = {"note": f"правки кода не поехали: {type(exc).__name__}: {exc}"}
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
        try:
            receipt["code"] = _import_code(zf, config_path.parent, data)
        except Exception as exc:                  # данные уже на месте — не отказ всего переноса
            receipt["code"] = {"note": f"правки кода не легли: {type(exc).__name__}: {exc}"}
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
    code = p.get("code") or {}
    if code.get("whole"):
        print("правки кода: сравнить было не с чем — едет весь прежний код")
    elif "edited" in code:
        print(f"правки агента в коде: {code['edited']} файл(ов)")
    elif code.get("note"):
        print(f"правки кода: {code['note']}")
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
        # Человеку — фраза, машине — строка. ⚠ Раньше окно и установщик доставали
        # путь к архиву, отрезая от этой фразы префикс «архив: »: один перевод
        # интерфейса — и успешный экспорт показывался бы человеку провалом,
        # хотя архив лежит на диске готовый. Хуже исхода нет: агента экспортируют
        # перед переустановкой.
        print(f"архив: {passport['archive']} ({passport['archive_bytes'] / 1e6:.1f} МБ)")
        print("carry-export " + json.dumps(
            {"archive": str(passport["archive"]), "bytes": int(passport["archive_bytes"])},
            ensure_ascii=False))
        _print_passport(passport)
        return 0
    if args.cmd == "show":
        _print_passport(read_passport(Path(args.archive)))
        return 0
    receipt = import_(Path(args.config), Path(args.archive), keep_config=args.keep_config)
    _print_passport(receipt["passport"])
    print(f"развёрнуто в {receipt['data']}: файлов {receipt['files']}")
    code = receipt.get("code") or {}
    if code.get("summary") or code.get("note"):
        print("код агента: " + str(code.get("summary") or code.get("note"))
              + (f"; материалы — data/{code['folder']}" if code.get("folder") else ""))
    if receipt["backup"]:
        print(f"прежние данные: {receipt['backup']} (не удалены)")
    print("конфиг: " + ("слит — блоки агента из архива, блоки хоста местные"
                        if receipt["config_merged"] else "не тронут"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
