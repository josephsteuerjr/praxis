"""Rebuildable SQLite FTS over Praxis's canonical memory sources.

Markdown, append-only JSONL and timestamped computer-inventory JSON remain the source
of truth.  This module owns only a disposable query accelerator under
``memory/.state``.  Generated navigation views (``memory/INDEX.md`` and
``memory/maps``) are deliberately excluded so maps do not become a second,
self-reinforcing memory corpus.
"""

from __future__ import annotations

import bisect
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import memory_provenance

SCHEMA_VERSION = "praxis.memory.fts.v7"
_LOCK = threading.RLock()
_WORD_RE = re.compile(r"[\wа-яё]+", re.I)
_RU_ENDINGS = (
    "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими", "ая", "яя",
    "ое", "ее", "ые", "ие", "ый", "ий", "ой", "ам", "ям", "ах", "ях",
    "ов", "ев", "ом", "ем", "ую", "юю", "а", "я", "ы", "и", "у", "ю", "е", "о",
)
_TEXT_FIELDS = (
    "text", "summary", "subject", "message", "purpose", "why_now", "outcome",
    "lesson", "status", "kind", "action", "capability", "goal", "reason",
    "details", "from_status", "to_status", "name", "path", "error", "code",
    "note", "statement", "why_it_matters", "next_move", "stage", "title",
    "topic_title", "sender_name", "media",
)
_ENTITY_FIELDS = (
    "actor", "person", "peer", "peer_id", "chat_id", "device_id", "task_id",
    "source", "source_id", "desire_id", "run_id", "event_id",
    "topic_id", "message_id", "sender_id", "reply_to_message_id",
)


@dataclass(frozen=True)
class Source:
    path: Path
    rel: str
    kind: str
    visibility: str
    meta: dict[str, Any] | None = None


def _db_path(memory_dir: Path) -> Path:
    return memory_dir / ".state" / "recall.sqlite3"


# ⚠ РАЗРЕШЕНИЕ ПУТИ — САМАЯ ДОРОГАЯ СТРОКА ЭТОГО МОДУЛЯ. `Path.resolve()` ходит в
# файловую систему, а звали его на КАЖДЫЙ файл при КАЖДОМ поиске: замер 07.08 на живом
# проде — 119 988 вызовов `resolve()` и 114 027 `relative_to()` за ОДИН вопрос к памяти,
# 37 из 62 секунд. Её явная рука отвечала 29–70 секунд, и это при том, что она согласилась
# снять автоматический recall при условии «сначала ускорить руку».
#
# Кэш не меняет семантику: разрешение пути — чистая функция от строки, пока дерево не
# двигается, а внутри одного поиска оно не двигается по построению. Ключ — сама строка,
# поэтому символические ссылки разрешаются ровно так же, как раньше, просто один раз.
_POSIX: dict[str, str] = {}

# Поколение кэша. Растёт на каждом сбросе, потому что сброс идёт ВНЕ `_LOCK`, а поход в
# файловую систему долгий: разрешение, НАЧАТОЕ до сброса, относится к прошлому дереву и
# не имеет права лечь обратно. Без этого счётчика `clear_path_cache()` — не барьер, а
# пожелание, и тот, кто честно «подвинул дерево и сбросил кэш», получал прежний ответ.
# Воспроизведено адверсаркой 07.08 подменой `Path.resolve` на медленный вариант.
_POSIX_GEN = 0


def clear_path_cache() -> None:
    """Забыть разрешённые пути. Нужно стенду и всякому, кто двигает дерево под собой."""
    global _POSIX_GEN
    _POSIX_GEN += 1
    _POSIX.clear()


def _posix(path: Path) -> str:
    """`path.resolve().as_posix()` с памятью. Явный сброс — `clear_path_cache()`.

    ⚠ КЭШИРУЕТСЯ ТОЛЬКО СТРОКА. Отдельный словарь разрешённых `Path` здесь был — и не
    экономил НИЧЕГО: ключи у обоих словарей совпадали по построению, поэтому попадание
    ловилось строкой ниже и до второго словаря управление не доходило ни разу. Замер
    адверсарки: 0 попаданий на 70 000 путях, включая переход через потолок. Цена была
    ~62 МБ удержанных объектов `Path` на полном потолке — вчетверо больше полезной
    половины, в процессе, который живёт сутками. Кэш заводили ради скорости, а он тихо
    держал память; это ровно тот класс, за которым мы охотимся в её памяти, только в
    нашем собственном коде.

    ⚑ Граница применимости, доказанная и НЕ закрытая намеренно: на Windows `resolve()`
    берёт регистр с диска, поэтому разрешение НЕСУЩЕСТВУЮЩЕГО пути меняется, как только
    каталог создадут, — запомнить его значит запомнить неправду. На проде (Linux)
    `resolve()` несуществующего пути чисто лексический, класса нет вовсе, а лекарство
    стоит лишний lstat на каждый из ~13 000 файлов обхода. Записано, не заплатано.
    """
    key = str(path)
    found = _POSIX.get(key)
    if found is not None:
        return found
    generation = _POSIX_GEN
    found = path.resolve().as_posix()
    if generation != _POSIX_GEN:
        # Дерево подвинули, пока мы ходили в файловую систему. Ответ отдаём (он верен для
        # ТОГО дерева и вызывающему нужен сейчас), но в память не кладём.
        return found
    # Потолок не декоративный: без него длинный процесс копит словарь по числу когда-либо
    # увиденных путей. Сброс целиком дешевле вытеснения по одному.
    if len(_POSIX) > 65536:
        _POSIX.clear()
    _POSIX[key] = found
    return found


# ⚠ ВТОРОЙ ПОЖИРАТЕЛЬ: САМА АРИФМЕТИКА ПУТЕЙ. Профиль после кэша resolve():
# 114 229 вызовов `Path.relative_to` — 23.3 с, плюс 1.1 млн `with_segments` внутри.
# `relative_to` разбирает путь на сегменты и собирает НОВЫЙ объект Path; нам же нужен
# ответ «внутри ли» и «как выглядит относительно», а на УЖЕ РАЗРЕШЁННЫХ абсолютных
# путях это ровно префикс строки. Семантика та же — для разрешённых путей вложенность
# и есть префикс, — но без единого объекта Path на каждый файл каждого поиска.
def _rel(path: Path, base: Path) -> str:
    child, parent = _posix(path), _posix(base)
    if child == parent:
        return "."
    prefix = parent if parent.endswith("/") else parent + "/"
    return child[len(prefix):] if child.startswith(prefix) else child


def _inside(path: Path, parent: Path) -> bool:
    child, root = _posix(path), _posix(parent)
    if child == root:
        return True
    prefix = root if root.endswith("/") else root + "/"
    return child.startswith(prefix)


_SEPS = frozenset(s for s in (os.sep, os.altsep) if s)


def _rel_under(path: Path, base: Path) -> str:
    """`path.relative_to(base).as_posix()`, но без разбора пути на сегменты.

    ⚠ ЭТО НЕ МИКРООПТИМИЗАЦИЯ. В CPython 3.12 `relative_to` зовёт `is_relative_to`, а тот
    проверяет `base in self.parents` — то есть материализует последовательность родителей,
    по объекту Path на каждый уровень. Замер на живом проде 07.08: 54 860 вызовов = 11.5
    секунды из 31.4 на ОДИН вопрос к её памяти. Каждый markdown разбирался четырежды —
    в обходе, в `_generated_markdown`, в `_markdown_visibility`.

    Ответ строкой ТОЧЕН, а не приблизителен, и это видно из построения: обход отдаёт пути,
    буквально собранные как `base / …`, поэтому префикс совпадает всегда. Если не совпал —
    мы не отвечаем сами, а отдаём вопрос настоящему `relative_to` вместе с его ValueError.
    Расхождению взяться неоткуда: либо префикс есть и ответ тот же, либо мы молчим.

    ⚑ Сторож на нормализуемые куски (`.`, `..`, двойной разделитель) — ПОЛ, а не
    проверенная защита, и это названо вслух. Проверка нарочной поломкой показала: снять
    его — стенд остаётся зелёным, потому что Path съедает `.` и двойной разделитель ещё в
    конструкторе, а `..` обе стороны сохраняют одинаково. Входа, на котором он меняет
    ответ, не существует; он стоит на случай строки, пришедшей когда-нибудь не от Path.

    ⚑ Разделитель режется ИМЕННО `os.sep`/`os.altsep`, а не «слэш и обратный слэш»: на
    Linux обратный слэш — законный символ ИМЕНИ файла, и слепая замена испортила бы путь.
    """
    text, root = str(path), str(base)
    if text.startswith(root) and len(text) > len(root) and text[len(root)] in _SEPS:
        parts = text[len(root) + 1:].split(os.sep)
        if os.altsep:
            parts = [piece for part in parts for piece in part.split(os.altsep)]
        if parts and all(part and part not in (".", "..") for part in parts):
            return "/".join(parts)
    return path.relative_to(base).as_posix()


def _generated_markdown(path: Path, memory_dir: Path, rel: str = "") -> bool:
    # `rel` необязателен намеренно: у функции есть вызывающие в стенде, которые знают её
    # по двум аргументам, и при пустом `rel` поведение прежнее байт в байт.
    rel = rel or _rel_under(path, memory_dir)
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            if stream.readline().lstrip().startswith("<!-- praxis-generated:"):
                return True
    except OSError:
        pass
    if rel == "INDEX.md" or rel.startswith("maps/"):
        return True
    # Canon is the append-only desire event stream.  CURRENT.md is only its
    # deterministic, grep-friendly projection and must not feed recall twice.
    if rel == "desires/CURRENT.md":
        return True
    # These are deterministic projections of computer JSONL/inventory, not new facts.
    if rel == "computer/MAP.md" or rel.startswith("computer/devices/") \
            or rel.startswith("computer/tasks/"):
        return True
    return rel.startswith("computer/inventory/") and path.name == "CURRENT.md"


