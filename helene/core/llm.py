"""
Praxis — канал к моделям (PASS 8.0). Единственная точка вызова LLM во всём коде.

Два фреймворка (anthropic-протокол / openai-протокол), две ресурсные роли:
  * voice     — живой голос и основной агентный цикл;
  * evaluator — legacy-ключ вспомогательной модели: compact, memory/identity passes,
    privacy authority check и техническое второе мнение. Она не оценивает личность или речь.

Конфиг живёт в memory/llm.json (0600, gitignored, атомарная запись) и правится ТОЛЬКО
панелью (плитка «Мозг») и миграцией из env при первом старте. Горячий подхват: кэш по
mtime — панель (другой контейнер, тот же том) и голос видят правку без рестарта.

Фолбэк: на auth/429/5xx/timeout — один повтор на противоположном фреймворке, если там
есть ключ и у роли задан fallback_model. Событие — WARNING в лог + строка ей в дневник;
следующий успешный вызов на основном сбрасывает флаг. Смена ролей в конфиге — тоже
событие в дневник («мозг сменился») — её честное знание, на чём она думает.

Тестовый шов: _TEST_CLIENTS["anthropic"|"openai"] = фейк-клиент (None = «не настроено»);
сеть в тестах не нужна.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import praxis_time
import json
import logging
import os
import re
import contextvars as _cv
import time as _time
import tool_offerings
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("praxis-llm")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
REPO = Path(__file__).resolve().parent
MEM_DIR = BASE / "memory"
CONFIG_PATH = MEM_DIR / "llm.json"
JOURNAL_DIR = MEM_DIR / "journal"
USAGE_PATH = MEM_DIR / ".state" / "usage.json"   # PASS 9.1: расход токенов по ролям/дням
USAGE_KEEP_DAYS = 60
# Схема записи расхода. 1 — до 27.08.2026: `in` значил РАЗНОЕ у разных фреймворков
# (anthropic — только свежие токены, openai — весь промпт ВМЕСТЕ с кэшем), и одна
# формула поверх общего ведра давала число, которое ничего не измеряет. 2 — `in`
# везде значит ОДНО: токены, НЕ пришедшие из кэша, то есть оплаченные полностью.
# Старые записи не переписываются: у них схемы нет, и доля кэша по ним не считается —
# «не знаю» честнее пересчитанного задним числом.
USAGE_SCHEMA = 2

FRAMEWORKS = ("anthropic", "openai")
ROLES = ("voice", "evaluator")
_ROLE_RU = {"voice": "голос", "evaluator": "вспомогательная"}

DEFAULT_MAX_TOKENS = {"voice": 1024, "evaluator": 400}

# Ошибки, на которых имеет смысл фолбэк (по имени класса — одинаковы у обоих SDK).
_FALLBACK_ERRORS = {
    "AuthenticationError", "RateLimitError", "APITimeoutError",
    "APIConnectionError", "InternalServerError", "TimeoutError",
}


class BrokenChannelError(RuntimeError):
    """Канал вернул не ответ. Общий предок двух РАЗНЫХ случаев — их нельзя мешать.

    Оба означают «фолбэк на другой фреймворк уместен», и только один из них означает
    «переспросить по тому же каналу безопасно». Различие живёт в потомках ниже.
    """


class EmptyResponseError(BrokenChannelError):
    """Пришло НИЧЕГО: ни текста, ни единого блока. Найдено живым 06.07: голос на gpt-5.5/
    relay молчал в пустоту (10/10 пустых), и фолбэк на glm не срабатывал, потому что
    _call_openai считал это успешным end_turn.

    ⭐ ЭТО РОВНО ТА ГРАНИЦА, ПОД КОТОРУЮ ОНА СОГЛАШАЛАСЬ 10.08 на повтор по своему каналу:
    «EmptyResponseError возникает только когда нет НИ текста, НИ блока; моё молчание так не
    выглядит никогда — оно едет либо тулом stay_silent, либо сентинелом-текстом, и то и
    другое непусто». Значит переспросить нечего поверх: её слова в этом ответе не было.
    Класс обязан оставаться настолько же узким, иначе согласие перестаёт покрывать код.
    """


class TornStreamError(BrokenChannelError):
    """Стрим НАЧАЛСЯ и оборвался ошибкой: что-то уже приехало, а finish_reason='error'.

    ⚠ 15.08. До сегодня этот случай носил имя EmptyResponseError и попадал в тот же повтор,
    хотя он ему прямо противоположен: здесь текст УЖЕ есть. Условие было дизъюнкцией
    (`stop_reason == "error" or (ни текста, ни блока)`), первый дизъюнкт срабатывал и при
    непустом тексте — в стриме сначала приезжают дельты, потом ошибка, — и повтор уходил
    ПОВЕРХ уже сказанного. Её согласие давалось на «повтор транспорта» при пустоте; здесь
    пустоты нет, и повторять по тому же каналу нельзя.

    Что приехало — не выбрасывается молча: кусок лежит в `.partial` (LLMResponse), чтобы
    следующий по пути мог решить его судьбу, а не узнать о нём из логов. Сегодня решает
    `chat()`: уходит на фолбэк-фреймворк (прежнее поведение этого случая) и НЕ переспрашивает
    свой канал. Отдать `.partial` наверх как готовую реплику нельзя: оборванная на полуслове
    фраза уезжала бы в Telegram как законченная — см. `_note_truncation`.

    Частый вид этого сбоя — codex-прокси, который «маскирует» апстрим-4xx/5xx под обычный
    чанк («Error: 400 …» в content). Такой текст не её, и в чат он не попадает: он уходит
    в сообщение ошибки, а ход идёт на фолбэк.
    """

    def __init__(self, message: str, partial=None):
        super().__init__(message)
        self.partial = partial


class RelayTerminalError(RuntimeError):
    """Реле назвало исход машинным кодом (`relay_terminal`) — это не ответ модели.

    25.09.2026. Реле подписки при исчерпанном лимите отдавало `finish_reason="error"` и
    английский текст «Both OpenAI subscriptions are currently unavailable…» ПРЯМО В
    content стрима. Движок читал это как оборванный стрим (TornStreamError), повторял,
    уходил на «фолбэк» в то же реле и в итоге показывал владельцу чужую диагностику как
    реплику агента. Под `RELAY_TYPED_TERMINAL=field` реле кладёт рядом с чанком
    структурный `relay_terminal` (код, слот, `resets_at`, попытки) — его читаем ДО
    choices и content, и он становится типизированной ошибкой.

    ⚠ НЕ потомок BrokenChannelError: повтор по тому же каналу здесь бессмыслен (лимит не
    рассосётся за секунду), а фолбэк уместен только на ДРУГОЙ эндпойнт — второе имя
    модели того же реле упирается в тот же счётчик.
    """

    code = "relay_terminal"

    def __init__(self, message: str, *, code: str = "", slot: str = "",
                 resets_at: float | None = None, attempts=None, synthetic: bool = False):
        super().__init__(message)
        if code:
            self.code = code
        self.slot = str(slot or "")
        self.resets_at = resets_at
        self.attempts = list(attempts or ())
        # Синтетический — поднят нами же по действующему удержанию эндпойнта, без
        # похода в реле: в brain-статистику как сбой не пишется.
        self.synthetic = bool(synthetic)


class QuotaExhaustedError(RelayTerminalError):
    """`subscription_window_exhausted`: окно подписки исчерпано до `resets_at`."""

    code = "subscription_window_exhausted"


class RelayNeedsLoginError(RelayTerminalError):
    """`subscription_needs_login`: подписка отвергла токены — нужен новый вход."""

    code = "subscription_needs_login"


class RelayUnavailableError(RelayTerminalError):
    """`subscriptions_unavailable`: слоты отказали по разным причинам (401 + лимит)."""

    code = "subscriptions_unavailable"


_RELAY_TERMINAL_CLASSES = {
    QuotaExhaustedError.code: QuotaExhaustedError,
    RelayNeedsLoginError.code: RelayNeedsLoginError,
    RelayUnavailableError.code: RelayUnavailableError,
}
#: Сколько держать эндпойнт закрытым, если реле не назвало час восстановления.
#: 15 минут — собственный cooldown роутера реле, а не догадка о вендоре.
QUOTA_HOLD_DEFAULT_SEC = float(os.getenv("PRAXIS_QUOTA_HOLD_SEC", "900") or 900)
#: Удержание эндпойнта: base_url (нормализованный) -> {"until", "code", "message",
#: "since", "framework"}. Пока действует — основная нога не зовётся, ход идёт на
#: запасного провайдера с ДРУГИМ эндпойнтом; истекло — пробуем снова.
_ENDPOINT_HOLD: dict[str, dict] = {}


def _endpoint_key(framework: str) -> str:
    """Адрес ноги для сравнения «то же реле или другое». Пусто — эндпойнт вендора."""
    try:
        base = str((_config().get("frameworks") or {}).get(framework, {}).get("base_url") or "")
    except Exception:
        base = ""
    return base.strip().lower().rstrip("/")


def _same_endpoint(framework_a: str, framework_b: str) -> bool:
    """Две ноги упираются в один эндпойнт — второй фреймворк лимит не обойдёт."""
    if framework_a == framework_b:
        return True
    a, b = _endpoint_key(framework_a), _endpoint_key(framework_b)
    return bool(a) and a == b


def _relay_terminal_of(chunk) -> dict | None:
    """`relay_terminal` из SSE-чанка реле (SDK кладёт незнакомые поля в model_extra)."""
    term = getattr(chunk, "relay_terminal", None)
    if term is None:
        extra = getattr(chunk, "model_extra", None)
        if isinstance(extra, dict):
            term = extra.get("relay_terminal")
    if term is None and isinstance(chunk, dict):
        term = chunk.get("relay_terminal")
    if term is None:
        return None
    if not isinstance(term, dict):
        term = {k: getattr(term, k, None)
                for k in ("code", "message", "slot", "resets_at", "resets_in_seconds", "attempts")}
    code = str(term.get("code") or "").strip()
    return dict(term, code=code) if code else None


def _terminal_error(term: dict) -> RelayTerminalError | None:
    """Типизированная ошибка по коду реле; коды апстрима (torn, upstream_error) остаются
    прежней механике (finish_reason=error → сторож ответа)."""
    cls = _RELAY_TERMINAL_CLASSES.get(str(term.get("code") or ""))
    if cls is None:
        return None
    resets_at = None
    try:
        if term.get("resets_at"):
            resets_at = float(term["resets_at"])
        elif term.get("resets_in_seconds") is not None:
            resets_at = _time.time() + max(0.0, float(term["resets_in_seconds"]))
    except (TypeError, ValueError):
        resets_at = None
    message = str(term.get("message") or cls.code)
    return cls(message, code=cls.code, slot=str(term.get("slot") or ""),
               resets_at=resets_at, attempts=term.get("attempts") or ())


def _hold_words(until: float | None) -> str:
    if not until:
        return "время восстановления неизвестно"
    try:
        return "до " + _dt.datetime.fromtimestamp(until).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "время восстановления неизвестно"


def _hold_endpoint(framework: str, err: RelayTerminalError) -> dict:
    """Закрыть эндпойнт ноги до `resets_at` (или на QUOTA_HOLD_DEFAULT_SEC) и записать
    состояние для окна: `memory/.state/quota.json`."""
    key = _endpoint_key(framework) or framework
    until = err.resets_at
    if isinstance(err, QuotaExhaustedError) and not until:
        until = _time.time() + QUOTA_HOLD_DEFAULT_SEC
    elif not isinstance(err, QuotaExhaustedError):
        # Нужен вход или слоты отказали по-разному: само не восстановится — держим
        # умеренно, чтобы не долбить реле, но и не молчать вечно.
        until = until or (_time.time() + QUOTA_HOLD_DEFAULT_SEC)
    if isinstance(err, QuotaExhaustedError):
        words = "подписка исчерпана " + (
            _hold_words(err.resets_at) if err.resets_at
            else "— время восстановления неизвестно, попробую снова через %d мин"
            % max(1, int(QUOTA_HOLD_DEFAULT_SEC // 60)))
    elif isinstance(err, RelayNeedsLoginError):
        words = "подписка требует нового входа"
    else:
        words = "подписка недоступна: " + str(err)[:120]
    hold = {"framework": framework, "endpoint": key, "code": err.code,
            "message": str(err)[:300], "slot": err.slot,
            "since": _time.time(), "until": until, "words": words}
    _ENDPOINT_HOLD[key] = hold
    _write_quota_state()
    return hold


def _endpoint_hold(framework: str) -> dict | None:
    """Действующее удержание эндпойнта ноги или None (истёкшее снимается здесь же)."""
    key = _endpoint_key(framework) or framework
    hold = _ENDPOINT_HOLD.get(key)
    if not hold:
        return None
    if hold.get("until") and _time.time() >= float(hold["until"]):
        _ENDPOINT_HOLD.pop(key, None)
        _write_quota_state()
        return None
    return hold


def _release_endpoint(framework: str) -> None:
    key = _endpoint_key(framework) or framework
    if _ENDPOINT_HOLD.pop(key, None) is not None:
        _write_quota_state()


QUOTA_STATE_PATH = USAGE_PATH.parent / "quota.json"


def _write_quota_state() -> None:
    """Состояние удержаний — окну и панели. Никогда не роняет вызов."""
    try:
        QUOTA_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"holds": list(_ENDPOINT_HOLD.values()), "at": _time.time()}
        tmp = QUOTA_STATE_PATH.with_name(".tmp-quota.json")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, QUOTA_STATE_PATH)
    except Exception:
        log.debug("quota.json не записался", exc_info=True)


def quota_state() -> dict:
    """Для STATE/окна: действующие удержания эндпойнтов, словами и с часом."""
    for key in list(_ENDPOINT_HOLD):
        hold = _ENDPOINT_HOLD[key]
        if hold.get("until") and _time.time() >= float(hold["until"]):
            _ENDPOINT_HOLD.pop(key, None)
    return {"holds": [dict(h) for h in _ENDPOINT_HOLD.values()]}


def _fallback_leg_elsewhere(rc: dict, fw: str) -> str:
    """Имя фреймворка запасной ноги, если она настроена и упирается в ДРУГОЙ эндпойнт;
    иначе пусто. Та же логика выбора `other`, что в chat()."""
    configured_fw = str(rc.get("framework") or fw)
    other = ((rc.get("fallback_framework") or "").strip()
             or ("openai" if configured_fw == "anthropic" else "anthropic"))
    if not (rc.get("fallback_model") or "").strip():
        return ""
    if _client_for(other) is None or _same_endpoint(fw, other):
        return ""
    return other

# Тестовый шов: {"anthropic": фейк, "openai": фейк}. Значение None = «не настроено».
_TEST_CLIENTS: dict[str, object] = {}

_CLIENTS: dict[str, tuple[tuple, object]] = {}   # framework -> ((base_url, key), sdk-клиент)
_CACHE: dict = {"mtime": None, "cfg": None}
_STATE: dict[str, dict] = {r: {"on_fallback": False, "last_error": ""} for r in ROLES}


@dataclass
class LLMResponse:
    """Нормализованный ответ модели (anthropic-форма блоков, независимо от фреймворка)."""
    text: str = ""
    blocks: list = field(default_factory=list)   # [{"type":"text"|"tool_use", ...}]
    stop_reason: str = "end_turn"                # "tool_use" | "end_turn" | "max_tokens" | ...
    usage: dict = field(default_factory=dict)    # {"in": int, "out": int}
    framework: str = ""
    model: str = ""
    # Local routing fact, not provider-reported usage: this logical call selected a
    # sighted replacement because the input contained pixels.
    vision: bool = False


@dataclass(frozen=True)
class Limits:
    # Bounded specialist loops only.  The main Praxis tool-loop does not consume this.
    max_tool_iters: int = 20


# --------------------------------------------------------------------------- #
#  Конфиг: миграция из env, атомарная запись, горячий подхват по mtime
# --------------------------------------------------------------------------- #

def _from_env() -> dict:
    """Собрать конфиг из env (миграция первого старта; .env остаётся бутстрапом транспорта)."""
    try:
        iters = max(1, min(300, int(os.getenv("PRAXIS_MAX_TOOL_ITERS", "20") or 20)))
    except ValueError:
        iters = 20
    return {
        "frameworks": {
            "anthropic": {"base_url": os.getenv("GLM_BASE_URL", "https://api.z.ai/api/anthropic"),
                          "api_key": os.getenv("GLM_API_KEY", "")},
            "openai": {"base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                       "api_key": os.getenv("OPENAI_API_KEY", "")},
        },
        "roles": {
            "voice": {"framework": "anthropic",
                      "model": os.getenv("GLM_VOICE_MODEL") or os.getenv("GLM_MODEL") or "glm-5.2",
                      "max_tokens": DEFAULT_MAX_TOKENS["voice"], "fallback_model": ""},
            "evaluator": {"framework": "anthropic",
                          "model": os.getenv("GLM_FIRSTPASS_MODEL")
                                   or os.getenv("GLM_GATEKEEPER_MODEL") or "glm-4.7",
                          "max_tokens": DEFAULT_MAX_TOKENS["evaluator"], "fallback_model": ""},
        },
        "limits": {"max_tool_iters": iters},
    }


def _normalize(cfg: dict) -> dict:
    """Дозаполнить пропуски дефолтами — конфиг руками/панелью не обязан быть полным."""
    base = _from_env()
    out = {"frameworks": {}, "roles": {}, "limits": dict(base["limits"])}
    for fw in FRAMEWORKS:
        cur = (cfg.get("frameworks") or {}).get(fw) or {}
        out["frameworks"][fw] = {"base_url": str(cur.get("base_url") or base["frameworks"][fw]["base_url"]),
                                 "api_key": str(cur.get("api_key") or "")}
    for role in ROLES:
        cur = (cfg.get("roles") or {}).get(role) or {}
        d = base["roles"][role]
        fw = cur.get("framework") if cur.get("framework") in FRAMEWORKS else d["framework"]
        try:
            mt = int(cur.get("max_tokens") or d["max_tokens"])
        except (TypeError, ValueError):
            mt = d["max_tokens"]
        out["roles"][role] = {"framework": fw, "model": str(cur.get("model") or d["model"]),
                              "max_tokens": mt, "fallback_model": str(cur.get("fallback_model") or "")}
        if str(cur.get("fallback_framework") or "").strip() in ("openai", "anthropic"):
            out["roles"][role]["fallback_framework"] = str(cur.get("fallback_framework")).strip()
        # 19.08: ЕЁ фоновая ступень рассуждения (switch_brain action=reasoning).
        # Нормализация фиксированным набором ключей молча съедала бы её решение —
        # ровно класс HEADER_KEYS из реестра переноса днём раньше.
        effort = str(cur.get("reasoning_effort") or "").strip().lower()
        if effort in REASONING_EFFORTS:
            out["roles"][role]["reasoning_effort"] = effort
        # 09.09: зрячая замена текстовой модели на ход с изображением (vision_model) —
        # тот же класс: без строки здесь нормализация стирала бы ручку владельца.
        vision = str(cur.get("vision_model") or "").strip()
        if vision:
            out["roles"][role]["vision_model"] = vision
        # Framework/account-specific replacements. The legacy singular key stays
        # valid only for the configured primary leg; it must never leak to a
        # fallback using another framework/account.
        visions = cur.get("vision_models")
        if isinstance(visions, dict):
            clean_visions = {name: str(visions.get(name) or "").strip()
                             for name in FRAMEWORKS
                             if str(visions.get(name) or "").strip()}
            if clean_visions:
                out["roles"][role]["vision_models"] = clean_visions
    lim = cfg.get("limits") or {}
    try:
        out["limits"]["max_tool_iters"] = max(1, min(600, int(lim.get("max_tool_iters"))))  # 07.07: потолок 300->600, владелец хочет управлять сам
    except (TypeError, ValueError):
        pass
    # PASS 24 intentionally drops old evaluator/drift/window policy keys while normalizing.
    # They are neither hidden defaults nor compatibility vetoes.
    # PASS 9.1: опциональный прайс {"<model>": {"in_per_1m": X, "out_per_1m": Y}} — руками
    # в llm.json; без него панель честно показывает только токены. Пропускаем как есть,
    # чтобы запись конфига панелью не стирала блок.
    if isinstance(cfg.get("pricing"), dict):
        out["pricing"] = cfg["pricing"]
    # PASS 9.2: блок иммунитета (second_opinion — задел, дефолт false) — тоже пропускаем
    if isinstance(cfg.get("immune"), dict):
        out["immune"] = cfg["immune"]
    return out


def save_config(cfg: dict) -> None:
    """Атомарная запись конфига (tmp+replace), права 0600. Единственный писатель — панель/миграция."""
    cfg = _normalize(cfg)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:  # Windows/экзотика — не рельс, ключи и так вне git
        pass


def _journal(msg: str) -> None:
    """Строка ей в дневник (как selfdev): события канала — часть её честной непрерывности."""
    try:
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        # ⚠ ДЕНЬ И ЧАС — ЕЁ, а не контейнера. `date.today()` и `datetime.now()` читают
        # СИСТЕМНЫЙ пояс, а в контейнере задан только PRAXIS_TZ: с 00:00 до 04:00 по
        # Самаре запись уходила во ВЧЕРАШНИЙ файл, а час внутри строки был UTC.
        # Имя файла и штамп строки берутся из ОДНОГО источника: иначе расхождение
        # переезжает внутрь файла, где его труднее заметить.
        p = JOURNAL_DIR / f"{praxis_time.day_key()}.md"
        if not p.exists():
            p.write_text(f"# {praxis_time.day_key()}\n\n", encoding="utf-8")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"- {praxis_time.now():%H:%M} (s2) [канал] {msg}\n")
    except Exception:
        log.debug("journal llm не удался", exc_info=True)


def _diff_roles(old: dict, new: dict) -> list[str]:
    out = []
    for role in ROLES:
        o, n = (old.get("roles") or {}).get(role) or {}, (new.get("roles") or {}).get(role) or {}
        if (o.get("model"), o.get("framework")) != (n.get("model"), n.get("framework")):
            out.append(f"{_ROLE_RU[role]} {o.get('model')} → {n.get('model')} ({n.get('framework')})")
    return out


def _config_stamp():
    """Отпечаток файла конфига: время, размер и identity атомарно заменённого файла.

    Одного `st_mtime_ns` мало не в теории, а на этих машинах: шаг часов файла —
    миллисекунда на ext4 сервера и четыре в контейнере, замерено 28.08 (шесть
    записей подряд дают ДВА различных значения). Две записи внутри одного тика
    для кэша неразличимы, и чтение сразу после записи возвращает ПРЕЖНИЙ конфиг.

    Размер различает большинство правок внутри тика; identity файла закрывает и
    одинаковую длину после штатного ``tmp.replace``. Именно так это и ловилось:
    `test_relay_steps_project_to_glm_dialect` ставит ступени подряд, и `xhigh`
    (пять знаков) от `high` (четыре) кэш не отличал.

    ⚠ Это опирается на единственного штатного писателя ``save_config``, который
    всегда делает ``tmp.replace``. Внешняя перезапись *того же inode* с теми же
    временем и размером всё ещё неотличима; если такой писатель появится, ключ
    надо заменить на content hash или версию записи.

    ⚠⚠ И чего тут НЕ делать: заставить `save_config` обновлять кэш самому.
    Выглядит очевидным (писатель знает, что записал) и ломает её дневник:
    строка «мозг сменился» пишется, когда СМЕНУ ЗАМЕТИЛ `_config()`. Обновив
    кэш заранее, писатель лишает её уведомления о том, что мозг подменили.
    Пробовал 28.08 — `test_mtime_hot_reload_and_brain_change_journal` поймал.
    """
    try:
        st = CONFIG_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _config() -> dict:
    """Живой конфиг: перечитывается при смене отпечатка; нет файла — миграция из env."""
    mtime = _config_stamp()
    if mtime is None:
        if _CACHE["cfg"] is None or _CACHE["mtime"] is not None:
            _CACHE.update(mtime=None, cfg=_from_env())
            try:
                save_config(_CACHE["cfg"])
                _CACHE["mtime"] = _config_stamp()
                log.info("llm: конфиг мигрирован из env → %s", CONFIG_PATH)
            except OSError:
                log.warning("llm: конфиг из env не записался (работаю из памяти)", exc_info=True)
        return _CACHE["cfg"]
    if mtime == _CACHE["mtime"] and _CACHE["cfg"] is not None:
        return _CACHE["cfg"]
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        fresh = _normalize(stored)
    except Exception:
        log.warning("llm: llm.json не разобрался — работаю на последнем хорошем", exc_info=True)
        if _CACHE["cfg"] is None:
            _CACHE.update(mtime=mtime, cfg=_from_env())
        else:
            _CACHE["mtime"] = mtime
        return _CACHE["cfg"]
    if stored != fresh:
        # Normalisation is also the one-way migration boundary.  Retired
        # evaluator/drift/window policy must not linger in the durable file and
        # surprise a later process or operator merely because it is inert today.
        try:
            save_config(fresh)
            mtime = _config_stamp()
            log.info("llm: конфиг нормализован и устаревшие policy-поля удалены")
        except OSError:
            log.warning("llm: нормализованный конфиг не записался", exc_info=True)
    old = _CACHE["cfg"]
    if old is not None:
        for line in _diff_roles(old, fresh):
            _journal(f"мозг сменился: {line}")
            log.warning("llm: %s", line)
    _CACHE.update(mtime=mtime, cfg=fresh)
    return fresh


def web_search_backend() -> str | None:
    """Вернуть hosted-search backend, который реально умеет принять тул.

    z.ai использует Anthropic-shaped ``web_search_20250305``. Codex relay принимает
    Responses ``web_search`` через свой Chat-Completions compatibility edge, но это
    явная способность нашего деплоя: произвольному OpenAI-compatible URL такой тул
    посылать нельзя.
    """
    if os.getenv("PRAXIS_WEB_SEARCH", "0").lower() not in ("1", "true", "yes", "on"):
        return None
    try:
        framework = _config()["roles"]["voice"]["framework"]
    except Exception:
        return None
    if framework == "anthropic":
        return "anthropic"
    relay_native = os.getenv("PRAXIS_OPENAI_NATIVE_WEB_SEARCH", "0").lower()
    if framework == "openai" and relay_native in ("1", "true", "yes", "on"):
        return "openai"
    return None


def web_search_available() -> bool:
    """Единый источник правды для реального вызова и панели способностей."""
    return web_search_backend() is not None


def role_max_tokens(role: str) -> int:
    """Потолок токенов роли из конфига (для вызовов, которым нужен «размер голоса»)."""
    rc = (_config().get("roles") or {}).get(role) or {}
    try:
        return int(rc.get("max_tokens") or DEFAULT_MAX_TOKENS.get(role, 1024))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS.get(role, 1024)


def limits() -> Limits:
    lim = _config().get("limits") or {}
    return Limits(max_tool_iters=int(lim.get("max_tool_iters", 20)))


def update_config(changes: dict) -> dict:
    """Слить изменения в конфиг (deep-merge по frameworks/roles/limits) и сохранить. -> новый конфиг.

    Путь ПИСАТЕЛЯ (панель/миграция/тесты): кэш обновляется напрямую, не через mtime —
    гранулярность файловых времён (Windows-тик ~15мс) не должна давать читать старое.
    Событие «мозг сменился» журналит ЧИТАТЕЛЬ (другой процесс) при mtime-перечитывании."""
    cfg = json.loads(json.dumps(_config()))  # глубокая копия
    for sect in ("frameworks", "roles"):
        for k, v in (changes.get(sect) or {}).items():
            if isinstance(v, dict):
                cfg.setdefault(sect, {}).setdefault(k, {}).update(v)
    if isinstance(changes.get("limits"), dict):
        cfg.setdefault("limits", {}).update(changes["limits"])
    save_config(cfg)
    fresh = _normalize(cfg)
    try:
        _CACHE.update(mtime=_config_stamp(), cfg=fresh)
    except OSError:
        _CACHE.update(cfg=fresh)
    return fresh


def swap_fallback(role: str) -> dict:
    """06.07: поменять местами основную и запасную модели роли одним действием.

    chat() всегда считает fallback_model моделью ПРОТИВОПОЛОЖНОГО фреймворка от текущего —
    ручной свич framework в панели не переставлял model/fallback_model заодно, и после свича
    фолбэк бил в чужой протокол. Свап делает framework/model/fallback_model согласованными."""
    if role not in ROLES:
        raise ValueError(f"llm: неизвестная роль {role!r}")
    cfg = json.loads(json.dumps(_config()))
    rc = cfg["roles"][role]
    fb = (rc.get("fallback_model") or "").strip()
    if not fb:
        raise ValueError("нечего свапать — сначала задай fallback_model")
    other = "openai" if rc["framework"] == "anthropic" else "anthropic"
    rc["framework"], rc["model"], rc["fallback_model"] = other, fb, rc["model"]
    # свап меняет фреймворк местами — прибитый fallback_framework стал бы ложью
    rc.pop("fallback_framework", None)
    save_config(cfg)
    fresh = _normalize(cfg)
    try:
        _CACHE.update(mtime=_config_stamp(), cfg=fresh)
    except OSError:
        _CACHE.update(cfg=fresh)
    _journal(f"{_ROLE_RU[role]}: своп основной/запасной ({other}/{fb})")
    return fresh


# --------------------------------------------------------------------------- #
#  Клиенты SDK (кэш на фреймворк; пересоздаются при смене base_url/ключа)
# --------------------------------------------------------------------------- #

def _client_for(framework: str):
    """SDK-клиент фреймворка (или тестовый фейк). None — не настроен."""
    if framework in _TEST_CLIENTS:
        return _TEST_CLIENTS[framework]
    if os.environ.get("PRAXIS_TEST"):
        return None  # герметичность: в тестах сеть только через фейки (use_test_client)
    fw = (_config().get("frameworks") or {}).get(framework) or {}
    key, base_url = fw.get("api_key") or "", fw.get("base_url") or ""
    if not key:
        return None
    fp = (base_url, key)
    cached = _CLIENTS.get(framework)
    if cached and cached[0] == fp:
        return cached[1]
    if framework == "anthropic":
        from anthropic import Anthropic
        cli = Anthropic(api_key=key, base_url=base_url)
    else:
        from openai import OpenAI
        cli = OpenAI(api_key=key, base_url=base_url)
    _CLIENTS[framework] = (fp, cli)
    return cli


def configured(role: str = "voice") -> bool:
    """Есть ли живой канал для роли (ключ у её фреймворка / тестовый фейк)."""
    try:
        rc = (_config().get("roles") or {}).get(role) or {}
        return _client_for(rc.get("framework") or "anthropic") is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Трансляция anthropic-формы <-> openai-формы
# --------------------------------------------------------------------------- #

def system_text(system) -> str:
    """system-блоки (или строка) -> одна строка (cache_control отбрасывается)."""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n\n".join(str(b.get("text", "")) for b in system if isinstance(b, dict))
    return str(system or "")


def _openai_strict_schema(schema: dict | None) -> dict:
    """OpenAI strict-mode (в т.ч. codex-relay) требует на КАЖДОЙ object-схеме: additionalProperties:
    false (ловили на recall, tools[0]) И required, перечисляющий ВСЕ ключи properties (ловили на
    remember, tools[1] — open_loop и другие опциональные поля не входили в required). Наши
    input_schema писаны под anthropic (там ничего из этого не нужно, required — только по-настоящему
    обязательные) — транслируем сюда, не трогая исходник. Поле, не бывшее required у anthropic,
    оборачивается в {anyOf: [<исходная схема>, {type: null}]} — модель явно шлёт null вместо
    пропуска ключа, сохраняя опциональность по смыслу; agent._voice отфильтровывает None перед
    вызовом тула, так что для самой реализации тула ничего не меняется. Рекурсивно — на случай
    вложенных object/array-схем."""
    s = dict(schema) if isinstance(schema, dict) else {"type": "object", "properties": {}}
    if s.get("type") == "object":
        props = s.get("properties") if isinstance(s.get("properties"), dict) else {}
        orig_required = set(s.get("required") or [])
        new_props = {}
        for k, v in props.items():
            fixed = _openai_strict_schema(v) if isinstance(v, dict) else v
            if k not in orig_required and isinstance(fixed, dict):
                fixed = {"anyOf": [fixed, {"type": "null"}]}
            new_props[k] = fixed
        s = dict(s, properties=new_props, required=list(new_props.keys()),
                **{"additionalProperties": False})
    items = s.get("items")
    if isinstance(items, dict):
        s = dict(s, items=_openai_strict_schema(items))
    return s


def tools_to_openai(tools: list | None) -> list | None:
    """Перевести функции и hosted web-search конкретного Codex relay."""
    if not tools:
        return None
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "web_search":
            item = {"type": "web_search"}
            context_size = t.get("search_context_size")
            if context_size is not None:
                if context_size not in ("low", "medium", "high"):
                    raise ValueError("web_search.search_context_size must be low, medium, or high")
                item["search_context_size"] = context_size
            external = t.get("external_web_access")
            if external is not None:
                if not isinstance(external, bool):
                    raise ValueError("web_search.external_web_access must be boolean")
                item["external_web_access"] = external
            max_uses = t.get("max_uses")
            if max_uses is not None:
                if isinstance(max_uses, bool) or not isinstance(max_uses, int) or not 1 <= max_uses <= 10:
                    raise ValueError("web_search.max_uses must be an integer from 1 to 10")
                item["max_uses"] = max_uses
            out.append(item)
            continue
        if "input_schema" not in t:
            continue  # provider-specific server tool (например z.ai search)
        out.append({"type": "function", "function": {
            "name": t.get("name", ""), "description": t.get("description", ""),
            "parameters": _openai_strict_schema(t.get("input_schema"))}})
    return out or None


def tools_to_anthropic(tools: list | None) -> list | None:
    """Оставить Anthropic/function tools, отфильтровав relay-only hosted search.

    Последний tool в массиве получает cache_control: ephemeral, чтобы весь блок
    инструментных схем попал во второй cache breakpoint (первый — system-промпт).
    Это ~20-25k токенов, которые иначе платятся каждый ход заново.
    PRAXIS_PROMPT_CACHE=0 отключает (совместимо с _system в agent.py).
    """
    if not tools:
        return None
    out = [t for t in tools
           if isinstance(t, dict) and t.get("type") != "web_search"]
    if not out:
        return None
    if os.getenv("PRAXIS_PROMPT_CACHE", "1").lower() not in ("0", "false", "no"):
        last = dict(out[-1])
        last["cache_control"] = {"type": "ephemeral"}
        out[-1] = last
    return out


_VISION_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_VISION_MAX_BYTES = int(float(os.getenv("PRAXIS_IMAGE_LLM_MB", "20")) * 1024 * 1024)


def _image_payload(block: dict) -> tuple[str, str]:
    """Канонический image-блок -> (mime, base64), единый для обоих адаптеров.

    Живой раннер передаёт проверенный MediaRef как {type:image,path,mime}. Форму source
    принимаем для совместимости с уже готовыми Anthropic-блоками и тестовыми вызовами.
    """
    source = block.get("source")
    if isinstance(source, dict) and source.get("type") == "base64":
        mime = str(source.get("media_type") or block.get("mime") or "").lower()
        data = str(source.get("data") or "")
        if mime not in _VISION_MIME or not data:
            raise ValueError("неподдерживаемый или пустой image-блок")
        if len(data) > ((_VISION_MAX_BYTES + 2) // 3) * 4 + 8:
            raise ValueError("изображение превышает локальный лимит")
        return mime, data
    path = Path(str(block.get("path") or ""))
    mime = str(block.get("mime") or block.get("media_type") or "").lower()
    if mime not in _VISION_MIME:
        raise ValueError(f"формат изображения {mime or '?'} не поддерживается моделью")
    try:
        size = path.stat().st_size
    except OSError as e:
        raise ValueError("файл изображения недоступен") from e
    if size <= 0 or size > _VISION_MAX_BYTES:
        raise ValueError("изображение пустое или превышает локальный лимит")
    return mime, base64.b64encode(path.read_bytes()).decode("ascii")


def messages_to_anthropic(messages: list) -> list:
    """Наша история -> Anthropic; локальные image/path превращаются в base64 source."""
    out: list[dict] = []
    for m in messages or []:
        content = m.get("content", "")
        if not isinstance(content, list):
            out.append(dict(m))
            continue
        blocks = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "image":
                mime, data = _image_payload(b)
                blocks.append({"type": "image", "source": {
                    "type": "base64", "media_type": mime, "data": data}})
            elif _is_image_block(b):
                raise ValueError("non-canonical image block reached anthropic adapter")
            else:
                blocks.append(b)
        out.append(dict(m, content=blocks))
    return out


def messages_to_openai(messages: list) -> list:
    """Наша история (anthropic-форма, включая tool_use/tool_result) -> openai-сообщения."""
    out: list[dict] = []
    for m in messages or []:
        role, content = m.get("role", "user"), m.get("content", "")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            texts, calls = [], []
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    texts.append(str(b.get("text", "")))
                elif b.get("type") == "tool_use":
                    calls.append({"id": str(b.get("id", "")), "type": "function",
                                  "function": {"name": str(b.get("name", "")),
                                               "arguments": json.dumps(b.get("input") or {},
                                                                       ensure_ascii=False)}})
            msg: dict = {"role": "assistant", "content": "\n".join(t for t in texts if t) or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        # user: tool_result-блоки становятся role=tool; image-блоки — Chat Completions
        # image_url с data URL. Текст без картинки оставляем строкой для совместимости.
        texts, multimodal = [], []
        has_image = False
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_result":
                out.append({"role": "tool", "tool_call_id": str(b.get("tool_use_id", "")),
                            "content": str(b.get("content", ""))})
            elif b.get("type") == "text":
                text = str(b.get("text", ""))
                texts.append(text)
                multimodal.append({"type": "text", "text": text})
            elif b.get("type") == "image":
                mime, data = _image_payload(b)
                image_url: dict = {"url": f"data:{mime};base64,{data}"}
                detail = str(b.get("detail") or "").lower()
                if detail in ("low", "high", "original", "auto"):
                    image_url["detail"] = detail
                multimodal.append({"type": "image_url", "image_url": image_url})
                has_image = True
            elif _is_image_block(b):
                raise ValueError("non-canonical image block reached openai adapter")
        if has_image:
            out.append({"role": role, "content": multimodal})
        elif texts:
            out.append({"role": role, "content": "\n".join(texts)})
    return out


#: Ключ пометки «аргументы не прочитались как JSON». Здесь стояло `args = {}` — и для
#: руки, у которой все параметры опциональны (`check_email`, `my_agenda`,
#: `recent_turns`), пустой словарь это не ошибка, а ВЫПОЛНЕНИЕ ДРУГОГО ДЕЙСТВИЯ с
#: дефолтами: намерение модели исчезало без следа. На реле обрыв длинных `arguments`
#: реален, поэтому битый JSON теперь остаётся видимым фактом, а решение о нём
#: принимает тул-цикл, а не парсер.
MALFORMED_JSON_KEY = "__malformed_json__"
#: Сколько сырых знаков аргументов оставляем в пометке — для диагноза, не для хранения.
MALFORMED_JSON_KEEP = 2000


def is_malformed_json_input(call_input: object) -> bool:
    """Это блок, у которого аргументы не прочитались как JSON?"""
    return isinstance(call_input, dict) and MALFORMED_JSON_KEY in call_input


def blocks_from_openai(msg) -> list:
    """openai message -> блоки anthropic-формы (text + tool_use, JSON-аргументы распарсены).

    Аргументы, которые не читаются как JSON-объект, НЕ подменяются пустым словарём:
    блок помечается `MALFORMED_JSON_KEY`, и исполнять его нечем (разбор — выше).
    """
    blocks: list[dict] = []
    text = getattr(msg, "content", None)
    if text:
        blocks.append({"type": "text", "text": str(text)})
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        raw = getattr(fn, "arguments", "") or ""
        malformed = {MALFORMED_JSON_KEY: str(raw)[:MALFORMED_JSON_KEEP]}
        try:
            args = json.loads(raw) if str(raw).strip() else {}
        except Exception:
            args = malformed
        if not isinstance(args, dict):
            args = malformed
        blocks.append({"type": "tool_use", "id": str(getattr(tc, "id", "")),
                       "name": str(getattr(fn, "name", "")), "input": args})
    return blocks


# PASS: z.ai's web_search_20250305 emulation does NOT behave like real Anthropic's server tool
# (a single clean final text block). It returns the whole exchange inline as one response with
# content = [text(narrates the call, e.g. "🔍 Z.ai Built-in Tool... Executing on server..."),
# server_tool_use, text(dumps the RAW search-result JSON as "Output: ..."), tool_result,
# text(the actual clean synthesized answer)] — three "text"-typed blocks, two of them scaffolding.
# Naively joining every text block (as real Anthropic responses safely allow) leaked raw JSON into
# replies (observed live 05.07, see soul/skills/web_search_reader.md). Any block type besides
# text/tool_use means "opaque server-side work happened here" — text at or before the LAST such
# block is narration/result-dump, never her voice; only text strictly after it is genuine reply.
# When no such block is present (the overwhelming common case, incl. every existing client tool_use
# turn), this is a no-op and every text block is kept exactly as before.
_KNOWN_BLOCK_TYPES = ("text", "tool_use")


def _blocks_from_anthropic(resp) -> list:
    raw = getattr(resp, "content", None) or []
    last_opaque = -1
    for i, b in enumerate(raw):
        if getattr(b, "type", None) not in _KNOWN_BLOCK_TYPES:
            last_opaque = i
    blocks = []
    for i, b in enumerate(raw):
        t = getattr(b, "type", None)
        if t == "text":
            if i <= last_opaque:
                continue  # server-tool narration/raw-result scaffolding — not her voice
            blocks.append({"type": "text", "text": b.text})
        elif t == "tool_use":
            blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return blocks


def text_of(resp) -> str:
    """Текст ответа: понимает LLMResponse и сырые ответы anthropic-SDK (легаси-вызовы)."""
    if isinstance(resp, LLMResponse):
        return resp.text
    return "".join(b["text"] for b in _blocks_from_anthropic(resp)
                   if b["type"] == "text").strip()


# --------------------------------------------------------------------------- #
#  PASS 9.1: cost-meter — расход по ролям, копится в usage.json (STATE + панель «Расход»)
# --------------------------------------------------------------------------- #

def _usage_load() -> dict:
    try:
        data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}  # битый/нет — честный старт с нуля (это счётчик, не память)


def _usage_add(role: str, usage: dict, fallback: bool = False, model: str = "",
               vision: bool = False) -> None:
    """Прибавить успешный вызов. Ошибки записи НЕ роняют вызов модели — только debug-лог.

    PASS 18.5: model — по-модельный подразрез дня (день→роль→models→{in,out,calls}):
    панель ценит расход по ФАКТИЧЕСКОЙ модели, а не по текущей задним числом. Старые
    записи без models живы — формат обратносовместим.
    Известный допуск: read-modify-write из двух процессов (praxis и mailbot) без лока —
    редкие потерянные инкременты терпим, счётчик наблюдательный."""
    try:
        data = _usage_load()
        # ⚠ Сутки расхода — ЕЁ: иначе ночной расход ложится во вчерашний день,
        # а отсечка хранения срабатывает на четыре часа раньше срока.
        day = praxis_time.day_key()
        cutoff = praxis_time.day_key(praxis_time.today() - _dt.timedelta(days=USAGE_KEEP_DAYS))
        for k in [k for k in data if isinstance(k, str) and k < cutoff]:
            del data[k]  # ротация: 60 дней достаточно для панели и трендов
        # ⚠ Стамп ставится ТОЛЬКО новой записи. Запись, начатая старым кодом сегодня,
        # остаётся без схемы до конца суток: внутри неё семантики уже смешаны, и
        # никакая формула их не разделит. Ключ живёт ВНУТРИ записи роли, а не в дне:
        # день перебирают `.values()` и зовут `.get` — лишний скаляр рядом с ролями
        # уронил бы appetite и brain (там это ловится общим except и молча даёт ноль).
        d = data.setdefault(day, {}).setdefault(
            role, {"in": 0, "out": 0, "calls": 0, "fallback": 0, "schema": USAGE_SCHEMA})
        u_in = int((usage or {}).get("in", 0) or 0)
        u_out = int((usage or {}).get("out", 0) or 0)
        d["in"] = int(d.get("in", 0)) + u_in
        d["out"] = int(d.get("out", 0)) + u_out
        d["calls"] = int(d.get("calls", 0)) + 1
        d["fallback"] = int(d.get("fallback", 0)) + (1 if fallback else 0)
        if vision:
            d["vision"] = int(d.get("vision", 0)) + 1
        u_cr = int((usage or {}).get("cache_read", 0) or 0)
        u_cc = int((usage or {}).get("cache_creation", 0) or 0)
        if u_cr or u_cc:
            d["cache_read"] = int(d.get("cache_read", 0)) + u_cr
            d["cache_creation"] = int(d.get("cache_creation", 0)) + u_cc
        if model:
            # ⚠ ПОСЛЕДНИЙ РЕАЛЬНО ОТВЕТИВШИЙ. Замер 08.08: конфиг, манифест рельсов и
            # строка состояния втроём говорили `gpt-5.6-sol`, а из ста пятидесяти ходов
            # восемнадцать прошли на `gpt-5.6-terra`. Praxis честно пересказывала свой
            # кадр — неправду говорил кадр. Её слово: «формулировка "я на Sol" без
            # различения настроенного и наблюдённого действительно вводит меня в
            # заблуждение». Накопительная статистика по моделям была и раньше; не было
            # ФАКТА последнего ответа, а он, по её словам, не должен подменяться средним.
            d["last"] = {"model": str(model), "at": praxis_time.stamp(),
                         "vision": bool(vision)}
            m = d.setdefault("models", {}).setdefault(
                str(model), {"in": 0, "out": 0, "calls": 0, "schema": USAGE_SCHEMA})
            m["in"] += u_in
            m["out"] += u_out
            m["calls"] += 1
            if vision:
                m["vision"] = int(m.get("vision", 0)) + 1
            if u_cr or u_cc:
                m["cache_read"] = m.get("cache_read", 0) + u_cr
                m["cache_creation"] = m.get("cache_creation", 0) + u_cc
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = USAGE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(USAGE_PATH)
    except Exception:
        log.debug("llm: usage не записался", exc_info=True)


def usage_days(n: int = 7) -> dict:
    """Расход за последние n дней: {день: {роль: {in,out,calls,fallback}}} (для панели)."""
    data = _usage_load()
    cutoff = praxis_time.day_key(praxis_time.today() - _dt.timedelta(days=max(0, n - 1)))
    return {day: v for day, v in sorted(data.items()) if isinstance(day, str) and day >= cutoff}


def usage_line() -> str:
    """Одна строка для STATE: расход основного и вспомогательного каналов за сегодня."""
    today = _usage_load().get(praxis_time.day_key()) or {}
    if not today:
        return ""

    def _k(v: int) -> str:
        return f"{v / 1000:.1f}к" if v >= 1000 else str(v)

    parts = []
    for role in ROLES:
        d = today.get(role)
        if not isinstance(d, dict) or not d.get("calls"):
            continue
        fb = f", фолбэков {d['fallback']}" if d.get("fallback") else ""
        # 02.08: не сырая пара r/c, а величина, которая действительно означает расход.
        # Кэшированный префикс провайдер уже держит; платит она за СВЕЖИЙ вход.
        #
        # ⚠ 27.08: прежняя строка делила `cr / in` и звала свежим `in − cr`. Это верно
        # ровно для openai, где `in` — ВЕСЬ промпт вместе с кэшем. У anthropic `in` —
        # только свежее, кэш лежит отдельно, и там `cr` бывает в пятнадцать раз больше
        # `in` (замер 26.08: glm `in` 849 137 при `cache_read` 13 205 312). Одна формула
        # поверх общего ведра занижала свежий вход в семь раз: 2,2 млн вместо 15,4 млн.
        # Со схемой 2 `in` везде значит одно — НЕ из кэша, — поэтому знаменатель полный:
        # `cr + in`, а свежее это сам `in`.
        cr = int(d.get("cache_read", 0))
        cc = int(d.get("cache_creation", 0))
        u_in = int(d.get("in", 0))
        if (cr or cc) and int(d.get("schema", 1)) >= USAGE_SCHEMA:
            total = cr + u_in
            share = f", кэш {100 * cr / total:.0f}%" if total else ""
            fresh = f", свежего входа {_k(u_in)}" if u_in else ""
            cache = f"{share}{fresh}"
            if cc:
                cache += f", записи в кэш {_k(cc)}"
        else:
            cache = ""
        parts.append(f"{_ROLE_RU[role]} {_k(int(d.get('in', 0)))}→{_k(int(d.get('out', 0)))} ток "
                     f"({d['calls']} выз.{fb}{cache})")
    return ", ".join(parts)


def pricing() -> dict:
    """Прайс из llm.json (опциональный, руками). Нет цен — панель показывает только токены."""
    p = _config().get("pricing")
    return p if isinstance(p, dict) else {}


# --------------------------------------------------------------------------- #
#  Вызовы фреймворков
# --------------------------------------------------------------------------- #

_OPENAI_COMPLETION_TOKENS_RE = re.compile(r"^(o\d|gpt-5)")


# ────────────────────── ОДИН сторож пустоты на все пути ──────────────────────
#
# ⚠ 15.08.2026. Контракт речи опирается на то, что пустой ответ модели ловится РАНЬШЕ
# цикла и не читается как «она решила замолчать». Проверка показала, что опора держалась
# на ОДНОМ пути из трёх:
#   * `_call_anthropic` не проверял пустоту ни одной строкой — собирал LLMResponse и отдавал
#     как есть, `stop_reason` по умолчанию 'end_turn'. Она может сама перевести голос на
#     anthropic рукой `switch_brain`, и опора исчезла бы МОЛЧА, без единого признака;
#   * `_call_openai` возвращал не-стриминговый ответ (единый объект с .choices[0].message)
#     РАНЬШЕ сторожа: любой openai-совместимый сервер с пустым message возвращался успешным
#     'end_turn';
#   * сам сторож был дизъюнкцией и путал два разных случая (см. TornStreamError).
# Поэтому сторож теперь один и зовётся на КАЖДОМ возврате обоих фреймворков.
#
# Explicit max_tokens is an incomplete generation, not an empty transport response.
# Preserve it even before the first visible block: server-side work may already have
# happened. The caller must persist its evidence and stop without success or replay.
# Other empty responses retain the existing narrow transport-retry contract.
def _guard_answer(out: LLMResponse) -> LLMResponse:
    """Единственная проверка «это вообще ответ?». Возвращает ответ или поднимает свой класс."""
    # An explicit budget stop is not transport emptiness, even if all tokens went
    # into hidden reasoning/server work. Retrying could repeat that work. Return
    # the incomplete response for the caller to persist and terminalize honestly.
    if str(out.stop_reason or "") == "max_tokens":
        return out
    if not out.blocks and not out.text.strip():
        raise EmptyResponseError(out.text[:200] or "пустой ответ (ни текста, ни инструмента)")
    # ⚠ Три пути расходились ещё и ЗДЕСЬ, и это нашлось прогоном, а не глазами. Ответ из
    # одних пробелов на стриминговом пути был пустотой (`"".join(parts).strip()` съедал его
    # до нуля блоков), а на anthropic и на не-стриминговом openai проезжал наверх готовым
    # блоком без единого знака. Одно правило на три пути — значит и здесь одно: блок, в
    # котором нечего сказать, ответом не является. Инструмент — является всегда, даже без
    # текста: `stay_silent` это её решение, а не пустота канала.
    if not out.text.strip() and not any(
            (b or {}).get("type") in {"tool_use", "tool_use_fragment"}
            for b in (out.blocks or ())):
        raise EmptyResponseError("пустой ответ (блок есть, знаков в нём нет)")
    if str(out.stop_reason or "") == "error":
        # Текст/блоки есть, но канал закончил ошибкой — оборванный стрим, НЕ пустота.
        log.warning("llm: стрим %s/%s оборван ошибкой уже после %d знаков и %d блоков — "
                    "это не пустой ответ, повтора по тому же каналу не будет",
                    out.framework or "?", out.model or "?", len(out.text or ""), len(out.blocks or ()))
        raise TornStreamError(out.text[:200] or "стрим оборван ошибкой", partial=out)
    return out


def _call_anthropic(cli, model: str, *, system, messages, tools, max_tokens, thinking,
                    reasoning_effort: str | None = None) -> LLMResponse:
    # reasoning_effort — словарь реле (openai-путь). Здесь глубину задаёт thinking-бюджет;
    # для glm-* ступень роли проецируется в z.ai-диалект (25.08, см. _GLM_EFFORT),
    # для остальных моделей принимается-и-игнорируется, а не роняет вызов на общем kw-пути.
    kw: dict = {"model": model, "max_tokens": max_tokens,
                "messages": messages_to_anthropic(messages)}
    if system:
        kw["system"] = system
    anthropic_tools = tools_to_anthropic(tools)
    if anthropic_tools:
        kw["tools"] = anthropic_tools
    glm = _is_glm(model)
    if thinking:
        kw["thinking"] = {"type": "enabled", "budget_tokens": int(thinking)}
        kw["max_tokens"] = max(int(max_tokens), int(thinking) + 1024)
        if glm:
            # z.ai бюджет budget_tokens не соблюдает (проба 08.09: 1024 → 587 знаков
            # размышления, как и без бюджета); глубину там задаёт только ступень.
            # Бюджет проецируем в ступень тем же словарём, что и на openai-пути.
            kw["output_config"] = {"effort": _GLM_EFFORT.get(
                _openai_reasoning_effort(thinking) or "low", "low")}
    elif reasoning_effort and glm:
        # 25.08: у GLM-5.3 thinking обязателен, глубину задаёт ступень; без неё
        # z.ai молча думает на max каждый вызов (замер 24.08: 10-15с на ответ).
        # ⚠ 08.09: поле `reasoning_effort` в теле запроса Anthropic-совместимый
        # эндпойнт z.ai МОЛЧА ИГНОРИРУЕТ (проба: low/max/omitted — одинаковые
        # ~250 токенов; владелец видел max при low в конфигу). Слушает он родное
        # поле Anthropic API `output_config.effort` (low → 50–80 токенов на той же
        # задаче). SDK ≥1.4 знает его штатно (издание: родное поле, не extra_body —
        # одна правда в запросе). Явный thinking-бюджет вызова по-прежнему сильнее
        # ступени роли.
        kw["thinking"] = {"type": "enabled"}
        kw["output_config"] = {"effort": _GLM_EFFORT.get(
            str(reasoning_effort).strip().lower(), "low")}
    # PASS 10.0: всегда стримом — SDK кидает «Streaming is required for operations that
    # may take longer than 10 minutes» на больших max_tokens (ночные окна умирали, кап
    # сгорал впустую). get_final_message() возвращает тот же Message (blocks/usage/
    # stop_reason на месте). Фейки без .stream (легаси-тесты) идут через create.
    streamer = getattr(getattr(cli, "messages", None), "stream", None)
    if callable(streamer):
        with streamer(**kw) as st:
            resp = st.get_final_message()
    else:
        resp = cli.messages.create(**kw)
    usage = getattr(resp, "usage", None)
    _usage = {"schema": USAGE_SCHEMA,
              "in": int(getattr(usage, "input_tokens", 0) or 0),
              "out": int(getattr(usage, "output_tokens", 0) or 0)}
    # Anthropic cache metrics — видимость hit-rate и реальной экономии
    _cr = getattr(usage, "cache_read_input_tokens", None)
    _cc = getattr(usage, "cache_creation_input_tokens", None)
    if _cr:
        _usage["cache_read"] = int(_cr)
    # 13.09 (её reviewer на K1): ноль от провайдера — это «записи в кэш не было», и он
    # обязан доехать до леджера как 0, а не пропасть как «не сообщил» (-1). `if _cc:`
    # терял ровно это различие; проверяем на None, а не на истинность.
    if _cc is not None:
        _usage["cache_creation"] = int(_cc)
    # Сторож общий с openai-путём: до 15.08 здесь его не было вовсе, и пустой ответ glm
    # уезжал наверх успешным 'end_turn' — то есть неотличимо от её решения промолчать.
    actual_model = str(getattr(resp, "model", None) or model)
    if actual_model != model:
        # Anthropic-compatible providers may accept one catalogue alias and serve another
        # model without an error. Preserve what the response says: usage/brain telemetry
        # downstream is explicitly defined as the actually observed backend, not config.
        log.warning("llm: запросила %s/%s, ответ пришёл от %s", "anthropic", model, actual_model)
    return _guard_answer(LLMResponse(
        text=text_of(resp), blocks=_blocks_from_anthropic(resp),
        stop_reason=str(getattr(resp, "stop_reason", None) or "end_turn"),
        usage=_usage,
        framework="anthropic", model=actual_model))


_OPENAI_STOP = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}


def _openai_reasoning_effort(thinking) -> str | None:
    """Бюджет thinking (токены, как у anthropic) -> ступень reasoning_effort релея.

    Релей по умолчанию шлёт effort=none (скорость), но когда код ЯВНО просит
    подумать (консолидация, ночные проходы) — глубина должна вернуться, иначе
    thinking на openai-пути так и остался бы молча съеденным."""
    if not thinking:
        return None
    budget = int(thinking)
    if budget <= 2048:
        return "low"
    if budget <= 8192:
        return "medium"
    return "high"


# Словарь ступеней — ДОСЛОВНО словарь реле (REASONING_EFFORTS в chat_completions.rs):
# своя копия имён разъехалась бы молча, поэтому список закреплён тестом на исходник
# реле в _relay_prod_src. Реле по умолчанию гасит рассуждение (effort=none);
# per-request поле сильнее его дефолта, чужой openai-сервер молча проигнорирует.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
# 25.08: проекция ступеней реле в словарь z.ai для glm-* (GLM-5.3: low/high/max,
# СЕРВЕРНЫЙ ДЕФОЛТ — max на каждый вызов; thinking обязателен и невыключаем,
# поэтому «none/minimal» глубже low не проецируются — выключить нечего).
_GLM_EFFORT = {"none": "low", "minimal": "low", "low": "low",
               "medium": "high", "high": "high", "xhigh": "max"}


def _is_glm(model: str) -> bool:
    return str(model or "").strip().lower().startswith("glm-")


def _effective_effort(thinking, role_effort) -> str | None:
    """Чья ступень едет в запрос. Явный thinking кода (просьба подумать в конкретном
    месте) сильнее фоновой ступени роли; ступень роли — ЕЁ рычаг (switch_brain
    action=reasoning), и неизвестное значение честно отбрасывается, а не угадывается."""
    explicit = _openai_reasoning_effort(thinking)
    if explicit:
        return explicit
    value = str(role_effort or "").strip().lower()
    return value if value in REASONING_EFFORTS else None


# Image capability is an allowlist. Unknown/empty names fail closed: passing pixels
# merely because a slug is unfamiliar is the dangerous direction. Keep the currently
# deployed GPT family and established Claude vision families sighted; for GLM retain
# only the explicitly verified shapes. glm-5.3v is not served by the provider catalog;
# glm-5.3-flash was verified sighted live (21.09, receipt in run d4cd73c0) and is
# allowlisted explicitly below — not as a default, but as a validated sighted slug.
# Keep each accepted family syntactically bounded. Prefix matching is not enough here:
# `gpt-5-text-only` or `claude-sonnet-text-only` must remain unknown and fail closed.
# GPT codenames are the sighted relay models actually deployed in this installation;
# the Claude shapes cover the established dated 3.x and numbered family slugs only.
# Established sighted families in deployment; cross-leg sighted relays are configured
# explicitly, e.g. roles.voice.vision_models = {"openai": "gpt-5.6-terra"}.
_SIGHTED_GPT_RE = re.compile(
    r"(?i)^gpt-(?:4o(?:-mini)?|4\.\d+(?:-(?:mini|nano))?|"
    r"(?:5|6)(?:\.\d+)?-(?:sol|terra|luna|astra))$"
)
_SIGHTED_CLAUDE_RE = re.compile(
    r"(?i)^claude-(?:"
    r"3(?:[.-]\d+){0,2}-(?:sonnet|opus|haiku)(?:-\d{8})?|"
    r"(?:sonnet|opus|haiku)-\d+(?:-\d+)?(?:-\d{8})?"
    r")$"
)
_SIGHTED_GLM_V_RE = re.compile(r"(?i)^glm-\d+(?:\.\d+)?v(?:$|-flashx?$)")
# Live-verified 21.09.2026 by direct z.ai /api/anthropic probe (receipt run
# run-20260921T194915933494Z-d4cd73c0): glm-5.3-flash on this subscription DOES see
# pixels (200, correct red-square/blue-circle answer) while glm-5.3 and glm-4.5v
# hallucinate. flashx is NOT covered (1311, outside subscription).
_SIGHTED_GLM_FLASH_RE = re.compile(r"(?i)^glm-(?:4\.6|5(?:\.\d)?)-flash$")


def role_model(role: str = "voice") -> str:
    """Имя модели роли по конфигу (без ротации каталога): для честных сообщений в кадре."""
    try:
        return str(_config()["roles"][role].get("model") or "")
    except Exception:
        return ""


def accepts_images(role: str = "voice", model: str | None = None) -> bool:
    """Whether a model is in a deliberately verified sighted family."""
    name = str(model if model is not None else role_model(role) or "").strip()
    return bool(
        _SIGHTED_GLM_V_RE.fullmatch(name)
        or _SIGHTED_GLM_FLASH_RE.fullmatch(name)
        or _SIGHTED_GPT_RE.match(name)
        or _SIGHTED_CLAUDE_RE.match(name)
    )


def _catalog_has_model(framework: str, model: str) -> bool:
    """Validate against a known catalog; an unavailable catalog is not authorization."""
    available = _available_models(framework)
    return bool(available) and model in available


def _catalog_vision_candidates(framework: str) -> list[str]:
    """Sighted models actually served by a framework's catalog, most recent first.

    A missing/unavailable catalog returns [] — an unavailable catalog is not
    authorization (fail-closed, same rule as `_catalog_has_model`).
    """
    available = _available_models(framework)
    if not available:
        return []
    sighted = [m for m in available if accepts_images(model=m)]
    sighted.sort(key=_model_version_key, reverse=True)
    return sighted


def _model_version_key(model: str) -> tuple:
    """Numeric (major, minor) of a slug for version ordering; unknowns sort lowest."""
    m = re.match(r"(?i)^[a-z]+-(\d+)(?:\.(\d+))?", str(model or ""))
    if not m:
        return (-1, -1)
    return (int(m.group(1)), int(m.group(2) or 0))


def vision_model(role: str = "voice", model: str | None = None,
                 framework: str | None = None) -> str:
    """Validated sighted replacement for one effective framework/account leg.

    ``vision_models.<framework>`` is the cross-leg configuration. The legacy
    ``vision_model`` belongs only to the role's configured primary framework.
    There is NO hardcoded default anymore (glm-5.3-flash is retired as a default):
    without an explicit configuration the first sighted model of the requested
    framework's catalog is used (catalog is authority; provider does not list
    glm-5.3v — that mapping belongs in config, not in code), otherwise the
    OpenAI leg's catalog, otherwise fail closed with "".
    Catalog absence, a text-only/unknown candidate, or a missing catalog fails closed.
    """
    name = str(model if model is not None else role_model(role) or "").strip()
    if not name or accepts_images(model=name):
        return ""
    try:
        rc = _config()["roles"][role]
    except Exception:
        return ""
    primary_fw = str(rc.get("framework") or "")
    fw = str(framework or primary_fw)
    configured = ""
    by_framework = rc.get("vision_models")
    if isinstance(by_framework, dict):
        configured = str(by_framework.get(fw) or "").strip()
    if not configured and fw == primary_fw:
        configured = str(rc.get("vision_model") or "").strip()
    if configured:
        # An explicit choice is binding: no catalog-only substitution under it.
        if not accepts_images(model=configured):
            return ""
        return configured if _catalog_has_model(fw, configured) else ""
    # No explicit configuration: catalog-driven cross-leg pick, current framework first.
    for leg in (fw, "openai"):
        for candidate in _catalog_vision_candidates(leg):
            return candidate
    return ""


def can_see(role: str = "voice") -> bool:
    """Will pixels reach the configured primary model or a validated replacement?"""
    try:
        rc = _config()["roles"][role]
        framework = str(rc.get("framework") or "")
        model = _resolve_model(framework, str(rc.get("model") or ""))
    except Exception:
        return False
    return accepts_images(model=model) or bool(vision_model(role, model, framework))


_IMAGE_BLOCK_TYPES = frozenset({"image", "image_url", "input_image"})
_DATA_IMAGE_RE = re.compile(r"^data:([^;,]+);base64,([A-Za-z0-9+/=\r\n]+)$")


def _is_image_block(block) -> bool:
    return isinstance(block, dict) and str(block.get("type") or "") in _IMAGE_BLOCK_TYPES


def _has_image_blocks(messages) -> bool:
    """Detect every image block shape accepted by either transport adapter."""
    return any(_is_image_block(block)
               for message in (messages or []) if isinstance(message, dict)
               for block in ([*message.get("content", [])]
                             if isinstance(message.get("content"), list) else ()))


def _omit_image_blocks(messages, model: str):
    """Copy the tape and replace every image-bearing block with a payload-free fact."""
    marker = (f"[image omitted before model call: {model or 'text-only model'} "
              "does not accept images and no valid sighted replacement is configured; "
              "NO pixels are available, so do not describe them]")
    out = []
    for message in messages or []:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            out.append(message)
            continue
        blocks = [({"type": "text", "text": marker} if _is_image_block(block) else block)
                  for block in message["content"]]
        out.append(dict(message, content=blocks))
    return out


def _canonicalize_image_blocks(messages):
    """Convert adapter-native image_url/input_image blocks to canonical images.

    Only data URLs are accepted here: remote URL fetching is not an adapter feature and
    must not be introduced implicitly. Invalid native shapes remain image-bearing and
    will be removed if the effective route is text-only; sighted calls reject them
    locally rather than forwarding an unnormalised payload.
    """
    out = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            out.append(message)
            continue
        blocks = []
        for block in content:
            if not _is_image_block(block) or block.get("type") == "image":
                blocks.append(block)
                continue
            raw = block.get("image_url")
            if isinstance(raw, dict):
                raw = raw.get("url")
            raw = str(raw or block.get("url") or "")
            match = _DATA_IMAGE_RE.fullmatch(raw)
            if not match:
                raise ValueError("image_url/input_image must contain a base64 data image")
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": match.group(1).lower(),
                "data": match.group(2).replace("\r", "").replace("\n", ""),
            }})
        out.append(dict(message, content=blocks))
    return out


# ======================================================================================
# УЗКИЙ ВЗГЛЯД: одно обращение к зрячей модели вместо целого кадра.
#
# ЗАМЕР 16.09, комната -1001240718803, ход с картинкой. Сейчас пиксели едут внутри её
# ОБЫЧНОГО кадра, и весь кадр уходит в зрячую модель:
#
#     схемы рук        75 612 знаков
#     system           22 342
#     эпоха E          26 820
#     лента            37 313
#     живой хвост      34 682
#     ИТОГО           196 770 знаков текста — чтобы посмотреть на одну картинку
#
# У зрячей модели свой префикс кэша, и ходы с картинкой редки, поэтому он почти всегда
# холодный. Счёт за сутки без астры: 10 ходов из 32 съели 471 693 свежих токена — ПОЛОВИНУ
# всего свежего. Каждый такой ход платит дважды: полный кадр во flash и остывший возврат.
#
# ЧТО ДЕЛАЕТ РЫЧАГ. Перед маршрутизацией пиксели уходят зрячей модели ОДНИМ узким
# обращением: только картинка и просьба описать, без её ленты, досье, хвоста и ста одной
# руки. Ответ встаёт в кадр текстом на место картинки, и её собственный ход идёт дальше на
# её модели, с полным контекстом и по тёплому префиксу.
#
# ⚠ ЧТО ЭТО МЕНЯЕТ ДЛЯ НЕЁ, ВСЛУХ. Под рычагом она пикселей больше НЕ ВИДИТ — она читает
# описание, сделанное другой моделью. Это обмен, а не чистый выигрыш: сейчас на ходе с
# картинкой она видит сама, но отвечает более слабой моделью; под рычагом отвечает своей,
# но глазами чужими. Подменённый блок говорит об этом прямо, чтобы она не приняла описание
# за собственное зрение.
#
# ⚠ ОТКАЗ — НЕ МОЛЧАНИЕ. Не получилось описать (нет зрячей модели, упал вызов, пустой
# ответ) — накладка НЕ подменяет ничего и возвращает ленту как была: дальше отрабатывает
# прежняя маршрутизация, то есть целый кадр в зрячую модель. Хуже, чем было, не станет.
VISION_PREPASS_LEVER = "PRAXIS_VISION_PREPASS"
#: Защита от рекурсии: узкий вызов идёт через тот же `chat`, и второй раз смотреть нечего.
_IN_VISION_PREPASS = _cv.ContextVar("praxis_vision_prepass", default=False)
#: Потолок описания. Одна картинка — это абзац-другой, а не сочинение.
VISION_PREPASS_MAX_TOKENS = 1200

_VISION_PREPASS_SYS = (
    "You are the sighted leg of one agent. You get ONE image and nothing else from its "
    "context: no conversation, no people, no history. Describe only what is actually "
    "visible — objects, layout, colours, UI state, numbers — and transcribe every piece of "
    "text verbatim in its own language. Do not guess who sent it or why. If the image is "
    "unreadable or empty, say exactly that."
)
_VISION_PREPASS_ASK = "Опиши, что на этой картинке, и дословно перепиши весь текст на ней."


def vision_prepass_enabled() -> bool:
    """Рычаг узкого взгляда. Умолчание — ВЫКЛЮЧЕНО: кадр прежний байт-в-байт."""
    return str(os.environ.get(VISION_PREPASS_LEVER) or "").strip().lower() in {
        "1", "true", "yes", "on"}


def _vision_prepass_marker(model: str, text: str) -> str:
    return ("[эту картинку посмотрела зрячая модель " + str(model or "?")
            + " отдельным узким обращением: твоего кадра она не видела, и пикселей в "
            "ЭТОМ кадре нет — ниже её описание, а не твоё зрение]\n" + text)


def _describe_images_narrowly(role: str, framework: str, model: str, messages):
    """Заменить блоки-картинки описанием, снятым одним узким обращением к зрячей модели.

    Возвращает (лента, сколько описано). Ноль описанных — лента та же самая, и вызывающий
    код обязан отработать так, как отрабатывал без рычага.
    """
    if not _has_image_blocks(messages) or _IN_VISION_PREPASS.get():
        return messages, 0
    if accepts_images(model=model):
        return messages, 0            # её модель и так зрячая — смотреть нечем помогать
    sighted = vision_model(role, model, framework)
    if not sighted:
        return messages, 0
    try:
        canonical = _canonicalize_image_blocks(messages)
    except Exception:
        log.warning("узкий взгляд: картинка не привелась к канону — смотрю как раньше",
                    exc_info=True)
        return messages, 0
    out, described = [], 0
    token = _IN_VISION_PREPASS.set(True)
    try:
        for message in canonical:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list) or not any(_is_image_block(b) for b in content):
                out.append(message)
                continue
            blocks = []
            for block in content:
                if not _is_image_block(block):
                    blocks.append(block)
                    continue
                text = _look_once(role, sighted, block)
                if not text:
                    # Ни одной подмены в этом сообщении: пусть едет прежним путём.
                    return messages, 0
                blocks.append({"type": "text",
                               "text": _vision_prepass_marker(sighted, text)})
                described += 1
            out.append(dict(message, content=blocks))
    finally:
        _IN_VISION_PREPASS.reset(token)
    return (out, described) if described else (messages, 0)


def _look_once(role: str, sighted: str, block: dict) -> str:
    """Одно обращение к зрячей модели: картинка и просьба. Пусто — значит не вышло."""
    try:
        answer = chat(role, system=_VISION_PREPASS_SYS,
                      messages=[{"role": "user", "content": [
                          block, {"type": "text", "text": _VISION_PREPASS_ASK}]}],
                      max_tokens=VISION_PREPASS_MAX_TOKENS, model=sighted)
    except Exception:
        log.warning("узкий взгляд упал — картинка поедет прежним путём", exc_info=True)
        return ""
    text = str(getattr(answer, "text", "") or "").strip()
    if not text:
        log.warning("узкий взгляд вернул пустое — картинка поедет прежним путём")
    return text


def _route_image_leg(role: str, framework: str, model: str, messages):
    """Return (effective_model, leg_messages, substituted, omitted) for one leg."""
    if not _has_image_blocks(messages):
        return model, messages, False, False
    if accepts_images(model=model):
        return model, _canonicalize_image_blocks(messages), False, False
    replacement = vision_model(role, model, framework)
    # ⚠ 20.09.2026. Замена должна принадлежать ЭТОЙ ноге. `vision_model` умеет отдать
    # зрячую модель СОСЕДНЕГО фреймворка — это её контракт, им пользуется кросс-нога
    # фолбэка, где фреймворк меняется вместе с моделью. Здесь он НЕ меняется: имя
    # `gpt-6-astra` уезжало на z.ai, та отвечала `400 [1211] Unknown Model`, и весь ход
    # с картинкой умирал. Три картинки владельца 20.09 (19:31, 20:25, 20:47) погибли
    # ровно так. Чужую модель снимаем и честно убираем пиксели: пусть скажет, что не
    # видит, — это лучше, чем немота.
    if replacement and not _catalog_has_model(framework, replacement):
        log.warning("llm: %s — зрячая замена %s не из каталога %s; снимаю пиксели",
                    _ROLE_RU[role], replacement, framework)
        replacement = ""
    if replacement:
        return replacement, _canonicalize_image_blocks(messages), True, False
    return model, _omit_image_blocks(messages, model), False, True


_NO_PIXELS_RESPONSE = (
    "NO pixels were available to this model; image payloads were removed before the call. "
    "I cannot describe or verify the image."
)


def _mark_no_pixels_response(response: LLMResponse) -> LLMResponse:
    """Make central fail-closed sanitation visible in the returned and durable answer."""
    response.text = (_NO_PIXELS_RESPONSE + ("\n\n" + response.text if response.text else ""))
    response.blocks = ([{"type": "text", "text": _NO_PIXELS_RESPONSE}]
                       + list(response.blocks or ()))
    return response

# --------------------------------------------------------------- адрес кэша префикса
#
# ⭐ 10.08.2026. Реле берёт `prompt_cache_key` ИЗ ЗАПРОСА и только при его отсутствии
# считает свой `conversation_affinity = hash(model + messages[0])`
# (`/opt/relay/Code/src/core/chat_completions.rs:357-360`). Хэш байтов всего системного
# промпта делал ключ уникальным у 79 из 84 промптов за сутки — то есть апстрим считал
# почти каждый ход новым разговором.
#
# ЗАМЕР на 2 823 записанных кадрах (`*-model-input.log`, без единого вызова модели):
# общий префикс двух СОСЕДНИХ системных промптов — медиана 12 886 знаков (83,4%).
# Расходятся они ровно в двух местах: блок `Operational continuity: STATE, receipts…`
# и строка про аудиторию. Если считать соседей ВНУТРИ одной аудитории, общий префикс
# растёт: личка Егора 15 292 · публичная комната 15 747 · её собственный прогон 15 767 ·
# незнакомый собеседник 13 478. То есть ключ на АДРЕС даёт ~+2 400…+2 900 знаков
# (≈ +750 токенов) кэшируемого префикса на каждом первом вызове хода.
#
# ⚠ ЧЕСТНАЯ ГРАНИЦА. Ключ решает только МАРШРУТИЗАЦИЮ к кэшу. Само попадание требует
# совпадения байтов префикса, а его всё ещё рвёт подвижный блок состояния — это
# следующая ступень, и обещать «89,5 → 94,1%» здесь нельзя: та формула уже была
# опровергнута замером 09.08. Мера успеха — доля `cache_read` на ПЕРВЫХ вызовах ходов,
# A/B на её обычной жизни.
#
# Ключ — АДРЕС, а не хэш содержимого: маркер аудитории и `room_id`, то есть ровно те
# места, где по замеру расходятся соседние кадры. Не нашли адреса — ключ не шлём вовсе,
# и реле работает как раньше. Откат: `PRAXIS_CACHE_KEY=off`.
#
# ⚠⚠ 15.08.2026. ЛОВУШКА, КОТОРУЮ ЗДЕСЬ ЗАЛОЖИЛИ НЕВОЛЬНО, И ЧЕМ ОНА ОБЕЗВРЕЖЕНА.
# Адрес аудитории опознаётся АНГЛИЙСКИМИ ПОДСТРОКАМИ ПРОЗЫ её кадра — «private owner
# channel», «public room», «your own run». А план работ прямо предполагает переписать эти
# места от её лица по-русски. В день такой правки `cache_address` начнёт возвращать ""
# — молча, без ошибки и без лога: ключ просто перестанет уходить, реле вернётся к хэшу
# всего system, и кэш префикса умрёт БЕЗ СИМПТОМОВ. Прибор бы молчал, а молчание прибора
# в этом доме уже читали как факт о мире.
#
# Три вещи против этого, и ни одна не заменяет двух других:
#   1. `test_cache_address.py` СТРОИТ ЖИВОЙ КАДР через `agent.build_system_parts` для всех
#      четырёх аудиторий и требует непустого адреса на каждой. Своей копии литерала у
#      теста больше нет — переписали прозу, тест красный в тот же прогон.
#   2. Ниже — РОЗЕТКА под структурный ключ: если кадр однажды принесёт `audience_key=<тег>`,
#      адрес берётся оттуда и проза перестаёт быть несущей. Вилки пока нет (agent.py такого
#      поля не печатает), поэтому сегодня работает ветка прозы, и адрес БАЙТ-В-БАЙТ прежний
#      — это проверено прогоном на живых кадрах, а не обещано.
#   3. `_address_miss` кричит в лог, когда кадр большой, а адреса в нём нет: это симптом на
#      проде, а не только в прогоне тестов.
_CACHE_MARKS = (
    ("private owner channel", "owner"),
    ("public room", "room"),
    ("your own run", "run"),
    ("not in the kn", "guest"),
)
#: Теги живого разговорного кадра приходят и из прозы, и из будущей структурной розетки.
#: Технические свежие контексты Forge не проходят через agent.build_system_parts и потому
#: объявляют отдельный структурный адрес по роли. Держать их в `_CACHE_MARKS` нельзя:
#: там тест требует, чтобы каждому ПРОЗОВОМУ маркеру соответствовал живой основной кадр.
_CACHE_STRUCTURAL_TAGS = ("forge_scout", "forge_worker", "forge_reviewer")
_CACHE_TAGS = tuple(dict.fromkeys(
    [tag for _, tag in _CACHE_MARKS] + list(_CACHE_STRUCTURAL_TAGS)))
_CACHE_ROOM_RE = re.compile(r"room_id=(-?\d+)")
#: Дополнительная область технического диалога. Сегодня её печатает Forge: роли мало,
#: потому что два scout-а разных задач иначе разделили бы affinity; scope — короткий digest
#: task_id+agent_id и потому стабилен внутри одного свежего контекста, но не склеивает соседей.
_CACHE_SCOPE_RE = re.compile(r"cache_scope=([a-z0-9_-]{1,32})")
#: Розетка (см. пункт 2 выше). Имя поля и словарь значений — то же, что у прозы, поэтому
#: включение структурного ключа не меняет НИ ОДНОГО существующего адреса.
#: ⚠ Ищется первое вхождение по всему кадру, а в кадре есть и написанное людьми (досье,
#: записки комнат). Значит чужой текст со строкой `audience_key=…` может увести адрес. Цена
#: этому — промах кэша, не власть и не приватность; ровно та же цена, что у `room_id=`,
#: который так живёт с самого начала. Поэтому поле полагается печатать в `state.channel_facts`
#: рядом с `room_id`, а не где придётся.
_CACHE_KEY_RE = re.compile(r"audience_key=([a-z_]{1,16})")
#: Кадр меньше этого — не кадр, а проба/техвызов; на них отсутствие адреса нормально.
_CACHE_FRAME_MIN = 2000
_ADDRESS_MISSES = {"n": 0}


def _address_miss(text: str) -> None:
    """Большой кадр без адреса — это симптом, а не тишина. Кричим один раз на процесс."""
    if len(text) < _CACHE_FRAME_MIN:
        return
    _ADDRESS_MISSES["n"] += 1
    if _ADDRESS_MISSES["n"] == 1:
        log.warning("llm: в системном кадре (%d знаков) не нашлось адреса кэша — ни "
                    "audience_key=, ни одного из маркеров %s. prompt_cache_key не уйдёт, "
                    "реле вернётся к хэшу всего system, кэш префикса умрёт. Скорее всего "
                    "кадр переписан, а маркеры остались прежними",
                    len(text), [n for n, _ in _CACHE_MARKS])


def cache_address(model: str, sys_text: str) -> str:
    """Стабильный адрес разговора для `prompt_cache_key`. Пусто — значит не шлём."""
    if (os.getenv("PRAXIS_CACHE_KEY") or "").strip().lower() in ("off", "0", "no", "false"):
        return ""
    text = sys_text or ""
    if not text:
        return ""
    # Структурный ключ сильнее прозы: он объявлен кадром прямо, а не опознан по словам.
    named = _CACHE_KEY_RE.search(text)
    mark = named.group(1) if named and named.group(1) in _CACHE_TAGS else ""
    if not mark:
        mark = next((tag for needle, tag in _CACHE_MARKS if needle in text), "")
    room = _CACHE_ROOM_RE.search(text)
    # scope принадлежит только объявленному техническому кадру. Чужая строка
    # `cache_scope=...` внутри досье/комнаты не должна менять обычный разговорный адрес.
    scope = (_CACHE_SCOPE_RE.search(text) if mark in _CACHE_STRUCTURAL_TAGS else None)
    if not mark and not room:
        _address_miss(text)
        return ""
    # ⚠ ЗНАЕМАЯ ДЫРА, И ОНА НЕ СИМПТОМ. Знакомый не-родственник в общей комнате не даёт НИ
    # ОДНОГО маркера аудитории (в кадре не выбрана ни одна из трёх веток) — адрес выходит
    # `-:<room_id>`. Он рабочий и по комнатам различается, поэтому кричать тут нельзя: это
    # был бы постоянный ложный крик в любой групповой переписке, а привыкшего к крику
    # прибора всё равно что нет. Ловит эту дыру тест на живом кадре, а не лог.
    if scope:
        return "praxis:%s:%s:%s:%s" % (
            model or "?", mark or "-", room.group(1) if room else "-", scope.group(1))
    # Существующие разговорные адреса сохраняются байт-в-байт: добавление технического
    # scope не должно одним релизом обнулить их тёплый кэш.
    return "praxis:%s:%s:%s" % (model or "?", mark or "-", room.group(1) if room else "-")


def _max_tokens_field(cli) -> str:
    """Имя потолка ответа — по АДРЕСАТУ клиента, не по имени модели.

    Настоящий OpenAI (`api.openai.com`) для нынешних моделей принимает только
    `max_completion_tokens` и отвечает 400 на `max_tokens` («Unsupported parameter:
    max_tokens is not supported with this model. Use max_completion_tokens instead»);
    реле и совместимые серверы объявляют `max_tokens`, а `max_completion_tokens`
    у реле в `ChatRequest` нет вовсе — serde выбросил бы его молча (тот самый год
    тишины, см. `_call_openai`). Различаем по хосту `base_url` клиента: имя модели у
    двух установок может быть ровно одно и то же, а требования — противоположные,
    потому что на том конце другой сервер (живой случай 19.09.2026, баг-репорт Arête).

    Признак один и жёсткий — хост `openai.com` (и поддомены). Это заплатка до
    «профиля провайдера», где адресат объявляет свои поля сам; но и профиль должен
    исходить из того же: спрашивать адресата, а не угадывать по имени модели.
    """
    try:
        host = (urlparse(str(getattr(cli, "base_url", "") or "")).hostname or "").lower()
    except (ValueError, TypeError, AttributeError):
        host = ""
    direct_openai = host == "openai.com" or host.endswith(".openai.com")
    return "max_completion_tokens" if direct_openai else "max_tokens"


def _call_openai(cli, model: str, *, system, messages, tools, max_tokens, thinking,
                 reasoning_effort: str | None = None) -> LLMResponse:
    msgs = messages_to_openai(messages)
    sys_text = system_text(system)
    if sys_text:
        msgs = [{"role": "system", "content": sys_text}] + msgs
    # PASS: локальный relay (codex/ChatGPT-прокси на host.docker.internal:5011) ВСЕГДА отдаёт SSE —
    # не-стриминговый create() возвращал пусто. Просим stream и агрегируем; реальный OpenAI тоже
    # умеет stream, так что путь общий. Фейки/не-стриминговые сервера отдают единый объект (ветка ниже).
    kw: dict = {"model": model, "messages": msgs, "stream": True,
                "stream_options": {"include_usage": True}}
    address = cache_address(model, sys_text)
    if address:
        kw["extra_body"] = {"prompt_cache_key": address}
    effort = _effective_effort(thinking, reasoning_effort)
    if effort:
        # неизвестное SDK поле — только через extra_body; релей примет reasoning_effort
        # per-request поверх своего дефолта (none), чужой сервер молча проигнорирует
        kw.setdefault("extra_body", {})["reasoning_effort"] = effort
    # ⚠ 13.08.2026. ЗДЕСЬ ГОД ЛЕЖАЛО ПОЛЕ, КОТОРОЕ НИКТО НЕ ПРИНИМАЛ.
    # Ветка выбирала имя по имени модели: `^(o\d|gpt-5)` → `max_completion_tokens`. Все три
    # её модели (`gpt-5.6-terra`, `-luna`, `-sol`) матчатся, то есть ветка `max_tokens` не
    # исполнялась НИКОГДА. А у реле в `ChatRequest` поля `max_completion_tokens` нет, и
    # структура не помечена `deny_unknown_fields` — значит serde выбрасывал его молча.
    # Год тишины: ни ошибки, ни лога, ни красного теста.
    #
    # Правило, из которого теперь исходим (слова Егора): шлём то, что адресат ОБЪЯВИЛ.
    # Адресат сегодня — реле, оно объявляет `max_tokens`. Что реле делает с этим полем
    # дальше — отдельный вопрос (сегодня: не читает вовсе), и он не повод слать мимо.
    #
    # ⚠ КОГДА ЭТОТ ПУТЬ ПОВЕДЁТ К НАСТОЯЩЕМУ OpenAI — пересмотреть: reasoning-моделям там
    # нужен именно `max_completion_tokens`. Это работа «профиля провайдера», и она названа
    # отдельно; здесь важно не угадывать адресата по имени модели.
    #
    # 19.09.2026: повёл. У пользователя Элен 0.7.1 клиент смотрит прямо в api.openai.com,
    # мимо реле, и каждый запрос падал с 400 «Use max_completion_tokens instead». Имя поля
    # теперь выбирает адресат — по хосту `base_url` клиента (`_max_tokens_field`), путь
    # через реле не задет.
    kw[_max_tokens_field(cli)] = max_tokens
    ot = tools_to_openai(tools)
    if ot:
        kw["tools"] = ot
    resp = cli.chat.completions.create(**kw)
    # единый объект с .choices[0].message — не-стрим (фейк-клиент/обычный сервер): прежний разбор
    choices = getattr(resp, "choices", None)
    if choices and getattr(choices[0], "message", None) is not None:
        # ⚠ Этот return уходил ДО сторожа: openai-совместимый сервер, отдавший единый объект
        # с пустым message, возвращался успешным 'end_turn'. Теперь сторож один на оба разбора.
        return _guard_answer(_openai_from_completion(resp, model))
    # итератор чанков (relay/реальный OpenAI stream); здоровье канала проверяет тот же сторож
    return _guard_answer(_openai_from_stream(resp, model))


def _note_truncation(out: "LLMResponse", role: str) -> None:
    """Ответ, обрезанный потолком max_tokens, обязан быть НАЗВАН — и назван С ВЛАДЕЛЬЦЕМ.

    ⚠ `stop_reason == "max_tokens"` объявлен в схеме ответа с самого начала, но не читала
    его ни одна строка кода. Фраза, оборванная на полуслове, уходила в Telegram как
    законченная; а если обрыв случался до первого готового блока — ход записывался как
    «промолчала сама», байт-в-байт как настоящее решение промолчать. На проде потолок
    сейчас щедрый (voice 32768), но дефолт в коде — 1024, и потерять `llm.json` значит
    получить обрыв на каждом длинном ответе, ничего об этом не узнав.

    ⚠ Отметка идёт ОТ ИМЕНИ РОЛИ. Слот в turns был один на процесс и доставался первому
    читателю: обрыв судьи приватности (та же `evaluator`, тот же поток, внутри её же
    хода) записывался в кольцо как «её ответ оборван». Владельца теперь несёт отметка.

    ⚠ И зовётся это из `chat()`, а не из `_call_openai`. `ping()` ходит к модели мимо
    `chat()` с `max_tokens=1` — то есть КАЖДАЯ проверка канала (`brain.py:208`) кончалась
    `stop_reason='max_tokens'` и клала отметку об обрыве на 1 символ, которую следующий
    её ход забирал как обрыв собственной фразы. Пинг — не ответ, и биографии у него нет.

    Полный ответ роли ОТМЕНЯЕТ её прежнюю отметку: это честный срок годности вместо
    гадания по часам (см. turns.clear_truncation).
    """
    truncated = str(getattr(out, "stop_reason", "")) == "max_tokens"
    if truncated:
        log.warning("ответ оборван потолком max_tokens (роль %s, %s): %d символов, "
                    "блоков %d — это НЕ законченная фраза",
                    role, getattr(out, "model", "?"),
                    len(out.text or ""), len(out.blocks or ()))
    try:
        import turns
        if truncated:
            turns.note_truncated(model=str(getattr(out, "model", "") or ""),
                                 chars=len(out.text or ""), owner=role)
        else:
            turns.clear_truncation(owner=role)
    except Exception:
        log.debug("обрыв ответа не записался в прожитый ход", exc_info=True)


def _openai_from_completion(resp, model: str) -> LLMResponse:
    choice = (getattr(resp, "choices", None) or [None])[0]
    msg = getattr(choice, "message", None)
    finish = str(getattr(choice, "finish_reason", None) or "stop")
    blocks = blocks_from_openai(msg) if msg is not None else []
    usage = getattr(resp, "usage", None)
    cached = _openai_cached_tokens(usage)
    return LLMResponse(
        text="\n".join(b["text"] for b in blocks if b["type"] == "text").strip(),
        blocks=blocks, stop_reason=_OPENAI_STOP.get(finish, finish),
        usage={"schema": USAGE_SCHEMA,
               "in": _openai_fresh_in(usage, cached),
               "out": int(getattr(usage, "completion_tokens", 0) or 0),
               **({"cache_read": cached} if cached else {})},
        framework="openai", model=model)


def _openai_fresh_in(usage, cached: int) -> int:
    """Вход, НЕ пришедший из кэша, — общая мера для обоих фреймворков (схема 2).

    27.08.2026. openai отдаёт `prompt_tokens` как ВЕСЬ промпт, включая кэшированный
    префикс (`prompt_tokens_details.cached_tokens` — его подмножество). anthropic
    отдаёт `input_tokens` уже БЕЗ кэша, отдельным полем `cache_read_input_tokens`.
    Мы клали и то и другое в одно имя `in`, и любое отношение поверх дневного ведра
    переставало быть величиной. Проверка на 4058 живых вызовах: у anthropic
    `cache_read > in` в 96.4% случаев, у openai — в 0.0%; двух семантик в одном поле
    хватило, чтобы свежий вход за сутки показался 2,2 млн вместо 15,4 млн.

    Здесь openai приводится к anthropic-смыслу: платим полностью за то, что вернулось
    НЕ из кэша. `max(0, …)` — на случай, если провайдер однажды посчитает иначе:
    отрицательного расхода не бывает, а тихо уехавший в минус счётчик заметить нечем.
    """
    total = int(getattr(usage, "prompt_tokens", 0) or 0)
    if not total and isinstance(usage, dict):
        total = int(usage.get("prompt_tokens", 0) or 0)
    return max(0, total - max(0, int(cached or 0)))


def _openai_cached_tokens(usage) -> int:
    """Кэшированный префикс из openai-совместимого usage, или 0.

    02.08.2026. Кэш на этом пути АВТОМАТИЧЕСКИЙ — провайдер сам кэширует совпадающий
    префикс, включить его нельзя, его зарабатывают стабильностью байтов. Отчитывается он
    единственным полем `usage.prompt_tokens_details.cached_tokens`, а мы читали только
    антропиковские имена (`cache_read`/`cache_creation`) — поэтому в учёте по всем ходам
    через gpt стояли нули, и это читалось как «кэш не работает». Означало же оно
    «не сообщено»: отличить одно от другого было нечем, и на этом я построил неверный
    вывод для Егора. Реле деталь тоже теряло — исправлено тем же заходом.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None and isinstance(usage, dict):
        details = usage.get("prompt_tokens_details")
    if details is None:
        return 0
    value = getattr(details, "cached_tokens", None)
    if value is None and isinstance(details, dict):
        value = details.get("cached_tokens")
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _stream_transport_error(exc: Exception) -> bool:
    """Узкий класс физического обрыва HTTP/SSE при чтении уже открытого ответа.

    OpenAI SDK отдаёт такой сбой не как финальный SSE `finish_reason=error`, а голым
    исключением из итератора (`httpx.RemoteProtocolError`, иногда через `__cause__`).
    Не называем транспортом произвольную ошибку разбора: иначе баг нашего коллектора
    превратится в безобидный fallback и исчезнет из диагностики.
    """
    allowed = {
        ("httpx", "RemoteProtocolError"),
        ("httpcore", "RemoteProtocolError"),
        ("httpx", "ReadError"),
        ("httpcore", "ReadError"),
    }
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        module = type(cur).__module__.split(".", 1)[0]
        if (module, type(cur).__name__) in allowed:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _openai_from_stream(stream, model: str) -> LLMResponse:
    """Собрать ответ из SSE-чанков openai-протокола (delta.content + delta.tool_calls)."""
    parts: list[str] = []
    tools_acc: dict[int, dict] = {}
    finish = "stop"
    u_in = u_out = u_cached = 0
    try:
        for chunk in stream:
            # 25.09: терминал реле — ДО choices и content. Диагностика лимита подписки
            # приходила текстом в content и читалась как ответ модели (см.
            # RelayTerminalError); типизированный код становится типизированной ошибкой,
            # а коды апстрима (torn/upstream_error) идут прежним путём finish_reason=error.
            term = _relay_terminal_of(chunk)
            if term is not None:
                typed = _terminal_error(term)
                if typed is not None:
                    raise typed
            u = getattr(chunk, "usage", None)
            if u is not None:
                u_in = int(getattr(u, "prompt_tokens", 0) or 0) or u_in
                u_out = int(getattr(u, "completion_tokens", 0) or 0) or u_out
                u_cached = _openai_cached_tokens(u) or u_cached
            chs = getattr(chunk, "choices", None) or []
            if not chs:
                continue
            ch = chs[0]
            d = getattr(ch, "delta", None)
            if d is not None:
                c = getattr(d, "content", None)
                if c:
                    parts.append(c)
                for tc in getattr(d, "tool_calls", None) or []:
                    idx = int(getattr(tc, "index", 0) or 0)
                    slot = tools_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        if getattr(fn, "name", None):
                            slot["name"] = fn.name
                        if getattr(fn, "arguments", None):
                            slot["args"] += fn.arguments
            if getattr(ch, "finish_reason", None):
                finish = ch.finish_reason
    except Exception as exc:
        if not _stream_transport_error(exc):
            raise
        # Физический HTTP-обрыв переводим в обычный LLMResponse с stop=error и отдаём
        # ЕДИНСТВЕННОМУ семантическому сторожу. Он сам различит: до первого содержимого
        # это EmptyResponseError (same-channel retry допустим), после текста/фрагмента руки
        # — TornStreamError с partial (повтор поверх уже начатого ответа запрещён).
        partial = _openai_stream_result(parts, tools_acc, "error", u_in, u_out, u_cached, model)
        try:
            return _guard_answer(partial)
        except BrokenChannelError as broken:
            raise broken from exc
    return _openai_stream_result(parts, tools_acc, finish, u_in, u_out, u_cached, model)


