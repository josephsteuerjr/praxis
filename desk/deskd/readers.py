# -*- coding: utf-8 -*-
"""Читатели дерева агента для окна Hélène. ТОЛЬКО чтение.

Правила, выученные до этого приложения:
- никаких замков её раннера (`run_listing` берёт .run.lock — сюда нельзя);
- никакого `revision_hint` и глобов по всему дереву;
- каталог прогона вычисляется из его id (месяц зашит в имени);
- любой хвост журнала читается с конца файла, а не через весь файл.
"""
from __future__ import annotations

import difflib
import ipaddress
import json
import logging
import math
import os
import pathlib
import re
import shutil
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from deskd import rooms  # noqa: E402  — комнаты окна (реестр и архивы)

log = logging.getLogger("helene.readers")

_RUN_ID_RE = re.compile(r"^run-(\d{4})(\d{2})\d{2}T\d{6,}Z?-[0-9a-f]{8}$")
_MD_CAP = 400_000

_TREE_SAID = {"path": None}


def tree() -> Path:
    """Дерево агента. Источник — только HELENE_TREE; остальное фолбэки.

    Было: `Path("/data")` без оговорок. На Windows это НЕ линуксовый /data, а
    `C:\\data` — корень ТЕКУЩЕГО диска. При ручном запуске без HELENE_TREE труба
    молча брала за дерево агента чужую папку и safe_write_md писал бы туда.
    Ветка оставлена только там, где она и задумана (сервер), и выбор пишется
    в лог одной строкой: раньше не логировался ни один из трёх вариантов.
    """
    override = os.environ.get("HELENE_TREE") or os.environ.get("PRAXIS_DESK_TREE")
    if override:
        return _said(Path(override), "HELENE_TREE")
    if os.name != "nt":
        data = Path("/data")
        if data.is_dir():
            return _said(data, "/data (сервер)")
        # Установка macOS без переменной (канал запущен руками из папки
        # программы): дерево — там, куда указывает helene.json рядом, тем же
        # правилом, что у движка (`tree` относительно папки конфига). Только на
        # POSIX: цепочка Windows остаётся прежней по правилу порта.
        stated = _tree_from_config()
        if stated is not None:
            return _said(stated, "helene.json рядом (HELENE_TREE не задан)")
    # локальная разработка: клон прода лежит рядом с desk/
    return _said(Path(__file__).resolve().parent.parent.parent / "live",
                 "фолбэк рядом с desk/ (HELENE_TREE не задан!)")


def _tree_from_config() -> Path | None:
    """Дерево по `tree` из helene.json рядом с каналом; None — конфига нет ИЛИ
    указанной папки нет на диске. Проверка `is_dir()` важна: без неё труба брала
    бы за дерево несуществующий путь из конфига и не падала бы в фолбэк рядом с
    `desk/`, а safe_write_md писал бы в пустоту."""
    path = config_path()
    if path is None:
        return None
    raw = str(_load_json(path).get("tree") or "data")
    tree_path = Path(raw)
    tree_path = tree_path if tree_path.is_absolute() else (path.parent / tree_path).resolve()
    return tree_path if tree_path.is_dir() else None


def _said(path: Path, why: str) -> Path:
    if _TREE_SAID["path"] != str(path):
        _TREE_SAID["path"] = str(path)
        log.info("дерево агента: %s — %s", path, why)
    return path


# ------------------------------------------------------- конфиг продукта
# helene.json лежит рядом с exe (Helene/helene.json), труба — в Helene/app/.
# Читаем ровно два-три скаляра: настроена ли модель и включено ли реле. Ключи
# и токены отсюда не уходят в ответы НИКОГДА — берём поимённо, не блоком.

_CFG_CACHE: dict[str, Any] = {"stamp": None, "value": {}}

# Как в ui-kit/contract.json (сверка — tests/t_contract.py).
CONFIG_NAME = "helene.json"
RELAY_PORT_DEFAULT = 5011


def config_path() -> Path | None:
    raw = os.environ.get("HELENE_CONFIG")
    if raw:
        path = Path(raw)
        return path if path.is_file() else None
    here = Path(__file__).resolve()
    for cand in (here.parents[2] / CONFIG_NAME, here.parents[1] / CONFIG_NAME):
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


_DESK_CACHE: dict = {"stamp": None, "value": {}}


def desk_build(root: Path | None = None) -> dict:
    """Чем поднят канал: версия и отпечаток пакета desk (``desk.json`` рядом).

    Пакет кладут обе установки — и поставка Windows в ``app/``, и выкладка
    Пульта на сервер (``deskpkg.build``). На сервере это ЕДИНСТВЕННЫЙ способ
    узнать версию: оболочки, которая отвечает окну ``app_info``, там нет, и
    подпись в окне до 0.5.1 показывала одно имя продукта без числа.

    Пусто — пакета нет (запуск из репозитория или поставка старше 0.5.1):
    окно тогда молчит о версии, а не выдумывает её.
    """
    path = (Path(root) if root else Path(__file__).resolve().parent.parent) / "desk.json"
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        _DESK_CACHE.update(stamp=None, value={})
        return {}
    if _DESK_CACHE["stamp"] == stamp:
        return _DESK_CACHE["value"]
    raw = _load_json(path)
    value: dict = {}
    if isinstance(raw, dict) and raw.get("product") == "desk":
        value = {"version": str(raw.get("version") or ""),
                 "flavor": str(raw.get("flavor") or ""),
                 # Двенадцати знаков хватает, чтобы сверить выкладку с
                 # собранным пакетом, и они не превращают ответ в простыню.
                 "digest": str(raw.get("digest") or "")[:12],
                 "built_utc": str(raw.get("built_utc") or "")}
        skipped = [s.get("name") for s in (raw.get("skipped") or []) if isinstance(s, dict)]
        if skipped:
            value["skipped"] = skipped
    _DESK_CACHE.update(stamp=stamp, value=value)
    return value


def product_config() -> dict:
    path = config_path()
    if path is None:
        return {}
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    if _CFG_CACHE["stamp"] == stamp:
        return _CFG_CACHE["value"]
    raw = _load_json(path)
    relay = raw.get("relay") or {}
    model = raw.get("model") or {}
    try:
        port = int(relay.get("port") or RELAY_PORT_DEFAULT)
    except (TypeError, ValueError):
        port = RELAY_PORT_DEFAULT
    value = {
        "relay_enabled": bool(relay.get("enabled")),
        "relay_port": port,
        "model": str(model.get("model") or "").strip(),
        "base_url": str(model.get("base_url") or "").strip(),
        # Только ФАКТ «ключ задан», сам ключ отсюда не берётся никогда и наружу
        # не уходит. Это самый свежий из трёх источников: файл переписывают
        # «Настройки» окна, и пустой ключ виден сразу после «Сохранить», а не
        # после перезапуска раннера.
        "key_present": bool(str(model.get("key") or model.get("api_key")
                                or "").strip()),
        # Имя агента — для комнаты окна по умолчанию (rooms.title): она
        # зовётся именем агента, а не «Окно».
        "agent_name": str((raw.get("agent") or {}).get("name")
                          or (raw.get("telegram") or {}).get("agent_name") or "").strip(),
    }
    _CFG_CACHE["stamp"] = stamp
    _CFG_CACHE["value"] = value
    return value


# ---------------------------------------------------------------- режим
# Режим-ограда (песочница | интерактивный) и ОТДЕЛЬНО от него служба — всё это
# разбирает ОДИН модуль, `localharness/modes.py`. Читатели трубы его
# импортируют, а не повторяют: вторая реализация правил миграции разошлась бы с
# руннером в первый же месяц, и окно показывало бы не тот режим, в котором агент
# живёт.
#
# ⚠ Служба здесь НЕ режим (04.09). Она опция поверх любой из двух оград и ограду
# не снимает; поэтому `name` — всегда `sandbox` или `interactive`, а про службу
# отвечают `service_installed`, `session0` и `firewall`.

#: Ответ, когда режим прочитать не вышло. Форма та же, что при удаче: окно
#: читает поля без проверок, и рубеж не должен ронять экран вместо трубы. Имя
#: режима пустое намеренно — назвать наугад «интерактивный» значило бы соврать
#: про то, в каких правах живёт агент. Одна копия на обе аварийные ветки
#: (`mode_state` и `_mode_state_safe`): третья разошлась бы с `modes.describe`.
_MODE_UNKNOWN: dict = {
    "name": "", "title": "Режим не прочитан", "text": "",
    "sandbox": False, "explicit": False, "source": "",
    "service_here": False,
    "service_installed": None, "service_title": "", "service_text": "",
    "session0": False, "session0_set": False, "session0_warning": "",
    "firewall": False, "firewall_set": False, "legacy_service": False,
    "notes": [], "choices": [], "service": None, "config": "",
}


def mode_unknown(why: str) -> dict:
    """Копия `_MODE_UNKNOWN` с причиной на месте описания и в тревогах."""
    out = dict(_MODE_UNKNOWN)
    out["text"] = why
    out["notes"] = [why] if why else []
    return out


_MODES: dict = {"mod": None, "tried": False}


def _modes():
    """Модуль режима. None — не нашёлся (тогда окно скажет об этом честно).

    Раскладка одна и в дереве разработки (desk/deskd + desk/localharness), и в
    поставке (Helene/app/deskd + Helene/app/localharness): сосед по папке.
    """
    if not _MODES["tried"]:
        _MODES["tried"] = True
        try:
            here = Path(__file__).resolve().parent.parent / "localharness"
            if str(here) not in sys.path:
                sys.path.append(str(here))
            import modes as mod
            _MODES["mod"] = mod
        except Exception:
            log.warning("модуль режима не импортировался (%s)", "localharness/modes.py",
                        exc_info=True)
    return _MODES["mod"]