def _markdown_visibility(path: Path, memory_dir: Path, skills_dir: Path | None,
                         rel: str = "") -> str:
    if skills_dir is not None and _inside(path, skills_dir):
        return "public"
    rel = rel or _rel_under(path, memory_dir)
    if rel.startswith("people/"):
        return "mixed"
    if rel == "graph.md":
        return "public"
    return "owner"


def _selected_jsonl(path: Path, memory_dir: Path, rel: str = "") -> tuple[str, str] | None:
    rel = rel or _rel_under(path, memory_dir)
    rules = (
        (r"^life/events/[^/]+\.jsonl$", "life_event"),
        (r"^computer/events/[^/]+\.jsonl$", "computer_event"),
        (r"^runs/.+/events\.jsonl$", "run_event"),
        (r"^self/[^/]+\.jsonl$", "self_event"),
        # Её собственные прожитые ходы в комнатах. Раньше они УНИЧТОЖАЛИСЬ при перекате
        # кольца — из опасения, что recall, намеренно слепой к аудитории, унесёт тему из
        # комнаты в комнату. Опасение верное, вывод был неверный: приватность — политика
        # ВЫДАЧИ, а не удаления. Теперь ходы сохраняются с меткой origin, а строка recall
        # называет комнату (`agent._recall_origin`), так что «откуда это» видно и ей, и
        # границе раскрытия. Отдельный kind, а не self_event: перепутать её ход в чужой
        # комнате с записью из своей лички нельзя ни в одном потребителе.
        (r"^self/rooms/[^/]+/turns-[^/]+\.jsonl$", "room_turn"),
        # 02.08.2026. То, что она записывает СЕБЕ САМА (`praxis.authored_note.event.v1`),
        # не индексировалось вовсе — этого пути в списке просто не было. То есть её
        # собственные уроки не находил не только автоматический recall, но и явное
        # «вспомни»: урок написан и невидим. Это и был настоящий блокер её четвёртого
        # шага «извлечение в точке выбора», а не аллоулист, как мы думали.
        (r"^notes/events\.jsonl$", "authored_note"),
        (r"^desires/events\.jsonl$", "desire_event"),
        (r"^social/.+\.jsonl$", "social_event"),
        (r"^access/events/[^/]+\.jsonl$", "access_event"),
        (r"^groups/[^/]+/archive\.jsonl$", "group_message"),
    )
    for pattern, kind in rules:
        if re.match(pattern, rel):
            return kind, "owner"
    return None


def _inventory_snapshot_key(path: Path) -> tuple[int, str, int, str]:
    """Prefer server-observed snapshots; keep legacy history deterministic.

    Old snapshots were named from the Windows clock.  A skewed legacy filename
    must not outrank a newer server-observed record forever.
    """
    try:
        item = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        observed = str(item.get("observed_at") or "") if isinstance(item, dict) else ""
        rank = 2 if observed else 1
    except (OSError, TypeError, ValueError):
        observed, rank = "", 0
    try:
        modified = path.stat().st_mtime_ns
    except OSError:
        modified = 0
    return rank, observed, modified, path.name



def _walk_pruned(root: Path, pattern: str, *, prune: set[str], prune_under: Path):
    """`root.rglob(pattern)`, но без спуска в каталоги `prune` внутри `prune_under`.

    `Path.rglob` подрезать поддерево не умеет — отсюда собственный обход. Он обязан
    отдавать РОВНО то же множество путей, что `rglob`, минус подрезанное; на проде это
    сверено побайтово (9 455 файлов до и после, списки равны).
    """
    stack = [root]
    inside = _posix(prune_under) if prune_under.exists() else None
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for item in entries:
            if item.is_dir():
                if (item.name in prune and inside is not None
                        and _posix(item).startswith(inside)):
                    continue
                stack.append(item)
            elif item.match(pattern):
                yield item


def _memory_files(memory_dir: Path, pattern: str, *, include_runs: bool) -> Iterable[Path]:
    """Walk canonical memory while allowing the automatic path to prune runs early."""
    if include_runs:
        # ⚠ ДЕРЕВО ПРОГОНОВ ОБХОДИТСЯ РАДИ ТОГО, ЧЕГО В НЁМ НЕТ. Замер на живом проде
        # 08.08: под `memory/runs` 70 183 файла, из них 51 573 — `.log` в `results/`.
        # Ни одного `.md` и ни одного `.jsonl` там нет НА ЛЮБОЙ ГЛУБИНЕ (проверено), но
        # `rglob` всё равно заходит в каждый такой каталог: 1.24 с против 0.70 с.
        #
        # ⚑ Подрезается ТОЛЬКО каталог с именем `results`, и только внутри `runs`.
        # Соседний `artifacts/` обходится по-прежнему: там лежат ЕЁ рабочие продукты —
        # на проде два её черновика исходящих на 403 куска, — и наивное «перечислить
        # три канонических имени» их бы молча потеряло. Проверено на её индексе, а не
        # предположено.
        #
        # ⚑ И подрезка СТРУКТУРНАЯ, а не по глубине. Первая редакция предполагала
        # раскладку `runs/<месяц>/<прогон>/` — на проде она такая, но контракта на неё
        # нет, и прод-гейт поймал это сразу: снимок прогона в стенде лежит на другой
        # глубине, и цель `audit` вернула пустоту. Ходим рекурсивно и пропускаем
        # `results` там, где он встретится.
        yield from _walk_pruned(memory_dir, pattern, prune={"results"},
                                prune_under=memory_dir / "runs")
        return    # Path.rglob cannot prune a subtree.  Walking each top-level root except runs
    # avoids enumerating the multi-gigabyte durable execution tree at all.
    #
    # `self/rooms/**` подрезается по той же причине, что и `runs`, и это не вкусовщина:
    # автоматический recall обходит его на КАЖДОМ живом ходе, платя stat+resolve за
    # каждый файл, и получает оттуда РОВНО НОЛЬ строк — `automatic_recall_allowed`
    # отказывает виду `room_turn` явно. Замер адверсарки на Windows: 1500 файлов ≈
    # 1.4 секунды на ход, линейно и без потолка (каталогов там по одному на каждую
    # ветку разговора, где она когда-либо просыпалась). Явный recall ходит другим
    # путём (include_runs=True) и комнаты по-прежнему находит.
    for child in memory_dir.iterdir():
        if child.name == "runs":
            continue
        if child.is_file():
            if child.match(pattern):
                yield child
        elif child.name == "self":
            for path in child.rglob(pattern):
                if "rooms" not in path.relative_to(child).parts:
                    yield path
        elif child.is_dir():
            yield from child.rglob(pattern)