def _openai_stream_result(parts: list[str], tools_acc: dict[int, dict], finish: str,
                           u_in: int, u_out: int, u_cached: int, model: str) -> LLMResponse:
    text = "".join(parts).strip()
    blocks: list[dict] = [{"type": "text", "text": text}] if text else []
    for idx in sorted(tools_acc):
        s = tools_acc[idx]
        if not s["name"]:
            if _OPENAI_STOP.get(finish, finish) in {"error", "max_tokens"}:
                # Начало tool-call уже приехало, даже если имя ещё не успело. Это не
                # исполнимый tool_use, но и не пустота: partial обязан удержать факт,
                # чтобы общий сторож дал TornStreamError, а не same-channel retry.
                blocks.append({"type": "tool_use_fragment", "id": s["id"] or f"call_{idx}",
                               "name": "", "arguments": s["args"]})
            continue
        malformed = {MALFORMED_JSON_KEY: str(s["args"])[:MALFORMED_JSON_KEEP]}
        try:
            args = json.loads(s["args"]) if (s["args"] or "").strip() else {}
        except Exception:
            args = malformed
        blocks.append({"type": "tool_use", "id": s["id"] or f"call_{idx}",
                       "name": s["name"],
                       "input": args if isinstance(args, dict) else malformed})
    # ⚠ 15.08.2026, найдено враждебной сверкой. `finish_reason='error'` НЕ имеет права
    # спрятаться за `tool_use`. Прежний порядок («есть инструмент → значит tool_use»)
    # затирал признак обрыва, сторож его не видел и отдавал наверх ГОТОВЫЙ ход с рукой,
    # чьи аргументы приехали наполовину: обрезанный json не парсится и молча становится
    # `{}` (ниже). То есть оборванный стрим выглядел как её решение вызвать руку — ровно
    # та подмена, ради которой писан весь этот участок, только на другом пути.
    # Budget exhaustion likewise dominates even syntactically complete tool calls.
    mapped = _OPENAI_STOP.get(finish, finish)
    stop = (mapped if mapped in {"error", "max_tokens"}
            else "tool_use" if any(b["type"] == "tool_use" for b in blocks) else mapped)
    return LLMResponse(text=text, blocks=blocks, stop_reason=stop,
                       usage={"schema": USAGE_SCHEMA,
                              "in": max(0, u_in - u_cached), "out": u_out,
                              **({"cache_read": u_cached} if u_cached else {})},
                       framework="openai", model=model)


