"""Topic-routing regressions for Telegram forum groups and discussion threads.

This suite is hermetic: no Telegram connection, subprocess, or Forge worker is used.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import datetime
import os
import sys
import tempfile
import time
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("PRAXIS_TEST", "1")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault("TELEGRAM_SESSION", ":memory:")

import agent
import media
import telegram_topics


class _ImportOnlyTelegramClient:
    def __init__(self, *args, **kwargs):
        pass

    def on(self, *args, **kwargs):
        return lambda fn: fn


class _ImportOnlyEvents:
    @staticmethod
    def NewMessage(*args, **kwargs):
        return object()

    @staticmethod
    def ChatAction(*args, **kwargs):
        return object()


if "mtproto_runner" not in sys.modules:
    _prior_telethon = sys.modules.get("telethon")
    _fake_telethon = types.ModuleType("telethon")
    _fake_telethon.TelegramClient = _ImportOnlyTelegramClient
    _fake_telethon.events = _ImportOnlyEvents
    sys.modules["telethon"] = _fake_telethon
    try:
        import mtproto_runner as runner
    finally:
        if _prior_telethon is None:
            sys.modules.pop("telethon", None)
        else:
            sys.modules["telethon"] = _prior_telethon
else:
    import mtproto_runner as runner


class _ReplyHeader:
    def __init__(self, *, top=None, immediate=None, forum=True):
        self.reply_to_top_id = top
        self.reply_to_msg_id = immediate
        self.forum_topic = forum


class _Message:
    def __init__(self, mid: int, text: str, topic: int):
        self.id = mid
        self.message = text
        self.reply_to = _ReplyHeader(top=topic, immediate=topic)
        self.reply_to_msg_id = topic
        self.reply_to_top_id = topic
        self.mentioned = True
        self.is_reply = False
        self.date = datetime.datetime.now(datetime.timezone.utc)


class _Event:
    is_private = False

    def __init__(self, peer: int, msg: _Message, sender_id: int = 501):
        self.chat_id = peer
        self.message = msg
        self.sender_id = sender_id
        self._sender = types.SimpleNamespace(
            id=sender_id, first_name="Миша", last_name="", username="misha",
            usernames=(), is_self=False,
        )

    async def get_sender(self):
        return self._sender

    async def get_chat(self):
        return types.SimpleNamespace(title="Micellium", participants_count=20)


class _Client:
    def __init__(self):
        self.sent = []
        self.files = []
        self.history = []

    async def send_message(self, entity, text, **kwargs):
        self.sent.append((entity, text, kwargs))
        return types.SimpleNamespace(id=900 + len(self.sent))

    async def send_file(self, entity, path, **kwargs):
        self.files.append((entity, path, kwargs))
        return types.SimpleNamespace(id=950 + len(self.files))

    async def get_messages(self, entity, **kwargs):
        self.history.append((entity, kwargs))
        return []

    def action(self, *args, **kwargs):
        # DM path shows a typing indicator via `async with client.action(...)`.
        return _typing_noop()


@contextlib.asynccontextmanager
async def _typing_noop():
    yield


class _RawClient:
    """Tiny callable Telethon-shaped client for the raw idempotent send path."""

    def __init__(self):
        self.requests = []
        self.conversions = []

    async def get_input_entity(self, entity):
        return ("input", entity)

    async def _file_to_media(self, path, **kwargs):
        self.conversions.append((path, kwargs))
        return None, ("media", path, kwargs), None

    async def _parse_message_text(self, message, parse_mode):
        return message, ["parsed-entity"]

    async def __call__(self, request):
        self.requests.append(request)
        return object()

    def _get_response_message(self, request, response, input_entity):
        return types.SimpleNamespace(id=977)


def _wake(mid: int, topic_text: str) -> runner.GroupWake:
    return runner.GroupWake(
        message_id=mid,
        message_ts=1.0,
        kind="mention",
        speaker="Миша",
        sender_id=501,
        owner=False,
        known=True,
        family=False,
        context_snapshot=topic_text,
        reply_targets_snapshot=((mid, "Миша", topic_text),),
        media_snapshot=(),
    )


async def _no_compact(_chat_id):
    return None


async def _drive_run_pass(chat_id, patches):
    """PASS 29 helper: enter many patches via ExitStack so the giant patch list does not
    blow Python's 20 statically-nested-block limit (parenthesized `with` nests each item)."""
    with contextlib.ExitStack() as stack:
        for pt in patches:
            stack.enter_context(pt)
        with contextlib.suppress(asyncio.CancelledError):
            await runner._run_pass(chat_id)
        await asyncio.sleep(0)


def _run_here(factory, _timeout):
    return asyncio.run(factory())


class TestTopicContract(unittest.TestCase):
    def test_message_shapes_route_by_header_only_with_the_catalogue(self):
        """Формы заголовка читаются как раньше, но минтят ключ только по каталогу."""
        catalogue = {55, 77, 91}
        message = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=77, immediate=88),
            reply_to_top_id=None,
        )
        self.assertEqual(
            telegram_topics.route_for_message(
                -100123, message, is_forum=True,
                confirmed_topics=catalogue).conversation_id,
            "-100123__topic__77",
        )
        root_reply = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=None, immediate=91, forum=True),
            reply_to_top_id=None,
        )
        self.assertEqual(
            telegram_topics.route_for_message(
                -100123, root_reply, is_forum=True,
                confirmed_topics=catalogue).topic_id, 91)
        projected = types.SimpleNamespace(
            reply_to=None, reply_to_top_id=55, forum_topic=False)
        self.assertEqual(
            telegram_topics.route_for_message(
                -100123, projected, is_forum=True,
                confirmed_topics=catalogue).topic_id, 55)
        self.assertIsNone(
            telegram_topics.route_for_message(123, projected, is_private=True).topic_id)

    def test_zero_projected_top_falls_back_to_forum_opener(self):
        message = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=0, immediate=77, forum=True),
            reply_to_top_id=0, reply_to_msg_id=77, forum_topic=True,
        )
        self.assertEqual(
            telegram_topics.route_for_message(
                -100123, message, is_forum=True,
                confirmed_topics={77}).topic_id, 77,
        )

    def test_ordinary_supergroup_is_one_room_not_a_topic_per_reply_chain(self):
        """Регрессия 24.07.2026: реплай-цепочка в обычной группе рвала разговор надвое.

        Telegram не ставит ``reply_to_top_id`` на первый ответ, поэтому корень цепочки и
        первый ответ на него уходили на голый peer, а всё остальное — на
        ``__topic__<корень>``. Итог в живом -1001240718803: 164 буфера на один чат и её
        собственная реплика «у меня в топик попал только твой ответ, без сообщения #93708».
        """

        opener = types.SimpleNamespace(reply_to=None, reply_to_top_id=None, id=93707)
        first_reply = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=None, immediate=93707), reply_to_top_id=None)
        deep_reply = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=93707, immediate=93708), reply_to_top_id=None)

        keys = {
            telegram_topics.route_for_message(
                -1001240718803, message, is_forum=False).conversation_id
            for message in (opener, first_reply, deep_reply)
        }
        self.assertEqual(keys, {"-1001240718803"})

    def test_real_forum_keeps_its_topics_only_by_catalogue(self):
        """Грибница — настоящий форум: тема остаётся местом ТОЛЬКО по каталогу.

        Её решение 22.08 отменило переходное «каталог не наблюдался — верим
        заголовку»: первый же ход успевал навсегда оставить фантомный хвост
        (`peer__topic__999` до свипа, комната после — одна логическая беседа в двух
        ключах). Отсутствие полного знания означает комнату; первую реплику
        настоящей темы спасает single-flight preflight вызывающего.
        """

        deep_reply = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=7849, immediate=7850), reply_to_top_id=None)
        self.assertEqual(
            telegram_topics.route_for_message(
                -1001152779373, deep_reply, is_forum=True).conversation_id,
            "-1001152779373",
            "нет каталога — нет темы: fail-safe комната, не заголовок",
        )
        self.assertEqual(
            telegram_topics.route_for_message(
                -1001152779373, deep_reply, is_forum=True,
                confirmed_topics={7849, 8000}).conversation_id,
            "-1001152779373__topic__7849",
        )

    def test_unknown_room_nature_files_under_the_room(self):
        """«Не знаю» для КЛЮЧА ХРАНЕНИЯ значит «не форум»: комната, не фантомная тема.

        Прежде ``is_forum=None`` оставляло поведение по заголовку — так новая комната
        минтила выдуманные места весь срок до вердикта (AbstractDL: три недели, 552
        фантомные темы, 59% сообщений; замер 21.08.2026). Адрес ветки при этом жив:
        ``thread_root_for_message`` считается отдельно и ключом не становится.
        """

        message = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=77, immediate=88), reply_to_top_id=None)
        self.assertEqual(
            telegram_topics.route_for_message(-100123, message).conversation_id,
            "-100123",
        )
        self.assertEqual(
            telegram_topics.route_for_message(
                -100123, message, is_forum=None).conversation_id,
            "-100123",
        )
        self.assertEqual(telegram_topics.thread_root_for_message(message), 77)

    def test_catalogue_gates_the_topic_key_in_a_real_forum(self):
        """Тема — ключ хранения, только если её id есть в каталоге Telegram.

        Ветка ответов в General несёт в заголовке корень цепочки — раньше он
        объявлялся «темой» (в настоящих форумах так родилось 145 и 135 фантомов).
        С каталогом такой корень честно падает в комнату, настоящая тема остаётся
        собой, а General (id 1) — это сама комната, и второго хвоста у него нет.
        """

        general_chain = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=None, immediate=93707, forum=True),
            reply_to_top_id=None)
        self.assertEqual(
            telegram_topics.route_for_message(
                -1001152779373, general_chain, is_forum=True,
                confirmed_topics={7849}).conversation_id,
            "-1001152779373",
        )
        general_marker = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=1, immediate=42), reply_to_top_id=None)
        self.assertEqual(
            telegram_topics.route_for_message(
                -1001152779373, general_marker, is_forum=True,
                confirmed_topics={1, 7849}).conversation_id,
            "-1001152779373",
        )
        junk_catalogue = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=7849, immediate=7850), reply_to_top_id=None)
        self.assertEqual(
            telegram_topics.route_for_message(
                -1001152779373, junk_catalogue, is_forum=True,
                confirmed_topics=["7849", "junk"]).conversation_id,
            "-1001152779373__topic__7849",
            "мусорная запись каталога пропускается по одной, настоящая тема живёт",
        )
        self.assertEqual(
            telegram_topics.route_for_message(
                -1001152779373, junk_catalogue, is_forum=True,
                confirmed_topics=()).conversation_id,
            "-1001152779373",
            "пустой каталог — это ответ «тем нет», а не «не знаю»",
        )

    def test_topic_opener_mints_without_the_catalogue(self):
        """Служебное «создана тема» — прямое свидетельство Telegram: каталог мог не успеть.

        Её регрессия 3 от 22.08: опенер при unknown создаёт настоящую тему, но
        опенер General (id=1) всё равно нормализуется в комнату — второй хвост
        General не рождается ни одним путём (её блокер 6).
        """

        opener = types.SimpleNamespace(id=9100, reply_to=None, reply_to_top_id=None)
        opener.action = type("MessageActionTopicCreate", (), {"title": "Новая тема"})()
        self.assertEqual(
            telegram_topics.route_for_message(
                -1001152779373, opener, is_forum=True,
                confirmed_topics={7849}).conversation_id,
            "-1001152779373__topic__9100",
        )
        self.assertEqual(
            telegram_topics.route_for_message(
                -1001152779373, opener, is_forum=None).conversation_id,
            "-1001152779373__topic__9100",
            "опенер минтит и при «не знаю»: он сам — прямое свидетельство",
        )
        general_opener = types.SimpleNamespace(id=1, reply_to=None, reply_to_top_id=None)
        general_opener.action = type(
            "MessageActionTopicCreate", (), {"title": "General"})()
        self.assertEqual(
            telegram_topics.route_for_message(
                -1001152779373, general_opener, is_forum=True,
                confirmed_topics={1, 7849}).conversation_id,
            "-1001152779373",
            "опенер General — это сама комната, второго хвоста нет",
        )

    def test_thread_root_stays_available_as_behavioural_identity(self):
        """Ветка не ключ хранения, но темп и адресация по-прежнему знают о ней."""

        deep = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=93707, immediate=93708), reply_to_top_id=None)
        first = types.SimpleNamespace(
            reply_to=_ReplyHeader(top=None, immediate=93707), reply_to_top_id=None)
        opener = types.SimpleNamespace(reply_to=None, reply_to_top_id=None, id=93707)
        self.assertEqual(telegram_topics.thread_root_for_message(deep), 93707)
        self.assertEqual(telegram_topics.thread_root_for_message(first), 93707)
        self.assertEqual(telegram_topics.thread_root_for_message(opener), 93707)

    def test_selector_and_persisted_key_roundtrip(self):
        selected = telegram_topics.route_from_reference("-100123#topic:77")
        persisted = telegram_topics.route_from_reference(selected.conversation_id)
        self.assertEqual(selected, persisted)
        self.assertEqual(selected.selector, "-100123#topic:77")
        self.assertEqual(
            telegram_topics.route_from_reference("@micellium"),
            telegram_topics.TopicRoute("@micellium"),
        )


class TestIncomingTopicIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_same_peer_and_message_id_stay_in_two_conversations(self):
        peer = -1009990000004
        topic_a = telegram_topics.TopicRoute(str(peer), 101)
        topic_b = telegram_topics.TopicRoute(str(peer), 202)
        refs = [object(), object()]
        capture = AsyncMock(side_effect=[(refs[0], ""), (refs[1], "")])
        arms = Mock()
        buf = collections.defaultdict(lambda: collections.deque(maxlen=100))
        pending = collections.defaultdict(lambda: collections.deque(maxlen=16))
        seen = collections.defaultdict(lambda: collections.deque(maxlen=50))
        recent = collections.defaultdict(lambda: collections.deque(maxlen=12))
        senders = collections.defaultdict(lambda: collections.deque(maxlen=40))
        with contextlib.ExitStack() as stack:
            for target, name, value in (
                (runner, "OWNER_ID", 999),
                (runner, "_buf", buf),
                (runner, "_buf_dirty", set()),
                (runner, "_meta", {}),
                (runner, "_pending_media", pending),
                (runner, "_seen_ids", seen),
                (runner, "_recent_msgs", recent),
                (runner, "_recent_senders", senders),
                (runner, "_group_wakes", {}),
                (runner, "_entity_cache", {}),
                (runner, "_capture_typed_media", capture),
                (runner, "_chat_descriptor", AsyncMock(return_value={
                    "title": "Micellium", "kind": "group", "size": 20,
                })),
                (runner, "_arm", arms),
            ):
                stack.enter_context(patch.object(target, name, value))
            # Комната объявлена настоящим форумом с подтверждёнными темами: с 22.08
            # ключ темы минтится только по каталогу, «не знаю» кладёт в комнату.
            stack.enter_context(patch.object(
                runner, "_known_forum", lambda *a, **k: True))
            stack.enter_context(patch.object(
                runner, "_catalog_for_routing",
                AsyncMock(return_value=frozenset({101, 202}))))
            stack.enter_context(patch.object(runner, "_under_tests", return_value=True))
            stack.enter_context(patch.object(runner.bufstore, "meta_update", return_value=None))
            stack.enter_context(patch.object(runner.rooms, "is_frozen", return_value=False))
            allowed = stack.enter_context(
                patch.object(runner.rooms, "is_allowed", return_value=True))
            stack.enter_context(patch.object(
                runner.rooms, "effective_mode", return_value="normal"))
            stack.enter_context(patch.object(runner.social, "category", return_value="known"))
            stack.enter_context(patch.object(
                runner.telegram_contacts, "observe", return_value=None))
            stack.enter_context(patch.object(
                runner.telegram_followups.LEDGER, "observe_incoming", return_value=None))
            stack.enter_context(patch.object(runner.reflex, "triage", return_value="answer"))
            # Telegram message ids are peer-local, so reusing the same id here proves
            # the dedupe ring itself is topic-local rather than root-peer-local.
            await runner.on_new(_Event(peer, _Message(7, "в первой теме", 101)))
            await runner.on_new(_Event(peer, _Message(7, "во второй теме", 202)))

            self.assertEqual(set(runner._meta), {
                topic_a.conversation_id, topic_b.conversation_id})
            self.assertIn("в первой теме", "\n".join(buf[topic_a.conversation_id]))
            self.assertNotIn("во второй теме", "\n".join(buf[topic_a.conversation_id]))
            self.assertIn("во второй теме", "\n".join(buf[topic_b.conversation_id]))
            self.assertEqual(list(pending[topic_a.conversation_id]), [refs[0]])
            self.assertEqual(list(pending[topic_b.conversation_id]), [refs[1]])
            self.assertEqual(set(runner._group_wakes), {
                topic_a.conversation_id, topic_b.conversation_id})
            self.assertEqual(arms.call_args_list, [
                unittest.mock.call(topic_a.conversation_id),
                unittest.mock.call(topic_b.conversation_id),
            ])
            self.assertEqual(
                [call.kwargs["chat_id"] for call in capture.await_args_list],
                [topic_a.conversation_id, topic_b.conversation_id],
            )
            self.assertEqual(
                [call.args[0] for call in allowed.call_args_list],
                [str(peer), str(peer)],
            )


