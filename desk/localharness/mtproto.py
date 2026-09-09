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
import logging
import queue
import threading
import time
from pathlib import Path

import botapi

log = logging.getLogger("helene.mtproto")


def session_path(tree: Path) -> Path:
    return Path(tree) / "telegram" / "account"


class MtprotoClient:
    """`call`/`upload` в терминах Bot API поверх Telethon в своём asyncio-потоке."""

    def __init__(self, api_id: int, api_hash: str, session: Path):
        from telethon import TelegramClient  # тяжёлый импорт — только когда нужен

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        name="mtproto-loop", daemon=True)
        self._thread.start()
        self._queue: queue.Queue[dict] = queue.Queue()
        self._seq = 0
        self._me = None
        session.parent.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(str(session), int(api_id), str(api_hash), loop=self._loop)

    # ------------------------------------------------------------- жизнь
    def _run(self, coro, timeout: float = 60.0):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def start(self) -> None:
        self._run(self._connect(), timeout=90)

    def close(self) -> None:
        try:
            self._run(self.client.disconnect(), timeout=15)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    async def _connect(self) -> None:
        from telethon import events

        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise botapi.BotApiError("connect", 401,
                                     "аккаунт не вошёл в Telegram — сделай вход в настройках")
        self._me = await self.client.get_me()
        self.client.add_event_handler(self._on_message, events.NewMessage(incoming=True))

    # ------------------------------------------------------------- приём
    async def _on_message(self, event) -> None:
        try:
            update = await self._to_update(event)
        except Exception:
            log.exception("событие Telegram не переварилось")
            return
        if update:
            self._queue.put(update)

    async def _to_update(self, event) -> dict | None:
        from telethon.tl.types import Channel, MessageActionTopicCreate

        m = event.message
        chat = await event.get_chat()
        sender = await event.get_sender()
        if event.is_private:
            ctype = "private"
        elif isinstance(chat, Channel):
            ctype = "channel" if getattr(chat, "broadcast", False) else "supergroup"
        else:
            ctype = "group"
        title = getattr(chat, "title", None) or " ".join(
            x for x in (getattr(chat, "first_name", None), getattr(chat, "last_name", None)) if x)
        frm = {}
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
        action = getattr(m, "action", None)
        if isinstance(action, MessageActionTopicCreate):
            message["forum_topic_created"] = {"name": action.title}
            message["message_thread_id"] = int(m.id)
        self._seq += 1
        return {"update_id": self._seq, "message": message}

    # ------------------------------------------------------------- Bot API
    def call(self, method: str, _http_timeout: float = 30.0, **params):
        if method == "getMe":
            me = self._me
            return {
                "id": int(getattr(me, "id", 0) or 0),
                "is_bot": False,
                "first_name": getattr(me, "first_name", "") or "",
                "username": getattr(me, "username", "") or "",
                "can_read_all_group_messages": True,
            }
        if method == "getUpdates":
            return self._updates(float(params.get("timeout") or 20))
        if method == "sendMessage":
            return self._run(self._send_message(params), timeout=_http_timeout)
        if method in ("sendChatAction", "setMessageReaction", "setMyName",
                      "setMyShortDescription"):
            return True  # у аккаунта это либо не нужно, либо не про него
        raise botapi.BotApiError(method, 400, "метод не поддержан аккаунтом Telegram")

    def _updates(self, wait: float) -> list[dict]:
        rows: list[dict] = []
        try:
            rows.append(self._queue.get(timeout=max(0.5, min(wait, 25.0))))
        except queue.Empty:
            return rows
        while True:
            try:
                rows.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return rows

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
            link_preview=not bool(params.get("disable_web_page_preview")))
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
        self.client = MtprotoClient(int(tg.get("api_id") or 0), str(tg.get("api_hash") or ""),
                                    session_path(tree))

    def start(self) -> None:
        self.client.start()
        self.me = self.client.call("getMe")
        self.username = str(self.me.get("username") or "")
        thread = threading.Thread(target=self._poll_forever, name="mtproto-poll", daemon=True)
        thread.start()
        log.info("аккаунт Telegram: @%s (id %s) — слушаю события", self.username, self.me.get("id"))

    def stop(self) -> None:
        super().stop()
        self.client.close()
