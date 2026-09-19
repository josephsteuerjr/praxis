# -*- coding: utf-8 -*-
"""Управление харнессом, который живёт не здесь: кто им надзирает, что поднято,
перезапуск и хвосты журналов.

Зачем это есть. Окно к серверу до 0.5.2 было смотрелкой: видно переписку, ходы
и кадр, а если раннер там упал в петлю или реле не поднялось — сделать из окна
нельзя ничего, и владелец шёл в ssh. На Windows этого вопроса нет: надзор —
оболочка рядом, и у неё есть `restart_self`. На сервере надзор — `serverboot`,
и он в ДРУГОМ процессе, чем канал: канал читает дерево, а детей поднимает не он.

Поэтому здесь файловый протокол, а не вызов функции:

    memory/.state/supervisor.json          надзор пишет о себе каждые ~3 с
    memory/.state/supervisor-request.json  канал кладёт просьбу владельца
    memory/.state/supervisor-receipt.json  надзор отвечает, что сделал

Просьба — не команда: канал её только КЛАДЁТ. Исполняет надзор, и он же
единственный, кто вправе трогать процессы. Если надзора нет (запуск руками,
Windows, упавший контейнер), просьба останется лежать, и окно скажет об этом
прямо, а не «перезапускаю…» в пустоту.

⚠ Молчание здесь запрещено ровно так же, как в ограде: «управление недоступно»
всегда идёт с причиной, а причина вычисляется из реальности — есть ли записка
надзора и свежая ли она, — а не из конфига.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import time
from pathlib import Path

SCHEMA = "helene.supervisor.v1"
SUPERVISOR = "supervisor.json"
REQUEST = "supervisor-request.json"
RECEIPT = "supervisor-receipt.json"

#: Надзор бьётся каждые три секунды (`serverboot`, шаг цикла). Впятеро больше —
#: запас на медленный диск и на паузу контейнера, но всё ещё «только что».
BEAT_STALE = 15.0

#: Что можно перезапустить. Ключ — в просьбе, значение — как это назвать вслух.
TARGETS = {
    "runner": "агента",
    "relay": "реле",
    "all": "весь харнесс",
}

#: Журналы, которые окно вправе прочитать. Список закрытый: имя из просьбы
#: НИКОГДА не превращается в путь — иначе ключ окна открывал бы любой файл
#: дерева, включая `helene.json` с ключами.
LOGS: dict[str, tuple[str, str]] = {
    "runner": ("runner.log", "агент (раннер)"),
    "channel": ("deskapp.log", "канал"),
    "relay": ("relay.log", "реле"),
}
#: Собственный журнал реле лежит внутри его дома и пишется по дням.
RELAY_LOG_DIR = ("relay", "logs")

MAX_LINES = 500
MAX_BYTES = 256 * 1024


def _state_dir(tree: Path) -> Path:
    return Path(tree) / "memory" / ".state"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".tmp-" + path.name)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


INTERRUPT = "interrupt.json"


def interrupt(tree: Path, by: str = "owner", scope: str = "all", reason: str = "") -> dict:
    """Попросить раннер прервать живой ход агента (12.09).

    Пишется файл `memory/.control/interrupt.json`; раннер читает его на тике часов
    (≤ 5–10 с) и кооперативно отменяет живые прогоны через run_manager: руки дальше
    не зовутся, черновик ответа не уходит, а идущий вызов модели дорабатывает до
    границы. На сервере канал держит `memory/.control` на запись ровно для таких
    просьб; на Windows дерево своё. scope — «all» или id одного прогона.
    """
    scope = str(scope or "all").strip() or "all"
    path = Path(tree) / "memory" / ".control" / INTERRUPT
    request = {"by": str(by or "owner")[:40], "scope": scope,
               "reason": str(reason or "").strip()[:200] or "прервано с Пульта", "at": _utc()}
    try:
        _write(path, request)
    except OSError as exc:
        return {"ok": False, "note": f"просьба не записалась: {exc}"}
    return {"ok": True, "request": request,
            "note": "просьба записана; раннер остановит ход на ближайшем тике (до 10 с), "
                    "идущий ответ модели дорабатывает до границы и не отправляется"}


def supervisor_state(tree: Path) -> dict:
    """Что известно о надзоре: жив ли, кого держит, можно ли им управлять.

    Свежесть записки — единственный признак «надзор живой». Ни pid, ни имя
    хоста для этого не годятся: в контейнере pid из чужого пространства
    процессов, а имя хоста меняется при каждом пересоздании (тот же корень, что
    у брошенного замка дерева в `serverboot`).
    """
    note = _read(_state_dir(tree) / SUPERVISOR)
    now = time.time()
    beat = float(note.get("beat_epoch") or 0.0)
    age = (now - beat) if beat else None
    alive = bool(note) and age is not None and age <= BEAT_STALE
    state = {
        "kind": str(note.get("kind") or ""),
        "alive": alive,
        "beat_age": round(age, 1) if age is not None else None,
        "started_utc": str(note.get("started_utc") or ""),
        "in_container": bool(note.get("in_container")),
        "children": note.get("children") if isinstance(note.get("children"), list) else [],
        "targets": [{"id": key, "title": title} for key, title in TARGETS.items()],
        "receipt": _read(_state_dir(tree) / RECEIPT) or None,
        "pending": _read(_state_dir(tree) / REQUEST) or None,
    }
    if alive:
        state["control"] = {"available": True, "why": ""}
    elif note:
        state["control"] = {
            "available": False,
            "why": f"надзор молчит {int(age or 0)} с — записка есть, но не обновляется: "
                   "контейнер стоит или надзор упал",
        }
    else:
        state["control"] = {
            "available": False,
            "why": "надзора рядом с этим деревом нет: харнесс поднят не им "
                   "(на Windows это оболочка — перезапуск делается её кнопкой)",
        }
    return state


def ask(tree: Path, target: str, by: str = "owner") -> dict:
    """Положить просьбу о перезапуске. Возвращает то, что показать владельцу.

    Отказ здесь — обычный ответ, а не исключение: «надзора нет» окно обязано
    показать словами, и лучше это делать до того, как кнопка нажата.
    """
    target = str(target or "").strip().lower()
    if target not in TARGETS:
        return {"ok": False, "note": f"так перезапускать нечего: {target or '(пусто)'}"}
    state = supervisor_state(tree)
    if not state["control"]["available"]:
        return {"ok": False, "note": state["control"]["why"], "supervisor": state}
    request = {
        "schema": SCHEMA,
        "id": secrets.token_hex(8),
        "action": "restart",
        "target": target,
        "asked_utc": _utc(),
        "by": by,
    }
    _write(_state_dir(tree) / REQUEST, request)
    return {"ok": True, "request": request,
            "note": f"просьба положена: перезапустить {TARGETS[target]}"}


def log_names(tree: Path) -> list[dict]:
    """Какие журналы есть у этого дерева и насколько они свежие."""
    out = []
    tree = Path(tree)
    for key, (name, title) in LOGS.items():
        path = tree / name
        try:
            stat = path.stat()
        except OSError:
            out.append({"id": key, "title": title, "exists": False})
            continue
        out.append({"id": key, "title": title, "exists": True,
                    "size": stat.st_size,
                    "changed_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.UTC)
                    .strftime("%Y-%m-%dT%H:%M:%SZ")})
    relay = _relay_own_log(tree)
    if relay is not None:
        stat = relay.stat()
        out.append({"id": "relay_own", "title": f"реле, свой журнал ({relay.name})",
                    "exists": True, "size": stat.st_size,
                    "changed_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.UTC)
                    .strftime("%Y-%m-%dT%H:%M:%SZ")})
    return out


def _relay_own_log(tree: Path) -> Path | None:
    """Самый свежий файл журнала реле: оно пишет по дням (`relay.log.ГГГГ-ММ-ДД`)."""
    folder = Path(tree).joinpath(*RELAY_LOG_DIR)
    try:
        files = [p for p in folder.iterdir() if p.is_file() and p.name.startswith("relay.log")]
    except OSError:
        return None
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def tail(tree: Path, name: str, lines: int = 200) -> dict:
    """Хвост названного журнала. Имя — только из закрытого списка."""
    tree = Path(tree)
    key = str(name or "").strip().lower()
    if key == "relay_own":
        path = _relay_own_log(tree)
        title = f"реле, свой журнал ({path.name})" if path else "реле, свой журнал"
    elif key in LOGS:
        path = tree / LOGS[key][0]
        title = LOGS[key][1]
    else:
        return {"ok": False, "note": f"такого журнала нет: {key or '(пусто)'}"}
    want = max(1, min(int(lines or 200), MAX_LINES))
    if path is None or not path.is_file():
        return {"ok": True, "id": key, "title": title, "exists": False, "text": "",
                "note": "журнала ещё нет — этот ребёнок ничего не написал"}
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - MAX_BYTES))
        blob = fh.read()
    text = blob.decode("utf-8", "replace")
    if size > MAX_BYTES:
        # Обрезанную первую строку не показываем: она врёт видом целой.
        text = text.split("\n", 1)[-1]
    rows = text.splitlines()[-want:]
    return {"ok": True, "id": key, "title": title, "exists": True,
            "size": size, "lines": len(rows), "text": "\n".join(rows)}


# --- контейнеры: то, что живёт НЕ в дереве ------------------------------------
#
# Файловый протокол выше бесполезен ровно тогда, когда он нужнее всего: если
# агент лёг, просьбу со стола некому взять. Поэтому рядом с Пультом может стоять
# отдельная служба (`server/deskctl.py`) — она вне агента, знает закрытый список
# контейнеров и умеет три вещи: показать их состояние, отдать хвост журнала,
# перезапустить. Канал сюда только ходит; решать, что позволено, — её дело.
#
# Нет адреса службы — нет и раздела: окно говорит «управления контейнерами здесь
# нет», а не рисует мёртвые кнопки.

DESKCTL_URL = "HELENE_DESKCTL"
DESKCTL_TOKEN = "HELENE_DESKCTL_TOKEN"
DESKCTL_TIMEOUT = 20


def deskctl_where() -> tuple[str, str]:
    """Адрес и ключ службы контейнеров. Пустой адрес — службы нет."""
    return ((os.environ.get(DESKCTL_URL) or "").strip().rstrip("/"),
            (os.environ.get(DESKCTL_TOKEN) or "").strip())


def deskctl_call(path: str, method: str = "GET", body: dict | None = None) -> dict:
    """Один запрос к службе контейнеров. Отказ — обычный ответ с причиной."""
    import urllib.error  # noqa: PLC0415 — нужны только здесь
    import urllib.request

    base, token = deskctl_where()
    if not base:
        return {"ok": False, "available": False,
                "why": "служба управления контейнерами рядом с этим каналом не объявлена "
                       f"({DESKCTL_URL})"}
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=DESKCTL_TIMEOUT) as answer:
            return json.loads(answer.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "available": True,
                "why": f"служба ответила {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}"}
    except Exception as exc:  # noqa: BLE001 — причина обязана доехать до окна
        return {"ok": False, "available": False,
                "why": f"служба не ответила: {type(exc).__name__}: {exc}"}


def containers() -> dict:
    """Что за контейнеры рядом и живы ли они."""
    got = deskctl_call("/state")
    if "containers" not in got:
        return {"available": False, "why": got.get("why", "служба молчит"), "containers": []}
    return {"available": True, "why": "", "containers": got.get("containers", []),
            "allowed": got.get("allowed", []), "at": got.get("at", "")}


def container_log(name: str, lines: int = 200, only_errors: bool = False) -> dict:
    query = f"?lines={max(1, min(int(lines or 200), 400))}" + ("&errors=1" if only_errors else "")
    return deskctl_call(f"/logs/{name}{query}")


def container_restart(name: str) -> dict:
    return deskctl_call(f"/restart/{name}", method="POST")


def brain() -> dict:
    """Роли мозга и их модели. Ключи провайдеров служба не отдаёт вовсе."""
    return deskctl_call("/brain")


def brain_models() -> dict:
    """Живой список моделей у мозга — не наш список, а его собственный ответ."""
    return deskctl_call("/brain/models")


def brain_set(role: str, fields: dict) -> dict:
    return deskctl_call("/brain", method="POST", body={"role": role, "fields": fields})


# --- сторона надзора ---------------------------------------------------------
#
# Ниже — то, что зовёт `serverboot`. Оно живёт здесь же, чтобы обе стороны
# протокола читались рядом: разнесённые по файлам, они разъезжаются молча.


def beat(tree: Path, kind: str, started_utc: str, children: list[dict]) -> None:
    """Надзор говорит о себе: жив, вот дети, вот когда начал."""
    _write(_state_dir(tree) / SUPERVISOR, {
        "schema": SCHEMA,
        "kind": kind,
        "pid": os.getpid(),
        "started_utc": started_utc,
        "beat_utc": _utc(),
        "beat_epoch": time.time(),
        "in_container": Path("/.dockerenv").exists(),
        "children": children,
    })


def take_request(tree: Path) -> dict:
    """Взять просьбу владельца, если она есть (и убрать её со стола)."""
    path = _state_dir(tree) / REQUEST
    request = _read(path)
    if not request:
        return {}
    try:
        path.unlink()
    except OSError:
        pass
    return request


def receipt(tree: Path, request: dict, done: bool, note: str) -> None:
    """Расписка о просьбе: что просили, что вышло, когда."""
    _write(_state_dir(tree) / RECEIPT, {
        "schema": SCHEMA,
        "id": str(request.get("id") or ""),
        "action": str(request.get("action") or ""),
        "target": str(request.get("target") or ""),
        "asked_utc": str(request.get("asked_utc") or ""),
        "done": bool(done),
        "note": note,
        "done_utc": _utc(),
    })


# --------------------------------------------------------------------------- #
#  Кто держит порт — БЕЗ КЛЮЧА
# --------------------------------------------------------------------------- #

def who(root: Path | None) -> dict:
    """Опознавательная карточка канала. Отдаётся ДО ключа и всем.

    ⚠⚠ ЗАЧЕМ ОНА ЕСТЬ (находка судей 19.09). Окно при старте видело на своём
    порту чужой процесс, получало от него 401/403 — и отправляло ему
    `?key=<токен дерева>`, то есть само вручало ключ владельца первому, кто
    занял порт. А занять локальный порт может ЛЮБАЯ программа учётки, в том
    числе поднятая самим агентом. По этому ключу отдаются ключ модели, токен
    бота, конституция и правка конфига.

    Теперь окно сперва спрашивает здесь — анонимно — и предъявляет ключ только
    тому, кто назвался нашим продуктом И нашим корнем установки.

    Секретов тут нет по построению: имя продукта видно в заголовке окна, корень
    установки — это путь, а не тайна (его же печатает мастер и видно в `ps`), а
    pid виден в списке процессов. Дерево данных, токены и конфиг сюда НЕ едут:
    опознание — это «свой или чужой», а не «расскажи о себе всё».
    """
    try:
        where = str(Path(root).resolve()) if root is not None else ""
    except OSError:
        where = str(root or "")
    return {
        "product": (os.environ.get("HELENE_PRODUCT") or "Hélène").strip() or "Hélène",
        "root": where,
        "pid": os.getpid(),
    }
