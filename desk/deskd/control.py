# -*- coding: utf-8 -*-
"""Управление харнессом, который живёт не здесь: кто им надзирает, что поднято,
перезапуск, хвосты журналов и — на сервере — обновление через исполнителя рядом
(раздел «обновление на сервере» ниже, 27.09).

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
import re
import secrets
import threading
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
    tmp = path.with_name(".tmp-" + path.name + "-" + secrets.token_hex(6))
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


INTERRUPT = "interrupt.json"
_INTERRUPT_LOCK = threading.Lock()


def interrupt(tree: Path, by: str = "owner", scope: str = "all", reason: str = "") -> dict:
    """Order this channel's concurrent publishers, including their timestamps."""
    with _INTERRUPT_LOCK:
        return _interrupt_locked(tree, by, scope, reason)


def _interrupt_locked(tree: Path, by: str, scope: str, reason: str) -> dict:
    """Попросить раннер прервать живой ход агента (12.09).

    Пишется файл `memory/.control/interrupt.json`; независимый наблюдатель движка
    кооперативно отменяет живые прогоны через run_manager: руки дальше
    не зовутся, черновик ответа не уходит, а идущий вызов модели дорабатывает до
    границы. На сервере канал держит `memory/.control` на запись ровно для таких
    просьб; на Windows дерево своё. scope — «all» или id одного прогона.
    """
    scope = str(scope or "all").strip() or "all"
    path = Path(tree) / "memory" / ".control" / INTERRUPT
    request = {"id": secrets.token_hex(12), "by": str(by or "owner")[:40], "scope": scope,
               "reason": str(reason or "").strip()[:200] or "прервано из окна",
               "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        _write(path, request)
    except OSError as exc:
        return {"ok": False, "note": f"просьба не записалась: {exc}"}
    return {"ok": True, "request": request,
            "note": "просьба записана; движок проверит её независимо от текущего хода. "
                    "Остановка кооперативная: вызов модели дорабатывает до границы; "
                    "уже совершённые действия не отменяются"}


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
        "interrupt_receipt": _read(Path(tree) / "memory" / ".control" / "interrupt-receipt.json") or None,
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
            "why": "движок не отвечает: записки надзора нет — агент не поднят или ещё "
                   "поднимается; окно или служба поднимут его сами",
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
# агент лёг, просьбу со стола некому взять. Поэтому рядом с каналом может стоять
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


# --- обновление на сервере: план → «да» человека → исполнитель снаружи ---------
#
# Жалоба Дмитрия К (26.09): его агент весь день готовил обновление и упирался в «нет
# доступа» — агент живёт В контейнере, который надо заменить, и изнутри у него нет ни
# докера, ни путей хоста. Отмычку внутрь (сокет докера в контейнер агента) мы не даём
# и давать не будем. Вместо неё — исполнитель РЯДОМ (`server/updater/updater.py`,
# свой контейнер со своим сокетом) и тот же файловый протокол, что у надзора:
#
#     memory/.control/update-plan.json          план: агент (рука update_request) или окно
#     memory/.control/update-plan.receipt.json  исполнитель: сверил / ждёт «да» / идёт / итог
#     memory/.control/update-plan.confirm.json  «да» или «нет» человека
#     memory/.control/update-plan.reported.json агент рассказал владельцу об итоге
#     memory/.control/updater.json              исполнитель о себе, каждые ~5 с
#     memory/.control/update-history.jsonl      итоги прошлых обновлений
#
# План без «да» не исполняется никогда. «Да» привязано к одноразовому ключу (`nonce`)
# из расписки «жду подтверждения»: подтвердить можно только то, что исполнитель уже
# сверил и показал, — версию, архив, сумму, что уйдёт в копию. Исполнитель умеет одно:
# поставить ОФИЦИАЛЬНЫЙ выпуск новее текущего, с копией и откатом; адрес выпусков он
# берёт из своей среды, а не из helene.json — тот правит агент.
#
# ⚠ Все поля плана — из закрытого списка (`validate_plan`), и ни одно не становится
# путём, командой или адресом. Эту проверку делят канал, рука агента и сам исполнитель:
# разнесённая по трём местам, она разъехалась бы молча.

UPDATE_SCHEMA = "helene.update.v1"
UPDATE_PLAN = "update-plan.json"
UPDATE_RECEIPT = "update-plan.receipt.json"
UPDATE_CONFIRM = "update-plan.confirm.json"
UPDATE_REPORTED = "update-plan.reported.json"
UPDATER_BEAT = "updater.json"
UPDATE_HISTORY = "update-history.jsonl"

#: Исполнитель бьётся каждые ~5 с — и во время долгих шагов тоже (отдельным потоком):
#: сборка образа идёт минуты, и молчание в это время читалось бы как «исполнителя нет».
UPDATER_STALE = 30.0

#: Что уходит в копию перед подменой. Ключ — в плане, значение — как это назвать вслух.
UPDATE_BACKUPS = {
    "full": "код, helene.json и вся папка data/ (память, переписка, ключи)",
    "code": "код и helene.json — БЕЗ копии data/",
}

#: Проверки после подъёма. Закрытый список: имя из плана никогда не становится командой.
UPDATE_CHECKS = {
    "running": "контейнер поднят и не перезапускается",
    "channel": "канал отвечает (/api/health)",
    "version": "канал поднят новой версией (/api/state → desk.version)",
    "supervisor": "надзор serverboot бьётся в своей записке",
    "runner": "агент (раннер) жив",
    "config": "адрес, порт, имена хостов и модели перенесены с прежнего контейнера",
    "brain": "мозг настроен: модель и ключ на месте",
}
#: Эти идут всегда — план может только ДОБАВИТЬ к ним, не убрать: провал любой = откат.
UPDATE_MANDATORY = ("running", "channel", "version", "supervisor", "runner", "config")

UPDATE_ACTIVE = ("checking", "awaiting", "confirmed", "running")
UPDATE_FINAL = ("refused", "declined", "expired", "superseded", "done", "rolled_back", "failed")
#: Итоги, о которых агент рассказывает владельцу сам, поднявшись.
UPDATE_REPORTABLE = ("done", "rolled_back", "failed")

#: Сколько расписка ждёт «да», прежде чем план истечёт.
UPDATE_AWAIT_HOURS = 24
#: Сколько минут исполнитель ждёт проверок после подъёма (раннер поднимается минуты).
UPDATE_WAIT_MIN = (2, 30)
UPDATE_WAIT_DEFAULT = 10

#: Как поднять исполнителя, если его нет. Из корня установки на хосте.
UPDATER_COMMAND = "docker compose -f server/updater/docker-compose.yml up -d --build"

_VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,5}$")


