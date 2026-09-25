"""Praxis как пользователь Telegram (MTProto/Telethon) — перцепционная петля.

Входящее НЕ дёргает ответ по сообщению. Оно кладётся в per-chat буфер и взводит дебаунс;
всплеск склеивается в одну ситуацию. По тишине (дебаунс) с учётом кулдауна — ход ГОЛОСА
(PASS 8.1: привратник-perceive снесён, большая модель работает и в группе). В группе
голос получает presence-фрейм: тишина — его полноценный выбор, сентинел [молчу]; раннер
молчание не отправляет. Говорит в разговор (`send_message`), не цитатой.

По умолчанию группу будит только прямое обращение (@упоминание или реплай ей). Корневой
room-profile может явно включить engagement=reflective: тогда meaningful-фон склеивается
дебаунсом в один ambient-проход, а address всегда имеет приоритет. Топики не смешиваются.
Кост-гард группы: кулдаун (300с дефолт) + reflex + [молчу]. Вся фоновая
периодика (буферы/расписание/«сон»/сердцебиение) — один тик `_clock()`, «её часы».

Перед запуском нужен логин: python mtproto_login.py (см. README).
"""
from __future__ import annotations
import asyncio, concurrent.futures, contextvars, datetime, functools, hashlib, inspect, json, logging, mimetypes, os, re, tempfile, threading, time, types
from collections import defaultdict, deque
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path

from telethon import TelegramClient, events
from dotenv import load_dotenv

import praxis_time
import agent
import bufstore
import context_envelope
import formation
import frame_epoch
import group_context
import llm
import memory_life
import media as media_core
import moderation_shadow
import owner_delivery
import perception
import selfdev
import reflex
import rooms
import social
import social_pulse
import telegram_contacts
import telegram_confirmation
import telegram_followups
import telegram_membership
import telegram_admin
import telegram_moderation
import telegram_outbox
import telegram_registry
import telegram_routes
import telegram_topics
import history_scan
import unanswered
import workshop

try:  # герметичность: любой тест-запуск (unittest/pytest/PRAXIS_TEST) не читает боевой .env
    from _sandbox import _looks_like_test_run as _looks_like_test_run_now
except Exception:
    _looks_like_test_run_now = lambda: bool(os.environ.get("PRAXIS_TEST"))
# 23.09: вердикт «это тест» снимается ОДИН раз — при импорте раннера. `_looks_like_test_run`
# смотрит и в `sys.modules`, а живой процесс позже сам подтягивает `unittest`: прогрев
# whisper → faster_whisper → ctranslate2 → torch → `torch/utils/_config_module.py`
# делает `import unittest`. С 21.09 19:19 (первый бут после pip install torch) прод
# считал себя тестом и молча выключил запись жизни (`_buf_push`, `_persist_sent_reply`),
# архив групп и свёртку буферов: лента ЛС замёрзла, кадр подавал старое сообщение как
# текущее, а бут пересобирал буферы из замёрзшего слоя — и boot-sweep воскрешал
# отвеченное. При импорте torch ещё не загружен, поэтому этот снимок и есть правда.
# Имя `_under_tests` оставлено функцией: стенды подменяют его через patch.object.
_UNDER_TESTS_AT_IMPORT = bool(_looks_like_test_run_now())


def _under_tests() -> bool:
    return _UNDER_TESTS_AT_IMPORT or bool(os.environ.get("PRAXIS_TEST"))


if not _under_tests():
    load_dotenv(override=True)  # .env-тюнинг применяется и на простом рестарте (§9 пакета 2)
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION = os.getenv("TELEGRAM_SESSION", "praxis")
OWNER_ID = int(os.getenv("PRAXIS_OWNER_ID", "0"))
CONSOLIDATE_HOURS = float(os.getenv("PRAXIS_CONSOLIDATE_HOURS", "24"))  # ≤0 — сон выключен
# PASS 10.2: сон — по будильнику (sleep.due: окно PRAXIS_SLEEP_WINDOW + persist-метка),
# тик проверки раз в 30 мин; CONSOLIDATE_HOURS остался выключателем, не секундомером.
SLEEP_CHECK_SEC = float(os.getenv("PRAXIS_SLEEP_CHECK_SEC", "1800"))
_STARTED_AT = time.time()
# Legacy selector heartbeat is kept as a callable compatibility helper, but the clock has
# one autonomous wake source: durable social_pulse.  Two hourly jobs used to race and could
# open two independent task windows for the same hour.
DEBOUNCE_SEC = float(os.getenv("PRAXIS_DEBOUNCE_SEC", "4"))
COOLDOWN_DM = float(os.getenv("PRAXIS_COOLDOWN_DM", "8"))
COOLDOWN_GROUP = float(os.getenv("PRAXIS_COOLDOWN_GROUP", "300"))  # PASS 8.1: кост-гард групп
COOLDOWN_ADDRESSED = float(os.getenv("PRAXIS_COOLDOWN_ADDRESSED", "180"))
LAST_N = int(os.getenv("PRAXIS_LAST_N", "50"))
# Бюджет ленты для комнаты, которую Telegram НЕ делил форумными топиками (группа
# обсуждения канала, обычная супергруппа). Там лента должна покрывать разговор МЕСТА, а
# не хвост одной цепочки ответов: в AbstractDL при 14 000 символов в кадр влезало ~45
# строк, и все из общего чата — живое обсуждение под постом канала не попадало вовсе.
# Связывает именно бюджет символов, а не потолок сообщений (тот был 200 при 45 строках).
WHOLE_ROOM_CONTEXT_CHARS = int(os.getenv("PRAXIS_WHOLE_ROOM_CONTEXT_CHARS", "32000"))
# LAST_N bounds the legacy text view; storage/compaction and dialogue roles keep their own caps.
COMPACT_MARGIN = int(os.getenv("PRAXIS_COMPACT_MARGIN", "20"))
# Deep-room profiles may ask for a 500-message hot view.  Ordinary rooms still slice
# at memory_life.HOT_HARD_HI; the extra retention is inert until their root profile opts in.
BUF_MAXLEN = max(int(os.getenv("PRAXIS_BUF_MAXLEN", "160")),
                 memory_life.HOT_HARD_HI + 25, group_context.MAX_HOT + 25)
SCHED_TICK = float(os.getenv("PRAXIS_SCHED_TICK", "60"))  # период проверки due-задач (локально, без модели)
CLOCK_TICK = float(os.getenv("PRAXIS_CLOCK_TICK", "2"))   # PASS 4: удар «её часов» (и период флаша буферов)
# Кому позволен стоп-кран (Егор + хосты чужих пространств, напр. Хоуп). Owner всегда можно.
PANIC_IDS = {int(x) for x in os.getenv("PRAXIS_PANIC_IDS", "").replace(" ", "").split(",")
             if x.lstrip("-").isdigit()}
# PASS 10.7: новичок-протокол — сколько сообщений предыстории читать при входе в группу
BACKFILL_N = int(os.getenv("PRAXIS_BACKFILL_N", "200"))
# PASS 9.0: непрерывность приёма — сообщение в даунтайм не теряется молча (кейс Евгения).
MISSED_DM_HOURS = float(os.getenv("PRAXIS_MISSED_DM_HOURS", "48"))
MISSED_SWEEP_DELAY = float(os.getenv("PRAXIS_MISSED_SWEEP_DELAY", "25"))  # после полного подъёма
SEEN_IDS_KEEP = 300  # сторож дедупа msg_id на чат (catch_up может доиграть уже виденное)
# Потолок текста улики в пробуждении на спам: спам короткий, а простыня в кадре
# стоит места, которое нужно ей на решение.
MODERATION_WAKE_TEXT_MAX = 1200
# PASS 9.2: как часто разбирать очередь иммунитета (0 — выключить заботу)
IMMUNE_MINUTES = float(os.getenv("PRAXIS_IMMUNE_MINUTES", "15"))
# PASS 12.1: как часто проверять простаивающие важные сообщения в окно отсутствия владельца
# (тик локален и дёшев: без активного окна absence.due() сразу пуст; 0 — выключить заботу).
ABSENCE_TICK = float(os.getenv("PRAXIS_ABSENCE_TICK", "300"))
# PASS 13.1 compatibility: timeout for an explicitly announced transport reconnect.
# PASS 24 task runs never use this state: Telethon stays connected while they execute.
# С 20.08 тот же бюджет — грейс ДЕГРАДИРОВАННОГО режима: сколько она держится без сети,
# не умирая (см. _reconnect_with_backoff). Смысл один и тот же — «сколько терплю немоту
# транспорта, прежде чем признать сбой», поэтому второго тумблера не завожу.
RECONNECT_TIMEOUT_SEC = float(os.getenv("PRAXIS_RECONNECT_TIMEOUT_SEC", "1800"))
# Обрыв сети — не повод умирать. Telethon 1.44 сдаётся сам за ~10-15с (5 попыток × 1с)
# и бросает ConnectionError из run_until_disconnected(); до 20.08 это исключение не ловил
# НИКТО — оно улетало из main(), процесс падал секунд через 15 после старта, bootguard видел
# rc≠0 при elapsed<grace(60с) и на третьем круге откатывал ЗДОРОВЫЙ код на last_good.
# ConnectionError и TimeoutError — подклассы OSError, перечисляю все три ради читаемости.
_NET_ERRORS = (ConnectionError, OSError, asyncio.TimeoutError)
RECONNECT_BACKOFF_START = float(os.getenv("PRAXIS_RECONNECT_BACKOFF_START", "5"))
RECONNECT_BACKOFF_MAX = float(os.getenv("PRAXIS_RECONNECT_BACKOFF_MAX", "60"))
# HOTFIX 07.07: сколько ждать фонового прогрева кэша диалогов при ХОЛОДНОМ резолве по имени.
# Свой бюджет, не доля чужого: раньше холодный iter_dialogs() платился из общих 30 секунд
# _sync_send_message — первый «напиши Евгению» после рестарта умирал TimeoutError'ом.
DIALOG_WARMUP_WAIT_SEC = float(os.getenv("PRAXIS_DIALOG_WARMUP_WAIT_SEC", "90"))


def _positive_env_float(name: str, default: float) -> float:
    """Read probe timing without letting a bad observability knob break startup."""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Deliberately more tolerant than the normal two-second clock tick. This reports
# delayed execution of one callback on the asyncio loop and nothing broader.
LOOP_LIVENESS_INTERVAL_SEC = _positive_env_float(
    "PRAXIS_LOOP_LIVENESS_INTERVAL_SEC", 5.0)
LOOP_LIVENESS_THRESHOLD_SEC = _positive_env_float(
    "PRAXIS_LOOP_LIVENESS_THRESHOLD_SEC", 30.0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("praxis-mt")
import logsink  # noqa: E402  (после basicConfig: файловый хвост для панели)
logsink.attach("praxis")

# catch_up=True (PASS 9.0): пропущенные в даунтайм апдейты доезжают после реконнекта —
# сообщение, пришедшее между рестартами, больше не теряется молча.
client = TelegramClient(SESSION, API_ID, API_HASH, catch_up=True)
_self_id = None

# HOTFIX 07.07 (вечер): луп main() — захватывается при старте, ЕДИНСТВЕННЫЙ живой.
# НЕЛЬЗЯ брать client.loop из воркер-треда: с telethon>=1.39 это динамический
# get_running_loop(), который в треде без лупа МОЛЧА создаёт новый мёртвый луп
# (new_event_loop + set_event_loop) — run_coroutine_threadsafe планирует корутину
# в никуда, и .result() честно выедает весь таймаут-бюджет. Так 07.07 умерли ВСЕ
# sync-тулы Telethon разом: send_message (120с TimeoutError), get_id («[не нашла]» =
# скрытый 20с-таймаут), read_chat, search_chats... — при живом приёме апдейтов
# (он на настоящем лупе). В свежем клиенте (проба) те же вызовы шли за 0.2с.
_LOOP: asyncio.AbstractEventLoop | None = None
_TELEGRAM_DISPATCHER: telegram_registry.TelegramAccountDispatcher | None = None
_TELEGRAM_CONFIRMATIONS: telegram_confirmation.ConfirmationStore | None = None
_TELEGRAM_CRITICAL_CHALLENGES: telegram_confirmation.CriticalChallengeStore | None = None


def _main_loop() -> asyncio.AbstractEventLoop:
    """Луп, на котором реально живёт Telethon. Для sync-обёрток тулов (воркер-треды)."""
    if _LOOP is None:
        raise RuntimeError("луп Telethon ещё не захвачен (main() не стартовал)")
    return _LOOP


def _threadsafe_result(coro_factory, timeout: float):
    """Run on Telethon's captured loop without leaking or ghosting a coroutine.

    Resolve the loop before creating the coroutine. If the synchronous caller times out,
    propagate cancellation so a tool cannot report failure and then send something later.
    """
    loop = _main_loop()
    coro = coro_factory()
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception:
        coro.close()
        raise
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()
        raise


# PASS 13.1: run_until_disconnected() в main() возвращается на ЛЮБОЙ disconnect(), намеренный
# или нет — раньше это было неразличимо, и любой намеренный disconnect (_task_window) мог
# ронять процесс на удаче тайминга. Явные флаги намерения — см. _supervise_connection().
_EXPECT_DISCONNECT = asyncio.Event()  # legacy/explicit transport recovery; never a work mode
_SHUTDOWN = asyncio.Event()           # кто-то хочет, чтобы main() реально закончился (_control_once)
# Она ОДНА: единый когнитивный проход за раз. Живой ход по чату (_run_pass) и автономное
# окно (_task_window) держат этот замок, поэтому параллельных «я» не бывает — ни два чата
# разом, ни окно поверх живого разговора. Окно, увидев занятость, откладывается до следующего
# тика (не блокирует часы); живой ход дожидается (при закрытом на окно Telethon это и не
# наступает). Устраняет диссоциацию, где пульс и разговор одновременно били в мозг.
class _OneMind(asyncio.Lock):
    """Её единый замок плюс один честный вопрос: «возьмётся ли он сейчас, не засыпая?».

    Сам asyncio.Lock такого вопроса не отвечает: ``locked()`` говорит только про владельца
    и ничего — про очередь, а между ``release()`` и тем, как очередной ждущий реально
    возьмёт замок, ``locked()`` уже False, тогда как ``acquire()`` встанет в хвост.
    Заботам, которых зовут прямо из часов (отсутствие, ночь), засыпать нельзя — с ними
    встанет весь тик.

    Считаем СВОЮ глубину очереди вокруг родного ``acquire()``: поведение замка не
    трогаем, добавляем только наблюдение. Так ответ не зависит от приватных полей и не
    может тихо испортиться на другой версии Python.
    """

    def __init__(self) -> None:
        super().__init__()
        self._parked = 0
        self._generation = 0

    async def acquire(self) -> bool:  # type: ignore[override]
        # A busy period begins only when the lock is completely idle.  If waiters already
        # exist, ownership is merely being handed over inside the same continuous period.
        if not self.locked() and self._parked == 0:
            self._generation += 1
        self._parked += 1
        try:
            return await super().acquire()
        finally:
            self._parked -= 1

    def free_now(self) -> bool:
        """True — захват вернётся не отдав управление (никто не держит и не ждёт)."""
        return not self.locked() and self._parked == 0

    @property
    def generation(self) -> int:
        """Identifies the current continuous busy period; advances on idle acquisition."""
        return self._generation


_ONE_MIND = _OneMind()
# One visible defer per intention and continuous _ONE_MIND ownership period.  The scheduler
# may retry the same due wake/window every few seconds; those retries are transport churn,
# not thousands of distinct decisions by Praxis.  ``generation`` advances only when the
# lock is acquired from a completely idle state; a handoff to an existing waiter remains
# part of the same busy period.
_ONE_MIND_DEFERRED: dict[tuple[str, str], int] = {}
_buf: dict[str, deque] = defaultdict(lambda: deque(maxlen=BUF_MAXLEN))
_buffer_message_ids: dict[str, deque] = defaultdict(lambda: deque(maxlen=BUF_MAXLEN))
_persisted_life_sources: set[tuple[str, str, object]] = set()
# Per peer/message reception order captured before edit handlers await sender/routing work.
# It breaks equal-second concurrent completion ties without changing the stable revision id.
_revision_reception_counter = 0
_revision_reception_orders: dict[tuple[str, int, str], int] = {}
_revision_order_by_chat_source: dict[tuple[str, str], int] = {}
_meta: dict[str, dict] = {}


@dataclass(frozen=True)
class GroupWake:
    """Immutable provenance for an addressed or reflective group pass."""

    message_id: int | None
    message_ts: float
    kind: str
    speaker: str
    sender_id: int | None
    owner: bool
    known: bool
    family: bool
    context_snapshot: str
    reply_targets_snapshot: tuple
    media_snapshot: tuple[media_core.MediaRef, ...]
    addressed: bool = True
    query: str = ""
    # Та же лента, но с авторством: ((её ли это строка, строка для роли), …).
    # Замораживается ВМЕСТЕ с текстом, одним чтением архива. Пустой кортеж —
    # честное «авторства не знаю» (запасной путь по строковому буферу).
    turns_snapshot: tuple = ()
    # Native refs are frozen with the wake; later room traffic cannot enter KEAT.
    occurrence_sidecar: dict | None = None


_group_wakes: dict[str, GroupWake] = {}
_seen_ids: dict[str, deque] = defaultdict(lambda: deque(maxlen=SEEN_IDS_KEEP))  # 9.0: дедуп catch_up
# 15.09, эпоха комнаты: якорь свёртки, с которого собран последний снимок ленты комнаты.
# Ход читает его через frame_epoch.bind, чтобы E и лента говорили об одной границе.
_EPOCH_ANCHORS: dict[str, int] = {}
_recent_msgs: dict[str, deque] = defaultdict(lambda: deque(maxlen=12))  # 15: (msg_id, автор, гист) для ОТВЕТ->#id
# PASS 16.2: недавние отправители на чат — (ts, имя, id). Честный источник для get_id
# в классе «айди спамера»: отправитель БЫЛ в апдейте, но резолв по диалогам/участникам
# его не видит (забанен/ушёл). RAM: с рестарта; глубже по времени — тул read_log.
_recent_senders: dict[str, deque] = defaultdict(lambda: deque(maxlen=40))
_BOUNDARY_REPLY_TTL_S = 6 * 60 * 60
# chat -> (sent message id, provocateur sender id, timestamp). A direct reply to an explicit
# boundary is context, but does not earn another defensive pass.
_boundary_replies: dict[str, deque] = defaultdict(lambda: deque(maxlen=8))
_pending_media: dict[str, deque] = defaultdict(
    lambda: deque(maxlen=max(2, media_core.TURN_MEDIA_MAX * 2)))
_missed: dict[str, float] = {}            # 9.0: chat_id -> возраст (ч) для честной [missed]-метки
_chat_desc: dict[str, dict] = {}          # §2: ленивая осознанность чата (title/kind/size), кэш на чат
_debounce: dict[str, asyncio.Task] = {}
_deferred: dict[str, asyncio.Task] = {}   # §8: отложенный проход на остаток кулдауна
_last_pass: dict[str, float] = defaultdict(float)
_passing: set[str] = set()
_dm_rearm: set[str] = set()  # PASS 21: ЛС-триггеры, сгоревшие об идущий ход, — перевзвести
_supersede_gen: dict[str, int] = {}  # PASS 29: бамп поколения == создан новый пасс-преемник (см. _arm)
_buf_dirty: set[str] = set()              # §1: чаты с несохранённым буфером
_compacting: set[str] = set()             # §6: чтобы не свернуть один чат дважды разом
# 12.09: свёртки идут в общем пуле потоков `asyncio.to_thread` (8 рабочих на 4 ядрах).
# Разовая свёртка всех 239 мест под новый потолок ленты забила пул целиком: 135 мест
# за полчаса, а часы, пробуждения и ходы голоса стояли 40 минут (ни одного прогона).
# Свёртка — фон, ей хватит двух потоков; остальное — живому ходу.
_COMPACT_SLOTS = asyncio.Semaphore(max(1, int(os.getenv("PRAXIS_COMPACT_PARALLEL", "2") or 2)))
_MEDIA_SPOOL: media_core.MediaSpool | None = None
_MEDIA_SENDING: set[str] = set()
_MEDIA_ACCEPTED: dict[str, dict] = {}
_TEXT_SENDING: set[str] = set()
_MEMBERSHIP_LEDGER: telegram_membership.MembershipLedger | None = None
_DIRECT_OUTBOX: telegram_outbox.TelegramOutbox | None = None
_DIRECT_OUTBOX_RECONCILED: set[str] = set()
#: Тумбстоны спула медиа, чьи прогоны уже закрыты: раз опознали — манифест больше не читаем.
_MEDIA_TOMBSTONES_SETTLED: set[str] = set()
_SOCIAL_PULSE_TASK: asyncio.Task | None = None
_FORGE_EVENTS_TASK: asyncio.Task | None = None   # PASS 30 Этап 1: single-flight насоса событий
_MODERATION_EVENTS_TASK: asyncio.Task | None = None  # shadow moderation -> её live wake
# A durable review event gets first claim on the next free voice turn. It is a scheduling
# hint only: the moderation wake still supplies facts and Praxis still decides any action.
_MODERATION_PRIORITY_PENDING = False
# Сбои чтения очереди модерации ПОДРЯД; успешный тик обнуляет. Порог — предохранитель
# для поднятого флага: одиночный сбой флага не трогает (её решение), серия снимает громко.
_MODERATION_TICK_FAILURES = 0
_MODERATION_FAILURE_TICKS = 6  # тики ≥5 с → потолок глухоты ~30 с вместо «до рестарта»
_SLEEP_TASK: asyncio.Task | None = None          # 26.07: ночь ждёт замок, но не в часах


def _one_mind_is_free() -> bool:
    """Возьмётся ли замок БЕЗ ожидания. False — кто-то держит его или уже стоит в очереди.

    Одного ``locked()`` мало, и это не педантизм: у asyncio.Lock честная очередь, и между
    ``release()`` и тем, как очередной ждущий реально возьмёт замок, ``locked()`` уже
    False — а ``acquire()`` всё равно встанет в хвост, то есть ЗАСНЁТ. Для заботы, которую
    зовут прямо из часов, это означало бы вставший тик: буферы, планировщик, outbox.

    ⚠ Здесь читалось приватное поле ``Lock._waiters``. Праксис назвала это хрупким, и была
    права: молчаливая деградация при переименовании поля — ровно тот случай, когда защита
    исчезает, а выглядит работающей. Теперь глубину очереди считаем сами, через публичный
    API замка; честность очереди, пробуждение и порядок остаются его.
    """
    return _ONE_MIND.free_now()
_FORGE_EVENT_LAST = {"ts": 0.0}                  # зазор между forge-event ходами (её рычаг)
_CLOCK_STARTUP_DUE = frozenset({
    "durable_resume", "social_pulse", "computer_inventory",
    "forge_events",   # догнать события, случившиеся при даунтайме, сразу после подъёма
    # Реконсайлер предложений с периодом 30 минут ГОЛОДАЛ: каждый перезапуск отодвигал
    # его срок на полный период, а из последних 39 запусков 16 случились быстрее чем
    # через полчаса после предыдущего (её самомёрж перезапускает её дважды, плюс выкаты).
    # Итог: за 13 дней он отработал один раз (метки updated у building стоят на 19.07),
    # и всё это время реестр предложений врал о себе, потому что поправить его было
    # некому. Он тоже дешёвая догоняющая проверка сохранённого состояния — ей место здесь.
    "selfdev_reconcile",
})
_TURN_TOPIC_ROUTE: ContextVar[telegram_topics.TopicRoute | None] = ContextVar(
    "praxis_telegram_topic_route", default=None)
_topic_titles: dict[tuple[str, int], str] = {}
# Добыча durable-каталога тем (её блокер 5 от 22.08: никакого скрытого фан-аута и
# синхронного диска в event loop): один tracked single-flight на комнату, короткий
# in-memory кэш каталога, окно проверки давности/незнакомого корня — не чаще
# кулдауна, повтор после неудачи — короче и отдельно. Кулдаун и давность — её числа.
_topic_catalog_cache: dict[str, tuple[float, frozenset | None]] = {}
_TOPIC_CATALOG_CACHE_TTL = 60.0
_topic_catalog_flights: dict[str, asyncio.Task] = {}
_topic_catalog_next_attempt: dict[str, float] = {}
# Дисковая проверка давности живёт на СВОЁМ окне: фан-аут 22.08 поймал, что общее
# окно сжигалось сообщением, которому рефреш не нужен, и глушило бесплатный
# in-memory сигнал «незнакомый корень» на 15 минут (P0).
_topic_catalog_stale_probe: dict[str, float] = {}
# Корни, по которым свип уже летал и темы не нашёл (цепочки General): один корень —
# одна разведка, чтобы болтливая ветка не жгла RPC. Кап — защита памяти, не политика.
_topic_catalog_probed_roots: dict[str, set[int]] = {}
_TOPIC_CATALOG_PROBED_CAP = 512
# Анти-флуд на разведку незнакомых корней: поток РАЗНЫХ корней (враждебный или
# просто болтливый) не смеет жечь RPC чаще раза в окно. Настоящая новая тема
# попадает в первый же свободный запуск.
_topic_catalog_unknown_gate: dict[str, float] = {}
# Её гейт REPAIR2: холодное durable-чтение каталога — один read-flight на комнату
# (P1-3, stampede), а корни, ради которых летал свип, хоронятся в probed ТОЛЬКО
# после доказанно полного успеха (P0-1: transient не смеет хоронить навсегда).
# Поколение кэша защищает от гонки «устаревший read-flight дописал старое знание
# ПОВЕРХ свежего»: запись в кэш действительна только в том поколении, в котором
# чтение началось; каждая инвалидация поколение двигает.
_topic_catalog_read_flights: dict[str, asyncio.Task] = {}
_topic_catalog_pending_roots: dict[str, set[int]] = {}
_topic_catalog_cache_gen: dict[str, int] = {}
# Бюджет сходимости чтения знания (её гейт REPAIR3): при непрерывном churn
# опенеров/инвалидаций перечитывание ограничено временем, а не числом попыток —
# лимит попыток не доказательство свежести. Число — её, как и остальные.
_TOPIC_KNOWLEDGE_CONVERGE_BUDGET = 2.0


def _invalidate_topic_cache(peer: str) -> None:
    _topic_catalog_cache.pop(peer, None)
    _topic_catalog_cache_gen[peer] = _topic_catalog_cache_gen.get(peer, 0) + 1
_TOPIC_CATALOG_COOLDOWN = 900.0
_TOPIC_CATALOG_RETRY = 60.0
_TOPIC_CATALOG_REFRESH = 86_400.0
_TOPIC_CATALOG_PREFLIGHT_TIMEOUT = 8.0


def _media_spool() -> media_core.MediaSpool:
    global _MEDIA_SPOOL
    if _MEDIA_SPOOL is None:
        _MEDIA_SPOOL = media_core.MediaSpool()
    return _MEDIA_SPOOL


def _membership_ledger() -> telegram_membership.MembershipLedger:
    global _MEMBERSHIP_LEDGER
    if _MEMBERSHIP_LEDGER is None:
        _MEMBERSHIP_LEDGER = telegram_membership.MembershipLedger()
    return _MEMBERSHIP_LEDGER


def _direct_outbox() -> telegram_outbox.TelegramOutbox:
    global _DIRECT_OUTBOX
    if _DIRECT_OUTBOX is None:
        _DIRECT_OUTBOX = telegram_outbox.TelegramOutbox()
    return _DIRECT_OUTBOX


def _route_from_state(chat_id: str | int) -> telegram_topics.TopicRoute:
    """Conversation id -> root peer/topic; ordinary chat ids remain unchanged."""

    return telegram_topics.route_from_conversation_id(chat_id)


def _route_from_reference(value: str | int) -> telegram_topics.TopicRoute:
    """Tool/queue address -> root Telethon peer plus isolated conversation key."""

    return telegram_topics.route_from_reference(value)


def _meta_for_peer(peer_id: str | int) -> tuple[str, dict] | tuple[None, None]:
    """Find usable live metadata for a root peer, including topic-scoped entries."""

    peer = str(peer_id)
    direct = _meta.get(peer)
    if isinstance(direct, dict):
        return peer, direct
    # Dicts preserve insertion order; the last matching topic is the freshest one.
    for conversation_id, meta in reversed(list(_meta.items())):
        if isinstance(meta, dict) and str(meta.get("peer_id") or "") == peer:
            return conversation_id, meta
    return None, None


def _meta_for_delivery(target: str | int, reply_to=None) -> tuple[str | None, dict | None]:
    """Find metadata without collapsing a durable topic address to its root peer.

    New queue records carry a conversation id, so their topic is unambiguous after
    restart even before an incoming update repopulates ``_meta``.  The recent-reply
    lookup remains only as backwards compatibility for old root-peer queue records.
    """

    raw = str(target)
    direct = _meta.get(raw)
    if isinstance(direct, dict):
        return raw, direct
    route = _route_from_reference(raw)
    conversation_id = route.conversation_id
    routed = _meta.get(conversation_id)
    if isinstance(routed, dict):
        return conversation_id, routed
    # A persisted topic route is already authoritative.  Borrowing another
    # topic's fresh metadata would silently reroute a restart retry across
    # threads in the same group; resolve the root entity without metadata.
    if route.topic_id is not None:
        return conversation_id, None
    peer = route.peer_id
    if reply_to is not None:
        try:
            wanted = int(reply_to)
        except (TypeError, ValueError):
            wanted = None
        if wanted is not None:
            for conversation_id, ring in reversed(list(_recent_msgs.items())):
                meta = _meta.get(conversation_id)
                if not isinstance(meta, dict) or str(meta.get("peer_id") or conversation_id) != peer:
                    continue
                if any(mid == wanted for mid, _author, _gist in ring):
                    return conversation_id, meta
    return _meta_for_peer(peer)


def _cooldown(is_dm: bool, room_mode: str = "normal", *, addressed: bool = False) -> float:
    # PASS 21: темп — её живой рычаг (manage_perception), env остался дефолтом
    try:
        if is_dm:
            return float(perception.value("cooldown_dm"))
        knob = "cooldown_addressed" if addressed else "cooldown_group"
        base = float(perception.value(knob))
    except Exception:
        base = COOLDOWN_DM if is_dm else (COOLDOWN_ADDRESSED if addressed else COOLDOWN_GROUP)
        if is_dm:
            return base
    if room_mode == "quiet":  # 10.3: в тихой комнате она вдвое реже
        base *= 2
    return base


def _life_source_key(chat_id: str, source_id: str | int | None) -> tuple[str, str, object]:
    return str(chat_id), str(source_id or ""), memory_life.record_message


def _life_source_persisted(chat_id: str, source_id: str | int | None) -> bool:
    return source_id is not None and _life_source_key(chat_id, source_id) in _persisted_life_sources


_LIFE_RECORD_LOCKS: dict[str, asyncio.Lock] = {}


async def _record_life_message_offloop(chat_id: str, line: str, **kwargs) -> bool:
    """`_record_life_message` в потоке, а не в главном цикле (25.09, поток I).

    `memory_life.record_message` пересобирает состояние места под замком
    (`_load_state(rebuild=True)`); для правки или удаления сообщения в большой комнате это
    минуты, в течение которых главный цикл стоял — не приходили сообщения, не шли часы,
    не работал «прервать». Здесь пересборка уходит в поток; порядок записей ОДНОГО чата
    держит asyncio-замок на чат, между чатами порядок и раньше не обещался.

    ⚠ Честно о границе (ревью 25.09, A11 F1): `memory_life._WRITE_LOCK` — один на процесс и
    берётся на всю пересборку. Пока поток пересобирает большую комнату, ЛЮБАЯ новая запись
    памяти из главного цикла (`_buf_push` → `_record_life_message` на входящее сообщение
    или свой ответ, синхронно) встанет за этим замком — и цикл снова стоит до конца
    пересборки. Правки/удаления сюда уведены, но стена возвращается первым же сообщением
    в окно пересборки. Настоящее лечение — в memory_life: не пересобирать место целиком
    из-за одной правки (править запись на месте, пока сообщение в горячем кольце) и держать
    `_WRITE_LOCK` только на append/save, а пересборку — под доменным `_state_write_guard`.
    """
    lock = _LIFE_RECORD_LOCKS.get(str(chat_id))
    if lock is None:
        lock = _LIFE_RECORD_LOCKS.setdefault(str(chat_id), asyncio.Lock())
    async with lock:
        return await asyncio.to_thread(_record_life_message, chat_id, line, **kwargs)


def _record_life_message(chat_id: str, line: str, *, actor: str, direction: str,
                         source_id: str | int | None, is_dm: bool | None,
                         ts: float | None, dedupe_key: str,
                         revision_order: int | None = None,
                         capture_live: bool = False,
                         logical_send: dict | None = None,
                         root_chat_id=None, principal_id=None,
                         ordinary_root: bool = False) -> bool:
    """Persist one Telegram life event, retrying only until one append/dedupe succeeds."""

    key = _life_source_key(chat_id, source_id)
    if source_id is not None and key in _persisted_life_sources:
        return True
    occurrence = None
    if capture_live and source_id is not None:
        try:
            from keat_live import capture_ingress
            occurrence = capture_ingress(
                chat_id, is_dm=is_dm, source_id=str(source_id), direction=direction,
                payload={"role": "assistant" if direction == "out" else "user", "content": line, "actor": actor,
                         **({"logical_send": logical_send} if logical_send else {})},
                **({} if is_dm is True else dict(root_chat_id=root_chat_id,
                    principal_id=principal_id, ordinary_root=ordinary_root)))
        except Exception:
            log.debug("KEAT ingress unavailable")
    try:
        memory_life.record_message(
            chat_id, line, actor=actor, direction=direction, source="telegram",
            source_id=source_id, is_dm=is_dm, ts=ts, dedupe_key=dedupe_key,
            revision_order=revision_order, keat_occurrence=occurrence,
            logical_send=logical_send,
        )
    except Exception:
        log.exception("life event не записался [%s]", chat_id)
        return False
    if source_id is not None:
        _persisted_life_sources.add(key)
    return True


def _buf_push(chat_id: str, line: str, *, author: str = "",
              is_dm: bool | None = None, name: str | None = None,
              source_id: str | int | None = None, ts: float | None = None,
              record_life: bool = True, capture_live: bool = False,
              root_chat_id=None, principal_id=None, ordinary_root: bool = False) -> None:
    """Добавить строку в буфер и пометить чат к персисту (§1).

    PASS 9.0: заодно фиксируем время/автора последней строки в buf_meta.json —
    буфер строк времени не хранит, а boot-sweep после рестарта должен знать возраст."""
    buffer = _buf[chat_id]
    message_ids = _buffer_message_ids[chat_id]
    while len(message_ids) < len(buffer):
        message_ids.appendleft("")
    while len(message_ids) > len(buffer):
        message_ids.popleft()
    if buffer.maxlen is not None and len(buffer) >= buffer.maxlen and message_ids:
        message_ids.popleft()
    buffer.append(line)
    message_ids.append(str(source_id) if source_id is not None else "")
    _buf_dirty.add(chat_id)
    if record_life and not _under_tests():
        actor = author or line.split(":", 1)[0]
        direction = "out" if actor.strip().casefold() == "praxis" else "in"
        dedupe = (f"telegram:{chat_id}:{source_id}:{direction}"
                  if source_id is not None else "")
        _record_life_message(
            chat_id, line, actor=actor, direction=direction,
            source_id=source_id, is_dm=is_dm, ts=ts, dedupe_key=dedupe,
            capture_live=capture_live, root_chat_id=root_chat_id,
            principal_id=principal_id, ordinary_root=ordinary_root,
        )
    try:
        bufstore.meta_update(chat_id, author=author or line.split(":", 1)[0],
                             is_dm=is_dm, name=name)
    except Exception:
        log.debug("buf_meta не записалась [%s]", chat_id, exc_info=True)


def _group_native_persistence_enabled(chat_id: str) -> bool:
    """Select split native persistence only for the fully ready exact canary pair."""
    if str(chat_id) != '-1001240718803':
        return False
    try:
        import keat_readiness
        import keat_runtime
        return bool(
            keat_runtime.staged('group', '-1001240718803') and
            keat_readiness.receipt()['serve_ready'])
    except Exception:
        return False


def _keat_group_user_principal(sender, sender_id):
    """Return an exact Telegram User principal; reject channel/anonymous authorship."""
    try:
        from telethon.tl.types import User
        if not isinstance(sender, User) or isinstance(sender_id, bool):
            return None
        principal = int(sender_id)
        if principal <= 0 or int(getattr(sender, 'id', 0)) != principal:
            return None
        return principal
    except (TypeError, ValueError, OverflowError):
        return None


def _persist_sent_reply(chat_id, chunks, message_ids, *, is_dm):
    """Keep the legacy buffer entry, but persist each native receipt separately.

    The accepted prefix is the logical send (including on partial failure). A
    different send gets a different batch even if its text happens to be equal.
    """
    sent_reply = "".join(chunks)
    meta = ({"source_id": ",".join(message_ids), "ts": time.time()}
            if message_ids else {})
    native = (len(message_ids) == len(chunks) and
              (is_dm or _group_native_persistence_enabled(str(chat_id))))
    _buf_push(chat_id, f"Praxis: {sent_reply}", author="Praxis", is_dm=is_dm,
              record_life=not native, **meta)
    if native and not _under_tests():
        import uuid
        batch_id = uuid.uuid4().hex
        for index, (chunk, mid) in enumerate(zip(chunks, message_ids)):
            _record_life_message(
                chat_id, f"Praxis: {chunk}", actor="Praxis", direction="out",
                source_id=mid, is_dm=is_dm, ts=time.time(),
                dedupe_key=f"telegram:{chat_id}:{mid}:out", capture_live=True,
                logical_send=dict(id=batch_id, index=index, count=len(chunks)),
                root_chat_id=(chat_id if not is_dm else None),
                principal_id=(_self_id if not is_dm else None),
                ordinary_root=(not is_dm and str(chat_id) == '-1001240718803'))


def _telegram_handle(entity) -> str | None:
    """Доступный для @-упоминания username из Telethon entity.

    У новых аккаунтов Telegram основной ``.username`` может быть пустым, а живой
    публичный ник лежит в списке ``.usernames``. Не берём неактивные исторические
    ники: ими нельзя надёжно упомянуть человека.
    """
    primary = str(getattr(entity, "username", "") or "").strip().lstrip("@")
    if primary:
        return primary
    for item in getattr(entity, "usernames", None) or ():
        if not getattr(item, "active", False):
            continue
        handle = str(getattr(item, "username", "") or "").strip().lstrip("@")
        if handle:
            return handle
    return None


def _sender_label(sender) -> str:
    """Имя автора с доступным @-ником, чтобы голос мог адресовать его в Telegram."""
    name = " ".join(filter(None, [getattr(sender, "first_name", None),
                                  getattr(sender, "last_name", None)]))
    handle = _telegram_handle(sender)
    if name:
        return f"{name} (@{handle})" if handle else name
    return f"@{handle}" if handle else "кто-то"


def _sender_name(m) -> str:
    """Имя автора Telethon-сообщения; её собственные — 'Praxis'."""
    if getattr(m, "out", False):
        return "Praxis"
    return _sender_label(getattr(m, "sender", None))


def _media_tag(m) -> str:
    """Медиа-конверт (PASS_3 §2): структурный тег, чтобы она видела, ЧТО пришло, даже без текста.

    Порядок проверок важен: стикеры/гифки — тоже документы с видео-атрибутом."""
    try:
        if getattr(m, "photo", None) is not None:
            return "[Изображение]"
        if getattr(m, "voice", None) is not None:
            return "[Голосовое]"
        if getattr(m, "video_note", None) is not None:
            return "[Видеосообщение]"
        if getattr(m, "sticker", None) is not None:
            return "[Стикер]"
        if getattr(m, "gif", None) is not None:
            return "[GIF]"
        if getattr(m, "video", None) is not None:
            return "[Видео]"
        if getattr(m, "audio", None) is not None:
            return "[Аудио]"
        if getattr(m, "document", None) is not None:
            fname = getattr(getattr(m, "file", None), "name", None)
            return f"[Документ: {fname}]" if fname else "[Документ]"
        if getattr(m, "media", None) is not None:
            return "[Медиа]"
    except Exception:
        pass
    return ""


def _typed_media_kind(m) -> str | None:
    """Только то, что реально понимает новый тракт: фото и голос/аудио (не видео/GIF)."""
    if getattr(m, "photo", None) is not None:
        return "photo"
    if getattr(m, "voice", None) is not None or getattr(m, "audio", None) is not None:
        return "audio"
    return None


class _CappedMediaSink:
    """Synchronous file-like sink Telethon can stream into without a RAM-sized blob."""

    def __init__(self, path: Path, limit: int):
        self.path = path
        self.limit = int(limit)
        self.total = 0
        self._file = path.open("wb")

    def write(self, chunk) -> int:
        data = bytes(chunk)
        if self.total + len(data) > self.limit:
            raise media_core.MediaTooLargeError(
                f"download exceeds {self.limit} byte media cap")
        written = self._file.write(data)
        self.total += written
        return written

    def flush(self) -> None:
        self._file.flush()

    def fileno(self) -> int:
        return self._file.fileno()

    def close(self) -> None:
        if not self._file.closed:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


async def _capture_typed_media(msg, *, chat_id: str, scope: str,
                               caption: str = "") -> tuple[media_core.MediaRef | None, str]:
    """Stream through a hard cap, verify magic/quota, then bind to the scoped spool."""
    kind = _typed_media_kind(msg)
    if kind is None:
        return None, ""
    f = getattr(msg, "file", None)
    size = int(getattr(f, "size", 0) or 0)
    name = getattr(f, "name", None) or f"telegram{getattr(f, 'ext', '') or ''}"
    temp_path: Path | None = None
    try:
        spool = _media_spool()
        if size:
            spool.check_size(kind, size)
        cap = spool.photo_max_bytes if kind == "photo" else spool.audio_max_bytes
        fd, raw_temp = tempfile.mkstemp(prefix="praxis-media-", suffix=".part")
        os.close(fd)
        temp_path = Path(raw_temp)
        with _CappedMediaSink(temp_path, cap) as sink:
            got = await msg.download_media(file=sink)
            # Hermetic adapters may return bytes instead of honoring a file-like sink.
            if sink.total == 0 and isinstance(got, (bytes, bytearray, memoryview)):
                sink.write(got)
        if not temp_path.is_file() or temp_path.stat().st_size <= 0:
            return None, f"[{('Изображение' if kind == 'photo' else 'Аудио')}: скачать не вышло]"
        ref = spool.ingest_path(
            temp_path, kind=kind, filename=name, chat_id=chat_id,
            message_id=getattr(msg, "id", None), scope=scope, caption=caption or "",
            move=True)
        temp_path = None
        return ref, ""
    except media_core.MediaTooLargeError:
        log.info("медиа сверх лимита [%s]: %s (%d)", chat_id, name, size)
        return None, f"[{('Изображение' if kind == 'photo' else 'Аудио')}: слишком большое]"
    except media_core.MediaError as e:
        log.info("медиа отклонено [%s]: %s", chat_id, str(e)[:160])
        return None, f"[{('Изображение' if kind == 'photo' else 'Аудио')}: формат не поддержан]"
    except Exception:
        log.warning("скачивание мультимедиа упало [%s]", chat_id, exc_info=True)
        return None, f"[{('Изображение' if kind == 'photo' else 'Аудио')}: скачать не вышло]"
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _format_messages(msgs) -> list[str]:
    """Telethon-сообщения -> строки 'Имя: текст' в хронологии (старые сверху). Её — 'Praxis'.

    Медиа получают структурный тег; реплаи — маркер '(в ответ <кому>)', если цель в выборке."""
    msgs = list(msgs)
    by_id = {}
    for m in msgs:
        mid = getattr(m, "id", None)
        if mid is not None:
            by_id[mid] = _sender_name(m)
    lines = []
    for m in msgs:
        text = getattr(m, "message", None) or ""
        tag = _media_tag(m)
        if not text and not tag:
            continue
        rid = getattr(m, "reply_to_msg_id", None)
        mark = (f" (в ответ {by_id[rid]})" if rid in by_id else " (в ответ)") if rid else ""
        body = " ".join(x for x in (tag, text) if x)
        lines.append(f"{_sender_name(m)}{mark}: {body}")
    return lines


def _hot_window() -> int:
    """Bound the legacy text view equally for local tape and cold Telegram fetch.

    This is a read window, not a retention or compaction threshold. 12.09: то же окно
    стоит и на ролевой ленте (`_dm_dialogue`) — прежде она резала по HOT_HARD_HI, и
    PRAXIS_LAST_N до модели не доезжал. Room-policy snapshots keep their own caps.
    """
    return min(memory_life.HOT_HARD_HI, memory_life.HOT_HI,
               max(memory_life.HOT_LO, LAST_N))


def _tape_cut_lines(lines: list[str]) -> list[str]:
    """Свежие строки сплошной ленты под потолок memory_life.TAPE_CHARS (последняя — всегда)."""
    limit = memory_life.TAPE_CHARS
    if limit <= 0 or not lines:
        return list(lines)
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        cost = len(line) + 1
        if kept and used + cost > limit:
            break
        kept.append(line)
        used += cost
    kept.reverse()
    return kept


def _tape_cut_turns(turns):
    """То же для групповой ленты с авторством: (is_self, line, role_line) — по role_line."""
    limit = memory_life.TAPE_CHARS
    turns = list(turns or ())
    if limit <= 0 or not turns:
        return turns
    kept = []
    used = 0
    for item in reversed(turns):
        cost = len(str(item[2] if len(item) > 2 else item[1])) + 1
        if kept and used + cost > limit:
            break
        kept.append(item)
        used += cost
    kept.reverse()
    return kept


async def _last_n_text(chat_id: str) -> str:
    """Read the bounded legacy hot-text view without mutating persisted tape.

    The incoming handler has already observed and appended the current message before this call,
    so the buffer is the exact live slice. Telegram remains the cold-start fallback.
    """
    limit = _hot_window()
    local = _tape_cut_lines(list(_buf[chat_id])[-limit:])
    if local:
        return "\n".join(local)
    meta = _meta.get(chat_id, {})
    entity = meta.get("entity", None)
    if entity is not None:
        try:
            topic_id = meta.get("topic_id")
            kwargs = {"reply_to": int(topic_id)} if topic_id is not None else {}
            msgs = await client.get_messages(entity, limit=limit, **kwargs)
            lines = _format_messages(reversed(list(msgs)))  # get_messages отдаёт новые сверху
            if lines:
                return "\n".join(_tape_cut_lines(lines[-limit:]))
        except Exception:
            log.warning("live-fetch контекста упал [%s] — фолбэк на буфер", chat_id, exc_info=True)
    return ""


def _dialogue_roles_on() -> bool:
    """Рычаг отката без передеплоя. По умолчанию ВКЛЮЧЕНО.

    Это не ограничитель её восприятия: и с ролями, и без них в модель уезжает один и тот
    же разговор. Разница только в том, чьей репликой приезжают её собственные слова.
    """
    return os.getenv("PRAXIS_DIALOGUE_ROLES", "1").strip().lower() not in ("0", "false", "no", "off")


def _strip_author_prefix(line: str, actor: str) -> str:
    """«Praxis: текст» -> «текст». Префикс — служебный, и в её собственной реплике ему
    не место: она не подписывает свои слова своим именем, когда говорит."""
    head = f"{actor}: "
    return line[len(head):] if actor and line.startswith(head) else line


def _turns_to_dialogue(turns) -> tuple[list[dict], str]:
    """Лента с авторством -> (история ролями, то-на-что-она-отвечает-сейчас).

    Одно правило на личку и на группу: граница проходит по ЕЁ ПОСЛЕДНЕЙ реплике.
    Всё до неё включительно — разговор, всё после — то, что пришло и ждёт ответа.
    Если она здесь ещё не говорила либо последней говорила она сама — ролей нет и
    вызывающий идёт прежним путём: пустое user-сообщение хуже сплошного текста.
    """
    rows = [(bool(is_self), str(text)) for is_self, text in turns if str(text).strip()]
    if not rows:
        return [], ""
    last_self = -1
    for i, (is_self, _text) in enumerate(rows):
        if is_self:
            last_self = i
    if last_self < 0:
        return [], ""
    history: list[dict] = []
    run_role, run_lines = "", []

    def flush() -> None:
        if run_lines:
            history.append({"role": run_role, "content": "\n\n".join(run_lines).strip()})

    for is_self, text in rows[:last_self + 1]:
        role = "assistant" if is_self else "user"
        if role != run_role:
            flush()
            run_role, run_lines = role, []
        run_lines.append(text)
    flush()
    if not history:
        return [], ""
    current = "\n".join(text for _is_self, text in rows[last_self + 1:])
    if not current.strip():
        return [], ""
    return history, current


def _group_dialogue(turns) -> tuple[list[dict], str]:
    """Групповая лента ролями. Пустой снимок авторства -> прежний путь."""
    if not _dialogue_roles_on() or not turns:
        return [], ""
    try:
        return _turns_to_dialogue([(is_self, role_line)
                                   for is_self, _line, role_line in _tape_cut_turns(turns)])
    except Exception:
        log.warning("роли группового разговора не собрались — иду сплошным текстом",
                    exc_info=True)
        return [], ""


def _dm_dialogue(chat_id: str, *, occurrence_sidecar: dict | None = None) -> tuple[list[dict], str]:
    """Разговор в личке ролями: (история, то-на-что-она-отвечает-сейчас).

    Граница проведена там, где она и лежит по смыслу: всё до её последней реплики
    включительно — история, всё после — то, что пришло и ждёт ответа. Если она в этом
    чате ещё не говорила, истории нет и весь разговор остаётся текущим сообщением —
    ровно нынешнее поведение.

    Возвращает ([], "") если ролей не собрать: вызывающий тогда работает по-старому.
    """
    if occurrence_sidecar is not None:
        occurrence_sidecar.clear()
    if not _dialogue_roles_on():
        return [], ""
    try:
        # 12.09: окно то же, что у сплошной ленты (PRAXIS_LAST_N), и потолок в знаках
        # (PRAXIS_TAPE_CHARS) — то, что не влезло, лежит в сводке и достаётся recall.
        rows = memory_life.tape_window(memory_life.hot_records(chat_id, _hot_window()))
    except Exception:
        log.warning("роли разговора не собрались [%s] — иду сплошным текстом", chat_id,
                    exc_info=True)
        return [], ""
    if not rows:
        return [], ""
    # ⚠ Подпись снимается с ОБЕИХ сторон, и это не симметрия ради красоты.
    # 03.08 21:44 она ответила Егору в личке, говоря о нём в ТРЕТЬЕМ ЛИЦЕ: «вы сейчас
    # чините ровно то, от чего Егору стало не по себе» — обращаясь при этом к нему.
    # Причина ровно здесь: свою реплику я от подписи освободил, а его — нет, и роль
    # `user` приезжала с текстом «Yegor Kosyrev (@tatarskiy_e4pochmak): …». Пока весь
    # разговор был одним блобом, имя читалось как стенограмма. Когда роль `user` СТАЛА
    # собеседником, имя перед его же словами превращает его в пересказываемое третье
    # лицо: кадр говорит «мне докладывают слова Егора», а не «Егор говорит мне».
    # В личке собеседник ровно один, и кто он — уже сказано ролью, `speaker` и рамкой
    # присутствия. В ГРУППЕ подпись остаётся: там говорящих много, и без имени реплика
    # безадресна.
    turns = [(row["direction"] == "out",
              _strip_author_prefix(row["line"], row["actor"])) for row in rows]
    from logical_send import batch_groups
    batches = batch_groups([(r.get("meta") or {}).get("logical_send")
                            if r["direction"] == "out" else None for r in rows])
    logical_turns = [(turns[g[0]][0], "".join(turns[i][1] for i in g)) for g in batches]
    history, current = _turns_to_dialogue(logical_turns)
    if occurrence_sidecar is not None:
        occurrence_sidecar.clear()
        # Positions, never matching text: repeated equal messages remain distinct.
        admitted = [(rows[i], turns[i]) for g, turn in zip(batches, logical_turns)
                    if turn[1].strip() for i in g]
        if history and current and admitted:
            captured = [bool((r.get("meta") or {}).get("keat_occurrence"))
                        for r, _ in admitted]
            first = 0 if all(captured) else max(
                (i for i, present in enumerate(captured) if not present), default=-1) + 1
            # ⚠ 26.09: СУЖАТЬ ЛЕНТУ ДО ЗАХВАЧЕННОГО ХВОСТА ЗАПРЕЩЕНО. Её ответы уходят рукой
            # `reply` и в реестр захвата не пишутся, поэтому «хвост после последнего
            # незахваченного» — это строки владельца после её последнего ответа. Замер
            # 21–25.09: 21 обслуженный ход лички из 21 нёс в модель ОДНУ реплику, при том что
            # обычная лента того же хода — 49–78 сообщений (её история выпадала целиком).
            # Проекция КЕАТ отдаётся, только если захвачена вся лента; иначе ход идёт прежним
            # путём — с историей. Кадр v6 владельца от этого не зависит.
            if first:
                log.info("KEAT личка [%s]: захвачено %d из %d строк ленты — проекция не "
                         "отдаётся, ход идёт полной лентой", chat_id,
                         sum(captured), len(captured))
                return history, current
            suffix = admitted[first:]
            # A captured native suffix is a valid narrowing even while older hot
            # history predates capture.  Keep the full dialogue as the legacy
            # fallback; these extra fields address only the provider candidate.
            if suffix and all((r.get("meta") or {}).get("keat_occurrence")
                              for r, _ in suffix):
                suffix_turns = [turn for _, turn in suffix]
                self_positions = [i for i, turn in enumerate(suffix_turns) if turn[0]]
                if self_positions:
                    last_self = self_positions[-1]
                    candidate_history, candidate_current = _turns_to_dialogue(suffix_turns)
                else:
                    last_self = -1
                    candidate_history = []
                    candidate_current = "\n".join(turn[1] for turn in suffix_turns)
                if candidate_current.strip():
                    groups, role = [], None
                    for row, turn in suffix[:last_self + 1]:
                        if turn[0] != role:
                            groups.append([])
                            role = turn[0]
                        groups[-1].append(row["meta"]["keat_occurrence"])
                    occurrence_sidecar.update(history=groups, current=[
                        row["meta"]["keat_occurrence"] for row, _ in suffix[last_self + 1:]])
                    # 26.09 (ревью W1 S8): ветки `projection_history` здесь больше нет — сюда
                    # доходит только полностью захваченная лента (`first == 0`, см. выше), и
                    # сужать проекции нечего.
    return history, current


async def _chat_descriptor(event, chat_id: str) -> dict:
    """§2: осознанность чата — {title, kind, size}, кэш на чат (без API-запроса на каждое сообщение).

    title/size тянем один раз при первом сообщении из чата; для больших групп число участников
    берём дешёвым GetParticipants(limit=0).total (счётчик, не выкачивая список)."""
    d = _chat_desc.get(chat_id)
    if d is not None:
        return d
    kind = "dm" if event.is_private else "group"
    title = size = None
    try:
        chat = await event.get_chat()
        title = getattr(chat, "title", None)  # у групп/каналов; в личке None
        size = getattr(chat, "participants_count", None)
        if kind == "group" and not size:
            try:
                size = (await client.get_participants(chat, limit=0)).total
            except Exception:
                size = None
    except Exception:
        log.debug("chat descriptor [%s] не получен", chat_id, exc_info=True)
    d = {"title": title, "kind": kind, "size": size}
    _chat_desc[chat_id] = d
    if title:
        # Имя места живёт на диске, а не только в памяти процесса: без этого адресная
        # книга её эпохи состоит из одних цифр (замер 20.08 — двенадцать «Комнат <id>»).
        # Пишется один раз поверх машинной заглушки и никогда поверх живого имени.
        try:
            if rooms.remember_title(chat_id, title):
                log.info("имя места записано в профиль [%s]: %s", chat_id, title)
        except Exception:
            log.debug("имя места [%s] не записалось", chat_id, exc_info=True)
    return d


def _dm_policy() -> str:
    """`open` (как было) или `whitelist`. Неизвестное значение — как `open`.

    Неизвестное значение НЕ закрывает личку: опечатка в переменной среды не должна
    молча обрывать её разговоры с людьми. Отказ дороже ошибки настройки.
    """
    raw = str(os.getenv("PRAXIS_DM_POLICY", "open") or "open").strip().lower()
    return "whitelist" if raw in {"whitelist", "closed", "allowlist"} else "open"


def _dm_allow_ids() -> set[str]:
    """Кому открыта личка: переменная среды плюс файл состояния.

    Файл — чтобы список правился без передеплоя (и из Пульта позже); переменная —
    чтобы он был виден в паспорте среды. Оба читаются, ни один не главнее.
    """
    ids: set[str] = set()
    raw = str(os.getenv("PRAXIS_DM_ALLOW", "") or "")
    ids |= {p.strip() for p in re.split(r"[\s,;]+", raw) if p.strip()}
    try:
        path = memory_life.BASE / "memory" / ".state" / "dm-allow.json"
        if path.is_file():
            got = json.loads(path.read_text(encoding="utf-8"))
            rows = got.get("allow") if isinstance(got, dict) else got
            ids |= {str(x).strip() for x in (rows or []) if str(x).strip()}
    except Exception:
        log.warning("список допуска личек не прочитался — считаю его пустым", exc_info=True)
    return ids


def _dm_allowed(sender_id, *, family: bool) -> bool:
    """Пускать ли этого человека в личку. Владелец сюда не доходит (проверен выше).

    ⚠ ЗНАКОМЫЕ НЕ ПРОХОДЯТ, и это названо, а не подразумевается: под `whitelist`
    отвечает она владельцу, семье и тем, кто назван по id. Человек из её досье, но
    не из семьи и не из списка, ответа не получит — его сообщение будет записано, а
    ход не родится. Решение Егора 12.09: рычаг есть, умолчание — open.
    """
    if _dm_policy() == "open":
        return True
    if family:
        return True
    return str(sender_id) in _dm_allow_ids()


def _note_dm_refused(chat_id, sender_id, name: str, *, already_noted: bool) -> None:
    """Отказ НАЗВАН: и в журнале, и в её памяти о первом контакте.

    Гейт, который молчит, превращает закрытую личку в «её никто не пишет». Она
    должна знать, что к ней приходили и кого не пустили — иначе решение открыть
    человеку принимать не на чем.

    `already_noted` — когда первый контакт уже отмечен веткой `admission` выше:
    второй вызов `social.note_unknown` посчитал бы одно сообщение дважды.
    """
    log.info("личка закрыта: %s (%s) — политика whitelist", name, sender_id)
    if already_noted:
        return
    try:
        social.note_unknown(sender_id, praxis_time.day_key())
    except Exception:
        log.warning("отметка первого контакта не записалась [%s]", sender_id, exc_info=True)


def _wants_inbox(is_private: bool, is_owner: bool, msg, addressed: bool = False) -> bool:
    """Archive every Telegram document that reaches a live/admitted chat.

    Addressing controls whether Praxis speaks, not whether she was allowed to notice an
    attachment.  Photos/audio already take the typed media path below and are therefore
    excluded here to avoid downloading the same bytes twice.
    """
    if _typed_media_kind(msg) is not None:
        return False
    has_file = (getattr(msg, "document", None) is not None or
                getattr(msg, "photo", None) is not None)
    return bool(has_file)


async def _inbox_download(msg, *, scope: str = "", chat_id: str | int | None = None,
                          chat_kind: str = "", chat_label: str = "") -> str | None:
    """Скачать Telegram-файл в scoped workspace/inbox. -> буфер-тег ('[Файл: имя → путь]' или честный
    отказ по капам) | None при неожиданном сбое (остаётся обычный медиа-тег)."""
    try:
        f = getattr(msg, "file", None)
        name = getattr(f, "name", None) or ("photo" + str(getattr(f, "ext", None) or ".jpg"))
        size = int(getattr(f, "size", 0) or 0)
        path, why = workshop.inbox_accept(
            name, size, scope=scope, chat_id=chat_id,
            chat_kind=chat_kind, chat_label=chat_label)
        if path is None:
            return f"[Файл: {name} — {why}]"
        got = await msg.download_media(file=str(path))
        if not got:
            return f"[Файл: {name} — скачать не вышло]"
        rel = Path(str(got)).resolve().relative_to(workshop.BASE.resolve()).as_posix()
        log.info("inbox: сохранила Telegram-файл %s → %s", name, rel)
        return f"[Файл: {name} → {rel}]"
    except Exception:
        log.exception("inbox: скачивание Telegram-файла упало")
        return None


class _DeadRoomFilter(logging.Filter):
    """PASS 10.8: banned/private-канал в catch-up тракте Telethon (прод: одна и та же
    строка каждый час, комната обезличена). Первое появление: mode=dead в профиле
    + одна строка в журнал; повторы для уже мёртвой комнаты глушатся из лога."""

    _RE = re.compile(r"Account is now banned in (\d+)|"
                     r"channel (\d+).{0,40}(?:private|forbidden)", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            m = self._RE.search(record.getMessage())
        except Exception:
            return True
        if not m:
            return True
        cid = "-100" + (m.group(1) or m.group(2))
        try:
            if rooms.profile_read(cid)["mode"] == "dead":
                return False  # уже знаем — не засорять лог
            rooms.set_mode(cid, "dead", reason="Telegram: канал недоступен (banned/private)",
                           set_by="praxis")
            agent.tool_journal(f"[комната] {cid} мертва — Telegram отдаёт banned/private; "
                               "перестаю её слушать", salience=2)
            log.warning("комната %s помечена dead — дальнейший banned-спам глушится", cid)
        except Exception:
            log.debug("dead-room фильтр не смог пометить %s", cid, exc_info=True)
        return True  # первую строку показать честно


def _install_dead_room_filter() -> None:
    """Вешаем фильтр на хендлеры root-логгера: telethon-логгеры — дети root, записи
    проходят через его хендлеры (фильтры логгеров не наследуются, хендлеров — да)."""
    f = _DeadRoomFilter()
    for h in logging.getLogger().handlers:
        h.addFilter(f)


@client.on(events.ChatAction)
async def on_chat_action(event) -> None:
    """A real Telegram membership event admits the room to Praxis's perception."""
    try:
        if not (getattr(event, "user_added", False) or getattr(event, "created", False)):
            return
        uids = set(getattr(event, "user_ids", None) or [])
        uid = getattr(event, "user_id", None)
        if uid is not None:
            uids.add(uid)
        if not _self_id or _self_id not in uids:
            return
        await _newcomer(event)
    except Exception:
        log.exception("новичок-протокол упал")


async def _newcomer(event) -> None:
    """Вход в новую группу: room-профиль mode=observer, предыстория (её же аккаунтом,
    ровно то, что видит любой новый участник) → компакт-сводка в профиль (НЕ в буфер,
    НЕ в журнал), затем один проход «осмотрись» (нормы + опц. one-shot приветствие)
    и inbox-карточка владельцу."""
    chat_id = str(event.chat_id)
    owner_added = False
    try:
        adder = await event.get_added_by()
        owner_added = bool(
            adder is not None and OWNER_ID != 0 and getattr(adder, "id", 0) == OWNER_ID
        )
        if rooms.add_room(chat_id):
            log.info("новичок-протокол [%s]: реальное членство добавлено в каталог", chat_id)
    except AttributeError:
        pass  # событие без get_added_by (нечем проверить — allowlist не трогаем)
    except Exception:
        log.debug("get_added_by не удался [%s]", chat_id, exc_info=True)
    title = None
    ent = None
    try:
        ent = await event.get_chat()
        title = getattr(ent, "title", None)
    except Exception:
        log.debug("get_chat в новичок-протоколе не удался", exc_info=True)
    await _initialize_joined_room(chat_id, ent or event.chat_id, title=title,
                                  allow=True,
                                  set_by="owner" if owner_added else "praxis")


async def _initialize_joined_room(chat_id: str, entity, *, title: str | None = None,
                                  allow: bool = False, set_by: str = "praxis") -> None:
    """Initialize a room joined either by ChatAction or a sovereign link tool.

    This is deliberately idempotent: Telethon may deliver the ChatAction after
    ImportChatInviteRequest already returned.  Rejoining an explicitly departed
    room revives it while preserving its accumulated room memory.
    """
    set_by = "owner" if set_by == "owner" else "praxis"
    if allow:
        rooms.add_room(chat_id)
    prof = rooms.profile_read(chat_id)
    if prof["exists"] and prof["structured"]:
        if prof.get("mode") == "dead":
            rooms.set_mode(
                chat_id, "normal", reason="вернулась", set_by=set_by,
            )
            rooms.owner_card(chat_id, "join", f"снова вошла в «{title or chat_id}»; режим normal")
        else:
            log.info("новичок-протокол [%s]: профиль уже есть — не сбрасываю (re-add)", chat_id)
        return
    rooms.set_mode(chat_id, "normal", reason="", set_by=set_by)
    # 16.09: новая комната больше не открывается «могу заговорить сама». Протокол не
    # пишет участие в профиль вовсе — пусть действует умолчание `rooms.default_policy`
    # (`addressed`, рычаг `PRAXIS_ROOM_ENGAGEMENT`). Прежняя строка вписывала
    # `reflective` ЯВНО и тем перебивала рычаг владельца: в проде стояло
    # `PRAXIS_ROOM_ENGAGEMENT=addressed`, а каждая новая комната всё равно рождалась
    # разговорчивой, и её потом правили руками.
    log.info("новичок-протокол [%s]: вошла в «%s», режим normal, участие — по умолчанию (%s)",
             chat_id, title or "?", rooms.default_policy()["engagement"])
    lines: list[str] = []
    try:
        msgs = await client.get_messages(entity, limit=BACKFILL_N)
        for m in reversed(list(msgs or [])):
            t = getattr(m, "message", "") or ""
            if t.strip():
                lines.append(f"{_sender_name(m)}: {t}")
    except Exception:
        log.info("новичок-протокол [%s]: история недоступна (скрыта/приватна)", chat_id)
    if lines:
        summ = await asyncio.to_thread(agent.backfill_summary, lines)
        text = ("_(прочитанное, не пережитое)_\n" + summ) if summ else "истории не видно"
    else:
        text = "истории не видно"
    rooms.section_set(chat_id, "Сводка предыстории", text)
    norms, greeting = await asyncio.to_thread(agent.lookaround, chat_id, title)
    if norms:
        rooms.section_set(chat_id, "Нормы и атмосфера", norms)
    greeted = rooms.profile_read(chat_id)["header"].get("greeted") == "yes"
    # ⚠ Под контрактом «ответ рукой» `greeting` приходит ПУСТЫМ всегда, и эта ветка не
    # берётся: приветствие она отправляет сама, своей рукой, через ту же исходящую границу,
    # что и всё остальное. Ветка ниже — прежний путь для опущенного рычага: сырой
    # `send_message` мимо гарда, кольца и идемпотентности. Второй такой двери здесь больше
    # не появится; когда рычаг поднимут насовсем, ветка уходит целиком.
    if greeting and not greeted:
        try:
            await client.send_message(entity, greeting)
            rooms.profile_update(chat_id, greeted="yes")
            _buf_push(chat_id, f"Praxis: {greeting}", author="Praxis", is_dm=False)
            log.info("новичок-протокол [%s]: поздоровалась (один раз)", chat_id)
        except Exception:
            log.exception("приветствие не ушло [%s]", chat_id)
    two = " / ".join([l for l in text.splitlines() if l.strip()][:2])[:200]
    rooms.owner_card(chat_id, "join",
                     f"вошла в «{title or chat_id}», профиль готов, режим normal/reflective: {two}")


async def _forum_topic_catalog(entity, peer_id: str, *,
                               hard_cap: int = 2000) -> tuple[list[dict], bool]:
    """Каталог тем комнаты + ДОКАЗАНА ли его полнота. -> (rows, complete).

    ⚠ Её блокер 2 от 22.08: раньше свип резался message-cap'ом бэкфила и безусловно
    объявлялся полным — тема №501 «не существовала» и схлопывалась в комнату. Теперь
    пагинация идёт до доказанного исчерпания (страница короче запрошенной или без
    новых тем) и сверяется с server-side `count`; защитный `hard_cap` остался, но
    упирание в него — явное «неполно»: неполное знание не рождает отрицательного.

    ⚠ Её блокер 3: запись каталога строгая (межпроцессный замок в реестре, OSError
    не глотается) и подтверждается re-read — без подтверждения полнота не заявляется
    и успех не объявляется.
    """

    from telethon.tl.functions.messages import GetForumTopicsRequest

    rows: list[dict] = []
    seen: set[int] = set()
    offset_date = None
    offset_id = offset_topic = 0
    total: int | None = None
    exhausted = False
    while len(rows) < hard_cap:
        page_size = 100
        result = await client(GetForumTopicsRequest(
            peer=entity, offset_date=offset_date, offset_id=offset_id,
            offset_topic=offset_topic, limit=page_size,
        ))
        if total is None:
            raw_count = getattr(result, "count", None)
            total = raw_count if isinstance(raw_count, int) and raw_count >= 0 else None
        page = list(getattr(result, "topics", None) or ())
        fresh = []
        for item in page:
            try:
                topic_id = int(getattr(item, "id"))
            except (TypeError, ValueError):
                continue
            if topic_id <= 0 or topic_id in seen:
                continue
            seen.add(topic_id)
            # Нет настоящего title — нет имени. Выдуманное «topic #N» здесь месяцами
            # выглядело в её кадре как место с настоящим названием.
            title = str(getattr(item, "title", "") or "").strip()
            date = getattr(item, "date", None)
            top_message = getattr(item, "top_message", None)
            row = {
                "topic_id": topic_id, "title": title,
                "date": date, "top_message": top_message,
            }
            rows.append(row)
            fresh.append(item)
            _topic_titles[(str(peer_id), topic_id)] = title
            await asyncio.to_thread(
                group_context.record_topic, peer_id, topic_id, title,
                timestamp=date, message_id=topic_id,
                origin="forum_list",
            )
        if len(page) < page_size or not fresh:
            exhausted = True
            break
        last = fresh[-1]
        offset_date = getattr(last, "date", None)
        offset_id = int(getattr(last, "top_message", 0) or 0)
        offset_topic = int(getattr(last, "id", 0) or 0)
    complete = exhausted and (total is None or len(rows) >= total)
    if not complete:
        log.warning("каталог тем [%s]: свип НЕПОЛНЫЙ (собрано %d, count=%s, "
                    "exhausted=%s) — полнота не заявляется", peer_id, len(rows),
                    total, exhausted)
    # Ответ на самый важный вопрос про комнату больше не выбрасывается в локальную
    # переменную: свип уезжает в durable-карту реестра, и построение ключа хранения
    # сверяется с ним, а не с заголовком ответа.
    await asyncio.to_thread(
        telegram_routes.observe_topics, peer_id,
        {row["topic_id"]: {"title": row["title"], "top_message": row["top_message"]}
         for row in rows},
        source="get_forum_topics", complete=complete)
    stored = await asyncio.to_thread(telegram_routes.topics_of, peer_id)
    lost = [row["topic_id"] for row in rows if int(row["topic_id"]) not in stored]
    if lost:
        raise OSError(f"каталог тем [{peer_id}]: запись не подтвердилась re-read "
                      f"(потеряно {len(lost)} тем)")
    _invalidate_topic_cache(str(peer_id))
    return rows, complete


async def _backfill_group_context(peer_id: str, entity, *, limit: int) -> dict:
    """Archive a bounded newest slice, idempotently and without any model call.

    Repeating after a crash is the resume protocol: canonical message ids de-duplicate,
    and the archive's message count remains the hard ceiling.
    """

    peer = str(peer_id)
    cap = max(0, min(rooms.BACKFILL_MAX, int(limit)))
    before = await asyncio.to_thread(group_context.archived_message_count, peer)
    if cap <= 0 or before >= cap:
        return {"peer_id": peer, "limit": cap, "before": before, "added": 0,
                "complete": before >= cap if cap else True}
    # ⚠ Здесь `except Exception` сплющивал ТРИ разных факта в один пустой список:
    # «Telegram говорит, что это не форум» (CHANNEL_FORUM_MISSING — прямой и durable
    # ответ), «сеть/флуд» (не говорит ничего) и «форум без тем». Ответ на самый важный
    # вопрос про комнату приходил сюда бесплатно и выбрасывался в локальную переменную.
    try:
        # ⚠ Свип каталога больше НЕ связан message-cap'ом бэкфила (её блокер 2):
        # маленький backfill_limit резал каталог, а срез объявлялся полным.
        topics, catalog_complete = await _forum_topic_catalog(entity, peer)
        await asyncio.to_thread(
            lambda: telegram_routes.observe(
                peer, kind="get_forum_topics_ok",
                detail=f"тем: {len(topics)}" + ("" if catalog_complete
                                                else " (свип неполный)")))
    except Exception as exc:
        topics = []
        missing = type(exc).__name__ == "ChannelForumMissingError" or \
            "CHANNEL_FORUM_MISSING" in str(exc)
        try:
            await asyncio.to_thread(
                lambda: telegram_routes.observe(
                    peer,
                    kind="channel_forum_missing" if missing else "rpc_unavailable",
                    detail=type(exc).__name__))
        except Exception:
            log.debug("реестр маршрутов не принял свидетельство [%s]", peer, exc_info=True)
        # Non-forum groups legitimately reject GetForumTopics.  Their root history is
        # still useful and follows the same canonical path.
        log.info("group backfill [%s]: forum topic list unavailable (%s)",
                 peer, type(exc).__name__)

    messages = []
    async for item in client.iter_messages(entity, limit=cap):
        messages.append(item)
    # Свидетельство об ИСТОРИИ, а не о «сейчас». Прямой ответ Telegram
    # (GetForumTopics) говорит только про текущее состояние комнаты и потому не
    # покрывает старые ключи — а их в AbstractDL 279. Зато мы только что прошли
    # диапазон сообщений: у настоящего форума в нём есть служебные «создана тема»
    # (в Грибнице их 18), у обычной супергруппы — ни одного (на 4138 сообщений).
    # Отдаём реестру именно диапазон: тогда исторические ключи получают ответ, а не
    # «не знаю», и слой B наконец начинает работать на том, ради чего писался.
    ids = [int(m.id) for m in messages if getattr(m, "id", None) is not None]
    if ids:
        openers = sum(1 for m in messages if telegram_topics.is_topic_opener(m))
        # 25.08 transport-gate: тишина не свидетель. Ноль opener-ов в диапазоне —
        # не «комната не форум» (General живёт без TopicCreate, стёртое не оставляет
        # следа): воздерживаемся. Позитивное свидетельство пишет только genuine
        # opener; полный честный проход живёт в history_scan.run_history_scan.
        if openers:
            try:
                await asyncio.to_thread(
                    lambda: telegram_routes.observe(
                        peer,
                        kind="topic_opener_seen",
                        since_message_id=min(ids), until_message_id=max(ids),
                        detail=f"пройдено {len(ids)}, openers {openers}"))
            except Exception:
                log.debug("реестр маршрутов: историческое свидетельство не записалось [%s]",
                          peer, exc_info=True)
        else:
            log.info("group backfill [%s]: %d сообщений без opener-ов — воздерживаюсь "
                     "от форумного вердикта (placement остаётся как есть)", peer, len(ids))
    added = 0
    count = before
    _archived_any = False
    # Знание только что уехало в durable-карту (или было там раньше): ключи бэкфила
    # строятся по POSITIVE-знанию, а не по заголовку. Один снимок на весь проход,
    # через ту же кэш-машинерию — без синхронного диска в event loop и мимо
    # анти-stampede контура (фан-аут REPAIR2).
    catalog, _catalog_complete_known = await _topic_knowledge_cached(peer)
    for msg in reversed(messages):
        if count >= cap:
            break
        mid = getattr(msg, "id", None)
        if mid is None:
            continue
        route = telegram_topics.route_for_message(
            peer, msg, is_private=False, is_forum=_known_forum(peer, msg),
            confirmed_topics=catalog)
        opener = telegram_topics.topic_opener_title(msg)
        topic_title = opener
        if route.topic_id is not None and not topic_title:
            topic_title = _topic_titles.get((peer, int(route.topic_id)), "")
        if opener and route.topic_id is not None:
            await asyncio.to_thread(
                group_context.record_topic, peer, route.topic_id, opener,
                timestamp=getattr(msg, "date", None), message_id=mid,
                origin="opener",
            )
        sender = getattr(msg, "sender", None)
        sender_id = getattr(msg, "sender_id", None) or getattr(sender, "id", None)
        name = _sender_name(msg)
        try:
            accepted = await asyncio.to_thread(
                group_context.observe_message,
                peer_id=peer, topic_id=route.topic_id, message_id=mid,
                sender_id=sender_id, sender_name=name,
                reply_to_message_id=getattr(msg, "reply_to_msg_id", None),
                timestamp=getattr(msg, "date", None),
                edited_at=getattr(msg, "edit_date", None),
                text=getattr(msg, "message", "") or "",
                topic_title=topic_title, media=_media_tag(msg),
                outgoing=bool(getattr(msg, "out", False)),
            )
        except (TypeError, ValueError):
            continue
        if accepted:
            added += 1
            count += 1
            _archived_any = True
    # Границы мест. Реестр уже знает, форум ли комната; но вердикт «форум» сам по себе
    # её не чинит: в General Грибницы 631 сообщение плюс 437 в 37 наших псевдоветках.
    # Кто из веток настоящая тема Telegram, а кто наш артефакт, видно прямо в архиве —
    # по тому, лежит ли корневое сообщение ветки в ней самой. Считаем это здесь, один
    # раз на обход, чтобы на чтении остался просмотр за O(1).
    if _archived_any or before:
        try:
            mapping = await asyncio.to_thread(group_context.branch_containers, peer)
            if mapping:
                await asyncio.to_thread(telegram_routes.observe_branches, peer, mapping)
                log.info("реестр маршрутов [%s]: наших псевдоветок %d", peer, len(mapping))
        except Exception:
            log.debug("границы мест не посчитались [%s]", peer, exc_info=True)
    # Пункт 5, ДАТЧИК. Обе стороны считаются на одних и тех же сообщениях, которые мы
    # только что прошли: сегодняшний маршрут и тот, что был бы при is_forum=False.
    # Ничего не меняет; журнал лежит в .state (прибор, не память).
    try:
        # Живой ряд датчика считается с ЖИВЫМ знанием: вердикт реестра + каталог.
        status, _epoch = await asyncio.to_thread(telegram_routes.status_at, peer)
        nature = (True if status == telegram_routes.TRUE
                  else False if status == telegram_routes.FALSE else None)
        split = await asyncio.to_thread(
            telegram_topics.measure_split, peer, messages,
            is_forum=nature, confirmed_topics=catalog)
        if split.get("messages"):
            context_envelope.record_probe("route_split", split)
            if split["keys_live"] > split["keys_if_not_forum"]:
                log.warning(
                    "route split [%s]: %d сообщений в %d ключах (было бы %d); "
                    "самая крупная ветка держит %.0f%% комнаты",
                    peer, split["messages"], split["keys_live"],
                    split["keys_if_not_forum"], 100 * split["largest_branch_share"])
    except Exception:
        log.debug("датчик расщепления не отработал [%s]", peer, exc_info=True)
    projection = await asyncio.to_thread(group_context.rebuild_projection, peer)
    complete = int(projection.get("message_count") or 0) >= cap or len(messages) < cap
    await asyncio.to_thread(
        group_context.mark_backfill, peer, limit=cap,
        scanned=len(messages), complete=complete,
    )
    log.info("group backfill [%s]: %d -> %d/%d; topics=%d",
             peer, before, projection.get("message_count", 0), cap, len(topics))
    return {
        "peer_id": peer, "limit": cap, "before": before, "added": added,
        "message_count": int(projection.get("message_count") or 0),
        "topics": len(topics), "complete": complete,
    }


def _consume_boundary_reply(chat_id: str, reply_to_id: int | None,
                            sender_id: int | None, *, is_owner: bool) -> bool:
    if is_owner or reply_to_id is None or sender_id is None:
        return False
    now = time.time()
    live = deque(maxlen=8)
    matched = False
    for sent_id, provocateur_id, created_at in _boundary_replies.get(chat_id, ()):
        if now - created_at > _BOUNDARY_REPLY_TTL_S:
            continue
        if not matched and sent_id == reply_to_id and provocateur_id == sender_id:
            matched = True
            continue
        live.append((sent_id, provocateur_id, created_at))
    if live:
        _boundary_replies[chat_id] = live
    else:
        _boundary_replies.pop(chat_id, None)
    return matched


def _should_wake(is_private: bool, addressed: bool, decision: str,
                  room_mode: str = "normal") -> bool:
    """Wake contract: DM/address always wins; reflective rooms batch ambient flow."""
    if is_private or addressed:
        return True
    if decision == "ignore":
        return False
    return str(room_mode or "normal").casefold() == "reflective"


def _room_policy_for_state(chat_id: str | int) -> dict:
    """Resolve a conversation key to its root peer before reading room policy."""

    try:
        peer_id = _route_from_state(chat_id).peer_id
        return rooms.room_policy(peer_id)
    except Exception:
        log.debug("deep-room profile не прочитался [%s]", chat_id, exc_info=True)
        return rooms.default_policy()


def _group_archive_enabled() -> bool:
    """Keep hermetic runner fixtures off the real memory tree unless explicitly opted in."""

    return not _under_tests() or os.getenv("PRAXIS_TEST_GROUP_ARCHIVE") == "1"


def _group_context_snapshot(chat_id: str, policy: dict) -> str:
    """Freeze one topic only; a deep profile reads its larger canonical archive tail."""
    return _group_context_frozen(chat_id, policy)[0]


def _fold_service_rows(rows) -> tuple:
    """Служебные строки ленты едут в кадр ВНУТРИ соседней реплики — не ходами и не в никуда.

    История в два шага. Сначала пометка обреза стояла нулевой строкой с живыми
    числами и рвала кэш-префикс комнаты (числа увезены в хвост, голова стала
    постоянной). Потом c82f38c8 чинил границу ролей — хвостовые числа фабриковали
    «реплику человека» после её ответа — и вырезал из ролевого пути ВСЕ службы
    разом. Цену замерила адверсарка 28.08: модель не видела ни «лента обрезана»,
    ни темы ветки — отвечала на срез как на целую ветку и не знала, в какой теме
    находится.

    Оба требования держатся одновременно: службы НЕ становятся ходами (граница
    ролей цела), но доезжают до модели. Головные — корень ветки, пометка обреза —
    приклеиваются ПЕРЕД первой репликой ленты; хвостовые — точные числа обреза —
    ПОСЛЕ последней, внутри её role_line. `line` не трогается: на нём стоят
    расписки и дифф продолжения разговора. Лента из одних служб отдаёт пустой
    кортеж — ходов не было и нет, прежняя граница дословно."""
    turns: list[tuple[bool, str, str]] = []
    pending_head: list[str] = []
    for row in rows:
        if bool(row.get("service")):
            text = str(row["role_line"])
            if turns:
                is_self, line, role_line = turns[-1]
                turns[-1] = (is_self, line, role_line + "\n" + text)
            else:
                pending_head.append(text)
            continue
        role_line = str(row["role_line"])
        if pending_head:
            role_line = "\n".join((*pending_head, role_line))
            pending_head.clear()
        turns.append((bool(row["self"]), str(row["line"]), role_line))
    return tuple(turns)


def _group_context_frozen(chat_id: str, policy: dict) -> tuple[str, tuple]:
    """Тот же снимок ленты, но ВМЕСТЕ с авторством: (текст, строки-записи).

    Текст — байт в байт прежний: на нём стоят расписки, прожитый ход и исходящая
    граница. Записи нужны, чтобы разложить ту же ленту по ролям, ничего не пересобирая:
    снимок группы заморожен на момент пробуждения, и второй проход по архиву показал бы
    модели уже другой разговор.

    Пустой кортеж записей — честное «авторство неизвестно»: так возвращается запасной
    путь по строковому буферу, где своё от чужого отличается только префиксом. Роли в
    таком проходе не собираются вовсе, и ход идёт как раньше.
    """
    limit = int(policy.get("context_hot") or 0)
    if limit > 0 and _group_archive_enabled():
        route = _route_from_state(chat_id)
        # Слой B: спрашиваем реестр и читаем комнату целиком там, где Telegram ПРЯМО
        # ответил, что форума нет. Никакого «по умолчанию» и никакого угадывания:
        # `unknown` ведёт себя ровно как раньше. Хранение не трогается — меняется
        # только то, сколько своей комнаты она видит, просыпаясь в ветке.
        #
        # ⚠ Границы места считает `telegram_routes.read_scope`, а не этот код. Прежде
        # решение было переписано на месте: спрашивалось про `topic_id` вместо `peer_id`
        # и только при непустой ветке — на корневом ключе слой B не включался вовсе.
        # Читателей этого решения теперь трое; разойтись они могут только молча.
        scope = telegram_routes.read_scope(route.peer_id, route.topic_id)
        summary_chars = int(policy.get("context_summary_chars") or 7000)
        # ⚠ Связывает БЮДЖЕТ СИМВОЛОВ, а не потолок сообщений: при 14 000 в кадр влезало
        # ~45 строк при потолке в 200 сообщений. Поэтому «длиннее» — это про символы.
        # В не-форуме лента должна покрывать РАЗГОВОР места, а не хвост одной ветки:
        # там веток нет, есть одна комната, и в оживлённой комнате 45 строк — это
        # десять минут. Решение Егора 04.08.
        max_chars = max(8_000, summary_chars * 2)
        if scope.whole_room:
            max_chars = max(WHOLE_ROOM_CONTEXT_CHARS, summary_chars * 3)
        # 13.09: лента группы — под СВОЙ рычаг PRAXIS_GROUP_TAPE_CHARS (умолчание 0 = потолок
        # комнаты). Общий TAPE_CHARS=5500 12.09 дал в AbstractDL 10 сообщений за 19 минут
        # вместо 181 за 20 часов: контракт T писался под личку, а лента комнаты — это и есть
        # разговор. Замер до того: архивный снимок 70+ тыс. знаков, ход 46 тыс. токенов.
        if memory_life.GROUP_TAPE_CHARS > 0:
            max_chars = min(max_chars, memory_life.GROUP_TAPE_CHARS)
        # 15.09: ЛЕНТА ЭПОХИ (frame_epoch, рычаг PRAXIS_FRAME_EPOCH + список комнат). Окно не
        # скользит: начало — якорь свёртки memory_life, конец — последняя запись архива, каждая
        # запись отрисована один раз, правки и удаления дописаны там, где пришли. Замер 15.09:
        # под стабильной головой первый вызов хода всё равно платил ~48k свежих токенов —
        # ленту, потому что окно уезжало на первом же сообщении. Потолок в знаках здесь не
        # применяется (слово Егора 15.09: «больше никаких лимитов контекста, только
        # дописывание»); физический предохранитель — frame_epoch.max_chars, и при его
        # срабатывании ход идёт прежним окном, а причина называется в логе.
        if frame_epoch.room_enabled(chat_id):
            anchor = frame_epoch.anchor_for(chat_id)
            if anchor is None:
                log.info("эпоха [%s]: якоря нет (горячее окно пусто) — лента прежним окном", chat_id)
            else:
                try:
                    rows = group_context.epoch_rows(
                        route.peer_id, since_message_id=anchor, topic_id=route.topic_id,
                        whole_room=scope.whole_room, members=scope.members,
                        thread_word=scope.thread_word)
                    size = sum(len(row["line"]) + 1 for row in rows)
                    cap = frame_epoch.max_chars()
                    if rows and (cap <= 0 or size <= cap):
                        _EPOCH_ANCHORS[chat_id] = int(anchor)
                        return "\n".join(row["line"] for row in rows), _fold_service_rows(rows)
                    log.warning("эпоха [%s]: лента с якоря #%s %s — лента прежним окном", chat_id,
                                anchor, ("пуста" if not rows else
                                         f"превысила предохранитель ({size} зн. > {cap})"))
                except Exception:
                    log.exception("эпоха [%s]: лента с якоря не собралась — прежнее окно", chat_id)
            _EPOCH_ANCHORS.pop(chat_id, None)
        try:
            rows = group_context.context_rows(
                route.peer_id, topic_id=route.topic_id, limit=limit,
                max_chars=max_chars,
                whole_room=scope.whole_room, members=scope.members,
                thread_word=scope.thread_word,
            )
            archived = "\n".join(row["line"] for row in rows)
            if archived:
                return archived, _fold_service_rows(rows)
        except Exception:
            log.exception("group archive context не собрался [%s]", chat_id)
    # 25.09: запасная лента комнаты — по порогам ЕЁ окна (hot_bounds), не личечным.
    return "\n".join(_tape_cut_lines(
        list(_buf[chat_id])[-(limit or memory_life.hot_bounds(chat_id)[2]):])), ()


def _group_native_projection(chat_id: str, trigger_mid) -> tuple[list[dict], str, dict]:
    """Freeze a fully captured role projection ending at the trigger message."""
    try:
        # 25.09: проекция комнаты — по её горячему окну (hot_bounds), не по HOT_HARD_HI личек.
        rows = memory_life.hot_records(chat_id, memory_life.hot_bounds(chat_id)[2])
        if not rows or str(rows[-1].get('source_id') or
                           (rows[-1].get('meta') or {}).get('source_id') or '') != str(trigger_mid):
            return [], '', {}
        if not all((row.get('meta') or {}).get('keat_occurrence') for row in rows):
            return [], '', {}
        turns = [(row['direction'] == 'out', row['line']) for row in rows]
        history, current = _turns_to_dialogue(turns)
        if not history or not current.strip():
            return [], '', {}
        last_self = max(i for i, turn in enumerate(turns) if turn[0])
        groups, role = [], None
        for row, turn in zip(rows[:last_self + 1], turns[:last_self + 1]):
            if turn[0] != role:
                groups.append([]); role = turn[0]
            groups[-1].append(row['meta']['keat_occurrence'])
        sidecar = dict(history=groups,
                       current=[row['meta']['keat_occurrence'] for row in rows[last_self + 1:]])
        return history, current, sidecar
    except Exception:
        return [], '', {}


def _group_trigger_snapshot(chat_id: str, *, mid, message_ts: float,
                            kind: str, addressed: bool, query: str,
                            name: str, sender_id, is_owner: bool,
                            known: bool, family: bool) -> GroupWake:
    policy = _room_policy_for_state(chat_id)
    # Авторство замораживается ВМЕСТЕ с лентой, одним чтением архива: пробуждение может
    # ждать в кулдауне минуты, и второй проход показал бы модели уже другой разговор.
    frozen_text, frozen_turns = _group_context_frozen(chat_id, policy)
    native_history, native_current, native_sidecar = _group_native_projection(chat_id, mid)
    return GroupWake(
        message_id=int(mid) if mid is not None else None,
        message_ts=float(message_ts),
        kind=str(kind),
        addressed=bool(addressed),
        query=str(query or "")[:4000],
        speaker=name,
        sender_id=int(sender_id) if sender_id is not None else None,
        owner=bool(is_owner) if addressed else False,
        known=bool(known),
        family=bool(family),
        context_snapshot=frozen_text,
        turns_snapshot=frozen_turns,
        occurrence_sidecar=(dict(native_sidecar, projection_history=native_history,
                                 projection_current=native_current)
                            if native_sidecar else None),
        media_snapshot=tuple(_pending_media.get(chat_id, ())),
        reply_targets_snapshot=tuple(_recent_msgs[chat_id]),
    )


def _addressed_trigger_snapshot(chat_id: str, *, mid, message_ts: float,
                                kind: str, name: str, sender_id,
                                is_owner: bool, known: bool, family: bool) -> GroupWake:
    """Freeze the exact group situation that earned a future model pass.

    Cooldown may delay a turn for minutes. Without this envelope, a sticky
    ``addressed=True`` is later combined with a moving Telegram tail, another
    speaker, newer media, and newer reply targets. The model then appears to
    answer an unrelated message. Only a newer explicit address may replace the
    snapshot; background traffic cannot mutate it.
    """
    return _group_trigger_snapshot(
        chat_id, mid=mid, message_ts=message_ts, kind=kind,
        addressed=True, query="", name=name, sender_id=sender_id,
        is_owner=is_owner, known=known, family=family,
    )


def _note_address_overwritten(chat_id: str, old: GroupWake, new: GroupWake) -> None:
    """Канарейка: одно обращение вытеснило другое, ещё не отработанное.

    Пробуждение на комнату одно. Пока держится кулдаун, второе обращение перезаписывает
    первое, и проход пойдёт по последнему. Сообщение остаётся в контексте комнаты, но
    перестаёт быть обращением: вместе с ним уходят `speaker`, `kind` и `owner` — то есть
    обращение Егора, вытесненное чужим, идёт в проход БЕЗ его полномочий.

    Здесь ничего не чинится, только считается. Какой ход правильный, когда двое обратились
    подряд, — вопрос не механический; чинить его вслепую значит выбрать за неё. Сколько это
    стоит на живом потоке, скажет журнал (`canary.dropped_addresses`).
    """
    try:
        meta = {"dropped": "addressed"}
        if old.owner and not new.owner:
            meta["owner_lost"] = "1"
        perception.note_skip(
            "group_wake", "отложила", chat_id=chat_id,
            detail=(f"обращение #{old.message_id} ({old.kind}, {old.speaker or '?'}) "
                    f"вытеснено #{new.message_id} до прохода"),
            meta=meta,
        )
    except Exception:
        log.debug("канарейка вытесненного обращения не записалась", exc_info=True)


def _install_group_wake(chat_id: str, wake: GroupWake) -> bool:
    """Install a generation without letting ambient traffic replace an address."""

    current = _group_wakes.get(chat_id)
    if current is not None and current.addressed and not wake.addressed:
        return False
    if (current is not None and current.addressed and wake.addressed
            and current.message_id != wake.message_id):
        _note_address_overwritten(chat_id, current, wake)
    _group_wakes[chat_id] = wake
    return True


def _revise_recent_message(chat_id: str, message_id: int, *,
                           author: str = "", gist: str = "",
                           deleted: bool = False) -> None:
    """Keep Telegram reply targets aligned with the current message revision."""

    ring = _recent_msgs[chat_id]
    updated = []
    for mid, old_author, old_gist in ring:
        if int(mid) != int(message_id):
            updated.append((mid, old_author, old_gist))
        elif not deleted:
            updated.append((mid, author or old_author, str(gist or old_gist)[:60]))
    ring.clear()
    ring.extend(updated)


def _buffer_revision_source(chat_id: str, message_id: int) -> str:
    token = str(int(message_id))
    buffer = _buf.get(chat_id, ())
    source_ids = _buffer_message_ids.get(chat_id, ())
    if len(source_ids) != len(buffer):
        return ""
    for value in reversed(source_ids):
        source_id = str(value or "")
        if (source_id == token or source_id.startswith(f"{token}:edit:")
                or source_id == f"{token}:delete"):
            return source_id
    return ""


def _revision_source_rank(source_id: str, *, chat_id: str = "") -> tuple[int, float, int]:
    value = str(source_id or "")
    if ":delete" in value:
        return 2, float("inf"), 0
    if ":edit:" in value:
        revision = value.split(":edit:", 1)[1].rsplit(":", 1)[0]
        order = _revision_order_by_chat_source.get((str(chat_id), value), 0)
        try:
            stamp = datetime.datetime.fromisoformat(revision.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=datetime.timezone.utc)
            return 1, stamp.timestamp(), order
        except (TypeError, ValueError):
            return 1, 0.0, order
    return 0, 0.0, 0


def _revision_is_stale(existing_source_id: str, incoming_source_id: str, *,
                       chat_id: str = "") -> bool:
    existing = _revision_source_rank(existing_source_id, chat_id=chat_id)
    incoming = _revision_source_rank(incoming_source_id, chat_id=chat_id)
    if existing[0] == 2 and incoming[0] < 2:
        return True
    if existing[0] == 1 and incoming[0] == 0:
        return True
    return existing[0] == incoming[0] == 1 and incoming[1:] < existing[1:]


def _replace_buffer_message(chat_id: str, message_id: int, line: str, *,
                            deleted: bool = False, source_id: str = "") -> None:
    """Keep the restart-proof string buffer at one current line per Telegram message."""

    token = str(int(message_id))
    new_source_id = str(source_id or (
        f"{token}:delete" if deleted else f"{token}:edit:current"
    ))
    original = list(_buf[chat_id])
    meta = _buffer_message_ids[chat_id]
    if len(meta) != len(original):
        meta.clear()
        meta.extend("" for _ in original)
    filtered_lines = []
    filtered_meta = []
    for index, prior in enumerate(original):
        prior_source_id = str(meta[index] if index < len(meta) else "")
        if (prior_source_id == token or prior_source_id.startswith(f"{token}:edit:")
                or prior_source_id == f"{token}:delete"):
            continue
        filtered_lines.append(prior)
        filtered_meta.append(prior_source_id)
    _buf[chat_id].clear()
    _buf[chat_id].extend(filtered_lines)
    _buffer_message_ids[chat_id].clear()
    _buffer_message_ids[chat_id].extend(filtered_meta)
    if line:
        _buf[chat_id].append(str(line))
        _buffer_message_ids[chat_id].append(new_source_id)
    _buf_dirty.add(chat_id)


def _drop_pending_message_media(chat_id: str, message_id: int) -> None:
    queue = _pending_media.get(chat_id)
    if not queue:
        return
    keep = [ref for ref in queue if int(getattr(ref, "message_id", -1) or -1) != int(message_id)]
    queue.clear()
    queue.extend(keep)
    if not queue:
        _pending_media.pop(chat_id, None)


def _sync_buffer_from_hot(chat_id: str) -> bool:
    """Rebuild the string mirror from current hot records after a revision."""

    try:
        rows = memory_life.hot_records(chat_id, BUF_MAXLEN)
    except Exception:
        log.debug("hot buffer sync failed [%s]", chat_id, exc_info=True)
        return False
    if not rows:
        return False
    _buf[chat_id].clear()
    _buffer_message_ids[chat_id].clear()
    for row in rows[-BUF_MAXLEN:]:
        source_id = str(row.get("source_id") or "")
        _buf[chat_id].append(str(row.get("line") or ""))
        _buffer_message_ids[chat_id].append(source_id)
        if source_id:
            _persisted_life_sources.add(_life_source_key(chat_id, source_id))
    _buf_dirty.add(chat_id)
    return True


def _group_message_conversations(peer_id: str, message_id: int) -> tuple[str, ...]:
    """Known conversation keys that currently point at one peer/message."""

    peer, mid = str(peer_id), int(message_id)
    found: set[str] = set()
    for chat_id, source_ids in list(_buffer_message_ids.items()):
        buffer = _buf.get(chat_id)
        if buffer is None or len(source_ids) != len(buffer):
            continue
        try:
            same_peer = _route_from_state(chat_id).peer_id == peer
        except Exception:
            same_peer = False
        token = str(mid)
        if same_peer and any(
            str(value or "") == token
            or str(value or "").startswith(f"{token}:edit:")
            or str(value or "") == f"{token}:delete"
            for value in source_ids
        ):
            found.add(str(chat_id))
    for chat_id, wake in list(_group_wakes.items()):
        try:
            same_peer = _route_from_state(chat_id).peer_id == peer
        except Exception:
            same_peer = False
        if same_peer and (wake.message_id == mid
                          or any(int(item[0]) == mid
                                 for item in wake.reply_targets_snapshot)):
            found.add(str(chat_id))
    for chat_id, ring in list(_recent_msgs.items()):
        try:
            same_peer = _route_from_state(chat_id).peer_id == peer
        except Exception:
            same_peer = False
        if same_peer and any(int(item[0]) == mid for item in ring):
            found.add(str(chat_id))
    for chat_id, refs in list(_pending_media.items()):
        try:
            same_peer = _route_from_state(chat_id).peer_id == peer
        except Exception:
            same_peer = False
        if same_peer and any(int(getattr(ref, "message_id", -1) or -1) == mid
                             for ref in refs):
            found.add(str(chat_id))
    return tuple(sorted(found))


def _deletion_projection_conversations(peer_id: str, previous: dict | None) -> tuple[str, ...]:
    """Authoritative state keys for one archived Telegram deletion.

    A message id is only peer-local, not topic-local.  Local buffers can nevertheless
    contain stale copies under many topic-looking keys (for example, old reply-chain
    routes).  They are observations, not routing evidence, and must never turn one
    Telegram deletion into a tombstone fanout.

    The root conversation always receives the terminal marker.  A recorded topic is
    accepted only through the durable place resolver: true topics remain separate,
    while historical pseudo-topics collapse to their room or real containing topic.
    Unreadable/ambiguous route knowledge therefore degrades to the root, not to a newly
    minted topic key.
    """

    peer = str(peer_id)
    found = {peer}
    topic_raw = previous.get("topic_id") if isinstance(previous, dict) else None
    if topic_raw is None or isinstance(topic_raw, bool):
        return tuple(sorted(found))
    try:
        topic_id = int(topic_raw)
    except (TypeError, ValueError):
        return tuple(sorted(found))
    if topic_id <= 0 or topic_id == telegram_topics.GENERAL_TOPIC_ID:
        return tuple(sorted(found))
    candidate = telegram_topics.TopicRoute(peer, topic_id).conversation_id
    try:
        canonical = str(telegram_routes.place_of(candidate) or peer)
        route = _route_from_state(canonical)
    except Exception:
        log.debug("deletion topic route unresolved [%s] #%s", peer, topic_id,
                  exc_info=True)
        return tuple(sorted(found))
    if route.peer_id == peer and route.topic_id is not None:
        found.add(route.conversation_id)
    return tuple(sorted(found))


def _refresh_group_wake_context(chat_id: str, *, message_id: int | None = None,
                                query: str | None = None,
                                author: str | None = None,
                                deleted: bool = False,
                                media_snapshot: tuple[media_core.MediaRef, ...] | None = None) -> bool:
    """Refresh the revision-sensitive fields of a pending wake.

    Authority, media and the addressed message id remain frozen.  The conversation,
    query text, speaker label and reply-target gists track an edit of that same message;
    otherwise a delayed turn would reason over the new body while retrieval and
    attribution still carried the old one.
    """

    wake = _group_wakes.get(chat_id)
    if wake is None:
        return False
    try:
        frozen_text, frozen_turns = _group_context_frozen(
            chat_id, _room_policy_for_state(chat_id),
        )
    except Exception:
        log.exception("group wake revision refresh failed [%s]", chat_id)
        return False
    if _group_wakes.get(chat_id) is not wake:
        return False
    targets = tuple(wake.reply_targets_snapshot)
    if message_id is not None:
        targets = tuple(
            (mid, author or old_author, str(query or old_gist)[:60])
            if int(mid) == int(message_id) else (mid, old_author, old_gist)
            for mid, old_author, old_gist in targets
            if not (deleted and int(mid) == int(message_id))
        )
    changes = {
        "context_snapshot": frozen_text,
        "turns_snapshot": frozen_turns,
        "reply_targets_snapshot": targets,
    }
    if media_snapshot is not None:
        changes["media_snapshot"] = tuple(media_snapshot)
    if message_id is not None and wake.message_id == int(message_id):
        if query is not None:
            changes["query"] = str(query)[:4000]
        if author is not None:
            changes["speaker"] = str(author)
    _group_wakes[chat_id] = replace(wake, **changes)
    return True


def _edit_revision_source_id(message_id: int, edited_at, *, text: str = "",
                             media: str = "") -> str:
    """Stable identity for one Telegram revision, including its visible payload.

    Telegram exposes ``edit_date`` only to whole-second precision in common update
    paths.  Two real edits can therefore share both message id and timestamp.  The
    visible payload digest keeps those revisions distinct while duplicate delivery
    of the same update retains exactly the same source id.
    """

    if isinstance(edited_at, datetime.datetime):
        stamp = edited_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=datetime.timezone.utc)
        revision = stamp.astimezone(datetime.timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
    else:
        revision = str(edited_at or "").strip()
    payload = json.dumps(
        {"media": str(media or ""), "text": str(text or "")},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    payload_sha = hashlib.sha256(payload).hexdigest()[:20]
    return f"{int(message_id)}:edit:{revision}:{payload_sha}"


def _revision_reception_order(peer_id: str, message_id: int, source_id: str) -> int:
    """Return one stable in-process reception order for a concrete edit revision."""
    global _revision_reception_counter
    key = (str(peer_id), int(message_id), str(source_id))
    existing = _revision_reception_orders.get(key)
    if existing is not None:
        return existing
    _revision_reception_counter += 1
    _revision_reception_orders[key] = _revision_reception_counter
    return _revision_reception_counter


@client.on(getattr(events, "MessageEdited", events.NewMessage)())
async def on_edited(event) -> None:
    """Archive an admitted group edit without turning it into a new voice turn.

    Telegram edits retain their original message id.  ``edit_date`` plus a visible
    payload digest is therefore the revision identity in both the canonical group
    archive and the life spine (Telegram may collapse multiple edits into one second).
    This path intentionally does not perform media downloads, contact/authority
    mutations, follow-up matching, panic handling, or any model/wake work.
    """

    is_private = bool(getattr(event, "is_private", False))
    msg = getattr(event, "message", None)
    mid = getattr(msg, "id", None)
    peer_id = str(getattr(event, "chat_id", None))
    try:
        # Telethon message ids are exact positive ``int`` values.  Do not
        # normalize any other shape (including bools or numeric strings) into
        # authority for one occurrence: at the enrolled root it instead means
        # the entire coordinate space must fail closed.
        if type(mid) is not int or mid <= 0:
            if peer_id == '-1001240718803':
                import keat_live
                await asyncio.to_thread(keat_live.invalidate_native, peer_id, None)
            return
        message_id = mid
    except (TypeError, ValueError, OverflowError):
        if peer_id == '-1001240718803':
            import keat_live
            await asyncio.to_thread(keat_live.invalidate_native, peer_id, None)
        return

    # The root peer and Telegram message id are already sufficient to revoke the
    # immutable native occurrence. In particular, edit_date is not authority to
    # keep old bytes alive: some edit update shapes omit it, and such an update
    # must fail closed for KEAT while retaining the legacy handler's old no-op.
    if peer_id == '-1001240718803':
        import keat_live
        await asyncio.to_thread(keat_live.invalidate_native, peer_id, message_id)

    edited_at = getattr(msg, "edit_date", None)
    if edited_at is None:
        return

    # Корневая комната известна без маршрута; сам маршрут фиксируется НИЖЕ — после
    # допуска и той же single-flight добычи каталога, что и в on_new (её регрессия 11:
    # правка и оригинал не смеют разъехаться по ключам вокруг первой добычи).
    room_nature = (None if is_private else
                   _known_forum(event.chat_id, msg, getattr(event, "chat", None)))
    # Capture reception order before the first await.  Telegram edit_date is only
    # second-granular, so two distinct edits in one second must follow arrival order,
    # not whichever sender lookup happens to finish last.
    text = str(getattr(msg, "message", "") or "")
    media = _media_tag(msg)
    base_source_id = _edit_revision_source_id(
        int(mid), edited_at, text=text, media=media,
    )
    reception_order = _revision_reception_order(peer_id, int(mid), base_source_id)
    # Persist/revoke before ANY revision effects or duplicate/self short circuit.
    # Failure propagates: an update must not be acknowledged as safely projected.
    # Exact peer evidence exists on edits for both DMs and the one canonical root
    # group; invalidate_native itself remains default-off and enrollment-scoped.
    if is_private:
        import keat_live
        await asyncio.to_thread(keat_live.invalidate_native, peer_id, int(mid))
    sender = await event.get_sender()
    if sender is not None and getattr(sender, "is_self", False):
        return
    sender_id = (getattr(event, "sender_id", None)
                 or getattr(sender, "id", None))
    is_owner = OWNER_ID != 0 and sender_id == OWNER_ID
    if not is_private and (not _group_archive_enabled()
                           or not rooms.is_allowed(peer_id, is_owner)):
        return

    room_mode, _resolution_failed = _resolve_room_mode(
        peer_id, peer_id, where="edited message",
    )
    if room_mode in ("frozen", "dead"):
        return

    catalog = None
    if room_nature is True:
        catalog = await _catalog_for_routing(
            peer_id, getattr(event, "chat", None),
            unknown_topic=(telegram_topics.thread_root_for_message(msg)
                           if getattr(msg, "reply_to", None) is not None else None))
    route = telegram_topics.route_for_message(
        event.chat_id, msg, is_private=is_private, is_forum=room_nature,
        confirmed_topics=catalog)
    if not is_private:
        # Правка живёт там, где живёт оригинал (фан-аут 22.08, P1): пересчёт места
        # «по сегодняшнему каталогу» разводил правку и оригинал по разным ключам,
        # когда каталог приезжал между ними. Якорь — archived topic_id оригинала,
        # прогнанный через ТОТ ЖЕ каталожный гейт: фантом из старого канона не
        # воскресает, настоящая тема остаётся собой, General — комнатой.
        try:
            prior = await asyncio.to_thread(
                group_context.latest_message, peer_id, int(mid))
        except Exception:
            prior = None
            log.debug("прежняя запись оригинала не прочиталась [%s] #%s",
                      peer_id, mid, exc_info=True)
        if isinstance(prior, dict) and prior.get("kind") == "message":
            try:
                prior_topic = int(prior.get("topic_id"))
            except (TypeError, ValueError):
                prior_topic = 0
            if (prior_topic > telegram_topics.GENERAL_TOPIC_ID
                    and catalog is not None and prior_topic in catalog):
                route = telegram_topics.TopicRoute(peer_id, prior_topic)
            else:
                route = telegram_topics.TopicRoute(peer_id)
    chat_id = route.conversation_id

    name = _sender_label(sender)
    text = str(getattr(msg, "message", "") or "")
    media = _media_tag(msg)
    reply_to_mid = getattr(msg, "reply_to_msg_id", None)
    try:
        message_ts = float(msg.date.timestamp())
    except Exception:
        message_ts = time.time()

    if is_private:
        body = " ".join(part for part in (media, text) if part).strip()
        if not body:
            body = "[empty message after edit]"
        source_id = _edit_revision_source_id(
            int(mid), edited_at, text=text, media=media,
        )
        _revision_order_by_chat_source[(chat_id, source_id)] = reception_order
        revision = source_id.split(":edit:", 1)[-1]
        marker = f"[edited #{mid} at {revision}]"
        existing_source = _buffer_revision_source(chat_id, int(mid))
        stale_revision = _revision_is_stale(
            existing_source, source_id, chat_id=chat_id)
        durable_seen = _life_source_persisted(chat_id, source_id)
        volatile_seen = (
            any(marker in prior for prior in _buf[chat_id])
            or any(str(value or "") == source_id
                   for value in _buffer_message_ids.get(chat_id, ()))
        )
        try:
            edit_ts = float(edited_at.timestamp())
        except Exception:
            edit_ts = time.time()
        try:
            revised_followup = await asyncio.to_thread(
                telegram_followups.LEDGER.revise_response,
                peer_id=peer_id, message_id=int(mid), text=body,
                revision_source_id=source_id, observed_at=edit_ts,
                revision_order=reception_order,
            )
            if revised_followup:
                await _supersede_pending_followup_deliveries(
                    str(revised_followup.get("id") or ""), reason="response_edited")
        except Exception:
            log.exception("follow-up response edit projection failed [%s] #%s", chat_id, mid)
        if durable_seen and volatile_seen:
            return
        buffer_line = f"{name} {marker}: {body}"
        if stale_revision:
            persisted = await _record_life_message_offloop(
                chat_id, buffer_line, actor=name, direction="in",
                source_id=source_id, is_dm=True, ts=edit_ts,
                dedupe_key=f"telegram:{chat_id}:{source_id}:in",
                revision_order=reception_order,
            )
            if persisted:
                await asyncio.to_thread(
                    memory_life.note_message_revision,
                    chat_id, int(mid), f"{name}: {body}", actor=name, ts=message_ts,
                )
            return
        _replace_buffer_message(chat_id, int(mid), buffer_line, source_id=source_id)
        persisted = await _record_life_message_offloop(
            chat_id, buffer_line, actor=name, direction="in",
            source_id=source_id, is_dm=True, ts=edit_ts,
            dedupe_key=f"telegram:{chat_id}:{source_id}:in",
            revision_order=reception_order,
        )
        if persisted:
            try:
                await asyncio.to_thread(
                    memory_life.note_message_revision,
                    chat_id, int(mid), f"{name}: {body}", actor=name, ts=message_ts,
                )
                _sync_buffer_from_hot(chat_id)
            except Exception:
                log.exception("DM edit hot projection failed [%s] #%s", chat_id, mid)
        _revise_recent_message(chat_id, int(mid), author=name, gist=body)
        live_meta = _meta.get(chat_id)
        if isinstance(live_meta, dict) and live_meta.get("origin_message_id") == int(mid):
            live_meta.update({
                "origin_text": text, "sender_id": sender_id, "name": name,
                "title": live_meta.get("title") or name,
            })
            _arm(chat_id)
        try:
            bufstore.meta_update(chat_id, author=name, is_dm=True, name=name)
        except Exception:
            log.debug("buf_meta не записалась [%s]", chat_id, exc_info=True)
        log.info("EDIT [DM %s] %s (id=%s): #%s", chat_id, name, sender_id, mid)
        return

    raw_topic_title = ""
    if route.topic_id is not None:
        cache_key = (peer_id, int(route.topic_id))
        raw_topic_title = _topic_titles.get(cache_key, "")
        if not raw_topic_title:
            try:
                raw_topic_title = await asyncio.to_thread(
                    telegram_routes.topic_title, peer_id, route.topic_id)
            except Exception:
                log.debug("durable topic title unavailable [%s]", chat_id, exc_info=True)
        if not raw_topic_title:
            try:
                raw_topic_title = await asyncio.to_thread(
                    group_context.topic_title, peer_id, route.topic_id)
            except Exception:
                log.debug("edited message topic title unavailable [%s]", chat_id,
                          exc_info=True)
        if raw_topic_title == f"topic #{route.topic_id}":
            raw_topic_title = ""     # выдуманное имя из старых записей — не имя
        if raw_topic_title:
            _topic_titles[cache_key] = raw_topic_title

    try:
        added = await asyncio.to_thread(
            group_context.observe_message,
            peer_id=peer_id, topic_id=route.topic_id, message_id=mid,
            sender_id=sender_id, sender_name=name,
            reply_to_message_id=reply_to_mid, timestamp=message_ts,
            edited_at=edited_at, text=text, topic_title=raw_topic_title,
            media=media, revision_order=reception_order,
        )
    except Exception:
        added = False
        log.exception("group edit archive failed [%s] #%s; live revision continues",
                      chat_id, mid)
    body = " ".join(part for part in (media, text) if part).strip()
    if not body:
        body = "[empty message after edit]"
    reply_mark = (f", reply to #{reply_to_mid}"
                  if reply_to_mid is not None else "")
    source_id = _edit_revision_source_id(
        int(mid), edited_at, text=text, media=media,
    )
    _revision_order_by_chat_source[(chat_id, source_id)] = reception_order
    revision = source_id.split(":edit:", 1)[-1]
    marker = f"[edited #{mid} at {revision}{reply_mark}]"
    try:
        edit_ts = float(edited_at.timestamp())
    except Exception:
        edit_ts = time.time()
    live_meta = _meta.get(chat_id) or {}
    buffer_line = f"{name} {marker}: {body}"
    existing_source = _buffer_revision_source(chat_id, int(mid))
    stale_revision = _revision_is_stale(
        existing_source, source_id, chat_id=chat_id)
    durable_seen = _life_source_persisted(chat_id, source_id)
    volatile_seen = (
        any(marker in prior for prior in _buf[chat_id])
        or any(str(value or "") == source_id
               for value in _buffer_message_ids.get(chat_id, ()))
    )
    if not stale_revision:
        try:
            revised_followup = await asyncio.to_thread(
                telegram_followups.LEDGER.revise_response,
                peer_id=peer_id, message_id=int(mid), text=body,
                revision_source_id=source_id, observed_at=edit_ts,
                revision_order=reception_order,
            )
            if revised_followup:
                await _supersede_pending_followup_deliveries(
                    str(revised_followup.get("id") or ""), reason="response_edited")
        except Exception:
            log.exception("follow-up response edit projection failed [%s] #%s", chat_id, mid)
    if durable_seen and volatile_seen:
        return
    if stale_revision:
        persisted = await _record_life_message_offloop(
            chat_id, buffer_line, actor=name, direction="in",
            source_id=source_id, is_dm=False, ts=edit_ts,
            dedupe_key=f"telegram:{chat_id}:{source_id}:in",
            revision_order=reception_order,
        )
        if persisted:
            try:
                await asyncio.to_thread(
                    memory_life.note_message_revision,
                    chat_id, int(mid), f"{name}{reply_mark}: {body}",
                    actor=name, ts=message_ts,
                )
                _sync_buffer_from_hot(chat_id)
            except Exception:
                log.exception("stale edit hot projection failed [%s] #%s", chat_id, mid)
        return
    _replace_buffer_message(chat_id, int(mid), buffer_line, source_id=source_id)
    persisted = await _record_life_message_offloop(
        chat_id, buffer_line, actor=name, direction="in",
        source_id=source_id, is_dm=False, ts=edit_ts,
        dedupe_key=f"telegram:{chat_id}:{source_id}:in",
        revision_order=reception_order,
    )
    try:
        bufstore.meta_update(
            chat_id, author=name, is_dm=False,
            name=str(live_meta.get("title") or raw_topic_title or name),
        )
    except Exception:
        log.debug("buf_meta не записалась [%s]", chat_id, exc_info=True)
    if persisted:
        try:
            await asyncio.to_thread(
                memory_life.note_message_revision,
                chat_id, int(mid), f"{name}{reply_mark}: {body}", actor=name, ts=message_ts,
            )
            _sync_buffer_from_hot(chat_id)
        except Exception:
            log.exception("edit hot projection failed [%s] #%s", chat_id, mid)
    _revise_recent_message(chat_id, int(mid), author=name, gist=body)
    if _refresh_group_wake_context(
            chat_id, message_id=int(mid), query=body, author=name,
            media_snapshot=tuple(_pending_media.get(chat_id, ()))):
        # If a pass is already waiting/authoring, its local wake may have been read
        # before this revision.  A successor generation cancels that stale pass and
        # re-runs the same address with the corrected payload; without a wake this path
        # remains silent.
        _arm(chat_id)
    log.info("EDIT [%s] %s (id=%s): #%s archived=%s",
             chat_id, name, sender_id, mid, added)


@client.on(getattr(events, "MessageDeleted", events.NewMessage)())
async def on_deleted(event) -> None:
    """Archive channel/supergroup deletions as terminal, append-only tombstones.

    Telegram supplies the peer only for channels and supergroups.  A deletion without
    ``chat_id`` stays unnamed; KEAT denies enrolled candidate source coordinates
    rather than guessing a chat or retiring unrelated native evidence. No readable DM tombstone is manufactured.
    Like edits, this updates the readable conversation but never wakes the model or
    triggers follow-up/authority handlers.
    """

    peer_raw = getattr(event, "chat_id", None)
    if peer_raw is None:
        if getattr(event, "deleted_ids", None):
            import keat_live
            await asyncio.to_thread(keat_live.invalidate_peerless_deletion, event.deleted_ids)
        return
    # Revocation is independent of group archive admission/room mode. Known
    # peers (including a DM supplied by an adapter) must not bypass this seam.
    import keat_live
    for mid in (getattr(event, "deleted_ids", None) or ()):
        await asyncio.to_thread(keat_live.invalidate_native, peer_raw, mid)
    if not _group_archive_enabled():
        return
    peer_id = str(peer_raw)
    if not rooms.is_allowed(peer_id, False):
        return
    room_mode, _resolution_failed = _resolve_room_mode(
        peer_id, peer_id, where="deleted message",
    )
    if room_mode in ("frozen", "dead"):
        return

    try:
        deleted_ids = [int(value) for value in (getattr(event, "deleted_ids", None) or ())
                       if int(value) > 0]
    except (TypeError, ValueError):
        return
    if not deleted_ids:
        return

    deleted_ts = time.time()
    for mid in deleted_ids:
        previous = {}
        try:
            observed = await asyncio.to_thread(
                group_context.latest_message, peer_id, mid,
            )
            previous = observed if isinstance(observed, dict) else {}
        except Exception:
            log.exception("group deletion prior state unavailable [%s] #%s", peer_id, mid)
        archive_failed = False
        try:
            added = await asyncio.to_thread(
                group_context.append_deletion_retry,
                peer_id=peer_id, message_id=mid, timestamp=deleted_ts,
            )
        except Exception:
            added = False
            archive_failed = True
            log.exception("group deletion archive failed [%s] #%s; live withdrawal continues",
                          peer_id, mid)
        try:
            deleted_followup = await asyncio.to_thread(
                telegram_followups.LEDGER.delete_response,
                peer_id=peer_id, message_id=mid, observed_at=deleted_ts,
            )
            if deleted_followup:
                await _supersede_pending_followup_deliveries(
                    str(deleted_followup.get("id") or ""), reason="response_deleted")
        except Exception:
            log.exception("follow-up response deletion projection failed [%s] #%s",
                          peer_id, mid)
        # A duplicate archive tombstone may be a replay after a partial durable write.
        # Continue through every rebuildable projection: their own source/dedupe identity
        # makes this idempotent, while returning here would strand stale life/recall state.

        conversations = _deletion_projection_conversations(peer_id, previous)
        if not conversations:
            continue
        for chat_id in conversations:
            sender_name = str(previous.get("sender_name") or "unknown")
            reply_to_mid = previous.get("reply_to_message_id")
            reply_mark = (f", reply to #{reply_to_mid}"
                          if reply_to_mid is not None else "")
            marker = f"[deleted #{mid}{reply_mark}]"
            topic_title = str(previous.get("topic_title") or "")
            live_meta = _meta.get(chat_id) or {}
            buffer_line = (
                f"Telegram {marker}: message removed"
                + (f" (former sender: {sender_name})" if sender_name != "unknown" else "")
            )
            _replace_buffer_message(chat_id, mid, buffer_line, deleted=True,
                                    source_id=f"{mid}:delete")
            persisted = await _record_life_message_offloop(
                chat_id, buffer_line, actor="Telegram", direction="in",
                source_id=f"{mid}:delete", is_dm=False, ts=deleted_ts,
                dedupe_key=f"telegram:{chat_id}:{mid}:delete:in",
            )
            try:
                bufstore.meta_update(
                    chat_id, author="Telegram", is_dm=False,
                    name=str(live_meta.get("title") or topic_title or peer_id),
                )
            except Exception:
                log.debug("buf_meta не записалась [%s]", chat_id, exc_info=True)
            if persisted:
                try:
                    await asyncio.to_thread(
                        memory_life.note_message_revision,
                        chat_id, mid, f"Telegram {marker}: message removed",
                        actor="Telegram", ts=deleted_ts,
                    )
                    _sync_buffer_from_hot(chat_id)
                except Exception:
                    log.exception("deletion hot projection failed [%s] #%s", chat_id, mid)
            _revise_recent_message(chat_id, mid, deleted=True)
            _drop_pending_message_media(chat_id, mid)
            if wake := _group_wakes.get(chat_id):
                if wake.message_id == mid:
                    _group_wakes.pop(chat_id, None)
                    # A pass may already be waiting or authoring on the deleted address.
                    # Re-arm one successor generation after withdrawing the wake: the old
                    # pass is cancelled as superseded, while the successor is an explicit
                    # no-op because there is no address left to answer.
                    _arm(chat_id)
                    perception.note_skip(
                        "deleted_address", "отложила", chat_id=chat_id,
                        detail=f"обращение #{mid} удалено до ответа",
                    )
                else:
                    if _refresh_group_wake_context(
                            chat_id, message_id=mid, deleted=True,
                            media_snapshot=tuple(_pending_media.get(chat_id, ()))):
                        _arm(chat_id)
            log.info("DELETE [%s]: #%s archived=%s", chat_id, mid, added)


def mid_of(message) -> int | None:
    """Идентификатор сообщения или None. Нужен реестру маршрутов как граница эпохи."""
    value = getattr(message, "id", None)
    return int(value) if isinstance(value, int) else None


def _known_forum(peer_id, message, chat_obj=None) -> bool | None:
    """Природа комнаты ПО ЗНАНИЮ: True/False, либо None — «не знаем».

    ⚑ СПРАШИВАЕМ СНАЧАЛА ОБЪЕКТ АПДЕЙТА, И ЭТО НЕ ОПТИМИЗАЦИЯ.
    Природа комнаты приезжает вместе с сообщением и стоит ноль: `event.chat` —
    синхронное свойство, сети не будет. Реестр с эпохами и весами строили, чтобы
    ответить на вопрос, ответ на который уже лежит в контейнере обновления. Прямой
    ответ Telegram и сам реестр считает высшим свидетельством — так что спрашивать
    накопленный вердикт там, где есть прямой, значит отвечать вчерашним днём.

    ⚠ Кроме одного случая: объект с флагом `min` документирован как ненадёжный —
    урезанная сущность может не нести `forum` или нести его неверно. Такой объект не
    смеет перебить реестр, и мы уходим к накопленному знанию.

    Реестр при этом не лишний, у него своя работа: комната может СМЕНИТЬ природу, а
    историю нельзя роутить сегодняшним флагом — добивке архива нужен режим ЕЁ времени.
    Поэтому исторические пути объект не передают и спрашивают эпохи.

    ⚑ ЗАЧЕМ ЭТА ФУНКЦИЯ ВООБЩЕ ПОЯВИЛАСЬ.
    `route_for_message` умеет главное с 25.07: `is_forum is False` → обычная супергруппа
    это ОДНА комната, и цепочка ответов не становится ключом хранения. Там же записан
    живой случай ровно из этой комнаты: «observed 23.07.2026 in -1001240718803: „у меня в
    топик попал только твой ответ, без сообщения #93708"».

    Но знание в эту функцию не доезжало. Раннер копил свидетельства о природе комнаты в
    реестр и честно писал рядом: «Маршрутизацию НЕ трогаем — только копим свидетельства,
    чтобы перекладка ключа однажды делалась по знанию». Чтение перевели на реестр 06.08
    (`read_scope`), а построение ключа осталось тенью — и продолжало резать комнату на
    псевдоветки `<peer>__topic__<корень>`.

    Цена этого разрыва, живьём 16.08: лента комнаты в кадре была ПОЛНОЙ (53 упоминания
    Арета, 47 корневых строк, 182 тредовых), а адрес хода говорил
    `origin_chat_id: "-1001240718803__topic__98994"` — псевдоветка из одного сообщения.
    Она ответила про ветку и сказала «контекста до этого сообщения в ветке нет»: для
    ветки это правда, для комнаты — нет.

    Спрашиваем `status_at`, а не `current`: у комнаты бывают эпохи, и сообщение из
    прошлого должно роутиться режимом СВОЕГО времени, а не сегодняшним.
    """
    if chat_obj is not None and not getattr(chat_obj, "min", False):
        flag = getattr(chat_obj, "forum", None)
        if flag is not None:
            return bool(flag)
    try:
        status, _epoch = telegram_routes.status_at(peer_id, mid_of(message))
    except Exception:
        log.debug("реестр маршрутов не ответил о природе комнаты [%s]", peer_id,
                  exc_info=True)
        return None
    if status == telegram_routes.FALSE:
        return False
    if status == telegram_routes.TRUE:
        return True
    return None  # «не знаем» — и для КЛЮЧА ХРАНЕНИЯ это теперь «не форум» (21.08.2026)


def _read_topic_knowledge_versioned(peer: str):
    """Синхронное чтение в потоке: ревизия ДО разбора → снимок → ревизия ПОСЛЕ.

    -> (ids, complete, revision, snapshot_proven). Доказательство снимка (её гейт
    REPAIR5, свойство 3): ревизии равны И чётны ⇒ за время разбора не завершился
    и не шёл НИ ОДИН durable-коммит — ничей: seqlock-токен становится нечётным
    ДО замены основного файла, поэтому окна «main сменился, токен прежний» нет.
    Нечётная ревизия (чужой коммит идёт или упал посередине) — недоказанно.
    """
    before = telegram_routes.topic_revision(peer)
    ids, complete = telegram_routes.topic_knowledge(peer)
    after = telegram_routes.topic_revision(peer)
    proven = (before == after and after % 2 == 0)
    return ids, complete, after, proven


async def _topic_knowledge_read_flight(peer: str):
    """Одно холодное durable-чтение. -> (ids, complete, revision, proven).

    ``proven`` — доказательство свежести: seqlock-снимок целостен (видит ВСЕХ
    писателей, включая чужие процессы, без метаданных ФС — её REPAIR5) И
    процесс-локальное поколение не изменилось за чтение. Кэшируется ТОЛЬКО
    доказанный успех; кэш хранит ревизию для перепроверки на каждом хите.
    """
    generation = _topic_catalog_cache_gen.get(peer, 0)
    ids, complete, revision, snapshot_proven = await asyncio.to_thread(
        _read_topic_knowledge_versioned, peer)
    proven = (snapshot_proven
              and _topic_catalog_cache_gen.get(peer, 0) == generation)
    if proven:
        _topic_catalog_cache[peer] = (
            time.time() + _TOPIC_CATALOG_CACHE_TTL, ids, complete, revision)
    return ids, complete, revision, proven


def _drop_read_flight(peer: str, task) -> None:
    entry = _topic_catalog_read_flights.get(peer)
    if entry is not None and entry[1] is task:
        _topic_catalog_read_flights.pop(peer, None)


def _reap_read_flight(peer: str, task) -> None:
    """Снять полёт с реестра И забрать его исключение (её REPAIR5, P2 lifecycle):
    единственный shielded-ждущий мог быть отменён, а полёт упасть позже — его
    exception обязан быть retrieved, иначе loop получает фоновый мусор."""
    _drop_read_flight(peer, task)
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            log.debug("холодное чтение каталога упало без ждущих [%s]: %r",
                      peer, exc)


async def _topic_knowledge_cached(peer: str) -> tuple[frozenset, bool]:
    """Знание о темах без синхронного диска в event loop и без stampede.

    Её блокер 5 (сто чтений на сто сообщений) закрыт коротким кэшем; её P1-3
    (25 холодных конкурентов = 25 чтений) — read-single-flight'ом: на комнату
    одновременно идёт не более одного холодного чтения, остальные ждут его же.
    Ошибка чтения не кэшируется и не отравляет полёт; отмена САМОГО чтения
    (shutdown) отвечает ждущим fail-safe, а не роняет их ходы; отмена одного
    ждущего (shield) не трогает остальных. Фан-аут REPAIR2: ждущий, пришедший
    ПОСЛЕ инвалидации, не присоединяется к чтению из прошлого поколения и не
    уносит досвежее знание — поколение сверяется и при выборе полёта, и после
    ожидания.
    """
    # Протокол свежести (её гейты REPAIR3 + REPAIR4). Точка линеаризации — атомарное
    # чтение route-файла. Доказательство свежести ДВУНОГОЕ: (1) durable-штамп файла
    # (mtime_ns, size) не изменился за разбор — это видит ВСЕХ писателей, включая
    # чужие процессы, потому что каждый коммит идёт atomic replace'ом под
    # межпроцессным замком; (2) процесс-локальное поколение не изменилось за
    # ожидание — внутрипроцессный конвейер. Кэш-хит перепроверяет durable-штамп
    # против живого файла на КАЖДОМ обращении (os.stat, микросекунды): чужой коммит
    # виден следующему же вызову, TTL-окна stale-complete нет. Поколение/штамп
    # сдвинулись — перечитываем: магического лимита попыток нет, при конечном churn
    # первое чтение после последнего коммита доказанно свежо. Граница бюджета —
    # НЕ доказательство непрерывности churn (её блокер A): после дедлайна
    # выполняется ровно ОДНО финальное чтение — оно начинается после последней
    # замеченной инвалидации, и при конечном churn возвращает полный честный
    # результат; при непрерывном — объединение всех прочитанных id с complete=False
    # (append-only ⇒ каждый прочитанный id доказанно durable, минтить честно;
    # полнота честно не заявляется; объединение не кэшируется), включающее все
    # коммиты, завершённые до старта финального чтения. Ограниченность: ровно +1
    # чтение после дедлайна, каждая итерация ждёт настоящий диск — busy-loop нет.
    union_ids: set = set()
    deadline = time.monotonic() + _TOPIC_KNOWLEDGE_CONVERGE_BUDGET
    final_attempt = False
    while True:
        row = _topic_catalog_cache.get(peer)
        now = time.time()
        if row is not None and row[0] > now:
            if telegram_routes.topic_revision(peer) == row[3]:
                return row[1], row[2]
            # Durable-ревизия сдвинулась под тёплым кэшем — межпроцессный писатель
            # (её блокер B): кэш сбрасывается, чтение идёт заново. Монотонность
            # токена исключает ABA (её REPAIR5, P0-A).
            _invalidate_topic_cache(peer)
        current_gen = _topic_catalog_cache_gen.get(peer, 0)
        entry = _topic_catalog_read_flights.get(peer)
        if entry is None or entry[1].done() or entry[0] != current_gen:
            task = asyncio.create_task(_topic_knowledge_read_flight(peer))
            _topic_catalog_read_flights[peer] = (current_gen, task)
            task.add_done_callback(lambda t, p=peer: _reap_read_flight(p, t))
        else:
            task = entry[1]
        try:
            ids, complete, revision, proven = await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                # Отменили само чтение (shutdown/чистка) — чужой ход не роняем.
                return frozenset(), False
            raise
        except Exception:
            log.debug("холодное чтение каталога не удалось [%s]", peer, exc_info=True)
            return frozenset(), False
        # Caller-local acceptance (её REPAIR5, P0-B): точка проверки свежести — не
        # раньше СОБСТВЕННОГО начала. Caller, стартовавший после чужого коммита, не
        # смеет унаследовать доказательство старого shared-полёта: живая ревизия
        # обязана совпасть с ревизией снимка в момент принятия.
        if (proven and _topic_catalog_cache_gen.get(peer, 0) == current_gen
                and telegram_routes.topic_revision(peer) == revision):
            return ids, complete
        # Знание сдвинулось, пока читали (опенер/свип/чужой процесс) — перечитываем.
        union_ids |= ids
        if final_attempt:
            log.warning("знание о темах [%s]: непрерывный churn, свежесть не "
                        "доказана за %.1fс + финальное чтение — отдаю объединение "
                        "%d id БЕЗ полноты", peer,
                        _TOPIC_KNOWLEDGE_CONVERGE_BUDGET, len(union_ids))
            return frozenset(union_ids), False
        if time.monotonic() >= deadline:
            # Дедлайн — не доказательство непрерывного churn: ровно одно финальное
            # чтение, начатое ПОСЛЕ последней замеченной инвалидации.
            final_attempt = True


async def _catalog_is_stale(peer: str) -> bool:
    """Пора ли плановое обновление полного свипа (раз в сутки)."""
    raw = await asyncio.to_thread(telegram_routes.topics_seen_at, peer)
    if not raw:
        return True
    try:
        seen = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return True
    return (time.time() - seen) >= _TOPIC_CATALOG_REFRESH


async def _note_live_opener(peer_id, topic_id: int, title: str) -> None:
    """Живой опенер: имя в durable-карту, вердикт «форум» в реестр, кэш — сброшен.

    Сброс кэша обязателен (P1 фан-аута 22.08): без него ответы в только что
    созданной теме до минуты падали в комнату — кэш ещё держал каталог без неё.
    """
    peer = str(peer_id)
    try:
        await asyncio.to_thread(
            telegram_routes.observe_topics, peer,
            {int(topic_id): {"title": title, "top_message": int(topic_id)}},
            source="topic_opener", complete=False)
        await asyncio.to_thread(
            lambda: telegram_routes.observe(
                peer, kind="topic_opener_seen",
                message_id=int(topic_id), detail=title[:60]))
        _invalidate_topic_cache(peer)
    except Exception:
        log.debug("опенер не записался в карту тем [%s]", peer, exc_info=True)


def _spawn_catalog_flight(peer: str, entity=None) -> asyncio.Task:
    """Один tracked-полёт добычи на комнату: параллельные просьбы ждут его же.

    Завершённый таск снимается со словаря сам (done-callback): реестр полётов не
    растёт по числу комнат и не держит отработавшие таски с их трейсбеками.
    """
    task = _topic_catalog_flights.get(peer)
    if task is not None and not task.done():
        return task
    task = asyncio.create_task(_catalog_flight(peer, entity))
    _topic_catalog_flights[peer] = task

    def _reap_catalog_flight(done, p=peer):
        if _topic_catalog_flights.get(p) is done:
            _topic_catalog_flights.pop(p, None)
        # Lifecycle (её REPAIR5, P2): исключение полёта всегда retrieved, даже если
        # ждущих не осталось — фоновых «Task exception was never retrieved» нет.
        if not done.cancelled() and done.exception() is not None:
            log.debug("полёт каталога упал без ждущих [%s]: %r",
                      p, done.exception())

    task.add_done_callback(_reap_catalog_flight)
    return task


async def _catalog_flight(peer: str, entity=None):
    """Полёт добычи каталога: свип → строгая запись → re-read → только потом успех.

    Её блокер 3: успех, кулдаун успеха и лог «добыто» — только после ПОДТВЕРЖДЁННОЙ
    записи (re-read внутри `_forum_topic_catalog`). Неудача — наблюдаемое событие
    (свидетельство в реестр, warning из _save) и КОРОТКОЕ окно повтора, а не
    15-минутный кулдаун поверх незаписанного знания. Полёт не бросает: ждущие
    сообщения получают None и честно падают в комнату (fail-safe).
    """
    # Срез pending снимается ДО RPC (фан-аут REPAIR2, P0): полёт вправе судить
    # только корни, заявленные до его снимка. Корень, заявленный уже летящему свипу,
    # этот свип физически не мог видеть — он остаётся следующему полёту, а не
    # хоронится свидетельством, добытым до его рождения. При любой неудаче или
    # отмене срез просто пропадает: корни не похоронены и получат новую попытку.
    claimed = _topic_catalog_pending_roots.pop(peer, set())
    try:
        if entity is None:
            entity = await _resolve_entity(peer)
        rows, complete = await _forum_topic_catalog(entity, peer)
        await asyncio.to_thread(
            lambda: telegram_routes.observe(
                peer, kind="get_forum_topics_ok",
                detail=f"тем: {len(rows)}" + ("" if complete else " (свип неполный)")))
        _topic_catalog_next_attempt[peer] = time.time() + _TOPIC_CATALOG_COOLDOWN
        # Кэш инвалидируется и здесь, не только внутри свипа, а свежее знание полёт
        # читает НАПРЯМУЮ (мимо общего read-flight): решение о захоронении корней не
        # имеет права опираться на чтение, начавшееся до записи свипа.
        _invalidate_topic_cache(peer)
        generation = _topic_catalog_cache_gen.get(peer, 0)
        fresh_ids, fresh_complete, fresh_revision, snapshot_proven = (
            await asyncio.to_thread(_read_topic_knowledge_versioned, peer))
        proven = (snapshot_proven
                  and _topic_catalog_cache_gen.get(peer, 0) == generation
                  and telegram_routes.topic_revision(peer) == fresh_revision)
        if proven:
            _topic_catalog_cache[peer] = (
                time.time() + _TOPIC_CATALOG_CACHE_TTL, fresh_ids, fresh_complete,
                fresh_revision)
            # Её P0-1: корень уходит в probed ТОЛЬКО после durable-успеха, и только
            # ДОКАЗАННО ПОЛНОГО свипа (неполный не доказывает отсутствия — принцип
            # её P0-2), и только если знание не сдвинулось, пока полёт дочитывал:
            # опенер, приехавший в хвосте, не даёт хоронить по устаревшему снимку.
            if complete and claimed:
                still_unknown = {root for root in claimed if root not in fresh_ids}
                if still_unknown:
                    probed = _topic_catalog_probed_roots.setdefault(peer, set())
                    if len(probed) + len(still_unknown) > _TOPIC_CATALOG_PROBED_CAP:
                        probed.clear()
                    probed.update(still_unknown)
        log.info("каталог тем [%s]: %d настоящих тем в durable-карте (полнота: %s)",
                 peer, len(rows), "да" if complete else "НЕТ")
        return fresh_ids
    except asyncio.CancelledError:
        # Отмена (shutdown) — не успех: окно повтора взводится, срез не хоронится.
        _topic_catalog_next_attempt[peer] = time.time() + _TOPIC_CATALOG_RETRY
        raise
    except Exception as exc:
        missing = type(exc).__name__ == "ChannelForumMissingError" or \
            "CHANNEL_FORUM_MISSING" in str(exc)
        try:
            await asyncio.to_thread(
                lambda: telegram_routes.observe(
                    peer,
                    kind="channel_forum_missing" if missing else "rpc_unavailable",
                    detail=type(exc).__name__))
        except Exception:
            log.debug("реестр маршрутов не принял свидетельство [%s]", peer, exc_info=True)
        _topic_catalog_next_attempt[peer] = time.time() + _TOPIC_CATALOG_RETRY
        log.info("каталог тем [%s]: добыча не удалась (%s) — ключи идут в комнату, "
                 "повтор через %.0fс", peer, type(exc).__name__, _TOPIC_CATALOG_RETRY)
        return None


async def _catalog_for_routing(peer, entity=None, *,
                               unknown_topic: int | None = None):
    """Знание для ФИКСАЦИИ ключа хранения. -> frozenset засвидетельствованных id.

    Возвращает POSITIVE durable-знание (её гейт REPAIR2, P0-2): ключ темы минтится
    по membership — засвидетельствованный опенером id минтит и без полного свипа,
    и после рестарта. Полнота решает только, надо ли ЖДАТЬ добычу. Зовётся ТОЛЬКО
    после допуска и room-mode-гейта (замороженная/чужая комната не рождает ни RPC,
    ни записи). Случаи:

    * полнота доказана — ключ не ждёт; незнакомый корень из reply-заголовка
      (бесплатный in-memory сигнал, НЕ дросселируется дисковым окном) открывает
      фоновую разведку: один корень — одна разведка ПОСЛЕ полного успеха, transient
      не хоронит (P0-1); давность каталога — своё окно и свой фоновый рефреш;
    * полноты нет и корень ТЕКУЩЕГО хода неизвестен — single-flight добыча ДО
      фиксации ключа, ограниченное ожидание; не успели/не смогли — сообщение
      честно ложится в комнату (fail-safe — комната, не заголовок);
    * полноты нет, но ключ хода от свипа не зависит (корень известен или его нет) —
      добыча полноты идёт фоном, ход не ждёт;
    * недавняя неудача — без новой долбёжки до конца окна повтора.
    """
    peer = str(peer)
    ids, complete = await _topic_knowledge_cached(peer)
    now = time.time()
    candidate = None
    if unknown_topic is not None:
        try:
            parsed = int(unknown_topic)
            if parsed > telegram_topics.GENERAL_TOPIC_ID and parsed not in ids:
                candidate = parsed
        except (TypeError, ValueError):
            candidate = None
    if complete:
        if candidate is not None:
            probed = _topic_catalog_probed_roots.setdefault(peer, set())
            if (candidate not in probed
                    and now >= _topic_catalog_unknown_gate.get(peer, 0.0)):
                _topic_catalog_unknown_gate[peer] = now + _TOPIC_CATALOG_RETRY
                # В probed корень уйдёт только при полном успехе полёта (P0-1);
                # здесь он лишь ЗАЯВЛЕН полёту через pending.
                _topic_catalog_pending_roots.setdefault(peer, set()).add(candidate)
                _spawn_catalog_flight(peer, entity)
        elif now >= _topic_catalog_stale_probe.get(peer, 0.0):
            # Плановая давность — единственное, что стоит дискового чтения; у неё
            # своё окно, и оно ничего не решает за незнакомые корни.
            _topic_catalog_stale_probe[peer] = now + _TOPIC_CATALOG_COOLDOWN
            if await _catalog_is_stale(peer):
                _spawn_catalog_flight(peer, entity)
        return ids
    # Полного свипа не было. Positive-знание уже минтит свои id; ждать добычу имеет
    # смысл только когда корень ТЕКУЩЕГО хода неизвестен — иначе ключ от результата
    # свипа не зависит, и полнота добывается фоном.
    if candidate is None:
        if now >= _topic_catalog_next_attempt.get(peer, 0.0):
            _spawn_catalog_flight(peer, entity)
        return ids
    if now < _topic_catalog_next_attempt.get(peer, 0.0):
        return ids
    task = _spawn_catalog_flight(peer, entity)
    try:
        await asyncio.wait_for(
            asyncio.shield(task), _TOPIC_CATALOG_PREFLIGHT_TIMEOUT)
    except asyncio.CancelledError:
        if not task.cancelled():
            raise                     # отменили НАС — пробрасываем честно
        return ids                    # отменили полёт (shutdown) — fail-safe комната
    except (asyncio.TimeoutError, Exception):
        return ids
    fresh_ids, _fresh_complete = await _topic_knowledge_cached(peer)
    return fresh_ids


def _resolve_room_mode(peer_id, chat_id, *, where: str) -> tuple[str, bool]:
    """Return the live mode; a broken mode sensor closes the route instead of guessing normal."""
    try:
        return rooms.effective_mode(peer_id, strict=True), False
    except Exception:
        log.warning("room mode unavailable at %s [%s] — fail-closed", where, chat_id,
                    exc_info=True)
        return "frozen", True


def _note_room_mode_skip(peer_id, chat_id, mode: str, *, stage: str,
                         resolution_failed: bool = False) -> None:
    profile = {}
    try:
        profile = rooms.profile_read(peer_id)
    except Exception:
        log.debug("room profile provenance упал [%s]", chat_id, exc_info=True)
    # `mode` уже вычислен effective_mode(): legacy sidecar может честно дать frozen при
    # сыром profile.mode=normal. Сырой профиль — provenance, но не вправе перебить исход.
    effective = str(mode or profile.get("mode") or "frozen")
    # Провенанс режима уже лежал в meta — и всё равно КЛАСС был жёстко «запретил_егор»,
    # включая случай mode_set_by == "praxis". То есть собственную границу она читала как
    # чужой запрет, а именно от этой подмены авторства словарь классов и защищает.
    set_by = str(profile.get("mode_set_by") or "")
    if resolution_failed or set_by in ("", "unknown", "protocol"):
        klass = "не_увидела"
    elif set_by == "praxis":
        klass = "мой_ритм"
    elif set_by == "owner":
        klass = "запретил_егор"
    else:
        klass = "не_увидела"
    detail = ("режим комнаты не прочитан — тракт закрыт" if resolution_failed
              else f"режим {effective}")
    perception.note_skip(
        stage, klass, chat_id=chat_id,
        detail=detail,
        meta={
            "mode": "unavailable" if resolution_failed else effective,
            "mode_set_by": profile.get("mode_set_by"),
            "mode_reason": profile.get("mode_reason"),
            "mode_until": profile.get("mode_until"),
        },
    )


@client.on(events.NewMessage(incoming=True))
async def on_new(event) -> None:
    msg = event.message
    text = msg.message or ""
    media = _media_tag(msg)
    is_private = bool(event.is_private)
    opener_title = "" if is_private else telegram_topics.topic_opener_title(msg)
    if opener_title and not text and not media:
        media = f"[Создан топик: {opener_title}]"
    if not text and not media:
        return
    sender = await event.get_sender()
    if sender is not None and getattr(sender, "is_self", False):
        return
    name = _sender_label(sender)
    # Корневая комната известна без маршрута. САМ МАРШРУТ фиксируется НИЖЕ — после
    # допуска, room-mode-гейта и preflight-каталога (её блокеры 1 и 5 от 22.08):
    # замороженная/чужая комната не рождает RPC, а ключ хранения не строится раньше,
    # чем добыто знание о настоящих темах.
    peer_id = str(event.chat_id)
    room_nature = (None if is_private else
                   _known_forum(event.chat_id, msg, getattr(event, "chat", None)))
    # Пункт 5, ТЕНЬ. Природа комнаты лежит прямо здесь и бесплатно: `event.chat` —
    # синхронное свойство, объект уже пришёл в контейнере апдейта, сети не будет.
    # Маршрутизацию НЕ трогаем — только копим свидетельства, чтобы перекладка ключа
    # однажды делалась по знанию. Объект с флагом `min` документирован как ненадёжный,
    # поэтому идёт как слабое свидетельство и не смеет перебить прямой ответ Telegram.
    if not is_private:
        try:
            chat_obj = getattr(event, "chat", None)
            if telegram_topics.is_topic_opener(msg):
                await asyncio.to_thread(
                    lambda: telegram_routes.observe(
                        peer_id, kind="topic_opener_seen", message_id=mid_of(msg),
                        detail=opener_title[:80]))
            elif chat_obj is not None:
                flag = getattr(chat_obj, "forum", None)
                kind = ("update_min_entity" if getattr(chat_obj, "min", False)
                        else ("entity_forum_flag" if flag is not None else "legacy_chat"))
                await asyncio.to_thread(
                    lambda: telegram_routes.observe(
                        peer_id, kind=kind, forum=bool(flag) if flag is not None else False,
                        message_id=mid_of(msg), detail=type(chat_obj).__name__))
            # ⚠ 03.08.2026, долг с 28.07. Природа «могу ли я сюда писать» лежит здесь
            # ровно так же бесплатно: `broadcast` — поле того же объекта, сети не будет.
            # Живьём 03.08 в 14:08 она ответила в вещательный канал AbstractDL, и узнала
            # об этом ПОСЛЕ — из отказа доставки и из поправки Егора вручную. Знание было
            # доступно ДО, и просто не спрашивалось.
            if chat_obj is not None and getattr(chat_obj, "broadcast", None) is not None:
                await asyncio.to_thread(
                    lambda: telegram_routes.note_writing(
                        peer_id, broadcast=bool(getattr(chat_obj, "broadcast", False))))
                await _note_linked_discussion(chat_obj, peer_id)
        except Exception:
            log.debug("реестр маршрутов: живое свидетельство не записалось [%s]",
                      peer_id, exc_info=True)
        # ⚠ ВЕЩАТЕЛЬНЫЙ КАНАЛ ЕЙ ЗАКРЫТ. Решение Егора 06.08: она живёт в ЧАТЕ канала, а
        # не в самом канале. Запись канала и так приезжает в связанное обсуждение
        # авторством канала — то есть ровно так, как её видит человек. Отдельное место
        # для той же записи даёт ей второй экземпляр одного события и комнату, в которой
        # у неё нет голоса.
        #
        # Это уже стоило живьём: 03.08 в 14:08 она ответила в вещательный канал и узнала
        # об этом ПОСЛЕ, из отказа доставки. Знание было доступно ДО и не спрашивалось.
        #
        # ⚑ КАЛИТКА СТОИТ ПОСЛЕ НАБЛЮДЕНИЯ НАМЕРЕННО: выше по этому же блоку записаны
        # природа места и адрес связанного обсуждения. Закрыть вход, не узнав, куда
        # ведёт дверь, значило бы оставить её без адреса чата навсегда.
        #
        # Закрывается только ПРЯМОЕ `broadcast=True` от Telegram. Молчание про природу
        # места не закрывает ничего: закрывают знанием, а не подозрением.
        #
        # Рычаг возврата — её условие приёмки: `PRAXIS_BROADCAST_INTAKE=1` открывает вход
        # обратно. Читается на КАЖДОМ событии, а не на импорте: рычаг, ради которого надо
        # перезапускать её, — это не рычаг, а ещё один повод не трогать.
        if (getattr(getattr(event, "chat", None), "broadcast", False)
                and os.getenv("PRAXIS_BROADCAST_INTAKE", "0").strip() not in ("1", "true", "yes")):
            log.info("канал [%s]: вход ей закрыт, её место — связанное обсуждение",
                     peer_id)
            return
    sender_id = getattr(event, "sender_id", None) or getattr(sender, "id", None)
    keat_group_principal = _keat_group_user_principal(sender, sender_id)
    # Shadow-only moderation seam: exact Telegram ids in, no route/room mutation and no
    # actuator.  The detector persists only ids/timestamps/a text digest locally; durable
    # review events contain no text, name, username or digest.
    if not is_private and sender_id is not None and text:
        try:
            await asyncio.to_thread(
                moderation_shadow.observe_message,
                peer_id=int(event.chat_id), message_id=mid_of(msg),
                sender_id=int(sender_id), text=text,
                observed_at=(msg.date.timestamp() if getattr(msg, "date", None) else None),
            )
        except Exception:
            log.warning("moderation shadow failed closed for peer=%s message=%s",
                        peer_id, mid_of(msg), exc_info=True)
    is_owner = OWNER_ID != 0 and sender_id == OWNER_ID
    if sender is not None:
        try:
            telegram_contacts.observe(sender, aliases=(name,), interacted=True)
            if sender_id is not None:
                _entity_cache[str(sender_id)] = sender
            handle = _telegram_handle(sender)
            if handle:
                _entity_cache[handle.casefold()] = sender
        except Exception:
            log.debug("адресная книга: отправитель не сохранился", exc_info=True)

    # Стоп-кран: /panic от владельца или хоста чужого пространства (PRAXIS_PANIC_IDS).
    # Только стоп. Стоит ДО гейтов, как и раньше; сторож дублей теперь ниже (ему нужен
    # финальный ключ) — повторный /panic из catch_up упрётся в уже идущий disconnect.
    if text.strip().lower() in ("/panic", "паника") and (is_owner or sender_id in PANIC_IDS):
        log.warning("PANIC от id=%s", sender_id)
        try:
            await asyncio.to_thread(agent.panic, f"by {sender_id}")
            await client.disconnect()
        except Exception:
            log.exception("panic")
        return

    room_policy = rooms.default_policy()
    # Гейты допуска и режима идут по КОРНЕВОЙ комнате и стоят ДО фиксации маршрута:
    # заморож/чужое событие не рождает ни каталожного RPC, ни ключа (её блокер 5).
    # Пометки пропуска поэтому тоже комнатные: сам гейт комнатный.
    if not is_private and not rooms.is_allowed(peer_id, is_owner):
        perception.note_skip("departed", "явный leave", chat_id=peer_id)
        return
    # Один живой режим, один гейт. Раньше отдельный legacy-флаг проверялся ПЕРВЫМ:
    # истёкший frozen TTL не доходил до effective_mode(), который как раз снимает и
    # профиль, и sidecar. Ошибка выглядела как свежая заморозка после срока.
    room_mode, resolution_failed = _resolve_room_mode(
        peer_id, peer_id, where="incoming message",
    )
    if room_mode in ("frozen", "dead"):
        _note_room_mode_skip(
            peer_id, peer_id, room_mode, stage="room_mode",
            resolution_failed=resolution_failed,
        )
        return
    if not is_private:
        try:
            room_policy = rooms.room_policy(peer_id)
        except Exception:
            log.debug("room_policy упал [%s]", peer_id, exc_info=True)

    # ФИКСАЦИЯ МАРШРУТА — только теперь. Каталог настоящих тем добывается
    # single-flight ДО ключа (первая живая реплика настоящей темы не теряется);
    # не добыли — fail-safe комната, не заголовок (её блокер 1).
    catalog = None
    if room_nature is True:
        catalog = await _catalog_for_routing(
            peer_id, getattr(event, "chat", None),
            unknown_topic=(telegram_topics.thread_root_for_message(msg)
                           if getattr(msg, "reply_to", None) is not None else None))
    route = telegram_topics.route_for_message(
        event.chat_id, msg, is_private=bool(is_private), is_forum=room_nature,
        confirmed_topics=catalog)
    # All conversational state is topic-local.  Access/room policy above deliberately
    # used peer_id: a topic is not a second Telegram room or authority.
    chat_id = route.conversation_id

    # PASS 9.0: catch_up может доиграть уже виденный апдейт — msg_id-сторож против
    # дублей буфера. Считается по ФИНАЛЬНОМУ ключу и смотрит ещё в комнатное кольцо:
    # сообщение, принятое ДО прихода каталога под ключом комнаты, при повторе уехало
    # бы в тему и продублировало буфер (фан-аут 22.08).
    mid = getattr(msg, "id", None)
    if mid is not None:
        if mid in _seen_ids[chat_id] or (
                chat_id != peer_id and mid in _seen_ids[peer_id]):
            return
        _seen_ids[chat_id].append(mid)

    cat = social.category(sender_id)
    known = cat in ("owner", "known")
    fam = bool(is_private and not is_owner and known and social.is_family(sender_id))  # 10.10
    # Незнакомец — полноценный разговор, не заявка владельцу. Первый контакт только
    # отмечается в её собственной памяти; заморозить неприятный чат она может сама.
    admission = None
    if is_private and not is_owner and cat == "unknown":
        today = praxis_time.day_key()
        first, count = social.note_unknown(sender_id, today)
        admission = {"first": first, "count": count, "over_cap": False}

    mentioned = bool(getattr(msg, "mentioned", False))
    replied, reply_mark = False, ""
    reply_to_mid = getattr(msg, "reply_to_msg_id", None)
    if msg.is_reply:
        try:
            r = await msg.get_reply_message()
            if r is not None:
                replied = bool(_self_id and r.sender_id == _self_id)
                # PASS 15: она видит, НА ЧТО ответили — короткий гист цитируемого
                # (скобки/переносы срезаны: маркер должен сниматься _REPLY_MARKER_RE).
                tgt = re.sub(r"[()\n\r]", " ", (getattr(r, "message", "") or ""))
                tgt = re.sub(r"\s+", " ", tgt).strip()[:50]
                who = "Praxis" if replied else _sender_name(r)
                reply_mark = f" (в ответ {who}" + (f": «{tgt}»" if tgt else "") + ")"
        except Exception:
            pass
    named = agent._named(text)
    addressed = mentioned or replied or named
    # Все документы из допущенных чатов — в мастерскую, даже когда сообщение не было
    # адресовано Praxis. Addressing решает, просыпается ли голос, но не стирает её обзор.
    # Каталоги человеческие: workspace/inbox/groups/<чат> и private/<личка>.
    download_scope = ("group" if not is_private else "owner" if is_owner else
                      "family" if fam else "known" if known else "unknown")
    desc = await _chat_descriptor(event, peer_id)  # нужен и каталогу inbox, и мета хода
    if _wants_inbox(is_private, is_owner, msg, addressed):
        inbox_tag = await _inbox_download(
            msg, scope=download_scope, chat_id=chat_id,
            chat_kind="private" if is_private else "group",
            chat_label=name if is_private else str(desc.get("title") or ""))
        if inbox_tag:
            media = inbox_tag
    # Фото и аудио получают отдельный типизированный тракт. Скачиваем только ПОСЛЕ
    # room/access-гейтов выше; ref привязан к этому scope/chat и не попадёт в live-history другого.
    media_scope = download_scope
    ref, media_error = await _capture_typed_media(
        msg, chat_id=chat_id, scope=media_scope, caption=text)
    if ref is not None:
        _pending_media[chat_id].append(ref)
    elif media_error:
        media = media_error
    body = " ".join(x for x in (media, text) if x)  # медиа-конверт: тег + подпись
    try:
        message_ts = float(msg.date.timestamp()) if getattr(msg, "date", None) is not None else time.time()
    except Exception:
        message_ts = time.time()
    raw_topic_title = ""
    if route.topic_id is not None:
        cache_key = (peer_id, int(route.topic_id))
        raw_topic_title = opener_title or _topic_titles.get(cache_key, "")
        if opener_title:
            # Служебное «создана тема» — прямое свидетельство Telegram: тема и её
            # настоящее имя уезжают в durable-карту, не дожидаясь полного свипа, а
            # реестр получает вердикт «форум» — следующий ход роутится по знанию.
            await _note_live_opener(peer_id, int(route.topic_id), opener_title)
        if not raw_topic_title:
            try:
                raw_topic_title = await asyncio.to_thread(
                    telegram_routes.topic_title, peer_id, route.topic_id)
            except Exception:
                log.debug("durable topic title не прочитался [%s]", chat_id, exc_info=True)
        if not raw_topic_title:
            try:
                raw_topic_title = await asyncio.to_thread(
                    group_context.topic_title, peer_id, route.topic_id)
            except Exception:
                log.debug("topic title не прочитался [%s]", chat_id, exc_info=True)
        if raw_topic_title == f"topic #{route.topic_id}":
            raw_topic_title = ""     # выдуманное имя из старых записей — не имя
        if raw_topic_title:
            _topic_titles[cache_key] = raw_topic_title
    # Нет настоящего имени — нет имени: без синтетики «topic #N». Адрес темы честен
    # и без имени — тем же селектором, которым её адресуют руки.
    topic_title = (
        f"{desc.get('title')} · {raw_topic_title or f'#topic:{route.topic_id}'}"
        if route.topic_id is not None else desc.get("title")
    )
    buffer_name = name if route.topic_id is None else str(topic_title or name)

    # Edit/delete updates can overtake NewMessage while handlers await sender/media work.
    # Preserve the delayed original as audit evidence, but never let it resurrect into
    # live buffer, follow-up matching or a wake when a newer current revision already exists.
    current_revision = None
    if mid is not None:
        if is_private:
            existing_source = _buffer_revision_source(chat_id, int(mid))
            if _revision_is_stale(existing_source, str(mid), chat_id=chat_id):
                current_revision = {"source_id": existing_source}
        elif _group_archive_enabled():
            try:
                observed = await asyncio.to_thread(
                    group_context.latest_message, peer_id, int(mid))
                if (isinstance(observed, dict)
                        and (observed.get("kind") == "deletion" or observed.get("edited_at"))):
                    current_revision = observed
                    await asyncio.to_thread(
                        group_context.observe_message,
                        peer_id=peer_id, topic_id=route.topic_id, message_id=mid,
                        sender_id=sender_id, sender_name=name,
                        reply_to_message_id=reply_to_mid, timestamp=message_ts,
                        edited_at=getattr(msg, "edit_date", None),
                        text=text, topic_title=raw_topic_title, media=media,
                    )
            except Exception:
                log.exception("late original current-state check failed [%s] #%s", chat_id, mid)
    if current_revision is not None:
        _record_life_message(
            chat_id, f"{name}{reply_mark}: {body}", actor=name, direction="in",
            source_id=mid, is_dm=is_private, ts=message_ts,
            dedupe_key=f"telegram:{chat_id}:{mid}:in",
        )
        current_source = str(current_revision.get("source_id") or "")
        if not current_source and current_revision.get("kind") == "deletion":
            current_source = f"{mid}:delete"
        if not current_source and current_revision.get("edited_at"):
            current_source = _edit_revision_source_id(
                int(mid), current_revision.get("edited_at"),
                text=str(current_revision.get("text") or ""),
                media=str(current_revision.get("media") or ""),
            )
        projection_chats = {chat_id}
        if not is_private:
            if current_revision.get("kind") == "deletion":
                # A delayed original must obey the same narrow terminal route as the
                # deletion handler.  Local buffer sightings are stale observations and
                # cannot recreate/fan out tombstones after the canonical delete won.
                projection_chats = set(
                    _deletion_projection_conversations(peer_id, current_revision))
            else:
                projection_chats.update(_group_message_conversations(peer_id, int(mid)))
                topic_id = current_revision.get("topic_id")
                projection_chats.add(
                    telegram_topics.TopicRoute(peer_id, topic_id).conversation_id)
        current_line = (
            f"Telegram [deleted #{mid}]: message removed"
            if current_revision.get("kind") == "deletion" else
            f"{current_revision.get('sender_name') or name} "
            f"[edited #{mid} at {current_revision.get('edited_at')}]: "
            f"{' '.join(part for part in (current_revision.get('media'), current_revision.get('text')) if part)}"
        )
        for projection_chat in projection_chats:
            if current_source:
                _replace_buffer_message(
                    projection_chat, int(mid), current_line,
                    deleted=current_revision.get("kind") == "deletion",
                    source_id=current_source,
                )
                _record_life_message(
                    projection_chat, current_line,
                    actor=("Telegram" if current_revision.get("kind") == "deletion"
                           else str(current_revision.get("sender_name") or name)),
                    direction="in", source_id=current_source, is_dm=is_private,
                    ts=(time.time() if current_revision.get("kind") == "deletion" else
                        float(datetime.datetime.fromisoformat(
                            str(current_revision.get("edited_at")).replace("Z", "+00:00")
                        ).timestamp())),
                    dedupe_key=f"telegram:{projection_chat}:{current_source}:in",
                    revision_order=(int(current_revision.get("revision_order") or 0)
                                    if current_revision.get("edited_at") else None),
                )
            else:
                _sync_buffer_from_hot(projection_chat)
        try:
            matched = telegram_followups.LEDGER.observe_incoming(
                peer_id=peer_id, sender_id=sender_id, message_id=mid, text=body,
                reply_to_message_id=reply_to_mid, received_at=message_ts,
                sender_name=name, sender_is_owner=bool(is_owner),
            )
            if matched:
                log.info("FOLLOW-UP %s: delayed original #%s projected as current revision",
                         matched.get("id"), mid)
        except Exception:
            log.exception("follow-up ledger не принял delayed original [%s] #%s", chat_id, mid)
        return

    _buf_push(chat_id, f"{name}{reply_mark}: {body}", author=name, is_dm=is_private,
              name=buffer_name,
              source_id=mid, ts=message_ts,
              capture_live=(not bool(media or ref) and
                            (is_private or (peer_id == '-1001240718803' and
                             chat_id == peer_id and room_nature is False and
                             keat_group_principal is not None))),
              root_chat_id=(peer_id if not is_private else None),
              principal_id=(keat_group_principal if not is_private else None),
              ordinary_root=(not is_private and room_nature is False and chat_id == peer_id))
    # ⚠ ГЕЙТ ЛИЧЕК СТОИТ ЗДЕСЬ, А НЕ ВЫШЕ — И ЭТО СУТЬ, А НЕ ОФОРМЛЕНИЕ.
    # Сообщение записано строкой выше: горячий слой и `memory/life` его держат.
    # Гейт, поставленный рядом с `social.category`, возвращался бы раньше записи —
    # то есть стирал бы событие из её памяти вовсе. «Не отвечать незнакомым» и
    # «молча терять людей» — разные вещи. Умолчание `PRAXIS_DM_POLICY=open`
    # оставляет прежнее поведение байт-в-байт: `_dm_allowed` отвечает True первой
    # строкой. `whitelist` пускает владельца, семью и названных явно
    # (`PRAXIS_DM_ALLOW`, id через запятую, плюс `memory/.state/dm-allow.json`).
    if is_private and not is_owner and not _dm_allowed(sender_id, family=fam):
        _note_dm_refused(chat_id, sender_id, name, already_noted=admission is not None)
        try:
            perception.note_skip("dm-gate", "личка закрыта белым списком", chat_id=chat_id)
        except Exception:
            log.debug("perception.note_skip dm-gate не записался", exc_info=True)
        return
    # The archive sees every admitted group update, not only messages that wake the
    # model.  It is exact-topic and append-only; projection/search failures are loud
    # but must never make the live Telegram update disappear.
    if not is_private and mid is not None and _group_archive_enabled():
        try:
            if opener_title and route.topic_id is not None:
                await asyncio.to_thread(
                    group_context.record_topic,
                    peer_id, route.topic_id, opener_title,
                    timestamp=message_ts, message_id=mid,
                    origin="opener",
                )
            await asyncio.to_thread(
                group_context.observe_message,
                peer_id=peer_id, topic_id=route.topic_id, message_id=mid,
                sender_id=sender_id, sender_name=name,
                reply_to_message_id=reply_to_mid, timestamp=message_ts,
                edited_at=getattr(msg, "edit_date", None),
                text=text, topic_title=raw_topic_title, media=media,
            )
        except Exception:
            log.exception("group archive не записал [%s] #%s", chat_id, mid)
    if mid is not None:
        try:
            # Имя и «это сам Егор» известны ровно здесь и уже посчитаны выше: в модуле
            # леджера OWNER_ID неизвестен вовсе. 27.07 02:31 раннер напечатал в лог
            # «получен ответ #94244 от Yegor Kosyrev» — и это знание никуда дальше не
            # ехало: в письмо шла метка ЧАТА, а то, что ответил сам адресат письма, не
            # проверял никто.
            matched = telegram_followups.LEDGER.observe_incoming(
                peer_id=peer_id, sender_id=sender_id, message_id=mid, text=body,
                reply_to_message_id=reply_to_mid, received_at=message_ts,
                sender_name=name, sender_is_owner=bool(is_owner),
            )
            if matched:
                log.info("FOLLOW-UP %s: получен ответ #%s от %s",
                         matched.get("id"), mid, name)
        except Exception:
            # Follow-up bookkeeping must never make a live incoming update disappear.
            log.exception("follow-up ledger не записал входящее [%s] #%s", chat_id, mid)
    # Passive group traffic also ages into memory; compaction cannot depend on
    # Praxis being addressed and producing an outgoing turn.
    if not _under_tests():
        asyncio.create_task(_maybe_compact(chat_id))
    if mid is not None:  # PASS 15: карта адресных ответов (ОТВЕТ->#id)
        _recent_msgs[chat_id].append((int(mid), name, body[:60]))
    if sender_id is not None:  # PASS 16.2: кэш отправителей для get_id (спамер-класс)
        _recent_senders[chat_id].append((time.time(), name, sender_id))
    # §2: прямое обращение — упоминание/реплай ей. Для группы его provenance живёт
    # отдельно в immutable GroupWake; rolling _meta остаётся только снимком последнего сообщения.
    # 9.6: голое имя из adressed УБРАНО (Егор: «на имя — ещё осторожнее») — оно не будит
    # и не поднимает флаг «к тебе обратились»; имя остаётся просто словом в буфере.
    suppress_boundary_reply = bool(
        not is_private and replied and _consume_boundary_reply(
            chat_id, reply_to_mid, sender_id, is_owner=is_owner))
    _meta[chat_id] = {"entity": event.chat_id, "peer_id": peer_id,
                      "topic_id": route.topic_id, "sender_id": sender_id,
                      "origin_message_id": (int(mid) if mid is not None else None),
                      "origin_text": str(text or ""),
                      "room_nature": room_nature,
                      "is_dm": is_private, "is_owner": is_owner,
                      "known": known, "family": fam, "name": name,
                      "title": topic_title, "size": desc.get("size"),
                      "addressed": bool(addressed),
                      "addressed_mid": (int(mid) if (addressed and mid is not None) else None),
                      "room_mode": room_mode, "room_policy": room_policy}
    log.info("MSG [%s] %s (id=%s, %s): %r", "DM" if is_private else chat_id, name, sender_id, cat, body[:60])
    # 25.09 (G §1): обращение к ней или личка — в накопитель уведомлений. Не будильник:
    # ход этой комнаты придёт своим порядком и сам снимет запись (`clear_chat` в
    # `_run_pass`); пока она занята другим, строка покажется в её ближайшем вводе модели.
    # Реплика владельца в личке — ещё и `owner_line` для её живых окон и будильников.
    # Голый неадресованный поток группы сюда не идёт: это среда, а не событие.
    notice_kind = ("dm" if is_private else
                   "mention" if mentioned else "reply" if replied else
                   "name" if named else "")
    if notice_kind:
        try:
            from core import notices as core_notices
            await asyncio.to_thread(
                core_notices.note_incoming,
                kind=notice_kind, chat_id=chat_id,
                chat_title=str(topic_title or name or ""), who=name, message_id=mid,
                gist=body, private=bool(is_private), ts=message_ts,
                is_owner_dm=bool(is_private and is_owner))
        except Exception:
            log.debug("накопитель уведомлений: запись [%s] #%s не легла", chat_id, mid,
                      exc_info=True)

    # Незнакомец остаётся самостоятельным разговором Praxis: адрес уже сохранён в книге.
    if is_private and not is_owner and cat == "unknown":
        adm = admission or {}
        if adm["first"]:
            agent.tool_journal(f"[новый] {name} (id={sender_id}): {body[:200]}")

    # reflex — 0-токенный пре-фильтр: одиночный шум (смайл/«ок»/стикер) не будит проход,
    # но остаётся в буфере как контекст для следующего прохода.
    # Boundary provenance remains observable, but it has no authority to decide that a
    # person's next reply is "bait" or suppress Praxis before she sees it.
    if suppress_boundary_reply:
        log.info("reply to prior boundary [%s] #%s — visible to the normal voice pass",
                 chat_id, reply_to_mid)
    decision = reflex.triage(text, is_private=is_private, mentioned=mentioned,
                             replied_to_self=replied, named=named, media=media)
    engagement = (str(room_policy.get("engagement") or "reflective")
                  if room_mode == "normal" else "addressed")
    if not _should_wake(is_private, addressed, decision, engagement):
        # PASS 21: причина видна. ЛС-игнор (стикер-шум) — «не сочла важным»; групповой
        # неадресованный поток — не пропуск, а среда: копится в буфере, здесь только счётчик.
        if is_private:
            perception.note_skip("reflex", "не_сочла_важным", chat_id=chat_id,
                                 detail=media or f"шум, len={len(text)}")
        else:
            perception.note_ambient(chat_id)
        return
    if not is_private:
        msg_date = getattr(msg, "date", None)
        try:
            message_ts = float(msg_date.timestamp())
        except (AttributeError, TypeError, ValueError, OSError):
            message_ts = time.time()
        kind = ("mention+reply" if mentioned and replied else
                "mention" if mentioned else "reply" if replied else
                "name" if named else "ambient")
        wake = await asyncio.to_thread(
            _group_trigger_snapshot,
            chat_id, mid=mid, message_ts=message_ts, kind=kind,
            addressed=bool(addressed), query=body, name=name,
            sender_id=sender_id, is_owner=is_owner, known=known, family=fam,
        )
        if not _install_group_wake(chat_id, wake):
            perception.note_ambient(chat_id)
            return
    # PASS 9.0: чужая реплика человека в ЛС, которую она собралась разобрать, —
    # кандидат в неотвеченные. Бот может разбудить тот же голосовой проход, но не
    # должен превращаться в социальный долг; automated=True также лечит старую запись.
    if is_private:
        try:
            unanswered.note_incoming(
                chat_id, name, automated=bool(getattr(sender, "bot", False)))
        except Exception:
            log.debug("unanswered.note_incoming упал [%s]", chat_id, exc_info=True)
    _arm(chat_id)


def _arm(chat_id: str) -> None:
    """Взвести/перевзвести дебаунс — всплеск склеивается в одну ситуацию."""
    # PASS 29: инкремент поколения == запланирован новый пасс-преемник. Бамп и отмена
    # текущего пасса синхронны (без await между ними), поэтому «gen изменился» строго
    # эквивалентно «преемник существует» и не может разойтись с реальностью. Это
    # единственный в процессе отменитель живого хода — значит отмена БЕЗ бампа = shutdown.
    _supersede_gen[chat_id] = _supersede_gen.get(chat_id, 0) + 1
    t = _debounce.get(chat_id)
    if t and not t.done():
        t.cancel()
    _debounce[chat_id] = asyncio.create_task(_debounced(chat_id))


async def _debounced(chat_id: str) -> None:
    try:
        try:
            wait = float(perception.value("debounce_sec"))  # PASS 21: живой рычаг
        except Exception:
            wait = DEBOUNCE_SEC
        await asyncio.sleep(wait)
    except asyncio.CancelledError:
        return
    await _run_pass(chat_id)


def _defer_pass(chat_id: str, delay: float) -> None:
    """§8: не ронять накопленное в кулдауне — перепланировать проход на остаток кулдауна."""
    t = _deferred.get(chat_id)
    if t and not t.done():
        return  # уже отложен — один таймер на чат

    async def _later():
        try:
            await asyncio.sleep(max(0.0, delay))
        except asyncio.CancelledError:
            return
        # Пока текущая задача числится живой в `_deferred`, повторный defer из
        # `_run_pass` видит саму себя и ничего не ставит. Снимаем слот ПЕРЕД вызовом:
        # тогда временная причина (например, недоступный mode-sensor) может честно
        # перепланировать ещё один retry, не дожидаясь внешнего сообщения.
        current = asyncio.current_task()
        if _deferred.get(chat_id) is current:
            _deferred.pop(chat_id, None)
        await _run_pass(chat_id)

    _deferred[chat_id] = asyncio.create_task(_later())


def _telegram_media_random_id(queue_id: str) -> int:
    """Stable signed int64 so retrying one durable queue id cannot duplicate a message."""
    raw = hashlib.sha256(f"praxis-media\0{queue_id}".encode("utf-8")).digest()[:8]
    value = int.from_bytes(raw, "big", signed=True)
    return value or 1


def _telegram_text_random_id(delivery_key: str) -> int:
    """Stable signed int64 for one logical text delivery or chunk."""
    raw = hashlib.sha256(f"praxis-text\0{delivery_key}".encode("utf-8")).digest()[:8]
    value = int.from_bytes(raw, "big", signed=True)
    return value or 1


async def _send_message_idempotent(entity, message: str, *, delivery_key: str,
                                   reply_to=None, random_id: int | None = None):
    """Telethon ``send_message`` with a caller-owned stable MTProto random id.

    Telegram de-duplicates retries carrying the same ``random_id``.  The public
    method does not expose that field, so the live client uses the equivalent
    raw request; small hermetic clients keep their public-method fallback.
    """
    random_id = (_telegram_text_random_id(delivery_key)
                 if random_id is None else int(random_id))
    if random_id == 0:
        raise ValueError("Telegram random_id must be non-zero")
    response_parser = inspect.getattr_static(type(client), "_get_response_message", None)
    if not (hasattr(client, "_parse_message_text")
            and response_parser is not None
            and hasattr(client, "get_input_entity") and callable(client)):
        kwargs = {"reply_to": reply_to} if reply_to is not None else {}
        sent = await client.send_message(entity, message, **kwargs)
        return sent, random_id

    from telethon.tl import functions as tl_functions, types as tl_types

    input_entity = await client.get_input_entity(entity)
    parsed, formatting_entities = await client._parse_message_text(str(message), ())
    if not parsed:
        raise ValueError("Telegram message cannot be empty")
    input_reply = (tl_types.InputReplyToMessage(reply_to_msg_id=int(reply_to))
                   if reply_to is not None else None)
    request = tl_functions.messages.SendMessageRequest(
        peer=input_entity,
        message=parsed,
        entities=formatting_entities,
        no_webpage=False,
        reply_to=input_reply,
        random_id=random_id,
    )
    response = await client(request)
    # Telegram may use this compact response for ordinary user dialogs.  The
    # runner only needs the accepted message id, not a fully hydrated Message.
    if isinstance(response, tl_types.UpdateShortSentMessage):
        sent = types.SimpleNamespace(id=response.id, message=parsed)
    else:
        if isinstance(response_parser, staticmethod):
            sent = response_parser.__func__(request, response, input_entity)
        else:
            sent = client._get_response_message(request, response, input_entity)
    if sent is None:
        raise RuntimeError("Telegram accepted no identifiable message")
    return sent, random_id


async def _send_file_idempotent(entity, item: media_core.OutboundMedia, *, reply_to=None,
                                random_id: int | None = None,
                                visible_filename: str | None = None):
    """Telethon send_file equivalent with a stable MTProto random_id.

    Uploading bytes may repeat after a crash; the visible SendMediaRequest does
    not.  Small hermetic test clients fall back to their public send_file stub.
    """
    random_id = (_telegram_media_random_id(item.queue_id)
                 if random_id is None else int(random_id))
    if random_id == 0:
        raise ValueError("Telegram random_id must be non-zero")
    force_document = item.kind == "document"
    if not (hasattr(client, "_file_to_media") and hasattr(client, "_parse_message_text")
            and hasattr(client, "_get_response_message")
            and hasattr(client, "get_input_entity") and callable(client)):
        send_kwargs = {
            "caption": item.caption[:900] or None,
            "reply_to": reply_to,
            "voice_note": bool(item.voice_note),
        }
        # Preserve the old photo/audio fallback call shape for small adapters;
        # only document delivery requires the additional Telethon flag.
        if force_document:
            send_kwargs["force_document"] = True
        sent = await client.send_file(entity, str(item.path), **send_kwargs)
        return sent, random_id

    from telethon.tl import functions, types

    input_entity = await client.get_input_entity(entity)
    conversion_kwargs = {
        "voice_note": bool(item.voice_note),
        "force_document": force_document,
    }
    if force_document:
        # The spool's collision-proof msg-<hash>-<nonce> prefix is private
        # implementation state.  Set Telegram's visible filename explicitly
        # so the recipient sees the requested artifact name only.
        conversion_kwargs["attributes"] = [types.DocumentAttributeFilename(
            file_name=(str(visible_filename).strip() if visible_filename
                       else media_core.delivery_basename(item.path)),
        )]
        conversion_kwargs["mime_type"] = item.mime
    _handle, input_media, _image = await client._file_to_media(
        str(item.path), **conversion_kwargs,
    )
    if not input_media:
        raise TypeError(f"cannot convert outbound media {item.path}")
    caption, formatting_entities = await client._parse_message_text(
        item.caption[:900], ())
    input_reply = (types.InputReplyToMessage(reply_to_msg_id=int(reply_to))
                   if reply_to is not None else None)
    request = functions.messages.SendMediaRequest(
        peer=input_entity,
        media=input_media,
        message=caption,
        entities=formatting_entities,
        reply_to=input_reply,
        random_id=random_id,
    )
    response = await client(request)
    return client._get_response_message(request, response, input_entity), random_id


def _direct_tool_execution(expected_tool: str | tuple) -> dict[str, object]:
    """Require the durable tool identity which owns a direct Telegram mutation.

    PASS 30 Этап 2: у моста может быть несколько законных хозяев (send_message И
    narrate) — контракт расширен до множества имён, идентичность по-прежнему
    обязана совпасть с реально исполняемым тулом."""

    expected = (expected_tool,) if isinstance(expected_tool, str) else tuple(expected_tool)
    label = "/".join(expected)
    execution = agent.current_tool_execution()
    if not isinstance(execution, dict):
        raise agent.DurableExecutionError(
            f"{label} requires a durable run/call identity"
        )
    run_id = str(execution.get("run_id") or "").strip()
    call_id = str(execution.get("call_id") or "").strip()
    tool = str(execution.get("tool") or "").strip()
    if not run_id or not call_id or tool not in expected:
        raise agent.DurableExecutionError(
            f"{label} has no matching durable run/call identity"
        )
    result = dict(execution)
    result["run_id"] = run_id
    result["call_id"] = call_id
    result["tool"] = tool
    return result


def _direct_tool_key(execution: dict[str, object]) -> str:
    key = str(execution.get("idempotency_key") or "").strip()
    if key:
        return key
    return (
        f"telegram-outbox:{execution['run_id']}:tool:{execution['call_id']}"
    )


def _one_sent_message_id(sent) -> int:
    rows = list(sent) if isinstance(sent, (list, tuple)) else [sent]
    ids = [int(getattr(row, "id")) for row in rows
           if getattr(row, "id", None) is not None]
    if len(ids) != 1 or ids[0] <= 0:
        raise RuntimeError("Telegram accepted no unique message id")
    return ids[0]


def _direct_outbox_result(entry: dict, *, label: str = "") -> str:
    """Deterministic tool receipt derived only from the durable acceptance row."""

    receipt = dict(entry.get("receipt") or {})
    message_id = receipt.get("message_id") or "?"
    peer_id = entry.get("peer_id")
    topic_id = entry.get("topic_id")
    selector = (telegram_topics.TopicRoute(str(peer_id), int(topic_id)).selector
                if topic_id is not None else str(peer_id))
    destination = str(label or selector)
    if entry.get("kind") == "file":
        payload = dict(entry.get("payload") or {})
        filename = str(payload.get("visible_filename") or "file")
        # Расписка называет ВИД вложения, а не «файл» вообще. 13.08 именно неразличимость
        # видов дала ход, в котором отправленный текст был прочитан как ушедшая картинка.
        kind = str(receipt.get("media_kind")
                   or payload.get("media_kind")
                   or telegram_outbox.LEGACY_MEDIA_KIND)
        noun = {"photo": "фото", "audio": "голосовое" if receipt.get("voice_note")
                or payload.get("voice_note") else "аудио"}.get(kind, "документ")
        return (
            f"Отправлено: {noun} → {destination} "
            f"(chat_id={selector}, message_id={message_id}): {filename}"
        )
    text = str((entry.get("payload") or {}).get("text") or "")
    return (
        f"Отправлено → {destination} "
        f"(chat_id={selector}, message_id={message_id}): {text[:60]}"
    )


async def _send_direct_outbox_entry(entry: dict, *, entity=None) -> dict:
    """Send one already-durable intent and persist acceptance before returning."""

    if entry.get("state") == "accepted":
        return entry
    # ⚠ 21.09, «почему архивы уходили» (20.09): dead_letter — приговор, а не отсрочка.
    # Повтор прогона (replay_outstanding_tool → _sync_send_file → сюда) приносил ту же
    # запись снова к сети, если её успели закрыть до входа. Отменённое намерение
    # («cancelled by owner before acceptance») и умершее по потолку попыток обязаны
    # остаться мёртвыми: отправка стала бы ровно тем «прервать = доставить» из записки.
    if entry.get("state") == "dead_letter":
        raise DirectOutboxUnsendable(
            "direct Telegram outbox entry is dead_letter and must never be sent: "
            + str(entry.get("last_error") or "")[:300])
    # Upgrade fence: old releases persisted kind=message as a directly executable
    # outbox row. It carries no proof of a due-time model decision, so retire it
    # before entity resolution (the first network-capable operation). Fresh ordinary
    # send-tool rows remain replayable below with their stable random_id.
    if str(entry.get("purpose") or "") == "task:message":
        return await asyncio.to_thread(
            _direct_outbox().dead_letter,
            str(entry["key"]),
            "legacy scheduled message lacks a fresh due-time model decision; "
            "old intent retained as evidence and must be reassessed",
        )
    if str(entry.get("purpose") or "").startswith("tool:"):
        # ⚠ 21.09, из записки «почему архивы уходили» (20.09): отмена хода с висящим
        # send-намерением не смела исполнять это намерение. Инцидент: отмена в 16:46,
        # резюм-машинерия «узнала исход» единственным знакомым способом — отправила;
        # файл дошёл в 19:24 (message_id 4444), ход стал cancelled в 19:24:15.
        # Просьба об отмене в манифесте (control.action=cancel) означает: у записей
        # этого прогона нет будущего, в котором отправка законна. Уводим в dead_letter
        # БЕЗ сети, с причиной, которую увидят и владелец, и леджер прогона.
        # Записи без run_id и повторы после явной приёмки (state=accepted выше) — не трогаем.
        run_id = str(entry.get("run_id") or "")
        if run_id and await asyncio.to_thread(_run_cancel_requested, run_id):
            reason = "cancelled by owner before acceptance"
            retired = await asyncio.to_thread(
                _direct_outbox().dead_letter, str(entry["key"]), reason)
            log.warning("direct Telegram outbox: отмена хода — намерение не отправлено "
                        "и закрыто dead_letter [%s]", entry.get("key"))
            await asyncio.to_thread(
                _announce_direct_outbox_dead_letter, dict(retired), reason)
            return retired
        prepared = await asyncio.to_thread(agent.direct_outbox_prepared, dict(entry))
        if not prepared:
            # ⚠ 17.08: отсутствие proof бывает двух сортов. Гонка с ещё живым тул-вызовом
            # (proof доедет через секунды) — ретраить законно. Терминальный прогон без
            # proof — константа: proof пишет только сам тул-вызов, ретраи изображали
            # надежду 2,5 часа и кончились тихим dead_letter. Различаем по прогону.
            if await asyncio.to_thread(agent.direct_outbox_proof_unreachable, dict(entry)):
                raise DirectOutboxUnsendable(
                    "direct Telegram tool entry has no exact pre-network run proof "
                    "and its run is terminal — the proof can never appear")
            raise agent.DurableExecutionError(
                "direct Telegram tool entry has no exact pre-network run proof")
    peer_id = int(entry["peer_id"])
    if entity is None:
        entity = await _resolve_entity(peer_id)
    if entity is None:
        raise RuntimeError(f"Telegram peer is unavailable: {peer_id}")
    reply_to = entry.get("reply_to")
    if reply_to is None:
        reply_to = entry.get("topic_id")
    payload = dict(entry.get("payload") or {})
    if entry.get("kind") == "text":
        sent, random_id = await _send_message_idempotent(
            entity,
            str(payload.get("text") or ""),
            delivery_key=str(entry["key"]),
            reply_to=reply_to,
            random_id=int(entry["random_id"]),
        )
    elif entry.get("kind") == "file":
        # ⚠ Здесь стояло `kind="document"` константой. Намерение могло говорить «фото»
        # или «голосовое», транспорт умел и то и другое (`_send_file_idempotent` читает
        # `item.kind` и `item.voice_note`) — а между ними лежала эта строка и делала
        # документом всё. 13.08.2026: тип читается из durable-намерения, поэтому и
        # первая отправка, и повтор после падения дают ОДИН И ТОТ ЖЕ вид вложения.
        item = media_core.OutboundMedia(
            kind=str(payload.get("media_kind") or telegram_outbox.LEGACY_MEDIA_KIND),
            path=Path(str(payload["staged_path"])),
            mime=str(payload["mime"]),
            size=int(payload["size"]),
            target_chat_id=peer_id,
            scope=("group" if peer_id < 0 else "known"),
            caption=str(payload.get("caption") or ""),
            reply_to_message_id=reply_to,
            voice_note=bool(payload.get("voice_note")),
            queue_id=str(entry["id"]),
            run_id=str(entry.get("run_id") or ""),
            sha256=str(payload.get("sha256") or ""),
        )
        sent, random_id = await _send_file_idempotent(
            entity,
            item,
            reply_to=reply_to,
            random_id=int(entry["random_id"]),
            visible_filename=str(payload.get("visible_filename") or "document.bin"),
        )
    else:
        raise RuntimeError(f"unsupported Telegram outbox kind: {entry.get('kind')}")
    message_id = _one_sent_message_id(sent)
    return await asyncio.to_thread(
        _direct_outbox().mark_accepted,
        str(entry["key"]),
        message_id=message_id,
        random_id=random_id,
    )


class DirectOutboxUnsendable(RuntimeError):
    """Доставка этой записи невозможна по построению; повтор не поможет никогда."""


def _outbox_run_call(entry: dict) -> tuple[str, str]:
    """(run_id, call_id) записи — из полей, с фолбэком на разбор ключа."""
    run_id = str(entry.get("run_id") or "")
    call_id = str(entry.get("call_id") or "")
    if not (run_id and call_id):
        parts = str(entry.get("key") or "").split(":")
        if len(parts) == 4 and parts[0] == "telegram-outbox" and parts[2] == "tool":
            run_id, call_id = run_id or parts[1], call_id or parts[3]
    return run_id, call_id


def _announce_direct_outbox_dead_letter(entry: dict, error: BaseException | str) -> None:
    """Мёртвая запись очереди обязана родить расписку, а не только строку WARNING.

    ⚠ 17.08, живой инцидент: реплика ей в личку Егора умерла dead_letter'ом через
    2,5 часа ретраев — ни записки владельцу, ни следа в прогоне. Егор узнал об отказе
    по часу тишины, а RECAP прогона продолжал говорить Delivery `sent`. Слово не
    имеет права исчезать беззвучно: текст сохраняется распиской владельцу (тот же
    механизм, что у постоянного отказа Telegram в run_delivery_failed), а прогон
    получает durable-событие. Обе расписки не смеют упасть громче самой потери.
    """
    payload = dict(entry.get("payload") or {})
    text = str(payload.get("text") or "") or str(payload.get("caption") or "")
    where = str(entry.get("peer_id") or "?")
    reason = str(entry.get("last_error") or error or "")[:400]
    run_id, call_id = _outbox_run_call(entry)
    key = str(entry.get("key") or "")
    # ⚠ 21.09: для ЗАПИСЕЙ РУК (purpose "tool:…") dead_letter обязан ЗАКРЫТЬ их
    # незакрытый вызов (tool_failed). Без этого отмена хода, закрывшая намерение
    # «cancelled by owner before acceptance», оставляла вызов outstanding навечно:
    # прогон не терминализуем, «paused» держался, скан отписывал resume_attempt_idle
    # вхолостую — та самая немота 17:19–18:42 из записки. tool_failed входит в
    # TOOL_OUTCOME_KINDS: расписка закрывает вызов, терминальные блокеры исчезают,
    # и скан доводит уже авторизованную отмену до cancelled.
    if run_id and call_id and str(entry.get("purpose") or "").startswith("tool:"):
        try:
            agent._runs().store_result(
                run_id, str(entry.get("last_error") or "outbox dead_letter"),
                call_id=call_id, name="telegram-outbox-dead-letter",
                event_kind="tool_failed", idempotent=False,
            )
        except Exception:
            log.warning("dead_letter не закрыл вызов руки [%s/%s]", run_id, call_id,
                        exc_info=True)
    try:
        owner_delivery.LEDGER.emit(
            "run_result",
            title=f"Не доставлено ({where}) — очередь закрыла запись, текст сохранён",
            body=text[:12000],
            outcome="failure",
            thread_key=f"run:{run_id}" if run_id else f"outbox:{entry.get('id')}",
            reason=f"outbox dead_letter: {reason}",
            correlation={"run_id": run_id, "peer_id": where, "outbox_key": key},
            provenance={"source": "direct_telegram_outbox", "source_id": key},
            expectation="Текст НЕ ушёл и сам уже не уйдёт; отправить можно только заново и осознанно.",
            dedupe_key=f"outbox-dead:{key}",
        )
    except Exception:
        log.warning("расписка владельцу о dead_letter не записалась [%s]", key, exc_info=True)
    if run_id and call_id:
        try:
            agent.record_direct_outbox_dead_letter(run_id, call_id, dict(entry))
        except Exception:
            log.warning("durable-событие dead_letter не записалось [%s]", key, exc_info=True)


def _record_direct_outbox_failure(key: str, error: BaseException) -> tuple[bool, dict]:
    """Отказ Telegram в журнал: (постоянный?, новое состояние записи).

    ⚠ 26.07, живой инцидент. Она отправила пост в «@abstractDL» — а это КАНАЛ, не чат
    обсуждения, и прав писать там у неё нет. Telegram ответил ChatAdminRequiredError, то
    есть «нет и не будет». Журнал записал это как обычную неудачу, и часы резюма стали
    поднимать тот же ран каждые 45 секунд: 18:08:49, 18:09:02, 18:09:47, 18:10:35 и
    дальше без конца. Дверь закрыта навсегда, а стук не прекращался.

    Разница между «сейчас не вышло» и «здесь нельзя» — не оттенок, а два разных мира:
    первое стоит повторить, второе надо СКАЗАТЬ ей, чтобы она исправила адрес сама. Список
    постоянных отказов уже есть в agent и уже используется на другом пути доставки; берём
    его, а не заводим второй — иначе два пути разойдутся, а это ровно тот класс беды,
    который весь этот день и разбирали.
    """
    outbox = _direct_outbox()
    if isinstance(error, DirectOutboxUnsendable):
        return True, outbox.dead_letter(
            key, f"unsendable by construction: {str(error)[:300]}")
    if agent._is_permanent_delivery_error(error):
        reason = (f"permanent Telegram refusal: {type(error).__name__}: "
                  f"{str(error)[:300]}")
        return True, outbox.dead_letter(key, reason)
    return False, outbox.record_retry(key, error)


async def _retry_direct_outbox_entry(entry: dict, error: BaseException) -> dict:
    permanent, state = await asyncio.to_thread(
        _record_direct_outbox_failure, str(entry["key"]), error,
    )
    if permanent:
        log.warning("direct Telegram outbox: постоянный отказ, больше не повторяю [%s]: %s",
                    entry.get("key"), error)
    # Переход в dead_letter (постоянным отказом ЛИБО исчерпанием попыток в record_retry)
    # объявляет себя расписками — см. _announce_direct_outbox_dead_letter (инцидент 17.08).
    if (str(state.get("state") or "") == "dead_letter"
            and str(entry.get("state") or "") != "dead_letter"):
        await asyncio.to_thread(
            _announce_direct_outbox_dead_letter, {**dict(entry), **dict(state)}, error)
    return state


def _run_is_settled(run_id: str) -> bool:
    """Прогон терминален по манифесту (без замка: терминальность поглощающая, файл пишется
    атомарно — см. run_manager.live_run_ids). Любая осечка — «не знаю» → False."""
    if not run_id:
        return False
    import json as _json
    import run_manager as _rm
    try:
        raw = _json.loads((agent._runs().path(run_id) / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    return str((raw or {}).get("status") or "") in _rm.TERMINAL_STATUSES


def _run_cancel_requested(run_id: str) -> bool:
    """Владелец попросил отмену — висящие отправки этого прогона не имеют будущего.

    ⚠ 21.09, живой инцидент из записки «почему архивы уходили» (20.09). В 16:46 Егор
    нажал «прервать» в Пульте; `request_cancel` честно не терминализовал прогон с
    незакрытым вызовом, а единственный способ узнать исход висящего send-намерения
    машинерия знала один — ИСПОЛНИТЬ его. В 19:24:14 файл дошёл (message_id 4444),
    и лишь в 19:24:15 ход стал cancelled. «Прервать» означало «доставить». Здесь —
    фильтр для outbox-периодики: просьба об отмене уже есть в манифесте (control),
    значит досылать намерение нельзя. Читается без замка, как _run_is_settled;
    любая осечка — «не знаю» → False (не хороним чужую отправку по ошибке чтения).
    """
    if not run_id:
        return False
    import json as _json
    try:
        raw = _json.loads((agent._runs().path(run_id) / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    control = dict((raw or {}).get("control") or {})
    if control.get("action") == "cancel":
        return True
    # Терминальная отмена чистит control в манифесте — но «cancelled» сам по себе
    # приговор намерениям этого прогона: их не возвращают к сети ни при каких раскладах.
    return str((raw or {}).get("status") or "") == "cancelled"


_LATE_ACCEPTANCE_ANNOUNCED: set[str] = set()
# ⚠ 21.09.2026, вечер того же дня. Строка ниже стоила микросекунд ровно до тех пор, пока
# объявлять было нечего. Утром её включили — и `_direct_outbox_once` вторым циклом пошёл
# по ВСЕМ принятым записям (3443 на этот день, от июля), на каждую дописывая строку в
# дневник и ПОЛНОСТЬЮ переиндексируя дневник в `recall.sqlite3` на 443 МБ. Дневник дня
# вырос до 16 192 строк, `main()` встал на `await _direct_outbox_once()` ДО
# `asyncio.create_task(_clock())` — часы не рождались, и просьбу владельца «прервать»
# читать было НЕКОМУ; ядро держалось на 96 %. Набор выше живёт в памяти процесса, поэтому
# каждый перезапуск объявлял всё заново и делал следующий boot медленнее: рестарт не
# лечил, а углублял.
#
# Предел по ВОЗРАСТУ, а не по памяти: возраст лежит в самой записи и переживает
# перезапуск по построению. Поздняя приёмка — это новость «ты считала, что не ушло, а
# ушло»; приёмка недельной давности новостью не является ни для кого. Сверку расписок
# это не трогает вовсе: гасится только объявление.
_LATE_ACCEPTANCE_MAX_AGE_SEC = 24 * 3600


def _iso_epoch(value) -> float:
    """Время записи в секундах эпохи; неразобранное считаем СВЕЖИМ, не старым."""
    try:
        return datetime.datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return float("inf")


async def _announce_late_acceptance(entry: dict) -> None:
    """Поздняя приёмка Telegram обязана вернуться к ней — а не только в Пульт.

    ⚠ 21.09, из записки «почему архивы уходили» (20.09). Крупные отправки валились по
    потолку ожидания, ход уходил в paused/in_doubt, считая файл НЕ ушедшим, — а повтор
    доводил его до приёмки. Квитанция file_ready улетала в owner_delivery с
    transports=("pwa",) — то есть Егору в Пульт; ей не возвращалось ничего: тул-результат
    модели не доезжал (ход мёртв), в журнал и буфер не попадало ни строки. Она честно
    отвечала «ничего не отправляю», пока файлы приезжали. Эта строка — возврат знания
    тому, кто отправлял: файл ушёл, ход считал его упавшим. Только для записей рук
    (purpose "tool:…") и только когда ход уже terminaled/in_doubt; живому ходу тул-результат
    доедет своим маршрутом, дублировать нечего.
    """
    key = str(entry.get("key") or "")
    if not key or key in _LATE_ACCEPTANCE_ANNOUNCED:
        return
    if _iso_epoch(entry.get("updated_at")) < time.time() - _LATE_ACCEPTANCE_MAX_AGE_SEC:
        _LATE_ACCEPTANCE_ANNOUNCED.add(key)
        return
    _LATE_ACCEPTANCE_ANNOUNCED.add(key)
    payload = dict(entry.get("payload") or {})
    filename = str(payload.get("visible_filename") or payload.get("text")
                   or "document.bin")[:240]
    receipt = dict(entry.get("receipt") or {})
    message_id = receipt.get("message_id")
    chat = str(entry.get("peer_id") or "?")
    try:
        agent.tool_journal(
            f"поздняя приёмка: файл {filename} ушёл в {chat}, "
            f"message_id {message_id} — ход считал его упавшим",
            salience=2)
    except Exception:
        log.warning("журнал о поздней приёмке не записался [%s]", key, exc_info=True)


async def _reconcile_direct_outbox_entry(entry: dict) -> bool:
    """Project a transport receipt into its run ledger; never invoke the model."""

    key = str(entry.get("key") or "")
    if key in _DIRECT_OUTBOX_RECONCILED:
        return True
    purpose = str(entry.get("purpose") or "")
    if not purpose.startswith("tool:"):
        _DIRECT_OUTBOX_RECONCILED.add(key)
        return True
    # 13.09: прогон уже закрыт — проецировать расписку некуда, его доставка сведена своим
    # ходом (терминальная причина «Telegram delivery reconciled from durable receipts»).
    # Раньше ~30 принятых записей августа сверялись заново КАЖДЫЙ тик и каждый старт
    # (набор выше — в памяти процесса); когда ретенция сняла results/ у тех прогонов,
    # каждая сверка стала трейсбеком: 1 609 за 25 минут.
    if _run_is_settled(str(entry.get("run_id") or "")):
        await _announce_late_acceptance(entry)
        _DIRECT_OUTBOX_RECONCILED.add(key)
        return True
    reconcile = getattr(agent, "run_direct_outbox_accepted", None)
    if not callable(reconcile):
        return False
    try:
        reconciled = await asyncio.to_thread(reconcile, dict(entry))
    except Exception:
        log.exception("direct Telegram outbox reconciliation failed [%s]", key)
        return False
    if not reconciled:
        # Проекция не состоялась (ход уже не paused/running, расписку некуда класть) —
        # отправителю всё равно нужно узнать, что файл ушёл: тот же поздний возврат.
        await _announce_late_acceptance(entry)
        return False
    if reconciled:
        if (entry.get("kind") == "file" and OWNER_ID
                and int(entry.get("peer_id") or 0) == int(OWNER_ID)):
            payload = dict(entry.get("payload") or {})
            run_id = str(entry.get("run_id") or "")
            filename = str(payload.get("visible_filename") or "document.bin")[:240]
            await asyncio.to_thread(
                owner_delivery.LEDGER.emit,
                "file_ready",
                title=f"Файл готов: {filename}",
                body=str(payload.get("caption") or "")[:1200],
                outcome="success",
                thread_key=f"run:{run_id}" if run_id else "telegram-file",
                correlation={
                    "run_id": run_id,
                    "message_id": str((entry.get("receipt") or {}).get("message_id") or ""),
                    "sha256": str(payload.get("sha256") or ""),
                },
                reason="Telegram принял файл под стабильным random_id.",
                provenance={"source": "direct_telegram_outbox", "source_id": key},
                expectation="Файл уже в Telegram; полный след и артефакты доступны в run.",
                action={
                    "label": "Открыть run", "domain": "praxis",
                    "action": "run.open", "run_id": run_id,
                } if run_id else {},
                result=filename,
                dedupe_key=f"direct-file:{key}",
                transports=("pwa",),
            )
        _DIRECT_OUTBOX_RECONCILED.add(key)
        return True
    return False


async def _direct_outbox_once() -> None:
    """Replay authorized sends; retire pre-freshness scheduled messages fail-closed."""

    outbox = _direct_outbox()
    pending = await asyncio.to_thread(
        outbox.pending, due_only=True, verify_files=True,
    )
    for entry in pending[:100]:
        if str(entry.get("purpose") or "") == "task:message":
            retired = await asyncio.to_thread(
                outbox.dead_letter,
                str(entry["key"]),
                "legacy scheduled message lacks a fresh due-time model decision; "
                "old intent retained as evidence and must be reassessed",
            )
            log.warning("legacy scheduled Telegram outbox intent retired without transport [%s]",
                        entry.get("key"))
            await asyncio.to_thread(
                _announce_direct_outbox_dead_letter, retired,
                "upgrade freshness quarantine",
            )
            continue
        # 21.09: отмена хода с висящим намерением — его не отправляем (см. фильтр в
        # _send_direct_outbox_entry); периодика закрывает запись dead_letter здесь же,
        # не тратя попытки и не подходя к сети.
        pending_run_id = str(entry.get("run_id") or "")
        if (pending_run_id
                and await asyncio.to_thread(_run_cancel_requested, pending_run_id)):
            reason = "cancelled by owner before acceptance"
            retired = await asyncio.to_thread(
                outbox.dead_letter, str(entry["key"]), reason)
            log.warning("direct Telegram outbox: отмена хода — периодика не отправляет "
                        "запись и закрывает её [%s]", entry.get("key"))
            await asyncio.to_thread(
                _announce_direct_outbox_dead_letter, dict(retired), reason)
            continue
        try:
            accepted = await _send_direct_outbox_entry(entry)
        except Exception as exc:
            try:
                state = await _retry_direct_outbox_entry(entry, exc)
                log.warning(
                    "direct Telegram outbox retry [%s] state=%s attempts=%s: %s",
                    entry.get("key"), state.get("state"), state.get("attempts"), exc,
                )
            except Exception:
                log.exception("direct Telegram outbox retry journal failed [%s]", entry.get("key"))
            continue
        await _reconcile_direct_outbox_entry(accepted)

    accepted_rows = await asyncio.to_thread(outbox.accepted, verify_files=False)
    for entry in accepted_rows:
        if str(entry.get("key") or "") in _DIRECT_OUTBOX_RECONCILED:
            continue
        await _reconcile_direct_outbox_entry(entry)


async def _send_turn_media(entity, item: media_core.OutboundMedia, *,
                           ctx: agent.ChannelContext, reply_to=None,
                           state_chat_id: str | None = None) -> dict | None:
    """Return a Telegram acceptance receipt; persistence failures never cause a resend."""
    if item.run_id:
        try:
            started = await asyncio.to_thread(
                agent.run_delivery_media_started, item.run_id, item.queue_id,
            )
        except Exception:
            log.exception("media tool-start receipt не записался [%s]", item.queue_id)
            return None
        if not started:
            log.error("media tool-start receipt отсутствует [%s]", item.queue_id)
            return None
    try:
        _media_spool().validate_outbound(
            item, expected_scope=ctx.scope, expected_chat_id=ctx.chat_id)
        media_reply_to = (item.reply_to_message_id
                          if item.reply_to_message_id is not None else reply_to)
        sent, random_id = await _send_file_idempotent(
            entity, item, reply_to=media_reply_to,
        )
    except Exception as exc:
        # ⚠ Классификация постоянного отказа снимается ЗДЕСЬ, с самого исключения.
        # Раньше наверх уезжала строка «queued for retry», а `_is_permanent_delivery_error`
        # по построению отвечает False на строку — то есть медийный шов не мог отличить
        # «сеть моргнула» от «сюда файлы нельзя» в принципе. 03.08 это стоило 728 отказов
        # за тринадцать часов и потерянной работы, о которой она не знала.
        permanent = agent._is_permanent_delivery_error(exc)
        log.log(logging.ERROR if not permanent else logging.WARNING,
                "исходящее медиа не отправилось [%s]%s", ctx.chat_id,
                " — маршрут отказал НАВСЕГДА, повторять не буду" if permanent else "",
                exc_info=not permanent)
        if permanent:
            log.warning("файл остался у неё: %s (%s)", item.path, type(exc).__name__)
        if item.run_id:
            try:
                await asyncio.to_thread(
                    agent.run_delivery_media_result, item.run_id, item.queue_id,
                    ok=False,
                    error=(f"{type(exc).__name__}: {exc}"[:200] if permanent
                           else "Telegram media upload failed; queued for retry"),
                    permanent=permanent,
                    chat_id=str(ctx.chat_id or ""),
                    path=str(getattr(item, "path", "") or ""),
                    caption=str(getattr(item, "caption", "") or ""),
                )
            except Exception:
                log.exception("media failure receipt не записался [%s]", item.queue_id)
        return None

    receipt = {
        "message_id": getattr(sent, "id", None),
        "random_id": random_id,
        "accepted_at": time.time(),
    }
    try:
        tag = {
            "photo": "[Изображение]",
            "audio": "[Аудио]",
            "document": "[Файл]",
        }.get(item.kind, "[Медиа]")
        _buf_push(str(state_chat_id or ctx.chat_id),
                  f"Praxis: {tag}" + (f" {item.caption}" if item.caption else ""),
                  author="Praxis", is_dm=ctx.is_dm,
                  source_id=str(receipt["message_id"] or ""), ts=receipt["accepted_at"])
    except Exception:
        log.exception("принятое Telegram media не попало в hot buffer [%s]", item.queue_id)
    log.info("МЕДИА(%s) [%s] -> %s id=%s", item.kind, ctx.chat_id, item.path.name,
             receipt["message_id"])
    return receipt


async def _attempt_queued_media(entity, item: media_core.OutboundMedia, *,
                                ctx: agent.ChannelContext, reply_to=None,
                                finalize_recovered: bool = True,
                                state_chat_id: str | None = None) -> bool:
    """One in-flight upload per queue id; acknowledge the queue only after success."""
    if item.queue_id in _MEDIA_SENDING:
        return False
    _MEDIA_SENDING.add(item.queue_id)
    try:
        spool = _media_spool()
        receipt = _MEDIA_ACCEPTED.get(item.queue_id)
        if receipt is None:
            receipt = await _send_turn_media(
                entity, item, ctx=ctx, reply_to=reply_to,
                state_chat_id=state_chat_id,
            )
        if receipt is None:
            return False

        # Telegram has accepted the stable random_id.  From this point onward
        # no bookkeeping exception is allowed to turn acceptance into a resend.
        acknowledged = False
        for delay in (0.0, 0.05, 0.2):
            if delay:
                await asyncio.sleep(delay)
            try:
                changed = await asyncio.to_thread(
                    spool.discard, item.queue_id, receipt=receipt,
                )
                if changed or any(
                        row.get("queue_id") == item.queue_id
                        for row in await asyncio.to_thread(spool.outbox_results, "delivered")):
                    acknowledged = True
                    break
            except Exception:
                log.exception("media acceptance tombstone не записался [%s]", item.queue_id)
        if acknowledged:
            _MEDIA_ACCEPTED.pop(item.queue_id, None)
        else:
            _MEDIA_ACCEPTED[item.queue_id] = dict(receipt)
            log.critical("Telegram принял media, но outbox ack пока не записан [%s]",
                         item.queue_id)

        run_receipt = not bool(item.run_id)
        if item.run_id:
            try:
                await asyncio.to_thread(
                    agent.run_delivery_media_result, item.run_id, item.queue_id,
                    ok=True, message_id=receipt.get("message_id"),
                )
                run_receipt = True
            except Exception:
                # The outbox tombstone is now the recovery source for this
                # secondary run receipt.  Keep the staged bytes until the
                # tombstone has been projected into the run WAL; never retry
                # Telegram because of this bookkeeping gap.
                log.exception("media run receipt не записался после acceptance [%s]",
                              item.queue_id)
        if acknowledged and run_receipt:
            try:
                item.path.unlink(missing_ok=True)
            except OSError:
                pass
        if (acknowledged and run_receipt and finalize_recovered and item.run_id
                and not any(pending.run_id == item.run_id for pending in spool.pending())):
            await asyncio.to_thread(
                agent.run_delivery_finalize_recovered, item.run_id, media_count=1,
            )
        return True
    finally:
        _MEDIA_SENDING.discard(item.queue_id)


async def _queue_and_send_media(entity, item: media_core.OutboundMedia, *,
                                ctx: agent.ChannelContext, reply_to=None,
                                state_chat_id: str | None = None) -> bool:
    """Persist the retry intent in the bounded in-process queue before first upload."""
    # The original address is durable routing provenance.  Without persisting it, a
    # failed forum upload retries into General after restart.
    if item.reply_to_message_id is None and reply_to is not None:
        item = replace(item, reply_to_message_id=reply_to)
    spool = _media_spool()
    if not any(pending.queue_id == item.queue_id for pending in spool.pending()):
        try:
            spool.enqueue(item)
        except media_core.MediaError:
            log.exception("исходящее медиа не встало в retry-очередь [%s]", ctx.chat_id)
            return False
    return await _attempt_queued_media(
        entity, item, ctx=ctx, reply_to=reply_to, finalize_recovered=False,
        state_chat_id=state_chat_id,
    )


def _consume_pending_media(chat_id: str, refs: tuple[media_core.MediaRef, ...]) -> None:
    """Detach only refs from a terminally processed attempt; keep newer concurrent arrivals."""
    queue = _pending_media.get(chat_id)
    if not queue:
        return
    for ref in refs:
        try:
            queue.remove(ref)
        except ValueError:
            pass
    if not queue:
        _pending_media.pop(chat_id, None)


TELEGRAM_TEXT_CHUNK_UTF16 = 3800


def _utf16_units(text: str) -> int:
    """Telegram measures message length in UTF-16 code units, not Python chars."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in (text or ""))


def _split_telegram_text(text: str, limit: int = TELEGRAM_TEXT_CHUNK_UTF16) -> tuple[str, ...]:
    """Lossless shared UTF-16/Markdown boundary contract."""
    from telegram_text import split_text
    return split_text(text, limit)


async def _await_despite_cancellation(awaitable):
    """Return an awaitable's result and whether cancellation arrived meanwhile."""
    task = asyncio.create_task(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


async def _voice_turn_offloaded(*args, **kwargs):
    return await asyncio.to_thread(agent.voice_turn_envelope, *args, **kwargs)


async def _voice_turn_with_typing(entity, *args, **kwargs):
    async with client.action(entity, "typing"):
        return await _voice_turn_offloaded(*args, **kwargs)


def _gate_group_wake_room_mode(peer_id, chat_id, wake: GroupWake, *, where: str) -> tuple[str, str]:
    """Resolve one live group mode and settle this wake mechanically.

    Returns ``(mode, outcome)`` where outcome is ``open``, ``retry`` or ``closed``.
    A sensor failure keeps the immutable wake/media and schedules another read; a real
    frozen/dead mode consumes only this wake generation so it cannot revive later.
    """
    room_mode, resolution_failed = _resolve_room_mode(peer_id, chat_id, where=where)
    if room_mode not in ("frozen", "dead"):
        return room_mode, "open"
    _note_room_mode_skip(
        peer_id, chat_id, room_mode, stage="room_mode",
        resolution_failed=resolution_failed,
    )
    if resolution_failed:
        _defer_pass(chat_id, 60.0)
        return room_mode, "retry"
    if _group_wakes.get(chat_id) is wake:
        _group_wakes.pop(chat_id, None)
    _consume_pending_media(chat_id, wake.media_snapshot)
    return room_mode, "closed"


def _release_answered_wake(chat_id: str, wake: "GroupWake") -> bool:
    """Снять wake, чей адрес этот ход уже ответил. True — снят.

    25.09, AbstractDL 08:53/08:59: ход ответил на #109479 (109494, done), но пока он шёл,
    Анатолий отредактировал сообщение — `_revise_group_wake` подменяет объект
    (`replace(wake, …)`), тот же адрес, новый объект. Прежняя сверка «is wake» его не
    узнавала: wake переживал свой же отвеченный ход, `_arm` взводил его снова, и тот же
    #id получил второй ответ (109499) — «Пракс раздуплилась надвое». Отвеченный адрес —
    это message_id, а не идентичность объекта. Новый адрес (другой message_id) остаётся:
    им владеет свой проход.
    """
    live = _group_wakes.get(chat_id)
    if live is None:
        return False
    same_object = live is wake
    same_address = (wake.message_id is not None and live.message_id == wake.message_id)
    if same_object or same_address:
        _group_wakes.pop(chat_id, None)
        if not same_object:
            log.info("wake [%s] #%s: снят по адресу (объект подменила правка сообщения)",
                     chat_id, wake.message_id)
        return True
    return False


async def _run_pass(chat_id: str) -> None:
    """Ход по чату (PASS 8.1): и личка, и группа — голос. Путь: reflex (в on_new) → voice →
    audience-aware finalizer (в owner-DM без оценки речи) → send | [молчу]. Фокус-окно она открывает
    сама тулом focus — планировщик часов подхватит намерение на ближайшем тике."""
    if chat_id in _passing:
        # PASS 21: раньше ЛС-триггер здесь ТЕРЯЛСЯ (асимметрия с group-rearm в finally) —
        # теперь помечаем и перевзводим после хода; причина пропуска видна.
        if _meta.get(chat_id, {}).get("is_dm", True):
            _dm_rearm.add(chat_id)
            perception.note_skip("busy", "отложила", chat_id=chat_id,
                                 detail="ход уже идёт — вернусь после него")
        return
    meta = _meta.get(chat_id, {})
    is_dm = meta.get("is_dm", True)
    persisted_route = _route_from_state(chat_id)
    peer_id = str(meta.get("peer_id") or persisted_route.peer_id)
    topic_id = meta.get("topic_id", persisted_route.topic_id)
    route = telegram_topics.TopicRoute(peer_id, topic_id)
    wake = None if is_dm else _group_wakes.get(chat_id)
    # Отложенный таймер несёт только chat_id и может пережить уже завершённый/заменённый
    # ход. Без живого immutable wake он не имеет права брать свежий хвост Telegram.
    if not is_dm and wake is None:
        log.debug("устаревший групповой проход без wake [%s] — no-op", chat_id)
        perception.note_skip("stale_wake", "отложила", chat_id=chat_id,
                             detail="проход пережил свой wake")
        return
    if not is_dm:
        room_mode, room_outcome = _gate_group_wake_room_mode(
            peer_id, chat_id, wake, where="delayed group pass",
        )
        if room_outcome != "open":
            return
        meta["room_mode"] = room_mode
    elapsed = time.time() - _last_pass[chat_id]
    addressed = bool(meta.get("addressed", False)) if is_dm else bool(wake.addressed)
    cd = _cooldown(is_dm, meta.get("room_mode", "normal"), addressed=addressed)
    if elapsed < cd:
        _defer_pass(chat_id, cd - elapsed + 0.05)  # transport retry, не task и не loop
        perception.note_skip("cooldown", "отложила", chat_id=chat_id,
                             detail=(f"transport retry через {cd - elapsed:.0f}с; "
                                     "после него актуальность решается заново"),
                             count_repeats=False)
        return
    # A queued moderation review is the next live turn once a current pass has finished.
    # Do not let a newly due ordinary chat debounce claim the only mind first.
    if _MODERATION_PRIORITY_PENDING:
        # 0,5 с, а не 0,05: при застрявшем флаге прежние 50 мс делали 20 Гц холостого
        # хода НА ЧАТ (28.08 — ~31 000 оборотов за инцидент, каждый с note_skip на
        # диск из event loop). Разбор модерации в любом случае живёт секунды — более
        # частый опрос не возвращает голос раньше, только жжёт цикл.
        _defer_pass(chat_id, 0.5)
        perception.note_skip("moderation_priority", "отложила", chat_id=chat_id,
                             detail="очередь shadow-модерации берёт следующий свободный ход",
                             count_repeats=False)
        return
    _passing.add(chat_id)
    armed_gen = _supersede_gen.get(chat_id, 0)  # PASS 29: снимок ДО первого await
    addressed_mid = meta.get("addressed_mid") if is_dm else (
        wake.message_id if wake.addressed else None)
    terminal = False
    superseded_abandon = False  # PASS 29: этот черновик брошен в пользу преемника
    group_retry_scheduled = False  # post-lock mode sensor already owns the next retry
    cancellation_seen = False
    delivery_run_id = ""
    delivery_message_ids: list[str] = []
    # Она одна: этот живой ход держит единый когнитивный проход, пока идёт. Второй чат или
    # автономное окно не побегут параллельно (окно, увидев замок, отложится). Освобождаем
    # в finally.
    #
    # ⚠ Ожидание замка — снаружи try, поэтому отмена ЗДЕСЬ не дошла бы до finally и
    # оставила бы chat_id в `_passing` навсегда: дальше каждый проход этого чата выходит
    # на первой же строке, и комната глохнет насовсем. Раньше сюда было не попасть — при
    # закрытом на окно Telethon новое сообщение не приходило, а значит и `_arm` не
    # отменял дебаунс-таск (об этом и говорил прежний комментарий). 26.07 пульс перестал
    # рвать связь, и ждать замка стало можно минутами ПОД живым входящим потоком: второе
    # сообщение в тот же чат отменяет таск ровно на этом await. Держим свою уборку сами.
    try:
        await _ONE_MIND.acquire()
    except BaseException:
        _passing.discard(chat_id)
        raise
    try:
        if not is_dm:
            # Edits can revise the immutable wake while this pass waits for the global
            # lock; deletion can withdraw it entirely.  Rebind at the last safe point so
            # local variables do not preserve a stale query/speaker or answer a removed
            # address.  A genuinely newer message owns its own pass and is re-armed in
            # finally; this old one becomes a no-op.
            live_wake = _group_wakes.get(chat_id)
            if live_wake is None:
                return
            if live_wake is not wake:
                if live_wake.message_id != wake.message_id:
                    return
                wake = live_wake
                addressed_mid = wake.message_id if wake.addressed else None
            # The first read happened before a potentially minutes-long wait for the global
            # cognitive lock. Re-read now, at the last safe point before context/model work:
            # a room frozen while we waited must not receive a reply from the old wake.
            room_mode, room_outcome = _gate_group_wake_room_mode(
                peer_id, chat_id, wake, where="group pass after one-mind lock",
            )
            if room_outcome != "open":
                group_retry_scheduled = room_outcome == "retry"
                return
            meta["room_mode"] = room_mode
        _last_pass[chat_id] = time.time()
        # 25.09 (G §3): ход в этом чате начался — его уведомления из накопителя сняты. Она
        # зашла в комнату как обычно и видит весь контекст сама; слова владельца для окон
        # (`owner_*`) этим не трогаются.
        try:
            from core import notices as core_notices
            await asyncio.to_thread(core_notices.clear_chat, chat_id)
        except Exception:
            log.debug("накопитель уведомлений: снятие для [%s] не удалось", chat_id, exc_info=True)
        # Тот же разговор, но ролями: её реплики поедут в модель как ЕЁ реплики, а не
        # строками «Praxis: …» / «[…; Praxis [id …]] …» внутри чужого текста. Сплошная
        # склейка при этом никуда не девается — на ней стоят расписки, исходящая граница
        # и прожитый ход.
        turn_history: list[dict] = []
        turn_current = ""
        turn_occurrences = {}
        if is_dm:
            last_n = await _last_n_text(chat_id)
            turn_history, turn_current = await asyncio.to_thread(
                _dm_dialogue, chat_id, occurrence_sidecar=turn_occurrences)
            turn_media = tuple(_pending_media.get(chat_id, ()))
            speaker = meta.get("name")
            owner = meta.get("is_owner", False)
            known = meta.get("known", True)
            family = meta.get("family", False)
            reply_targets = tuple(_recent_msgs[chat_id])
            address_kind = None
            address_age_sec = None
        else:
            # Кулдаун может длиться минуты: исходный wake остаётся неизменным, но новый
            # разговор после него виден отдельно для свежего решения об актуальности.
            current_context, current_turns = _group_context_frozen(
                chat_id, meta.get("room_policy") or _room_policy_for_state(chat_id))
            last_n = wake.context_snapshot
            group_turns = tuple(wake.turns_snapshot)
            if current_context and current_context != wake.context_snapshot:
                # ⚠ Здесь приклеивался ВЕСЬ свежий снимок комнаты — то есть ранние
                # сообщения уезжали в её кадр по второму разу, до +20 000 символов
                # дословного дубля на ход (замер 03.08). Показываем ДОБАВИВШЕЕСЯ.
                #
                # Сравнение идёт по записям и по их содержимому, а не по префиксу строк:
                # у ленты есть служебные вставки (корень ветки, пометка обреза), и
                # пометка МЕНЯЕТСЯ вместе с числом показанных сообщений — на префиксе
                # это разошлось бы каждый раз. Отредактированное сообщение при этом
                # честно приезжает ещё раз: оно и правда стало другим.
                known_lines = {line for _s, line, _r in group_turns}
                extra = tuple(item for item in current_turns
                              if item[1] not in known_lines)
                if not group_turns and not current_turns:
                    # Авторства нет ни там, ни там (запасной путь по буферу) — прежнее
                    # поведение: показываем свежий снимок целиком.
                    extra = ()
                    addition = current_context
                else:
                    addition = "\n".join(line for _s, line, _r in extra)
                if addition.strip():
                    note = ("\n\n---\n[После исходной реплики разговор продолжился; "
                            "это контекст для новой проверки актуальности, а не новая задача.]\n")
                    last_n += note + addition.lstrip("\n")
                    if group_turns and extra:
                        group_turns = group_turns + ((False, note.strip(), note.strip()),) + extra
            turn_history, turn_current = _group_dialogue(group_turns)
            turn_occurrences = (dict(wake.occurrence_sidecar)
                                if isinstance(wake.occurrence_sidecar, dict) else {})
            # Preserve the complete legacy dialogue, including traffic after the
            # wake. The frozen projection is only a sidecar: agent adoption and
            # exact provider selection must both succeed before it can be served.
            turn_media = wake.media_snapshot
            speaker = wake.speaker if wake.addressed else None
            owner = wake.owner
            known = wake.known if wake.addressed else True
            family = wake.family
            reply_targets = wake.reply_targets_snapshot
            address_kind = wake.kind
            address_age_sec = max(0.0, time.time() - wake.message_ts)
            log.info("%s проход [%s] закреплён за #%s (возраст %.1fс)",
                     "адресный" if wake.addressed else "reflective ambient",
                     chat_id, wake.message_id, address_age_sec)
        entity = meta.get("entity", peer_id)
        # Пункт 4, ТЕНЬ. Настоящий отправитель известен прямо здесь и прямо здесь же
        # ниже затирается на praxis:self, когда пробуждение не адресное. Меряем факт до
        # подмены; поведение не меняется ни на строку — конверт никем не читается.
        _raw_actor = (meta.get("sender_id") if is_dm
                      else (wake.sender_id if wake is not None else None))
        _synth = bool(not is_dm and wake is not None and not wake.addressed)
        _origin_mid = (meta.get("origin_message_id") if is_dm
                       else (wake.message_id if wake is not None else None))
        _envelope = context_envelope.measure(
            chat_id=chat_id, room_id=peer_id, is_dm=is_dm, owner=owner,
            praxis_self=_synth, actor_raw=_raw_actor, synthesized=_synth,
            triggers=((context_envelope.Trigger(
                principal_id=str(_raw_actor), message_id=_origin_mid,
                ts=time.time(), kind=("dm" if is_dm else (
                    "addressed" if (wake is not None and wake.addressed) else "ambient"))),)
                if _raw_actor is not None else ()),
            delegation_ref=("wake:not_addressed" if _synth else ""),
            origin_message_id=_origin_mid,
            origin_addressed=bool(is_dm or (wake is not None and wake.addressed)),
        )
        # §2: единый объект канала (scope + осознанность чата) — один источник правды на весь ход
        ctx = agent.ChannelContext(
            # Agent-local state (notes, runs, inbound/outbound media guards) is
            # conversation-scoped.  The runner keeps the real peer/topic route and
            # is the only layer that translates it into Telethon delivery arguments.
            chat_id=chat_id, room_id=peer_id,
            principal_id=(meta.get("sender_id") if is_dm else
                          wake.sender_id if wake.addressed else agent.PRAXIS_SELF_PRINCIPAL),
            origin_message_id=(meta.get("origin_message_id") if is_dm else wake.message_id),
            # ⚠ В группе это поле оставалось ПУСТЫМ, хотя текст обращения лежит рядом, в
            # снимке пробуждения. Из-за пустоты поиск по её памяти звался всем контекстом
            # комнаты целиком: замер 26.07 — 40104 символа вместо 170, 21.2с вместо 3.8с,
            # и находки мимо (всплывала чужая архитектурная выкладка вместо самой темы).
            # Склейка комнаты это ухудшила: запрос вырос с ветки до всего места.
            origin_text=(str(meta.get("origin_text") or "") if is_dm
                         else str((wake.query if wake is not None else "") or "")),
            is_dm=is_dm, owner=owner,
            known=known, family=family,  # 10.10
            addressed=addressed,
            address_message_id=addressed_mid if not is_dm else None,
            address_kind=address_kind,
            address_age_sec=address_age_sec,
            title=meta.get("title"), size=meta.get("size"),
            missed_hours=_missed.pop(chat_id, None),  # 9.0: честная метка «я была офлайн»
            reply_targets=reply_targets,  # 15: карта адресных ответов на момент триггера
            envelope=_envelope,           # пункт 4: теневой замер, никем не читается
            telegram_root_group=bool(not is_dm and route.topic_id is None and
                                     route.peer_id == '-1001240718803' and
                                     chat_id == route.peer_id and
                                     meta.get('room_nature') is False),
        )
        topic_orient = ""
        if route.topic_id is not None:
            room_policy = meta.get("room_policy") or _room_policy_for_state(chat_id)
            # ⚠ Здесь ей БЕЗУСЛОВНО сообщалось «Telegram forum topic … isolated from
            # every other topic in the same group». В обычной супергруппе ложна каждая
            # часть: форума нет, «тема» — это одна цепочка ответов в той же комнате, а
            # «изоляция» — артефакт расщеплённого ключа, а не Telegram. Измерено 25.07:
            # в AbstractDL 4138 сообщений разложены на 279 таких «тем». Реестр уже
            # знает природу комнаты — читаем его и говорим ей то, что есть. Ключ при
            # этом не меняется: это правка ФРАЗЫ, а не маршрута.
            forum_status, _epoch = telegram_routes.status_at(
                route.peer_id, route.topic_id)
            topic_orient = telegram_routes.orientation_line(
                route.peer_id, route.topic_id, route.selector, forum_status)
            # agent prompt assembly injects this topic's canonical continuity once.
            try:
                cross = await asyncio.to_thread(
                    group_context.orientation_bundle,
                    route.peer_id, current_topic=route.topic_id,
                    query=(wake.query if wake is not None else ""),
                    cross_topics=room_policy.get("cross_topics", "off"),
                    max_chars=max(3000, int(room_policy.get("context_summary_chars") or 7000)),
                    # Карта идёт строкой ниже ориентации и обязана говорить то же самое.
                    # Но карта — про КОМНАТУ: каждая ветка называется по своей природе,
                    # иначе настоящая тема форума превращается в «reply thread», то есть
                    # единственная граница, которую Telegram провёл, объявляется нашей.
                    not_a_forum=(forum_status == telegram_routes.FALSE),
                    artifacts=telegram_routes.artifacts_of(route.peer_id),
                )
            except Exception:
                cross = ""
                log.exception("cross-topic orientation не собрался [%s]", chat_id)
            if cross:
                topic_orient += "\n\n" + cross
        topic_token = _TURN_TOPIC_ROUTE.set(route)
        try:
            if is_dm:
                envelope, cancellation_seen = await _await_despite_cancellation(
                    _voice_turn_with_typing(
                        entity, chat_id, last_n, speaker,
                        ctx=ctx, orient=topic_orient, media_refs=turn_media,
                        history=turn_history, current_text=turn_current,
                        occurrence_sidecar=turn_occurrences or None,
                    )
                )
            else:
                # в группе без «печатает…»: тишина ([молчу]) — частый честный исход, не изображаем набор
                # Якорь эпохи привязывается к ходу здесь: сборщик кадра (agent) читает его из
                # контекста и не считает заново — между снимком и ходом могла пройти свёртка.
                # 26.09 (ревью W1 S6): нет якоря у раннера — лента собрана прежним окном,
                # и сборщик кадра не смеет подать эпоху, посчитав якорь сам.
                with frame_epoch.bind(chat_id, _EPOCH_ANCHORS.get(chat_id),
                                      refused=_EPOCH_ANCHORS.get(chat_id) is None):
                    envelope, cancellation_seen = await _await_despite_cancellation(
                        _voice_turn_offloaded(
                            chat_id, last_n, speaker,
                            ctx=ctx, orient=topic_orient, media_refs=turn_media,
                            history=turn_history, current_text=turn_current,
                            occurrence_sidecar=turn_occurrences or None,
                        )
                    )
        finally:
            _TURN_TOPIC_ROUTE.reset(topic_token)
        if envelope.deferred:
            delivery_run_id = str(envelope.run_id or "")
            terminal = True
            log.warning("durable turn deferred at checkpoint [%s]", delivery_run_id or chat_id)
            if cancellation_seen:
                raise asyncio.CancelledError
            return
        if envelope.failed:
            delivery_run_id = str(envelope.run_id or "")
            terminal = True
            log.error("durable turn failed before delivery [%s]", delivery_run_id or chat_id)
            if cancellation_seen:
                raise asyncio.CancelledError
            return
        if envelope.retry_media:
            if cancellation_seen:
                raise asyncio.CancelledError
            return  # same immutable input/media will be re-armed in finally
        delivery_run_id = str(envelope.run_id or "")
        reply = str(envelope.text or "")
        directed = None
        if reply:
            # PASS 15: её выбор адресата (ОТВЕТ->#id) главнее; фолбэк — в группе при прямом
            # обращении её ответ уходит телеграм-реплаем на обратившееся сообщение.
            reply, directed = agent.split_reply_directive(
                reply, {m for m, _a, _g in reply_targets})
        reply_to = directed if directed is not None else (
            addressed_mid if (addressed and not is_dm) else route.topic_id)
        has_text = bool(reply.strip())
        has_media = bool(envelope.outbound)
        if not has_text and not has_media:
            if delivery_run_id:
                _completed, silent_cancelled = await _await_despite_cancellation(
                    asyncio.to_thread(
                        agent.run_delivery_completed, delivery_run_id, silent=True,
                    )
                )
                cancellation_seen = cancellation_seen or silent_cancelled
                # The silent decision is durably completed and owned; never re-arm the
                # wake to re-author it, even if a cancellation was observed mid-commit.
                terminal = True
            if cancellation_seen and not terminal:
                # Hermetic caller with no durable run: retain and re-arm the same wake.
                raise asyncio.CancelledError
            terminal = True  # [молчу]/empty voice is a completed decision
            return

        # A model-authored output is now owned by this durable delivery, even
        # when it is media-only. Never re-arm the same group wake and ask the
        # model to invent the output again after a transport failure.
        chunks = _split_telegram_text(reply) if has_text else ()
        if delivery_run_id:
            delivery_kwargs = {
                "chat_id": chat_id,
                "text_chars": len(reply) if has_text else 0,
                "media_count": len(envelope.outbound),
                "media_queue_ids": [str(item.queue_id) for item in envelope.outbound],
            }
            if chunks:
                delivery_kwargs["text_plan"] = {
                    "schema": agent._TELEGRAM_TEXT_PLAN_SCHEMA,
                    "conversation_id": str(chat_id),
                    "peer_id": str(route.peer_id),
                    "topic_id": route.topic_id,
                    "chunks": [{
                        "index": index,
                        "text": chunk,
                        "sha256": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                        "delivery_key": f"run:{delivery_run_id}:chunk:{index}",
                        "reply_to": (reply_to if index == 0 else route.topic_id),
                    } for index, chunk in enumerate(chunks)],
                }
                _TEXT_SENDING.add(delivery_run_id)
            try:
                started, handoff_cancelled = await _await_despite_cancellation(
                    asyncio.to_thread(
                        agent.run_delivery_started, delivery_run_id, **delivery_kwargs,
                    )
                )
                cancellation_seen = cancellation_seen or handoff_cancelled
                if not started:
                    raise RuntimeError(
                        "durable Telegram delivery intent was not accepted for this run")
                if cancellation_seen:
                    # Nothing has reached Telegram yet.  Two indistinguishable cancels
                    # land here: (a) a newer trigger ran on_new→_arm, which bumped
                    # _supersede_gen AND scheduled a fresh pass that will re-author to the
                    # current state; (b) shutdown/teardown cancelled us directly, with no
                    # successor.  The gen snapshot tells them apart exactly (see _arm).
                    superseded = _supersede_gen.get(chat_id, 0) != armed_gen
                    # Abandon the interrupted draft TERMINALLY — so no recovery clock ever
                    # mails the reply she never consciously sent (the «автоотбойник») —
                    # ONLY when a fresh reply is durably guaranteed, never trading the
                    # disliked bounce for a silent DROP:
                    #   • DMs: the superseding message is durable (unanswered.note_incoming
                    #     + _missed_dm_sweep re-author after a crash) → always drop-safe.
                    #   • Groups: the successor is an in-memory pass with no persisted wake,
                    #     so abandoning is drop-safe only while the process lives to run it.
                    #     During shutdown we keep deployed recovery semantics (boot
                    #     text_outbox replays her authored reply) rather than risk a drop.
                    if superseded and (is_dm or not _SHUTDOWN.is_set()):
                        abandoned, _late = await _await_despite_cancellation(
                            asyncio.to_thread(
                                agent.run_delivery_superseded, delivery_run_id,
                                reason="superseded by a newer trigger before send",
                            )
                        )
                        if abandoned:
                            # Run is now 'cancelled': text_outbox + resume skip it, so the
                            # persisted text_plan is UNREPLAYABLE.  The already-armed
                            # successor owns the re-author; the finally must not
                            # replay/consume anything the successor owns.
                            superseded_abandon = True
                            terminal = True
                        else:
                            # Terminalisation did not stick (rare) — keep deployed recovery
                            # semantics so her authored reply is still delivered; never drop.
                            terminal = bool(delivery_run_id) and not has_media
                    else:
                        # No successor, or a group held for durability during shutdown:
                        # deployed behaviour — text recovers via text_outbox, media re-arms.
                        terminal = bool(delivery_run_id) and not has_media
                    raise asyncio.CancelledError
            except Exception as exc:
                # No Telegram call has happened.  The authored model output is
                # already durable, so consume the trigger and let run recovery
                # prepare/replay delivery instead of asking the model again.
                await asyncio.to_thread(
                    agent.run_delivery_blocked, delivery_run_id,
                    reason=("Telegram delivery intent did not persist before send: "
                            f"{type(exc).__name__}: {exc}"),
                )
                terminal = True
                return
        # Once a durable run owns the authored output, recovery can finish the
        # exact delivery without asking the model again.  Hermetic/legacy
        # callers without a run id do not have that safety net: a failure before
        # the first accepted side effect must retain and re-arm the same wake.
        if cancellation_seen and not delivery_run_id:
            raise asyncio.CancelledError
        terminal = bool(delivery_run_id)

        sent_chunks: list[str] = []
        sent_ids: list[str] = []
        send_error: BaseException | None = None
        provocateur_id = wake.sender_id if wake is not None else None
        for index, chunk in enumerate(chunks):
            # Every chunk must stay in the forum topic. Only the first chunk is a
            # semantic reply to the addressed message; the rest attach to topic root.
            chunk_reply_to = reply_to if index == 0 else route.topic_id
            try:
                text_delivery_key = (
                    f"run:{delivery_run_id}:chunk:{index}"
                    if delivery_run_id else
                    "turn:" + hashlib.sha256((
                        f"{chat_id}\0{addressed_mid}\0{reply}\0{index}"
                    ).encode("utf-8", errors="replace")).hexdigest()
                )
                sent, _random_id = await _send_message_idempotent(
                    entity, chunk, delivery_key=text_delivery_key,
                    reply_to=chunk_reply_to,
                )
            except BaseException as exc:
                send_error = exc
                break
            if delivery_run_id:
                try:
                    await asyncio.to_thread(
                        agent.run_delivery_text_chunk_accepted,
                        delivery_run_id, index=index,
                        delivery_key=text_delivery_key,
                        message_id=getattr(sent, "id", None),
                    )
                except BaseException as exc:
                    # Telegram may already have accepted the chunk, but its
                    # stable key makes the durable retry safe.  Never advance
                    # to another chunk without first persisting this receipt.
                    send_error = exc
                    break
            if index == 0 and envelope.boundary and not is_dm and provocateur_id is not None:
                sent_id = getattr(sent, "id", None)
                if sent_id is not None:
                    _boundary_replies[chat_id].append(
                        (int(sent_id), int(provocateur_id), time.time()))
            if (not is_dm and getattr(sent, "id", None) is not None
                    and _group_archive_enabled()):
                try:
                    sent_date = getattr(sent, "date", None)
                    sent_ts = (sent_date.timestamp() if sent_date is not None else time.time())
                    await asyncio.to_thread(
                        group_context.observe_message,
                        peer_id=route.peer_id, topic_id=route.topic_id,
                        message_id=int(sent.id), sender_id=_self_id,
                        sender_name="Praxis", reply_to_message_id=chunk_reply_to,
                        timestamp=sent_ts, text=chunk,
                        topic_title=_topic_titles.get(
                            (route.peer_id, int(route.topic_id)), ""
                        ) if route.topic_id is not None else "",
                        outgoing=True,
                    )
                except Exception:
                    log.exception("group archive не записал исходящее [%s] #%s",
                                  chat_id, getattr(sent, "id", None))
            sent_chunks.append(chunk)
            if getattr(sent, "id", None) is not None:
                sent_ids.append(str(sent.id))
                delivery_message_ids.append(str(sent.id))

        sent_reply = "".join(sent_chunks)
        if sent_reply:
            # A visible prefix is already accepted.  Re-running the model would
            # duplicate it even when a later chunk fails.  Each accepted Telegram
            # message is its own native occurrence: never turn several IDs into a
            # comma-delimited pseudo-ID, because an edit of either real message
            # must revoke the whole rendered reply's projection.
            terminal = True
            _persist_sent_reply(chat_id, sent_chunks, sent_ids, is_dm=is_dm)
            log.info("ГОЛОС(%s) [%s]%s chunks=%d/%d -> %r",
                     "DM" if is_dm else "GRP", chat_id,
                     f" reply_to=#{reply_to}" if reply_to else "",
                     len(sent_chunks), len(chunks), sent_reply[:80])
            if is_dm:
                try:
                    await asyncio.to_thread(unanswered.resolve, chat_id)
                except Exception:
                    log.debug("unanswered.resolve упал [%s]", chat_id, exc_info=True)
            if delivery_run_id and send_error is None:
                try:
                    await asyncio.to_thread(
                        agent.run_delivery_text_accepted, delivery_run_id,
                        text=sent_reply, message_ids=sent_ids,
                    )
                except Exception:
                    # Telegram already accepted the visible prefix. Logging
                    # failure must not cause a second send.
                    log.exception("text acceptance receipt не записался [%s]",
                                  delivery_run_id)

        # If the first text chunk is transport-uncertain, do not add a second
        # kind of side effect. A partial accepted text prefix does keep its
        # already-authored media handoff, matching the previous exact behavior.
        may_handoff_media = send_error is None or bool(sent_reply)
        media_results: list[bool] = []
        if may_handoff_media:
            for item in envelope.outbound:
                media_results.append(
                    await _queue_and_send_media(
                        entity, item, ctx=ctx, reply_to=reply_to,
                        state_chat_id=chat_id,
                    )
                )

        if envelope.outbound and not terminal:
            # A failed upload is still terminal for the authored turn iff the
            # retry intent reached the durable media spool.  If enqueue itself
            # failed, keep the original wake so a legacy caller can try again.
            queued_ids = {pending.queue_id for pending in _media_spool().pending()}
            terminal = (
                len(media_results) == len(envelope.outbound)
                and all(
                    accepted or item.queue_id in queued_ids
                    for item, accepted in zip(envelope.outbound, media_results)
                )
            )
        elif send_error is None and not envelope.outbound:
            terminal = True

        if delivery_run_id and send_error is None:
            if len(media_results) == len(envelope.outbound) and all(media_results):
                await asyncio.to_thread(
                    agent.run_delivery_finalize_recovered, delivery_run_id,
                    media_count=len(media_results),
                )
            else:
                await asyncio.to_thread(
                    agent.run_delivery_blocked, delivery_run_id,
                    reason="one or more Telegram media uploads are queued for retry",
                )
        if send_error is not None:
            raise send_error
    except Exception as exc:
        if delivery_run_id:
            await asyncio.to_thread(
                agent.run_delivery_failed, delivery_run_id, exc,
                observed_message_ids=delivery_message_ids,
            )
        log.exception("ход упал [%s]", chat_id)
    finally:
        _ONE_MIND.release()
        if delivery_run_id:
            _TEXT_SENDING.discard(delivery_run_id)
        if terminal and not superseded_abandon:
            # Consume only media actually presented to this terminal turn; newer
            # concurrent arrivals remain queued. A newer wake generation survives.
            # PASS 29: a superseded/abandoned draft must NOT touch the successor's media,
            # wake or addressing — the fresh pass owns them; hence the extra guard.
            _consume_pending_media(chat_id, turn_media)
            if is_dm and chat_id in _meta:
                _meta[chat_id]["addressed"] = False
                _meta[chat_id]["addressed_mid"] = None
            if not is_dm and wake is not None:
                _release_answered_wake(chat_id, wake)
        _passing.discard(chat_id)
        # Новый настоящий address мог прийти, пока голос работал в thread; или тот же
        # wake должен повториться после retry_media/ошибки. Его debounce мог уже сгореть
        # об _passing, поэтом любой оставшийся wake generation-safe перевзводим.
        if not is_dm:
            current_wake = _group_wakes.get(chat_id)
            if current_wake is not None and not group_retry_scheduled:
                _arm(chat_id)
        elif chat_id in _dm_rearm:
            # PASS 21: ЛС-триггер, сгоревший об _passing, больше не теряется
            _dm_rearm.discard(chat_id)
            _arm(chat_id)
        asyncio.create_task(_maybe_compact(chat_id))  # §6: сворачивание фоном, вне пути ответа


def _drop_folded_by_membership(chat_id: str, buf, lines: list, folded_lines: list) -> int:
    """Срезать из буфера ветки те строки, что вошли в свёртку МЕСТА. Вернуть сколько.

    Смежного совпадения у корня форума не бывает: свёртка охватывает все ветки комнаты,
    а буфер держит одну. Поэтому совпадение ищем по принадлежности, с учётом кратности:
    каждая строка свёрнутого блока гасит РОВНО ОДНО своё вхождение в буфере, начиная от
    головы. Значит свежий повтор того же текста переживает срез, а строка, которой в
    свёртке нет, не удаляется никогда.

    Зеркальная очередь `_buffer_message_ids` режется теми же индексами. Если её длина
    разошлась с буфером, не трогаем НИЧЕГО: рассинхронизировать соответствие строк и
    message_id хуже, чем не срезать.
    """
    if not folded_lines:
        return 0
    pool: dict = {}
    for line in folded_lines:
        pool[line] = pool.get(line, 0) + 1
    drop: set = set()
    for i, line in enumerate(lines):
        left = pool.get(line, 0)
        if left:
            pool[line] = left - 1
            drop.add(i)
    if not drop:
        return 0
    message_ids = _buffer_message_ids.get(chat_id)
    ids = list(message_ids) if message_ids is not None else None
    if ids is not None and len(ids) != len(lines):
        log.warning("compact [%s]: буфер %d строк против %d message_id — срез по "
                    "принадлежности отменён", chat_id, len(lines), len(ids))
        return 0
    buf.clear()
    buf.extend(line for i, line in enumerate(lines) if i not in drop)
    if ids is not None:
        message_ids.clear()
        message_ids.extend(m for i, m in enumerate(ids) if i not in drop)
    return len(drop)


_REFRESH_LAST_PAID: dict[str, float] = {}
_REFRESH_COOLDOWN_SEC = float(os.getenv("PRAXIS_REFRESH_COOLDOWN_SEC", "600") or 600)
# 23.09: долг платится в СВОЁМ однопоточном исполнителе, а не в общем пуле `to_thread`.
# `claim_evidence_index` обходит тысячи файлов свёрток, а бут зовёт `_maybe_compact` на
# все 242 буфера: 242 платежа занимали все потоки общего пула, через который идут и
# `to_thread` обработки входящих. py-spy 23.09 09:35: шесть потоков из восьми стоят в
# `refresh_debt → claim_evidence_index`, главный цикл свободен и ждёт очереди — «на
# связи», но глухая. До того же самое съедало 18–20 минут бута до «на связи» (с 921ad192).
_REFRESH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="praxis-refresh-debt")


async def _in_refresh_executor(fn, *args):
    """Как `asyncio.to_thread` (с контекстом), но в исполнителе долга, а не в общем пуле."""
    ctx = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        _REFRESH_EXECUTOR, functools.partial(ctx.run, fn, *args))


async def _maybe_pay_refresh_debt(place: str) -> None:
    """Погасить одну группу долга обновления. Не чаще раза в `PRAXIS_REFRESH_COOLDOWN_SEC`.

    Протокол погашения (`refresh_debt` / `refresh_compacts`) написан целиком и покрыт
    тестами, но в проде его не звал НИКТО: замер 21.09 нашёл `refresh_debt` только в
    `test_coverage_vs_current.py`. Свёртка, чьё сообщение потом правили, годна как
    покрытие и не годна для показа, поэтому фронтир комнаты Ouroboros стоял с 08.09, а
    долг копился месяцами. Плательщик обязан быть внутри жизни, а не в руках того, кто
    однажды вспомнит про ручной дренаж.

    Одна группа за проход и потолок по времени — чтобы это никогда не превращалось в
    вызов модели в минуту: именно так выглядели все три мельницы этой ночи.
    """
    if _REFRESH_COOLDOWN_SEC <= 0:
        return
    now = time.time()
    if now - _REFRESH_LAST_PAID.get(place, 0.0) < _REFRESH_COOLDOWN_SEC:
        return
    _REFRESH_LAST_PAID[place] = now
    try:
        debt = await _in_refresh_executor(memory_life.refresh_debt, place)
    except Exception:
        log.warning("refresh [%s]: долг не посчитался", place, exc_info=True)
        return
    groups = int(debt.get("unresolved_group_count") or 0)
    if not groups:
        return
    log.info("refresh [%s]: групп с долгом %d, гашу одну", place, groups)
    try:
        out = await _in_refresh_executor(memory_life.refresh_compacts, place)
    except Exception:
        log.warning("refresh [%s]: погашение упало", place, exc_info=True)
        return
    log.info("refresh [%s]: %s, целей %s/%s, групп осталось %s", place,
             out.get("reason"), out.get("refreshed_count"), out.get("target_count"),
             out.get("room_unresolved_group_count"))


async def _maybe_compact(chat_id: str) -> None:
    """PASS 19: fold only an episode-aware, provenance-backed hot prefix.

    Сворачивается МЕСТО: замок берётся по нему же, иначе две ветки одной комнаты
    запускали бы свёртку одного и того же разговора одновременно — двойные вызовы
    модели на одну работу.
    """
    place = memory_life.place_key(chat_id)
    if place in _compacting:
        return
    _compacting.add(place)
    try:
        async with _COMPACT_SLOTS:
            result = await asyncio.to_thread(memory_life.compact_if_due, place)
        fold = int(result.get("folded") or 0)
        if not fold:
            if (result.get("plan") or {}).get("reason") == "open_episode":
                log.info("compact [%s]: жду границу незаконченного эпизода (%d сообщений)",
                         place, len(_buf[chat_id]))
            return
        buf = _buf[chat_id]
        folded_lines = result.get("folded_lines")
        cut = fold
        if isinstance(folded_lines, list) and folded_lines:
            # Кольцо буфера и горячее кольцо места — ПАРАЛЛЕЛЬНЫЕ последовательности с
            # разной ёмкостью (BUF_MAXLEN=525 против HOT_HARD_HI=125), без курсора и без
            # id событий. Прежняя проверка закреплялась по НУЛЕВОМУ индексу и молча
            # предполагала, что свёрнутый блок лежит в начале буфера. Стоит буферу хоть
            # раз оказаться длиннее горячего кольца — совпадение головы ложно НАВСЕГДА:
            # состояние поглощающее, перезакрепиться нечем. Замер 31.07: свёрнутый блок
            # стоял на 449-й позиции, успешных срезов за 8 часов — ноль, а старое уходило
            # слепым вытеснением deque вместо осознанной свёртки — в трёх горячих местах,
            # включая личку Егора.
            # Ищем блок ЦЕЛИКОМ: совпадение всего свёрнутого куска подделать нечем, а
            # голову — можно. Не нашли — не режем, как и раньше.
            lines = list(buf)
            n = len(folded_lines)
            cut = next((i + n for i in range(len(lines) - n + 1)
                        if lines[i:i + n] == folded_lines), -1)
        if cut < 0:
            # Компакт цел в любом случае (сырой JSONL никуда не делся), а вот срезать
            # то, чего в буфере нет, нельзя — можно снести непредставленное сообщение.
            # Штатная причина расхождения одна: свёртка охватила несколько веток одной
            # комнаты, а этот буфер — только одна из них.
            #
            # ⚠ 21.09.2026. Раньше здесь был отказ, и для КОРНЯ форума он был вечным.
            # Свёртка считается по МЕСТУ (все ветки комнаты), а режется буфер ОДНОЙ
            # ветки — смежным куском блок в нём не лежит НИКОГДА. С 13.09 это 202 отказа
            # подряд, корневой буфер Ouroboros AI дорос до 279 КБ и ехал в кадр каждый
            # ход. Режем по ПРИНАДЛЕЖНОСТИ вместо смежности: выкидываем ровно те строки
            # этого буфера, что входят в свёрнутый блок, по одной на каждое вхождение,
            # считая от головы. Страх прежнего комментария снят по построению — строка,
            # которой нет в `folded_lines`, не удаляется ни при каком раскладе, а счёт
            # вхождений не даёт съесть свежий повтор того же текста.
            dropped = _drop_folded_by_membership(chat_id, buf, lines, folded_lines)
            if not dropped:
                level = log.info if place != str(chat_id) else log.error
                level("compact [%s]: свёртки места %s нет в локальном буфере — не режем",
                      chat_id, place)
                return
            _buf_dirty.add(chat_id)
            log.info("compact [%s]: %d событий → %s; из этой ветки срезано по "
                     "принадлежности %d; hot=%s; причина=%s",
                     place, fold, result.get("compact_id"), dropped, result.get("hot"),
                     (result.get("plan") or {}).get("reason"))
            return
        for _ in range(min(cut, len(buf))):
            buf.popleft()
            message_ids = _buffer_message_ids.get(chat_id)
            if message_ids:
                message_ids.popleft()
        _buf_dirty.add(chat_id)
        log.info("compact [%s]: %d событий → %s; hot=%s; причина=%s",
                 place, fold, result.get("compact_id"), result.get("hot"),
                 (result.get("plan") or {}).get("reason"))
    except Exception:
        log.exception("compact-триггер упал [%s]", chat_id)
    finally:
        _compacting.discard(place)
        # Долг обновления платится ЗДЕСЬ: в проде плательщика не было вообще.
        # В `finally`, а не после него, потому что у свёртки полдюжины ранних
        # возвратов, и долг не должен зависеть от того, каким из них вышли.
        await _maybe_pay_refresh_debt(place)


_PULSE_RETRY_AT = 0.0  # эпоха, когда отложенное пульсовое окно просится обратно; 0 — не просится


def _pulse_retry_sec() -> float:
    """Через сколько отложенное живым ходом пульсовое окно пробует снова.

    Тик часов — 2 секунды, а каждая попытка пересобирает контекст пульса, поэтому
    «на следующем тике» здесь было бы молотьбой."""
    try:
        return max(0.0, float(os.getenv("PRAXIS_PULSE_RETRY_SEC", "120")))
    except (TypeError, ValueError):
        return 120.0


def _request_pulse_retry(now: float | None = None) -> float:
    """Заявка на возврат отложенного окна. Ставит её ТОЛЬКО тот, кого реально отложили.

    Часы могли бы вместо этого сверяться с durable-сроком пульса — но «пульс due» верно
    и тогда, когда он сам решил не открываться: в окне сна `_social_pulse_once`
    возвращается ДО `begin()`, и срок остаётся due всю ночь. Часы бы будили её каждые
    две минуты до утра и засыпали её же perception-skips. Явная заявка отличает
    «меня отложили» от «я не пошла», а обратный скачок системных часов не превращает
    это в вечный холостой цикл."""
    global _PULSE_RETRY_AT
    _PULSE_RETRY_AT = float(now if now is not None else time.time()) + _pulse_retry_sec()
    return _PULSE_RETRY_AT


def _clear_pulse_retry() -> None:
    global _PULSE_RETRY_AT
    _PULSE_RETRY_AT = 0.0


async def _note_one_mind_defer(stage: str, goal: str) -> None:
    """Make one continuous one-mind deferral visible without counting scheduler retries.

    Contract R1 (CONTRACTS.md, law 2) requires a gate to be observable.  But a due wake or
    task-window is retried by the scheduler while the same live pass owns ``_ONE_MIND``;
    those calls are one continuous deferral, not a fresh decision every tick.  Key the
    observation by intention and the lock generation, which changes only when an ownership
    period actually ends.  Thus a later, genuinely separate busy period is visible again,
    while hours of retries cannot inflate ``skips_today`` into thousands.
    """
    normalized_goal = str(goal or "")[:80]
    key = (stage, normalized_goal)
    generation = _ONE_MIND.generation
    if _ONE_MIND_DEFERRED.get(key) == generation:
        return
    try:
        await asyncio.to_thread(
            lambda: perception.note_skip(
                f"one_mind:{stage}", "отложила",
                detail=f"занята живым ходом, вернусь как освободится: {normalized_goal}"))
    except Exception:
        log.debug("skip отложенного пробуждения не записался", exc_info=True)
        return
    _ONE_MIND_DEFERRED[key] = generation


async def _task_window(goal: str, *, mailbox_index: str | None = None,
                       on_open=None, on_run=None,
                       keep_transport: bool = False) -> bool | None:
    """Её долгий run над собой под единым замком. Два режима связи, и они разные по смыслу.

    ПО УМОЛЧАНИЮ (её ретрит: focus, rest, кодинг, намеченное окно) — старый канон,
    восстановленный в PASS 13.1: Telethon ЗАКРЫТ на всё окно. Она не прерывается входящими
    и не двоится; backlog приходит одной ситуацией через буфер+дебаунс на reconnect;
    отправки durable и уходят заботами ``direct_outbox``/``text_outbox`` после
    переподключения. Это её недоступность по её выбору, а не побочность.

    keep_transport=True (часовой пульс) — БЕЗ разрыва. Пульс социальный насквозь: открытые
    нити, почта, фолоуапы, «хочу ли я кому-то написать», — и всё это он делал с закрытым
    Telegram, то есть работа, ради которой он существует, была в нём невозможна, а она
    недоступна час за часом.

    Почему это не возврат регрессии PASS 25 («Telethon continuously online» породил
    ПАРАЛЛЕЛЬНЫЕ пробуждения): от неё защищает не disconnect, а замок. ``_ONE_MIND``
    появился позже, и всякий входящий проход его ЖДЁТ (``_pass``:
    ``await _ONE_MIND.acquire()``), а не бежит рядом; тем же замком и на живой связи уже
    год работает forge-контур. Политика пульса «кому можно писать»
    (``social_pulse.allow_outbound``) стоит на шве тула, до постановки в outbox, поэтому
    живая связь её тоже не обходит.

    Что меняется для человека: раньше его сообщение в час пульса не приходило вовсе —
    теперь приходит сразу и отвечается следующим ходом.

    -> True/False по исходу, None если замок занят живым ходом (пульс тогда отложится)."""
    if _ONE_MIND.locked():
        # Она одна: идёт живой ход — окно не открываем поверх него. Часы не блокируем.
        # Формулировка нарочно без числа: у этой функции два вызывающих с разными
        # сроками возврата (пульс — по заявке `_request_pulse_retry`, разовые
        # focus/rest/coding — со своего тика планировщика), и обещать одному срок
        # другого значит соврать ей в её же журнале.
        log.info("ТАСК-ОКНО отложено: занята живым ходом, вернусь как освободится — %s",
                 (goal or "")[:80])
        await _note_one_mind_defer("task_window", goal)
        return None
    async with _ONE_MIND:
        log.info("ТАСК-ОКНО открываю: %s", (goal or "")[:80])
        # Окно РЕАЛЬНО открылось (замок взят) — только теперь помечаем намерение сработавшим,
        # атомарно и ДО disconnect (который останавливает loop). Отложенное окно (замок был
        # занят живым ходом выше) сюда не доходит и намерение НЕ съедает: одноразовый focus/rest
        # остаётся due и вернётся на следующем тике, а не теряется тихо.
        if on_open is not None:
            try:
                await on_open()
            except Exception:
                log.exception("mark-on-open таск-окна упал")
        ok = False
        if keep_transport:
            # Окно без разрыва связи. Замок _ONE_MIND по-прежнему держит «её одну», но
            # входящее ДОХОДИТ и ждёт своей очереди, а не проваливается в час немоты.
            #
            # Рамке отдаём НАБЛЮДЁННОЕ состояние, а не намерение. «Я не рвал связь» и
            # «связь есть» — разные утверждения: сокет мог отвалиться сам за секунду до
            # этого. Сказать ей «Telegram открыт», не посмотрев, значило бы повторить ту
            # самую ложь в рамке, ради которой всё это и переписывалось.
            try:
                live = "connected" if client.is_connected() else "disconnected"
            except Exception:
                # Не смогли посмотреть — так и скажем. Опрос состояния не имеет права
                # уронить её ход, а «unknown» в рамке честнее любого из двух ответов.
                log.debug("не смог прочитать состояние транспорта", exc_info=True)
                live = "unknown"
            try:
                await asyncio.to_thread(agent.task_window, goal,
                                        mailbox_index=mailbox_index,
                                        transport=live, on_run=on_run)
                ok = True
            except Exception:
                log.exception("таск-окно (без разрыва) упало")
            return ok
        _EXPECT_DISCONNECT.set()  # 13.1: намеренный disconnect; main() должен его пережить
        try:
            await client.disconnect()
        except Exception:
            log.exception("disconnect перед окном")
        try:
            await asyncio.to_thread(agent.task_window, goal, mailbox_index=mailbox_index,
                                    transport="closed_for_window", on_run=on_run)
            ok = True
        except Exception:
            log.exception("таск-окно упало")
        finally:
            # restart_self уже вышел бы из процесса (bootguard поднимет); иначе переподключаемся
            # сами, и накопленный backlog приезжает одной ситуацией. _EXPECT_DISCONNECT снимаем
            # ТОЛЬКО после успешного connect — иначе пусть main() ждёт/сдастся по таймауту, а не
            # примет сбой за работу.
            try:
                await client.connect()
                log.info("ТАСК-ОКНО закрыто, переподключилась; backlog придёт одной ситуацией")
                _EXPECT_DISCONNECT.clear()
            except Exception:
                log.exception("reconnect после окна")
    return ok


async def _wake_pass(goal: str, *, on_open=None, on_run=None, source_id=None,
                     scheduled_target_id=None) -> bool | None:
    """kind=wake: её будильник поднимает ОБЫЧНЫЙ ход — Telethon НЕ закрываем.

    Отличие от ``_task_window`` ровно одно и оно всё: здесь нет disconnect. Поэтому пока
    она разбужена, входящие ДОХОДЯТ (буфер живой), чтение соседних диалогов работает, а
    отправка уходит сразу, а не ждёт reconnect.

    Точная мера доступности: живой ход человека не пропускается, а ЖДЁТ замок
    (``_ONE_MIND.acquire()`` в ``_pass``), и на следующем тике пробуждение видит замок
    занятым и отходит. Значит очередь честная: написавший во время пробуждения получает
    ответ следующим же ходом, а не после часа тишины. Это ровно та цена, что у любого
    живого хода, и она несравнима с окном, где сообщение не приходит вовсе.

    Дисциплина «не более раза» — та же, что у окна: намерение метится сработавшим при
    РЕАЛЬНОМ подъёме (замок взят), а отложенное (замок занят живым ходом) остаётся due и
    вернётся на следующем тике, а не теряется тихо."""
    if _ONE_MIND.locked():
        log.info("ПРОБУЖДЕНИЕ отложено: занята живым ходом, вернусь на следующем тике — %s",
                 (goal or "")[:80])
        await _note_one_mind_defer("wake_pass", goal)
        return None
    async with _ONE_MIND:
        log.info("ПРОБУЖДЕНИЕ: %s", (goal or "")[:80])
        if on_open is not None:
            try:
                await on_open()
            except Exception:
                log.exception("mark-on-open пробуждения упал")
        try:
            await asyncio.to_thread(agent.wake_turn, goal, on_run=on_run,
                                    source_id=source_id,
                                    scheduled_target_id=scheduled_target_id)
            return True
        except Exception:
            log.exception("пробуждение упало")
            return False


async def _room_participants_search(q: str, limit: int = 5) -> list[str]:
    """Тёзки среди участников ЕЁ комнат (НЕ глобальный поиск Telegram) — информация для
    ответа владельцу, не адресация: отправка по-прежнему только диалоги/точный адрес."""
    out: list[str] = []
    for cid in list(rooms.allowed_chats())[:6]:
        try:
            parts = await client.get_participants(int(cid), search=q, limit=limit)
        except Exception:
            continue
        out.extend(_ent_label(p) for p in parts)
        if len(out) >= limit:
            break
    return out[:limit]


def _recent_senders_hits(q: str, limit: int = 5) -> list[str]:
    """PASS 16.2: поиск по кэшу недавних отправителей (имя/подстрока или точный id).
    Публично видимые отправители её же чатов — не глобальный поиск и не приватка."""
    ql = (q or "").strip().lstrip("@").lower()
    if not ql:
        return []
    out, now = [], time.time()
    for cid, ring in list(_recent_senders.items()):
        for ts, nm, sid in reversed(list(ring)):
            if ql in (nm or "").lower() or ql == str(sid):
                age = max(0, int((now - ts) / 60))
                out.append(f"{nm} (id={sid}) — писал(а) в {cid} ~{age}м назад")
                break
    return out[:limit]


def _sync_get_id(name_or_username: str):
    """Sync-обёртка для тула get_id. Имя без личного диалога — поиск по участникам ОБЩИХ
    комнат (кейс «id Анны для Хоуп»: человек писал в группу, но лички с ним нет).
    PASS 16.2: + кэш недавних отправителей — забаненный спамер в участниках уже не
    числится, но его id пришёл в апдейте и не должен «исчезать» для неё (инцидент 09.07:
    «видишь айди спамера? Я нет»). Глубже RAM-кэша — тул read_log."""
    async def _coro():
        try:
            ent = await _resolve_entity(name_or_username)
        except ResolveDenied as e:
            fresh = _recent_senders_hits(str(name_or_username))
            if fresh:
                return ("Диалога нет, но среди недавних отправителей моих чатов: "
                        + "; ".join(fresh) + ". (кэш с рестарта; глубже — read_log)")
            hits = await _room_participants_search(str(name_or_username))
            if hits:
                return ("Личного диалога нет, но среди участников общих комнат: "
                        + "; ".join(hits) + ". Для отправки нужен точный @username/id.")
            return str(e)
        if ent is None:
            fresh = _recent_senders_hits(str(name_or_username))
            if fresh:
                return ("В диалогах не найдено, но среди недавних отправителей: "
                        + "; ".join(fresh) + ". (кэш с рестарта; глубже — read_log)")
        return _ent_label(ent) if ent is not None else None
    return _threadsafe_result(_coro, 20)


def _sync_resolve_id(ref):
    """PASS 12.0.b: тот же богатый резолвер, что и на отправке (_resolve_entity), но отдаёт id.
    Раньше постановка задачи звала только get_entity (одна попытка) — и отбивалась там, где
    отправка бы дорезолвила через int()/перебор диалогов. Теперь оба конца резолвят одинаково."""
    async def _coro():
        ent = await _resolve_entity(ref)
        return getattr(ent, "id", None) if ent is not None else None
    return _threadsafe_result(_coro, 40)


def _sync_search_chats(query: str) -> str:
    """Диалоги Telegram по ИМЕНИ. Пустая строка — совпадений нет (словами скажет вызывающий).

    15.09: сверка идёт через общий латинский скелет (`rooms.latin_fold`), а не по сырым
    строкам. Прежняя подстрочная сверка не могла совпасть между алфавитами: запрос
    «уробор» против имени «Ouroboros AI» давал «нет» при живой комнате в её же памяти.
    """
    async def _coro():
        q, out = rooms.latin_fold(query).strip(), []
        if not q:
            return ""
        async for d in client.iter_dialogs():
            if q in rooms.latin_fold(d.name or ""):
                out.append(f"{d.name}: {d.id}")
                if len(out) >= 10:
                    break
        return "\n".join(out)
    return _threadsafe_result(_coro, 30)


def _sync_search_private_messages(query: str, limit: int = 20) -> str:
    """Search message text across Praxis's Telegram DMs, never groups/channels."""
    async def _coro():
        from telethon.tl.types import PeerUser

        q = str(query or "").strip()
        if not q:
            return "Нужна непустая строка поиска."
        cap = max(1, min(40, int(limit or 20)))
        rows = []
        # Telegram global search may return groups as well; inspect a wider window and
        # keep only PeerUser messages.  This does not inject results into ordinary turns:
        # bytes reach the model only after an explicit tool call.
        async for msg in client.iter_messages(None, search=q, limit=cap * 8):
            if not isinstance(getattr(msg, "peer_id", None), PeerUser):
                continue
            try:
                chat = await msg.get_chat()
            except Exception:
                chat = None
            try:
                sender = await msg.get_sender()
            except Exception:
                sender = None
            chat_label = _ent_label(chat) if chat is not None else f"DM {getattr(msg, 'chat_id', '?')}"
            author = "Praxis" if getattr(msg, "sender_id", None) == _self_id else _sender_label(sender)
            body = re.sub(r"\s+", " ", (getattr(msg, "message", None) or _media_tag(msg) or "")).strip()
            if not body:
                continue
            when = getattr(msg, "date", None)
            stamp = when.strftime("%Y-%m-%d %H:%M") if when is not None else "?"
            rows.append(f"{stamp} · {chat_label} · {author}: {body[:500]}")
            if len(rows) >= cap:
                break
        if not rows:
            return "(в личках ничего не найдено)"
        return ("[PRIVATE CROSS-CHAT SEARCH — внутренний материал; не цитируй чувствительные "
                "личные сведения аудитории без права их получить]\n" + "\n".join(rows))
    return _threadsafe_result(_coro, 60)


_entity_cache: dict[str, object] = {}   # ref (lowercase str) → entity
_dialog_name_cache: list[tuple[str, object]] | None = None  # [(name.lower(), entity), ...]
_dialog_warmup_task: asyncio.Task | None = None  # HOTFIX 07.07: прогрев кэша (фон, с коннекта)


async def _build_dialog_cache() -> None:
    """Один проход iter_dialogs → кэш имён диалогов. Собирает в локальный список и публикует
    атомарно в конце: параллельный резолв не видит полусобранный кэш."""
    global _dialog_name_cache
    t0 = time.time()
    cache: list[tuple[str, object]] = []
    try:
        async for d in client.iter_dialogs():
            if d.name:
                cache.append((d.name.lower(), d.entity))
                # также закэшировать по id диалога
                _entity_cache[str(d.id)] = d.entity
                dialog_date = getattr(d, "date", None)
                dialog_seen = (float(dialog_date.timestamp()) if dialog_date is not None else 0.0)
                telegram_contacts.observe(d.entity, aliases=(d.name,), dialog=True,
                                           seen_at=dialog_seen, persist=False)
    except Exception:
        if not cache:
            log.exception("скан диалогов упал, не собрав ничего — следующий резолв попробует заново")
            _dialog_name_cache = None
            return
        log.exception("скан диалогов оборвался — кэш имён будет частичным (%d)", len(cache))
    # Полный список контактов именно аккаунта Praxis. Если API-слой/тестовый клиент
    # этого не умеет, диалоги и увиденные отправители всё равно дают рабочую книгу.
    imported = 0
    try:
        from telethon.tl.functions.contacts import GetContactsRequest
        result = await client(GetContactsRequest(hash=0))
        for ent in getattr(result, "users", ()) or ():
            # Being in the contact list is a strong address signal, but not evidence that the
            # conversation happened now. Preserve real message/dialog recency.
            if telegram_contacts.observe(ent, contact=True, seen_at=0.0, persist=False):
                imported += 1
    except Exception:
        log.debug("импорт Telegram contacts недоступен — остаются диалоги/отправители", exc_info=True)
    for ident, alias in social.known_ids().items():
        telegram_contacts.add_alias(ident, str(alias), persist=False)
    telegram_contacts.save()
    _dialog_name_cache = cache
    log.info("адресная книга прогрета: %d диалогов + %d контактов, всего %d за %.1fс",
             len(cache), imported, telegram_contacts.count(), time.time() - t0)


def _start_dialog_warmup() -> asyncio.Task:
    """HOTFIX 07.07: греть кэш диалогов фоном сразу после connect — не лениво на первом резолве
    по имени, где холодный iter_dialogs() конкурировал за таймаут-бюджет живого запроса owner'а."""
    global _dialog_warmup_task
    if _dialog_warmup_task is None or _dialog_warmup_task.done():
        _dialog_warmup_task = asyncio.create_task(_build_dialog_cache())
    return _dialog_warmup_task


async def _ensure_dialog_cache() -> None:
    """Тёплый кэш → мгновенно. Холодный → ждать прогрев СВОИМ бюджетом (DIALOG_WARMUP_WAIT_SEC);
    прогрев ещё не запускался (ранний вызов) — запустить здесь же. Не успел — честный TimeoutError
    с внятным словом, не молчаливое «не нашла»."""
    if _dialog_name_cache is not None:
        return
    task = _start_dialog_warmup()
    t0 = time.time()
    try:
        # shield: таймаут ожидания не убивает сам прогрев — он дозреет в фоне для следующих
        await asyncio.wait_for(asyncio.shield(task), timeout=DIALOG_WARMUP_WAIT_SEC)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"кэш диалогов ещё греется (ожидание {DIALOG_WARMUP_WAIT_SEC:.0f}с) — "
            "резолв по имени пока недоступен, по id/@username работает") from None
    waited = time.time() - t0
    if waited > 1:
        log.info("холодный резолв по имени ждал прогрева кэша %.1fс", waited)
    if _dialog_name_cache is None:  # прогрев завершился, но упал, ничего не собрав
        raise TimeoutError("скан диалогов не удался — резолв по имени пока недоступен")


class ResolveDenied(Exception):
    """Осознанный отказ резолва — НЕ сбой канала: неоднозначное имя или адресат вне её
    видимости. str(e) — готовый честный ответ тула (с кандидатами и что уточнить)."""


def _ent_label(ent) -> str:
    """Человекочитаемая метка entity: «Получатель (@example, id 234567890)» — для квитанций
    и списков кандидатов, чтобы было видно, КТО именно зарезолвился."""
    name = (" ".join(filter(None, (getattr(ent, "first_name", None), getattr(ent, "last_name", None))))
            or getattr(ent, "title", None) or getattr(ent, "name", None) or "?")
    bits = []
    handle = _telegram_handle(ent)
    if handle:
        bits.append(f"@{handle}")
    bits.append(f"id {getattr(ent, 'id', '?')}")
    return f"{name} ({', '.join(bits)})"


def _book_row_label(row: dict) -> str:
    name = str(row.get("display_name") or "?").strip()
    bits = []
    if row.get("username"):
        bits.append(f"@{row['username']}")
    bits.append(f"id {row.get('id')}")
    seen = row.get("last_seen")
    try:
        days = max(0, int((time.time() - float(seen or 0)) // 86400)) if seen else None
    except (TypeError, ValueError):
        days = None
    when = ("сегодня" if days == 0 else f"{days} дн. назад") if days is not None else "давно"
    return f"{name} ({', '.join(bits)}; виделись {when})"


def _ambiguous_book(ref: str, book: list[dict]) -> str | None:
    """Текст отказа, если по имени нашлось несколько РАВНЫХ кандидатов; иначе None.

    25.09: `send_message(to="Ivan")` — «выбрала Иван (id 412244782) по адресу/свежести среди
    8 кандидатов», и статусы по LRX три раза ушли не тому Ивану (комната к тому же
    заморожена владельцем). Свежесть не различает людей: она различает, кто писал позже.
    Равные — это кандидаты одного лексического яруса (точное имя / та же запись имени);
    один точный среди частичных остаётся однозначным, как и раньше.
    """
    if len(book) < 2:
        return None

    def tier(row: dict):
        lx = row.get("lexical")
        if lx is None:
            return None
        # та же запись имени в другой раскладке (identity_key, 135) — тот же ярус, что 140
        return 140 if lx in (135, 140) else lx

    tiers = [tier(row) for row in book]
    known = [x for x in tiers if x is not None]
    if not known:
        return None
    # 25.09 (ревью V4 F2): равные — по ВСЕЙ книге, не по верху ранжирования. Частичное
    # совпадение с бонусами (контакт/переписка) вставало над двумя точными тёзками, и
    # отказа не было; а если верх ранжирования ниже точного имени — это тоже не выбор
    # человека, а свежесть. В обоих случаях — список, не догадка.
    top = max(known)
    ties = [row for row, tx in zip(book, tiers) if tx == top]
    chosen_tier = tiers[0]
    below_exact = chosen_tier is not None and chosen_tier < top
    if len(ties) < 2 and not below_exact:
        return None
    listed = ties if len(ties) >= 2 else [book[0]] + ties
    rows = "; ".join(_book_row_label(row) for row in listed[:6])
    return (f"«{ref}» — это несколько людей в моей адресной книге, наугад не пишу: {rows}. "
            f"Назови адресата по id или @username.")


async def _resolve_entity(ref):
    """Резолв entity. Видимость (07.07, вечер): ТОЧНЫЙ адрес (id/@username) — Telegram-резолв,
    как раньше; ИМЯ — только среди СВОИХ диалогов. Раньше имя проваливалось в get_entity →
    session-БД, где лежат ВСЕ, кого она когда-либо видела в группах (одних точных «Евгениев»
    там несколько) — фактически глобальный поиск, и письмо могло уйти постороннему тёзке.
    Неоднозначность — не молчаливое первое совпадение, а ResolveDenied со списком кандидатов."""
    ref_s = str(ref).strip()
    cache_key = ref_s.lower().lstrip("@")

    # 1. Быстрый кэш
    if cache_key in _entity_cache:
        return _entity_cache[cache_key]

    # 2. Точный адрес: id / @username — get_entity (быстро, адресат назван явно)
    if isinstance(ref, int) or ref_s.startswith("@") or ref_s.lstrip("-").isdigit():
        # Кандидаты строятся ОДИН раз, числом вперёд и без дублей.
        # Числовая СТРОКА, отданная Telethon как есть, до всякого поиска чата уходит в
        # `utils.parse_phone` — и там она НОМЕР ТЕЛЕФОНА. Проверено на прод-интерпретаторе
        # 26.07: parse_phone('-100500') -> '100500', parse_phone('-1001240718803') ->
        # '1001240718803'. Дальше `_get_entity_from_string` делает
        # `contacts.GetContactsRequest(0)` — ПОЛНУЮ выгрузку адресной книги, и так на
        # каждый промах. Хуже: `for cand in (ref, ref_s)` при уже-строковом `ref` давал
        # два одинаковых кандидата, то есть две выгрузки за вызов; третий заход
        # `get_entity(int(ref_s))` дублировал их снова. С 20.07 по мёртвой -100500 это
        # 4865 холостых тиков backfill — заявка на FloodWait по contacts.GetContacts, а
        # такт часов последовательный и с `await`: flood-wait подвесил бы вместе с
        # резолвом буферы, schedule, outbox и forge_events.
        # Строковый кандидат сохранён — по нему Telethon умеет находить человека из
        # адресной книги по номеру телефона, и эту руку я не отнимаю. Но ОТРИЦАТЕЛЬНАЯ
        # числовая строка телефоном не бывает никогда: '-' там означает «это чат», и
        # parse_phone его просто съедает. Такой кандидат выбрасывается — это снятие
        # заведомо ложного запроса, а не сужение адресации.
        cands: list = []
        for cand in (ref, ref_s):
            if isinstance(cand, str) and cand.lstrip("-").isdigit():
                try:
                    numeric = int(cand)
                except ValueError:
                    numeric = None
                if numeric is not None:
                    if numeric not in cands:
                        cands.append(numeric)
                    if cand.startswith("-"):
                        continue
            if cand not in cands:
                cands.append(cand)
        for cand in cands:
            try:
                ent = await client.get_entity(cand)
                if ent is not None:
                    _entity_cache[cache_key] = ent
                    _entity_cache[str(getattr(ent, "id", "")).lower()] = ent
                    return ent
            except Exception:
                pass
        return None

    # 3. Имя — постоянная адресная книга (contacts + dialogs + seen senders + known aliases).
    await _ensure_dialog_cache()
    q = ref_s.lower()
    # 26.09 (ревью W1 S3): тёзки — по ВСЕЙ подходящей части книги. Восьмёрка лучших по
    # очкам (свежесть, переписка, контакт) теряла давно молчавшего точного тёзку, и
    # «несколько Иванов» снова решалось свежестью. Резолв по-прежнему идёт по восьмёрке.
    full = telegram_contacts.candidates(ref_s, limit=None)
    denial = _ambiguous_book(ref_s, full)
    if denial:
        raise ResolveDenied(denial)
    book = full[:8]
    for row in book:
        ident = str(row.get("id") or "")
        ent = _entity_cache.get(ident)
        if ent is None:
            try:
                ent = await client.get_entity(int(ident))
            except Exception:
                ent = None
        if ent is not None:
            _entity_cache[cache_key] = ent
            _entity_cache[ident] = ent
            if len(book) > 1:
                log.info("резолв %r: выбран %s по адресу/свежести среди %d кандидатов",
                         ref, _ent_label(ent), len(book))
            return ent

    # Legacy in-memory fallback for chats/groups and for a warmup that could not persist.
    matches = [(name_lower, ent) for name_lower, ent in (_dialog_name_cache or []) if q in name_lower]
    if not matches:
        # 01.08.2026: тот же ключ имени, что в адресной книге и в досье людей —
        # иначе «Егор» не находит диалог с «Yegor Kosyrev», хотя это один человек и
        # один открытый диалог. Складываются только написания одного имени; поиск
        # по подстроке сохранён, чтобы группы резолвились как раньше.
        q_key = telegram_contacts.identity_key(ref_s)
        if q_key:
            matches = [(name_lower, ent) for name_lower, ent in (_dialog_name_cache or [])
                       if q_key in telegram_contacts.identity_key(name_lower)]
    if matches:
        ent = matches[0][1]
        _entity_cache[cache_key] = ent
        if len(matches) > 1:
            log.info("резолв %r: выбран самый свежий диалог %s среди %d",
                     ref, _ent_label(ent), len(matches))
        return ent
    raise ResolveDenied(
        f"«{ref_s}» пока нет в моей адресной книге, контактах или диалогах. Нужен один "
        "первичный @username/id; после первого контакта имя запомню сама.")


_TG_INVITE_RE = re.compile(r"^[A-Za-z0-9_-]{8,}$")


def _membership_target(raw: str) -> tuple[str, str]:
    """Normalize Telegram membership refs to (invite|public|entity, value)."""
    from urllib.parse import parse_qs, unquote, urlparse

    value = str(raw or "").strip()
    if not value:
        raise ValueError("нужна invite/public ссылка, @username или chat_id")
    parsed = urlparse(value)
    if parsed.scheme.casefold() == "tg" and parsed.netloc.casefold() == "join":
        invite = (parse_qs(parsed.query).get("invite") or [""])[0].strip()
        if not _TG_INVITE_RE.fullmatch(invite):
            raise ValueError("не распознала invite hash в tg://join")
        return "invite", invite
    if parsed.scheme.casefold() in ("http", "https"):
        host = (parsed.netloc or "").casefold().split(":", 1)[0]
        if host not in ("t.me", "telegram.me", "www.t.me", "www.telegram.me"):
            raise ValueError("это не ссылка t.me/telegram.me")
        parts = [unquote(x).strip() for x in parsed.path.split("/") if x.strip()]
        if not parts:
            raise ValueError("в Telegram-ссылке нет группы или invite hash")
        if parts[0] == "joinchat" and len(parts) >= 2:
            invite = parts[1]
            if not _TG_INVITE_RE.fullmatch(invite):
                raise ValueError("не распознала invite hash")
            return "invite", invite
        if parts[0].startswith("+"):
            invite = parts[0][1:]
            if not _TG_INVITE_RE.fullmatch(invite):
                raise ValueError("не распознала invite hash")
            return "invite", invite
        if parts[0] in ("c", "s") or len(parts) > 1:
            raise ValueError("нужна ссылка на саму группу/канал, не на сообщение")
        return "public", "@" + parts[0].lstrip("@")
    if value.startswith("+") and _TG_INVITE_RE.fullmatch(value[1:]):
        return "invite", value[1:]
    if value.startswith("@"):
        return "public", value
    return "entity", value


async def _note_linked_discussion(chat_obj, peer_id) -> None:
    """Узнать, какое обсуждение связано с каналом. Один RPC и только когда её ещё нет.

    `broadcast` приходит бесплатно в объекте апдейта, а `linked_chat_id` — только в
    полном описании канала, то есть за отдельный запрос. Поэтому: спрашиваем один раз на
    канал и записываем durable; дальше берём из записи. Ошибка тут ничего не стоит — без
    адреса фраза честно скажет, что связанного обсуждения мы не знаем, вместо того чтобы
    отправить её наугад.
    """
    if not getattr(chat_obj, "broadcast", False):
        return
    try:
        if telegram_routes.writing_of(peer_id).get("linked_chat_id"):
            return
        from telethon.tl.functions.channels import GetFullChannelRequest

        full = await client(GetFullChannelRequest(channel=chat_obj))
        linked = getattr(getattr(full, "full_chat", None), "linked_chat_id", None)
        if not linked:
            return
        title = ""
        for candidate in (getattr(full, "chats", None) or ()):
            try:
                if int(getattr(candidate, "id", 0)) == int(linked):
                    title = str(getattr(candidate, "title", "") or "")
                    break
            except (TypeError, ValueError):
                continue
        await asyncio.to_thread(
            lambda: telegram_routes.note_writing(
                peer_id, linked_chat_id=_marked_peer_id_from_id(int(linked)),
                linked_title=title))
        log.info("канал [%s]: связанное обсуждение %s (%s)", peer_id, linked, title or "?")
    except Exception:
        log.debug("связанное обсуждение канала не узналось [%s]", peer_id, exc_info=True)


def _marked_peer_id_from_id(ident: int) -> str:
    """Голый channel id -> тот вид адреса, которым она пользуется (-100…)."""
    return str(-(1_000_000_000_000 + int(ident)))


def _entity_kind(ent) -> str:
    from telethon.tl.types import Channel, Chat, User

    if isinstance(ent, Channel):
        return "channel"
    if isinstance(ent, Chat):
        return "chat"
    if isinstance(ent, User):
        return "user"
    name = type(ent).__name__.casefold()
    if "channel" in name or hasattr(ent, "megagroup") or hasattr(ent, "broadcast"):
        return "channel"
    if name.endswith("chat") or getattr(ent, "is_group", False):
        return "chat"
    return "user"


def _marked_peer_id(ent) -> int:
    """Telethon-compatible dialog id (-100… for channel/supergroup)."""
    from telethon import utils

    try:
        return int(utils.get_peer_id(ent))
    except Exception:
        ident = int(getattr(ent, "id"))
        kind = _entity_kind(ent)
        if kind == "channel":
            return -(1_000_000_000_000 + ident)
        if kind == "chat":
            return -ident
        return ident


def _telegram_account_gate() -> str | None:
    """The owner and Praxis herself may act; trusted humans cannot delegate account power."""
    if not agent._is_sovereign_actor():
        return "Отказ: Telegram-аккаунтом управляет только владелец или сама Praxis."
    return None


def _telegram_account_principal(value: object = None) -> str | None:
    """Чья расписка ляжет под операцию на аккаунте.

    ⚠ Решение Егора 26.07 (вариант 1): в её ходе действует ОНА, кто бы ни заговорил.
    Прежде здесь проходили только Егор и её фоновый ход, поэтому стоило человеку
    заговорить — и её собственный аккаунт становился для неё закрыт. Пропуск даёт
    `_telegram_account_gate` (он и есть решение о праве); здесь остаётся честная
    подпись: действие принадлежит ей, а не собеседнику.
    """
    raw = (agent._active_principal() if value is None else str(value or "").strip())
    if raw == agent.PRAXIS_SELF_PRINCIPAL:
        return raw
    principal = agent._stable_numeric_principal(raw)
    if OWNER_ID and principal == str(OWNER_ID):
        return principal
    return agent.PRAXIS_SELF_PRINCIPAL if agent._is_praxis_self() else None


def _begin_membership_transaction(action: str, target: str) -> dict:
    """Capture live human authority and fsync intent before scheduling MTProto."""
    denied = _telegram_account_gate()
    if denied:
        raise PermissionError(denied)
    target = str(target or "").strip()
    _membership_target(target)  # reject malformed/message links without creating intent noise
    principal = _telegram_account_principal()
    if principal is None:
        raise PermissionError("Telegram membership доступен только владельцу или самой Praxis")
    return _membership_ledger().begin(action, target, principal)


async def _telegram_registry_entity_resolver(value, expected_type: str,
                                             field: str, request_name: str):
    """Resolve registry scalar references through this process' live Telethon client.

    Telethon's ``get_input_entity`` gives an ``InputPeer*``.  Some TL constructors
    require the narrower ``InputChannel``/``InputUser`` wrapper, while dialog and
    notification constructors require one additional envelope.  Keeping this here
    (rather than in :mod:`telegram_registry`) makes the registry session-agnostic and
    guarantees that raw calls use the same connected client and entity cache as the
    ordinary Praxis Telegram hands.
    """
    from telethon import utils
    from telethon.tl import types

    peer = await client.get_input_entity(value)
    expected = str(expected_type or "")
    if expected in {"InputChannel", "TypeInputChannel"}:
        return utils.get_input_channel(peer)
    if expected in {"InputUser", "TypeInputUser"}:
        return utils.get_input_user(peer)
    if expected in {"InputDialogPeer", "TypeInputDialogPeer"}:
        return types.InputDialogPeer(peer=utils.get_input_peer(peer))
    if expected in {"InputNotifyPeer", "TypeInputNotifyPeer"}:
        return types.InputNotifyPeer(peer=utils.get_input_peer(peer))
    return utils.get_input_peer(peer)


def _install_telegram_dispatcher() -> telegram_registry.TelegramAccountDispatcher:
    """Bind installed TL schema dispatch to the already-connected account client."""
    global _TELEGRAM_DISPATCHER, _TELEGRAM_CONFIRMATIONS, _TELEGRAM_CRITICAL_CHALLENGES
    try:
        confirmation_ttl = max(
            30, min(900, int(os.getenv("PRAXIS_TELEGRAM_CONFIRM_TTL_SEC", "300"))),
        )
    except ValueError:
        confirmation_ttl = 300
    _TELEGRAM_CONFIRMATIONS = telegram_confirmation.ConfirmationStore(
        owner_id=OWNER_ID, ttl_seconds=confirmation_ttl,
        confirmable_principals=(agent.PRAXIS_SELF_PRINCIPAL,),
    )
    _TELEGRAM_CRITICAL_CHALLENGES = telegram_confirmation.CriticalChallengeStore(
        owner_id=OWNER_ID, ttl_seconds=confirmation_ttl,
        initiator_principals=(agent.PRAXIS_SELF_PRINCIPAL,),
    )
    _TELEGRAM_DISPATCHER = telegram_registry.TelegramAccountDispatcher(
        caller=client,
        entity_resolver=_telegram_registry_entity_resolver,
        owner_id=OWNER_ID,
        confirmation_verifier=_TELEGRAM_CONFIRMATIONS.verify_and_consume,
        sovereign_principals=(agent.PRAXIS_SELF_PRINCIPAL,),
    )
    meta = _TELEGRAM_DISPATCHER.registry.metadata
    log.info(
        "Telethon TL registry: %d requests, layer=%s, version=%s, fingerprint=%s",
        meta["request_count"], meta["tl_layer"], meta["telethon_version"],
        str(meta["fingerprint"])[:12],
    )
    return _TELEGRAM_DISPATCHER


async def _telegram_account_async(*, action: str, target: str = "", query: str = "",
                                  request: str = "", params: dict | None = None,
                                  confirm: str = "", challenge_id: str = "",
                                  scope: str = "",
                                  namespace: str = "", risk: str = "",
                                  offset: int = 0, limit: int = 25,
                                  _principal: str | int = "unknown",
                                  _origin: dict | None = None,
                                  _execution: dict | None = None) -> dict:
    """Compact list/search/describe/call adapter over the live dispatcher."""
    dispatcher = _TELEGRAM_DISPATCHER
    if dispatcher is None:
        raise RuntimeError("Telethon registry dispatcher ещё не инициализирован")
    if not OWNER_ID or str(_principal) not in {str(OWNER_ID), agent.PRAXIS_SELF_PRINCIPAL}:
        raise PermissionError("raw MTProto dispatcher доступен только владельцу или самой Praxis")
    action = str(action or "").strip().lower()

    def owner_origin() -> telegram_confirmation.OwnerOrigin:
        if str(_principal) != str(OWNER_ID):
            raise PermissionError(
                "account-critical подтверждение даёт только владелец из личного Telegram-чата"
            )
        try:
            origin = telegram_confirmation.OwnerOrigin.from_mapping(_origin or {})
        except (TypeError, ValueError, OverflowError) as exc:
            raise PermissionError(
                "для account-critical действия нет точного immutable origin owner-сообщения"
            ) from exc
        if (origin.principal_id != str(OWNER_ID)
                or origin.chat_id != str(OWNER_ID) or not origin.is_dm):
            raise PermissionError(
                "account-critical действие разрешено только владельцу из личного Telegram-чата"
            )
        return origin

    critical_store = _TELEGRAM_CRITICAL_CHALLENGES
    proof_store = _TELEGRAM_CONFIRMATIONS
    if action in {"confirm", "pending_confirmations", "cancel_confirmation"}:
        if critical_store is None or proof_store is None:
            raise RuntimeError("Telegram critical confirmation stores не инициализированы")
        selector = str(challenge_id or target or request or "").strip()
        if action == "pending_confirmations":
            active = [
                item for item in critical_store.list()
                if item.get("status") in {"pending", "in_doubt"}
                and (
                    str(_principal) == str(OWNER_ID)
                    or item.get("requested_by") == agent.PRAXIS_SELF_PRINCIPAL
                )
            ]
            return {"action": action, "items": active, "count": len(active)}
        origin = owner_origin()
        if not selector:
            raise ValueError(f"telegram_account {action}: challenge_id обязателен")
        if action == "cancel_confirmation":
            cancelled = critical_store.cancel(selector, origin=origin)
            return {
                "action": action, "challenge_id": selector,
                "cancelled": bool(cancelled),
            }

        # The model sees only the challenge id.  The exact phrase is compared with
        # immutable raw Telegram text captured by the runner, never with a tool arg.
        claimed = critical_store.claim(selector, origin=origin)
        # Recompute the keyed binding from the authenticated envelope in this
        # process.  The commitment key is deliberately not durable, so a plain
        # verifier for a password/code can never be recovered from the ledger.
        binding = dispatcher.confirmation_binding(
            str(claimed["request_name"]), dict(claimed["parameters"]),
            principal=str(claimed["principal"]),
        )
        try:
            proof = proof_store.issue(binding, principal=_principal)
        except Exception as exc:
            critical_store.finish(
                selector, error=f"proof issue failed: {type(exc).__name__}: {exc}",
            )
            raise
        try:
            receipt = await dispatcher.handle(
                "call",
                {
                    "name": binding.request_name,
                    "parameters": dict(claimed["parameters"]),
                    "mode": "raw",
                },
                principal=claimed["principal"],
                confirmation=proof,
                delivery_context={
                    "chat_id": origin.chat_id,
                    "run_id": origin.run_id,
                    "origin_message_id": origin.message_id,
                    "challenge_id": selector,
                    "requested_by": claimed["principal"],
                    "confirmed_by": str(_principal),
                    "request_origin": claimed.get("origin"),
                },
            )
        except Exception:
            # Claim is already fsynced.  We cannot know whether an unexpected crash
            # happened before or after Telethon accepted the mutation, so it remains
            # in_doubt and can never be replayed automatically.
            raise
        challenge = critical_store.finish(selector, receipt=receipt)
        safe_receipt = dict((challenge.get("terminal") or {}).get("receipt") or {})
        return {
            "action": action,
            "requested_by": claimed["principal"],
            "confirmed_by": str(_principal),
            "challenge": challenge,
            # Never feed submitted/serialized auth parameters back into the durable
            # model/run log.  The challenge ledger exposes only opaque receipt identity.
            "receipt": {"action": "call", "receipt": safe_receipt},
        }

    mapped = {
        "registry_list": "list",
        "registry_search": "search",
        "list": "list",
        "search": "search",
        "describe": "describe",
        "call": "call",
    }.get(action)
    if mapped is None:
        raise ValueError(
            "registry action должен быть list | search | describe | call | confirm | "
            "pending_confirmations | cancel_confirmation"
        )

    arguments: dict = {}
    if mapped == "list":
        arguments = {"offset": max(0, int(offset)), "limit": int(limit)}
        if scope:
            arguments["scope"] = str(scope)
        if namespace:
            arguments["namespace"] = str(namespace)
        if risk:
            arguments["risk"] = str(risk)
    elif mapped == "search":
        arguments = {"query": str(query or target), "limit": int(limit)}
        if scope:
            arguments["scope"] = str(scope)
    elif mapped == "describe":
        arguments = {"name": str(request or target or query)}
    else:
        arguments = {
            "name": str(request or target),
            "parameters": dict(params or {}),
            "mode": "raw",
        }

        # `confirm` is a legacy model-controlled field.  It is intentionally ignored.
        # Critical dispatch is split into two durable runs and proof never crosses the
        # model boundary.
        try:
            descriptor = dispatcher.registry.get(arguments["name"])
        except Exception:
            descriptor = None
        if descriptor is not None and descriptor.requires_confirmation:
            if critical_store is None or proof_store is None:
                raise RuntimeError("Telegram critical confirmation stores не инициализированы")
            execution = dict(_execution or {})
            execution_run = str(execution.get("run_id") or "").strip()
            call_id = str(execution.get("call_id") or "").strip()
            if (not execution_run or not call_id
                    or execution.get("tool") != "telegram_account"):
                raise PermissionError(
                    "account-critical вызов не связан с точным durable tool intent"
                )
            if str(_principal) == agent.PRAXIS_SELF_PRINCIPAL:
                intent_origin = telegram_confirmation.CriticalIntentOrigin.background(
                    run_id=execution_run,
                    call_id=call_id,
                    principal_id=agent.PRAXIS_SELF_PRINCIPAL,
                    confirmation_owner_id=str(OWNER_ID),
                )
            else:
                origin = owner_origin()
                if execution_run != origin.run_id:
                    raise PermissionError(
                        "account-critical owner-вызов не связан с точным durable run"
                    )
                intent_origin = telegram_confirmation.CriticalIntentOrigin.from_owner(
                    origin, call_id=call_id,
                )
            stable_key = str(execution.get("idempotency_key") or "").strip()
            if not stable_key:
                stable_key = f"telegram-critical:{execution_run}:tool:{call_id}"
            binding = dispatcher.confirmation_binding(
                descriptor.name, arguments["parameters"], principal=_principal,
            )
            challenge = critical_store.prepare(
                binding, arguments["parameters"], origin=intent_origin,
                idempotency_key=stable_key,
            )
            return {
                "action": "challenge",
                "challenge": challenge,
                "instruction": (
                    "Владелец должен прислать exact_phrase отдельным новым сообщением "
                    "в личный чат; затем можно вызвать action=confirm с challenge_id."
                ),
            }

    current = agent.run_context.current_run()
    origin_chat_id = (_origin or {}).get("chat_id") if isinstance(_origin, dict) else None
    execution_run_id = ((_execution or {}).get("run_id")
                        if isinstance(_execution, dict) else None)
    delivery_context = {
        "chat_id": origin_chat_id or agent._active_chat(),
        "run_id": execution_run_id or (current.run_id if current is not None else None),
    }
    return await dispatcher.handle(
        mapped,
        arguments,
        principal=_principal,
        delivery_context=delivery_context,
    )


async def _resolve_membership_entity(target: str):
    kind, value = _membership_target(target)
    if kind == "invite":
        from telethon.tl.functions.messages import CheckChatInviteRequest

        checked = await client(CheckChatInviteRequest(value))
        ent = getattr(checked, "chat", None)
        if ent is None:
            raise ValueError("по invite-ссылке я ещё не состою; сначала нужен join")
        return ent
    ent = await _resolve_entity(value)
    if ent is None:
        raise ValueError(f"не найдена Telegram-группа: {target}")
    return ent


def _membership_entity_facts(ent) -> dict:
    chat_id = _marked_peer_id(ent)
    title = getattr(ent, "title", None) or getattr(ent, "name", None) or str(chat_id)
    return {
        "chat_id": chat_id,
        "entity_id": int(getattr(ent, "id")),
        "entity_kind": _entity_kind(ent),
        "title": str(title),
    }


async def _apply_membership_acceptance(
    action: str, result: dict, ent=None, *, principal_id: object
) -> bool:
    """Project a recorded Telegram acceptance into root-room state idempotently."""
    set_by = (
        "praxis" if str(principal_id or "") == agent.PRAXIS_SELF_PRINCIPAL else "owner"
    )
    if action == "join":
        if result.get("status") == "request_sent":
            return False  # keep polling: approval has not established membership yet
        chat_id = str(result.get("chat_id") or "")
        if not chat_id:
            raise ValueError("accepted join has no root chat_id")
        if ent is not None:
            await _initialize_joined_room(
                chat_id, ent, title=str(result.get("title") or chat_id), allow=True,
                set_by=set_by,
            )
            _entity_cache[chat_id] = ent
        else:
            # Recovery must not collapse a forum topic into a conversation id: membership
            # and room admission always use the marked root peer stored in the receipt.
            rooms.add_room(chat_id)
            profile = rooms.profile_read(chat_id)
            if not profile.get("exists") or not profile.get("structured"):
                rooms.set_mode(chat_id, "observer", reason="восстановила подтверждённый вход",
                               set_by=set_by)
            elif profile.get("mode") == "dead":
                rooms.set_mode(chat_id, "observer", reason="восстановила подтверждённый вход",
                               set_by=set_by)
            rooms.owner_card(chat_id, "join", "восстановила подтверждённый Telegram-вход")
        return True

    chat_id = str(result.get("chat_id") or "")
    if not chat_id:
        raise ValueError("accepted leave has no root chat_id")
    rooms.remove_room(chat_id)
    rooms.set_mode(chat_id, "dead", reason="вышла из Telegram", set_by=set_by)
    _entity_cache.pop(chat_id, None)
    return True


def _sync_history_scan(target: str = "", params: dict | None = None,
                       _principal: str | int = "unknown") -> str:
    """Runtime-owned forum-history scan: единая дверь (TRANSPORT-GATE 25.08).

    Исполняется в event-loop раннера через глобальный подключённый client;
    caller не передаёт ни transport, ни receipt. apply=true допускает запись
    evidence только внутри этого же вызова после complete-прохода.
    """
    if not OWNER_ID or str(_principal) not in {str(OWNER_ID), agent.PRAXIS_SELF_PRINCIPAL}:
        return "Отказ: history_scan — суверенная дверь владельца и самой Praxis."
    if client is None:
        return "history_scan: Telethon-клиент не подключён"
    cfg = dict(params or {})
    freeze_flag = bool(cfg.get("freeze", False))
    raw_ceiling = cfg.get("ceiling_id")
    # A freeze must begin at the newest message that the runtime itself
    # observes. Accepting the explicit marker avoids callers fabricating a
    # sentinel ceiling (and then overflowing offset_id = ceiling + 1).
    auto_ceiling = freeze_flag and raw_ceiling == "latest"
    if auto_ceiling:
        ceiling_id = None
    else:
        try:
            ceiling_id = int(raw_ceiling)
        except (TypeError, ValueError):
            suffix = " или 'latest' при freeze=true" if freeze_flag else ""
            return f"history_scan: ceiling_id обязателен и целый{suffix}"
    if freeze_flag:
        floor_id = None
    else:
        try:
            floor_id = int(cfg.get("floor_id"))
        except (TypeError, ValueError):
            return "history_scan: floor_id обязателен и целый"
    apply_flag = bool(cfg.get("apply", False))
    if apply_flag and freeze_flag:
        return "history_scan: freeze=true несовместим с apply=true"
    page_size = int(cfg.get("page_size", 100))
    max_pages = int(cfg.get("max_pages", 200))
    eligible_limit = int(cfg.get("eligible_limit", 500))
    if freeze_flag and "floor_id" in cfg:
        return "history_scan: freeze=true discovers its own floor; floor_id не передаётся"
    if not freeze_flag:
        try:
            scan_range = history_scan.ScanRange(floor_id=floor_id, ceiling_id=ceiling_id)
        except ValueError as exc:
            return f"history_scan: {exc}"
    async def _run() -> dict:
        entity = await _resolve_entity(target)
        if entity is None:
            return {"error": f"target не резолвится: {target}"}
        raw_id = getattr(entity, "id", None)
        if raw_id is None:
            return {"error": f"entity без id: {target}"}
        resolved = int(raw_id)
        # A numeric ref carries an independently checkable peer claim. A
        # title/@username is authoritative only through the resolution above.
        if not history_scan.resolved_peer_matches_target(target, resolved):
            return {"error": f"entity mismatch: target={target} resolved={resolved}"}

        def _page(offset_id: int, size: int):
            return client.iter_messages(entity, limit=size, offset_id=offset_id)

        async def fetch_page(offset_id: int, size: int):
            bucket: list = []
            async for message in _page(offset_id, size):
                bucket.append(message)
            return bucket

        peer_id = f"-100{resolved}"
        if freeze_flag:
            effective_ceiling = ceiling_id
            if auto_ceiling:
                try:
                    async for newest in client.iter_messages(entity, limit=1):
                        newest_id = getattr(newest, "id", None)
                        if (isinstance(newest_id, bool)
                                or not isinstance(newest_id, int) or newest_id < 1):
                            return {"error": "newest ceiling lookup returned invalid message id"}
                        effective_ceiling = newest_id
                        break
                except Exception as exc:
                    return {"error": f"newest ceiling lookup failed:{type(exc).__name__}"}
                if effective_ceiling is None:
                    return {"error": "newest ceiling lookup found no messages"}
            discovery = await history_scan.acquire_eligible_corpus(
                peer_id, effective_ceiling, fetch_page, eligible_limit=eligible_limit,
                page_size=page_size, max_pages=max_pages)
            if not discovery.complete or discovery.corpus is None:
                return {"error": f"acquisition incomplete:{discovery.reason}"}
            if not discovery.corpus:
                return {"error": "acquisition found no eligible text rows"}
            attestation = await history_scan.run_history_scan(
                peer_id, discovery.range, fetch_page,
                page_size=page_size, max_pages=max_pages)
            return history_scan.freeze_attested_corpus(
                discovery, attestation,
                acquired_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"))

        result = await history_scan.run_history_scan(
            peer_id, scan_range, fetch_page, page_size=page_size, max_pages=max_pages)
        return history_scan.observe_history_floor(result, apply=apply_flag)

    try:
        outcome = asyncio.run_coroutine_threadsafe(_run(), _LOOP).result(timeout=900)
    except TimeoutError:
        return "history_scan: transport timeout (900s), ничего не записано"
    if isinstance(outcome, dict) and outcome.get("error"):
        return f"history_scan: {outcome['error']}"
    return json.dumps(outcome, ensure_ascii=False, default=str)


_MODERATION_LOCKS: dict[str, asyncio.Lock] = {}


async def _moderate_abstractdl(peer_id: int, message_id: int, sender_id: int,
                               action: str, principal: str) -> dict:
    """One exact moderation action over the runtime-owned Telethon client."""
    key = telegram_moderation.validate(peer_id, message_id, sender_id, action)
    lock = _MODERATION_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        completed = telegram_moderation.prior(key)
        if completed:
            return {"replayed": True, **completed}
        latest = telegram_moderation.latest(key) or {}
        deleted = bool(latest.get("deleted"))
        banned = bool(latest.get("banned"))
        sender_verified = bool(latest.get("sender_verified"))
        entity = await client.get_entity(int(peer_id))
        if _marked_peer_id(entity) != int(peer_id):
            receipt = telegram_moderation.append_receipt({
                "idempotency_key": key, "actor": principal, "peer_id": int(peer_id),
                "message_id": int(message_id), "sender_id": int(sender_id), "action": action,
                "status": "failed", "deleted": deleted, "banned": banned,
                "sender_verified": sender_verified,
                "error": "resolved_peer_identity_mismatch",
            })
            return receipt
        if not latest:
            telegram_moderation.append_receipt({
                "idempotency_key": key, "actor": principal, "peer_id": int(peer_id),
                "message_id": int(message_id), "sender_id": int(sender_id), "action": action,
                "status": "intent", "deleted": False, "banned": False,
                "sender_verified": False, "error": "",
            })
        if not deleted:
            message = await client.get_messages(entity, ids=int(message_id))
            if message is None:
                # An intent exists before the delete RPC.  After restart, absence of the exact
                # message is the only observable post-state: continue as deleted rather than
                # permanently stranding delete_and_ban between the two effects.
                deleted = bool(sender_verified and latest.get("status") in {"verified", "partial"})
                if not deleted:
                    return telegram_moderation.append_receipt({
                        "idempotency_key": key, "actor": principal, "peer_id": int(peer_id),
                        "message_id": int(message_id), "sender_id": int(sender_id), "action": action,
                        "status": "failed", "deleted": False, "banned": banned,
                        "sender_verified": sender_verified,
                        "error": "exact_message_unavailable",
                    })
            else:
                actual_sender = int(getattr(message, "sender_id", 0) or 0)
                if actual_sender != int(sender_id):
                    return telegram_moderation.append_receipt({
                        "idempotency_key": key, "actor": principal, "peer_id": int(peer_id),
                        "message_id": int(message_id), "sender_id": int(sender_id), "action": action,
                        "status": "failed", "deleted": False, "banned": banned,
                        "sender_verified": False,
                        "error": f"sender_mismatch:{actual_sender}",
                    })
                sender_verified = True
                telegram_moderation.append_receipt({
                    "idempotency_key": key, "actor": principal, "peer_id": int(peer_id),
                    "message_id": int(message_id), "sender_id": int(sender_id),
                    "action": action, "status": "verified", "deleted": False,
                    "banned": banned, "sender_verified": True, "error": "",
                })
                try:
                    await client.delete_messages(entity, [int(message_id)], revoke=True)
                    deleted = True
                except Exception as exc:
                    return telegram_moderation.append_receipt({
                        "idempotency_key": key, "actor": principal, "peer_id": int(peer_id),
                        "message_id": int(message_id), "sender_id": int(sender_id), "action": action,
                        "status": "failed", "deleted": False, "banned": banned,
                        "sender_verified": sender_verified,
                        "error": f"delete:{type(exc).__name__}:{exc}"[:500],
                    })
        if action == "delete_and_ban" and not banned:
            try:
                await client.edit_permissions(entity, int(sender_id), view_messages=False)
                banned = True
            except Exception as exc:
                return telegram_moderation.append_receipt({
                    "idempotency_key": key, "actor": principal, "peer_id": int(peer_id),
                    "message_id": int(message_id), "sender_id": int(sender_id), "action": action,
                    "status": "partial", "deleted": deleted, "banned": False,
                    "sender_verified": sender_verified,
                    "error": f"ban:{type(exc).__name__}:{exc}"[:500],
                })
        return telegram_moderation.append_receipt({
            "idempotency_key": key, "actor": principal, "peer_id": int(peer_id),
            "message_id": int(message_id), "sender_id": int(sender_id), "action": action,
            "status": "completed", "deleted": deleted,
            "banned": banned if action == "delete_and_ban" else False,
            "sender_verified": sender_verified, "error": "",
        })


def _sync_moderate_abstractdl(params: dict | None = None,
                               _principal: object = None, **_ignored) -> str:
    denied = _telegram_account_gate()
    if denied:
        return denied
    if _LOOP is None or not client.is_connected():
        return "moderation: Telethon-клиент не подключён"
    data = dict(params or {})
    allowed = {"peer_id", "message_id", "sender_id", "decision"}
    extras = sorted(set(data) - allowed)
    if extras:
        return f"moderation: only exact fields are accepted; unexpected: {', '.join(extras)}"
    if set(data) != allowed:
        missing = sorted(allowed - set(data))
        return f"moderation: missing exact fields: {', '.join(missing)}"
    values = (data["peer_id"], data["message_id"], data["sender_id"])
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        return "moderation: peer_id/message_id/sender_id must be exact JSON integers"
    if not isinstance(data["decision"], str):
        return "moderation: decision must be an exact string"
    principal = _telegram_account_principal(_principal)
    if principal is None:
        return "moderation: sovereign principal unavailable"
    try:
        result = asyncio.run_coroutine_threadsafe(
            _moderate_abstractdl(data["peer_id"], data["message_id"],
                                 data["sender_id"], data["decision"],
                                 principal), _LOOP).result(timeout=90)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        return f"moderation: {type(exc).__name__}: {exc}"


_ADMIN_LOCKS: dict[str, asyncio.Lock] = {}

# ⚠ В Telegram ChatBannedRights флаг True означает ЗАПРЕЩЕНО, а не разрешено.
# «Разрешить файлы» — это выставить send_docs=False.  Перепутать здесь значит
# сделать ровно обратное тому, что она решила, и притом молча.
_RIGHT_DENIED = True
_RIGHT_ALLOWED = False


def _banned_rights_snapshot(rights, names) -> dict:
    """Что сейчас запрещено из тех прав, которых касается запрос."""
    return {name: bool(getattr(rights, name, False)) for name in sorted(names)}


async def _admin_state(entity, action: str, subject: dict) -> dict:
    """Состояние комнаты ДО меры — без него мера необратима."""
    # ⚠ Модульный `types` здесь — стандартная библиотека (импорт в шапке файла),
    # а `functions` на уровне модуля не связан вовсе.  Телетоновские имена берём
    # локально, ровно как остальной раннер: иначе `types.ChatBannedRights` тихо
    # становится AttributeError на первом же живом вызове.
    from telethon.tl import functions
    if action == "slow_mode":
        full = await client(functions.channels.GetFullChannelRequest(entity))
        return {"slowmode_seconds": int(getattr(full.full_chat, "slowmode_seconds", 0) or 0)}
    if action == "default_rights":
        rights = getattr(entity, "default_banned_rights", None)
        names = set(subject["allow"]) | set(subject["deny"])
        return {"denied": _banned_rights_snapshot(rights, names)}
    try:
        participant = await client(functions.channels.GetParticipantRequest(
            entity, int(subject["user_id"])))
        current = getattr(participant.participant, "banned_rights", None)
        snapshot = {"kind": type(participant.participant).__name__,
                    "denied": _banned_rights_snapshot(current, telegram_admin.RIGHTS)}
        # Срок — половина смысла ограничения. Без него в чеке вечная мера
        # неотличима от тридцатисекундной НИ В ОДНОМ артефакте, который она читает
        # (адверсарка 28.08: until_date не попадал в снимок никогда).
        until = getattr(current, "until_date", None)
        if until:
            snapshot["until_date"] = str(until)
        return snapshot
    except Exception as exc:
        # Не в комнате — это факт о мире, а не сбой: снимать ограничение с
        # отсутствующего можно, накладывать бессмысленно, и обе развилки решает
        # апстрим ниже.  Молчать об этом нельзя, поэтому пишем в чек.
        return {"kind": "absent", "lookup_error": f"{type(exc).__name__}: {exc}"[:200]}


# Telegram трактует until_date ближе ~30 секунд ОТ СВОИХ часов как «навсегда».
# restrict с ровно now+30 проигрывал эту гонку в 10 000 пробах из 10 000 (RTT,
# очередь, расхождение часов) — «на 30 секунд» уходило на провод вечным баном.
# Запас делает названный срок ПОЛОМ: мера чуть длиннее, но никогда не вечная.
_RESTRICT_WIRE_MARGIN = 45


async def _admin_apply(entity, action: str, subject: dict) -> dict | None:
    """Применить меру; возвращает провод-факты для чека (или None, если их нет)."""
    from telethon.tl import functions, types
    if action == "slow_mode":
        await client(functions.channels.ToggleSlowModeRequest(
            channel=entity, seconds=int(subject["seconds"])))
        return None
    if action == "default_rights":
        # ⚠ EditChatDefaultBannedRights ЗАМЕЩАЕТ весь объект прав целиком.  Слепая
        # отправка нового объекта стёрла бы все прочие ограничения комнаты, которых
        # она не касалась.  Поэтому читаем текущее и правим только названные флаги.
        current = getattr(entity, "default_banned_rights", None)
        # Сохраняем *все* известные Telethon-поля, не лишь узкий набор, который
        # эта рука разрешает менять.  RPC заменяет ChatBannedRights целиком;
        # пропуск неуправляемого флага (например view_messages/manage_topics)
        # снял бы уже действующий запрет.
        fields = ({name: value for name, value in vars(current).items()
                   if name != "until_date"} if current is not None else {})
        fields.update({name: _RIGHT_ALLOWED for name in subject["allow"]})
        fields.update({name: _RIGHT_DENIED for name in subject["deny"]})
        # Telethon deserializes this as datetime (DateLike), not an epoch integer.
        # Preserve that value verbatim: coercing with int() rejects ordinary live
        # room state before the request can reach Telegram.
        until = getattr(current, "until_date", None)
        await client(functions.messages.EditChatDefaultBannedRightsRequest(
            peer=entity, banned_rights=types.ChatBannedRights(until_date=until, **fields)))
        return None
    if action == "restrict":
        until = int(time.time()) + int(subject["seconds"]) + _RESTRICT_WIRE_MARGIN
        await client(functions.channels.EditBannedRequest(
            channel=entity, participant=int(subject["user_id"]),
            banned_rights=types.ChatBannedRights(
                until_date=until, send_messages=True, send_media=True,
                send_stickers=True, send_gifs=True, send_games=True,
                send_inline=True, embed_links=True, send_polls=True)))
        # Что ИМЕННО ушло на провод — в чек: иначе named-срок и wire-срок
        # нечем сверить ни ей, ни аудиту.
        return {"until_date_sent": until, "wire_margin_seconds": _RESTRICT_WIRE_MARGIN}
    if action == "ban_member":
        # Перманентный бан: until_date=None — это «навсегда» на проводе Telegram.
        # Полный набор send-флагов, как у restrict, но без срока.
        await client(functions.channels.EditBannedRequest(
            channel=entity, participant=int(subject["user_id"]),
            banned_rights=types.ChatBannedRights(
                until_date=None, send_messages=True, send_media=True,
                send_stickers=True, send_gifs=True, send_games=True,
                send_inline=True, embed_links=True, send_polls=True)))
        return {"until_date_sent": None, "permanent": True}
    if action == "purge_member":
        # DeleteParticipantHistory требует живого бана — Telegram отклоняет
        # запрос к не-ограниченному участнику. Бан идёт первым, чистка вторым;
        # оба эффекта в одном чеке, чтобы «забанен, но история жива» было видно.
        await client(functions.channels.EditBannedRequest(
            channel=entity, participant=int(subject["user_id"]),
            banned_rights=types.ChatBannedRights(
                until_date=None, send_messages=True, send_media=True,
                send_stickers=True, send_gifs=True, send_games=True,
                send_inline=True, embed_links=True, send_polls=True)))
        await client(functions.channels.DeleteParticipantHistoryRequest(
            channel=entity, participant=int(subject["user_id"])))
        return {"until_date_sent": None, "permanent": True,
                "history_purged": True}
    # unrestrict: все флаги сняты и срока нет — это полное восстановление прав,
    # и оно же единственный способ снять `delete_and_ban`, наложенный модерацией.
    await client(functions.channels.EditBannedRequest(
        channel=entity, participant=int(subject["user_id"]),
        banned_rights=types.ChatBannedRights(until_date=0)))
    return None


async def _admin_abstractdl(peer_id: int, action: str, params: dict,
                            principal: str) -> dict:
    """Мера над комнатой, а не над сообщением: тот же забор, свой журнал.

    Отличие от `_moderate_abstractdl` не только в предмете.  Там мера состоит из
    двух эффектов (удалить и забанить), и между ними можно застрять; здесь эффект
    один, зато он касается всех 968 участников сразу — поэтому чек хранит
    состояние ДО и ПОСЛЕ, а не только «сделано».  Мера, у которой не записано
    предыдущее состояние, необратима на практике, даже если обратима в теории.
    """
    key, action, subject = telegram_admin.validate(peer_id, action, params)
    lock = _ADMIN_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        completed = telegram_admin.prior(key)
        if completed:
            return {"replayed": True, **completed}
        entity = await client.get_entity(int(peer_id))
        base = {"idempotency_key": key, "actor": principal, "peer_id": int(peer_id),
                "action": action, "subject": subject}
        if _marked_peer_id(entity) != int(peer_id):
            return telegram_admin.append_receipt({
                **base, "status": "failed", "before": {}, "after": {},
                "error": "resolved_peer_identity_mismatch"})
        before = await _admin_state(entity, action, subject)
        telegram_admin.append_receipt({
            **base, "status": "intent", "before": before, "after": {}, "error": ""})
        try:
            wire = await _admin_apply(entity, action, subject)
        except Exception as exc:
            return telegram_admin.append_receipt({
                **base, "status": "failed", "before": before, "after": {},
                "error": f"{action}:{type(exc).__name__}:{exc}"[:500]})
        try:
            entity = await client.get_entity(int(peer_id))
            after = await _admin_state(entity, action, subject)
        except Exception as exc:
            after = {"readback_error": f"{type(exc).__name__}: {exc}"[:200]}
        return telegram_admin.append_receipt({
            **base, "status": "completed", "before": before, "after": after,
            **({"wire": wire} if wire else {}),
            "error": ""})


def _sync_admin_abstractdl(params: dict | None = None,
                           _principal: object = None, **_ignored) -> str:
    denied = _telegram_account_gate()
    if denied:
        return denied
    if _LOOP is None or not client.is_connected():
        return "admin: Telethon-клиент не подключён"
    data = dict(params or {})
    if data.pop("history", None):
        return json.dumps(telegram_admin.history(20), ensure_ascii=False, default=str)
    allowed = {"peer_id", "action", "params"}
    extras = sorted(set(data) - allowed)
    if extras:
        return f"admin: only exact fields are accepted; unexpected: {', '.join(extras)}"
    missing = sorted(allowed - set(data))
    if missing:
        return f"admin: missing exact fields: {', '.join(missing)}"
    principal = _telegram_account_principal(_principal)
    if principal is None:
        return "admin: sovereign principal unavailable"
    try:
        result = asyncio.run_coroutine_threadsafe(
            _admin_abstractdl(data["peer_id"], data["action"],
                              data["params"], principal), _LOOP).result(timeout=90)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        return f"admin: {type(exc).__name__}: {exc}"


def _canonical_peer_id(entity) -> str | None:
    """Канонический id пира из резолвнутой entity (без -100 префикса)."""
    for attr in ("id",):
        value = getattr(entity, attr, None)
        if value is not None:
            try:
                return str(int(value))
            except (TypeError, ValueError):
                continue
    return None


def _membership_transaction(action: str, target: str, principal_id: object,
                            transaction_id: str | None) -> dict:
    if transaction_id is None:
        raise PermissionError("membership requires a durable sovereign intent")
    ledger = _membership_ledger()
    state = ledger.get(transaction_id)
    if state is None:
        raise KeyError(f"unknown membership transaction {transaction_id}")
    principal = _telegram_account_principal(state.get("principal_id"))
    supplied = _telegram_account_principal(principal_id)
    if principal is None or supplied != principal:
        raise PermissionError("membership transaction actor is no longer valid")
    if (state.get("action"), state.get("target"), state.get("principal_id")) != (
            action, target, principal):
        raise PermissionError("membership transaction provenance mismatch")
    return state


async def _join_chat_async(target: str, *, principal_id: object = None,
                           transaction_id: str | None = None, recovery: bool = False) -> dict:
    from telethon.errors import InviteRequestSentError, UserAlreadyParticipantError
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

    target = str(target or "").strip()
    kind, value = _membership_target(target)  # local validation before creating an intent
    state = _membership_transaction("join", target, principal_id, transaction_id)
    tx_id = state["id"]
    ledger = _membership_ledger()
    if state["status"] == "applied":
        return dict(state.get("result") or {})

    ent = None
    mutation_started = False
    try:
        # Telegram accepted but the process died before room projection.  Apply without
        # repeating the network mutation; a pending invite is polled read-only below.
        if state["status"] == "accepted" and state.get("result", {}).get("status") != "request_sent":
            result = dict(state["result"])
            try:
                ent = await _resolve_entity(result.get("chat_id"))
            except Exception:
                ent = None
            if await _apply_membership_acceptance(
                "join", result, ent, principal_id=state["principal_id"],
            ):
                ledger.applied(tx_id)
            return result

        already = False
        if kind == "invite":
            checked = await client(CheckChatInviteRequest(value))
            ent = getattr(checked, "chat", None)
            if ent is not None:
                already = True
            elif state["status"] == "accepted":
                # A join-request acceptance is not yet membership.  Do not send a second
                # request; leave it pending for the next clock tick.
                return dict(state["result"])
            else:
                mutation_started = True
                try:
                    updates = await client(ImportChatInviteRequest(value))
                except UserAlreadyParticipantError:
                    checked = await client(CheckChatInviteRequest(value))
                    ent = getattr(checked, "chat", None)
                    already = True
                except InviteRequestSentError:
                    result = {
                        "status": "request_sent", "chat_id": None,
                        "title": getattr(checked, "title", None) or "?",
                        "detail": "заявка на вступление отправлена администраторам",
                    }
                    ledger.accepted(tx_id, result)
                    return result
                else:
                    chats = list(getattr(updates, "chats", None) or ())
                    ent = chats[0] if chats else None
            if ent is None:
                raise RuntimeError("Telegram подтвердил invite, но не вернул chat entity")
        else:
            ent = await _resolve_entity(value)
            if ent is None:
                raise ValueError(f"не найдена Telegram-группа: {target}")
            if _entity_kind(ent) not in ("channel", "chat"):
                raise ValueError("это Telegram-пользователь, а не группа/канал")
            ledger.prepared(tx_id, _membership_entity_facts(ent))
            mutation_started = True
            try:
                updates = await client(JoinChannelRequest(ent))
                chats = list(getattr(updates, "chats", None) or ())
                if chats:
                    ent = chats[0]
            except UserAlreadyParticipantError:
                already = True
        if _entity_kind(ent) not in ("channel", "chat"):
            raise ValueError("invite ведёт не в группу/канал")
        facts = _membership_entity_facts(ent)
        ledger.prepared(tx_id, facts)
        result = dict(facts)
        result.pop("entity_kind", None)
        result["status"] = "already_joined" if already else "joined"
        ledger.accepted(tx_id, result)  # acceptance is durable before local room writes
    except Exception as exc:
        current = ledger.get(tx_id) or {}
        if current.get("status") != "accepted":
            if isinstance(exc, (ValueError, ResolveDenied)) and not mutation_started:
                ledger.failed(tx_id, exc)
            else:
                ledger.in_doubt(tx_id, exc)
        raise

    try:
        if await _apply_membership_acceptance(
            "join", result, ent, principal_id=state["principal_id"],
        ):
            ledger.applied(tx_id)
    except Exception as exc:
        result = dict(result)
        result["projection"] = "pending"
        result["projection_error"] = f"{type(exc).__name__}: {exc}"[:300]
        log.exception("membership join принят Telegram, room projection ждёт retry [%s]", tx_id)
    return result


async def _leave_chat_async(target: str, *, principal_id: object = None,
                            transaction_id: str | None = None, recovery: bool = False) -> dict:
    from telethon.errors import UserNotParticipantError
    from telethon.tl.functions.channels import LeaveChannelRequest
    from telethon.tl.functions.messages import DeleteChatUserRequest
    from telethon.tl.types import InputUserSelf

    target = str(target or "").strip()
    target_kind, _ = _membership_target(target)
    state = _membership_transaction("leave", target, principal_id, transaction_id)
    tx_id = state["id"]
    ledger = _membership_ledger()
    if state["status"] == "applied":
        return dict(state.get("result") or {})
    if state["status"] == "accepted":
        result = dict(state["result"])
        if await _apply_membership_acceptance(
            "leave", result, principal_id=state["principal_id"],
        ):
            ledger.applied(tx_id)
        return result

    ent = None
    mutation_started = False
    try:
        try:
            ent = await _resolve_membership_entity(target)
        except ValueError:
            prepared = dict(state.get("prepared") or {})
            if not (recovery and target_kind == "invite" and prepared.get("chat_id")):
                raise
            # The initial run proved membership and durably prepared the root entity.
            # If the same invite now says no membership, the desired leave is already true.
            result = {
                "status": "already_left",
                "chat_id": prepared["chat_id"],
                "entity_id": prepared.get("entity_id"),
                "title": prepared.get("title") or str(prepared["chat_id"]),
            }
            ledger.accepted(tx_id, result)
        else:
            facts = _membership_entity_facts(ent)
            kind = facts["entity_kind"]
            if kind not in {"channel", "chat"}:
                raise ValueError("это Telegram-пользователь, а не группа/канал")
            ledger.prepared(tx_id, facts)
            mutation_started = True
            try:
                if kind == "channel":
                    await client(LeaveChannelRequest(ent))
                else:
                    await client(DeleteChatUserRequest(int(getattr(ent, "id")), InputUserSelf()))
                status = "left"
            except UserNotParticipantError:
                status = "already_left"
            result = dict(facts)
            result.pop("entity_kind", None)
            result["status"] = status
            ledger.accepted(tx_id, result)  # durable before allowlist/profile mutation
    except Exception as exc:
        current = ledger.get(tx_id) or {}
        if current.get("status") != "accepted":
            if isinstance(exc, (ValueError, ResolveDenied)) and not mutation_started:
                ledger.failed(tx_id, exc)
            else:
                ledger.in_doubt(tx_id, exc)
        raise

    try:
        if await _apply_membership_acceptance(
            "leave", result, ent, principal_id=state["principal_id"],
        ):
            ledger.applied(tx_id)
    except Exception as exc:
        result = dict(result)
        result["projection"] = "pending"
        result["projection_error"] = f"{type(exc).__name__}: {exc}"[:300]
        log.exception("membership leave принят Telegram, room projection ждёт retry [%s]", tx_id)
    return result


def _membership_receipt(action: str, result: dict) -> str:
    status = result.get("status")
    if status == "request_sent":
        return f"Заявка на вход отправлена → {result.get('title')} (status=request_sent)."
    verb = "Вошла" if action == "join" else "Вышла"
    if status == "already_joined":
        verb = "Уже состою"
    elif status == "already_left":
        verb = "Уже вышла"
    pending = " local_projection=pending" if result.get("projection") == "pending" else ""
    return (
        f"{verb} → {result.get('title')} "
        f"(chat_id={result.get('chat_id')}, entity_id={result.get('entity_id')}, "
        f"status={status}{pending})."
    )


def _sync_join_chat(target: str) -> str:
    denied = _telegram_account_gate()
    if denied:
        return denied
    state = _begin_membership_transaction("join", target)
    result = _threadsafe_result(
        lambda: _join_chat_async(
            str(state["target"]), principal_id=state["principal_id"],
            transaction_id=state["id"],
        ), 120,
    )
    return _membership_receipt("join", result)


def _sync_leave_chat(target: str) -> str:
    denied = _telegram_account_gate()
    if denied:
        return denied
    state = _begin_membership_transaction("leave", target)
    result = _threadsafe_result(
        lambda: _leave_chat_async(
            str(state["target"]), principal_id=state["principal_id"],
            transaction_id=state["id"],
        ), 120,
    )
    return _membership_receipt("leave", result)


def _sync_telegram_account(**arguments) -> str:
    """Sovereign synchronous bridge from model tool thread to the live Telethon loop."""
    denied = _telegram_account_gate()
    if denied:
        return denied
    if not OWNER_ID:
        return "Отказ: PRAXIS_OWNER_ID не настроен; raw MTProto dispatcher закрыт."
    principal = _telegram_account_principal()
    if principal is None:
        return "Отказ: raw MTProto dispatcher доступен только владельцу или самой Praxis."
    call_arguments = dict(arguments)
    call_arguments["_principal"] = principal
    # Capture both contextvars before crossing into the Telethon event-loop thread.
    # current_origin_evidence re-reads and verifies the immutable run snapshot; a
    # rolling buffer, model argument or mutable process global is never accepted.
    call_arguments["_origin"] = agent.current_origin_evidence()
    call_arguments["_execution"] = agent.current_tool_execution()
    result = _threadsafe_result(lambda: _telegram_account_async(**call_arguments), 120)
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


async def _set_profile_photo_async(path: str) -> str:
    from telethon.tl.functions.photos import UploadProfilePhotoRequest
    p = Path(str(path).strip())
    if not p.is_file():
        return f"(нет файла: {path})"
    uploaded = await client.upload_file(str(p))
    await client(UploadProfilePhotoRequest(file=uploaded))
    return f"Аватарка обновлена ({p.name})."


def _sync_set_avatar(path: str) -> str:
    denied = _telegram_account_gate()
    if denied:
        return denied
    return _threadsafe_result(lambda: _set_profile_photo_async(str(path)), 120)


async def _update_profile_async(about: str, first_name: str, last_name: str) -> str:
    from telethon.tl.functions.account import UpdateProfileRequest

    def _field(value: str):
        v = str(value or "").strip()
        if not v:
            return None            # пустое — не трогаем поле
        return "" if v == "-" else v

    kw = {"about": _field(about), "first_name": _field(first_name),
          "last_name": _field(last_name)}
    kw = {k: v for k, v in kw.items() if v is not None}
    if not kw:
        return "Нечего менять."
    await client(UpdateProfileRequest(**kw))
    changed = ", ".join(f"{k}={'(очищено)' if v == '' else v}" for k, v in kw.items())
    return f"Профиль обновлён: {changed}."


def _sync_update_profile(about: str = "", first_name: str = "", last_name: str = "") -> str:
    denied = _telegram_account_gate()
    if denied:
        return denied
    return _threadsafe_result(
        lambda: _update_profile_async(about, first_name, last_name), 120)


async def _react_async(chat, message_id: int, emoji: str, remove: bool) -> str:
    from telethon.tl.functions.messages import SendReactionRequest
    from telethon.tl.types import ReactionEmoji
    ref = str(chat or "").strip()
    if ref:
        route = _route_from_reference(ref)
        ent = await _resolve_entity(route.peer_id)
    else:
        current = agent._active_chat()
        if current is None:
            return "(нет текущего чата — укажи chat)"
        route = _route_from_reference(current)
        ent = _meta.get(route.conversation_id, {}).get("entity") or await _resolve_entity(route.peer_id)
    if ent is None:
        return f"(не найден чат: {chat or 'текущий'})"
    reaction = None if remove else [ReactionEmoji(emoticon=str(emoji))]
    await client(SendReactionRequest(peer=ent, msg_id=int(message_id), reaction=reaction))
    return (f"Реакция с #{message_id} снята." if remove
            else f"Поставлено {emoji} на #{message_id}.")


def _sync_react(chat: str = "", message_id: int = 0, emoji: str = "", remove: bool = False) -> str:
    denied = _telegram_account_gate()
    if denied:
        return denied
    if not int(message_id or 0):
        return "Нужен message_id (номер сообщения из контекста)."
    return _threadsafe_result(
        lambda: _react_async(chat, int(message_id), emoji, bool(remove)), 60)


def _sync_followups(action: str = "list", followup_id: str = "",
                    limit: int = 0, offset: int = 0) -> str:
    denied = _telegram_account_gate()
    if denied:
        return denied
    action = str(action or "list").strip().casefold()
    if action in ("list", "status"):
        kw = {"offset": max(0, int(offset or 0))}
        if int(limit or 0) > 0:
            kw["limit"] = int(limit)
        return telegram_followups.LEDGER.context(**kw)
    if action == "cancel":
        return (f"Отменён follow-up {followup_id}." if telegram_followups.LEDGER.cancel(followup_id)
                else f"Не найден активный follow-up {followup_id}.")
    if action in ("watch", "unwatch"):
        # Её рука. Отчёт Егору больше не заводится автоматически (см. _sync_send_message):
        # раз так, у неё обязана остаться возможность его ПОПРОСИТЬ — иначе снятие
        # автоматизма стало бы отнятой способностью. Раньше она могла только гасить чужое
        # решение (шесть раз и гасила), теперь она автор нити.
        on = action == "watch"
        item = telegram_followups.LEDGER.set_notice(followup_id, on, source="praxis")
        if item is None:
            return f"Не найдена живая нить {followup_id}."
        return (f"Отчёт Егору по {followup_id} " + (
            "включено — когда ответят, ему уйдёт письмо; возрастной срок с нити снят."
            if on else "выключено — нить остаётся моим следом, Егору не уйдёт."))
    return "action должен быть list | watch | unwatch | cancel."


def _sync_read_chat(chat_ref, limit: int = 30) -> str:
    """§3: последние сообщения соседнего диалога (по id/@username/имени)."""
    async def _coro():
        route = _route_from_reference(chat_ref)
        ent = await _resolve_entity(route.peer_id)
        if ent is None:
            return "(не найден такой чат)"
        kwargs = {"reply_to": route.topic_id} if route.topic_id is not None else {}
        msgs = await client.get_messages(ent, limit=int(limit), **kwargs)
        return "\n".join(_format_messages(reversed(list(msgs)))) or "(пусто)"
    return _threadsafe_result(_coro, 40)


def _sync_fetch_context(chat_id, limit: int = LAST_N) -> str:
    """§4: живой контекст текущего чата из Telegram (последние N, её реплики включены)."""
    async def _coro():
        route = _route_from_reference(chat_id)
        ent = _meta.get(route.conversation_id, {}).get("entity")
        if ent is None:
            ent = await _resolve_entity(route.peer_id)
        if ent is None:
            return "(нет такого чата)"
        kwargs = {"reply_to": route.topic_id} if route.topic_id is not None else {}
        msgs = await client.get_messages(ent, limit=int(limit), **kwargs)
        return "\n".join(_format_messages(reversed(list(msgs)))) or "(пусто)"
    return _threadsafe_result(_coro, 40)


def _project_direct_outbox_acceptance(proof: dict, entry: dict) -> str:
    """Apply accepted-send bookkeeping exactly once per durable outbox key."""

    identity = dict(proof.get("entry") or {})
    projection = dict(proof.get("projection") or {})
    receipt = dict(entry.get("receipt") or {})
    key = str(identity.get("key") or "")
    message_id = receipt.get("message_id")
    peer_id = identity.get("peer_id")
    target_user_id = projection.get("target_user_id")
    contact_id = target_user_id if target_user_id is not None else peer_id
    raw_accepted_at = entry.get("updated_at")
    if isinstance(raw_accepted_at, bool):
        raise agent.DurableExecutionError("direct outbox acceptance time is malformed")
    if isinstance(raw_accepted_at, (int, float)):
        accepted_at = float(raw_accepted_at)
    elif isinstance(raw_accepted_at, str):
        try:
            parsed = datetime.datetime.fromisoformat(
                raw_accepted_at[:-1] + "+00:00"
                if raw_accepted_at.endswith("Z") else raw_accepted_at
            )
        except ValueError as exc:
            raise agent.DurableExecutionError(
                "direct outbox acceptance time is malformed"
            ) from exc
        if parsed.tzinfo is None:
            raise agent.DurableExecutionError(
                "direct outbox acceptance time has no timezone"
            )
        accepted_at = parsed.timestamp()
    else:
        raise agent.DurableExecutionError("direct outbox acceptance time is missing")
    if not (0 < accepted_at < float("inf")):
        raise agent.DurableExecutionError("direct outbox acceptance time is out of range")
    # ⚑ ОТВЕТ СОБЕСЕДНИКУ — НЕ ИНИЦИАТИВНАЯ ОТПРАВКА, И УЧЁТ У НЕГО ДРУГОЙ.
    #
    # Проектор один на все прямые отправки, и он таким и остаётся: второй развёл бы швы
    # заново. Но три записи ниже описывают ИНИЦИАТИВУ — «я пошла и написала человеку»:
    # леджер контактов, социальный пульс и след нити с отчётом. Реплика в разговоре, где
    # человек только что написал сам, ничего из этого не означает.
    #
    # Решение Егора 15.08, дословно: «ни ограничений, ни пульса пока что». Голосовой шов
    # сегодня в пульс и контакты не пишет вовсе (у обоих по одному вызывающему на всё
    # дерево — этот), и перенос ответа в руку НЕ ДОЛЖЕН включить их молча: это было бы
    # изменение поведения под видом рефакторинга.
    #
    # Что остаётся ответу и почему: снятие неотвеченности, возврат своей реплики в буфер,
    # записка «сказала» и архив комнаты — без них она перестала бы видеть в разговоре
    # собственные слова, а это ровно та дыра, которую здесь закрывали 26.07.
    is_reply = str(entry.get("purpose") or "") == "tool:reply"
    if not is_reply:
        telegram_contacts.mark_outbound(
            contact_id, idempotency_key=key, at=accepted_at,
        )
    try:
        unanswered.resolve(str(contact_id))
    except Exception:
        pass
    if not is_reply:
        social_pulse.note_outbound(
            peer_id, message_id=message_id,
            label=str(projection.get("target_label") or ""), now=accepted_at,
            pulse_id_override=str(projection.get("pulse_id") or ""),
            idempotency_key=key,
        )
    followup_request = str(projection.get("followup_request") or "")
    said_text = str((entry.get("payload") or {}).get("text") or "").strip()
    is_owner_peer = bool(OWNER_ID) and str(peer_id) == str(OWNER_ID)
    if message_id is not None and str(entry.get("kind") or "") == "text" and not is_reply:
        # ⚠ 27.07: здесь были слиты два разных понятия, и из-за этого Егору в ЛС уехала
        # его же реплика из AbstractDL под заголовком «AbstractDL Chat ответил(а)».
        # Разделяю:
        #   СЛЕД нити — ЕЁ память, и он заводится ВСЕГДА. Внутри часового пульса Telethon
        #   разорван, и эта запись — единственное, по чему она через час понимает, что уже
        #   сказала и кому (см. докстринг FollowUpLedger.context). Егору след не уходит.
        #   ОТЧЁТ Егору — отдельное свойство нити и уходит, только когда его КТО-ТО
        #   заказал: он словами или она сама (telegram_account watch_reply).
        # До сегодня заказчика не было ни у одной ветки: 32 записи на проде, заказано
        # словами 0, писем ему 17 (89% его личного ящика от неё), шесть она гасила руками.
        # Бухгалтерия не имеет права уронить уже состоявшуюся доставку — отсюда except:
        # иначе исключение здесь стало бы вердиктом «не отправилось» на доставленном.
        try:
            item = telegram_followups.LEDGER.create(
                target_ref=str(projection.get("target_ref") or peer_id),
                target_label=str(projection.get("target_label") or peer_id),
                target_peer_id=peer_id, target_user_id=target_user_id,
                sent_message_id=message_id, request_text=followup_request,
                # Текст отдаём целиком: длину режет и НАЗЫВАЕТ сам леджер, второй свой
                # кап здесь стал бы молчаливым пределом поверх названного.
                sent_excerpt=said_text,
                # 01.08: исключение владельца висело на ВСЁМ условии выше, а не
                # только на отчёте — и убивало сам след. Из 80 записей леджера по
                # его личке было НОЛЬ, поэтому внутри часового пульса (Telethon
                # разорван, буфер лички не читается вовсе) она не имела ни одного
                # способа узнать, что уже ему написала: 01.08 поздоровалась дважды
                # за утро, дважды перед этим спросив ленту нитей. Разделение теперь
                # такое, каким его описывает комментарий выше: СЛЕД заводится
                # всегда, ОТЧЁТ — только не ему и только по заказу.
                notify_owner=bool(followup_request) and not is_owner_peer,
                notice_source=("owner" if followup_request and not is_owner_peer else ""),
                sent_at=accepted_at, idempotency_key=key,
                # 04.08: чем именно сказано ("tool:send_message" / "tool:narrate" / …).
                # Сам след заводится по-прежнему ВСЕГДА и на наррацию тоже — он её
                # память. Метка нужна только читателям, которые отвечают на вопрос
                # «кому я писала», чтобы строка процесса не вытесняла письмо человеку.
                purpose=str(entry.get("purpose") or ""),
            )
            log.info("FOLLOW-UP %s: слежу за нитью %s message_id=%s (отчёт Егору: %s)",
                     item.get("id"), projection.get("target_label") or peer_id, message_id,
                     "заказан им словами" if item.get("notify_owner")
                     else "нет — это мой след")
        except Exception:
            log.exception(
                "след нити не завёлся [%s] #%s — через час в пульсе я не вспомню, что "
                "уже сказано в этой комнате", peer_id, message_id)
    # ⚠ Её собственная реплика обязана вернуться в разговор. Обычный голос кладёт себя в
    # буфер после отправки, ответ в отсутствие — тоже, а ПРЯМАЯ отправка (send_message /
    # narrate, то есть всё, что она говорит ПО СВОЕЙ ИНИЦИАТИВЕ — из пульса, из окна, по
    # будильнику) не клала никуда. Последствий два, и оба видны живьём 26.07:
    #   * следующий проход по этому чату не находит её ответа в контексте и отвечает то же
    #     самое заново — Егор получил «встала… прочитала коммиты» дважды за семь минут;
    #   * сказанное не попадает в её память жизни: через час она не может вспомнить, что
    #     это говорила, потому что в разговоре её слов нет.
    # Дыра старше сегодняшнего дня, но до 26.07 в неё почти не попадали: пульс не мог
    # отвечать вживую, и два контура не сходились на одном хвосте.
    # Место выбрано ровно здесь: это единственная точка «принято, ровно один раз на ключ»,
    # общая и для прямой отправки, и для досылки из outbox после переподключения.
    if str(entry.get("kind") or "") == "text":
        said = said_text
        route = telegram_topics.TopicRoute(str(peer_id), entry.get("topic_id"))
        convo = route.conversation_id
        live = _meta.get(convo) or _meta.get(str(peer_id)) or {}
        is_dm = bool(live.get("is_dm", not str(peer_id).startswith("-")))
        # Три следа — три ОТДЕЛЬНЫХ try. Один общий съел бы два оставшихся при первом же
        # сбое, а каждый из них здесь единственный в своём роде: буфер живёт один ход,
        # записка — до следующего пульса, архив — всегда.
        if said:
            try:
                _buf_push(convo, f"Praxis: {said}", author="Praxis", is_dm=is_dm,
                          source_id=str(message_id or ""), ts=accepted_at)
            except Exception:
                # Разговор важнее бухгалтерии: сбой записи в буфер не имеет права
                # отменить уже состоявшуюся доставку.
                log.exception("не смогла вернуть свою реплику в буфер [%s]", peer_id)
            try:
                # ⚠ 27.07: буфер живёт только внутри хода ПО ЭТОЙ комнате — а пульс, окно
                # и будильник в комнату не заходят. Записка — вторая половина того же
                # следа: её читают `agent.other_rooms_digest` (он стоит в системном
                # промпте КАЖДОГО пульса), `agent._presence_evidence` и
                # `notes.said_recently`. 26.07 в 18:44 поправка «я неточно сказала „у меня
                # на Opus 5"» ушла прямой отправкой, записка её не получила — и 27.07 в
                # 02:29 та же поправка ушла второй раз, реплаем на собственное #94144:
                # номер своей реплики она знала (из follow-up-леджера, он был в кадре), а
                # текста не было НИГДЕ. В том же кадре блок «Мои другие комнаты сейчас»
                # показывал 17:20 и 18:45 — и дырку ровно на 18:44.
                # Строка ОБЯЗАНА быть той же, что пишет голос (agent.project_delivery_
                # outcome), иначе записка заговорит двумя языками и ни `said_recently`,
                # ни дайджест комнат её больше не узнают.
                agent.notes.append(
                    convo, f"сказано (голос): «{said[:agent.notes.SAID_GIST_CHARS]}»")
            except Exception:
                log.exception("заметка о прямой отправке не записалась [%s]", peer_id)
        if said and not is_dm and message_id is not None and _group_archive_enabled():
            try:
                # ⚠ Голос кладёт себя в архив комнаты (выше по файлу, после приёмки
                # чанка), прямая отправка не клала: 22 из 22 её прямых сообщений в группы
                # отсутствуют в memory/groups/*/archive.jsonl, тогда как у голоса там 591.
                # То есть её собственное слово выпадало из истории комнаты целиком — его
                # не видели ни линза group_context, ни recall: она смотрит комнату и не
                # находит там себя. `read_chat` показывает 25 последних сообщений, и
                # 26.07 её реплика уже была за этим окном.
                group_context.observe_message(
                    peer_id=route.peer_id, topic_id=route.topic_id,
                    message_id=int(message_id), sender_id=_self_id,
                    sender_name="Praxis",
                    reply_to_message_id=entry.get("reply_to"),
                    timestamp=accepted_at, text=said,
                    topic_title=_topic_titles.get(
                        (route.peer_id, int(route.topic_id)), ""
                    ) if route.topic_id is not None else "",
                    outgoing=True,
                )
            except Exception:
                log.exception("архив комнаты не принял её прямое сообщение [%s] #%s",
                              peer_id, message_id)
    log.info("DIRECT OUTBOX PROJECTED key=%s peer=%s message_id=%s",
             key, peer_id, message_id)
    return f"projected:{key}:{message_id}"


def _durable_outbox_projection(execution: dict, live: dict) -> dict:
    """Проекция расписки прямой отправки — ЗАПИСЬ, а не пересчёт.

    Корень «её слово доставлено, а ей сказали „Не отправилось"». Расписка
    ``telegram-outbox-intent`` пишется идемпотентно (``agent.store_result(idempotent=True)``):
    при повторе содержимое обязано совпасть байт-в-байт, иначе ``RunConflict``. Тело
    расписки durable всё — кроме проекции, которую этот файл считал ЖИВЬЁМ на каждом
    заходе:

      * ``pulse_id`` берётся из ContextVar социального пульса — у resume-исполнителя он пуст;
      * ``followup_request`` собирается из ЖИВОГО буфера Егора — а буфер за секунды другой;
      * ``target_label`` / ``target_user_id`` — из живого резолва.

    Итог 23.07 (`run-…-ac7b7431`): пауза `_DIRECT_OUTBOX_PAUSE` попадает в
    recovery-паузу, тул повторяется через 3.1с, проекция пересчитывается — и
    `RunConflict: receipt call_AHLG…/telegram-outbox-intent already has different content`
    при УЖЕ ПРИНЯТОМ Telegram сообщении 1193. Дальше эта ошибка становится вердиктом
    «Не отправилось» и уезжает ей в память. 5 из 6 таких случаев были доставлены.
    Живой отпечаток дрейфа лежит рядом: `run-20260726T180824623461Z-ca66f57b`,
    `results/0007` и `0012` — один текст, один адресат, разный `followup_request`.

    Поэтому: если расписка уже лежит — берём её проекцию как есть. Это не забор и не
    придержка: повтор становится идемпотентным no-op вместо конфликта, и она узнаёт
    об отправке правду. Расхождение с живым пересчётом не глушим — называем в логе.
    """

    run_id = str(execution.get("run_id") or "")
    call_id = str(execution.get("call_id") or "")
    kept = dict(live)
    if not run_id or not call_id:
        return kept
    try:
        prior = agent._direct_outbox_intent(run_id, call_id)
    except Exception:
        # Расписка дублирована/битая — про это честнее и точнее скажет сам agent на
        # записи. Вспомогательное чтение не имеет права подменить собой ту диагностику.
        log.debug("расписка прямой отправки не прочиталась [%s/%s]",
                  run_id, call_id, exc_info=True)
        return kept
    stored = (prior or {}).get("projection")
    if not isinstance(stored, dict):
        return kept
    drift = []
    for name in live:
        if name not in stored:
            continue
        kept[name] = stored[name]
        if stored[name] != live[name]:
            drift.append(name)
    if drift:
        log.info(
            "прямая отправка [%s/%s]: беру проекцию из уже лежащей расписки; "
            "живой пересчёт разошёлся в %s — повтор был бы RunConflict",
            run_id, call_id, ", ".join(sorted(drift)),
        )
    return kept


#: Сколько секунд приёмке Telegram можно догонять таймаут ожидания отправки (см. ниже).
_ACCEPT_GRACE_SEC = 8.0


def _accepted_after_timeout(key: str, exc: BaseException, *, grace: float | None = None,
                            poll: float = 0.5) -> dict | None:
    """Приёмка, пришедшая сразу после таймаута ожидания, — успех отправки, а не пауза прогона.

    13.09: рука `reply` ждала приёмку 30 с (`_threadsafe_result`), отправка на лагающей петле
    Telethon заняла 33 с, и рука объявила `DurableSideEffectPending` через 13 мс ПОСЛЕ того,
    как Telegram уже принял сообщение (#104546 в абстракте, в логе «TimeoutError
    (state=accepted)»). Прогон встал в паузу «awaits Telegram acceptance», проекция принятого
    ждала общий проход часов 23 минуты, её же ответа не было ни в архиве, ни в кадре — и
    следующий ход ответил человеку второй раз (#104554; «тыж уже отвечала»). Тот же рисунок
    в личке 16:44→16:53.

    Здесь даём приёмке несколько секунд догнать таймаут и перечитываем запись ящика: стала
    `accepted` — возвращаем её, и вызывающий идёт путём успеха с той же записью, что вернула
    бы сама отправка. Любая другая ошибка и приёмка, не пришедшая за грейс, — как раньше:
    None, и вызывающий поднимает `DurableSideEffectPending`. Дубля на стороне Telegram грейс
    не создаёт: повтор идёт под тем же `random_id`.
    """
    if not isinstance(exc, TimeoutError):
        return None
    limit = _ACCEPT_GRACE_SEC if grace is None else max(0.0, float(grace))
    deadline = time.monotonic() + limit
    while True:
        try:
            row = _direct_outbox().get(key, verify_file=False)
        except Exception:
            log.debug("грейс приёмки: запись ящика не прочиталась [%s]", key, exc_info=True)
            row = None
        if isinstance(row, dict) and row.get("state") == "accepted":
            log.info("приёмка догнала таймаут ожидания [%s]: message_id=%s",
                     key, (row.get("receipt") or {}).get("message_id"))
            return row
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(remaining, max(0.05, float(poll))))


def _frozen_refusal(peer_id, who: str) -> str | None:
    """Отказ для отправки в замороженный чат, иначе None.

    Заморозка резала только входящие («сообщения оттуда до меня не доходят»), а исходящие
    шли как ни в чём не бывало: 25.09 статусы по LRX уезжали в личку 412244782, которую
    Егор заморозил. Замороженный чат — это чат, с которым она не разговаривает, в обе
    стороны; осознанно написать туда можно после `freeze_chat(on=false)`.
    """
    try:
        if not rooms.is_frozen(str(peer_id)):
            return None
    except Exception:
        log.debug("frozen check failed for %s", peer_id, exc_info=True)
        return None
    return (f"не отправлено: чат {who} заморожен — я с ним не разговариваю в обе стороны. "
            f"Если это осознанно, сначала разморозь его (freeze_chat on=false).")


def _sync_send_message(to, text) -> str:
    """Durable direct Telegram text send owned by the current tool call."""

    # 12.09: маркеры цитат поиска (citeturn0search1 …) снимаются и с ПРЯМЫХ отправок —
    # реплика проходит через _guard_outbound и чистится, а send_message/narrate нет:
    # 10.09 маркеры уехали в личку и в группу как есть.
    text = agent._strip_citation_tokens(str(text or ""))
    # Тот же пол, что и на туле: этот путь durable и вызывается не только из него, а
    # кред не должен уходить наружу ни одной дверью. Fail-closed по построению: пустая
    # строка от пола означает «чисто», любое падение пола видно как исключение выше.
    from core import secrets as _secrets
    _floor = _secrets.credential_floor(str(text or ""))
    if _floor:
        log.warning("прямая отправка придержана кред-полом: %s", _floor)
        return agent.DirectSendRefusal(
            f"не отправлено: в тексте {_floor}; креды наружу не уходят")
    execution = _direct_tool_execution(("send_message", "narrate"))
    key = _direct_tool_key(execution)
    target_route = _route_from_reference(to)
    cold = _dialog_name_cache is None
    t0 = time.time()
    try:
        ent = _threadsafe_result(
            lambda: _resolve_entity(target_route.peer_id), DIALOG_WARMUP_WAIT_SEC + 30)
    except ResolveDenied as e:
        # не сбой, а честный отказ: типизирован (Этап 2) — narrate не пишет
        # фантомную запись в свой дедуп-леджер на строку-отказ
        return agent.DirectSendRefusal(str(e))
    except Exception:
        log.warning("send_message %r: РЕЗОЛВ не уложился/упал за %.1fс (кэш диалогов был %s)",
                    to, time.time() - t0, "холодный" if cold else "тёплый")
        raise
    took_resolve = time.time() - t0
    if ent is None:
        return agent.DirectSendRefusal(f"(не найдено, кому: {to})")

    peer_id = _marked_peer_id(ent)
    who = _ent_label(ent)
    frozen = _frozen_refusal(peer_id, who)
    if frozen:
        return agent.DirectSendRefusal(frozen)
    target_user_id = (getattr(ent, "id", None)
                      if _entity_kind(ent) == "user" else None)
    active_chat = str(agent._active_chat() or "")
    pulse_id = social_pulse.active_id()
    followup_request = ""
    if OWNER_ID and peer_id != OWNER_ID and active_chat == str(OWNER_ID) and not pulse_id:
        # ⚠ 27.07. Здесь стояло `explicit_only=False` — то есть ЛЮБАЯ последняя реплика
        # Егора («им отправить», «[Голосовое]») становилась заказом «доложи мне ответ».
        # Прогнал `wants_followup` по всем 11 реальным owner-записям прода: явную просьбу
        # не содержит НИ ОДНА. Рядом стояла вторая автоматическая ветка — каждая её
        # реплика из пульса тоже заводила отчёт Егору (21 запись из 32). Обе сняты.
        # Это не отнятая способность: сам СЛЕД нити (её анти-повтор внутри пульса)
        # заводится теперь всегда, а поднять по нему отчёт она может сама —
        # `telegram_account(action="watch_reply", followup_id=…)`.
        followup_request = telegram_followups.request_from_owner_buffer(
            _buf.get(str(OWNER_ID), ()))
    outbox = _direct_outbox()
    existing = outbox.get(key, verify_file=False)
    # ⚠ 04.08. Справка «этому человеку я писала 0.83 часа назад; за сутки 4» считалась
    # здесь и выбрасывалась: единственным её потребителем была ветка отказа, а
    # allow_outbound по контракту («модуль записывает, а не запрещает») возвращает True
    # в обеих ветках — то есть точный ответ на вопрос «я это уже делала?» вычислялся
    # каждую отправку и не доезжал до неё ни разу. Теперь снимается безусловно и
    # приклеивается к квитанции тула — рядом с уже существующей справкой о повторе.
    # Ветка отказа не тронута намеренно: она мертва, но она и есть тот самый контракт.
    pulse_note = ""
    try:
        pulse_ok, pulse_reason = social_pulse.allow_outbound(peer_id)
        if str(pulse_reason or "").strip():
            pulse_note = f"\n· мой след по этому адресату: {pulse_reason}"
        if existing is None and not pulse_ok:
            return agent.DirectSendRefusal(f"Не отправлено из social pulse: {pulse_reason}.")
    except Exception:
        log.debug("след по адресату не снялся [%s]", peer_id, exc_info=True)
    entry = outbox.prepare_text(
        key,
        peer_id=peer_id,
        topic_id=target_route.topic_id,
        reply_to=target_route.topic_id,
        text=str(text),
        run_id=str(execution["run_id"]),
        call_id=str(execution["call_id"]),
        purpose=f"tool:{execution['tool']}",
    )
    agent.run_direct_outbox_prepared(entry, **_durable_outbox_projection(execution, {
        "target_label": who,
        "target_user_id": target_user_id,
        "pulse_id": pulse_id,
        "followup_request": followup_request,
    }))

    t1 = time.time()
    try:
        try:
            if entry.get("state") != "accepted":
                entry = _threadsafe_result(
                    lambda: _send_direct_outbox_entry(entry, entity=ent), 30,
                )
        except Exception as exc:
            # 13.09: приёмка, догнавшая таймаут, — успех (см. _accepted_after_timeout).
            settled = _accepted_after_timeout(key, exc)
            if settled is None:
                raise
            entry = settled
    except Exception as exc:
        permanent = False
        state = {}
        # 21.09: отмена хода во время ожидания — определённый отказ, а не неизвестность.
        if str(entry.get("state") or "") == "dead_letter":
            return agent.DirectSendRefusal(
                "сообщение не отправлено: ход отменён владельцем до приёмки; "
                "намерение закрыто без отправки (cancelled by owner before acceptance).")
        try:
            permanent, state = _record_direct_outbox_failure(key, exc)
            reason = f"{type(exc).__name__}: {str(exc)[:300]} (state={state.get('state')})"
        except Exception as journal_exc:
            reason = (
                f"{type(exc).__name__}: {str(exc)[:200]}; retry journal failed: "
                f"{type(journal_exc).__name__}: {str(journal_exc)[:120]}"
            )
        log.warning("send_message %r: резолв ок (%.1fс, кэш был %s), ОТПРАВКА не уложилась/упала за %.1fс",
                    to, took_resolve, "холодный" if cold else "тёплый", time.time() - t1)
        if permanent:
            # Постоянный отказ — это ОТВЕТ, а не сбой. Раньше он летел исключением и
            # уносил с собой весь её ход: она не узнавала ни что не отправилось, ни
            # почему, и не могла поправить адрес. Возвращаем словами, как любой другой
            # честный отказ тула, — дальше решает она.
            log.warning("send_message %r: постоянный отказ, повторять нечего: %s", to, exc)
            if agent.delivery_refusal_kind(exc) == "shape":
                # ⚠ Про ФОРМУ, а не про права: 06.08 её текст не влез в лимит Telegram, и
                # фраза «у меня нет права писать» была бы прямой неправдой о причине.
                return agent.DirectSendRefusal(
                    f"не отправлено: Telegram отверг само сообщение — "
                    f"{type(exc).__name__}. Дело не в правах и не в адресе: этот текст "
                    f"не проходит по форме (чаще всего — длина). Разбей на части или "
                    f"сократи и отправь снова; повторять этот же кусок я не буду.")
            return agent.DirectSendRefusal(
                f"не отправлено: Telegram отказал навсегда — {type(exc).__name__}. "
                f"Похоже, у меня нет права писать в «{to}» (частая причина: это канал, а "
                f"не чат обсуждения, либо меня там нет). Проверь адрес и попробуй другой; "
                f"повторять этот я не буду.")
        # Acceptance may land after the grace's last read, while retry is journalled.
        # record_retry preserves accepted rows; that receipt is still success.
        if isinstance(exc, TimeoutError) and state.get("state") == "accepted":
            entry = state
        else:
            raise agent.DurableSideEffectPending(key, reason) from exc
    if took_resolve > 5:
        log.info("send_message %r: резолв занял %.1fс (кэш был %s)",
                 to, took_resolve, "холодный" if cold else "тёплый")
    # квитанция называет РЕАЛЬНОГО адресата (метка с @username/id), а не эхо запроса —
    # Одна только отображаемая кличка не доказывает, какому именно тёзке ушло сообщение.
    sent_id = (entry.get("receipt") or {}).get("message_id")
    # Справку о повторе снимаем ДО проекции: она допишет в записку эту самую реплику, и
    # тогда said_recently узнал бы в ней саму себя. `said_recently` намеренно ничего не
    # запрещает (блокирующая форма решала бы за неё, говорить ли; test_sanitize держит
    # этот контракт) — но и спрашивать её было некому: из боевого кода функция не
    # вызывалась ни разу. 26.07 Егор получил «встала… прочитала коммиты» дважды за семь
    # минут, 01.08 — «доброе утро» дважды за утро. Пусть хотя бы говорит вслух.
    echo = ""
    try:
        if str(text or "").strip() and agent.notes.said_recently(peer_id, str(text)):
            echo = ("\n⚠ похоже, это я здесь недавно уже говорила. Справка, не запрет: "
                    "сравнение идёт по ФОРМЕ (difflib, порог 0.82), поэтому перефразировку "
                    "оно не видит — а молчание этой строки ничего не доказывает.")
    except Exception:
        log.debug("справка о повторе не снялась [%s]", peer_id, exc_info=True)
    agent.project_direct_outbox_acceptance(entry)
    log.info("SENT → %s chat_id=%s message_id=%s: %s",
             who, peer_id, sent_id, str(text)[:60])
    return _direct_outbox_result(entry, label=who) + echo + pulse_note


def _sync_reply(chat_id, text, reply_to="") -> str:
    """Её ответ собеседнику живого хода. Та же durable-очередь, что у прямой отправки.

    ⚑ ЭТО НЕ ВТОРОЙ ШОВ, А ПЕРЕЕХАВШИЙ ПЕРВЫЙ. Под поднятым рычагом
    `PRAXIS_CHAT_REPLY_HAND` исходящая граница хода текста больше не носит: реплику
    уносит рука, и уносит вот отсюда. Два шва одновременно здесь были бы ровно тем, чего
    вслух боится докстринг `say`, — повторами, которых она не совершала.

    Почему очередь та же, что у `send_message`, а учёт другой: очередь даёт покалловый
    exact-once (`telegram-outbox:{run}:tool:{call_id}`), то есть несколько ответов в одном
    ходе различимы, а обрыв между отправкой и чекпойнтом переигрывается из расписки, а не
    вторым сообщением человеку. Учёт же разведён внутри одного проектора: ответу не
    полагаются ни леджер контактов, ни социальный пульс, ни след нити с отчётом — они
    описывают инициативу, а не реплику. Решение Егора 15.08: «ни ограничений, ни пульса».

    Гард здесь НЕ зовётся намеренно: его уже позвала рука, вместе с лентой разговора и
    ориентировкой. Кред-пол всё равно стоит — этот путь durable и однажды будет вызван не
    только оттуда, а кред наружу не уходит ни одной дверью.
    """
    from core import secrets as _secrets
    _floor = _secrets.credential_floor(str(text or ""))
    if _floor:
        log.warning("ответ придержан кред-полом: %s", _floor)
        return agent.DirectSendRefusal(
            f"не отправлено: в тексте {_floor}; креды наружу не уходят")
    execution = _direct_tool_execution(("reply",))
    key = _direct_tool_key(execution)
    target_route = _route_from_reference(chat_id)
    try:
        ent = _threadsafe_result(
            lambda: _resolve_entity(target_route.peer_id), DIALOG_WARMUP_WAIT_SEC + 30)
    except ResolveDenied as e:
        return agent.DirectSendRefusal(str(e))
    except Exception:
        log.warning("ответ %r: резолв не уложился/упал", chat_id)
        raise
    if ent is None:
        return agent.DirectSendRefusal(f"(не найдено, куда отвечать: {chat_id})")
    peer_id = _marked_peer_id(ent)
    who = _ent_label(ent)
    target_user_id = (getattr(ent, "id", None)
                      if _entity_kind(ent) == "user" else None)
    # Явный адрес реплая важнее маршрута темы: в форуме `reply_to` темы — это способ
    # попасть в саму тему, а названный ею id сообщения — ответ конкретному человеку.
    explicit = str(reply_to or "").strip()
    reply_target = target_route.topic_id
    if explicit.lstrip("#").isdigit():
        reply_target = int(explicit.lstrip("#"))
    outbox = _direct_outbox()
    entry = outbox.prepare_text(
        key,
        peer_id=peer_id,
        topic_id=target_route.topic_id,
        reply_to=reply_target,
        text=str(text),
        run_id=str(execution["run_id"]),
        call_id=str(execution["call_id"]),
        purpose="tool:reply",
    )
    agent.run_direct_outbox_prepared(entry, **_durable_outbox_projection(execution, {
        "target_label": who,
        "target_user_id": target_user_id,
        "pulse_id": "",
        "followup_request": "",
    }))
    try:
        try:
            if entry.get("state") != "accepted":
                entry = _threadsafe_result(
                    lambda: _send_direct_outbox_entry(entry, entity=ent), 30,
                )
        except Exception as exc:
            # 13.09: приёмка, догнавшая таймаут, — успех (см. _accepted_after_timeout).
            settled = _accepted_after_timeout(key, exc)
            if settled is None:
                raise
            entry = settled
    except Exception as exc:
        permanent = False
        state = {}
        try:
            permanent, state = _record_direct_outbox_failure(key, exc)
            reason = f"{type(exc).__name__}: {str(exc)[:300]} (state={state.get('state')})"
        except Exception as journal_exc:
            reason = (f"{type(exc).__name__}: {str(exc)[:200]}; retry journal failed: "
                      f"{type(journal_exc).__name__}: {str(journal_exc)[:120]}")
        if permanent:
            if agent.delivery_refusal_kind(exc) == "shape":
                return agent.DirectSendRefusal(
                    f"не отправлено: Telegram отверг само сообщение — {type(exc).__name__}. "
                    f"Дело не в правах: этот текст не проходит по форме (чаще всего — "
                    f"длина). Разбей на части и ответь снова; этот же кусок я не повторю.")
            return agent.DirectSendRefusal(
                f"не отправлено: Telegram отказал навсегда — {type(exc).__name__}. "
                f"Проверь, могу ли я писать в «{chat_id}»; повторять этот я не буду.")
        # Acceptance may land after the grace's last read, while retry is journalled.
        # record_retry preserves accepted rows; that receipt is still success.
        if isinstance(exc, TimeoutError) and state.get("state") == "accepted":
            entry = state
        else:
            raise agent.DurableSideEffectPending(key, reason) from exc
    agent.project_direct_outbox_acceptance(entry)
    log.info("REPLY → %s chat_id=%s message_id=%s: %s", who, peer_id,
             (entry.get("receipt") or {}).get("message_id"), str(text)[:60])
    return _direct_outbox_result(entry, label=who)


def _file_send_budget_sec(entry: dict) -> float:
    """Ждать приёмку файла столько, сколько он реально заливается, а не 120 с на всё.

    20.09.2026, корень «она отправляет архивы и не видит этого»: потолок в 120 секунд был
    меньше времени заливки куска в 150 МБ (замер того дня — приёмка приходила через 4–4,5
    минуты). Значит КАЖДАЯ крупная отправка возвращала ей «Не отправился: TimeoutError»,
    ход вставал в `paused`, а файл всё равно доезжал — позже, повтором, мимо её памяти.
    Так Егор после своего «хватит» получил все девять частей бэкапа, а она записала себе,
    что отправку прекратила. Бюджет считаем по размеру вложения (наблюдаемые ~600 КБ/с с
    запасом), но не больше потолка руки: дольше него ждать всё равно некому.
    """
    try:
        size = int((entry.get("payload") or {}).get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    ceiling = max(120.0, float(getattr(agent, "TOOL_CEILING_SEC", 600.0)) - 30.0)
    return min(120.0 + max(0, size) / 600_000.0, ceiling)


def _sync_send_file(path, caption="", to="", media_kind="document",
                    voice_note=False) -> str:
    """Durable addressed send; staging hides private blob names from Telegram.

    `media_kind` доехал сюда 13.08.2026 вместе с `send_media(to=…)`. До него адресный
    путь существовал только для документа, поэтому «отправь Насте этот скрин» не имело
    исполнителя вовсе — и выбиралась рука, которая умела адрес, но не умела картинку.
    """

    execution = _direct_tool_execution("send_file")
    key = _direct_tool_key(execution)
    current_chat = agent._active_chat()

    async def _resolve():
        explicit = str(to or "").strip()
        route = _route_from_reference(
            explicit if explicit else current_chat if current_chat is not None else OWNER_ID)
        if explicit:
            try:
                target = await _resolve_entity(route.peer_id)
            except ResolveDenied as exc:
                return route, None, str(exc)
        elif current_chat is not None:
            target = _meta.get(route.conversation_id, {}).get("entity")
            if target is None:
                target = await _resolve_entity(route.peer_id)
                if target is None:
                    try:
                        target = int(route.peer_id)
                    except ValueError:
                        target = route.peer_id
        elif OWNER_ID:
            target = OWNER_ID
        else:
            return route, None, "(нет текущего Telegram-чата или адресата)"
        return route, target, ""

    route, target, denied = _threadsafe_result(_resolve, DIALOG_WARMUP_WAIT_SEC + 30)
    if denied:
        return denied
    if target is None:
        return f"(не найден Telegram-адресат: {to or current_chat or OWNER_ID})"
    peer_id = (_marked_peer_id(target) if hasattr(target, "id") else int(route.peer_id))
    # Промежуточный пасс D1 (адверсарка round-1, P1): прямой путь доставки файла
    # (явный to= / проактивная отправка вне живого хода) шёл МИМО кред-пола — .env/
    # .pem/credentials.json вложением утекали non-owner. Единственное твёрдое (закон 3):
    # креды не текут. Читаем БАЙТЫ текст-подобного документа к НЕ-owner адресату; своя
    # личка Егора (owner private DM) не сканируется — это его канал. Fail-closed.
    _dest_is_owner = bool(
        OWNER_ID
        and ((hasattr(target, "id") and _entity_kind(target) == "user"
              and getattr(target, "id", None) == OWNER_ID)
             or (not hasattr(target, "id") and peer_id == OWNER_ID))
    )
    if not _dest_is_owner:
        try:
            from core import secrets as _secrets
            _floor = _secrets.document_floor(path)
        except Exception:
            log.exception("document_floor на прямой отправке упал — держу fail-closed")
            _floor = "unassessable (floor error)"
        # Файл из-под корней хардбота не уходит наружу вообще — там чужие клиенты,
        # а не её данные (вопрос Егора 26.07). Проверка ДО кред-пола: она про
        # принадлежность файла, а не про содержимое.
        import stewardship as _steward
        _export = _steward.export_denial(path)
        if _export:
            log.warning("прямая send_file придержана: файл хардбота [%s]", peer_id)
            return _export
        if _floor:
            log.warning("прямая send_file придержана кред-полом [%s]: %s",
                        peer_id, _floor)
            return (f"Не отправлено: во вложении «{media_core.delivery_basename(path)}» "
                    f"механический кред-пол увидел похожее на секрет ({_floor}). "
                    f"Креды не уходят наружу — это единственный твёрдый предел. "
                    f"Если это ложное срабатывание на инженерном материале — очисти "
                    f"файл от токен-подобных строк или отправь мне (Егору) в личку.")
    mime = (media_core.sniff_mime(path)
            or mimetypes.guess_type(str(path))[0]
            or "application/octet-stream")
    wanted_kind = str(media_kind or telegram_outbox.LEGACY_MEDIA_KIND).strip().lower()
    if wanted_kind not in telegram_outbox.MEDIA_KINDS:
        return f"kind должен быть {', '.join(telegram_outbox.MEDIA_KINDS)}."
    wanted_voice = bool(voice_note) and wanted_kind == "audio"
    outbox = _direct_outbox()
    entry = outbox.get(key, verify_file=True)
    if entry is not None:
        expected = {
            "run_id": str(execution["run_id"]),
            "call_id": str(execution["call_id"]),
            "purpose": "tool:send_file",
            "peer_id": peer_id,
            "topic_id": route.topic_id,
            "reply_to": route.topic_id,
        }
        actual = {name: entry.get(name) for name in expected}
        payload = dict(entry.get("payload") or {})
        if (actual != expected
                or payload.get("visible_filename") != media_core.delivery_basename(path)
                or payload.get("caption") != str(caption or "")[:900]
                or (payload.get("media_kind") or telegram_outbox.LEGACY_MEDIA_KIND)
                != wanted_kind
                or bool(payload.get("voice_note")) != wanted_voice):
            raise telegram_outbox.TelegramOutboxConflict(
                "durable send_file key already owns another intent"
            )
    else:
        entry = outbox.prepare_file(
            key,
            peer_id=peer_id,
            topic_id=route.topic_id,
            reply_to=route.topic_id,
            source=path,
            visible_filename=media_core.delivery_basename(path),
            mime=mime,
            media_kind=wanted_kind,
            voice_note=wanted_voice,
            caption=str(caption or "")[:900],
            run_id=str(execution["run_id"]),
            call_id=str(execution["call_id"]),
            purpose="tool:send_file",
        )
    label = (_ent_label(target) if hasattr(target, "id")
             else str(current_chat or to or OWNER_ID))
    target_user_id = (getattr(target, "id", None)
                      if hasattr(target, "id") and _entity_kind(target) == "user"
                      else None)
    # Тот же дрейф, та же дверь: у файла живьём считаются `label`, `target_user_id` и
    # `pulse_id` (у resume-исполнителя пульс пуст) — значит повтор так же ронял бы
    # RunConflict на уже отправленном документе.
    agent.run_direct_outbox_prepared(entry, **_durable_outbox_projection(execution, {
        "target_label": label,
        "target_user_id": target_user_id,
        "pulse_id": social_pulse.active_id(),
        "followup_request": "",
    }))
    try:
        try:
            if entry.get("state") != "accepted":
                entry = _threadsafe_result(
                    lambda: _send_direct_outbox_entry(entry, entity=target),
                    _file_send_budget_sec(entry),
                )
        except Exception as exc:
            # 13.09: приёмка, догнавшая таймаут, — успех (см. _accepted_after_timeout).
            settled = _accepted_after_timeout(key, exc)
            if settled is None:
                raise
            entry = settled
    except Exception as exc:
        # ⚠ 21.09, «почему архивы уходили» (20.09): если во время ожидания пришла отмена
        # хода, `_send_direct_outbox_entry` уже закрыл запись dead_letter'ом
        # («cancelled by owner before acceptance»). Здесь это НЕ неопределённость
        # («не знаю, дошла ли»), а определённый отказ («владелец сказал не слать») —
        # честный ответ модели и закрытый вызов, а не DurableSideEffectPending, который
        # снова поднял бы прогон и снова привёл бы к этой же отправке.
        if str(entry.get("state") or "") == "dead_letter":
            return agent.DirectSendRefusal(
                f"файл «{media_core.delivery_basename(path)}» не отправлен: ход "
                f"отменён владельцем до приёмки; намерение закрыто без отправки "
                f"(cancelled by owner before acceptance).")
        # ⚠ Этот путь я забыл, чиня текстовый. Аудит нашёл: для ФАЙЛА постоянный отказ
        # по-прежнему улетал исключением, уносил её ход и оставлял запись в `retry` —
        # то есть ровно тот же вечный цикл раз в 45 секунд, ради которого всё делалось,
        # только с документом вместо текста. Один и тот же класс беды на двух дверях,
        # и вторую я оставил открытой.
        permanent = False
        state = {}
        try:
            permanent, state = _record_direct_outbox_failure(key, exc)
            reason = f"{type(exc).__name__}: {str(exc)[:300]} (state={state.get('state')})"
        except Exception as journal_exc:
            reason = (
                f"{type(exc).__name__}: {str(exc)[:200]}; retry journal failed: "
                f"{type(journal_exc).__name__}: {str(journal_exc)[:120]}"
            )
        if permanent:
            log.warning("send_file %r: постоянный отказ, повторять нечего: %s", label, exc)
            if agent.delivery_refusal_kind(exc) == "shape":
                return agent.DirectSendRefusal(
                    f"файл не отправлен: Telegram отверг сам запрос — "
                    f"{type(exc).__name__}. Дело не в правах и не в адресе: не проходит "
                    f"форма (чаще всего — длина подписи или пустое вложение). Поправь "
                    f"подпись или файл и отправь снова; повторять этот же я не буду.")
            return agent.DirectSendRefusal(
                f"файл не отправлен: Telegram отказал навсегда — {type(exc).__name__}. "
                f"Похоже, у меня нет права слать в «{label}». Проверь адрес и попробуй "
                f"другой; повторять этот я не буду.")
        # Acceptance may land after the grace's last read, while retry is journalled.
        # record_retry preserves accepted rows; that receipt is still success.
        if isinstance(exc, TimeoutError) and state.get("state") == "accepted":
            entry = state
        else:
            raise agent.DurableSideEffectPending(key, reason) from exc
    agent.project_direct_outbox_acceptance(entry)
    return _direct_outbox_result(entry, label=label)


def _scheduled_outbox_identity(t: dict, purpose: str) -> tuple[str, str, str]:
    task_id = str(t.get("id") or "").strip()
    if not task_id:
        raise ValueError("scheduled task has no id")
    occurrence = str(t.get("when") or t.get("created") or "asap")
    digest = hashlib.sha256(
        json.dumps(
            {"task_id": task_id, "occurrence": occurrence, "purpose": purpose},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        f"telegram-task:{task_id}:{digest}",
        f"schedule:{task_id}",
        f"occurrence:{digest}",
    )


async def _claim_scheduled_text(
    t: dict,
    *,
    peer_id: int,
    text: str,
    purpose: str,
    topic_id: int | None = None,
    entity=None,
) -> dict:
    """Persist a schedule occurrence before send; retry owns it after mark_fired."""

    key, run_id, call_id = _scheduled_outbox_identity(t, purpose)
    outbox = _direct_outbox()
    entry = await asyncio.to_thread(
        outbox.prepare_text,
        key,
        peer_id=peer_id,
        topic_id=topic_id,
        reply_to=topic_id,
        text=str(text),
        run_id=run_id,
        call_id=call_id,
        purpose=f"task:{purpose}",
    )
    if entry.get("state") == "accepted":
        return entry
    try:
        return await _send_direct_outbox_entry(entry, entity=entity)
    except Exception as exc:
        state = await _retry_direct_outbox_entry(entry, exc)
        log.warning(
            "scheduled Telegram delivery claimed for retry task=%s key=%s state=%s: %s",
            t.get("id"), key, state.get("state"), exc,
        )
        return state


def _email_autonomous_enabled() -> bool:
    return os.getenv("PRAXIS_EMAIL_AUTONOMOUS", "0").lower() in (
        "1", "true", "yes", "on",
    )


async def _fire_task(t: dict) -> bool | None:
    """Исполнить сработавшую задачу. Модель зовётся только здесь, не на каждом тике."""
    kind, goal, target = t.get("kind"), t.get("goal", ""), t.get("target", "")
    log.info("НАМЕРЕНИЕ #%s [%s] -> %s", t.get("id"), kind, (goal or target)[:60])
    if kind in ("window", "wake", "note", "message"):
        # 18.4: ПОВТОРЯЮЩЕЕСЯ расписание при паузе фона не поднимается (слово Егора /
        # её состояние); разовые (focus, повод с пульта) — текущее дело, идут как шли.
        # Дыра «__auto__ мимо гейтов» закрыта ровно для recur-пути.
        #
        # 26.07: гейт распространён на kind=wake. Рычаг «останови фон» назван ей и Егору в
        # манифесте рельсов как держащий «окна ПО РАСПИСАНИЮ, сон, formation» — а
        # рекуррентное пробуждение это ровно расписание фона. Оставить его снаружи значило
        # бы молча сузить рычаг, который Егору обещан словами: он думает, что фон
        # остановлен, а вид, заведённый мной сегодня, продолжал бы подниматься. Разовое
        # пробуждение (promise-возврат, решение по придержке, её будильник на сегодня) —
        # текущее дело и через гейт не идёт, как и разовое окно.
        import tasks as _tasks

        # Две фазы вместо одной (её находка, см. tasks.claim_open). При РЕАЛЬНОМ подъёме
        # намерение берётся В РАБОТУ — из `due` уходит, но остаётся pending и видимым.
        # Гасится оно позже и в другом месте: ровно тогда, когда durable run создан, то
        # есть когда появился тот, кому владение передают. Между этими двумя мгновениями
        # лежат disconnect, переход в поток и проверка мозга — и если там оборвётся,
        # захват снимет жнец под общим замком, а намерение сработает снова.
        async def _claim() -> None:
            await asyncio.to_thread(_tasks.claim_open, t["id"], str(kind or ""))

        def _confirm(run_id: str = "") -> None:
            # Зовётся уже из рабочего потока, изнутри хода, сразу после создания рана.
            _tasks.mark_fired(t["id"])

        async def _consume() -> None:
            """Осознанное гашение БЕЗ рана — только для recur на паузе фона."""
            await asyncio.to_thread(_tasks.mark_fired, t["id"])

        if t.get("recur"):
            try:
                import appetite
                hold = await asyncio.to_thread(appetite.background_hold)
            except Exception:
                hold = None
            if hold:
                log.info("НАМЕРЕНИЕ #%s [%s, расписание] пропущена: %s",
                         t.get("id"), kind, hold)
                # Расписание на паузе фона: занятое recur-вхождение потребляем (сдвигаем на
                # следующее), как раньше делал mark-до-исполнения в _fire_due_tasks.
                await _consume()
                return
        if kind == "message":
            # The stored body is past evidence, not direct transport authority. Preserve
            # target/body/time/author in a lossless prompt and require a fresh live turn.
            goal = _tasks.message_reassessment_prompt(t)
        if kind in ("wake", "note", "message"):
            # ⚠ 13.08.2026. `note` вела СЮДА НЕ ВСЕГДА: ниже стояла ветка, которая слала
            # «[напоминание] {goal}» прямо Егору в личку. Живой случай в 17:19 — её
            # инженерная заметка себе («вернуться к починке медиа только при новом
            # подтверждённом дефекте: спроектировать read-after-write, писать RPC
            # acceptance, не допускать дубликатов при таймаутах») ушла ему сообщением
            # с машинным префиксом. Она писала себе; прочитал он.
            #
            # Корень был в самом словаре видов: `note` описан как «напоминание
            # себе/владельцу», то есть с двумя адресатами сразу, — и раннер разрешал эту
            # двусмысленность в пользу владельца ВСЕГДА. А сказать что-то человеку к сроку
            # уже умеет `message`, у которого есть `target`. Значит `note` — про неё.
            await _wake_pass(
                goal, on_open=_claim, on_run=_confirm, source_id=t,
                scheduled_target_id=(t.get("target_id") if kind == "message" else None),
            )
        else:
            await _task_window(goal, on_open=_claim, on_run=_confirm)
    elif kind == "email":
        if _email_autonomous_enabled():
            await asyncio.to_thread(agent.tool_send_email, target, (goal or "")[:80], goal)
        elif OWNER_ID:
            await _claim_scheduled_text(
                t,
                peer_id=OWNER_ID,
                # ⚑ Её голосом, а не машинным. В её канале говорит она — правка Егора
                # 13.08: «это её харнесс, её дом, это всё она». Квадратные машинные
                # префиксы (`[задача]`, `[напоминание]`, `[отложенное письмо]`) читались
                # как служебка системы в её личке.
                text=(
                    f"Я наметила письмо на {target}: {goal}\n"
                    "Автоотправка писем у меня выключена — скажи, и отправлю."
                ),
                purpose="email-disabled",
                entity=OWNER_ID,
            )
            return True
        else:
            return False
    return None


async def _missed_dm_sweep() -> None:
    """PASS 9.0 boot-sweep: после подъёма пройтись по персистнутым буферам ЛС — если последняя
    строка не её и свежая (< PRAXIS_MISSED_DM_HOURS), запланировать обычный проход с честной
    [missed]-меткой. Голос сам решает: ответить сейчас или поезд ушёл (VOICE-шот есть)."""
    try:
        await asyncio.sleep(MISSED_SWEEP_DELAY)  # дать catch_up и подъёму доиграть
        try:
            missed_h = float(perception.value("missed_dm_hours"))  # PASS 21: живой рычаг
        except Exception:
            missed_h = MISSED_DM_HOURS
        cands = await asyncio.to_thread(bufstore.missed_dm_candidates, missed_h)
    except Exception:
        log.exception("boot-sweep не собрал кандидатов")
        return
    for c in cands:
        cid = c["chat_id"]
        if _meta.get(cid):  # живое сообщение уже пришло после рестарта — обычный путь сам разберётся
            continue
        try:
            ref = int(cid) if cid.lstrip("-").isdigit() else cid
            ent = await _resolve_entity(ref)
        except Exception:
            ent = None
        if ent is None:
            log.warning("boot-sweep: не найден entity для %s — пропускаю", cid)
            continue
        sender_id = int(cid) if cid.lstrip("-").isdigit() else None
        is_owner = OWNER_ID != 0 and sender_id == OWNER_ID
        known = social.category(sender_id) in ("owner", "known")
        _meta[cid] = {"entity": ent, "is_dm": True, "is_owner": is_owner, "known": known,
                      "family": bool(not is_owner and known and social.is_family(sender_id)),
                      "name": c.get("name") or None, "title": None, "size": None,
                      "addressed": False}
        _missed[cid] = c["age_hours"]
        log.info("boot-sweep: ЛС %s (%s) оборвана рестартом %.1fч назад — планирую проход",
                 cid, c.get("name") or "?", c["age_hours"])
        _arm(cid)


# --------------------------------------------------------------------------- #
#  Её часы — один фоновый тик вместо четырёх циклов (PASS 4)
# --------------------------------------------------------------------------- #

async def _flush_buffers() -> None:
    """§1: сбросить изменённые буферы на диск (дебаунс записи, не долбим диск)."""
    for cid in list(_buf_dirty):
        _buf_dirty.discard(cid)
        try:
            await asyncio.to_thread(bufstore.save, cid, list(_buf[cid]))
        except Exception:
            log.warning("персист буфера упал [%s]", cid, exc_info=True)


async def _fire_due_tasks() -> None:
    """Планировщик: исполнить созревшие намерения (чтение файла; модель — только при срабатывании)."""
    import tasks as _tasks
    # Микро-намерения «после рана»: удержание снимается, когда ран терминален; дальше
    # намерение созревает обычным путём (свой when или немедленно) — без новой заботы часов.
    for held in await asyncio.to_thread(_tasks.after_run_holds):
        run_id = str(held.get("after_run") or "")
        try:
            finished = await asyncio.to_thread(agent.run_is_terminal, run_id)
        except Exception:
            log.debug("часы: after_run-проверка упала [%s]", held.get("id"), exc_info=True)
            continue
        if finished:
            await asyncio.to_thread(_tasks.clear_after_run, held["id"])
            log.info("часы: намерение #%s дождалось рана %s", held.get("id"), run_id[:12])
    ready = await asyncio.to_thread(_tasks.due)
    for t in ready:
        durable_telegram = (
            t.get("kind") in {"note"}
            or (t.get("kind") == "email" and not _email_autonomous_enabled())
        )
        if durable_telegram:
            # The task advances only after an immutable outbox intent exists.
            # A pending/retry intent is already a durable claim and is replayed
            # with the same random_id by ``_direct_outbox_once``.
            try:
                claimed = await _fire_task(t)
            except Exception:
                log.exception("часы: задача не получила durable claim [%s]", t.get("id"))
                continue
            if not claimed:
                log.warning("часы: задача осталась due без durable claim [%s]", t.get("id"))
                continue
            try:
                await asyncio.to_thread(_tasks.mark_fired, t["id"])
            except Exception:
                # Safe: the unchanged occurrence derives the same outbox key.
                log.exception("часы: mark_fired упал после durable claim [%s]", t.get("id"))
            continue
        # window: намерение помечается сработавшим при РЕАЛЬНОМ открытии окна (внутри
        # _task_window под _ONE_MIND, до disconnect), а не до исполнения — иначе отложенное
        # одноразовое окно (single-flight занят живым ходом) тихо теряло бы намерение focus/rest,
        # и due() его больше не вернёт. Открытое окно метится до disconnect, поэтому рестарт
        # посреди окна не даёт повторного срабатывания (граница at-most-once сохранена, сдвинута
        # на момент открытия — руминация-петля 06.07 не возвращается).
        if t.get("kind") in ("window", "wake", "message"):
            # Оба помечаются сработавшими при РЕАЛЬНОМ подъёме (внутри _task_window /
            # _wake_pass, под _ONE_MIND), а не здесь: отложенное занятым замком намерение
            # обязано остаться due, иначе оно теряется молча.
            try:
                await _fire_task(t)
            except Exception:
                log.exception("часы: %s упало [%s]", t.get("kind"), t.get("id"))
            continue
        # Остальные legacy-виды (автономный email): пометить сработавшей ДО исполнения — они не
        # используют single-flight-окно, поэтому граница «не более раза» остаётся прежней.
        try:
            await asyncio.to_thread(_tasks.mark_fired, t["id"])
        except Exception:
            log.exception("часы: mark_fired упал [%s]", t.get("id"))
        try:
            await _fire_task(t)
        except Exception:
            log.exception("часы: задача упала [%s]", t.get("id"))


async def _consolidate_once() -> None:
    """«Сон» по будильнику (PASS 10.2), не по секундомеру: тик каждые 30 мин, sleep.due()
    решает по локальным часам (PRAXIS_SLEEP_WINDOW) и persist-метке last_run в
    memory/.state/sleep.json — рестарты больше не обнуляют отсчёт (диагноз 04.07:
    reflections.md от 26.06). Догон >48ч — вне окна, но не раньше 10 мин после старта.

    26.07: ночной цикл взят под ``_ONE_MIND``. Он не разговор, но и не мелочь: свернуть
    прожитое в долгую память, выкопать из свежего новые основания, заново спросить «всё
    ещё так?» про характер — всё это ПЕРЕПИСЫВАЕТ то, из чего она в этот же миг может
    думать. В main() записан инвариант «мозг работает только под этим замком, кроме
    Forge», и здесь его не было: любой живой ход в четыре утра шёл поверх переписываемой
    памяти — ночной вопрос Егора, намеченное на ночь окно (планировщик окном сна не
    ограничен), пробуждение по её будильнику, доставка forge-события, durable-resume.
    Способов было много и до будильника; будильник добавил ещё один.

    Цена названа честно: пока идёт ночная работа, ответ на ночное сообщение ждёт её
    конца. Это хуже, чем мгновенный ответ, и лучше, чем ответ, собранный из памяти,
    которую в тот же момент перекладывают.

    Ночь не ПРОПУСКАЕТСЯ при занятом замке, а ЖДЁТ его — но ждёт в своей задаче, как это
    давно делает часовой пульс. Заботу зовут часы, и заснуть прямо в ней значило бы
    остановить весь тик: буферы, планировщик, outbox, доставку. А пропускать её было бы
    хуже: последний тик внутри окна сна пришёлся бы на занятый замок — и ночь уехала бы на
    сутки. Ожидание в задаче даёт и то и другое: часы идут, ночь состоится, как только она
    освободится. Гейта здесь нет, поэтому и раскрывать нечего."""
    global _SLEEP_TASK
    if _SLEEP_TASK is not None and not _SLEEP_TASK.done():
        return  # ночь уже идёт или ждёт своей очереди — второй не заводим
    import sleep as _sleep
    if not await asyncio.to_thread(_sleep.due, None, _STARTED_AT):
        return
    _SLEEP_TASK = asyncio.create_task(_run_night_cycle())


async def _run_night_cycle() -> None:
    """Ночной цикл под общим замком: он переписывает то, из чего она думает."""
    import sleep as _sleep
    async with _ONE_MIND:
        summary = await asyncio.to_thread(_sleep.run_scheduled)
    log.info("сон: %s", summary)


# ⚠ Здесь жил `_heartbeat_once` — «legacy manual selector». Он не стоял ни в одной
# строке `_clock_jobs()`, то есть не вызывался ниоткуда с тех пор, как часы свели к
# одному автономному пробуждению (два часовых джоба гонялись и открывали два окна на
# один час). Он был единственным читателем `heartbeat.window_goal`, `mark_window`,
# `record_decision`, а через них — единственным потребителем `appetite.windows_off()`
# и `considerate_hint()`. Из-за этого просьба Егора «умерь аппетиты» не влияла ни на
# что, а счётчик `opened_today` вечно показывал ей ноль окон.
# Решение Егора 25.07: контур выбросить, ручку не восстанавливать. Осталась одна
# ручка аппетита — `background_hold` (окна и пробуждения по расписанию, сон, formation),
# и она названа в манифесте ровно этим объёмом.


def _absence_portrait(name: str, slug: str = "") -> str:
    """Портрет важного человека для узкого контекста ответа в отсутствие (read-only, без recall)."""
    try:
        import graph
        s = slug or graph.resolve(name)
    except Exception:
        s = slug or agent._slug(name)
    try:
        import people
        return people.read_text(s)
    except Exception:
        return ""


def _last_incoming(convo: str, name: str) -> str:
    """Последняя НЕ её строка из «Имя: текст»-контекста — «что спросили» для heads-up владельцу."""
    for line in reversed((convo or "").splitlines()):
        s = line.strip()
        if s and not s.startswith("Praxis:"):
            return s[:160]
    return ""


async def _absence_once() -> None:
    """PASS 12.1: пока владелец в отсутствии — ЕЁ голос простаивающим важным сообщениям.

    Триггер (absence.due) — пересечение трёх сигналов (unanswered старше кулдауна ∩ важный
    ∩ активное окно) + кап на человека. Голос — узкий проход без тулов (не имперсонация).
    Heads-up владельцу ПОСЛЕ отправки (его нет, чтобы одобрять до — в этом весь смысл).

    26.07: контур взят под ``_ONE_MIND``. Он зовёт модель и ОТПРАВЛЯЕТ людям, а в main()
    записан инвариант «мозг работает только под этим замком, кроме Forge» — здесь его не
    было. Пока пульс рвал связь, разойтись во времени помогал сам разрыв: её отправка из
    окна ложилась в outbox и уходила уже после reconnect, так что двух живых голосов
    разом не выходило. Теперь пульс идёт со связью, и без замка absence заговорил бы
    ОДНОВРЕМЕННО с ней — два её голоса в Telegram в одну секунду. Занят — вернёмся на
    следующем тике: отсутствие живёт часами, минута ожидания ничего не стоит."""
    import absence as _absence
    due = await asyncio.to_thread(_absence.due)
    if not due:
        return
    w = await asyncio.to_thread(_absence.window)
    if not w:
        return
    note = await asyncio.to_thread(_absence.schedule_note)
    # ⚠ Проверка и захват — БЕЗ await между ними, иначе это не проверка: за любой await
    # замок успевает уйти, и `async with` тогда не пропустил бы тик, а ЗАСТРЯЛ на нём — а
    # заботу зовут прямо часы, и вместе с ней встали бы буферы, планировщик, outbox.
    # Ночь в таком случае ЖДЁТ (в своей задаче), а здесь правильнее пропустить: тик частый,
    # и следующий пересчитает `due` по свежему состоянию, а не отправит ответ, собранный
    # на данных десятиминутной давности. Контракт R1: пропуск оставляет след.
    if not _one_mind_is_free():
        try:
            await asyncio.to_thread(
                lambda: perception.note_skip(
                    "one_mind_absence", "мой_ритм",
                    detail="кому-то стоило ответить в отсутствие Егора, но я занята "
                           "живым ходом — вернусь на следующем тике"))
        except Exception:
            log.debug("skip отложенного отсутствия не записался", exc_info=True)
        return
    await _ONE_MIND.acquire()
    try:
        for item in due:
            chat_id = item["chat_id"]
            person = item.get("person") or {}
            name = person.get("name") or item.get("name") or str(chat_id)
            try:
                convo = await _last_n_text(chat_id)
            except Exception:
                convo = ""
            portrait = await asyncio.to_thread(_absence_portrait, name, person.get("slug", ""))
            reply = await asyncio.to_thread(agent.compose_absence_reply, name, portrait, note, convo)
            if not reply:
                continue
            ctx = agent.ChannelContext(chat_id=str(chat_id), is_dm=True, owner=False, known=True, title=name)
            guard_context = (
                "Autonomous absence reply. Owner is away; enforce only cross-person/cross-chat "
                "private disclosure at this non-owner DM boundary. Do not edit or judge Praxis's speech.\n\n"
                f"Conversation:\n{(convo or '')[-1500:]}\n\nSchedule note:\n{(note or '')[:600]}"
            )
            reply = await asyncio.to_thread(agent.guard_outbound_reply, reply, convo, ctx=ctx,
                                            orient=guard_context)
            if not reply:
                log.info("отсутствие: исходящий guard удержал ответ [%s]", chat_id)
                continue
            ent = await _resolve_entity(chat_id)
            if ent is None:
                log.info("отсутствие: не резолвится адресат [%s] — пропускаю", chat_id)
                continue
            try:
                await client.send_message(ent, reply)
            except Exception:
                log.exception("отсутствие: отправка упала [%s]", chat_id)
                continue
            n = await asyncio.to_thread(_absence.note_sent, chat_id, w)
            await asyncio.to_thread(unanswered.resolve, str(chat_id))
            _buf_push(str(chat_id), f"Praxis: {reply}", author="Praxis", is_dm=True)
            try:
                agent.tool_journal(f"[отсутствие] отвечено {name} (#{n} за окно): «{reply[:120]}»", salience=2)
            except Exception:
                log.debug("отсутствие: журнал не записался", exc_info=True)
            log.info("ОТСУТСТВИЕ [%s] %s -> %r", chat_id, name, reply[:80])
            if OWNER_ID:  # heads-up ПОСЛЕ отправки
                asked = _last_incoming(convo, name)
                try:
                    await client.send_message(
                        OWNER_ID,
                        f"пока тебя нет, отвечено {name} (id={chat_id}). "
                        + (f"Их последнее: «{asked}». " if asked else "")
                        + f"Мой ответ: «{reply[:200]}»")
                except Exception:
                    log.exception("отсутствие: heads-up владельцу не ушёл")
    finally:
        _ONE_MIND.release()


async def _selfdev_reconcile_once() -> None:
    """Забота часов: submit падал на длинном гейте при рестарте — оболочки предложений
    копились безымянными. Реконсайлер закрывает беспредметные и возвращает титулы;
    решения о повторном submit остаются за Праксис (никакого авто-мёржа)."""
    out = await asyncio.to_thread(selfdev.reconcile)
    if out.get("closed") or out.get("restored"):
        log.info("selfdev-оболочки: закрыто %s, титулов восстановлено %s",
                 out.get("closed"), out.get("restored"))


async def _immune_once() -> None:
    """PASS 9.2: ревью вдогонку её живых самокоммитов (очередь наполняют call-sites selfgit).
    Модель зовётся только когда очередь непуста; вердикты — в журнал, red — карточка Егору."""
    import immune
    n = await asyncio.to_thread(immune.process_queue)
    if n:
        log.info("иммунитет: отревьюила самокоммитов — %d", n)


async def _formation_once() -> None:
    """PASS 19: owner/panel request is executed by her main process, outside live turns."""
    if _passing:
        return
    out = await asyncio.to_thread(formation.run_requested)
    if not out.get("noop"):
        log.info("formation request: %s", out.get("summary") or out)


#: Сколько ждём вежливого прощания с Telegram при перезапуске по заявке контура.
_DISCONNECT_TIMEOUT = 10.0
#: Через сколько выход происходит в любом случае, даже если поток застрял в
#: библиотеке и не отвечает вовсе.
_EXIT_FUSE_SECONDS = 20.0


def _arm_restart_exit() -> "threading.Timer":
    """Пообещать выход таймером — на случай, если штатный путь не вернётся.

    Таймер демонский и ОТМЕНЯЕМЫЙ. Отменяемость здесь не украшение: в стендах
    `agent._exit_process` подменён и возвращается, и неотменяемый запал позвал бы
    настоящий `os._exit(42)` через двадцать секунд — посреди чужого прогона.
    Штатный путь гасит его сам (см. `_control_once`).

    Если же запал сработал — значит штатный путь не вернулся вовсе, и разница
    между этими двумя исходами есть разница между «перезапустилась» и «молчала
    два с половиной часа».
    """
    def _fuse() -> None:
        log.error("выход по запалу: штатный путь перезапуска не завершился за %s с",
                  _EXIT_FUSE_SECONDS)
        agent._exit_process()

    timer = threading.Timer(_EXIT_FUSE_SECONDS, _fuse)
    timer.name = "restart-exit-fuse"
    timer.daemon = True
    timer.start()
    return timer


def _apply_desk_interrupt(request: dict) -> dict:
    """Прервать живые прогоны по просьбе Пульта — кооперативно, через run_manager.

    `request_cancel` терминализует прогон, если у него нет незакрытых вызовов рук, иначе
    записывает просьбу и ставит паузу; `_run_status_gate` в тул-цикле видит статус
    ≠ running на ближайшей границе (перед рукой, после ответа модели) и поднимает
    RunStopped — черновик ответа брошен, руки дальше не зовутся. Идущий вызов модели
    дорабатывает до конца: рвать поток посреди генерации значило бы врать провайдеру
    о расходе. scope — «all» или id одного прогона.
    """
    scope = str(request.get("scope") or "all").strip() or "all"
    by = str(request.get("by") or "desk").strip()[:40] or "desk"
    reason = str(request.get("reason") or "прервано с Пульта").strip()[:200]
    manager = agent._runs()
    rows = manager.list_runs(statuses=tuple(agent.run_manager.NONTERMINAL_STATUSES))
    cancelled: list[str] = []
    waiting: list[str] = []
    skipped: list[str] = []
    for row in rows:
        rid = str(row.get("run_id") or row.get("id") or "")
        if not rid or (scope != "all" and rid != scope):
            continue
        try:
            manifest = manager.request_cancel(rid, actor=f"desk:{by}", reason=reason)
            # ⚠ 21.09.2026. Здесь любой вызов без исключения шёл в «отменено», и Пульт
            # рапортовал бодрое число. Но `request_cancel` при незакрытом вызове руки
            # НАМЕРЕННО не терминализует ход: он пишет просьбу и ставит `paused`,
            # возвращая манифест. То есть в счёт попадали и ходы, которые просто встали
            # на паузу с висящим намерением, — а владелец читал это как «остановлено».
            # Считаем раздельно: остановлено сейчас и ждёт незакрытых вызовов.
            status = str((manifest or {}).get("status") or "")
            if status == "cancelled":
                cancelled.append(rid)
            else:
                waiting.append(f"{rid}: {status or 'unknown'}")
        except Exception as exc:
            skipped.append(f"{rid}: {type(exc).__name__}")
    return {"cancelled": cancelled, "waiting": waiting, "skipped": skipped,
            "scope": scope, "by": by}


_INTERRUPT_MAX_AGE_SEC = 120.0
_CLOCK_ALIVE = [False]


async def _early_control_watch() -> None:
    """Просьбы владельца читаются, пока boot ещё догоняет хвосты.

    ⚠ 21.09.2026. `interrupt.json` читает единственный потребитель — забота часов, а
    часы рождаются в самом КОНЦЕ `main()`, после recover/outbox/resume. Пока догоняющие
    проверки идут, «прервать» из Пульта физически некому исполнить: не «не сработало», а
    исполнителя нет. В этот день проверки шли часами, и владелец жал кнопку впустую.
    Эта задача живёт ровно до рождения часов и делает ТОЛЬКО чтение просьбы: транспорт
    и мягкий перезапуск не трогает, чтобы не выйти из процесса посреди загрузки.
    """
    while not _CLOCK_ALIVE[0]:
        try:
            await _desk_interrupt_once()
        except Exception:
            log.debug("ранний надзор за управлением споткнулся", exc_info=True)
        await asyncio.sleep(3.0)


async def _desk_interrupt_once() -> None:
    """Тик часов: просьба Пульта прервать ход (memory/.control/interrupt.json)."""
    request = selfdev.interrupt_requested()
    if not request:
        if selfdev.INTERRUPT_REQ.exists():
            selfdev.clear_interrupt_request()   # битый файл не перечитываем каждый тик
        return
    # ⚠ 21.09: у просьбы не было срока годности. Пролежавшая час `scope=all` срабатывала
    # на первом же тике ожившых часов и убивала ход, который владелец к тому времени
    # прерывать уже не просил. Непрочитанная просьба — симптом мёртвых часов, и он
    # обязан быть виден, а не исполнен задним числом.
    asked = _iso_epoch(request.get("at"))
    if asked != float("inf") and time.time() - asked > _INTERRUPT_MAX_AGE_SEC:
        selfdev.clear_interrupt_request()
        log.warning("просьба прервать от %s протухла непрочитанной (%.0f с) — гашу",
                    request.get("by") or "desk", time.time() - asked)
        try:
            agent.tool_journal(
                f"[прервано] просьба владельца остановить ход пролежала непрочитанной "
                f"{int(time.time() - asked)} с и погашена: часы не читали управление",
                salience=2)
        except Exception:
            log.debug("журнал о протухшей просьбе не записался", exc_info=True)
        return
    selfdev.clear_interrupt_request()
    try:
        result = await asyncio.to_thread(_apply_desk_interrupt, request)
    except Exception:
        log.warning("прерывание с Пульта не исполнилось", exc_info=True)
        return
    log.warning("прерывание с Пульта (%s, scope=%s): остановлено %d, ждут незакрытых "
                "вызовов %d %s, пропущено %s",
                result["by"], result["scope"], len(result["cancelled"]),
                len(result.get("waiting") or []), result.get("waiting") or "",
                result["skipped"] or "0")
    try:
        waiting = len(result.get("waiting") or [])
        agent.tool_journal(
            f"[прервано] живой ход остановлен с Пульта ({result['by']}): прогонов "
            f"{len(result['cancelled'])}"
            + (f"; ещё {waiting} встали на паузу с незакрытым вызовом" if waiting else ""),
            salience=2)
    except Exception:
        log.debug("журнал о прерывании не записался", exc_info=True)


async def _control_once() -> None:
    """Забота часов: смёрженное предложение просит перезапуск — уходим мягко (exit 42),
    когда нет активного прохода; bootguard поднимет на новом коде (preflight + откат).
    12.09: та же забота читает просьбу Пульта прервать живой ход."""
    await _desk_interrupt_once()
    reason = selfdev.restart_requested()
    if not reason:
        return
    if _passing:
        return  # не роняем живой ход — попробуем на следующем тике
    log.warning("перезапуск по запросу контура: %s", reason)
    selfdev.clear_restart_request()
    _SHUTDOWN.set()  # 13.1: этот disconnect — конец процесса, не «поработать и вернуться»
    # ⚠⚠ ВЫХОД ВООРУЖАЕТСЯ ДО DISCONNECT, и это несущее свойство, а не осторожность.
    #
    # 10.09 в 12:37 смёрженное предложение попросило перезапуск — и дальше 2 ч 26 мин
    # без связи и без ходов. Процесс остался ЖИВ: Telegram отключён, `bootguard` ждёт
    # мёртвого, сердцебиение (раз в час) молчит, заявка контура УЖЕ погашена строкой
    # выше — то есть следующий тик не повторит попытку никогда. Подняли руками.
    #
    # `try/except` здесь ловил исключение, но не ЗАВИСАНИЕ: если `disconnect()` не
    # возвращается, до `_exit_process()` дело просто не доходит. Поэтому выход теперь
    # обещан таймером ещё до того, как мы попросим Telegram попрощаться, а сам
    # disconnect получил срок: он вежливость, а не условие выхода.
    fuse = _arm_restart_exit()
    try:
        await asyncio.wait_for(client.disconnect(), timeout=_DISCONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        log.warning("disconnect не вернулся за %s с — выхожу без него", _DISCONNECT_TIMEOUT)
    except Exception:
        log.debug("disconnect перед перезапуском не удался", exc_info=True)
    agent._exit_process()
    # Сюда попадает только стенд: настоящий `_exit_process` не возвращается.
    # Запал гасим, иначе он позовёт выход позже, когда подмена уже снята.
    fuse.cancel()


async def _text_outbox_once() -> None:
    """Replay versioned Telegram text plans without invoking Praxis' voice."""
    if _ONE_MIND.locked():
        # A live pass delivers its own reply inline (see _run_pass); this
        # whole-run-tree recovery scan otherwise runs every tick straight through
        # the pass and, holding the GIL over hundreds of run manifests, starved
        # the pass's prompt recall (~5 min pre-model freeze; py-spy confirmed).
        # Recovery is idempotent and retries on the next tick once the pass ends.
        return
    plans = await asyncio.to_thread(agent.run_pending_text_deliveries, limit=20)
    for plan in plans:
        run_id = str(plan.get("run_id") or "")
        if not run_id or run_id in _TEXT_SENDING:
            continue
        _TEXT_SENDING.add(run_id)
        try:
            conversation_id = str(plan.get("conversation_id") or "")
            peer_id = str(plan.get("peer_id") or "")
            topic_id = plan.get("topic_id")
            # The durable plan is the routing authority.  Live metadata may
            # supply a cached entity only for that exact conversation, never a
            # different topic under the same forum root.
            meta = _meta.get(conversation_id)
            meta = meta if isinstance(meta, dict) else {}
            if meta and (
                str(meta.get("peer_id") or peer_id) != peer_id
                or meta.get("topic_id") != topic_id
            ):
                meta = {}
            entity = meta.get("entity")
            if entity is None:
                try:
                    entity = await _resolve_entity(peer_id)
                except Exception:
                    entity = None
            if entity is None:
                try:
                    entity = int(peer_id)
                except ValueError:
                    entity = peer_id

            recovered_text: list[str] = []
            recovered_ids: list[str] = []
            for chunk in plan.get("pending_chunks") or ():
                sent, _random_id = await _send_message_idempotent(
                    entity, str(chunk.get("text") or ""),
                    delivery_key=str(chunk.get("delivery_key") or ""),
                    reply_to=chunk.get("reply_to"),
                )
                message_id = getattr(sent, "id", None)
                await asyncio.to_thread(
                    agent.run_delivery_text_chunk_accepted,
                    run_id, index=int(chunk.get("index")),
                    delivery_key=str(chunk.get("delivery_key") or ""),
                    message_id=message_id,
                )
                recovered_text.append(str(chunk.get("text") or ""))
                if message_id is not None:
                    recovered_ids.append(str(message_id))

            reconciled = await asyncio.to_thread(agent.run_delivery_text_reconcile, run_id)
            if recovered_text:
                is_dm = bool(meta.get("is_dm", not peer_id.startswith("-")))
                _buf_push(
                    conversation_id, f"Praxis: {''.join(recovered_text)}",
                    author="Praxis", is_dm=is_dm,
                    **({"source_id": ",".join(recovered_ids), "ts": time.time()}
                       if recovered_ids else {}),
                )
                if is_dm:
                    await asyncio.to_thread(unanswered.resolve, conversation_id)
            log.info(
                "durable Telegram text replay [%s] topic=%s chunks=%d reconciled=%s",
                run_id, topic_id, len(recovered_text), reconciled,
            )
        except Exception as exc:
            await asyncio.to_thread(agent.run_delivery_failed, run_id, exc)
            log.exception("durable Telegram text replay failed [%s]", run_id)
        finally:
            _TEXT_SENDING.discard(run_id)


async def _media_cleanup_once() -> None:
    """Retry guarded uploads, then remove expired spool copies; never calls a model."""
    spool = _media_spool()
    # A Telegram acceptance tombstone is stronger than a missing secondary run
    # receipt. Rebuild that receipt idempotently before considering retries.
    for record in (await asyncio.to_thread(spool.outbox_results, "delivered"))[-200:]:
        payload = record.get("item") or {}
        run_id = str(payload.get("run_id") or "")
        queue_id = str(record.get("queue_id") or "")
        if not run_id or not queue_id:
            continue
        # 13.09: закрытый прогон — расписку перепроецировать некуда, его доставка уже
        # сведена своим ходом (та же логика, что в _reconcile_direct_outbox_entry).
        # Раньше 21 тумбстон августа, чьи results/ сняла ретенция, каждую минуту бил
        # трейсбеком «ResultRef integrity» (275 в час), а staged-копия жила вечно,
        # потому что unlink стоял только ПОСЛЕ успешной финализации.
        if queue_id in _MEDIA_TOMBSTONES_SETTLED or _run_is_settled(run_id):
            _MEDIA_TOMBSTONES_SETTLED.add(queue_id)
            source_rel = str(payload.get("path") or "").strip()
            if source_rel:
                try:
                    media_core.contained_path(spool.root, source_rel).unlink(missing_ok=True)
                except Exception:
                    log.debug("settled media tombstone: staged copy not removed [%s]",
                              queue_id, exc_info=True)
            continue
        result = record.get("result") or {}
        try:
            await asyncio.to_thread(
                agent.run_delivery_media_result, run_id, queue_id,
                ok=True, message_id=result.get("message_id"),
            )
            await asyncio.to_thread(agent.run_delivery_finalize_recovered, run_id)
            # The Telegram tombstone and run WAL now agree.  Only at this point
            # may the staged bytes disappear; before it, strict recovery still
            # needs the checkpointed path to reconcile the bookkeeping gap.
            source_rel = str(payload.get("path") or "").strip()
            if source_rel:
                source = media_core.contained_path(spool.root, source_rel)
                source.unlink(missing_ok=True)
        except Exception:
            log.exception("outbox->run media reconciliation упал [%s]", queue_id)

    # 25.09: перечисление спула читает журнал с диска — не в потоке цикла Telegram
    # (профиль 25.09: часы держали цикл на `_reload_ledger_locked` каждый тик).
    for item in await asyncio.to_thread(spool.pending):
        policy = await asyncio.to_thread(
            agent.run_delivery_media_retry_policy, item.run_id, item.queue_id,
        )
        if policy == "ack":
            try:
                await asyncio.to_thread(
                    spool.discard, item.queue_id,
                    receipt={"reconciled_from": "durable run receipt"},
                )
                item.path.unlink(missing_ok=True)
            except OSError:
                pass
            except Exception:
                log.exception("run-confirmed media outbox ack упал [%s]", item.queue_id)
            continue
        if policy == "drop":
            try:
                await asyncio.to_thread(
                    spool.fail, item.queue_id,
                    reason="linked durable run is terminal without this upload",
                )
                item.path.unlink(missing_ok=True)
            except OSError:
                pass
            except Exception:
                log.exception("terminal run media cleanup упал [%s]", item.queue_id)
            continue
        stored_route = _route_from_reference(item.target_chat_id)
        state_chat_id, meta = _meta_for_delivery(
            item.target_chat_id, item.reply_to_message_id)
        meta = meta or {}
        state_chat_id = str(state_chat_id or stored_route.conversation_id)
        meta_route = telegram_topics.TopicRoute(
            str(meta.get("peer_id") or stored_route.peer_id),
            meta.get("topic_id", stored_route.topic_id),
        )
        entity = meta.get("entity")
        if entity is None:
            try:
                entity = await _resolve_entity(meta_route.peer_id)
            except Exception:
                entity = None
        if entity is None:
            try:
                entity = int(meta_route.peer_id)
            except ValueError:
                entity = meta_route.peer_id
        ctx = agent.ChannelContext(
            # Validate against the exact persisted target (conversation id for
            # new records, root peer for legacy records); write retry receipts to
            # the isolated state conversation selected above.
            chat_id=str(item.target_chat_id),
            room_id=meta_route.peer_id,
            is_dm=meta.get("is_dm", item.scope != "group"),
            owner=meta.get("is_owner", False), known=meta.get("known", True),
            family=meta.get("family", False), addressed=meta.get("addressed", False),
            title=meta.get("title"), size=meta.get("size"),
            _scope_override=item.scope)
        await _attempt_queued_media(
            entity, item, ctx=ctx, reply_to=meta_route.topic_id,
            state_chat_id=state_chat_id,
        )
    removed = await asyncio.to_thread(spool.cleanup)
    if removed:
        log.info("media spool: убрано просроченных файлов: %d", len(removed))
    for state in ("expired", "failed"):
        for record in (await asyncio.to_thread(spool.outbox_results, state))[-100:]:
            payload = record.get("item") or {}
            run_id = str(payload.get("run_id") or "")
            if run_id:
                queue_id = str(record.get("queue_id") or "")
                result = record.get("result") or {}
                reason = str(result.get("reason") or "no reason")[:1000]
                await asyncio.to_thread(
                    agent.run_delivery_blocked, run_id,
                    reason=f"Telegram media {queue_id} is {state}: {reason}",
                )
                await asyncio.to_thread(
                    owner_delivery.LEDGER.emit,
                    "system_alert",
                    title="Файл не доставлен в Telegram",
                    urgency="high",
                    outcome="blocked",
                    thread_key=f"run:{run_id}",
                    correlation={"run_id": run_id, "queue_id": queue_id},
                    reason="Доставка файла перешла в терминальное состояние без приёмки.",
                    provenance={"source": "media_outbox", "source_id": queue_id},
                    result=f"{Path(str(payload.get('path') or 'файл')).name}: {reason}",
                    expectation="Открыть запуск, проверить причину и при необходимости повторить отправку.",
                    action={
                        "label": "Открыть run", "domain": "praxis",
                        "action": "run.open", "run_id": run_id,
                    },
                    dedupe_key=f"media-outbox:{queue_id}:{state}",
                )


def _followup_delivery_projection(item: dict) -> dict:
    """Small current-state fingerprint used immediately before owner transport."""
    response = item.get("response") or {}
    target = str(item.get("target_label") or "собеседник")[:240]
    speaker = str(response.get("sender_name") or "").strip()
    is_dm_thread = bool(item.get("target_user_id"))
    if not speaker and is_dm_thread:
        speaker = target
    if not speaker:
        sender = str(response.get("sender_id") or "").strip()
        speaker = f"id {sender}" if sender else "не знаю кто"
    return {
        "status": str(item.get("status") or ""),
        "revision_source_id": str(
            response.get("revision_source_id") or response.get("message_id") or ""),
        "title": (f"{speaker[:110]} ответил(а)" if is_dm_thread
                  else f"{speaker[:110]} ответил(а) в {target[:120]}"),
        "excerpt": str(response.get("text") or "(без текста)")[:1200],
    }


async def _deliver_owner_item(delivery: dict) -> dict:
    """Deliver one queued inbox item with a stable Telegram transport identity."""
    if (delivery.get("status") != "queued"
            or "telegram" not in (delivery.get("transports") or ())):
        return delivery
    current = await asyncio.to_thread(
        owner_delivery.LEDGER.current_for_transport,
        str(delivery.get("id") or ""), transport="telegram",
        expected_revision=int(delivery.get("revision") or 0),
    )
    if current is None:
        return (await asyncio.to_thread(
            owner_delivery.LEDGER.get, str(delivery.get("id") or "")) or delivery)
    followup_id = str((current.get("correlation") or {}).get("followup_id") or "")
    if current.get("type") == "followup_answer" and followup_id:
        thread = await asyncio.to_thread(telegram_followups.LEDGER.get, followup_id)
        if isinstance(thread, dict):
            projection = _followup_delivery_projection(thread)
            if (projection.get("status") != "answered"
                    or projection.get("revision_source_id")
                    != str((current.get("provenance") or {}).get("revision_source_id") or "")
                    or projection.get("excerpt") != str(current.get("body") or "")
                    or projection.get("title") != str(current.get("title") or "")):
                await _supersede_pending_followup_deliveries(
                    followup_id, reason="response_changed_at_transport_boundary")
                return (await asyncio.to_thread(
                    owner_delivery.LEDGER.get, str(current.get("id") or "")) or current)
    sent, random_id = await _send_message_idempotent(
        OWNER_ID, owner_delivery.format_telegram(current),
        delivery_key=f"owner-delivery:{current['id']}",
    )
    return await asyncio.to_thread(
        owner_delivery.LEDGER.mark_delivered,
        current["id"], transport="telegram",
        receipt={
            "message_id": getattr(sent, "id", None),
            "random_id": str(random_id),
        },
    )


async def _owner_deliveries_once() -> None:
    """Replay the private owner inbox through Telegram without invoking a model."""
    if not OWNER_ID:
        return
    pending = await asyncio.to_thread(owner_delivery.LEDGER.pending, limit=10)
    for delivery in pending:
        await _deliver_owner_item(delivery)


async def _supersede_pending_followup_deliveries(followup_id: str, *, reason: str) -> int:
    """Suppress queued (never sent) owner copies for a revised/deleted response.

    Delivered/read items are immutable evidence: Telegram cannot recall them and this
    helper deliberately makes no such claim.  A later accepted edit gets a fresh delivery
    identity from its revision source id; deletion leaves no current notification.
    """
    if not followup_id:
        return 0
    changed = 0
    rows = await asyncio.to_thread(
        owner_delivery.LEDGER.list, limit=100_000, include_superseded=False)
    for row in rows:
        if (row.get("status") != "queued"
                or str((row.get("correlation") or {}).get("followup_id") or "")
                != followup_id):
            continue
        try:
            await asyncio.to_thread(
                owner_delivery.LEDGER.transition,
                str(row.get("id") or ""), "superseded",
                expected_revision=int(row.get("revision") or 0),
                detail={"reason": reason, "followup_id": followup_id},
            )
            changed += 1
        except (KeyError, ValueError):
            # A concurrent delivery/transition won the race.  Already-sent Telegram
            # cannot be recalled, and the durable state truthfully records that result.
            log.info("follow-up owner delivery changed concurrently [%s]", followup_id)
    return changed


async def _followups_once() -> None:
    """Project concrete replies into the owner inbox, then deliver via Telegram."""
    if not OWNER_ID:
        return
    pending = await asyncio.to_thread(telegram_followups.LEDGER.pending_notifications)
    for item in pending[:10]:
        response = item.get("response") or {}
        excerpt = str(response.get("text") or "(без текста)")[:1200]
        followup_id = str(item.get("id") or "")
        target = str(item.get("target_label") or "собеседник")[:240]
        # ⚠ 27.07: заголовок назывался меткой ЧАТА — «AbstractDL Chat ответил(а)». Чат не
        # отвечает, отвечает человек, и имя было под рукой с самого начала: строкой выше
        # раннер печатает «получен ответ #94244 от Yegor Kosyrev». Теперь оно доезжает.
        # Если имени нет — говорим «id N» или прямо «не знаю кто», но комнату вместо
        # человека не подставляем: выдуманный автор хуже пустого места (закон 3).
        speaker = str(response.get("sender_name") or "").strip()
        is_dm_thread = bool(item.get("target_user_id"))
        if not speaker and is_dm_thread:
            # В ЛС отвечает ровно тот, кому она писала, — метка нити ЗДЕСЬ и есть человек.
            # Это же спасает записи, заведённые до 27.07: имени в них нет вовсе.
            speaker = target
        if not speaker:
            sender = str(response.get("sender_id") or "").strip()
            speaker = f"id {sender}" if sender else "не знаю кто"
        title = (f"{speaker[:110]} ответил(а)" if is_dm_thread
                 else f"{speaker[:110]} ответил(а) в {target[:120]}")
        delivery = await asyncio.to_thread(
            owner_delivery.LEDGER.emit,
            "followup_answer",
            title=title,
            body=excerpt,
            outcome="success",
            thread_key=f"telegram-followup:{followup_id}",
            correlation={
                "followup_id": followup_id,
                "peer_id": str(response.get("peer_id") or ""),
                "message_id": str(response.get("message_id") or ""),
                "sent_message_id": str(item.get("sent_message_id") or ""),
            },
            reason="Пришёл ответ в отслеживаемой Telegram-нити.",
            provenance={
                "source": "telegram_followups", "source_id": followup_id,
                "revision_source_id": str(
                    response.get("revision_source_id") or response.get("message_id") or ""),
            },
            expectation="Прочитать ответ и решить, нужно ли продолжение.",
            action={
                "label": "Открыть нить",
                "domain": "telegram", "action": "followup.open",
                "followup_id": followup_id,
            },
            result=excerpt,
            dedupe_key=(
                f"telegram-followup:{followup_id}:answer:"
                f"{response.get('peer_id')}:{response.get('message_id')}:"
                f"{response.get('revision_source_id') or response.get('message_id')}"
            ),
            coalesce_key=f"telegram-followup:{followup_id}:answer",
        )
        # The response may be edited/deleted after pending_notifications() but before
        # transport send.  Re-read its lineage and never send a stale queued payload.
        current = await asyncio.to_thread(
            telegram_followups.LEDGER.get, followup_id)
        if isinstance(current, dict):
            current_response = current.get("response") or {}
            if (current.get("status") != "answered"
                    or str(current_response.get("revision_source_id")
                           or current_response.get("message_id") or "")
                    != str(response.get("revision_source_id")
                           or response.get("message_id") or "")):
                await _supersede_pending_followup_deliveries(
                    followup_id, reason="response_changed_before_delivery")
                continue
        delivery = await _deliver_owner_item(delivery)
        if delivery.get("status") != "delivered":
            log.info(
                "FOLLOW-UP %s: owner-delivery=%s not delivered (state=%s)",
                followup_id, delivery.get("id"), delivery.get("status"),
            )
            continue
        await asyncio.to_thread(telegram_followups.LEDGER.mark_notified, followup_id)
        log.info(
            "FOLLOW-UP %s: owner-delivery=%s state=%s",
            followup_id, delivery.get("id"), delivery.get("status"),
        )


async def _run_social_pulse(pulse_id: str) -> None:
    ok = False
    outcome: bool | None = False
    mailbox_hashes: list[str] = []
    try:
        followups = await asyncio.to_thread(telegram_followups.LEDGER.context)
        # One wake, one authored task window, one full bounded context.  Heartbeat still
        # owns the useful context builders/receipts, not a second timer or gatekeeper call.
        import heartbeat
        continuity = await asyncio.to_thread(
            heartbeat.window_context, include_pacing=False,
        )
        pulse_observability = await asyncio.to_thread(
            social_pulse.observability, pulse_id,
        )
        context = "\n\n".join(
            x for x in (pulse_observability, continuity, followups) if x
        )
        goal = social_pulse.goal(context)
        import mailroom
        seen = await asyncio.to_thread(social_pulse.mailbox_seen)
        open_mail = await asyncio.to_thread(mailroom.list_open)
        mailbox_hashes = [str(entry.get("hash")) for entry in open_mail
                          if entry.get("hash") and entry.get("hash") not in seen][:12]
        mailbox_index = await asyncio.to_thread(
            mailroom.index_block, 12, exclude_hashes=seen,
        )
        with social_pulse.active(pulse_id):
            # 26.07: пульс идёт БЕЗ разрыва связи. Он — её единственное регулярное
            # автономное пробуждение, и по содержанию он социальный насквозь: открытые
            # нити, почта, фолоуапы, «хочу ли я кому-то написать». Всё это он делал с
            # закрытым Telegram — то есть ровно та работа, ради которой он существует, в
            # нём была невозможна, а она была недоступна час за часом.
            #
            # Разрыв здесь восстановили в 13.1 после регрессии 25 («Telethon continuously
            # online» породил ПАРАЛЛЕЛЬНЫЕ пробуждения). От этого сегодня защищает не
            # disconnect, а замок: _ONE_MIND появился позже, и всякий входящий проход его
            # ЖДЁТ (_pass: `await _ONE_MIND.acquire()`), а не бежит рядом. Тем же замком и
            # на живой связи уже год работает forge-контур.
            #
            # Что меняется для человека: раньше его сообщение в час пульса не приходило
            # вовсе; теперь приходит сразу и отвечается следующим ходом.
            outcome = await _task_window(goal, mailbox_index=mailbox_index,
                                         keep_transport=True)
            if outcome is None:
                await asyncio.to_thread(
                    social_pulse.defer, pulse_id,
                    detail="task window deferred while another authored turn was active",
                )
                # Её ЕДИНСТВЕННОЕ регулярное автономное пробуждение. `defer` вернул
                # durable claim, но часы уже перевзвели срок на период вперёд — без
                # заявки окно теряется на час (15% пробуждений, 25 из 168 по леджеру).
                retry_at = _request_pulse_retry()
                log.info("ПУЛЬС отложен живым ходом — вернусь через %.0fс",
                         max(0.0, retry_at - time.time()))
                return
            ok = outcome
    finally:
        if outcome is not None:
            await asyncio.to_thread(
                social_pulse.finish, pulse_id, ok=ok,
                detail="hourly social scan completed" if ok else "task window failed",
                mailbox_hashes=mailbox_hashes,
            )


def _in_sleep_window(now: float | None = None) -> bool:
    """True while the configured sleep window (PRAXIS_SLEEP_WINDOW, local hours) is open.

    Same window sleep.due() uses, read the same way."""
    try:
        import heartbeat
        window = heartbeat.parse_hours(os.getenv("PRAXIS_SLEEP_WINDOW", "4-6"))
        moment = float(now if now is not None else time.time())
        return bool(window) and heartbeat.hour_in(heartbeat.local_now(moment).hour, window)
    except Exception:
        return False


async def _social_pulse_once() -> None:
    """Start the hourly social run without blocking buffers/schedule/follow-up clocks."""
    global _SOCIAL_PULSE_TASK
    if _SOCIAL_PULSE_TASK is not None and not _SOCIAL_PULSE_TASK.done():
        return
    if _in_sleep_window():
        # The pulse is Praxis waking *herself*, and night is night: a wake inside her own
        # sleep window is not rest.  (Until 26.07 the recorded reason was that the pulse
        # disconnects Telegram and would therefore reason about people while blind to the
        # live chats — which is how blind night repeats happened.  The pulse no longer
        # disconnects, so that premise is gone; the gate stays, with its own honest
        # reason, rather than keeping a justification that stopped being true.)  Sleep,
        # delivery and every non-model clock keep running.  We return before
        # social_pulse.begin(), so the durable claim clock is untouched (the module stays
        # a pure recorder) and the first tick after the window still finds it due.
        #
        # Контракт R1 (CONTRACTS.md): гейт может существовать — молча нет. Раньше это был
        # голый return: ни лога, ни skip, ни receipt, при том что rails.md утверждал
        # «поведенческих гейтов нет». Она не могла обнаружить, что её собственное
        # пробуждение съедено, никаким доступным ей способом.  note_skip кладёт причину
        # туда же, куда ложатся остальные пропуски восприятия — в manage_perception("skips").
        try:
            await asyncio.to_thread(
                lambda: perception.note_skip(
                    "sleep_window", "мой_ритм",
                    detail=f"окно сна {os.getenv('PRAXIS_SLEEP_WINDOW', '4-6')}: "
                           "автономное пробуждение не открываю"))
        except Exception:
            log.debug("skip окна сна не записался", exc_info=True)
        return
    pulse_id = await asyncio.to_thread(social_pulse.begin)
    if not pulse_id:
        return
    _SOCIAL_PULSE_TASK = asyncio.create_task(_run_social_pulse(pulse_id))


async def _membership_reconcile_once() -> None:
    """Finish owner-authorized membership transactions after restart/transport loss."""
    ledger = _membership_ledger()
    pending = await asyncio.to_thread(ledger.pending)
    for state in pending[:20]:
        tx_id = str(state.get("id") or "")
        principal = _telegram_account_principal(state.get("principal_id"))
        if principal is None:
            # Human authority never transfers with a stale ledger when PRAXIS_OWNER_ID changes.
            await asyncio.to_thread(
                ledger.failed, tx_id, "configured owner changed; stale intent not executed",
            )
            log.warning("membership stale owner intent закрыт [%s]", tx_id)
            continue
        try:
            if state.get("action") == "join":
                result = await _join_chat_async(
                    str(state.get("target") or ""), principal_id=principal,
                    transaction_id=tx_id, recovery=True,
                )
            else:
                result = await _leave_chat_async(
                    str(state.get("target") or ""), principal_id=principal,
                    transaction_id=tx_id, recovery=True,
                )
            logger = log.debug if result.get("status") == "request_sent" else log.info
            logger("membership reconcile [%s]: %s", tx_id, result.get("status"))
        except Exception:
            # The transaction remains accepted/in_doubt and will be retried by the clock.
            log.exception("membership reconcile ждёт следующего retry [%s]", tx_id)


async def _computer_inventory_once() -> None:
    """Refresh the server-owned Windows map daily, whenever the body is online."""
    import computer_inventory

    if not await asyncio.to_thread(computer_inventory.due):
        return
    result = await asyncio.to_thread(computer_inventory.refresh)
    if result.get("ok"):
        log.info("computer inventory: %s", result)
    else:
        # Offline is not marked fresh; the next hourly tick catches up after
        # the PC returns instead of waiting for a fixed night window.
        log.info("computer inventory отложен: %s", result.get("code") or result.get("error"))


RESUME_BACKOFF_BASE_SEC = 45.0    # первая неудача стоит один такт часов...
RESUME_BACKOFF_MAX_SEC = 900.0    # ...а потолок отсрочки — четверть часа
# Что именно проваливалось в прошлый раз и сколько раз подряд.
_resume_failures: dict = {"ids": frozenset(), "n": 0, "not_before": 0.0}


def _resume_backoff_delay(misses: int) -> float:
    """Отсрочка РАСТЁТ, а не остаётся тактом часов.

    ⚠ ИСТОРИЯ ДЕФЕКТА, ЗАМЕР ПРОДА 10.08.2026.  Такт был фиксированные 45 секунд, потолка
    попыток не было вовсе, и когда апстрим ответил 401 на всё, один и тот же прогон
    `cb4898bf` повторил `continue_checkpoint -> failed` **165 раз за 2 часа 20 минут** —
    по три вызова модели на попытку, каждый с полным префиксом. Ни одна строка при этом не
    сказала «я упёрлась»: в логе была ровно та же INFO, что и при здоровой работе.
    Всплеск переживается ВРЕМЕНЕМ, а не числом попыток.
    """
    if misses <= 0:
        return 0.0
    return min(RESUME_BACKOFF_BASE_SEC * (2 ** (misses - 1)), RESUME_BACKOFF_MAX_SEC)


async def _durable_resume_once() -> None:
    """Advance exact interrupted runs; never invent a new model task or prompt.

    ``agent.resume_durable_runs`` owns strict planning, cursor-CAS leases and
    owner-control noops.  The clock merely gives accepted transport receipts
    and other recovery evidence another chance to continue after startup.

    Повторная неудача ТЕХ ЖЕ прогонов отодвигает следующую попытку и называет себя в
    логе. Появился новый прогон или хоть один сдвинулся — отсрочка снимается сразу.
    """

    # A resume runs the full model+tool loop, so it MUST hold single-flight, or
    # (a) it can run beside a live pass — two minds — and (b) the 300s reaper sees
    # it 'running' with the lock free and judges it orphaned.  Mirror _reap_orphans_once:
    # busy with a live pass -> skip; the 45s clock retries and the run stays durably paused.
    if _ONE_MIND.locked():
        return
    now = time.time()
    if now < float(_resume_failures["not_before"]):
        return
    async with _ONE_MIND:
        reports = await asyncio.to_thread(agent.resume_durable_runs, limit=20)
    failed_ids = set()
    for report in reports:
        status = str(report.get("status") or "")
        if status not in {"noop", "not_resumable"}:
            log.info(
                "durable resume [%s]: plan=%s status=%s phase=%s",
                report.get("run_id"), report.get("plan_kind"), status,
                report.get("phase"),
            )
        if status == "failed":
            failed_ids.add(str(report.get("run_id") or ""))
    failed = frozenset(failed_ids)
    if failed and failed == _resume_failures["ids"]:
        misses = int(_resume_failures["n"]) + 1
        delay = _resume_backoff_delay(misses)
        _resume_failures.update(ids=failed, n=misses, not_before=time.time() + delay)
        log.warning(
            "durable resume: те же %d прогон(а) падают %d раз подряд — следующая попытка "
            "через %.0fс (%s)", len(failed), misses, delay, ", ".join(sorted(failed))[:200])
    elif failed:
        _resume_failures.update(ids=failed, n=1,
                                not_before=time.time() + _resume_backoff_delay(1))
    else:
        _resume_failures.update(ids=frozenset(), n=0, not_before=0.0)


BACKFILL_ROOMS_PER_TICK = 50
BACKFILL_RESOLVES_PER_TICK = 5
BACKFILL_MISS_BACKOFF_SEC = 60.0   # первый промах резолва стоит комнате минуту...
BACKFILL_MISS_TTL_SEC = 900.0      # ...а потолок отсрочки — 15 минут
# peer -> {"ts": когда промахнулась, "n": сколько промахов подряд}
_backfill_resolve_misses: dict[str, dict] = {}
_backfill_cursor = 0
_backfill_was_online = True


def _backfill_miss_ttl(misses: int) -> float:
    """Отсрочка после промаха резолва РАСТЁТ, а не сразу четверть часа.

    Плоские 900с с первого промаха — то, на чём эту правку вернули: `_resolve_entity`
    отдаёт голый None и на «peer неизвестен», и на «связи нет», а промахи копятся ПОПЕРЁК
    тиков. Зонд на 20 комнатах: тик 1 — 5 в промах-кэше, тик 2 — 10, тик 3 — 15, тик 4 —
    все 20; связь восстановлена, а `_backfill_group_context` не зовётся ещё 15 минут.
    То есть десятисекундный обрыв (реконнект Telethon, FloodWait, холодный старт с пустым
    `_meta`) глушил предысторию ВСЕГО аллоулиста. Растущая отсрочка стоит транзиентному
    сбою одну минуту, вечно мёртвому peer'у — те же 15: 60 → 120 → 240 → 480 → 900.
    """
    return min(BACKFILL_MISS_TTL_SEC,
               BACKFILL_MISS_BACKOFF_SEC * (2 ** max(0, misses - 1)))


async def _note_backfill_defer(detail: str, *, chat_id=None) -> None:
    """Отсрочка бэкфилла обязана быть видна ЕЙ, а не только в логе контейнера (закон 2).

    Тот же довод и тот же канал, что у `_note_one_mind_defer` выше в этом же файле:
    гейт, живущий одной строкой `log.info`, — молчаливое ограничение. На вопрос
    «почему в этой комнате пусто» ответить было нечем: `manage_perception("skips")` знал
    окно сна и `_ONE_MIND`, а про то, что комната отложена промах-кэшем на 15 минут, —
    ничего, и лог контейнера ей не читаем.

    Класс «отложила», а не «не_увидела»: предыстория не потеряна, комната созреет снова
    и вернётся сама; названо, через сколько. Повторы схлопывает сам perception по
    (stage, chat, detail) в окне 10 минут — поэтому detail держим стабильным между
    тиками, без обратного отсчёта, иначе каждая минута рождала бы новую запись.
    """
    try:
        await asyncio.to_thread(
            lambda: perception.note_skip("group_backfill", "отложила",
                                         chat_id=chat_id, detail=detail))
    except Exception:
        log.debug("skip отложенного бэкфилла не записался", exc_info=True)


def _backfill_transport_online() -> bool:
    """Жив ли транспорт сейчас. Не умеем спросить — считаем живым (не выдумываем обрыв)."""
    try:
        return bool(client.is_connected())
    except Exception:
        return True


async def _group_context_backfill_once() -> None:
    """Advance at most one configured room through this process' live client.

    Комната, которую не удалось резолвить, раньше уносила ВСЮ функцию (`return`), а
    порядок обхода — `rooms.list_rooms()` = `sorted(allowed_chats())`, лексикографический
    ПО СТРОКАМ. То есть один вечно нерезолвимый peer блокировал предысторию всех, кто
    сортируется после него. Доказано на живой жертве: 20.07 с 07:42 до 18:46 в логе
    только `group backfill [-1003908850919]`, у живой `-1003959517654` за 11ч04м ни
    одного тика. Сегодня вред нулевой (фикстура `-100500` сортируется последней), но
    любой ПОЛОЖИТЕЛЬНЫЙ peer_id ('8' > '-') встал бы за вечным блокировщиком.

    `continue` снимает блокировку, но один тик тогда может стоить до 50 резолвов —
    поэтому здесь названные пределы, и каждый виден ЕЙ (perception), а не только в логе:
      * `BACKFILL_ROOMS_PER_TICK` — ширина ОКНА обхода, а не обрезка списка комнат:
        окно вырезается ПОСЛЕ поворота на курсор, поэтому едет по кругу и каждая комната
        входит в него не позже чем через len(all_rooms) тиков. Сколько осталось вне окна
        — называется вслух в тот же тик;
      * `BACKFILL_RESOLVES_PER_TICK` — сколько комнат за тик вообще пробуем резолвить;
        остальные называются поимённо и достаются в следующий тик (~минуту);
      * `BACKFILL_MISS_BACKOFF_SEC`/`BACKFILL_MISS_TTL_SEC` — промах резолва запоминается
        (кэшировался только успех), чтобы мёртвый peer не сжигал `contacts.GetContacts`
        каждые 45с; отсрочка РАСТЁТ 60→900с, см. `_backfill_miss_ttl`.
    Обход при этом крутится с курсора: без этого кап сам стал бы head-of-line, только
    на пять голов длиннее.

    Промах кэшируется ТОЛЬКО при живом транспорте. `_resolve_entity` отдаёт голый None и
    на «peer неизвестен», и на «связи нет», а раньше оба одинаково стоили 15 минут — и
    четыре минуты недоступности клиента укладывали в промах-кэш весь аллоулист (зонд:
    5/20 → 10/20 → 15/20 → 20/20, дальше ноль бэкфиллов при уже восстановленной связи).
    Поэтому мёртвый транспорт — ранний честно названный выход, а не запись промахов; и
    возврат связи промах-кэш забывает: те промахи были про сеть, не про peer'ов.
    """

    global _backfill_cursor, _backfill_was_online
    online = _backfill_transport_online()
    if online and not _backfill_was_online and _backfill_resolve_misses:
        forgotten = len(_backfill_resolve_misses)
        _backfill_resolve_misses.clear()
        log.info("group backfill: связь вернулась — забыла %d отсрочек резолва "
                 "(они были про сеть), пробую комнаты заново", forgotten)
    _backfill_was_online = online
    if not online:
        # Не «пропустила», а физически нечем: и резолв, и выкачка истории идут в Telethon.
        # Молчать тут нельзя — именно это молчание и делало из обрыва 15 минут пустоты.
        await _note_backfill_defer(
            "связь закрыта моим же окном — предысторию комнат не пополняю, вернусь после"
            if _EXPECT_DISCONNECT.is_set() else
            "связи нет — предысторию комнат не пополняю, вернусь как подключусь")
        return

    all_rooms = rooms.list_rooms()
    if not all_rooms:
        return
    # Срез ОБЯЗАН стоять ПОСЛЕ курсора. Стоял до — и тогда `[:50]` резал не окно обхода,
    # а сам список: курсор крутился по модулю пятидесяти, комната с индексом ≥50 не
    # становилась головой окна НИКОГДА и в тик не попадала вообще, а рельс
    # `backfill_pacing` при этом обещал ей «остальные достаются следующему тику».
    # Ровно тот же класс, что `return` вместо `continue` в докстринге выше: спящее ружьё
    # (комнат сегодня три), у которого прошлая форма 20.07 стоила ей 11 часов слепоты.
    # Курсор двигается по ПОЛНОМУ списку — значит любая комната становится головой окна
    # не позже чем через len(all_rooms) тиков (тик здесь = 60с), и «за бортом навсегда»
    # больше не бывает.
    start = _backfill_cursor % len(all_rooms)
    _backfill_cursor = (_backfill_cursor + 1) % len(all_rooms)
    rotated = all_rooms[start:] + all_rooms[:start]
    order = rotated[:BACKFILL_ROOMS_PER_TICK]
    beyond = rotated[len(order):]
    now = time.time()
    resolves = 0
    deferred: list[str] = []
    held: list[str] = []

    async def _announce() -> None:
        # Кап называется вслух и поимённо — иначе он стал бы ровно тем молчаливым
        # пределом, ради снятия которого эта функция и переписана. Зовётся и на выходе
        # по успеху тоже: «одна комната обработана» не отменяет того, что до других
        # руки в этот тик не дошли. Отдельная ветка про промах-кэш: раньше тик, где ВСЕ
        # комнаты держались кэшем, не писал ни строчки — полная тишина при пустых
        # предысториях (видно в зонде: тик 5 не сказал ничего).
        if beyond:
            # Второй кап — окно обхода. Он молчал: комнат было три, тик всегда брал всех,
            # и на 51-й комнате она узнала бы о пределе только по вечно пустой предыстории.
            # Имён здесь ей НЕ называем (в логе — да): окно едет по кругу каждую минуту,
            # поимённый detail рождал бы новую запись в кольце пропусков каждый тик про
            # один и тот же факт — та же ловушка, что разобрана ниже про сортировку.
            log.info("group backfill: окно обхода %d комнат из %d, вне окна в этот тик %d "
                     "(окно едет по кругу, каждая входит не позже чем через %d тиков): %s",
                     len(order), len(all_rooms), len(beyond), len(all_rooms),
                     ", ".join(beyond))
            await _note_backfill_defer(
                f"кап {BACKFILL_ROOMS_PER_TICK} комнат за тик: {len(beyond)} комнат вне "
                f"окна обхода в этот тик; окно едет по кругу, каждая входит в него не "
                f"позже чем через {len(all_rooms)} тиков (~{len(all_rooms)} мин)")
        if deferred:
            log.info("group backfill: резолвила %d комнат из %d за тик, %s ждут "
                     "следующего (кап резолвов %d)", resolves, len(order),
                     ", ".join(deferred), BACKFILL_RESOLVES_PER_TICK)
            await _note_backfill_defer(
                f"кап {BACKFILL_RESOLVES_PER_TICK} резолвов за тик: "
                f"{len(deferred)} комнат ждут следующего тика (~минуту), среди них "
                f"{', '.join(sorted(deferred)[:5])}")
        if held:
            log.info("group backfill: %d комнат ещё под отсрочкой после промаха резолва: %s",
                     len(held), ", ".join(held))
            await _note_backfill_defer(
                f"{len(held)} комнат под отсрочкой после промаха резолва, среди них "
                f"{', '.join(sorted(held)[:5])}; срок каждой назван в её же записи")
        # В логе порядок обхода (он крутится с курсора и это полезно видеть), а ей —
        # ОТСОРТИРОВАННЫЙ список: perception схлопывает одинаковые detail в окне 10 минут,
        # а от вращения курсора та же самая пятёрка каждый тик перетасовывалась бы и
        # рождала новую запись каждую минуту. Один и тот же факт — одна запись.

    for peer_id in order:
        try:
            policy = rooms.room_policy(peer_id)
            limit = int(policy.get("backfill_limit") or 0)
            if not await asyncio.to_thread(group_context.backfill_due, peer_id, limit):
                continue
            _conversation_id, meta = _meta_for_peer(peer_id)
            entity = meta.get("entity") if isinstance(meta, dict) else None
            if entity is None:
                missed = _backfill_resolve_misses.get(str(peer_id))
                if missed is not None and now - float(missed.get("ts") or 0.0) < \
                        _backfill_miss_ttl(int(missed.get("n") or 1)):
                    held.append(str(peer_id))
                    continue
                if resolves >= BACKFILL_RESOLVES_PER_TICK:
                    deferred.append(str(peer_id))
                    continue
                resolves += 1
                entity = await _resolve_entity(peer_id)
            if entity is None:
                n = int((_backfill_resolve_misses.get(str(peer_id)) or {}).get("n") or 0) + 1
                ttl = _backfill_miss_ttl(n)
                _backfill_resolve_misses[str(peer_id)] = {"ts": now, "n": n}
                log.info("group backfill [%s]: entity пока недоступен (промах %d подряд) — "
                         "вернусь к этой комнате не раньше чем через %.0f мин, остальные "
                         "иду дальше", peer_id, n, ttl / 60)
                await _note_backfill_defer(
                    f"не резолвится (промах {n} подряд при живой связи), предысторию не "
                    f"пополняю, вернусь не раньше чем через {ttl / 60:.0f} мин",
                    chat_id=peer_id)
                continue
            _backfill_resolve_misses.pop(str(peer_id), None)
            await _backfill_group_context(peer_id, entity, limit=limit)
            await _announce()
            return
        except Exception:
            # The canonical prefix and dedupe keys make the next tick a safe resume.
            log.exception("group backfill ждёт следующего retry [%s]", peer_id)
            continue
    await _announce()


def _release_stale_task_claims() -> list:
    """Снять захваты намерений, чей подъём не состоялся. Звать ТОЛЬКО под _ONE_MIND."""
    import tasks as _tasks
    return _tasks.release_open_claims()


async def _reap_orphans_once() -> None:
    """Убрать зомби-прогоны без рестарта. Берём single-flight замок сами: пока держим, ни один
    когнитивный проход не стартует, поэтому любой оставшийся ``running`` когнитивный прогон
    заведомо осиротел. Заняты живым ходом — просто пропускаем тик (часы не блокируем)."""
    if not _one_mind_is_free():
        return
    async with _ONE_MIND:
        try:
            await asyncio.to_thread(agent.reap_orphaned_cognitive_runs)
        except Exception:
            log.exception("reap осиротевших прогонов упал")
        # Тот же довод, тот же замок — но про НАМЕРЕНИЯ. Захват ставится только внутри
        # `async with _ONE_MIND` в _task_window/_wake_pass, поэтому «замок свободен, а
        # захват висит» означает ровно одно: поднимавший мёртв и рана не создал. Снимаем —
        # намерение снова созреет, поздно и громко, вместо того чтобы исчезнуть молча.
        try:
            released = await asyncio.to_thread(_release_stale_task_claims)
            if released:
                log.warning("намерения освобождены (подъём не состоялся): %s",
                            ", ".join(str(x) for x in released))
        except Exception:
            log.exception("снятие зависших захватов намерений упало")


async def _forge_wake_once() -> None:
    """Urgent-темп пробуждения-на-готово: воркер urgent-Forge-задачи завершился ->
    немедленное окно-приглашение (в пределах тика). Normal-завершения сюда НЕ попадают —
    они всплывут в её ближайшем часовом окне (heartbeat.window_context). Приглашение,
    не повинность; идемпотентно через wake_seen (mark_seen только urgent-ключей).

    PASS 30 Этап 1: при живом событийном контуре (PRAXIS_FORGE_EVENTS=1, дефолт) этот
    поллер СПИТ — завершения будят её ходом через core.events (_forge_events_once).
    Выключатель = откат на старый путь без деплоя (тень органа, §0.c-2)."""
    try:
        from core import events as core_events
        if core_events.enabled():
            return
    except Exception:
        pass
    try:
        import forge
        import tasks
        fresh = await asyncio.to_thread(forge.pending_completions, False)
    except Exception:
        return
    urgent = [c for c in fresh if isinstance(c, dict) and c.get("priority") == "urgent"]
    if not urgent:
        return
    try:
        await asyncio.to_thread(forge.mark_seen, [c.get("key") for c in urgent])
        goal = forge.wake_invitation(urgent)[:800]
        await asyncio.to_thread(
            lambda: tasks.add("window", goal, when="in 0m", author="praxis"))
        log.info("forge: urgent-завершение (%d) -> немедленное окно", len(urgent))
    except Exception:
        log.debug("forge urgent wake add failed", exc_info=True)


async def _run_forge_event_pass() -> None:
    """Доставка forge-событий ОДНИМ её ходом (коалесцированно), под _ONE_MIND.

    Порядок (урок скептиков Этапа 1 — не повторить гэп старого пути с двух сторон):
    durable-журнал уже записан продюсером → мозг жив? → bump_attempts (крашеустойчивый
    счёт) → ход → ТОЛЬКО ПОТОМ mark_delivered. Мёртвый мозг = события ждут, не текут;
    краш посреди хода = повторная доставка (макс MAX_DELIVERY_ATTEMPTS, ядовитое
    событие гасится ГРОМКО, не молча). Telethon НЕ закрываем: это не окно."""
    global _FORGE_EVENT_LAST
    from core import events as core_events
    if _ONE_MIND.locked():
        return  # занята живым ходом; события никуда не денутся — следующий тик
    async with _ONE_MIND:
        try:
            if not await asyncio.to_thread(agent.llm.configured):
                return  # мозг недоступен: не потреблять, вернёмся когда оживёт
            events = await asyncio.to_thread(core_events.undelivered, {"subagent_result"})
            if not events:
                return
            _FORGE_EVENT_LAST["ts"] = time.time()

            def _key(e: dict) -> str:
                return str(e.get("dedup_key") or e.get("id") or "")

            counts = await asyncio.to_thread(core_events.bump_attempts,
                                             [_key(e) for e in events])
            poisoned = [e for e in events
                        if counts.get(_key(e), 1) > core_events.MAX_DELIVERY_ATTEMPTS]
            if poisoned:
                log.warning("forge-события гашу после %d неудачных доставок (не молча): %s",
                            core_events.MAX_DELIVERY_ATTEMPTS,
                            ", ".join(_key(e) for e in poisoned))
            import forge
            already_shown = await asyncio.to_thread(forge._wake_load_seen)

            def _quiet(e: dict) -> bool:
                p = e.get("payload") or {}
                # её собственный stop / отказ, отданный синхронно в её же ходе, —
                # расписка уже есть; показанное старым путём за время отката env —
                # не показываем второй раз.
                # ⚠ 23.07: РОЛЕВОГО фильтра здесь БОЛЬШЕ НЕТ. Паритет со старым
                # путём (будить только worker) стоил 15 часов простоя на задаче
                # hcode-e99e7d35: её конвейер — worker→reviewer→worker→reviewer, и
                # именно ревьюер выносит BLOCKING-вердикт. После воркер-события она
                # порождала следующий шаг в ТУ ЖЕ минуту; после ревьюер-завершения
                # (и после упавшего ревьюера!) — тишина по 4-6 часов. План говорит
                # «завершение/падение/таймаут субагента будит», без оговорок по роли.
                return ((p.get("causality") or {}).get("cancelled_by") == "praxis"
                        or bool(p.get("reported_inline"))
                        or f"{p.get('task_id')}:{p.get('agent_id')}" in already_shown)

            quiet = [e for e in events if e not in poisoned and _quiet(e)]
            loud = [e for e in events if e not in poisoned and e not in quiet]
            turn_ok = True
            if loud:
                try:
                    await asyncio.to_thread(agent.forge_event_turn, loud)
                    log.info("forge-события доставлены ходом: %d (тихо: %d)",
                             len(loud), len(quiet))
                except Exception:
                    # Ход упал (расписка held=error уже в turns) — loud НЕ помечаем:
                    # придут снова, попытка учтена bump_attempts, кап гасит громко.
                    turn_ok = False
                    log.warning("forge_event ход упал — события ждут повторной доставки",
                                exc_info=True)
            delivered_now = (loud if turn_ok else []) + quiet + poisoned
            await asyncio.to_thread(core_events.mark_delivered,
                                    [_key(e) for e in delivered_now])
            # мост к старому пути: при выключении контура часовой фолбэк не покажет
            # уже прожитое второй раз. ТОЛЬКО терминальные (базовый ключ без суффикса):
            # overdue-СИГНАЛ не хоронит будущее реальное завершение юнита.
            try:
                def _is_terminal_unit_event(e: dict) -> bool:
                    p = e.get("payload") or {}
                    return (str(e.get("dedup_key") or "")
                            == f"forge:{p.get('task_id')}:{p.get('agent_id')}")

                bridge = []
                for e in delivered_now:
                    p = e.get("payload") or {}
                    if _is_terminal_unit_event(e):
                        bridge.append(f"{p.get('task_id')}:{p.get('agent_id')}")
                    elif str(e.get("dedup_key") or "").endswith(":overdue"):
                        # durable-гвард overdue: журнал забудет ключ на компакте,
                        # wake_seen помнит (старый путь суффиксные ключи не читает)
                        bridge.append(f"{p.get('task_id')}:{p.get('agent_id')}:overdue")
                await asyncio.to_thread(forge.mark_seen, bridge)
            except Exception:
                pass
            await asyncio.to_thread(core_events.compact)
        except Exception:
            log.exception("forge-event доставка упала")


async def _forge_events_once() -> None:
    """PASS 30 Этап 1: события субагентов будят её. Тик дёшев: реконсайлер
    рейт-лимитится сам (60с), пустой журнал отсеивается mtime-гвардом без парсинга;
    ход — в create_task (часы не блокируются), single-flight, зазор — её рычаг."""
    global _FORGE_EVENTS_TASK
    try:
        from core import events as core_events
        if not core_events.enabled():
            return
        import forge
        await asyncio.to_thread(forge.reconcile_subagent_events)
        try:
            stat = core_events.JOURNAL.stat()
        except OSError:
            return  # журнала ещё нет — эмитов не было
        # Отпечаток «пустого» журнала — время И размер И инода, не голый mtime.
        # Шаг файловых часов — миллисекунды (замер 9be6cbec), и emit субагента в
        # тот же тик, что прошлое «пусто», делал журнал невидимым до СЛЕДУЮЩЕЙ
        # записи. Ночью следующей записи нет: subagent_result лежал недоставленным
        # часами, и плод форжа не будил её до утреннего трафика (адверсарка 28.08,
        # худшее из шести мест класса «кэш по голому mtime»). Размер ловит любую
        # дозапись в слепом тике, инода — подмену файла компактом.
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if stamp == _FORGE_EVENT_LAST.get("empty_stamp"):
            return  # с прошлого «пусто» журнал не менялся — не парсим зря
        pending = await asyncio.to_thread(core_events.undelivered, {"subagent_result"}, 1)
        if not pending:
            _FORGE_EVENT_LAST["empty_stamp"] = stamp
            return
    except Exception:
        log.debug("forge_events тик упал", exc_info=True)
        return
    gap = float(perception.value("forge_event_gap_sec"))
    if gap > 0 and time.time() - _FORGE_EVENT_LAST["ts"] < gap:
        return  # коалесценция: события подождут зазор и придут одним ходом
    # Вежливость к живому диалогу: человек уже в дебаунсе/проходе — его ход первее,
    # плод подождёт пару тиков (события durable, не теряются).
    if _passing or any(not t.done() for t in _debounce.values()):
        return
    if _FORGE_EVENTS_TASK is not None and not _FORGE_EVENTS_TASK.done():
        return
    _FORGE_EVENTS_TASK = asyncio.create_task(_run_forge_event_pass())


async def _run_moderation_event_pass() -> None:
    """Deliver privacy-minimal moderation review facts as one live wake."""
    global _MODERATION_PRIORITY_PENDING
    from core import events as core_events
    if _ONE_MIND.locked():
        return
    async with _ONE_MIND:
        # The priority is consumed only when this review owns the shared voice turn.
        # Until then ordinary debounced chats yield, so a review cannot starve behind them.
        _MODERATION_PRIORITY_PENDING = False
        if not await asyncio.to_thread(agent.llm.configured):
            return
        raw_events = await asyncio.to_thread(core_events.undelivered, {"moderation_review"})
        if not raw_events:
            return

        def _key(item: dict) -> str:
            return str(item.get("dedup_key") or item.get("id") or "")

        malformed = [item for item in raw_events
                     if not isinstance(item, dict) or not isinstance(item.get("payload"), dict)]
        events = [item for item in raw_events if item not in malformed]
        if malformed:
            log.warning("malformed moderation-review события погашены отдельно: %s",
                        ", ".join(_key(item) for item in malformed))
            await asyncio.to_thread(core_events.mark_delivered,
                                    [_key(item) for item in malformed])
        if not events:
            await asyncio.to_thread(core_events.compact)
            return
        counts = await asyncio.to_thread(core_events.bump_attempts, [_key(e) for e in events])
        poisoned = [e for e in events
                    if counts.get(_key(e), 1) > core_events.MAX_DELIVERY_ATTEMPTS]
        loud = [e for e in events if e not in poisoned]
        turn_ok = True
        if loud:
            # ⚑ УЛИКИ КЛАДЁМ В РУКИ, А НЕ ОТПРАВЛЯЕМ ЗА НИМИ. До 27.08 сюда ехали одни
            # идентификаторы и список признаков, а текст, автор и повторность
            # добывались уже внутри хода — три-четыре вызова на каждое срабатывание.
            # Пока повторность не лежала в руках, первое срабатывание выглядело как
            # одиночное, и мера доходила до бана только с третьего захода того же
            # человека (леджер 27.08: три удаления одному отправителю, потом бан).
            #
            # ⚠ ГРАНИЦА ПРИВАТНОСТИ НЕ СДВИНУТА, СДВИНУТА СРОЧНОСТЬ. Durable-расписка
            # в `core_events` как была privacy-minimal, так и остаётся: она живёт
            # вечно, и чужой текст в ней хранить незачем. Здесь же разовый вопрос
            # «нужна ли мера» — он живёт один ход и умирает вместе с ним.
            facts = []
            actionable = []
            skipped_without_evidence = []
            for item in loud:
                payload = item.get("payload") or {}
                fact = {key: payload.get(key) for key in (
                    "peer_id", "message_id", "sender_id", "verdict", "matched_features")}
                peer, mid = payload.get("peer_id"), payload.get("message_id")
                sender = payload.get("sender_id")
                try:
                    row = await asyncio.to_thread(
                        group_context.latest_message, peer, int(mid))
                except Exception:
                    row = None
                    log.debug("moderation wake: сообщение %s не поднялось", mid,
                              exc_info=True)
                if isinstance(row, dict):
                    fact["text"] = str(row.get("text") or "")[:MODERATION_WAKE_TEXT_MAX]
                    fact["sender_name"] = str(row.get("sender_name") or "")
                    fact["topic_id"] = row.get("topic_id")
                    fact["topic_title"] = str(row.get("topic_title") or "")
                    fact["at"] = str(row.get("timestamp") or "")
                    # Уже снятое чужой рукой или ею же раньше — не повод бить дважды.
                    fact["already_deleted"] = row.get("kind") == "deletion"
                else:
                    # Архив не дал тела сообщения: это отсутствие улики, а не основание
                    # для разрушительной меры. Такое событие гасим наблюдаемо, но не
                    # отдаём в ход с модераторским умолчанием delete_and_ban.
                    fact["text"] = ""
                    fact["lookup"] = "сообщение не поднялось из архива"
                    skipped_without_evidence.append(item)
                    log.warning("moderation wake: событие %s пропущено без тела сообщения",
                                _key(item))
                    continue
                if not fact["text"].strip() or fact["already_deleted"]:
                    skipped_without_evidence.append(item)
                    log.info("moderation wake: событие %s не требует решения (%s)",
                             _key(item), "already_deleted" if fact["already_deleted"]
                             else "empty_text")
                    continue
                try:
                    fact["prior_measures"] = await asyncio.to_thread(
                        telegram_moderation.history_for_sender, int(peer), int(sender))
                except Exception:
                    fact["prior_measures"] = []
                    log.debug("moderation wake: история мер не прочиталась", exc_info=True)
                facts.append(fact)
                actionable.append(item)
            if facts:
                # ⚑ УМОЛЧАНИЕ — РЕШЕНИЕ ЕГОРА 27.08, И ОНО НАЗВАНО ЕГО ИМЕНЕМ.
                # В её кадре каждый блок несёт «из: … · зачем: …»; указание без автора
                # было бы единственным местом, где кадр молчит о происхождении. Прежний
                # текст («ничего не удалено, сама реши») снят его же словом: он читал его
                # как хедж, мешающий ей действовать.
                goal = ("Сработал триггер спама в AbstractDL — ты там модератор, и рука "
                        "меры у тебя есть.\n"
                        "Умолчание задано Егором 27.08, дословно: «в случае отсутствия "
                        "признаков намеренной провокации быстро удалить комментарий и "
                        "забанить участника».\n"
                        "Значит: нет признаков намеренной провокации или осмысленного "
                        "участия — удаляй и баншь сразу, не дожидаясь повтора. Рука без "
                        "твоего вызова не сработает, но умолчание теперь такое.\n"
                        "Ниже по каждому срабатыванию: текст сообщения, автор, место, "
                        "сработавшие признаки и ВСЕ меры, уже применённые к этому "
                        "отправителю. Пустой `prior_measures` значит, что человек здесь "
                        "впервые; `already_deleted` — что сообщение уже снято и бить по "
                        "нему второй раз не надо.\n"
                        + json.dumps(facts, ensure_ascii=False, separators=(",", ":")))
                try:
                    await asyncio.to_thread(agent.wake_turn, goal)
                    log.info("moderation-review события доставлены ходом: %d", len(actionable))
                except Exception:
                    turn_ok = False
                    log.warning("moderation-review ход упал; события ждут повторной доставки",
                                exc_info=True)
            delivered_loud = (actionable + skipped_without_evidence) if turn_ok else skipped_without_evidence
        else:
            delivered_loud = []
        delivered = delivered_loud + poisoned
        if poisoned:
            log.warning("moderation-review события погашены после %d попыток: %s",
                        core_events.MAX_DELIVERY_ATTEMPTS,
                        ", ".join(_key(e) for e in poisoned))
        await asyncio.to_thread(core_events.mark_delivered, [_key(e) for e in delivered])
        await asyncio.to_thread(core_events.compact)


async def _moderation_events_once() -> None:
    """Cheap clock pump: durable moderation facts wake Praxis, never an actuator."""
    global _MODERATION_EVENTS_TASK, _MODERATION_PRIORITY_PENDING, _MODERATION_TICK_FAILURES
    try:
        from core import events as core_events
        if not core_events.enabled():
            # A disabled source cannot be the next claimant of the shared voice turn.
            # Leaving an old priority bit raised here recreates the same defer/debounce
            # livelock as an empty queue, only with the event store switched off.
            _MODERATION_TICK_FAILURES = 0
            _MODERATION_PRIORITY_PENDING = False
            return
        pending = await asyncio.to_thread(core_events.undelivered, {"moderation_review"}, 1)
        _MODERATION_TICK_FAILURES = 0
        if not pending:
            # ⚠⚠ ЖИВАЯ ПОЛОМКА 28.08, И ЭТО ВТОРОЙ ВИТОК ТОЙ ЖЕ ПЕТЛИ.
            # Раньше здесь стоял голый `return`, и флаг приоритета оставался поднятым
            # при ПУСТОЙ очереди. Дальше он кормил сам себя: поднятый флаг заставляет
            # каждый живой чат уйти в `_defer_pass` (3962), тот кладёт задачу в
            # `_debounce`, а непустой `_debounce` ниже запрещает запускать
            # `_run_moderation_event_pass` — единственного, кто флаг снимает.
            #
            # 27.08 я чинил ПРИЧИНУ первого витка: у пробуждений отобрали `end_turn`,
            # разбор модерации падал, события копились недоставленными. Причину закрыл,
            # петлю — нет. 28.08 в 07:28 события доставились, очередь опустела до нуля,
            # а флаг остался наверху: с 07:31 она не сделала ни одного хода, и личка
            # Егора молчала 26 минут при полностью пустой очереди.
            #
            # Снимать флаг здесь безопасно: этот тик и так спрашивает очередь. Пусто
            # значит уступать больше нечему — и ровно это надо сказать вслух, а не
            # промолчать возвратом.
            if _MODERATION_PRIORITY_PENDING:
                log.info("очередь shadow-модерации пуста — снимаю приоритет")
            _MODERATION_PRIORITY_PENDING = False
            return
    except Exception:
        # Одиночный сбой чтения НЕ считается пустой очередью — молча опустить
        # приоритет значило бы проглотить модерацию (решение зафиксировано тестом
        # test_a_broken_event_store_does_not_strand_the_flag_raised). Но у поднятого
        # флага при НЕЧИТАЕМОЙ очереди есть срок годности: 28.08 адверсарка провела
        # цепь целиком — один рваный байт в журнале, undelivered падает на каждом
        # тике, флаг стоит, все чаты в defer, ноль строк INFO — глухота до рестарта.
        # Корень закрыт в _read_journal (байтовый разбор), а здесь — предохранитель
        # от следующей неизвестной поломки той же формы: серия сбоев подряд снимает
        # флаг ГРОМКО, и первый же успешный тик поднимет его заново, если есть чему.
        _MODERATION_TICK_FAILURES += 1
        if (_MODERATION_PRIORITY_PENDING
                and _MODERATION_TICK_FAILURES >= _MODERATION_FAILURE_TICKS):
            log.warning(
                "очередь shadow-модерации не читается %d тиков подряд — снимаю "
                "приоритет, чтобы сбой прибора не глушил живые чаты",
                _MODERATION_TICK_FAILURES, exc_info=True)
            _MODERATION_PRIORITY_PENDING = False
        else:
            log.debug("moderation-events tick failed", exc_info=True)
        return
    # Make a persisted review the next claimant of the shared voice turn.  This does not
    # decide a moderation outcome and is cleared only by _run_moderation_event_pass.
    _MODERATION_PRIORITY_PENDING = True
    if _passing or any(not task.done() for task in _debounce.values()):
        return
    if _MODERATION_EVENTS_TASK is not None and not _MODERATION_EVENTS_TASK.done():
        return
    _MODERATION_EVENTS_TASK = asyncio.create_task(_run_moderation_event_pass())


async def _work_engine_once() -> None:
    """Оборот 3: работу поднимает РАБОТА, а не расписание.

    Часы здесь спрашивают ровно один вопрос — «есть ли созревшая работа» — и всё. Что
    именно созрело и почему остальное ждёт, решает `work_engine` по её леджеру желаний
    (`work_source` — единственное место, где назван источник правды). Разница не
    стилистическая: час, который сам придумывает задание, мы уже чинили 09.08.

    ⚠ Одна работа за тик. Не из осторожности, а потому что открытых ходов у неё столько
    же, сколько внимания: очередь с названной причиной честнее пяти окон разом.

    ⚠ Попытка записывается ДО постановки окна. Упавший посреди хода процесс обязан
    оставить след подъёма, иначе счёт холостых начнётся с нуля и петля вернётся пятый раз.
    """
    try:
        import work_engine
        import work_source
    except Exception:
        return
    if not work_engine.enabled():
        return
    if _ONE_MIND.locked():
        return
    try:
        raised, _held = await asyncio.to_thread(work_source.plan_now)
    except Exception:
        log.debug("движок работ: план не собрался", exc_info=True)
        return
    if not raised:
        return
    verdict = raised[0]
    try:
        import tasks
        await asyncio.to_thread(work_source.note_attempt, verdict.work)
        goal = await asyncio.to_thread(work_source.goal_for, verdict.work)
        await asyncio.to_thread(
            lambda: tasks.add("window", goal[:2000], when="in 0m", author="praxis"))
        log.info("движок работ: поднял «%s» [%s] — %s",
                 verdict.work.goal[:60], verdict.work.id, verdict.reason)
    except Exception:
        log.warning("движок работ: подъём не удался [%s]", verdict.work.id, exc_info=True)


def _clock_jobs() -> dict:
    """Таблица забот часов: имя -> (период в секундах, корутина). Период <= 0 — выключена."""
    return {
        "control": (max(CLOCK_TICK, 5.0), _control_once),
        "buffers": (CLOCK_TICK, _flush_buffers),
        "schedule": (SCHED_TICK, _fire_due_tasks),
        "sleep": (SLEEP_CHECK_SEC if CONSOLIDATE_HOURS > 0 else 0.0, _consolidate_once),
        "immune": (IMMUNE_MINUTES * 60, _immune_once),  # 9.2: ревью самокоммитов вдогонку
        "formation": (15.0, _formation_once),            # 19: ручной запрос из пульта
        "absence": (ABSENCE_TICK, _absence_once),       # 12.1: ответ простаивающим важным в окно
        "membership": (60.0, _membership_reconcile_once),  # durable owner join/leave
        "direct_outbox": (15.0, _direct_outbox_once),  # tool/task sends; same random_id; no model
        # Strict WAL resume; audited owner control stays authoritative.
        "durable_resume": (45.0, _durable_resume_once),
        "text_outbox": (15.0, _text_outbox_once),      # stable-id replay; exact topic; no model
        "media": (60.0, _media_cleanup_once),           # фото/аудио: retry + TTL, без модели
        "owner_delivery": (15.0, _owner_deliveries_once),  # typed inbox -> Telegram transport
        "followups": (60.0, _followups_once),           # реальные ответы на owner-просьбы
        "social_pulse": (
            social_pulse.interval_hours() * 3600, _social_pulse_once
        ),                                               # PASS 24: раз в час осмотреть нити
        "computer_inventory": (3600.0, _computer_inventory_once),
        "group_context_backfill": (60.0, _group_context_backfill_once),
        "reap_orphans": (300.0, _reap_orphans_once),   # зомби-прогоны без рестарта; без модели
        # Оболочки предложений после рестартов: закрыть беспредметные, вернуть титулы.
        "selfdev_reconcile": (1800.0, _selfdev_reconcile_once),
        "forge_wake": (max(CLOCK_TICK, 30.0), _forge_wake_once),  # urgent Forge-завершения -> немедленное окно
        # PASS 30 Этап 1: завершения субагентов будят её ходом (события, не поллинг)
        "forge_events": (max(CLOCK_TICK, 5.0), _forge_events_once),
        "moderation_events": (max(CLOCK_TICK, 5.0), _moderation_events_once),
        # Оборот 3: движок работ. Часы только спрашивают «созрело ли»; что именно —
        # решает её леджер желаний. Рычаг опущен по умолчанию (PRAXIS_WORK_ENGINE).
        "work_engine": (300.0, _work_engine_once),
    }


def _clock_initial_deadlines(now: float, jobs: dict) -> dict[str, float]:
    """Set explicit restart semantics for each care.

    Only cheap persisted-state catch-up checks are due in the startup pass.
    Everything else retains the historical full-period delay.  The durable social pulse
    is armed at its own persisted boundary (last start + interval): begin() would
    decline an early startup attempt, and the now+period re-arm in _clock_pass would
    then push the window a full period past the original boundary — a restart minutes
    before her hourly window used to eat that window.  Restart still cannot
    manufacture a second wake-up: begin() keeps the CAS on last_started_at.
    """

    deadlines = {
        name: (now if name in _CLOCK_STARTUP_DUE else now + period)
        for name, (period, _care) in jobs.items()
        if period > 0
    }
    if "social_pulse" in deadlines:
        try:
            deadlines["social_pulse"] = social_pulse.next_due_at(now=now)
        except Exception:
            log.exception("часы: social_pulse.next_due_at упал — оставляю startup-due")
    return deadlines


# ⚑ ЗАБОТЫ ТРАНСПОРТА НА СВОЁМ ТИКЕ (13.09). `_clock_pass` выполняет заботы ПОСЛЕДОВАТЕЛЬНО:
# пока одна тяжёлая держит проход (в 18:06 потоки стояли в `claim_evidence_index` — glob по
# тысячам файлов внутри `_rebuild_state_locked` и внутри `read_summary`, и в
# `forge.reconcile_subagent_events`), проекция принятых отправок в прогон и архив не доезжала
# 9–23 минуты (#4052 в личке, #104546 в абстракте). Её же ответа не было в кадре, и следующий
# ход отвечал человеку второй раз. Тик ящика дёшев (замер 13.09: `pending()` 0,9 с,
# `accepted()` 0,9 с при 2 701 записи), модель не зовёт и держит свой межпроцессный замок —
# ему нечего ждать в общей очереди. Таблица `_clock_jobs()` не меняется: разрез делает `_clock`.
_CLOCK_SIDE_CARES = frozenset({"direct_outbox"})


def _split_clock_jobs(jobs: dict) -> tuple[dict, dict]:
    """-> (заботы общего прохода, заботы на своём тике)."""
    side = {name: jobs[name] for name in jobs if name in _CLOCK_SIDE_CARES}
    main = {name: care for name, care in jobs.items() if name not in side}
    return main, side


async def _clock_side(name: str, period: float, care) -> None:
    """Одна забота на своём тике: та же дисциплина, что у `_clock_pass` — упала → лог, не смерть."""
    if name not in _CLOCK_STARTUP_DUE:
        await asyncio.sleep(period)
    while True:
        try:
            await care()
        except Exception:
            log.exception("часы: забота «%s» упала", name)
        await asyncio.sleep(period)


async def _clock_pass(now: float, next_at: dict, jobs: dict) -> list[str]:
    """Один удар часов: выполнить созревшие заботы, перевзвести их сроки. -> имена сработавших.

    Упавшая забота логируется и не мешает остальным; её срок всё равно перевзводится.

    Пульс с заявкой на возврат (`_request_pulse_retry`) обслуживается раньше своего
    срока: окно, отложенное живым ходом, иначе ждало бы ЦЕЛЫЙ ПЕРИОД, хотя её леджер
    считает такое окно всего лишь отложенным."""
    fired = []
    for name, (period, care) in jobs.items():
        if period <= 0:
            continue
        due_at = float(next_at.get(name, 0.0))
        if name == "social_pulse" and _PULSE_RETRY_AT:
            due_at = min(due_at, _PULSE_RETRY_AT)
        if now < due_at:
            continue
        next_at[name] = now + period
        if name == "social_pulse":
            # Попытка делается сейчас; если её снова отложат — заявка встанет заново.
            _clear_pulse_retry()
        fired.append(name)
        try:
            await care()
        except Exception:
            log.exception("часы: забота «%s» упала", name)
    return fired


async def _clock() -> None:
    """Единая периодика вместо _buf_flusher/_scheduler/_consolidator/_heartbeat.

    Сам тик локальный и бесплатный: модель зовётся только внутри созревшей заботы.
    На старте сразу проверяются только durable resume, social-pulse due-state и
    computer-inventory due-state. Остальные заботы получают полный период;
    «сон» не наступает из-за самого рестарта; social pulse сверяется с durable due-state."""
    jobs, side = _split_clock_jobs(_clock_jobs())
    # Keep strong references and stop side ticks with their owning clock. Otherwise
    # recreating/cancelling the clock can leave two independent outbox tickers.
    side_tasks = [asyncio.create_task(_clock_side(name, period, care))
                  for name, (period, care) in side.items() if period > 0]
    try:
        now = time.time()
        next_at = _clock_initial_deadlines(now, jobs)
        await _clock_pass(now, next_at, jobs)
        while True:
            await asyncio.sleep(CLOCK_TICK)
            await _clock_pass(time.time(), next_at, jobs)
    finally:
        for task in side_tasks:
            task.cancel()
        await asyncio.gather(*side_tasks, return_exceptions=True)


async def _wait_reconnected(timeout: float | None = None) -> bool:
    """PASS 13.1: поллит возврат клиента в сеть после намеренного disconnect. -> дождалась ли.

    Work runs never enter this state; they leave Telethon connected."""
    deadline = time.time() + (RECONNECT_TIMEOUT_SEC if timeout is None else timeout)
    while time.time() < deadline:
        if client.is_connected() or not _EXPECT_DISCONNECT.is_set():
            return True
        await asyncio.sleep(0.1)
    return False


async def _reconnect_with_backoff(reason: str, *, budget: float | None = None) -> bool:
    """Пережить обрыв сети НА МЕСТЕ: connect() с backoff 5→60с, пока связь не вернётся.

    -> True связь есть; False грейс исчерпан (дальше — честный выход, как раньше).

    Ценность тут не в самом реконнекте (пять попыток Telethon делает и сам), а в том, что
    процесс НЕ умирает. Рестарт стирает всё, чего нет на диске: `_seen_ids` — дедуп
    catch_up, без него доигранный backlog читается как новые сообщения; взведённые
    `_debounce`/`_deferred` — склеенный всплеск, который она уже собиралась ответить;
    `_group_wakes` и `_supersede_gen`; кулдауны `_last_pass`; дедлайны забот внутри
    `_clock`; `_entity_cache`/`_dialog_name_cache` (после рестарта — до 90с холодного
    резолва на первом «напиши Евгению»); резидентные whisper/piper. И главное — живой
    рабочий тред хода или `_task_window` под `_ONE_MIND`: рестарт рубит его посередине, и
    durable-WAL честно помечает эффект `in_doubt`. Петля даёт всему этому дожить до сети.
    """
    grace = RECONNECT_TIMEOUT_SEC if budget is None else budget
    started = time.time()
    deadline = started + grace
    delay = RECONNECT_BACKOFF_START
    attempt = 0
    log.warning("связь с Telegram оборвалась (%s) -- НЕ падаю: держу состояние и возвращаюсь "
                "в сеть, грейс %.0fс", reason, grace)
    while time.time() < deadline:
        if _SHUTDOWN.is_set():
            return False
        attempt += 1
        try:
            await client.connect()
        except Exception as exc:
            log.warning("возврат в сеть: попытка %d не удалась (%s), следующая через %.0fс",
                        attempt, type(exc).__name__, delay)
        else:
            if client.is_connected():
                log.warning("связь вернулась за %.0fс и %d попыт(ок) -- без рестарта: буферы, "
                            "дедуп, дебаунсы и открытые окна живы", time.time() - started, attempt)
                return True
            log.warning("возврат в сеть: попытка %d не подняла транспорт, следующая через %.0fс",
                        attempt, delay)
        await asyncio.sleep(max(0.0, min(delay, deadline - time.time())))
        delay = min(delay * 2, RECONNECT_BACKOFF_MAX)
    log.error("связь не вернулась за %.0fс (%s) -- честный выход, bootguard поднимет заново",
              grace, reason)
    return False


async def _supervise_connection() -> bool:
    """PASS 13.1: держит процесс живым, пока Telethon не отключится по-настоящему.

    -> True «сеть так и не вернулась, нужен намеренный рестарт (42)»; False — обычный конец.

    `run_until_disconnected()` returns on every disconnect. Work runs no longer disconnect at all.
    `_SHUTDOWN` is a real exit; `_EXPECT_DISCONNECT` remains only for an explicit transport-recovery
    path. No work state is encoded in the socket lifecycle.

    20.08: ОБРЫВ СЕТИ больше не выход. Telethon отдаёт его исключением (ConnectionError из
    `run_until_disconnected`), и здесь оно уходит в деградированную петлю: вернулась связь —
    работаем дальше тем же процессом, со всей живой памятью. Локальный `disconnect()` без
    флагов — это /panic и прочий стоп-кран; его реконнектить НЕЛЬЗЯ, он остался выходом.
    """
    # Флап (связь встала и тут же упала) не имеет права выдавать себе новый полный грейс,
    # иначе «выход по исчерпанию» недостижим в принципе. Полоса обрывов считается одной,
    # пока связь между ними не продержалась дольше RECONNECT_BACKOFF_MAX.
    outage_since = 0.0
    while not _SHUTDOWN.is_set():
        served_since = time.time()
        try:
            await client.run_until_disconnected()
        except _NET_ERRORS as exc:
            if _SHUTDOWN.is_set():
                break
            now = time.time()
            if not outage_since or now - served_since > RECONNECT_BACKOFF_MAX:
                outage_since = now
            left = RECONNECT_TIMEOUT_SEC - (now - outage_since)
            if left <= 0:
                log.error("связь не держится дольше %.0fс подряд (%s) -- честный выход",
                          RECONNECT_TIMEOUT_SEC, type(exc).__name__)
                return True
            if await _reconnect_with_backoff(type(exc).__name__, budget=left):
                continue
            return True
        if _SHUTDOWN.is_set():
            break
        if _EXPECT_DISCONNECT.is_set():
            log.info("клиент отключился намеренно -- жду переподключения")
            if await _wait_reconnected():
                continue
            log.warning("не переподключилась за %.0fс после намеренного disconnect -- считаю сбоем, выхожу",
                        RECONNECT_TIMEOUT_SEC)
            break
        log.warning("клиент отключился локально и без объявления (стоп-кран/чужой disconnect) "
                    "-- настоящий выход")
        break
    return False


def _bridge_canary() -> None:
    """HOTFIX 07.07 (вечер): проверка sync-моста тулов тем же путём, каким ходят все
    sync-обёртки (воркер-тред -> run_coroutine_threadsafe -> луп main). Мёртвый мост —
    знать при старте одной строкой лога, а не при первом «напиши Евгению» через 2 минуты."""
    try:
        _threadsafe_result(lambda: asyncio.sleep(0), 10)
        log.info("sync-мост тулов к лупу жив")
    except Exception:
        log.error("sync-мост тулов к лупу МЁРТВ — все Telethon-тулы будут таймаутить", exc_info=True)


class _LoopLivenessProbe:
    """Observe whether the main asyncio loop executes a thread-safe callback on time.

    The observer has no recovery action. In particular, it does not disconnect the
    client, restart or terminate anything. One pending callback represents one probe,
    so a delayed loop cannot create an unbounded callback queue.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, *, interval: float,
                 threshold: float) -> None:
        self._loop = loop
        self._interval = max(0.001, float(interval))
        self._threshold = max(0.001, float(threshold))
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="praxis-loop-liveness", daemon=True)

    @property
    def thread(self) -> threading.Thread:
        """The owned thread, exposed for lifecycle tests and diagnostics."""
        return self._thread

    def start(self) -> "_LoopLivenessProbe":
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        """Request a clean observer-only stop and wait briefly for the daemon."""
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=max(0.0, timeout))

    def _run(self) -> None:
        while not self._stop.is_set():
            acknowledged = threading.Event()
            acknowledged_at: list[float] = []
            sent_at = time.monotonic()

            def acknowledge() -> None:
                acknowledged_at.append(time.monotonic())
                acknowledged.set()

            try:
                self._loop.call_soon_threadsafe(acknowledge)
            except RuntimeError:
                # A closing/closed loop is normal during process shutdown. The probe
                # observes scheduling latency; it does not reinterpret loop lifecycle.
                return

            warned = False
            while not acknowledged.is_set():
                if self._stop.is_set():
                    return
                elapsed = max(0.0, time.monotonic() - sent_at)
                if elapsed >= self._threshold and not warned:
                    # Recheck after measuring: avoid an overdue episode if the callback
                    # completed while this thread was waking up.
                    if acknowledged.is_set():
                        break
                    warned = True
                    log.warning(
                        "telegram_loop_probe_overdue callback_lag_sec=%.3f threshold_sec=%.3f",
                        elapsed, self._threshold)
                # Short waits make stop prompt while still avoiding a polling hot loop.
                remaining = max(0.0, self._threshold - elapsed)
                wait_for = min(0.25, self._interval)
                if not warned and remaining:
                    wait_for = min(wait_for, remaining)
                acknowledged.wait(max(0.001, wait_for))

            if warned and acknowledged.is_set():
                ack_at = acknowledged_at[-1] if acknowledged_at else time.monotonic()
                lag = max(0.0, ack_at - sent_at)
                log.info(
                    "telegram_loop_probe_recovered episode_duration_sec=%.3f callback_lag_sec=%.3f",
                    lag, lag)
            if self._stop.wait(self._interval):
                return


def _start_loop_liveness_probe(
        loop: asyncio.AbstractEventLoop | None = None) -> _LoopLivenessProbe:
    """Start the observer after startup has completed; caller owns ``stop()``."""
    return _LoopLivenessProbe(
        loop or _main_loop(), interval=LOOP_LIVENESS_INTERVAL_SEC,
        threshold=LOOP_LIVENESS_THRESHOLD_SEC).start()


def _warm_media() -> None:
    """Warm resident media only; isolated TTS workers deliberately stay cold.

    Whisper and in-process Piper keep the historical boot-warm contract.  A
    process-isolated backend (Silero) advertises ``skip_warmup`` and is launched
    only by the first actual synthesis request, so PyTorch never enters the
    Telegram runner and no child model exists merely because Praxis restarted.
    Warmup is best-effort and never blocks boot.
    """
    try:
        import media_audio
        result = media_audio.warm()
        log.info("прогрев медиа-моделей на буте: %s", result)
    except Exception:
        log.warning("прогрев медиа-моделей упал (не критично — подхватится лениво)", exc_info=True)


async def _start_shared_stt():
    """Expose the same process-local Whisper object over an authenticated UDS."""

    try:
        import media_audio
        import stt_rpc

        backend = media_audio.get_default_backend().stt
        return await stt_rpc.start_from_env(backend)
    except Exception as exc:
        # The endpoint is optional and must not take the Telegram runtime down.
        # Keep OS paths and any secret-adjacent exception text out of the log.
        log.error("shared STT unavailable error_type=%s", type(exc).__name__)
        return None


def _restored_buffer_partition(restored: dict[str, list[str]]) -> tuple[dict[str, list[str]], tuple[tuple[str, str], ...]]:
    """Keep absorbed legacy aliases on disk, but never rehydrate them as live routes.

    Buffer files are a restart cache, not routing authority.  If durable route
    knowledge maps a legacy topic-looking key into a root room or another canonical
    topic, hydrating that alias would clone the place-wide hot ring back into memory and
    recreate deletion fanout.  The file remains untouched as forensic/rollback evidence.
    """

    accepted: dict[str, list[str]] = {}
    absorbed: list[tuple[str, str]] = []
    for raw_cid, lines in restored.items():
        cid = str(raw_cid)
        try:
            canonical = str(telegram_routes.place_of(cid) or cid)
        except Exception:
            log.debug("буфер: canonical place не определилось [%s]", cid, exc_info=True)
            canonical = cid
        if canonical != cid:
            absorbed.append((cid, canonical))
            continue
        accepted[cid] = lines
    return accepted, tuple(absorbed)


async def main() -> None:
    global _LOOP
    _LOOP = asyncio.get_running_loop()  # единственный живой луп — для sync-обёрток тулов
    try:
        import goals as _legacy_goals
        migration = _legacy_goals.decommission_legacy()
        if migration.get("migrated"):
            log.info("legacy moral goals archived: %s", migration["migrated"])
    except Exception:
        # Automatic recall excludes these paths independently, so an archival I/O
        # problem cannot re-author Praxis; keep the evidence and make it visible.
        log.exception("legacy moral goals could not be archived")
    if not API_ID or not API_HASH:
        raise SystemExit("Нет TELEGRAM_API_ID/TELEGRAM_API_HASH в .env")
    try:
        await client.connect()
    except _NET_ERRORS as exc:
        # Подъём В МОМЕНТ обрыва — не «плохой код». Раньше это исключение улетало из main()
        # насквозь: процесс жил ~15с, bootguard видел rc≠0 при elapsed<grace и на третьем
        # круге откатывал ЗДОРОВЫЙ HEAD на last_good. Ждём сеть; не дождались — выходим 42,
        # который decide_after читает как намеренный рестарт и early_fails не растит.
        if not await _reconnect_with_backoff(f"стартовый connect: {type(exc).__name__}"):
            agent._exit_process()
    if not await client.is_user_authorized():
        raise SystemExit("Не залогинен. Сначала: python mtproto_login.py send  -> потом ... code <код>")
    _start_dialog_warmup()  # HOTFIX 07.07: кэш имён диалогов греется фоном с самого коннекта
    asyncio.create_task(asyncio.to_thread(_bridge_canary))
    asyncio.create_task(asyncio.to_thread(_warm_media))  # whisper+piper резидентно С БУТА, не лениво
    # Общий STT поднимается ЗДЕСЬ — до `_install_telegram_dispatcher()` ниже.
    # Это единственное окно, когда цикл событий заведомо свободен: как только
    # включён диспетчер, обработчики апдейтов делают тяжёлую синхронную работу
    # прямо в цикле (record_message → rebuild_state → _resolve_compact), и в
    # живом чате оставшийся bootstrap может не получить управления часами.
    # Раньше сокет стартовал последним шагом bootstrap и в такие часы молчал —
    # вместе с ним пропадал голосовой ввод «Харда», который живёт на нём.
    # Сам сервис ни от переписки, ни от прогрева не зависит: это UDS поверх
    # резидентного Whisper, который догрузится сам.
    shared_stt = await _start_shared_stt()
    global _self_id
    me = await client.get_me()
    _self_id = me.id
    _install_telegram_dispatcher()
    agent._TELETHON["get_id"] = _sync_get_id
    agent._TELETHON["resolve_entity"] = _sync_resolve_id  # PASS 12.0.b: единый резолвер постановки=отправки
    agent._TELETHON["search_chats"] = _sync_search_chats
    agent._TELETHON["search_private_messages"] = _sync_search_private_messages
    agent._TELETHON["read_chat"] = _sync_read_chat
    agent._TELETHON["fetch_context"] = _sync_fetch_context
    agent._TELETHON["send_message"] = _sync_send_message
    agent._TELETHON["reply"] = _sync_reply
    agent._TELETHON["send_file"] = _sync_send_file
    agent._TELETHON["project_direct_outbox_acceptance"] = (
        _project_direct_outbox_acceptance
    )
    # PASS 24 Telegram surface.  Owner turns and Praxis-self receive it; the sync
    # functions repeat the actor check so trusted humans cannot delegate it.
    agent._TELETHON["join_chat"] = _sync_join_chat
    agent._TELETHON["leave_chat"] = _sync_leave_chat
    agent._TELETHON["followups"] = _sync_followups
    agent._TELETHON["telegram_account"] = _sync_telegram_account
    # Transport gate (25.08): runtime-owned forum-history scan. Никакого client/
    # entity от caller-а: transport closure строится здесь над module-global
    # Telethon client. apply=true пишет evidence только внутри этого же вызова.
    agent._TELETHON["history_scan"] = _sync_history_scan
    agent._TELETHON["moderate_abstractdl"] = _sync_moderate_abstractdl
    # Меры над самой комнатой: кулдаун, права для всех, срочное ограничение
    # одного участника — и откат, которого у модерации нет вовсе.
    agent._TELETHON["admin_abstractdl"] = _sync_admin_abstractdl
    # Её лицо, слова о себе и жесты — тот же sovereign-гейт, что и остальной аккаунт.
    agent._TELETHON["set_profile_photo"] = _sync_set_avatar
    agent._TELETHON["update_profile"] = _sync_update_profile
    agent._TELETHON["send_reaction"] = _sync_react
    # PASS 30.0.e: честный сенсор транспорта для тулов и окон — синхронный и thread-safe.
    # «Закрыт намеренно (моё окно)» и «отвалился» — разные состояния, не одно молчание.
    agent._TELETHON["transport_state"] = lambda: {
        "connected": bool(client.is_connected()),
        "intentional_window": _EXPECT_DISCONNECT.is_set(),
    }
    # Replay append-only run WAL before accepting new work. Interrupted read-only work becomes
    # paused; unversioned side effects stay in_doubt. Versioned Telegram text plans are retried
    # below only with their persisted route/chunks and stable MTProto random-id keys.
    recovered_runs = await asyncio.to_thread(agent.recover_durable_state)
    if recovered_runs:
        log.warning("восстановлено durable runs: %d", len(recovered_runs))
    # §1: восстановить только канонические буферы переписки с диска. Поглощённые
    # legacy aliases остаются файлами-свидетельствами, но больше не становятся живыми
    # маршрутами, не получают place-wide hot ring и не участвуют в deletion fanout.
    restored_all = bufstore.load_all()
    # Управление владельца не должно быть заложником догоняющих проверок ниже.
    asyncio.create_task(_early_control_watch())
    restored, absorbed_buffers = _restored_buffer_partition(restored_all)
    if absorbed_buffers:
        sample = ", ".join(f"{source}->{canonical}" for source, canonical in absorbed_buffers[:5])
        log.warning("буферы: не гидратирую поглощённые aliases: %d (%s)",
                    len(absorbed_buffers), sample)
    restored_meta = bufstore.meta_load()
    for cid, lines in restored.items():
        _buf[cid].extend(lines[-BUF_MAXLEN:])
        try:
            meta = restored_meta.get(cid) if isinstance(restored_meta.get(cid), dict) else {}
            migration = await asyncio.to_thread(
                memory_life.bootstrap_legacy, cid, lines[-BUF_MAXLEN:], last_ts=meta.get("last_ts"))
            if not migration.get("already"):
                log.info("PASS19 bootstrap [%s]: events=%s legacy_compact=%s",
                         cid, migration.get("events"), migration.get("legacy_compact"))
            _sync_buffer_from_hot(cid)
        except Exception:
            log.exception("PASS19 bootstrap не удался [%s]", cid)
    if restored:
        log.info("восстановлено буферов: %d (%s)", len(restored), ", ".join(list(restored)[:5]))
        for cid in restored:
            asyncio.create_task(_maybe_compact(cid))
    # Reconcile accepted/in-doubt account membership before accepting fresh turns.
    await _membership_reconcile_once()
    # Direct tool and scheduled sends own immutable intents.  Replay them with
    # their original MTProto random ids before accepting fresh model work.
    await _direct_outbox_once()
    # Project delivered media tombstones and retry already-owned transport only
    # after Telegram routes/buffers are hydrated, before any model continuation.
    await _media_cleanup_once()
    # Buffers are in place; replay authored text before accepting fresh turns.
    await _text_outbox_once()
    # Single invariant: the brain only ever runs under _ONE_MIND (except Forge).
    # Uncontended here — the clock/supervisor start below — but held for uniformity.
    async with _ONE_MIND:
        resumed_runs = await asyncio.to_thread(agent.resume_durable_runs, limit=20)
    if resumed_runs:
        log.warning("обработано executable durable resumes: %d", len(resumed_runs))
    loop_probe: _LoopLivenessProbe | None = None
    try:
        log.info("Praxis на связи как @%s (id %s). мозг: %s; owner=%s комнат=%d "
                 "last_n=%d дебаунс=%.0fs кулдаун dm/grp=%.0f/%.0f",
                 me.username, me.id, llm.state_line() or "не настроен",
                 OWNER_ID or "—", len(rooms.allowed_chats()), LAST_N, DEBOUNCE_SEC, COOLDOWN_DM, COOLDOWN_GROUP)
        _install_dead_room_filter()  # 10.8: banned/private-каналы → mode=dead, лог не спамится
        _CLOCK_ALIVE[0] = True   # ранний надзор за управлением своё отслужил
        asyncio.create_task(_clock())  # PASS 4: буферы/расписание/«сон»/сердцебиение — один тик
        asyncio.create_task(_missed_dm_sweep())  # PASS 9.0: догнать ЛС, оборванные рестартом
        # Separate observer: a callback queued from another thread can expose a stalled
        # asyncio loop while loop-owned clocks necessarily cannot run. It only logs.
        loop_probe = _start_loop_liveness_probe()
        needs_restart = await _supervise_connection()
    finally:
        if loop_probe is not None:
            loop_probe.stop()
        if shared_stt is not None:
            await shared_stt.stop()
        try:
            import media_audio
            media_audio.get_default_backend().clear_caches()
        except Exception:
            log.warning("очистка медиа-моделей при остановке не удалась", exc_info=True)
    if needs_restart:
        # Грейс деградированного режима исчерпан: сети нет по-настоящему. Выходим кодом 42 —
        # bootguard.decide_after читает его как намеренный рестарт и НЕ считает early_fail,
        # то есть долгий обрыв больше не может откатить здоровый код.
        agent._exit_process()


if __name__ == "__main__":
    # НЕ client.loop.run_until_complete: с telethon>=1.39 client.loop — динамический
    # get_running_loop(), у которого столько же «лупов», сколько тредов его спрашивало.
    # asyncio.run — честный один луп на весь процесс (см. _main_loop).
    asyncio.run(main())
