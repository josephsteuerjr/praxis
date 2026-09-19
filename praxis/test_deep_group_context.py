from __future__ import annotations

import asyncio
import datetime
import contextlib
import json
import os
import sys
import tempfile
import time
import types
import unittest
from collections import defaultdict, deque
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("PRAXIS_TEST", "1")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault("TELEGRAM_SESSION", ":memory:")

import agent
import group_context
import memory_fts
import rooms
import telegram_topics


class _ImportClient:
    def __init__(self, *args, **kwargs):
        pass

    def on(self, *args, **kwargs):
        return lambda fn: fn


class _ImportEvents:
    @staticmethod
    def NewMessage(*args, **kwargs):
        return object()

    @staticmethod
    def MessageEdited(*args, **kwargs):
        return object()

    @staticmethod
    def MessageDeleted(*args, **kwargs):
        return object()

    @staticmethod
    def ChatAction(*args, **kwargs):
        return object()


if "mtproto_runner" not in sys.modules:
    prior = sys.modules.get("telethon")
    fake = types.ModuleType("telethon")
    fake.TelegramClient = _ImportClient
    fake.events = _ImportEvents
    sys.modules["telethon"] = fake
    try:
        import mtproto_runner as runner
    finally:
        if prior is None:
            sys.modules.pop("telethon", None)
        else:
            sys.modules["telethon"] = prior
else:
    import mtproto_runner as runner


class TempGroupMemory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.memory = self.base / "memory"
        self.groups = self.memory / "groups"
        self.state = self.memory / ".state" / "group_context"
        self.patchers = [
            patch.object(group_context, "BASE", self.base),
            patch.object(group_context, "MEM_DIR", self.memory),
            patch.object(group_context, "GROUPS_DIR", self.groups),
            patch.object(group_context, "STATE_DIR", self.state),
            patch.object(group_context, "_KEY_CACHE", {}),
            # Real hot rebuilds must read only this test's journal and projections.
            patch.object(runner.bufstore, "BASE", self.base),
            patch.object(runner.bufstore, "BUF_DIR", self.memory / ".buffers"),
            patch.object(runner.bufstore, "STATE_DIR", self.memory / ".state"),
            patch.object(runner.bufstore, "META_PATH",
                         self.memory / ".state" / "buf_meta.json"),
            patch.object(runner, "_buf", defaultdict(lambda: deque(maxlen=runner.BUF_MAXLEN))),
            patch.object(runner, "_buffer_message_ids",
                         defaultdict(lambda: deque(maxlen=runner.BUF_MAXLEN))),
            patch.object(runner, "_buf_dirty", set()),
            patch.object(runner, "_persisted_life_sources", set()),
            # Реестр маршрутов тоже в песочницу: иначе свидетельство, записанное
            # одним тестом, доживает до чужого и молча меняет place_of соседям
            # (поймано 22.08: verdict от бэкфил-теста уводил tombstone удаления в
            # комнатный hot, и он воскресал в буфере edit-тестов).
            patch.object(runner.telegram_routes, "DIR",
                         self.memory / ".state" / "group_context"),
        ]
        life = self.memory / "life"
        life_paths = {
            "BASE": self.base, "MEM_DIR": self.memory, "LIFE_DIR": life,
            "STATE_DIR": self.memory / ".state" / "life",
            "LEGACY_SUMMARIES_DIR": self.memory / ".summaries",
            "DIALOGUES_DIR": self.memory / "dialogues",
            **{name.upper() + "_DIR": life / name for name in (
                "events", "compacts", "episodes", "claims", "patches", "reflections",
            )},
        }
        self.patchers.extend(patch.object(runner.memory_life, name, path)
                             for name, path in life_paths.items())
        for item in self.patchers:
            item.start()
            # LIFO restoration also runs if a later setUp step or the test fails.
            self.addCleanup(item.stop)

    def add(self, peer, topic, mid, text, sender=10, name="Alice", reply=None,
            title=""):
        return group_context.observe_message(
            peer_id=peer, topic_id=topic, message_id=mid,
            sender_id=sender, sender_name=name, reply_to_message_id=reply,
            timestamp=f"2026-07-14T12:{mid:02d}:00Z", text=text,
            topic_title=title,
        )


class TestBranchIsTheReplyChain(TempGroupMemory):
    """Ветка — это реплай-цепочка, а не колонка topic_id.

    Регрессия 24.07.2026: вне форума Telegram не ставит reply_to_top_id на первый
    ответ, поэтому корень цепочки и первый ответ на него архивировались с
    topic_id=None, а всё остальное — под topic_id=<корень>. Фильтр по колонке
    выбрасывал ровно те два сообщения, о которых ветка и была, причём срез выглядел
    цельным. Живая улика: «у меня в топик попал только твой ответ, без сообщения
    #93708» — Арету пришлось дублировать выпавшее руками.
    """

    def test_chain_root_and_its_first_reply_are_part_of_the_branch(self):
        # Как это лежит в живом архиве.
        self.add("-1001", None, 7, "открывай своё репо")                      # корень
        self.add("-1001", None, 8, "имя репы было другое", sender=20,
                 name="Arete", reply=7)                                       # 1-й ответ
        self.add("-1001", 7, 10, "жду твой ход", reply=7)                     # 2-й уровень
        self.add("-1001", 99, 11, "совсем другая ветка", sender=30, name="Vadim")

        seen = group_context.context("-1001", topic_id=7, limit=20)
        self.assertIn("жду твой ход", seen)
        self.assertIn("открывай своё репо", seen)      # корень — предмет разговора
        self.assertIn("имя репы было другое", seen)    # выпадавший первый ответ
        self.assertNotIn("совсем другая ветка", seen)  # чужая ветка не втянута

    def test_bare_peer_branch_is_not_polluted_by_numbered_branches(self):
        """topic_id=None — такая же ветка, и она обязана остаться собой."""

        self.add("-1001", None, 1, "разговор на корневом уровне")
        for mid in range(20, 40):
            self.add("-1001", 99, mid, f"шум чужой ветки {mid}", sender=30, name="Noise")

        seen = group_context.context("-1001", topic_id=None, limit=50)
        self.assertIn("разговор на корневом уровне", seen)
        self.assertNotIn("шум чужой ветки", seen)

    def test_forum_topic_isolation_is_unchanged(self):
        """В настоящем форуме корень — служебный опенер, его прямые ответы и так в
        топике: множество то же самое, контракт изоляции держится."""

        self.add("-1001", 11, 1, "alpha", title="Ideas")
        self.add("-1001", 22, 2, "beta", sender=20, name="Bob", title="Rituals")
        self.add("-1001", 22, 3, "beta-2", sender=20, name="Bob", reply=2, title="Rituals")
        narrow = group_context.context("-1001", topic_id=11, limit=20)
        self.assertIn("alpha", narrow)
        self.assertNotIn("beta", narrow)

    def test_the_root_is_the_subject_and_survives_the_budget(self):
        """Корень — самое старое сообщение цепочки, значит обрез «от новых к старым»
        выбрасывает его первым. Живой случай: ветка #93759 на 33 реплики упиралась
        в бюджет и теряла собственную тему."""

        self.add("-1001", None, 7, "ТЕМА ВЕТКИ: почему у тебя закрытое репо")
        for mid in range(20, 70):
            self.add("-1001", 7, mid, "обсуждение " + "я" * 400, reply=7,
                     sender=30, name="Talker")

        seen = group_context.context("-1001", topic_id=7, limit=200, max_chars=3000)
        self.assertIn("ТЕМА ВЕТКИ", seen)
        self.assertIn("обсуждение", seen)                 # хвост тоже на месте
        self.assertLessEqual(len(seen), 3200)             # бюджет соблюдён
        self.assertEqual(seen.count("ТЕМА ВЕТКИ"), 1)     # и не задвоена

    def test_an_old_edit_cannot_evict_todays_conversation(self):
        """Порядок — по времени, когда сказано. Иначе правка недельной давности
        переезжает в конец ленты и вытесняет сегодняшний разговор."""

        for mid in range(1, 6):
            group_context.observe_message(
                peer_id="-1001", topic_id=7, message_id=mid, sender_id=10,
                sender_name="Alice", reply_to_message_id=7,
                timestamp="2026-07-24T12:00:00Z", text=f"сегодняшний разговор {mid}")
        for mid in range(50, 55):
            group_context.observe_message(
                peer_id="-1001", topic_id=7, message_id=mid, sender_id=20,
                sender_name="Bob", reply_to_message_id=7,
                timestamp="2026-07-17T08:00:00Z", edited_at="2026-07-24T13:00:00Z",
                text=f"прошлонедельное, поправлено сейчас {mid}")

        seen = group_context.context("-1001", topic_id=7, limit=5)
        self.assertIn("сегодняшний разговор", seen)



