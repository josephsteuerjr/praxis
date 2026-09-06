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
import datetime as dt
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path

from aiohttp import web
from aiohttp.abc import AbstractAccessLogger

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deskd import readers

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
               "/m/icon-192.png", "/m/icon-512.png", "/m/apple-touch-icon.png"}

# Область ключа устройства (телефона). Всё остальное — только окну: ключ
# устройства уезжает в чужие руки легче всех (Wi-Fi, лог, чужая камера над
# плечом), а /api/anatomy отдаёт OPENAI_API_KEY и /api/md переписывает
# конституцию. Мобильный UI дальше этого списка и не ходит.
# /tunnel и /events пускаем: внутри трубы область проверяется ещё раз, по
# каждому маршруту (_tunnel_dispatch), иначе телефон обошёл бы разбор прав.
_DEVICE_PATHS = {"/api/state", "/api/chats", "/api/say", "/api/health",
                 "/tunnel", "/events"}
_DEVICE_PREFIXES = ("/api/chat/",)


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


async def api_pair_new(request):
    """Новая пара — только со своей машины (окно программы)."""
    if not _is_loopback(request):
        raise web.HTTPForbidden(text="пару выдаёт только окно на этой машине")
    return _json(_new_pair())


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
    anatomy = readers.anatomy() or {}
    response = _json({"key": got["key"], "agent": anatomy.get("agent_name") or "Агент",
                      "uses_left": got["uses_left"]})
    response.set_cookie(COOKIE, got["key"], max_age=365 * 24 * 3600,
                        httponly=True, samesite="Lax")
    return response


async def api_devices(request):
    if not _is_loopback(request):
        raise web.HTTPForbidden(text="список устройств — только с этой машины")
    return _json(_device_rows())


async def api_device_revoke(request):
    if not _is_loopback(request):
        raise web.HTTPForbidden(text="отвязать — только с этой машины")
    payload = await _json_body(request)
    return _json(_revoke_device(str((payload or {}).get("id") or "")))


# ------------------------------------------------------------- PWA /m/
_HERE_DIR = Path(__file__).resolve().parent
MOBILE = next((d for d in (_HERE_DIR / "mobile", _HERE_DIR.parent / "mobile" / "dist")
               if (d / "index.html").is_file()), _HERE_DIR / "mobile")


async def mobile_index(request):
    if not (MOBILE / "index.html").is_file():
        raise web.HTTPNotFound(text="мобильная страница не собрана")
    return web.FileResponse(MOBILE / "index.html", headers={"Cache-Control": "no-cache"})


async def mobile_manifest(request):
    """Манифест PWA: start_url несёт токен пары, чтобы установленное на экран
    «Домой» приложение могло обменять его второй раз (iPhone)."""
    pair = str(request.query.get("pair") or "")
    anatomy = readers.anatomy() or {}
    name = str(anatomy.get("agent_name") or "Агент")
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

async def api_runs(request):
    kind = request.query.get("kind") or ""
    before = request.query.get("before") or ""
    limit = _int_arg(request.query, "limit", 80, 1, 200)
    return _json(await asyncio.to_thread(readers.list_runs, limit, kind, before))


async def api_run(request):
    run_id = request.match_info["run_id"]
    detail = await asyncio.to_thread(readers.run_detail, run_id)
    if not detail:
        raise web.HTTPNotFound(text="нет такого прогона")
    return _json(detail)


async def api_pulse(request):
    return _json(await asyncio.to_thread(readers.pulse))


async def api_errors(request):
    return _json(await asyncio.to_thread(readers.errors))


async def api_board(request):
    return _json(await asyncio.to_thread(readers.board))


async def api_agenda(request):
    return _json(await asyncio.to_thread(readers.agenda))


async def api_forge(request):
    return _json(await asyncio.to_thread(readers.forge_tasks))


async def api_shadow(request):
    return _json(await asyncio.to_thread(readers.shadow_streams))