def mode_state() -> dict:
    """Какая ограда выбрана, что со службой и что это значит — окну и агенту.

    Читает helene.json (единственный источник) плюс SCM: стоит ли служба на
    самом деле. Ничего не пишет: запись явного режима — дело руннера
    (`runner._settle_mode`), у читателей трубы права на правку конфига нет.

    `choices` (две ограды) и `service` (опция службы с её галочками) едут вместе
    с ответом намеренно: экраны установщика и настроек берут названия и
    описания ОТСЮДА, а не пишут свои. Разошедшиеся описания означали бы, что
    владелец выбирает одно, а получает другое.
    """
    mod = _modes()
    if mod is None:
        return mode_unknown("модуль режима не нашёлся рядом с каналом — "
                            "смотри helene.log")
    path = config_path()
    if path is None:
        return mode_unknown("helene.json рядом не найден — режим неизвестен")
    cfg = _load_json(path)
    try:
        picture = mod.describe(mod.resolve(cfg))
    except Exception:
        log.exception("режим не разобрался")
        return mode_unknown("режим не разобрался — смотри helene.log")
    picture["choices"] = mod.catalogue()
    # Секции службы и тела — только там, где они есть (Windows). На macOS их нет
    # по замыслу порта: вместо текстов — None, и окно карточек не рисует (прячет
    # по `app_info.platform`; `service_here` — та же правда со стороны канала).
    picture["service"] = mod.service_option() if picture.get("service_here", True) else None
    # Управление компьютером — опция поверх любого режима, не режим (06.09).
    # Что записал владелец — из конфига; что с телом на самом деле — снимок
    # раннера (`body.py` пишет его сторожем раз в несколько секунд). Старый
    # harness без опции отдаёт пустоту, и окно говорит об этом словами.
    has_body = bool(getattr(mod, "HAS_COMPUTER", True))
    try:
        picture["computer"] = mod.computer_state(cfg) if has_body else None
        picture["computer_option"] = mod.computer_option() if has_body else None
    except AttributeError:
        picture["computer"] = None
        picture["computer_option"] = None
    picture["computer_live"] = (_load_json(tree() / "memory" / ".state" / "body.json")
                                if has_body else {})
    # Просьбы агента о папках — живьём, а не через анатомию (ревью 06.09, §5:
    # анатомия пишется один раз на старте, и просьба `mount_request` доходила до
    # карточки только после перезапуска). Файл пишет ограда (`fence.Mounts.
    # save_requests`): {"v": 1, "updated_at": …, "requests": [{path, real,
    # access, why, at, asked}]}; отвеченные просьбы она же из него убирает.
    # Нет файла — пустой список, а не отсутствие поля: окно различает «просьб
    # нет» и «харнесс старый, поля не знает».
    live_mounts = _load_json(tree() / "memory" / ".state" / "mounts.json")
    picture["mounts_live"] = {
        "updated_at": live_mounts.get("updated_at"),
        "requests": [r for r in (live_mounts.get("requests") or [])
                     if isinstance(r, dict)],
    }
    picture["config"] = str(path)
    return picture


def _url_host_port(url: str) -> tuple[str, int | None]:
    try:
        parts = urlsplit(str(url or "").strip())
        return (parts.hostname or "").lower(), parts.port
    except ValueError:
        return "", None


def _url_is_local(url: str) -> bool:
    host, _ = _url_host_port(url)
    if host in ("localhost", ""):
        return host == "localhost"
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _load_json(path: Path) -> dict:
    """Любой json продукта. Кодировка — как её сохранил редактор ВЛАДЕЛЬЦА.

    ⚠ Через эту функцию читается и `helene.json`, который ПЕРВЫЙ-ЗАПУСК.md прямо
    зовёт править руками. Блокнот, VS Code и `Set-Content` из PowerShell 5.1
    пишут UTF-8 с меткой BOM или UTF-16 — строгий `encoding="utf-8"` возвращал на
    таком файле пустоту МОЛЧА, и окно писало «Модель не настроена» над верным
    конфигом. Оболочка тот же файл читает и BOM снимает; расходиться им нельзя.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    text = ""
    try:
        if raw[:3] == b"\xef\xbb\xbf":
            text = raw[3:].decode("utf-8")
        elif raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = raw.decode("utf-16")
        elif b"\x00" in raw[:4096]:          # UTF-16 без метки: в json нулей нет
            text = raw.decode("utf-16-le" if raw[1:2] == b"\x00" else "utf-16-be")
        else:
            text = raw.decode("utf-8")
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (UnicodeDecodeError, ValueError):
        return {}


def _num(value, default: float = 0.0) -> float:
    """Число из строки jsonl, которая может быть чем угодно.

    Здесь стоял голый `float(row.get("ts") or 0)` — в девяти местах. Строки в
    `llm_calls.jsonl` и `perception_skips.jsonl` пишет дерево агента, файл
    дописывается годами и НЕ РОТИРУЕТСЯ: достаточно одной строки с нечисловым
    `ts` (оборванная запись, ручная правка, чужой хвост после сбоя питания) —
    и `state()` с `health()` падают `ValueError` НАВСЕГДА. Наружу это выходило
    500-м: шапка окна пустела на каждом опросе, телефон получал голый отказ, и
    для владельца весь продукт выглядел мёртвым, хотя агент работал.

    Кривое значение — не число: возвращаем умолчание и идём дальше, как будто
    строки не было. NaN и бесконечность тоже отсекаем: они молча ломают max(),
    сравнения возрастов и округление в шапке.
    """
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _whole(value, default: int = 0) -> int:
    """То же для счётчиков токенов: `int("12.5")` — тоже ValueError."""
    return int(_num(value, default))


def tail_lines(path: Path, n: int, *, max_bytes: int = 4_000_000) -> list[str]:
    """Последние n строк файла без чтения его целиком.

    n нормализуется здесь: `lines[-n:]` при n<=0 отдаёт ВЕСЬ буфер (при n=-1 —
    `lines[1:]`), и объявленный вызывающими потолок (600 реплик) обходился
    одним `?n=-1`, вывозя до max_bytes переписки владельца.
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    take = min(size, max_bytes)
    try:
        with path.open("rb") as fh:
            fh.seek(size - take)
            raw = fh.read(take)
    except OSError:
        return []
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    if take < size and lines:
        lines = lines[1:]  # первая строка среза почти наверняка рваная
    return lines[-n:]


def tail_jsonl(path: Path, n: int) -> list[dict]:
    rows: list[dict] = []
    for line in tail_lines(path, n):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# ------------------------------------------------------------------ прогоны

def run_dir(run_id: str) -> Path | None:
    match = _RUN_ID_RE.match(run_id or "")
    if not match:
        return None
    month = f"{match.group(1)}-{match.group(2)}"
    path = tree() / "memory" / "runs" / month / run_id
    return path if path.is_dir() else None


def list_runs(limit: int = 80, kind: str = "", before: str = "",
              *, with_titles: bool = True) -> list[dict]:
    """Свежие прогоны, новые первыми. Имя каталога сортирует по времени само.

    with_titles=False — для сторожа: ему нужно только имя свежего прогона, а
    chat_titles() вычитывает 4 МБ хвоста turns.jsonl. Сторож тикал раз в 1.5 с
    круглосуточно и в простое читал десятки гигабайт в сутки ради заголовков,
    которые никто не смотрел.
    """
    root = tree() / "memory" / "runs"
    if not root.is_dir():
        return []
    months = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    titles = chat_titles() if with_titles else {}
    out: list[dict] = []
    for month in months:
        try:
            # Только каноничные id: рядом живут каталоги старого praxis_app
            # (`run-app-server-…`), их манифесты — другой мир.
            names = sorted((p.name for p in month.iterdir()
                            if _RUN_ID_RE.match(p.name)), reverse=True)
        except OSError:
            continue
        for name in names:
            if before and name >= before:
                continue
            manifest = _load_json(month / name / "manifest.json")
            context = manifest.get("context") or {}
            run_kind = str(context.get("kind") or "")
            if kind and run_kind != kind:
                continue
            goal = str(context.get("goal") or "").strip()
            terminal = manifest.get("terminal") or {}
            chat_id = context.get("delivery_chat_id") or context.get("origin_chat_id")
            out.append({
                "id": name,
                "created_at": manifest.get("created_at") or "",
                "updated_at": manifest.get("updated_at") or "",
                "event_seq": manifest.get("event_seq"),
                "status": manifest.get("status") or "",
                "kind": run_kind,
                "chat_id": chat_id,
                "chat_title": _title_for(chat_id, titles),
                "forge_task_id": context.get("forge_task_id") or "",
                "goal_head": goal.splitlines()[0][:140] if goal else "",
                "terminal_status": terminal.get("status") or "",
                "model_profile": context.get("model_profile") or "",
            })
            if len(out) >= limit:
                return out
    return out


_TITLES_CACHE: dict[str, Any] = {"stamp": None, "value": {}}


def chat_titles(n: int = 4000) -> dict[str, str]:
    """chat_id -> живое имя, из хвоста turns.jsonl.

    У комнат имя лежит в `title`, у личек `title` пуст — имя собеседника в `who`.

    Результат кэшируется по mtime/размеру turns.jsonl: на одно событие хода этот
    хвост (до 4 МБ) читался дважды — из list_runs и из chats().
    """
    path = tree() / "memory" / ".state" / "turns.jsonl"
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size, n)
    except OSError:
        stamp = (str(path), None, None, n)
    if _TITLES_CACHE["stamp"] == stamp:
        return _TITLES_CACHE["value"]
    titles: dict[str, str] = {}
    for row in tail_jsonl(path, n):
        chat_id = row.get("chat_id")
        if chat_id is None:
            continue
        key = str(chat_id)
        title = str(row.get("title") or "").strip()
        if not title and not key.startswith("-"):
            title = str(row.get("who") or "").strip()
        if title:
            titles[key] = title
    _TITLES_CACHE["value"] = titles
    _TITLES_CACHE["stamp"] = stamp
    return titles


_ROOM_TITLES_CACHE: dict[str, Any] = {"stamp": None, "value": {}}
# «Комната -100…» / «Комната 4122…» — заглушка ядра, когда настоящего имени у
# места ещё не было; заголовком чата такое не считается.
_PLACEHOLDER_TITLE_RE = re.compile(r"^(комната|чат|room)\s+-?\d+$", re.IGNORECASE)