class TestTopicPassAndHistory(unittest.IsolatedAsyncioTestCase):
    async def test_pass_uses_conversation_context_but_root_peer_delivery(self):
        route_a = telegram_topics.TopicRoute("-10042", 101)
        route_b = telegram_topics.TopicRoute("-10042", 202)
        meta = {
            route_a.conversation_id: {
                "entity": -10042, "peer_id": "-10042", "topic_id": 101,
                "is_dm": False, "is_owner": False, "known": True,
                "family": False, "name": "Миша", "title": "Micellium · topic #101",
                "size": 20, "addressed": True, "addressed_mid": 11,
                "room_mode": "normal",
            },
            route_b.conversation_id: {
                "entity": -10042, "peer_id": "-10042", "topic_id": 202,
                "is_dm": False, "is_owner": False, "known": True,
                "family": False, "name": "Лена", "title": "Micellium · topic #202",
                "size": 20, "addressed": True, "addressed_mid": 22,
                "room_mode": "normal",
            },
        }
        wakes = {
            route_a.conversation_id: _wake(11, "первая тема"),
            route_b.conversation_id: _wake(22, "вторая тема"),
        }
        client = _Client()
        captured = {}
        started = Mock()

        def voice(*args, **kwargs):
            captured.update(args=args, kwargs=kwargs)
            return media.TurnEnvelope(text="ответ в первую", run_id="run-topic-a")

        with (
            patch.object(runner, "client", client),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_last_pass", collections.defaultdict(float)),
            patch.object(runner, "_passing", set()),
            patch.object(runner, "_pending_media", collections.defaultdict(
                lambda: collections.deque(maxlen=16))),
            patch.object(runner, "_recent_msgs", collections.defaultdict(
                lambda: collections.deque(maxlen=12))),
            patch.object(runner, "_missed", {}),
            patch.object(runner, "_cooldown", return_value=0.0),
            patch.object(runner, "_buf_push", return_value=None),
            patch.object(runner, "_maybe_compact", side_effect=_no_compact),
            patch.object(agent, "voice_turn_envelope", side_effect=voice),
            patch.object(agent, "run_delivery_started", started),
            patch.object(agent, "run_delivery_text_chunk_accepted", return_value=None),
            patch.object(agent, "run_delivery_text_accepted", return_value=None),
            patch.object(agent, "run_delivery_finalize_recovered", return_value=True),
            patch.object(agent, "run_delivery_failed", return_value=None),
        ):
            await runner._run_pass(route_a.conversation_id)
            await asyncio.sleep(0)

        self.assertEqual(client.sent, [(-10042, "ответ в первую", {"reply_to": 11})])
        self.assertEqual(captured["kwargs"]["ctx"].chat_id, route_a.conversation_id)
        self.assertEqual(captured["kwargs"]["ctx"].room_chat_id, route_a.peer_id)
        self.assertIn("top_msg_id=101", captured["kwargs"]["orient"])
        text_plan = started.call_args.kwargs["text_plan"]
        self.assertEqual(text_plan["conversation_id"], route_a.conversation_id)
        self.assertEqual(text_plan["peer_id"], route_a.peer_id)
        self.assertEqual(text_plan["topic_id"], route_a.topic_id)
        self.assertEqual(text_plan["chunks"][0]["reply_to"], 11)
        self.assertEqual(text_plan["chunks"][0]["delivery_key"],
                         "run:run-topic-a:chunk:0")
        self.assertNotIn(route_a.conversation_id, wakes)
        self.assertIn(route_b.conversation_id, wakes)

    async def test_text_outbox_replays_saved_chunk_into_exact_topic_without_model(self):
        route = telegram_topics.TopicRoute("-10042", 101)
        other = telegram_topics.TopicRoute("-10042", 202)
        client = _Client()
        plan = {
            "run_id": "run-replay-topic",
            "status": "in_doubt",
            "conversation_id": route.conversation_id,
            "peer_id": route.peer_id,
            "topic_id": route.topic_id,
            "chunks": [],
            "accepted_indices": [0],
            "pending_chunks": [{
                "index": 1,
                "text": "сохранённое продолжение",
                "sha256": "digest",
                "delivery_key": "run:run-replay-topic:chunk:1",
                "reply_to": 101,
            }],
        }
        ack = Mock()
        reconcile = Mock(return_value=True)
        resolver = AsyncMock(return_value=-10042)
        with (
            patch.object(runner, "client", client),
            patch.object(runner, "_meta", {other.conversation_id: {
                "entity": "wrong-topic-entity", "peer_id": other.peer_id,
                "topic_id": other.topic_id,
            }}),
            patch.object(runner, "_TEXT_SENDING", set()),
            patch.object(runner, "_resolve_entity", resolver),
            patch.object(runner, "_buf_push", return_value=None),
            patch.object(runner.unanswered, "resolve", return_value=None),
            patch.object(agent, "run_pending_text_deliveries", return_value=[plan]),
            patch.object(agent, "run_delivery_text_chunk_accepted", ack),
            patch.object(agent, "run_delivery_text_reconcile", reconcile),
        ):
            await runner._text_outbox_once()

        self.assertEqual(client.sent, [
            (-10042, "сохранённое продолжение", {"reply_to": 101}),
        ])
        resolver.assert_awaited_once_with("-10042")
        ack.assert_called_once_with(
            "run-replay-topic", index=1,
            delivery_key="run:run-replay-topic:chunk:1", message_id=901,
        )
        reconcile.assert_called_once_with("run-replay-topic")

    async def test_cold_history_filters_by_topic_root(self):
        route = telegram_topics.TopicRoute("-10042", 101)
        client = _Client()
        with (
            patch.object(runner, "client", client),
            patch.object(runner, "_buf", collections.defaultdict(
                lambda: collections.deque(maxlen=100))),
            patch.object(runner, "_meta", {route.conversation_id: {
                "entity": -10042, "peer_id": "-10042", "topic_id": 101,
            }}),
        ):
            self.assertEqual(await runner._last_n_text(route.conversation_id), "")
        self.assertEqual(client.history[0][0], -10042)
        self.assertEqual(client.history[0][1]["reply_to"], 101)

    async def test_document_only_pass_is_terminal_and_keeps_topic(self):
        route = telegram_topics.TopicRoute("-10042", 101)
        item = media.OutboundMedia(
            kind="document", path=Path("result.txt"),
            mime="application/octet-stream", size=8,
            target_chat_id=route.conversation_id, scope="group",
            queue_id="topic-document-only", run_id="run-document-only",
        )
        meta = {route.conversation_id: {
            "entity": -10042, "peer_id": route.peer_id, "topic_id": route.topic_id,
            "is_dm": False, "is_owner": False, "known": True, "family": False,
            "name": "Миша", "title": "Micellium · topic #101", "size": 20,
            "addressed": True, "addressed_mid": 11, "room_mode": "normal",
        }}
        wakes = {route.conversation_id: _wake(11, "пришли результат")}
        queue_send = AsyncMock(return_value=True)
        started = Mock()
        finalized = Mock(return_value=True)
        failed = Mock()
        client = _Client()
        with (
            patch.object(runner, "client", client),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_last_pass", collections.defaultdict(float)),
            patch.object(runner, "_passing", set()),
            patch.object(runner, "_pending_media", collections.defaultdict(
                lambda: collections.deque(maxlen=16))),
            patch.object(runner, "_recent_msgs", collections.defaultdict(
                lambda: collections.deque(maxlen=12))),
            patch.object(runner, "_missed", {}),
            patch.object(runner, "_cooldown", return_value=0.0),
            patch.object(runner, "_queue_and_send_media", queue_send),
            patch.object(runner, "_maybe_compact", side_effect=_no_compact),
            patch.object(agent, "voice_turn_envelope", return_value=media.TurnEnvelope(
                text="", outbound=(item,), run_id="run-document-only")),
            patch.object(agent, "run_delivery_started", started),
            patch.object(agent, "run_delivery_finalize_recovered", finalized),
            patch.object(agent, "run_delivery_failed", failed),
        ):
            await runner._run_pass(route.conversation_id)
            await asyncio.sleep(0)

        self.assertEqual(client.sent, [])
        self.assertEqual(queue_send.await_count, 1)
        self.assertEqual(queue_send.await_args.args[:2], (-10042, item))
        self.assertEqual(queue_send.await_args.kwargs["reply_to"], 11)
        self.assertEqual(queue_send.await_args.kwargs["state_chat_id"], route.conversation_id)
        started.assert_called_once_with(
            "run-document-only", chat_id=route.conversation_id,
            text_chars=0, media_count=1,
            media_queue_ids=["topic-document-only"],
        )
        finalized.assert_called_once_with("run-document-only", media_count=1)
        failed.assert_not_called()
        self.assertNotIn(route.conversation_id, wakes)

    async def test_media_only_reply_under_cancellation_rearms_not_terminal(self):
        # PASS28 hole-A: a media-only reply cancelled AFTER run_delivery_started must
        # NOT be marked terminal.  The media bytes are not staged into the durable
        # spool until the inline send loop (which the cancellation raise skips), and
        # no recovery clock delivers an unstaged media intent — so the finally must
        # re-arm the wake and let the model re-author+deliver the media (the original
        # known-good path).  Marking it terminal would silently DROP the media.
        route = telegram_topics.TopicRoute("-10042", 101)
        item = media.OutboundMedia(
            kind="document", path=Path("result.txt"),
            mime="application/octet-stream", size=8,
            target_chat_id=route.conversation_id, scope="group",
            queue_id="cancel-media-only", run_id="run-cancel-media",
        )
        meta = {route.conversation_id: {
            "entity": -10042, "peer_id": route.peer_id, "topic_id": route.topic_id,
            "is_dm": False, "is_owner": False, "known": True, "family": False,
            "name": "Миша", "title": "Micellium · topic #101", "size": 20,
            "addressed": True, "addressed_mid": 11, "room_mode": "normal",
        }}
        wakes = {route.conversation_id: _wake(11, "пришли результат")}
        queue_send = AsyncMock(return_value=True)
        started = Mock()
        arm = Mock()
        client = _Client()

        async def cancelled_handoff(awaitable):
            return (await awaitable), True   # inject cancellation at each shielded await

        with (
            patch.object(runner, "client", client),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_last_pass", collections.defaultdict(float)),
            patch.object(runner, "_passing", set()),
            patch.object(runner, "_pending_media", collections.defaultdict(
                lambda: collections.deque(maxlen=16))),
            patch.object(runner, "_recent_msgs", collections.defaultdict(
                lambda: collections.deque(maxlen=12))),
            patch.object(runner, "_missed", {}),
            patch.object(runner, "_cooldown", return_value=0.0),
            patch.object(runner, "_queue_and_send_media", queue_send),
            patch.object(runner, "_maybe_compact", side_effect=_no_compact),
            patch.object(runner, "_await_despite_cancellation", cancelled_handoff),
            patch.object(runner, "_arm", arm),
            patch.object(agent, "voice_turn_envelope", return_value=media.TurnEnvelope(
                text="", outbound=(item,), run_id="run-cancel-media")),
            patch.object(agent, "run_delivery_started", started),
            patch.object(agent, "run_delivery_finalize_recovered", Mock(return_value=True)),
            patch.object(agent, "run_delivery_failed", Mock()),
        ):
            with contextlib.suppress(asyncio.CancelledError):
                await runner._run_pass(route.conversation_id)
            await asyncio.sleep(0)

        started.assert_called_once()                    # the durable intent WAS accepted
        self.assertEqual(queue_send.await_count, 0)     # raised before the inline send loop
        self.assertIn(route.conversation_id, wakes)     # wake NOT consumed (not terminal)
        self.assertTrue(arm.called)                     # re-armed: model will re-author the media

    async def test_text_only_reply_under_cancellation_is_terminal_no_rearm(self):
        # PASS28 finding #3: a text-only reply cancelled AFTER run_delivery_started must
        # be terminal.  The text_plan is durably persisted and text_outbox replays it
        # exactly once even on a still-'running' run, so re-arming the wake would make
        # the model re-author and DUPLICATE the reply.
        route = telegram_topics.TopicRoute("-10042", 101)
        meta = {route.conversation_id: {
            "entity": -10042, "peer_id": route.peer_id, "topic_id": route.topic_id,
            "is_dm": False, "is_owner": False, "known": True, "family": False,
            "name": "Миша", "title": "Micellium · topic #101", "size": 20,
            "addressed": True, "addressed_mid": 11, "room_mode": "normal",
        }}
        wakes = {route.conversation_id: _wake(11, "ответь")}
        started = Mock()
        arm = Mock()
        client = _Client()

        async def cancelled_handoff(awaitable):
            return (await awaitable), True

        with (
            patch.object(runner, "client", client),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_last_pass", collections.defaultdict(float)),
            patch.object(runner, "_passing", set()),
            patch.object(runner, "_pending_media", collections.defaultdict(
                lambda: collections.deque(maxlen=16))),
            patch.object(runner, "_recent_msgs", collections.defaultdict(
                lambda: collections.deque(maxlen=12))),
            patch.object(runner, "_missed", {}),
            patch.object(runner, "_cooldown", return_value=0.0),
            patch.object(runner, "_maybe_compact", side_effect=_no_compact),
            patch.object(runner, "_await_despite_cancellation", cancelled_handoff),
            patch.object(runner, "_arm", arm),
            patch.object(runner, "_TEXT_SENDING", set()),
            patch.object(agent, "voice_turn_envelope", return_value=media.TurnEnvelope(
                text="готовый ответ", run_id="run-cancel-text")),
            patch.object(agent, "run_delivery_started", started),
            patch.object(agent, "run_delivery_text_chunk_accepted", return_value=None),
            patch.object(agent, "run_delivery_text_accepted", return_value=None),
            patch.object(agent, "run_delivery_finalize_recovered", Mock(return_value=True)),
            patch.object(agent, "run_delivery_failed", Mock()),
        ):
            with contextlib.suppress(asyncio.CancelledError):
                await runner._run_pass(route.conversation_id)
            await asyncio.sleep(0)

        started.assert_called_once()                    # intent accepted; text_outbox will deliver
        self.assertEqual(client.sent, [])               # raised before inline send -> no double send
        self.assertNotIn(route.conversation_id, wakes)  # wake consumed (terminal) -> no re-author
        self.assertFalse(arm.called)                    # NOT re-armed -> no duplicate reply

    async def test_group_superseded_before_send_abandons_terminally(self):
        # PASS 29: a newer trigger scheduled a fresh pass BEFORE anything was sent. The
        # interrupted draft must be abandoned terminally (run_delivery_superseded) so the
        # text_outbox never mails the автоотбойник; the successor owns the re-author.
        route = telegram_topics.TopicRoute("-10042", 101)
        meta = {route.conversation_id: {
            "entity": -10042, "peer_id": route.peer_id, "topic_id": route.topic_id,
            "is_dm": False, "is_owner": False, "known": True, "family": False,
            "name": "Миша", "title": "Micellium · topic #101", "size": 20,
            "addressed": True, "addressed_mid": 11, "room_mode": "normal",
        }}
        wakes = {route.conversation_id: _wake(11, "ответь")}
        gens = {}
        started = Mock(); arm = Mock()
        superseded = Mock(return_value=True)
        client = _Client()

        async def cancelled_handoff(awaitable):
            # a newer message arrived during the shielded delivery-started handoff
            gens[route.conversation_id] = gens.get(route.conversation_id, 0) + 1
            return (await awaitable), True

        not_shutting_down = asyncio.Event()  # NOT set -> process alive
        await _drive_run_pass(route.conversation_id, [
            patch.object(runner, "client", client),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_supersede_gen", gens),
            patch.object(runner, "_SHUTDOWN", not_shutting_down),
            patch.object(runner, "_last_pass", collections.defaultdict(float)),
            patch.object(runner, "_passing", set()),
            patch.object(runner, "_pending_media", collections.defaultdict(
                lambda: collections.deque(maxlen=16))),
            patch.object(runner, "_recent_msgs", collections.defaultdict(
                lambda: collections.deque(maxlen=12))),
            patch.object(runner, "_missed", {}),
            patch.object(runner, "_cooldown", return_value=0.0),
            patch.object(runner, "_maybe_compact", side_effect=_no_compact),
            patch.object(runner, "_await_despite_cancellation", cancelled_handoff),
            patch.object(runner, "_arm", arm),
            patch.object(runner, "_TEXT_SENDING", set()),
            patch.object(agent, "voice_turn_envelope", return_value=media.TurnEnvelope(
                text="готовый ответ", run_id="run-superseded-grp")),
            patch.object(agent, "run_delivery_started", started),
            patch.object(agent, "run_delivery_superseded", superseded),
            patch.object(agent, "run_delivery_failed", Mock()),
        ])

        superseded.assert_called_once()                 # draft abandoned terminally
        self.assertEqual(superseded.call_args.args[0], "run-superseded-grp")
        self.assertEqual(client.sent, [])               # raised before inline send
        self.assertIn(route.conversation_id, wakes)     # successor's wake NOT consumed
        self.assertTrue(arm.called)                     # successor re-armed to re-author

    async def test_group_superseded_during_shutdown_holds_recoverable(self):
        # PASS 29: supersession that coincides with shutdown must NOT terminalise the group
        # draft (no persisted group wake => terminalising would DROP it on the way down).
        # Keep deployed recovery semantics so boot text_outbox replays her authored reply.
        route = telegram_topics.TopicRoute("-10042", 101)
        meta = {route.conversation_id: {
            "entity": -10042, "peer_id": route.peer_id, "topic_id": route.topic_id,
            "is_dm": False, "is_owner": False, "known": True, "family": False,
            "name": "Миша", "title": "t", "size": 20,
            "addressed": True, "addressed_mid": 11, "room_mode": "normal",
        }}
        wakes = {route.conversation_id: _wake(11, "ответь")}
        gens = {}
        started = Mock(); arm = Mock(); superseded = Mock(return_value=True)
        client = _Client()

        async def cancelled_handoff(awaitable):
            gens[route.conversation_id] = gens.get(route.conversation_id, 0) + 1
            return (await awaitable), True

        shutting_down = asyncio.Event(); shutting_down.set()
        await _drive_run_pass(route.conversation_id, [
            patch.object(runner, "client", client),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_supersede_gen", gens),
            patch.object(runner, "_SHUTDOWN", shutting_down),
            patch.object(runner, "_last_pass", collections.defaultdict(float)),
            patch.object(runner, "_passing", set()),
            patch.object(runner, "_pending_media", collections.defaultdict(
                lambda: collections.deque(maxlen=16))),
            patch.object(runner, "_recent_msgs", collections.defaultdict(
                lambda: collections.deque(maxlen=12))),
            patch.object(runner, "_missed", {}),
            patch.object(runner, "_cooldown", return_value=0.0),
            patch.object(runner, "_maybe_compact", side_effect=_no_compact),
            patch.object(runner, "_await_despite_cancellation", cancelled_handoff),
            patch.object(runner, "_arm", arm),
            patch.object(runner, "_TEXT_SENDING", set()),
            patch.object(agent, "voice_turn_envelope", return_value=media.TurnEnvelope(
                text="готовый ответ", run_id="run-shutdown-grp")),
            patch.object(agent, "run_delivery_started", started),
            patch.object(agent, "run_delivery_superseded", superseded),
            patch.object(agent, "run_delivery_failed", Mock()),
        ])

        superseded.assert_not_called()                  # HELD: not terminalised on the way down
        self.assertEqual(client.sent, [])               # nothing sent
        self.assertNotIn(route.conversation_id, wakes)  # deployed terminal -> outbox recovers
        self.assertFalse(arm.called)                    # deployed: text-only not re-armed

    async def test_no_successor_keeps_deployed_recovery(self):
        # PASS 29: cancelled with NO newer trigger (gen unchanged) = shutdown/teardown.
        # Behaviour must be deployed: text_outbox replays exactly once; the draft is NEVER
        # abandoned (so it is never dropped).
        route = telegram_topics.TopicRoute("-10042", 101)
        meta = {route.conversation_id: {
            "entity": -10042, "peer_id": route.peer_id, "topic_id": route.topic_id,
            "is_dm": False, "is_owner": False, "known": True, "family": False,
            "name": "Миша", "title": "t", "size": 20,
            "addressed": True, "addressed_mid": 11, "room_mode": "normal",
        }}
        wakes = {route.conversation_id: _wake(11, "ответь")}
        gens = {route.conversation_id: 7}                # armed_gen==7, never changes
        started = Mock(); arm = Mock(); superseded = Mock(return_value=True)
        client = _Client()

        async def cancelled_handoff(awaitable):
            return (await awaitable), True                # gen NOT bumped -> no successor

        await _drive_run_pass(route.conversation_id, [
            patch.object(runner, "client", client),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_supersede_gen", gens),
            patch.object(runner, "_SHUTDOWN", asyncio.Event()),
            patch.object(runner, "_last_pass", collections.defaultdict(float)),
            patch.object(runner, "_passing", set()),
            patch.object(runner, "_pending_media", collections.defaultdict(
                lambda: collections.deque(maxlen=16))),
            patch.object(runner, "_recent_msgs", collections.defaultdict(
                lambda: collections.deque(maxlen=12))),
            patch.object(runner, "_missed", {}),
            patch.object(runner, "_cooldown", return_value=0.0),
            patch.object(runner, "_maybe_compact", side_effect=_no_compact),
            patch.object(runner, "_await_despite_cancellation", cancelled_handoff),
            patch.object(runner, "_arm", arm),
            patch.object(runner, "_TEXT_SENDING", set()),
            patch.object(agent, "voice_turn_envelope", return_value=media.TurnEnvelope(
                text="готовый ответ", run_id="run-nosucc")),
            patch.object(agent, "run_delivery_started", started),
            patch.object(agent, "run_delivery_superseded", superseded),
            patch.object(agent, "run_delivery_failed", Mock()),
        ])

        superseded.assert_not_called()                  # NEVER abandoned with no successor
        started.assert_called_once()                    # intent accepted; outbox will replay
        self.assertEqual(client.sent, [])               # no double send
        self.assertNotIn(route.conversation_id, wakes)  # deployed text-only terminal
        self.assertFalse(arm.called)                    # deployed: not re-armed

    async def test_dm_superseded_abandons_even_during_shutdown(self):
        # PASS 29: DMs are drop-safe to abandon even mid-shutdown because unanswered +
        # _missed_dm_sweep durably re-author. Proves the is_dm leg of the guard.
        chat_id = "555"
        meta = {chat_id: {
            "entity": 555, "peer_id": "555", "topic_id": None, "is_dm": True,
            "is_owner": True, "known": True, "family": False, "name": "Егор",
            "addressed": True, "addressed_mid": 9, "room_mode": "normal",
            "sender_id": 555, "origin_message_id": 9, "origin_text": "мой вопрос",
        }}
        gens = {}
        started = Mock(); arm = Mock(); superseded = Mock(return_value=True)
        client = _Client()

        async def cancelled_handoff(awaitable):
            gens[chat_id] = gens.get(chat_id, 0) + 1
            return (await awaitable), True

        shutting_down = asyncio.Event(); shutting_down.set()
        await _drive_run_pass(chat_id, [
            patch.object(runner, "client", client),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_supersede_gen", gens),
            patch.object(runner, "_SHUTDOWN", shutting_down),
            patch.object(runner, "_last_pass", collections.defaultdict(float)),
            patch.object(runner, "_passing", set()),
            patch.object(runner, "_dm_rearm", set()),
            patch.object(runner, "_pending_media", collections.defaultdict(
                lambda: collections.deque(maxlen=16))),
            patch.object(runner, "_recent_msgs", collections.defaultdict(
                lambda: collections.deque(maxlen=12))),
            patch.object(runner, "_missed", {}),
            patch.object(runner, "_cooldown", return_value=0.0),
            patch.object(runner, "_last_n_text", AsyncMock(return_value="мой вопрос")),
            patch.object(runner, "_maybe_compact", side_effect=_no_compact),
            patch.object(runner, "_await_despite_cancellation", cancelled_handoff),
            patch.object(runner, "_arm", arm),
            patch.object(runner, "_TEXT_SENDING", set()),
            patch.object(agent, "voice_turn_envelope", return_value=media.TurnEnvelope(
                text="ответ", run_id="run-superseded-dm")),
            patch.object(agent, "run_delivery_started", started),
            patch.object(agent, "run_delivery_superseded", superseded),
            patch.object(agent, "run_delivery_failed", Mock()),
        ])

        superseded.assert_called_once()                 # DM abandon is drop-safe even in shutdown
        self.assertEqual(client.sent, [])