async def api_shadow_captures(request):
    stream = request.match_info["stream"]
    return _json(await asyncio.to_thread(readers.shadow_captures, stream))


async def api_shadow_capture(request):
    stream = request.match_info["stream"]
    name = request.query.get("name") or ""
    text = await asyncio.to_thread(readers.shadow_capture, stream, name)
    return _json({"stream": stream, "name": name, "text": text})


async def api_shadow_diff(request):
    stream = request.match_info["stream"]
    old = request.query.get("old") or ""
    new = request.query.get("new") or ""
    return _json(await asyncio.to_thread(readers.shadow_diff, stream, old, new))


async def api_shadow_metrics(request):
    stream = request.query.get("stream") or request.match_info.get("stream") or ""
    return _json(await asyncio.to_thread(readers.shadow_metrics, 120, stream))


async def api_chats(request):
    return _json(await asyncio.to_thread(readers.chats))


async def api_chat(request):
    peer = request.match_info["peer"]
    n = _int_arg(request.query, "n", 200, 1, 600)
    return _json(await asyncio.to_thread(readers.chat_tail, peer, n))


async def api_md(request):
    return _json(await asyncio.to_thread(readers.safe_read_md,
                                         request.query.get("path") or ""))


async def api_md_tree(request):
    return _json(await asyncio.to_thread(readers.md_tree))


async def api_home(request):
    """Чьё это дерево. Оболочка спрашивает перед тем, как признать живой на
    порту харнесс своим: осиротевший процесс прежней установки держал порт, и
    новое окно молча показывало чужого агента."""
    return _json({"tree": str(readers.tree().resolve())})


# Обе ручки окно и телефон опрашивают непрерывно, и раньше ни у одной не было
# рубежа: одна кривая строка в llm_calls.jsonl (голый `float(ts)` в девяти
# местах readers) — и обе отвечали 500 навсегда, пока файл не ротируется.
# Рубеж стоит ВНУТРИ readers.state/readers.health, а не здесь, потому что вторая
# дверь к тем же читателям — труба (_tunnel_dispatch), и телефон ходит именно в
# неё: обёртка на HTTP-обработчике телефон бы не прикрыла. Ответ при отказе той
# же формы, что при удаче, с честной фразой «не прочиталось».
async def api_health(request):
    return _json(await asyncio.to_thread(readers.health))


async def api_state(request):
    return _json(await asyncio.to_thread(readers.state))


async def api_mode(request):
    """Ограда (песочница | интерактивный) и ОТДЕЛЬНО от неё служба.

    Два независимых ответа в одном теле: `name`/`choices` — какая ограда,
    `service_installed`/`session0`/`firewall`/`service` — что со службой. Служба
    режимом не является: она ставится поверх любой ограды и ограду не снимает
    (склейка этих двух измерений и была P0 первой редакции).

    Отдельной ручкой, а не только полем в /api/state: экраны установщика и
    настроек спрашивают режим и список выборов сами по себе, без всей шапки.

    ⚠ ЛОВУШКА ПРОДУКТА: окно ходит по ТРУБЕ, а не по HTTP. Ручка, заведённая
    только здесь, в окне не работает — так уже было с /api/home, единственным
    маршрутом из двадцати, жившим в одном канале из двух. Поэтому она заведена
    И в `_tunnel_dispatch` (маршрут "/api/mode").
    """
    return _json(await asyncio.to_thread(readers.mode_state))