def version_tuple(text) -> tuple[int, ...] | None:
    """«1.1.0» / «v1.1.0» -> (1, 1, 0). Непонятное — None, а не «0.0.0»."""
    raw = str(text or "").strip().lstrip("vV")
    if not _VERSION_RE.match(raw):
        return None
    return tuple(int(part) for part in raw.split("."))


def _control_dir(tree: Path) -> Path:
    return Path(tree) / "memory" / ".control"


def validate_plan(raw) -> tuple[dict | None, str]:
    """План -> (чистый план, "") или (None, причина отказа словами).

    Выживают только поля из закрытого списка; всё прочее отбрасывается молча — оно
    всё равно ничего не значит для исполнителя. Отказ — только там, где поле есть,
    но годным его не сделать.
    """
    if not isinstance(raw, dict):
        return None, "план — не объект"
    plan_id = str(raw.get("id") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{8,32}", plan_id):
        return None, "у плана нет годного id"
    version = str(raw.get("version") or "latest").strip().lstrip("vV") or "latest"
    if version != "latest" and version_tuple(version) is None:
        return None, f"версия «{version}» не похожа на номер выпуска (1.2.3) и не «latest»"
    backup = str(raw.get("backup") or "full").strip().lower()
    if backup not in UPDATE_BACKUPS:
        return None, f"копия бывает {' или '.join(UPDATE_BACKUPS)}, а не «{backup}»"
    asked = raw.get("checks") or []
    if not isinstance(asked, list):
        return None, "checks — список имён проверок"
    unknown = [str(name) for name in asked if str(name) not in UPDATE_CHECKS]
    if unknown:
        return None, (f"таких проверок нет: {', '.join(unknown)} "
                      f"(есть: {', '.join(UPDATE_CHECKS)})")
    checks = list(UPDATE_MANDATORY) + [str(n) for n in asked
                                       if str(n) in UPDATE_CHECKS and str(n) not in UPDATE_MANDATORY]
    try:
        wait_min = int(raw.get("wait_min") or UPDATE_WAIT_DEFAULT)
    except (TypeError, ValueError):
        return None, "wait_min — число минут"
    wait_min = max(UPDATE_WAIT_MIN[0], min(UPDATE_WAIT_MIN[1], wait_min))
    return {
        "schema": UPDATE_SCHEMA,
        "id": plan_id,
        "version": version,
        "backup": backup,
        "checks": list(dict.fromkeys(checks)),
        "wait_min": wait_min,
        "force_extensions": bool(raw.get("force_extensions")),
        "reason": " ".join(str(raw.get("reason") or "").split())[:500],
        "asked_by": str(raw.get("asked_by") or "agent")[:40],
        "asked_utc": str(raw.get("asked_utc") or "")[:40],
        "chat": " ".join(str(raw.get("chat") or "").split())[:160],
    }, ""


def updater_state(tree: Path) -> dict:
    """Есть ли рядом исполнитель обновлений и в каком он виде — по свежести его записки."""
    beat = _read(_control_dir(tree) / UPDATER_BEAT)
    stamp = float(beat.get("beat_epoch") or 0.0) if beat else 0.0
    age = (time.time() - stamp) if stamp else None
    alive = bool(beat) and age is not None and age <= UPDATER_STALE
    healthy = alive and bool(beat.get("ok"))
    if healthy:
        why = ""
    elif alive:
        why = str(beat.get("why") or "исполнитель на связи, но говорит, что работать не может")
    elif beat:
        why = (f"исполнитель молчит {int(age or 0)} с — его контейнер стоит или упал; "
               f"на хосте: docker logs helene-updater")
    else:
        why = ("исполнителя обновлений рядом нет. Владелец поднимает его один раз, на хосте, "
               f"из папки установки: {UPDATER_COMMAND}")
    latest = beat.get("latest") if isinstance(beat.get("latest"), dict) else {}
    return {
        "present": bool(beat),
        "alive": alive,
        "ok": healthy,
        "why": why,
        "beat_age": round(age, 1) if age is not None else None,
        "started_utc": str(beat.get("started_utc") or ""),
        "busy": str(beat.get("busy") or ""),
        "current": str(beat.get("current_version") or ""),
        "latest": {"version": str(latest.get("version") or ""),
                   "checked_utc": str(latest.get("checked_utc") or ""),
                   "why": str(latest.get("why") or "")},
        "newer": bool(version_tuple(latest.get("version")) and version_tuple(beat.get("current_version"))
                      and version_tuple(latest.get("version")) > version_tuple(beat.get("current_version"))),
        "command": UPDATER_COMMAND,
    }


def update_history(tree: Path, limit: int = 5) -> list[dict]:
    path = _control_dir(tree) / UPDATE_HISTORY
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 64 * 1024))
            rows = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for line in rows[-limit:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def update_state(tree: Path) -> dict:
    """Всё об обновлении одним ответом — для окна и для руки агента."""
    ctl = _control_dir(tree)
    return {
        "updater": updater_state(tree),
        "plan": _read(ctl / UPDATE_PLAN) or None,
        "receipt": _read(ctl / UPDATE_RECEIPT) or None,
        "reported": _read(ctl / UPDATE_REPORTED) or None,
        "history": update_history(tree),
        "checks": UPDATE_CHECKS,
        "backups": UPDATE_BACKUPS,
    }


def update_plan(tree: Path, version: str = "latest", backup: str = "full", reason: str = "",
                by: str = "owner", chat: str = "", checks=None, wait_min=None,
                force_extensions: bool = False) -> dict:
    """Положить план обновления. Исполнитель сверит его и попросит «да» у человека.

    Отказы — обычные ответы с причиной: исполнителя нет (и как его поднять), идёт
    другое обновление, поле плана не годится. План, ждущий «да», новый план заменяет:
    исполнитель пометит прежний «заменён».
    """
    state = updater_state(tree)
    if not state["ok"]:
        return {"ok": False, "note": state["why"], "updater": state}
    receipt = _read(_control_dir(tree) / UPDATE_RECEIPT)
    if str(receipt.get("state") or "") in ("checking", "confirmed", "running"):
        return {"ok": False, "note": f"идёт другое обновление ({receipt.get('state')}: "
                                     f"{receipt.get('note') or receipt.get('step') or '…'}) — "
                                     "новый план положу, когда оно закончится"}
    raw = {"id": secrets.token_hex(8), "version": version, "backup": backup,
           "checks": list(checks or []), "wait_min": wait_min,
           "force_extensions": force_extensions, "reason": reason, "asked_by": by,
           "asked_utc": _utc(), "chat": chat}
    plan, why = validate_plan(raw)
    if plan is None:
        return {"ok": False, "note": why}
    _write(_control_dir(tree) / UPDATE_PLAN, plan)
    target = "последнюю версию" if plan["version"] == "latest" else f"версию {plan['version']}"
    return {"ok": True, "plan": plan,
            "note": (f"план положен: обновить Hélène на {target}, копия — "
                     f"{UPDATE_BACKUPS[plan['backup']]}. Исполнитель сверит выпуск за несколько "
                     "секунд и попросит подтверждения; без «да» человека ничего не начнётся")}


def update_confirm(tree: Path, plan_id: str, nonce: str, decision: str, by: str = "window",
                   words: str = "") -> dict:
    """«Да» или «нет» человека на план, который исполнитель уже сверил и показал.

    Подтвердить можно только расписку в состоянии «жду подтверждения» и только с её
    ключом: так «да» относится ровно к показанной версии и сумме, а не к плану вообще.
    """
    decision = str(decision or "").strip().lower()
    if decision not in ("yes", "no"):
        return {"ok": False, "note": "ответ бывает yes или no"}
    receipt = _read(_control_dir(tree) / UPDATE_RECEIPT)
    if str(receipt.get("state") or "") != "awaiting":
        what = receipt.get("state") or "расписки нет"
        return {"ok": False, "note": f"подтверждать нечего: план не ждёт ответа ({what})"}
    if str(plan_id or "") != str(receipt.get("id") or ""):
        return {"ok": False, "note": "план сменился, пока ты смотрел — перечитай расписку"}
    if not nonce or str(nonce) != str(receipt.get("nonce") or ""):
        return {"ok": False, "note": "ключ подтверждения не тот — перечитай расписку"}
    confirm = {"schema": UPDATE_SCHEMA, "id": str(plan_id), "nonce": str(nonce),
               "decision": decision, "by": str(by or "window")[:40],
               "words": " ".join(str(words or "").split())[:500],
               "at_utc": _utc(), "at_epoch": time.time()}
    _write(_control_dir(tree) / UPDATE_CONFIRM, confirm)
    if decision == "no":
        return {"ok": True, "confirm": confirm, "note": "ответ записан: не обновлять"}
    return {"ok": True, "confirm": confirm,
            "note": (f"«да» записано: {receipt.get('from_version') or '?'} → "
                     f"{receipt.get('to_version') or '?'}. Исполнитель скачает и сверит архив, "
                     "соберёт новый образ и только потом остановит агента — на несколько минут "
                     "он будет недоступен")}


def update_unreported(tree: Path, max_age_days: float = 3.0) -> dict | None:
    """Итог обновления, о котором агент ещё не рассказал владельцу (или None)."""
    ctl = _control_dir(tree)
    receipt = _read(ctl / UPDATE_RECEIPT)
    if str(receipt.get("state") or "") not in UPDATE_REPORTABLE:
        return None
    if str(_read(ctl / UPDATE_REPORTED).get("id") or "") == str(receipt.get("id") or ""):
        return None
    finished = float(receipt.get("finished_epoch") or 0.0)
    if not finished or time.time() - finished > max_age_days * 86400:
        return None
    return receipt


def update_mark_reported(tree: Path, plan_id: str) -> None:
    _write(_control_dir(tree) / UPDATE_REPORTED, {"schema": UPDATE_SCHEMA, "id": str(plan_id),
                                                   "at_utc": _utc()})


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
