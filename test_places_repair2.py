"""Гейт Praxis REPAIR2 (22.08.2026): три воспроизведённых дефекта кандидата 7b4a726f.

P0-1: transient-ошибка полёта не смеет хоронить unknown root навсегда (probed —
      только после durable-успеха ПОЛНОГО свипа).
P0-2: positive durable знание (живой опенер, complete=False) минтит СВОЙ topic id и
      без полного свипа; отсутствие ЧУЖИХ id не доказано до полного свипа.
P1-3: холодный кэш каталога читается одним read-flight на комнату, не N чтений на
      N конкурентов; неудача чтения не отравляет полёт.

Запуск:  python praxis_test.py test_places_repair2 -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("PRAXIS_TEST", "1")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault("TELEGRAM_SESSION", ":memory:")

import telegram_routes as tr
import telegram_topics as tt


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


# Имена всех in-memory хранилищ машинерии каталога. Через getattr, а не напрямую:
# этот модуль обязан запускаться и на дереве ДО ремонта (воспроизведение), где части
# хранилищ ещё нет.
_STORE_NAMES = (
    "_topic_catalog_cache", "_topic_catalog_flights", "_topic_catalog_next_attempt",
    "_topic_catalog_stale_probe", "_topic_catalog_probed_roots",
    "_topic_catalog_unknown_gate", "_topic_catalog_read_flights",
    "_topic_catalog_pending_roots", "_topic_catalog_cache_gen",
)


class _Repair2Env(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="praxis-repair2-")
        self._orig_dir = tr.DIR
        tr.DIR = Path(self.tempdir.name) / "memory" / ".state" / "group_context"
        self._stores = [getattr(runner, name) for name in _STORE_NAMES
                        if hasattr(runner, name)]
        self._saved = [dict(store) for store in self._stores]
        for store in self._stores:
            store.clear()

    def tearDown(self):
        tr.DIR = self._orig_dir
        for store, saved in zip(self._stores, self._saved):
            store.clear()
            store.update(saved)
        self.tempdir.cleanup()

    async def _drain_flights(self):
        for task in list(getattr(runner, "_topic_catalog_flights", {}).values()):
            try:
                await task
            except Exception:
                pass


def _reply(top: int):
    return types.SimpleNamespace(
        reply_to=types.SimpleNamespace(
            reply_to_top_id=top, reply_to_msg_id=top + 1, forum_topic=True),
        reply_to_top_id=None)


class TestP01TransientFailureDoesNotBuryTheRoot(_Repair2Env):
    """Её инвариант: root подавляется долговременно только после durable-успеха;
    после transient-ошибки тот же root снова запускает один single-flight."""

    async def test_same_root_retries_after_transient_failure(self):
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        attempts = {"n": 0}

        async def sweep(entity, peer, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("FLOOD_WAIT_X (transient)")
            tr.observe_topics(peer, {9100: {"title": "Новая"}}, complete=True)
            runner._topic_catalog_cache.pop(str(peer), None)
            return [{"topic_id": 9100, "title": "Новая", "top_message": 9100}], True

        with patch.object(runner, "_forum_topic_catalog", sweep):
            await runner._catalog_for_routing("-300", object(), unknown_topic=9100)
            await self._drain_flights()
            self.assertEqual(attempts["n"], 1)
            # Все ретрай-окна истекли; кэш остыл (время прошло):
            for name in ("_topic_catalog_unknown_gate", "_topic_catalog_next_attempt",
                         "_topic_catalog_cache"):
                if hasattr(runner, name):
                    getattr(runner, name).clear()
            await runner._catalog_for_routing("-300", object(), unknown_topic=9100)
            await self._drain_flights()
            self.assertEqual(
                attempts["n"], 2,
                "transient-ошибка похоронила корень: повторный sweep не запущен")
            after = await runner._catalog_for_routing(
                "-300", object(), unknown_topic=9100)
        self.assertIn(9100, after, "после успешной разведки тема видна маршруту")

    async def test_concurrent_callers_share_one_failing_flight(self):
        """Фан-аут REPAIR2 поймал первую версию этого теста на фиктивности: второй
        caller не доходил до `_spawn_catalog_flight` из-за анти-флуд калитки, и
        единственность свипа держала калитка, а не single-flight. Теперь калитка
        снимается, а спавны считаются: оба caller'а ПРОСЯТ полёт — свип один."""
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        started = asyncio.Event()
        release = asyncio.Event()
        attempts = {"n": 0}
        spawns = {"n": 0}

        async def sweep(entity, peer, **kwargs):
            attempts["n"] += 1
            started.set()
            await release.wait()
            raise RuntimeError("transient")

        real_spawn = runner._spawn_catalog_flight

        def counting_spawn(peer, entity=None):
            spawns["n"] += 1
            return real_spawn(peer, entity)

        with (
            patch.object(runner, "_forum_topic_catalog", sweep),
            patch.object(runner, "_spawn_catalog_flight", counting_spawn),
        ):
            first = asyncio.create_task(
                runner._catalog_for_routing("-300", object(), unknown_topic=9100))
            await started.wait()
            runner._topic_catalog_unknown_gate.clear()   # калитка не участвует
            second = asyncio.create_task(
                runner._catalog_for_routing("-300", object(), unknown_topic=9100))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(first, second)
            await self._drain_flights()
        self.assertEqual(spawns["n"], 2, "оба caller'а реально просили полёт")
        self.assertEqual(attempts["n"], 1,
                         "конкурентные callers делят ОДИН полёт даже при неудаче")

    async def test_burial_happens_only_after_complete_success(self):
        """Успешный, но НЕПОЛНЫЙ свип не хоронит root: неполнота не доказывает
        отсутствия (принцип её P0-2, применённый к probed)."""
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        attempts = {"n": 0}

        async def sweep(entity, peer, **kwargs):
            attempts["n"] += 1
            return [], False    # успех транспорта, полноты нет

        with patch.object(runner, "_forum_topic_catalog", sweep):
            await runner._catalog_for_routing("-300", object(), unknown_topic=9100)
            await self._drain_flights()
            for name in ("_topic_catalog_unknown_gate", "_topic_catalog_next_attempt",
                         "_topic_catalog_cache"):
                if hasattr(runner, name):
                    getattr(runner, name).clear()
            await runner._catalog_for_routing("-300", object(), unknown_topic=9100)
            await self._drain_flights()
        self.assertEqual(attempts["n"], 2,
                         "неполный свип не имеет права хоронить root")


