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
        # адресаты реплики владельца (A10 F4): живые окна и будильники — крючки хозяина
        self._hooks = (core_notices.live_window_runs, core_notices.pending_alarm_ids)
        self.live_windows = ["run-W1", "run-W2", "run-W", "run-O"]
        self.alarms = []
        core_notices.live_window_runs = lambda: list(self.live_windows)
        core_notices.pending_alarm_ids = lambda: list(self.alarms)

    def tearDown(self):
        self.env.stop()
        core_notices.STATE_FILE, core_processes.STATE_FILE = self._orig
        core_notices.live_window_runs, core_notices.pending_alarm_ids = self._hooks

    @staticmethod
    def block(run="run-A", kind="chat", chat="-100A", owner=False, mid=False, now=None):
        return core_notices.block_for_input(run_id=run, run_kind=kind, chat_id=chat,
                                            owner_context=owner, mid_turn=mid, now=now)


class RoomEvents(Base):
    def test_other_room_event_shows_new_then_read_and_stays(self):
        core_notices.note_incoming(kind="mention", chat_id="-100B", chat_title="абстракт",
                                   who="Hope", message_id=7, gist="а Praxis считает…",
                                   private=False, ts=time.time() - 60)
        first = self.block(owner=True)
        self.assertIn("НОВОЕ", first)
        self.assertIn("абстракт · Hope — упоминание", first)
        self.assertIn("«а Praxis считает…»", first)
        second = self.block(owner=True)
        self.assertIn("прочитано", second, "строка остаётся, но уже не как новое")
        self.assertNotIn("НОВОЕ", second)
        other_run = self.block(run="run-B", owner=True)
        self.assertIn("прочитано", other_run, "НОВОЕ — по записи, не по run (A10 F6)")
        self.assertNotIn("НОВОЕ", other_run)
        public = self.block(run="run-P", owner=False)
        self.assertIn("абстракт · Hope — упоминание", public, "вне владельческой аудитории — кто и где")
        self.assertNotIn("Praxis считает", public, "…но не что (A10 F2)")

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
        self.assertIn("ЛС · Егор — написал(а) в ЛС", public)
        self.assertNotIn("не поднимаю", public, "содержимое личек — не в публичную комнату")
        owner = self.block(run="run-O", owner=True)
        self.assertIn("не поднимаю", owner)

    def test_node_result_is_a_notice(self):
        core_notices.note_node(task_id="t1", unit_id="agent-7c1d", status="done", goal="LRX")
        core_notices.note_node(task_id="t1", unit_id="agent-7c1d", status="done", goal="LRX")
        self.assertEqual(len(core_notices.pending()), 1)
        self.assertIn("узел agent-7c1d задачи t1 закончил: done", self.block(owner=True))
        self.assertIn("Forge · узел agent-7c1d закончил", self.block(run="run-P", owner=False))
        self.assertNotIn("LRX", self.block(run="run-P2", owner=False), "заказ владельца — не в чужую комнату")

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
        self.assertIn("ЛС · Егор — написал(а) в ЛС", chat, "ход чата видит событие комнаты…")
        self.assertNotIn("владелец в ЛС", chat, "…но не строку для окон")
        self.assertEqual(window.count("не поднимаю"), 1,
                         "в окне реплика владельца одна строка, не две (A10 F7)")

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
        self.assertIn("first", self.block(run="run-A", owner=True))
        self.assertEqual(self.block(run="run-A", mid=True, owner=True), "", "посреди хода нового нет")
        core_notices.note_incoming(kind="mention", chat_id="-100B", chat_title="B", who="a",
                                   message_id=2, gist="second", private=False, ts=time.time() - 10)
        mid = self.block(run="run-A", mid=True, owner=True)
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
        self.assertEqual(core_processes.display_name("cd /srv && python -u a.py > log 2>&1 &"), "a.py",
                         "cd — обёртка, не программа (A10 F10)")
        self.assertEqual(core_processes.display_name("cd /x; ./mr_ifub2 7 7 &"), "mr_ifub2")


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



class HooksAreCheapAndReal(unittest.TestCase):
    """Ревью V2 F1/F2 (25.09): живые окна — без обхода всех манифестов; будильники — из
    существующего API задач; привязка — по id задачи, а не по str(dict)."""

    def test_live_window_runs_read_only_live_manifests(self):
        import agent
        from core import notices as core_notices
        kinds = {"r1": next(iter(core_notices.WINDOW_RUN_KINDS)), "r2": "chat_turn"}

        class _Runs:
            def live_run_ids(self):
                return ["r1", "r2"]

            def manifest(self, run_id):
                return {"context": {"kind": kinds[run_id]}, "run_id": run_id}

            def _manifest_listing_row(self, run_id, manifest):
                # 26.09 (ревью W1 S9): настоящая строка листинга всегда несёт статус,
                # и мёртвые окна адресатами не становятся.
                return {"run_id": run_id, "kind": manifest["context"]["kind"],
                        "status": "running"}

            def list_runs(self, **kw):
                raise AssertionError("обход всех манифестов запрещён (V2 F1)")

        with mock.patch.object(agent, "_runs", return_value=_Runs()):
            self.assertEqual(agent._live_window_run_ids(), ["r1"])

    def test_pending_alarms_come_from_list_open_and_only_wakeable_kinds(self):
        import agent
        rows = [{"id": "a1", "kind": "wake", "status": "pending"},
                {"id": "a2", "kind": "email", "status": "pending"},
                {"id": "a3", "kind": "window", "status": "pending"},
                {"id": "", "kind": "wake", "status": "pending"}]
        with mock.patch.object(agent.tasks, "list_open", return_value=rows):
            self.assertEqual(agent._pending_alarm_ids(), ["a1", "a3"])

    def test_alarm_id_is_taken_from_the_task_dict(self):
        import agent
        self.assertEqual(agent._alarm_id_of({"id": "t-7", "kind": "wake"}), "t-7")
        self.assertEqual(agent._alarm_id_of("t-8"), "t-8")
        self.assertEqual(agent._alarm_id_of(None), "")