def _room_titles() -> dict[str, str]:
    """chat_id -> имя места из ПОСТОЯННЫХ реестров агента, не из хвоста журнала.

    Хвост turns.jsonl (см. chat_titles) живёт 300–600 строк и после компакта
    забывает чаты, где давно не было хода, — список «Чаты» показывал «чат
    -1004301095307» вместо «mycelium» (замечено владельцем 08.09). Настоящие имена
    лежат дольше: заголовок `# Имя` в memory/rooms/<peer>.md (профиль места) и
    known_ids.json (id → имя для личек). Кэш — по mtime каталога rooms и файла
    known_ids; топики докладываются из проекции group_context по запросу.
    """
    root = tree() / "memory"
    rooms_dir = root / "rooms"
    known_path = root / "known_ids.json"
    stamp: list[Any] = []
    for p in (rooms_dir, known_path):
        try:
            st = p.stat()
            stamp.append((st.st_mtime_ns, st.st_size))
        except OSError:
            stamp.append(None)
    if _ROOM_TITLES_CACHE["stamp"] == stamp:
        return _ROOM_TITLES_CACHE["value"]
    titles: dict[str, str] = {}
    try:
        for p in rooms_dir.glob("*.md"):
            try:
                with p.open(encoding="utf-8", errors="replace") as fh:
                    first = fh.readline().strip()
            except OSError:
                continue
            if not first.startswith("#"):
                continue
            name = first.lstrip("#").strip()
            if name and not _PLACEHOLDER_TITLE_RE.match(name):
                titles[p.stem] = name[:120]
    except OSError:
        pass
    known = _load_json(known_path)
    if isinstance(known, dict):
        for k, v in known.items():
            key = str(k)
            if key not in titles and isinstance(v, str) and v.strip():
                titles[key] = v.strip()[:120]
    _ROOM_TITLES_CACHE["value"] = titles
    _ROOM_TITLES_CACHE["stamp"] = stamp
    return titles


def _topic_title(peer: str, topic_id: str) -> str:
    """Имя темы форума из проекции group_context (`topics[<id>].title`)."""
    path = tree() / "memory" / ".state" / "group_context" / f"{peer}.json"
    topics = _load_json(path).get("topics") or {}
    row = topics.get(str(topic_id)) if isinstance(topics, dict) else None
    title = str((row or {}).get("title") or "").strip()
    return "" if _PLACEHOLDER_TITLE_RE.match(title) or title.startswith("topic") and title[5:].strip(": #").isdigit() else title[:120]


def _last_foreign_sender(peer: str) -> str:
    """Личка без профиля и без хода в хвосте: имя собеседника — из архива переписки."""
    root = tree() / "memory" / ".state" / "group_context"
    data = _load_json(root / f"{peer}.json")
    rel = str(data.get("archive") or "").replace("\\", "/").lstrip("/")
    if not rel.startswith("memory/groups/") or ".." in rel.split("/"):
        return ""
    for row in reversed(tail_jsonl(tree() / rel, 60)):
        if row.get("kind") == "message" and not row.get("outgoing"):
            name = str(row.get("sender_name") or "").strip()
            if name:
                return name[:120]
    return ""


def _title_for(chat_id, titles: dict[str, str]) -> str:
    key = str(chat_id or "")
    if not key:
        return ""
    if key == "pult" or rooms.is_room(key):
        # Комната окна — не Telegram, чужого имени у неё нет: имя из реестра
        # комнат, у комнаты по умолчанию — имя агента.
        return rooms.title(tree(), "window" if key == "pult" else key,
                           product_config().get("agent_name") or "")
    if key in titles:
        return titles[key]
    base, _, topic = key.partition("__topic__")
    stable = _room_titles()
    name = titles.get(base) or stable.get(base) or ""
    if not name and not base.startswith("-"):
        name = _last_foreign_sender(base)
    if topic and name:
        sub = _topic_title(base, topic)
        return f"{name} · {sub}" if sub else name
    return name


def _inline_preview(result: dict) -> dict:
    ref = result if isinstance(result, dict) else {}
    inline = ref.get("inline") or {}
    return {
        "head": str(inline.get("head") or "")[:4000],
        "tail": str(inline.get("tail") or "")[:1000],
        "truncated": bool(inline.get("truncated")),
        "result_id": str(ref.get("result_id") or ""),
        "size": ref.get("size"),
        "line_count": ref.get("line_count"),
        "path": ref.get("path"),
        "media_type": ref.get("media_type"),
    }


def _model_text(result: dict) -> str:
    """Видимый текст её реплики из model_output, если инлайн-голова цельная."""
    head = ((result or {}).get("inline") or {}).get("head") or ""
    truncated = bool(((result or {}).get("inline") or {}).get("truncated"))
    if truncated:
        return ""
    try:
        data = json.loads(head)
    except ValueError:
        return ""
    text = str(data.get("text") or "")
    for block in data.get("blocks") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            extra = str(block.get("text") or "")
            if extra and extra not in text:
                text = (text + "\n" + extra).strip()
    return text


_RESULT_ID_RE = re.compile(r"^result-\d{4,8}$")
RESULT_READ_MAX = 2_000_000


