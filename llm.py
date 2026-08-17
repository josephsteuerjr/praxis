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
import praxis_time
import json
import logging
import os
import re
import contextvars as _cv
import time as _time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("praxis-llm")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
REPO = Path(__file__).resolve().parent
MEM_DIR = BASE / "memory"
CONFIG_PATH = MEM_DIR / "llm.json"
JOURNAL_DIR = MEM_DIR / "journal"
USAGE_PATH = MEM_DIR / ".state" / "usage.json"   # PASS 9.1: расход токенов по ролям/дням
USAGE_KEEP_DAYS = 60

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


def _config() -> dict:
    """Живой конфиг: перечитывается при смене mtime; нет файла — миграция из env (и запись)."""
    try:
        mtime = CONFIG_PATH.stat().st_mtime_ns
    except OSError:
        mtime = None
    if mtime is None:
        if _CACHE["cfg"] is None or _CACHE["mtime"] is not None:
            _CACHE.update(mtime=None, cfg=_from_env())
            try:
                save_config(_CACHE["cfg"])
                _CACHE["mtime"] = CONFIG_PATH.stat().st_mtime_ns
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
            mtime = CONFIG_PATH.stat().st_mtime_ns
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
        _CACHE.update(mtime=CONFIG_PATH.stat().st_mtime_ns, cfg=fresh)
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
        _CACHE.update(mtime=CONFIG_PATH.stat().st_mtime_ns, cfg=fresh)
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
        if has_image:
            out.append({"role": role, "content": multimodal})
        elif texts:
            out.append({"role": role, "content": "\n".join(texts)})
    return out


def blocks_from_openai(msg) -> list:
    """openai message -> блоки anthropic-формы (text + tool_use, JSON-аргументы распарсены)."""
    blocks: list[dict] = []
    text = getattr(msg, "content", None)
    if text:
        blocks.append({"type": "text", "text": str(text)})
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        raw = getattr(fn, "arguments", "") or ""
        try:
            args = json.loads(raw) if raw.strip() else {}
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}
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