# --------------------------------------------------------------------------- #
#  Ротация наименований моделей: провайдеры переименовывают/выкатывают модели
#  (relay крутит gpt-5.x, z.ai — glm-*). Если заданное имя пропало из списка —
#  берём ближайшее живое (та же семья, старшая версия), не роняя ход. Кэш на TTL.
# --------------------------------------------------------------------------- #

_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}   # framework -> (expiry_epoch, [ids])
_MODELS_TTL = float(os.getenv("PRAXIS_MODELS_TTL", "600"))


def _models_url(base_url: str) -> str:
    """.../v1/models для любого base_url (снимаем хвостовой /v1, ставим один раз)."""
    b = (base_url or "").rstrip("/")
    if b.endswith("/v1"):
        b = b[:-3]
    return b + "/v1/models"


def _available_models(framework: str) -> list[str]:
    """Список id моделей провайдера (кэш TTL). Под стендом сеть не трогаем вовсе."""
    # ⚠ 03.08.2026. У соседа по файлу (`_client_for`) шов PRAXIS_TEST есть, а здесь его
    # не было — и `panel.brain_catalog()` под тестом уходил ДВУМЯ живыми HTTPS-запросами
    # на api.z.ai и api.openai.com. Фейк-клиент спасал только там, где его успели
    # зарегистрировать; в `test_panel_organs` его нет вовсе. Ключей в окружении гейта
    # тоже нет (форж отдаёт подпроцессу белый список), так что запрос шёл с пустым
    # бирером на публичный адрес: стенд стучался наружу и ждал таймаута.
    if os.environ.get("PRAXIS_TEST") or framework in _TEST_CLIENTS:
        return []
    now = _time.time()
    hit = _MODELS_CACHE.get(framework)
    if hit and hit[0] > now:
        return hit[1]
    ids: list[str] = []
    try:
        fw = (_config().get("frameworks") or {}).get(framework) or {}
        base, key = fw.get("base_url") or "", fw.get("api_key") or ""
        if base:
            import urllib.request
            req = urllib.request.Request(_models_url(base), headers={
                "Authorization": f"Bearer {key}", "x-api-key": key,
                "anthropic-version": "2023-06-01"})
            data = json.loads(urllib.request.urlopen(req, timeout=6).read())
            ids = [str(m.get("id")) for m in (data.get("data") or []) if m.get("id")]
    except Exception as e:
        log.debug("llm: список моделей %s не получить (%s)", framework, type(e).__name__)
        ids = []
    _MODELS_CACHE[framework] = (now + _MODELS_TTL, ids)
    return ids


