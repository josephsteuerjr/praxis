# -*- coding: utf-8 -*-
"""Правки агента в своём коде: увидеть их и перенести на новую версию.

Общий для двух исполнителей обновления (28.09, «ужесточить приёмку — и на сервере, и на
десктопе»): исполнитель на сервере (`updater.py` рядом) и установщик на ПК (он зовёт
`python codecarry.py desk …` из СВЕЖЕЙ поставки, до первого запуска новой версии). Правило
одно: код агента против чистой версии, с которой он начинал, — это его правки; каждая
ложится на новую версию, а что не легло — уезжает ему в `data/workspace/update-<версия>/`.

Файл живёт рядом с исполнителем, а не в `app/`: `app/` и `tree/` правит агент, и исполнитель
не исполняет ничего, что агент может переписать (см. шапку `updater.py`). Поэтому здесь
только стандартная библиотека и git — для трёхстороннего слияния.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

CODE_DIRS = ("tree", "app")

#: Что в сравнении кода — не правка агента: байткод и кэши инструментов.
CODE_IGNORE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
CODE_IGNORE_SUFFIXES = (".pyc", ".pyo")
CODE_IGNORE_NAMES = frozenset({".DS_Store"})
#: Файл кода больше этого — не правка, а груз: не переношу и не сравниваю по байтам.
MAX_CODE_FILE = 8 * 1024 ** 2
#: Дифф правок агента одним файлом — не больше; дальше — «обрезан».
MAX_DIFF = 2 * 1024 ** 2
#: Файл больше этого в дифф не идёт (difflib на мегабайтах — минуты процессора).
MAX_DIFF_FILE = 512 * 1024
#: Материалы агенту (стороны того, что не легло, прежний код без базы) — всего не больше.
#: Размер правок задаёт агент: без потолка тысяча больших файлов забила бы диск и память.
MAX_MATERIALS = 256 * 1024 ** 2
#: Чем сливать. На сервере git — в PATH образа исполнителя; на ПК — MinGit поставки
#: (`runtime/git/cmd/git.exe`), его ставит `desk_main`. Нет git — слить нельзя, правка,
#: которую тронул и выпуск, уезжает агенту материалом.
GIT = [os.environ.get("HELENE_GIT") or "git"]


# Открытие без следования ссылкам. На Windows флагов нет (исполнитель живёт на Linux, но
# чистые разборы ниже гоняют и там — стенды).
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
BINARY = getattr(os, "O_BINARY", 0)


#: Обход через дескрипторы есть только на Linux (там исполнитель и живёт).
FWALK = hasattr(os, "fwalk") and bool(NOFOLLOW)


def members(root: Path, *, ignore: bool = True):
    """Члены папки без следования ссылкам: -> (путь от корня, lstat, dir_fd, имя).

    Папки обходятся, но не выдаются; ссылка на папку выдаётся членом, а не обходится.
    На Linux — `os.fwalk`: каждая папка открывается относительно родителя, и открытой
    оказывается ровно та, что была в листинге, — ссылка, которую живой агент подложил
    между листингом и открытием, обход наружу не уводит (с `os.walk` это окно было).
    `dir_fd` действителен, пока идёт обход. На Windows (стенды) — `os.walk`, `dir_fd` —
    None, имя — полный путь. `ignore` — не заходить в кэши и не брать байткод.
    """
    root = str(root)
    if FWALK:
        for base, dirs, files, dfd in os.fwalk(root, follow_symlinks=False):
            head = os.path.relpath(base, root)
            head = "" if head == "." else head.replace(os.sep, "/") + "/"
            keep = []
            for name in dirs:
                if ignore and name in CODE_IGNORE_DIRS:
                    continue
                try:
                    st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    keep.append(name)
                else:
                    yield head + name, st, dfd, name
            dirs[:] = keep
            for name in files:
                if ignore and (name in CODE_IGNORE_NAMES or name.endswith(CODE_IGNORE_SUFFIXES)):
                    continue
                try:
                    st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                except OSError:
                    continue
                yield head + name, st, dfd, name
        return
    for base, dirs, files in os.walk(root, followlinks=False):
        keep = []
        for name in dirs:
            full = os.path.join(base, name)
            if ignore and name in CODE_IGNORE_DIRS:
                continue
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                keep.append(name)
            else:
                yield os.path.relpath(full, root).replace(os.sep, "/"), st, None, full
        dirs[:] = keep
        for name in files:
            if ignore and (name in CODE_IGNORE_NAMES or name.endswith(CODE_IGNORE_SUFFIXES)):
                continue
            full = os.path.join(base, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            yield os.path.relpath(full, root).replace(os.sep, "/"), st, None, full


def read_member(dfd: int | None, name: str, limit: int = MAX_CODE_FILE) -> bytes | None:
    """Содержимое обычного файла без следования ссылке (относительно `dfd`, если он есть)."""
    try:
        fd = os.open(name, os.O_RDONLY | NOFOLLOW | NONBLOCK | BINARY,
                     **({"dir_fd": dfd} if dfd is not None else {}))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        info = os.fstat(fh.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        blob = fh.read(limit + 1)
    return blob if len(blob) <= limit else None


def link_target(dfd: int | None, name: str) -> str:
    try:
        return os.readlink(name, **({"dir_fd": dfd} if dfd is not None else {}))
    except OSError:
        return "?"


def executable(mode: int) -> bool:
    return bool(mode & 0o111)



# --------------------------------------------------------------------------- #
#  Правки агента в своём коде: увидеть и перенести на новую версию
# --------------------------------------------------------------------------- #

def read_plain(path: Path, limit: int = MAX_CODE_FILE) -> bytes | None:
    """Содержимое обычного файла без следования ссылке. Не файл или больше предела — None."""
    return read_member(None, str(path), limit)


def skipped(rel: str, skip: tuple[str, ...]) -> bool:
    """Путь внутри одной из пропускаемых папок (или она сама)."""
    return any(rel == s or rel.startswith(s + "/") for s in skip)


def snapshot(root: Path | None, skip: tuple[str, ...] = ()) -> dict[str, tuple[str, str, bool]]:
    """Папка кода -> {путь: (вид, отпечаток, исполняемый)}; ссылки не разыменовываются.

    Вид: «file» (отпечаток — sha256), «link» (куда ведёт), «big» (файл больше предела —
    отпечаток по размеру), «other» (FIFO, сокет, устройство). Корень-ссылка — пусто:
    папку кода, подменённую ссылкой, я не читаю вовсе. Кэши и байткод — не в счёт.
    `skip` — пути от корня, которые не код агента (на ПК — интерфейс окна `app/static`:
    его ведёт установщик своим правилом, см. ОБНОВЛЕНИЕ.md).
    """
    out: dict[str, tuple[str, str, bool]] = {}
    if root is None or not os.path.isdir(root) or os.path.islink(root):
        return out
    for rel, st, dfd, name in members(root):
        if skip and skipped(rel, skip):
            continue
        if stat.S_ISLNK(st.st_mode):
            out[rel] = ("link", link_target(dfd, name), False)
        elif stat.S_ISREG(st.st_mode):
            blob = read_member(dfd, name)
            out[rel] = (("file", hashlib.sha256(blob).hexdigest(), executable(st.st_mode))
                        if blob is not None else ("big", str(st.st_size), False))
        else:
            out[rel] = ("other", "", False)
    return out


def differs(was: tuple | None, now: tuple | None) -> bool:
    """Правка ли это агента: другое содержимое, другой вид — или бит исполнения, который
    агент ПОСТАВИЛ. Снятый бит правкой не считается: его «снимает» и распаковка без
    режимов (7-Zip, zipfile), и я не отличил бы её от агента. Бит базы None — база
    известна только отпечатками (`hashes_snapshot`): бит исполнения тогда не в счёт."""
    if was is None or now is None:
        return was is not now
    if was[:2] != now[:2]:
        return True
    return was[0] == "file" and was[2] is not None and bool(now[2]) and not was[2]


def code_edits(base: Path | None, mine: Path | None, *, skip: tuple[str, ...] = (),
               base_snap: dict | None = None) -> list[str]:
    """Что в `mine` отличается от чистой `base` — это и есть правки агента."""
    left = base_snap if base_snap is not None else snapshot(base, skip)
    right = snapshot(mine, skip)
    return sorted(rel for rel in set(left) | set(right) if differs(left.get(rel), right.get(rel)))


def hashes_snapshot(table: dict, prefix: str) -> dict[str, tuple[str, str, None]]:
    """Чистая версия, известная только отпечатками: {"tree/agent.py": sha256, …} -> снимок
    одной папки кода (`prefix`) в виде `snapshot`, бит исполнения — неизвестен (None)."""
    head = prefix + "/"
    return {rel[len(head):]: ("file", str(sha), None) for rel, sha in (table or {}).items()
            if isinstance(rel, str) and rel.startswith(head) and isinstance(sha, str)}


def is_binary(*blobs: bytes | None) -> bool:
    return any(blob is not None and b"\x00" in blob[:65536] for blob in blobs)


def merge_text(mine: bytes, base: bytes, theirs: bytes, labels: tuple[str, str, str],
               work: Path) -> tuple[bytes | None, int, str]:
    """Трёхстороннее слияние (`git merge-file --diff3`). -> (текст, конфликтов, почему нет).

    Конфликтов 0 — слилось чисто; больше нуля — текст с метками конфликта (в нём видны
    все три стороны); -1 — слить не вышло вовсе (git нет или он упал).
    """
    work.mkdir(parents=True, exist_ok=True)
    paths = []
    try:
        for name, blob in (("mine", mine), ("base", base), ("theirs", theirs)):
            path = work / f"{name}-{secrets.token_hex(6)}"
            path.write_bytes(blob)
            paths.append(path)
        # merge-file работает и вне репозитория, но git ищет его, поднимаясь от текущей
        # папки процесса, и на битом `.git` выше (стенд 27.09: ссылка worktree на путь
        # Windows) отказывается сливать вовсе. Поэтому — из своей папки и не выше неё.
        env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(work.resolve().parent),
                   GIT_CONFIG_NOSYSTEM="1")
        env.pop("GIT_DIR", None)
        env.pop("GIT_WORK_TREE", None)
        try:
            done = subprocess.run([GIT[0], "merge-file", "-p", "--diff3", "-L", labels[0], "-L", labels[1],
                                   "-L", labels[2], *(str(p) for p in paths)],
                                  capture_output=True, timeout=120, cwd=str(work), env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, -1, f"git merge-file не запустился: {exc}"
    finally:
        for path in paths:
            try:
                path.unlink()
            except OSError:
                pass
    if done.returncode < 0 or done.returncode > 127:
        return None, -1, "git merge-file: " + done.stderr.decode("utf-8", "replace").strip()[:300]
    return done.stdout, done.returncode, ""


def unified(name: str, before: bytes | None, after: bytes | None) -> str:
    """Правка одним куском диффа — агенту видно, что у него было."""
    if is_binary(before, after):
        return f"Binary files a/{name} and b/{name} differ\n"
    left = (before or b"").decode("utf-8", "replace").splitlines(keepends=True)
    right = (after or b"").decode("utf-8", "replace").splitlines(keepends=True)
    return "".join(difflib.unified_diff(left, right,
                                        fromfile=f"a/{name}" if before is not None else "/dev/null",
                                        tofile=f"b/{name}" if after is not None else "/dev/null"))


def _skip_why(was: tuple | None, now: tuple | None) -> str:
    """Почему правку не переношу — словами, с тем, что у агента на этом месте."""
    kind = (now or was or ("?",))[0]
    if now and now[0] == "link":
        what = f"у тебя здесь ссылка → {now[1][:200]}"
    elif now and now[0] == "big":
        what = f"файл больше {MAX_CODE_FILE // 1024 ** 2} МБ"
    elif now and now[0] == "other":
        what = "особый файл (FIFO, сокет, устройство)"
    elif was and was[0] != "file":
        what = f"в чистой версии здесь не обычный файл ({kind})"
    else:
        what = "не обычный файл"
    return what + " — такие не переношу; оригинал остался в прежней версии"


def _blocked(new: Path, rel: str) -> str:
    """Где-то выше файла в новой версии стоит не папка (файл или ссылка) -> её путь, иначе ""."""
    cur = new
    for part in rel.split("/")[:-1]:
        cur = cur / part
        try:
            mode = os.lstat(cur).st_mode
        except FileNotFoundError:
            return ""
        if not stat.S_ISDIR(mode):
            return cur.relative_to(new).as_posix()
    return ""


def carry_code(base: Path | None, mine: Path, new: Path, *, work: Path, labels: tuple[str, str, str],
               prefix: str, budget: list[int] | None = None, base_snap: dict | None = None,
               skip: tuple[str, ...] = ()) -> dict:
    """Перенести правки агента (`mine` против чистой `base`) на новую версию `new` — на месте.

    Правило одно на все случаи: новая версия, где она не спорит с агентом, получает его
    правку; где спорит — остаётся как выпущена, а правка агента уезжает ему материалом.
      * агент правил, выпуск не трогал            -> правка агента как есть;
      * правили оба, слилось                       -> слитое;
      * правили оба, не слилось / двоичный файл    -> вариант выпуска + материал агенту;
      * агент добавил файл                         -> файл агента (если выпуск не добавил свой);
      * агент удалил файл, выпуск его не трогал    -> удалён и в новой;
      * там, где у агента файл, в новой версии папка (или наоборот) -> как у выпуска + материал;
      * ссылки, огромные и особые файлы            -> не переношу, называю.
    Бит исполнения агента переезжает вместе с файлом (снятый — нет, см. `differs`).
    Сбой на одном файле — конфликт этого файла, а не отказ всего обновления.
    `budget` — сколько байт сторон конфликтов ещё можно держать (общий на tree/ и app/):
    сверх него конфликт остаётся в списке, а стороны — нет.

    `base_snap` — чистая версия снимком (`hashes_snapshot`, архив переноса): тогда
    содержимое базы необязательно — где его нет, «выпуск файл не трогал» узнаётся по
    отпечатку, а правку, которую тронул и выпуск, слить не с чем — она уезжает агенту.
    `skip` — пути от корня папки, которые не код агента (см. `snapshot`).
    -> отчёт: edited, carried, merged, conflicts, skipped, materials (байты), diff.
    """
    budget = budget if budget is not None else [MAX_MATERIALS]
    rep: dict = {"edited": [], "carried": [], "merged": [], "conflicts": [], "skipped": [],
                 "materials": [], "diff": []}
    diff_left = [MAX_DIFF]
    left = base_snap if base_snap is not None else snapshot(base, skip)
    right = snapshot(mine, skip)

    def conflict(name: str, why: str, sides: dict) -> None:
        size = sum(len(blob) for blob in sides.values() if blob is not None)
        if size > budget[0]:
            sides, why = {}, why + " (стороны не поместились в материалы)"
        else:
            budget[0] -= size
        rep["conflicts"].append({"path": name, "why": why})
        rep["materials"].append({"path": name, "why": why, **sides})

    def add_diff(name: str, before: bytes | None, after: bytes | None) -> None:
        if diff_left[0] <= 0:
            return
        if max(len(before or b""), len(after or b"")) > MAX_DIFF_FILE:
            text = f"--- a/{name}\n+++ b/{name}\n@@ файл больше {MAX_DIFF_FILE // 1024} КБ — дифф не строю @@\n"
        else:
            text = unified(name, before, after)
        diff_left[0] -= len(text)
        rep["diff"].append(text if diff_left[0] > 0 else text + "\n… дифф обрезан: правок больше, чем помещается\n")

    for rel in sorted(set(left) | set(right)):
        was, now = left.get(rel), right.get(rel)
        if not differs(was, now):
            continue
        name = f"{prefix}/{rel}"
        rep["edited"].append(name)
        if (now and now[0] != "file") or (was and was[0] != "file"):
            rep["skipped"].append({"path": name, "why": _skip_why(was, now)})
            continue
        sides: dict = {}
        try:
            mine_blob = read_plain(mine / rel) if now else None
            base_blob = read_plain(base / rel) if (was and base is not None) else None
            # База только отпечатком: содержимого нет — и это не сбой чтения.
            by_print = bool(was) and base_blob is None and base_snap is not None
            if (now and mine_blob is None) or (was and base_blob is None and not by_print):
                rep["skipped"].append({"path": name, "why": "файл изменился или пропал, пока я его читал"})
                continue

            def as_base(blob: bytes | None) -> bool:
                if blob is None or not was:
                    return False
                if base_blob is not None:
                    return blob == base_blob
                return hashlib.sha256(blob).hexdigest() == was[1]

            sides = {"mine": mine_blob, "base": base_blob}
            target = new / rel
            wall = _blocked(new, rel) if now else ""
            if wall:
                conflict(name, f"в новой версии `{prefix}/{wall}` — файл, а у тебя там папка", sides)
                continue
            present = target.exists() or target.is_symlink()
            theirs = read_plain(target) if present else None
            if present and theirs is None:
                conflict(name, "в новой версии на этом месте не файл (папка или ссылка)", sides)
                continue
            sides["theirs"] = theirs
            if by_print and diff_left[0] > 0:
                rep["diff"].append(f"# {name}: чистая версия известна только отпечатком — "
                                   "ниже твой файл целиком\n")
            add_diff(name, base_blob, mine_blob)
            mine_x = bool(now and now[2])
            theirs_x = bool(theirs is not None and executable(os.lstat(target).st_mode))
            if now and was:                                          # агент правил
                if theirs is None:
                    conflict(name, "в новой версии этого файла нет", sides)
                elif as_base(theirs):
                    _write_code(target, mine_blob, mine_x or theirs_x)
                    rep["carried"].append(name)
                elif theirs == mine_blob:
                    _write_code(target, mine_blob, mine_x or theirs_x)
                    rep["carried"].append(name)
                elif by_print:
                    conflict(name, "выпуск тоже менял этот файл, а чистой прежней версии, чтобы слить "
                                   "правки, у меня нет — только её отпечаток", sides)
                elif is_binary(mine_blob, base_blob, theirs):
                    conflict(name, "двоичный файл, и выпуск его тоже менял", sides)
                else:
                    merged, count, why = merge_text(mine_blob, base_blob, theirs, labels, work)
                    if merged is not None and count == 0:
                        _write_code(target, merged, mine_x or theirs_x)
                        rep["merged"].append(name)
                    elif merged is not None:
                        conflict(name, f"ты и выпуск меняли одни и те же строки (конфликтов: {count})",
                                 {**sides, "merged": merged})
                    else:
                        conflict(name, f"слить не вышло: {why}", sides)
            elif now:                                                # агент добавил
                if theirs is None or theirs == mine_blob:
                    _write_code(target, mine_blob, mine_x or theirs_x)
                    rep["carried"].append(name)
                else:
                    conflict(name, "выпуск добавил свой файл с тем же именем", sides)
            else:                                                    # агент удалил
                if theirs is None:
                    rep["carried"].append(name)
                elif as_base(theirs):
                    target.unlink()
                    rep["carried"].append(name)
                else:
                    conflict(name, "ты этот файл удалил, а выпуск его изменил — в новой версии он есть",
                             sides)
        except OSError as exc:
            conflict(name, f"не перенёсся: {exc}", sides)
    return rep


def _write_code(target: Path, blob: bytes, exec_bit: bool = False) -> None:
    """Запись в НОВУЮ поставку: она распакована мной и агенту ещё не видна."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".tmp-{target.name}-{secrets.token_hex(4)}")
    try:
        tmp.write_bytes(blob)
        tmp.chmod(0o755 if exec_bit else 0o644)
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def materials_readme(report: dict, from_version: str, to_version: str, *, no_base: str = "",
                     old_code_differs: bool = False) -> str:
    """Записка агенту рядом с материалами: что перенесено, что нет и что с этим делать."""
    lines = [f"# Обновление {from_version or '?'} → {to_version}: твои правки кода", ""]
    if no_base and old_code_differs:
        lines += [f"Сравнить твой код с чистой {from_version or 'прежней версией'} не с чем: {no_base}.",
                  f"В `old-code/` рядом — файлы твоего прежнего кода, которые отличаются от {to_version}:",
                  "там и правки выпуска, и твои, если были. Сравни их с новым сам и перенеси своё.", ""]
    elif no_base:
        lines += [f"Сравнить твой код с чистой {from_version or 'прежней версией'} не с чем: {no_base}.",
                  "Твой прежний код целиком — в `old-code/` рядом: сравни его с новым сам и",
                  "перенеси то, что было твоим.", ""]
    else:
        lines += ["Исполнитель сравнил твой `tree/` и `app/` с чистым исходником "
                  f"{from_version} — отличия и есть твои правки — и перенёс их на {to_version}.", ""]

    def block(title: str, rows: list) -> None:
        if rows:
            lines.append(f"## {title}")
            lines.extend(f"- `{row['path']}` — {row['why']}" if isinstance(row, dict) else f"- `{row}`"
                         for row in rows)
            lines.append("")

    block("Перенесено как было (выпуск эти файлы не трогал)", report.get("carried") or [])
    block("Слито с правками выпуска", report.get("merged") or [])
    block("НЕ легло — в новой версии стоит вариант выпуска, твой рядом", report.get("conflicts") or [])
    block("Не переносил", report.get("skipped") or [])
    if report.get("conflicts"):
        lines += ["Для каждого, что не легло, рядом лежат стороны: `*.mine` — твой вариант,",
                  "`*.base` — чистая прежняя версия, `*.theirs` — новая, `*.merged` — попытка",
                  "слияния с метками конфликта (`<<<<<<<`, `|||||||`, `=======`, `>>>>>>>`).",
                  "Реши сам: перенести правку вручную (и перезапуститься) или жить без неё —",
                  "и скажи владельцу, что решил.", ""]
    lines += ["Все твои правки относительно прежней версии одним куском — `edits.diff`."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
#  На ПК: чистая версия у установки и перенос правок при обновлении
# --------------------------------------------------------------------------- #
#
# До 1.2.5 установщик ПК менял `tree/` и `app/` целиком, и правки агента в своём коде
# пропадали при каждом обновлении (на сервере их переносил исполнитель). Теперь так же, как
# на сервере: установщик, подменив программу и до её первого запуска, зовёт отсюда `desk`.

#: Чистый код версии — `<установка>/pristine/<версия>.zip`: база следующего обновления.
PRISTINE_DIR = "pristine"
PRISTINE_MANIFEST = "manifest.json"
#: Что в папках кода на ПК — не код агента: интерфейс окна (у него своё правило, ОБНОВЛЕНИЕ.md).
DESK_SKIP = {"tree": (), "app": ("static", "static.prev")}
#: Отпечатки выпусков, у которых чистой копии у установки ещё не было (до 1.2.5):
#: {"1.2.4": {"tree/agent.py": sha256, …}} — собирает `installer/code_prints.py`.
SHIPPED = Path(__file__).resolve().with_name("shipped-code.json")
#: Снимок кода сразу после переноса — по нему откат видит правки агента НА испытании.
AFTER_CARRY = "after-carry-{version}.json"


def pristine_path(install: Path, version: str) -> Path:
    return Path(install) / PRISTINE_DIR / f"{version}.zip"


def data_of(install: Path) -> Path:
    """Папка данных установки: ключ `tree` в helene.json (по умолчанию `data`)."""
    raw = "data"
    try:
        cfg = json.loads((Path(install) / "helene.json").read_text("utf-8-sig"))
        raw = str(cfg.get("tree") or "data") if isinstance(cfg, dict) else "data"
    except (OSError, ValueError):
        pass
    path = Path(raw)
    return path if path.is_absolute() else Path(install) / path


def code_prints(root: Path, skip: dict = DESK_SKIP) -> dict[str, str]:
    """{"tree/…": sha256} обычных файлов кода версии — её отпечатки."""
    out: dict[str, str] = {}
    for name in CODE_DIRS:
        for rel, kind in snapshot(Path(root) / name, tuple(skip.get(name, ()))).items():
            if kind[0] == "file":
                out[f"{name}/{rel}"] = kind[1]
    return out


def write_pristine(root: Path, version: str, dest: Path, skip: dict = DESK_SKIP) -> int:
    """Чистый код версии (как выпущен) одним zip, с отпечатками внутри. -> файлов."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}")
    prints: dict[str, str] = {}
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in CODE_DIRS:
                for rel, st, dfd, member in members(Path(root) / name):
                    if skipped(rel, tuple(skip.get(name, ()))) or not stat.S_ISREG(st.st_mode):
                        continue
                    blob = read_member(dfd, member)
                    if blob is None:
                        continue
                    info = zipfile.ZipInfo(f"{name}/{rel}")
                    info.external_attr = (0o755 if executable(st.st_mode) else 0o644) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(info, blob)
                    prints[f"{name}/{rel}"] = hashlib.sha256(blob).hexdigest()
            zf.writestr(PRISTINE_MANIFEST, json.dumps({"version": version, "files": prints},
                                                      ensure_ascii=False))
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return len(prints)


def open_pristine(zip_path: Path, work: Path) -> Path | None:
    """Чистая версия из zip -> временная папка с `tree/` и `app/` (None — нет или битый)."""
    if not Path(zip_path).is_file():
        return None
    dest = Path(work) / f"base-{secrets.token_hex(4)}"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                parts = info.filename.split("/")
                if (info.is_dir() or len(parts) < 2 or parts[0] not in CODE_DIRS
                        or any(p in ("", ".", "..") for p in parts)):
                    continue
                target = dest.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
                if os.name != "nt" and executable(info.external_attr >> 16):
                    target.chmod(0o755)
    except (OSError, zipfile.BadZipFile, ValueError, RuntimeError):
        shutil.rmtree(dest, ignore_errors=True)
        return None
    return dest


def flavor_of(install: Path) -> str:
    """Платформа поставки из её паспорта (`windows`, `macos`, …); не прочиталась — ""."""
    try:
        build = json.loads((Path(install) / "helene-build.json").read_text("utf-8-sig"))
        return str(((build.get("desk") or {}).get("flavor")) or "")
    except (OSError, ValueError, AttributeError):
        return ""


def shipped_prints(version: str, flavor: str = "", path: Path | None = None) -> dict | None:
    """Отпечатки выпуска `version` этой платформы из поставки (None — их нет). Ключ —
    `<версия>/<платформа>`: `app/` у Windows и macOS разный, и отпечатки чужой платформы
    читались бы правками агента."""
    try:
        table = json.loads(Path(path or SHIPPED).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(table, dict) or not version or not flavor:
        return None
    got = table.get(f"{version}/{flavor}")
    return got if isinstance(got, dict) and got else None


def materials_folder(data: Path, version: str, tag: str = "update") -> Path:
    """Свежая папка материалов в доме агента: `<tag>-<версия>`, занята — с меткой времени."""
    ws = Path(data) / "workspace"
    folder = ws / f"{tag}-{version}"
    if folder.exists() or folder.is_symlink():
        folder = ws / f"{tag}-{version}-{time.strftime('%Y%m%dT%H%M%S')}"
    return folder


def _put(folder: Path, parts: list[str], blob: bytes) -> None:
    target = folder.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(blob)


def _plain_report(report: dict) -> dict:
    """Отчёт без байтов — для расписки и JSON установщику."""
    return {k: v for k, v in report.items() if k not in ("materials", "diff")}


def carry_install(old: Path, new: Path, *, data: Path, from_version: str, to_version: str,
                  work: Path, prints_path: Path | None = None) -> dict:
    """Правки агента в коде прежней программы (`old`) — на новую (`new`), на месте.

    База — чистая прежняя версия: её zip у установки (`pristine/`, с 1.2.5; при подмене он
    переехал в `new` вместе со всем владельческим), иначе отпечатки выпуска
    (`shipped-code.json`), иначе сравнить не с чем — тогда агенту уезжают файлы прежнего
    кода, которые отличаются от новой версии. Материалы — `data/workspace/update-<to>/`.
    """
    old, new, work = Path(old), Path(new), Path(work)
    report: dict = {"desktop": True, "mounted": True, "edited": [], "carried": [], "merged": [],
                    "conflicts": [], "skipped": [], "folder": ""}
    work.mkdir(parents=True, exist_ok=True)
    # Чистая копия прежней версии: при обновлении она переехала в `new`; при переезде из
    # другой папки («продолжить с найденной памятью») — лежит у прежней установки.
    base_root = None
    for home in (new, old):
        if base_root is None and from_version:
            base_root = open_pristine(pristine_path(home, from_version), work)
    table = (None if base_root is not None
             else shipped_prints(from_version, flavor_of(new), prints_path))
    report["base"] = "pristine" if base_root is not None else ("prints" if table else "")
    try:
        if base_root is None and table is None:
            why = (f"чистой копии {from_version} у установки нет, и её отпечатков в поставке тоже"
                   if from_version else "версия прежней установки неизвестна")
            _give_differing_code(old, new, data, report, why, from_version, to_version)
            return _plain_report(report)
        labels = (f"агент ({from_version})", f"чистая {from_version}", f"выпуск {to_version}")
        materials: list[dict] = []
        diffs: list[str] = []
        budget = [MAX_MATERIALS]
        for name in CODE_DIRS:
            rep = carry_code(base_root / name if base_root is not None else None, old / name,
                             new / name, work=work, labels=labels, prefix=name, budget=budget,
                             base_snap=None if base_root is not None else hashes_snapshot(table, name),
                             skip=tuple(DESK_SKIP[name]))
            for key in ("edited", "carried", "merged", "conflicts", "skipped"):
                report[key].extend(rep[key])
            materials.extend(rep["materials"])
            diffs.extend(rep["diff"])
        if report["edited"]:
            folder = materials_folder(data, to_version)
            try:
                _put(folder, ["README.md"],
                     materials_readme(report, from_version, to_version).encode("utf-8"))
                _put(folder, ["edits.diff"], "".join(diffs).encode("utf-8")[:MAX_DIFF])
                for row in materials:
                    parts = row["path"].split("/")
                    for side in ("mine", "base", "theirs", "merged"):
                        blob = row.get(side)
                        if blob is not None:
                            _put(folder, parts[:-1] + [f"{parts[-1]}.{side}"], blob)
                report["folder"] = f"workspace/{folder.name}"
            except OSError as exc:
                report["materials_error"] = f"материалы не легли в workspace/{folder.name}: {exc}"
    finally:
        if base_root is not None:
            shutil.rmtree(base_root, ignore_errors=True)
    report["summary"] = (f"правок агента в коде: {len(report['edited'])}; перенесено "
                         f"{len(report['carried'])}, слито {len(report['merged'])}, не легло "
                         f"{len(report['conflicts'])}"
                         + (f", не переносил {len(report['skipped'])}" if report["skipped"] else ""))
    return _plain_report(report)


def _give_differing_code(old: Path, new: Path, data: Path, report: dict, why: str,
                         from_version: str, to_version: str) -> None:
    """Без базы правок не видно: файлы прежнего кода, что отличаются от новой версии, — агенту."""
    report["no_base"] = why
    folder = materials_folder(data, to_version)
    written, left = 0, MAX_MATERIALS
    try:
        for name in CODE_DIRS:
            skip = tuple(DESK_SKIP[name])
            theirs = snapshot(new / name, skip)
            for rel, kind in snapshot(old / name, skip).items():
                if kind[0] != "file" or theirs.get(rel, ("",))[:2] == kind[:2]:
                    continue
                blob = read_plain(old / name / rel)
                if blob is None or len(blob) > left:
                    continue
                left -= len(blob)
                _put(folder, ["old-code", name, *rel.split("/")], blob)
                written += 1
        if written:
            _put(folder, ["README.md"], materials_readme(report, from_version, to_version, no_base=why,
                                                         old_code_differs=True).encode("utf-8"))
            report["folder"] = f"workspace/{folder.name}"
    except OSError as exc:
        report["materials_error"] = f"прежний код не лёг в workspace/{folder.name}: {exc}"
    report["old_code_files"] = written
    report["summary"] = (f"сравнить код с чистой {from_version or 'прежней'} не с чем; файлов прежнего "
                         f"кода, отличных от {to_version}: {written}")


def after_carry_path(install: Path, version: str) -> Path:
    return Path(install) / PRISTINE_DIR / AFTER_CARRY.format(version=version)


def remember_after_carry(install: Path, version: str) -> None:
    """Снимок кода новой версии сразу после переноса: по нему откат увидит правки агента
    уже В НОВОЙ версии (на испытании) и отдаст их ему."""
    snap = {name: snapshot(Path(install) / name, tuple(DESK_SKIP[name])) for name in CODE_DIRS}
    path = after_carry_path(install, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap), "utf-8")


def give_trial_edits(install: Path, version: str, data: Path | None = None) -> dict:
    """Перед откатом: что агент поменял в коде новой версии за испытание — ему в workspace.

    Откат вернёт прежнюю программу с его прежним кодом, а правки, сделанные на испытании,
    иначе пропали бы молча. -> {"edited": [...], "folder": "..."}
    """
    install = Path(install)
    data = Path(data) if data is not None else data_of(install)
    try:
        before = json.loads(after_carry_path(install, version).read_text("utf-8"))
    except (OSError, ValueError):
        return {"edited": [], "folder": "", "note": "снимка кода после переноса нет — сравнить не с чем"}
    edited: list[str] = []
    gone: list[str] = []
    folder = materials_folder(data, version, tag="trial-edits")
    try:
        for name in CODE_DIRS:
            was = {rel: tuple(v) for rel, v in (before.get(name) or {}).items()}
            now = snapshot(install / name, tuple(DESK_SKIP[name]))
            for rel in sorted(set(was) | set(now)):
                if not differs(was.get(rel), now.get(rel)):
                    continue
                edited.append(f"{name}/{rel}")
                blob = read_plain(install / name / rel) if now.get(rel, ("",))[0] == "file" else None
                if blob is not None:
                    _put(folder, [name, *rel.split("/")], blob)
                else:
                    gone.append(f"{name}/{rel}")
        if edited:
            _put(folder, ["README.md"], (
                f"# Правки кода на испытании {version}\n\nОбновление до {version} откачено. Здесь — "
                "файлы, которые ты поменял в коде новой версии за испытание, такими, какими они были "
                "в миг отката. В прежней версии их нет — перенеси сам, если они нужны.\n\n"
                + "\n".join(f"- `{p}`" + (" — удалён или не обычный файл" if p in gone else "")
                            for p in edited) + "\n").encode("utf-8"))
    except OSError as exc:
        return {"edited": edited, "folder": "", "note": f"правки на испытании не легли: {exc}"}
    return {"edited": edited, "folder": f"workspace/{folder.name}" if edited else ""}


def _bundled_git(install: Path) -> str:
    for cand in (Path(install) / "runtime" / "git" / "cmd" / "git.exe",
                 Path(install) / "runtime" / "git" / "bin" / "git"):
        if cand.is_file():
            return str(cand)
    return GIT[0]


def desk_main(argv: list[str]) -> int:
    """Вход установщика ПК. Ответ — одна строка JSON в stdout.

      desk --old <прежняя программа> --new <установка> --from V --to W
          чистая W — в <установка>/pristine/W.zip (ДО переноса), правки агента — на W,
          снимок кода после переноса — для отката;
      trial-edits --install <установка> --to W
          перед откатом — правки агента за испытание ему в workspace.
    """
    import argparse
    parser = argparse.ArgumentParser(prog="codecarry.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    desk = sub.add_parser("desk")
    desk.add_argument("--old", required=True)
    desk.add_argument("--new", required=True)
    desk.add_argument("--from", dest="from_version", default="")
    desk.add_argument("--to", dest="to_version", required=True)
    trial = sub.add_parser("trial-edits")
    trial.add_argument("--install", required=True)
    trial.add_argument("--to", dest="to_version", required=True)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "trial-edits":
            out = give_trial_edits(Path(args.install), args.to_version)
            print(json.dumps({"ok": True, **out}, ensure_ascii=False))
            return 0
        new, old = Path(args.new), Path(args.old)
        GIT[0] = _bundled_git(new)
        files = write_pristine(new, args.to_version, pristine_path(new, args.to_version))
        work = new / PRISTINE_DIR / f".work-{secrets.token_hex(4)}"
        try:
            report = carry_install(old, new, data=data_of(new), from_version=args.from_version,
                                   to_version=args.to_version, work=work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        remember_after_carry(new, args.to_version)
        # Старые чистые копии — прочь: нужны нынешняя (база следующего обновления) и прежняя
        # (база, если откатят); остальное — вес без смысла.
        keep = {f"{args.to_version}.zip", f"{args.from_version}.zip",
                AFTER_CARRY.format(version=args.to_version)}
        for item in (new / PRISTINE_DIR).iterdir():
            if item.is_file() and item.name not in keep:
                try:
                    item.unlink()
                except OSError:
                    pass
        print(json.dumps({"ok": True, "pristine_files": files, **report}, ensure_ascii=False))
        return 0
    except Exception as exc:                       # установщику — словами, не трассой
        print(json.dumps({"ok": False, "why": f"{type(exc).__name__}: {exc}"[:500]},
                         ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(desk_main(sys.argv[1:]))