class TestCanonicalArchive(TempGroupMemory):
    def test_exact_topic_dedup_map_and_search_are_root_bounded(self):
        self.assertTrue(self.add("-1001", 11, 1, "alpha mushrooms", title="Ideas"))
        self.assertTrue(self.add("-1001", 22, 1, "beta ritual", sender=20, name="Bob",
                                 title="Rituals"))
        self.assertFalse(self.add("-1001", 11, 1, "changed"))
        self.assertTrue(self.add("-1002", 11, 1, "private neighbouring alpha"))

        first = group_context.context("-1001", topic_id=11, limit=20)
        second = group_context.context("-1001", topic_id=22, limit=20)
        self.assertIn("alpha mushrooms", first)
        self.assertNotIn("beta ritual", first)
        self.assertIn("beta ritual", second)
        self.assertEqual(group_context.search("-1001", "neighbouring"), [])

        mapped = group_context.projection("-1001")
        self.assertEqual(mapped["message_count"], 2)
        self.assertEqual(set(mapped["topics"]), {"11", "22"})
        self.assertEqual(len(mapped["participants"]), 2)
        self.assertTrue(
            group_context.projection_markdown_path("-1001").read_text(encoding="utf-8")
            .startswith("<!-- praxis-generated:"))
        sources = memory_fts.iter_sources(
            base=self.base, memory_dir=self.memory, skills_dir=None)
        paths = {item.path: item.kind for item in sources}
        self.assertEqual(paths[group_context.archive_path("-1001")], "group_message")
        self.assertNotIn(group_context.projection_markdown_path("-1001"), paths)

    def test_message_edit_is_an_idempotent_append_only_revision(self):
        self.assertTrue(self.add("-1001", 11, 7, "original"))
        self.assertFalse(self.add("-1001", 11, 7, "original"))
        arguments = {
            "peer_id": "-1001", "topic_id": 11, "message_id": 7,
            "sender_id": 10, "sender_name": "Alice",
            "reply_to_message_id": None,
            "timestamp": "2026-07-14T12:07:00Z",
            "edited_at": "2026-07-14T12:09:00Z",
            "text": "corrected",
        }
        self.assertTrue(group_context.observe_message(**arguments))
        self.assertFalse(group_context.observe_message(**arguments))
        same_second = {**arguments, "text": "final same-second correction"}
        self.assertTrue(group_context.observe_message(**same_second))
        self.assertFalse(group_context.observe_message(**same_second))

        rows = [row for row in group_context.iter_records("-1001", max_records=None)
                if row.get("kind") == "message"]
        self.assertEqual(len(rows), 3)
        self.assertIsNone(rows[0].get("edited_at"))
        self.assertEqual(rows[1]["edited_at"], "2026-07-14T12:09:00Z")
        rendered = group_context.context("-1001", topic_id=11, limit=10)
        self.assertNotIn("original", rendered)
        self.assertNotIn("corrected", rendered)
        self.assertIn("final same-second correction", rendered)
        self.assertIn("edited=2026-07-14T12:09:00Z", rendered)
        mapped = group_context.projection("-1001")
        self.assertEqual(mapped["message_count"], 1)
        self.assertEqual(mapped["revision_count"], 2)
        self.assertEqual(group_context.search("-1001", "original"), [])
        self.assertEqual(len(group_context.search("-1001", "final")), 1)

    def test_older_edit_appended_late_does_not_replace_newer_revision(self):
        arguments = dict(
            peer_id="-1001", topic_id=11, message_id=41,
            sender_id=10, sender_name="Alice", reply_to_message_id=11,
            timestamp="2026-07-14T12:00:00Z", media="",
        )
        self.assertTrue(group_context.observe_message(
            **arguments, edited_at="2026-07-14T12:10:00Z", text="newer correction"))
        self.assertTrue(group_context.observe_message(
            **arguments, edited_at="2026-07-14T12:05:00Z", text="older delayed correction"))

        rendered = group_context.context("-1001", topic_id=11, limit=10)
        self.assertIn("newer correction", rendered)
        self.assertNotIn("older delayed correction", rendered)
        self.assertEqual(group_context.search("-1001", "older delayed"), [])

    def test_torn_tail_is_isolated_before_next_append(self):
        self.add("-1001", 11, 1, "first")
        path = group_context.archive_path("-1001")
        with path.open("ab") as stream:
            stream.write(b'{"broken":')
        group_context._KEY_CACHE.clear()
        self.assertTrue(self.add("-1001", 11, 2, "second"))
        text = group_context.context("-1001", topic_id=11, limit=20)
        self.assertIn("first", text)
        self.assertIn("second", text)

    def test_structurally_invalid_json_row_cannot_break_projection(self):
        self.add("-1001", 11, 1, "valid")
        invalid = {
            "schema": group_context.SCHEMA_MESSAGE,
            "kind": "message", "peer_id": "-1001", "topic_id": "not-an-id",
            "message_id": "also-bad", "sender_id": None, "sender_name": "Mallory",
            "reply_to_message_id": None, "timestamp": "bad", "edited_at": None,
            "text": "must be ignored", "media": "", "outgoing": False,
        }
        with group_context.archive_path("-1001").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(invalid) + "\n")
        group_context._KEY_CACHE.clear()
        mapped = group_context.rebuild_projection("-1001")
        self.assertEqual(mapped["message_count"], 1)
        self.assertNotIn("must be ignored", group_context.context("-1001", topic_id=11))

    def test_cross_topic_bundle_is_map_plus_marked_matches(self):
        self.add("-1001", 11, 1, "current seed", title="Current")
        self.add("-1001", 22, 2, "shared mycelium idea", title="Elsewhere")
        value = group_context.orientation_bundle(
            "-1001", current_topic=11, query="mycelium", cross_topics="map")
        self.assertIn("aggregate only", value)
        self.assertIn("[CROSS-TOPIC EXCERPT]", value)
        self.assertIn("topic #22", value)

    def test_legacy_invented_topic_title_is_not_a_name(self):
        """Её регрессия 8 от 22.08: старый канон с topic_title="topic #N" не
        показывается как имя — ни в строке ленты (`[topic #24255 «topic #24255»]`),
        ни в карте/проекции. Канон append-only, лечится ЧТЕНИЕ."""
        self.add("-1001", 24255, 1, "живой текст", title="topic #24255")
        rendered = group_context.context("-1001", topic_id=24255, limit=10)
        self.assertIn("topic #24255", rendered)       # адрес ветки остаётся
        self.assertNotIn("«topic #24255»", rendered)  # но именем не притворяется
        self.assertEqual(group_context.topic_title("-1001", 24255), "")
        mapped = group_context.projection("-1001")["topics"]["24255"]
        self.assertEqual(mapped["title"], "",
                         "проекция не сохраняет выдуманный title как настоящий")

    def test_generated_group_map_is_bounded_and_rebuild_stable(self):
        with patch.object(group_context, "MAX_MAP_TOPICS", 3), \
             patch.object(group_context, "MAX_MAP_PARTICIPANTS", 2), \
             patch.object(group_context, "MAX_TOPIC_PARTICIPANTS", 2), \
             patch.object(group_context, "MAX_PARTICIPANT_TOPICS", 2):
            for index in range(5):
                group_context.observe_message(
                    peer_id="-1001", topic_id=100 + index, message_id=index + 1,
                    sender_id=1000 + index, sender_name=f"Person {index}",
                    reply_to_message_id=None,
                    timestamp=datetime.datetime(2026, 7, 14, 12, index,
                                                tzinfo=datetime.timezone.utc),
                    text=f"bounded row {index}", topic_title=f"Topic {index}",
                )
            first = group_context.rebuild_projection("-1001")
            first_md = group_context.projection_markdown_path("-1001").read_bytes()
            second = group_context.rebuild_projection("-1001")

        self.assertEqual(first, second)
        self.assertEqual(first_md, group_context.projection_markdown_path("-1001").read_bytes())
        self.assertEqual(first["topic_count"], 5)
        self.assertEqual(first["participant_count"], 5)
        self.assertEqual(len(first["topics"]), 3)
        self.assertEqual(len(first["participants"]), 2)
        self.assertEqual(len(list(group_context.iter_records("-1001", max_records=None))), 5)