async def api_md_write(request):
    """Правка маркдауна агента из окна: конституция, навыки, заметки.

    Первый шаг конструктора: то, что окно показывает, оно же и правит. Путь
    только внутри разрешённых групп (readers.safe_write_md), только .md,
    запись атомарная.

    Прежний докстринг обещал безопасность («файл переписывается целиком тем,
    что человек видел») — но именно это и означало «текстом ДО правки агента».
    Владелец правит soul/SOUL.md в окне, агент правит его же своей рукой:
    выигрывал тот, кто записал последним, чужая работа исчезала без следа.
    Теперь окно возвращает mtime_ns, который получило при чтении, и при
    расхождении ручка отвечает 409, а не переписывает молча.
    """
    payload = await _json_body(request) or {}
    result = await asyncio.to_thread(readers.safe_write_md,
                                     str(payload.get("path") or ""),
                                     str(payload.get("text") or ""),
                                     payload.get("mtime_ns"))
    if result.get("error"):
        if result.get("code") == "conflict":
            raise web.HTTPConflict(text=result["error"])
        raise web.HTTPBadRequest(text=result["error"])
    return _json(result)


async def api_anatomy(request):
    return _json(await asyncio.to_thread(readers.anatomy))


async def api_chat_turns(request):
    peer = request.match_info["peer"]
    n = _int_arg(request.query, "n", 120, 1, 300)
    return _json(await asyncio.to_thread(readers.chat_turns, peer, n))


_SAY_RE = re.compile(r"[^\w\-]+")


_CHAT_KEY_RE = re.compile(r"^-?\d+(?:__topic__\d+)?$")


async def _say(text: str, chat: str = "") -> dict:
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
    if not text:
        raise web.HTTPBadRequest(text="пустое сообщение")
    if len(text) > 20_000:
        raise web.HTTPBadRequest(text="слишком длинно (20k)")
    if chat and chat not in ("window", "pult") and not _CHAT_KEY_RE.match(chat):
        raise web.HTTPBadRequest(text=f"не похоже на адрес комнаты: {chat!r}")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    body = (f"# Сообщение с окна владельца · {stamp}\n\n{text}\n")
    targeted = bool(chat and chat not in ("window", "pult"))

    def _write() -> dict:
        written = []
        control = readers.tree() / "memory" / ".control" / "desk_inbox"
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
            tmp.write_text(body, encoding="utf-8", newline="\n")
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
                "chat": chat or "window"}

    result = await asyncio.to_thread(_write)
    if not result["written"]:
        raise web.HTTPInternalServerError(text="не записалось никуда")
    return result


async def api_say(request):
    payload = await _json_body(request) or {}
    return _json(await _say(payload.get("text"), payload.get("chat") or ""))


# ------------------------------------------------------------------ труба

PROTOCOL = "frame.desk.v1"
_STARTED_AT = time.time()


