"""
PASS 26 — восстановлен старый канон: Telethon ЗАКРЫТ на время её автономного окна.

Task/self runs execute in a worker thread while Telethon is intentionally disconnected; the
accumulated backlog arrives as one situation on reconnect. Единый single-flight замок
(_ONE_MIND) не даёт окну открыться поверх живого хода. Регрессию PASS 25 «Telethon continuously
online» этот файл больше не закрепляет — она снята.

Запуск:  python praxis_test.py test_runner_reconnect -v
"""
from __future__ import annotations

import asyncio
import threading
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "x")
os.environ.setdefault("TELEGRAM_SESSION", str(Path(tempfile.gettempdir()) / "praxis_test_reconnect"))

import mtproto_runner  # noqa: E402


class FakeTgClient:
    """Управляемый клиент: disconnect()/connect() двигают is_connected(); run_until_disconnected()
    возвращается сразу, когда кто-то дёрнул disconnect() (эмулирует реальный Telethon-квирк)."""

    def __init__(self):
        self.connected = True
        self._disconnect_event = asyncio.Event()
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.fail_connect = False

    def is_connected(self) -> bool:
        return self.connected

    async def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False
        self._disconnect_event.set()

    async def connect(self):
        self.connect_calls += 1
        if self.fail_connect:
            raise ConnectionError("reconnect отказал (тест)")
        self.connected = True

    async def run_until_disconnected(self):
        await self._disconnect_event.wait()
        self._disconnect_event.clear()  # готова снова ждать следующий disconnect


class Base(unittest.TestCase):
    def setUp(self):
        self._orig_client = mtproto_runner.client
        self._orig_expect = mtproto_runner._EXPECT_DISCONNECT
        self._orig_shutdown = mtproto_runner._SHUTDOWN
        self._orig_timeout = mtproto_runner.RECONNECT_TIMEOUT_SEC
        self._orig_one_mind = mtproto_runner._ONE_MIND
        self.fake = FakeTgClient()
        mtproto_runner.client = self.fake
        mtproto_runner._EXPECT_DISCONNECT = asyncio.Event()
        mtproto_runner._SHUTDOWN = asyncio.Event()
        mtproto_runner._ONE_MIND = mtproto_runner._OneMind()

    def tearDown(self):
        mtproto_runner.client = self._orig_client
        mtproto_runner._EXPECT_DISCONNECT = self._orig_expect
        mtproto_runner._SHUTDOWN = self._orig_shutdown
        mtproto_runner.RECONNECT_TIMEOUT_SEC = self._orig_timeout
        mtproto_runner._ONE_MIND = self._orig_one_mind


