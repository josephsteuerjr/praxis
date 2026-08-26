"""Transport gate 25.08: forum-history scan (runtime-owned, fail-closed).

Red→green контракт: workspace/TRANSPORT-GATE-FORUM-HISTORY-25.08.md.
Все тесты — на pure-ядре history_scan + границе тула; живой Telegram не трогается.
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("PRAXIS_TEST", "1")

import history_scan  # noqa: E402
import telegram_routes  # noqa: E402


def _routes_dir():
    tmp = tempfile.mkdtemp(prefix="praxis-history-scan-test-")
    return Path(tmp)


class Msg:
    """Минимальная модель Telethon-сообщения."""

    def __init__(self, mid, topic=None, opener=False, title=None):
        self.id = mid
        self.reply_to = None
        self.action = None
        if topic is not None:
            class RT:
                pass
            rt = RT()
            rt.reply_to_top_id = topic
            rt.forum_topic = True
            rt.reply_to_msg_id = None
            self.reply_to = rt
        if opener:
            class Act:
                pass
            a = Act()
            a.title = title
            self.action = a
            type(a).__name__ = "MessageActionTopicCreate"


class TestParameterValidation(unittest.TestCase):
    def test_page_size_zero_rejected_before_scan(self):
        async def fp(offset, size):
            return []

        with self.assertRaises(ValueError):
            asyncio.run(history_scan.run_history_scan(
                "-100", history_scan.ScanRange(1, 10), fp, page_size=0))

    def test_max_pages_zero_rejected(self):
        async def fp(offset, size):
            return []

        with self.assertRaises(ValueError):
            asyncio.run(history_scan.run_history_scan(
                "-100", history_scan.ScanRange(1, 10), fp, page_size=5, max_pages=0))

    def test_negative_range_rejected(self):
        with self.assertRaises(ValueError):
            history_scan.ScanRange(floor_id=10, ceiling_id=1)


class TestHonestPagination(unittest.TestCase):
    def test_complete_happy_path(self):
        pages = [[Msg(150), Msg(140, topic=1)], [Msg(120, opener=True, title="Тема")],
                 [Msg(60)]]

        async def fp(offset, size):
            return pages.pop(0) if pages else []

        r = asyncio.run(history_scan.run_history_scan(
            "-100123", history_scan.ScanRange(1, 150), fp, page_size=2, max_pages=10))
        self.assertTrue(r.complete)
        self.assertEqual(r.reason, "history_exhausted")
        self.assertEqual(r.opener_count, 1)
        opener = [s for s in r.signals if s.origin == "history_opener"][0]
        # id темы = id сообщения-открытия (сквозная нумерация комнаты)
        self.assertEqual(opener.topic_id, 120)
        self.assertEqual(opener.title, "Тема")

    def test_non_monotonic_page_incomplete_no_signals(self):
        async def bad(offset, size):
            return [Msg(offset + 999)]

        r = asyncio.run(history_scan.run_history_scan(
            "-100", history_scan.ScanRange(1, 100), bad, page_size=5, max_pages=5))
        self.assertFalse(r.complete)
        self.assertEqual(r.reason, "pagination_violation")
        self.assertEqual(r.signals, [])

    def test_duplicate_id_incomplete(self):
        async def dup(offset, size):
            return [Msg(50), Msg(50)]

        r = asyncio.run(history_scan.run_history_scan(
            "-100", history_scan.ScanRange(1, 100), dup, page_size=5, max_pages=5))
        self.assertFalse(r.complete)
        self.assertEqual(r.reason, "pagination_violation")

    def test_rpc_failure_midscan_incomplete(self):
        async def rpcfail(offset, size):
            if offset < 80:
                raise RuntimeError("FloodWait")
            return [Msg(offset - 1)]

        r = asyncio.run(history_scan.run_history_scan(
            "-100", history_scan.ScanRange(1, 100), rpcfail, page_size=10, max_pages=50))
        self.assertFalse(r.complete)
        self.assertTrue(r.reason.startswith("rpc_error"))
        self.assertEqual(r.signals, [])

    def test_page_budget_incomplete(self):
        async def endless(offset, size):
            return [Msg(offset - 1)]

        r = asyncio.run(history_scan.run_history_scan(
            "-100", history_scan.ScanRange(1, 1000000), endless, page_size=1, max_pages=5))
        self.assertFalse(r.complete)
        self.assertEqual(r.reason, "page_budget_exhausted")


class TestPositiveOnlySemantics(unittest.TestCase):
    def test_clean_scan_no_verdict_no_writes(self):
        async def clean(offset, size):
            return [Msg(offset - 1)] if offset > 3 else []

        r = asyncio.run(history_scan.run_history_scan(
            "-100clean", history_scan.ScanRange(1, 10), clean, page_size=1, max_pages=50))
        self.assertTrue(r.complete)
        self.assertEqual(r.signals, [])
        out = history_scan.observe_history_floor(r, apply=False)
        self.assertFalse(out["applied"])
        self.assertEqual(out["written"], [])

    def test_dry_run_with_signals_is_read_only_and_does_not_consume_admission(self):
        async def fp(offset, size):
            return [Msg(120, opener=True, title="Тема")] if offset > 120 else []

        r = asyncio.run(history_scan.run_history_scan(
            "-100dry", history_scan.ScanRange(1, 150), fp, page_size=5, max_pages=10))
        history_scan.reset_admission_registry()
        original_observe = telegram_routes.observe
        calls = []

        def forbidden_observe(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("apply=false вызвал durable writer")

        telegram_routes.observe = forbidden_observe
        try:
            first = history_scan.observe_history_floor(r, apply=False)
            second = history_scan.observe_history_floor(r, apply=False)
        finally:
            telegram_routes.observe = original_observe

        self.assertFalse(first["applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(first["written"], [])
        self.assertEqual(second["written"], [])
        self.assertEqual(calls, [])

        # Оба dry-run не занимают admission: тот же exact scan можно применить.
        applied = history_scan.observe_history_floor(r, apply=True)
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["written"], ["topic_opener_seen"])

    def test_general_header_is_positive_signal(self):
        async def fp(offset, size):
            return [Msg(90, topic=1)] if offset > 90 else []

        r = asyncio.run(history_scan.run_history_scan(
            "-100gen", history_scan.ScanRange(1, 100), fp, page_size=5, max_pages=10))
        self.assertTrue(r.complete)
        self.assertEqual(r.general_count, 1)
        self.assertEqual(r.signals[0].origin, "history_header")

    def test_apply_writes_single_topic_opener_epoch(self):
        async def fp(offset, size):
            return [Msg(120, opener=True, title="Тема")] if offset > 120 else []

        r = asyncio.run(history_scan.run_history_scan(
            "-100apply", history_scan.ScanRange(1, 150), fp, page_size=5, max_pages=10))
        out = history_scan.observe_history_floor(r, apply=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["written"], ["topic_opener_seen"])

    def test_incomplete_apply_refused(self):
        result = history_scan.HistoryScanResult(
            peer_id="-100x", range=history_scan.ScanRange(1, 10), complete=False,
            reason="pagination_violation")
        result.signals = [history_scan.ScanSignal(5, 5, "history_opener", "t")]
        out = history_scan.observe_history_floor(result, apply=True)
        self.assertFalse(out["applied"])
        self.assertTrue(out["reason"].startswith("incomplete"))

    def test_admission_single_use_and_idempotent(self):
        history_scan.reset_admission_registry()
        async def fp(offset, size):
            return [Msg(120, opener=True, title="Тема")] if offset > 120 else []

        r = asyncio.run(history_scan.run_history_scan(
            "-100adm", history_scan.ScanRange(1, 150), fp, page_size=5, max_pages=10))
        first = history_scan.observe_history_floor(r, apply=True)
        second = history_scan.observe_history_floor(r, apply=True)
        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(second["reason"], "already_admitted")

    def test_foreign_object_rejected(self):
        with self.assertRaises(history_scan.HistoryScanError):
            history_scan.observe_history_floor({"peer_id": "-100"}, apply=False)


class TestBackfillSilenceIsNotFalse(unittest.TestCase):
    """П.1 гейта: production backfill больше не пишет no_topic_openers_in_range."""

    def test_no_false_from_silence_kind_missing_from_runner(self):
        # Инвентаризация: в живом backfill не осталось production-ветки тишины.
        source = Path(__file__).with_name("mtproto_runner.py").read_text(
            encoding="utf-8")
        self.assertNotIn(
            'kind=("topic_opener_seen" if openers else "no_topic_openers_in_range")',
            source,
            "backfill снова пишет FALSE из тишины",
        )
        self.assertIn("воздерживаюсь", source)


class TestToolBoundary(unittest.TestCase):
    """П.2: тул-граница — transport параметры запрещены на входе."""

    def setUp(self):
        import agent
        self._agent = agent
        self._orig = agent._active_principal
        agent._active_principal = lambda: getattr(agent, "PRAXIS_SELF_PRINCIPAL", None) \
            or "praxis:self"
        self._orig_hook = agent._TELETHON.get("history_scan")
        agent._TELETHON["history_scan"] = lambda target="", params=None, _principal="unknown": (
            f"SCAN({target}, {sorted((params or {}).items())})")

    def tearDown(self):
        self._agent._active_principal = self._orig
        if self._orig_hook is None:
            self._agent._TELETHON.pop("history_scan", None)
        else:
            self._agent._TELETHON["history_scan"] = self._orig_hook

    def test_banned_params_blocked(self):
        agent = self._agent
        out = agent.tool_telegram_account(
            action="history_scan", target="-100123",
            params={"floor_id": 1, "ceiling_id": 10, "client": "FAKE"})
        self.assertIn("запрещены на границе тула", out)
        self.assertIn("client", out)

    def test_valid_params_reach_runtime_hook(self):
        agent = self._agent
        out = agent.tool_telegram_account(
            action="history_scan", target="-100123",
            params={"floor_id": 1, "ceiling_id": 10})
        self.assertIn("SCAN(-100123", out)
        self.assertIn("floor_id", out)

    def test_no_hook_returns_honest_refusal(self):
        agent = self._agent
        saved = agent._TELETHON.pop("history_scan", None)
        try:
            out = agent.tool_telegram_account(
                action="history_scan", target="-100123",
                params={"floor_id": 1, "ceiling_id": 10})
            self.assertIn("недоступен", out)
        finally:
            if saved is not None:
                agent._TELETHON["history_scan"] = saved

    def test_params_json_object_only(self):
        agent = self._agent
        out = agent.tool_telegram_account(
            action="history_scan", target="-100123",
            params_json='[1,2,3]')
        self.assertIn("params_json должен быть JSON object", out)

    def test_no_hook_returns_honest_refusal(self):
        agent = self._agent
        saved = dict(agent._TELETHON)
        agent._TELETHON.pop("history_scan", None)
        try:
            out = agent.tool_telegram_account(
                action="history_scan", target="-100123",
                params={"floor_id": 1, "ceiling_id": 10})
            self.assertIn("недоступен", out)
        finally:
            agent._TELETHON.clear()
            agent._TELETHON.update(saved)


    def test_schema_enum_declares_history_scan(self):
        agent = self._agent
        schema = agent.TELEGRAM_ACCOUNT_TOOL["input_schema"]
        enum = schema["properties"]["action"]["enum"]
        self.assertIn("history_scan", enum)

    def test_every_handled_action_is_in_schema_enum(self):
        agent = self._agent
        schema = agent.TELEGRAM_ACCOUNT_TOOL["input_schema"]
        enum = set(schema["properties"]["action"]["enum"])
        handled = {"join", "leave", "followups", "cancel_followup",
                   "watch_reply", "unwatch_reply", "history_scan"}
        self.assertLessEqual(handled, enum)


if __name__ == "__main__":
    unittest.main()
