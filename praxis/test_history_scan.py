"""Transport gate 25.08: forum-history scan (runtime-owned, fail-closed).

Red→green контракт: workspace/TRANSPORT-GATE-FORUM-HISTORY-25.08.md.
Все тесты — на pure-ядре history_scan + границе тула; живой Telegram не трогается.
"""

import asyncio
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("PRAXIS_TEST", "1")

import history_scan  # noqa: E402
import telegram_routes  # noqa: E402
import mtproto_runner  # noqa: E402


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
    def test_resolved_name_or_username_is_transport_authoritative(self):
        self.assertTrue(history_scan.resolved_peer_matches_target(
            "AbstractDL Chat", 1240718803))
        self.assertTrue(history_scan.resolved_peer_matches_target(
            "@abstractdl_chat", 1240718803))

    def test_explicit_numeric_peer_forms_remain_fail_closed(self):
        self.assertTrue(history_scan.resolved_peer_matches_target(
            "-1001240718803", 1240718803))
        self.assertTrue(history_scan.resolved_peer_matches_target(
            "1240718803", 1240718803))
        self.assertTrue(history_scan.resolved_peer_matches_target("-123", 123))
        self.assertFalse(history_scan.resolved_peer_matches_target(
            "-1001240718803", 1240718804))
        self.assertFalse(history_scan.resolved_peer_matches_target(
            "1240718803", 1240718804))
        self.assertFalse(history_scan.resolved_peer_matches_target("-123", 124))
        self.assertFalse(history_scan.resolved_peer_matches_target("-100", 100))
        self.assertFalse(history_scan.resolved_peer_matches_target("", 1240718803))

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