class TestTaskWindowClosesTelethon(Base):
    """Старый канон восстановлен: окно ЗАКРЫВАЕТ Telethon на время работы над собой и
    переподключается после — backlog приходит одной ситуацией; single-flight не даёт окну
    открыться поверх живого хода."""

    def setUp(self):
        super().setUp()
        self._orig_agent_task_window = mtproto_runner.agent.task_window

    def tearDown(self):
        mtproto_runner.agent.task_window = self._orig_agent_task_window
        super().tearDown()

    def test_task_window_closes_and_reopens_telethon_on_success(self):
        seen = {}

        def fake_agent_task_window(goal, *, mailbox_index=None, transport="", on_run=None):
            # во время работы Telethon ЗАКРЫТ и намерение помечено
            seen["connected"] = self.fake.is_connected()
            seen["flag"] = mtproto_runner._EXPECT_DISCONNECT.is_set()
            seen["transport"] = transport
            return ""

        mtproto_runner.agent.task_window = fake_agent_task_window
        ok = asyncio.run(mtproto_runner._task_window("цель"))

        self.assertTrue(ok)
        self.assertFalse(seen["connected"], "Telethon закрыт на время окна")
        # 26.07: рамке отдают ground truth, а не догадку сенсора — и здесь он «закрыт».
        # Тест шва: соврать соседнему органу нечем, disconnect либо был, либо нет.
        self.assertEqual(seen["transport"], "closed_for_window",
                         "рамка узнаёт о разрыве от того, кто его и сделал")
        self.assertTrue(seen["flag"], "_EXPECT_DISCONNECT помечен на время окна")
        self.assertTrue(self.fake.is_connected(), "после окна переподключилась")
        self.assertFalse(mtproto_runner._EXPECT_DISCONNECT.is_set(), "флаг снят после connect")
        self.assertEqual(self.fake.disconnect_calls, 1)
        self.assertEqual(self.fake.connect_calls, 1)

    def test_task_window_error_still_reconnects(self):
        def boom(goal, *, mailbox_index=None, transport="", on_run=None):
            raise RuntimeError("работа упала")

        mtproto_runner.agent.task_window = boom
        ok = asyncio.run(mtproto_runner._task_window("цель"))

        self.assertFalse(ok, "работа упала -- окно не исполнилось")
        # finally всё равно переподключает и снимает флаг: транспорт не остаётся в намеренном обрыве
        self.assertTrue(self.fake.is_connected())
        self.assertFalse(mtproto_runner._EXPECT_DISCONNECT.is_set())
        self.assertEqual(self.fake.disconnect_calls, 1)
        self.assertEqual(self.fake.connect_calls, 1)

    def test_window_defers_when_one_mind_busy(self):
        # single-flight: живой ход держит _ONE_MIND -> окно откладывается, транспорт не трогает
        called = {"work": False}

        def fake_agent_task_window(goal, *, mailbox_index=None, transport="", on_run=None):
            called["work"] = True
            return ""

        mtproto_runner.agent.task_window = fake_agent_task_window

        async def run():
            await mtproto_runner._ONE_MIND.acquire()  # эмулируем идущий живой ход
            try:
                return await mtproto_runner._task_window("цель")
            finally:
                mtproto_runner._ONE_MIND.release()

        outcome = asyncio.run(run())
        self.assertIsNone(outcome, "занята живым ходом -- окно отложено")
        self.assertFalse(called["work"], "работа окна не запускалась")
        self.assertEqual(self.fake.disconnect_calls, 0, "транспорт не тронут при отложенном окне")
        self.assertEqual(self.fake.connect_calls, 0)

    def test_task_window_holds_one_mind_during_work(self):
        # пока окно работает, _ONE_MIND занят -> второй когнитивный проход не побежит параллельно
        import time
        seen = {}

        def fake_agent_task_window(goal, *, mailbox_index=None, transport="", on_run=None):
            seen["locked_during"] = mtproto_runner._ONE_MIND.locked()
            time.sleep(0.02)
            return ""

        mtproto_runner.agent.task_window = fake_agent_task_window
        ok = asyncio.run(mtproto_runner._task_window("цель"))
        self.assertTrue(ok)
        self.assertTrue(seen["locked_during"], "окно держит single-flight замок во время работы")
        self.assertFalse(mtproto_runner._ONE_MIND.locked(), "после окна замок отпущен")

    def test_window_on_open_fires_before_disconnect_under_lock(self):
        # F3: on_open вызывается ровно при открытии окна — замок взят, disconnect ещё не случился.
        seen = {}

        async def on_open():
            seen["disconnect_at_open"] = self.fake.disconnect_calls
            seen["locked_at_open"] = mtproto_runner._ONE_MIND.locked()
            seen["called"] = True

        mtproto_runner.agent.task_window = lambda goal, *, mailbox_index=None, transport="", on_run=None: ""
        ok = asyncio.run(mtproto_runner._task_window("цель", on_open=on_open))
        self.assertTrue(ok)
        self.assertTrue(seen.get("called"), "on_open вызван при открытии окна")
        self.assertEqual(seen.get("disconnect_at_open"), 0, "on_open ДО disconnect")
        self.assertTrue(seen.get("locked_at_open"), "on_open под single-flight замком")

    def test_window_on_open_skipped_when_deferred(self):
        # F3: занят живым ходом -> окно отложено -> on_open НЕ вызывается (намерение не съедается).
        seen = {"called": False}

        async def on_open():
            seen["called"] = True

        async def run():
            await mtproto_runner._ONE_MIND.acquire()
            try:
                return await mtproto_runner._task_window("цель", on_open=on_open)
            finally:
                mtproto_runner._ONE_MIND.release()

        outcome = asyncio.run(run())
        self.assertIsNone(outcome, "занята живым ходом -> окно отложено")
        self.assertFalse(seen["called"], "отложенное окно НЕ метит намерение")

    def test_fire_due_deferred_window_survives_then_fires(self):
        # F3 сквозной: одноразовое focus/rest-намерение при занятом замке НЕ теряется, а на
        # свободном тике открывается и метится.
        import tasks as _tasks
        self.addCleanup(setattr, _tasks, "due", _tasks.due)
        self.addCleanup(setattr, _tasks, "mark_fired", _tasks.mark_fired)
        marked: list[str] = []
        claimed: list[str] = []
        self.addCleanup(setattr, _tasks, "claim_open", _tasks.claim_open)
        _tasks.due = lambda: [{"id": "w1", "kind": "window", "goal": "фокус"}]
        _tasks.mark_fired = lambda tid: marked.append(tid)
        _tasks.claim_open = lambda tid, kind="": claimed.append(tid) or True
        # 26.07: подделка окна ТЕПЕРЬ зовёт on_run — так делает настоящая, в тот миг, когда
        # durable run создан. Именно там намерение и гасится: раньше его гасили при взятии
        # замка, и всё, что между, могло сжечь его без следа (её находка номер один).
        mtproto_runner.agent.task_window = (
            lambda goal, *, mailbox_index=None, transport="", on_run=None:
            (on_run("run-тест") if on_run else None) or "")

        async def busy_tick():
            await mtproto_runner._ONE_MIND.acquire()  # живой ход держит замок
            try:
                await mtproto_runner._fire_due_tasks()
            finally:
                mtproto_runner._ONE_MIND.release()

        asyncio.run(busy_tick())
        self.assertEqual((claimed, marked), ([], []),
                         "замок занят -> окно отложено, намерение не тронуто вовсе")

        asyncio.run(mtproto_runner._fire_due_tasks())  # замок свободен -> окно открывается
        self.assertEqual(claimed, ["w1"], "взято в работу при открытии")
        self.assertEqual(marked, ["w1"], "и погашено, когда ран появился")