def _usage_add(role: str, usage: dict, fallback: bool = False, model: str = "") -> None:
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
        d = data.setdefault(day, {}).setdefault(role, {"in": 0, "out": 0, "calls": 0, "fallback": 0})
        u_in = int((usage or {}).get("in", 0) or 0)
        u_out = int((usage or {}).get("out", 0) or 0)
        d["in"] = int(d.get("in", 0)) + u_in
        d["out"] = int(d.get("out", 0)) + u_out
        d["calls"] = int(d.get("calls", 0)) + 1
        d["fallback"] = int(d.get("fallback", 0)) + (1 if fallback else 0)
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
            d["last"] = {"model": str(model), "at": praxis_time.stamp()}
            m = d.setdefault("models", {}).setdefault(str(model), {"in": 0, "out": 0, "calls": 0})
            m["in"] += u_in
            m["out"] += u_out
            m["calls"] += 1
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
        # Кэшированный префикс провайдер уже держит; платит она за СВЕЖИЙ вход —
        # `in − cache_read`. На живом прогоне это 42к на ход при 85% кэша ≈ 6.3к свежего.
        # До этой ночи `cache_read` через gpt не приходил вовсе, и строка молчала —
        # молчание читалось как «кэша нет», хотя означало «нам не сказали».
        cr = int(d.get("cache_read", 0))
        cc = int(d.get("cache_creation", 0))
        u_in = int(d.get("in", 0))
        if cr or cc:
            share = f", кэш {100 * cr / u_in:.0f}%" if u_in else ""
            fresh = f", свежего входа {_k(max(0, u_in - cr))}" if u_in else ""
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
# ПОРЯДОК ПРОВЕРОК ЗДЕСЬ СОДЕРЖАТЕЛЕН, А НЕ СЛУЧАЕН.
# Сначала «ни текста, ни блока» — это её граница из 10.08, и она сильнее всего: пусто
# значит пусто, каким бы ни был stop_reason, и повторить такое безопасно. Только то, что
# пустотой НЕ является, может оказаться оборванным стримом.
#
# ЧТО СЮДА СОЗНАТЕЛЬНО НЕ ВНЕСЕНО.
# Ответ, срезанный потолком ДО первого блока (`stop_reason='max_tokens'`, ни текста, ни
# блока), тоже попадает под «пусто» и поднимет EmptyResponseError. Соблазн сделать для него
# исключение был: повтор упрётся в тот же потолок, а `ping()` ходит к модели с
# `max_tokens=1` и на anthropic получил бы красноту вместо «канал жив». Исключение не
# сделано намеренно — молча вернуть пустой ответ значит отдать наверх ровно ту вещь, ради
# которой писан `_note_truncation`: обрыв до первого блока записывался как «промолчала
# сама», байт-в-байт как настоящее решение промолчать. Громкая ложная тревога на пинге
# честнее тихой подмены её молчания; если пинг однажды начнёт краснеть на живом канале —
# чинить надо пинг (просить не 1 токен), а не сторожа.
def _guard_answer(out: LLMResponse) -> LLMResponse:
    """Единственная проверка «это вообще ответ?». Возвращает ответ или поднимает свой класс."""
    if not out.blocks and not out.text.strip():
        raise EmptyResponseError(out.text[:200] or "пустой ответ (ни текста, ни инструмента)")
    # ⚠ Три пути расходились ещё и ЗДЕСЬ, и это нашлось прогоном, а не глазами. Ответ из
    # одних пробелов на стриминговом пути был пустотой (`"".join(parts).strip()` съедал его
    # до нуля блоков), а на anthropic и на не-стриминговом openai проезжал наверх готовым
    # блоком без единого знака. Одно правило на три пути — значит и здесь одно: блок, в
    # котором нечего сказать, ответом не является. Инструмент — является всегда, даже без
    # текста: `stay_silent` это её решение, а не пустота канала.
    if not out.text.strip() and not any(
            (b or {}).get("type") == "tool_use" for b in (out.blocks or ())):
        raise EmptyResponseError("пустой ответ (блок есть, знаков в нём нет)")
    if str(out.stop_reason or "") == "error":
        # Текст/блоки есть, но канал закончил ошибкой — оборванный стрим, НЕ пустота.
        log.warning("llm: стрим %s/%s оборван ошибкой уже после %d знаков и %d блоков — "
                    "это не пустой ответ, повтора по тому же каналу не будет",
                    out.framework or "?", out.model or "?", len(out.text or ""), len(out.blocks or ()))
        raise TornStreamError(out.text[:200] or "стрим оборван ошибкой", partial=out)
    return out


def _call_anthropic(cli, model: str, *, system, messages, tools, max_tokens, thinking) -> LLMResponse:
    kw: dict = {"model": model, "max_tokens": max_tokens,
                "messages": messages_to_anthropic(messages)}
    if system:
        kw["system"] = system
    anthropic_tools = tools_to_anthropic(tools)
    if anthropic_tools:
        kw["tools"] = anthropic_tools
    if thinking:
        kw["thinking"] = {"type": "enabled", "budget_tokens": int(thinking)}
        kw["max_tokens"] = max(int(max_tokens), int(thinking) + 1024)
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
    _usage = {"in": int(getattr(usage, "input_tokens", 0) or 0),
              "out": int(getattr(usage, "output_tokens", 0) or 0)}
    # Anthropic cache metrics — видимость hit-rate и реальной экономии
    _cr = getattr(usage, "cache_read_input_tokens", None)
    _cc = getattr(usage, "cache_creation_input_tokens", None)
    if _cr:
        _usage["cache_read"] = int(_cr)
    if _cc:
        _usage["cache_creation"] = int(_cc)
    # Сторож общий с openai-путём: до 15.08 здесь его не было вовсе, и пустой ответ glm
    # уезжал наверх успешным 'end_turn' — то есть неотличимо от её решения промолчать.
    return _guard_answer(LLMResponse(
        text=text_of(resp), blocks=_blocks_from_anthropic(resp),
        stop_reason=str(getattr(resp, "stop_reason", None) or "end_turn"),
        usage=_usage,
        framework="anthropic", model=model))


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
#: Словарь тегов адреса — ОДИН на оба источника (проза и структурный ключ), чтобы они не
#: разъехались. Расширять его — значит расширять и `_CACHE_MARKS`, и наоборот.
_CACHE_TAGS = tuple(dict.fromkeys(tag for _, tag in _CACHE_MARKS))
_CACHE_ROOM_RE = re.compile(r"room_id=(-?\d+)")
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
    if not mark and not room:
        _address_miss(text)
        return ""
    # ⚠ ЗНАЕМАЯ ДЫРА, И ОНА НЕ СИМПТОМ. Знакомый не-родственник в общей комнате не даёт НИ
    # ОДНОГО маркера аудитории (в кадре не выбрана ни одна из трёх веток) — адрес выходит
    # `-:<room_id>`. Он рабочий и по комнатам различается, поэтому кричать тут нельзя: это
    # был бы постоянный ложный крик в любой групповой переписке, а привыкшего к крику
    # прибора всё равно что нет. Ловит эту дыру тест на живом кадре, а не лог.
    return "praxis:%s:%s:%s" % (model or "?", mark or "-", room.group(1) if room else "-")


