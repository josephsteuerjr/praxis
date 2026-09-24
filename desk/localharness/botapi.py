# -*- coding: utf-8 -*-
"""Telegram Bot API как ЕЁ транспорт — тот же шов, что окно и Telethon.

Зачем: MTProto-вход (номер, api_id/api_hash, сессия) отсекает большинство
тестеров продукта. Бот от BotFather — один токен в helene.json, и её организм
живёт в Telegram без выдачи ему человеческого аккаунта.

Устройство повторяет transport.py: наполняем `agent._TELETHON` крючками и НЕ
трогаем ни строчки её кода. Поверх desk-крючков встаёт маршрутизатор: комната
окна остаётся у окна, все остальные адреса — у бота.

Что здесь честно работает: приём (long-poll getUpdates в своём потоке),
отправка текстом и файлом, реакции, имя/описание бота (её update_profile),
чтение СВОИХ комнат из её же архивов. Чего у ботов нет — истории чужих чатов,
списка диалогов, первого слова незнакомцу, аватара, вступления по ссылке — то
отвечает честным отказом, а не изображает действие.

⚠ Восприятие пишет память ДО подтверждения курсора: сообщение сначала ложится
в архив и события жизни, потом offset уезжает в getUpdates. Упали между — при
рестарте Telegram отдаст сообщение снова, и dedupe_key его отсеет.

⚠ КТО ЗАПУСКАЕТ ХОД. Имя бота публично по построению, поэтому написать ему может
кто угодно, а ход агента — это деньги владельца и руки на его машине. Поэтому
входящее от постороннего ЗАПИСЫВАЕТСЯ в память (агент видит, что ему писали) и
НЕ запускает ход. Кому можно — `telegram.allow_from`: "owner" (по умолчанию),
"listed" (+ `telegram.allowed_ids`), "any" (прежнее поведение, теперь — явное
решение владельца).
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import threading

import boot
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from pathlib import Path

from transport import _append_jsonl, _registry, _write_json, is_room

log = logging.getLogger("frame.botapi")

_MSG_LIMIT = 4096          # потолок sendMessage; длиннее — режем по абзацам
_CAPTION_LIMIT = 1024
_POLL_TIMEOUT = 25         # long-poll: столько держит сервер, +10 наш сокет
_STALE_SEC = 90            # связь считается живой, пока последний poll моложе
_INGEST_TRIES = 5          # столько раз пробуем переварить один апдейт


# --------------------------------------------------------------------------- #
#  HTTP: stdlib, без зависимостей — продукт обязан подниматься на голой машине
# --------------------------------------------------------------------------- #

class BotApiError(RuntimeError):
    def __init__(self, method: str, code: int, description: str,
                 retry_after: float = 0.0):
        super().__init__(f"{method}: [{code}] {description}")
        self.method, self.code = method, int(code)
        self.description = str(description)
        self.retry_after = float(retry_after)

    @property
    def permanent(self) -> bool:
        """4xx, кроме 429, — переспрашивать бессмысленно (нет прав/чата/формы)."""
        return 400 <= self.code < 500 and self.code != 429


def _multipart(fields: dict[str, str], file_field: str, path: Path,
               mime: str) -> tuple[bytes, str]:
    boundary = "praxis" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append((f"--{boundary}\r\nContent-Disposition: form-data; "
                       f'name="{name}"\r\n\r\n{value}\r\n').encode("utf-8"))
    chunks.append((f"--{boundary}\r\nContent-Disposition: form-data; "
                   f'name="{file_field}"; filename="{path.name}"\r\n'
                   f"Content-Type: {mime}\r\n\r\n").encode("utf-8"))
    chunks.append(path.read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


class BotClient:
    """Тонкий клиент Bot API. Ошибка метода — BotApiError, сеть — URLError."""

    def __init__(self, token: str):
        self._base = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, _http_timeout: float = 30.0, **params) -> dict | list:
        # `_http_timeout` с подчёркиванием: у самого API есть параметр `timeout`
        # (long-poll getUpdates), и имена не должны сталкиваться.
        data = {k: (json.dumps(v, ensure_ascii=False)
                    if isinstance(v, (dict, list, bool)) else str(v))
                for k, v in params.items() if v is not None}
        req = urllib.request.Request(
            self._base + method,
            data=urllib.parse.urlencode(data).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        return self._read(method, req, _http_timeout)

    def upload(self, method: str, file_field: str, path: Path, *,
               mime: str = "", timeout: float = 300.0, **params) -> dict | list:
        fields = {k: (json.dumps(v, ensure_ascii=False)
                      if isinstance(v, (dict, list, bool)) else str(v))
                  for k, v in params.items() if v is not None}
        mime = mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body, boundary = _multipart(fields, file_field, path, mime)
        req = urllib.request.Request(
            self._base + method, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return self._read(method, req, timeout)

    @staticmethod
    def _read(method: str, req: urllib.request.Request, timeout: float):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                raise BotApiError(method, exc.code, str(exc)) from exc
            retry = float((payload.get("parameters") or {}).get("retry_after") or 0)
            raise BotApiError(method, int(payload.get("error_code") or exc.code),
                              str(payload.get("description") or exc), retry) from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise BotApiError(method, int(payload.get("error_code") or 0),
                              str(payload.get("description") or "not ok"))
        return payload.get("result")


# --------------------------------------------------------------------------- #
#  Контакты: кого бот ВИДЕЛ. Единственный резолвер имён — Bot API не умеет
#  разыскивать произвольные @username, и врать об этом нельзя.
# --------------------------------------------------------------------------- #

class Contacts:
    def __init__(self, tree: Path):
        self._path = tree / "memory" / ".state" / "botapi_contacts.json"
        self._lock = threading.Lock()
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}
        if not isinstance(self._data, dict):
            self._data = {}

    def note(self, chat_id: str, *, name: str = "", username: str = "",
             kind: str = "user") -> None:
        with self._lock:
            row = self._data.get(str(chat_id)) or {}
            fresh = {"name": name or row.get("name") or "",
                     "username": (username or row.get("username") or "").lstrip("@"),
                     "kind": kind or row.get("kind") or "user",
                     "seen_at": time.time()}
            if {k: row.get(k) for k in ("name", "username", "kind")} != \
               {k: fresh[k] for k in ("name", "username", "kind")}:
                self._data[str(chat_id)] = fresh
                _write_json(self._path, self._data)
            else:
                self._data[str(chat_id)] = fresh

    def resolve(self, ref: str) -> str | None:
        needle = str(ref or "").strip()
        if not needle:
            return None
        if needle.lstrip("-").isdigit():
            return needle                     # числовому id верим как есть
        bare = needle.lstrip("@").lower()
        with self._lock:
            for cid, row in self._data.items():
                if (row.get("username") or "").lower() == bare:
                    return cid
            for cid, row in self._data.items():
                if bare in (row.get("name") or "").lower():
                    return cid
        return None

    def label(self, chat_id: str) -> str:
        row = self._data.get(str(chat_id)) or {}
        return row.get("name") or ("@" + row["username"] if row.get("username")
                                   else str(chat_id))

    def search(self, query: str, limit: int = 10) -> list[str]:
        needle = str(query or "").strip().lower()
        out = []
        with self._lock:
            for cid, row in sorted(self._data.items(),
                                   key=lambda kv: -float(kv[1].get("seen_at") or 0)):
                hay = " ".join((row.get("name") or "", row.get("username") or "")).lower()
                if not needle or needle in hay:
                    out.append(f"{self.label(cid)}: {cid}")
                if len(out) >= limit:
                    break
        return out


# --------------------------------------------------------------------------- #
#  Комната бота: архив + события жизни, форматы её дерева
# --------------------------------------------------------------------------- #

class Rooms:
    """Записи и чтение переписки по чатам — в тех же файлах, что читают её кадр,
    group_context и окно: memory/groups/<id>.jsonl + реестр .state/group_context."""

    def __init__(self, tree: Path, memory_life, agent_name: str):
        self.tree = Path(tree)
        self._life = memory_life
        self.agent_name = agent_name
        self._titles: dict[str, dict] = {}   # chat_id -> {title, is_dm, size}

    def describe(self, chat_id: str, *, title: str, is_dm: bool,
                 size: int | None = None,
                 sender: tuple[str, str] | None = None) -> None:
        row = self._titles.setdefault(str(chat_id), {})
        row.update({"title": title, "is_dm": is_dm, "size": size})
        if sender is not None:
            row["last_sender"] = sender      # (имя, telegram id) — для owner-гейта

    def meta(self, chat_id: str) -> dict:
        known = self._titles.get(str(chat_id))
        if known:
            return known
        # Карта комнат живёт в RAM и после рестарта пуста, а комнаты — на диске.
        # Минимум, который можно сказать честно по одному ключу: минусовый peer —
        # группа. Иначе ход из окна в группу после рестарта шёл бы личкой.
        return {"is_dm": not str(chat_id).startswith("-"), "title": ""}

    def _registry(self, chat_id: str, archive: Path) -> None:
        # Одна реализация реестра на оба транспорта (transport._registry): при
        # ошибке чтения архива прежние числа НЕ затираются нулём, а счёт идёт
        # приращением, а не полным перечитыванием файла на каждом сообщении.
        meta = self.meta(chat_id)
        _registry(self.tree, str(chat_id), archive,
                  participants=int(meta.get("size") or 2))

    def record(self, chat_id: str, text: str, *, outgoing: bool, sender: str = "",
               source_id: str = "", ts: float | None = None,
               source: str = "botapi") -> None:
        import datetime as dt
        moment = dt.datetime.fromtimestamp(ts, dt.timezone.utc) if ts else \
            dt.datetime.now(dt.timezone.utc)
        # Автор пишется и у ИСХОДЯЩИХ: имя агента правится в настройках, а без
        # подписи в строке вся прошлая переписка задним числом становилась
        # сказанной новым именем — и в окне, и в ленте, которую читает модель.
        row = {"timestamp": moment.isoformat(timespec="seconds"),
               "outgoing": bool(outgoing), "text": str(text),
               "sender_name": (self.agent_name if outgoing else (sender or "?"))}
        archive = self.tree / "memory" / "groups" / (str(chat_id) + ".jsonl")
        _append_jsonl(archive, row)
        self._registry(chat_id, archive)
        if self._life is None:
            return
        is_dm = bool(self.meta(chat_id).get("is_dm", True))
        # ⚠ Подпись — по правилу её живого раннера, и оно РАЗНОЕ для лички и группы.
        # В личке подпись снимается с обеих сторон: роль уже говорит, кто говорит, а
        # имя перед словами собеседника превращает его в пересказываемое третье лицо
        # (инцидент 03.08: она отвечала владельцу, говоря о нём в третьем лице). В группе
        # подпись ОСТАЁТСЯ на чужих репликах: говорящих много, и безымянная реплика
        # безадресна — первая же встреча двух агентов в теме 19 это показала: реплика
        # живого агента без подписи неотличима от реплики другого агента.
        line = str(text) if (outgoing or is_dm) else f"{sender or '?'}: {text}"
        try:
            self._life.record_message(
                str(chat_id), line,
                actor=(self.agent_name if outgoing else (sender or "?")),
                direction=("out" if outgoing else "in"), source=source,
                source_id=source_id or None,
                is_dm=is_dm,
                ts=moment.timestamp(),
                dedupe_key=(f"{source}:{chat_id}:{source_id}:"
                            f"{'out' if outgoing else 'in'}" if source_id else ""))
        except Exception:
            log.exception("событие жизни не записалось [%s]", chat_id)

    def lines(self, chat_id: str, limit: int = 200) -> list[str]:
        archive = self.tree / "memory" / "groups" / (str(chat_id) + ".jsonl")
        # errors="replace": один битый байт архива (жёсткое убийство руннера
        # посреди записи, кончившийся диск) роняет ход UnicodeDecodeError —
        # подклассом ValueError, мимо `except OSError`, — и агент глохнет на
        # КАЖДОМ следующем сообщении, пока окно показывает чат целым.
        try:
            with archive.open(encoding="utf-8", errors="replace") as src:
                raw = src.readlines()[-max(1, int(limit)):]
        except OSError:
            return []
        out = []
        for line in raw:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("system"):
                continue
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            who = str(row.get("sender_name") or "").strip() or (
                self.agent_name if row.get("outgoing") else "?")
            out.append(f"{who}: {text}")
        return out

    def dm_archives(self) -> list[str]:
        """Чаты-лички бота (для поиска по личкам): положительный id = человек."""
        root = self.tree / "memory" / "groups"
        if not root.is_dir():
            return []
        return [p.stem for p in root.glob("*.jsonl")
                if p.stem.lstrip("-").isdigit() and not p.stem.startswith("-")]


# --------------------------------------------------------------------------- #
#  Сам транспорт
# --------------------------------------------------------------------------- #

def _placeholder(message: dict) -> str:
    """Медиа без STT/глаз в v1 — честный плейсхолдер с тем, что знаем."""
    caption = str(message.get("caption") or "").strip()
    if "photo" in message:
        base = "[фото]"
    elif "voice" in message:
        base = (f"[голосовое, {int((message['voice'] or {}).get('duration') or 0)} с "
                "— расшифровки в этой версии нет]")
    elif "video_note" in message:
        base = "[кружок — просмотра в этой версии нет]"
    elif "audio" in message:
        base = f"[аудио: {(message['audio'] or {}).get('file_name') or ''}]".replace(": ]", "]")
    elif "document" in message:
        base = f"[файл: {(message['document'] or {}).get('file_name') or 'без имени'}]"
    elif "sticker" in message:
        base = f"[стикер {(message['sticker'] or {}).get('emoji') or ''}]".replace(" ]", "]")
    elif "video" in message:
        base = "[видео]"
    elif "location" in message:
        loc = message["location"] or {}
        base = f"[локация {loc.get('latitude')}, {loc.get('longitude')}]"
    else:
        return caption
    return (base + ("\n" + caption if caption else "")).strip()


def _chunks(text: str, limit: int = _MSG_LIMIT) -> list[str]:
    """Резка длинного текста по абзацам, затем по строкам, затем жёстко."""
    body = str(text or "")
    if len(body) <= limit:
        return [body]
    out: list[str] = []
    current = ""
    for para in body.split("\n\n"):
        candidate = (current + "\n\n" + para) if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            out.append(current)
            current = ""
        while len(para) > limit:
            cut = para.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            out.append(para[:cut])
            para = para[cut:].lstrip("\n")
        current = para
    if current:
        out.append(current)
    return out


def peer_thread(conversation_id: str) -> tuple[str, int | None]:
    """Ключ разговора -> (peer_id, тема|None).

    Конвенция ЖИВОГО дерева, дословно: место = настоящая тема форума, ключ
    `<chat>__topic__<id>` (см. telegram_routes; читатели окна режут по нему же).
    Обычный чат — ключ без суффикса.
    """
    peer, _, thread = str(conversation_id).partition("__topic__")
    return peer, (int(thread) if thread.isdigit() else None)


def is_telegram_key(ref: str) -> bool:
    """Похож ли адрес на телеграмный ключ (id чата, с темой или без)."""
    return peer_thread(ref)[0].lstrip("-").isdigit()


class BotTransport:
    def __init__(self, agent_mod, tree: Path, memory_life, cfg: dict):
        tg = dict(cfg.get("telegram") or {})
        self.agent = agent_mod
        self.tree = Path(tree)
        self.client = BotClient(str(tg.get("bot_token") or ""))
        # Пульс хода (turn_pulse): последний входящий id по комнате — чтобы правка
        # поста «думаю» не перебивала человека, написавшего следом; крючок перед
        # первой отправкой наружу — снять пост до ответа.
        self.last_incoming: dict[str, int] = {}
        self.before_send = None
        # .strip(): id сверяется СТРОКОЙ (`ident == str(self.owner_id)`), и
        # " 111 " из руками правленного helene.json не совпал бы с "111"
        # никогда — владелец получил бы бота, молчащего лично на него.
        self.owner_id = str(tg.get("owner_id") or "").strip()
        # Кому позволено ЗАПУСКАТЬ ход на деньги и на машине владельца.
        # По умолчанию — только владельцу: имя бота публично по построению, и до
        # этой правки любой посторонний, написавший боту, получал полный ход
        # агента (93 руки из 95, включая shell, fs_write, git, restart_self).
        self.allow_from = str(tg.get("allow_from") or "owner").strip().lower()
        if self.allow_from not in ("owner", "listed", "any"):
            log.warning("telegram.allow_from = %r — не знаю такого; беру owner",
                        self.allow_from)
            self.allow_from = "owner"
        self.allowed_ids = {str(x).strip() for x in (tg.get("allowed_ids") or [])
                            if str(x).strip()}
        self._stuck: dict = {}      # апдейт, который не переваривается: {id, n}
        self._muted: set[str] = set()   # о ком уже сказано «не отвечаю» — в лог один раз
        owner_name = str((cfg.get("owner") or {}).get("name") or "владелец")
        self.owner_name = owner_name
        agent_name = boot.agent_name(cfg)
        self.rooms = Rooms(tree, memory_life, agent_name)
        self.contacts = Contacts(tree)
        self.me: dict = {}
        self.username = ""
        self._offset_path = tree / "memory" / ".state" / "botapi_offset.json"
        self._topic_names: dict[tuple[str, int], str] = {}   # (chat, тема) -> имя
        self._pending: deque[str] = deque()
        self._pending_set: set[str] = set()
        self._queue_lock = threading.Lock()
        self._last_poll_ok = 0.0
        self._stop = threading.Event()
        self.sent_now: list[tuple[str, str]] = []   # (chat_id, text) этого хода
        if not self.owner_id and self.allow_from != "any":
            log.warning("telegram.owner_id не задан: владельца в Telegram нет, и ход "
                        "по входящему не пойдёт ни от кого. Впиши свой id в "
                        "настройках (или telegram.allow_from=\"any\", если бот "
                        "заведомо открыт всем).")

    # ------------------------------------------------------------- жизнь
    def start(self) -> None:
        self.me = self.client.call("getMe")
        self.username = str(self.me.get("username") or "")
        thread = threading.Thread(target=self._poll_forever,
                                  name="botapi-poll", daemon=True)
        thread.start()
        log.info("бот: @%s (id %s) — long-poll запущен%s", self.username,
                 self.me.get("id"),
                 "" if self.me.get("can_read_all_group_messages")
                 else " · ⚠ privacy mode ВКЛЮЧЁН: в группах бот видит только адресованное"
                      " (/setprivacy off у BotFather)")

    def stop(self) -> None:
        self._stop.set()

    def connected(self) -> bool:
        return (time.time() - self._last_poll_ok) < _STALE_SEC

    def pop_pending(self) -> str | None:
        with self._queue_lock:
            if not self._pending:
                return None
            chat_id = self._pending.popleft()
            self._pending_set.discard(chat_id)
            return chat_id

    def _enqueue(self, chat_id: str) -> None:
        with self._queue_lock:
            if chat_id not in self._pending_set:
                self._pending_set.add(chat_id)
                self._pending.append(chat_id)

    # ------------------------------------------------------------- приём
    def _load_offset(self) -> int:
        try:
            return int(json.loads(self._offset_path.read_text(encoding="utf-8"))
                       .get("offset") or 0)
        except (OSError, ValueError):
            return 0

    def _save_offset(self, offset: int) -> None:
        _write_json(self._offset_path, {"offset": int(offset)})

    def _poll_forever(self) -> None:
        offset = self._load_offset()
        while not self._stop.is_set():
            try:
                updates = self.client.call(
                    "getUpdates", _http_timeout=_POLL_TIMEOUT + 10,
                    offset=offset or None, timeout=_POLL_TIMEOUT,
                    allowed_updates=["message"])
            except BotApiError as exc:
                self._last_poll_ok = 0.0
                if exc.code == 409:
                    # Второй поллер этого же токена. Молча делить апдейты нельзя:
                    # половина сообщений уезжала бы в чужой процесс.
                    log.error("getUpdates 409: этот токен уже поллит кто-то ещё — "
                              "жду 30 с (%s)", exc.description)
                    time.sleep(30)
                    continue
                log.warning("getUpdates упал: %s — пауза %d c", exc,
                            max(3, int(exc.retry_after or 3)))
                time.sleep(max(3, exc.retry_after or 3))
                continue
            except Exception as exc:
                self._last_poll_ok = 0.0
                log.warning("сеть getUpdates: %s — пауза 5 с", exc)
                time.sleep(5)
                continue
            self._last_poll_ok = time.time()
            stalled = False
            for update in updates or []:
                update_id = int(update.get("update_id") or 0)
                try:
                    self._ingest(update)
                except Exception:
                    # ⚠ Курсор двигался ЗДЕСЬ ЖЕ, на упавшем апдейте, и
                    # подтверждённый offset — необратимое удаление сообщения на
                    # стороне Telegram: слово владельца исчезало навсегда, вопреки
                    # обещанию шапки этого файла («упали между — Telegram отдаст
                    # сообщение снова»). Теперь курсор стоит на непереваренном
                    # апдейте и Telegram отдаёт его снова (dedupe_key отсеет
                    # повтор) — но не вечно: неперевариваемый апдейт после
                    # _INGEST_TRIES попыток пропускается ГРОМКО, иначе одна
                    # битая запись заглушила бы весь транспорт навсегда.
                    if self._stuck.get("id") == update_id:
                        self._stuck["n"] = int(self._stuck.get("n") or 0) + 1
                    else:
                        self._stuck = {"id": update_id, "n": 1}
                    if self._stuck["n"] >= _INGEST_TRIES:
                        log.exception("апдейт %s не переваривается %d раз — ПРОПУСКАЮ "
                                      "его (сообщение потеряно): %s", update_id,
                                      self._stuck["n"], str(update)[:200])
                        self._stuck = {}
                    else:
                        log.exception("апдейт %s не переварился (попытка %d) — курсор "
                                      "оставляю на нём: %s", update_id,
                                      self._stuck["n"], str(update)[:200])
                        stalled = True
                        break
                else:
                    if self._stuck.get("id") == update_id:
                        self._stuck = {}
                offset = max(offset, update_id + 1)
            if updates:
                # Восприятие уже в памяти — теперь можно подтвердить курсор.
                self._save_offset(offset)
            if stalled:
                time.sleep(2)      # не крутить холостой цикл на застрявшем апдейте

    def _ingest(self, update: dict) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = str(chat.get("id") or "")
        if not chat_id or bool(sender.get("is_bot")):
            return                      # чужих ботов не слушаем: петли и эхо
        is_dm = str(chat.get("type") or "") == "private"
        sender_name = " ".join(x for x in (sender.get("first_name"),
                                           sender.get("last_name")) if x).strip() \
            or (sender.get("username") or "кто-то")
        title = str(chat.get("title") or sender_name)
        # Место = настоящая тема форума, не адрес реплая (закон её механики мест).
        # Каждая тема — свой разговор: свой архив, свой кадр, своя эпоха.
        thread_id = (int(message.get("message_thread_id") or 0)
                     if message.get("is_topic_message") else None)
        conversation = f"{chat_id}__topic__{thread_id}" if thread_id else chat_id
        self.last_incoming[conversation] = int(message.get("message_id") or 0)
        created = message.get("forum_topic_created")
        if isinstance(created, dict) and created.get("name"):
            # Служебное сообщение создания темы: имени больше взять неоткуда —
            # getForumTopics ботам не дают.
            self._topic_names[(chat_id, int(message.get("message_thread_id")
                                            or 0))] = str(created["name"])
        replied_topic = ((message.get("reply_to_message") or {})
                         .get("forum_topic_created") or {})
        if thread_id and replied_topic.get("name"):
            self._topic_names[(chat_id, thread_id)] = str(replied_topic["name"])
        if thread_id:
            topic = self._topic_names.get((chat_id, thread_id)) or f"тема {thread_id}"
            title = f"{title} · {topic}"
        self.rooms.describe(conversation, title=title, is_dm=is_dm,
                            sender=(sender_name, str(sender.get("id") or "")))
        self.contacts.note(str(sender.get("id") or ""), name=sender_name,
                           username=str(sender.get("username") or ""), kind="user")
        if not is_dm:
            self.contacts.note(chat_id, name=str(chat.get("title") or title),
                               kind=str(chat.get("type")))
        text = str(message.get("text") or "").strip() or _placeholder(message)
        if not text:
            return
        self.rooms.record(conversation, text, outgoing=False, sender=sender_name,
                          source_id=str(message.get("message_id") or ""),
                          ts=float(message.get("date") or 0) or None)
        if not (is_dm or self._addressed(message)):
            return
        sender_id = str(sender.get("id") or "")
        if not self.is_allowed(sender_id):
            # ⚠ ГЛАВНЫЙ ГЕЙТ ПРОДУКТА, которого здесь не было вовсе.
            # Имя бота от BotFather публично и ищется поиском в Telegram. Любой
            # посторонний, написавший боту «привет», запускал на компьютере
            # владельца полноценный ход агента: вызов модели с префиксом ~19к
            # токенов, неограниченный тул-цикл и руки shell, fs_write, git,
            # restart_self, manage_service, computer. owner_id при этом был не
            # гейтом, а ярлыком — он читался ПОСЛЕ запуска хода, только чтобы
            # выбрать аудиторию. Ни белого списка, ни лимита частоты не было.
            # Сообщение по-прежнему ложится в память (агент видит, что ему
            # писали), но ход от него не идёт.
            if sender_id not in self._muted:
                self._muted.add(sender_id)
                log.warning("сообщение от %s (%s) записано, но хода не будет: "
                            "запускать ход может только владелец "
                            "(telegram.allow_from=%s)", sender_name, sender_id,
                            self.allow_from)
            return
        self._enqueue(conversation)

    def is_allowed(self, sender_id) -> bool:
        """Может ли этот человек ЗАПУСТИТЬ ход. Владелец — всегда.

        `telegram.allow_from`: "owner" (по умолчанию) — только владелец;
        "listed" — владелец и `telegram.allowed_ids`; "any" — кто угодно (так
        было до 03.09; оставлено как явное, названное владельцем решение).
        """
        ident = str(sender_id or "")
        if self.allow_from == "any":
            return True
        if self.owner_id and ident == str(self.owner_id):
            return True
        if self.allow_from == "listed" and ident in self.allowed_ids:
            return True
        if not self.owner_id and self.allow_from == "owner":
            # owner_id не задан: владельца в Telegram нет вовсе, и «только
            # владелец» означает «никто». Это честнее, чем «кто угодно», и уже
            # названо предупреждением при старте транспорта.
            return False
        return False

    def _addressed(self, message: dict) -> bool:
        """В группе ход начинается только с обращения: @username или reply боту.
        Всё остальное она видит памятью, но не отвечает — v1 без самоинициативы."""
        text = str(message.get("text") or message.get("caption") or "").lower()
        if self.username and ("@" + self.username.lower()) in text:
            return True
        replied = (message.get("reply_to_message") or {}).get("from") or {}
        return str(replied.get("id") or "") == str(self.me.get("id") or "")

    # ----------------------------------------------------------- доставка
    def _before_send(self) -> None:
        """Крючок пульса хода: снять пост «думаю» ровно перед первым словом наружу."""
        hook = self.before_send
        if callable(hook):
            try:
                hook()
            except Exception:
                log.debug("крючок before_send отказал", exc_info=True)

    def deliver_text(self, chat_id: str, text: str, reply_to: str = "") -> str:
        self._before_send()
        peer, thread = peer_thread(chat_id)
        parts = _chunks(text)
        first_id = None
        for i, part in enumerate(parts):
            params: dict = {"chat_id": peer, "text": part}
            if thread is not None:
                params["message_thread_id"] = thread
            target = str(reply_to or "").strip().lstrip("#")
            if i == 0 and target.isdigit():
                params["reply_parameters"] = {"message_id": int(target),
                                              "allow_sending_without_reply": True}
            try:
                sent = self.client.call("sendMessage", **params)
            except BotApiError as exc:
                if exc.code == 429 and exc.retry_after:
                    time.sleep(min(30.0, exc.retry_after + 0.5))
                    sent = self.client.call("sendMessage", **params)
                elif exc.permanent:
                    if i:
                        raise  # часть уже ушла — это не «не отправила», пусть видно
                    return self.agent.DirectSendRefusal(
                        f"не отправлено: Telegram отказал навсегда — {exc.description}. "
                        f"Боту можно писать только тем, кто сам ему писал, и только "
                        f"пока не заблокировал.")
                else:
                    raise
            if first_id is None:
                first_id = (sent or {}).get("message_id")
        self.rooms.record(chat_id, text, outgoing=True,
                          source_id=str(first_id or ""))
        self.sent_now.append((str(chat_id), str(text)))
        label = str(self.rooms.meta(chat_id).get("title") or "") \
            or self.contacts.label(peer)
        extra = f", частей {len(parts)}" if len(parts) > 1 else ""
        return f"Отправлено → {label} (Telegram, id {first_id}{extra})"

    def deliver_file(self, path: Path, *, chat_id: str, caption: str = "",
                     media_kind: str = "document", voice_note: bool = False) -> str:
        self._before_send()
        peer, thread = peer_thread(chat_id)
        method, field = {"photo": ("sendPhoto", "photo"),
                         "audio": ("sendAudio", "audio"),
                         "video": ("sendVideo", "video")}.get(
            str(media_kind or "document"), ("sendDocument", "document"))
        if voice_note:
            method, field = "sendVoice", "voice"
        try:
            sent = self.client.upload(method, field, Path(path),
                                      chat_id=peer, message_thread_id=thread,
                                      caption=(caption or "")[:_CAPTION_LIMIT] or None)
        except BotApiError as exc:
            if exc.permanent:
                return self.agent.DirectSendRefusal(
                    f"файл не отправлен: Telegram отказал — {exc.description}.")
            raise
        note = f"[файл] {Path(path).name}" + (f"\n{caption}" if caption else "")
        self.rooms.record(chat_id, note, outgoing=True,
                          source_id=str((sent or {}).get("message_id") or ""))
        return (f"Отправлен файл → {self.contacts.label(chat_id)} "
                f"(id {(sent or {}).get('message_id')})")

    def typing(self, chat_id: str) -> None:
        peer, thread = peer_thread(chat_id)
        try:
            self.client.call("sendChatAction", chat_id=peer,
                             message_thread_id=thread, action="typing")
        except Exception:
            pass                        # индикатор — не повод для шума

    # ------------------------------------------------------- пост «думаю…» (F)
    # Служебный пост о ходе (turn_pulse.TurnPulse): отправляется МИМО памяти агента —
    # это не его слово, а плашка транспорта, и в ленту она не пишется.
    def post_status(self, chat_id: str, text: str) -> int | None:
        peer, thread = peer_thread(chat_id)
        params: dict = {"chat_id": peer, "text": text, "disable_notification": True}
        if thread is not None:
            params["message_thread_id"] = thread
        sent = self.client.call("sendMessage", **params)
        mid = (sent or {}).get("message_id") if isinstance(sent, dict) else None
        return int(mid) if mid else None

    def edit_status(self, chat_id: str, message_id: int, text: str) -> bool:
        peer, _thread = peer_thread(chat_id)
        self.client.call("editMessageText", chat_id=peer, message_id=int(message_id), text=text)
        return True

    def delete_status(self, chat_id: str, message_id: int) -> bool:
        peer, _thread = peer_thread(chat_id)
        self.client.call("deleteMessage", chat_id=peer, message_id=int(message_id))
        return True

    def is_last_message(self, chat_id: str, message_id: int) -> bool:
        """Пост ещё последний в комнате: после него никто не писал и агент не отвечал."""
        last_in = int(self.last_incoming.get(str(chat_id), 0) or 0)
        return last_in < int(message_id) and not any(c == str(chat_id) for c, _ in self.sent_now)


# --------------------------------------------------------------------------- #
#  Крючки: маршрутизатор поверх desk-транспорта
# --------------------------------------------------------------------------- #

def install(agent_mod, desks, bot: BotTransport) -> None:
    """Встроить бота в `agent._TELETHON` ПОВЕРХ крючков окна.

    Правило одно: адрес комнаты окна (`window`, `window-<hex>`, имя комнаты,
    имя владельца) — окно, любой числовой Telegram-адрес — бот, неизвестное
    имя — резолв по контактам бота. `desks` — все комнаты окна
    (`transport.Desks`); `desks.default` — комната по умолчанию.
    """
    hooks = agent_mod._TELETHON
    desk_hooks = dict(hooks)            # то, что положил transport.install
    desk = desks.default

    def _is_desk(ref) -> bool:
        ref = str(ref or "").strip()
        return ref in ("", desk.speaker) or desks.find(ref) is not None

    def _reply(chat_id, text, reply_to="") -> str:
        if _is_desk(chat_id):
            return desk_hooks["reply"](chat_id, text, reply_to)
        return bot.deliver_text(str(chat_id), str(text), str(reply_to or ""))

    def _send_message(to, text) -> str:
        # ⚠ Владелец окна и человек в Telegram — ОДИН И ТОТ ЖЕ человек с двумя
        # каналами, и имя между ними двусмысленно. Первый живой прогон это доказал:
        # «владелец» совпал с именем окна, реплика легла в окно, а расписка без канала
        # позволила ей отчитаться «второй транспорт дышит». Правило теперь такое:
        # send_message — рука ДОТЯНУТЬСЯ, поэтому известный Telegram-контакт
        # предпочитается окну; окно достаётся адресам самого окна и владельцу,
        # которого бот (ещё) не видел. Канал в любом случае назван распиской.
        ref = str(to or "").strip()
        if ref == "" or desks.find(ref) is not None and is_room(ref):
            return desk_hooks["send_message"](to, text)
        target = ref if is_telegram_key(ref) else bot.contacts.resolve(ref)
        if target:
            return bot.deliver_text(target, str(text))
        if _is_desk(ref):
            return desk_hooks["send_message"](to, text)
        return agent_mod.DirectSendRefusal(
            f"не отправлено: «{ref}» через бота не известен. Бот пишет только тем, "
            f"кого видел (они писали ему или в общий чат); разыскивать людей по "
            f"имени, как раньше, здесь нечем.")

    def _send_file(path, caption="", to="", media_kind="document",
                   voice_note=False) -> str:
        ref = str(to or "").strip()
        if not ref:
            active = agent_mod._active_chat()
            ref = str(active) if active is not None else desk.stream
        if is_room(ref):
            return desk_hooks["send_file"](path, caption, to, media_kind, voice_note)
        # Тот же порядок, что у send_message: Telegram-контакт раньше имени окна.
        target = ref if is_telegram_key(ref) else bot.contacts.resolve(ref)
        if not target:
            if _is_desk(ref):
                return desk_hooks["send_file"](path, caption, to, media_kind,
                                               voice_note)
            return agent_mod.DirectSendRefusal(
                f"файл не отправлен: адресата «{ref}» бот не видел.")
        src = Path(str(path))
        if not src.is_file():
            return f"Нет файла {path}."
        return bot.deliver_file(src, chat_id=target, caption=str(caption or ""),
                                media_kind=str(media_kind or "document"),
                                voice_note=bool(voice_note))

    def _fetch_context(chat_id, limit: int = 50) -> str:
        if _is_desk(chat_id):
            return desk_hooks["fetch_context"](chat_id, limit)
        lines = bot.rooms.lines(str(chat_id), int(limit))
        return "\n".join(lines) if lines else "(пусто)"

    def _read_chat(chat_ref, limit: int = 30) -> str:
        ref = str(chat_ref or "").strip()
        if _is_desk(ref):
            return desk_hooks["read_chat"](chat_ref, limit)
        target = ref if is_telegram_key(ref) else bot.contacts.resolve(ref)
        if not target:
            return "(такого чата не найдено — бот видит только тех, кто ему писал)"
        lines = bot.rooms.lines(target, int(limit))
        if not lines:
            return ("(архив этого чата пуст: бот помнит только то, что пришло при "
                    "нём — истории до его прихода Telegram ботам не отдаёт)")
        return "\n".join(lines)

    def _search_chats(query: str) -> str:
        rows = bot.contacts.search(query, limit=10)
        head = desk_hooks["search_chats"](query)
        if head and not head.startswith("("):
            rows = [head] + rows          # комнаты окна — первыми
        return "\n".join(rows) if rows else "(ничего не найдено)"

    def _search_private(query: str, limit: int = 20) -> str:
        needle = str(query or "").strip().lower()
        if not needle:
            return "Нужна непустая строка поиска."
        hits: list[str] = []
        desk_part = desk_hooks["search_private_messages"](query, limit)
        if desk_part and not desk_part.startswith("("):
            hits.append(desk_part)
        for cid in bot.rooms.dm_archives():
            label = bot.contacts.label(cid)
            for line in bot.rooms.lines(cid, 2000):
                if needle in line.lower():
                    hits.append(f"[{label}] {line}")
        if not hits:
            return "(ничего не найдено)"
        return "\n".join(hits[-max(1, int(limit)):])

    def _get_id(name_or_username):
        ref = str(name_or_username or "").strip()
        if _is_desk(ref):
            return desk_hooks["get_id"](ref) or desk.stream
        return bot.contacts.resolve(ref)

    def _react(chat="", message_id=0, emoji="", remove=False) -> str:
        target = str(chat or "").strip() or str(agent_mod._active_chat() or "")
        if _is_desk(target):
            return "В окне реакций нет — скажи словами."
        peer, _thread = peer_thread(target)   # реакция висит на сообщении, тема не нужна
        try:
            bot.client.call("setMessageReaction", chat_id=peer,
                            message_id=int(message_id),
                            reaction=([] if remove else
                                      [{"type": "emoji", "emoji": str(emoji)}]))
        except BotApiError as exc:
            return f"Реакция не прошла: {exc.description}"
        return "Сняла реакцию." if remove else f"Поставила {emoji}."

    def _update_profile(about="", first_name="", last_name="") -> str:
        done = []
        name = " ".join(x for x in (str(first_name or "").strip(),
                                    str(last_name or "").strip()) if x)
        try:
            if name:
                bot.client.call("setMyName", name=name[:64])
                done.append(f"имя → «{name[:64]}»")
            if str(about or "").strip():
                bot.client.call("setMyShortDescription",
                                short_description=str(about).strip()[:120])
                done.append("описание обновила")
        except BotApiError as exc:
            return f"Профиль бота не обновился: {exc.description}"
        return ("Готово: " + ", ".join(done)) if done else "Нечего менять."

    def _set_avatar(path) -> str:
        return ("У бота аватар меняется только руками владельца через BotFather "
                "(/setuserpic) — этой руки у меня здесь честно нет.")

    def _transport_state() -> dict:
        return {"connected": bot.connected(), "intentional_window": False}

    hooks.update({
        "reply": _reply,
        "send_message": _send_message,
        "send_file": _send_file,
        "fetch_context": _fetch_context,
        "read_chat": _read_chat,
        "search_chats": _search_chats,
        "search_private_messages": _search_private,
        "get_id": _get_id,
        "resolve_entity": _get_id,
        "send_reaction": _react,
        "update_profile": _update_profile,
        "set_profile_photo": _set_avatar,
        "transport_state": _transport_state,
    })
    log.info("транспорт: бот встроен поверх окна (%d крючков)", len(hooks))