class TestDurableResumeSingleFlight(Base):
    """Fix A (F1/F2): durable resume runs the full model loop, so it must hold
    single-flight like _task_window/_reap_orphans_once — never beside a live pass,
    never visible to the reaper as an orphan.  Busy with a live pass -> skip; the
    45s clock retries and the run stays durably paused."""

    def setUp(self):
        super().setUp()
        self._orig_resume = mtproto_runner.agent.resume_durable_runs

    def tearDown(self):
        mtproto_runner.agent.resume_durable_runs = self._orig_resume
        super().tearDown()

    def test_durable_resume_holds_one_mind_during_work(self):
        seen = {}

        def fake_resume(limit=20):
            seen["locked_during"] = mtproto_runner._ONE_MIND.locked()
            return []

        mtproto_runner.agent.resume_durable_runs = fake_resume
        asyncio.run(mtproto_runner._durable_resume_once())
        self.assertTrue(seen.get("locked_during"),
                        "resume держит single-flight замок во время работы")
        self.assertFalse(mtproto_runner._ONE_MIND.locked(), "после resume замок отпущен")

    def test_durable_resume_defers_when_one_mind_busy(self):
        called = {"work": False}

        def fake_resume(limit=20):
            called["work"] = True
            return []

        mtproto_runner.agent.resume_durable_runs = fake_resume

        async def run():
            await mtproto_runner._ONE_MIND.acquire()  # эмулируем идущий живой ход
            try:
                await mtproto_runner._durable_resume_once()
            finally:
                mtproto_runner._ONE_MIND.release()

        asyncio.run(run())
        self.assertFalse(called["work"],
                         "занята живым ходом -> resume отложен, модель не бежит параллельно")