class TestPrivateFreeze(unittest.TestCase):
    def _result(self):
        result = history_scan.HistoryScanResult(
            peer_id="-100secret", range=history_scan.ScanRange(1, 2),
            complete=True, reason="history_exhausted")
        result.corpus = [
            {"message_id": 2, "date": "2025-01-02T03:04:05Z",
             "text": "TOP SECRET TEXT", "reply_to_message_id": 1, "topic_id": 1},
            {"message_id": 1, "date": None, "text": "first",
             "reply_to_message_id": None, "topic_id": None},
        ]
        return result

    def test_freeze_is_canonical_private_and_route_read_only(self):
        result = self._result()
        original = telegram_routes.observe
        telegram_routes.observe = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("freeze mutated route state"))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                receipt = history_scan.freeze_corpus(result, root=Path(tmp))
                artifact = Path(receipt["path"])
                exact = artifact.read_bytes()
                expected = (
                    b'{"message_id":1,"date":null,"text":"first",'
                    b'"reply_to_message_id":null,"topic_id":null}\n'
                    b'{"message_id":2,"date":"2025-01-02T03:04:05Z",'
                    b'"text":"TOP SECRET TEXT","reply_to_message_id":1,"topic_id":1}\n')
                self.assertEqual(exact, expected)
                self.assertNotIn(b'"sender_id"', exact)
                import hashlib
                self.assertEqual(receipt["sha256"], hashlib.sha256(expected).hexdigest())
                self.assertEqual(artifact.name, receipt["sha256"] + ".jsonl")
                self.assertNotIn("TOP SECRET TEXT", json.dumps(receipt))
                self.assertNotIn("first", json.dumps(receipt))
        finally:
            telegram_routes.observe = original

    def test_content_address_is_immutable_and_incomplete_refused(self):
        result = self._result()
        with tempfile.TemporaryDirectory() as tmp:
            first = history_scan.freeze_corpus(result, root=Path(tmp))
            Path(first["path"]).write_bytes(b"tampered")
            with self.assertRaises(history_scan.HistoryScanError):
                history_scan.freeze_corpus(result, root=Path(tmp))
        result.complete = False
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(history_scan.HistoryScanError):
                history_scan.freeze_corpus(result, root=Path(tmp))

    def test_ordinary_scan_does_not_retain_message_corpus(self):
        async def fetch(offset, limit):
            first = Msg(2)
            first.message = "SECRET"
            second = Msg(1)
            second.message = "other"
            return [first, second] if offset > 2 else []

        ordinary = asyncio.run(history_scan.run_history_scan(
            "-100secret", history_scan.ScanRange(1, 2), fetch,
            page_size=10, max_pages=5))
        captured = asyncio.run(history_scan.run_history_scan(
            "-100secret", history_scan.ScanRange(1, 2), fetch,
            page_size=10, max_pages=5, capture_corpus=True))
        self.assertIsNone(ordinary.corpus)
        self.assertEqual([row["text"] for row in captured.corpus], ["SECRET", "other"])

    def test_bounded_acquisition_excludes_blank_and_action_rows(self):
        async def fetch(offset, limit):
            if offset > 5:
                first, service, blank, second = Msg(5), Msg(4, opener=True), Msg(3), Msg(2)
                first.message = "first"
                service.message = "service text must not enter corpus"
                blank.message = "   "
                second.message = "second"
                return [first, service, blank, second]
            return []

        result = asyncio.run(history_scan.acquire_eligible_corpus(
            "-100secret", 5, fetch, eligible_limit=2, page_size=10, max_pages=5))
        self.assertTrue(result.complete)
        self.assertEqual(result.reason, "eligible_limit_reached")
        self.assertEqual(result.range, history_scan.ScanRange(2, 5))
        self.assertEqual([row["message_id"] for row in result.corpus], [5, 2])
        self.assertEqual(set(result.corpus[0]), set(history_scan.FREEZE_FIELDS))

    def test_bounded_acquisition_fails_closed_on_page_budget(self):
        async def fetch(offset, limit):
            row = Msg(offset - 1)
            row.message = "text"
            return [row]

        result = asyncio.run(history_scan.acquire_eligible_corpus(
            "-100secret", 10, fetch, eligible_limit=5, page_size=1, max_pages=1))
        self.assertFalse(result.complete)
        self.assertEqual(result.reason, "page_budget_exhausted")
        self.assertIsNone(result.corpus)

    def test_attested_freeze_requires_matching_complete_range(self):
        discovery = self._result()
        attestation = history_scan.HistoryScanResult(
            peer_id=discovery.peer_id, range=discovery.range,
            complete=True, reason="history_exhausted",
            observed_ids={1, 2})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "history_scan"
            receipt = history_scan.freeze_attested_corpus(
                discovery, attestation, root=root, acquired_at="2026-08-26T21:00:00Z")
            manifest_path = Path(receipt["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            second = self._result()
            second.corpus[0]["text"] = "different corpus"
            second_receipt = history_scan.freeze_attested_corpus(
                second, attestation, root=root, acquired_at="2026-08-26T21:01:00Z")
            self.assertNotEqual(manifest_path, Path(second_receipt["manifest_path"]))
            self.assertEqual(manifest, json.loads(manifest_path.read_text(encoding="utf-8")))
        self.assertEqual(receipt["manifest"]["row_count"], 2)
        self.assertEqual(receipt["manifest"]["history_scan_sha256"], attestation.scan_sha256())
        self.assertEqual(manifest["corpus_sha256"], receipt["sha256"])
        self.assertNotIn("TOP SECRET TEXT", json.dumps(manifest))
        attestation.range = history_scan.ScanRange(2, 2)
        with self.assertRaises(history_scan.HistoryScanError):
            history_scan.freeze_attested_corpus(discovery, attestation)

    def test_freeze_rejects_symlink_escape_and_secures_modes(self):
        result = self._result()
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            outside = parent / "outside"
            outside.mkdir()
            root_link = parent / "root-link"
            root_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(history_scan.HistoryScanError):
                history_scan.freeze_corpus(result, root=root_link)
            self.assertEqual(list(outside.iterdir()), [])

            ancestor_link = parent / "private"
            ancestor_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(history_scan.HistoryScanError):
                history_scan.freeze_corpus(result, root=ancestor_link / "history_scan")
            self.assertFalse((outside / "history_scan").exists())

            root = parent / "root"
            root.mkdir(mode=0o777)
            payload = history_scan._canonical_jsonl(result.corpus)
            digest = hashlib.sha256(payload).hexdigest()
            victim = parent / "victim"
            victim.write_bytes(b"victim")
            (root / f"{digest}.jsonl").symlink_to(victim)
            with self.assertRaises(history_scan.HistoryScanError):
                history_scan.freeze_corpus(result, root=root)
            self.assertEqual(victim.read_bytes(), b"victim")
            self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)

        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "root"
            root.mkdir()
            payload = history_scan._canonical_jsonl(result.corpus)
            digest = hashlib.sha256(payload).hexdigest()
            victim = parent / "victim"
            victim.write_bytes(payload)
            destination = root / f"{digest}.jsonl"
            os.link(victim, destination)
            with self.assertRaises(history_scan.HistoryScanError):
                history_scan.freeze_corpus(result, root=root)
            self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o644)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            receipt = history_scan.freeze_corpus(result, root=root)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            artifact = Path(receipt["path"])
            self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)
            self.assertEqual(artifact.stat().st_nlink, 1)


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