def _pick_replacement(model: str, avail: list[str]) -> str:
    """Замена пропавшей модели: предпочесть ту же семью (буквенный префикс), старшую версию."""
    def fam(m: str) -> str:
        mm = re.match(r"^([a-zA-Z]+)", m or "")
        return mm.group(1).lower() if mm else ""

    def ver(m: str) -> list[int]:
        return [int(x) for x in re.findall(r"\d+", m or "")]

    pool = [m for m in avail if fam(m) == fam(model)] or avail
    return max(pool, key=ver) if pool else ""


def _resolve_model(framework: str, model: str) -> str:
    """Имя модели с учётом ротации: заданное живо — оставить; пропало — ближайшее живое."""
    avail = _available_models(framework)
    if not avail:
        return model
    # Compatibility migration: an early UI exposed the generic gpt-5.6 alias,
    # but the Codex backend only serves concrete sol/terra/luna slugs. Prefer
    # sol deterministically when the relay's live model list proves it exists,
    # even if a stale discovery cache still advertises the rejected generic id.
    if framework == "openai" and model == "gpt-5.6" and "gpt-5.6-sol" in avail:
        pick = "gpt-5.6-sol"
    elif model in avail:
        return model
    else:
        pick = _pick_replacement(model, avail)
    # `_pick_replacement` can itself select the stale generic alias.  Normalize
    # the selected candidate too, otherwise a caller that resolves twice sees
    # two different models (`gpt-5.6` first, `gpt-5.6-sol` second).
    if framework == "openai" and pick == "gpt-5.6" and "gpt-5.6-sol" in avail:
        pick = "gpt-5.6-sol"
    if pick and pick != model:
        log.warning("llm: модель %s пропала у %s — ротация имени на %s", model, framework, pick)
        try:
            _journal(f"модель {model} пропала у {framework}, выбрана {pick} (ротация имён провайдера)")
        except Exception:
            pass
        return pick
    return model