class TestControlOnceSetsShutdown(Base):
    def test_control_once_sets_shutdown_before_disconnect(self):
        import selfdev

        orig_restart_requested = selfdev.restart_requested
        orig_clear = selfdev.clear_restart_request
        orig_exit = mtproto_runner.agent._exit_process
        exited = []

        selfdev.restart_requested = lambda: "тест: смёржили предложение"
        selfdev.clear_restart_request = lambda: None
        mtproto_runner.agent._exit_process = lambda: exited.append(True)
        try:
            asyncio.run(mtproto_runner._control_once())
        finally:
            selfdev.restart_requested = orig_restart_requested
            selfdev.clear_restart_request = orig_clear
            mtproto_runner.agent._exit_process = orig_exit

        self.assertTrue(mtproto_runner._SHUTDOWN.is_set(),
                         "control_once умирает НАСОВСЕМ -- _SHUTDOWN, не _EXPECT_DISCONNECT")
        self.assertFalse(mtproto_runner._EXPECT_DISCONNECT.is_set())
        self.assertTrue(self.fake.disconnect_calls >= 1)
        self.assertTrue(exited, "_exit_process должен был вызваться")


class TestControlOnceExitsEvenIfDisconnectHangs(Base):
    """Живой случай 10.09: заявка контура исполнилась наполовину.

    `disconnect()` не вернулся, а `try/except` ловит исключение, но не зависание —
    до выхода дело не дошло. Заявка при этом УЖЕ погашена, поэтому следующий тик
    не повторит попытку никогда: процесс жив, Telegram отключён, надзор ждёт
    мёртвого. Итог — 2 ч 26 мин молчания и перезапуск руками.
    """

    def test_hanging_disconnect_still_ends_the_process(self):
        import selfdev

        orig_restart_requested = selfdev.restart_requested
        orig_clear = selfdev.clear_restart_request
        orig_exit = mtproto_runner.agent._exit_process
        orig_timeout = mtproto_runner._DISCONNECT_TIMEOUT
        orig_fuse = mtproto_runner._EXIT_FUSE_SECONDS
        exited = []

        async def _never_returns():
            await asyncio.sleep(3600)

        self.fake.disconnect = _never_returns
        selfdev.restart_requested = lambda: "тест: зависший disconnect"
        selfdev.clear_restart_request = lambda: None
        mtproto_runner.agent._exit_process = lambda: exited.append(True)
        mtproto_runner._DISCONNECT_TIMEOUT = 0.2
        mtproto_runner._EXIT_FUSE_SECONDS = 30.0     # запал не нужен: срок сработает раньше
        try:
            asyncio.run(mtproto_runner._control_once())
        finally:
            selfdev.restart_requested = orig_restart_requested
            selfdev.clear_restart_request = orig_clear
            mtproto_runner.agent._exit_process = orig_exit
            mtproto_runner._DISCONNECT_TIMEOUT = orig_timeout
            mtproto_runner._EXIT_FUSE_SECONDS = orig_fuse

        self.assertTrue(exited, "зависший disconnect не имеет права оставить процесс живым")
        self.assertTrue(mtproto_runner._SHUTDOWN.is_set())

    def test_fuse_ends_the_process_when_nothing_returns(self):
        """Вторая половина обещания: если не вернулся и сам путь, выходит запал."""
        orig_exit = mtproto_runner.agent._exit_process
        orig_fuse = mtproto_runner._EXIT_FUSE_SECONDS
        fired = threading.Event()
        mtproto_runner.agent._exit_process = fired.set
        mtproto_runner._EXIT_FUSE_SECONDS = 0.1
        try:
            timer = mtproto_runner._arm_restart_exit()
            self.assertTrue(fired.wait(5), "запал обязан сработать сам")
            timer.cancel()
        finally:
            mtproto_runner.agent._exit_process = orig_exit
            mtproto_runner._EXIT_FUSE_SECONDS = orig_fuse

    def test_fuse_is_cancellable_and_does_not_fire_afterwards(self):
        """⚠ Отменяемость — не украшение: неотменяемый запал звал бы настоящий
        `os._exit(42)` посреди чужого прогона, когда подмена стенда уже снята."""
        orig_exit = mtproto_runner.agent._exit_process
        orig_fuse = mtproto_runner._EXIT_FUSE_SECONDS
        fired = threading.Event()
        mtproto_runner.agent._exit_process = fired.set
        mtproto_runner._EXIT_FUSE_SECONDS = 0.3
        try:
            mtproto_runner._arm_restart_exit().cancel()
            self.assertFalse(fired.wait(1.0), "погашенный запал не стреляет")
        finally:
            mtproto_runner.agent._exit_process = orig_exit
            mtproto_runner._EXIT_FUSE_SECONDS = orig_fuse


