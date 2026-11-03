"""Real startup ownership and authored-only refresh, not evaluator compaction."""
from __future__ import annotations

import asyncio
import contextvars
import sys
import threading
import types
import unittest
from collections import defaultdict, deque
from contextlib import ExitStack
from unittest import mock

import memory_life as life
import llm
import mtproto_runner as runner
from test_coverage_vs_current import Base, ROOM


class AuthoredDebtOffer(Base):
    def test_debt_publishes_real_offer_without_generation_or_canonical_change(self):
        rows = [self._msg(i, f"original {i}") for i in range(1, 4)]
        compact = self._compact(rows)
        self._edit(2, "changed current revision")
        before_debt = life.refresh_debt(ROOM)
        self.assertEqual(before_debt["unresolved_group_count"], 1)
        before_files = {str(p): p.read_bytes() for p in life.LIFE_DIR.rglob("*")
                        if p.is_file() and p.suffix in {".jsonl", ".md"}}
        with mock.patch.object(runner, "_REFRESH_LAST_PAID", {}), \
             mock.patch.object(runner, "_REFRESH_COOLDOWN_SEC", 600.0), \
             mock.patch.object(life, "refresh_compacts", side_effect=AssertionError("no evaluator refresh")), \
             mock.patch.object(llm, "chat", side_effect=AssertionError("no model")):
            asyncio.run(runner._maybe_pay_refresh_debt(ROOM))
            self.assertIn(ROOM, life.fold_offers())
            forced = life.fold_now(ROOM)
        self.assertTrue(forced["author_required"])
        self.assertEqual(forced["folded"], 0)
        self.assertIn(ROOM, life.fold_offers())
        self.assertEqual(before_files, {str(p): p.read_bytes() for p in life.LIFE_DIR.rglob("*")
                                      if p.is_file() and p.suffix in {".jsonl", ".md"}})
        self.assertEqual(life.refresh_debt(ROOM), before_debt)
        self.assertNotIn(compact["id"], life.context_summary(ROOM))
        selected = life.prepare_authored_fold(ROOM, compact_id=compact["id"])
        self.assertTrue(selected["ok"], selected)
        self.assertEqual(selected["kind"], "refresh")
        self.assertIn("changed current revision", str(selected["sources"]))
        self.assertNotIn("original 2", str(selected["sources"]))


class RefreshExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_offer_failure_keeps_cooldown_and_other_places_progress(self):
        seen = []
        def offer(place, *, force):
            seen.append(place)
            if place == "broken":
                raise OSError("offer not written")
            return {"offered": True, "author_required": True, "folded": 0}
        with mock.patch.object(runner, "_REFRESH_LAST_PAID", {}), \
             mock.patch.object(runner, "_REFRESH_COOLDOWN_SEC", 600.0), \
             mock.patch.object(life, "refresh_debt", return_value={"unresolved_group_count": 1}), \
             mock.patch.object(life, "compact_if_due", side_effect=offer):
            await runner._maybe_pay_refresh_debt("broken")
            await runner._maybe_pay_refresh_debt("broken")
            await runner._maybe_pay_refresh_debt("next")
        self.assertEqual(seen, ["broken", "next"])

    async def test_executor_keeps_context_and_keyword_contract(self):
        value = contextvars.ContextVar("maintenance-test", default="absent")
        token = value.set("owned")
        try:
            out = await runner._in_refresh_executor(
                lambda *, force: (value.get(), force, threading.current_thread().name),
                force=True)
        finally:
            value.reset(token)
        self.assertEqual(out[:2], ("owned", True))
        self.assertTrue(out[2].startswith("praxis-refresh-debt"), out)

    async def test_cancelling_compact_does_not_enqueue_new_debt(self):
        entered = asyncio.Event()
        async def slow_offer(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        with mock.patch.object(runner.memory_life, "place_key", return_value="room"), \
             mock.patch.object(runner, "_compacting", set()), \
             mock.patch.object(runner.asyncio, "to_thread", side_effect=slow_offer), \
             mock.patch.object(runner, "_maybe_pay_refresh_debt", new_callable=mock.AsyncMock) as debt:
            task = asyncio.create_task(runner._maybe_compact("room"))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            debt.assert_not_awaited()
            self.assertEqual(runner._compacting, set())


class StartupProducer(unittest.IsolatedAsyncioTestCase):
    async def _main_scenario(self, *, supervise_error=False):
        entered, release = asyncio.Event(), asyncio.Event()
        calls, cancelled, created = [], [], []
        original_create = asyncio.create_task
        async def check(chat):
            calls.append(chat)
            if chat == "first":
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.append(chat)
                    raise
        async def supervise():
            await asyncio.wait_for(entered.wait(), 2)
            self.assertEqual(calls, ["first"])
            owned = [t for t in created if t.get_name() == "startup-memory-maintenance"]
            self.assertEqual(len(owned), 1)
            self.assertFalse(owned[0].done())
            if supervise_error:
                raise ConnectionError("transport stopped")
            return False
        async def idle():
            return None
        def create(coro, **kwargs):
            task = original_create(coro, **kwargs)
            created.append(task)
            return task
        client = types.SimpleNamespace(connect=mock.AsyncMock(),
            is_user_authorized=mock.AsyncMock(return_value=True),
            get_me=mock.AsyncMock(return_value=types.SimpleNamespace(id=1, username="synthetic")))
        probe = types.SimpleNamespace(stop=mock.Mock())
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(sys.modules, {"goals": types.SimpleNamespace(
                decommission_legacy=lambda: {})}))
            stack.enter_context(mock.patch.object(runner, "client", client))
            stack.enter_context(mock.patch.object(runner, "_LOOP", None))
            stack.enter_context(mock.patch.object(runner, "_self_id", None))
            stack.enter_context(mock.patch.object(runner, "_CLOCK_ALIVE", [False]))
            stack.enter_context(mock.patch.object(runner, "_ONE_MIND", asyncio.Lock()))
            stack.enter_context(mock.patch.object(runner, "_buf", defaultdict(deque)))
            stack.enter_context(mock.patch.dict(runner.agent._TELETHON, {}, clear=True))
            stack.enter_context(mock.patch.object(runner.bufstore, "load_all", return_value={"first": [], "second": []}))
            stack.enter_context(mock.patch.object(runner.bufstore, "meta_load", return_value={}))
            stack.enter_context(mock.patch.object(runner, "_restored_buffer_partition", side_effect=lambda x: (x, ())))
            stack.enter_context(mock.patch.object(runner.memory_life, "bootstrap_legacy", return_value={"already": True}))
            stack.enter_context(mock.patch.object(runner, "_sync_buffer_from_hot"))
            stack.enter_context(mock.patch.object(runner.agent, "recover_durable_state", return_value=[]))
            stack.enter_context(mock.patch.object(runner.agent, "resume_durable_runs", return_value=[]))
            stack.enter_context(mock.patch.object(runner, "_maybe_compact", side_effect=check))
            stack.enter_context(mock.patch.object(runner.asyncio, "create_task", side_effect=create))
            stack.enter_context(mock.patch.object(runner, "_supervise_connection", side_effect=supervise))
            stack.enter_context(mock.patch.object(runner, "_start_loop_liveness_probe", return_value=probe))
            for name in ("_start_dialog_warmup", "_bridge_canary", "_warm_media",
                         "_install_telegram_dispatcher", "_install_dead_room_filter"):
                stack.enter_context(mock.patch.object(runner, name))
            for name in ("_start_shared_stt", "_early_control_watch", "_membership_reconcile_once",
                         "_direct_outbox_once", "_media_cleanup_once", "_text_outbox_once",
                         "_clock", "_missed_dm_sweep"):
                stack.enter_context(mock.patch.object(runner, name, side_effect=idle))
            try:
                if supervise_error:
                    with self.assertRaises(ConnectionError):
                        await runner.main()
                else:
                    await runner.main()
                self.assertEqual(calls, ["first"])
                self.assertEqual(cancelled, ["first"])
                owned = [t for t in created if t.get_name() == "startup-memory-maintenance"]
                self.assertEqual(len(owned), 1)
                self.assertTrue(owned[0].cancelled())
                probe.stop.assert_called_once()
            finally:
                release.set()
                for task in created:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*created, return_exceptions=True)

    async def test_real_main_tracks_one_check_and_awaits_shutdown(self):
        await self._main_scenario()

    async def test_real_main_transport_failure_cancels_remaining_checks(self):
        await self._main_scenario(supervise_error=True)
