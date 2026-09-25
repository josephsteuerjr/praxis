# -*- coding: utf-8 -*-
"""Уведомления в кадре (25.09, поток G): накопитель, блок, гашение ходом чата, слова владельца.

Инварианты (страница `УВЕДОМЛЕНИЯ-В-КАДРЕ-25.09.md`, одобрена Егором 25.09):
  * событие другой комнаты ложится в накопитель; своя комната блок не получает;
  * первый показ — «НОВОЕ», дальше — «прочитано», строка остаётся;
  * ход исходного чата снимает его комнатные записи; слова владельца при этом живут;
  * потолок строк и «…и ещё N»; дедуп по (chat, message);
  * приватность fail-closed: содержимое личек — только владельческой аудитории;
  * слова владельца — только окнам/будильникам, один раз на окно; пометка decision держится;
  * посреди хода (mid_turn) — только то, чего этот run не видел;
  * без run блок не строится; PRAXIS_NOTICES=off выключает всё;
  * реестр фоновых процессов: обёртка `&`, маркер pid, чистка мёртвых.

Запуск:  python praxis_test.py test_core_notices_2509 -v
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import notices as core_notices
from core import processes as core_processes


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="core_notices_"))
        self._orig = (core_notices.STATE_FILE, core_processes.STATE_FILE)
        core_notices.STATE_FILE = self.tmp / "notices.json"
        core_processes.STATE_FILE = self.tmp / "processes.json"
        self.env = mock.patch.dict(os.environ, {"PRAXIS_NOTICES": "on"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        core_notices.STATE_FILE, core_processes.STATE_FILE = self._orig

    @staticmethod
    def block(run="run-A", kind="chat", chat="-100A", owner=False, mid=False, now=None):
        return core_notices.block_for_input(run_id=run, run_kind=kind, chat_id=chat,
                                            owner_context=owner, mid_turn=mid, now=now)


class RoomEvents(Base):
    def test_other_room_event_shows_new_then_read_and_stays(self):
        core_notices.note_incoming(kind="mention", chat_id="-100B", chat_title="абстракт",
                                   who="Hope", message_id=7, gist="а Praxis считает…",
                                   private=False, ts=time.time() - 60)
        first = self.block()
        self.assertIn("НОВОЕ", first)
        self.assertIn("абстракт · Hope — упоминание", first)
        self.assertIn("«а Praxis считает…»", first)
        second = self.block()
        self.assertIn("прочитано", second, "строка остаётся, но уже не как новое")
        self.assertNotIn("НОВОЕ", second)

    def test_own_room_gets_no_block(self):
        core_notices.note_incoming(kind="reply", chat_id="-100A", chat_title="доска",
                                   who="torvn77", message_id=1, gist="x", private=False)
        self.assertEqual(self.block(chat="-100A"), "")

    def test_chat_turn_start_clears_that_room_only(self):
        core_notices.note_incoming(kind="mention", chat_id="-100B", chat_title="B", who="a",
                                   message_id=1, gist="x", private=False)
        core_notices.note_incoming(kind="reply", chat_id="-100C", chat_title="C", who="b",
                                   message_id=2, gist="y", private=False)
        self.assertEqual(core_notices.clear_chat("-100B"), 1)
        text = self.block()
        self.assertNotIn("B · a", text)
        self.assertIn("C · b", text)

    def test_dedup_by_chat_and_message(self):
        for _ in range(3):
            core_notices.note_incoming(kind="mention", chat_id="-100B", chat_title="B",
                                       who="a", message_id=5, gist="x", private=False)
        self.assertEqual(len(core_notices.pending()), 1)

    def test_row_cap_and_spread(self):
        for i in range(9):
            core_notices.note_incoming(kind="mention", chat_id=f"-100B{i % 2}",
                                       chat_title=f"комната{i % 2}", who=f"u{i}",
                                       message_id=i, gist="x", private=False,
                                       ts=time.time() - 100 + i)
        text = self.block()
        self.assertEqual(text.count("• "), core_notices.ROWS)
        self.assertIn("…и ещё 3 (", text)
        self.assertIn("в комната", text)

    def test_private_content_hidden_outside_owner_context(self):
        core_notices.note_incoming(kind="dm", chat_id="777", chat_title="Егор", who="Егор",
                                   message_id=3, gist="ну их, не поднимаю", private=True)
        public = self.block(owner=False)
        self.assertIn("ЛС · Егор — новое сообщение", public)
        self.assertNotIn("не поднимаю", public, "содержимое личек — не в публичную комнату")
        owner = self.block(run="run-O", owner=True)
        self.assertIn("не поднимаю", owner)

    def test_node_result_is_a_notice(self):
        core_notices.note_node(task_id="t1", unit_id="agent-7c1d", status="done", goal="LRX")
        core_notices.note_node(task_id="t1", unit_id="agent-7c1d", status="done", goal="LRX")
        self.assertEqual(len(core_notices.pending()), 1)
        self.assertIn("узел agent-7c1d задачи t1 закончил: done", self.block())

    def test_ttl_and_cap(self):
        now = time.time()
        core_notices.note_incoming(kind="mention", chat_id="-1", chat_title="B", who="a",
                                   message_id=1, gist="old", private=False,
                                   ts=now - core_notices.TTL_SEC - 10)
        core_notices.note_incoming(kind="mention", chat_id="-1", chat_title="B", who="a",
                                   message_id=2, gist="new", private=False, ts=now)
        gists = [r["gist"] for r in core_notices.pending(now)]
        self.assertEqual(gists, ["new"])

    def test_switch_off(self):
        with mock.patch.dict(os.environ, {"PRAXIS_NOTICES": "off"}):
            self.assertIsNone(core_notices.note_incoming(
                kind="mention", chat_id="-1", chat_title="B", who="a", message_id=1,
                gist="x", private=False))
            self.assertEqual(self.block(), "")

    def test_no_run_no_block(self):
        core_notices.note_incoming(kind="mention", chat_id="-1", chat_title="B", who="a",
                                   message_id=1, gist="x", private=False)
        self.assertEqual(core_notices.block_for_input(run_id="", run_kind="chat",
                                                      chat_id="-2", owner_context=True), "")


class OwnerWords(Base):
    def test_owner_dm_reaches_windows_once_and_not_chat_turns(self):
        core_notices.note_incoming(kind="dm", chat_id="777", chat_title="Егор", who="Егор",
                                   message_id=9, gist="ну их, mr_ifub больше не поднимаю",
                                   private=True, is_owner_dm=True)
        kinds = {r["kind"] for r in core_notices.pending()}
        self.assertEqual(kinds, {"dm", "owner_line"})
        window = self.block(run="run-W1", kind="task_window", chat="777", owner=True)
        self.assertIn("владелец в ЛС", window)
        self.assertIn("не поднимаю", window)
        self.assertEqual(self.block(run="run-W1", kind="task_window", chat="777", owner=True),
                         "", "окно видело один раз — из этого окна строка ушла")
        other = self.block(run="run-W2", kind="task_window", chat="777", owner=True)
        self.assertIn("не поднимаю", other, "другое живое окно видит свою копию")
        chat = self.block(run="run-C", kind="chat", chat="-100B", owner=False)
        self.assertIn("ЛС · Егор — новое сообщение", chat, "ход чата видит событие комнаты…")
        self.assertNotIn("владелец в ЛС", chat, "…но не строку для окон")

    def test_owner_line_content_hidden_when_window_is_not_owner_audience(self):
        core_notices.note_owner_line("секретное", chat_id="777", message_id=1)
        text = self.block(run="run-W", kind="task_window", chat="-100B", owner=False)
        self.assertIn("владелец написал в ЛС", text)
        self.assertNotIn("секретное", text)

    def test_owner_line_survives_owner_chat_turn(self):
        core_notices.note_incoming(kind="dm", chat_id="777", chat_title="Егор", who="Егор",
                                   message_id=9, gist="решили", private=True, is_owner_dm=True)
        core_notices.clear_chat("777")
        self.assertEqual([r["kind"] for r in core_notices.pending()], ["owner_line"])

    def test_decision_is_held_in_window_until_its_end(self):
        core_notices.note_owner_decision("mr_ifub больше не поднимаю", run_id="run-DM")
        for i in range(3):
            text = self.block(run="run-W", kind="task_window", chat="777", owner=True)
            self.assertIn("решение владельца", text, f"показ {i}")
            self.assertIn("не поднимаю", text)
        self.assertIn("прочитано", text)


class MidTurn(Base):
    def test_mid_turn_shows_only_what_this_run_has_not_seen(self):
        core_notices.note_incoming(kind="mention", chat_id="-100B", chat_title="B", who="a",
                                   message_id=1, gist="first", private=False, ts=time.time() - 20)
        self.assertIn("first", self.block(run="run-A"))
        self.assertEqual(self.block(run="run-A", mid=True), "", "посреди хода нового нет")
        core_notices.note_incoming(kind="mention", chat_id="-100B", chat_title="B", who="a",
                                   message_id=2, gist="second", private=False, ts=time.time() - 10)
        mid = self.block(run="run-A", mid=True)
        self.assertIn("Пока шла работа, пришло:", mid)
        self.assertIn("second", mid)
        self.assertNotIn("first", mid)


class Processes(Base):
    def test_wrap_only_background_launches(self):
        cmd, bg = core_processes.wrap_background_launch("nohup python mr_ifub2.py > log 2>&1 &")
        self.assertTrue(bg)
        self.assertTrue(cmd.startswith("nohup python mr_ifub2.py > log 2>&1 &\n"))
        self.assertIn(core_processes.MARKER, cmd)
        for plain in ("ls -la", "a && b", "sleep 1; echo x"):
            self.assertEqual(core_processes.wrap_background_launch(plain), (plain, False))

    def test_marker_is_taken_out_and_registered(self):
        out, pid = core_processes.take_pid_marker("started\n[praxis-bg-pid] 4242\n")
        self.assertEqual(pid, 4242)
        self.assertEqual(out, "started\n")
        self.assertEqual(core_processes.take_pid_marker("plain"), ("plain", None))
        core_processes.register(4242, "nohup python mr_ifub2.py > log 2>&1 &",
                                run_id="run-20260923T150213145031Z-1d3f0464", run_kind="task_window")
        line = core_processes.state_line(alive=lambda pid: pid == 4242)
        self.assertIn("mr_ifub2.py (pid 4242, запустило task_window", line)
        self.assertIn("не в моём реестре", line)
        self.assertEqual(core_processes.state_line(alive=lambda pid: False), "")
        self.assertEqual(core_processes.live(alive=lambda pid: False), [])

    def test_display_name_skips_wrappers(self):
        self.assertEqual(core_processes.display_name("nohup /usr/bin/python3 -u x.py &"), "x.py")
        self.assertEqual(core_processes.display_name("FOO=1 setsid ./run.sh &"), "run.sh")
        self.assertEqual(core_processes.display_name("python -m http.server 8000 &"), "http.server")
        self.assertEqual(core_processes.display_name("python3 &"), "python3")


class AgentSeams(unittest.TestCase):
    """Швы agent.py: блок в evidence и в тул-цикле, пометка decision в дневнике."""

    @classmethod
    def setUpClass(cls):
        import agent  # noqa: F401 — тяжёлый импорт один раз
        cls.agent = agent

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="core_notices_agent_"))
        self._orig = core_notices.STATE_FILE
        core_notices.STATE_FILE = self.tmp / "notices.json"

    def tearDown(self):
        core_notices.STATE_FILE = self._orig

    def test_evidence_block_carries_notices_only_with_a_live_run(self):
        agent = self.agent
        core_notices.note_incoming(kind="mention", chat_id="-100B", chat_title="B", who="a",
                                   message_id=1, gist="x", private=False)
        with mock.patch.object(agent.run_context, "current_run", return_value=None):
            self.assertNotIn("notices_while_busy", agent.build_state_evidence_block())
        fake_run = mock.Mock(run_id="run-T", kind="task_window", goal="Твой час",
                             origin_chat_id="777", delivery_chat_id="777")
        with mock.patch.object(agent.run_context, "current_run", return_value=fake_run):
            text = agent.build_state_evidence_block()
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        block = next(r for r in rows if r["label"] == "notices_while_busy")
        self.assertIn("окно «Твой час»", block["content"])
        self.assertIn("B · a — упоминание", block["content"])

    def test_journal_decision_flag_lands_in_notices(self):
        agent = self.agent
        with mock.patch.object(agent, "JOURNAL_DIR", self.tmp), \
             mock.patch.object(agent, "_reindex", lambda path: None), \
             mock.patch.object(agent.run_context, "current_run", return_value=None):
            word = agent.tool_journal("решили: mr_ifub больше не поднимаю", decision=True)
            plain = agent.tool_journal("просто запись")
        self.assertIn("увидят все живые окна", word)
        self.assertEqual(plain, "Записано в дневник.")
        kinds = [r["kind"] for r in core_notices.pending()]
        self.assertEqual(kinds, ["owner_decision"])

    def test_journal_schema_declares_decision(self):
        agent = self.agent
        schema = next(t for t in agent.BASE_TOOLS if t.get("name") == "journal")
        self.assertIn("decision", schema["input_schema"]["properties"])
        self.assertIn("decision=true", schema["description"])


if __name__ == "__main__":
    unittest.main()
