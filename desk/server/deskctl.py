# -*- coding: utf-8 -*-
"""Пультовой надзор: перезапустить контейнер, прочитать его журнал, сменить мозг.

Зачем отдельная служба. Пульт — окно к ЧУЖОМУ дереву на сервере: оно
смонтировано ему на чтение, докера у него нет и быть не должно. Пока это так,
владелец видит, что агент лёг, и не может сделать ничего — идёт в ssh. А
когда агент лёг, файловый протокол (`deskd/control.py`) бесполезен по
построению: просьбу некому взять.

Отсюда правило этой службы: **она работает тогда, когда агент мёртв**. Она не
внутри агента и не зависит от него.

Что она делает и чего не делает:

  * знает ЗАКРЫТЫЙ список контейнеров (`DESKCTL_CONTAINERS`) — на общем сервере
    рядом живут чужие проекты, и «перезапусти что угодно» здесь означало бы
    отмычку к ним. Имя не из списка — отказ, и он назван;
  * умеет ровно три вещи: состояние списка, хвост журнала, перезапуск;
  * плюс мозг: показать роли из `llm.json` и сменить МОДЕЛЬ роли. Ключи она не
    показывает и не трогает — меняются только `model`, `fallback_model` и
    `framework` у названной роли, и прежний файл ложится рядом с отметкой
    времени;
  * ключ обязателен (`DESKCTL_TOKEN`), слушает только петлю. Наружу её выпускать
    незачем: к ней ходит канал Пульта, а не браузер.

⚠ Доступ к докеру у неё настоящий (сокет хоста). Поэтому белый список — не
украшение, а единственная граница: всё, что она умеет делать с контейнерами,
она умеет делать ТОЛЬКО с названными.

⚠ С докером она говорит ПО СОКЕТУ, а не через `docker` в контейнере: класть
пятьдесят мегабайт клиента в образ ради трёх запросов — и тащить его версию
следом за версией демона — дороже, чем три HTTP-вызова.

Запуск:  python server/deskctl.py [порт]
Среда:   DESKCTL_TOKEN, DESKCTL_CONTAINERS (через запятую), DESKCTL_TREE,
         DESKCTL_SOCKET (по умолчанию /var/run/docker.sock).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import shutil
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("deskctl")

SCHEMA = "praxis.deskctl.v1"
TOKEN = (os.environ.get("DESKCTL_TOKEN") or "").strip()
TREE = Path(os.environ.get("DESKCTL_TREE") or "/data")
SOCKET = os.environ.get("DESKCTL_SOCKET") or "/var/run/docker.sock"
MAX_LINES = 400

#: Что этой службе позволено трогать. Пусто — не позволено ничего, и она об этом
#: говорит: пустой список безопаснее догадки о том, что владелец имел в виду.
CONTAINERS = tuple(name.strip() for name in
                   (os.environ.get("DESKCTL_CONTAINERS") or "").split(",") if name.strip())

#: Роли мозга, которые вправе менять окно. Остальные ключи `llm.json` — не наше
#: дело: там ключи провайдеров и цены.
BRAIN_FIELDS = ("model", "fallback_model", "framework", "fallback_framework", "reasoning_effort")


def _utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _docker(path: str, method: str = "GET", timeout: float = 30) -> tuple[int, bytes]:
    """Один запрос к демону докера по сокету. -> (код ответа, тело)."""
    try:
        connector = aiohttp.UnixConnector(path=SOCKET)
    except Exception as exc:  # noqa: BLE001 — сокета может не быть вовсе
        return 0, f"сокет докера недоступен ({SOCKET}): {exc}".encode()
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.request(method, "http://docker" + path,
                                       timeout=aiohttp.ClientTimeout(total=timeout)) as answer:
                return answer.status, await answer.read()
    except Exception as exc:  # noqa: BLE001 — причина обязана доехать до окна
        return 0, f"{type(exc).__name__}: {exc}".encode()


def _plain(blob: bytes) -> str:
    """Журнал докера идёт кадрами по восемь байт заголовка на кусок.

    Без снятия заголовков в окно приезжали бы обрывки с управляющими байтами в
    начале каждой строки — «мусор перед текстом», который выглядит как поломка
    журнала, а не как наш недосмотр.
    """
    out, i = [], 0
    while i + 8 <= len(blob):
        head = blob[i:i + 8]
        if head[0] in (0, 1, 2) and head[1:4] == bytes(3):
            size = int.from_bytes(head[4:8], "big")
            out.append(blob[i + 8:i + 8 + size])
            i += 8 + size
        else:                       # поток без кадров (TTY) — читаем как есть
            return blob.decode("utf-8", "replace")
    if i < len(blob):
        out.append(blob[i:])
    return b"".join(out).decode("utf-8", "replace")


def allowed(name: str) -> bool:
    return name in CONTAINERS


def refuse(name: str) -> dict:
    """Один отказ на все три двери: имя вне списка — и сказано, какой список."""
    return {"ok": False, "note": f"этой службе не позволено трогать «{name}»: "
                                 f"список закрыт ({', '.join(CONTAINERS) or 'пуст'})"}


def digest(rows: list[dict]) -> list[dict]:
    """Ответ докера -> строки для окна. Разбор отдельно от запроса: его и проверяют."""
    seen = {}
    for row in rows if isinstance(rows, list) else []:
        for raw in row.get("Names") or []:
            seen[str(raw).lstrip("/")] = row
    out = []
    for name in CONTAINERS:
        got = seen.get(name)
        out.append({
            "name": name,
            "known": got is not None,
            "up": bool(got and str(got.get("State", "")).lower() == "running"),
            "status": str(got.get("Status") or "") if got else "нет такого контейнера",
            "image": str(got.get("Image") or "") if got else "",
            "since": str(got.get("State") or "") if got else "",
        })
    return out


async def containers() -> list[dict]:
    """Состояние названных контейнеров — один запрос к докеру на всех."""
    if not CONTAINERS:
        return []
    code, blob = await _docker("/containers/json?all=1")
    if code != 200:
        why = blob.decode("utf-8", "replace")[:200]
        return [{"name": name, "known": False, "up": False, "status": "докер не ответил",
                 "image": "", "since": "", "why": why} for name in CONTAINERS]
    try:
        rows = json.loads(blob.decode("utf-8", "replace"))
    except ValueError as exc:
        return [{"name": name, "known": False, "up": False,
                 "status": "докер ответил не JSON", "image": "", "since": "",
                 "why": str(exc)[:200]} for name in CONTAINERS]
    return digest(rows)


def only_error_rows(text: str) -> list[str]:
    """Строки, похожие на беду. Список слов — на двух языках: журнал двуязычный."""
    needles = ("error", "traceback", "exception", "critical", "failed",
               "ошибк", "провал", "не поднял", "падени")
    return [row for row in text.splitlines() if any(n in row.lower() for n in needles)]


async def logs(name: str, lines: int, only_errors: bool = False) -> dict:
    if not allowed(name):
        return refuse(name)
    want = max(1, min(int(lines or 200), MAX_LINES))
    code, blob = await _docker(
        f"/containers/{name}/logs?stdout=1&stderr=1&tail={want}", timeout=60)
    if code != 200:
        return {"ok": False, "note": blob.decode("utf-8", "replace")[:400]}
    text = _plain(blob)
    rows = only_error_rows(text) if only_errors else text.splitlines()
    return {"ok": True, "name": name, "lines": len(rows), "only_errors": only_errors,
            "text": "\n".join(rows[-want:])}


async def restart(name: str) -> dict:
    if not allowed(name):
        return refuse(name)
    code, blob = await _docker(f"/containers/{name}/restart", method="POST", timeout=180)
    if code not in (204, 304):
        return {"ok": False, "name": name,
                "note": blob.decode("utf-8", "replace")[:400] or f"докер ответил {code}"}
    return {"ok": True, "name": name, "note": f"перезапущен ({_utc()})"}


# --- мозг ---------------------------------------------------------------------


def brain_path() -> Path:
    return TREE / "memory" / "llm.json"


def brain_state() -> dict:
    """Роли и их модели. Ключи провайдеров НЕ отдаются — только адреса и имена."""
    path = brain_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "note": f"мозг не прочитался ({path}): {exc}"}
    roles = {}
    for name, spec in (raw.get("roles") or {}).items():
        if isinstance(spec, dict):
            roles[name] = {key: spec.get(key) for key in BRAIN_FIELDS if key in spec}
    frameworks = {}
    for name, spec in (raw.get("frameworks") or {}).items():
        if isinstance(spec, dict):
            # Адрес — да, ключ — никогда: он тут же уехал бы в окно и в журнал.
            frameworks[name] = {"base_url": spec.get("base_url", ""),
                                "key_present": bool(str(spec.get("api_key") or "").strip())}
    return {"ok": True, "path": str(path), "roles": roles, "frameworks": frameworks,
            "writable": os.access(path, os.W_OK)}


def brain_models() -> dict:
    """Какие модели вправду отдаёт мозг сейчас — спрашиваем у него, а не у списка.

    Захардкоженный список моделей уже стоил подписки: каталог у реле сжимается
    под лимитом, имя молча подменяется, и выбор в окне оказывается предложением
    того, чего нет. Поэтому здесь живой `/v1/models` по адресу из `llm.json`.
    """
    import urllib.request  # noqa: PLC0415 — нужен только здесь

    state = brain_state()
    if not state.get("ok"):
        return state
    out: dict = {"ok": True, "by_framework": {}}
    for name, spec in (state.get("frameworks") or {}).items():
        base = str(spec.get("base_url") or "").rstrip("/")
        if not base.startswith("http"):
            out["by_framework"][name] = {"ok": False, "why": "адрес не похож на HTTP"}
            continue
        url = base + ("/v1/models" if not base.endswith("/v1") else "/models")
        try:
            with urllib.request.urlopen(url, timeout=15) as answer:
                body = json.loads(answer.read().decode("utf-8", "replace"))
            ids = [str(row.get("id")) for row in (body.get("data") or []) if row.get("id")]
            out["by_framework"][name] = {"ok": True, "models": ids, "url": url}
        except Exception as exc:  # noqa: BLE001 — причина важна как текст
            out["by_framework"][name] = {"ok": False, "url": url,
                                         "why": f"{type(exc).__name__}: {exc}"[:200]}
    return out


def brain_set(role: str, fields: dict) -> dict:
    """Сменить модель роли. Прежний файл ложится рядом с отметкой времени."""
    path = brain_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "note": f"мозг не прочитался: {exc}"}
    roles = raw.get("roles")
    if not isinstance(roles, dict) or role not in roles:
        return {"ok": False, "note": f"роли «{role}» в мозге нет "
                                     f"(есть: {', '.join(sorted(roles or {}))})"}
    change = {key: value for key, value in (fields or {}).items()
              if key in BRAIN_FIELDS and isinstance(value, (str, int, float))}
    if not change:
        return {"ok": False, "note": f"менять нечего: разрешены {', '.join(BRAIN_FIELDS)}"}
    was = {key: roles[role].get(key) for key in change}
    backup = path.with_name(f"llm.json.before-desk-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}")
    try:
        shutil.copy2(path, backup)
        roles[role].update(change)
        tmp = path.with_name(".tmp-llm.json")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        return {"ok": False, "note": f"мозг не записался: {exc}"}
    log.info("мозг: роль %s %s -> %s (бэкап %s)", role, was, change, backup.name)
    return {"ok": True, "role": role, "was": was, "now": change,
            "backup": backup.name,
            "note": "применится, когда агент перечитает мозг — "
                    "перезапусти его, если нужно прямо сейчас"}


# --- дверь --------------------------------------------------------------------


def _authorized(request: web.Request) -> bool:
    if not TOKEN:
        return False
    given = request.headers.get("Authorization", "")
    if given.startswith("Bearer "):
        given = given[7:]
    return (given or request.query.get("key", "")).strip() == TOKEN


def guard(handler):
    async def wrapped(request: web.Request):
        if not _authorized(request):
            return web.json_response({"error": "нужен ключ службы"}, status=403)
        return await handler(request)
    return wrapped


@guard
async def h_state(request: web.Request):
    rows = await containers()
    return web.json_response({"schema": SCHEMA, "containers": rows,
                              "allowed": list(CONTAINERS), "at": _utc()})


@guard
async def h_logs(request: web.Request):
    name = request.match_info["name"]
    lines = request.query.get("lines", "200")
    only = request.query.get("errors", "") in ("1", "true", "yes")
    try:
        want = int(lines)
    except ValueError:
        want = 200
    return web.json_response(await logs(name, want, only))


@guard
async def h_restart(request: web.Request):
    name = request.match_info["name"]
    return web.json_response(await restart(name))


@guard
async def h_brain(request: web.Request):
    return web.json_response(await asyncio.to_thread(brain_state))


@guard
async def h_brain_models(request: web.Request):
    return web.json_response(await asyncio.to_thread(brain_models))


@guard
async def h_brain_set(request: web.Request):
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    role = str((body or {}).get("role") or "").strip()
    fields = (body or {}).get("fields") or {}
    if not role or not isinstance(fields, dict):
        return web.json_response({"ok": False, "note": "нужны role и fields"}, status=400)
    return web.json_response(await asyncio.to_thread(brain_set, role, fields))


async def h_health(request: web.Request):
    return web.json_response({"schema": SCHEMA, "ok": True, "containers": len(CONTAINERS),
                              "tree": str(TREE), "at": _utc()})


def app() -> web.Application:
    server = web.Application()
    server.add_routes([
        web.get("/health", h_health),
        web.get("/state", h_state),
        web.get("/logs/{name}", h_logs),
        web.post("/restart/{name}", h_restart),
        web.get("/brain", h_brain),
        web.get("/brain/models", h_brain_models),
        web.post("/brain", h_brain_set),
    ])
    return server


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("DESKCTL_PORT") or 8096)
    if not TOKEN:
        raise SystemExit("нет DESKCTL_TOKEN — служба без ключа не поднимается")
    if not CONTAINERS:
        log.warning("список контейнеров пуст (DESKCTL_CONTAINERS) — "
                    "служба поднимется, но трогать ей нечего")
    log.info("пультовой надзор на 0.0.0.0:%s, контейнеры: %s, дерево: %s",
             port, ", ".join(CONTAINERS) or "(пусто)", TREE)
    web.run_app(app(), host="0.0.0.0", port=port, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
