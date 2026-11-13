# -*- coding: utf-8 -*-
"""Кандидат latency-0711 (07.11): задержка до ответа.

1. Потолок кэша ленты событий (`memory_life._EVENTS_CACHE_LIMIT`). Лента перевалила
   128 МБ 09.10, и месяц самые свежие файлы дня разбирались заново на каждый
   `_lookup_events`/`iter_events` — молча. Переполнение теперь пишет предупреждение.
2. Реплика владельца в ЛС (`core.notices.note_owner_line`) искала живые окна полным
   `live_run_ids` — ~3 syscall на каждый из ~9 000 терминальных прогонов; под чужой
   пересборкой памяти это 6–26 с до `_arm`. Теперь — `RunManager.unsettled_run_ids`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import memory_life as ml
from test_run_manager import RunManagerBase


class EventsCacheOverflowIsLoud(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        for patcher in (mock.patch.object(ml, 'EVENTS_DIR', self.dir),
                        mock.patch.object(ml, '_same_place', lambda a, b: str(a) == str(b)),
                        mock.patch.object(ml, '_EVENTS_CACHE_OVERFLOW_AT', None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        for cache in (ml._EVENTS_CACHE, ml._EVENT_LOOKUPS):
            cache.clear()
            self.addCleanup(cache.clear)

    def write(self, name, rows):
        with (self.dir / name).open('a', encoding='utf-8') as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    @staticmethod
    def row(i, chat='A'):
        return {'id': f'ev{i:03d}', 'ts': f'2026-11-0{i % 7 + 1}T10:{i:02d}:00.000Z',
                'kind': 'conversation_message', 'chat_id': chat, 'text': f't{i}',
                'dedupe_key': f'k{i}', 'meta': {'is_dm': True}}

    def test_default_ceiling_holds_a_year_of_todays_ledger(self):
        # 174 МБ ленты на 07.11 и ~1,5–2 МБ в день: прежние 128 МБ — уже обрыв.
        env = dict(os.environ)
        env.pop("PRAXIS_EVENTS_CACHE_MB", None)
        value = subprocess.check_output(
            [sys.executable, "-c", "import memory_life; print(memory_life._EVENTS_CACHE_LIMIT)"],
            env=env, text=True)
        self.assertGreaterEqual(int(value.strip()), 512 * 1024 * 1024)

    def test_overflow_is_logged_and_records_stay_correct(self):
        self.write('2026-11-06.jsonl', [self.row(1), self.row(2, chat='B')])
        self.write('2026-11-07.jsonl', [self.row(3)])
        with mock.patch.object(ml, '_EVENTS_CACHE_LIMIT', 10):
            with self.assertLogs('praxis-life', level='WARNING') as seen:
                got = ml.iter_events(chat_id='A')
            self.assertEqual([r['id'] for r in got], ['ev001', 'ev003'])
            self.assertEqual(ml._EVENTS_CACHE, {})
        self.assertTrue(any('events cache overflow' in line for line in seen.output), seen.output)

    def test_overflow_warning_is_rate_limited(self):
        self.write('2026-11-07.jsonl', [self.row(1)])
        with mock.patch.object(ml, '_EVENTS_CACHE_LIMIT', 10):
            with self.assertLogs('praxis-life', level='WARNING') as seen:
                ml.iter_events(chat_id='A')
                ml.iter_events(chat_id='A')
                ml._lookup_events(dedupe_key='k1')
        overflow = [line for line in seen.output if 'events cache overflow' in line]
        self.assertEqual(len(overflow), 1, seen.output)

    def test_lookup_overflow_is_logged_and_still_finds_the_row(self):
        self.write('2026-11-07.jsonl', [self.row(5)])
        with mock.patch.object(ml, '_EVENTS_CACHE_LIMIT', 10):
            with self.assertLogs('praxis-life', level='WARNING') as seen:
                found = ml._lookup_events(dedupe_key='k5')
        self.assertEqual([r['id'] for r in found], ['ev005'])
        self.assertTrue(any('[lookups]' in line or '[records]' in line for line in seen.output),
                        seen.output)

    def test_no_warning_when_everything_fits(self):
        self.write('2026-11-07.jsonl', [self.row(1)])
        with mock.patch.object(ml.log, 'warning') as warning:
            ml.iter_events(chat_id='A')
            ml._lookup_events(dedupe_key='k1')
        warning.assert_not_called()


class UnsettledViewSkipsSettledRuns(RunManagerBase):
    def settle(self, count):
        done = []
        for i in range(count):
            created = self.create(f'done-{i}')
            self.manager.transition(created.run_id, 'running')
            self.manager.transition(created.run_id, 'done')
            done.append(created.run_id)
        return done

    def test_before_any_full_scan_it_is_the_full_scan(self):
        live = self.create('live').run_id
        self.settle(3)
        with mock.patch.object(self.manager, 'live_run_ids',
                               wraps=self.manager.live_run_ids) as full:
            self.assertEqual(self.manager.unsettled_run_ids(), [live])
        full.assert_called_once()

    def test_settled_runs_are_not_touched_after_a_full_scan(self):
        done = self.settle(30)
        live = self.create('live').run_id
        self.assertEqual(self.manager.live_run_ids(), [live])
        touched = []
        real_stat = Path.stat

        def observe(path, *args, **kwargs):
            touched.append(str(path))
            return real_stat(path, *args, **kwargs)

        def touched_done():
            return [p for p in touched if any(run_id in p for run_id in done)]

        with mock.patch.object(Path, 'stat', observe):
            self.assertEqual(self.manager.unsettled_run_ids(), [live])
            cheap = touched_done()
            touched.clear()
            self.assertEqual(self.manager.live_run_ids(), [live])
            full = touched_done()
        self.assertEqual(cheap, [], 'дешёвый вид не должен трогать терминальные прогоны')
        # Чувствительность стенда: полный обход их трогает, иначе тест ничего не доказывает.
        self.assertTrue(full, 'полный обход обязан перепроверять подписи терминальных')

    def test_run_created_after_the_full_scan_is_visible(self):
        self.settle(2)
        self.manager.live_run_ids()
        new = self.create('late').run_id
        self.assertIn(new, self.manager.unsettled_run_ids())

    def test_run_finished_after_the_full_scan_stays_listed_for_the_caller(self):
        # Вердикт «терминален» даёт только полный обход; дешёвый вид ошибается лишь
        # в сторону лишнего кандидата, а его статус проверяет вызывающий по манифесту.
        live = self.create('soon-done').run_id
        self.manager.live_run_ids()
        self.manager.transition(live, 'running')
        self.manager.transition(live, 'done')
        self.assertIn(live, self.manager.unsettled_run_ids())


class ForeignReopenOwnerNotice(RunManagerBase):
    def closed_window(self):
        from run_context import RunContext
        ctx = RunContext.create(run_id="run-window", kind="task_window", goal="test",
                                principal_id="telegram:1", scope="owner",
                                origin_chat_id="1", delivery_chat_id="1")
        self.manager.create(ctx, "hermetic window")
        self.manager.transition(ctx.run_id, "running")
        paused = self.manager.transition(ctx.run_id, "paused")
        warned = self.manager.mark_resume_stale(
            ctx.run_id, expected_revision=paused["revision"], progress=[],
            previous_status="paused")
        self.manager.close_resume_stale(
            ctx.run_id, expected_revision=warned["revision"], progress=[])
        self.assertEqual(self.manager.live_run_ids(), [])
        return ctx.run_id

    def assert_notice(self, run_id):
        import agent
        from core import notices
        with mock.patch.object(agent, "_runs", return_value=self.manager), \
                mock.patch.object(notices, "live_window_runs", agent._live_window_run_ids), \
                mock.patch.object(notices, "pending_alarm_ids", lambda: []), \
                mock.patch.object(notices, "STATE_FILE", self.base / "notices.json"), \
                mock.patch.dict(os.environ, {"PRAXIS_NOTICES": "on"}), \
                mock.patch.object(self.manager, "live_run_ids",
                                  side_effect=AssertionError("hot path full scan")):
            note = notices.note_owner_line("owner after reopen", chat_id="1", message_id=42)
            self.assertIsNotNone(note)
            self.assertIn(run_id, note["for_runs"])
            block = notices.block_for_input(run_id=run_id, run_kind="task_window",
                                             chat_id="1", owner_context=True)
            self.assertIn("owner after reopen", block)

    def test_foreign_manager_reopen_reaches_owner_note_without_full_scan(self):
        from run_manager import RunManager
        run_id = self.closed_window()
        reopened = RunManager(self.base).authorize_resume(run_id, actor="praxis:self")
        self.assertEqual(reopened["status"], "paused")
        self.assert_notice(run_id)

    def test_foreign_process_reopen_reaches_owner_note_without_full_scan(self):
        run_id = self.closed_window()
        subprocess.run([sys.executable, "-c",
                        "from run_manager import RunManager; import sys; "
                        "RunManager(sys.argv[1]).authorize_resume(sys.argv[2], actor='praxis:self')",
                        str(self.base), run_id], check=True)
        self.assert_notice(run_id)

    def test_foreign_wal_only_reopen_reaches_owner_note_without_full_scan(self):
        import run_manager
        run_id = self.closed_window()
        foreign = run_manager.RunManager(self.base)
        with mock.patch.object(run_manager, "_atomic_json", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                foreign.authorize_resume(run_id, actor="praxis:self")
        self.assert_notice(run_id)

    def test_foreign_close_after_live_scan_still_reaches_reopen(self):
        from run_manager import RunManager
        run_id = self.closed_window()
        foreign = RunManager(self.base)
        foreign.authorize_resume(run_id, actor="praxis:self")
        self.assertIn(run_id, self.manager.live_run_ids())
        paused = foreign.manifest(run_id)
        warned = foreign.mark_resume_stale(run_id, expected_revision=paused["revision"],
                                           progress=[], previous_status="paused")
        foreign.close_resume_stale(run_id, expected_revision=warned["revision"], progress=[])
        # Re-cache the newly closed terminal in the reader, then reopen elsewhere.
        self.assertEqual(self.manager.live_run_ids(), [])
        foreign.authorize_resume(run_id, actor="praxis:self")
        self.assert_notice(run_id)


class OwnerLineUsesTheCheapView(unittest.TestCase):
    def test_full_scan_is_not_used_when_the_cheap_view_exists(self):
        import agent
        from core import notices as core_notices
        kind = next(iter(core_notices.WINDOW_RUN_KINDS))
        runs = types.SimpleNamespace(
            unsettled_run_ids=lambda: ['w1', 'chat'],
            live_run_ids=mock.Mock(side_effect=AssertionError('полный обход в горячем пути')),
            manifest=lambda rid: {'run_id': rid},
            _manifest_listing_row=lambda rid, m: {
                'run_id': rid, 'status': 'running',
                'kind': kind if rid == 'w1' else 'chat_turn'})
        with mock.patch.object(agent, '_runs', return_value=runs):
            self.assertEqual(agent._live_window_run_ids(), ['w1'])
        runs.live_run_ids.assert_not_called()


if __name__ == '__main__':
    unittest.main()
