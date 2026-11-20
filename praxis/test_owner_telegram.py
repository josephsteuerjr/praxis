"""Synthetic account and restart coverage for the second Telegram process."""

import asyncio
import datetime as dt
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from telethon import functions, types

import owner_telegram_rpc as rpc
from owner_telegram_service import OwnerTelegram, serve
from owner_telegram_tools import NAMES, TOOLS
from owner_telegram_wakes import WakeStore


class Client:
    def __init__(self, account, *, authorized=True):
        self.account = account
        self.authorized = authorized
        self.connected = False
        self.calls = []
        self.mutes = {}

    async def connect(self):
        self.connected = True
        self.disconnected = asyncio.get_running_loop().create_future()

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.connected = False
        if not self.disconnected.done():
            self.disconnected.set_result(None)

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return SimpleNamespace(id=self.account)

    async def get_input_entity(self, peer):
        return types.InputPeerSelf() if peer == "me" else types.InputPeerUser(int(peer), 0)

    async def _parse_message_text(self, text, parse_mode):
        return text, []

    async def _file_to_media(self, path, **conversion):
        self.conversion = conversion
        upload = types.InputFile(id=12, parts=1, name=Path(path).name, md5_checksum="")
        media = (types.InputMediaUploadedPhoto(file=upload) if conversion["as_image"] else
                 types.InputMediaUploadedDocument(file=upload, mime_type=conversion["mime_type"],
                                                  attributes=conversion["attributes"]))
        return upload, media, conversion["as_image"]

    async def __call__(self, request):
        self.calls.append(request)
        if isinstance(request, (functions.messages.SendMessageRequest, functions.messages.SendMediaRequest)):
            return types.UpdateShortSentMessage(51, 1, 1, dt.datetime.now(dt.timezone.utc))
        if isinstance(request, functions.account.GetNotifySettingsRequest):
            return types.PeerNotifySettings(mute_until=self.mutes.get(type(request.peer)))
        if isinstance(request, functions.help.GetConfigRequest):
            return {"synthetic_account": self.account}
        raise AssertionError(type(request).__name__)


class OwnerTelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = Client(42)
        self.service = OwnerTelegram(self.client, 42, self.root / "state", self.root / "media")

    async def test_rejects_another_authenticated_account(self):
        self.client.account = 21
        await self.service.initialize()
        result = await self.service.handle("yegor_read", {"chat": "me"}, {})
        self.assertEqual(result, {"status": "account_mismatch"})
        self.assertFalse(self.client.connected)
        self.assertEqual(self.client.calls, [])

    async def test_separate_client_and_durable_send_receipt(self):
        native = Client(75)
        await native.connect()
        await self.service.initialize()
        call = {"run_id": "run-example", "call_id": "tool-example"}
        args = {"chat": "me", "text": "synthetic message"}
        first = await self.service.handle("yegor_send", args, call)
        self.assertEqual(first["receipt"]["account"], 42)
        self.assertEqual(first["receipt"]["chat"], 42)
        self.assertEqual(first["receipt"]["message_id"], 51)
        replacement = OwnerTelegram(Client(42), 42, self.root / "state", self.root / "media")
        await replacement.initialize()
        second = await replacement.handle("yegor_send", args, call)
        self.assertEqual(first, second)
        self.assertEqual(replacement.client.calls, [])
        self.assertTrue(replacement.wakes.was_sent(42, 51))
        self.assertEqual(native.calls, [])
        conflict = await replacement.handle("yegor_send", dict(args, text="other intent"), call)
        self.assertEqual(conflict["status"], "refused")

    async def test_raw_discovery_and_execution(self):
        await self.service.initialize()
        query = await self.service.handle("yegor_mtproto", {"action": "search", "query": "GetConfig"}, {})
        self.assertEqual(query["status"], "ok")
        result = await self.service.handle("yegor_mtproto", {
            "action": "call", "request": "functions.help.GetConfigRequest",
        }, {})
        self.assertEqual(result["receipt"]["status"], "ok")
        self.assertEqual(result["receipt"]["result"]["synthetic_account"], 42)

    async def test_staged_photo_and_voice_keep_the_intended_media_type(self):
        await self.service.initialize()
        self.service.media.mkdir()
        for kind, filename in (("photo", "photo.png"), ("audio", "voice.ogg")):
            with self.subTest(kind=kind):
                path = self.service.media / filename
                path.write_bytes(b"synthetic payload")
                result = await self.service.handle("yegor_media", {
                    "action": "send", "chat": "me", "path": str(path), "kind": kind,
                    "voice": kind == "audio",
                }, {"run_id": "run-media", "call_id": kind})
                self.assertEqual(result["receipt"]["media_kind"], kind)
                self.assertEqual(self.client.conversion["as_image"], kind == "photo")
                self.assertEqual(self.client.calls[-1].media.file.name, filename)
                if kind == "audio":
                    self.assertEqual(self.client.conversion["mime_type"], "audio/ogg")
                    self.assertTrue(self.client.conversion["voice_note"])

    async def test_topic_overrides_chat_and_global_rules(self):
        await self.service.initialize()
        peer = types.InputPeerChannel(56, 0)

        async def get_input_chat():
            return peer

        event = SimpleNamespace(is_private=False, is_group=True, get_input_chat=get_input_chat)
        forever = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)
        self.client.mutes = {types.InputNotifyChats: forever, types.InputNotifyForumTopic: 0}
        self.assertTrue(await self.service.notifications_on(event, 4))
        self.assertFalse(await self.service.notifications_on(event, None))
        self.client.mutes[types.InputNotifyPeer] = 0
        await self.service.notification_changed(types.UpdateNotifySettings(types.NotifyPeer(types.PeerChannel(56)),
                                                                             types.PeerNotifySettings()))
        self.assertTrue(await self.service.notifications_on(event, None))

    @unittest.skipUnless(os.name == "posix", "Linux Unix socket and executable adapter")
    async def test_real_socket_and_executable_adapter(self):
        path = self.root / "owner.sock"
        task = asyncio.create_task(serve(self.service, path))
        try:
            for _ in range(100):
                if path.exists() and self.service.state == "ready":
                    break
                await asyncio.sleep(0.01)
            with patch.dict(os.environ, {"PRAXIS_OWNER_TELEGRAM_SOCKET": str(path)}):
                result = await asyncio.to_thread(rpc.run_tool_script, "yegor_send",
                                                 {"chat": "me", "text": "synthetic socket message"},
                                                 {"run_id": "run-test", "call_id": "tool-test"})
            self.assertEqual(result["receipt"]["message_id"], 51)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(path.exists())
        self.assertEqual(rpc.request("status", path=path)["status"], "unavailable")


class WakeTests(unittest.TestCase):
    def test_quiet_default_combinations_and_restart_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = WakeStore(root, 42)
            event = {"chat": -77, "topic": 5, "message": 21, "sender": 52,
                     "signals": ["notifications", "mention", "reply"]}
            store.observe(event)
            self.assertIsNone(store.next_event())
            rules = [{"name": "unmuted and addressed", "all": ["notifications"],
                      "any": ["mention", "reply"], "chat": "-77", "topic": 5},
                     {"name": "reply", "any": ["reply"]}]
            self.assertEqual(store.preview(rules)["matches"][0]["matched"],
                             ["unmuted and addressed", "reply"])
            self.assertEqual(store.change(rules=rules, revision=0)["status"], "ok")
            self.assertEqual(store.change(rules=[], revision=0)["status"], "conflict")
            self.assertTrue(store.matches_name("Здесь ЕГОР?"))
            self.assertFalse(store.matches_name("говорили об Егорове"))
            store.observe(event)
            match = store.next_event()
            store.acknowledge(match["id"], "run-confirmed")
            store = WakeStore(root, 42)
            store.observe(event)
            self.assertIsNone(store.next_event())

    def test_explicit_tool_schemas(self):
        self.assertEqual(len(TOOLS), len(NAMES))
        for schema in TOOLS:
            self.assertTrue(schema["name"].startswith("yegor_"))
            self.assertFalse(schema["input_schema"]["additionalProperties"])