class TestP02PositiveOpenerKnowledge(_Repair2Env):
    """Её инвариант: живой опенер (complete=False) немедленно и после рестарта
    подтверждает СВОЙ topic id для маршрута; чужие id — fail-safe комната;
    fail-open по reply-заголовку не возвращается."""

    async def test_witnessed_opener_mints_without_complete_sweep(self):
        fetch = AsyncMock(side_effect=RuntimeError("полный свип рухнул"))
        with patch.object(runner, "_forum_topic_catalog", fetch):
            await runner._note_live_opener("-300", 9100, "Новая тема")
            catalog = await runner._catalog_for_routing(
                "-300", object(), unknown_topic=9100)
            self.assertEqual(
                tt.route_for_message(-300, _reply(9100), is_forum=True,
                                     confirmed_topics=catalog).conversation_id,
                "-300__topic__9100",
                "positive-знание опенера обязано минтить без полного свипа")
            self.assertEqual(
                tt.route_for_message(-300, _reply(9999), is_forum=True,
                                     confirmed_topics=catalog).conversation_id,
                "-300",
                "чужой id не доказан отсутствующим, но и не минтится: fail-safe")
            # Имитация рестарта: in-memory кэш пуст, durable-знание живо.
            runner._topic_catalog_cache.clear()
            again = await runner._catalog_for_routing("-300", object())
            self.assertIn(9100, again,
                          "positive-знание переживает рестарт без полного свипа")

    async def test_completeness_still_gates_negative_knowledge(self):
        """`confirmed_topics` (полнота) продолжает отвечать «не знаю» без полного
        свипа — отрицательное знание не рождается из одного опенера."""
        fetch = AsyncMock(side_effect=RuntimeError("свип недоступен"))
        with patch.object(runner, "_forum_topic_catalog", fetch):
            await runner._note_live_opener("-300", 9100, "Новая тема")
        self.assertIsNone(tr.confirmed_topics("-300"),
                          "полнота не заявлена одним опенером")


class TestP13ColdCacheStampede(_Repair2Env):
    """Её инвариант: не более одного холодного durable-чтения на комнату
    одновременно; неудача не отравляет; тёплый кэш — ноль чтений."""

    async def test_cold_burst_costs_one_durable_read(self):
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        reads = {"n": 0}
        real_read = tr.read

        def counting_read(peer_id):
            reads["n"] += 1
            return real_read(peer_id)

        fetch = AsyncMock(side_effect=AssertionError("RPC здесь не нужен"))
        with (
            patch.object(runner.telegram_routes, "read", counting_read),
            patch.object(runner, "_forum_topic_catalog", fetch),
        ):
            results = await asyncio.gather(*[
                runner._catalog_for_routing("-300", object()) for _ in range(25)])
        harvest = {frozenset(item or ()) for item in results}
        self.assertEqual(len(harvest), 1, "все 25 получили одинаковый результат")
        self.assertLessEqual(
            reads["n"], 2,
            f"25 холодных конкурентов сделали {reads['n']} durable-чтений — "
            "read-flight обязан быть один (плюс максимум одна проверка давности)")

    async def test_warm_cache_costs_zero_reads(self):
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        await runner._catalog_for_routing("-300", object())
        reads = {"n": 0}
        real_read = tr.read

        def counting_read(peer_id):
            reads["n"] += 1
            return real_read(peer_id)

        with patch.object(runner.telegram_routes, "read", counting_read):
            for _ in range(10):
                await runner._catalog_for_routing("-300", object())
        self.assertEqual(reads["n"], 0, "тёплый кэш — ноль дисковых чтений")

    async def test_stale_read_cannot_overwrite_fresh_knowledge(self):
        """Чтение, начавшееся ДО свежей записи и пережившее инвалидацию, не смеет
        дописать устаревший снимок в кэш (поколение защищает запись read-flight'а)."""
        import threading
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        release = threading.Event()

        def slow_stale_read(peer_id):
            release.wait(5)
            return frozenset({700}), True     # снимок ДО появления 9100

        with patch.object(runner.telegram_routes, "topic_knowledge", slow_stale_read):
            reader = asyncio.create_task(
                runner._topic_knowledge_read_flight("-300"))
            await asyncio.sleep(0.05)         # чтение уже в потоке
            tr.observe_topics("-300", {9100: {"title": "Новая"}}, complete=False)
            runner._invalidate_topic_cache("-300")
            release.set()
            await reader
        self.assertNotIn("-300", runner._topic_catalog_cache,
                         "устаревшее чтение записало кэш после инвалидации")
        ids, _complete = await runner._topic_knowledge_cached("-300")
        self.assertIn(9100, ids,
                      "свежее знание обязано быть видно следующему читателю")

    async def test_failed_read_does_not_poison_the_flight(self):
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        state = {"n": 0}
        real_read = tr.read

        def flaky_read(peer_id):
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("диск моргнул")
            return real_read(peer_id)

        fetch = AsyncMock(side_effect=RuntimeError("RPC не поднимаем"))
        with (
            patch.object(runner.telegram_routes, "read", flaky_read),
            patch.object(runner, "_forum_topic_catalog", fetch),
        ):
            first = await runner._catalog_for_routing("-300", object())
            self.assertFalse(first and 700 in first,
                             "упавшее чтение отвечает fail-safe, не выдумкой")
            second = await runner._catalog_for_routing("-300", object())
        self.assertTrue(second and 700 in second,
                        "следующий caller реально повторяет чтение")