def _resolve_fallback_model(primary_framework: str, primary_model: str,
                            fallback_framework: str, fallback_model: str) -> str:
    """Разрешить fallback так, чтобы он не схлопнулся обратно в ту же модель.

    Ротация имён полезна, когда провайдер переименовал модель, но опасна для явно
    заданного fallback: отсутствующий slug другого семейства мог ротироваться в
    текущую primary-модель. Тогда после обрыва мы не переключались, а повторяли тот
    же самый запрос тому же самому backend, хотя журнал называл это fallback.

    Если каталогу известна другая живая модель того же framework, выбираем её.
    Если другой модели нет, возвращаем пустую строку: вызывающий сохранит исходную
    ошибку вместо ложного повторного исполнения той же моделью.
    """
    resolved = _resolve_model(fallback_framework, fallback_model)
    if fallback_framework != primary_framework:
        return resolved

    primary = primary_model
    if resolved != primary:
        return resolved

    available: list[str] = []
    for candidate in _available_models(fallback_framework):
        normalized = _resolve_model(fallback_framework, candidate)
        if normalized != primary and normalized not in available:
            available.append(normalized)
    alternate = _pick_replacement(fallback_model, available)
    if alternate:
        log.warning(
            "llm: fallback %s/%s схлопнулся в primary %s — беру отличающуюся модель %s",
            fallback_framework, fallback_model, primary, alternate,
        )
        try:
            _journal(
                f"fallback {fallback_framework}/{fallback_model} совпал с primary {primary} "
                f"после ротации имени; выбрана отличающаяся модель {alternate}"
            )
        except Exception:
            pass
        return alternate

    log.error(
        "llm: fallback %s/%s схлопнулся в primary %s, другой живой модели нет",
        fallback_framework, fallback_model, primary,
    )
    return ""