class TestWaitReconnected(Base):
    def test_returns_true_once_flag_clears(self):
        mtproto_runner._EXPECT_DISCONNECT.set()

        async def _clear_soon():
            await asyncio.sleep(0.05)
            mtproto_runner._EXPECT_DISCONNECT.clear()

        async def _run():
            asyncio.get_event_loop().create_task(_clear_soon())
            return await mtproto_runner._wait_reconnected(timeout=2.0)

        self.assertTrue(asyncio.run(_run()))

    def test_returns_true_immediately_if_flag_never_set(self):
        self.assertTrue(asyncio.run(mtproto_runner._wait_reconnected(timeout=1.0)))

    def test_times_out_if_never_reconnects(self):
        self.fake.connected = False
        mtproto_runner._EXPECT_DISCONNECT.set()
        self.assertFalse(asyncio.run(mtproto_runner._wait_reconnected(timeout=0.2)))


class TestSuperviseConnection(Base):
    """Сценарии из спеки: обычный disconnect+reconnect не выходит; исключение до reconnect не
    выходит (finally всё равно реконнектит); необъявленный обрыв выходит как раньше."""

    def test_intentional_disconnect_survives_and_keeps_serving(self):
        # эмулируем _task_window целиком: помечаем намерение, дисконнектим, коннектимся, снимаем
        async def fake_window_cycle():
            await asyncio.sleep(0.02)
            mtproto_runner._EXPECT_DISCONNECT.set()
            await self.fake.disconnect()
            await asyncio.sleep(0.02)
            await self.fake.connect()
            mtproto_runner._EXPECT_DISCONNECT.clear()
            await asyncio.sleep(0.02)
            mtproto_runner._SHUTDOWN.set()  # закончить тест, иначе supervise живёт вечно
            await self.fake.disconnect()

        async def _run():
            task = asyncio.get_event_loop().create_task(fake_window_cycle())
            await asyncio.wait_for(mtproto_runner._supervise_connection(), timeout=5.0)
            await task

        asyncio.run(_run())
        self.assertEqual(self.fake.connect_calls, 1)
        self.assertEqual(self.fake.disconnect_calls, 2)

    def test_local_disconnect_without_flags_still_exits(self):
        """Голый `disconnect()` без флагов — это стоп-кран (/panic) или чужой внешний стоп,
        а НЕ обрыв сети: реконнектить его нельзя, иначе стоп-кран перестанет стопить.

        Раньше этот же сценарий назывался «настоящий обрыв» и пинил выход процесса КАК НОРМУ
        реакции на потерю сети. Форма обмана была в фейке: настоящий Telethon на потере сети
        не возвращается из `run_until_disconnected()`, а бросает ConnectionError (см.
        TestNetworkOutageIsSurvived). Имя поправлено, поведение оставлено — оно верное
        для того, что здесь реально смоделировано."""
        async def fake_stop_cock():
            await asyncio.sleep(0.02)
            await self.fake.disconnect()  # локальный стоп: ни один флаг не стоит

        async def _run():
            task = asyncio.get_event_loop().create_task(fake_stop_cock())
            out = await asyncio.wait_for(mtproto_runner._supervise_connection(), timeout=2.0)
            await task
            return out

        # не должно зависнуть -- supervise вышел сам и НЕ просит намеренного рестарта
        self.assertFalse(asyncio.run(_run()))

    def test_expect_disconnect_reconnect_never_arrives_exits_after_timeout(self):
        mtproto_runner.RECONNECT_TIMEOUT_SEC = 0.2

        async def fake_stuck_window():
            await asyncio.sleep(0.02)
            mtproto_runner._EXPECT_DISCONNECT.set()
            await self.fake.disconnect()
            # ...и никогда не переподключается (reconnect в finally тоже упал бы в реальности)

        async def _run():
            task = asyncio.get_event_loop().create_task(fake_stuck_window())
            await asyncio.wait_for(mtproto_runner._supervise_connection(), timeout=2.0)
            await task

        asyncio.run(_run())  # должно вернуться само по истечении RECONNECT_TIMEOUT_SEC, не зависнуть


