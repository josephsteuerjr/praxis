#!/usr/bin/env python3
"""Ядро и слой Элен: сверить, что издание объявлено честно, и снять отпечатки.

Раскладка 10.09 объявила продукт так: ядро агента живёт в `praxis/`, а Windows-
издание — СЛОЕМ в `helene/core`, где лежат только те файлы, которые Элен несёт
иначе. Красивое объявление, но проверять его было нечем, а непроверяемое
объявление разъезжается с делом молча — тот же класс, что уже стрелял на реле
(09.09 в поставку уехал бинарь от исходника недельной давности, и узнали об этом
случайно).

    python installer/core_src.py --check    # слой = настоящая разница?
    python installer/core_src.py --digest   # отпечатки ядра и слоя

Что сверяется. Берём три дерева — ядро (`praxis/`), слой (`helene/core`) и нашу
рабочую копию — и спрашиваем: совпадает ли объявленный слой с ФАКТИЧЕСКОЙ
разницей между ядром и рабочей копией. Три исхода, и каждый значит своё:

* **не объявлено** — файл расходится, а в слое его нет. Либо ядро отстало от
  живого (её экспорт делается её рукой и делается не каждый день), либо слой не
  полон. Это НЕ «наш файл» по умолчанию: спутать её невыложенную починку с нашей
  правкой — как раз то, из-за чего «одно ядро» было ложной целью;
* **объявлено зря** — файл в слое, а расхождения нет: наша правка уехала в ядро
  или была отозвана, и запись протухла;
* **только у нас** — файла нет ни в ядре, ни в слое.

⚠ Что этот прибор НЕ делает: он не решает, чья правка. Отличить её невыложенную
починку от нашей может только человек или она сама — прибор лишь не даёт
принять расхождение за пустоту.

Переводы строк приводятся к LF: копия на Windows и оригинал с Linux-сервера иначе
разошлись бы каждым файлом, и сверка не значила бы ничего (тот же приём, что в
`relay_src.py`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

DESK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESK))
import layout  # noqa: E402 — где на диске лежат соседи раскладки

ROOT = layout.ROOT                       # раскладка 10.09: praxis/, helene/, desk/, pult/
CORE_DEFAULT = layout.CORE
LAYER_DEFAULT = layout.LAYER
STAMP = "CORE-SOURCE.json"

#: Чего в сверке нет и не должно быть. `soul/` — её письмо (конституция, навыки):
#: оно ОБЯЗАНО отличаться, и объявлять это слоем значило бы объявлять слоем её
#: личность. `memory/`, `workspace/`, `data/`, `private/` — жизнь агента, а не код.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".vectors", ".proposals",
             "memory", "workspace", "data", "private", "soul", "target", "node_modules"}

#: Чего нет в сверке по имени: следы среды и снимки «до правки», которые дерево
#: копит рядом с файлами. Их расхождение не значит ничего.
SKIP_SUFFIXES = (".pyc", ".pyo", ".bak", ".session-journal")
SKIP_PREFIXES = (".env",)

#: Машинные файлы: пишет их прогон, а не человек, и расходятся они ВСЕГДА — на каждой
#: машине свои секунды. В отчёте это шум, который топит настоящее: 10.09 замеры дали 423
#: строки расхождения на файле, о котором нечего решать.
SKIP_NAMES = {".praxis_test_durations.json"}


def _skip(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in SKIP_DIRS for p in parts):
        return True
    name = parts[-1]
    if name in SKIP_NAMES:
        return True
    if name.endswith(SKIP_SUFFIXES) or name.startswith(SKIP_PREFIXES):
        return True
    # `agent.py.pre-что-то-1784583545` — снимок дерева перед правкой, не файл кода.
    return ".pre-" in name


def files(root: Path) -> dict[str, Path]:
    """Файлы дерева относительным путём -> путь на диске."""
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not _skip(rel):
            out[rel] = path
    return out


def sha(path: Path) -> str:
    """Отпечаток содержимого с одной нормой переводов строк."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def digest(root: Path) -> tuple[str, dict[str, str]]:
    """Отпечаток дерева -> (общий sha256, файл -> sha256)."""
    each = {rel: sha(p) for rel, p in files(root).items()}
    total = hashlib.sha256(
        "\n".join(f"{rel} {s}" for rel, s in sorted(each.items())).encode("utf-8")
    ).hexdigest()
    return total, each


def git_head(root: Path) -> tuple[str, bool]:
    """Коммит репозитория и грязна ли ИМЕННО ЭТА папка; пусто — не репозиторий.

    ⚠ `status --porcelain` без пути отвечает про весь репозиторий, а спрашиваем
    мы про ядро. В раскладке 10.09 ядро — одна папка из четырёх в общем
    репозитории, и правка в `desk/` объявляла бы грязным ядро, которого никто не
    трогал. Паспорт бы врал ровно в том поле, ради которого он и заведён.
    """
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=30)
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "."],
                               capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return "", False
    if head.returncode != 0:
        return "", False
    return head.stdout.strip(), bool(dirty.stdout.strip())