if __name__ == "__main__":
    unittest.main()


class ReviewA10(Base):
    def test_fresh_owner_line_is_not_buried_behind_old_rows(self):
        now = time.time()
        for i in range(8):
            core_notices.note_owner_decision(f"старая пометка {i}", run_id="run-DM", ts=now - 3600 + i)
        # окно уже видело их: они «прочитано»
        self.block(run="run-W", kind="task_window", chat="777", owner=True)
        core_notices.note_incoming(kind="dm", chat_id="777", chat_title="Егор", who="Егор",
                                   message_id=99, gist="mr_ifub больше не поднимаю",
                                   private=True, is_owner_dm=True, ts=now)
        text = self.block(run="run-W", kind="task_window", chat="777", owner=True)
        self.assertIn("не поднимаю", text, "свежее — впереди прочитанного (A10 F3)")
        self.assertTrue(text.splitlines()[1].startswith("• НОВОЕ"), text)

    def test_owner_line_reaches_only_windows_alive_at_that_moment(self):
        self.live_windows = ["run-W1"]
        core_notices.note_owner_line("ну их", chat_id="777", message_id=1)
        self.assertIn("ну их", self.block(run="run-W1", kind="task_window", chat="777", owner=True))
        self.assertEqual(self.block(run="run-W2", kind="task_window", chat="777", owner=True), "",
                         "окно, рождённое позже, реплику не получает — оно читает дневник (A10 F4)")

    def test_alarm_born_run_inherits_the_owner_line(self):
        self.live_windows = []
        self.alarms = ["alarm-1"]
        core_notices.note_owner_line("не считай графы", chat_id="777", message_id=2)
        self.assertEqual(self.block(run="run-X", kind="wake", chat="777", owner=True), "")
        self.assertEqual(core_notices.bind_alarm_run("alarm-1", "run-X"), 1)
        self.assertIn("не считай графы", self.block(run="run-X", kind="wake", chat="777", owner=True))
        self.assertEqual(core_notices.bind_alarm_run("alarm-9", "run-Y"), 0)

    def test_owner_line_without_addressees_is_not_stored(self):
        self.live_windows = []
        self.alarms = []
        self.assertIsNone(core_notices.note_owner_line("в пустоту", chat_id="777", message_id=3))
        self.assertEqual(core_notices.pending(), [])

    def test_window_flag_beats_run_kind(self):
        core_notices.note_owner_decision("решено", run_id="run-DM")
        self.assertEqual(self.block(run="run-V", kind="voice", chat="777", owner=True), "")
        text = core_notices.block_for_input(run_id="run-V", run_kind="voice", chat_id="777",
                                            owner_context=True, window=True)
        self.assertIn("решено", text, "пульс — её ход по контексту, а не по виду run (A10 F9)")

    def test_save_leaves_no_shared_tmp_and_no_stray_files(self):
        core_notices.note_incoming(kind="mention", chat_id="-1", chat_title="B", who="a",
                                   message_id=1, gist="x", private=False)
        names = sorted(p.name for p in self.tmp.iterdir())
        self.assertNotIn("notices.json.tmp", names)
        self.assertTrue(all(not n.endswith(".tmp") for n in names), names)


class Seams(unittest.TestCase):
    def test_notices_block_uses_owner_audience_and_context_window(self):
        import agent
        seen = {}

        def fake_block(**kw):
            seen.update(kw)
            return "блок"
        run = mock.Mock(run_id="run-1", kind="voice")
        ctx = mock.Mock(owner=True, owner_audience=False, praxis_self=True, is_dm=True)
        token = agent._TURN_CHANNEL.set(ctx)
        try:
            with mock.patch.object(agent.run_context, "current_run", return_value=run), \
                 mock.patch.object(agent, "_active_chat", return_value="-100"), \
                 mock.patch("core.notices.block_for_input", side_effect=fake_block), \
                 mock.patch("core.notices.enabled", return_value=True):
                self.assertEqual(agent._notices_block(), "блок")
        finally:
            agent._TURN_CHANNEL.reset(token)
        self.assertFalse(seen["owner_context"], "владелец в публичной комнате — не владельческая аудитория (A10 F1)")
        self.assertTrue(seen["window"], "её собственный ход по контексту (A10 F9)")

    def test_hooks_are_installed_on_agent(self):
        import agent
        from core import notices as core_notices
        self.assertIs(core_notices.live_window_runs, agent._live_window_run_ids)
        self.assertIs(core_notices.pending_alarm_ids, agent._pending_alarm_ids)
