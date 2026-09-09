#!/usr/bin/env python3
"""Пакет desk: состав, версия и зависимости — одним объявлением.

Desk ставится в двух местах, и до 10.09 каждое место знало состав по-своему:

* сервер — Пульт рядом с деревом агента (``server/deploy_desk.py`` клал пять
  путей в ``/opt/praxisdesk``);
* Windows — папка ``app/`` внутри поставки (``installer/build_dist.py`` клал
  свой набор: питонью часть по списку, статику и телефон отдельными функциями,
  мини-апп не клал вовсе).

Списка было полтора: 09.09 состав объявили один раз (``deploy_desk.MODULE``), но
фронты в него не входили и по-прежнему ехали разными дорогами, а зависимости
канала не объявлял никто — на сервере ``deskd`` жил без своего ``requirements``.
Отсюда класс ошибок «на сервере есть, на Windows нет» (``rooms.py`` 07.09 так и
не уехал в поставку) и «контейнер Пульта нечем пересобрать».

Теперь состав объявлен ЗДЕСЬ. Обе установки не перечисляют пути, а ставят
собранный пакет целиком:

    python deskpkg.py --flavor server  --out /tmp/desk     # что и куда
    python deskpkg.py --list                               # состав обоих видов

Пакет — это каталог с одинаковой раскладкой у обеих установок
(``deskapp.py``, ``deskd/``, ``static/``, ``mobile/``…), плюс два файла о себе:

* ``desk.json`` — манифест: версия продукта, вид пакета, sha256 каждого файла и
  один общий отпечаток. Его кладут рядом с каналом, и канал отдаёт его окну
  (``/api/state`` → ``desk``): на сервере это единственный способ узнать, какая
  версия Пульта там живёт — оболочки, которая отвечает окну ``app_info``, там
  нет.
* ``requirements-desk.txt`` — зависимости именно desk, по видам: канал просит
  ``aiohttp``, раннер Windows — ещё и ``telethon``. Сервер ставит этот файл,
  поставка вливает его в общий ``requirements.txt``.

Виды пакета (``flavor``) — не два разных состава, а один список с пометкой, где
часть нужна: мини-апп Telegram живёт только на сервере (страницу открывает
Telegram по HTTPS, до localhost он не дойдёт), а раннер и ресурсы — только на
Windows (на сервере ходы ведёт её собственный харнесс). Разница видна здесь
строкой, а не расхождением двух скриптов.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

DESK = Path(__file__).resolve().parent

MANIFEST = "desk.json"
STATIC_MANIFEST = ".helene-static.json"
REQUIREMENTS = "requirements-desk.txt"

SERVER, WINDOWS = "server", "windows"
FLAVORS = (SERVER, WINDOWS)

# Что не едет никогда: байт-код машины сборщика (его никто не проверял и он
# чужой для целевой версии Python) и мусор файловых менеджеров.
SKIP_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = (".pyc", ".pyo")


@dataclass(frozen=True)
class Part:
    """Часть пакета: что берём в репозитории и как оно зовётся в пакете."""

    src: str
    dest: str
    flavors: tuple[str, ...]
    kind: str = "tree"        # tree — каталог целиком, py — только *.py, file — файл
    dist: bool = False        # сборка Vite: без index.html это не сборка, а исходники
    why: str = ""

    def path(self) -> Path:
        return DESK / self.src


PARTS: tuple[Part, ...] = (
    Part("deskapp.py", "deskapp.py", FLAVORS, kind="file",
         why="канал: HTTP, вебсокет, ключи, комнаты"),
    Part("deskd", "deskd", FLAVORS, kind="py",
         why="читалки канала: состояние, комнаты, расход, разрезы кадра"),
    Part("app/dist", "static", FLAVORS, dist=True,
         why="окно: сборка Vite (npm --prefix app run build)"),
    Part("mobile/dist", "mobile", FLAVORS, dist=True,
         why="телефон: PWA на /m/"),
    Part("miniapp/dist", "miniapp", (SERVER,), dist=True,
         why="мини-апп Telegram: его отдаёт Caddy, и только с публичного адреса"),
    Part("server/desk-recipe", "recipe", (SERVER,),
         why="рецепт контейнера Пульта: образ на requirements-desk.txt пакета"),
    Part("localharness", "localharness", (WINDOWS,), kind="py",
         why="раннер Windows: первый запуск, ходы, доставка слова, Telegram"),
    Part("resources", "resources", (WINDOWS,),
         why="ресурсы продукта: каноническая конституция и всё, что читает boot.py"),
)

# Зависимости desk по видам. Ядро агента объявляет свои отдельно
# (``installer/build_dist.TREE_DEPS``) — здесь только то, без чего не живут
# канал и раннер.
DEPS_CHANNEL = ["aiohttp"]                 # deskapp.py: web, AbstractAccessLogger
DEPS_RUNNER = ["telethon==1.44.0"]         # localharness/mtproto*.py: свой аккаунт Telegram


def parts(flavor: str) -> tuple[Part, ...]:
    if flavor not in FLAVORS:
        raise ValueError(f"вид пакета «{flavor}»: знаю только {', '.join(FLAVORS)}")
    return tuple(p for p in PARTS if flavor in p.flavors)


def requirements(flavor: str) -> list[str]:
    """Зависимости пакета этого вида — по тому же списку частей, а не отдельно."""
    names = {p.dest for p in parts(flavor)}
    deps = list(DEPS_CHANNEL)
    if "localharness" in names:
        deps += DEPS_RUNNER
    return deps


# --- версия -------------------------------------------------------------------

def _cargo_version(path: Path) -> str:
    if not path.is_file():
        return ""
    in_pkg = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("["):
            in_pkg = s == "[package]"
        elif in_pkg and s.startswith("version"):
            return s.split("=", 1)[1].strip().strip('"')
    return ""


def _json_version(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("version", ""))
    except (ValueError, OSError):
        return ""


def product_version() -> tuple[str, dict]:
    """Версия продукта = версия трёх Cargo.toml, и они обязаны совпадать.

    Живёт здесь, а не в сборке поставки: версию спрашивает и выкладка на сервер
    (в манифест пакета), а импортировать ради этого ``installer/build_dist.py``
    — значит тянуть за собой всю сборку архива.
    """
    declared = {
        "shell/Cargo.toml": _cargo_version(DESK / "shell" / "Cargo.toml"),
        "setup/Cargo.toml": _cargo_version(DESK / "setup" / "Cargo.toml"),
        "svc/Cargo.toml": _cargo_version(DESK / "svc" / "Cargo.toml"),
        "shell/tauri.conf.json": _json_version(DESK / "shell" / "tauri.conf.json"),
        "setup/tauri.conf.json": _json_version(DESK / "setup" / "tauri.conf.json"),
        "app/package.json": _json_version(DESK / "app" / "package.json"),
        "mobile/package.json": _json_version(DESK / "mobile" / "package.json"),
        "setup/ui/package.json": _json_version(DESK / "setup" / "ui" / "package.json"),
    }
    core = {k: v for k, v in declared.items() if k.endswith("Cargo.toml")}
    if not all(core.values()):
        raise SystemExit("не прочиталась версия из Cargo.toml: " +
                         ", ".join(k for k, v in core.items() if not v))
    if len(set(core.values())) != 1:
        raise SystemExit("версии разъехались, поставка была бы смесью:\n  " +
                         "\n  ".join(f"{k} = {v}" for k, v in core.items()))
    return next(iter(core.values())), declared


# --- сборка пакета ------------------------------------------------------------

def _skip(path: Path) -> bool:
    return (any(part in SKIP_NAMES for part in path.parts)
            or path.suffix.lower() in SKIP_SUFFIXES)


def _why_missing(part: Part) -> str:
    """Почему часть взять нельзя; пустая строка — всё на месте."""
    path = part.path()
    if part.kind == "file":
        return "" if path.is_file() else "нет файла"
    build_hint = f"собери фронт: npm --prefix {part.src.split('/')[0]} run build"
    if not path.is_dir():
        return "нет каталога" + (f" ({build_hint})" if part.dist else "")
    if part.dist and not (path / "index.html").is_file():
        return f"без index.html, это не сборка Vite: {build_hint}"
    return ""


def check(flavor: str) -> list[str]:
    """Чего не хватает для пакета этого вида — списком, а не первой ошибкой."""
    return [f"{p.src} — {why}" for p in parts(flavor) if (why := _why_missing(p))]


def _copy_part(part: Part, dest_root: Path) -> int:
    """Одна часть в пакет -> сколько файлов положено."""
    src, dest = part.path(), dest_root / part.dest
    if part.kind == "file":
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return 1
    if dest.exists():
        shutil.rmtree(dest)
    count = 0
    for item in sorted(src.rglob("*")):
        if not item.is_file() or _skip(item.relative_to(src)):
            continue
        if part.kind == "py" and item.suffix != ".py":
            continue
        target = dest / item.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def static_manifest(root: Path) -> dict:
    """Манифест статики окна: sha256 каждого файла и один общий отпечаток.

    По нему установщик решает, менял ли ВЫПУСК интерфейс (слово владельца
    07.09: «если я не трогал статику — оставлять пользовательскую»).
    Сравниваются манифесты двух поставок, а не файлы пользователя.
    """
    files: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel == STATIC_MANIFEST:
            continue
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(
        "\n".join(f"{rel} {sha}" for rel, sha in files.items()).encode("utf-8")).hexdigest()
    return {"v": 1, "digest": digest, "files": files}


def copy_static(dest: Path) -> str:
    """Статика окна в ``dest`` + манифест рядом -> отпечаток.

    Отдельной ручкой, потому что вариант Praxis — это окно без канала: там
    пакета desk нет вовсе, а статика та же самая и отпечаток нужен тот же.
    """
    part = next(p for p in PARTS if p.dest == "static")
    why = _why_missing(part)
    if why:
        raise SystemExit(f"{part.src} — {why}")
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(part.path(), dest)
    man = static_manifest(dest)
    (dest / STATIC_MANIFEST).write_text(
        json.dumps(man, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8", newline="\n")
    return man["digest"]


def build(dest: Path, flavor: str, *, version: str | None = None,
          clean: bool = False, allow_partial: bool = False, log=print) -> dict:
    """Собрать пакет вида ``flavor`` в каталоге ``dest`` -> манифест.

    ``clean`` — стереть каталог целиком (для временной сборки выкладки). В
    поставке нельзя: пакет ложится в ``app/`` рядом с тем, что кладёт сборка.

    ``allow_partial`` — отладочная полусборка: несобранный ФРОНТ пропускается
    с громкой строкой и попадает в манифест полем ``skipped``, всё остальное
    по-прежнему обязательно. Полусборка так и остаётся видимой в поставке, а
    не забывается до первого 404 у владельца.
    """
    version = version or product_version()[0]
    dest = Path(dest)
    if clean and dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    take: list[Part] = []
    skipped: list[dict] = []
    hard: list[str] = []
    for part in parts(flavor):
        why = _why_missing(part)
        if not why:
            take.append(part)
        elif allow_partial and part.dist:
            log(f"  ⚠ {part.dest.upper()} В ЭТОМ ПАКЕТЕ НЕТ ({part.src}: {why}) — --allow-partial")
            skipped.append({"name": part.dest, "src": part.src, "why": why})
        else:
            hard.append(f"{part.src} — {why}")
    if hard:
        raise SystemExit("пакет desk не собрать:\n  " + "\n  ".join(hard))

    counted: list[dict] = []
    static_digest = ""
    for part in take:
        if part.dest == "static":
            # Отпечаток статики пишем ВНУТРЬ статики: установщик читает его в
            # уже поставленной копии, где манифеста пакета может не быть
            # (поставки до 0.5.1).
            static_digest = copy_static(dest / "static")
            n = sum(1 for p in (dest / "static").rglob("*") if p.is_file())
        else:
            n = _copy_part(part, dest)
        counted.append({"name": part.dest, "src": part.src, "files": n, "why": part.why})

    deps = requirements(flavor)
    (dest / REQUIREMENTS).write_text(
        "# Зависимости пакета desk — объявлены в deskpkg.py (вид: "
        f"{flavor}).\n" + "\n".join(deps) + "\n", encoding="utf-8", newline="\n")

    files: dict[str, str] = {}
    for path in sorted(p for p in dest.rglob("*") if p.is_file()):
        rel = path.relative_to(dest).as_posix()
        if rel == MANIFEST:
            continue
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(
        "\n".join(f"{rel} {sha}" for rel, sha in files.items()).encode("utf-8")).hexdigest()

    manifest = {
        "v": 1,
        "product": "desk",
        "version": version,
        "flavor": flavor,
        "built_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "digest": digest,
        "static": static_digest,
        "parts": counted,
        "skipped": skipped,
        "requirements": deps,
        "files": files,
    }
    (dest / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8", newline="\n")
    return manifest


def top_level(flavor: str) -> list[str]:
    """Имена в корне пакета — то, что установка подменяет целиком.

    Всё, чего здесь нет (``desk.env``, данные, чужие копии), установка не
    трогает: пакет отвечает за себя и только за себя.
    """
    return [p.dest for p in parts(flavor)] + [REQUIREMENTS, MANIFEST]


def read_manifest(root: Path) -> dict | None:
    """Манифест уже поставленного пакета; None — пакета нет или он битый."""
    path = Path(root) / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("product") == "desk" else None


def verify(root: Path) -> list[str]:
    """Сверить поставленный пакет с его манифестом -> список расхождений."""
    man = read_manifest(root)
    if man is None:
        return [f"нет манифеста {MANIFEST} — это не пакет desk"]
    bad: list[str] = []
    root = Path(root)
    for rel, want in (man.get("files") or {}).items():
        path = root / rel
        if not path.is_file():
            bad.append(f"нет файла: {rel}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != want:
            bad.append(f"изменён: {rel}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="пакет desk: состав, версия, сборка")
    ap.add_argument("--flavor", choices=FLAVORS, default=SERVER)
    ap.add_argument("--out", help="куда собрать пакет (без него — только показать состав)")
    ap.add_argument("--list", action="store_true", help="состав обоих видов")
    ap.add_argument("--verify", help="сверить уже поставленный пакет с его манифестом")
    ap.add_argument("--allow-partial", action="store_true",
                    help="отладка: собрать без несобранных фронтов")
    args = ap.parse_args()

    if args.verify:
        bad = verify(Path(args.verify))
        print("\n".join(bad) if bad else "пакет сходится с манифестом")
        return 1 if bad else 0
    if args.list or not args.out:
        version = product_version()[0]
        print(f"desk {version}")
        for p in PARTS:
            where = "+".join(p.flavors)
            print(f"  {p.src:<18} -> {p.dest:<14} [{where}]  {p.why}")
        for flavor in FLAVORS:
            print(f"  зависимости {flavor}: {', '.join(requirements(flavor))}")
            miss = check(flavor)
            if miss:
                print("    не хватает: " + "; ".join(miss))
        return 0

    man = build(Path(args.out), args.flavor, clean=True, allow_partial=args.allow_partial)
    print(f"desk {man['version']} ({man['flavor']}): {len(man['files'])} файлов, "
          f"отпечаток {man['digest'][:12]}")
    for part in man["parts"]:
        print(f"  {part['name']:<14} {part['files']:>4}")
    for part in man["skipped"]:
        print(f"  {part['name']:<14}    — не поехало: {part['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