class TestRoomPolicy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.memory = self.base / "memory"
        self.room_dir = self.memory / "rooms"
        self.patchers = [
            patch.object(rooms, "BASE", self.base),
            patch.object(rooms, "MEM_DIR", self.memory),
            patch.object(rooms, "ROOMS_DIR", self.room_dir),
            patch.object(rooms, "ALLOWLIST", self.memory / "rooms_allowlist.json"),
            patch.object(rooms, "FROZEN", self.memory / "frozen_chats.json"),
        ]
        for item in self.patchers:
            item.start()

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temp.cleanup()

    def test_default_is_deep_addressed_and_values_are_clamped(self):
        # 16.09, слово Егора: умолчание участия — `addressed`. Глубина комнаты (лента,
        # сводка, кросс-темы, добор) осталась прежней — поехало ровно участие, и только
        # оно. Почему: из шестнадцати живых комнат пятнадцать стояли `addressed` и одна
        # `reflective` — самая свежая, то есть та, которую ещё не успели поправить руками
        # после протокола входа. Умолчание, которое всегда правят, умолчанием не было.
        self.assertEqual(rooms.room_policy("-1001"), {
            "engagement": "addressed", "context_hot": 200,
            "context_summary_chars": 24000, "cross_topics": "map",
            "backfill_limit": 1500,
        })
        # Явный выбор по-прежнему сильнее умолчания — ниже он и проверяется.
        rooms.profile_update(
            "-1001", engagement="reflective", context_hot=999,
            context_summary_chars=999999, cross_topics="map", backfill_limit=99999,
        )
        self.assertEqual(rooms.room_policy("-1001"), {
            "engagement": "reflective", "context_hot": 500,
            "context_summary_chars": 40000, "cross_topics": "map",
            "backfill_limit": 5000,
        })
        with self.assertRaises(ValueError):
            rooms.profile_update("-1001", engagement="always")

    def test_owner_configure_uses_root_room(self):
        rooms.add_room("-1001")
        with (
            patch.object(agent, "_is_human_owner", return_value=True),
            patch.object(agent, "ROOMS_DIR", self.room_dir),
        ):
            out = agent.tool_manage_room(
                "configure", "-1001", engagement="reflective",
                context_hot=500, context_summary_chars=18000,
                cross_topics="map", backfill_limit=2000,
            )
        self.assertIn('"engagement": "reflective"', out)
        self.assertEqual(rooms.room_policy("-1001")["backfill_limit"], 2000)


class TestTopicRouting(unittest.TestCase):
    def test_topic_create_uses_own_message_id_and_title(self):
        action_type = type("MessageActionTopicCreate", (), {})
        action = action_type()
        action.title = "Introductions"
        message = types.SimpleNamespace(id=77, action=action, reply_to=None)
        route = telegram_topics.route_for_message("-1001", message)
        self.assertEqual(route.topic_id, 77)
        self.assertEqual(telegram_topics.topic_opener_title(message), "Introductions")

    def test_pending_wake_revision_refreshes_payload_without_changing_authority(self):
        original = runner.GroupWake(
            message_id=41, message_ts=1.0, kind="mention", speaker="Alice",
            sender_id=10, owner=True, known=True, family=False,
            context_snapshot="old context",
            reply_targets_snapshot=((41, "Alice", "old gist"), (42, "Bob", "other")),
            media_snapshot=(), addressed=True, query="old query",
            turns_snapshot=((False, "old", "old"),),
        )
        wakes = {"room": original}
        revised_turns = ((False, "new", "new"),)
        with (
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_room_policy_for_state", return_value={}),
            patch.object(runner, "_group_context_frozen",
                         return_value=("new context", revised_turns)),
        ):
            self.assertTrue(runner._refresh_group_wake_context(
                "room", message_id=41, query="new query", author="Alice Revised",
            ))

        current = wakes["room"]
        self.assertEqual(current.context_snapshot, "new context")
        self.assertEqual(current.turns_snapshot, revised_turns)
        self.assertEqual(current.query, "new query")
        self.assertEqual(current.speaker, "Alice Revised")
        self.assertTrue(current.owner)
        self.assertEqual(current.reply_targets_snapshot, (
            (41, "Alice Revised", "new query"), (42, "Bob", "other"),
        ))

    def test_reflective_wake_never_replaces_an_address(self):
        addressed = runner.GroupWake(
            message_id=1, message_ts=1.0, kind="mention", speaker="A",
            sender_id=1, owner=False, known=True, family=False,
            context_snapshot="A: hi", reply_targets_snapshot=(), media_snapshot=(),
            addressed=True,
        )
        ambient = runner.GroupWake(
            message_id=2, message_ts=2.0, kind="ambient", speaker="B",
            sender_id=2, owner=False, known=True, family=False,
            context_snapshot="B: thought", reply_targets_snapshot=(), media_snapshot=(),
            addressed=False,
        )
        with patch.object(runner, "_group_wakes", {}):
            self.assertTrue(runner._install_group_wake("room", addressed))
            self.assertFalse(runner._install_group_wake("room", ambient))
            self.assertIs(runner._group_wakes["room"], addressed)
        with patch.object(runner, "_group_wakes", {}):
            self.assertTrue(runner._install_group_wake("room", ambient))
            self.assertTrue(runner._install_group_wake("room", addressed))
            self.assertIs(runner._group_wakes["room"], addressed)
        self.assertFalse(runner._should_wake(False, False, "maybe", "addressed"))
        self.assertTrue(runner._should_wake(False, False, "maybe", "reflective"))


class _LiveMessage:
    def __init__(self, mid, text, *, mentioned=False):
        self.id = mid
        self.message = text
        self.mentioned = mentioned
        self.is_reply = False
        self.reply_to = types.SimpleNamespace(
            reply_to_top_id=77, reply_to_msg_id=77, forum_topic=True)
        self.reply_to_msg_id = 77
        self.date = datetime.datetime.now(datetime.timezone.utc)
        self.action = None


class _LiveEvent:
    is_private = False

    def __init__(self, message):
        self.message = message
        self.chat_id = -1001
        self.sender_id = 10
        self.sender = types.SimpleNamespace(
            id=10, first_name="Alice", last_name="", username=None,
            usernames=(), is_self=False,
        )

    async def get_sender(self):
        return self.sender


class _EditedMessage(_LiveMessage):
    def __init__(self, mid, text):
        super().__init__(mid, text)
        self.date = datetime.datetime(
            2026, 7, 14, 12, 0, tzinfo=datetime.timezone.utc)
        self.edit_date = datetime.datetime(
            2026, 7, 14, 12, 5, tzinfo=datetime.timezone.utc)
        self.photo = object()


class _BlockingEditedEvent(_LiveEvent):
    def __init__(self, message, gate: asyncio.Event):
        super().__init__(message)
        self.gate = gate

    async def get_sender(self):
        await self.gate.wait()
        return self.sender