def _result_file(path: Path, ref: dict) -> Path | None:
    """Файл результата строго внутри каталога прогона: чужие пути не читаются."""
    rel = str(ref.get("path") or "")
    if not rel or rel.startswith(("/", "\\")) or ".." in rel.replace("\\", "/").split("/"):
        return None
    target = (path / rel)
    try:
        target.resolve().relative_to(path.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


def _model_text_from_file(path: Path, ref: dict, *, max_bytes: int = 512_000) -> str | None:
    """Текст слова из файла вывода модели. None — файл не прочитался или слишком велик;
    "" — прочитан, но слова в нём нет (только вызовы рук)."""
    target = _result_file(path, ref)
    if target is None:
        return None
    try:
        with target.open("rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError:
        return None
    if len(raw) > max_bytes:
        return None
    return _model_text({"inline": {"head": raw.decode("utf-8", errors="replace"), "truncated": False}})


def run_result(run_id: str, result_id: str, *, max_bytes: int = RESULT_READ_MAX) -> dict:
    """Сохранённый результат прогона целиком: то, что inline-превью (2000 знаков) обрезает.

    Только чтение; путь берётся из расписки события и проверяется на принадлежность
    каталогу прогона. Для вывода модели рядом отдаётся извлечённый текст (`model_text`)."""
    path = run_dir(run_id)
    if path is None or not _RESULT_ID_RE.match(str(result_id or "")):
        return {}
    ref: dict | None = None
    row_name = ""
    for line in tail_lines(path / "events.jsonl", 4000, max_bytes=16_000_000):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        candidate = row.get("result") if isinstance(row, dict) else None
        if isinstance(candidate, dict) and str(candidate.get("result_id") or "") == result_id:
            ref = candidate
            row_name = str(row.get("name") or "")  # имя результата лежит на событии, не в ссылке
            break
    if ref is None:
        return {}
    target = _result_file(path, ref)
    if target is None:
        return {}
    try:
        with target.open("rb") as fh:
            raw = fh.read(max_bytes)
    except OSError:
        return {}
    text = raw.decode("utf-8", errors="replace")
    size = int(ref.get("size") or len(raw))
    out = {"run_id": run_id, "result_id": result_id, "name": ref.get("name"),
           "media_type": ref.get("media_type"), "size": size, "complete": len(raw) >= size,
           "text": text}
    if row_name == "model-output" or str(ref.get("name") or "") == "model-output":
        out["model_text"] = _model_text({"inline": {"head": text, "truncated": False}})
    return out


def frame_cuts(days: int = 7) -> dict:
    """Разрезы кэша/времени по группам действий за N дней — экран «Система»."""
    from . import frame_cuts as _cuts
    return _cuts.cuts(tree(), days=max(1, min(int(days or 7), 90)))


def _run_origin(path: Path, manifest: dict) -> dict:
    """Only the first authority block supplies the trigger, never conversation head.

    v1/v2 snapshots share this JSON envelope. Decode JSON rather than searching
    for closing fences inside potentially quoted user text. This is display data,
    not an authority decision. Unsupported/incomplete envelopes remain unknown.
    """
    unknown = {"text": "", "source": "unknown"}
    try:
        with (path / "context.md").open(encoding="utf-8") as fh:
            head = fh.read(256_000)
        match = re.match(r'\A# Immutable run context\r?\n(?:<!--[^\n]*-->\r?\n)?\s*## Authority and address\s*\n\s*```json\s*\n', head)
        if not match:
            return unknown
        authority, end = json.JSONDecoder().raw_decode(head[match.end():])
        if not head[match.end() + end:].lstrip().startswith("```") or not isinstance(authority, dict):
            return unknown
        context = manifest.get("context") or {}
        if authority.get("schema") not in ("praxis.run.authority.v1", "praxis.run.authority.v2"):
            return unknown
        if str(authority.get("origin_chat_id")) != str(context.get("origin_chat_id")):
            return unknown
        expected = [str(v) for v in context.get("origin_message_ids") or []]
        actual = [str(v) for v in authority.get("origin_message_ids") or []]
        if expected != actual:
            return unknown
        text = authority.get("origin_text")
        return {"text": text, "source": "snapshot"} if isinstance(text, str) and text.strip() else unknown
    except (OSError, ValueError, TypeError):
        return unknown


_RUN_WORDS_CACHE: dict[str, Any] = {"stamp": None, "value": {}}


def _run_words(run_id: str) -> dict:
    path = tree() / "memory" / ".state" / "turns.jsonl"
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    if stamp != _RUN_WORDS_CACHE["stamp"]:
        _RUN_WORDS_CACHE.update(stamp=stamp, value={
            str(r["run_id"]): {k: str(r.get(k) or "") for k in ("in", "out", "note", "who")}
            for r in tail_jsonl(path, 4000) if r.get("run_id")
        })
    return _RUN_WORDS_CACHE["value"].get(run_id, {})


def run_detail(run_id: str, *, max_events: int = 4000) -> dict:
    path = run_dir(run_id)
    if path is None:
        return {}
    manifest = _load_json(path / "manifest.json")
    iterations: list[dict] = []
    current: dict | None = None
    tools_by_call: dict[str, dict] = {}
    events_path = path / "events.jsonl"
    # Хвостом, а не через весь файл: правило записано в шапке этого же модуля,
    # а здесь оно нарушалось. Окно перечитывает идущий прогон каждые ~1.5 с;
    # на events.jsonl в 59 МБ это было 2.4 с и 148 МБ пика памяти на запрос.
    rows: list[dict] = []
    for line in tail_lines(events_path, max_events, max_bytes=16_000_000):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    status_flow: list[dict] = []
    for row in rows:
        kind = row.get("kind")
        if kind == "model_started":
            current = {"at": row.get("at"), "seq": row.get("seq"),
                       "call_id": row.get("call_id"), "tools": [], "text": "",
                       "status": "running"}
            iterations.append(current)
        elif kind == "model_output":
            if current is not None:
                ref = row.get("result") or {}
                text = _model_text(ref)
                if not text and bool((ref.get("inline") or {}).get("truncated")):
                    # Слово длиннее inline-головы (2000 знаков) раньше пропадало из карточки
                    # целиком. Файл вывода модели невелик — читаем его здесь; ссылка окну
                    # остаётся только если файл не прочитался (велик/недоступен). Обрубленный
                    # вывод без слова (одни tool_use) — не «длинный ответ», а его отсутствие.
                    extracted = _model_text_from_file(path, ref)
                    if extracted is None:
                        current["text_ref"] = str(ref.get("result_id") or "")
                        current["text_truncated"] = True
                    else:
                        text = extracted
                if text:
                    current["text"] = text
        elif kind == "model_completed":
            if current is None or current.get("call_id") != row.get("call_id"):
                current = {"at": row.get("at"), "seq": row.get("seq"),
                           "call_id": row.get("call_id"), "tools": [], "text": ""}
                iterations.append(current)
            current.update({
                "status": "completed",
                "model": row.get("model"), "role": row.get("role"),
                "ms": row.get("duration_ms"), "stop": row.get("stop_reason"),
                "usage": row.get("usage") or {},
                "tool_calls": row.get("tool_calls"),
                "text_chars": row.get("text_chars"),
            })
        elif kind == "model_failed":
            if current is None or current.get("call_id") != row.get("call_id"):
                current = {"at": row.get("at"), "seq": row.get("seq"),
                           "call_id": row.get("call_id"), "tools": [], "text": ""}
                iterations.append(current)
            current.update({"status": "failed", "error": row.get("error"),
                            "ms": row.get("duration_ms")})
        elif kind == "tool_started":
            card = {"tool": row.get("tool"), "args": row.get("args"),
                    "at": row.get("at"), "seq": row.get("seq"),
                    "side_effect": bool(row.get("side_effect")),
                    "call_id": row.get("call_id"), "result": None,
                    "status": "running"}
            tools_by_call[str(row.get("call_id") or row.get("seq"))] = card
            if current is None:
                current = {"at": row.get("at"), "seq": row.get("seq"),
                           "call_id": None, "tools": [], "text": ""}
                iterations.append(current)
            current["tools"].append(card)
        elif kind == "tool_result":
            card = tools_by_call.get(str(row.get("call_id") or ""))
            preview = _inline_preview(row.get("result") or {})
            if card is not None:
                card["result"] = preview
                card["status"] = "received"
                card["finished_at"] = row.get("at")
            else:  # результат без старта — покажем как есть
                if current is None:
                    current = {"at": row.get("at"), "seq": row.get("seq"),
                               "call_id": None, "tools": [], "text": ""}
                    iterations.append(current)
                card = {"tool": row.get("name"), "args": None,
                        "at": row.get("at"), "seq": row.get("seq"),
                        "call_id": row.get("call_id"),
                        "status": "received", "result": preview}
                current["tools"].append(card)
                tools_by_call[str(row.get("call_id") or row.get("seq"))] = card
        elif kind in ("tool_failed", "tool_completed", "tool_reconciled"):
            card = tools_by_call.get(str(row.get("call_id") or ""))
            if card is None:
                if current is None:
                    current = {"at": row.get("at"), "seq": row.get("seq"),
                               "call_id": None, "tools": [], "text": ""}
                    iterations.append(current)
                card = {"tool": row.get("tool") or row.get("name"),
                        "call_id": row.get("call_id"), "seq": row.get("seq"),
                        "args": None, "result": None}
                current["tools"].append(card)
                tools_by_call[str(row.get("call_id") or row.get("seq"))] = card
            # Receipt completion does not certify the external action's success.
            card["status"] = "failed" if kind == "tool_failed" else "received"
            card["error"] = row.get("error") or row.get("reason")
            card["finished_at"] = row.get("at")
        elif kind == "status_changed":
            status_flow.append({"at": row.get("at"), "from": row.get("from_status"),
                                "to": row.get("to_status")})
    recap = ""
    try:
        recap = (path / "RECAP.md").read_text(encoding="utf-8", errors="replace")[:120_000]
    except OSError:
        pass
    context = manifest.get("context") or {}
    words = _run_words(run_id)
    origin = _run_origin(path, manifest)
    if origin["source"] == "unknown" and words.get("in"):
        origin = {"text": words["in"], "source": "turn_log"}
    if context.get("kind") != "chat_turn" and origin["source"] == "unknown":
        origin = {"text": str(context.get("goal") or ""), "source": "task_goal"}
    return {
        "id": run_id,
        "origin": origin,
        "outcome": {"text": words.get("out", ""), "note": words.get("note", "")},
        "manifest": {
            "created_at": manifest.get("created_at"),
            "status": manifest.get("status"),
            "terminal": manifest.get("terminal") or {},
            "event_seq": manifest.get("event_seq"),
            "kind": context.get("kind"),
            "goal": context.get("goal"),
            "model_profile": context.get("model_profile"),
            "chat_id": context.get("delivery_chat_id") or context.get("origin_chat_id"),
        },
        "iterations": iterations,
        "status_flow": status_flow,
        "recap": recap,
    }


# ------------------------------------------------------------------ пульс/ошибки

def pulse(n: int = 300) -> dict:
    import time as _time
    rows = tail_jsonl(tree() / "memory" / ".state" / "llm_calls.jsonl", n)
    last = rows[-1] if rows else {}
    by_run: dict[str, dict] = {}
    for row in rows:
        run_id = str(row.get("run") or "")
        if not run_id:
            continue
        agg = by_run.setdefault(run_id, {"calls": 0, "in": 0, "cached": 0, "out": 0,
                                         "err": 0, "last_ts": 0.0})
        agg["calls"] += 1
        agg["in"] += _whole(row.get("in"))
        agg["cached"] += _whole(row.get("cached"))
        agg["out"] += _whole(row.get("out"))
        agg["err"] += 1 if row.get("err") else 0
        agg["last_ts"] = max(agg["last_ts"], _num(row.get("ts")))
    # Кэш двумя честными числами (просьба владельца): среднесуточный — скользящие
    # сутки назад от СЕЙЧАС; текущий — последние 15 минут (или последний вызов).
    now = _time.time()
    day_rows = [r for r in tail_jsonl(
        tree() / "memory" / ".state" / "llm_calls.jsonl", 20_000)
        if _num(r.get("ts")) >= now - 86_400]
    def _share(rows_):
        fresh = sum(_whole(r.get("in")) for r in rows_)
        cached = sum(_whole(r.get("cached")) for r in rows_)
        total = fresh + cached
        return round(100 * cached / total) if total else None
    recent = [r for r in day_rows if _num(r.get("ts")) >= now - 900]
    hours = round((now - _num(day_rows[0].get("ts"), now)) / 3600) if day_rows else 0
    return {"last": last, "by_run": by_run, "rows": rows[-40:],
            "cache_day": _share(day_rows), "cache_day_hours": hours,
            "cache_now": _share(recent) if recent else _share(rows[-1:] if rows else []),
            "calls_day": len(day_rows)}


def errors(n: int = 2000) -> dict:
    llm_rows = tail_jsonl(tree() / "memory" / ".state" / "llm_calls.jsonl", n)
    failed = [row for row in llm_rows if row.get("err")][-120:]
    skips = tail_jsonl(tree() / "memory" / ".state" / "perception_skips.jsonl", 250)
    return {"llm": failed, "skips": skips}


# ------------------------------------------------------------------ доска

def board() -> dict:
    base = tree() / "memory" / "work"
    text = ""
    try:
        text = (base / "BOARD.md").read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        pass
    tasks: list[dict] = []
    tasks_dir = base / "tasks"
    if tasks_dir.is_dir():
        try:
            dirs = sorted(tasks_dir.iterdir(), key=lambda p: p.stat().st_mtime,
                          reverse=True)
        except OSError:
            dirs = []
        for entry in dirs[:40]:
            if not entry.is_dir():
                continue
            head = ""
            try:
                with (entry / "TASK.md").open(encoding="utf-8", errors="replace") as fh:
                    head = fh.readline().strip()[:200]
            except OSError:
                pass
            tasks.append({"id": entry.name, "head": head})
    return {"board": text, "tasks": tasks}


def agenda() -> dict:
    """memory/tasks.json — её намеченное: будильники, окна, отложенные доставки."""
    try:
        items = json.loads((tree() / "memory" / "tasks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"active": [], "total": 0}
    if not isinstance(items, list):
        return {"active": [], "total": 0}
    active = [row for row in items if isinstance(row, dict)
              and str(row.get("status") or "") not in
              {"done", "cancelled", "canceled", "failed", "expired", "delivered"}]
    active.sort(key=lambda r: str(r.get("created") or ""), reverse=True)
    fields = ("id", "kind", "goal", "target", "when", "recur", "status", "created")
    return {"active": [{k: row.get(k) for k in fields} for row in active[:120]],
            "total": len(items)}


def _taskmd_frontmatter(path: Path) -> dict:
    """Скалярные строки YAML-фронтматтера TASK.md, без полного YAML-парсера."""
    out: dict[str, Any] = {}
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
            if first.strip() != "---":
                return out
            for _ in range(200):
                line = fh.readline()
                if not line or line.strip() == "---":
                    break
                match = re.match(r'^([a-z_]+):\s*(".*"|\S.*)$', line.rstrip("\n"))
                if not match:
                    continue
                key, raw = match.group(1), match.group(2)
                if raw.startswith('"') and raw.endswith('"'):
                    try:
                        out[key] = json.loads(raw)
                        continue
                    except ValueError:
                        pass
                out[key] = raw
    except OSError:
        pass
    return out


def forge_tasks(limit: int = 30) -> list[dict]:
    """Форж-дети: memory/work/tasks/<задача>/agents/<юнит>/ request+result."""
    root = tree() / "memory" / "work" / "tasks"
    if not root.is_dir():
        return []
    try:
        dirs = sorted((p for p in root.iterdir() if p.is_dir()),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    out: list[dict] = []
    for entry in dirs[:limit]:
        task = _load_json(entry / "task.json")
        agents = []
        agents_dir = entry / "agents"
        if agents_dir.is_dir():
            try:
                agent_dirs = sorted(agents_dir.iterdir())
            except OSError:
                agent_dirs = []
            for unit in agent_dirs:
                request = _load_json(unit / "request.json")
                result = _load_json(unit / "result.json")
                agents.append({
                    "id": unit.name,
                    "role": request.get("role") or result.get("role"),
                    "status": result.get("status") or request.get("status") or "",
                    "created": request.get("created"),
                    "finished": result.get("finished"),
                    "error": str(result.get("error") or "")[:200],
                    "result_head": str(result.get("result") or "")[:300],
                })
        goal = str(task.get("goal") or "")[:300]
        status = task.get("status")
        if not goal:
            # Её рабочие задачи держат TASK.md с YAML-фронтматтером, а не task.json.
            meta = _taskmd_frontmatter(entry / "TASK.md")
            goal = str(meta.get("goal") or "")[:300]
            status = status or ("closed" if meta.get("closed_at") else
                                (meta.get("kind") or "task"))
        out.append({
            "id": entry.name,
            "goal": goal,
            "status": status,
            "priority": task.get("priority"),
            "agents": agents,
        })
    return out


# ------------------------------------------------------------------ тень (КАДР)

def shadow_root() -> Path:
    return tree() / "memory" / ".state" / "shadow"


def shadow_streams() -> list[dict]:
    root = shadow_root()
    if not root.is_dir():
        return []
    out = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name == "report":
            continue
        try:
            captures = sorted(p.name for p in entry.iterdir() if p.suffix == ".md")
        except OSError:
            captures = []
        if not captures:
            continue
        out.append({"stream": entry.name, "captures": len(captures),
                    "latest": captures[-1]})
    return out


_CAPTURE_RE = re.compile(r"^[0-9TZ-]+-[0-9a-f]{8}\.md$")


def shadow_captures(stream: str, limit: int = 30) -> list[str]:
    entry = shadow_root() / os.path.basename(stream)
    if not entry.is_dir():
        return []
    names = sorted((p.name for p in entry.iterdir()
                    if p.suffix == ".md" and _CAPTURE_RE.match(p.name)), reverse=True)
    return names[:limit]


def shadow_capture(stream: str, name: str) -> str:
    if not _CAPTURE_RE.match(name or ""):
        return ""
    path = shadow_root() / os.path.basename(stream) / name
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:600_000]
    except OSError:
        return ""


def _sections(text: str) -> list[tuple[str, str]]:
    """Разрез захвата по верхним заголовкам — зоны кадра."""
    parts: list[tuple[str, list[str]]] = []
    title = "(преамбула)"
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("# "):
            if buf:
                parts.append((title, buf))
            title, buf = line[2:].strip(), []
        else:
            buf.append(line)
    parts.append((title, buf))
    return [(t, "\n".join(b)) for t, b in parts]


def shadow_diff(stream: str, old: str, new: str) -> dict:
    """Дифф двух захватов: где порвался префикс — по зонам и первым байтом."""
    old_text = shadow_capture(stream, old)
    new_text = shadow_capture(stream, new)
    if not old_text or not new_text:
        return {}
    prefix = 0
    for a, b in zip(old_text, new_text):
        if a != b:
            break
        prefix += 1
    zones = []
    old_secs = dict(_sections(old_text))
    new_secs = _sections(new_text)
    for title, body in new_secs:
        was = old_secs.get(title)
        if was is None:
            zones.append({"zone": title, "state": "new", "chars": len(body)})
        elif was == body:
            zones.append({"zone": title, "state": "same", "chars": len(body)})
        else:
            zone_prefix = 0
            for a, b in zip(was, body):
                if a != b:
                    break
                zone_prefix += 1
            zones.append({"zone": title, "state": "changed", "chars": len(body),
                          "was_chars": len(was), "prefix": zone_prefix})
    gone = [t for t in old_secs if t not in dict(new_secs)]
    diff_lines = list(difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(),
        fromfile=old, tofile=new, lineterm="", n=2))[:800]
    return {"prefix_bytes": prefix, "old_len": len(old_text), "new_len": len(new_text),
            "zones": zones, "gone": gone, "diff": diff_lines}


def shadow_metrics(n: int = 120, stream: str = "") -> list[dict]:
    """Метрики тени. Лежат ПО ПОТОКАМ: frame_shadow пишет в
    `<корень>/<поток>/metrics.jsonl`, а читалось из `<корень>/metrics.jsonl`,
    куда не пишет никто, — кнопка «Метрики» на экране «Контекст» была мертва
    всегда, на любой машине. Без имени потока берём поток со свежими метриками,
    чтобы кнопка ожила и у старого окна, которое имя не передаёт.
    """
    root = shadow_root()
    name = os.path.basename(str(stream or "").strip())
    if not name:
        best, best_mtime = "", -1.0
        try:
            for entry in root.iterdir():
                if not entry.is_dir() or entry.name == "report":
                    continue
                try:
                    mtime = (entry / "metrics.jsonl").stat().st_mtime
                except OSError:
                    continue
                if mtime > best_mtime:
                    best, best_mtime = entry.name, mtime
        except OSError:
            pass
        name = best
    if not name:
        return tail_jsonl(root / "metrics.jsonl", n)
    return tail_jsonl(root / name / "metrics.jsonl", n)


# ------------------------------------------------------------------ переписки

def chats() -> list[dict]:
    """Комнаты из реестра group_context: по свежему состоянию на пир.

    `kind` — "window" (комната окна: `window` или `window-<hex>`, см.
    deskd/rooms.py) или "telegram"; `title` у комнат окна — из реестра комнат
    (по умолчанию — имя агента). Комната окна, заведённая, но ещё без единого
    сообщения, тоже здесь: `rooms.create` кладёт ей запись в group_context.
    """
    root = tree() / "memory" / ".state" / "group_context"
    if not root.is_dir():
        return []
    titles = chat_titles()
    best: dict[str, dict] = {}
    for path in root.glob("*.json"):
        name = path.name
        if name.endswith(".route.json") or name.endswith(".backfill.json"):
            continue
        data = _load_json(path)
        peer = str(data.get("peer_id") or "")
        if not peer or not data.get("archive"):
            continue
        row = {
            "peer_id": peer,
            "title": _title_for(peer, titles),
            "kind": "window" if rooms.is_room(peer) else "telegram",
            "messages": data.get("message_count"),
            "participants": data.get("participant_count"),
            "topics": data.get("topic_count"),
            "archive": str(data.get("archive")),
            # Тот же класс, что девять голых float() ниже: строка пишется
            # деревом, кривое значение уронило бы весь экран «Чаты» 500-м.
            "mtime_ns": _whole(data.get("archive_mtime_ns")),
            "size": data.get("archive_size"),
        }
        if peer not in best or row["mtime_ns"] > best[peer]["mtime_ns"]:
            best[peer] = row
    return sorted(best.values(), key=lambda r: -r["mtime_ns"])


def chat_tail(peer_id: str, n: int = 200) -> list[dict]:
    for row in chats():
        if row["peer_id"] == str(peer_id):
            rel = row["archive"].replace("\\", "/").lstrip("/")
            if ".." in rel.split("/") or not rel.startswith("memory/groups/"):
                return []
            return tail_jsonl(tree() / rel, n)
    return []


#: Что окно вправе проиграть и показать. Список закрытый — по РАСШИРЕНИЮ, а не
#: по угадыванию содержимого: канал отдаёт байты, и «что это на самом деле»
#: решает уже браузер.
MEDIA_TYPES = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".m4a": "audio/mp4",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

#: Откуда разрешено отдавать. Не «всё дерево»: в дереве лежат её память,
#: конституция и `helene.json` соседей — там нечего проигрывать.
MEDIA_ROOTS = ("media", "memory/.control/desk_inbox/attachments")


def media_file(rel: str) -> tuple[pathlib.Path | None, str, str]:
    """Путь к вложению по строке из ленты. -> (файл|None, тип, причина отказа).

    ⚠ Три проверки, и ни одна не лишняя. Путь берётся из строки ленты, а ленту
    пишет агент — то есть это ВХОДЯЩАЯ строка, а не наша константа:

      1. никаких `..` и абсолютных путей — иначе `../../helene.json` уехал бы
         владельцу вместе с ключом модели по первой же просьбе;
      2. только из разрешённых корней дерева (`MEDIA_ROOTS`);
      3. только известные расширения — канал не должен раздавать `.py` и `.jsonl`.

    Сверх этого путь РАЗРЕШАЕТСЯ и сверяется с корнем ещё раз: символическая
    ссылка внутри дерева обошла бы проверку строки, но не проверку результата.
    """
    said = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if not said or ".." in said.split("/"):
        return None, "", "путь не годится"
    root = tree().resolve()
    if not any(said == top or said.startswith(top + "/") for top in MEDIA_ROOTS):
        return None, "", "этот путь окно не отдаёт: вложения живут в " + ", ".join(MEDIA_ROOTS)
    path = (root / said)
    suffix = path.suffix.lower()
    if suffix not in MEDIA_TYPES:
        return None, "", f"расширение {suffix or '(нет)'} канал не отдаёт"
    try:
        real = path.resolve()
        real.relative_to(root)
    except (OSError, ValueError):
        return None, "", "файл вне дерева"
    if not real.is_file():
        return None, "", "файла нет"
    return real, MEDIA_TYPES[suffix], ""


def chat_turns(peer_id: str, n: int = 120) -> list[dict]:
    """Ходы одной комнаты из turns.jsonl — подписи для правой панели.

    `in`/`out` здесь — то, на что она отвечала и что сказала В ЭТОМ ходе; goal
    прогона для подписи не годится: у чат-хода это ВЕСЬ разговор, и первая
    строка у всех ходов комнаты одинаковая (панель подписывала всё «/start»).
    """
    key = str(peer_id)
    out = []
    for row in tail_jsonl(tree() / "memory" / ".state" / "turns.jsonl", 4000):
        if str(row.get("chat_id")) != key or not row.get("run_id"):
            continue
        out.append({"run_id": row.get("run_id"), "ts": row.get("ts"),
                    "kind": row.get("kind"), "who": row.get("who"),
                    "in": str(row.get("in") or ""),
                    "out": str(row.get("out") or ""),
                    "note": str(row.get("note") or ""),
                    "delivery": row.get("delivery")})
    return out[-max(1, int(n)):]


# ------------------------------------------------------------------ устройство

def anatomy() -> dict:
    """Снимок устройства для вкладки «Устройство»: его пишет руннер на старте
    из ЖИВОГО списка рук (offered_tools_for). Пусто = руннер ещё не поднимался."""
    return _load_json(tree() / "memory" / ".state" / "anatomy.json")


# ------------------------------------------------------------------ сторож тишины

def health() -> dict:
    """Сторож тишины — с рубежом: его отказ не должен гасить шапку окна.

    Обе эти ручки окно и телефон опрашивают каждые несколько секунд, и обе
    читают файлы, которые пишет НЕ продукт. Любая неожиданность внутри выходила
    наружу 500-м (`api_health` обёртки не имела вовсе) — и на телефоне это голый
    отказ, а в окне «Не прочиталось» на каждом экране. Отказ прибора — не повод
    объявлять мёртвым весь продукт: отдаём пустой, но ЧЕСТНЫЙ ответ и говорим о
    поломке вслух — тревогой в шапке и трассировкой в `helene.log`.
    """
    import time as _time
    try:
        return _health_impl()
    except Exception:
        log.exception("сторож тишины не собрался")
        return {"alarms": [{"kind": "reader_failed",
                            "text": "сторож тишины не прочитался — "
                                    "подробности в helene.log"}],
                "checked_at": _time.time(), "last_call_min_ago": None,
                "restarts_20m": 0, "undelivered": 0, "skips_5m": 0}


def _health_impl() -> dict:
    """Тревоги «она молчит/зациклена, хотя не должна» — мета-класс всех глухот.

    Каждый из четырёх инцидентов глухоты (27.08 end_turn, 28.08 пустая очередь,
    28.08 битый байт, 29.08 петля рестартов) замечал ЧЕЛОВЕК по ощущению тишины.
    Этот прибор делает отсутствие видимым. Только чтение, только её артефакты.
    """
    import time as _time
    now = _time.time()
    alarms: list[dict] = []
    state = tree() / "memory" / ".state"

    # 1. Петля рестартов: строки [restart] в дневнике за последние 20 минут.
    #
    # Было структурно неработоспособно, и это единственный прибор против
    # инцидента 29.08: (а) имя файла бралось по дате UTC, а дерево пишет дневник
    # по СВОЕЙ дате (Europe/Samara по умолчанию) — с 20:00 UTC читался вчерашний
    # файл; (б) «HH:MM» из строки трактовалось как UTC, а agent._now() пишет
    # ЛОКАЛЬНЫЕ часы машины — на UTC+4 отметка всегда оказывалась на 4 часа в
    # будущем и условие `0 <= now - stamp` не выполнялось НИКОГДА.
    # Теперь: три соседних дня по именам файлов (день дерева может отличаться от
    # локального на ±1), и HH:MM каждой строки привязывается к дате ЕЁ файла —
    # поэтому одна строка не может попасть в счёт дважды.
    import datetime as _dt
    today = _dt.datetime.now()
    recent_restarts = 0
    journal = tree() / "memory" / "journal"
    for shift in (-1, 0, 1):
        day = today + _dt.timedelta(days=shift)
        path = journal / f"{day:%Y-%m-%d}.md"
        try:    # файл, который никто не трогал час, не может нести свежий рестарт
            if now - path.stat().st_mtime > 3600:
                continue
        except OSError:
            continue
        for line in tail_lines(path, 300):
            if "[restart]" not in line and "перезапускаюсь" not in line:
                continue
            try:  # «- 02:36 (s3) [restart] …» — часы:минуты её локальных часов
                hh, mm = line.split("- ", 1)[1].split(" ", 1)[0].split(":")
                stamp = day.replace(hour=int(hh), minute=int(mm), second=0,
                                    microsecond=0)
            except (ValueError, IndexError):
                continue
            if 0 <= (now - stamp.timestamp()) < 1200:
                recent_restarts += 1
    if recent_restarts >= 3:
        alarms.append({"kind": "restart_loop",
                       "text": f"петля рестартов: {recent_restarts} за 20 минут"})

    # 2. Молчание при долге: вызовов модели давно нет, а недоставленные события есть.
    llm_rows = tail_jsonl(state / "llm_calls.jsonl", 5)
    last_call = max((_num(r.get("ts")) for r in llm_rows), default=0.0)
    quiet_min = (now - last_call) / 60 if last_call else None
    undelivered = 0
    try:
        delivered = set()
        raw = _load_json(state / "core_events_delivered.json")
        delivered = set((raw.get("delivered") or raw or {}).keys())
        for row in tail_jsonl(state / "core_events.jsonl", 400):
            key = str(row.get("dedup_key") or row.get("id") or "")
            if key and key not in delivered:
                undelivered += 1
    except Exception:
        pass
    if quiet_min is not None and quiet_min > 20 and undelivered > 0:
        alarms.append({"kind": "deaf_with_debt",
                       "text": (f"тишина {quiet_min:.0f} мин при {undelivered} "
                                "недоставленных событиях")})
    elif quiet_min is not None and quiet_min > 90:
        alarms.append({"kind": "long_silence",
                       "text": f"ни одного вызова модели {quiet_min:.0f} мин"})

    # 3. Шторм откладываний: perception_skips растёт лавиной (defer-петля 20 Гц).
    skips = tail_jsonl(state / "perception_skips.jsonl", 400)
    recent_skips = sum(1 for r in skips if now - _num(r.get("ts")) < 300)
    if recent_skips >= 150:
        alarms.append({"kind": "defer_storm",
                       "text": f"{recent_skips} откладываний за 5 минут — похоже на defer-петлю"})

    return {"alarms": alarms, "checked_at": now,
            "last_call_min_ago": round(quiet_min, 1) if quiet_min is not None else None,
            "restarts_20m": recent_restarts, "undelivered": undelivered,
            "skips_5m": recent_skips}


# ------------------------------------------------------------------ маркдауны

_MD_GROUPS = (
    ("Заметки", "memory/notes"),
    ("Дневник", "memory/journal"),
    ("Workspace", "workspace"),
    ("Inbox", "workspace/inbox"),
    ("Soul", "soul"),
    ("Работа", "memory/work"),
    ("Желания", "memory/desires"),
)


_MD_SCAN_CAP = 20_000


def md_tree() -> list[dict]:
    """Её маркдауны по корням: имя, размер, свежесть. Без рекурсии в runs.

    Обход идёт ДО КОНЦА, и только потом сортировка по свежести. Было наоборот:
    обрыв на 400-м файле, а rglob идёт в порядке файловой системы (алфавит) —
    из 1001 заметки окно показывало произвольную СТАРУЮ полосу из середины
    алфавита, свежей заметки в списке не было вовсе, а счётчик печатал «200».
    Теперь в группе есть `total` — настоящее число файлов, чтобы окно не выдавало
    длину среза за размер памяти агента.
    """
    out: list[dict] = []
    base = tree()
    for label, rel in _MD_GROUPS:
        root = base / rel
        if not root.is_dir():
            continue
        files: list[dict] = []
        try:
            for path in root.rglob("*.md"):
                relative = _posix_rel(path, base)
                if relative is None or "/runs/" in relative or "/.state/" in relative:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append({"path": relative, "name": relative[len(rel) + 1:],
                              "size": stat.st_size, "mtime": stat.st_mtime,
                              "readonly": md_readonly(relative)})
                if len(files) >= _MD_SCAN_CAP:
                    break     # предохранитель от патологического дерева
        except OSError:
            continue
        files.sort(key=lambda f: -f["mtime"])
        if files:
            out.append({"group": label, "root": rel, "total": len(files),
                        "files": files[:200]})
    return out


def _posix_rel(path: Path, base: Path) -> str | None:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def md_readonly(clean: str) -> bool:
    """Файлы, которые окно ПОКАЗЫВАЕТ, но править не должно.

    soul/self/** — провенансная самомодель: live/self_model.py сверяет sha256
    файла истории с распиской, и любая правка снаружи уводит источник в
    fail-closed (source=missing) НАВСЕГДА — отката по построению нет, блок
    «кто я сейчас» исчезает из кадра, а тень пишет ложный диагноз.
    memory/work/**/TASK.md — YAML-фронтматтер, который разбирают и дерево, и
    окно; правка руками ломает разбор молча.
    """
    key = _md_key(clean)
    if key == "soul/self" or key.startswith("soul/self/"):
        return True
    if key.startswith("memory/work/") and key.endswith("/task.md"):
        return True
    return False


def _md_key(clean: str) -> str:
    """Путь в том виде, в каком его видит файловая система Windows.

    NTFS не различает регистр и молча отбрасывает точки и пробелы в конце
    сегмента: `soul/Self/CURRENT.md`, `soul/self./CURRENT.md` и
    `soul/self /CURRENT.md` — ТОТ ЖЕ файл, что `soul/self/CURRENT.md`. Голый
    `startswith` этого не знает, и запрет обходился бы так же тривиально, как
    через двойной слэш (его схлопывает `_md_path`). Сверку групп это не
    касается: там несовпадение регистра даёт отказ, а отказ безопасен.
    """
    return "/".join(seg.rstrip(". ").lower() for seg in clean.split("/"))


def _md_in_groups(clean: str) -> bool:
    return any(clean == root or clean.startswith(root + "/")
               for _, root in _MD_GROUPS)


def _md_path(rel: str) -> tuple[str, Path] | dict:
    """Общая проверка пути для чтения и записи. Ошибка -> dict с code.

    ⚠ Путь приводится к каноническому виду ДО всех проверок, иначе запрет
    `md_readonly` обходится одним лишним слэшем. Было: `replace("\\\\","/")` и
    `strip("/")` — повторные слэши и сегменты «.» оставались как есть. Тогда
    `soul//self/CURRENT.md` не начинается с `soul/self/` (readonly не видит
    самомодель -> False), `_md_in_groups` пускает (корень группы — сам `soul`),
    а файловая система схлопывает `//` и отдаёт ТУ ЖЕ защищённую цель. Правка
    самомодели снаружи уводит `live/self_model.py` в fail-closed навсегда —
    отката по построению нет. Проверено живой трубой (адверсарий №3):
    `soul//self/CURRENT.md` и `soul/./self/history/0003.md` -> 200, файл
    перезаписан. Порядок важен: «..» ищется по ИСХОДНЫМ сегментам, до
    схлопывания, — иначе `a/..//b` мог бы схлопнуться во что-то безобидное.
    """
    parts = str(rel or "").replace("\\", "/").split("/")
    if ".." in parts:
        return {"error": "путь не похож на маркдаун агента", "code": "outside"}
    clean = "/".join(p for p in parts if p and p != ".")
    if not clean:
        return {"error": "путь не похож на маркдаун агента", "code": "outside"}
    if not clean.endswith(".md"):
        # Расширение проверялось только на записи. На чтении не проверялось
        # ВОВСЕ, а корни сверялись голыми префиксами ("memory/"), поэтому через
        # /api/md уходил любой файл под memory/ — в том числе memory/llm.json с
        # ключом модели и memory/rooms/*/messages.jsonl со всей перепиской.
        return {"error": "окно показывает только маркдауны агента",
                "code": "not_markdown"}
    if not _md_in_groups(clean):
        return {"error": "этот файл окно не показывает", "code": "outside"}
    base = tree()
    path = base / clean
    try:
        path.resolve().relative_to(base.resolve())
    except (OSError, ValueError):
        return {"error": "путь выходит из дерева", "code": "outside"}
    return clean, path


def safe_read_md(rel: str) -> dict:
    checked = _md_path(rel)
    if isinstance(checked, dict):
        return checked
    clean, path = checked
    try:
        stat = path.stat()
    except FileNotFoundError:
        # Было: сырой OSError с английским именем класса, кодом WinError и
        # полным абсолютным путём — окно печатало его как есть.
        return {"error": "Этого файла больше нет — агент его убрал.",
                "code": "not_found"}
    except PermissionError:
        return {"error": "Нет доступа к файлу.", "code": "denied"}
    except OSError as exc:
        return {"error": "Файл не открылся.", "code": "io",
                "detail": f"{type(exc).__name__}: {exc}"}
    if stat.st_size > _MD_CAP:
        return {"error": f"Файл слишком большой, чтобы показать целиком "
                         f"({stat.st_size} байт).", "code": "too_big"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": "Файл не прочитался.", "code": "io",
                "detail": f"{type(exc).__name__}: {exc}"}
    # mtime_ns отдаётся наружу, чтобы окно могло вернуть его в POST и получить
    # 409 вместо тихой перезаписи чужой правки.
    #
    # СТРОКОЙ, а не числом, и это не косметика. st_mtime_ns сейчас ~1.79e18 —
    # больше Number.MAX_SAFE_INTEGER (9.0e15) в двести раз, и JSON.parse в окне
    # округляет его до ближайшего представимого double (шаг 256 нс):
    # 1788476136444380500 возвращается как 1788476136444380400. Отпечаток,
    # прошедший через число в JS, НЕ СОВПАДАЕТ сам с собой, то есть сверка ниже
    # давала бы конфликт на каждое сохранение. `safe_write_md` принимает и
    # строку, и число (int(...)), так что старые клиенты не ломаются.
    return {"path": clean, "text": text, "mtime_ns": str(stat.st_mtime_ns),
            "size": stat.st_size, "readonly": md_readonly(clean)}


# ---------------------------------------------------------------------------
#  Единая модель состояния: одна фраза человеку и одно действие рядом.
# ---------------------------------------------------------------------------

_SLEEP_KINDS = {"wake", "window"}

# Единственный порог живости раннера на весь продукт. Раньше их было два:
# state() считала живой квитанцию свежее 45 с (поле `at`), а deskapp._say —
# свежее 48 ЧАСОВ (mtime файла). Из-за расхождения окно писало «Не запущен» и
# тут же принимало сообщение как доставленное mid-turn.
READER_FRESH_S = 45


def _receipt_run(base: Path, receipt: dict, now: float) -> str:
    """Связать занятость локального раннера с созданным внутри ядра прогоном.

    envelope отдаёт id только после завершения. Пока он выполняется, ищем
    единственный running chat_turn в той же комнате, созданный после начала
    занятости. Старые прогоны, другие комнаты и неоднозначность не подходят.
    Читаем только свежие манифесты; журнал, кадры и замки ядра не трогаем.
    """
    import datetime as dt
    since = _num(receipt.get("since"))
    chat_id = str(receipt.get("chat_id") or "")
    if not chat_id or not 0 < since <= now:
        return ""
    start = dt.datetime.fromtimestamp(since, dt.timezone.utc)
    end = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    floor = start.strftime("run-%Y%m%dT%H%M%S")
    root = base / "memory" / "runs"
    found = ""
    try:
        months = sorted((p for p in root.iterdir() if p.is_dir()
                         and start.strftime("%Y-%m") <= p.name <= end.strftime("%Y-%m")), reverse=True)
        for month in months:
            for path in sorted((p for p in month.iterdir() if _RUN_ID_RE.fullmatch(p.name)), reverse=True):
                if path.name < floor:
                    break
                manifest = _load_json(path / "manifest.json")
                ctx = manifest.get("context") or {}
                if manifest.get("status") != "running" or ctx.get("kind") != "chat_turn":
                    continue
                if str(ctx.get("origin_chat_id") or ctx.get("delivery_chat_id") or "") != chat_id:
                    continue
                try:
                    created = dt.datetime.fromisoformat(str(manifest.get("created_at") or "").replace("Z", "+00:00")).timestamp()
                except (ValueError, OverflowError, OSError):
                    continue
                if since <= created <= now:
                    if found:
                        return ""  # Не выбираем между двумя возможными авторами.
                    found = path.name
    except OSError:
        return ""
    return found


def reader_status(base: Path | None = None, now: float | None = None) -> dict:
    """Квитанция читателя desk_inbox: жив ли раннер и занят ли он ходом."""
    import time as _time
    base = base or tree()
    now = now if now is not None else _time.time()
    receipt = _load_json(base / "memory" / ".control" / "desk_inbox" / ".reader.json")
    age = (now - _num(receipt.get("at"))) if receipt else None
    alive = age is not None and 0 <= age < READER_FRESH_S
    busy = bool(receipt.get("busy")) and alive
    run = str(receipt.get("run") or "")
    if busy and not run:
        run = _receipt_run(base, receipt, now)
    return {"alive": alive,
            "age_s": None if age is None else round(age, 1),
            "busy": busy,
            "run": run,
            "since": _num(receipt.get("since")),
            # Была ли квитанция ХОТЬ РАЗ. Раннер Hélène пишет её при старте, поэтому
            # «нет квитанции вовсе» значит не «агент сейчас выключен», а «этот агент
            # окно не читает» — так живёт Пульт Праксис на сервере, где записка
            # владельца ложится в дерево и ждёт читателя, которого нет. Окно обязано
            # говорить это словами, а не обещать «прочитает в следующий ход».
            "ever": bool(receipt)}


def _short_error(err) -> str:
    """Ошибка модели человеческими словами; хвост сырого текста — в last_error_raw."""
    text = str(err or "").strip().replace("\n", " ")
    low = text.lower()
    if "401" in low or "unauthorized" in low or "invalid api key" in low \
            or "incorrect api key" in low:
        return "ключ не подошёл"
    if "429" in low or "rate limit" in low or "quota" in low or "insufficient" in low:
        return "лимит или баланс исчерпан"
    if "timeout" in low or "timed out" in low:
        return "модель не ответила вовремя"
    if "connect" in low or "getaddrinfo" in low or "name or service" in low \
            or "network" in low:
        return "нет связи с моделью"
    if "404" in low or "not found" in low or "model_not_found" in low:
        return "такой модели нет по этому адресу"
    if "unsupported_parameter" in low or "unsupported parameter" in low \
            or "unsupported value" in low or "unrecognized request argument" in low:
        return "модель не принимает одну из настроек — проверь усилие рассуждения"
    if "context_length" in low or "context length" in low \
            or "maximum context" in low or "too many tokens" in low:
        return "разговор длиннее, чем модель принимает"
    if "content_filter" in low or "content policy" in low or "safety" in low:
        return "модель отказалась отвечать по своим правилам"
    if "invalid_request_error" in low or "400" in low:
        return "модель не приняла запрос"
    if "500" in low or "502" in low or "503" in low or "overloaded" in low \
            or "internal server error" in low:
        return "сбой на стороне модели"
    # Было: `text[:90]` — сырой английский Python-эксепшн, обрезанный по счётчику
    # посреди слова, прямо в шапку окна (и ещё дважды на других экранах).
    # Сырой текст остаётся в last_error_raw и в Журнале, человеку — фраза.
    return "подробности в Журнале"


def _key_present(anatomy: dict, llm: dict, voice: dict, product: dict) -> bool:
    """Есть ли у мозга ключ. Наружу уходит только булево, сам ключ — никогда.

    Настроенность считалась по одному ИМЕНИ модели. Владелец сохранял Настройки
    с пустым ключом — `llm.configured()` в дереве становилась False, агент
    замолкал НАВСЕГДА, а шапка окна писала зелёное «На связи» над мозгом,
    который сам про себя записал `brain_ready: false`. Руннер оба факта пишет
    (`has_key`, `brain_ready` в `anatomy.json`, `runner.py:_write_anatomy`) —
    читать их было некому, и «Модель не настроена» на пустом ключе не
    показывалась НИ РАЗУ.

    Голосов три, от свежего к старому:
      * `helene.json` — то, что владелец только что сохранил в Настройках;
      * `memory/llm.json` — то, по чему модель зовут на самом деле (тот же файл
        читает `llm._client_for`: пустой `api_key` -> клиента нет вообще);
      * снимок раннера — `has_key` и приговор дерева `brain_ready`.

    Правило «хоть один говорит „есть“ — значит есть» выбрано намеренно. Ложное
    «Модель не настроена» над работающим агентом здесь уже было один раз (F-10,
    снимок anatomy не собрался) и оно хуже запоздалого «На связи»: снимок
    пишется один раз на старте и стареет, а llm.json переписывается только при
    следующем запуске. Никто не высказался (старая анатомия, сервер без
    helene.json) — считаем, что ключ есть.
    """
    votes: list[bool] = []
    if "key_present" in product:
        votes.append(bool(product["key_present"]))
    block = (llm.get("frameworks") or {}).get(str(voice.get("framework") or "openai"))
    if isinstance(block, dict):
        votes.append(bool(str(block.get("api_key") or "").strip()))
    for field in ("has_key", "brain_ready"):
        if field in anatomy:
            votes.append(bool(anatomy.get(field)))
    return any(votes) if votes else True


def _mode_state_safe() -> dict:
    """Режим для аварийной ветки `state()`: не имеет права упасть второй раз."""
    try:
        return mode_state()
    except Exception:
        log.exception("режим не прочитался и в аварийной ветке")
        return mode_unknown("")


def state() -> dict:
    """Шапка окна — с рубежом: отказ читателя не гасит окно и телефон.

    Форма ответа та же, что у удачного разбора (окно читает `level`, `phrase`,
    `runner`, `brain` без проверок) — иначе рубеж уронил бы окно вместо трубы.
    Фраза при этом честная: продукт не делает вид, что всё в порядке.
    """
    try:
        return _state_impl()
    except Exception:
        log.exception("состояние не собралось")
        return {
            "agent": "Агент", "owner": "",
            "level": "error", "phrase": "Состояние не прочиталось",
            # Кнопки нет намеренно: окно умеет ровно два действия («settings» и
            # «restart»), и любое третье слово дало бы мёртвую кнопку. Куда
            # смотреть — сказано тревогой ниже.
            "action": None,
            "runner": {"alive": False, "age_s": None, "busy": False,
                       "run": "", "since": 0.0},
            "anatomy": False,
            "brain": {"configured": False, "model": "", "base_url": "",
                      "last_call_at": None, "last_error": None,
                      "last_error_raw": None},
            "relay": {"used": False, "authorized": False},
            "telegram": {"enabled": False},
            # Форма ответа обязана совпадать с удачной: окно читает state.mode
            # без проверок. Режим здесь пробуем отдельно — он читается из
            # helene.json и обычно жив, даже когда упало всё остальное.
            "mode": _mode_state_safe(),
            "desk": desk_build(),
            "next_wake": None,
            "alarms": [{"kind": "reader_failed",
                        "text": "состояние агента не прочиталось — "
                                "подробности в helene.log"}],
        }


def _state_impl() -> dict:
    """Что с агентом прямо сейчас — одна фраза и одно действие.

    Только чтение артефактов: квитанция читателя руннера (жив ли, думает ли),
    хвост вызовов модели (ошибки), настройка мозга из anatomy.json, вход реле,
    ближайший будильник. Порядок важности: мёртвый руннер > нет модели >
    реле без входа > ошибка модели > думает > спит > на связи.
    """
    import time as _time
    import datetime as _dt
    now = _time.time()
    base = tree()
    st = base / "memory" / ".state"
    anatomy = _load_json(st / "anatomy.json")
    # Дерево без снимка Hélène (Пульт Праксис) — имя из конфига продукта или
    # среды сервера (КОНТРАКТ-B→A §9), а не «Агент».
    agent = str(anatomy.get("agent_name") or product_config().get("agent_name")
                or os.environ.get("HELENE_AGENT_NAME") or "").strip() or "Агент"
    runner = reader_status(base, now)
    runner_alive = runner["alive"]
    busy = runner["busy"]
    # Настроенность НЕ определяется одним снимком anatomy.json: руннер пишет его
    # один раз на старте, вся сборка под глушителем `except Exception`, и при
    # пропавшем снимке окно говорило «Модель не настроена» при живом руннере и
    # настроенной модели — а экран Настроек показывал заполненные поля. Тупик.
    # Спрашиваем ещё и то, по чему модель РЕАЛЬНО зовут: memory/llm.json (его
    # пишет boot.project_brain из helene.json) и сам helene.json.
    model_cfg = dict(anatomy.get("model") or {})
    llm = _load_json(base / "memory" / "llm.json")
    voice = ((llm.get("roles") or {}).get("voice") or {})
    if not str(model_cfg.get("model") or "").strip():
        model_cfg["model"] = str(voice.get("model") or "").strip()
    if not str(model_cfg.get("base_url") or "").strip():
        frameworks = llm.get("frameworks") or {}
        block = frameworks.get(str(voice.get("framework") or "openai")) or {}
        model_cfg["base_url"] = str(block.get("base_url") or "").strip()
    product = product_config()
    named = bool(str(model_cfg.get("model") or "").strip() or product.get("model"))
    key_present = _key_present(anatomy, llm, voice, product)
    configured = named and key_present
    if not str(model_cfg.get("base_url") or "").strip():
        model_cfg["base_url"] = str(product.get("base_url") or "")
    llm_rows = tail_jsonl(st / "llm_calls.jsonl", 12)
    last = llm_rows[-1] if llm_rows else {}
    last_ts = _num(last.get("ts"))
    last_err = str(last.get("err") or "") if last else ""
    recent_error = bool(last_err) and (now - last_ts) < 900
    # Реле определяется фактом, а не подстрокой. Было
    # `"127.0.0.1:50" in base_url`: любой локальный сервер модели на порту 5000
    # (text-generation-webui по умолчанию) навсегда получал жёлтое «Подписка
    # ChatGPT не подключена», а настоящее реле на localhost:5011 или на другом
    # порту не опознавалось никогда — предупреждение о невыполненном входе не
    # появлялось, и агент молча получал 401.
    relay_home = base / "relay"
    relay_auth = (relay_home / "local_auth" / "auth.json").exists()
    base_url = str(model_cfg.get("base_url") or "")
    _, port = _url_host_port(base_url)
    if product:
        relay_used = bool(product.get("relay_enabled")) and _url_is_local(base_url) \
            and port == product.get("relay_port")
    else:   # конфига рядом нет (сервер) — по дому реле, который создаёт оболочка
        relay_used = _url_is_local(base_url) and relay_home.is_dir()
    next_wake = None
    for row in agenda().get("active", []):
        if str(row.get("kind") or "") not in _SLEEP_KINDS:
            continue
        when = str(row.get("when") or "")
        try:
            stamp = _dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_dt.timezone.utc)
        if stamp.timestamp() > now and (next_wake is None or stamp < next_wake):
            next_wake = stamp
    transports = list(anatomy.get("transports") or [])

    action = None
    if not runner_alive:
        level, phrase = "error", "Не запущен"
        action = {"label": "Перезапустить", "target": "restart"}
    elif not configured:
        if named:
            # Отдельная фраза: имя модели заполнено, а ключа нет. Прежнее
            # «Модель не настроена» отправляло владельца искать пустое поле
            # модели, которое на самом деле заполнено, — и он уходил ни с чем.
            # Коротко намеренно: шапка окна режет длинную фразу многоточием.
            level, phrase = "warn", "Ключ модели не введён"
            action = {"label": "Ввести ключ", "target": "settings"}
        else:
            level, phrase = "warn", "Модель не настроена"
            action = {"label": "Настроить", "target": "settings"}
    elif recent_error:
        # Свежая ошибка модели важнее предупреждения про вход в реле: раньше
        # ветка реле стояла выше и перекрывала настоящую причину молчания.
        level, phrase = "error", f"Модель отвечает ошибкой: {_short_error(last_err)}"
        action = {"label": "Настройки", "target": "settings"}
    elif relay_used and not relay_auth:
        level, phrase = "warn", "Подписка ChatGPT не подключена"
        action = {"label": "Войти", "target": "settings"}
    elif busy:
        level, phrase = "live", "Думает"
    elif next_wake is not None:
        local = next_wake.astimezone()
        level, phrase = "ok", f"Ждёт, проснётся в {local.strftime('%H:%M')}"
    else:
        level, phrase = "ok", "На связи"
    return {
        "agent": agent,
        "owner": str(anatomy.get("owner_name") or ""),
        "level": level,
        "phrase": phrase,
        "action": action,
        "runner": runner,
        # anatomy=False при configured=True значит «снимок устройства не собрался»,
        # а не «модель не настроена»: это разные беды с разными действиями.
        "anatomy": bool(anatomy),
        "brain": {"configured": configured, "model": str(model_cfg.get("model") or ""),
                  "base_url": str(model_cfg.get("base_url") or ""),
                  "last_call_at": last_ts or None,
                  "last_error": _short_error(last_err) if recent_error else None,
                  "last_error_raw": (last_err[:300] if recent_error else None)},
        "relay": {"used": relay_used, "authorized": relay_auth},
        "telegram": {"enabled": any("Telegram" in x for x in transports)},
        # Режим — в шапке состояния, а не только в анатомии: владелец должен
        # видеть, в каких правах живёт агент, не открывая отдельный экран.
        # Ходит по обоим каналам сразу, потому что /api/state есть и в HTTP, и
        # в диспетчере трубы (окно ходит именно трубой).
        "mode": mode_state(),
        "next_wake": next_wake.isoformat() if next_wake else None,
        "alarms": health().get("alarms", []),
        # Чем поднят сам канал: версия пакета desk. На сервере это
        # единственный источник версии (оболочки там нет), а выкладка по нему
        # сверяет, что канал встал именно с тем пакетом, который положен.
        "desk": desk_build(),
    }


def safe_write_md(rel: str, text: str, mtime_ns=None) -> dict:
    """Записать маркдаун по тем же правилам, по каким safe_read_md читает.

    Только файлы внутри групп _MD_GROUPS, только .md, без выхода из дерева;
    запись атомарная (tmp + replace), перевод строк "\n". Возвращает размер.

    Две защиты от молчаливой потери чужой работы (владелец правит файл в окне,
    агент правит тот же файл своей рукой — раньше выигрывал тот, кто записал
    последним, и чужая работа исчезала целиком, без предупреждения и копии):
      * `mtime_ns` — то, что окно видело при чтении. Не совпало -> code=conflict,
        писать не начинаем;
      * `<имя>.md.bak` рядом перед подменой — последний рубеж, когда окно
        отпечаток не прислало (старая сборка UI).
    """
    checked = _md_path(rel)
    if isinstance(checked, dict):
        checked["error"] = checked["error"].replace("показывает", "правит")
        return checked
    clean, path = checked
    if md_readonly(clean):
        return {"error": "Этот файл окно не правит: он подписан агентом, "
                         "ручная правка его обнуляет.", "code": "readonly"}
    try:
        stat = path.stat()
    except OSError:
        stat = None
    if stat is not None and mtime_ns not in (None, ""):
        try:
            seen = int(mtime_ns)
        except (TypeError, ValueError):
            seen = None
        if seen is not None and seen != stat.st_mtime_ns:
            return {"error": "Файл изменился с тех пор, как окно его открыло — "
                             "перечитай и перенеси правку заново.",
                    "code": "conflict", "mtime_ns": str(stat.st_mtime_ns)}
    body = str(text or "").replace("\r\n", "\n")
    if not body.endswith("\n"):
        body += "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if stat is not None:
            try:
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            except OSError:
                pass          # дерево только на чтение — не повод не пытаться писать
        tmp = path.with_name(".tmp-" + path.name)
        tmp.write_text(body, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        return {"error": f"не записалось: {exc}", "code": "io"}
    try:
        written = str(path.stat().st_mtime_ns)   # строкой — см. safe_read_md
    except OSError:
        written = None
    return {"path": clean, "size": len(body.encode("utf-8")), "mtime_ns": written}
