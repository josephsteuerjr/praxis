"""Поиск по файлам отдельным процессом: `python -I -S hands_search.py`, запрос JSON в stdin.

Отдельный файл ради старта: дом обходится десятками частей, и дочернему процессу незачем
поднимать logging/subprocess/tempfile. Только stdlib; вызывают hands.search и hands.walk.
Основной процесс файлов поиска не читает: при сбое здесь ответ «не знаю», а не второй обход.
"""
import fnmatch
import json
import os
import sys
import time


def valid_glob(mask):
    # A glob is a selector *within* its root, never a second path resolver.
    import pathlib
    return (bool(mask) and not pathlib.PurePath(mask).is_absolute()
            and ".." not in mask.split("/") and "\\" not in mask)


def scoped_file(name, root):
    """Allow file links only to this resolved root; never traverse directory links.

    Explicit root links were resolved by the caller. Implicit directory links (even
    in-root ones) are excluded to avoid cycles/duplicate subtrees in every route.
    This is a stable-tree scope contract, not a race-proof OS sandbox.
    """
    import pathlib
    path, base = pathlib.Path(name), pathlib.Path(root).resolve()
    try:
        path.relative_to(base)
        if any(parent.is_symlink() for parent in path.parents if parent != base
               and base in parent.parents):
            return False
        path.resolve().relative_to(base)
        return path.is_file()
    except (OSError, ValueError, RuntimeError):
        return False


def collect(start, recursive, mask, skip_dirs, budget, deadline, scope_root=None):
    """Файлы под маску, предсказуемым порядком. -> (пути, упёрлись ли в срок)."""
    found = []
    stack = [start]
    while stack:
        if deadline is not None and time.monotonic() > deadline:
            return found, True
        folder = stack.pop()
        try:
            with os.scandir(folder) as listing:
                entries = sorted(listing, key=lambda entry: entry.name)
        except OSError:
            continue
        nested = []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive and entry.name not in skip_dirs:
                        nested.append(entry.path)
                elif scoped_file(entry.path, scope_root or start) and fnmatch.fnmatchcase(entry.name, mask):
                    found.append(entry.path)
                    if len(found) >= budget:
                        return found, False
            except OSError:
                continue
        stack.extend(reversed(nested))
    return found, False


def search(request):
    root, literal, cap = request["root"], request["literal"], int(request["cap"])
    seconds, limit = float(request["seconds"]), int(request["max_bytes"])
    deadline = time.monotonic() + seconds if seconds > 0 else None
    glob = request["glob"]
    if not valid_glob(glob):
        return {"ok": False, "msg": "плохая маска: только относительная внутри корня, без .."}
    mask = glob[3:] if glob.startswith("**/") else glob
    if "/" in mask:
        # Direct hands.search also supports composite globs, with literal matching.
        import re
        result = walk(dict(request, pattern=re.escape(literal), scan_cap=request["budget"],
                           skip_runs=False))
        if not result.get("ok"):
            return result
        return {"ok": True, "files_seen": result["files_seen"],
                "capped": result["stop"] in ("cap", "scan"),
                "hits": [f"{p}:{n}: {text}" for p, n, text in result["rows"]],
                "out_of_time": result["stop"] == "time", "too_large": result["too_large"]}
    files, out_of_time = collect(root, glob.startswith("**/"), glob.rsplit("/", 1)[-1],
                                 frozenset(request["skip_dirs"]), int(request["budget"]), deadline, request.get("scope_root", root))
    files.sort()
    needle = literal.encode("utf-8")
    hits = []
    too_large = 0
    for name in files:
        if len(hits) >= cap:
            break
        if deadline is not None and time.monotonic() > deadline:
            out_of_time = True
            break
        try:
            if os.stat(name).st_size > limit:
                too_large += 1
                continue
            with open(name, "rb") as fh:
                head = fh.read(8192)
                if b"\0" in head:
                    continue  # бинарный файл
                raw = head + fh.read()
        except OSError:
            continue
        if needle not in raw:
            continue
        rel = os.path.relpath(name, root).replace(os.sep, "/").encode("utf-8", "replace").decode("utf-8")
        for number, line in enumerate(raw.decode("utf-8", "replace").split("\n"), 1):
            if literal in line:
                hits.append(f"{rel}:{number}: {line.strip()[:180]}")
                if len(hits) >= cap:
                    break
    return {"ok": True, "files_seen": len(files), "capped": len(hits) >= cap, "hits": hits,
            "out_of_time": out_of_time, "too_large": too_large}


def walk(request):
    """Регулярка или составная маска: ленивый обход с потолками мастерской и потолком размера.

    `re` и `pathlib` подключаются только здесь: литерал запускают десятками за один обход дома."""
    import pathlib
    import re

    if not valid_glob(request["glob"]):
        return {"ok": False, "msg": "плохая маска: только относительная внутри корня, без .."}
    base = pathlib.Path(request["root"]).resolve()
    scope = pathlib.Path(request.get("scope_root", base)).resolve()
    rx = re.compile(request["pattern"])
    cap, scan_cap, limit = int(request["cap"]), int(request["scan_cap"]), int(request["max_bytes"])
    seconds = float(request["seconds"])
    deadline = time.monotonic() + seconds if seconds > 0 else None
    skip_dirs = frozenset(request["skip_dirs"])
    rows, files_seen, scanned, too_large, stop = [], 0, 0, 0, ""
    try:
        for path in base.glob(request["glob"]):
            scanned += 1
            if scan_cap > 0 and scanned > scan_cap:
                stop = "scan"
                break
            # Часы не на каждом пути: сам вызов дороже проверки маски. После чтения файла — всегда.
            if deadline is not None and not scanned % 256 and time.monotonic() > deadline:
                stop = "time"
                break
            try:
                rel = path.relative_to(base)
                # Exclusions apply within this traversal, not to its ancestors
                # (nor to the filename). Scope can span home budget partitions.
                if not scoped_file(path, scope) or skip_dirs.intersection(rel.parts[:-1]):
                    continue
                if request["skip_runs"] and rel.parts[:2] == ("memory", "runs"):
                    continue
                files_seen += 1
                if path.stat().st_size > limit:
                    too_large += 1
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, ValueError, RuntimeError):
                continue
            name = str(rel).encode("utf-8", "replace").decode("utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    rows.append([name, number, line.strip()[:180]])
                    if len(rows) > cap:
                        break
            if len(rows) >= cap:
                stop = "cap"
                break
            if deadline is not None and time.monotonic() > deadline:
                stop = "time"
                break
    except (ValueError, NotImplementedError) as exc:
        return {"ok": False, "msg": f"плохая маска: {exc}"}
    return {"ok": True, "rows": rows, "files_seen": files_seen, "scanned": scanned, "stop": stop,
            "too_large": too_large}


if __name__ == "__main__":
    query = json.load(sys.stdin)
    json.dump(walk(query) if query.get("mode") == "walk" else search(query), sys.stdout)