class FlakyNetClient(FakeTgClient):
    """Сеть так, как её отдаёт Telethon 1.44: потеря связи — это НЕ «вернулся
    run_until_disconnected()», а брошенный из него ConnectionError (клиент сдаётся сам за
    ~10-15с: 5 попыток × 1с) при уже отключённом клиенте (`finally: await self.disconnect()`).
    Пока сеть лежит, connect() тоже отказывает."""

    def __init__(self, drops: int = 1, connect_failures: int = 0):
        super().__init__()
        self.drops = drops                        # сколько раз ронять связь
        self.connect_failures = connect_failures  # сколько connect() подряд отказывают
        self.dropped = 0

    async def run_until_disconnected(self):
        if self.dropped < self.drops:
            self.dropped += 1
            self.connected = False  # telethon дисконнектится сам ПЕРЕД тем как бросить
            raise ConnectionError("Connection to Telegram failed 5 time(s)")
        await super().run_until_disconnected()

    async def connect(self):
        self.connect_calls += 1
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise ConnectionError("нет маршрута до Telegram (тест)")
        self.connected = True


class NetBase(Base):
    """Тайминги ужаты до долей секунды: те же переходы, секунды вместо получаса."""

    def setUp(self):
        super().setUp()
        self._orig_start = mtproto_runner.RECONNECT_BACKOFF_START
        self._orig_max = mtproto_runner.RECONNECT_BACKOFF_MAX
        mtproto_runner.RECONNECT_BACKOFF_START = 0.01
        mtproto_runner.RECONNECT_BACKOFF_MAX = 0.04
        mtproto_runner.RECONNECT_TIMEOUT_SEC = 5.0

    def tearDown(self):
        mtproto_runner.RECONNECT_BACKOFF_START = self._orig_start
        mtproto_runner.RECONNECT_BACKOFF_MAX = self._orig_max
        super().tearDown()

    def use(self, client):
        mtproto_runner.client = client
        self.fake = client
        return client