class TestExplicitTopicTools(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="praxis-topic-outbox-")
        self.old_outbox = runner._DIRECT_OUTBOX
        self.old_execution = runner._direct_tool_execution
        self.call_seq = 0
        runner._DIRECT_OUTBOX = runner.telegram_outbox.TelegramOutbox(
            Path(self.tempdir.name) / "outbox",
        )
        self.prepared_patch = patch.object(
            runner.agent, "run_direct_outbox_prepared", return_value={"schema": "test"},
        )
        self.project_patch = patch.object(
            runner.agent, "project_direct_outbox_acceptance", return_value=True,
        )
        self.proof_check_patch = patch.object(
            runner.agent, "direct_outbox_prepared", return_value=True,
        )
        self.prepared_patch.start()
        self.project_patch.start()
        self.proof_check_patch.start()

        def _execution(tool):
            self.call_seq += 1
            call_id = f"topic-call-{self.call_seq}"
            return {
                "run_id": "run-topic-tools",
                "call_id": call_id,
                "tool": tool,
                "idempotency_key": f"telegram-outbox:run-topic-tools:tool:{call_id}",
            }

        runner._direct_tool_execution = _execution

    def tearDown(self):
        self.proof_check_patch.stop()
        self.project_patch.stop()
        self.prepared_patch.stop()
        runner._DIRECT_OUTBOX = self.old_outbox
        runner._direct_tool_execution = self.old_execution
        self.tempdir.cleanup()

    def test_read_and_send_message_parse_selector_before_telethon(self):
        client = _Client()
        entity = types.SimpleNamespace(id=42, first_name="Micellium", last_name="",
                                       title="Micellium", username=None, usernames=())
        resolver = AsyncMock(return_value=entity)
        with (
            patch.object(runner, "client", client),
            patch.object(runner, "_resolve_entity", resolver),
            patch.object(runner, "_threadsafe_result", side_effect=_run_here),
            patch.object(runner, "_marked_peer_id", return_value=-10042),
            patch.object(runner, "OWNER_ID", 0),
            patch.object(runner.social_pulse, "allow_outbound", return_value=(True, "ok")),
            patch.object(runner.social_pulse, "note_outbound", return_value=None),
            patch.object(runner.telegram_contacts, "mark_outbound", return_value=None),
            patch.object(runner.unanswered, "resolve", return_value=None),
        ):
            runner._sync_read_chat("-10042#topic:101", 5)
            receipt = runner._sync_send_message("-10042#topic:101", "привет теме")

        self.assertEqual([call.args[0] for call in resolver.await_args_list],
                         ["-10042", "-10042"])
        self.assertEqual(client.history[0][1], {"limit": 5, "reply_to": 101})
        self.assertEqual(client.sent[0][2], {"reply_to": 101})
        self.assertIn("-10042#topic:101", receipt)

    def test_current_topic_file_delivery_keeps_topic(self):
        route = telegram_topics.TopicRoute("-10042", 101)
        client = _Client()
        entity = types.SimpleNamespace(id=42, title="Micellium", name="Micellium")
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "result.txt"
            source.write_text("ready", encoding="utf-8")
            with (
                patch.object(runner, "client", client),
                patch.object(runner, "_meta", {route.conversation_id: {"entity": entity}}),
                patch.object(runner.agent, "_active_chat", return_value=route.conversation_id),
                patch.object(runner, "_threadsafe_result", side_effect=_run_here),
                patch.object(runner, "_marked_peer_id", return_value=-10042),
            ):
                receipt = runner._sync_send_file(str(source), "готово")

        self.assertEqual(client.files[0][2]["reply_to"], 101)
        self.assertIn("-10042#topic:101", receipt)


class TestTopicMediaRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_document_acceptance_is_recorded_as_file_in_topic_buffer(self):
        route = telegram_topics.TopicRoute("-10042", 101)
        item = media.OutboundMedia(
            kind="document", path=Path("result.txt"),
            mime="application/octet-stream", size=8,
            target_chat_id=route.conversation_id, scope="group",
            caption="готово", queue_id="document-buffer",
        )
        spool = Mock()
        send = AsyncMock(return_value=(types.SimpleNamespace(id=993), 789))
        push = Mock()
        ctx = agent.ChannelContext(
            chat_id=route.conversation_id, room_id=route.peer_id,
            is_dm=False, known=True, addressed=True,
        )
        with (
            patch.object(runner, "_MEDIA_SPOOL", spool),
            patch.object(runner, "_send_file_idempotent", send),
            patch.object(runner, "_buf_push", push),
        ):
            receipt = await runner._send_turn_media(
                -10042, item, ctx=ctx, reply_to=101,
                state_chat_id=route.conversation_id)

        self.assertEqual(receipt["message_id"], 993)
        self.assertEqual(receipt["random_id"], 789)
        spool.validate_outbound.assert_called_once_with(
            item, expected_scope="group", expected_chat_id=route.conversation_id)
        self.assertEqual(send.await_args.kwargs["reply_to"], 101)
        self.assertEqual(push.call_args.args[0], route.conversation_id)
        self.assertIn("[Файл]", push.call_args.args[1])

    async def test_document_public_client_fallback_keeps_force_and_topic(self):
        client = _Client()
        item = media.OutboundMedia(
            kind="document", path=Path("build report.txt"),
            mime="application/octet-stream", size=12,
            target_chat_id="-10042__topic__101", scope="group",
            caption="готово", queue_id="fallback-document",
        )
        with patch.object(runner, "client", client):
            sent, random_id = await runner._send_file_idempotent(
                -10042, item, reply_to=101)

        self.assertEqual(sent.id, 951)
        self.assertNotEqual(random_id, 0)
        self.assertEqual(client.files[0][2]["reply_to"], 101)
        self.assertTrue(client.files[0][2]["force_document"])
        self.assertFalse(client.files[0][2]["voice_note"])

    async def test_raw_text_send_reuses_stable_random_id_and_topic_reply(self):
        class InputReplyToMessage:
            def __init__(self, *, reply_to_msg_id):
                self.reply_to_msg_id = reply_to_msg_id

        class SendMessageRequest:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class UpdateShortSentMessage:
            def __init__(self, mid):
                self.id = mid

        fake_root = types.ModuleType("telethon")
        fake_root.__path__ = []
        fake_tl = types.ModuleType("telethon.tl")
        fake_tl.functions = types.SimpleNamespace(
            messages=types.SimpleNamespace(SendMessageRequest=SendMessageRequest))
        fake_tl.types = types.SimpleNamespace(
            InputReplyToMessage=InputReplyToMessage,
            UpdateShortSentMessage=UpdateShortSentMessage,
        )
        fake_root.tl = fake_tl
        client = _RawClient()
        with (
            patch.object(runner, "client", client),
            patch.dict(sys.modules, {"telethon": fake_root, "telethon.tl": fake_tl}),
        ):
            first, first_random_id = await runner._send_message_idempotent(
                -10042, "ответ", delivery_key="run:r1:chunk:0", reply_to=101)
            second, second_random_id = await runner._send_message_idempotent(
                -10042, "ответ", delivery_key="run:r1:chunk:0", reply_to=101)
            _, other_random_id = await runner._send_message_idempotent(
                -10042, "продолжение", delivery_key="run:r1:chunk:1", reply_to=101)

        self.assertEqual(first.id, 977)
        self.assertEqual(second.id, 977)
        self.assertEqual(first_random_id, second_random_id)
        self.assertNotEqual(first_random_id, other_random_id)
        self.assertNotEqual(first_random_id, 0)
        self.assertEqual([request.random_id for request in client.requests[:2]],
                         [first_random_id, first_random_id])
        self.assertEqual([request.reply_to.reply_to_msg_id
                          for request in client.requests], [101, 101, 101])
        self.assertEqual(client.requests[0].entities, ["parsed-entity"])

    async def test_raw_text_send_uses_static_response_parser_without_instance_lookup(self):
        class InputReplyToMessage:
            def __init__(self, *, reply_to_msg_id):
                self.reply_to_msg_id = reply_to_msg_id

        class SendMessageRequest:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class UpdateShortSentMessage:
            def __init__(self, mid):
                self.id = mid

        fake_root = types.ModuleType("telethon")
        fake_root.__path__ = []
        fake_tl = types.ModuleType("telethon.tl")
        fake_tl.functions = types.SimpleNamespace(
            messages=types.SimpleNamespace(SendMessageRequest=SendMessageRequest))
        fake_tl.types = types.SimpleNamespace(
            InputReplyToMessage=InputReplyToMessage,
            UpdateShortSentMessage=UpdateShortSentMessage,
        )
        fake_root.tl = fake_tl

        class StaticResponseClient(_RawClient):
            @staticmethod
            def _get_response_message(request, response, input_entity):
                return types.SimpleNamespace(id=978)

            def __getattribute__(self, name):
                if name == "_get_response_message":
                    raise RecursionError("instance lookup must not happen")
                return super().__getattribute__(name)

        client = StaticResponseClient()
        with (
            patch.object(runner, "client", client),
            patch.dict(sys.modules, {"telethon": fake_root, "telethon.tl": fake_tl}),
        ):
            sent, _random_id = await runner._send_message_idempotent(
                -10042, "ответ", delivery_key="run:r2:chunk:0", reply_to=101)

        self.assertEqual(sent.id, 978)
        self.assertEqual(len(client.requests), 1)

        class _ShortResponseClient(_RawClient):
            async def __call__(self, request):
                self.requests.append(request)
                return UpdateShortSentMessage(444)

        short_client = _ShortResponseClient()
        with (
            patch.object(runner, "client", short_client),
            patch.dict(sys.modules, {"telethon": fake_root, "telethon.tl": fake_tl}),
        ):
            short, _ = await runner._send_message_idempotent(
                42, "короткий ответ", delivery_key="run:r2:chunk:0")
        self.assertEqual(short.id, 444)

    async def test_raw_media_and_document_reuse_stable_ids_and_topic_reply(self):
        class InputReplyToMessage:
            def __init__(self, *, reply_to_msg_id):
                self.reply_to_msg_id = reply_to_msg_id

        class DocumentAttributeFilename:
            def __init__(self, *, file_name):
                self.file_name = file_name

        class SendMediaRequest:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        fake_root = types.ModuleType("telethon")
        fake_root.__path__ = []
        fake_tl = types.ModuleType("telethon.tl")
        fake_tl.functions = types.SimpleNamespace(
            messages=types.SimpleNamespace(SendMediaRequest=SendMediaRequest))
        fake_tl.types = types.SimpleNamespace(
            InputReplyToMessage=InputReplyToMessage,
            DocumentAttributeFilename=DocumentAttributeFilename,
        )
        fake_root.tl = fake_tl
        client = _RawClient()
        item = media.OutboundMedia(
            kind="photo", path=Path("spooled.png"), mime="image/png", size=8,
            target_chat_id="-10042__topic__101", scope="group",
            caption="готово", queue_id="stable-topic-media",
        )
        document = media.OutboundMedia(
            kind="document", path=Path("build report.txt"),
            mime="application/octet-stream", size=12,
            target_chat_id="-10042__topic__101", scope="group",
            caption="отчёт", queue_id="stable-topic-document",
        )
        with (
            patch.object(runner, "client", client),
            patch.dict(sys.modules, {"telethon": fake_root, "telethon.tl": fake_tl}),
        ):
            first, first_random_id = await runner._send_file_idempotent(
                -10042, item, reply_to=101)
            second, second_random_id = await runner._send_file_idempotent(
                -10042, item, reply_to=101)
            first_document, document_random_id = await runner._send_file_idempotent(
                -10042, document, reply_to=101)
            second_document, document_random_id_2 = await runner._send_file_idempotent(
                -10042, document, reply_to=101)

        self.assertEqual(first.id, 977)
        self.assertEqual(second.id, 977)
        self.assertEqual(first_random_id, second_random_id)
        self.assertEqual(first_document.id, 977)
        self.assertEqual(second_document.id, 977)
        self.assertEqual(document_random_id, document_random_id_2)
        self.assertNotEqual(first_random_id, document_random_id)
        self.assertNotEqual(first_random_id, 0)
        self.assertEqual([request.random_id for request in client.requests],
                         [first_random_id, first_random_id,
                          document_random_id, document_random_id])
        self.assertEqual([request.reply_to.reply_to_msg_id for request in client.requests],
                         [101, 101, 101, 101])
        self.assertEqual([request.message for request in client.requests],
                         ["готово", "готово", "отчёт", "отчёт"])
        self.assertEqual([request.entities for request in client.requests],
                         [["parsed-entity"]] * 4)
        self.assertEqual(
            [kwargs["force_document"] for _path, kwargs in client.conversions],
            [False, False, True, True],
        )
        document_conversions = [kwargs for _path, kwargs in client.conversions[2:]]
        self.assertEqual(
            [kwargs["attributes"][0].file_name for kwargs in document_conversions],
            ["build_report.txt", "build_report.txt"],
        )
        self.assertEqual(
            [kwargs["mime_type"] for kwargs in document_conversions],
            ["application/octet-stream", "application/octet-stream"],
        )

    async def test_acceptance_survives_outbox_ack_failure_without_resend(self):
        route = telegram_topics.TopicRoute("-10042", 101)
        item = media.OutboundMedia(
            kind="document", path=Path("report.txt"),
            mime="application/octet-stream", size=8,
            target_chat_id=route.conversation_id, scope="group",
            queue_id="ack-gap", run_id="run-ack-gap",
        )
        spool = Mock()
        spool.discard.side_effect = [
            RuntimeError("disk busy"), RuntimeError("disk busy"),
            RuntimeError("disk busy"), True,
        ]
        spool.outbox_results.return_value = ()
        spool.pending.return_value = (item,)
        send = AsyncMock(return_value={
            "message_id": 991, "random_id": 123, "accepted_at": 1.0,
        })
        ctx = agent.ChannelContext(
            chat_id=route.conversation_id, room_id=route.peer_id,
            is_dm=False, known=True, addressed=True,
        )
        with (
            patch.object(runner, "_MEDIA_SPOOL", spool),
            patch.object(runner, "_MEDIA_ACCEPTED", {}),
            patch.object(runner, "_MEDIA_SENDING", set()),
            patch.object(runner, "_send_turn_media", send),
            patch.object(runner.asyncio, "sleep", AsyncMock()),
            patch.object(runner.log, "exception"),
            patch.object(agent, "run_delivery_media_result", return_value={}),
        ):
            self.assertTrue(await runner._attempt_queued_media(
                -10042, item, ctx=ctx, reply_to=101))
            self.assertTrue(await runner._attempt_queued_media(
                -10042, item, ctx=ctx, reply_to=101))

        self.assertEqual(send.await_count, 1)
        self.assertEqual(spool.discard.call_count, 4)
        self.assertEqual(runner._MEDIA_ACCEPTED, {})

    async def test_document_outbox_ack_is_durable_and_removes_staged_copy(self):
        route = telegram_topics.TopicRoute("-10042", 101)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "spool"
            source = Path(td) / "build report.txt"
            source.write_bytes(b"verified build output\n")
            spool = media.MediaSpool(
                root, photo_max_bytes=1024, audio_max_bytes=2048,
                document_max_bytes=4096, max_total_bytes=8192,
                ttl_seconds=60, max_queue=4, max_turn_media=4,
            )
            item = spool.queue_outbound(
                source, kind="document", target_chat_id=route.conversation_id,
                reply_to_message_id=101, scope="group", caption="result",
            )
            staged_path = item.path
            send = AsyncMock(return_value={
                "message_id": 992, "random_id": 456, "accepted_at": 2.0,
            })
            ctx = agent.ChannelContext(
                chat_id=route.conversation_id, room_id=route.peer_id,
                is_dm=False, known=True, addressed=True,
            )
            with (
                patch.object(runner, "_MEDIA_SPOOL", spool),
                patch.object(runner, "_MEDIA_ACCEPTED", {}),
                patch.object(runner, "_MEDIA_SENDING", set()),
                patch.object(runner, "_send_turn_media", send),
            ):
                self.assertTrue(await runner._attempt_queued_media(
                    -10042, item, ctx=ctx, reply_to=101,
                    state_chat_id=route.conversation_id))

            self.assertEqual(spool.pending(), ())
            self.assertFalse(staged_path.exists())
            delivered = spool.outbox_results("delivered")
            self.assertEqual(len(delivered), 1)
            self.assertEqual(delivered[0]["item"]["kind"], "document")
            self.assertEqual(delivered[0]["item"]["reply_to_message_id"], 101)
            self.assertEqual(delivered[0]["result"]["message_id"], 992)
            self.assertEqual(delivered[0]["result"]["random_id"], 456)
            self.assertEqual(send.await_args.kwargs["reply_to"], 101)
            self.assertEqual(send.await_args.kwargs["state_chat_id"],
                             route.conversation_id)

    async def test_run_receipt_gap_keeps_bytes_until_tombstone_reconciliation(self):
        route = telegram_topics.TopicRoute("-10042", 101)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "spool"
            source = Path(td) / "receipt-gap.txt"
            source.write_bytes(b"accepted file\n")
            spool = media.MediaSpool(
                root, photo_max_bytes=1024, audio_max_bytes=2048,
                document_max_bytes=4096, max_total_bytes=8192,
                ttl_seconds=60, max_queue=4, max_turn_media=4,
            )
            item = spool.resolve_outbound(
                source, kind="document", target_chat_id=route.conversation_id,
                reply_to_message_id=101, scope="group",
            )
            item = spool.enqueue(replace(item, run_id="run-receipt-gap"))
            staged_path = item.path
            send = AsyncMock(return_value={
                "message_id": 993, "random_id": 457, "accepted_at": 3.0,
            })
            ctx = agent.ChannelContext(
                chat_id=route.conversation_id, room_id=route.peer_id,
                is_dm=False, known=True, addressed=True,
            )
            with (
                patch.object(runner, "_MEDIA_SPOOL", spool),
                patch.object(runner, "_MEDIA_ACCEPTED", {}),
                patch.object(runner, "_MEDIA_SENDING", set()),
                patch.object(runner, "_send_turn_media", send),
                patch.object(
                    agent, "run_delivery_media_result",
                    side_effect=OSError("run WAL temporarily unavailable"),
                ),
            ):
                self.assertTrue(await runner._attempt_queued_media(
                    -10042, item, ctx=ctx, reply_to=101,
                    state_chat_id=route.conversation_id))

            self.assertEqual(spool.pending(), ())
            self.assertTrue(staged_path.exists())
            self.assertEqual(len(spool.outbox_results("delivered")), 1)

            result = Mock(return_value={})
            finalize = Mock(return_value=True)
            with (
                patch.object(runner, "_MEDIA_SPOOL", spool),
                patch.object(agent, "run_delivery_media_result", result),
                patch.object(agent, "run_delivery_finalize_recovered", finalize),
            ):
                await runner._media_cleanup_once()

            result.assert_called_once_with(
                "run-receipt-gap", item.queue_id, ok=True, message_id=993)
            finalize.assert_called_once_with("run-receipt-gap")
            self.assertFalse(staged_path.exists())

    async def test_queue_route_recovers_without_live_meta(self):
        route = telegram_topics.TopicRoute("-10042", 101)
        item = types.SimpleNamespace(
            target_chat_id=route.conversation_id,
            reply_to_message_id=101,
            scope="group",
            queue_id="topic-retry",
            run_id="",
        )
        spool = Mock()
        spool.pending.return_value = [item]
        spool.cleanup.return_value = []
        spool.outbox_results.return_value = []
        attempt = AsyncMock(return_value=True)
        entity = object()
        with (
            patch.object(runner, "_MEDIA_SPOOL", spool),
            # Fresh traffic in another Micellium topic must not steal this retry.
            patch.object(runner, "_meta", {
                "-10042__topic__202": {
                    "entity": object(), "peer_id": "-10042", "topic_id": 202,
                },
            }),
            patch.object(runner, "_recent_msgs", {}),
            patch.object(runner, "_resolve_entity", AsyncMock(return_value=entity)),
            patch.object(runner, "_attempt_queued_media", attempt),
        ):
            await runner._media_cleanup_once()

        kwargs = attempt.await_args.kwargs
        self.assertIs(attempt.await_args.args[0], entity)
        self.assertEqual(kwargs["ctx"].chat_id, route.conversation_id)
        self.assertEqual(kwargs["ctx"].room_chat_id, route.peer_id)
        self.assertEqual(kwargs["reply_to"], 101)
        self.assertEqual(kwargs["state_chat_id"], route.conversation_id)

    async def test_delivered_outbox_tombstone_repairs_run_receipt(self):
        spool = Mock()
        spool.pending.return_value = ()
        spool.cleanup.return_value = []
        record = {
            "state": "delivered", "queue_id": "q-recovered",
            "item": {"run_id": "run-recovered"},
            "result": {"message_id": 771, "random_id": 991},
        }
        spool.outbox_results.side_effect = lambda state: (
            (record,) if state == "delivered" else ())
        result = Mock(return_value={})
        finalize = Mock(return_value=True)
        with (
            patch.object(runner, "_MEDIA_SPOOL", spool),
            patch.object(agent, "run_delivery_media_result", result),
            patch.object(agent, "run_delivery_finalize_recovered", finalize),
        ):
            await runner._media_cleanup_once()

        result.assert_called_once_with(
            "run-recovered", "q-recovered", ok=True, message_id=771)
        finalize.assert_called_once_with("run-recovered")


class TestFollowupDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_failed_media_projects_one_system_alert_into_shared_inbox(self):
        record = {
            "state": "failed", "queue_id": "q-file-failed",
            "item": {"run_id": "run-file", "path": "outbox/report.pdf"},
            "result": {"reason": "upload retries exhausted"},
        }
        spool = Mock()
        spool.pending.return_value = ()
        spool.cleanup.return_value = []
        spool.outbox_results.side_effect = lambda state: (
            (record,) if state == "failed" else ()
        )
        with tempfile.TemporaryDirectory(prefix="owner-delivery-media-") as temp:
            deliveries = runner.owner_delivery.OwnerDeliveryLedger(
                Path(temp) / "events.jsonl"
            )
            with (
                patch.object(runner, "_MEDIA_SPOOL", spool),
                patch.object(runner.owner_delivery, "LEDGER", deliveries),
                patch.object(agent, "run_delivery_blocked", return_value={}),
            ):
                await runner._media_cleanup_once()
                await runner._media_cleanup_once()
            inbox = deliveries.snapshot()

        self.assertEqual(len(inbox["items"]), 1)
        alert = inbox["items"][0]
        self.assertEqual(alert["type"], "system_alert")
        self.assertEqual(alert["outcome"], "blocked")
        self.assertIn("report.pdf", alert["result"])
        self.assertNotIn("outbox/", alert["result"])

    async def test_generic_transport_formats_and_marks_non_followup_item(self):
        send = AsyncMock(return_value=(types.SimpleNamespace(id=801), 701))
        with tempfile.TemporaryDirectory(prefix="owner-delivery-alert-") as temp:
            deliveries = runner.owner_delivery.OwnerDeliveryLedger(
                Path(temp) / "events.jsonl"
            )
            alert = deliveries.emit(
                "run_result", title="Деплой завершён частично", outcome="partial",
                result="Сервис собран, healthcheck не прошёл.",
                reason="Процесс не поднялся.", expectation="Продолжить диагностику.",
                dedupe_key="run:deploy:result",
            )
            with (
                patch.object(runner, "OWNER_ID", 999),
                patch.object(runner.owner_delivery, "LEDGER", deliveries),
                patch.object(runner, "_send_message_idempotent", send),
            ):
                await runner._owner_deliveries_once()
                await runner._owner_deliveries_once()
            delivered = deliveries.get(alert["id"])

        self.assertEqual(send.await_count, 1)
        self.assertEqual(delivered["status"], "delivered")
        text = send.await_args.args[1]
        self.assertIn("Статус: частично", text)
        self.assertIn("Результат:", text)
        self.assertIn("Почему:", text)
        self.assertIn("Дальше:", text)

    async def test_mark_crash_reuses_delivered_inbox_item_without_resend(self):
        item = {
            "id": "tgfu_restart_safe", "target_label": "Миша",
            # ⚠ Без target_user_id запись по контракту леджера объявляет себя ГРУППОВОЙ
            # нитью, и заголовок честно говорит «не знаю кто ответил(а) в Миша» — потому
            # что в группе отвечает человек, а не чат, и имени здесь нет. Фикстура про ЛС.
            "target_user_id": "42",
            "sent_message_id": 55,
            "response": {"text": "ответил", "peer_id": "42", "message_id": 77},
        }
        ledger = Mock()
        ledger.pending_notifications.return_value = [item]
        ledger.mark_notified.side_effect = [OSError("disk busy"), True]
        send = AsyncMock(return_value=(types.SimpleNamespace(id=901), 123))
        with tempfile.TemporaryDirectory(prefix="owner-delivery-followup-") as temp:
            deliveries = runner.owner_delivery.OwnerDeliveryLedger(
                Path(temp) / "events.jsonl"
            )
            with (
                patch.object(runner, "OWNER_ID", 999),
                patch.object(runner.telegram_followups, "LEDGER", ledger),
                patch.object(runner.owner_delivery, "LEDGER", deliveries),
                patch.object(runner, "_send_message_idempotent", send),
            ):
                with self.assertRaises(OSError):
                    await runner._followups_once()
                await runner._followups_once()

        self.assertEqual(send.await_count, 1)
        delivery_key = send.await_args.kwargs["delivery_key"]
        self.assertRegex(delivery_key, r"^owner-delivery:delivery-[0-9a-f]{32}$")
        text = send.await_args.args[1]
        self.assertEqual(text, "↩️ Миша ответил(а):\nответил")
        self.assertNotIn("Статус:", text)
        self.assertNotIn("Почему:", text)
        self.assertNotIn("Дальше:", text)
        self.assertNotIn("Действие:", text)
        self.assertEqual(ledger.mark_notified.call_count, 2)