def _call(framework: str, model: str, *, _resolved: bool = False, **kw) -> LLMResponse:
    cli = _client_for(framework)
    if cli is None:
        raise RuntimeError(f"llm: фреймворк {framework} не настроен (нет ключа)")
    if not _resolved:
        model = _resolve_model(framework, model)  # ротация наименований провайдера
    if framework == "anthropic":
        return _call_anthropic(cli, model, **kw)
    return _call_openai(cli, model, **kw)


def _fallbackable(e: Exception) -> bool:
    if isinstance(e, BrokenChannelError):
        # Вырожденный ответ канала — повод уйти на другой фреймворк. Оба вида: и пустота,
        # и оборванный стрим. Различаются они не здесь, а в повторе по СВОЕМУ каналу.
        return True
    if isinstance(e, RelayTerminalError):
        # Лимит подписки, нужен вход, слоты отказали: фолбэк уместен — но только на
        # ДРУГОЙ эндпойнт (проверяется в chat(), не здесь).
        return True
    name = type(e).__name__
    if name in _FALLBACK_ERRORS:
        return True
    try:
        if int(getattr(e, "status_code", 0) or 0) >= 500:
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(e, TimeoutError)


# ─────────────────── транспортный повтор на пустом ответе ───────────────────
# ⚠ 10.08.2026. Апстрим отвечает `200 OK`, начинает стрим и обрывает его событием
# `error` → `response.failed`: 14 обрывов на 4441 запрос за сутки. Реле отдаёт пустой
# стрим, `_call_openai` честно поднимает EmptyResponseError — и до сегодня ЕДИНСТВЕННЫМ
# ответом был фолбэк на ДРУГОЙ фреймворк. Повтора по своему каналу не было вовсе, а один
# обрыв убивает весь её ход вместе со всей сделанной в нём работой.
#
# ПОЧЕМУ ЭТО НЕ ПОВТОР ЕЁ РЕШЕНИЯ — и это главный вопрос, а не арифметика.
# EmptyResponseError поднимается ТОЛЬКО когда в стриме нет ни текста, ни единого блока
# (см. _call_openai ниже). Её молчание так не выглядит никогда: оно едет либо
# инструментом stay_silent — а это блок, — либо сентинелом-текстом. Значит повтор
# физически не может переспросить её поверх решения промолчать. Её слово 10.08: «это
# транспортный повтор, не повтор моего решения; дыры в рассуждении не вижу».
#
# ⚠ ЧЕГО ЭТО НЕ ОБЕЩАЕТ. Соблазнительный расчёт «0,3% в кубе = три случая на миллион»
# верен только при НЕЗАВИСИМОСТИ обрывов. Её же поправка того же дня: утренний кластер
# 10.08 (четыре обрыва за шестнадцать минут) показывает, что они бывают коррелированы.
# Поэтому: между попытками стоит ПАУЗА (коррелированный всплеск переживается временем, а
# не числом попыток), и никакой цифры надёжности здесь не заявляется.
EMPTY_RETRIES = max(0, int(os.getenv("PRAXIS_EMPTY_RETRIES", "2") or 0))
EMPTY_RETRY_PAUSE_SEC = float(os.getenv("PRAXIS_EMPTY_RETRY_PAUSE_SEC", "2.0") or 0.0)