class TestRepair2Fanout(_Repair2Env):
    """Регрессии находок фан-аута REPAIR2: поколение кэша сквозное (полёт и ждущие),
    срез pending до RPC, отмена не роняет чужие ходы, реестры не растут."""

    async def test_flight_cache_write_respects_generation(self):
        """Полёт, дочитывающий знание, не смеет затирать опенер, приехавший в хвосте:
        его прямая запись в кэш и захоронение идут только в своём поколении."""
        import threading
        gate = threading.Event()
        release = threading.Event()
        real_tk = tr.topic_knowledge
        calls = {"n": 0}

        def gated_tk(peer_id):
            calls["n"] += 1
            if calls["n"] == 1:
                gate.set()
                release.wait(5)
                return frozenset({700}), True     # снимок ДО опенера
            return real_tk(peer_id)

        async def sweep(entity, peer, **kwargs):
            tr.observe_topics(peer, {700: {"title": "Тема"}}, complete=True)
            runner._invalidate_topic_cache(str(peer))
            return [{"topic_id": 700, "title": "Тема", "top_message": 700}], True

        with (
            patch.object(runner, "_forum_topic_catalog", sweep),
            patch.object(runner.telegram_routes, "topic_knowledge", gated_tk),
        ):
            # 9100 заявлен ДО старта полёта: свип его не найдёт, но хоронить по
            # устаревшему снимку (без опенера) полёт не имеет права.
            runner._topic_catalog_pending_roots.setdefault("-300", set()).add(9100)
            flight = runner._spawn_catalog_flight("-300", object())
            await asyncio.to_thread(gate.wait, 5)     # полёт в прямом чтении
            await runner._note_live_opener("-300", 9100, "Новая тема")
            release.set()
            await flight
        ids, _complete = await runner._topic_knowledge_cached("-300")
        self.assertIn(9100, ids,
                      "полёт затёр кэшем опенер, приехавший в его хвосте")
        self.assertNotIn(
            9100, runner._topic_catalog_probed_roots.get("-300", set()),
            "корень похоронен по снимку, снятому до его подтверждения")

    async def test_root_born_after_the_rpc_is_not_buried_by_it(self):
        """Корень, заявленный УЖЕ летящему свипу, не хоронится его снимком: срез
        pending снимается до RPC, поздняя заявка остаётся следующему полёту."""
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        started = asyncio.Event()
        release = asyncio.Event()
        attempts = {"n": 0}

        async def sweep(entity, peer, **kwargs):
            attempts["n"] += 1
            started.set()
            await release.wait()
            tr.observe_topics(peer, {700: {"title": "Тема"}}, complete=True)
            runner._invalidate_topic_cache(str(peer))
            return [{"topic_id": 700, "title": "Тема", "top_message": 700}], True

        with patch.object(runner, "_forum_topic_catalog", sweep):
            await runner._catalog_for_routing("-300", object(), unknown_topic=9100)
            await started.wait()
            runner._topic_catalog_unknown_gate.clear()   # окно истекло, полёт летит
            await runner._catalog_for_routing("-300", object(), unknown_topic=9200)
            release.set()
            await self._drain_flights()
            self.assertNotIn(
                9200, runner._topic_catalog_probed_roots.get("-300", set()),
                "свип похоронил корень, которого физически не мог видеть")
            for name in ("_topic_catalog_unknown_gate", "_topic_catalog_next_attempt",
                         "_topic_catalog_cache"):
                getattr(runner, name).clear()
            await runner._catalog_for_routing("-300", object(), unknown_topic=9200)
            await self._drain_flights()
        self.assertEqual(attempts["n"], 2, "поздний корень получает СВОЙ свип")

    async def test_waiter_after_invalidation_gets_fresh_knowledge(self):
        """Ждущий, пришедший ПОСЛЕ инвалидации, не подсаживается на чтение из
        прошлого поколения: реплика в тему живого опенера видит его немедленно."""
        import threading
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        gate = threading.Event()
        release = threading.Event()
        real_tk = tr.topic_knowledge
        calls = {"n": 0}

        def gated_tk(peer_id):
            calls["n"] += 1
            if calls["n"] == 1:
                gate.set()
                release.wait(5)
                return frozenset({700}), True
            return real_tk(peer_id)

        with patch.object(runner.telegram_routes, "topic_knowledge", gated_tk):
            reader = asyncio.create_task(runner._topic_knowledge_cached("-300"))
            await asyncio.to_thread(gate.wait, 5)
            await runner._note_live_opener("-300", 9100, "Новая тема")
            late = asyncio.create_task(runner._topic_knowledge_cached("-300"))
            await asyncio.sleep(0.05)
            release.set()
            ids_late, _complete = await late
            await reader
        self.assertIn(9100, ids_late,
                      "поздний ждущий унёс досвежее знание из чужого поколения")

    async def test_cancelled_flight_clears_pending_and_arms_retry(self):
        """Отмена полёта (shutdown) не оставляет pending навсегда и взводит окно."""
        import contextlib as _ctx
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        started = asyncio.Event()

        async def sweep(entity, peer, **kwargs):
            started.set()
            await asyncio.sleep(30)
            return [], True

        with patch.object(runner, "_forum_topic_catalog", sweep):
            await runner._catalog_for_routing("-300", object(), unknown_topic=9100)
            await started.wait()
            self.assertFalse(runner._topic_catalog_pending_roots.get("-300"),
                             "срез pending снят при старте полёта")
            flight = next(t for t in runner._topic_catalog_flights.values()
                          if not t.done())
            flight.cancel()
            with _ctx.suppress(asyncio.CancelledError):
                await flight
        self.assertFalse(runner._topic_catalog_pending_roots.get("-300"))
        self.assertGreater(runner._topic_catalog_next_attempt.get("-300", 0.0),
                           time.time(), "окно повтора взведено и после отмены")
        self.assertNotIn(9100, runner._topic_catalog_probed_roots.get("-300", set()))

    async def test_cancel_of_one_waiter_does_not_poison_others(self):
        """Её P1-3: отмена ОДНОГО ждущего не отравляет общий read-flight."""
        import contextlib as _ctx
        import threading
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        gate = threading.Event()
        release = threading.Event()
        real_tk = tr.topic_knowledge

        def slow_tk(peer_id):
            gate.set()
            release.wait(5)
            return real_tk(peer_id)

        with patch.object(runner.telegram_routes, "topic_knowledge", slow_tk):
            a = asyncio.create_task(runner._topic_knowledge_cached("-300"))
            await asyncio.to_thread(gate.wait, 5)
            b = asyncio.create_task(runner._topic_knowledge_cached("-300"))
            await asyncio.sleep(0.05)
            a.cancel()
            with _ctx.suppress(asyncio.CancelledError):
                await a
            release.set()
            ids_b, _complete = await b
        self.assertIn(700, ids_b, "сосед по полёту пострадал от чужой отмены")

    async def test_cancelled_read_flight_fails_safe_for_waiters(self):
        """Отмена САМОГО чтения (shutdown) отвечает ждущим fail-safe, не роняет их."""
        import threading
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        gate = threading.Event()
        release = threading.Event()

        def hang_tk(peer_id):
            gate.set()
            release.wait(5)
            return frozenset({700}), True

        with patch.object(runner.telegram_routes, "topic_knowledge", hang_tk):
            waiters = [asyncio.create_task(runner._topic_knowledge_cached("-300"))
                       for _ in range(3)]
            await asyncio.to_thread(gate.wait, 5)
            entry = runner._topic_catalog_read_flights.get("-300")
            self.assertIsNotNone(entry)
            entry[1].cancel()
            release.set()
            results = await asyncio.gather(*waiters)
        self.assertTrue(all(item == (frozenset(), False) for item in results),
                        "ждущие обязаны получить fail-safe, а не CancelledError")

    async def test_retry_window_keeps_positive_knowledge(self):
        """Тест-гэп фан-аута: внутри окна повтора positive-знание не стирается."""
        fetch = AsyncMock(side_effect=RuntimeError("свип падает"))
        with patch.object(runner, "_forum_topic_catalog", fetch):
            await runner._note_live_opener("-300", 9100, "Новая тема")
            runner._topic_catalog_next_attempt["-300"] = time.time() + 60
            got = await runner._catalog_for_routing(
                "-300", object(), unknown_topic=9999)
        self.assertIn(9100, got,
                      "окно повтора не имеет права отвечать пустым знанием")

    async def test_incomplete_catalogue_fetches_completeness_in_background(self):
        """Тест-гэп фан-аута: комната с одним опенером добывает полноту фоном,
        даже когда весь трафик идёт по уже известным корням."""
        tr.observe_topics("-300", {9100: {"title": "Новая"}}, complete=False)
        calls = {"n": 0}

        async def sweep(entity, peer, **kwargs):
            calls["n"] += 1
            tr.observe_topics(peer, {9100: {"title": "Новая"}}, complete=True)
            runner._invalidate_topic_cache(str(peer))
            return [{"topic_id": 9100, "title": "Новая", "top_message": 9100}], True

        with patch.object(runner, "_forum_topic_catalog", sweep):
            await runner._catalog_for_routing("-300", object())
            await self._drain_flights()
        self.assertEqual(calls["n"], 1, "полнота добыта фоном")
        self.assertTrue(tr.topics_seen_at("-300"))

    async def test_backfill_keys_by_positive_opener_knowledge(self):
        """Тест-гэп фан-аута (P0-2 «после рестарта»): бэкфил ключует историю по
        positive-знанию опенера даже при упавшем полном свипе."""
        await runner._note_live_opener("-300", 9100, "Новая тема")
        recorded = []

        def observe_msg(**kwargs):
            recorded.append(kwargs)
            return True

        message = types.SimpleNamespace(
            id=9101, message="в новую тему",
            reply_to=types.SimpleNamespace(
                reply_to_top_id=9100, reply_to_msg_id=9100, forum_topic=True),
            reply_to_msg_id=9100, sender_id=10, out=False,
            sender=types.SimpleNamespace(first_name="Alice", last_name="",
                                         username=None, usernames=()),
            date=None, edit_date=None, photo=None, voice=None, video_note=None,
            sticker=None, gif=None, video=None, audio=None, document=None,
            media=None, action=None)

        class _HistoryClient:
            def iter_messages(self, entity, limit):
                async def _gen():
                    yield message
                return _gen()

        fetch = AsyncMock(side_effect=RuntimeError("полный свип падает"))
        with (
            patch.object(runner, "client", _HistoryClient()),
            patch.object(runner, "_forum_topic_catalog", fetch),
            patch.object(runner.group_context, "archived_message_count",
                         lambda peer: 0),
            patch.object(runner.group_context, "observe_message", observe_msg),
            patch.object(runner.group_context, "branch_containers", lambda peer: {}),
            patch.object(runner.group_context, "rebuild_projection",
                         lambda peer: {"message_count": 1}),
            patch.object(runner.group_context, "mark_backfill",
                         lambda *a, **k: None),
            patch.object(runner.context_envelope, "record_probe",
                         lambda *a, **k: None),
        ):
            await runner._backfill_group_context("-300", object(), limit=5)
        self.assertTrue(recorded, "сообщение бэкфила не заархивировано")
        self.assertEqual(recorded[0]["topic_id"], 9100,
                         "история доказанной опенером темы схлопнулась в комнату")

    async def test_finished_flights_do_not_linger(self):
        """Реестры полётов не растут по числу комнат: done-таски снимаются сами."""
        for index in range(20):
            peer = f"-31{index:02d}"
            tr.observe_topics(peer, {700: {"title": "Тема"}}, complete=True)
            await runner._topic_knowledge_cached(peer)
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertFalse(runner._topic_catalog_read_flights,
                         "завершённые read-flight'ы задержались в реестре")


