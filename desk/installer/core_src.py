#!/usr/bin/env python3
"""Ядро и слой Элен: сверить, что издание объявлено честно, и снять отпечатки.

Раскладка 10.09 объявила продукт так: ядро агента живёт в `praxis/`, а Windows-
издание — СЛОЕМ в `helene/core`, где лежат только те файлы, которые Элен несёт
иначе. Красивое объявление, но проверять его было нечем, а непроверяемое
объявление разъезжается с делом молча — тот же класс, что уже стрелял на реле
(09.09 в поставку уехал бинарь от исходника недельной давности, и узнали об этом
случайно).

    python installer/core_src.py --check         # слой = настоящая разница?
    python installer/core_src.py --digest        # отпечатки ядра и слоя
    python installer/core_src.py --export-core   # обновить зеркало `praxis/` из её дерева

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

⚠ Зеркало `praxis/` — это ПУБЛИКАЦИЯ её кода на гитхабе (README ссылается прямо
туда), и до 15.09 обновлять его было нечем: копировали руками, а значит не
копировали. Отсюда и вечное «не объявлено, но расходится»: не слой врал, а
зеркало стояло. `--export-core` делает это одной командой — по образцу
`relay_src.py --pull`, с теми же двумя гарантиями: сверка на секреты ДО записи и
запись происхождения рядом с зеркалом (`CORE-SOURCE.json`).

⚠ Что этот прибор НЕ делает: он не решает, чья правка. Отличить её невыложенную
починку от нашей может только человек или она сама — прибор лишь не даёт
принять расхождение за пустоту.

Переводы строк приводятся к LF: копия на Windows и оригинал с Linux-сервера иначе
разошлись бы каждым файлом, и сверка не значила бы ничего (тот же приём, что в
`relay_src.py`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ⚠ Вывод — в UTF-8: в голой консоли владельца (cp1251) отчёт падал `UnicodeEncodeError`
# на знаке ⚠ — то есть ровно на строке «слой ОТСТАЛ от дерева», ради которой прибор и
# зовут перед выпуском (поймано 12.09). Тот же класс, что у `secret_scan` и `personal_scan`.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DESK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESK))
import layout  # noqa: E402 — где на диске лежат соседи раскладки

ROOT = layout.ROOT                       # раскладка 10.09: praxis/, helene/, desk/, remote/
CORE_DEFAULT = layout.CORE
LAYER_DEFAULT = layout.LAYER
STAMP = "CORE-SOURCE.json"

#: Чего в сверке нет и не должно быть. `soul/` — её письмо (конституция, навыки):
#: оно ОБЯЗАНО отличаться, и объявлять это слоем значило бы объявлять слоем её
#: личность. `memory/`, `workspace/`, `data/`, `private/` — жизнь агента, а не код.
#: `.proposal-orphans` — осиротевшие патчи её машинерии предложений: не код и не
#: издание, а мусор рядом с ним. В зеркале его не было, и переносить туда нечего.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".vectors", ".proposals",
             ".proposal-orphans",
             "memory", "workspace", "data", "private", "soul", "target", "node_modules"}

#: Чего нет в сверке по имени: следы среды и снимки «до правки», которые дерево
#: копит рядом с файлами. Их расхождение не значит ничего.
SKIP_SUFFIXES = (".pyc", ".pyo", ".bak", ".session-journal")
#: `.deploy.env` — секреты выкладки; он и не в git, и изданием быть не может.
# Точные имена, не префиксы: `.env.example` — часть дерева и обязан сверяться (A11 F7).
SKIP_NAMES_ENV = (".env", ".deploy.env")

#: Машинные файлы: пишет их прогон, а не человек, и расходятся они ВСЕГДА — на каждой
#: машине свои секунды. В отчёте это шум, который топит настоящее: 10.09 замеры дали 423
#: строки расхождения на файле, о котором нечего решать.
#: `SYNC-HEAD.txt` — наша метка «на чём сверялись»; она про процесс, не про код.
SKIP_NAMES = {".praxis_test_durations.json", "SYNC-HEAD.txt"}

#: Файлы, которые есть В ЗЕРКАЛЕ, но которых на её проде нет и не будет: лицензия,
#: NOTICE, документы сборки и исходник реле. Они часть публикации, а не часть ядра.
#:
#: ⚠ Без этого списка `--from-core` затаскивал бы `relay/**` (двадцать файлов исходника
#: реле) и документы зеркала прямо в дерево агента — поймано сверкой 10.09, когда
#: наложение оказалось на 56 файлов шире рабочей копии. Тот же список ведёт рецепт
#: зеркала, и разъезжаться им нельзя.
#:
#: ⚠ `.gitignore` здесь с 15.09, и его сюда привёл первый же настоящий перенос.
#: У зеркала свой хвост правил — `relay/target/`, `relay/logs/`: реле есть в
#: публикации и нет на её проде, и её `.gitignore` про него не знает. Хвост
#: возвращался РУКАМИ после каждого синка (его собственный комментарий это и
#: говорит), а `--export-core` снёс его первым же прогоном — молча, ровно тем
#: способом, ради отмены которого команда и писалась. Теперь файл зеркала
#: неприкосновенен, и в сверке он не шумит вечной разницей.
MIRROR_ONLY = {
    "LICENSE", "LICENSE-AGPL-3.0.txt", "NOTICE", "BUNDLE.md", "DEPLOY.md",
    "docker-compose.bundle.yml", "env.bundle.example",
    "soul/SOUL.example.md", "soul/VOICE.example.md",
    ".gitignore",
}
MIRROR_ONLY_DIRS = ("relay/",)


#: Мусор её прода, который она коммитит сама («self-edit: rep13.txt», логи счётов графов,
#: pid-файлы): это следы работы, а не код, и в зеркало (публикацию) им нельзя — 19.09 их
#: не брали руками, 25.09 правило записано, чтобы `--export-core` не тащил их молча.
JUNK_SUFFIXES = (".log", ".tmp", ".pid", ".err", ".out")
# Узко, по именам её рабочих следов (A9 F6): `rep13.txt`, `rep12_2.txt`, `gap_*.log.tmp`,
# `formula_*.log`, `mr_ifub*.pid`, `hs_ifub*` — а не любой `rep*.txt`/`gap_*` в корне.
_JUNK_TOP_RE = re.compile(r"^(rep\d+(_\d+)?\.txt|gap_[^/]*\.(log|tmp|log\.tmp)|formula_[^/]*\.(log|tmp)|mr_ifub[^/]*\.(pid|log|tmp)|hs_ifub[^/]*)$")


def _junk(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    if name.endswith(JUNK_SUFFIXES):
        return True
    return len(parts) == 1 and bool(_JUNK_TOP_RE.match(name))


def _skip(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in SKIP_DIRS for p in parts):
        return True
    name = parts[-1]
    if name in SKIP_NAMES:
        return True
    if name.endswith(SKIP_SUFFIXES) or name in SKIP_NAMES_ENV:
        return True
    if _junk(rel):
        return True
    # `agent.py.pre-что-то-1784583545` — снимок дерева перед правкой, не файл кода.
    return ".pre-" in name


def mirror_only(rel: str) -> bool:
    """Файл публикации, а не ядра: в дерево агента он не едет ни при каких условиях."""
    return rel in MIRROR_ONLY or rel.startswith(MIRROR_ONLY_DIRS)


def files(root: Path) -> dict[str, Path]:
    """Файлы дерева относительным путём -> путь на диске.

    Зеркало-онли отбрасывается ЗДЕСЬ, а не у каждого потребителя: и сверка, и сборка
    из ядра спрашивают одно и то же «что считается ядром», и второй ответ на этот
    вопрос немедленно разъехался бы с первым.
    """
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not _skip(rel) and not mirror_only(rel):
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
        # ⚠ Объявлено, расхождение есть — а В СЛОЕ ЛЕЖИТ НЕ ТО, что в дереве.
        # Прежде прибор этого не спрашивал: он проверял только ФАКТ объявления.
        # Значит слой мог отстать на любое число правок, а сверка говорила
        # «издание объявлено честно» — то есть молчала ровно там, ради чего
        # заведена. Поставка из ядра плюс такой слой воспроизводит не это дерево.
        # ⚠ Считаем и по `only_ours`: объявленный файл, которого в ядре нет,
        # из отчёта выпадал совсем — ни расхождения (сравнивать не с чем), ни
        # жалобы (объявлен). Значит про НОВЫЕ файлы издания слой мог держать
        # вчерашнее, и никто бы не сказал.
        "drifted": sorted(rel for rel in (declared & (differ | only_ours))
                          if rel in tree_f and sha(layer_f[rel]) != sha(tree_f[rel])),
        "gone": sorted(r for r in declared if r not in tree_f),
        # Файл ядра, которого у нас нет вовсе. Это НЕ ошибка: издание вправе чего-то
        # не нести. Но и молчать нельзя — сборка из ядра принесёт его в поставку, и
        # владелец должен узнать об этом здесь, а не из выросшего архива.
        "not_carried": sorted(set(core_f) - set(tree_f)),
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
    print(f"   ⚠ из них слой ОТСТАЛ от дерева: {len(res['drifted'])}")
    print(f"НЕ ОБЪЯВЛЕНО, но расходится    : {len(res['undeclared'])}")
    print(f"объявлено зря (совпадает)      : {len(res['stale'])}")
    print(f"только у нас (в ядре нет)      : {len(res['only_ours'])}")
    print(f"в ядре есть, у нас нет         : {len(res['not_carried'])}   "
          f"(сборка из ядра принесёт их)")
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

    if res["drifted"]:
        print("\nСЛОЙ ОТСТАЛ ОТ ДЕРЕВА — объявлено верно, а лежит вчерашнее:")
        for rel in res["drifted"]:
            print("   ", rel)
        print("    перенести: python installer/core_src.py --sync")

    if (not res["undeclared"] and not res["stale"] and not res["gone"]
            and not res["drifted"]):
        print("\nслой сходится с фактической разницей — издание объявлено честно")
        return 0
    print("\nслой и фактическая разница РАЗЪЕХАЛИСЬ")
    return 1


def sync(res: dict) -> int:
    """Перенести дерево в слой там, где слой отстал. Ничего не объявляет заново.

    ⚠ Только уже ОБЪЯВЛЕННЫЕ файлы. Дописывать в слой необъявленное этот ключ не
    будет: «расходится, а в слое нет» — это либо отставшее ядро, либо её
    невыложенная починка, и решать такое прибором значит принимать чужую работу
    за свою.
    """
    layer, tree = Path(res["layer"]), Path(res["tree"])
    moved = 0
    for rel in res["drifted"]:
        dst = layer / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((tree / rel).read_bytes())
        print("  перенесено:", rel)
        moved += 1
    print(f"перенесено файлов: {moved}" if moved else "слой и так совпадает с деревом")
    return 0


#: Её дерево рядом с раскладкой: рабочая копия её прода, не наша.
HER_TREE_DEFAULT = layout.neighbour("_her/tree")

#: Приметы её дерева. Без них опечатка в `--from` означала бы «в источнике ноль
#: файлов», а «ноль файлов» для переноса — это стереть зеркало целиком и молча.
#: Тот же класс, из-за которого сборка проверяет, что `tree/` не пустая.
HER_TREE_MARKS = ("agent.py", "llm.py", "turns.py")
HER_TREE_FLOOR = 300


def _her_tree_ok(source: Path) -> str:
    """Пустая строка — это её дерево; иначе причина отказа словами."""
    if not source.is_dir():
        return f"нет такой папки: {source}"
    missing = [m for m in HER_TREE_MARKS if not (source / m).is_file()]
    if missing:
        return f"это не дерево агента: нет {', '.join(missing)} в {source}"
    found = len(files(source))
    if found < HER_TREE_FLOOR:
        return (f"в {source} всего {found} файлов при поле {HER_TREE_FLOOR} — "
                "перенос стёр бы зеркало; проверь путь")
    return ""


def _secrets_clean(source: Path) -> str:
    """Сверка со значениями живых секретов ДО записи; пусто — чисто.

    Здесь, а не после: зеркало — публичный репозиторий, и откатывать уехавший
    туда ключ поздно. Прибор отказывается и при нуле проверяемых значений, так
    что «нечего искать» не пройдёт за «чисто» (его же урок 11.09).
    """
    here = Path(__file__).resolve().parent
    listing = here / "secret-strings.txt"
    if not listing.is_file():
        return (f"нет списка живых значений ({listing}) — сверить зеркало на секреты "
                "нечем, а публиковать несверенное нельзя")
    done = subprocess.run(
        [sys.executable, str(here / "secret_scan.py"), str(source), str(listing)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if done.returncode != 0:
        tail = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()
        return "сверка на секреты не прошла:\n  " + "\n  ".join(tail[-12:])
    return ""


def _write_stamp(core: Path, source: Path, head: str, dirty: bool, count: int) -> None:
    """Происхождение зеркала рядом с ним — как `RELAY-SOURCE.json` у реле."""
    total, _each = digest(core)
    (core.parent / STAMP).write_text(json.dumps({
        "mirror": core.name,
        # Откуда: абсолютный путь и, если это git-чекаут, его голова — не относительный путь
        # от неизвестной cwd (A9 F7).
        "source": str(Path(source).resolve()),
        "head": head,
        "dirty": dirty,
        "taken_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "files": count,
        "digest": total,
        "by": "installer/core_src.py --export-core",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def export_core(core: Path, source: Path, *, dry_run: bool) -> int:
    """Перенести её дерево в зеркало `praxis/`. Сначала отказы, потом запись."""
    why = _her_tree_ok(source)
    if why:
        print("перенос не пойдёт:", why)
        return 2
    why = _secrets_clean(source)
    if why:
        print("перенос не пойдёт:", why)
        return 2

    have, want = files(core), files(source)
    added = sorted(set(want) - set(have))
    gone = sorted(set(have) - set(want))
    changed = sorted(rel for rel in set(have) & set(want) if sha(have[rel]) != sha(want[rel]))

    head, dirty = git_head(source)
    print(f"источник: {source}" + (f" @ {head}" if head else "")
          + (" (грязный)" if dirty else ""))
    print(f"зеркало : {core} — {len(have)} файлов, станет {len(want)}")
    print(f"новых {len(added)}, изменилось {len(changed)}, исчезло {len(gone)}")
    for title, rows in (("новый", added), ("изменился", changed), ("исчез", gone)):
        for rel in rows[:40]:
            print(f"  {title}: {rel}")
        if len(rows) > 40:
            print(f"  … и ещё {len(rows) - 40}")
    if dry_run:
        print("\n--dry-run: не написано ничего"
              if (added or changed or gone) else "зеркало и так совпадает с её деревом")
        return 0
    if not (added or changed or gone):
        # Происхождение пишем ВСЁ РАВНО: «сверено с её e39af273 тогда-то» — это
        # ответ на вопрос «какого ядра эта поставка», и он нужен ровно так же,
        # когда переносить было нечего. Без этого запись протухала бы молча.
        print("зеркало и так совпадает с её деревом")
        _write_stamp(core, source, head, dirty, len(want))
        return 0

    for rel in added + changed:
        dst = core / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(want[rel].read_bytes())
    for rel in gone:
        target = core / rel
        target.unlink(missing_ok=True)
        # Пустую папку за собой убираем, но только пустую и только под ядром:
        # «только зеркало» (`relay/`, LICENSE, документы) сюда не попадает по
        # построению — `files()` их не видит, значит и в `gone` их нет.
        folder = target.parent
        while folder != core and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent

    _write_stamp(core, source, head, dirty, len(want))
    print(f"\nперенесено: {len(added) + len(changed)}, удалено: {len(gone)}")
    print(f"происхождение записано: {core.parent / STAMP}")
    # ⚠ Не обещаем, что «не объявлено» уменьшится: свежее зеркало обычно делает
    # это число БОЛЬШЕ, и это правда, а не беда. Пока зеркало стояло, часть
    # разницы пряталась за его древностью; теперь видно ровно одно — насколько
    # рабочая копия отстала от её живого дерева.
    print("дальше — python installer/core_src.py --check: число «не объявлено» "
          "теперь честное, и оно про отставание рабочей копии, а не зеркала")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="ядро и слой издания Элен: сверка и отпечатки")
    ap.add_argument("--check", action="store_true", help="слой против разницы ядра и дерева")
    ap.add_argument("--digest", action="store_true", help="отпечатки ядра и слоя")
    ap.add_argument("--sync", action="store_true",
                    help="перенести в слой содержимое дерева для объявленных файлов")
    ap.add_argument("--export-core", action="store_true",
                    help="обновить зеркало `praxis/` из её дерева")
    ap.add_argument("--from", dest="source", default=str(HER_TREE_DEFAULT),
                    help=f"её дерево (по умолчанию {HER_TREE_DEFAULT})")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать, что сделал бы перенос")
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

    # Перенос зеркала рабочей копии не спрашивает: он про ядро и её дерево.
    if args.export_core:
        raise SystemExit(export_core(core, Path(args.source), dry_run=args.dry_run))

    tree = Path(args.tree) if args.tree else _guess_tree()
    if tree is None or not tree.is_dir():
        raise SystemExit(
            "не нашлась рабочая копия дерева агента.\n"
            "Скажи её путь: --tree <папка> или HELENE_TREE_SRC.")
    res = compare(core, layer, tree)
    if args.sync:
        raise SystemExit(sync(res))
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