def iter_sources(*, base: Path, memory_dir: Path, skills_dir: Path | None = None,
                 include_runs: bool = True) -> list[Source]:
    """Return a stable, explicit list of canonical sources eligible for recall."""
    base, memory_dir = Path(base), Path(memory_dir)
    skills_dir = Path(skills_dir) if skills_dir is not None else None
    whole = whole_docs_enabled()
    drop_run_events = drop_run_events_enabled()
    rows: list[Source] = []
    evidence = memory_provenance.claim_evidence_index(memory_dir)
    automatic_life_event_ids = frozenset(
        event_id for event_id, row in (evidence.get("events") or {}).items()
        if memory_provenance.event_automatic_recall_allowed(row)
    )
    if memory_dir.exists():
        for path in _memory_files(memory_dir, "*.md", include_runs=include_runs):
            if (not path.is_file() or not _inside(path, memory_dir)
                    or path.name.startswith("_")):
                continue
            # Путь разбирается ОДИН раз и дальше едет строкой. Порядок отсечек тоже
            # изменён: `bench/` и скрытые каталоги отсеиваются ДО `_generated_markdown`,
            # который открывает файл ради первой строки. Исход тот же `continue` — но
            # теперь без чтения с диска ради заведомо отброшенного.
            rel = _rel_under(path, memory_dir)
            if rel.startswith("bench/"):
                continue
            # ⚠ ТРАНСПОРТНЫЕ СНИМКИ ПРОГОНОВ — 73% ВСЕГО ТЕКСТА ИНДЕКСА (133 млн знаков
            # из 181,5, 225 453 куска из 530 194). Её решение 02.08 уже вывело их из
            # обычной выдачи по ЦЕЛИ обращения; здесь они уходят и из индексации.
            # Цель `audit` при этом не остаётся пустым словом: она получает ПРЯМОЙ путь
            # к файлам прогонов (`_audit_transport_hits`), то есть читает канон, а не
            # одноразовую базу. Это строже прежнего, а не слабее.
            if whole and _is_transport_snapshot("memory/" + rel):
                continue
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            if _generated_markdown(path, memory_dir, rel):
                continue
            base_rel = _rel(path, base)
            if re.fullmatch(r"life/claims/[^/]+\.md", rel):
                source_kind, meta = memory_provenance.claim_source(
                    path, evidence_index=evidence,
                )
            else:
                source_kind, meta = memory_provenance.episodic_kind(base_rel) or "markdown", None
            rows.append(Source(path, base_rel, source_kind,
                               _markdown_visibility(path, memory_dir, skills_dir, rel), meta))
        for path in _memory_files(memory_dir, "*.jsonl", include_runs=include_runs):
            if not path.is_file() or not _inside(path, memory_dir):
                continue
            rel_jsonl = _rel_under(path, memory_dir)
            # ⚑ События прогона — документ прогона это его RECAP, и он есть у 2 760 прогонов
            # из 2 766, написанный в момент прогона тем, кто его прожил. 149 139 записей по
            # 65 знаков — не память, а лог.
            #
            # ⚠ НО ЭТО ЕЁ РЕШЕНИЕ, И ОНО НЕ ОТМЕНЕНО. 02.08 она оставила события прогона в
            # обычном recall; вопрос об отмене лежит у неё (workspace/EVENTS-JSONL-ВОПРОС-08.08.md)
            # и ответа НЕТ. Первая редакция привязывала отмену к рычагу цельных документов —
            # а тот на проде включён с 08.08, то есть её решение отменялось бы САМО, молча и
            # без её слова, на первом же обновлении индекса. Замер 09.08: в живой базе
            # 151 947 таких кусков — 75% всего индекса; они исчезли бы разом.
            # Поэтому отмена живёт СВОИМ рычагом, выключенным по умолчанию, и ждёт её.
            if drop_run_events and re.match(r"^runs/.+/events\.jsonl$", rel_jsonl):
                continue
            selected = _selected_jsonl(path, memory_dir, rel_jsonl)
            if selected:
                kind, visibility = selected
                meta = ({"automatic_event_ids": automatic_life_event_ids}
                        if kind == "life_event" else None)
                rows.append(Source(path, _rel(path, base), kind, visibility, meta))
        # Inventory history is canonical JSON rather than JSONL.  Index only the
        # newest timestamped snapshot per device: CURRENT.{json,md} are derived
        # projections, while indexing every daily snapshot would make stale app
        # and tool rows compete with the current machine state.
        inventory = memory_dir / "computer" / "inventory"
        if inventory.exists():
            for device in sorted((p for p in inventory.iterdir() if p.is_dir()),
                                 key=lambda p: p.name.casefold()):
                snapshots = sorted(
                    (p for p in device.glob("*.json") if p.is_file() and p.name != "CURRENT.json"),
                    key=_inventory_snapshot_key,
                )
                if snapshots:
                    path = snapshots[-1]
                    rows.append(Source(path, _rel(path, base), "inventory_snapshot", "owner"))
    if skills_dir is not None and skills_dir.exists():
        for path in skills_dir.glob("*.md"):
            if (path.is_file() and _inside(path, skills_dir)
                    and not path.name.startswith("_")
                    and path.name.casefold() != "index.md"):
                rows.append(Source(path, _rel(path, base), "skill", "public"))
    # Compact self history is immutable evidence outside memory/.  CURRENT is
    # already in the persona prompt; archived revisions remain explicitly
    # recallable without feeding the generated current projection back twice.
    self_history = base / "soul" / "self" / "history"
    if self_history.exists():
        for path in sorted(self_history.glob("*.md"), key=lambda p: p.name.casefold()):
            if path.is_file() and _inside(path, self_history):
                rows.append(Source(path, _rel(path, base), "self_history", "owner"))
    unique = {row.rel: row for row in rows}
    return [unique[key] for key in sorted(unique)]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _markdown_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    block: list[str] = []

    def flush() -> None:
        if block:
            value = " ".join(line.strip() for line in block).strip()
            if len(value) >= 3:
                chunks.append(value)
            block.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("#"):
            flush()
            continue
        if re.match(r"^[-*]\s+", line):
            flush()
            value = re.sub(r"^[-*]\s+", "", line).strip()
            if len(value) >= 3:
                chunks.append(value)
            continue
        block.append(line)
    flush()
    return chunks


def _stem(token: str) -> str:
    value = token.casefold().replace("ё", "е")
    if len(value) <= 4:
        return value
    for ending in _RU_ENDINGS:
        if value.endswith(ending) and len(value) - len(ending) >= 4:
            return value[:-len(ending)]
    return value


def _terms(text: str) -> str:
    return " ".join(_stem(token) for token in _WORD_RE.findall(str(text or "")) if len(token) > 1)


def _json_values(value: Any) -> Iterable[str]:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        if text:
            yield text
    elif isinstance(value, list):
        for item in value:
            yield from _json_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _json_values(item)


def _json_text(row: dict) -> str:
    values: list[str] = []
    for key in _TEXT_FIELDS:
        for value in _json_values(row.get(key)):
            if value not in values:
                values.append(value)
    meta = row.get("meta")
    if isinstance(meta, dict):
        for key in _TEXT_FIELDS:
            for value in _json_values(meta.get(key)):
                if value not in values:
                    values.append(value)
    # Desire events carry their current, source-of-truth snapshot under state.
    state = row.get("state")
    if isinstance(state, dict):
        for key in _TEXT_FIELDS:
            for value in _json_values(state.get(key)):
                if value not in values:
                    values.append(value)
    return " · ".join(values)[:16_000]


def _json_entities(row: dict) -> list[str]:
    values: list[str] = []
    for key in _ENTITY_FIELDS:
        for value in _json_values(row.get(key)):
            if value not in values:
                values.append(value)
    for value in _json_values(row.get("entities")):
        if value not in values:
            values.append(value)
    return values[:100]


def _list_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value is None or value == "":
        return []
    return [str(value)]


def _chunk_visibility(text: str, source_visibility: str, row: dict | None = None) -> str:
    item = row or {}
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    explicit = str(item.get("visibility") or meta.get("visibility") or "").strip().lower()
    if explicit == "public":
        return "public"
    if explicit in {"owner", "private"}:
        return "owner"
    if source_visibility == "mixed":
        return "owner" if re.search(r"\[private\]", text, re.I) else "public"
    return source_visibility


# ⚠ ОКНО ПОТОКА. При цельной единице памяти нельзя оставить jsonl построчным: BM25
# нормирует по длине, и запись в 92 знака обходит её же компакт в 3 765 — замер 08.08 дал
# −11,545 против −5,612, то есть вдвое. Смешение единиц в одном индексе хоронит документную
# половину ПО ПОСТРОЕНИЮ, а не иногда.
#
# ⚑ Естественного зерна у потоков нет, и это проверено: `life/events/2026-08-01.jsonl` —
# уже сутки, но 821 237 знаков; архив комнаты — 1 508 480. Полтора миллиона знаков это не
# документ, это стог. Поэтому окно режется ПО РАЗМЕРУ, по границам записей, и целится в
# порядок величины её markdown (в среднем 4 260 знаков).
#
# ⚑ Ни одна запись не рвётся пополам: окно закрывается на границе. Дословность внутри
# сохраняется — это склейка соседних записей, а не пересказ. Пересказ (сжатие её рукой)
# может лечь сверху отдельным слоем, но он не нужен для того, чтобы ранг стал честным.
STREAM_WINDOW_CHARS = 4000


def _stream_windows(rows: list[dict]) -> list[dict]:
    """Соседние записи потока — в окна около `STREAM_WINDOW_CHARS`, по границам записей."""
    if not rows:
        return []
    out: list[dict] = []
    bucket: list[dict] = []
    size = 0
    def flush() -> None:
        nonlocal bucket, size
        if not bucket:
            return
        head, tail = bucket[0], bucket[-1]
        out.append({
            **head,
            "chunk_key": "win:" + str(head.get("chunk_key") or len(out)),
            "text": "\n".join(str(r.get("text") or "") for r in bucket),
            # Метаданные окна берутся от ПЕРВОЙ записи, а время — от последней: окно
            # начинается там и заканчивается тут, и оба конца названы честно.
            "at": str(tail.get("at") or head.get("at") or ""),
            "refs": list(dict.fromkeys(
                ref for r in bucket for ref in (r.get("refs") or []))),
            "supersedes": list(dict.fromkeys(
                ref for r in bucket for ref in (r.get("supersedes") or []))),
            "entities": list(dict.fromkeys(
                e for r in bucket for e in (r.get("entities") or []))),
            # Окно наследует САМУЮ ЗАКРЫТУЮ видимость своих записей и самое строгое
            # право на автоматический канал: склейка не смеет раскрывать больше, чем
            # раскрывала любая её часть.
            "visibility": ("owner" if any(str(r.get("visibility") or "") == "owner"
                                          for r in bucket) else head.get("visibility")),
            "automatic_eligible": all(bool(r.get("automatic_eligible")) for r in bucket),
        })
        bucket, size = [], 0
    for row in rows:
        text = str(row.get("text") or "")
        if bucket and size + len(text) > STREAM_WINDOW_CHARS:
            flush()
        bucket.append(row)
        size += len(text)
    flush()
    return out