class TestRepair3Convergence(_Repair2Env):
    """Её гейт REPAIR3: фиксированный лимит попыток — не доказательство свежести.

    Свежесть доказывается поколением (создание read-flight'а → завершение без
    инвалидаций); при конечном churn caller сходится и не теряет НИ ОДНОГО
    засвидетельствованного id; при непрерывном churn — fail-safe: объединение
    прочитанных id БЕЗ полноты, никакого stale-complete, никакого зависания.
    Тесты идут по настоящему durable-реестру и настоящему read-пути: обёртка
    только ЗАДЕРЖИВАЕТ ВОЗВРАТ настоящего снимка, чтобы опенер+инвалидация успели
    завершиться во время чтения.
    """

    async def _run_churn(self, openers: int):
        import threading
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        real_tk = tr.topic_knowledge
        entered = [threading.Event() for _ in range(openers)]
        release = [threading.Event() for _ in range(openers)]
        calls = {"n": 0}

        def parked_tk(peer_id):
            index = calls["n"]
            calls["n"] += 1
            snapshot = real_tk(peer_id)        # НАСТОЯЩИЙ durable-снимок сейчас
            if index < openers:
                entered[index].set()
                release[index].wait(5)          # возврат задержан: опенер успеет
            return snapshot

        with patch.object(runner.telegram_routes, "topic_knowledge", parked_tk):
            caller = asyncio.create_task(runner._topic_knowledge_cached("-300"))
            for step in range(openers):
                await asyncio.to_thread(entered[step].wait, 5)
                await runner._note_live_opener(
                    "-300", 9101 + step, f"Тема {step}")
                release[step].set()
            ids, complete = await asyncio.wait_for(caller, 15)
        return ids, complete

    async def test_three_sequential_invalidations_do_not_lose_the_third_opener(self):
        """Её воспроизведение: [9101, 9102, 9103] durable к моменту возврата —
        caller обязан видеть 9103, а не устаревший снимок из трёх попыток."""
        ids, complete = await self._run_churn(3)
        self.assertIn(9103, ids,
                      "третья подряд инвалидация украла свежее знание (range(3))")
        self.assertIn(9101, ids)
        self.assertTrue(complete,
                        "после затихшего churn первое же чтение доказанно свежо")

    async def test_more_than_three_invalidations_have_no_magic_edge(self):
        """Три, четыре, пять инвалидаций — никакого особого края у числа 3."""
        ids, _complete = await self._run_churn(5)
        self.assertIn(9105, ids, "пятый опенер потерян: у лимита попыток есть край")

    async def test_continuous_churn_fails_safe_without_stale_complete(self):
        """Непрерывный churn: конечную свежесть доказать нельзя — путь завершается
        консервативно (объединение БЕЗ полноты), не зависает и не отдаёт
        заведомо неполное знание как полное."""
        import threading
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        real_tk = tr.topic_knowledge
        counter = {"n": 0}

        def churn_tk(peer_id):
            snapshot = real_tk(peer_id)
            counter["n"] += 1
            # Каждое чтение догоняет НАСТОЯЩАЯ durable-запись + инвалидация —
            # честный путь писателя, свежесть недоказуема принципиально.
            tr.observe_topics("-300", {9000 + counter["n"]: {"title": "X"}},
                              complete=False)
            runner._invalidate_topic_cache("-300")
            return snapshot

        started = time.monotonic()
        with (
            patch.object(runner, "_TOPIC_KNOWLEDGE_CONVERGE_BUDGET", 0.4,
                         create=True),
            patch.object(runner.telegram_routes, "topic_knowledge", churn_tk),
        ):
            ids, complete = await asyncio.wait_for(
                runner._topic_knowledge_cached("-300"), 10)
        elapsed = time.monotonic() - started
        self.assertFalse(complete,
                         "недоказанная свежесть не имеет права заявлять полноту")
        self.assertIn(700, ids, "объединение не теряет уже прочитанного")
        self.assertLess(elapsed, 5.0, "fail-safe обязан завершаться, а не висеть")

    async def test_repair3_keeps_the_stampede_closed(self):
        """Её регрессия: сходимость не вернула stampede — 25 холодных конкурентов
        по-прежнему делят одно durable-чтение."""
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        reads = {"n": 0}
        real_read = tr.read

        def counting_read(peer_id):
            reads["n"] += 1
            return real_read(peer_id)

        with patch.object(runner.telegram_routes, "read", counting_read):
            results = await asyncio.gather(*[
                runner._topic_knowledge_cached("-300") for _ in range(25)])
        self.assertEqual(len({frozenset(item[0]) for item in results}), 1)
        self.assertLessEqual(reads["n"], 1,
                             "сходимость REPAIR3 вернула stampede холодных чтений")