def _fake_telethon_functions():
    class GetForumTopicsRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_root = types.ModuleType("telethon")
    fake_root.__path__ = []
    fake_tl = types.ModuleType("telethon.tl")
    fake_functions = types.ModuleType("telethon.tl.functions")
    fake_messages = types.ModuleType("telethon.tl.functions.messages")
    fake_messages.GetForumTopicsRequest = GetForumTopicsRequest
    fake_functions.messages = fake_messages
    fake_tl.functions = fake_functions
    fake_root.tl = fake_tl
    return {
        "telethon": fake_root, "telethon.tl": fake_tl,
        "telethon.tl.functions": fake_functions,
        "telethon.tl.functions.messages": fake_messages,
    }


def _forum_item(topic_id, title=""):
    return types.SimpleNamespace(id=topic_id, title=title, date=None,
                                 top_message=topic_id)


class _CatalogueEnv(unittest.IsolatedAsyncioTestCase):
    """Общая песочница: реестр в tmp, вся in-memory машинерия добычи чистая."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="praxis-topic-catalogue-")
        self._orig_dir = runner.telegram_routes.DIR
        runner.telegram_routes.DIR = (
            Path(self.tempdir.name) / "memory" / ".state" / "group_context")
        self._stores = (runner._topic_catalog_cache, runner._topic_catalog_flights,
                        runner._topic_catalog_next_attempt,
                        runner._topic_catalog_stale_probe,
                        runner._topic_catalog_probed_roots,
                        runner._topic_catalog_unknown_gate,
                        runner._topic_catalog_read_flights,
                        runner._topic_catalog_pending_roots,
                        runner._topic_catalog_cache_gen)
        self._saved = tuple(dict(store) for store in self._stores)
        for store in self._stores:
            store.clear()

    def tearDown(self):
        runner.telegram_routes.DIR = self._orig_dir
        for store, saved in zip(self._stores, self._saved):
            store.clear()
            store.update(saved)
        self.tempdir.cleanup()


class TestTopicCatalogueAcquisition(_CatalogueEnv):
    """Каталог добывается single-flight ДО фиксации ключа; полный свип уезжает в
    durable-карту с подтверждением записи; неудача — наблюдаема и не косит под успех."""

    async def test_full_sweep_persists_the_catalogue_durably(self):
        result = types.SimpleNamespace(count=3, topics=[
            _forum_item(1, "General"),
            _forum_item(7849, "Открытые вопросы"),
            _forum_item(9100, ""),
        ])

        async def fake_client(request):
            return result

        recorded = []
        with (
            patch.object(runner, "client", fake_client),
            patch.dict(sys.modules, _fake_telethon_functions()),
            patch.object(runner.group_context, "record_topic",
                         lambda *a, **k: recorded.append(a) or True),
        ):
            rows, complete = await runner._forum_topic_catalog(object(), "-300")

        self.assertEqual(len(rows), 3)
        self.assertTrue(complete, "короткая страница + count сошлись — полнота доказана")
        self.assertEqual(runner.telegram_routes.confirmed_topics("-300"),
                         frozenset({1, 7849, 9100}))
        self.assertEqual(runner.telegram_routes.topic_title("-300", 7849),
                         "Открытые вопросы")
        self.assertEqual(runner.telegram_routes.topic_title("-300", 9100), "",
                         "нет настоящего title — нет имени, синтетики тоже нет")
        self.assertEqual(len(recorded), 3, "канон комнаты получает те же строки")

    async def test_truncated_sweep_never_claims_completeness(self):
        """Её регрессия 4: count>hard_cap / упирание в cap не включают ложный complete
        и не рождают отрицательного знания (каталог остаётся «не наблюдался»)."""
        pages = [
            types.SimpleNamespace(
                count=150, topics=[_forum_item(1000 + i) for i in range(100)]),
            types.SimpleNamespace(
                count=150, topics=[_forum_item(2000 + i) for i in range(100)]),
        ]
        calls = {"n": 0}

        async def fake_client(request):
            page = pages[min(calls["n"], len(pages) - 1)]
            calls["n"] += 1
            return page

        with (
            patch.object(runner, "client", fake_client),
            patch.dict(sys.modules, _fake_telethon_functions()),
            patch.object(runner.group_context, "record_topic",
                         lambda *a, **k: True),
        ):
            rows, complete = await runner._forum_topic_catalog(
                object(), "-301", hard_cap=100)

        self.assertEqual(len(rows), 100)
        self.assertFalse(complete, "упирание в защитный cap — явное «неполно»")
        self.assertIsNone(runner.telegram_routes.confirmed_topics("-301"),
                          "неполный свип не включает каталог: нет отрицательного знания")
        self.assertEqual(runner.telegram_routes.topic_title("-301", 1005), "",
                         "но добытые темы уже записаны durable")
        self.assertIn(1005, runner.telegram_routes.topics_of("-301"))

    async def test_short_count_with_exhausted_pages_stays_incomplete(self):
        """count с сервера больше собранного при исчерпании — полноту не заявляем."""
        result = types.SimpleNamespace(count=7, topics=[_forum_item(700, "Тема")])

        async def fake_client(request):
            return result

        with (
            patch.object(runner, "client", fake_client),
            patch.dict(sys.modules, _fake_telethon_functions()),
            patch.object(runner.group_context, "record_topic",
                         lambda *a, **k: True),
        ):
            _rows, complete = await runner._forum_topic_catalog(object(), "-302")
        self.assertFalse(complete)
        self.assertIsNone(runner.telegram_routes.confirmed_topics("-302"))

    async def test_preflight_acquires_before_the_key_is_fixed(self):
        """Её регрессия 2: первая живая реплика настоящей темы не теряется —
        `_catalog_for_routing` ждёт single-flight добычу и возвращает каталог,
        по которому ключ минтится сразу верно."""
        async def sweep(entity, peer, **kwargs):
            runner.telegram_routes.observe_topics(
                peer, {500: {"title": "Тема"}}, complete=True)
            return [{"topic_id": 500, "title": "Тема", "top_message": 500}], True

        with patch.object(runner, "_forum_topic_catalog", sweep):
            catalog = await runner._catalog_for_routing(
                "-300", object(), unknown_topic=500)
        self.assertEqual(catalog, frozenset({500}))
        message = types.SimpleNamespace(
            reply_to=types.SimpleNamespace(
                reply_to_top_id=500, reply_to_msg_id=501, forum_topic=True),
            reply_to_top_id=None)
        self.assertEqual(
            telegram_topics.route_for_message(
                -300, message, is_forum=True, confirmed_topics=catalog).conversation_id,
            "-300__topic__500",
        )

    async def test_concurrent_callers_share_one_flight_and_one_key(self):
        """Её регрессия 11: on_new и on_edited вокруг ПЕРВОЙ добычи ждут один и тот
        же полёт и получают один и тот же каталог — одна логическая запись не
        разъезжается по ключам."""
        started = asyncio.Event()
        release = asyncio.Event()
        fetches = {"n": 0}

        async def sweep(entity, peer, **kwargs):
            fetches["n"] += 1
            started.set()
            await release.wait()
            runner.telegram_routes.observe_topics(
                peer, {500: {"title": "Тема"}}, complete=True)
            return [{"topic_id": 500, "title": "Тема", "top_message": 500}], True

        with patch.object(runner, "_forum_topic_catalog", sweep):
            first = asyncio.create_task(
                runner._catalog_for_routing("-300", object(), unknown_topic=500))
            await started.wait()
            second = asyncio.create_task(
                runner._catalog_for_routing("-300", object(), unknown_topic=500))
            await asyncio.sleep(0)
            release.set()
            catalog_new, catalog_edit = await asyncio.gather(first, second)

        self.assertEqual(fetches["n"], 1, "один tracked-полёт на комнату")
        self.assertEqual(catalog_new, catalog_edit)
        self.assertEqual(catalog_new, frozenset({500}))

    async def test_failed_commit_is_failure_not_success(self):
        """Её регрессия 6: ошибка записи не ставит кулдаун успеха и не сообщает успех —
        каталог остаётся «не наблюдался», окно повтора короткое."""
        witnessed = {"oserror": False}

        async def sweep(entity, peer, **kwargs):
            try:
                with patch.object(runner.telegram_routes, "_save",
                                  lambda *a, **k: False):
                    runner.telegram_routes.observe_topics(
                        peer, {500: {"title": "Тема"}}, complete=True)
            except OSError:
                witnessed["oserror"] = True
                raise
            raise RuntimeError("observe_topics не поднял OSError при неудачной записи")

        before = time.time()
        with patch.object(runner, "_forum_topic_catalog", sweep):
            catalog = await runner._catalog_for_routing(
                "-300", object(), unknown_topic=500)
        self.assertTrue(witnessed["oserror"],
                        "потеря durable-записи обязана быть исключением, не тишиной")
        self.assertFalse(catalog, "недобытое знание не притворяется добытым")
        self.assertIsNone(runner.telegram_routes.confirmed_topics("-300"))
        retry_at = runner._topic_catalog_next_attempt.get("-300", 0.0)
        self.assertLessEqual(retry_at - before, runner._TOPIC_CATALOG_RETRY + 5.0,
                             "после неудачи — короткое окно повтора, не кулдаун успеха")
        self.assertGreater(retry_at, before, "но долбёжки без окна тоже нет")

    async def test_fresh_catalogue_costs_no_disk_and_no_flights(self):
        """Её регрессия 9: сто сообщений при свежем каталоге не рождают сто чтений
        реестра и сто задач."""
        runner.telegram_routes.observe_topics(
            "-300", {700: {"title": "Тема"}}, complete=True)
        reads = {"n": 0}
        real_read = runner.telegram_routes.read

        def counting_read(peer_id):
            reads["n"] += 1
            return real_read(peer_id)

        fetch = AsyncMock()
        with (
            patch.object(runner.telegram_routes, "read", counting_read),
            patch.object(runner, "_forum_topic_catalog", fetch),
        ):
            for _ in range(100):
                catalog = await runner._catalog_for_routing("-300", object(),
                                                            unknown_topic=700)
        self.assertEqual(catalog, frozenset({700}))
        self.assertEqual(fetch.await_count, 0, "знакомая тема и свежий каталог — без RPC")
        self.assertLessEqual(reads["n"], 4,
                             "кэш держит диск: не сто чтений на сто сообщений")
        self.assertFalse(any(not t.done() for t in runner._topic_catalog_flights.values()))

    async def test_forum_missing_answer_lands_in_the_registry(self):
        fetch = AsyncMock(side_effect=RuntimeError("CHANNEL_FORUM_MISSING (400)"))
        with patch.object(runner, "_forum_topic_catalog", fetch):
            catalog = await runner._catalog_for_routing(
                "-400", object(), unknown_topic=500)
            for task in list(runner._topic_catalog_flights.values()):
                try:
                    await task
                except Exception:
                    pass
        self.assertFalse(catalog)
        self.assertEqual(runner.telegram_routes.current("-400")["forum_status"],
                         runner.telegram_routes.FALSE,
                         "прямой ответ «тут нет форума» не выбрасывается")

    async def test_unknown_root_spawns_the_flight_immediately_once(self):
        """P0 фан-аута 22.08: незнакомый корень — бесплатный in-memory сигнал, и он
        НЕ дросселируется дисковым окном; один корень — одна разведка, болтливая
        цепочка General не жжёт RPC."""
        runner.telegram_routes.observe_topics(
            "-300", {700: {"title": "Тема"}}, complete=True)
        calls = {"n": 0}

        async def sweep(entity, peer, **kwargs):
            calls["n"] += 1
            runner.telegram_routes.observe_topics(
                peer, {9100: {"title": "Новая"}}, complete=True)
            runner._topic_catalog_cache.pop(str(peer), None)
            return [{"topic_id": 9100, "title": "Новая", "top_message": 9100}], True

        with patch.object(runner, "_forum_topic_catalog", sweep):
            known = await runner._catalog_for_routing(
                "-300", object(), unknown_topic=9100)
            self.assertEqual(known, frozenset({700}),
                             "ключ текущего хода разведку не ждёт: комната честнее")
            for task in list(runner._topic_catalog_flights.values()):
                await task
            after = await runner._catalog_for_routing(
                "-300", object(), unknown_topic=9100)
            self.assertEqual(calls["n"], 1, "разведка стартовала с ПЕРВОГО же корня")
            self.assertIn(9100, after, "следующий ход уже видит новую тему")
            runner._topic_catalog_unknown_gate.clear()
            await runner._catalog_for_routing("-300", object(), unknown_topic=9999)
            for task in list(runner._topic_catalog_flights.values()):
                await task
            self.assertEqual(calls["n"], 2, "новый незнакомый корень — новая разведка")
            runner._topic_catalog_unknown_gate.clear()
            await runner._catalog_for_routing("-300", object(), unknown_topic=9999)
            self.assertEqual(calls["n"], 2,
                             "разведанный корень (цепочка General) не жжёт RPC")

    async def test_live_opener_refreshes_the_routing_cache(self):
        """P1 фан-аута 22.08: опенер обязан сбрасывать кэш каталога — иначе ответы в
        свежесозданной теме минуту падали в комнату."""
        runner.telegram_routes.observe_topics(
            "-300", {700: {"title": "Тема"}}, complete=True)
        before = await runner._catalog_for_routing("-300", object())
        self.assertEqual(before, frozenset({700}))
        await runner._note_live_opener("-300", 9100, "Новая тема")
        after = await runner._catalog_for_routing("-300", object())
        self.assertIn(9100, after, "ответы в новой теме не ждут минуту кэша")
        self.assertEqual(runner.telegram_routes.current("-300")["forum_status"],
                         runner.telegram_routes.TRUE,
                         "живой опенер — свидетельство «это форум»")


class TestFrozenRoomStaysSilent(_CatalogueEnv):
    """Её регрессия 10: замороженная/чужая комната не рождает каталожного RPC."""

    async def _drive(self, *, mode="frozen", allowed=True):
        peer = -1009990000077
        preflight = AsyncMock(return_value=frozenset())
        buf = collections.defaultdict(lambda: collections.deque(maxlen=100))
        with contextlib.ExitStack() as stack:
            for target, name, value in (
                (runner, "OWNER_ID", 999),
                (runner, "_buf", buf),
                (runner, "_buf_dirty", set()),
                (runner, "_meta", {}),
                (runner, "_seen_ids", collections.defaultdict(
                    lambda: collections.deque(maxlen=50))),
                (runner, "_catalog_for_routing", preflight),
            ):
                stack.enter_context(patch.object(target, name, value))
            stack.enter_context(patch.object(
                runner, "_known_forum", lambda *a, **k: True))
            stack.enter_context(patch.object(runner, "_under_tests", return_value=True))
            stack.enter_context(patch.object(
                runner.telegram_contacts, "observe", return_value=None))
            stack.enter_context(patch.object(
                runner.rooms, "is_allowed", return_value=allowed))
            stack.enter_context(patch.object(
                runner.rooms, "effective_mode", return_value=mode))
            stack.enter_context(patch.object(runner, "_note_room_mode_skip", Mock()))
            stack.enter_context(patch.object(runner.perception, "note_skip", Mock()))
            await runner.on_new(_Event(peer, _Message(7, "живое сообщение", 101)))
        return preflight

    async def test_frozen_room_never_triggers_topic_rpc(self):
        preflight = await self._drive(mode="frozen", allowed=True)
        preflight.assert_not_awaited()

    async def test_disallowed_room_never_triggers_topic_rpc(self):
        preflight = await self._drive(mode="normal", allowed=False)
        preflight.assert_not_awaited()


class TestCatchupReplayAcrossCatalogueArrival(_CatalogueEnv):
    """Фан-аут 22.08 (P1): сообщение, принятое под ключом комнаты ДО прихода
    каталога, при catch_up-повторе после каталога не дублирует буфер — сторож
    дублей смотрит и в комнатное кольцо."""

    async def test_replay_is_deduped_across_key_transition(self):
        peer = -1009990000088
        buf = collections.defaultdict(lambda: collections.deque(maxlen=100))
        preflight = AsyncMock(side_effect=[None, frozenset({101})])
        with contextlib.ExitStack() as stack:
            for target, name, value in (
                (runner, "OWNER_ID", 999),
                (runner, "_buf", buf),
                (runner, "_buf_dirty", set()),
                (runner, "_meta", {}),
                (runner, "_pending_media", collections.defaultdict(
                    lambda: collections.deque(maxlen=16))),
                (runner, "_seen_ids", collections.defaultdict(
                    lambda: collections.deque(maxlen=50))),
                (runner, "_recent_msgs", collections.defaultdict(
                    lambda: collections.deque(maxlen=12))),
                (runner, "_recent_senders", collections.defaultdict(
                    lambda: collections.deque(maxlen=40))),
                (runner, "_group_wakes", {}),
                (runner, "_entity_cache", {}),
                (runner, "_catalog_for_routing", preflight),
                (runner, "_capture_typed_media", AsyncMock(return_value=(None, ""))),
                (runner, "_chat_descriptor", AsyncMock(return_value={
                    "title": "Room", "kind": "group", "size": 20})),
                (runner, "_arm", Mock()),
            ):
                stack.enter_context(patch.object(target, name, value))
            stack.enter_context(patch.object(
                runner, "_known_forum", lambda *a, **k: True))
            stack.enter_context(patch.object(runner, "_under_tests", return_value=True))
            stack.enter_context(patch.object(
                runner.bufstore, "meta_update", return_value=None))
            stack.enter_context(patch.object(
                runner.rooms, "is_frozen", return_value=False))
            stack.enter_context(patch.object(
                runner.rooms, "is_allowed", return_value=True))
            stack.enter_context(patch.object(
                runner.rooms, "effective_mode", return_value="normal"))
            stack.enter_context(patch.object(
                runner.social, "category", return_value="known"))
            stack.enter_context(patch.object(
                runner.telegram_contacts, "observe", return_value=None))
            stack.enter_context(patch.object(
                runner.telegram_followups.LEDGER, "observe_incoming",
                return_value=None))
            stack.enter_context(patch.object(
                runner.reflex, "triage", return_value="answer"))
            await runner.on_new(_Event(peer, _Message(7, "первый приём", 101)))
            await runner.on_new(_Event(peer, _Message(7, "catch_up повтор", 101)))

        room_key = str(peer)
        topic_key = f"{peer}__topic__101"
        self.assertIn("первый приём", "\n".join(buf[room_key]))
        self.assertFalse(list(buf[topic_key]),
                         "повтор не смеет продублировать буфер под новым ключом")


if __name__ == "__main__":
    unittest.main()