def _source_chunks(source: Source) -> tuple[list[dict], int]:
    rows: list[dict] = []
    corrupt = 0
    if source.kind in memory_provenance.CLAIM_KINDS:
        # The receipt body remains explicitly searchable, but metadata/status/revision
        # prose never inherits the statement's trust class.  Automatic recall gets one
        # exact, content-bound projection only.
        raw_kind = (source.kind if source.kind in {
            memory_provenance.CLAIM_CONTESTED_KIND,
            memory_provenance.CLAIM_UNSUPPORTED_KIND,
            memory_provenance.CLAIM_INVALID_KIND,
        } else memory_provenance.CLAIM_RAW_KIND)
        for index, text in enumerate(_markdown_chunks(_read(source.path))):
            rows.append({
                "chunk_key": f"raw:{index}", "text": text,
                "source_type": raw_kind,
                "automatic_eligible": False,
                "visibility": _chunk_visibility(text, source.visibility),
                "at": "", "event_id": "", "run_id": "", "refs": [],
                "supersedes": [], "entities": [],
            })
        meta = source.meta or {}
        if meta.get("_statement"):
            text, projection_kind, eligible = memory_provenance.claim_projection(meta)
            rows.append({
                "chunk_key": "claim:statement", "text": text,
                "source_type": projection_kind,
                "automatic_eligible": bool(eligible),
                "visibility": "owner" if meta.get("visibility") == "private" else "public",
                "at": str(meta.get("updated_at") or ""),
                "event_id": str(meta.get("id") or ""), "run_id": str(meta.get("last_run") or ""),
                "refs": list(meta.get("evidence_ids") or []), "supersedes": [],
                "entities": [str(meta.get("subject") or "")],
            })
        return rows, corrupt

    if source.kind in {
        "markdown", "skill", "self_history",
        memory_provenance.UNTRUSTED_EPISODIC_KIND,
        memory_provenance.UNTRUSTED_REFLECTION_KIND,
    }:
        if whole_docs_enabled():
            # ⚑ ОДИН ДОКУМЕНТ — ОДНА ЗАПИСЬ. Прежнее правило резало по абзацам и пунктам
            # списка: 6 604 файла давали 343 555 кусков, по 52 на файл, медиана 92 знака.
            # Её слова: «память, которая возвращает мысли, а не пятнадцать случайных слов».
            # Видимость считается по ВСЕМУ тексту, а не по куску: пометка [private] в
            # любом месте документа делает приватным весь документ — при цельной единице
            # это единственно честный вариант, и он строже прежнего.
            text = _read(source.path).strip()
            if len(text) >= 3:
                rows.append({
                    "chunk_key": "doc", "text": text,
                    "source_type": source.kind,
                    "automatic_eligible": source.kind in {"skill", "markdown"},
                    "visibility": _chunk_visibility(text, source.visibility),
                    "at": "", "event_id": "", "run_id": "", "refs": [],
                    "supersedes": [], "entities": [],
                })
            return rows, corrupt
        for index, text in enumerate(_markdown_chunks(_read(source.path))):
            rows.append({
                "chunk_key": f"md:{index}", "text": text,
                "source_type": source.kind,
                "automatic_eligible": source.kind in {"skill", "markdown"},
                "visibility": _chunk_visibility(text, source.visibility),
                "at": "", "event_id": "", "run_id": "", "refs": [],
                "supersedes": [], "entities": [],
            })
        return rows, corrupt

    if source.kind == "inventory_snapshot":
        try:
            item = json.loads(source.path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, TypeError, ValueError):
            return rows, 1
        if not isinstance(item, dict):
            return rows, 1
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        device = str(item.get("device_id") or payload.get("hostname") or source.path.parent.name)
        captured = str(item.get("captured_at") or payload.get("captured_at") or "")
        observed = str(item.get("observed_at") or captured)
        event_id = f"inventory:{device}:{observed or source.path.stem}"
        base_entities = [value for value in (device, payload.get("hostname"), payload.get("user"))
                         if str(value or "")]

        def add(key: str, text: str, entities=()) -> None:
            value = str(text or "").strip()
            if len(value) < 3:
                return
            rows.append({
                "chunk_key": f"inventory:{key}", "text": value, "source": device,
                "source_type": source.kind, "automatic_eligible": True,
                "visibility": "owner", "at": observed, "event_id": event_id,
                "run_id": "", "refs": [source.rel], "supersedes": [],
                "entities": list(dict.fromkeys([*base_entities, *[str(x) for x in entities if str(x)]])),
            })

        os_row = payload.get("os") if isinstance(payload.get("os"), dict) else {}
        machine = payload.get("machine") if isinstance(payload.get("machine"), dict) else {}
        identity = item.get("identity") if isinstance(item.get("identity"), dict) else {}
        add("overview", " · ".join(str(value) for value in (
            f"computer {device}", payload.get("hostname"), payload.get("user"),
            os_row.get("caption"), os_row.get("version"), os_row.get("build"),
            os_row.get("architecture"), machine.get("manufacturer"), machine.get("model"),
            identity.get("kind"), identity.get("integrity"),
        ) if str(value or "").strip()))
        for group, label in (("volumes", "volume"), ("tools", "tool"), ("apps", "application")):
            values = payload.get(group) if isinstance(payload.get(group), list) else []
            for index, value in enumerate(values):
                if not isinstance(value, dict):
                    continue
                fields = [str(part) for part in value.values() if part not in (None, "")]
                add(f"{group}:{index}", f"{label} " + " · ".join(fields), fields[:3])
        for group, label in (("known_roots", "known root"), ("project_roots", "project root")):
            values = payload.get(group) if isinstance(payload.get(group), list) else []
            for index, value in enumerate(values):
                add(f"{group}:{index}", f"{label} {value}", (value,))
        return rows, corrupt

    try:
        stream = source.path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return rows, corrupt
    seen_keys: set[str] = set()
    with stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                corrupt += 1
                continue
            if not isinstance(item, dict):
                corrupt += 1
                continue
            text = _json_text(item)
            automatic_text = text
            if source.kind == "computer_event" and item.get("goal"):
                automatic_item = dict(item)
                automatic_item.pop("goal", None)
                automatic_text = _json_text(automatic_item)
            if len(text) < 3:
                continue
            event_id = str(item.get("id") or item.get("event_id") or "")
            digest = hashlib.sha256(line.encode("utf-8", "replace")).hexdigest()[:16]
            key = f"event:{event_id}" if event_id else f"line:{line_no}:{digest}"
            if key in seen_keys:
                key = f"{key}:duplicate:{line_no}:{digest}"
            seen_keys.add(key)
            refs = _list_strings(item.get("refs") or item.get("evidence_refs"))
            supersedes = _list_strings(item.get("supersedes"))
            run_id = str(item.get("run_id") or (item.get("meta") or {}).get("run_id") or "") \
                if isinstance(item.get("meta") or {}, dict) else str(item.get("run_id") or "")
            row = {
                "chunk_key": key, "text": automatic_text,
                "source_type": source.kind,
                "automatic_eligible": (
                    event_id in set((source.meta or {}).get("automatic_event_ids") or ())
                    if source.kind == "life_event"
                    else memory_provenance.self_event_automatic_recall_allowed(item)
                    if source.kind == "self_event"
                    # 02.08: её заметки НАМЕРЕННО не в автоматическом канале. Здесь
                    # умолчание — True, поэтому новый вид иначе молча вошёл бы в её канон;
                    # а что попадает в кадр на КАЖДОМ ходе — решение Егора и её, не моё
                    # (граница из чеклиста: состав канона мы своей рукой не двигаем).
                    # Явный recall их находит с этого коммита — этого хватает, чтобы
                    # проверить гипотезу «урок возвращается», не меняя её канон.
                    else False
                    if source.kind == "authored_note"
                    else True
                ),
                "source": str(item.get("actor") or item.get("device_id")
                              or item.get("task_id") or source.kind),
                "visibility": _chunk_visibility(automatic_text, source.visibility, item),
                "at": str(item.get("ts") or item.get("at") or item.get("created_at") or ""),
                "event_id": event_id, "run_id": run_id, "refs": refs,
                "supersedes": supersedes, "entities": _json_entities(item),
                "desire_id": str(item.get("desire_id") or ""),
            }
            if source.kind == "computer_event" and automatic_text != text:
                rows.append({
                    **row, "chunk_key": f"{key}:explicit", "text": text,
                    "automatic_eligible": False,
                    "visibility": _chunk_visibility(text, source.visibility, item),
                })
            rows.append(row)
    if whole_docs_enabled():
        return _stream_windows(rows), corrupt
    return rows, corrupt


def _source_snapshot(source: Source) -> str:
    """Per-source stat key; '' when unreadable (excluded from the fingerprint)."""
    try:
        stat = source.path.stat()
    except OSError:
        return ""
    return f"{source.kind}\x00{stat.st_size}\x00{stat.st_mtime_ns}"


def _snapshots(sources: list[Source]) -> dict[str, str]:
    """One stat pass reused by both the corpus fingerprint and the v7 diff."""
    return {source.rel: _source_snapshot(source) for source in sources}


