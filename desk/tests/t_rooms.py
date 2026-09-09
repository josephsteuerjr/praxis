# -*- coding: utf-8 -*-
"""Стенд комнат окна (задача A §3).

Запуск:  python tests/t_rooms.py

Три части.
  1. Реестр (`deskd/rooms.py`): создать / переименовать / удалить; `window`
     не удаляется; архив удалённой уезжает в `memory/groups/archive/` и не
     смешивается с новой комнатой; список чатов канала видит комнату сразу,
     с `kind: window` и именем; комната по умолчанию зовётся именем агента.
  2. Канал: `POST /api/rooms`, `POST /api/rooms/{peer}`, `DELETE
     /api/rooms/{peer}` — через диспетчер канала, с отказами словами; `/api/say`
     принимает адрес `window-<hex>` и кладёт адресную записку.
  3. Харнесс: записка в новую комнату ведёт ход В НЕЙ (дерево — заглушка),
     ответ ложится в её архив, а архив комнаты по умолчанию не трогается;
     хуки транспорта (`reply`, `read_chat`, `search_chats`) знают обе комнаты.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "localharness"))

from deskd import readers, rooms  # noqa: E402
import deskapp  # noqa: E402
import runner  # noqa: E402
import transport  # noqa: E402


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        (self.tree / "memory" / "groups").mkdir(parents=True)
        self.saved_tree = readers.tree
        readers.tree = lambda: self.tree
        self.saved_cfg = readers.product_config
        readers.product_config = lambda: {"agent_name": "Мира"}

    def tearDown(self):
        readers.tree = self.saved_tree
        readers.product_config = self.saved_cfg
        self.tmp.cleanup()

    def test_create_rename_delete(self):
        made = rooms.create(self.tree, "  Проект   Хелен ")
        key = made["peer_id"]
        self.assertTrue(rooms.ROOM_PATTERN.match(key), key)
        self.assertEqual(made["title"], "Проект Хелен")
        self.assertTrue(rooms.archive_path(self.tree, key).exists(), "пустой архив заведён")
        # Список чатов видит комнату сразу, без единого сообщения.
        listed = {r["peer_id"]: r for r in readers.chats()}
        self.assertIn(key, listed)
        self.assertEqual(listed[key]["kind"], "window")
        self.assertEqual(listed[key]["title"], "Проект Хелен")
        self.assertEqual(listed[key]["messages"], 0)
        rooms.rename(self.tree, key, "Новое имя")
        self.assertEqual(rooms.title(self.tree, key), "Новое имя")
        self.assertEqual(readers._title_for(key, {}), "Новое имя")
        gone = rooms.delete(self.tree, key)
        self.assertTrue(gone["archived"].startswith("memory/groups/archive/" + key))
        self.assertTrue((self.tree / gone["archived"]).exists())
        self.assertFalse(rooms.archive_path(self.tree, key).exists())
        self.assertNotIn(key, {r["peer_id"] for r in readers.chats()})
        with self.assertRaises(rooms.RoomError) as caught:
            rooms.rename(self.tree, key, "x")
        self.assertEqual(caught.exception.status, 404)

    def test_default_room_is_named_after_the_agent_and_stays(self):
        self.assertEqual(rooms.title(self.tree, "window", "Мира"), "Мира")
        self.assertEqual(readers._title_for("window", {}), "Мира")
        with self.assertRaises(rooms.RoomError) as caught:
            rooms.delete(self.tree, "window")
        self.assertEqual(caught.exception.status, 409)
        rooms.rename(self.tree, "window", "Главная")
        self.assertEqual(rooms.title(self.tree, "window", "Мира"), "Главная")
        self.assertEqual(rooms.rows(self.tree, "Мира")[0]["peer_id"], "window")

    def test_archives_do_not_mix(self):
        a = rooms.create(self.tree, "A")["peer_id"]
        rooms.archive_path(self.tree, a).write_text('{"text":"из A"}\n', encoding="utf-8")
        rooms.delete(self.tree, a)
        b = rooms.create(self.tree, "B")["peer_id"]
        self.assertNotEqual(a, b)
        self.assertEqual(rooms.archive_path(self.tree, b).read_text(encoding="utf-8"), "")
        archived = list((self.tree / "memory" / "groups" / "archive").glob(a + "-*.jsonl"))
        self.assertEqual(len(archived), 1)
        self.assertIn("из A", archived[0].read_text(encoding="utf-8"))

    def test_bad_input(self):
        with self.assertRaises(rooms.RoomError):
            rooms.create(self.tree, "   ")
        self.assertFalse(rooms.is_room("-100123"))
        self.assertFalse(rooms.is_room("window-xyz"))
        self.assertFalse(rooms.is_room("window-0123456789"))
        self.assertTrue(rooms.is_room("window-0123abcd"))
        self.assertTrue(rooms.is_room("window"))


class Channel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        (self.tree / "memory" / ".control" / "desk_inbox").mkdir(parents=True)
        self.saved_tree = readers.tree
        readers.tree = lambda: self.tree
        self.saved_status = readers.reader_status
        readers.reader_status = lambda: {"alive": True}

    def tearDown(self):
        readers.tree = self.saved_tree
        readers.reader_status = self.saved_status
        self.tmp.cleanup()

    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_rooms_over_the_channel(self):
        made = self._run(deskapp._tunnel_dispatch("POST", "/api/rooms", {"title": "Сад"}, local=True))
        self.assertEqual(made["status"], 200, made)
        key = made["body"]["peer_id"]
        renamed = self._run(deskapp._tunnel_dispatch("POST", f"/api/rooms/{key}", {"title": "Огород"}))
        self.assertEqual(renamed["body"]["title"], "Огород")
        refused = self._run(deskapp._tunnel_dispatch("DELETE", "/api/rooms/window", None))
        self.assertEqual((refused["status"], refused["code"]), (409, "room"))
        self.assertIn("нельзя", refused["error"])
        gone = self._run(deskapp._tunnel_dispatch("DELETE", f"/api/rooms/{key}", None))
        self.assertEqual(gone["status"], 200)
        missing = self._run(deskapp._tunnel_dispatch("DELETE", f"/api/rooms/{key}", None))
        self.assertEqual(missing["status"], 404)
        empty = self._run(deskapp._tunnel_dispatch("POST", "/api/rooms", {"title": ""}))
        self.assertEqual(empty["status"], 400)

    def test_say_targets_a_window_room(self):
        key = rooms.create(self.tree, "Сад")["peer_id"]
        reply = self._run(deskapp._tunnel_dispatch("POST", "/api/say",
                                                   {"text": "привет", "chat": key}))
        self.assertEqual(reply["status"], 200, reply)
        self.assertEqual(reply["body"]["chat"], key)
        notes = list((self.tree / "memory" / ".control" / "desk_inbox").glob("*.md"))
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].stem.endswith("__to__" + key), notes[0].name)
        bad = self._run(deskapp._tunnel_dispatch("POST", "/api/say",
                                                 {"text": "x", "chat": "window-zz"}))
        self.assertEqual(bad["status"], 400)


class _Envelope:
    def __init__(self, run_id, text=""):
        self.run_id, self.text, self.outbound = run_id, text, []
        self.deferred = self.failed = False


class _Ctx:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Refusal(str):
    """Как `agent.DirectSendRefusal`: строка особого типа — «не ушло»."""


class _Agent:
    """Дерево-заглушка: отвечает рукой reply в ТУ комнату, в которой идёт ход."""
    ChannelContext = _Ctx
    DirectSendRefusal = _Refusal

    def __init__(self, tree: Path):
        self.tree = tree
        self._TELETHON: dict = {}
        self.seen: list[tuple[str, str]] = []
        import contextvars
        self._TURN_CHANNEL = contextvars.ContextVar("turn_channel", default=None)

    def voice_turn_envelope(self, chat_id, convo, speaker, *, ctx, history, current_text, orient):
        token = self._TURN_CHANNEL.set(ctx)
        try:
            self.seen.append((chat_id, ctx.title))
            self._TELETHON["reply"](chat_id, f"ответ в {ctx.title}")
            path = self.tree / "memory" / ".state" / "turns.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as sink:
                sink.write(json.dumps({"chat_id": chat_id, "run_id": "run-" + chat_id,
                                       "held": "", "title": ctx.title}) + "\n")
        finally:
            self._TURN_CHANNEL.reset(token)
        return _Envelope("run-" + chat_id)

    def run_delivery_started(self, *a, **k): pass
    def run_delivery_text_accepted(self, *a, **k): pass
    def run_delivery_finalize_recovered(self, *a, **k): pass
    def run_delivery_completed(self, *a, **k): pass


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        (self.tree / "memory" / "groups").mkdir(parents=True)
        self.agent = _Agent(self.tree)
        self.desks = transport.Desks(self.tree, "Егор", "Hélène", memory_life=None,
                                     agent_name="Мира")
        transport.install(self.agent, self.desks)
        self.saved = {k: getattr(runner, k) for k in
                      ("_tree", "_desks", "_desk", "_agent", "_life", "_agent_name",
                       "_deliver_unspoken", "_bot", "_speaker")}
        runner._tree, runner._desks, runner._desk = self.tree, self.desks, self.desks.default
        runner._agent, runner._life, runner._bot = self.agent, None, None
        runner._agent_name, runner._deliver_unspoken, runner._speaker = "Мира", True, "Егор"

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(runner, k, v)
        self.tmp.cleanup()

    def test_note_to_a_new_room_runs_the_turn_there(self):
        key = rooms.create(self.tree, "Сад")["peer_id"]
        runner.handle_desk("что с яблонями?", room=key)
        self.assertEqual(self.agent.seen, [(key, "Сад")])
        garden = self.desks.get(key).rows()
        self.assertEqual([r["text"] for r in garden], ["что с яблонями?", "ответ в Сад"])
        self.assertEqual(self.desks.default.rows(), [], "комната по умолчанию не тронута")
        # Контекст хода — свой у комнаты: реплика из окна по умолчанию идёт отдельно.
        runner.handle_desk("привет", room="window")
        self.assertEqual(self.agent.seen[-1], ("window", "Мира"))
        self.assertEqual([r["text"] for r in self.desks.default.rows()], ["привет", "ответ в Мира"])
        self.assertEqual(len(self.desks.get(key).rows()), 2)

    def test_hooks_know_every_room(self):
        key = rooms.create(self.tree, "Сад")["peer_id"]
        hooks = self.agent._TELETHON
        # Без хода (нет TURN_CHANNEL) reply по ключу другой комнаты доставляет туда.
        receipt = hooks["reply"](key, "слово в сад")
        self.assertIn("Отправлено", receipt)
        self.assertEqual(self.desks.get(key).rows()[-1]["text"], "слово в сад")
        # Telegram-адрес — честный отказ особого типа.
        refusal = hooks["reply"]("-100123", "x")
        self.assertIn("не отправила", str(refusal))
        self.assertIn("Сад", hooks["search_chats"]("сад"))
        self.assertIn("слово в сад", hooks["read_chat"]("Сад"))
        self.assertIn("слово в сад", hooks["fetch_context"](key))
        self.assertEqual(hooks["get_id"]("Сад"), key)
        self.assertIn("Мира", hooks["search_chats"]("мира"))

    def test_inbox_routes_by_suffix(self):
        key = rooms.create(self.tree, "Сад")["peer_id"]
        self.assertEqual(runner._inbox_target(f"20260907T000000000000Z__to__{key}"), key)
        self.assertEqual(runner._inbox_target("20260907T000000000000Z"), "window")
        self.assertEqual(runner._inbox_target("20260907T000000000000Z__to__window"), "window")
        self.assertEqual(runner._inbox_target("20260907T000000000000Z__to__-100123"), "-100123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