def compare(core: Path, layer: Path, tree: Path) -> dict:
    """Слой против фактической разницы ядра и рабочей копии."""
    core_f, layer_f, tree_f = files(core), files(layer), files(tree)
    declared = set(layer_f)

    differ, only_ours = set(), set()
    for rel, path in tree_f.items():
        base = core_f.get(rel)
        if base is None:
            only_ours.add(rel)
        elif sha(base) != sha(path):
            differ.add(rel)

    return {
        "core": str(core), "layer": str(layer), "tree": str(tree),
        "counts": {"core": len(core_f), "layer": len(layer_f), "tree": len(tree_f)},
        # Расходится, а в слое не объявлено. Причина — либо отставшее ядро, либо
        # неполный слой; прибор их не различает и не притворяется, что может.
        "undeclared": sorted(differ - declared),
        # Объявлено, а расхождения нет: запись слоя протухла.
        "stale": sorted(declared - differ - only_ours),
        # Наш файл, которого в ядре нет вовсе.
        "only_ours": sorted(only_ours - declared),
        "declared_ok": sorted(declared & differ),
        "gone": sorted(r for r in declared if r not in tree_f),
    }


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def stamp(root: Path = CORE_DEFAULT) -> dict:
    """Что записано о происхождении ядра; пусто — не записано ничего."""
    return _read(root.parent / STAMP)


def passport(core: Path, layer: Path) -> dict:
    """Отпечатки для паспорта сборки: чем было ядро и чем был слой.

    Именно ДВА отпечатка, а не один по собранному дереву: собранное дерево не
    отвечает на вопрос «какого ядра эта поставка», а он и есть главный.
    """
    core_total, core_each = digest(core)
    layer_total, layer_each = digest(layer)
    core_head, core_dirty = git_head(core)
    return {
        "core": {"path": str(core), "files": len(core_each), "digest": core_total,
                 "head": core_head, "dirty": core_dirty},
        "layer": {"path": str(layer), "files": len(layer_each), "digest": layer_total,
                  "names": sorted(layer_each)},
    }


def _report(res: dict) -> int:
    c = res["counts"]
    print(f"ядро  : {res['core']} — {c['core']} файлов")
    print(f"слой  : {res['layer']} — {c['layer']} файлов")
    print(f"дерево: {res['tree']} — {c['tree']} файлов")
    print()
    print(f"объявлено и вправду расходится : {len(res['declared_ok'])}")
    print(f"НЕ ОБЪЯВЛЕНО, но расходится    : {len(res['undeclared'])}")
    print(f"объявлено зря (совпадает)      : {len(res['stale'])}")
    print(f"только у нас (в ядре нет)      : {len(res['only_ours'])}")
    if res["gone"]:
        print(f"слой помнит файл, которого в дереве нет: {len(res['gone'])}")

    if res["undeclared"]:
        print("\nНЕ ОБЪЯВЛЕНО — либо ядро отстало от живого, либо слой не полон:")
        for rel in res["undeclared"][:60]:
            print("   ", rel)
        if len(res["undeclared"]) > 60:
            print(f"    … и ещё {len(res['undeclared']) - 60}")
    if res["stale"]:
        print("\nОБЪЯВЛЕНО ЗРЯ — файл в слое, а расхождения нет:")
        for rel in res["stale"]:
            print("   ", rel)
    if res["gone"]:
        print("\nСЛОЙ ПОМНИТ НЕСУЩЕСТВУЮЩЕЕ:")
        for rel in res["gone"]:
            print("   ", rel)

    if not res["undeclared"] and not res["stale"] and not res["gone"]:
        print("\nслой сходится с фактической разницей — издание объявлено честно")
        return 0
    print("\nслой и фактическая разница РАЗЪЕХАЛИСЬ")
    return 1


def main() -> None:
    ap = argparse.ArgumentParser(description="ядро и слой издания Элен: сверка и отпечатки")
    ap.add_argument("--check", action="store_true", help="слой против разницы ядра и дерева")
    ap.add_argument("--digest", action="store_true", help="отпечатки ядра и слоя")
    ap.add_argument("--core", default=str(CORE_DEFAULT), help="ядро (по умолчанию ../praxis)")
    ap.add_argument("--layer", default=str(LAYER_DEFAULT), help="слой (по умолчанию ../helene/core)")
    ap.add_argument("--tree", default=os.environ.get("HELENE_TREE_SRC") or "",
                    help="рабочая копия дерева агента")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args()

    core, layer = Path(args.core), Path(args.layer)
    if not core.is_dir():
        raise SystemExit(f"нет ядра: {core}")
    if not layer.is_dir():
        raise SystemExit(f"нет слоя: {layer}")

    if args.digest:
        note = passport(core, layer)
        print(json.dumps(note, ensure_ascii=False, indent=1))
        return

    tree = Path(args.tree) if args.tree else _guess_tree()
    if tree is None or not tree.is_dir():
        raise SystemExit(
            "не нашлась рабочая копия дерева агента.\n"
            "Скажи её путь: --tree <папка> или HELENE_TREE_SRC.")
    res = compare(core, layer, tree)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    raise SystemExit(_report(res))


def _guess_tree() -> Path | None:
    """Где лежит рабочая копия — по общему объявлению, своего мнения тут нет."""
    found = layout.tree()
    return found if found.is_dir() else None


if __name__ == "__main__":
    main()