class TestNetworkOutageIsSurvived(NetBase):
    """Главное: потеря сети больше не убивает процесс. До 20.08 ConnectionError из
    run_until_disconnected() не ловил никто — он улетал из main(), процесс падал секунд
    через 15 после старта, bootguard видел rc≠0 при elapsed<grace(60с), и на третьем круге
    откатывал ЗДОРОВЫЙ HEAD на last_good."""

    def test_five_minute_outage_does_not_end_the_process(self):
        # «сеть моргнула»: связь оборвалась, четыре попытки вернуться отказали, пятая взяла
        net = self.use(FlakyNetClient(drops=1, connect_failures=4))

        async def _run():
            task = asyncio.get_event_loop().create_task(mtproto_runner._supervise_connection())
            for _ in range(2000):
                if net.connect_calls >= 5:
                    break
                # супервизор обязан быть ЖИВ всё время обрыва: ни возврата, ни исключения
                self.assertFalse(task.done(), "супервизор вышел прямо посреди обрыва")
                await asyncio.sleep(0.005)
            else:
                self.fail("реконнект так и не дошёл до пятой попытки")
            await asyncio.sleep(0.05)
            self.assertFalse(task.done(), "супервизор вышел, хотя связь уже вернулась")
            self.assertTrue(net.is_connected(), "после возврата сети клиент обязан быть connected")
            mtproto_runner._SHUTDOWN.set()  # закончить тест: обычный конец процесса
            await net.disconnect()
            return await asyncio.wait_for(task, timeout=2.0)

        needs_restart = asyncio.run(_run())
        self.assertFalse(needs_restart, "связь вернулась -- намеренный рестарт не нужен")
        self.assertEqual(net.connect_calls, 5)
        self.assertEqual(net.dropped, 1)

    def test_in_memory_state_survives_the_outage(self):
        """Главный выигрыш правки не «реконнект», а то, что ЖИВЁТ через обрыв: дедуп
        catch_up, кулдауны, поколения преемников и уже взведённый дебаунс. Рестарт стирает
        ровно это (на диске их нет), и она отвечала на доигранный backlog как на новое."""
        net = self.use(FlakyNetClient(drops=1, connect_failures=3))
        cid = "-100777"
        fired = []

        async def pending_debounce():
            await asyncio.sleep(0.05)
            fired.append(True)

        async def _run():
            mtproto_runner._seen_ids[cid].append(4242)
            mtproto_runner._last_pass[cid] = 1234.5
            mtproto_runner._supersede_gen[cid] = 7
            mtproto_runner._debounce[cid] = asyncio.get_event_loop().create_task(pending_debounce())
            task = asyncio.get_event_loop().create_task(mtproto_runner._supervise_connection())
            for _ in range(2000):
                if net.connect_calls >= 4 and fired:
                    break
                await asyncio.sleep(0.005)
            mtproto_runner._SHUTDOWN.set()
            await net.disconnect()
            await asyncio.wait_for(task, timeout=2.0)

        try:
            asyncio.run(_run())
            self.assertIn(4242, mtproto_runner._seen_ids[cid], "дедуп catch_up пережил обрыв")
            self.assertEqual(mtproto_runner._last_pass[cid], 1234.5, "кулдаун пережил обрыв")
            self.assertEqual(mtproto_runner._supersede_gen[cid], 7, "поколение пережило обрыв")
            self.assertEqual(fired, [True], "взведённый дебаунс доехал, а не умер с процессом")
        finally:
            mtproto_runner._seen_ids.pop(cid, None)
            mtproto_runner._last_pass.pop(cid, None)
            mtproto_runner._supersede_gen.pop(cid, None)
            mtproto_runner._debounce.pop(cid, None)

    def test_grace_exhausted_is_an_honest_exit_asking_for_restart(self):
        # сеть не вернулась НИКОГДА: прежний честный выход, но с просьбой о рестарте 42
        net = self.use(FlakyNetClient(drops=1, connect_failures=10 ** 6))
        mtproto_runner.RECONNECT_TIMEOUT_SEC = 0.2

        needs_restart = asyncio.run(
            asyncio.wait_for(mtproto_runner._supervise_connection(), timeout=5.0))
        self.assertTrue(needs_restart, "исчерпанный грейс -- это выход 42, а не молчаливый ноль")
        self.assertFalse(net.is_connected())
        self.assertGreaterEqual(net.connect_calls, 2, "backoff обязан был попробовать не раз")

    def test_flapping_link_cannot_renew_its_grace_forever(self):
        # связь встаёт и тут же падает: каждый круг НЕ имеет права выдавать себе новый
        # полный грейс, иначе «выход по исчерпанию» недостижим в принципе
        net = self.use(FlakyNetClient(drops=10 ** 6, connect_failures=0))
        mtproto_runner.RECONNECT_TIMEOUT_SEC = 0.15

        needs_restart = asyncio.run(
            asyncio.wait_for(mtproto_runner._supervise_connection(), timeout=5.0))
        self.assertTrue(needs_restart)
        self.assertGreater(net.dropped, 1, "флап должен был случиться не один раз")


