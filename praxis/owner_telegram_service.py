"""Independent Telethon process for the owner's account."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import logging
import mimetypes
import os
from pathlib import Path
import signal
import stat
import time
import uuid

from telethon import TelegramClient, errors, events, functions, types, utils

from core import secrets
import owner_telegram_rpc as rpc
from owner_telegram_wakes import WakeStore, SIGNALS
import telegram_outbox
import telegram_registry
from telegram_topics import parse_selector


log = logging.getLogger(__name__)
STATE = Path("/var/lib/praxis-owner-telegram")
MEDIA = Path("/run/praxis-owner-telegram/media")


def _reference(value: str | int) -> str | int:
    text = str(value).strip()
    if not text:
        raise ValueError("chat is required")
    return int(text) if text.removeprefix("-").isdigit() else text


def _topic(message) -> int | None:
    header = getattr(message, "reply_to", None)
    if header is not None and getattr(header, "forum_topic", False):
        return header.reply_to_top_id or header.reply_to_msg_id
    return None


def _reply(message) -> int | None:
    return getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)


def _message(message, chat: int | None = None) -> dict:
    date = getattr(message, "date", None)
    return {"message": message.id, "chat": chat if chat is not None else message.chat_id,
            "sender": message.sender_id, "text": message.message or "", "topic": _topic(message),
            "reply_to": _reply(message), "outgoing": bool(message.out),
            "date": date.isoformat() if date else None,
            "media": type(message.media).__name__ if message.media else None}


def _limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    return value


class OwnerTelegram:
    def __init__(self, client, account: int, state: Path, media: Path):
        self.client = client
        self.account = account
        self.media = media
        self.wakes = WakeStore(state, account)
        self.outbox = telegram_outbox.TelegramOutbox(state / "outbox")
        self.dispatcher = telegram_registry.TelegramAccountDispatcher(
            caller=self._raw_call, entity_resolver=self.resolve, owner_id=account,
            sovereign_principals=("praxis:self",),
        )
        self.delivery = asyncio.Lock()
        self.notification_cache = {}
        self.state = "starting"
        self.ready_since = time.time()

    async def initialize(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            self.state = "login_required"
            return
        me = await self.client.get_me()
        if me is None or me.id != self.account:
            self.state = "account_mismatch"
            await self.client.disconnect()
            return
        self.ready_since = time.time()
        self.state = "ready"

    async def maintain_connection(self) -> None:
        delay = 1
        while True:
            try:
                await self.initialize()
                if self.state != "ready":
                    return
                delay = 1
                await asyncio.shield(self.client.disconnected)
            except (OSError, errors.RPCError) as exc:
                log.warning("owner Telegram connection failed: %s", type(exc).__name__)
            self.state = "offline"
            await asyncio.sleep(delay)
            delay = min(60, delay * 2)

    async def resolve(self, value, expected_type: str = "", field: str = "", request_name: str = ""):
        reference = _reference(value)
        try:
            peer = await self.client.get_input_entity(reference)
        except ValueError:
            if not isinstance(reference, int):
                raise
            async for dialog in self.client.iter_dialogs():
                if dialog.id == reference:
                    peer = dialog.input_entity
                    break
            else:
                raise
        if expected_type in {"InputChannel", "TypeInputChannel"}:
            return utils.get_input_channel(peer)
        if expected_type in {"InputUser", "TypeInputUser"}:
            return utils.get_input_user(peer)
        if expected_type in {"InputDialogPeer", "TypeInputDialogPeer"}:
            return types.InputDialogPeer(peer=utils.get_input_peer(peer))
        if expected_type in {"InputNotifyPeer", "TypeInputNotifyPeer"}:
            return types.InputNotifyPeer(peer=utils.get_input_peer(peer))
        return utils.get_input_peer(peer)

    async def route(self, chat, topic=None):
        name, suffix = parse_selector(chat)
        if topic is not None and suffix is not None and topic != suffix:
            raise ValueError("chat selector and topic disagree")
        topic = topic if topic is not None else suffix
        if topic is not None and (type(topic) is not int or topic < 1):
            raise ValueError("topic must be a positive integer")
        peer = await self.resolve(name)
        peer_id = self.account if isinstance(peer, types.InputPeerSelf) else utils.get_peer_id(peer)
        return peer, peer_id, topic

    async def read(self, chat, limit=30, before=None, topic=None, message=None) -> dict:
        peer, peer_id, topic = await self.route(chat, topic)
        if message is not None:
            item = await self.client.get_messages(peer, ids=message)
            return {"chat": peer_id, "messages": [self.message(item, peer_id)] if item else []}
        rows = [self.message(item, peer_id) async for item in self.client.iter_messages(
            peer, limit=_limit(limit), offset_id=before or 0, reply_to=topic)]
        return {"chat": peer_id, "topic": topic, "messages": rows,
                "before": rows[-1]["message"] if rows else None}

    def message(self, item, chat=None) -> dict:
        row = _message(item, chat)
        if row["outgoing"] and row["chat"] is not None:
            row["actor"] = "praxis" if self.wakes.was_sent(row["chat"], row["message"]) else "owner_session"
        return row

    async def search(self, query, chat=None, limit=20, topic=None, dialogs=False) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        limit = _limit(limit)
        if dialogs:
            folded = query.strip().removeprefix("@").casefold()
            rows = []
            async for dialog in self.client.iter_dialogs():
                username = getattr(dialog.entity, "username", "") or ""
                if folded in f"{dialog.name} {username}".casefold():
                    rows.append({"chat": dialog.id, "title": dialog.name, "username": username})
                    if len(rows) >= limit:
                        break
            return {"dialogs": rows}
        peer = None
        if chat is not None:
            peer, _, topic = await self.route(chat, topic)
        if topic is not None:
            # Telethon's reply_to iterator ignores search. Search the forum on
            # Telegram and select the exact topic from the returned messages.
            result = await self.client(functions.messages.SearchRequest(
                peer=peer, q=query, filter=types.InputMessagesFilterEmpty(), min_date=None,
                max_date=None, offset_id=0, add_offset=0, limit=limit, max_id=0, min_id=0,
                hash=0, top_msg_id=topic,
            ))
            return {"messages": [self.message(item) for item in result.messages
                                 if isinstance(item, types.Message)]}
        return {"messages": [self.message(item) async for item in self.client.iter_messages(
            peer, search=query, limit=limit)]}

    @staticmethod
    def delivery_identity(execution: dict) -> dict:
        run = execution.get("run_id")
        call = execution.get("call_id")
        if not run or not call:
            raise ValueError("send needs a durable tool invocation")
        identity = hashlib.sha256(f"owner-telegram\0{run}\0{call}".encode()).hexdigest()
        return {"idempotency_key": identity, "run_id": str(run), "call_id": str(call), "purpose": "owner_telegram"}

    async def send(self, execution, chat, text=None, topic=None, reply_to=None, path=None,
                   caption="", kind="document", voice=False) -> dict:
        peer, peer_id, topic = await self.route(chat, topic)
        arguments = {**self.delivery_identity(execution), "peer_id": peer_id,
                     "topic_id": topic, "reply_to": reply_to}
        async with self.delivery:
            if path is None:
                if secrets.credential_floor(text):
                    return {"status": "refused", "reason": "credential_floor"}
                entry = await asyncio.to_thread(self.outbox.prepare_text, text=text, **arguments)
            else:
                supplied = Path(path).expanduser()
                source = supplied.resolve(strict=True)
                if (not source.is_file() or not source.is_relative_to(Path("/app").resolve())
                        and not source.is_relative_to(self.media.resolve())):
                    raise ValueError("file must be in the Praxis workspace or media directory")
                floor = await asyncio.to_thread(secrets.document_floor, source)
                if floor or secrets.credential_by_identity(supplied) or secrets.credential_floor(caption):
                    return {"status": "refused", "reason": "credential_floor"}
                entry = await asyncio.to_thread(
                    self.outbox.prepare_file, source=source, visible_filename=supplied.name,
                    mime=mimetypes.guess_type(supplied.name)[0] or "application/octet-stream",
                    caption=caption, media_kind=kind, voice_note=voice, **arguments,
                )
                if await asyncio.to_thread(secrets.document_floor, entry["payload"]["staged_path"]):
                    return {"status": "refused", "reason": "credential_floor"}
            if entry["state"] != "accepted":
                entry = await self._deliver(entry, peer)
            message = entry["receipt"]["message_id"]
            self.wakes.note_sent(peer_id, message)
            return {"status": "ok", "receipt": {"account": self.account, "chat": peer_id,
                     "topic": topic, **entry["receipt"]}}

    async def _deliver(self, entry, peer) -> dict:
        topic, reply_to = entry["topic_id"], entry["reply_to"]
        reply = types.InputReplyToMessage(reply_to_msg_id=reply_to or topic,
                                          top_msg_id=topic if reply_to and reply_to != topic else None) if topic or reply_to else None
        payload = entry["payload"]
        text = payload.get("text", payload.get("caption", ""))
        text, entities = await self.client._parse_message_text(text, ())
        params = {"peer": peer, "message": text, "entities": entities, "reply_to": reply,
                  "random_id": entry["random_id"]}
        if entry["kind"] == "text":
            request = functions.messages.SendMessageRequest(**params)
        else:
            kind = payload["media_kind"]
            conversion = {"force_document": kind == "document", "as_image": kind == "photo",
                          "voice_note": payload["voice_note"]}
            if kind in {"audio", "document"}:
                conversion.update(mime_type=payload["mime"],
                                  attributes=[types.DocumentAttributeFilename(payload["visible_filename"])])
            handle, media, _ = await self.client._file_to_media(payload["staged_path"], **conversion)
            if media is None:
                raise ValueError("Telegram cannot encode the attachment")
            if handle is not None:
                handle.name = payload["visible_filename"]
            request = functions.messages.SendMediaRequest(media=media, **params)
        response = await self.client(request)
        sent = (response if isinstance(response, types.UpdateShortSentMessage)
                else self.client._get_response_message(request, response, peer))
        if sent is None:
            raise ConnectionError("Telegram returned no identifiable acceptance")
        return await asyncio.to_thread(self.outbox.mark_accepted, entry["key"],
                                       message_id=sent.id, random_id=entry["random_id"])

    async def media_operation(self, execution, action, chat, **fields) -> dict:
        if action == "send":
            if not fields.get("path"):
                raise ValueError("path is required for media send")
            if "message" in fields:
                raise ValueError("use reply_to to reply when sending an attachment")
            return await self.send(execution, chat, **fields)
        if action != "download" or not fields.get("message"):
            raise ValueError("download requires a message id")
        peer, peer_id, _ = await self.route(chat)
        item = await self.client.get_messages(peer, ids=fields["message"])
        if item is None or not item.media:
            return {"status": "not_found", "chat": peer_id, "message": fields["message"]}
        self.media.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory = self.media / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        path = await self.client.download_media(item, file=str(directory) + os.sep)
        if not path:
            directory.rmdir()
            return {"status": "not_found", "chat": peer_id, "message": fields["message"]}
        return {"chat": peer_id, "message": item.id, "path": str(path), "size": Path(path).stat().st_size}

    async def _raw_call(self, request):
        sends = (functions.messages.SendMessageRequest, functions.messages.SendMediaRequest,
                 functions.messages.SendMultiMediaRequest, functions.messages.ForwardMessagesRequest,
                 functions.messages.SendInlineBotResultRequest)
        if not isinstance(request, sends):
            return await self.client(request)
        if secrets.credential_floor(getattr(request, "message", ""),
                                    *(getattr(item, "message", "") for item in getattr(request, "multi_media", ()))):
            raise PermissionError("credential_floor")
        async with self.delivery:
            result = await self.client(request)
            if isinstance(result, types.UpdateShortSentMessage):
                peer = getattr(request, "peer", None) or request.to_peer
                chat = self.account if isinstance(peer, types.InputPeerSelf) else utils.get_peer_id(peer)
                self.wakes.note_sent(chat, result.id)
            for update in getattr(result, "updates", ()):
                if isinstance(update, (types.UpdateNewMessage, types.UpdateNewChannelMessage)):
                    message = update.message
                    if getattr(message, "out", False):
                        self.wakes.note_sent(utils.get_peer_id(message.peer_id), message.id)
        return result

    async def raw(self, action, request=None, query=None, params=None, limit=25, offset=0) -> dict:
        if action == "call":
            arguments = {"name": request, "parameters": params or {}, "mode": "raw"}
        elif action == "describe":
            arguments = {"name": request}
        elif action == "search":
            arguments = {"query": query or "", "limit": limit}
        else:
            arguments = {"limit": limit, "offset": offset}
        result = await self.dispatcher.handle(action, arguments, principal="praxis:self")
        receipt = result.get("receipt")
        if receipt is None:
            return result
        status = receipt["status"]
        error = receipt.get("error") or {}
        transport_errors = {"ConnectionError", "TimeoutError", "OSError", "ServerError", "RandomIdDuplicateError"}
        if status == "error" and error.get("type") in transport_errors:
            status = "in_doubt"
        return {**result, "status": status}

    async def _notify(self, peer):
        key = repr(peer)
        cached = self.notification_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < 600:
            return cached[1]
        current = await self.client(functions.account.GetNotifySettingsRequest(peer))
        self.notification_cache[key] = (time.monotonic(), current)
        return current

    async def notifications_on(self, event, topic) -> bool:
        peer = await event.get_input_chat()
        selectors = [types.InputNotifyPeer(peer)]
        if topic is not None:
            selectors.insert(0, types.InputNotifyForumTopic(peer, topic))
        selectors.append(types.InputNotifyUsers() if event.is_private else
                         types.InputNotifyChats() if event.is_group else types.InputNotifyBroadcasts())
        for selector in selectors:
            settings = await self._notify(selector)
            mute = getattr(settings, "mute_until", None)
            if mute is not None:
                return (mute.timestamp() if hasattr(mute, "timestamp") else mute) <= time.time()
        return True

    async def notification_changed(self, update) -> None:
        if isinstance(update, types.UpdateNotifySettings):
            self.notification_cache.clear()

    async def arrival(self, event) -> None:
        if self.state != "ready" or event.out or event.date.timestamp() < self.ready_since - 1:
            return
        signals = []
        topic = _topic(event.message)
        if getattr(event.message, "mentioned", False):
            signals.append("mention")
        # Ignore forwarded text and compare only the current sender's words.
        if not event.message.fwd_from and self.wakes.matches_name(event.raw_text or ""):
            signals.append("name")
        if event.is_reply:
            try:
                original = await event.get_reply_message()
                if original is not None and original.sender_id == self.account:
                    # Serialize with send-to-receipt so an early update cannot
                    # attribute our own just-accepted send to the owner.
                    async with self.delivery:
                        sent = self.wakes.was_sent(original.chat_id or event.chat_id, original.id)
                    signals.extend(["reply", "reply_to_praxis" if sent else "reply_to_owner"])
            except (errors.RPCError, OSError):
                log.debug("could not resolve the replied-to message")
        try:
            if await self.notifications_on(event, topic):
                signals.append("notifications")
        except (errors.RPCError, OSError):
            log.debug("could not resolve Telegram notification settings")
        self.wakes.observe({"chat": event.chat_id, "topic": topic, "message": event.id,
                            "sender": event.sender_id, "signals": signals})

    async def handle(self, method: str, arguments: dict, execution: dict) -> dict:
        if method == "status":
            return {"status": self.state if self.client.is_connected() or self.state != "ready" else "offline",
                    "account": self.account}
        if self.state != "ready":
            return {"status": self.state}
        try:
            if method == "next_event":
                return {"status": "ok", "event": self.wakes.next_event()}
            if method == "ack_event":
                self.wakes.acknowledge(arguments["id"], arguments["run"])
                return {"status": "ok"}
            if method == "yegor_wake_rules":
                action = arguments.pop("action")
                if action == "get":
                    return {"status": "ok", "policy": self.wakes.policy, "signals": sorted(SIGNALS)}
                if action == "set":
                    return self.wakes.change(**arguments)
                if action == "preview":
                    return self.wakes.preview(arguments.get("rules"))
                raise ValueError("action must be get, set or preview")
            if method == "yegor_send":
                return await self.send(execution, **arguments)
            if method == "yegor_media":
                result = await self.media_operation(execution, **arguments)
            elif method == "yegor_read":
                result = await self.read(**arguments)
            elif method == "yegor_search":
                result = await self.search(**arguments)
            elif method == "yegor_mtproto":
                result = await self.raw(**arguments)
            else:
                raise ValueError("unknown Telegram operation")
            return {"status": "ok", **result}
        except errors.FloodWaitError as exc:
            return {"status": "retry_later", "seconds": exc.seconds}
        except errors.UnauthorizedError:
            self.state = "login_required"
            return {"status": self.state}
        except (ValueError, TypeError, KeyError, telegram_outbox.TelegramOutboxValidationError,
                telegram_outbox.TelegramOutboxConflict) as exc:
            reason = "invalid MTProto arguments" if method == "yegor_mtproto" else str(exc)
            return {"status": "refused", "reason": reason}
        except errors.RPCError as exc:
            status = "in_doubt" if isinstance(exc, (errors.ServerError, errors.RandomIdDuplicateError)) else "refused"
            return {"status": status, "reason": type(exc).__name__}
        except (OSError, EOFError) as exc:
            return {"status": "in_doubt" if rpc.mutates(method, arguments) else "unavailable",
                    "reason": type(exc).__name__}


async def serve(service: OwnerTelegram, path: Path) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_id = None
        try:
            async with asyncio.timeout(10):
                header = await reader.readexactly(rpc.HEADER.size)
                payload = rpc.decode_payload(await reader.readexactly(rpc.frame_size(header)))
            request_id = payload["id"]
            result = await service.handle(payload["method"], payload["arguments"], payload["execution"])
            writer.write(rpc.encode_frame({"id": request_id, "result": result}))
            await writer.drain()
        except (EOFError, TimeoutError, ConnectionError):
            pass
        except Exception as exc:
            # Request bodies can contain account credentials.
            log.warning("owner Telegram RPC failed: %s", type(exc).__name__)
            if request_id is not None:
                status = "in_doubt" if rpc.mutates(payload["method"], payload["arguments"]) else "unavailable"
                writer.write(rpc.encode_frame({"id": request_id, "result": {"status": status,
                                                                             "reason": type(exc).__name__}}))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise RuntimeError("owner Telegram socket path is already occupied")
        try:
            _, writer = await asyncio.open_unix_connection(path)
        except ConnectionRefusedError:
            path.unlink()
        else:
            writer.close()
            await writer.wait_closed()
            raise RuntimeError("owner Telegram service is already listening")
    server = await asyncio.start_unix_server(handle, path)
    path.chmod(0o600)
    stop = asyncio.Event()
    signals = (signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    connection = asyncio.create_task(service.maintain_connection())
    try:
        async with server:
            await stop.wait()
    finally:
        for sig in signals:
            asyncio.get_running_loop().remove_signal_handler(sig)
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        await service.client.disconnect()
        path.unlink(missing_ok=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "login"), nargs="?", default="serve")
    args = parser.parse_args()
    os.umask(0o077)
    state = Path(os.getenv("PRAXIS_OWNER_TELEGRAM_STATE") or STATE)
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    account = int(os.environ["PRAXIS_OWNER_ID"])
    api_id = os.environ.get("PRAXIS_OWNER_TELEGRAM_API_ID") or os.environ["TELEGRAM_API_ID"]
    api_hash = os.environ.get("PRAXIS_OWNER_TELEGRAM_API_HASH") or os.environ["TELEGRAM_API_HASH"]

    async def run() -> None:
        client = TelegramClient(str(state / "owner"), int(api_id), api_hash,
                                catch_up=False, flood_sleep_threshold=0)
        if args.action == "login":
            try:
                await client.start(phone=lambda: input("Telegram phone: "),
                                   code_callback=lambda: getpass.getpass("Telegram code: "),
                                   password=lambda: getpass.getpass("Telegram 2FA: "))
                me = await client.get_me()
                if me is None or me.id != account:
                    raise SystemExit("Telegram account differs from the configured owner")
                print("Owner Telegram session authorized")
            finally:
                await client.disconnect()
            return
        service = OwnerTelegram(client, account, state,
                                Path(os.getenv("PRAXIS_OWNER_TELEGRAM_MEDIA") or MEDIA))
        client.add_event_handler(service.arrival, events.NewMessage(incoming=True))
        client.add_event_handler(service.notification_changed, events.Raw(types.UpdateNotifySettings))
        await serve(service, Path(os.getenv("PRAXIS_OWNER_TELEGRAM_SOCKET") or rpc.DEFAULT_SOCKET))

    # Both login and serve own the same SQLite session for their whole lifetime.
    import fcntl
    with (state / "process.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("Stop the owner Telegram service before opening its session") from exc
        asyncio.run(run())


if __name__ == "__main__":
    main()