class TestEditedIncoming(TempGroupMemory, unittest.IsolatedAsyncioTestCase):
    async def test_admitted_edit_is_one_archive_revision_and_one_topic_life_event(self):
        event = _LiveEvent(_EditedMessage(41, "corrected caption"))
        buffers = defaultdict(lambda: deque(maxlen=600))
        life = Mock(return_value={"id": "life-edit-41"})
        hot_revision = Mock(return_value={"matched": True})
        meta_update = Mock()
        refresh = Mock(return_value=False)
        wakes = {}

        with (
            patch.object(runner.rooms, "is_frozen", return_value=False),
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner.rooms, "effective_mode", return_value="normal"),
            # С 22.08 тема — ключ хранения только по знанию: форум + каталог,
            # каталог приезжает preflight'ом до фиксации маршрута.
            patch.object(runner, "_known_forum", lambda *a, **k: True),
            patch.object(runner, "_catalog_for_routing",
                         AsyncMock(return_value=frozenset({77}))),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.group_context, "topic_title", return_value="Ideas"),
            patch.object(runner, "_topic_titles", {}),
            patch.object(runner, "_buf", buffers),
            patch.object(runner, "_buf_dirty", set()),
            patch.object(runner, "_under_tests", return_value=False),
            patch.object(runner.memory_life, "record_message", life),
            patch.object(runner.memory_life, "note_message_revision", hot_revision),
            patch.object(runner.bufstore, "meta_update", meta_update),
            patch.object(runner, "_refresh_group_wake_context", refresh),
            patch.object(runner, "_group_wakes", wakes),
        ):
            await runner.on_edited(event)
            await runner.on_edited(event)

        rows = [
            row for row in group_context.iter_records("-1001", max_records=None)
            if row.get("kind") == "message"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["topic_id"], 77)
        self.assertEqual(rows[0]["message_id"], 41)
        self.assertEqual(rows[0]["sender_id"], 10)
        self.assertEqual(rows[0]["sender_name"], "Alice")
        self.assertEqual(rows[0]["reply_to_message_id"], 77)
        self.assertEqual(rows[0]["timestamp"], "2026-07-14T12:00:00Z")
        self.assertEqual(rows[0]["edited_at"], "2026-07-14T12:05:00Z")
        self.assertEqual(rows[0]["text"], "corrected caption")
        self.assertTrue(rows[0]["media"])

        topic_lines = list(buffers["-1001__topic__77"])
        self.assertEqual(len(topic_lines), 1)
        self.assertIn("edited #41", topic_lines[0])
        self.assertIn("2026-07-14T12:05:00Z", topic_lines[0])
        life.assert_called_once()
        source_id = runner._edit_revision_source_id(
            41,
            datetime.datetime(2026, 7, 14, 12, 5, tzinfo=datetime.timezone.utc),
            text="corrected caption", media="[Изображение]",
        )
        self.assertEqual(
            life.call_args.kwargs["source_id"],
            source_id,
        )
        self.assertEqual(
            life.call_args.kwargs["dedupe_key"],
            f"telegram:-1001__topic__77:{source_id}:in",
        )
        meta_update.assert_called_once()
        hot_revision.assert_called_once_with(
            "-1001__topic__77", 41,
            "Alice, reply to #77: [Изображение] corrected caption",
            actor="Alice", ts=1784030400.0,
        )
        self.assertEqual(refresh.call_count, 1)
        refresh.assert_called_with(
            "-1001__topic__77", message_id=41,
            query="[Изображение] corrected caption", author="Alice",
            media_snapshot=(),
        )
        self.assertEqual(wakes, {})

    async def test_edit_stays_with_the_original_key_across_catalogue_arrival(self):
        """Фан-аут 22.08 (P1): оригинал лёг в комнату до прихода каталога — правка
        обязана лечь туда же, а не уехать в тему по сегодняшнему знанию. Одна
        логическая запись не разъезжается по двум ключам."""
        group_context.observe_message(
            peer_id="-1001", topic_id=None, message_id=41, sender_id=10,
            sender_name="Alice", reply_to_message_id=77,
            timestamp="2026-07-14T12:00:00Z", text="original filed in the room")
        event = _LiveEvent(_EditedMessage(41, "corrected later"))
        buffers = defaultdict(lambda: deque(maxlen=600))

        with (
            patch.object(runner.rooms, "is_frozen", return_value=False),
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner.rooms, "effective_mode", return_value="normal"),
            patch.object(runner, "_known_forum", lambda *a, **k: True),
            patch.object(runner, "_catalog_for_routing",
                         AsyncMock(return_value=frozenset({77}))),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.group_context, "topic_title", return_value="Ideas"),
            patch.object(runner, "_topic_titles", {}),
            patch.object(runner, "_buf", buffers),
            patch.object(runner, "_buf_dirty", set()),
            patch.object(runner, "_under_tests", return_value=False),
            patch.object(runner.memory_life, "record_message",
                         Mock(return_value={"id": "life-anchor-41"})),
            patch.object(runner.memory_life, "note_message_revision",
                         Mock(return_value={"matched": True})),
            patch.object(runner, "_sync_buffer_from_hot", Mock()),
            patch.object(runner.bufstore, "meta_update"),
            patch.object(runner, "_group_wakes", {}),
        ):
            await runner.on_edited(event)

        self.assertTrue(list(buffers["-1001"]),
                        "правка легла к оригиналу — в комнату")
        self.assertIn("corrected later", buffers["-1001"][0])
        self.assertFalse(list(buffers["-1001__topic__77"]),
                         "и не уехала в тему по сегодняшнему каталогу")

    async def test_two_payloads_with_same_edit_second_are_distinct_revisions(self):
        first = _LiveEvent(_EditedMessage(51, "first correction"))
        second = _LiveEvent(_EditedMessage(51, "second correction"))
        buffers = defaultdict(lambda: deque(maxlen=600))
        life = Mock(return_value={"id": "life-edit-51"})

        with (
            patch.object(runner.rooms, "is_frozen", return_value=False),
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner.rooms, "effective_mode", return_value="normal"),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.group_context, "topic_title", return_value="Ideas"),
            patch.object(runner, "_topic_titles", {}),
            patch.object(runner, "_buf", buffers),
            patch.object(runner, "_buf_dirty", set()),
            patch.object(runner, "_under_tests", return_value=False),
            patch.object(runner.memory_life, "record_message", life),
            patch.object(runner.bufstore, "meta_update"),
            patch.object(runner, "_group_wakes", {}),
        ):
            await runner.on_edited(first)
            await runner.on_edited(first)  # replay of one payload stays idempotent
            await runner.on_edited(second)
            await runner.on_edited(second)

        rows = [
            row for row in group_context.iter_records("-1001", max_records=None)
            if row.get("kind") == "message" and row.get("message_id") == 51
        ]
        self.assertEqual([row["text"] for row in rows], [
            "first correction", "second correction",
        ])
        self.assertEqual(life.call_count, 2)
        source_ids = [call.kwargs["source_id"] for call in life.call_args_list]
        self.assertEqual(len(set(source_ids)), 2)
        self.assertTrue(all(":edit:2026-07-14T12:05:00Z:" in value
                            for value in source_ids))

    async def test_same_second_concurrent_edits_follow_reception_not_completion(self):
        gate = asyncio.Event()
        first = _BlockingEditedEvent(_EditedMessage(52, "received first"), gate)
        second = _LiveEvent(_EditedMessage(52, "received second"))
        buffers = defaultdict(lambda: deque(maxlen=600))
        message_ids = defaultdict(lambda: deque(maxlen=600))

        with (
            patch.object(runner.rooms, "is_frozen", return_value=False),
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner.rooms, "effective_mode", return_value="normal"),
            # С 22.08 тема — ключ хранения только по знанию: форум + каталог,
            # каталог приезжает preflight'ом до фиксации маршрута.
            patch.object(runner, "_known_forum", lambda *a, **k: True),
            patch.object(runner, "_catalog_for_routing",
                         AsyncMock(return_value=frozenset({77}))),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.group_context, "topic_title", return_value="Ideas"),
            patch.object(runner, "_topic_titles", {}),
            patch.object(runner, "_buf", buffers),
            patch.object(runner, "_buffer_message_ids", message_ids),
            patch.object(runner, "_buf_dirty", set()),
            patch.object(runner, "_under_tests", return_value=False),
            patch.object(runner.memory_life, "note_message_revision", return_value={"matched": True}),
            patch.object(runner.bufstore, "meta_update"),
            patch.object(runner, "_group_wakes", {}),
            patch.object(runner.telegram_followups.LEDGER, "revise_response", return_value=None),
        ):
            first_task = asyncio.create_task(runner.on_edited(first))
            await asyncio.sleep(0)
            await runner.on_edited(second)
            gate.set()
            await first_task

        current = group_context.latest_message("-1001", 52)
        self.assertEqual(current["text"], "received second")
        self.assertEqual(len(buffers["-1001__topic__77"]), 1)
        self.assertIn("received second", buffers["-1001__topic__77"][0])
        hot = runner.memory_life.hot_records("-1001__topic__77")
        self.assertEqual(len(hot), 1)
        self.assertIn("received second", hot[0]["line"])

    def test_route_drift_edit_collapses_stale_topic_copy(self):
        self.add("-1001", 77, 53, "stale topic-routed original", reply=77)
        group_context.observe_message(
            peer_id="-1001", topic_id=None, message_id=53,
            sender_id=10, sender_name="Alice", reply_to_message_id=None,
            timestamp="2026-07-14T12:00:00Z",
            edited_at="2026-07-14T12:05:00Z", revision_order=2,
            text="current root-routed edit",
        )

        current = group_context.latest_message("-1001", 53)
        self.assertEqual(current["text"], "current root-routed edit")
        stale_hits = group_context.search("-1001", "stale topic-routed")
        self.assertTrue(stale_hits)
        self.assertTrue(all(row["text"] == "current root-routed edit" for row in stale_hits))
        self.assertEqual(group_context.search("-1001", "current root-routed")[0]["text"],
                         "current root-routed edit")
        self.assertIn("current root-routed edit",
                      group_context.context("-1001", topic_id=None, limit=10))

    async def test_private_edit_replaces_current_dm_text(self):
        event = _LiveEvent(_EditedMessage(61, "corrected privately"))
        event.is_private = True
        event.chat_id = 10
        buffers = defaultdict(lambda: deque(maxlen=600))
        buffers["10"].append("Alice: obsolete private text")
        message_ids = defaultdict(lambda: deque(maxlen=600))
        message_ids["10"].append("61")
        hot = Mock(return_value={"matched": True})
        arms = Mock()
        meta = {"10": {
            "is_dm": True, "origin_message_id": 61,
            "origin_text": "obsolete private text", "name": "Alice",
        }}

        with (
            patch.object(runner, "_buf", buffers),
            patch.object(runner, "_buffer_message_ids", message_ids),
            patch.object(runner, "_buf_dirty", set()),
            patch.object(runner, "_pending_media", defaultdict(lambda: deque(maxlen=16))),
            patch.object(runner, "_meta", meta),
            patch.object(runner, "_under_tests", return_value=False),
            patch.object(runner.memory_life, "record_message", return_value={"id": "dm-edit"}),
            patch.object(runner.memory_life, "note_message_revision", hot),
            patch.object(runner.bufstore, "meta_update"),
            patch.object(runner, "_arm", arms),
        ):
            await runner.on_edited(event)

        self.assertEqual(len(buffers["10"]), 1)
        self.assertIn("corrected privately", buffers["10"][0])
        self.assertNotIn("obsolete private text", buffers["10"][0])
        self.assertEqual(meta["10"]["origin_text"], "corrected privately")
        hot.assert_called_once()
        arms.assert_called_once_with("10")

    async def test_edit_replay_repairs_a_failed_life_append(self):
        event = _LiveEvent(_EditedMessage(71, "repair durable edit"))
        buffers = defaultdict(lambda: deque(maxlen=600))
        source_ids = defaultdict(lambda: deque(maxlen=600))
        life = Mock(side_effect=[OSError("disk unavailable"), {"id": "repaired"}])
        hot = Mock(return_value={"matched": True})

        with (
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner.rooms, "effective_mode", return_value="normal"),
            # С 22.08 тема — ключ хранения только по знанию: форум + каталог,
            # каталог приезжает preflight'ом до фиксации маршрута.
            patch.object(runner, "_known_forum", lambda *a, **k: True),
            patch.object(runner, "_catalog_for_routing",
                         AsyncMock(return_value=frozenset({77}))),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.group_context, "topic_title", return_value="Ideas"),
            patch.object(runner, "_topic_titles", {}),
            patch.object(runner, "_buf", buffers),
            patch.object(runner, "_buffer_message_ids", source_ids),
            patch.object(runner, "_persisted_life_sources", set()),
            patch.object(runner, "_buf_dirty", set()),
            patch.object(runner, "_under_tests", return_value=False),
            patch.object(runner.memory_life, "record_message", life),
            patch.object(runner.memory_life, "note_message_revision", hot),
            patch.object(runner.bufstore, "meta_update"),
            patch.object(runner, "_group_wakes", {}),
        ):
            await runner.on_edited(event)
            await runner.on_edited(event)

        self.assertEqual(life.call_count, 2)
        hot.assert_called_once()
        self.assertEqual(len(buffers["-1001__topic__77"]), 1)
        self.assertIn("repair durable edit", buffers["-1001__topic__77"][0])

    async def test_expired_frozen_mode_does_not_block_an_edit(self):
        event = _LiveEvent(_EditedMessage(43, "arrived after expiry"))
        archive = Mock(return_value=True)
        push = Mock()
        runner.rooms.set_mode(
            "-1001", "frozen", set_by="praxis", ttl_h=1,
            now=time.time() - 2 * 3600,
        )
        self.assertTrue(runner.rooms.is_frozen("-1001"))

        with (
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.group_context, "observe_message", archive),
            patch.object(runner, "_buf_push", push),
            patch.object(runner.group_context, "topic_title", return_value="Ideas"),
            patch.object(runner, "_topic_titles", {}),
            patch.object(runner, "_under_tests", return_value=False),
            patch.object(runner.memory_life, "record_message", return_value={"id": "life-edit-43"}),
            patch.object(runner.bufstore, "meta_update"),
        ):
            await runner.on_edited(event)

        archive.assert_called_once()
        self.assertFalse(runner.rooms.is_frozen("-1001"))
        self.assertEqual(runner.rooms.effective_mode("-1001"), "normal")

    async def test_edit_obeys_frozen_allowlist_and_dead_room_gates(self):
        event = _LiveEvent(_EditedMessage(42, "must stay outside"))
        cases = (
            (True, True, "frozen"),
            (False, False, "normal"),
            (False, True, "dead"),
        )
        for frozen, allowed, mode in cases:
            archive = Mock(return_value=True)
            push = Mock()
            with self.subTest(frozen=frozen, allowed=allowed, mode=mode):
                with (
                    patch.object(runner.rooms, "is_frozen", return_value=frozen),
                    patch.object(runner.rooms, "is_allowed", return_value=allowed),
                    patch.object(runner.rooms, "effective_mode", return_value=mode),
                    patch.object(runner, "_group_archive_enabled", return_value=True),
                    patch.object(runner.group_context, "observe_message", archive),
                    patch.object(runner, "_buf_push", push),
                ):
                    await runner.on_edited(event)
            archive.assert_not_called()
            push.assert_not_called()