async def _tunnel_dispatch(method: str, path: str, body, local: bool = False,
                           role: str = "owner") -> dict:
    """Один запрос трубы -> тот же ответ, что у HTTP-ручки того же пути.

    Это ШОВ БУДУЩЕГО ПРОДУКТА: сегодня по трубе отвечает удалённый харнесс
    (этот демон на сервере), завтра — нативный харнесс на винде. Оболочка
    разницы не видит: протокол один, praxis.desk.v1 (по образцу praxis.body.v1
    её тела — исходящее соединение, ни одного открытого порта у клиента)."""
    from urllib.parse import urlsplit, parse_qs
    parts = urlsplit(path)
    route = parts.path
    query = {k: values[-1] for k, values in parse_qs(parts.query).items()}
    # Труба — ВТОРАЯ дверь к тем же ручкам, и область ключа устройства должна
    # быть в ней той же самой: иначе телефон, открыв /tunnel, обходил бы весь
    # разбор прав HTTP-слоя.
    if not _scope_ok(role, route):
        return {"status": 403, "error": "телефону сюда нельзя — это делают из окна"}
    try:
        if method == "POST" and route == "/api/md":
            result = await asyncio.to_thread(
                readers.safe_write_md, str((body or {}).get("path") or ""),
                str((body or {}).get("text") or ""), (body or {}).get("mtime_ns"))
            if result.get("error"):
                status = 409 if result.get("code") == "conflict" else 400
                return {"status": status, "error": result["error"],
                        "code": result.get("code")}
            return {"status": 200, "body": result}
        if method == "POST" and route == "/api/say":
            return {"status": 200,
                    "body": await _say((body or {}).get("text"),
                                       (body or {}).get("chat") or "")}
        if route == "/api/runs":
            return {"status": 200, "body": await asyncio.to_thread(
                readers.list_runs, _int_arg(query, "limit", 80, 1, 200),
                query.get("kind") or "", query.get("before") or "")}
        if route.startswith("/api/run/"):
            detail = await asyncio.to_thread(readers.run_detail,
                                             route.removeprefix("/api/run/"))
            return ({"status": 200, "body": detail} if detail
                    else {"status": 404, "error": "нет такого прогона"})
        if route == "/api/pulse":
            return {"status": 200, "body": await asyncio.to_thread(readers.pulse)}
        if route == "/api/errors":
            return {"status": 200, "body": await asyncio.to_thread(readers.errors)}
        if route == "/api/board":
            return {"status": 200, "body": await asyncio.to_thread(readers.board)}
        if route == "/api/agenda":
            return {"status": 200, "body": await asyncio.to_thread(readers.agenda)}
        if route == "/api/forge":
            return {"status": 200, "body": await asyncio.to_thread(readers.forge_tasks)}
        if route == "/api/chats":
            return {"status": 200, "body": await asyncio.to_thread(readers.chats)}
        if route.startswith("/api/chat/"):
            return {"status": 200, "body": await asyncio.to_thread(
                readers.chat_tail, route.removeprefix("/api/chat/"),
                _int_arg(query, "n", 200, 1, 600))}
        if route == "/api/shadow":
            return {"status": 200, "body": await asyncio.to_thread(readers.shadow_streams)}
        if route == "/api/shadow-metrics":
            return {"status": 200, "body": await asyncio.to_thread(
                readers.shadow_metrics, 120, query.get("stream") or "")}
        # Была заведена ТОЛЬКО в HTTP-роутере: единственная ручка из двадцати,
        # существовавшая в одном канале из двух. По трубе отвечала 404.
        if route == "/api/home":
            return {"status": 200, "body": {"tree": str(readers.tree().resolve())}}
        # Каждый маршрут — в ОБОИХ каналах. Ровно здесь была историческая
        # ловушка: /api/home существовала только в HTTP-роутере.
        match = re.match(r"^/api/shadow/([^/]+)/(captures|capture|diff|metrics)$", route)
        if match:
            stream, action = match.group(1), match.group(2)
            if action == "metrics":
                return {"status": 200, "body": await asyncio.to_thread(
                    readers.shadow_metrics, 120, stream)}
            if action == "captures":
                return {"status": 200, "body": await asyncio.to_thread(
                    readers.shadow_captures, stream)}
            if action == "capture":
                text = await asyncio.to_thread(readers.shadow_capture, stream,
                                               query.get("name") or "")
                return {"status": 200, "body": {"stream": stream,
                                                "name": query.get("name"),
                                                "text": text}}
            return {"status": 200, "body": await asyncio.to_thread(
                readers.shadow_diff, stream, query.get("old") or "",
                query.get("new") or "")}
        if route == "/api/md":
            return {"status": 200, "body": await asyncio.to_thread(
                readers.safe_read_md, query.get("path") or "")}
        if route == "/api/md-tree":
            return {"status": 200, "body": await asyncio.to_thread(readers.md_tree)}
        if route.startswith("/api/chat-turns/"):
            return {"status": 200, "body": await asyncio.to_thread(
                readers.chat_turns, route.removeprefix("/api/chat-turns/"),
                _int_arg(query, "n", 120, 1, 300))}
        if route == "/api/anatomy":
            return {"status": 200, "body": await asyncio.to_thread(readers.anatomy)}
        if route == "/api/health":
            return {"status": 200, "body": await asyncio.to_thread(readers.health)}
        if route == "/api/state":
            return {"status": 200, "body": await asyncio.to_thread(readers.state)}
        # Вторая дверь к режиму — та самая, которой ходит окно. HTTP-маршрут без
        # этой строки существовал бы только на бумаге (см. api_mode).
        if route == "/api/mode":
            return {"status": 200, "body": await asyncio.to_thread(readers.mode_state)}
        # телефон: пары выдаёт и отзывает только окно на этой машине
        if route.startswith("/pair/") and not local:
            return {"status": 403, "error": "только с этой машины"}
        if route == "/pair/devices":
            return {"status": 200, "body": _device_rows()}
        if method == "POST" and route == "/pair/new":
            return {"status": 200, "body": _new_pair()}
        if method == "POST" and route == "/pair/revoke":
            return {"status": 200, "body": _revoke_device(str((body or {}).get("id") or ""))}
        return {"status": 404, "error": "нет такого пути"}
    except web.HTTPError as exc:
        return {"status": exc.status, "error": exc.text or str(exc)}
    except Exception as exc:
        log.warning("канал: %s %s упал", method, route, exc_info=True)
        return {"status": 500, "error": f"{type(exc).__name__}: {exc}"[:300]}


