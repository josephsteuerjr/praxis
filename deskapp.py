# -*- coding: utf-8 -*-
"""Hélène — труба окна: читает дерево агента и отдаёт его окну. Референс — Claude Code.

Отдельный демон по форме Атланты: свой процесс, свой порт, дерево агента
смонтировано только на чтение. Пишет ровно в три места, и все три — её:
memory/.control/desk_inbox/ (сообщения владельца из окна и с телефона),
memory/.state/devices.json (спаренные телефоны) и маркдауны разрешённых групп
через /api/md.

Кто пускается (порядок проверок в auth_middleware):
  1. Host      — только IP-литерал, localhost, *.ts.net/*.local или HELENE_HOSTS;
  2. Origin    — пусто (не браузер), оболочка, свой же origin или HELENE_ORIGINS;
  3. ключ      — HELENE_TOKEN, ключ спаренного устройства, либо петля без токена;
  4. область   — ключ устройства ходит только по списку _DEVICE_PATHS.
Открыты до ключа ровно /m/* и /pair/redeem: телефон приходит по QR за ключом,
которого у него ещё нет.

Запуск:
  HELENE_TREE=/data HELENE_TOKEN=... python deskapp.py [port]
Ручки среды: HELENE_HOST, HELENE_PORT, HELENE_HOSTS, HELENE_ORIGINS, HELENE_CONFIG.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import hmac
import datetime as dt
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web
from aiohttp.abc import AbstractAccessLogger

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deskd import readers
from deskd import rooms
from deskd import usage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("frame.desk")

# UI окна — сборка Vite (app/dist); в экспортной поставке build_dist.py кладёт
# её в app/static. Старой папки static больше нет.
_HERE = Path(__file__).resolve().parent
STATIC = next((d for d in (_HERE / "static", _HERE / "app" / "dist", _HERE.parent / "app" / "dist")
               if (d / "index.html").is_file()), _HERE / "static")
TOKEN = (os.environ.get("HELENE_TOKEN") or os.environ.get("PRAXIS_DESK_TOKEN") or "").strip()
COOKIE = "desk_key"
_SSE_CLIENTS: set[asyncio.Queue] = set()
_WATCH_INTERVAL = 1.5

# ------------------------------------------------- кто вообще может стучаться
# ГЛАВНЫЙ ЗАМОК ЭТОГО ФАЙЛА, и раньше его не было.
#
# Труба верит петле без токена (HELENE_TOKEN не выставляет никто), а на все
# ответы вешала `Access-Control-Allow-Origin: *`. Значит ЛЮБАЯ страница в
# браузере владельца могла: прочитать memory/llm.json с ключом модели и всю
# переписку; выписать себе постоянный ключ устройства через /pair/new+/redeem;
# переписать soul/SOUL.md; и отправить POST /api/say с адресом комнаты —
# агент написал бы в рабочий чат Telegram ИМЕНЕМ ВЛАДЕЛЬЦА и продолжил бы
# отвечать на уточняющие вопросы.
#
# Замок из двух проверок, обе до всего остального:
#   Origin — браузер обязан его слать на всех не-GET/HEAD запросах и на
#            рукопожатии WebSocket (в no-cors тоже: там он либо чужой, либо
#            "null"), а читать ответ на GET страница может только в режиме
#            cors, который Origin тоже шлёт. Чужой Origin -> 403 и никакого
#            ACAO в ответе.
#   Host   — против перепривязки DNS: страница на evil.example, чей домен
#            указывает на 127.0.0.1, для браузера same-origin и Origin не
#            шлёт вовсе. Имя хоста должно быть IP-литералом или localhost.
#
# Токен это НЕ заменяет: полноценный замок — секрет от оболочки в HELENE_TOKEN
# от оболочки (shell/src/main.rs: spawn_child его НЕ ставит, поэтому TOKEN в
# продукте всегда пуст). Но Origin+Host закрывают весь браузерный класс, а его
# закрыть было нечем.

_SHELL_ORIGINS = {
    "http://tauri.localhost",      # Tauri 2 на Windows (WebView2), вшитая статика
    "https://tauri.localhost",
    "tauri://localhost",           # Tauri 2 на macOS/Linux
    # Свой протокол оболочки (0.3.0): окно читает app/static с диска и ходит
    # с origin helene.localhost — без этой строки труба отвечала 403 на всё,
    # и окно честно писало «Нет связи с харнессом» (найдено живьём 06.09).
    "http://helene.localhost",
    "https://helene.localhost",
    "helene://localhost",
    "http://localhost:5174",       # vite dev нашего же app/ (strictPort)
    "http://127.0.0.1:5174",
}
# Хосты оболочки: origin с таким именем пускается при любой схеме и порте.
_SHELL_HOSTNAMES = {"tauri.localhost", "helene.localhost"}
_ALLOWED_ORIGINS = _SHELL_ORIGINS | {
    o.strip().rstrip("/") for o in
    (os.environ.get("HELENE_ORIGINS") or "").split(",") if o.strip()
}
# Имена хостов сверх IP-литералов и localhost: Tailscale MagicDNS, mDNS и то,
# что владелец объявил сам (за Caddy на сервере — свой домен).
_ALLOWED_HOSTS = {h.strip().lower() for h in
                  (os.environ.get("HELENE_HOSTS") or "").split(",") if h.strip()}
_ALLOWED_HOST_SUFFIXES = (".ts.net", ".local")

# Пускается ДО ключа: телефон приходит по QR ровно за тем ключом, которого у
# него ещё нет. Без этого списка спаривание физически невозможно — телефон
# получал 403 на самой первой странице, и в Wi-Fi, и через Tailscale.
_OPEN_PATHS = {"/m", "/m/", "/m/manifest.webmanifest", "/pair/redeem",
               "/m/icon-192.png", "/m/icon-512.png", "/m/apple-touch-icon.png",
               # service worker регистрируется до ключа, как и сама /m/;
               # вход мини-аппа Telegram — по подписи initData, ключа ещё нет.
               "/m/sw.js", "/pair/telegram"}

# Область ключа устройства (телефона, мини-аппа). Всё остальное — только окну:
# ключ устройства уезжает в чужие руки легче всех (Wi-Fi, лог, чужая камера
# над плечом), а /api/anatomy отдаёт OPENAI_API_KEY и /api/md переписывает
# конституцию. Телефон в новом виде (КОНТРАКТ-B→A §4) показывает те же
# комнаты и «Действия», что окно: прогоны, ходы комнаты, пульс — читать
# можно, править конституцию, режим и анатомию — нет.
# /tunnel и /events пускаем: внутри канала область проверяется ещё раз, по
# каждому маршруту (_tunnel_dispatch), иначе телефон обошёл бы разбор прав.
_DEVICE_PATHS = {"/api/state", "/api/chats", "/api/say", "/api/health",
                 "/api/rooms", "/api/runs", "/api/pulse", "/api/usage", "/api/allowances", "/tunnel", "/events"}
_DEVICE_PREFIXES = ("/api/chat/", "/api/rooms/", "/api/chat-turns/", "/api/run/")


def _hostname(raw: str) -> str:
    raw = str(raw or "").strip()
    if raw.startswith("["):                      # [::1]:8094
        return raw[1:raw.find("]")] if "]" in raw else raw[1:]
    return raw.rsplit(":", 1)[0] if ":" in raw else raw


def _host_ok(request: web.Request) -> bool:
    raw = request.headers.get("Host") or ""
    if not raw:
        return True                              # HTTP/1.0 без Host — не браузер
    name = _hostname(raw).lower()
    if name in ("localhost", "") or name in _ALLOWED_HOSTS:
        return True
    if name.endswith(_ALLOWED_HOST_SUFFIXES):
        return True
    try:
        import ipaddress
        ipaddress.ip_address(name)
        return True                              # телефон приходит по IP
    except ValueError:
        return False


def _origin_ok(request: web.Request) -> tuple[bool, str]:
    """(пустить, какой Origin эхом). Пустой Origin — не браузер, пускаем."""
    origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    if not origin:
        return True, ""
    if origin in _ALLOWED_ORIGINS:
        return True, origin
    # tauri.localhost — внутренний хост оболочки, из веба на него не попасть.
    # По имени, а не по точной строке: Tauri может отдавать окно и по http, и
    # по https (useHttpsScheme), а неверная строка означала бы пустое окно.
    if _hostname(origin.split("//", 1)[-1]).lower() in _SHELL_HOSTNAMES:
        return True, origin
    # свой же origin: страница /m/ телефона стучится туда, откуда загрузилась.
    # Сравниваем host:port, а не схему: за Caddy наружу https, внутрь http.
    host = (request.headers.get("Host") or "").strip()
    if host and origin.split("//", 1)[-1] == host:
        return True, origin
    return False, origin


def _cors(origin: str) -> dict:
    """CORS ровно для одного разрешённого origin. Было `*` на всех ответах."""
    if not origin:
        return {"Vary": "Origin"}
    return {"Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Vary": "Origin"}


# ------------------------------------------------------------- телефон
# Спаривание по QR: окно просит одноразовую пару (с петли), телефон открывает
# ссылку /m/?pair=<токен> и меняет токен на свой ключ устройства. Токен живёт
# десять минут и годится ДВАЖДЫ: на iPhone страница в Safari и установленное
# на экран «Домой» приложение — разные хранилища, и второй обмен нужен ровно
# для него. Ключи устройств лежат хэшами в memory/.state/devices.json.

_PAIRS: dict[str, dict] = {}
_PAIR_TTL = 600
_PAIR_USES = 2


def _devices_path() -> Path:
    return readers.tree() / "memory" / ".state" / "devices.json"


_DEVICES_CACHE: dict = {"stamp": None, "rows": []}
_DEVICES_LOCK = threading.Lock()


def _devices() -> list[dict]:
    """Список устройств с кэшем по mtime: файл читался с диска синхронно в
    event loop и ДВАЖДЫ за каждый запрос (из _role и из переустановки cookie),
    а телефон опрашивает трубу раз в 6-8 секунд."""
    path = _devices_path()
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    if _DEVICES_CACHE["stamp"] == stamp:
        return _DEVICES_CACHE["rows"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = [d for d in data.get("devices", []) if isinstance(d, dict)]
    except (OSError, ValueError):
        rows = []
    _DEVICES_CACHE["rows"] = rows
    _DEVICES_CACHE["stamp"] = stamp
    return rows


def _save_devices(rows: list[dict]) -> None:
    path = _devices_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".tmp-" + path.name)
    tmp.write_text(json.dumps({"devices": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    _DEVICES_CACHE["stamp"] = None      # перечитать на следующем обращении


def _key_hash(key: str) -> str:
    import hashlib
    return hashlib.sha256(str(key or "").encode("utf-8")).hexdigest()


_DEVICE_SEEN: dict[str, float] = {}


def _device_ok(supplied: str) -> bool:
    if not supplied:
        return False
    h = _key_hash(supplied)
    for row in _devices():
        if secrets.compare_digest(h, str(row.get("hash") or "")):
            _DEVICE_SEEN[h] = time.time()   # сторож сольёт на диск не чаще раза в 5 мин
            return True
    return False


def _flush_device_seen() -> None:
    """«Последний раз выходил на связь» в списке устройств. Пишется редко: без
    этого владелец не мог отличить свой телефон от угнанного ключа — в карточке
    были только имя из User-Agent и дата."""
    if not _DEVICE_SEEN:
        return
    seen = dict(_DEVICE_SEEN)
    with _DEVICES_LOCK:
        rows = list(_devices())
        changed = False
        for row in rows:
            stamp = seen.get(str(row.get("hash") or ""))
            if stamp:
                row["last_seen"] = dt.datetime.fromtimestamp(
                    stamp, dt.timezone.utc).isoformat(timespec="seconds")
                changed = True
        if changed:
            try:
                _save_devices(rows)
            except OSError:
                log.debug("last_seen не записался", exc_info=True)


def _is_loopback(request: web.Request) -> bool:
    peer = request.transport.get_extra_info("peername") if request.transport else None
    host = str(peer[0]) if peer else ""
    return host in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def _role(request: web.Request) -> str:
    """"owner" — окно/эта машина, "device" — спаренный телефон, "" — никто."""
    supplied = request.query.get("key") or request.cookies.get(COOKIE) or ""
    if TOKEN and supplied:
        try:
            if secrets.compare_digest(supplied, TOKEN):
                return "owner"
        except TypeError:
            # compare_digest на не-ASCII строке бросает TypeError мимо всех
            # except'ов middleware: `?key=привет` при заданном токене отдавал
            # 500 на ЛЮБОМ маршруте. Не наш ключ — это «не наш ключ», а не
            # авария трубы.
            pass
    if _device_ok(supplied):
        return "device"
    if not TOKEN and _is_loopback(request):
        return "owner"  # своя машина без токена — как раньше
    return ""


def _scope_ok(role: str, path: str) -> bool:
    if role == "owner":
        return True
    if role != "device":
        return False
    return path in _DEVICE_PATHS or path.startswith(_DEVICE_PREFIXES)


def _open_path(path: str) -> bool:
    return path in _OPEN_PATHS or path.startswith("/m/assets/")


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not _host_ok(request):
        log.warning("отказ по Host: %r", request.headers.get("Host"))
        return web.Response(status=403, text="чужой адрес")
    origin_ok, origin = _origin_ok(request)
    if not origin_ok:
        log.warning("отказ по Origin: %r -> %s", origin, request.path)
        return web.Response(status=403, text="чужая страница")
    cors = _cors(origin)
    if request.method == "OPTIONS":  # CORS preflight нативной оболочки — без ключа
        return web.Response(status=204, headers=cors)
    role = "owner" if _open_path(request.path) else _role(request)
    if not role:
        return web.Response(status=403, text="нет ключа", headers=cors)
    if not _open_path(request.path) and not _scope_ok(role, request.path):
        return web.Response(status=403, headers=cors,
                            text="телефону сюда нельзя — это делают из окна")
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        # Раньше CORS дописывался ТОЛЬКО к успешному ответу: 400/409/404
        # уезжали голыми, браузер отбрасывал их по CORS, и вместо внятного
        # «руннер не поднят — запусти локальный харнесс и повтори» владелец
        # видел «Не ушло: Failed to fetch».
        exc.headers.update(cors)
        return exc
    response.headers.update(cors)
    supplied = request.query.get("key") or ""
    if supplied and ((TOKEN and supplied == TOKEN) or _device_ok(supplied)):
        response.set_cookie(COOKIE, supplied, max_age=365 * 24 * 3600,
                            httponly=True, samesite="Lax")
    return response


# ------------------------------------------------------- разбор параметров

def _int_arg(query, name: str, default: int, lo: int, hi: int) -> int:
    """Целое из query с честным 400 вместо 500 и с потолком СНИЗУ тоже.

    Было `min(200, int(...))`: `?limit=abc` роняло ручку в 500 с трассировкой в
    лог, а `?n=-1` обходило объявленный потолок (`lines[-n:]` при n=-1 отдаёт
    весь буфер) и вывозило до 4 МБ переписки одним запросом.
    """
    raw = query.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=f"{name}: нужно целое число, а не {raw!r}")
    return max(lo, min(hi, value))


async def _json_body(request: web.Request):
    """Тело POST с честным 400. Кривое тело давало 500 и трассировку в лог."""
    try:
        return await request.json()
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="ожидается JSON")


def _new_pair() -> dict:
    now = time.time()
    for token, row in list(_PAIRS.items()):
        if row["expires"] < now or row["uses"] <= 0:
            _PAIRS.pop(token, None)
    token = secrets.token_urlsafe(24)
    _PAIRS[token] = {"expires": now + _PAIR_TTL, "uses": _PAIR_USES}
    return {"token": token, "path": "/m/?pair=" + token,
            "expires_in": _PAIR_TTL, "uses": _PAIR_USES}


def _revoke_device(wanted: str) -> dict:
    """Отвязать устройство. Снимается ВСЯ пара, а не одна её половина: один QR
    рождал две строки (второй обмен для значка на «Домой» на iPhone), владелец
    видел два одинаковых «iPhone · 03.09.2026», жал «Отвязать» на одной — и
    телефон продолжал работать по второму ключу. Это дыра в отзыве."""
    with _DEVICES_LOCK:
        rows = _devices()
        pairs = {str(d.get("pair") or "") for d in rows
                 if str(d.get("id")) == str(wanted or "") and d.get("pair")}
        left = [d for d in rows
                if str(d.get("id")) != str(wanted or "")
                and not (d.get("pair") and str(d.get("pair")) in pairs)]
        _save_devices(left)
    return {"left": len(left)}


def _device_rows() -> list[dict]:
    return [{k: d.get(k) for k in ("id", "name", "created", "addr", "last_seen")}
            for d in _devices()]


def _redeem(token: str, ua: str, addr: str) -> dict:
    """Обмен токена пары на ключ устройства.

    Тот же токен -> ТОТ ЖЕ ключ и та же строка: два обмена (Safari и значок на
    «Домой») — это одно устройство в двух хранилищах, а не два устройства.
    Списание использования — ПОСЛЕ успешной записи: раньше `uses -= 1` стоял до
    неё, и при занятом файле или дереве только на чтение использование сгорало,
    ключ не сохранялся, а владелец видел «Код устарел» без объяснения.
    """
    with _DEVICES_LOCK:
        row = _PAIRS.get(token)
        if not row or row["expires"] < time.time() or row["uses"] <= 0:
            raise web.HTTPForbidden(
                text="код устарел или уже использован — покажи QR заново")
        if row.get("key"):
            row["uses"] -= 1
            return {"key": row["key"], "uses_left": row["uses"]}
        key = "dk-" + secrets.token_urlsafe(30)
        kind = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua
                else "Android" if "Android" in ua else "телефон")
        rows = list(_devices())
        rows.append({"id": secrets.token_hex(6), "pair": _key_hash(token)[:16],
                     "name": kind, "hash": _key_hash(key), "addr": addr,
                     "created": dt.datetime.now(dt.timezone.utc)
                     .isoformat(timespec="seconds"),
                     "last_seen": dt.datetime.now(dt.timezone.utc)
                     .isoformat(timespec="seconds")})
        try:
            _save_devices(rows)
        except OSError:
            log.warning("устройство не записалось", exc_info=True)
            raise web.HTTPServiceUnavailable(
                text="не смог записать устройство — проверь, что папка данных доступна")
        row["key"] = key
        row["uses"] -= 1
        return {"key": key, "uses_left": row["uses"]}


async def api_pair_redeem(request):
    """Телефон меняет токен на ключ устройства. Токен годится дважды (iPhone)."""
    token = str(request.query.get("token") or "")
    peer = request.transport.get_extra_info("peername") if request.transport else None
    got = await asyncio.to_thread(
        _redeem, token, str(request.headers.get("User-Agent") or ""),
        str(peer[0]) if peer else "")
    response = _json({"key": got["key"], "agent": _agent_name(),
                      "uses_left": got["uses_left"]})
    response.set_cookie(COOKIE, got["key"], max_age=365 * 24 * 3600,
                        httponly=True, samesite="Lax")
    return response


# ------------------------------------------------------------- PWA /m/
_HERE_DIR = Path(__file__).resolve().parent
MOBILE = next((d for d in (_HERE_DIR / "mobile", _HERE_DIR.parent / "mobile" / "dist")
               if (d / "index.html").is_file()), _HERE_DIR / "mobile")


async def mobile_index(request):
    """Страница телефона и мини-аппа. `no-store`: WebView Telegram и PWA на
    экране «Домой» живут неделями, и HTTP-кэш не должен подменять сетевой
    ответ старым index.html — офлайн-копией управляет только service worker,
    который знает штамп своей сборки (приём из hardbot, слово владельца 07.09;
    сама страница сверяет свою сборку с серверной — ui-kit/version.ts)."""
    if not (MOBILE / "index.html").is_file():
        raise web.HTTPNotFound(text="мобильная страница не собрана")
    return web.FileResponse(MOBILE / "index.html", headers={
        "Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})


async def mobile_sw(request):
    """Service worker телефона (`/m/sw.js`, КОНТРАКТ-B→A §5): лежит в корне
    области `/m/`, отдаётся с `no-cache`, чтобы новая сборка SW подхватывалась
    браузером при следующем открытии."""
    if not (MOBILE / "sw.js").is_file():
        raise web.HTTPNotFound(text="service worker не собран")
    return web.FileResponse(MOBILE / "sw.js", headers={
        "Cache-Control": "no-cache", "Content-Type": "text/javascript; charset=utf-8"})


def _agent_name() -> str:
    """Имя агента для телефона и мини-аппа (КОНТРАКТ-B→A §9): снимок анатомии
    → `agent.name` из helene.json → `HELENE_AGENT_NAME` (сервер без конфига
    продукта) → «Агент». У дерева без снимка Hélène (Пульт Праксис) первого
    источника нет — телефон получал «Агент»."""
    anatomy = readers.anatomy() or {}
    return str(anatomy.get("agent_name") or readers.product_config().get("agent_name")
               or os.environ.get("HELENE_AGENT_NAME") or "").strip() or "Агент"


# ---------------------------------------------------- вход мини-аппа Telegram

def _tg_tokens() -> list[str]:
    """Токены ботов, из которых открывается мини-апп: `telegram.bot_token`
    конфига и `HELENE_TG_BOT_TOKENS` (список через запятую — у Праксис два
    бота открывают один мини-апп). В чужой `.env` не лазим."""
    tokens: list[str] = []
    cfg_path = readers.config_path()
    if cfg_path is not None:
        raw = readers._load_json(cfg_path)
        token = str((raw.get("telegram") or {}).get("bot_token") or "").strip()
        if token:
            tokens.append(token)
    for token in (os.environ.get("HELENE_TG_BOT_TOKENS") or "").split(","):
        if token.strip() and token.strip() not in tokens:
            tokens.append(token.strip())
    return tokens


def _tg_owner_id() -> str:
    cfg_path = readers.config_path()
    owner = ""
    if cfg_path is not None:
        raw = readers._load_json(cfg_path)
        owner = str((raw.get("telegram") or {}).get("owner_id") or "").strip()
    if owner in ("", "0"):
        owner = str(os.environ.get("HELENE_TG_OWNER_ID") or "").strip()
    return "" if owner == "0" else owner


def _telegram_init_user(init_data: str, tokens: list[str], *, max_age: float = 7 * 86400,
                        now: float | None = None) -> dict:
    """Пользователь из `Telegram.WebApp.initData` — по подписи, иначе ValueError словами.

    Алгоритм Telegram Web Apps: `secret = HMAC_SHA256("WebAppData", bot_token)`,
    `hash == HMAC_SHA256(secret, data_check_string)`, где строка — пары
    `key=value` (без `hash`) через `\\n` в порядке ключей. Подпись сверяется с
    каждым токеном по очереди; `auth_date` не старше `max_age`.
    """
    from urllib.parse import parse_qsl
    data = dict(parse_qsl(str(init_data or ""), keep_blank_values=True))
    given = str(data.pop("hash", "") or "")
    if not given or not data:
        raise ValueError("нет подписи Telegram")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    for token in tokens:
        secret = hmac.new(b"WebAppData", str(token).encode("utf-8"), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(calc, given.lower()):
            break
    else:
        raise ValueError("подпись Telegram не сошлась")
    try:
        auth_date = int(data.get("auth_date") or 0)
    except (TypeError, ValueError):
        auth_date = 0
    if (now if now is not None else time.time()) - auth_date > max_age:
        raise ValueError("вход устарел — открой мини-апп заново")
    try:
        user = json.loads(data.get("user") or "{}")
    except ValueError:
        user = {}
    if not isinstance(user, dict) or not user.get("id"):
        raise ValueError("в подписи нет пользователя")
    return user


def _tg_login(user: dict, addr: str) -> dict:
    """Ключ устройства для мини-аппа — той же природы, что у QR (`_redeem`).
    Одно устройство на одного пользователя Telegram: прежняя строка того же
    `pair` заменяется, иначе каждое открытие мини-аппа плодило бы строку в
    списке устройств владельца."""
    key = "dk-" + secrets.token_urlsafe(30)
    pair = "tg:" + str(user.get("id"))
    name = "Telegram · " + (str(user.get("first_name") or user.get("username") or "").strip()
                            or str(user.get("id")))
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with _DEVICES_LOCK:
        rows = [d for d in _devices() if str(d.get("pair") or "") != pair]
        rows.append({"id": secrets.token_hex(6), "pair": pair, "name": name,
                     "hash": _key_hash(key), "addr": addr, "created": stamp,
                     "last_seen": stamp})
        _save_devices(rows)
    return {"key": key, "name": name}


async def api_pair_telegram(request):
    """`POST /pair/telegram {initData}` → `{key, agent}` (КОНТРАКТ-B→A §6).
    Только владельцу (`telegram.owner_id` или `HELENE_TG_OWNER_ID`); чужому —
    403 словами. Открыт до ключа: ключа у мини-аппа ещё нет."""
    payload = await _json_body(request) or {}
    tokens = _tg_tokens()
    if not tokens:
        raise web.HTTPServiceUnavailable(
            text="у канала нет токена бота — вход из Telegram не настроен")
    try:
        user = _telegram_init_user(str(payload.get("initData") or ""), tokens)
    except ValueError as exc:
        raise web.HTTPForbidden(text=str(exc))
    owner = _tg_owner_id()
    if not owner or str(user.get("id")) != owner:
        raise web.HTTPForbidden(text="этот мини-апп открыт только владельцу агента")
    peer = request.transport.get_extra_info("peername") if request.transport else None
    got = await asyncio.to_thread(_tg_login, user, str(peer[0]) if peer else "")
    response = _json({"key": got["key"], "agent": _agent_name(), "device": got["name"]})
    response.set_cookie(COOKIE, got["key"], max_age=365 * 24 * 3600,
                        httponly=True, samesite="Lax")
    return response


async def mobile_manifest(request):
    """Манифест PWA: start_url несёт токен пары, чтобы установленное на экран
    «Домой» приложение могло обменять его второй раз (iPhone)."""
    pair = str(request.query.get("pair") or "")
    name = _agent_name()
    start = "/m/?pair=" + pair if pair else "/m/"
    manifest = {
        "name": name, "short_name": name, "start_url": start, "scope": "/m/",
        "display": "standalone", "background_color": "#fdfcfa", "theme_color": "#fdfcfa",
        "icons": [{"src": "/m/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/m/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }
    # no-store обязателен: манифест — единственное место, где живёт токен пары,
    # и без заголовков свежести браузер вправе держать его сколько угодно.
    # Закэшированный манифест удерживал бы протухший токен и делал смерть значка
    # на «Домой» неизлечимой даже после переустановки значка (та же мина липкого
    # кэша, из-за которой в окне появился штамп ?v=<mtime>).
    return web.json_response(manifest, content_type="application/manifest+json",
                             headers={"Cache-Control": "no-store"},
                             dumps=lambda d: json.dumps(d, ensure_ascii=False))


class SafeAccessLogger(AbstractAccessLogger):
    """Access-log БЕЗ строки запроса с query.

    Формат aiohttp по умолчанию — `%r`, то есть «GET /api/health?key=dk-… HTTP/1.1».
    Телефон шлёт свой постоянный ключ в query каждые 6-8 секунд, а /pair/redeem
    несёт токен пары; оболочка заворачивает вывод ребёнка в <дерево>/deskapp.log,
    и этот же файл кнопка «Собрать логи для поддержки» кладёт в zip, который
    владелец отправляет постороннему. helene.json в архив осознанно НЕ берут
    (там ключ модели) — а лог с тысячами копий ключа устройства брали.
    Подменить атом %r нельзя: compile_format берёт методы из БАЗОВОГО класса,
    поэтому пишем строку сами. Путь без query, остальное как было.
    """

    def log(self, request, response, time) -> None:
        if not self.logger.isEnabledFor(logging.INFO):
            return
        self.logger.info('%s "%s %s" %s %s', request.remote or "-",
                         request.method, request.path, response.status,
                         response.body_length)


def _json(data) -> web.Response:
    return web.json_response(data, dumps=lambda d: json.dumps(d, ensure_ascii=False))


# ------------------------------------------------------------------ API
#
# ОДНА таблица маршрутов на оба канала (ревью 06.09, §5; задача A п. 1.11).
# Окно ходит по каналу (WebSocket /tunnel), браузер и телефон — по HTTP. До
# 07.09 у каждой двери был свой список ручек, и ручка, заведённая в одном
# списке, в другом отвечала 404: так было с /api/home (единственной из
# двадцати), потом с /api/mode. Теперь ручка — это строка в ROUTES: aiohttp-
# роутер и диспетчер канала строятся из неё, и «есть в одном канале из двух»
# стало невозможно по построению (стенд: tests/t_routes.py).
#
# Обработчик получает обезличенный запрос (Call) и возвращает ТЕЛО ответа —
# то, что уедет json-ом, — либо бросает web.HTTPError (404/400/409 словами),
# либо возвращает Fail, когда ответу нужен ещё и `code` (конфликт mtime).
# Оба канала переводят это в свою форму одинаково.

@dataclasses.dataclass
class Call:
    match: dict            # параметры пути: {peer}, {stream}, {run_id}
    query: dict            # query-строка, последнее значение на ключ
    body: Any              # разобранный JSON тела POST/DELETE или None
    local: bool            # запрос с этой машины (петля)
    role: str              # owner | device


@dataclasses.dataclass
class Fail:
    status: int
    error: str
    code: str = ""


@dataclasses.dataclass(frozen=True)
class Route:
    method: str
    path: str              # aiohttp-шаблон: "/api/chat/{peer}"
    handler: Callable[[Call], Awaitable[Any]]
    local_only: bool = False   # только с этой машины (окно): пары телефона


def _reader(fn: Callable[[], Any]):
    """Ручка-читатель без параметров: `readers.<имя>` в потоке."""
    async def handler(call: Call):
        return await asyncio.to_thread(fn)
    return handler


async def _r_runs(c: Call):
    return await asyncio.to_thread(readers.list_runs, _int_arg(c.query, "limit", 80, 1, 200),
                                   c.query.get("kind") or "", c.query.get("before") or "")


async def _r_run(c: Call):
    detail = await asyncio.to_thread(readers.run_detail, c.match["run_id"])
    if not detail:
        raise web.HTTPNotFound(text="нет такого прогона")
    return detail


async def _r_frame_stats(c: Call):
    return await asyncio.to_thread(readers.frame_cuts, _int_arg(c.query, "days", 7, 1, 90))


async def _r_spend(c: Call):
    # Ревизия расхода по чатам, людям и задачам (deskd/spend.py) — из журнала
    # вызовов и манифестов прогонов, дерево не трогает.
    from deskd import spend
    return await asyncio.to_thread(spend.collect, readers.tree(), _int_arg(c.query, "days", 7, 1, 90))


async def _r_run_result(c: Call):
    payload = await asyncio.to_thread(readers.run_result, c.match["run_id"], c.match["result_id"])
    if not payload:
        raise web.HTTPNotFound(text="нет такого результата")
    return payload


async def _r_shadow_captures(c: Call):
    return await asyncio.to_thread(readers.shadow_captures, c.match["stream"])


async def _r_shadow_capture(c: Call):
    name = c.query.get("name") or ""
    text = await asyncio.to_thread(readers.shadow_capture, c.match["stream"], name)
    return {"stream": c.match["stream"], "name": name, "text": text}


async def _r_shadow_diff(c: Call):
    return await asyncio.to_thread(readers.shadow_diff, c.match["stream"],
                                   c.query.get("old") or "", c.query.get("new") or "")


async def _r_shadow_metrics(c: Call):
    stream = c.query.get("stream") or c.match.get("stream") or ""
    return await asyncio.to_thread(readers.shadow_metrics, 120, stream)


async def _r_chats(c: Call):
    return await asyncio.to_thread(readers.chats)


async def _r_chat(c: Call):
    return await asyncio.to_thread(readers.chat_tail, c.match["peer"],
                                   _int_arg(c.query, "n", 200, 1, 600))


async def _r_chat_turns(c: Call):
    return await asyncio.to_thread(readers.chat_turns, c.match["peer"],
                                   _int_arg(c.query, "n", 120, 1, 300))


async def _r_md(c: Call):
    return await asyncio.to_thread(readers.safe_read_md, c.query.get("path") or "")


async def _r_md_write(c: Call):
    """Правка маркдауна агента из окна: конституция, навыки, заметки.

    Путь только внутри разрешённых групп (readers.safe_write_md), только .md,
    запись атомарная. Окно возвращает mtime_ns, который получило при чтении,
    и при расхождении ручка отвечает 409 (code=conflict), а не переписывает
    молча чужую правку — владелец правит SOUL.md в окне, агент правит его же
    своей рукой, и раньше выигрывал тот, кто записал последним.
    """
    body = c.body or {}
    result = await asyncio.to_thread(readers.safe_write_md, str(body.get("path") or ""),
                                     str(body.get("text") or ""), body.get("mtime_ns"))
    if result.get("error"):
        return Fail(409 if result.get("code") == "conflict" else 400,
                    result["error"], str(result.get("code") or ""))
    return result


async def _r_home(c: Call):
    """Чьё это дерево. Оболочка спрашивает перед тем, как признать живой на
    порту харнесс своим: осиротевший процесс прежней установки держал порт, и
    новое окно молча показывало чужого агента."""
    return {"tree": str(readers.tree().resolve())}


async def _r_mode(c: Call):
    """Ограда (песочница | интерактивный) и ОТДЕЛЬНО от неё служба.

    Два независимых ответа в одном теле: `name`/`choices` — какая ограда,
    `service_installed`/`session0`/`firewall`/`service` — что со службой. Служба
    режимом не является: она ставится поверх любой ограды и ограду не снимает
    (склейка этих двух измерений и была P0 первой редакции). Плюс опция
    `computer` с живым снимком тела и живые просьбы о папках (`mounts_live`).
    """
    return await asyncio.to_thread(readers.mode_state)


async def _r_say(c: Call):
    body = c.body or {}
    return await _say(body.get("text"), body.get("chat") or "",
                      attachments=body.get("attachments"))


async def _r_rooms_create(c: Call):
    return await asyncio.to_thread(_room_op, rooms.create, (c.body or {}).get("title"))


async def _r_rooms_rename(c: Call):
    return await asyncio.to_thread(_room_op, rooms.rename, c.match["peer"],
                                   (c.body or {}).get("title"))


async def _r_rooms_delete(c: Call):
    return await asyncio.to_thread(_room_op, rooms.delete, c.match["peer"])


def _room_op(fn, *args):
    """Комнаты окна (задача A §3): отказ реестра — словами и кодом, не 500."""
    try:
        return fn(readers.tree(), *args)
    except rooms.RoomError as exc:
        return Fail(exc.status, str(exc), "room")


async def _r_pair_new(c: Call):
    return _new_pair()


async def _r_devices(c: Call):
    return _device_rows()


async def _r_revoke(c: Call):
    return _revoke_device(str((c.body or {}).get("id") or ""))


ROUTES: tuple[Route, ...] = (
    Route("GET", "/api/runs", _r_runs),
    Route("GET", "/api/run/{run_id}", _r_run),
    Route("GET", "/api/run/{run_id}/result/{result_id}", _r_run_result),
    Route("GET", "/api/frame-stats", _r_frame_stats),
    Route("GET", "/api/spend", _r_spend),
    Route("GET", "/api/pulse", _reader(lambda: readers.pulse())),
    Route("GET", "/api/usage", _reader(lambda: usage.statistics(readers.tree()))),
    Route("GET", "/api/allowances", _reader(lambda: usage.allowances(readers.tree()))),
    Route("GET", "/api/errors", _reader(lambda: readers.errors())),
    Route("GET", "/api/board", _reader(lambda: readers.board())),
    Route("GET", "/api/agenda", _reader(lambda: readers.agenda())),
    Route("GET", "/api/forge", _reader(lambda: readers.forge_tasks())),
    Route("GET", "/api/shadow", _reader(lambda: readers.shadow_streams())),
    Route("GET", "/api/shadow/{stream}/captures", _r_shadow_captures),
    Route("GET", "/api/shadow/{stream}/capture", _r_shadow_capture),
    Route("GET", "/api/shadow/{stream}/diff", _r_shadow_diff),
    Route("GET", "/api/shadow/{stream}/metrics", _r_shadow_metrics),
    Route("GET", "/api/shadow-metrics", _r_shadow_metrics),
    Route("GET", "/api/chats", _r_chats),
    Route("GET", "/api/chat/{peer}", _r_chat),
    Route("GET", "/api/chat-turns/{peer}", _r_chat_turns),
    Route("GET", "/api/md", _r_md),
    Route("POST", "/api/md", _r_md_write),
    Route("GET", "/api/md-tree", _reader(lambda: readers.md_tree())),
    # Обе ручки окно и телефон опрашивают каждые несколько секунд; рубеж от
    # 500-х стоит ВНУТРИ readers.state/readers.health — он общий на оба канала.
    Route("GET", "/api/health", _reader(lambda: readers.health())),
    Route("GET", "/api/state", _reader(lambda: readers.state())),
    Route("GET", "/api/mode", _r_mode),
    Route("GET", "/api/home", _r_home),
    Route("GET", "/api/anatomy", _reader(lambda: readers.anatomy())),
    Route("POST", "/api/say", _r_say),
    # Комнаты окна (задача A §3): создать, переименовать, убрать в архив.
    Route("POST", "/api/rooms", _r_rooms_create),
    Route("POST", "/api/rooms/{peer}", _r_rooms_rename),
    Route("DELETE", "/api/rooms/{peer}", _r_rooms_delete),
    # Телефон: пары выдаёт и отзывает только окно на этой машине.
    Route("POST", "/pair/new", _r_pair_new, local_only=True),
    Route("GET", "/pair/devices", _r_devices, local_only=True),
    Route("POST", "/pair/revoke", _r_revoke, local_only=True),
)


def _route_regex(path: str) -> "re.Pattern[str]":
    return re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")


_ROUTE_INDEX: tuple[tuple[Route, "re.Pattern[str]"], ...] = tuple(
    (route, _route_regex(route.path)) for route in ROUTES)


def match_route(method: str, path: str) -> tuple[Route | None, dict]:
    """Строка таблицы для метода и пути канала. (None, {}) — нет такого пути."""
    method = str(method or "GET").upper()
    for route, pattern in _ROUTE_INDEX:
        found = pattern.match(path)
        if found and route.method == method:
            return route, dict(found.groupdict())
    return None, {}


def _http_handler(route: Route):
    """HTTP-дверь к строке таблицы: aiohttp-запрос -> Call -> json-ответ."""
    async def handler(request: web.Request):
        if route.local_only and not _is_loopback(request):
            raise web.HTTPForbidden(text="только с этой машины")
        body = None
        if route.method in ("POST", "DELETE") and request.can_read_body:
            body = await _json_body(request)
        call = Call(match=dict(request.match_info),
                    query={k: request.query.get(k) for k in request.query},
                    body=body, local=_is_loopback(request), role=_role(request))
        result = await route.handler(call)
        if isinstance(result, Fail):
            # Текстом, как прежние HTTPConflict/HTTPBadRequest: клиенты читают
            # причину из тела ответа словами.
            return web.Response(status=result.status, text=result.error)
        return _json(result)
    return handler


_SAY_RE = re.compile(r"[^\w\-]+")


_CHAT_KEY_RE = re.compile(r"^-?\d+(?:__topic__\d+)?$")


_ATTACH_MAX_FILES = 4
_ATTACH_MAX_BYTES = 8 * 1024 * 1024
_ATTACH_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                "image/gif": ".gif"}


def _attachments_in(raw) -> list[dict]:
    """Вложения из тела запроса: `[{name, mime, data(base64)}]` -> проверенные байты.

    Только картинки (те, что читает модель — см. `_MODEL_IMAGE_MIME` в дереве), до
    четырёх, до 8 МБ каждая. Всё остальное — отказ словами: окно показало бы
    «отправлено», а руннер молча выбросил бы файл, который модель не прочтёт.
    """
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise web.HTTPBadRequest(text="attachments должен быть списком")
    if len(raw) > _ATTACH_MAX_FILES:
        raise web.HTTPBadRequest(text=f"не больше {_ATTACH_MAX_FILES} вложений за раз")
    out: list[dict] = []
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise web.HTTPBadRequest(text=f"вложение #{i}: не объект")
        mime = str(item.get("mime") or "").strip().lower()
        if mime not in _ATTACH_MIME:
            raise web.HTTPBadRequest(
                text=f"вложение #{i}: тип {mime or '?'} не читается моделью — "
                     "можно PNG, JPEG, WebP, GIF")
        try:
            data = base64.b64decode(str(item.get("data") or ""), validate=True)
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text=f"вложение #{i}: data — не base64")
        if not data:
            raise web.HTTPBadRequest(text=f"вложение #{i}: пустой файл")
        if len(data) > _ATTACH_MAX_BYTES:
            raise web.HTTPBadRequest(text=f"вложение #{i}: больше 8 МБ")
        name = re.sub(r"[^\w.\-]+", "_", str(item.get("name") or "").strip(), flags=re.UNICODE)
        name = name.strip("._") or f"image{i}"
        if not name.lower().endswith(_ATTACH_MIME[mime]) and not (
                mime == "image/jpeg" and name.lower().endswith(".jpeg")):
            name += _ATTACH_MIME[mime]
        out.append({"name": name[:120], "mime": mime, "data": data})
    return out


def _write_attachments(control: Path, stamp: str, files: list[dict]) -> list[str]:
    """Файлы вложений в `desk_inbox/attachments/<stamp>/` — атомарно, до записки.

    -> относительные пути (от desk_inbox) для подвала записки; читатель (руннер)
    переносит их в свой медиа-спул и отдаёт модели картинкой в кадре.
    """
    folder = control / "attachments" / stamp
    folder.mkdir(parents=True, exist_ok=True)
    rel_paths: list[str] = []
    for i, f in enumerate(files, 1):
        name = f["name"]
        target = folder / name
        if target.exists():
            stem, ext = os.path.splitext(name)
            name = f"{stem}-{i}{ext}"
            target = folder / name
        tmp = target.with_name(".part-" + target.name)
        with open(tmp, "wb") as handle:
            handle.write(f["data"])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        rel_paths.append(f"attachments/{stamp}/{name}")
    return rel_paths


async def _say(text: str, chat: str = "", attachments=None) -> dict:
    """Сообщение ей. Durable-файл в memory/.control/desk_inbox — и всё.

    `chat` — адрес комнаты (слово владельца 31.08: окно — ещё одна дверь владельца в
    ЛЮБУЮ его комнату). Пусто/«pult» — прежний путь. Telegram-ключ — записка
    `<stamp>__to__<ключ>.md`: руннер запишет реплику владельца в память ЭТОЙ
    комнаты и поведёт ход там; ответ уедет в Telegram. Мёртвый руннер = мёртвый
    бот, и класть адресное сообщение туда, откуда оно никогда не уедет адресату,
    значило бы врать квитанцией — отвечаем 409 честно.

    Убран прежний «честный фолбэк» в workspace/inbox. Комментарий обещал, что
    «она читает его уже сегодня», но в дереве workspace/inbox — папка СКАЧАННЫХ
    телеграм-вложений с квотами и раскладкой groups/private; её опрашивают
    только руки inbox_list/inbox_read, которые агент зовёт по своей воле. Условие
    фолбэка ровно «руннер мёртв» — то есть звать руку некому: фолбэк по
    построению не мог сработать в том единственном случае, ради которого написан.
    Зато записка ложилась в плоский корень вне конвенции, могла быть прочитана
    вторым заходом как «файл из Telegram», а расписка written:["inbox"] считала
    это доставкой. Канал desk_inbox durable и переживает рестарт сам.

    Живость читателя берётся из readers.reader_status — ТОГО ЖЕ источника, что и
    шапка окна. Было два несовместимых определения: здесь mtime файла с порогом
    48 ЧАСОВ, в readers.state — поле `at` с порогом 45 секунд. Из-за этого окно
    показывало «Не запущен» и одновременно принимало сообщение как доставленное
    mid-turn: ни строки в ленте, ни тоста, ни ошибки.
    """
    text = str(text or "").strip()
    chat = str(chat or "").strip()
    files = _attachments_in(attachments)
    if not text and not files:
        raise web.HTTPBadRequest(text="пустое сообщение")
    if len(text) > 20_000:
        raise web.HTTPBadRequest(text="слишком длинно (20k)")
    if chat and chat not in ("window", "pult") and not _CHAT_KEY_RE.match(chat) \
            and not rooms.is_room(chat):
        raise web.HTTPBadRequest(text=f"не похоже на адрес комнаты: {chat!r}")
    if files and chat and _CHAT_KEY_RE.match(chat) and not rooms.is_room(chat):
        # Вложения из окна едут только в комнаты окна: в Telegram-комнату записка
        # уходит как реплика владельца через её бот-транспорт, и картинку туда
        # переправить пока нечем — честный отказ вместо молча потерянного файла.
        raise web.HTTPBadRequest(text="вложения из окна пока не едут в Telegram-комнаты — "
                                      "отправь текст, а картинку пришли в Telegram сама")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    body = (f"# Сообщение с окна владельца · {stamp}\n\n{text}\n")
    # Адресная записка — и в Telegram-комнату, и в другую комнату окна
    # (`window-<hex>`, задача A §3): раннер ведёт ход в ней (`__to__<ключ>`).
    targeted = bool(chat and chat not in ("window", "pult"))

    def _write() -> dict:
        written = []
        control = readers.tree() / "memory" / ".control" / "desk_inbox"
        # Вложения — файлами рядом с запиской, до её публикации: записка называет
        # их в подвале `[вложения]`, и читатель, захвативший записку, уже видит
        # готовые файлы (порядок записи = порядок видимости).
        footer = ""
        rel_paths: list[str] = []
        if files:
            rel_paths = _write_attachments(control, stamp, files)
            footer = "\n[вложения]\n" + "".join(
                f"- {rel} · {f['mime']} · {len(f['data'])}\n" for rel, f in zip(rel_paths, files))
        body_text = body + footer
        # Квитанция читателя: её раннер (кандидат desk-midturn) пишет .reader.json
        # при старте и обновляет на ходу. Свежая квитанция = канал живой,
        # сообщение уедет mid-turn.
        reader_alive = readers.reader_status()["alive"]
        # Контракт канала (её ревью 29.08): публикация ТОЛЬКО атомарной подменой.
        # Пишем во временное имя, которого glob читателя не видит, затем
        # os.replace — под финальным именем частичный файл не существует никогда,
        # и читатель, захватывающий rename-ом, не может получить обрезанный текст.
        def _publish(target: Path) -> None:
            tmp = target.with_name(".tmp-" + target.name)
            tmp.write_text(body_text, encoding="utf-8", newline="\n")
            os.replace(tmp, target)

        if targeted and not reader_alive:
            raise web.HTTPConflict(
                text="руннер не поднят — сообщение в комнату не уедет; "
                     "запусти локальный харнесс и повтори")
        try:
            control.mkdir(parents=True, exist_ok=True)
            name = f"{stamp}__to__{chat}.md" if targeted else f"{stamp}.md"
            _publish(control / name)
            written.append("control")
        except OSError:
            log.warning("mid-turn канал недоступен", exc_info=True)
        return {"written": written, "stamp": stamp, "midturn": reader_alive,
                "chat": chat or "window", "attachments": rel_paths}

    result = await asyncio.to_thread(_write)
    if not result["written"]:
        raise web.HTTPInternalServerError(text="не записалось никуда")
    return result


# ------------------------------------------------------------------ труба

PROTOCOL = "frame.desk.v1"          # как в ui-kit/contract.json (tests/t_contract.py)
DEFAULT_PORT = 8094                 # порт канала по умолчанию — там же
_STARTED_AT = time.time()


async def _tunnel_dispatch(method: str, path: str, body, local: bool = False,
                           role: str = "owner") -> dict:
    """Один запрос канала -> тот же ответ, что у HTTP-ручки того же пути.

    Это ШОВ ПРОДУКТА: по каналу отвечает и удалённый харнесс (на сервере), и
    локальный на винде; оболочка разницы не видит — протокол один,
    frame.desk.v1 (по образцу praxis.body.v1 её тела — исходящее соединение,
    ни одного открытого порта у клиента). Маршруты — из той же таблицы
    ROUTES, что и HTTP-роутер: второго списка нет."""
    from urllib.parse import urlsplit, parse_qs
    parts = urlsplit(path)
    route_path = parts.path
    query = {k: values[-1] for k, values in parse_qs(parts.query).items()}
    # Канал — ВТОРАЯ дверь к тем же ручкам, и область ключа устройства должна
    # быть в нём той же самой: иначе телефон, открыв /tunnel, обходил бы весь
    # разбор прав HTTP-слоя.
    if not _scope_ok(role, route_path):
        return {"status": 403, "error": "телефону сюда нельзя — это делают из окна"}
    route, params = match_route(method, route_path)
    if route is None:
        return {"status": 404, "error": "нет такого пути"}
    if route.local_only and not local:
        return {"status": 403, "error": "только с этой машины"}
    try:
        result = await route.handler(Call(match=params, query=query, body=body,
                                          local=local, role=role))
    except web.HTTPError as exc:
        return {"status": exc.status, "error": exc.text or str(exc)}
    except Exception as exc:
        log.warning("канал: %s %s упал", method, route_path, exc_info=True)
        return {"status": 500, "error": f"{type(exc).__name__}: {exc}"[:300]}
    if isinstance(result, Fail):
        reply = {"status": result.status, "error": result.error}
        if result.code:
            reply["code"] = result.code
        return reply
    return {"status": 200, "body": result}


async def tunnel(request):
    """Одна труба на оболочку: запросы с id + события живьём, praxis.desk.v1.

    На рукопожатие WebSocket политика CORS не действует вообще, поэтому страница
    из браузера владельца открывала ws://127.0.0.1:<порт>/tunnel, получала
    `local = True` по факту петли — и вместе с ним права окна: выпустить пару,
    посмотреть и ОТВЯЗАТЬ устройства владельца, переписать конституцию.
    Починка одного только CORS эту дверь не закрывала. Origin браузер обязан
    слать на рукопожатии всегда, и middleware его уже проверил; здесь — второй
    рубеж на случай, если труба однажды переедет из-под middleware.
    """
    ok, _origin = _origin_ok(request)
    if not ok or not _host_ok(request):
        raise web.HTTPForbidden(text="чужая страница")
    role = _role(request)
    if not role:
        raise web.HTTPForbidden(text="нет ключа")
    local = _is_loopback(request) and role == "owner"
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    await ws.send_json({"hello": PROTOCOL,
                        "server": {"started_at": _STARTED_AT,
                                   "tree": str(readers.tree())}},
                       dumps=lambda d: json.dumps(d, ensure_ascii=False))
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _SSE_CLIENTS.add(queue)

    async def pump() -> None:
        # Смерть насоса от чего угодно, кроме отмены, раньше проходила молча:
        # задача исчезала, `async for` продолжал отвечать, и клиент считал трубу
        # живой (hello был, сокет открыт), не получая живых событий больше
        # никогда. Теперь сокет закрывается — клиент переподключится.
        try:
            while True:
                event = await queue.get()
                await ws.send_json({"event": event},
                                   dumps=lambda d: json.dumps(d, ensure_ascii=False))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("канал: насос событий умер — закрываю сокет", exc_info=True)
            await ws.close()

    pump_task = asyncio.create_task(pump())
    # Запросы обрабатываются КОНКУРЕНТНО. Было строго по одному внутри
    # `async for`: клиент мультиплексирует по одному сокету всё окно, и на
    # каждое событие хода выпускает разом /api/runs, /api/chats, /api/chat/…,
    # /api/chat-turns/… и /api/state — а POST /api/say, который владелец жмёт в
    # ту же секунду, вставал в очередь за ними. Клиент сопоставляет ответы по
    # id, порядок ему не нужен.
    inflight: set[asyncio.Task] = set()

    async def answer(req: dict) -> None:
        try:
            reply = await _tunnel_dispatch(
                str(req.get("method") or "GET").upper(),
                str(req.get("path") or ""), req.get("body"), local=local,
                role=role)
        except Exception as exc:               # ответ обязан уйти всегда
            log.warning("канал: обработчик упал", exc_info=True)
            reply = {"status": 500, "error": f"{type(exc).__name__}: {exc}"[:300]}
        reply["id"] = req.get("id")
        try:
            await ws.send_json(reply, dumps=lambda d: json.dumps(d, ensure_ascii=False))
        except (ConnectionResetError, RuntimeError):
            pass

    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                req = json.loads(msg.data)
            except ValueError:
                continue
            if len(inflight) >= 64:            # предохранитель от залива
                await ws.send_json({"id": req.get("id"), "status": 503,
                                    "error": "слишком много запросов сразу"})
                continue
            task = asyncio.create_task(answer(req))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        pump_task.cancel()
        for task in list(inflight):
            task.cancel()
        _SSE_CLIENTS.discard(queue)
    return ws


# ------------------------------------------------------------------ SSE

async def sse(request):
    response = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _SSE_CLIENTS.add(queue)
    try:
        await response.write(b": connected\n\n")
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
                data = json.dumps(event, ensure_ascii=False)
                await response.write(f"data: {data}\n\n".encode("utf-8"))
            except asyncio.TimeoutError:
                await response.write(b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        _SSE_CLIENTS.discard(queue)
    return response


def _put(queue: asyncio.Queue, event: dict) -> None:
    """Положить событие в очередь клиента, вытеснив старейшее при переполнении.

    Было `loop.call_soon_threadsafe(queue.put_nowait, event)` под `except
    Exception: pass` — но call_soon_threadsafe только ПЛАНИРУЕТ вызов, а
    QueueFull летит потом внутри цикла событий: сюда он не приходил никогда.
    Телефон вышел из Wi-Fi, насос встал на send_json, очередь на 200 набилась —
    и дальше каждое живое событие уходило в никуда, а лог забивался
    трассировками, при этом ни окну, ни телефону никто не говорил, что события
    пропали: интерфейс молча замирал на старых данных.
    """
    try:
        queue.put_nowait(event)
        return
    except asyncio.QueueFull:
        pass
    for _ in range(2):                               # освободить место под оба
        try:
            queue.get_nowait()                       # выбросить старейшее
        except asyncio.QueueEmpty:
            break
    try:
        queue.put_nowait({"t": "lost"})              # клиенту: перечитай состояние
        queue.put_nowait(event)
    except asyncio.QueueFull:
        pass


def _broadcast(loop: asyncio.AbstractEventLoop, event: dict) -> None:
    for queue in list(_SSE_CLIENTS):
        try:
            loop.call_soon_threadsafe(_put, queue, event)
        except RuntimeError:
            pass                                     # цикл уже закрыт


def _watcher(loop: asyncio.AbstractEventLoop) -> None:
    """Стат-поллинг живых файлов; смена размера -> событие клиентам.

    Три вещи, из-за которых программа в трее читала десятки гигабайт в сутки на
    ХОЛОСТОМ ходу (замер на живом дереве владельца: 12.6 мс и 1.12 МБ на тик
    раз в 1.5 с = 63 ГБ/сутки; на turns.jsonl в 7.5 МБ — 215 ГБ/сутки):
      * сторож тикал, даже когда клиентов нет вовсе (окно закрыто, трей);
      * list_runs(limit=1) безусловно звал chat_titles(), а тот вычитывал до
        4 МБ хвоста turns.jsonl — ради заголовков, которые событию не нужны;
      * словарь probe-состояния копил ключ `run:<id>` на каждый когда-либо
        виденный прогон и не чистился никогда.
    Плюс сторож следил ровно за одним, самым свежим прогоном: при двух живых
    прогонах (окно и служба) события второго не приходили вовсе.
    """
    state: dict[str, tuple] = {}

    def probe(name: str, path: Path) -> bool:
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        except OSError:
            stamp = None
        if state.get(name) != stamp:
            state[name] = stamp
            return True
        return False

    health_state = {"key": None, "next_at": 0.0}
    seen_flush = 0.0
    while True:
        try:
            if not _SSE_CLIENTS:
                # Никто не смотрит — не читаем дерево. Раз в пять минут сливаем
                # «последний раз на связи» устройств и спим дальше.
                if time.time() - seen_flush > 300:
                    seen_flush = time.time()
                    _flush_device_seen()
                state.clear()
                health_state["key"] = None      # первый тик после подключения — свежий
                time.sleep(2.0)
                continue
            if time.time() - seen_flush > 300:
                seen_flush = time.time()
                _flush_device_seen()
            base = readers.tree() / "memory" / ".state"
            # Сторож тишины: раз в ~30 с, событие клиентам ТОЛЬКО на смене состава
            # тревог (баннер не мигает от каждого тика).
            if time.time() >= health_state["next_at"]:
                health_state["next_at"] = time.time() + 30
                snapshot = readers.health()
                key = tuple(sorted(a["kind"] for a in snapshot.get("alarms") or []))
                if key != health_state["key"]:
                    health_state["key"] = key
                    _broadcast(loop, {"t": "health", "health": snapshot})
            if probe("llm", base / "llm_calls.jsonl"):
                _broadcast(loop, {"t": "llm"})
            if probe("skips", base / "perception_skips.jsonl"):
                _broadcast(loop, {"t": "skips"})
            if probe("shadow", base / "shadow" / "metrics.jsonl"):
                _broadcast(loop, {"t": "shadow"})
            # Все живые прогоны, а не только самый свежий; заголовки комнат не
            # запрашиваем (with_titles=False) — событию они не нужны.
            watched = readers.list_runs(limit=8, with_titles=False)
            alive = set()
            for index, row in enumerate(watched):
                run_id = str(row.get("id") or "")
                if not run_id:
                    continue
                # Самый свежий — всегда (его последняя дописка может лечь уже
                # после смены статуса), остальные — пока идут.
                if index and str(row.get("status") or "") not in ("running", "queued"):
                    continue
                alive.add("run:" + run_id)
                run_path = readers.run_dir(run_id)
                if run_path is not None and probe("run:" + run_id,
                                                 run_path / "events.jsonl"):
                    _broadcast(loop, {"t": "run", "run_id": run_id,
                                      "status": row.get("status")})
            for stale in [k for k in state if k.startswith("run:") and k not in alive]:
                state.pop(stale, None)
        except Exception:
            log.debug("watcher tick failed", exc_info=True)
        time.sleep(_WATCH_INTERVAL)


# ------------------------------------------------------------------ app

async def index(request):
    """index.html с версией статики в src: app.js?v=<mtime>.

    Без версии браузер кэширует app.js эвристикой (мы не слали Cache-Control
    вовсе), и КАЖДАЯ правка UI липла у клиента до жёсткого релоада: страница
    исполняла старый скрипт, а сервер уже отдавал новый — час отладки 31.08.
    mtime в качестве версии: правка файла = новый URL = мгновенный подхват.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "app.css", "config.js"):
        try:
            stamp = int((STATIC / name).stat().st_mtime)
        except OSError:
            continue
        html = html.replace(f'"{name}"', f'"{name}?v={stamp}"')
    return web.Response(text=html, content_type="text/html",
                        headers={"Cache-Control": "no-cache"})


