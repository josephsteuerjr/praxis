# -*- coding: utf-8 -*-
"""Telegram своим аккаунтом (MTProto, Telethon) — адаптер под транспорт бота.

Транспорт `botapi.BotTransport` говорит с Telegram через `client.call(method, …)`
в терминах Bot API: getMe, getUpdates, sendMessage, sendChatAction и загрузка
файлов. Здесь тот же интерфейс поверх Telethon: события клиента складываются в
очередь и отдаются как апдейты Bot API, отправка — через send_message/send_file.
Так весь учёт комнат, контактов и архива остаётся один, а транспортов два.

Аккаунт — отдельный номер телефона для агента (как у агента на сервере автора), не аккаунт
владельца. Вход — mtproto_login.py, сессия — data/telegram/account.session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
import time
import types
from pathlib import Path

import botapi
import telegram_proxy

log = logging.getLogger("helene.mtproto")


def session_path(tree: Path) -> Path:
    return Path(tree) / "telegram" / "account"


class MtprotoClient:
    """`call`/`upload` в терминах Bot API поверх Telethon в своём asyncio-потоке."""

    def __init__(self, api_id: int, api_hash: str, session: Path, *, proxy_url="", proxy_key=""):
        from telethon import TelegramClient  # тяжёлый импорт — только когда нужен

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        name="mtproto-loop", daemon=True)
        self._queue: queue.Queue[dict] = queue.Queue()
        self._batch: list[dict] = []
        self._seq = 0
        self._me = None
        self._handler_installed = False
        session.parent.mkdir(parents=True, exist_ok=True)
        self._proxy_url = proxy_url
        connection = {'connection': telegram_proxy.connection_type(proxy_url, proxy_key)} if proxy_url else {}
        self.client = TelegramClient(str(session), int(api_id), str(api_hash), **connection,
                                     loop=self._loop, sequential_updates=True,
                                     catch_up=True)

    # ------------------------------------------------------------- жизнь
    def _run(self, coro, timeout: float = 60.0):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()
        self._run(self._connect(), timeout=90)

    def close(self) -> None:
        if not self._thread.is_alive():
            self._loop.close()
            return
        try:
            self._run(self.client.disconnect(), timeout=15)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    async def _connect(self) -> None:
        from telethon import events

        if not self._handler_installed:
            self.client.add_event_handler(self._on_message, events.NewMessage())
            self.client.add_event_handler(self._on_edit, events.MessageEdited())
            self.client.add_event_handler(self._on_delete, events.MessageDeleted())
            self._handler_installed = True
        # After Telethon exhausts its automatic reconnects, dispose of the
        # disconnected update task before connecting the same client again.
        if not self.client.is_connected():
            await self.client.disconnect()
            await self.client.connect()
        if not await self.client.is_user_authorized():
            raise botapi.BotApiError("connect", 401,
                                     "аккаунт не вошёл в Telegram — сделай вход в настройках")
        self._me = await self.client.get_me()
        await self.client.catch_up()

    # ------------------------------------------------------------- приём
    async def _on_message(self, event) -> None:
        try:
            update = await self._to_update(event)
        except Exception:
            log.exception("событие Telegram не переварилось")
            return
        if update:
            self._queue.put(update)

    async def _on_edit(self, event) -> None:
        update = await self._to_update(event)
        if update:
            update['_edited'] = True
            self._queue.put(update)

    async def _on_delete(self, event) -> None:
        self._seq += 1
        self._queue.put({'update_id': self._seq, '_deleted': list(event.deleted_ids),
                         '_peer': str(event.chat_id or '')})

    async def _to_update(self, event, *, historical=False) -> dict | None:
        from telethon.tl.types import Channel, MessageActionTopicCreate

        m = event.message
        try:
            chat = await event.get_chat()
        except Exception:
            chat = getattr(event, "chat", None)
        try:
            sender = await event.get_sender()
        except Exception:
            sender = getattr(event, "sender", None)
        if event.is_private:
            ctype = "private"
        elif isinstance(chat, Channel):
            ctype = "channel" if getattr(chat, "broadcast", False) else "supergroup"
        else:
            ctype = "group"
        title = getattr(chat, "title", None) or " ".join(
            x for x in (getattr(chat, "first_name", None), getattr(chat, "last_name", None)) if x)
        frm = {"id": int(getattr(m, "sender_id", 0) or 0)}
        if sender is not None:
            frm = {
                "id": int(getattr(sender, "id", 0) or 0),
                "first_name": getattr(sender, "first_name", None) or getattr(sender, "title", "") or "",
                "last_name": getattr(sender, "last_name", None) or "",
                "username": getattr(sender, "username", None) or "",
                "is_bot": bool(getattr(sender, "bot", False)),
            }
        message = {
            "message_id": int(m.id),
            "date": int(m.date.timestamp()) if m.date else int(time.time()),
            "chat": {"id": int(event.chat_id), "type": ctype, "title": title},
            "from": frm,
        }
        text = m.message or ""
        if m.media is not None:
            message["caption"] = text
            if getattr(m, "photo", None):
                message["photo"] = [{}]
            else:
                file = getattr(m, "file", None)
                kind = next((k for k in ("voice", "video_note", "audio", "video", "sticker")
                             if getattr(m, k, None)), "document")
                message[kind] = {"file_name": getattr(file, "name", "") or "",
                                 "duration": getattr(file, "duration", 0) or 0}
        else:
            message["text"] = text
        reply = getattr(m, "reply_to", None)
        if reply is not None:
            if getattr(reply, "forum_topic", False):
                thread = getattr(reply, "reply_to_top_id", None) or getattr(reply, "reply_to_msg_id", None)
                if thread:
                    message["message_thread_id"] = int(thread)
                    message["is_topic_message"] = True
            if getattr(reply, "reply_to_msg_id", None):
                message["reply_to_message"] = {"message_id": int(reply.reply_to_msg_id)}
                try:
                    replied = None if historical else await m.get_reply_message()
                except Exception:
                    # Unknown reply ownership cannot wake the agent, but must
                    # never discard the incoming words during a network break.
                    replied = None
                sender_id = getattr(replied, "sender_id", None)
                if sender_id:
                    message["reply_to_message"]["from"] = {"id": int(sender_id)}
        action = getattr(m, "action", None)
        if isinstance(action, MessageActionTopicCreate):
            message["forum_topic_created"] = {"name": action.title}
            message["message_thread_id"] = int(m.id)
        self._seq += 1
        return {"update_id": self._seq, "message": message,
                "_outgoing": bool(getattr(m, "out", False)), "_history": bool(historical)}

    async def sync_history(self, tree: Path, ingest, initial_limit: int, stopped) -> None:
        """Reconcile missed posts with the archive before accepting wakes.

        Existing Telegram IDs set the gap boundary. A first import takes the
        configured recent-history window; historical reads never run the model.
        """
        boundaries = {}
        for path in (tree / 'memory' / 'groups').glob('*.jsonl'):
            peer = botapi.peer_thread(path.stem)[0]
            if not peer.lstrip('-').isdigit():
                continue
            for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
                try:
                    mid = int(json.loads(line).get('source_message_id') or 0)
                except (ValueError, TypeError, AttributeError):
                    continue
                boundaries[peer] = max(boundaries.get(peer, 0), mid)
        async for dialog in self.client.iter_dialogs():
            if stopped():
                return
            boundary = boundaries.get(str(dialog.id), 0)
            kwargs = {'min_id': boundary, 'reverse': True} if boundary else {'limit': initial_limit}
            messages = [m async for m in self.client.iter_messages(dialog.entity, **kwargs)]
            if not boundary:
                messages.reverse()
            for message in messages:
                if stopped():
                    return
                async def get_chat(): return dialog.entity
                event = types.SimpleNamespace(is_private=bool(dialog.is_user), chat_id=dialog.id,
                                              get_sender=message.get_sender,
                                              message=message, get_chat=get_chat)
                update = await self._to_update(event, historical=True)
                if update:
                    ingest(update)

    # ------------------------------------------------------------- Bot API
    def call(self, method: str, _http_timeout: float = 30.0, **params):
        if method == "getMe":
            me = self._run(self.client.get_me(), timeout=_http_timeout)
            self._me = me
            return {
                "id": int(getattr(me, "id", 0) or 0),
                "is_bot": bool(getattr(me, "bot", False)),
                "first_name": getattr(me, "first_name", "") or "",
                "last_name": getattr(me, "last_name", "") or "",
                "username": getattr(me, "username", "") or "",
                "can_read_all_group_messages": True,
            }
        if method == "getUpdates":
            if not self.client.is_connected():
                raise ConnectionError("MTProto is disconnected")
            return self._updates(float(params.get("timeout") or 20),
                                 int(params.get("offset") or 0))
        if method == "sendMessage":
            return self._run(self._send_message(params), timeout=_http_timeout)
        # 25.09 (F): у аккаунта тоже видно, что агент думает — «печатает…» идёт
        # SetTypingRequest; пост «думаю…» правится и удаляется штатно.
        if method == "sendChatAction":
            return self._run(self._typing(params), timeout=_http_timeout)
        if method == "editMessageText":
            return self._run(self._edit_message(params), timeout=_http_timeout)
        if method == "deleteMessage":
            return self._run(self._delete_message(params), timeout=_http_timeout)
        if method in ("setMyName", "setMyShortDescription"):
            return self._run(self._update_profile(method, params), timeout=_http_timeout)
        if method == "setMessageReaction":
            return True  # у аккаунта это либо не нужно, либо не про него
        raise botapi.BotApiError(method, 400, "метод не поддержан аккаунтом Telegram")

    async def _typing(self, params: dict) -> bool:
        from telethon.tl.functions.messages import SetTypingRequest
        from telethon.tl.types import SendMessageTypingAction

        chat = int(params["chat_id"])
        thread = params.get("message_thread_id")
        peer = await self.client.get_input_entity(chat)
        await self.client(SetTypingRequest(peer=peer, action=SendMessageTypingAction(),
                                           top_msg_id=int(thread) if thread else None))
        return True

    async def _edit_message(self, params: dict) -> bool:
        await self.client.edit_message(int(params["chat_id"]), int(params["message_id"]),
                                       str(params.get("text") or ""))
        return True

    async def _delete_message(self, params: dict) -> bool:
        await self.client.delete_messages(int(params["chat_id"]), [int(params["message_id"])])
        return True

    async def _update_profile(self, method: str, params: dict) -> bool:
        from telethon.tl.functions.account import UpdateProfileRequest
        args = ({"first_name": str(params.get("name") or ""), "last_name": ""}
                if method == "setMyName" else {"about": str(params.get("short_description") or "")})
        await self.client(UpdateProfileRequest(**args))
        return True

    def _updates(self, wait: float, offset: int = 0) -> list[dict]:
        # A read is not an acknowledgement. Failed ingest receives the same
        # adapter update again until the shared poller advances its offset.
        self._batch = [row for row in self._batch if row["update_id"] >= offset]
        if self._batch:
            return list(self._batch)
        rows: list[dict] = []
        try:
            rows.append(self._queue.get(timeout=max(0.5, min(wait, 25.0))))
        except queue.Empty:
            return rows
        while len(rows) < 100:
            try:
                rows.append(self._queue.get_nowait())
            except queue.Empty:
                break
        self._batch = rows
        return list(rows)

    @staticmethod
    def _parse_mode(value) -> str | None:
        v = str(value or "").lower()
        if v.startswith("html"):
            return "html"
        if v.startswith("markdown"):
            return "md"
        return None

    async def _send_message(self, params: dict) -> dict:
        chat = int(params["chat_id"])
        reply_to = params.get("reply_to_message_id") or params.get("message_thread_id")
        msg = await self.client.send_message(
            chat, str(params.get("text") or ""),
            reply_to=int(reply_to) if reply_to else None,
            parse_mode=self._parse_mode(params.get("parse_mode")),
            link_preview=not bool(params.get("disable_web_page_preview")),
            silent=bool(params.get("disable_notification")))
        return {"message_id": int(msg.id), "chat": {"id": chat}}

    def upload(self, method: str, field: str, path: Path, *, mime: str = "",
               timeout: float = 300.0, **params):
        async def _send():
            chat = int(params["chat_id"])
            reply_to = params.get("reply_to_message_id") or params.get("message_thread_id")
            msg = await self.client.send_file(
                chat, str(path), caption=params.get("caption") or None,
                reply_to=int(reply_to) if reply_to else None,
                voice_note=(method == "sendVoice"),
                force_document=(method == "sendDocument"))
            return {"message_id": int(getattr(msg, "id", 0) or 0)}
        return self._run(_send(), timeout=timeout)


class MtprotoTransport(botapi.BotTransport):
    """Тот же транспорт, что у бота, только клиент — аккаунт по MTProto."""

    def __init__(self, agent_mod, tree: Path, memory_life, cfg: dict):
        super().__init__(agent_mod, tree, memory_life, cfg)
        tg = dict(cfg.get("telegram") or {})
        proxy_url, proxy_key = telegram_proxy.settings(cfg)
        self.client = MtprotoClient(int(tg.get("api_id") or 0), str(tg.get("api_hash") or ""),
                                    session_path(tree), proxy_url=proxy_url, proxy_key=proxy_key)

    @property
    def transport_kind(self) -> str:
        return "MTProto / Telethon" + (" через сервер" if getattr(self.client, "_proxy_url", "") else "")

    def _connect(self) -> None:
        self.client.start()
        super()._connect()
        self._connection_state = 'syncing'
        limit = max(1, int((self._cfg.get('telegram') or {}).get('history_initial_limit') or 100))
        self.client._run(self.client.sync_history(self.tree, self._ingest, limit, self._stop.is_set), timeout=300)

    def _load_offset(self) -> int:
        # Adapter IDs are per process; Telegram's session owns persistent pts.
        return 0

    def _save_offset(self, offset: int) -> None:
        pass

    def stop(self) -> None:
        super().stop()
        self.client.close()

    # --- вступить в чат и выйти (1.2.5) ---------------------------------------------
    #
    # Рука дерева `telegram_account join|leave` зовёт крючки `_TELETHON["join_chat"]` и
    # `["leave_chat"]`; в издании их не клал никто, и агент на своём аккаунте отвечал
    # «Telethon hook недоступен». 28.09 Йону (агент Дмитрия, ботюзер) звали в общий чат
    # агентов, и слово Егора было «пусть заходит сама — дай ей тул». Аккаунт заходит
    # как человек: по ссылке-приглашению или по публичному @имени. У бот-транспорта этих
    # крючков нет и не будет — бот в группу сам не входит, его добавляют.

    def join_chat(self, target) -> str:
        kind, ref = join_target(target)
        if not kind:
            return ("telegram_account join: не понял адрес — нужна ссылка-приглашение "
                    "(t.me/+…), публичное @имя или t.me/имя")
        return self.client._run(self._join(kind, ref), timeout=60)

    def leave_chat(self, target) -> str:
        kind, ref = join_target(target)
        if kind != "public":
            return "telegram_account leave: нужен @имя, t.me/имя или числовой id чата"
        return self.client._run(self._leave(ref), timeout=60)

    async def _entity(self, ref: str):
        client = self.client.client
        return await client.get_entity(int(ref) if ref.lstrip("-").isdigit() else ref)

    async def _join(self, kind: str, ref: str) -> str:
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest
        client = self.client.client
        if kind == "invite":
            result = await client(ImportChatInviteRequest(ref))
        else:
            result = await client(JoinChannelRequest(await self._entity(ref)))
        chats = list(getattr(result, "chats", None) or [])
        title = str(getattr(chats[0], "title", "") or ref) if chats else ref
        return f"Вступление: «{title}» — теперь в участниках."

    async def _leave(self, ref: str) -> str:
        from telethon.tl.functions.channels import LeaveChannelRequest
        from telethon.tl.functions.messages import DeleteChatUserRequest
        client = self.client.client
        entity = await self._entity(ref)
        if getattr(entity, "megagroup", None) is not None or getattr(entity, "broadcast", None) is not None:
            await client(LeaveChannelRequest(entity))
        else:
            await client(DeleteChatUserRequest(entity.id, "me"))
        return f"Выход: «{getattr(entity, 'title', ref)}» — больше не в участниках."


def join_target(target) -> tuple[str, str]:
    """Адрес чата -> ("invite", хэш) | ("public", @имя или id) | ("", ""). Чистая функция.

    Ссылка-приглашение: `t.me/+HASH`, `t.me/joinchat/HASH`, голое `+HASH`. Публичный:
    `@name`, `t.me/name`, `name`, числовой id (со знаком минус тоже).
    """
    text = str(target or "").strip()
    if not text:
        return "", ""
    invite = re.search(r"(?:t(?:elegram)?\.me/)(?:\+|joinchat/)([\w-]+)", text) or re.fullmatch(r"\+([\w-]+)", text)
    if invite:
        return "invite", invite.group(1)
    ref = re.sub(r"^(?:https?://)?(?:t(?:elegram)?\.me)/", "", text).strip("/").lstrip("@")
    if re.fullmatch(r"-?\d+", ref) or re.fullmatch(r"[A-Za-z][\w]{3,}", ref):
        return "public", ref
    return "", ""