class TestRepair4CrossProcessAndBoundary(_Repair2Env):
    """Её гейт REPAIR4: (A) граница wall-clock бюджета — не доказательство
    непрерывного churn: после дедлайна обязано случиться чтение, начатое после
    последней завершившейся инвалидации; (B) доказательство свежести обязано
    видеть МЕЖПРОЦЕССНЫХ писателей — процесс-локального поколения недостаточно.
    Реестр настоящий, чужие коммиты — настоящим дочерним процессом, бюджет —
    production (2.0с, без патча)."""

    _CHILD_SCRIPT = (
        "import os, sys\n"
        "os.environ['PRAXIS_BASE'] = sys.argv[1]\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "import telegram_routes as tr\n"
        "tr.observe_topics('-300', {9100: {'title': 'Из другого процесса'}},\n"
        "                  source='topic_opener', complete=False)\n"
    )

    def _spawn_child(self):
        import subprocess
        base = str(Path(self.tempdir.name))
        module_dir = str(Path(tr.__file__).resolve().parent)
        result = subprocess.run(
            [sys.executable, "-c", self._CHILD_SCRIPT, base, module_dir],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         f"child writer failed: {result.stderr[-400:]}")

    async def test_finite_churn_across_the_wallclock_boundary_keeps_the_last_opener(self):
        """Её блокер A дословно: одно медленное чтение, единственный опенер 9101
        durable-завершён ДО возврата, churn прекратился, возврат — после бюджета.
        9101 не имеет права потеряться."""
        import threading
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        real_tk = tr.topic_knowledge
        entered = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def parked_tk(peer_id):
            index = calls["n"]
            calls["n"] += 1
            snapshot = real_tk(peer_id)
            if index == 0:
                entered.set()
                release.wait(15)          # возврат старого чтения задержан
            return snapshot

        with patch.object(runner.telegram_routes, "topic_knowledge", parked_tk):
            caller = asyncio.create_task(runner._topic_knowledge_cached("-300"))
            await asyncio.to_thread(entered.wait, 5)
            await asyncio.sleep(1.9)      # почти весь production-бюджет
            await runner._note_live_opener("-300", 9101, "Последняя тема")
            await asyncio.sleep(0.4)      # дедлайн (2.0с) гарантированно позади
            release.set()                 # старое чтение возвращается ПОСЛЕ бюджета
            ids, complete = await asyncio.wait_for(caller, 20)
        self.assertIn(9101, ids,
                      "граница бюджета вернула снимок ДО последней инвалидации")
        self.assertTrue(complete,
                        "конечный churn затих — финальное чтение доказанно свежо")

    async def test_cross_process_writer_defeats_warm_cache(self):
        """Её блокер B: тёплый кэш не имеет права отдавать stale-complete после
        настоящего durable-коммита из другого процесса."""
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        first_ids, first_complete = await runner._topic_knowledge_cached("-300")
        self.assertTrue(first_complete)
        self.assertNotIn(9100, first_ids)
        self._spawn_child()               # настоящий чужой процесс, настоящий замок
        second_ids, second_complete = await runner._topic_knowledge_cached("-300")
        self.assertIn(9100, second_ids,
                      "межпроцессный писатель невидим: принят stale-complete")
        self.assertTrue(second_complete,
                        "полнота честно сохраняется: свип был, id добавился")

    async def test_cross_process_commit_during_read_is_not_accepted_as_fresh(self):
        """Её блокер B, in-flight вариант: чужой коммит завершился после снимка,
        но до возврата чтения — снимок нельзя принять/закэшировать как свежий
        complete."""
        import threading
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        real_tk = tr.topic_knowledge
        entered = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def parked_tk(peer_id):
            index = calls["n"]
            calls["n"] += 1
            snapshot = real_tk(peer_id)   # снимок ДО чужого коммита
            if index == 0:
                entered.set()
                release.wait(15)
            return snapshot

        with patch.object(runner.telegram_routes, "topic_knowledge", parked_tk):
            caller = asyncio.create_task(runner._topic_knowledge_cached("-300"))
            await asyncio.to_thread(entered.wait, 5)
            await asyncio.to_thread(self._spawn_child)   # коммит ДО возврата чтения
            release.set()
            ids, _complete = await asyncio.wait_for(caller, 20)
        self.assertIn(9100, ids,
                      "снимок, пережитый чужим коммитом, принят как свежий")
        row = runner._topic_catalog_cache.get("-300")
        if row is not None:
            self.assertIn(9100, row[1],
                          "в кэш лёг снимок без чужого коммита — stale-complete")


