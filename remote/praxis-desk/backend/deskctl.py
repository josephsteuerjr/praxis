# -*- coding: utf-8 -*-
"""Служба надзора: перезапустить контейнер, прочитать его журнал, сменить мозг.

Зачем отдельная служба. Praxis — окно к ЧУЖОМУ дереву на сервере: оно
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
    незачем: к ней ходит канал окна, а не браузер.

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
XTTS_UNIT = 'praxis-xtts.service'
_xtts_change = asyncio.Lock()


async def _xtts_systemctl(*args: str) -> tuple[int, str]:
    # Existing owner-owned SSH helper keeps its pinned host identity and key.
    # Fixed arguments below expose neither arbitrary units nor host commands.
    helper = TREE / 'memory/.secrets/server-root/root_client.py'
    if not helper.is_file():
        return -1, ''
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-B', str(helper), 'exec', 'systemctl', *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 12)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    return process.returncode, stdout.decode('utf-8', 'replace')


async def xtts_service_state() -> dict:
    try:
        code, text = await _xtts_systemctl('show', XTTS_UNIT, '-p', 'LoadState',
            '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID', '-p', 'Result', '-p', 'UnitFileState')
        values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        if code != 0 or values.get('LoadState') != 'loaded':
            raise ValueError('service unavailable')
        return {'ok': True, 'available': True, 'name': 'XTTS',
                'active_state': values.get('ActiveState', 'unknown'),
                'sub_state': values.get('SubState', 'unknown'),
                'pid': int(values.get('MainPID') or 0),
                'result': values.get('Result', ''), 'unit_file_state': values.get('UnitFileState', '')}
    except (OSError, ValueError, asyncio.TimeoutError):
        return {'ok': False, 'available': False, 'name': 'XTTS',
                'note': 'Не удалось проверить службу XTTS. Обновите состояние.'}


async def xtts_service_action(action: str) -> dict:
    if action not in ('start', 'stop'):
        return {'ok': False, 'error': 'invalid_action', 'note': 'Разрешены только запуск и остановка XTTS.'}
    if _xtts_change.locked():
        return {'ok': False, 'error': 'busy', 'note': 'Предыдущая просьба ещё обрабатывается.'}
    async with _xtts_change:
        before = await xtts_service_state()
        if not before['available']:
            return before
        desired = ('active', 'activating') if action == 'start' else ('inactive', 'deactivating')
        if before['active_state'] in desired:
            return {'ok': True, 'accepted': True, 'changed': False, 'service': before}
        try:
            code, _ = await _xtts_systemctl('--no-block', action, XTTS_UNIT)
        except (OSError, asyncio.TimeoutError):
            return {'ok': False, 'error': 'unconfirmed', 'note': 'Результат передачи неизвестен. Обновите состояние перед повтором.'}
        if code != 0:
            return {'ok': False, 'note': 'Служба не приняла просьбу. Обновите состояние.', 'service': await xtts_service_state()}
        return {'ok': True, 'accepted': True, 'changed': True, 'service': await xtts_service_state()}

#: Что этой службе позволено трогать. Пусто — не позволено ничего, и она об этом
#: говорит: пустой список безопаснее догадки о том, что владелец имел в виду.
CONTAINERS = tuple(name.strip() for name in
                   (os.environ.get("DESKCTL_CONTAINERS") or "").split(",") if name.strip())

#: Роли мозга, которые вправе менять окно. Остальные ключи `llm.json` — не наше
#: дело: там ключи провайдеров и цены.
BRAIN_FIELDS = ("model", "fallback_model", "framework", "fallback_framework", "reasoning_effort", "service_tier")
IMAGE_DEFAULTS = {"enabled": False, "model": "gpt-image-2", "quality": "auto", "size": "auto", "background": "opaque"}


def image_settings(raw: dict) -> dict:
    value = raw.get("images") if isinstance(raw.get("images"), dict) else {}
    return {**IMAGE_DEFAULTS, **{key: value[key] for key in IMAGE_DEFAULTS if key in value}}


def validate_image_fields(fields: dict) -> dict:
    import re
    if not isinstance(fields, dict) or set(fields) - set(IMAGE_DEFAULTS):
        raise ValueError("неизвестные настройки изображений")
    change = {}
    for key, value in fields.items():
        if key == "enabled":
            if not isinstance(value, bool):
                raise ValueError("enabled: нужно true/false")
            change[key] = value
        elif isinstance(value, str):
            change[key] = value.strip()
        else:
            raise ValueError(f"{key}: нужна строка")
    if "model" in change and (not change["model"] or len(change["model"]) > 200):
        raise ValueError("нужна модель изображений")
    if "quality" in change and change["quality"] not in ("auto", "low", "medium", "high"):
        raise ValueError("качество: auto, low, medium или high")
    if "background" in change and change["background"] not in ("auto", "opaque", "transparent"):
        raise ValueError("фон: auto, opaque или transparent")
    if "size" in change and change["size"] != "auto" and not re.fullmatch(r"[1-9]\d{0,4}x[1-9]\d{0,4}", change["size"]):
        raise ValueError("размер: auto или ШИРИНАxВЫСОТА")
    return change


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


OWNER_TELEGRAM = "praxis-owner-telegram"
_owner_telegram_lock = asyncio.Lock()


async def owner_telegram_state() -> dict:
    if not allowed(OWNER_TELEGRAM):
        return {"available": False}
    code, blob = await _docker(f"/containers/{OWNER_TELEGRAM}/json")
    if code == 404:
        return {"available": False}
    if code != 200:
        return {"available": True, "state": "unavailable"}
    try:
        state = json.loads(blob)["State"]
        running = bool(state["Running"])
        health = state.get("Health") or {}
        status = str(state.get("Status") or "")
    except (KeyError, TypeError, ValueError):
        return {"available": True, "state": "unavailable"}
    if state.get("Restarting"):
        return {"available": True, "enabled": True, "state": "starting"}
    if not running:
        label = "error" if status == "dead" or state.get("ExitCode") not in (0, None, 137, 143) else "off"
    elif health.get("Status") == "healthy":
        label = "ready"
    else:
        logs = health.get("Log") or []
        reason = str(logs[-1].get("Output") or "").strip() if logs else ""
        if reason in {"login_required", "account_mismatch", "offline"}:
            label = reason
        else:
            label = "error" if health.get("Status") == "unhealthy" else "starting"
    return {"available": True, "enabled": running, "state": label}


async def owner_telegram_set(enabled: bool) -> dict:
    if not allowed(OWNER_TELEGRAM):
        return {"available": False, "ok": False}
    async with _owner_telegram_lock:
        verb = "start" if enabled else "stop?t=15"
        code, _ = await _docker(f"/containers/{OWNER_TELEGRAM}/{verb}", method="POST", timeout=25)
        result = await owner_telegram_state()
        return {**result, "ok": code in (204, 304) and result.get("enabled") == enabled}


# --- мозг ---------------------------------------------------------------------


def brain_path() -> Path:
    return TREE / "memory" / "llm.json"


# --- скоростная линия (fast / ultrafast), 22.11 ---------------------------------
#
# Диалект тиров у серверного канала и у продукта разный. Продукт (Hélène) живёт
# на «priority» Codex-подписки; серверный агент умеет собственную скоростную
# линию релея: service_tier fast | ultrafast | default в llm.json. Списки моде-
# лей берутся из ЖИВОГО llm.py дерева, а не из копии в пакете: они меняются
# вместе с агентом, без перевыкладки канала. Реклама линии — заголовок релея
# X-Praxis-Relay-Service-Tier, тот же, по которому маршрут решает сам агент.

_SPEED_TIERS = ("default", "fast", "ultrafast")
_SPEED_TTL = 300.0
_SPEED_CACHE: dict = {}   # (base_url, key_sha) -> (expires, advertised|None)
_SPEED_LISTS: dict = {}   # llm.py path -> (mtime, fast:set, ultrafast:set)


def _speed_lists() -> tuple[set, set]:
    """(fast, ultrafast) id моделей скоростной линии агента; пустые, когда неизвестны.

    Основной источник — memory/.state/speed-models.json: контейнер надзора
    видит из дерева только memory/, а списки меняются вместе с llm.py агента.
    Фолбэк — разбор живого llm.py (когда он виден, например локально)."""
    import re as _re
    dump = TREE / "memory" / ".state" / "speed-models.json"
    try:
        stat = dump.stat()
        hit = _SPEED_LISTS.get(str(dump))
        if hit and hit[0] == stat.st_mtime:
            return hit[1], hit[2]
        payload = json.loads(dump.read_text(encoding="utf8"))
        fast = {str(item) for item in (payload.get("fast") or [])}
        ultra = {str(item) for item in (payload.get("ultrafast") or [])}
        if fast or ultra:
            fast |= ultra
            _SPEED_LISTS[str(dump)] = (stat.st_mtime, fast, ultra)
            return fast, ultra
    except (OSError, ValueError):
        pass
    # Фолбэк: разобрать сам llm.py, если дерево смонтировано целиком.
    path = TREE / "llm.py"
    try:
        stat = path.stat()
        hit = _SPEED_LISTS.get(str(path))
        if hit and hit[0] == stat.st_mtime:
            return hit[1], hit[2]
        text = path.read_text(encoding="utf8", errors="replace")
        ultra, fast = set(), set()
        m = _re.search(r"_ULTRAFAST_MODELS\s*=\s*frozenset\(\{([^}]*)\}\)", text)
        if m:
            ultra = set(_re.findall(r'"([^"]+)"', m.group(1)))
        m = _re.search(r"_FAST_MODELS\s*=\s*_ULTRAFAST_MODELS\s*\|\s*\{([^}]*)\}", text)
        if not m:
            m = _re.search(r"_FAST_MODELS\s*=\s*(?:_ULTRAFAST_MODELS\s*\|\s*)?frozenset\(\{([^}]*)\}\)", text)
        if m:
            fast = set(_re.findall(r'"([^"]+)"', m.group(1)))
        fast |= ultra
        _SPEED_LISTS[str(path)] = (stat.st_mtime, fast, ultra)
        return fast, ultra
    except (OSError, ValueError):
        return set(), set()


def _speed_advertised(base_url: str, key: str):
    """Релей рекламирует скоростную линию? None — спросить не удалось."""
    import hashlib as _hl
    import time as _time
    import urllib.request as _urlreq
    base = str(base_url or "").rstrip("/")
    if not base.startswith("http"):
        return None
    binding = (base, _hl.sha256(str(key or "").encode()).hexdigest())
    now = _time.monotonic()
    hit = _SPEED_CACHE.get(binding)
    if hit and hit[0] > now:
        return hit[1]
    b = base[:-3] if base.endswith("/v1") else base
    url = b + "/v1/models"
    advertised = None
    try:
        req = _urlreq.Request(url, headers={"Authorization": f"Bearer {key}"})
        with _urlreq.urlopen(req, timeout=6) as answer:
            advertised = (answer.headers.get("X-Praxis-Relay-Service-Tier") == "1"
                          and answer.geturl() == req.full_url)
    except Exception:  # noqa: BLE001 — причина не важна, важен честный «не знаем»
        advertised = None
    _SPEED_CACHE[binding] = (now + _SPEED_TTL, advertised)
    return advertised


def _tier_available(raw: dict, role: str) -> bool:
    """Скоростная линия доступна роли: openai-роут + модель из списков + реклама релея."""
    spec = ((raw.get("roles") or {}).get(role) or {})
    if spec.get("framework") != "openai":
        return False
    model = str(spec.get("model") or "")
    fast, ultra = _speed_lists()
    if model not in fast and model not in ultra:
        return False
    fw = ((raw.get("frameworks") or {}).get("openai") or {})
    return _speed_advertised(fw.get("base_url"), fw.get("api_key")) is True


def _speed_tier_value(value):
    """'' / None / 'auto' → снять; 'priority' → 'fast' (старый диалект); иначе строгий список."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ValueError("service_tier должен быть строкой: default, fast или ultrafast")
    tier = value.strip().lower()
    if tier == "auto":
        return None
    if tier == "priority":
        tier = "fast"
    if tier not in _SPEED_TIERS:
        raise ValueError("service_tier должен быть default, fast или ultrafast")
    return tier