class TestStartupConnectSurvivesOutage(NetBase):
    """Тот же helper на стартовом connect() в main(): подъём В МОМЕНТ обрыва — не «плохой код»."""

    def tearDown(self):
        mtproto_runner._LOOP = None
        super().tearDown()

    def test_main_exits_with_intentional_restart_code_when_network_never_comes(self):
        net = self.use(FlakyNetClient(drops=0, connect_failures=10 ** 6))
        mtproto_runner.RECONNECT_TIMEOUT_SEC = 0.2

        class _Exit42(BaseException):
            """Заменяет os._exit(42), чтобы тест пережил вызов."""

        calls = []

        def fake_exit():
            calls.append(42)
            raise _Exit42()

        orig_exit = mtproto_runner.agent._exit_process
        mtproto_runner.agent._exit_process = fake_exit
        try:
            with self.assertRaises(_Exit42):
                asyncio.run(mtproto_runner.main())
        finally:
            mtproto_runner.agent._exit_process = orig_exit

        self.assertEqual(calls, [42], "main() обязан просить намеренный рестарт, а не падать rc=1")
        self.assertGreaterEqual(net.connect_calls, 2, "стартовый connect обязан был ретраиться")

    def test_startup_connect_recovers_and_main_goes_on(self):
        # сеть вернулась на третьей попытке -- main() идёт дальше своим ходом и спотыкается
        # уже о СВОЮ следующую проверку (не залогинена), а не о сеть
        net = self.use(FlakyNetClient(drops=0, connect_failures=2))

        async def _not_authorized():
            return False

        net.is_user_authorized = _not_authorized

        with self.assertRaises(SystemExit) as ctx:
            asyncio.run(mtproto_runner.main())
        self.assertIn("Не залогинен", str(ctx.exception))
        self.assertEqual(net.connect_calls, 3)
        self.assertTrue(net.is_connected())


class TestBootguardTreatsRestartCodeAsIntent(unittest.TestCase):
    """Зачем именно 42: bootguard.decide_after не считает его early_fail — значит долгий
    обрыв сети больше не может откатить здоровый код на last_good."""

    def test_restart_code_does_not_grow_early_fails(self):
        import bootguard
        d = bootguard.decide_after(bootguard.RESTART_CODE, elapsed=5.0, launch_sha="new",
                                   last_good="old", early_fails=2, grace=60.0, max_fails=3)
        self.assertEqual(d["action"], "relaunch")
        self.assertEqual(d["early_fails"], 2, "намеренный рестарт не копит счётчик падений")

    def test_plain_crash_at_the_same_point_would_have_rolled_back(self):
        import bootguard
        d = bootguard.decide_after(1, elapsed=5.0, launch_sha="new", last_good="old",
                                   early_fails=2, grace=60.0, max_fails=3)
        self.assertEqual(d["action"], "rollback", "именно так обрыв сети и откатывал здоровый код")
        self.assertEqual(d["to"], "old")


if __name__ == "__main__":
    unittest.main(verbosity=2)