class _DeletedEvent:
    def __init__(self, mids, *, chat_id=-1001):
        self.deleted_ids = list(mids)
        self.chat_id = chat_id


class TestDeletedIncoming(TempGroupMemory, unittest.IsolatedAsyncioTestCase):
    def test_tombstone_hides_current_text_but_keeps_append_only_audit(self):
        self.add("-1001", 77, 41, "secret text", reply=77, title="Ideas")
        self.assertTrue(group_context.observe_deletion(
            peer_id="-1001", message_id=41,
            timestamp="2026-07-14T12:06:00Z",
        ))
        self.assertFalse(group_context.observe_deletion(
            peer_id="-1001", message_id=41,
            timestamp="2026-07-14T12:07:00Z",
        ))

        rows = list(group_context.iter_records("-1001", max_records=None))
        self.assertEqual([row["kind"] for row in rows], ["message", "deletion"])
        rendered = group_context.context("-1001", topic_id=77, limit=10)
        self.assertIn("message #41", rendered)
        self.assertIn("deleted=2026-07-14T12:06:00Z", rendered)
        self.assertIn("[message deleted in Telegram]", rendered)
        self.assertNotIn("secret text", rendered)
        self.assertEqual(group_context.search("-1001", "secret"), [])
        full = group_context.message_text("-1001", message_id=41)
        self.assertIn("[message deleted in Telegram]", full)
        self.assertNotIn("secret text", full)

        # A later history backfill may append the old body after the tombstone.  Current
        # surfaces must still treat deletion as terminal instead of resurrecting it.
        self.add("-1001", 77, 41, "secret text resurrected", reply=77, title="Ideas")
        rendered = group_context.context("-1001", topic_id=77, limit=10)
        self.assertIn("[message deleted in Telegram]", rendered)
        self.assertNotIn("resurrected", rendered)
        self.assertIn("former sender=Alice", rendered)
        rows = group_context.context_rows("-1001", topic_id=77, limit=10)
        deleted = next(row for row in rows if "message #41" in row["line"])
        self.assertFalse(deleted["self"], "Telegram tombstone must not become my assistant turn")
        self.assertEqual(group_context.search("-1001", "resurrected"), [])

    def test_unknown_deletion_binds_to_only_one_backfilled_topic(self):
        self.assertTrue(group_context.observe_deletion(
            peer_id="-1001", message_id=41,
            timestamp="2026-07-14T12:06:00Z",
        ))
        self.add("-1001", 11, 41, "first backfilled body", reply=11)
        self.add("-1001", 22, 41, "independent topic body", reply=22)

        first = group_context.context("-1001", topic_id=11, limit=10)
        second = group_context.context("-1001", topic_id=22, limit=10)
        self.assertIn("[message deleted in Telegram]", first)
        self.assertNotIn("first backfilled body", first)
        self.assertIn("independent topic body", second)
        self.assertNotIn("[message deleted in Telegram]", second)

    async def test_admitted_deletion_archives_and_updates_live_context_without_wake(self):
        self.add("-1001", 77, 41, "soon removed", reply=77, title="Ideas")
        buffers = defaultdict(lambda: deque(maxlen=600))
        life = Mock(return_value={"id": "life-delete-41"})
        arms = Mock()
        refresh = Mock(return_value=True)
        wake = runner.GroupWake(
            message_id=41, message_ts=1.0, kind="mention", speaker="Alice",
            sender_id=10, owner=False, known=True, family=False,
            context_snapshot="Alice: soon removed", reply_targets_snapshot=(),
            media_snapshot=(), addressed=True, query="soon removed",
        )
        wakes = {"-1001__topic__77": wake}

        with (
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner.rooms, "effective_mode", return_value="normal"),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.telegram_routes, "place_of", side_effect=lambda cid: cid),
            patch.object(runner, "_buf", buffers),
            patch.object(runner, "_buf_dirty", set()),
            patch.object(runner, "_meta", {}),
            patch.object(runner, "_under_tests", return_value=False),
            patch.object(runner.memory_life, "record_message", life),
            patch.object(runner.bufstore, "meta_update"),
            patch.object(runner, "_arm", arms),
            patch.object(runner, "_refresh_group_wake_context", refresh),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner.time, "time", return_value=1787170200.0),
        ):
            await runner.on_deleted(_DeletedEvent([41]))
            await runner.on_deleted(_DeletedEvent([41]))

        rows = [row for row in group_context.iter_records("-1001", max_records=None)
                if row.get("message_id") == 41]
        self.assertEqual([row["kind"] for row in rows], ["message", "deletion"])
        self.assertEqual(len(buffers["-1001"]), 1)
        self.assertEqual(len(buffers["-1001__topic__77"]), 1)
        self.assertIn("deleted #41", buffers["-1001__topic__77"][0])
        self.assertIn("former sender: Alice", buffers["-1001__topic__77"][0])
        self.assertEqual(life.call_count, 2)
        self.assertEqual(
            {call.args[0] for call in life.call_args_list},
            {"-1001", "-1001__topic__77"},
        )
        self.assertEqual(
            {call.kwargs["source_id"] for call in life.call_args_list}, {"41:delete"})
        arms.assert_called_once_with("-1001__topic__77")
        refresh.assert_not_called()
        self.assertEqual(wakes, {})

    async def test_deletion_ignores_hundreds_of_local_phantom_topic_buffers(self):
        """Buffer sightings cannot mint deletion routes for a peer-local message id."""
        self.add("-1001", 77, 41, "soon removed", reply=77, title="Ideas")
        buffers = defaultdict(lambda: deque(maxlen=600))
        source_ids = defaultdict(lambda: deque(maxlen=600))
        recent = defaultdict(lambda: deque(maxlen=12))
        pending_media = defaultdict(lambda: deque(maxlen=16))
        stale_line = "Alice: stale local clone"
        for topic_id in range(1000, 1450):
            chat_id = f"-1001__topic__{topic_id}"
            buffers[chat_id].append(stale_line)
            source_ids[chat_id].append("41")
            recent[chat_id].append((41, "Alice", "stale local clone"))
            pending_media[chat_id].append(types.SimpleNamespace(message_id=41))

        true_chat = "-1001__topic__77"
        buffers[true_chat].append("Alice: soon removed")
        source_ids[true_chat].append("41")
        recent[true_chat].append((41, "Alice", "soon removed"))
        pending_media[true_chat].append(types.SimpleNamespace(message_id=41))
        wake = runner.GroupWake(
            message_id=41, message_ts=1.0, kind="mention", speaker="Alice",
            sender_id=10, owner=False, known=True, family=False,
            context_snapshot="Alice: soon removed", reply_targets_snapshot=(),
            media_snapshot=(), addressed=True, query="soon removed",
        )
        wakes = {true_chat: wake}
        life = Mock(return_value={"id": "life-delete-41"})
        arms = Mock()

        with (
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner.rooms, "effective_mode", return_value="normal"),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.telegram_routes, "place_of", side_effect=lambda cid: cid),
            patch.object(runner, "_buf", buffers),
            patch.object(runner, "_buffer_message_ids", source_ids),
            patch.object(runner, "_recent_msgs", recent),
            patch.object(runner, "_pending_media", pending_media),
            patch.object(runner, "_buf_dirty", set()),
            patch.object(runner, "_meta", {}),
            patch.object(runner, "_persisted_life_sources", set()),
            patch.object(runner, "_under_tests", return_value=False),
            patch.object(runner.memory_life, "record_message", life),
            patch.object(runner.memory_life, "note_message_revision"),
            patch.object(runner, "_sync_buffer_from_hot", return_value=False),
            patch.object(runner.bufstore, "meta_update"),
            patch.object(runner.telegram_followups.LEDGER, "delete_response", return_value=None),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_arm", arms),
            patch.object(runner.time, "time", return_value=1787170200.0),
        ):
            await runner.on_deleted(_DeletedEvent([41]))
            await runner.on_deleted(_DeletedEvent([41]))

        projected = {call.args[0] for call in life.call_args_list}
        self.assertEqual(projected, {"-1001", true_chat})
        self.assertEqual(life.call_count, 2, "replayed tombstone must dedupe per true route")
        self.assertEqual(len(buffers["-1001"]), 1)
        self.assertIn("deleted #41", buffers["-1001"][0])
        self.assertEqual(len(buffers[true_chat]), 1)
        self.assertIn("deleted #41", buffers[true_chat][0])
        self.assertEqual(list(recent[true_chat]), [])
        self.assertNotIn(true_chat, pending_media)
        self.assertEqual(wakes, {})
        arms.assert_called_once_with(true_chat)

        phantom_ids = [f"-1001__topic__{topic_id}" for topic_id in range(1000, 1450)]
        self.assertTrue(all(list(buffers[key]) == [stale_line] for key in phantom_ids))
        self.assertTrue(all(list(source_ids[key]) == ["41"] for key in phantom_ids))
        self.assertTrue(all(list(recent[key]) == [(41, "Alice", "stale local clone")]
                            for key in phantom_ids))
        self.assertTrue(all(len(pending_media[key]) == 1 for key in phantom_ids))

    async def test_unknown_and_general_deletions_project_only_to_root(self):
        for previous, expected in (({}, ("-1001",)), ({"topic_id": 1}, ("-1001",))):
            with self.subTest(previous=previous):
                self.assertEqual(
                    runner._deletion_projection_conversations("-1001", previous), expected)

    def test_boot_restore_skips_absorbed_aliases_but_keeps_true_topics(self):
        restored = {
            "-1001": ["root"],
            "-1001__topic__77": ["true topic"],
            "-1001__topic__900": ["legacy clone"],
        }

        def place_of(cid):
            return "-1001" if cid == "-1001__topic__900" else cid

        with patch.object(runner.telegram_routes, "place_of", side_effect=place_of):
            accepted, absorbed = runner._restored_buffer_partition(restored)

        self.assertEqual(accepted, {
            "-1001": ["root"],
            "-1001__topic__77": ["true topic"],
        })
        self.assertEqual(absorbed, (("-1001__topic__900", "-1001"),))
        self.assertEqual(restored["-1001__topic__900"], ["legacy clone"],
                         "partition is read-only; migration/quarantine remains separate")

    def test_boot_restore_fails_open_when_route_registry_is_unreadable(self):
        restored = {"-1001__topic__900": ["unclassified"]}
        with patch.object(runner.telegram_routes, "place_of", side_effect=OSError("down")):
            accepted, absorbed = runner._restored_buffer_partition(restored)
        self.assertEqual(accepted, restored)
        self.assertEqual(absorbed, ())

    async def test_deletion_archive_retries_a_transient_first_failure(self):
        self.add("-1001", 77, 42, "remove after transient failure", title="Ideas")
        real = group_context.observe_deletion
        attempts = 0

        def flaky(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporary disk error")
            return real(**kwargs)

        with (
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner.rooms, "effective_mode", return_value="normal"),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.group_context, "observe_deletion", side_effect=flaky),
            patch.object(runner, "_buf", defaultdict(lambda: deque(maxlen=600))),
            patch.object(runner, "_buffer_message_ids", defaultdict(lambda: deque(maxlen=600))),
            patch.object(runner, "_buf_dirty", set()),
            patch.object(runner, "_meta", {}),
            patch.object(runner, "_under_tests", return_value=False),
            patch.object(runner.memory_life, "record_message", return_value={"id": "life-delete"}),
            patch.object(runner.bufstore, "meta_update"),
            patch.object(runner.telegram_followups.LEDGER, "delete_response", return_value=None),
            patch.object(runner, "_group_wakes", {}),
        ):
            await runner.on_deleted(_DeletedEvent([42]))

        self.assertEqual(attempts, 2)
        current = group_context.latest_message("-1001", 42)
        self.assertEqual(current["kind"], "deletion")

    async def test_deletion_projects_followup_even_when_archive_tombstone_is_replayed(self):
        ledger_path = self.base / "followups.json"
        ledger = runner.telegram_followups.FollowUpLedger(ledger_path)
        item = ledger.create(
            target_ref="-1001", target_label="Ideas", target_peer_id=-1001,
            target_user_id=None, sent_message_id=40,
            request_text="сообщи, когда ответит", notify_owner=True,
            notice_source="owner", sent_at=1)
        ledger.observe_incoming(
            peer_id=-1001, sender_id=10, message_id=41, text="STALE ANSWER",
            reply_to_message_id=40, received_at=2)
        # Archive already accepted deletion in an earlier partial attempt.  Handler must
        # still repair the independently durable follow-up projection on replay.
        group_context.observe_deletion(
            peer_id="-1001", message_id=41, timestamp="2026-08-20T08:00:00Z")
        suppress = AsyncMock(return_value=1)
        with (
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner.rooms, "effective_mode", return_value="normal"),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.telegram_followups, "LEDGER", ledger),
            patch.object(runner, "_supersede_pending_followup_deliveries", suppress),
        ):
            await runner.on_deleted(_DeletedEvent([41]))

        current = ledger.get(item["id"])
        self.assertEqual(current["status"], "response_deleted")
        self.assertNotIn("STALE ANSWER", ledger.context())
        suppress.assert_awaited_once_with(item["id"], reason="response_deleted")

    async def test_deletion_without_peer_or_from_blocked_room_is_not_guessed(self):
        archive = Mock(return_value=True)
        push = Mock()
        with (
            patch.object(runner.rooms, "is_allowed", return_value=False),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.group_context, "observe_deletion", archive),
            patch.object(runner, "_buf_push", push),
        ):
            await runner.on_deleted(_DeletedEvent([41], chat_id=None))
            await runner.on_deleted(_DeletedEvent([41]))
        archive.assert_not_called()
        push.assert_not_called()