def _recent_tiers() -> dict:
    """Последний наблюдённый тир роли — из хвоста журнала вызовов агента, если он его пишет."""
    result: dict = {}
    try:
        with (TREE / "memory" / ".state" / "llm_calls.jsonl").open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 65536))
            for line in stream.read().decode("utf8", "replace").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("role") and "service_tier_actual" in row:
                    previous = result.get(row["role"]) or {}
                    if isinstance(previous.get("at"), (int, float)) and row.get("ts", 0) < previous.get("at", 0):
                        continue
                    result[row["role"]] = {"actual": row.get("service_tier_actual"),
                                          "requested": row.get("service_tier_requested"),
                                          "at": row.get("ts")}
    except OSError:
        pass
    return result


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
    observed = _recent_tiers()
    fast, ultra = _speed_lists()
    tiers = {}
    for name in roles:
        tiers[name] = {"requested": (raw.get("roles") or {}).get(name, {}).get("service_tier"),
                       "actual": (observed.get(name) or {}).get("actual"),
                       "available": _tier_available(raw, name),
                       "ultrafast": str((raw.get("roles") or {}).get(name, {}).get("model") or "") in ultra,
                       "codex": False, "speed_line": True}
    return {"ok": True, "path": str(path), "roles": roles, "frameworks": frameworks,
            "service_tiers": tiers,
            "images": image_settings(raw), "image_models": ["gpt-image-2"],
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
                advertised = (answer.headers.get("X-Praxis-Relay-Service-Tier") == "1"
                              and answer.geturl() == url)
            ids = [str(row.get("id")) for row in (body.get("data") or []) if row.get("id")]
            entry = {"ok": True, "models": ids, "url": url}
            if advertised:
                # Каталог со скоростными тирами: окно само прячет Fast/Ultrafast
                # для моделей, которых нет в списке живого llm.py.
                fast, ultra = _speed_lists()
                entry["codex"] = True
                entry["model_details"] = [
                    {"id": mid, "service_tiers": [{"id": tier} for tier in
                                                  (("fast", "ultrafast") if mid in ultra else ("fast",))]}
                    for mid in ids if mid in fast
                ]
            out["by_framework"][name] = entry
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
    if role == "images":
        try:
            change = validate_image_fields(fields)
        except ValueError as exc:
            return {"ok": False, "note": str(exc)}
        if not change:
            return {"ok": False, "note": "настройки изображений не изменены"}
        was = {key: image_settings(raw)[key] for key in change}
        backup = path.with_name(f"llm.json.before-images-{dt.datetime.now().strftime('%Y%m%dT%H%M%S%f')}")
        try:
            shutil.copy2(path, backup)
            raw["images"] = {**image_settings(raw), **change}
            tmp = path.with_name(".tmp-images-llm.json")
            tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError as exc:
            return {"ok": False, "note": f"настройки изображений не записались: {type(exc).__name__}"}
        receipt_warning = ""
        try:
            # Observable config change, without keys or image/prompt content.
            state = path.parent / ".state"; state.mkdir(exist_ok=True)
            with (state / "image_config_events.jsonl").open("a", encoding="utf-8") as events:
                events.write(json.dumps({"at": _utc(), "by": "owner:miniapp", "was": was, "now": change}, ensure_ascii=False) + "\n")
        except OSError as exc:
            receipt_warning = f"; запись события не удалась: {type(exc).__name__}"
        return {"ok": True, "role": role, "was": was, "now": change, "backup": backup.name,
                "note": "настройки изображений сохранены; следующий вызов использует их" + receipt_warning}
    roles = raw.get("roles")
    if not isinstance(roles, dict) or role not in roles:
        return {"ok": False, "note": f"роли «{role}» в мозге нет "
                                     f"(есть: {', '.join(sorted(roles or {}))})"}
    change = {key: value for key, value in (fields or {}).items()
              if key in BRAIN_FIELDS and isinstance(value, (str, int, float))}
    if "service_tier" in (fields or {}):
        try:
            change["service_tier"] = _speed_tier_value(fields.get("service_tier"))
        except ValueError as exc:
            return {"ok": False, "note": str(exc)}
    if not change:
        return {"ok": False, "note": f"менять нечего: разрешены {', '.join(BRAIN_FIELDS)}"}
    preview = dict(raw, roles={**roles, role: {**roles[role], **change}})
    route_changed = any(key in change and change[key] != roles[role].get(key)
                        for key in ("framework", "model"))
    if change.get("service_tier") in ("fast", "ultrafast") and not _tier_available(preview, role):
        return {"ok": False,
                "note": f"скоростная линия недоступна роли «{role}»: модель не из скоростного списка или релей её не рекламирует"}
    if (route_changed and "service_tier" not in change
            and str(roles[role].get("service_tier") or "").strip().lower() in ("fast", "ultrafast", "priority")
            and not _tier_available(preview, role)):
        change["service_tier"] = None
    was = {key: roles[role].get(key) for key in change}
    backup = path.with_name(f"llm.json.before-desk-{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}")
    try:
        shutil.copy2(path, backup)
        roles[role].update(change)
        if change.get("service_tier") is None:
            roles[role].pop("service_tier", None)
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
async def h_allowances(request: web.Request):
    from deskd import usage
    return web.json_response(await asyncio.to_thread(usage.allowances, TREE))


@guard
async def h_xtts_state(request: web.Request):
    return web.json_response(await xtts_service_state())


@guard
async def h_xtts_action(request: web.Request):
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({'ok': False, 'error': 'invalid_action'}, status=400)
    if not isinstance(body, dict) or set(body) != {'action'} or body['action'] not in ('start', 'stop'):
        return web.json_response({'ok': False, 'error': 'invalid_action'}, status=400)
    return web.json_response(await xtts_service_action(body['action']))


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
async def h_owner_telegram_state(request: web.Request):
    return web.json_response(await owner_telegram_state())


@guard
async def h_owner_telegram_set(request: web.Request):
    try:
        payload = await request.json()
    except (ValueError, TypeError):
        payload = None
    if not isinstance(payload, dict) or set(payload) != {"enabled"} or type(payload["enabled"]) is not bool:
        return web.json_response({"ok": False}, status=400)
    return web.json_response(await owner_telegram_set(payload["enabled"]))


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


@guard
async def h_praxis_state(request: web.Request):
    from deskd import praxis_control
    if not allowed('praxis'):
        return web.json_response(refuse('praxis'), status=403)
    return web.json_response(await praxis_control.state(TREE, _docker))


@guard
async def h_praxis_action(request: web.Request):
    from deskd import praxis_control
    if not allowed('praxis'):
        return web.json_response(refuse('praxis'), status=403)
    try:
        value = await request.json()
    except ValueError:
        return web.json_response({'ok': False, 'note': 'Некорректный запрос.'}, status=400)
    if not isinstance(value, dict):
        return web.json_response({'ok': False}, status=400)
    if value.get('action') == 'cancel' and set(value) == {'action', 'run_id'}:
        result = await praxis_control.cancel_run(TREE, value['run_id'])
    else:
        result = await praxis_control.action(TREE, _docker, value)
    return web.json_response(result)


def app() -> web.Application:
    server = web.Application()
    server.add_routes([
        web.get("/health", h_health),
        web.get("/state", h_state),
        web.get('/praxis/control', h_praxis_state),
        web.post('/praxis/control', h_praxis_action),
        web.get('/allowances', h_allowances),
        web.get('/services/xtts', h_xtts_state),
        web.post('/services/xtts', h_xtts_action),
        web.get('/services/owner-telegram', h_owner_telegram_state),
        web.post('/services/owner-telegram', h_owner_telegram_set),
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
    log.info("надзор за контейнерами на 0.0.0.0:%s, контейнеры: %s, дерево: %s",
             port, ", ".join(CONTAINERS) or "(пусто)", TREE)
    web.run_app(app(), host="0.0.0.0", port=port, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