class TestRuntimeFreezeCeiling(unittest.TestCase):
    """The runtime, not a caller-supplied sentinel, finds the newest id."""

    def _invoke(self, params, client):
        entity = type("Entity", (), {"id": 1240718803})()

        class ImmediateResult:
            def __init__(self, coro):
                self.coro = coro

            def result(self, timeout=None):
                return asyncio.run(self.coro)

        with mock.patch.object(mtproto_runner, "OWNER_ID", 1), \
             mock.patch.object(mtproto_runner, "client", client), \
             mock.patch.object(mtproto_runner, "_LOOP", object()), \
             mock.patch.object(mtproto_runner, "_resolve_entity",
                               new=mock.AsyncMock(return_value=entity)), \
             mock.patch.object(asyncio, "run_coroutine_threadsafe",
                               side_effect=lambda coro, loop: ImmediateResult(coro)):
            return mtproto_runner._sync_history_scan(
                "-1001240718803", params, _principal="praxis:self")

    def test_latest_freeze_uses_runtime_newest_id_without_offset_overflow(self):
        class Client:
            def __init__(self):
                self.calls = []

            def iter_messages(self, entity, *, limit, offset_id=None):
                self.calls.append((limit, offset_id))

                async def rows():
                    if offset_id is None:
                        yield type("Message", (), {"id": 77})()
                    elif offset_id == 78:
                        yield type("Message", (), {
                            "id": 77, "message": "text", "action": None,
                            "reply_to": None, "date": None,
                        })()
                return rows()

        client = Client()
        with mock.patch.object(history_scan, "freeze_attested_corpus",
                               return_value={"sha256": "digest", "count": 1}) as freeze:
            result = self._invoke(
                {"freeze": True, "ceiling_id": "latest", "eligible_limit": 1}, client)
        self.assertIn('"sha256": "digest"', result)
        self.assertEqual(client.calls[0], (1, None))
        self.assertNotIn((100, 2147483648), client.calls)
        discovery = freeze.call_args.args[0]
        self.assertEqual(discovery.range.ceiling_id, 77)

    def test_latest_freeze_refuses_empty_history_without_freeze(self):
        class EmptyClient:
            def iter_messages(self, entity, *, limit, offset_id=None):
                async def rows():
                    if False:
                        yield None
                return rows()

        with mock.patch.object(history_scan, "freeze_attested_corpus") as freeze:
            result = self._invoke(
                {"freeze": True, "ceiling_id": "latest", "eligible_limit": 1}, EmptyClient())
        self.assertEqual(result, "history_scan: newest ceiling lookup found no messages")
        freeze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