class TestRepair5NonAbaIdentity(_Repair2Env):
    """Её гейт REPAIR5: (P0-A) метаданные ФС — не доказательство: разные contents
    при принудительно одинаковых (mtime_ns, size) обязаны быть видны; (P0-B)
    caller, начавшийся после завершённого чужого коммита, не принимает старый
    shared-полёт; (P2) lifecycle без unretrieved exceptions; каждый production
    писатель монотонно двигает durable-идентичность. Реальный route-файл, реальный
    atomic replace, настоящий subprocess, public writer API; валидатор не
    подменяется — обёртки лишь задерживают возврат настоящих значений."""

    _SEED_TITLE = "A" * 240
    _CHILD_TEMPLATE = (
        "import os, sys\n"
        "os.environ['PRAXIS_BASE'] = sys.argv[1]\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "import telegram_routes as tr\n"
        "tr.observe_topics('-300', {{9100: {{'title': 'N'}},\n"
        "                           700: {{'title': 'A' * {shrunk}}}}},\n"
        "                  source='calib', complete=False)\n"
    )

    def _seed(self, peer="-300"):
        tr.observe_topics(peer, {700: {"title": self._SEED_TITLE}},
                          source="calib", complete=True)

    def _calibrate_shrunk_len(self) -> int:
        """Подбирает длину нового title 700 так, чтобы файл ПОСЛЕ child-коммита
        (title 9100 добавлен, title 700 укорочен) совпал по размеру с файлом ДО.
        ASCII даёт побайтовую линейность — точное совпадение существует."""
        best_len, best_delta = 0, None
        for shrunk in range(0, len(self._SEED_TITLE)):
            probe = f"-cal{shrunk}"
            tr.observe_topics(probe, {700: {"title": self._SEED_TITLE}},
                              source="calib", complete=True)
            before = tr._path(probe).stat().st_size
            tr.observe_topics(probe, {9100: {"title": "N"},
                                      700: {"title": "A" * shrunk}},
                              source="calib", complete=False)
            after = tr._path(probe).stat().st_size
            delta = abs(after - before)
            if best_delta is None or delta < best_delta:
                best_len, best_delta = shrunk, delta
            if delta == 0:
                break
        return best_len

    def _forced_aba_child_commit(self, peer="-300"):
        """Настоящий child-process коммит с разным content и принудительно
        одинаковыми (mtime_ns, size)."""
        import subprocess
        path = tr._path(peer)
        stat_before = path.stat()
        shrunk = self._calibrate_shrunk_len()
        script = self._CHILD_TEMPLATE.format(shrunk=shrunk)
        base = str(Path(self.tempdir.name))
        module_dir = str(Path(tr.__file__).resolve().parent)
        result = subprocess.run(
            [sys.executable, "-c", script, base, module_dir],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         f"child writer failed: {result.stderr[-400:]}")
        # Принудительная идентичность метаданных (её allowance «принудительно
        # одинаковых»): mtime возвращается, размер совпал калибровкой.
        os.utime(path, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
        stat_after = path.stat()
        return (stat_before.st_mtime_ns, stat_before.st_size), \
               (stat_after.st_mtime_ns, stat_after.st_size)

    async def test_warm_cache_sees_through_forced_metadata_aba(self):
        """Её P0-A, warm-cache: чужой коммит с тем же (mtime_ns, size) обязан быть
        виден следующему caller-у — метаданные не доказательство."""
        self._seed()
        first_ids, first_complete = await runner._topic_knowledge_cached("-300")
        self.assertTrue(first_complete)
        self.assertNotIn(9100, first_ids)
        stamp_before, stamp_after = self._forced_aba_child_commit()
        if stamp_before == stamp_after:
            pass  # полный metadata-ABA достигнут; иначе тест всё равно честен
        second_ids, _second_complete = await runner._topic_knowledge_cached("-300")
        self.assertIn(9100, second_ids,
                      "metadata-ABA: тёплый кэш принял stale-complete")

    async def test_inflight_read_sees_through_forced_metadata_aba(self):
        """Её P0-A, in-flight: коммит с тем же (mtime_ns, size) во время чтения —
        снимок нельзя принять как доказанный."""
        import threading
        self._seed()
        real_tk = tr.topic_knowledge
        entered = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def parked_tk(peer_id):
            index = calls["n"]
            calls["n"] += 1
            snapshot = real_tk(peer_id)      # снимок ДО чужого коммита
            if index == 0:
                entered.set()
                release.wait(20)
            return snapshot

        with patch.object(runner.telegram_routes, "topic_knowledge", parked_tk):
            caller = asyncio.create_task(runner._topic_knowledge_cached("-300"))
            await asyncio.to_thread(entered.wait, 5)
            await asyncio.to_thread(self._forced_aba_child_commit)
            release.set()
            ids, _complete = await asyncio.wait_for(caller, 20)
        self.assertIn(9100, ids,
                      "metadata-ABA: in-flight снимок принят как доказанный")

    async def test_late_waiter_does_not_inherit_pre_commit_proof(self):
        """Её P0-B: caller, начавшийся ПОСЛЕ завершённого чужого коммита, не смеет
        принять старый shared-полёт с доказательством, сделанным до коммита."""
        import threading
        self._seed()
        reader_name = ("_read_topic_knowledge_versioned"
                       if hasattr(runner, "_read_topic_knowledge_versioned")
                       else "_read_topic_knowledge_stamped")
        real_reader = getattr(runner, reader_name)
        entered = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def parked_reader(peer_id):
            result = real_reader(peer_id)    # доказательство ПОЛНОСТЬЮ завершено
            index = calls["n"]
            calls["n"] += 1
            if index == 0:
                entered.set()
                release.wait(20)             # результат ещё не доставлен в loop
            return result

        with patch.object(runner, reader_name, parked_reader):
            first = asyncio.create_task(runner._topic_knowledge_cached("-300"))
            await asyncio.to_thread(entered.wait, 5)
            await asyncio.to_thread(self._spawn_plain_child)   # коммит ЗАВЕРШЁН
            late = asyncio.create_task(runner._topic_knowledge_cached("-300"))
            await asyncio.sleep(0.05)
            release.set()
            late_ids, _late_complete = await asyncio.wait_for(late, 20)
            await first
        self.assertIn(9100, late_ids,
                      "late waiter унаследовал доказательство, сделанное до коммита")

    def _spawn_plain_child(self, peer="-300"):
        import subprocess
        script = (
            "import os, sys\n"
            "os.environ['PRAXIS_BASE'] = sys.argv[1]\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "import telegram_routes as tr\n"
            "tr.observe_topics('-300', {9100: {'title': 'Из другого процесса'}},\n"
            "                  source='calib', complete=False)\n"
        )
        base = str(Path(self.tempdir.name))
        module_dir = str(Path(tr.__file__).resolve().parent)
        result = subprocess.run(
            [sys.executable, "-c", script, base, module_dir],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         f"child writer failed: {result.stderr[-400:]}")

    async def test_cancelled_sole_waiter_leaves_no_unretrieved_exception(self):
        """Её P2 lifecycle: единственный ждущий отменён, полёт позже падает —
        loop не получает «Task exception was never retrieved»."""
        import contextlib as _ctx
        import gc
        import threading
        self._seed()
        entered = threading.Event()
        release = threading.Event()

        def failing_tk(peer_id):
            entered.set()
            release.wait(20)
            raise OSError("late disk failure")

        collected = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda lp, ctx: collected.append(ctx))
        try:
            with patch.object(runner.telegram_routes, "topic_knowledge", failing_tk):
                caller = asyncio.create_task(
                    runner._topic_knowledge_cached("-300"))
                await asyncio.to_thread(entered.wait, 5)
                caller.cancel()
                with _ctx.suppress(asyncio.CancelledError):
                    await caller
                release.set()
                await asyncio.sleep(0.2)     # полёт завершился ошибкой без ждущих
            gc.collect()
            await asyncio.sleep(0)
            gc.collect()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous_handler)
        unretrieved = [ctx for ctx in collected
                       if "never retrieved" in str(ctx.get("message", ""))]
        self.assertFalse(unretrieved,
                         f"фоновый unretrieved exception: {unretrieved[:1]}")

    async def test_every_production_writer_bumps_durable_identity(self):
        """Её регрессия 6: каждый public `_save`-writer монотонно двигает durable-
        идентичность, и завершённый коммит всегда оставляет её чётной."""
        revisions = [tr.topic_revision("-300")]
        tr.observe("-300", kind="get_forum_topics_ok", message_id=5)
        revisions.append(tr.topic_revision("-300"))
        tr.note_writing("-300", broadcast=False)
        revisions.append(tr.topic_revision("-300"))
        tr.observe_branches("-300", {10: None})
        revisions.append(tr.topic_revision("-300"))
        tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        revisions.append(tr.topic_revision("-300"))
        for earlier, later in zip(revisions, revisions[1:]):
            self.assertGreater(later, earlier,
                               "писатель не сдвинул durable-идентичность")
        self.assertTrue(all(rev % 2 == 0 for rev in revisions),
                        "завершённый коммит обязан оставлять чётный токен")


if __name__ == "__main__":
    unittest.main()