async def tunnel(request):
    """Одна труба на оболочку: запросы с id + события живьём, praxis.desk.v1.

    На рукопожатие WebSocket политика CORS не действует вообще, поэтому страница
    из браузера владельца открывала ws://127.0.0.1:8094/tunnel, получала
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
    app.router.add_get("/api/runs", api_runs)
    app.router.add_get("/api/run/{run_id}", api_run)
    app.router.add_get("/api/pulse", api_pulse)
    app.router.add_get("/api/errors", api_errors)
    app.router.add_get("/api/board", api_board)
    app.router.add_get("/api/agenda", api_agenda)
    app.router.add_get("/api/forge", api_forge)
    app.router.add_get("/api/shadow", api_shadow)
    app.router.add_get("/api/shadow/{stream}/captures", api_shadow_captures)
    app.router.add_get("/api/shadow/{stream}/capture", api_shadow_capture)
    app.router.add_get("/api/shadow/{stream}/diff", api_shadow_diff)
    app.router.add_get("/api/shadow-metrics", api_shadow_metrics)
    app.router.add_get("/api/shadow/{stream}/metrics", api_shadow_metrics)
    app.router.add_get("/api/chats", api_chats)
    app.router.add_get("/api/chat/{peer}", api_chat)
    app.router.add_get("/api/md", api_md)
    app.router.add_get("/api/md-tree", api_md_tree)
    app.router.add_get("/api/health", api_health)
    app.router.add_get("/api/state", api_state)
    app.router.add_get("/api/mode", api_mode)
    app.router.add_get("/api/home", api_home)
    app.router.add_post("/pair/new", api_pair_new)
    app.router.add_get("/pair/redeem", api_pair_redeem)
    app.router.add_get("/pair/devices", api_devices)
    app.router.add_post("/pair/revoke", api_device_revoke)
    app.router.add_get("/m/", mobile_index)
    app.router.add_get("/m", mobile_index)
    app.router.add_get("/m/manifest.webmanifest", mobile_manifest)
    if (MOBILE / "assets").is_dir():
        app.router.add_static("/m/assets/", MOBILE / "assets")
    for name in ("icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        if (MOBILE / name).is_file():
            app.router.add_get("/m/" + name, _mobile_file(name))
    app.router.add_post("/api/md", api_md_write)
    app.router.add_get("/api/anatomy", api_anatomy)
    app.router.add_get("/api/chat-turns/{peer}", api_chat_turns)
    app.router.add_post("/api/say", api_say)
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
        os.environ.get("HELENE_PORT") or os.environ.get("PRAXIS_DESK_PORT") or 8094)
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