def _fingerprint_from(snapshots: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(snapshots):
        snapshot = snapshots[rel]
        if not snapshot:
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(snapshot.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _fingerprint(sources: list[Source]) -> str:
    return _fingerprint_from(_snapshots(sources))


def _create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            chunk_key TEXT NOT NULL,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            visibility TEXT NOT NULL,
            automatic_eligible INTEGER NOT NULL,
            at TEXT NOT NULL,
            event_id TEXT NOT NULL,
            desire_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            refs_json TEXT NOT NULL,
            supersedes_json TEXT NOT NULL,
            entities TEXT NOT NULL,
            terms TEXT NOT NULL,
            text TEXT NOT NULL,
            UNIQUE(path, chunk_key)
        );
        CREATE TABLE source_state (
            path TEXT PRIMARY KEY,
            corrupt_lines INTEGER NOT NULL DEFAULT 0,
            snapshot TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX chunks_path ON chunks(path);
        CREATE INDEX chunks_visibility ON chunks(visibility);
        CREATE INDEX chunks_automatic ON chunks(automatic_eligible);
        CREATE INDEX chunks_event ON chunks(event_id);
        CREATE INDEX chunks_desire ON chunks(desire_id);
        CREATE INDEX chunks_run ON chunks(run_id);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            text, terms, entities,
            content='chunks', content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
          INSERT INTO chunks_fts(rowid, text, terms, entities)
          VALUES (new.id, new.text, new.terms, new.entities);
        END;
        CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
          INSERT INTO chunks_fts(chunks_fts, rowid, text, terms, entities)
          VALUES ('delete', old.id, old.text, old.terms, old.entities);
        END;
        CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
          INSERT INTO chunks_fts(chunks_fts, rowid, text, terms, entities)
          VALUES ('delete', old.id, old.text, old.terms, old.entities);
          INSERT INTO chunks_fts(rowid, text, terms, entities)
          VALUES (new.id, new.text, new.terms, new.entities);
        END;
        """
    )


def _insert_source(db: sqlite3.Connection, source: Source, memory_dir: Path,
                   snapshot: str | None = None) -> tuple[int, int]:
    chunks, corrupt = _source_chunks(source)
    for chunk in chunks:
        db.execute(
            """INSERT INTO chunks
               (path, chunk_key, source, source_type, visibility, automatic_eligible,
                at, event_id, desire_id, run_id,
                refs_json, supersedes_json, entities, terms, text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source.rel, chunk["chunk_key"], chunk.get("source") or source.path.stem,
                chunk.get("source_type") or source.kind,
                chunk["visibility"], int(bool(chunk.get("automatic_eligible"))
                    and memory_provenance.automatic_recall_allowed(
                    source_type=chunk.get("source_type") or source.kind,
                    path=source.rel, text=chunk["text"],
                    memory_dir=memory_dir,
                )), chunk["at"], chunk["event_id"],
                chunk.get("desire_id") or "", chunk["run_id"],
                json.dumps(chunk["refs"], ensure_ascii=False, separators=(",", ":")),
                json.dumps(chunk["supersedes"], ensure_ascii=False, separators=(",", ":")),
                " ".join(chunk["entities"]), _terms(chunk["text"]), chunk["text"],
            ),
        )
    db.execute(
        "INSERT OR REPLACE INTO source_state(path, corrupt_lines, snapshot) VALUES (?, ?, ?)",
        (source.rel, corrupt,
         _source_snapshot(source) if snapshot is None else snapshot),
    )
    return len(chunks), corrupt


def rebuild(*, base: Path, memory_dir: Path, skills_dir: Path | None = None,
            db_path: Path | None = None) -> dict:
    """Atomically rebuild the disposable database from canonical sources."""
    base, memory_dir = Path(base), Path(memory_dir)
    path = Path(db_path) if db_path is not None else _db_path(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    sources = iter_sources(base=base, memory_dir=memory_dir, skills_dir=skills_dir)
    snapshots = _snapshots(sources)
    fingerprint = _fingerprint_from(snapshots)
    tmp = path.with_name(path.name + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    chunks = corrupt = 0
    with _LOCK:
        try:
            with contextlib.closing(sqlite3.connect(tmp)) as db:
                _create_schema(db)
                for source in sources:
                    count, broken = _insert_source(db, source, memory_dir,
                                                   snapshot=snapshots.get(source.rel, ""))
                    chunks += count
                    corrupt += broken
                db.executemany(
                    "INSERT INTO meta(key, value) VALUES (?, ?)",
                    (("schema", SCHEMA_VERSION), ("fingerprint", fingerprint),
                     ("sources", str(len(sources))), ("chunks", str(chunks)),
                     ("corrupt_lines", str(corrupt))),
                )
                db.commit()
                check = db.execute("PRAGMA integrity_check").fetchone()
                if not check or check[0] != "ok":
                    raise sqlite3.DatabaseError(f"integrity_check: {check}")
            os.replace(tmp, path)
            for suffix in ("-wal", "-shm"):
                with contextlib.suppress(FileNotFoundError):
                    Path(str(path) + suffix).unlink()
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
    return {
        "ok": True, "schema": SCHEMA_VERSION, "database": _rel(path, base),
        "sources": len(sources), "chunks": chunks, "corrupt_lines": corrupt,
        "fingerprint": fingerprint,
    }


def _meta(path: Path) -> dict[str, str]:
    try:
        with contextlib.closing(sqlite3.connect(path)) as db:
            tables = {
                str(row[0]) for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                )
            }
            if not {"meta", "chunks", "chunks_fts", "source_state"}.issubset(tables):
                return {}
            return {str(key): str(value) for key, value in db.execute("SELECT key, value FROM meta")}
    except (OSError, sqlite3.Error):
        return {}


def ensure(*, base: Path, memory_dir: Path, skills_dir: Path | None = None,
           db_path: Path | None = None, sources: list[Source] | None = None) -> dict:
    """`sources` — уже сделанная перепись корпуса, если она у вызывающего есть.

    Обход корпуса — самая дорогая часть вопроса к памяти (24.0 с из 31.4 на замере 07.08),
    и `search` делал его дважды: здесь и в `_canonical_candidates`. Умолчание `None`
    сохраняет прежнее поведение для всех, кто зовёт `ensure` в одиночку.
    """
    path = Path(db_path) if db_path is not None else _db_path(Path(memory_dir))
    if sources is None:
        sources = iter_sources(base=Path(base), memory_dir=Path(memory_dir),
                               skills_dir=skills_dir)
    current = _meta(path)
    snapshots = _snapshots(sources)
    fingerprint = _fingerprint_from(snapshots)
    if current.get("schema") != SCHEMA_VERSION:
        return rebuild(base=Path(base), memory_dir=Path(memory_dir), skills_dir=skills_dir,
                       db_path=path)
    if current.get("fingerprint") != fingerprint:
        # The durable run tree appends events on every live turn, so the corpus
        # fingerprint moves constantly.  Re-parsing the whole multi-gigabyte canon
        # for that (minutes, while the mind lock is held) is what made explicit
        # recall crawl; reconcile only the sources whose stat snapshot moved.
        return _refresh_changed(base=Path(base), memory_dir=Path(memory_dir),
                                db_path=path, sources=sources,
                                snapshots=snapshots, fingerprint=fingerprint)
    return {
        "ok": True, "schema": SCHEMA_VERSION, "database": _rel(path, Path(base)),
        "sources": int(current.get("sources") or 0), "chunks": int(current.get("chunks") or 0),
        "corrupt_lines": int(current.get("corrupt_lines") or 0), "fingerprint": fingerprint,
        "rebuilt": False,
    }


def _refresh_changed(*, base: Path, memory_dir: Path, db_path: Path,
                     sources: list[Source], snapshots: dict[str, str],
                     fingerprint: str) -> dict:
    """Incrementally reconcile the disposable index with canon (v7).

    Claim kind/eligibility and life-event automatic ids are derived from the
    provenance evidence index rather than from each file's own bytes alone, so
    any change under memory/life/ also re-derives every dependent source; the
    FTS delete/insert triggers keep chunks_fts consistent row by row."""
    by_rel = {source.rel: source for source in sources}
    with _LOCK, contextlib.closing(sqlite3.connect(db_path, timeout=15)) as db:
        db.execute("PRAGMA busy_timeout=15000")
        stored = {str(row[0]): str(row[1]) for row in db.execute(
            "SELECT path, snapshot FROM source_state")}
        changed = {rel for rel, snapshot in snapshots.items()
                   if stored.get(rel) != snapshot}
        removed = [rel for rel in stored if rel not in by_rel]
        if any(rel.startswith("memory/life/") for rel in (*changed, *removed)):
            changed.update(
                rel for rel, source in by_rel.items()
                if rel.startswith("memory/life/claims/") or source.kind == "life_event"
            )
        for rel in removed:
            db.execute("DELETE FROM chunks WHERE path = ?", (rel,))
            db.execute("DELETE FROM source_state WHERE path = ?", (rel,))
        for rel in sorted(changed):
            db.execute("DELETE FROM chunks WHERE path = ?", (rel,))
            _insert_source(db, by_rel[rel], memory_dir, snapshot=snapshots.get(rel, ""))
        chunks = int(db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] or 0)
        corrupt = int(db.execute(
            "SELECT COALESCE(SUM(corrupt_lines), 0) FROM source_state").fetchone()[0] or 0)
        for key, value in (("fingerprint", fingerprint), ("sources", str(len(sources))),
                           ("chunks", str(chunks)), ("corrupt_lines", str(corrupt))):
            db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
        db.commit()
    return {
        "ok": True, "schema": SCHEMA_VERSION, "database": _rel(db_path, Path(base)),
        "sources": len(sources), "chunks": chunks, "corrupt_lines": corrupt,
        "fingerprint": fingerprint, "rebuilt": False, "refreshed": len(changed) + len(removed),
    }


def whole_docs_enabled() -> bool:
    """Единица памяти — документ, а не кусок в 92 знака.

    ⚠ ВЫКЛЮЧЕНО ПО УМОЛЧАНИЮ, И ВКЛЮЧЕНИЕ ТРЕБУЕТ ПЕРЕСБОРКИ: ключи кусков меняются, то
    есть старый индекс новому коду не годится. Её слово 08.08: «сначала подготовить
    реализацию, проверки и обратимый план без применения. Саму пересборку — после нашей
    паузы, отдельным осознанным окном. Старый индекс сохранить до успешной приёмки нового.»

    Замер, ради которого: 530 194 куска, медиана 92 знака, 47,6% короче восьмидесяти.
    Девяносто два знака — пятнадцать слов; на «вспомни» к ней приезжали обрывки
    предложений вместо мыслей.
    """
    return str(os.getenv("PRAXIS_MEMORY_WHOLE_DOCS") or "").strip().lower() in {
        "1", "true", "yes", "on"}


def drop_run_events_enabled() -> bool:
    """Убрать события прогонов из обычного recall. ЕЁ РЕШЕНИЕ, И ОНО НЕ ПРИНЯТО.

    ⚠ СВОЙ РЫЧАГ, А НЕ ХВОСТ ЧУЖОГО. Первая редакция привязывала эту отмену к рычагу
    цельных документов — а тот на проде включён с 08.08. То есть её решение от 02.08
    («события прогона остаются в обычном recall», закреплено `test_recap_and_events_
    stay_in_ordinary_recall`) отменилось бы САМО, молча, на первом же обновлении индекса,
    и я бы узнал об этом от неё, а не от прибора.

    Замер живой базы 09.08: 151 947 кусков из 203 182 — 75% индекса. Именно столько
    исчезло бы разом, без её слова и без единой строки в отчёте.

    Вопрос лежит у неё: `workspace/EVENTS-JSONL-ВОПРОС-08.08.md`. До ответа — выключено.
    """
    return str(os.getenv("PRAXIS_MEMORY_DROP_RUN_EVENTS") or "").strip().lower() in {
        "1", "true", "yes", "on"}


def lazy_canon_enabled() -> bool:
    """Сверять канон по мере надобности вместо «все 240 строк заранее».

    ⚠ ВЫКЛЮЧЕНО ПО УМОЛЧАНИЮ, И ЭТО НЕ ОСТОРОЖНОСТЬ РАДИ ОСТОРОЖНОСТИ. Свойство «ни одна
    строка не попадает в её промпт без сверки с каноном» ленивый путь сохраняет ДОСЛОВНО:
    он сверяет ровно те строки, которые дойдут до выдачи, и ни одной несверенной не
    пропускает. Меняется другое следствие: строка, которую всё равно выбросят фильтры,
    больше не сверяется — а значит её расхождение с одноразовой базой больше не запускает
    пересборку индекса. Это край договора о доверии к её памяти, и решение здесь ЕЁ.

    Замер, ради которого рычаг заведён (прод, «провенанс кадра», её рука целиком):
    240 строк из базы → 191 исходный файл перечитан ЦЕЛИКОМ → до неё доехало шесть.
    """
    return str(os.getenv("PRAXIS_RECALL_LAZY_CANON") or "").strip().lower() in {
        "1", "true", "yes", "on"}


def _select(candidates: list[dict], *, cap: int, purpose: str) -> list[dict]:
    """Фильтры выдачи. Одно тело на оба пути — иначе ленивый отдавал бы ДРУГОЙ ответ."""
    selected: list[dict] = []
    seen_desires: set[str] = set()
    for row in candidates:
        if purpose != "audit" and _is_transport_snapshot(str(row.get("path") or "")):
            continue
        desire_id = str(row.get("desire_id") or "")
        if row.get("source_type") == "desire_event" and desire_id:
            if desire_id in seen_desires:
                continue
            seen_desires.add(desire_id)
        selected.append(row)
        if len(selected) >= cap:
            break
    return selected


def _select_lazily(*, base: Path, memory_dir: Path, skills_dir: Path | None,
                   rows: list[dict], sources: list[Source] | None,
                   cap: int, purpose: str) -> tuple[list[dict], bool]:
    """Тот же отбор, но исходный файл перечитывается только когда до него дошла очередь.

    Порядок строк, поля и сама сверка — те же самые (`_canonical_row_ok`, одно тело на
    оба пути). Разница только в том, КОГДА платится чтение канона.
    """
    walked = sources if sources is not None else iter_sources(
        base=base, memory_dir=memory_dir, skills_dir=skills_dir,
    )
    by_rel = {source.rel: source for source in walked}
    parsed: dict[str, dict[str, tuple[Source, dict]]] = {}
    selected: list[dict] = []
    seen_desires: set[str] = set()
    mismatch = False
    for row in rows:
        rel = str(row.get("path") or "")
        # Транспорт и повтор желания отсеиваются ДО чтения канона: сверять то, что
        # заведомо не доедет до неё, — ровно та работа, ради отказа от которой всё это.
        if purpose != "audit" and _is_transport_snapshot(rel):
            continue
        desire_id = str(row.get("desire_id") or "")
        if row.get("source_type") == "desire_event" and desire_id and desire_id in seen_desires:
            continue
        known = parsed.get(rel)
        if known is None:
            source = by_rel.get(rel)
            known = ({} if source is None
                     else {str(chunk.get("chunk_key") or ""): (source, chunk)
                           for chunk in _source_chunks(source)[0]})
            parsed[rel] = known
        canonical = known.get(str(row.get("chunk_key") or ""))
        if canonical is None or not _canonical_row_ok(row, canonical[0], canonical[1],
                                                      memory_dir):
            mismatch = True
            continue
        if row.get("source_type") == "desire_event" and desire_id:
            seen_desires.add(desire_id)
        selected.append(row)
        if len(selected) >= cap:
            # Набрали. Расхождения ДАЛЬШЕ по списку до неё не доехали бы в любом случае,
            # поэтому и пересборку они больше не запускают — вот вся разница с прежним
            # путём, и она названа вслух здесь, а не спрятана в «оптимизации».
            return selected, False
    return selected, mismatch


def _match_query(query: str) -> str:
    stems = []
    for token in _WORD_RE.findall(str(query or "")):
        stem = _stem(token)
        if len(stem) > 1 and stem not in stems:
            stems.append(stem)
    # Unqualified phrases intentionally search original text, normalized terms and entity
    # identifiers.  Quoting every token keeps FTS operators in user input inert.
    return " OR ".join(f'"{stem.replace(chr(34), chr(34) * 2)}"*' for stem in stems)


def _canonical_candidates(*, base: Path, memory_dir: Path, skills_dir: Path | None,
                          rows: list[dict],
                          sources: list[Source] | None = None) -> tuple[list[dict], bool]:
    """Rebind cache locators to current canonical chunks.

    The SQLite file is disposable and may be stale or tampered with.  A returned row
    therefore has authority only when every prompt-relevant field exactly matches the
    chunk re-derived from the current source file.
    """
    wanted = {str(row.get("path") or "") for row in rows}
    # ⚑ Разделяемая перепись НЕ ослабляет сверку. Подлинность строки решает не список
    # источников, а `_source_chunks` ниже: он перечитывает БАЙТЫ файла. Список — только
    # перечисление, и здесь это ровно то же перечисление, которым `ensure` секундой раньше
    # сверял индекс, то есть сверка идёт против того же среза мира, а не против двух
    # разных. Файл, исчезнувший между шагами, по-прежнему даёт mismatch и уводит в
    # пересборку; после пересборки вызывающий обязан передать `sources=None`.
    walked = sources if sources is not None else iter_sources(
        base=base, memory_dir=memory_dir, skills_dir=skills_dir,
    )
    sources = {source.rel: source for source in walked if source.rel in wanted}
    chunks: dict[tuple[str, str], tuple[Source, dict]] = {}
    for rel, source in sources.items():
        for chunk in _source_chunks(source)[0]:
            chunks[(rel, str(chunk.get("chunk_key") or ""))] = (source, chunk)
    valid: list[dict] = []
    mismatch = False
    for row in rows:
        key = (str(row.get("path") or ""), str(row.get("chunk_key") or ""))
        canonical = chunks.get(key)
        if canonical is None:
            mismatch = True
            continue
        source, chunk = canonical
        if not _canonical_row_ok(row, source, chunk, memory_dir):
            mismatch = True
            continue
        valid.append(row)
    return valid, mismatch


def _canonical_row_ok(row: dict, source: Source, chunk: dict, memory_dir: Path) -> bool:
    """Совпадает ли строка одноразовой базы с куском, перечитанным из канона.

    Вынесено из тела `_canonical_candidates` ради ленивого пути: два способа сверки в
    двух телах разошлись бы молча, и разошлись бы именно там, где цена ошибки — её промпт.
    Одно тело, два вызывающих.
    """
    expected = {
        "source": chunk.get("source") or source.path.stem,
        "source_type": chunk.get("source_type") or source.kind,
        "visibility": chunk.get("visibility") or source.visibility,
        "automatic_eligible": int(bool(chunk.get("automatic_eligible"))
            and memory_provenance.automatic_recall_allowed(
                source_type=chunk.get("source_type") or source.kind,
                path=source.rel, text=chunk.get("text") or "", memory_dir=memory_dir,
            )),
        "at": chunk.get("at") or "", "event_id": chunk.get("event_id") or "",
        "desire_id": chunk.get("desire_id") or "", "run_id": chunk.get("run_id") or "",
        "refs_json": json.dumps(chunk.get("refs") or [], ensure_ascii=False,
                                separators=(",", ":")),
        "supersedes_json": json.dumps(chunk.get("supersedes") or [], ensure_ascii=False,
                                       separators=(",", ":")),
        "entities": " ".join(chunk.get("entities") or []),
        "terms": _terms(chunk.get("text") or ""),
        "text": chunk.get("text") or "",
    }
    return not any(str(row.get(field) if row.get(field) is not None else "") != str(value)
                   for field, value in expected.items())


_CANON_CACHE: dict[str, tuple[str, list[dict]]] = {}
_CANON_GEN = 0
_INDEX_CACHE: dict = {}


def _build_source_rows(source: "Source", memory_dir: Path) -> list[dict]:
    """Parse one source into automatic-eligible rows with precomputed match tokens.

    Pure function of the source bytes; memoised by stat snapshot in
    ``_automatic_canonical_chunks`` so an unchanged source is not re-parsed or
    re-stemmed on every live turn.  That per-turn rescan of the whole canon was
    the dominant pre-model latency."""
    out: list[dict] = []
    for chunk in _source_chunks(source)[0]:
        text = str(chunk.get("text") or "")
        source_type = str(chunk.get("source_type") or source.kind)
        visibility = str(chunk.get("visibility") or source.visibility)
        eligible = bool(chunk.get("automatic_eligible")) and memory_provenance.automatic_recall_allowed(
            source_type=source_type, path=source.rel, text=text, memory_dir=memory_dir,
        )
        if not eligible:
            continue
        refs = list(chunk.get("refs") or [])
        supersedes = list(chunk.get("supersedes") or [])
        entities = [str(value) for value in chunk.get("entities") or []]
        entities_text = " ".join(entities)
        source_name = str(chunk.get("source") or source.path.stem)
        terms = _terms(text)
        match_tokens = terms.split()
        match_tokens.extend(_terms(f"{entities_text} {source_name} {source.rel}").split())
        out.append({
            "path": source.rel,
            "chunk_key": str(chunk.get("chunk_key") or ""),
            "source": source_name,
            "source_type": source_type,
            "visibility": visibility,
            "automatic_eligible": 1,
            "at": str(chunk.get("at") or ""),
            "event_id": str(chunk.get("event_id") or ""),
            "desire_id": str(chunk.get("desire_id") or ""),
            "run_id": str(chunk.get("run_id") or ""),
            "refs_json": json.dumps(refs, ensure_ascii=False, separators=(",", ":")),
            "supersedes_json": json.dumps(
                supersedes, ensure_ascii=False, separators=(",", ":"),
            ),
            "entities": entities_text,
            "terms": terms,
            "text": text,
            "_mtokens": match_tokens,
        })
    return out


def _automatic_canonical_chunks(*, base: Path, memory_dir: Path,
                                skills_dir: Path | None, scope: str) -> list[dict]:
    """Derive the complete automatic corpus from canon, never from an index selection.

    Per-source parsing/stemming is memoised by stat snapshot: only sources whose
    bytes moved are re-parsed on a live turn.  ``_CANON_GEN`` is bumped whenever the
    row set changes so the inverted index (``_automatic_index``) knows to rebuild.
    Run artifacts stay excluded because the durable run tree grows on every turn and
    yields no eligible automatic chunk."""
    global _CANON_GEN
    rows: list[dict] = []
    seen: set[str] = set()
    for source in iter_sources(base=base, memory_dir=memory_dir, skills_dir=skills_dir,
                               include_runs=False):
        if source.rel.startswith("memory/runs/"):
            continue
        seen.add(source.rel)
        snapshot = _source_snapshot(source)
        cached = _CANON_CACHE.get(source.rel)
        if cached is not None and snapshot and cached[0] == snapshot:
            source_rows = cached[1]
        else:
            source_rows = _build_source_rows(source, memory_dir)
            if snapshot:
                _CANON_CACHE[source.rel] = (snapshot, source_rows)
            else:
                _CANON_CACHE.pop(source.rel, None)
            _CANON_GEN += 1
        if scope == "owner":
            rows.extend(source_rows)
        else:
            rows.extend(row for row in source_rows if row["visibility"] == "public")
    stale = [rel for rel in _CANON_CACHE if rel not in seen]
    for rel in stale:
        _CANON_CACHE.pop(rel, None)
    if stale:
        _CANON_GEN += 1
    return rows

_AUTOMATIC_MANIFEST_FIELDS = (
    "path", "chunk_key", "source", "source_type", "visibility", "automatic_eligible",
    "at", "event_id", "desire_id", "run_id", "refs_json", "supersedes_json",
    "entities", "terms", "text",
)


def _manifest(rows: Iterable[dict]) -> list[tuple[str, ...]]:
    return sorted(
        tuple(str(row.get(field) if row.get(field) is not None else "")
              for field in _AUTOMATIC_MANIFEST_FIELDS)
        for row in rows
    )


def _automatic_cache_matches(path: Path, canonical: list[dict]) -> bool:
    """Detect logical cache edits/omissions; automatic retrieval does not depend on it."""
    try:
        with contextlib.closing(sqlite3.connect(path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            fields = ", ".join(_AUTOMATIC_MANIFEST_FIELDS)
            cached = [dict(row) for row in db.execute(
                f"SELECT {fields} FROM chunks WHERE automatic_eligible = 1"
            ).fetchall()]
    except (OSError, sqlite3.Error):
        return False
    return _manifest(cached) == _manifest(canonical)


def _automatic_index(*, base: Path, memory_dir: Path,
                     skills_dir: Path | None, scope: str):
    """(retrieval_rows, sorted_tokens, postings) for automatic recall.

    ``postings`` maps each match token to {row_index: occurrence_count}.  Built once
    per (corpus generation, scope) and cached, so a live turn ranks only the rows that
    actually contain a query stem (O(matches)) instead of scanning every chunk against
    every stem (O(chunks x stems x tokens) — that grew to minutes once the recall query
    was the whole last_n conversation, hundreds of unique stems)."""
    canonical = _automatic_canonical_chunks(
        base=base, memory_dir=memory_dir, skills_dir=skills_dir, scope=scope,
    )
    key = (_CANON_GEN, scope)
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    latest_desire_rows: dict[str, dict] = {}
    retrieval_rows: list[dict] = []
    for row in canonical:
        desire_id = str(row.get("desire_id") or "")
        if row.get("source_type") != "desire_event" or not desire_id:
            retrieval_rows.append(row)
            continue
        previous = latest_desire_rows.get(desire_id)
        row_order = (str(row.get("at") or ""), str(row.get("chunk_key") or ""))
        previous_order = (
            str(previous.get("at") or ""), str(previous.get("chunk_key") or "")
        ) if previous else ("", "")
        if previous is None or row_order > previous_order:
            latest_desire_rows[desire_id] = row
    retrieval_rows.extend(latest_desire_rows.values())
    postings: dict[str, dict[int, int]] = {}
    for idx, row in enumerate(retrieval_rows):
        tokens = row.get("_mtokens")
        if tokens is None:
            tokens = str(row["terms"]).split()
            tokens.extend(_terms(
                f"{row['entities']} {row['source']} {row['path']}"
            ).split())
        for token in tokens:
            bucket = postings.get(token)
            if bucket is None:
                postings[token] = {idx: 1}
            else:
                bucket[idx] = bucket.get(idx, 0) + 1
    result = (retrieval_rows, sorted(postings), postings)
    for stale_key in [k for k in _INDEX_CACHE if k[0] != _CANON_GEN]:
        del _INDEX_CACHE[stale_key]
    _INDEX_CACHE[key] = result
    return result


def _automatic_search(query: str, *, base: Path, memory_dir: Path,
                      skills_dir: Path | None, limit: int, scope: str,
                      db_path: Path | None) -> list[dict]:
    """Rank a small strict corpus directly; SQLite/FTS cannot inject or hide a cue."""
    # Automatic prompt recall is derived and ranked from canonical sources below; it
    # never reads candidates from the disposable explicit-search database.  Do not
    # synchronously ensure that database here.  A live turn creates its durable run
    # before prompt recall, so run context/events change the full-corpus fingerprint on
    # every turn.  Ensuring here consequently rebuilt the complete index on every DM
    # (minutes of pre-model latency) despite the result being unused.  Explicit search
    # and explicit rebuild hooks remain the owners of cache materialization and repair.
    database = Path(db_path) if db_path is not None else _db_path(memory_dir)
    state = {"schema": SCHEMA_VERSION, "database": _rel(database, base)}

    stems = list(dict.fromkeys(
        _stem(token) for token in _WORD_RE.findall(str(query or ""))
        if len(_stem(token)) > 1
    ))
    if not stems:
        return []
    retrieval_rows, sorted_tokens, postings = _automatic_index(
        base=base, memory_dir=memory_dir, skills_dir=skills_dir, scope=scope,
    )
    n_stems = len(stems)
    n_tokens = len(sorted_tokens)
    row_hits: dict[int, list[int]] = {}
    for si, stem in enumerate(stems):
        j = bisect.bisect_left(sorted_tokens, stem)
        while j < n_tokens:
            token = sorted_tokens[j]
            if not token.startswith(stem):
                break
            for row_idx, cnt in postings[token].items():
                counts = row_hits.get(row_idx)
                if counts is None:
                    counts = [0] * n_stems
                    row_hits[row_idx] = counts
                counts[si] += cnt
            j += 1
    ranked: list[tuple[float, dict]] = []
    for row_idx, hits in row_hits.items():
        matched = sum(1 for count in hits if count)
        if not matched:
            continue
        row = retrieval_rows[row_idx]
        tokens_len = len(row.get("_mtokens") or ())
        score = matched / n_stems + min(0.25, sum(hits) / max(8.0, tokens_len))
        ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["chunk_key"]))
    selected = ranked[:max(1, min(int(limit or 30), 500))]
    strongest = max((score for score, _ in selected), default=1.0)
    out: list[dict] = []
    for score, row in selected:
        refs = json.loads(row["refs_json"] or "[]")
        supersedes = json.loads(row["supersedes_json"] or "[]")
        out.append({
            "id": f"{row['path']}#{row['chunk_key']}", "text": row["text"],
            "source": row["source"], "path": row["path"],
            "lexical": round(score / strongest, 6), "source_type": row["source_type"],
            "visibility": row["visibility"], "automatic_eligible": True,
            "automatic_canonical": True, "at": row["at"],
            "event_id": row["event_id"], "run_id": row["run_id"],
            "desire_id": row["desire_id"], "refs": refs,
            "supersedes": supersedes,
            "provenance": list(dict.fromkeys([*refs, *supersedes])),
            "index": f"{state.get('schema') or SCHEMA_VERSION}:canonical",
        })
    return out


def _is_transport_snapshot(rel: str) -> bool:
    """`memory/runs/**/context.md` — конверт хода, а не её память.

    02.08.2026, её решение по замеру: 154 488 кусков из 379 977 (40.7% кусков, 64.9%
    текста индекса) — это `context.md`. Он не написан ею: это техническая карточка,
    которую собирает система, чтобы дать ей ход — схемы, идентификаторы, маршрут, флаги
    прав, возраст обращения. Её слова: «транспортный след притворяется моей памятью».

    Прячется он именно ЗДЕСЬ, а не при индексации, и это существенно. Автоматический
    recall его и так не видел; но в `context.md` лежит ДОСЛОВНЫЙ `origin_text` прошлых
    реплик, поэтому на обычном явном «вспомни» FTS вытаскивал его по точным словам —
    и её собственный журнал конкурировал с транспортом сто к одному. При этом контракт
    «generated run context remains explicitly searchable for audit» верен и остаётся: он
    про возможность посмотреть, а не про то, чтобы это подмешивалось в каждый ответ
    памяти. Поэтому граница проходит по ЦЕЛИ обращения, а не по составу индекса — файлы
    и их куски на месте, аудит их достаёт, обычный recall больше не наступает на них.
    """
    return rel.startswith("memory/runs/") and rel.endswith("/context.md")


def _audit_transport_hits(query: str, *, base: Path, memory_dir: Path,
                          limit: int) -> list[dict]:
    """Транспортные снимки прогонов для цели `audit` — ПРЯМЫМ чтением канона.

    ⚠ Это половина, без которой правка «не индексировать context.md» нарушила бы её
    контракт от 02.08, закреплённый тестом `test_nothing_left_the_index`: «ничего не
    удалено и не разындексировано, иначе аудит был бы пустым словом».

    Здесь аудит не слабее, а СТРОЖЕ прежнего: он больше не спрашивает одноразовую базу,
    он читает сами файлы. Цена — обход дерева прогонов при аудите; она уместна, потому
    что аудит редок и точен, а recall част и широк.
    """
    stems = [s for s in (_stem(t) for t in _WORD_RE.findall(str(query or "")))
             if len(s) > 1]
    if not stems:
        return []
    runs = Path(memory_dir) / "runs"
    if not runs.is_dir():
        return []
    hits: list[dict] = []
    for path in sorted(runs.rglob("context.md"), key=lambda p: p.name, reverse=True):
        if len(hits) >= limit:
            break
        text = _read(path)
        if not text:
            continue
        terms = _terms(text)
        if not all(stem in terms for stem in stems):
            continue
        rel = _rel(path, Path(base))
        # Форма записи — ТА ЖЕ, что у индексных: сборщик выдачи ниже читает `chunk_key`
        # и `refs_json`, и вторая форма молча упала бы KeyError'ом на первом же аудите.
        hits.append({
            "path": rel, "chunk_key": "doc", "text": text,
            "source": path.parent.name, "source_type": "run_context",
            "visibility": "owner", "automatic_eligible": 0, "at": "",
            "event_id": "", "run_id": path.parent.name, "desire_id": "",
            "refs_json": "[]", "supersedes_json": "[]", "rank": 0.0,
        })
    return hits


def search(query: str, *, base: Path, memory_dir: Path, skills_dir: Path | None = None,
           limit: int = 30, scope: str = "owner", purpose: str = "explicit",
           db_path: Path | None = None) -> list[dict]:
    """Rank FTS candidates and expose provenance/visibility metadata.

    ``purpose='audit'`` — третья цель (02.08): всё то же, что explicit, плюс
    транспортные снимки прогонов. Ровно она сохраняет прежний контракт «run context
    остаётся явно доступным для аудита», не заставляя её память подмешивать конверт
    хода в каждый ответ.
    """
    query = str(query or "").strip()
    if not query:
        return []
    base, memory_dir = Path(base), Path(memory_dir)
    skills_dir = Path(skills_dir) if skills_dir is not None else None
    purpose = ("automatic" if purpose == "automatic"
               else "audit" if purpose == "audit" else "explicit")
    if purpose == "automatic":
        return _automatic_search(
            query, base=base, memory_dir=memory_dir, skills_dir=skills_dir,
            limit=limit, scope=scope, db_path=db_path,
        )
    # ⚠ ОДИН ОБХОД КОРПУСА НА ПОИСК, А НЕ ДВА. Профиль 07.08 на живом проде: `iter_sources`
    # 24.0 с из 31.4, при ДВУХ вызовах — сначала из `ensure`, потом из
    # `_canonical_candidates`. Перечисление между ними одно и то же.
    sources = iter_sources(base=base, memory_dir=memory_dir, skills_dir=skills_dir)
    state = ensure(base=base, memory_dir=memory_dir, skills_dir=skills_dir,
                   db_path=db_path, sources=sources)
    path = Path(db_path) if db_path is not None else _db_path(Path(memory_dir))
    match = _match_query(query)
    if not match:
        return []
    cap = max(1, min(int(limit or 30), 500))
    visibility_sql = "" if scope == "owner" else " AND c.visibility = 'public'"
    automatic_sql = " AND c.automatic_eligible = 1" if purpose == "automatic" else ""
    sql = f"""
        SELECT c.*, bm25(chunks_fts, 1.0, 0.45, 0.7) AS rank
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        WHERE chunks_fts MATCH ? {visibility_sql} {automatic_sql}
        ORDER BY rank ASC, c.path ASC, c.chunk_key ASC
        LIMIT ?
    """
    candidates: list[dict] = []
    selected: list[dict] = []
    for attempt in range(2):
        with contextlib.closing(sqlite3.connect(path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            cached = [dict(row) for row in db.execute(
                sql, (match, min(2000, max(80, cap * 8))),
            ).fetchall()]
        if lazy_canon_enabled():
            selected, mismatch = _select_lazily(
                base=Path(base), memory_dir=Path(memory_dir), skills_dir=skills_dir,
                rows=cached, sources=sources, cap=cap, purpose=purpose,
            )
        else:
            candidates, mismatch = _canonical_candidates(
                base=Path(base), memory_dir=Path(memory_dir), skills_dir=skills_dir,
                rows=cached, sources=sources,
            )
            selected = _select(candidates, cap=cap, purpose=purpose)
        if not mismatch:
            break
        if attempt == 0:
            state = rebuild(base=Path(base), memory_dir=Path(memory_dir),
                            skills_dir=skills_dir, db_path=path)
            # Расхождение означает, что мир поехал под нами. Вторая попытка обязана
            # смотреть ЗАНОВО — переиспользовать перепись, которая уже соврала, нельзя.
            sources = None
            continue
        # Canon changed twice during one read or the cache cannot be reconciled.
        # Fail closed instead of letting a disposable row become prompt authority.
        selected = []
    if purpose == "audit" and whole_docs_enabled():
        # Транспорт больше не в индексе — аудит добирает его прямым чтением канона.
        # Дописывается В КОНЕЦ: индексные попадания ранжированы, эти нет, и смешивать
        # два порядка молча нельзя.
        # ⚠ Раньше здесь стоял ОСТАТОК бюджета — и индексные попадания съедали его
        # целиком, отчего аудит возвращал ноль транспортных строк. Поймано приёмкой
        # 08.08. Теперь у транспорта своя доля: аудит за ним и ходит.
        share = max(1, cap // 2)
        transport = _audit_transport_hits(
            query, base=Path(base), memory_dir=Path(memory_dir), limit=share)
        selected = (selected[:max(0, cap - len(transport))] + transport) if transport else selected
    strengths = [max(0.0, -float(row.get("rank") or 0.0)) for row in selected]
    strongest = max(strengths, default=0.0)
    out = []
    for index, (row, strength) in enumerate(zip(selected, strengths)):
        lexical = strength / strongest if strongest > 0 else 1.0 - index / max(1, len(selected))
        refs = json.loads(row.get("refs_json") or "[]")
        supersedes = json.loads(row.get("supersedes_json") or "[]")
        provenance = list(dict.fromkeys([*refs, *supersedes]))
        out.append({
            "id": f"{row['path']}#{row['chunk_key']}", "text": row["text"],
            "source": row["source"], "path": row["path"],
            "lexical": round(float(lexical), 6), "source_type": row["source_type"],
            "visibility": row["visibility"],
            "automatic_eligible": bool(row["automatic_eligible"]), "at": row["at"],
            "automatic_canonical": purpose == "automatic",
            "event_id": row["event_id"], "run_id": row["run_id"],
            "desire_id": row.get("desire_id") or "",
            "refs": refs, "supersedes": supersedes, "provenance": provenance,
            "index": state.get("schema"),
        })
    return out


def upsert(path: str | Path, *, base: Path, memory_dir: Path,
           skills_dir: Path | None = None, db_path: Path | None = None) -> dict:
    """Refresh one eligible source; rebuild if the database is absent or incompatible."""
    base, memory_dir = Path(base), Path(memory_dir)
    target = Path(path).resolve()
    database = Path(db_path) if db_path is not None else _db_path(memory_dir)
    current = _meta(database)
    if current.get("schema") != SCHEMA_VERSION:
        return rebuild(base=base, memory_dir=memory_dir, skills_dir=skills_dir, db_path=database)
    sources = iter_sources(base=base, memory_dir=memory_dir, skills_dir=skills_dir)
    source = next((item for item in sources if item.path.resolve() == target), None)
    rel = _rel(target, base)
    with _LOCK, contextlib.closing(sqlite3.connect(database, timeout=15)) as db:
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("DELETE FROM chunks WHERE path = ?", (rel,))
        db.execute("DELETE FROM source_state WHERE path = ?", (rel,))
        count = corrupt = 0
        if source is not None:
            count, corrupt = _insert_source(db, source, memory_dir)
        fingerprint = _fingerprint(sources)
        totals = db.execute("SELECT COUNT(*), COUNT(DISTINCT path) FROM chunks").fetchone()
        corrupt_total = db.execute(
            "SELECT COALESCE(SUM(corrupt_lines), 0) FROM source_state"
        ).fetchone()[0]
        values = {
            "fingerprint": fingerprint, "chunks": str(int(totals[0] or 0)),
            "sources": str(len(sources)),
            "corrupt_lines": str(int(corrupt_total or 0)),
        }
        for key, value in values.items():
            db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
        db.commit()
    return {"ok": True, "path": rel, "chunks": count, "indexed": source is not None}


if __name__ == "__main__":
    import sys

    root = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
    mem = root / "memory"
    skills = root / "soul" / "skills"
    if len(sys.argv) > 1 and sys.argv[1] == "search":
        print(json.dumps(search(" ".join(sys.argv[2:]), base=root, memory_dir=mem,
                                skills_dir=skills), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(rebuild(base=root, memory_dir=mem, skills_dir=skills),
                         ensure_ascii=False, indent=2))