class TestReflectiveIncoming(unittest.IsolatedAsyncioTestCase):
    async def test_ambient_batches_but_address_wins_and_archive_sees_all(self):
        buffers = defaultdict(lambda: deque(maxlen=600))
        wakes = {}
        arms = Mock()
        archive = Mock(return_value=True)

        def push(chat_id, line, **_kwargs):
            buffers[chat_id].append(line)

        common = (
            patch.object(runner.rooms, "is_frozen", return_value=False),
            patch.object(runner.rooms, "is_allowed", return_value=True),
            patch.object(runner.rooms, "effective_mode", return_value="normal"),
            # С 22.08 тема — ключ хранения только по знанию: форум + каталог,
            # каталог приезжает preflight'ом до фиксации маршрута.
            patch.object(runner, "_known_forum", lambda *a, **k: True),
            patch.object(runner, "_catalog_for_routing",
                         AsyncMock(return_value=frozenset({77}))),
            patch.object(runner.rooms, "room_policy", return_value={
                "engagement": "reflective", "context_hot": 0,
                "context_summary_chars": 7000, "cross_topics": "off",
                "backfill_limit": 0,
            }),
            patch.object(runner.social, "category", return_value="known"),
            patch.object(runner.telegram_contacts, "observe"),
            patch.object(runner.telegram_followups.LEDGER, "observe_incoming", return_value=None),
            patch.object(runner, "_chat_descriptor", AsyncMock(return_value={
                "title": "Mycelium", "size": 100,
            })),
            patch.object(runner, "_capture_typed_media", AsyncMock(return_value=(None, ""))),
            patch.object(runner, "_group_archive_enabled", return_value=True),
            patch.object(runner.group_context, "topic_title", return_value="Ideas"),
            patch.object(runner.group_context, "observe_message", archive),
            patch.object(runner, "_buf_push", side_effect=push),
            patch.object(runner, "_buf", buffers),
            patch.object(runner, "_pending_media", defaultdict(lambda: deque(maxlen=16))),
            patch.object(runner, "_seen_ids", defaultdict(lambda: deque(maxlen=50))),
            patch.object(runner, "_recent_msgs", defaultdict(lambda: deque(maxlen=12))),
            patch.object(runner, "_recent_senders", defaultdict(lambda: deque(maxlen=40))),
            patch.object(runner, "_meta", {}),
            patch.object(runner, "_group_wakes", wakes),
            patch.object(runner, "_entity_cache", {}),
            patch.object(runner, "_arm", arms),
        )
        with contextlib.ExitStack() as stack:
            for item in common:
                stack.enter_context(item)
            await runner.on_new(_LiveEvent(_LiveMessage(1, "a meaningful ambient thought")))
            key = "-1001__topic__77"
            self.assertFalse(wakes[key].addressed)
            await runner.on_new(_LiveEvent(_LiveMessage(
                2, "@praxis please consider this", mentioned=True)))
            self.assertTrue(wakes[key].addressed)
            addressed = wakes[key]
            await runner.on_new(_LiveEvent(_LiveMessage(3, "another ambient thought")))

        self.assertIs(wakes[key], addressed)
        self.assertEqual(archive.call_count, 3)
        self.assertEqual(arms.call_count, 2)