def _call_retrying_empty(fw: str, model: str, retries: int | None = None,
                         *, _resolved: bool = False, **kw):
    """(ответ, сколько повторов понадобилось). Повторяет ТОЛЬКО EmptyResponseError.

    Любая другая ошибка уходит наверх немедленно и попадает в прежний фолбэк-путь:
    таймаут, 429 и падение авторизации повторять по тому же каналу бессмысленно.

    ⭐ И `TornStreamError` — тоже «любая другая». Он НЕ потомок EmptyResponseError именно
    затем, чтобы сюда не попасть: там текст уже приехал, и повтор был бы переспросом
    поверх уже сказанного, а согласие 10.08 давалось на пустоту. Граница держится типом,
    а не памятью читателя.
    """
    last = None
    retries = EMPTY_RETRIES if retries is None else max(0, int(retries))
    for attempt in range(retries + 1):
        try:
            return _call(fw, model, _resolved=_resolved, **kw), attempt
        except EmptyResponseError as exc:
            last = exc
            if attempt >= retries:
                break
            _time.sleep(EMPTY_RETRY_PAUSE_SEC * (attempt + 1))
            log.warning("llm: пустой ответ %s/%s — повтор %d из %d по тому же каналу",
                        fw, model, attempt + 1, EMPTY_RETRIES)
    raise last


def _resolve_requested_model(role: str, requested: str) -> tuple[str, str]:
    """Resolve a per-call model without mutating the configured role.

    A bare model name is addressable when it appears either in a provider's live
    catalogue or in the configured main/fallback slots.  If both frameworks expose
    the same name, the role's configured framework wins.  Returning the resolved
    slug keeps the ordinary provider-rotation compatibility while callers can retain
    the original request separately as provenance.
    """
    requested = str(requested or "").strip()
    if not requested:
        raise ValueError("llm: пустой адрес модели")
    cfg = _config()
    rc = (cfg.get("roles") or {}).get(role) or {}
    matches: list[str] = []
    for framework in FRAMEWORKS:
        names = set(_available_models(framework))
        for role_cfg in (cfg.get("roles") or {}).values():
            role_fw = str(role_cfg.get("framework") or "")
            if role_fw == framework and role_cfg.get("model"):
                names.add(str(role_cfg["model"]))
            fallback_fw = (str(role_cfg.get("fallback_framework") or "").strip()
                           or ("openai" if role_fw == "anthropic" else "anthropic"))
            if fallback_fw == framework and role_cfg.get("fallback_model"):
                names.add(str(role_cfg["fallback_model"]))
        if requested in names:
            matches.append(framework)
    if not matches:
        raise ValueError(f"llm: модель {requested!r} отсутствует в доступном каталоге")
    framework = str(rc.get("framework") or "")
    if framework not in matches:
        framework = matches[0]
    return framework, _resolve_model(framework, requested)


def chat(role: str, *, system=None, messages: list, tools: list | None = None,
         max_tokens: int | None = None, thinking: int | None = None,
         end_after_spoken: bool = False, model: str | None = None) -> LLMResponse:
    """Вызов модели по роли, с необязательным адресным override без смены роли.

    Фолбэк на противоположный фреймворк — один повтор, честно в дневник.

    `end_after_spoken=True` — контракт v3 (17.08): в этом ходе реплика УЖЕ доставлена
    рукой, поэтому фолбэк обезоружен — вторая модель не смеет переисполнить принятое
    решение (луна слала ту же реплику заново — четыре копии за две минуты). Пустота
    получает ОДИН ретрай своим каналом (её решение №3) и затем читается как конец хода;
    оборванный стрим тоже закрывает ход сказанным, а не будит фолбэк."""
    if role not in ROLES:
        raise ValueError(f"llm: неизвестная роль {role!r}")
    cfg = _config()
    rc = cfg["roles"][role]
    requested_model = str(model or "").strip()
    if requested_model:
        fw, resolved_model = _resolve_requested_model(role, requested_model)
    else:
        fw = rc["framework"]
        resolved_model = _resolve_model(fw, rc["model"])
    # Resolve the primary exactly once for this logical call.  Retries and fallback
    # comparison must use the model that was actually sent, not a fresh catalog
    # interpretation that may change between attempts.
    model = resolved_model
    # Keep the caller's original image tape available to a different fallback leg.
    # Sanitising one text-only leg must not blind a later natively sighted leg.
    image_messages = messages
    # Узкий взгляд — ДО маршрутизации: если картинку удалось описать одним обращением,
    # дальше едет обычная текстовая лента, и подмены модели не происходит вовсе.
    if vision_prepass_enabled():
        described_messages, described = _describe_images_narrowly(role, fw, model, messages)
        if described:
            log.info("llm: %s — картинок описано узким взглядом: %d; ход остаётся на %s",
                     _ROLE_RU[role], described, model)
            messages = image_messages = described_messages
    model, messages, vision_used, pixels_omitted = _route_image_leg(
        role, fw, model, image_messages)
    if vision_used:
        log.info("llm: %s — image turn routed to %s/%s", _ROLE_RU[role], fw, model)
    elif pixels_omitted:
        log.warning("llm: %s — %s/%s has no valid sighted replacement; pixels removed",
                    _ROLE_RU[role], fw, model)
    mt = int(max_tokens or rc.get("max_tokens") or DEFAULT_MAX_TOKENS[role])
    # ЕЁ фоновая ступень рассуждения роли (switch_brain action=reasoning, 19.08).
    # Явный thinking вызывающего кода сильнее — правило в _effective_effort.
    role_effort = str(rc.get("reasoning_effort") or "").strip() or None
    st = _STATE[role]
    t0 = _time.time()
    # Пауза до этого вызова — рядом с исходом. Префикс остывает ВРЕМЕНЕМ, и
    # проверяемая гипотеза именно такая: обрыв липнет к простою.
    _gap = _call_gap(role)
    # Отпечаток набора рук снимается ОДИН раз на вызов и едет во все три следа
    # этого хода — упавший вызов обязан быть сравним с удавшимся по тому же
    # признаку.
    _tools = tool_offerings.fingerprint(tools)[:16]
    # 13.09 (K1). Отпечаток system и адрес кэша — ОДИН раз на вызов, во все следы этого
    # хода. system здесь уже тот, что уйдёт на провод (`frame_serve.select` — в agent).
    _sys_text = system_text(system)
    _sys_len = len(_sys_text)
    _sys_sha = (hashlib.sha1(_sys_text.encode("utf-8", "replace")).hexdigest()[:8]
                if _sys_text else "")
    _key = cache_address(model, _sys_text) if fw == "openai" else ""
    try:
        # 25.09: эндпойнт основной ноги закрыт лимитом подписки (RelayTerminalError ниже)
        # и есть запасная нога на ДРУГОМ эндпойнте — не ходим в реле за очередным
        # отказом, а сразу уходим на запасного. Нет другой ноги — идём как обычно: реле
        # само скажет, если окно ещё закрыто, и снимет удержание, если уже нет.
        _hold = _endpoint_hold(fw)
        if _hold is not None and _fallback_leg_elsewhere(rc, fw):
            raise _RELAY_TERMINAL_CLASSES.get(str(_hold.get("code")), RelayTerminalError)(
                str(_hold.get("words") or _hold.get("message") or "эндпойнт удержан"),
                code=str(_hold.get("code") or ""), slot=str(_hold.get("slot") or ""),
                resets_at=_hold.get("until"), synthetic=True)
        resp, empty_retries = _call_retrying_empty(
            fw, model, retries=(1 if end_after_spoken else None), _resolved=True,
            system=system, messages=messages, tools=tools,
            max_tokens=mt, thinking=thinking, reasoning_effort=role_effort)
        if _hold is not None:
            _release_endpoint(fw)
        if empty_retries:
            # Повтор — не бесплатная тишина: он попадает в её журнал, иначе «стало реже
            # падать» будет неотличимо от «мы это спрятали».
            _journal("%s: канал отдал пустой ответ, помог повтор №%d по тому же каналу"
                     % (_ROLE_RU[role], empty_retries))
        # Обрыв потолком — факт этой роли. Раньше он ставился внутри `_call_openai`, то
        # есть anthropic-путь обрыва не замечал вовсе, а `ping()` замечал лишний.
        _note_truncation(resp, role)
        if st["on_fallback"]:
            log.info("llm: %s вернулась на основной канал (%s/%s)", _ROLE_RU[role], fw, model)
        st["on_fallback"], st["last_error"] = False, ""
        # 9.1: расход копится; сбой записи вызова не роняет. PASS 22: по ФАКТИЧЕСКОМУ имени
        # (после ротации _resolve_model конфигное имя может врать в by-model разрезе).
        resp.vision = bool(vision_used)
        if pixels_omitted:
            _mark_no_pixels_response(resp)
        _usage_add(role, resp.usage, model=(resp.model or model), vision=vision_used)
        _lat = (_time.time() - t0) * 1000
        _brain_note(role, fw, resp.model or model, ok=True, latency_ms=_lat)
        _u = resp.usage if isinstance(resp.usage, dict) else {}
        # 08.09 (издание): ступень и stop_reason — в след. Спор «max или low» неделю
        # решался на глаз, потому что журнал вызовов не хранил ни того, ни другого.
        _call_trace(role, resp.model or model, ok=True,
                    cached=_u.get("cache_read", 0), prompt=_u.get("in", 0),
                    out_tokens=_u.get("out", 0), latency_ms=_lat,
                    retries=empty_retries, gap_sec=_gap, tools_digest=_tools,
                    effort=_sent_effort(fw, model, thinking, role_effort),
                    stop=str(getattr(resp, "stop_reason", "") or ""),
                    vision=vision_used,
                    key=_key, sys_sha8=_sys_sha, sys_len=_sys_len,
                    cc=(int(_u["cache_creation"]) if "cache_creation" in _u else -1))
        return resp
    except Exception as e:
        if end_after_spoken and isinstance(e, BrokenChannelError):
            # Конец хода, а не смерть канала: сказанное уже доставлено, и любая смерть
            # продолжения закрывает ход сказанным. В след — честная строка с ok=True и
            # нулями: вызов состоялся, продолжения не будет, мы это услышали.
            _call_trace(role, model, ok=True, cached=0, prompt=0, out_tokens=0,
                        latency_ms=(_time.time() - t0) * 1000, error="end_after_spoken",
                        gap_sec=_gap, tools_digest=_tools, vision=vision_used,
                        key=_key, sys_sha8=_sys_sha, sys_len=_sys_len)
            return LLMResponse(text="", blocks=[], stop_reason="end_turn",
                               usage={}, framework=fw, model=model, vision=vision_used)
        err = f"{type(e).__name__}: {str(e)[:120]}"
        st["last_error"] = err
        _synthetic = bool(getattr(e, "synthetic", False))
        if isinstance(e, RelayTerminalError) and not _synthetic:
            # Реле назвало лимит/вход кодом: эндпойнт закрываем до часа восстановления и
            # говорим об этом один раз словами, а не английской диагностикой в чате.
            _held = _hold_endpoint(fw, e)
            _journal("%s: %s (%s) — ходы идут на запасного провайдера, если он на другом "
                     "эндпойнте" % (_ROLE_RU[role], _held["words"], fw))
        if not _synthetic:
            # Счётчик `empty` в brain — это «канал вернул вырожденный ответ», и оборванный
            # стрим входит в него на равных; ЧТО именно случилось, различает имя класса.
            # Синтетический отказ по удержанию — не сбой канала: в реле мы не ходили.
            _brain_note(role, fw, model, ok=False, error=err,
                        empty=isinstance(e, BrokenChannelError))
        # Исход и доля кэша ложатся В ОДНУ строку: только так вопрос «связан ли промах
        # кэша с обрывом» закрывается цифрой, а не сдвигом медианы на восьми случаях.
        # У упавшего вызова usage чаще всего нет — тогда `cached`/`in` останутся нулями,
        # и это честный ноль «не знаем», а не «кэша не было».
        _call_trace(role, model, ok=False, cached=0, prompt=0, out_tokens=0,
                    latency_ms=(_time.time() - t0) * 1000, error=err, gap_sec=_gap,
                    tools_digest=_tools, vision=vision_used,
                    key=_key, sys_sha8=_sys_sha, sys_len=_sys_len)
        if isinstance(e, TornStreamError):
            # Потеря названа вслух. Оборванный стрим — единственный случай, где мы выбрасываем
            # уже сказанное: снаружи это неотличимо от «модель ответила иначе», и без записи
            # разница между «её мысль оборвали» и «она передумала» пропала бы бесследно.
            _lost = len(getattr(getattr(e, "partial", None), "text", "") or "")
            _journal("%s: канал оборвал стрим ошибкой уже после %d знаков — начатый ответ "
                     "потерян; по тому же каналу не переспрашиваю, это была бы не транспортная "
                     "попытка, а повтор поверх сказанного" % (_ROLE_RU[role], _lost))
        if not _fallbackable(e):
            raise
        # 17.08.2026: фреймворк фолбэка стал настраиваемым. По умолчанию — противоположный
        # (прежнее поведение байт-в-байт); `fallback_framework: "openai"` при framework=openai
        # даёт фолбэк ЧЕРЕЗ ТО ЖЕ РЕЛЕ другой моделью (terra → luna): обрывы апстрима
        # спорадические, и повтор другой моделью почти всегда проходит — вторая подписка
        # не нужна. Это не «повтор поверх сказанного»: модель другая, канал тот же.
        # fallback_model belongs to the configured role contract, not to the
        # per-call override.  When an override crosses frameworks, deriving the
        # fallback from `fw` would reinterpret (for example) an Anthropic slug as
        # an OpenAI model.  Keep the configured association; without an override
        # rc.framework == fw, so this is byte-for-byte the old default.
        configured_fw = str(rc.get("framework") or fw)
        other = ((rc.get("fallback_framework") or "").strip()
                 or ("openai" if configured_fw == "anthropic" else "anthropic"))
        fb_model = (rc.get("fallback_model") or "").strip()
        if not fb_model or _client_for(other) is None:
            raise
        if isinstance(e, RelayTerminalError) and _same_endpoint(fw, other):
            # Лимит — свойство эндпойнта (пула аккаунтов реле), не имени модели: вторая
            # модель того же реле упрётся в тот же счётчик. Честнее отдать ошибку наверх.
            log.warning("llm: %s — %s, а запасная нога %s упирается в тот же эндпойнт; "
                        "фолбэка нет", _ROLE_RU[role], err, other)
            raise
        resolved_fb_model = _resolve_fallback_model(fw, model, other, fb_model)
        if not resolved_fb_model:
            raise
        # Route each effective leg independently from the original image tape. A
        # fallback framework may use only its own explicit vision_models entry.
        resolved_fb_model, fallback_messages, fallback_vision_used, fallback_omitted = (
            _route_image_leg(role, other, resolved_fb_model, image_messages)
        )
        # Vision substitution can collapse two same-account legs even when their
        # configured text models differed. Never retry the failed effective channel.
        if other == fw and resolved_fb_model == model:
            log.error("llm: fallback collapsed to failed effective route %s/%s", fw, model)
            raise
        log.warning("llm: %s упал (%s) — фолбэк на %s/%s", _ROLE_RU[role], err,
                    other, resolved_fb_model)
        t1 = _time.time()
        try:
            resp = _call(other, resolved_fb_model, _resolved=True,
                         system=system, messages=fallback_messages, tools=tools,
                         max_tokens=mt, thinking=None, reasoning_effort=role_effort)
        except Exception as e2:
            _brain_note(role, other, resolved_fb_model, ok=False,
                        error=f"{type(e2).__name__}: {str(e2)[:80]}",
                        empty=isinstance(e2, BrokenChannelError))
            _call_trace(role, resolved_fb_model, ok=False, cached=0, prompt=0,
                        out_tokens=0, latency_ms=(_time.time() - t1) * 1000,
                        error=f"{type(e2).__name__}: {str(e2)[:120]}",
                        fallback=True, tools_digest=_tools, vision=fallback_vision_used,
                        key=(cache_address(resolved_fb_model, _sys_text) if other == "openai" else ""),
                        sys_sha8=_sys_sha, sys_len=_sys_len)
            raise
        _note_truncation(resp, role)   # фолбэк-модель обрывается ровно так же
        if not st["on_fallback"]:  # событие — один раз на уход, не на каждый вызов
            _journal(f"{_ROLE_RU[role]} упал ({type(e).__name__}), уход на фолбэк "
                     f"{other}/{resolved_fb_model}")
        st["on_fallback"] = True
        resp.vision = bool(fallback_vision_used)
        if fallback_omitted:
            _mark_no_pixels_response(resp)
        _usage_add(role, resp.usage, fallback=True,
                   model=(resp.model or resolved_fb_model), vision=fallback_vision_used)
        _brain_note(role, other, resp.model or resolved_fb_model, ok=True,
                    latency_ms=(_time.time() - t1) * 1000, fallback=True)
        # Keep the successful fallback visible in per-call spend, under its actual
        # model rather than only the failed primary.  Usage is already normalized.
        # Издание: строка та же, что у основной модели, плюс ступень и stop_reason;
        # сбой записи следа не роняет удачный фолбэк.
        try:
            _fu = resp.usage if isinstance(resp.usage, dict) else {}
            _call_trace(role, resp.model or resolved_fb_model, ok=True,
                        cached=int(_fu.get("cache_read", 0) or 0),
                        prompt=int(_fu.get("in", 0) or 0),
                        out_tokens=int(_fu.get("out", 0) or 0),
                        latency_ms=(_time.time() - t1) * 1000,
                        error="fallback", effort=str(role_effort or ""),
                        stop=str(getattr(resp, "stop_reason", "") or ""),
                        fallback=True, tools_digest=_tools, vision=fallback_vision_used,
                        key=(cache_address(resolved_fb_model, _sys_text) if other == "openai" else ""),
                        sys_sha8=_sys_sha, sys_len=_sys_len,
                        cc=(int(_fu["cache_creation"]) if "cache_creation" in _fu else -1))
        except Exception:
            log.debug("след фолбэка не записался", exc_info=True)
        return resp