def _static_file(name: str):
    async def handler(request):
        # no-cache = «можно хранить, но каждый раз ревалидируй» (ETag → 304):
        # свежесть UI без повторной перекачки тела.
        return web.FileResponse(
            STATIC / name, headers={"Cache-Control": "no-cache"})
    return handler


def _mobile_file(name: str):
    async def handler(request):
        return web.FileResponse(MOBILE / name, headers={"Cache-Control": "no-cache"})
    return handler


def build_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/", index)
    # Все ручки API — из одной таблицы (та же, что у канала).
    for route in ROUTES:
        app.router.add_route(route.method, route.path, _http_handler(route))
    app.router.add_get("/pair/redeem", api_pair_redeem)
    app.router.add_post("/pair/telegram", api_pair_telegram)   # только HTTP: ключа ещё нет
    app.router.add_get("/m/", mobile_index)
    app.router.add_get("/m", mobile_index)
    app.router.add_get("/m/manifest.webmanifest", mobile_manifest)
    app.router.add_get("/m/sw.js", mobile_sw)
    if (MOBILE / "assets").is_dir():
        app.router.add_static("/m/assets/", MOBILE / "assets")
    for name in ("icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        if (MOBILE / name).is_file():
            app.router.add_get("/m/" + name, _mobile_file(name))
    app.router.add_get("/events", sse)
    app.router.add_get("/tunnel", tunnel)
    if STATIC.is_dir():
        app.router.add_static("/static/", STATIC)
    # Те же ассеты с корня: index.html ссылается относительно, чтобы один и тот
    # же дистрибутив жил и в вебе, и в нативной оболочке.
    for name in ("config.js", "favicon.svg"):
        if (STATIC / name).is_file():
            app.router.add_get("/" + name, _static_file(name))
    # Сборка Vite кладёт ассеты в assets/ с хэшами в именах.
    if (STATIC / "assets").is_dir():
        app.router.add_static("/assets/", STATIC / "assets")
    return app


def _ensure_local_tree() -> None:
    """Минимальный скелет дерева для локального режима. На сервере дерево
    смонтировано ro — mkdir там падает, и это не ошибка: молча пропускаем."""
    base = readers.tree()
    for rel in ("memory/.state", "memory/.control", "memory/runs",
                "workspace/inbox", "soul"):
        try:
            (base / rel).mkdir(parents=True, exist_ok=True)
        except OSError:
            return


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(
        os.environ.get("HELENE_PORT") or os.environ.get("PRAXIS_DESK_PORT") or DEFAULT_PORT)
    # Локальный харнесс обязан слушать ТОЛЬКО петлю: дерево без токена не должно
    # быть видно даже соседям по локальной сети. Сервер (за Caddy) — как раньше.
    host = (os.environ.get("HELENE_HOST") or os.environ.get("PRAXIS_DESK_HOST") or "0.0.0.0").strip()
    _ensure_local_tree()
    app = build_app()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    threading.Thread(target=_watcher, args=(loop,), daemon=True).start()
    log.info("Hélène слушает %s:%d, дерево %s, токен %s", host, port,
             readers.tree(), "задан" if TOKEN else "НЕ задан (локальная петля)")
    web.run_app(app, host=host, port=port, loop=loop, print=None,
                access_log_class=SafeAccessLogger)


if __name__ == "__main__":
    main()