def _call_openai(cli, model: str, *, system, messages, tools, max_tokens, thinking) -> LLMResponse:
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
    effort = _openai_reasoning_effort(thinking)
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
    kw["max_tokens"] = max_tokens
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
    return LLMResponse(
        text="\n".join(b["text"] for b in blocks if b["type"] == "text").strip(),
        blocks=blocks, stop_reason=_OPENAI_STOP.get(finish, finish),
        usage={"in": int(getattr(usage, "prompt_tokens", 0) or 0),
               "out": int(getattr(usage, "completion_tokens", 0) or 0),
               **({"cache_read": _openai_cached_tokens(usage)}
                  if _openai_cached_tokens(usage) else {})},
        framework="openai", model=model)


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


def _openai_from_stream(stream, model: str) -> LLMResponse:
    """Собрать ответ из SSE-чанков openai-протокола (delta.content + delta.tool_calls)."""
    parts: list[str] = []
    tools_acc: dict[int, dict] = {}
    finish = "stop"
    u_in = u_out = u_cached = 0
    for chunk in stream:
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
    text = "".join(parts).strip()
    blocks: list[dict] = [{"type": "text", "text": text}] if text else []
    for idx in sorted(tools_acc):
        s = tools_acc[idx]
        if not s["name"]:
            continue
        try:
            args = json.loads(s["args"]) if (s["args"] or "").strip() else {}
        except Exception:
            args = {}
        blocks.append({"type": "tool_use", "id": s["id"] or f"call_{idx}",
                       "name": s["name"], "input": args if isinstance(args, dict) else {}})
    # ⚠ 15.08.2026, найдено враждебной сверкой. `finish_reason='error'` НЕ имеет права
    # спрятаться за `tool_use`. Прежний порядок («есть инструмент → значит tool_use»)
    # затирал признак обрыва, сторож его не видел и отдавал наверх ГОТОВЫЙ ход с рукой,
    # чьи аргументы приехали наполовину: обрезанный json не парсится и молча становится
    # `{}` (ниже). То есть оборванный стрим выглядел как её решение вызвать руку — ровно
    # та подмена, ради которой писан весь этот участок, только на другом пути.
    mapped = _OPENAI_STOP.get(finish, finish)
    stop = (mapped if mapped == "error"
            else "tool_use" if any(b["type"] == "tool_use" for b in blocks) else mapped)
    return LLMResponse(text=text, blocks=blocks, stop_reason=stop,
                       usage={"in": u_in, "out": u_out,
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
    if pick and pick != model:
        log.warning("llm: модель %s пропала у %s — ротация имени на %s", model, framework, pick)
        try:
            _journal(f"модель {model} пропала у {framework}, взяла {pick} (ротация имён провайдера)")
        except Exception:
            pass
        return pick
    return model


def _call(framework: str, model: str, **kw) -> LLMResponse:
    cli = _client_for(framework)
    if cli is None:
        raise RuntimeError(f"llm: фреймворк {framework} не настроен (нет ключа)")
    model = _resolve_model(framework, model)  # ротация наименований провайдера
    if framework == "anthropic":
        return _call_anthropic(cli, model, **kw)
    return _call_openai(cli, model, **kw)


def _fallbackable(e: Exception) -> bool:
    if isinstance(e, BrokenChannelError):
        # Вырожденный ответ канала — повод уйти на другой фреймворк. Оба вида: и пустота,
        # и оборванный стрим. Различаются они не здесь, а в повторе по СВОЕМУ каналу.
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


def _call_retrying_empty(fw: str, model: str, retries: int | None = None, **kw):
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
            return _call(fw, model, **kw), attempt
        except EmptyResponseError as exc:
            last = exc
            if attempt >= retries:
                break
            _time.sleep(EMPTY_RETRY_PAUSE_SEC * (attempt + 1))
            log.warning("llm: пустой ответ %s/%s — повтор %d из %d по тому же каналу",
                        fw, model, attempt + 1, EMPTY_RETRIES)
    raise last


def chat(role: str, *, system=None, messages: list, tools: list | None = None,
         max_tokens: int | None = None, thinking: int | None = None,
         end_after_spoken: bool = False) -> LLMResponse:
    """Вызов модели по роли. Фолбэк на противоположный фреймворк — один повтор, честно в дневник.

    `end_after_spoken=True` — контракт v3 (17.08): в этом ходе реплика УЖЕ доставлена
    рукой, поэтому фолбэк обезоружен — вторая модель не смеет переисполнить принятое
    решение (луна слала ту же реплику заново — четыре копии за две минуты). Пустота
    получает ОДИН ретрай своим каналом (её решение №3) и затем читается как конец хода;
    оборванный стрим тоже закрывает ход сказанным, а не будит фолбэк."""
    if role not in ROLES:
        raise ValueError(f"llm: неизвестная роль {role!r}")
    cfg = _config()
    rc = cfg["roles"][role]
    fw, model = rc["framework"], rc["model"]
    mt = int(max_tokens or rc.get("max_tokens") or DEFAULT_MAX_TOKENS[role])
    st = _STATE[role]
    t0 = _time.time()
    # Пауза до этого вызова — рядом с исходом. Префикс остывает ВРЕМЕНЕМ, и
    # проверяемая гипотеза именно такая: обрыв липнет к простою.
    _gap = _call_gap(role)
    try:
        resp, empty_retries = _call_retrying_empty(
            fw, model, retries=(1 if end_after_spoken else None),
            system=system, messages=messages, tools=tools,
            max_tokens=mt, thinking=thinking)
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
        _usage_add(role, resp.usage, model=(resp.model or model))
        _lat = (_time.time() - t0) * 1000
        _brain_note(role, fw, resp.model or model, ok=True, latency_ms=_lat)
        _u = resp.usage if isinstance(resp.usage, dict) else {}
        _call_trace(role, resp.model or model, ok=True,
                    cached=_u.get("cache_read", 0), prompt=_u.get("in", 0),
                    out_tokens=_u.get("out", 0), latency_ms=_lat,
                    retries=empty_retries, gap_sec=_gap)
        return resp
    except Exception as e:
        if end_after_spoken and isinstance(e, BrokenChannelError):
            # Конец хода, а не смерть канала: сказанное уже доставлено, и любая смерть
            # продолжения закрывает ход сказанным. В след — честная строка с ok=True и
            # нулями: вызов состоялся, продолжения не будет, мы это услышали.
            _call_trace(role, model, ok=True, cached=0, prompt=0, out_tokens=0,
                        latency_ms=(_time.time() - t0) * 1000, error="end_after_spoken",
                        gap_sec=_gap)
            return LLMResponse(text="", blocks=[], stop_reason="end_turn",
                               usage={}, framework=fw, model=model)
        err = f"{type(e).__name__}: {str(e)[:120]}"
        st["last_error"] = err
        # Счётчик `empty` в brain — это «канал вернул вырожденный ответ», и оборванный стрим
        # входит в него на равных; ЧТО именно случилось, различает записанное имя класса.
        _brain_note(role, fw, model, ok=False, error=err,
                    empty=isinstance(e, BrokenChannelError))
        # Исход и доля кэша ложатся В ОДНУ строку: только так вопрос «связан ли промах
        # кэша с обрывом» закрывается цифрой, а не сдвигом медианы на восьми случаях.
        # У упавшего вызова usage чаще всего нет — тогда `cached`/`in` останутся нулями,
        # и это честный ноль «не знаем», а не «кэша не было».
        _call_trace(role, model, ok=False, cached=0, prompt=0, out_tokens=0,
                    latency_ms=(_time.time() - t0) * 1000, error=err, gap_sec=_gap)
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
        other = ((rc.get("fallback_framework") or "").strip()
                 or ("openai" if fw == "anthropic" else "anthropic"))
        fb_model = (rc.get("fallback_model") or "").strip()
        if not fb_model or _client_for(other) is None:
            raise
        log.warning("llm: %s упал (%s) — фолбэк на %s/%s", _ROLE_RU[role], err, other, fb_model)
        t1 = _time.time()
        try:
            resp = _call(other, fb_model, system=system, messages=messages, tools=tools,
                         max_tokens=mt, thinking=None)
        except Exception as e2:
            _brain_note(role, other, fb_model, ok=False,
                        error=f"{type(e2).__name__}: {str(e2)[:80]}",
                        empty=isinstance(e2, BrokenChannelError))
            raise
        _note_truncation(resp, role)   # фолбэк-модель обрывается ровно так же
        if not st["on_fallback"]:  # событие — один раз на уход, не на каждый вызов
            _journal(f"{_ROLE_RU[role]} упал ({type(e).__name__}), ушла на фолбэк {other}/{fb_model}")
        st["on_fallback"] = True
        _usage_add(role, resp.usage, fallback=True, model=(resp.model or fb_model))
        _brain_note(role, other, resp.model or fb_model, ok=True,
                    latency_ms=(_time.time() - t1) * 1000, fallback=True)
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
_CALL_TRACE_MAX = 20000


def _call_trace(role: str, model: str, *, ok: bool, cached: int, prompt: int,
                out_tokens: int, latency_ms: float, error: str = "",
                retries: int = 0, gap_sec: float = -1.0) -> None:
    """Одна строка на вызов: доля кэша рядом с исходом. Никогда не роняет вызов."""
    try:
        row = {"ts": round(_time.time(), 3), "role": role, "model": str(model or ""),
               "ok": bool(ok), "cached": int(cached or 0), "in": int(prompt or 0),
               "out": int(out_tokens or 0), "ms": int(latency_ms),
               "retries": int(retries or 0), "iter": int(_CALL_ITER.get() or 0)}
        try:
            import run_context
            _run = run_context.current_run()
            if _run is not None:
                # Без id прогона вызовы одного хода не собрать в цепочку, а вся суть
                # замера — увидеть профиль кэша ВДОЛЬ одного цикла.
                row["run"] = str(getattr(_run, "run_id", "") or "")[:64]
        except Exception:
            pass
        if gap_sec >= 0:
            row["gap"] = round(gap_sec, 1)
        if error:
            row["err"] = str(error)[:120]
        _CALL_TRACE.parent.mkdir(parents=True, exist_ok=True)
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
    """Проверка ОСНОВНОГО канала роли (без фолбэка): 1-токенный вызов. -> (ok, err)."""
    try:
        rc = _config()["roles"][role]
    except KeyError:
        return (False, f"нет роли {role}")
    try:
        resp = _call(rc["framework"], rc["model"], system="", messages=[{"role": "user", "content": "ping"}],
                     tools=None, max_tokens=1, thinking=None)
        _usage_add(role, resp.usage, model=rc["model"])  # 18.5: пинг — тоже расход, не мимо счётчика
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
        out[role] = {"framework": rc["framework"], "model": rc["model"],
                     "max_tokens": rc.get("max_tokens"),
                     "fallback_model": rc.get("fallback_model") or "",
                     "fallback_armed": armed,
                     "on_fallback": bool(st["on_fallback"]),
                     "last_error": st["last_error"]}
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