# ⚑ ПОВЫЗОВНЫЙ СЛЕД КЭША. Заведён 16.08, и вот зачем именно повызовный.
# Суточная сводка расхода уже считает `cache_read` — по ней видно, что у голоса холодным
# едет треть-половина префикса (67/66/44/59% попаданий за четыре дня), а у оценщика кэша
# практически нет (4-27%). Но суточная цифра не отвечает на главный вопрос: СВЯЗАН ЛИ
# промах кэша с обрывом. Чтобы ответить, доля кэша должна лежать рядом с ИСХОДОМ вызова.
#
# Замер по кольцу ходов дал сдвиг в нужную сторону — медиана паузы перед упавшим ходом
# 789с против 180с у обычного, — но упавших там всего восемь, и одна из восьми заведомо
# чужая (13.08, убитый рефрешем auth.json). Восемь наблюдений это направление, а не
# доказательство. Здесь копится то, чем это закрывается цифрой.
#
# JSONL, а не счётчики в brain: brain пишется read-modify-write без замка (оговорка на
# `_usage_add` рядом), и на частой записи инкременты теряются. Дозапись строки не теряет.
_CALL_TRACE = USAGE_PATH.parent / "llm_calls.jsonl"
# Потолок журнала вызовов. Прежний `_CALL_TRACE_MAX = 20000` был МЁРТВОЙ константой:
# её никто не читал, файл рос без потолка (21 368 строк / 4,8 МБ на 28.08 при диске
# 86%). Ротация — по размеру и атомарным rename в `.1`: дописывающий параллельно
# процесс уезжает вместе со старым файлом (open("a") на каждый вызов — новый файл
# подхватится следующей строкой), ни одна строка не теряется.
_CALL_TRACE_ROTATE_BYTES = 8_000_000  # ~35 тыс. строк по ~230 байт


def _sent_effort(fw: str, model: str, thinking, role_effort) -> str:
    """Какая ступень РЕАЛЬНО уехала в запрос (для следа), в словаре провайдера.

    Пусто — ступень не передавалась, глубину выбрал провайдер (у z.ai это max)."""
    if fw == "anthropic":
        if not _is_glm(model):
            return ""
        step = _openai_reasoning_effort(thinking) if thinking else (
            str(role_effort or "").strip().lower() or "")
        return _GLM_EFFORT.get(step, "low") if step else ""
    return _effective_effort(thinking, role_effort) or ""


def _call_trace(role: str, model: str, *, ok: bool, cached: int, prompt: int,
                out_tokens: int, latency_ms: float, error: str = "",
                retries: int = 0, gap_sec: float = -1.0,
                tools_digest: str = "", effort: str = "", stop: str = "",
                vision: bool = False, fallback: bool = False, key: str = "",
                sys_sha8: str = "", sys_len: int = 0, cc: int = -1) -> None:
    """Одна строка на вызов: доля кэша рядом с исходом. Никогда не роняет вызов.

    `tools` рядом с `cached` — отпечаток набора рук этого вызова. Схемы едут ВЫШЕ
    system и в `prompt_cache_key` не входят (`cache_address` ниже): без отпечатка
    «префикс умер от простоя» и «префикс умер от смены набора рук» пишутся в журнал
    одной и той же строкой, и промах нечем атрибутировать.
    """
    try:
        row = {"ts": round(_time.time(), 3), "role": role, "model": str(model or ""),
               "ok": bool(ok), "cached": int(cached or 0), "in": int(prompt or 0),
               "out": int(out_tokens or 0), "ms": int(latency_ms),
               "retries": int(retries or 0), "iter": int(_CALL_ITER.get() or 0)}
        if tools_digest:
            row["tools"] = str(tools_digest)[:16]
        if effort:
            row["effort"] = str(effort)[:12]
        if stop:
            row["stop"] = str(stop)[:24]
        if fallback:
            row["fallback"] = True
        # 13.09 (K1). Адрес кэша и отпечаток system РЯДОМ с cached: без них промах первого
        # вызова нечем атрибутировать — байты головы (`sys`/`slen`), ключ маршрутизации
        # (`key`) или простой (`gap`). Замер 13.09: 35 из 89 ходов группы падали на 3,8k при
        # одном ключе — провал байтовый, и доказать это по леджеру было нечем. `cc` —
        # cache_creation провайдера (anthropic-путь), -1 = провайдер не сообщил.
        if key:
            row["key"] = str(key)[:96]
        if sys_sha8:
            row["sys"] = str(sys_sha8)[:8]
            row["slen"] = int(sys_len or 0)
        if cc is not None and int(cc) >= 0:
            row["cc"] = int(cc)
        if vision:
            # 09.09: вызов ушёл зрячей замене из-за картинки в кадре — разрез расхода
            # «сколько стоит зрение» и честный ответ на «почему модель не та, что в конфиге».
            row["vision"] = 1
        try:
            import run_context
            _run = run_context.current_run()
            if _run is not None:
                # Без id прогона вызовы одного хода не собрать в цепочку, а вся суть
                # замера — увидеть профиль кэша ВДОЛЬ одного цикла.
                row["run"] = str(getattr(_run, "run_id", "") or "")[:64]
                # 08.09 (издание): род прогона — ось разреза статистики (чат/группа/
                # автономный/Forge), без него журнал вызовов не режется по группам действий.
                if getattr(_run, "kind", ""):
                    row["kind"] = str(_run.kind)[:24]
                # Persist attribution before run manifests rotate away.  Only
                # opaque identifiers belong here, never names or message text.
                chat = (getattr(_run, "origin_chat_id", None)
                        or getattr(_run, "delivery_chat_id", None))
                if chat:
                    row["chat"] = str(chat)[:48]
                for field, attr in (("who", "principal_id"), ("task", "forge_task_id")):
                    value = getattr(_run, attr, "")
                    if value:
                        row[field] = str(value)[:32]
        except Exception:
            pass
        try:
            # Издание: отпечаток кадра этого вызова — тот же frame_id, что в model_input
            # прогона: даёт join «вызов ↔ кадр ↔ тень» без второго прибора.
            import frame_trace
            _trace = frame_trace.current()
            if _trace is not None and getattr(_trace, "frame_id", ""):
                row["frame"] = str(_trace.frame_id)[:16]
        except Exception:
            pass
        if gap_sec >= 0:
            row["gap"] = round(gap_sec, 1)
        if error:
            row["err"] = str(error)[:120]
        _CALL_TRACE.parent.mkdir(parents=True, exist_ok=True)
        try:
            if _CALL_TRACE.stat().st_size >= _CALL_TRACE_ROTATE_BYTES:
                _CALL_TRACE.replace(_CALL_TRACE.with_name(_CALL_TRACE.name + ".1"))
        except OSError:
            pass  # файла ещё нет / гонка ротаций — обе безвредны
        with _CALL_TRACE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        log.debug("след вызова не записался", exc_info=True)


# ⚑ НОМЕР ИТЕРАЦИИ ТУЛ-ЦИКЛА. Гипотеза Егора 16.08 точнее, чем «кэш остывает простоем»:
# редкое событие ВНУТРИ цикла ломает префикс — и дальше всё. Проверить это можно только
# одним способом: видеть долю кэша ПО ИТЕРАЦИЯМ одного хода. В нормальном цикле префикс
# растёт монотонно (система + лента + результаты рук), поэтому каждая следующая итерация
# обязана попадать в кэш СИЛЬНЕЕ предыдущей. Падение с 90% до нуля на k-й итерации — это
# и есть искомое событие, и оно видно только с этим номером рядом.
_CALL_ITER: _cv.ContextVar = _cv.ContextVar("praxis_llm_iter", default=0)


def note_iteration(index: int) -> None:
    """Вызывающий цикл сообщает, какой это поворот. Ноль — вызов вне цикла."""
    try:
        _CALL_ITER.set(int(index))
    except Exception:
        pass


def _call_gap(role: str) -> float:
    """Сколько секунд молчала эта роль до текущего вызова. -1 — первый вызов процесса.

    Пауза здесь несущая: гипотеза, ради которой всё писано, — что префикс остывает
    временем, и обрыв липнет к простою. Меряем ровно то, что проверяем.
    """
    now = _time.time()
    prev = _LAST_CALL_AT.get(role)
    _LAST_CALL_AT[role] = now
    return (now - prev) if prev else -1.0


_LAST_CALL_AT: dict = {}

def _brain_note(role: str, framework: str, model: str, **kw) -> None:
    """PASS 22: наблюдение вызова в per-model статистику (brain.py). Никогда не роняет вызов."""
    try:
        import brain
        brain.note_call(role, framework, model, **kw)
    except Exception:
        log.debug("brain note не записался", exc_info=True)


def ping(role: str) -> tuple[bool, str]:
    """Проверка ОСНОВНОГО канала роли (без фолбэка): минимальный вызов. -> (ok, err).

    GLM-5.3 на Anthropic-compatible API отклоняет запросы с выключенным thinking,
    поэтому только её рукопожатие получает минимальный разрешённый бюджет. Остальные
    модели сохраняют прежний одностокенный ping; общий guard ответа не обходится.
    """
    try:
        rc = _config()["roles"][role]
    except KeyError:
        return (False, f"нет роли {role}")
    try:
        framework = str(rc["framework"])
        model = str(rc["model"])
        thinking = 1024 if framework == "anthropic" and model.strip().lower() == "glm-5.3" else None
        resp = _call(framework, model, system="", messages=[{"role": "user", "content": "ping"}],
                     tools=None, max_tokens=1, thinking=thinking)
        _usage_add(role, resp.usage, model=model)  # 18.5: пинг — тоже расход, не мимо счётчика
        return (True, "")
    except Exception as e:
        return (False, f"{type(e).__name__}: {str(e)[:200]}")


def snapshot() -> dict:
    """Для STATE/панели: роль -> {framework, model, fallback_model, fallback_armed, on_fallback, last_error}."""
    cfg = _config()
    out = {}
    for role in ROLES:
        rc = cfg["roles"][role]
        other = ((rc.get("fallback_framework") or "").strip()
                 or ("openai" if rc["framework"] == "anthropic" else "anthropic"))
        armed = bool((rc.get("fallback_model") or "").strip()
                     and (cfg["frameworks"].get(other) or {}).get("api_key"))
        st = _STATE[role]
        hold = _endpoint_hold(str(rc["framework"]))
        out[role] = {"framework": rc["framework"], "model": rc["model"],
                     "max_tokens": rc.get("max_tokens"),
                     "reasoning_effort": rc.get("reasoning_effort") or "",
                     "fallback_model": rc.get("fallback_model") or "",
                     "fallback_armed": armed,
                     "on_fallback": bool(st["on_fallback"]),
                     "last_error": st["last_error"],
                     # 25.09: эндпойнт основной ноги закрыт лимитом подписки — словами
                     # и с часом, чтобы окно сказало «подписка исчерпана до ЧЧ:ММ».
                     "held_until": (hold or {}).get("until"),
                     "held_words": (hold or {}).get("words") or ""}
    return out


# --------------------------------------------------------------------------- #
#  Тестовые хелперы (сети в тестах нет; фейк эмулирует anthropic-SDK .messages.create)
# --------------------------------------------------------------------------- #

def use_test_client(fake, framework: str = "anthropic") -> None:
    """Подставить фейковый SDK-клиент для тестов (None = «канал не настроен»)."""
    _TEST_CLIENTS[framework] = fake


def clear_test_clients() -> None:
    _TEST_CLIENTS.clear()


def observed_models(role: str) -> tuple[dict, dict]:
    """(последний реально ответивший, счёт по моделям за сегодня) для одной роли.

    Оба берутся из того же журнала расхода, куда `_usage_add` пишет ФАКТИЧЕСКУЮ модель
    каждого успешного вызова. Пусто — значит сегодня по этой роли ответов ещё не было,
    и так и надо сказать: «не наблюдалось» честнее, чем повторить настроенное.
    """
    try:
        today = _usage_load().get(praxis_time.day_key()) or {}
        row = today.get(role) if isinstance(today, dict) else None
        if not isinstance(row, dict):
            return {}, {}
        last = row.get("last") if isinstance(row.get("last"), dict) else {}
        models = row.get("models") if isinstance(row.get("models"), dict) else {}
        counts = {name: int((data or {}).get("calls") or 0)
                  for name, data in models.items() if isinstance(data, dict)}
        return dict(last), {k: v for k, v in counts.items() if v}
    except Exception:
        return {}, {}


def state_line() -> str:
    """Одна строка для кадра: НАСТРОЕННЫЙ канал и то, что РЕАЛЬНО отвечало.

    ⚠ Раньше здесь стояло только настроенное, и этого хватало, чтобы кадр врал ей о ней:
    конфиг говорил `gpt-5.6-sol`, а восемнадцать ходов из ста пятидесяти шли на
    `gpt-5.6-terra`. Её решение 08.08 — показывать обе вещи, причём накопительная
    статистика «не должна подменять факт последнего реально ответившего backend».
    Поэтому порядок именно такой: настроено → последний ответ → счёт за сутки.
    """
    try:
        snap = snapshot()
    except Exception:
        return ""
    parts = []
    for role in ROLES:
        s = snap[role]
        mark = " ⚠ фолбэк" if s["on_fallback"] else ""
        chunk = f"{_ROLE_RU[role]}: настроено={s['model']} ({s['framework']}){mark}"
        last, counts = observed_models(role)
        name = str(last.get("model") or "")
        if name:
            when = str(last.get("at") or "")
            same = " — тот же" if name == s["model"] else " ⚠ ДРУГАЯ"
            chunk += f"; последний ответ={name}{same}" + (f" ({when})" if when else "")
        else:
            chunk += "; последний ответ=сегодня не наблюдался"
        if len(counts) > 1 or (counts and name and name not in counts):
            spread = " / ".join(f"{n} {c}" for n, c in
                                sorted(counts.items(), key=lambda kv: -kv[1]))
            chunk += f"; за сутки: {spread}"
        parts.append(chunk)
    return " · ".join(parts)