class TestRootProfilePrompt(unittest.TestCase):
    def test_topic_summary_stays_local_but_room_profile_comes_from_root(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            room_dir = base / "rooms"
            soul_dir = base / "soul"
            room_dir.mkdir()
            soul_dir.mkdir()
            (room_dir / "-1001.md").write_text(
                "# Root\n\nmode: normal\ndisclosure: standard\n\n## Norms\nROOT PROFILE\n",
                encoding="utf-8",
            )
            (room_dir / "-1001__topic__77.md").write_text(
                "# Wrong\n\nTOPIC PROFILE MUST NOT LOAD\n", encoding="utf-8")
            ctx = agent.ChannelContext(
                chat_id="-1001__topic__77", room_id="-1001", principal_id=10,
                is_dm=False, known=True, addressed=True,
            )
            with (
                patch.object(agent, "ROOMS_DIR", room_dir),
                patch.object(agent, "SOUL_DIR", soul_dir),
                patch.object(agent, "INDEX_MD", base / "none.md"),
                patch.object(agent, "_persona_text", return_value=""),
                patch.object(agent, "_active_desires_block", return_value=""),
                patch.object(agent, "_participant_memory_block", return_value=""),
                patch.object(agent, "_recall_block", return_value=""),
                patch.object(agent, "recent_journal", return_value=""),
                patch.object(agent, "read_summary", return_value="TOPIC SUMMARY"),
            ):
                _static, dynamic, evidence = agent._build_prompt_parts(
                    speaker="Alice", query="hello", ctx=ctx)
            self.assertNotIn("ROOT PROFILE", dynamic)
            self.assertIn("ROOT PROFILE", evidence)
            self.assertIn("TOPIC SUMMARY", evidence)
            self.assertNotIn("TOPIC PROFILE MUST NOT LOAD", evidence)


class _HistoryClient:
    def __init__(self, messages):
        self.messages = messages

    async def iter_messages(self, entity, limit):
        for item in self.messages[:limit]:
            yield item


class TestBoundedBackfill(TempGroupMemory, unittest.IsolatedAsyncioTestCase):
    async def test_resume_deduplicates_without_a_voice_turn(self):
        header = types.SimpleNamespace(
            reply_to_top_id=77, reply_to_msg_id=77, forum_topic=True)
        now = datetime.datetime.now(datetime.timezone.utc)
        messages = [
            types.SimpleNamespace(
                id=2, message="newer", reply_to=header,
                reply_to_msg_id=77, sender_id=10, sender=types.SimpleNamespace(
                    first_name="Alice", last_name="", username=None, usernames=()),
                date=now, out=False, photo=None, voice=None, video_note=None,
                sticker=None, gif=None, video=None, audio=None, document=None, media=None,
            ),
            types.SimpleNamespace(
                id=1, message="older", reply_to=header,
                reply_to_msg_id=77, sender_id=20, sender=types.SimpleNamespace(
                    first_name="Bob", last_name="", username=None, usernames=()),
                date=now, out=False, photo=None, voice=None, video_note=None,
                sticker=None, gif=None, video=None, audio=None, document=None, media=None,
            ),
        ]
        fake = _HistoryClient(messages)
        with (
            patch.object(runner, "client", fake),
            patch.object(runner, "_forum_topic_catalog",
                         AsyncMock(return_value=([], True))),
            patch.object(agent, "voice_turn_envelope", Mock()) as voice,
        ):
            first = await runner._backfill_group_context("-1001", object(), limit=10)
            second = await runner._backfill_group_context("-1001", object(), limit=10)
        self.assertEqual(first["added"], 2)
        self.assertEqual(second["added"], 0)
        self.assertEqual(group_context.archived_message_count("-1001"), 2)
        voice.assert_not_called()


class TheSeamOfTheFeedMustNotMove(TempGroupMemory):
    """27.08. Пометка обреза стояла в ГОЛОВЕ ленты вместе с живыми счётчиками.

    Три подряд идущих хода в Грибнице: «выше ещё 1736» → «1740» → «1750», показано
    200 → 193 → 188. Голова ленты — начало префикса, который провайдер кэширует;
    сдвинулась она, и весь кадр за ней оплачивается заново. Замер по 143 парам
    соседних ходов: общий префикс ленты в комнатах был РОВНО НОЛЬ сообщений, без этой
    одной строки становится 6–10 — то есть 20–32 тысячи знаков на ход. В личке пометки
    нет, там префикс и так целый (48 сообщений), поэтому болезнь жила только в комнатах.

    Факт обреза остаётся наверху: ради него пометка и заведена, срез без неё читается
    как «вот вся ветка». Вниз уезжают только числа и совет.
    """

    ROOM = "-1002222"

    def _room(self):
        self.add(self.ROOM, None, 7, "тема ветки")
        for mid in range(20, 60):
            self.add(self.ROOM, 7, mid, "реплика " + "д" * 400, reply=7)

    def _rows(self):
        return group_context.context_rows(self.ROOM, topic_id=7, limit=200,
                                          max_chars=4000)

    @staticmethod
    def _is_service(line: str) -> bool:
        """Служебная строка ленты — по её же разметке `…[…]`, а не по содержимому."""
        return line.lstrip().startswith("…[")

    def _head(self, rows):
        """Голова ленты — всё до первой строки ЖИВОГО разговора.

        Корень ветки (`[root; …]`) в голове остаётся: он рендерится как сообщение, но
        стоит вне хронологии и между ходами не меняется. Разговор начинается с первой
        строки `[topic …]` — по ней и режем.
        """
        head = []
        for row in rows:
            if row["line"].startswith("[topic"):
                break
            head.append(row["line"])
        return head

    # ---- то, ради чего правка

    def test_the_head_of_the_feed_is_byte_identical_across_turns(self):
        """Главный пин: лента выросла, а её служебная голова не шевельнулась."""
        self._room()
        before = self._head(self._rows())
        for mid in range(60, 66):
            self.add(self.ROOM, 7, mid, "новая реплика " + "е" * 400, reply=7)
        after = self._head(self._rows())
        self.assertTrue(before, "головы ленты нет — тест ничего не проверяет")
        self.assertEqual(before, after,
                         "голова ленты сдвинулась — префикс кэша разорван")

    def test_no_live_counter_stands_before_the_conversation(self):
        """Никакого счётчика до первой живой строки: он и есть то, что двигалось."""
        self._room()
        for line in self._head(self._rows()):
            self.assertNotIn("показано", line)
            self.assertNotIn("выше ещё", line)

    def test_the_feed_prefix_survives_new_messages(self):
        """Следствие в том виде, в каком его считает провайдер: общий префикс ленты
        перестал быть нулевым."""
        self._room()
        before = [row["line"] for row in self._rows()]
        for mid in range(60, 64):
            self.add(self.ROOM, 7, mid, "новая реплика " + "е" * 400, reply=7)
        after = [row["line"] for row in self._rows()]
        common = 0
        for a, b in zip(before, after):
            if a != b:
                break
            common += 1
        self.assertGreater(common, 1,
                           "общий префикс ленты по-прежнему рвётся в голове")

    # ---- ничего не потеряно

    def test_the_cut_is_still_named_at_the_seam(self):
        self._room()
        head = self._head(self._rows())
        self.assertTrue(any("ЛЕНТА ОБРЕЗАНА" in line for line in head),
                        "срез без пометки читается как «вот вся ветка»")

    def test_the_exact_numbers_moved_but_did_not_vanish(self):
        self._room()
        rows = self._rows()
        tail = rows[-1]["line"]
        self.assertIn("показано", tail, "маркер обязан называть, сколько показано")
        self.assertIn("выше ещё", tail)
        self.assertIn("context_summary_chars", tail, "совет уехал вместе с числами")
        whole = "\n".join(row["line"] for row in rows)
        for kept in ("ЛЕНТА ОБРЕЗАНА", "показано", "выше ещё", "бюджет символов",
                     "manage_room", "group_context"):
            self.assertIn(kept, whole, f"из пометки пропало: {kept}")

    def test_the_numbers_are_exact_not_rounded(self):
        """Округлённый счётчик врал бы тихо — этого не делаем."""
        import re as _re
        self._room()
        rows = self._rows()
        shown = sum(1 for row in rows if not self._is_service(row["line"]))
        found = _re.search(r"показано (\d+) сообщений", rows[-1]["line"])
        self.assertIsNotNone(found, "числа в хвосте не нашлись")
        self.assertEqual(int(found.group(1)), shown,
                         "счётчик разошёлся с тем, что реально в ленте")

    # ---- границы

    def test_both_service_lines_are_setting_not_speech(self):
        """`self=False`: это строки обстановки, приписать их ей нельзя."""
        self._room()
        rows = self._rows()
        service = [row for row in rows if self._is_service(row["line"])]
        self.assertTrue(service)
        for row in service:
            self.assertFalse(row["self"], row["line"][:60])
            self.assertEqual(row["line"], row["role_line"])
            self.assertTrue(row.get("service"), row["line"][:60])

    def test_service_rows_stay_out_of_role_dialogue(self):
        """Footer numbers are context, never the current user turn after Praxis spoke."""
        self._room()
        group_context.observe_message(
            peer_id=self.ROOM, topic_id=7, message_id=60, sender_id=5,
            sender_name="Praxis", reply_to_message_id=7,
            timestamp="2026-07-14T13:00:00Z", text="мой последний ответ",
            topic_title="", outgoing=True)
        rows = group_context.context_rows(self.ROOM, topic_id=7, limit=50,
                                          max_chars=4000)
        turns = tuple((bool(row["self"]), str(row["line"]), str(row["role_line"]))
                      for row in rows if not row.get("service"))
        self.assertTrue(all("ОБРЕЗ ЛЕНТЫ" not in row[1] for row in turns))
        self.assertTrue(all("ЛЕНТА ОБРЕЗАНА" not in row[1] for row in turns))
        history, current = runner._group_dialogue(turns)
        self.assertEqual(history, [])
        self.assertEqual(current, "", "footer must not manufacture a user turn")

    def test_the_cut_and_the_topic_still_reach_the_model_inside_turns(self):
        """Адверсарка 28.08: c82f38c8 вырезал из ролевого пути ВСЕ службы разом —
        модель не видела ни «лента обрезана», ни темы ветки, отвечала на срез как
        на целую ветку. Службы обязаны доехать — внутри соседних реплик."""
        self._room()
        rows = self._rows()
        folded = runner._fold_service_rows(rows)
        real = [r for r in rows if not r.get("service")]
        self.assertEqual(len(folded), len(real),
                         "службы стали отдельными ходами — граница ролей снова дырявая")
        self.assertTrue(any(r.get("service") for r in rows),
                        "фикстура без служб ничего не проверяет")
        first_role = folded[0][2]
        self.assertIn("ЛЕНТА ОБРЕЗАНА", first_role,
                      "пометка обреза не доехала до модели")
        self.assertIn("[root;", first_role, "тема ветки не доехала до модели")
        self.assertLess(first_role.index("[root;"), first_role.index("ЛЕНТА ОБРЕЗАНА"),
                        "корень ветки обязан стоять раньше пометки, как в ленте")
        self.assertIn("ОБРЕЗ ЛЕНТЫ, точные числа", folded[-1][2],
                      "точные числа не доехали до модели")
        for _is_self, line, _role in folded:
            self.assertFalse(self._is_service(line),
                             "служебный текст пролез в line — расписки и дифф съедут")

    def test_a_feed_of_only_service_rows_makes_no_turns(self):
        """Граница ролей дословно: службы без реплик — ноль ходов, не «реплика человека»."""
        service_only = [{"self": False, "line": "…[ЛЕНТА ОБРЕЗАНА: …]",
                         "role_line": "…[ЛЕНТА ОБРЕЗАНА: …]", "service": True}]
        self.assertEqual(runner._fold_service_rows(service_only), ())

    def test_the_living_feed_is_untouched_between_the_two_lines(self):
        """Названная цена: числа приезжают ПОСЛЕ ленты. Сама лента цела."""
        self._room()
        rows = self._rows()
        self.assertIn("ОБРЕЗ ЛЕНТЫ", rows[-1]["line"])
        self.assertTrue(any(row["line"].startswith("[topic") for row in rows[:-1]),
                        "живые реплики пропали из ленты")


if __name__ == "__main__":
    unittest.main()


class TestTruncationPromisesAreReal(TempGroupMemory):
    """Пометка обреза обещает достать сообщение целиком — обещание обязано работать.

    Иначе молчаливая ложь просто меняется на громкую: она следует совету и получает
    ровно тот же обрезок.
    """

    def test_marker_names_a_retrieval_that_actually_returns_the_full_body(self):
        long_body = "ПЕРВЫЙ вердикт " + "г" * 2600 + " ВТОРОЙ вердикт"
        self.add("-1001", 7, 10, long_body, reply=7)

        clipped = group_context.context("-1001", topic_id=7, limit=50, max_chars=1500)
        self.assertIn("ОБРЕЗАНО", clipped)
        self.assertIn('action="message"', clipped)
        self.assertIn("limit=10", clipped)          # маркер называет message_id

        full = group_context.describe("-1001", action="message", limit=10)
        self.assertIn("ПЕРВЫЙ вердикт", full)
        self.assertIn("ВТОРОЙ вердикт", full)       # то, ради чего совет и даётся
        self.assertNotIn("ОБРЕЗАНО", full)

    def test_message_action_is_honest_about_a_missing_id(self):
        self.assertIn("not in this group", group_context.describe(
            "-1001", action="message", limit=999))

    def test_feed_level_cut_is_marked_not_silent(self):
        """Обрез ленты — та же болезнь этажом выше: срез без пометки читается как
        «вот вся ветка», и она отвечает, не зная, что выше есть ещё."""

        self.add("-1001", None, 7, "тема ветки")
        for mid in range(20, 60):
            self.add("-1001", 7, mid, "реплика " + "д" * 400, reply=7)

        seen = group_context.context("-1001", topic_id=7, limit=200, max_chars=4000)
        self.assertIn("ЛЕНТА ОБРЕЗАНА", seen)
        self.assertIn("показано", seen, "маркер называет, сколько показано")
        self.assertIn("тема ветки", seen)           # тема всё равно закреплена
        self.assertLessEqual(len(seen), 4600)

    def test_wide_cap_cannot_eat_the_whole_budget(self):
        """Четыре длинных свежих реплики не имеют права выбить остальную ветку —
        включая прямой вопрос владельца."""

        self.add("-1001", None, 7, "тема ветки")
        self.add("-1001", 7, 8, "ВОПРОС ВЛАДЕЛЬЦА про gh", reply=7)
        for mid in range(20, 25):
            self.add("-1001", 7, mid, "простыня " + "е" * 3500, reply=7, sender=30,
                     name="Talker")

        seen = group_context.context("-1001", topic_id=7, limit=200, max_chars=14000)
        self.assertIn("тема ветки", seen)
        self.assertIn("ВОПРОС ВЛАДЕЛЬЦА", seen)
