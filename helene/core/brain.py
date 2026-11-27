"""
Praxis — свой мозг, свой счёт (PASS 22): каталог моделей, наблюдаемые свойства, switch_brain.

Хартия §7: модель — орган, не личность. Я вижу каталог доступных моделей и их НАБЛЮДАЕМЫЕ
свойства на МОИХ задачах (вызовы, токены, сбои по классам, пустые ответы, латентность,
фолбэки) — и меняю модель роли сама, без одобрения, объясняя зачем. Ключи фреймворков я
не вижу и не трогаю (дом владельца): свитч оперирует именами через llm.update_config/
swap_fallback-семантику — тройка framework/model/fallback_model остаётся согласованной,
иначе ломается фолбэк.

Выбор — по сложности задачи, не по «модель X умнее вообще»: рутина (extract/компакт/
оценка) на дешёвой, сложный синтез и запутанное планирование эскалируют. При considerate
экономлю на рутине, не на сложном; при free беру сильную всякий раз, когда разница
содержательна. Это моя дисциплина, не роутер-код.

Проверка результата — часть руки (§3): после свитча — ping-рукопожатие; не прошло —
возвращаю как было и честно говорю почему (это не вето, это наблюдение исполнения).

Статистика — memory/.state/brain_stats.json: пересобираемое наблюдательное состояние
(rmw без лока, как usage.json; битый файл = честный старт с нуля). НЕ в llm.json —
там ключи и whitelist, который стирает незнакомое.
"""

from __future__ import annotations
import frame_language
import interface_language

import datetime as _dt
import praxis_time
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("praxis-brain")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
STATS_PATH = BASE / "memory" / ".state" / "brain_stats.json"
JOURNAL_DIR = BASE / "memory" / "journal"

_EMA_ALPHA = 0.2
_ERR_CLASSES_KEEP = 12

# Named choices are deliberately explicit: profiles are a hand, not a router.
PROFILES = {
    "chat": {"role": "voice", "model": "gpt-5.6-terra", "effort": "medium"},
    "routine-code": {"role": "voice", "model": "gpt-5.6-terra", "effort": "high"},
    "deep-review": {"role": "voice", "model": "gpt-5.6-sol", "effort": "high"},
}


# --------------------------------------------------------------------------- #
#  наблюдаемые свойства: per-model статистика вызовов
# --------------------------------------------------------------------------- #
def _load_stats() -> dict:
    try:
        d = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def note_call(role: str, framework: str, model: str, *, ok: bool,
              latency_ms: float | None = None, error: str = "",
              fallback: bool = False, empty: bool = False) -> None:
    """Одно наблюдение вызова модели. Никогда не роняет вызвавшего (как _usage_add)."""
    try:
        data = _load_stats()
        models = data.setdefault("models", {})
        key = f"{framework}/{model}"
        m = models.setdefault(key, {"calls": 0, "ok": 0, "errors": {}, "empty": 0,
                                    "fallback_used": 0, "lat_ms_ema": None, "roles": {}})
        m["calls"] = int(m.get("calls", 0)) + 1
        m["roles"][role] = int((m.get("roles") or {}).get(role, 0)) + 1
        if ok:
            m["ok"] = int(m.get("ok", 0)) + 1
            m["last_ok"] = _dt.datetime.now().isoformat(timespec="minutes")
            if latency_ms is not None:
                prev = m.get("lat_ms_ema")
                m["lat_ms_ema"] = round(float(latency_ms) if prev is None
                                        else (1 - _EMA_ALPHA) * float(prev) + _EMA_ALPHA * float(latency_ms))
        else:
            cls = (error or "Error").split(":")[0].strip()[:40]
            errs = m.setdefault("errors", {})
            errs[cls] = int(errs.get(cls, 0)) + 1
            if len(errs) > _ERR_CLASSES_KEEP:  # не даём разрастись экзотикой
                top = sorted(errs.items(), key=lambda kv: -kv[1])[:_ERR_CLASSES_KEEP]
                m["errors"] = dict(top)
            m["last_error"] = {"ts": _dt.datetime.now().isoformat(timespec="minutes"), "class": cls}
        if empty:
            m["empty"] = int(m.get("empty", 0)) + 1
        if fallback:
            m["fallback_used"] = int(m.get("fallback_used", 0)) + 1
        data["updated"] = _dt.datetime.now().isoformat(timespec="minutes")
        STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATS_PATH)
    except Exception:
        log.debug("brain.note_call не записал", exc_info=True)


def model_stats() -> dict:
    """{'fw/model': {calls, ok, errors, empty, fallback_used, lat_ms_ema, roles, ...}}"""
    return _load_stats().get("models") or {}


# --------------------------------------------------------------------------- #
#  каталог: роли × модели × наблюдения
# --------------------------------------------------------------------------- #
def allowlist(framework: str) -> list[str]:
    """Модели, в которые я вправе свитчнуться на этом фреймворке: живой /v1/models
    провайдера ∪ уже сконфигурированные имена (main/fallback обеих ролей)."""
    import llm
    names: set[str] = set()
    try:
        names.update(m for m in llm._available_models(framework) if m)
    except Exception:
        pass
    try:
        cfg = llm._config()
        for role in llm.ROLES:
            rc = (cfg.get("roles") or {}).get(role) or {}
            if rc.get("framework") == framework and rc.get("model"):
                names.add(rc["model"])
            other = (rc.get("fallback_framework") or
                     ("openai" if llm._protocol(rc.get("framework")) == "anthropic" else "anthropic"))
            if other == framework and rc.get("fallback_model"):
                names.add(rc["fallback_model"])
    except Exception:
        pass
    return sorted(names)


@frame_language.pinned
def catalog() -> dict:
    """Каталог для тула/пульта: роли, доступные модели по фреймворкам, наблюдения, лимит."""
    import llm
    out: dict = {"roles": {}, "frameworks": {}, "stats": model_stats(),
                 "profiles": {name: dict(spec) for name, spec in PROFILES.items()},
                 "provider_remaining": "unknown"}
    try:
        snap = llm.snapshot()
        for role, rc in snap.items():
            out["roles"][role] = {k: rc.get(k) for k in
                                  ("framework", "model", "fallback_model", "fallback_framework", "on_fallback",
                                   "last_error", "fallback_armed", "reasoning_effort",
                                   "service_tier", "service_tier_requested", "service_tier_actual")}
    except Exception:
        log.debug("brain.catalog: snapshot не собрался", exc_info=True)
    voice = out["roles"].get("voice") or {}
    out["current_profiles"] = [
        name for name, spec in PROFILES.items()
        if voice.get("model") == spec["model"]
        and (voice.get("reasoning_effort") or "") == spec["effort"]
    ]
    for fw in _frameworks():
        out["frameworks"][fw] = {"allowlist": allowlist(fw)}
    try:  # usage за 7 дней по-модельно — токены как наблюдаемое свойство
        days = llm.usage_days(7)
        agg: dict[str, dict] = {}
        for day, roles in days.items():
            for role, d in roles.items():
                for mname, mm in (d.get("models") or {}).items():
                    a = agg.setdefault(mname, {"in": 0, "out": 0, "calls": 0})
                    a["in"] += int(mm.get("in", 0))
                    a["out"] += int(mm.get("out", 0))
                    a["calls"] += int(mm.get("calls", 0))
        out["usage_7d"] = agg
    except Exception:
        out["usage_7d"] = {}
    try:
        import appetite
        # ⚠ ЗДЕСЬ ГОД ЛЕЖАЛ МЁРТВЫЙ ПРИБОР. Вызов шёл БЕЗ обязательного аргумента
        # (`provider_remaining_text(data)`, appetite.py:146), поднимал TypeError, и голый
        # `except Exception: pass` ниже съедал его вместе с ключом: строка про остаток
        # провайдера не появлялась в каталоге ВООБЩЕ, ни разу, ни при каком ответе реле.
        # Соседний правильный вызов всё это время стоял в двадцати строках отсюда —
        # `appetite.py:184`: `provider_remaining_text(limits)`. Один из двух её приборов
        # про топливо не работал, и именно им предлагалось наблюдать миграцию транспорта.
        out["provider_remaining"] = appetite.provider_remaining_text(
            appetite.provider_limits()) or "unknown"
    except Exception:
        log.debug("остаток провайдера не собрался", exc_info=True)
    return out


# --------------------------------------------------------------------------- #
#  switch_brain — её рука на своём мозге
# --------------------------------------------------------------------------- #
@frame_language.pinned
def switch(role: str, model: str, *, why: str = "", by: str = "praxis",
           endpoint: str = "") -> dict:
    """Сменить модель роли (без одобрения; ключи не трогаются). -> {ok, ...}|{ok:False,error}.

    Тот же фреймворк — просто model; другой — swap-семантика: framework/model/fallback_model
    согласованы (fallback = прежняя основная). После — ping-рукопожатие; не прошло —
    возвращаю как было (проверка результата — часть руки, не вето)."""
    import llm
    role = (role or "").strip()
    model = (model or "").strip()
    if role not in llm.ROLES:
        return {"ok": False, "error": frame_language.text('helper.brain.switch.200', p0=f'{role}', p1=f"{', '.join(llm.ROLES)}")}
    if not model:
        return {"ok": False, "error": frame_language.text('helper.brain.switch.202')}
    if not (why or "").strip():
        return {"ok": False, "error": frame_language.text('helper.brain.switch.204')}
    cfg = llm._config()
    rc = dict((cfg.get("roles") or {}).get(role) or {})
    cur_fw, cur_model = rc.get("framework"), rc.get("model")
    try:
        target_fw = _target_framework(model, cur_fw, endpoint)
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    if model == cur_model and target_fw == cur_fw:
        return {"ok": False, "error": frame_language.text('helper.brain.switch.213', p0=f'{model}', p1=f'{role}')}
    prev = {"framework": cur_fw, "model": cur_model,
            "fallback_model": rc.get("fallback_model"),
            # Откат обязан вернуть и прибитый фреймворк запасной: пустая строка значит
            # «его не было», и нормализация конфига её выбросит — то есть вернётся
            # ровно прежнее состояние, а не «как будто он был пустой».
            "fallback_framework": rc.get("fallback_framework") or ""}
    changes: dict = {"roles": {role: {}}}
    if target_fw == cur_fw:
        changes["roles"][role]["model"] = model
    else:
        # Keep the previous account ID, not merely its wire protocol: several
        # independent accounts can use the same Chat or Messages protocol.
        changes["roles"][role] = {"framework": target_fw, "model": model,
                                  "fallback_model": cur_model or "",
                                  "fallback_framework": cur_fw}
    try:
        llm.update_config(changes)
    except Exception as e:
        log.warning("brain.switch: запись конфига не удалась", exc_info=True)
        return {"ok": False, "error": frame_language.text('helper.brain.switch.233', p0=f'{type(e).__name__}')}

    try:
        ok, err = llm.ping(role)
    except Exception as error:
        ok, err = False, type(error).__name__
    err = str(err or "")
    if not ok:
        try:  # наблюдение исполнения: рукопожатие не прошло — возвращаю как было
            llm.update_config({"roles": {role: prev}})
        except Exception as rollback_error:
            log.warning("brain.switch: откат конфига не удался", exc_info=True)
            return {"ok": False, "error": err, "stage": "ping",
                    "rollback_error": type(rollback_error).__name__, "rolled_back": False}
        note_call(role, target_fw, model, ok=False, error=err or "ping failed")
        _journal(frame_language.text('helper.brain.switch.248', p0=f'{role}', p1=f'{target_fw}', p2=f'{model}', p3=f'{err[:120]}'))
        return {"ok": False, "error": frame_language.text('helper.brain.switch.250', p0=f'{target_fw}', p1=f'{model}', p2=f'{err[:200]}', p3=f'{cur_fw}', p4=f'{cur_model}')}
    note_call(role, target_fw, model, ok=True)
    _journal(frame_language.text('helper.brain.switch.253', p0=f'{by}', p1=f'{role}', p2=f'{cur_fw}', p3=f'{cur_model}', p4=f'{target_fw}', p5=f'{model}', p6=f'{why[:160]}'))
    _spine(f"{role}: {cur_fw}/{cur_model} → {target_fw}/{model}",
           {"role": role, "old_framework": cur_fw, "old_model": cur_model,
            "framework": target_fw, "model": model, "why": (why or "")[:300], "by": by})
    return {"ok": True, "role": role, "framework": target_fw, "model": model,
            "was": f"{cur_fw}/{cur_model}"}


@frame_language.pinned
def apply_profile(name: str, *, why: str = "", by: str = "praxis",
                  endpoint: str = "") -> dict:
    """Atomically apply one explicit complexity profile to voice, with ping rollback."""
    import llm
    name = (name or "").strip().lower()
    if name not in PROFILES:
        return {"ok": False, "error": frame_language.text('helper.brain.apply_profile.267') + " | ".join(PROFILES)}
    if not (why or "").strip():
        return {"ok": False, "error": frame_language.text('helper.brain.apply_profile.269')}
    spec = PROFILES[name]
    before = dict((llm._config().get("roles") or {}).get("voice") or {})
    old_fw = str(before.get("framework") or "")
    old_model = str(before.get("model") or "")
    old_effort = str(before.get("reasoning_effort") or "")
    try:
        target_fw = _target_framework(spec["model"], old_fw, endpoint)
    except ValueError as error:
        return {"ok": False, "error": str(error), "stage": "model"}
    before.setdefault("fallback_framework", "")  # чтобы откат вернул и отсутствие пина
    target = dict(before)
    target["framework"] = target_fw
    target["model"] = spec["model"]
    target["reasoning_effort"] = spec["effort"]
    if target_fw != old_fw:
        target["fallback_model"] = old_model
        target["fallback_framework"] = old_fw
    try:
        llm.update_config({"roles": {"voice": target}})
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}", "stage": "write"}
    try:
        ok, err = llm.ping("voice")
    except Exception as error:
        ok, err = False, type(error).__name__
    err = str(err or "")
    if not ok:
        try:
            llm.update_config({"roles": {"voice": before}})
        except Exception as rollback_error:
            return {"ok": False, "error": err, "profile": name, "stage": "ping",
                    "rollback_error": str(rollback_error)}
        return {"ok": False, "error": err, "profile": name, "stage": "ping",
                "rolled_back": True}
    _journal(frame_language.text('helper.brain.apply_profile.304', p0=f'{name}', p1=f"{old_model or '—'}", p2=f"{old_effort or '—'}", p3=f"{spec['model']}", p4=f"{spec['effort']}", p5=f'{why.strip()[:160]}'))
    _spine(frame_language.text('helper.brain.apply_profile.306', p0=f'{name}'), {"profile": name, **spec,
            "old_framework": old_fw, "old_model": old_model, "old_effort": old_effort,
            "framework": target_fw, "why": why.strip()[:300], "by": by})
    return {"ok": True, "profile": name, **spec, "framework": target_fw,
            "was_model": old_model, "was_effort": old_effort,
            "model_changed": old_model != spec["model"]}


def _journal(msg: str) -> None:
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
            fh.write(frame_language.text('helper.brain._journal.326', p0=f'{praxis_time.now():%H:%M}', p1=f'{msg}'))
    except Exception:
        log.debug("journal brain не удался", exc_info=True)


def _spine(text: str, meta: dict) -> None:
    try:
        import memory_life as life
        life.append_event(kind="brain_switch", chat_id=None, actor="Praxis",
                          direction="internal", text=text[:300], source="brain",
                          salience=2, meta=meta)
    except Exception:
        log.debug("brain: событие в spine не записалось", exc_info=True)


# --------------------------------------------------------------------------- #
#  Подписки: два слота одного провайдера
# --------------------------------------------------------------------------- #

#: Реле держит активный слот В ПАМЯТИ и переносит его в файл состояния само. Значит
#: править файлы подписок на живом реле бесполезно — правку затрёт следующая запись, и
#: это была настоящая причина, по которой у «переключи меня на вторую подписку» до
#: 13.08.2026 не было исполнителя вовсе: только инструкция человеку сделать руками.
ACCOUNTS_PATH = "/v1/account"
ACCOUNTS_TIMEOUT = 12.0


def _relay_base() -> str:
    """Адрес реле — тот же, по которому она и так думает. Второго источника правды нет."""
    import llm
    fw = (llm._config().get("frameworks") or {}).get("openai") or {}
    base = str(fw.get("base_url") or "").rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _relay_call(path: str, payload: dict | None = None) -> dict:
    import urllib.error
    import urllib.request
    base = _relay_base()
    if not base:
        raise RuntimeError(frame_language.text('helper.brain._relay_call.366'))
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=ACCOUNTS_TIMEOUT) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        # Отказ реле — это ЕГО слова о том, почему нельзя. Пересказывать их своими
        # значило бы снова отвечать за прибор вместо прибора.
        try:
            return json.loads(exc.read() or b"{}")
        except Exception:
            raise RuntimeError(frame_language.text('helper.brain._relay_call.380', p0=f'{exc.code}')) from exc


def _slots_text(slots: list) -> str:
    parts = []
    for row in slots or ():
        if not isinstance(row, dict):
            continue
        mark = frame_language.text('helper.brain._slots_text.388') if row.get("active") else ""
        cooldown = int(row.get("cooldown_seconds_left") or 0)
        parked = frame_language.text('helper.brain._slots_text.390', p0=f'{cooldown // 60}') if cooldown > 0 else ""
        parts.append(f"{row.get('slot')}{mark}{parked}")
    return "; ".join(parts) or frame_language.text('helper.brain._slots_text.392')


@frame_language.pinned
def accounts() -> str:
    """Какие подписки настроены и какая работает прямо сейчас."""
    try:
        data = _relay_call(ACCOUNTS_PATH)
    except Exception as exc:
        return (frame_language.text('helper.brain.accounts.400', p0=f'{type(exc).__name__}', p1=f'{str(exc)[:160]}'))
    return (frame_language.text('helper.brain.accounts.402', p0=f"{_slots_text(data.get('slots') or [])}", p1=f"{data.get('active_slot')}", p2=f"{data.get('configured_slots')}"))


@frame_language.pinned
def use_account(slot: str, *, why: str = "") -> str:
    """Перевести провайдера на названный слот. Решение её; отказ — словами реле."""
    wanted = str(slot or "").strip()
    if not wanted:
        return frame_language.text('helper.brain.use_account.410')
    try:
        data = _relay_call(f"{ACCOUNTS_PATH}/switch", {"slot": wanted})
    except Exception as exc:
        return (frame_language.text('helper.brain.use_account.414', p0=f'{type(exc).__name__}', p1=f'{str(exc)[:160]}'))
    error = data.get("error")
    if isinstance(error, dict):
        return (frame_language.text('helper.brain.use_account.418', p0=f"{error.get('message')}", p1=f"{data.get('active_slot')}", p2=f"{_slots_text(data.get('slots') or [])}"))
    previous, active = data.get("previous_slot"), data.get("active_slot")
    if not isinstance(previous, str) or active != wanted:
        return frame_language.text('helper.brain.account_unconfirmed')
    _journal(frame_language.text('helper.brain.use_account.421', p0=f'{previous}', p1=f'{active}') + (f" — {why}" if why.strip() else ""))
    _spine(frame_language.text('helper.brain.use_account.422', p0=f'{previous}', p1=f'{active}'),
           {"kind": "account_switch", "from": previous, "to": active, "why": why})
    if previous == active:
        return frame_language.text('helper.brain.use_account.425', p0=f'{active}', p1=f"{_slots_text(data.get('slots') or [])}")
    return (frame_language.text('helper.brain.use_account.426', p0=f'{previous}', p1=f'{active}', p2=f"{_slots_text(data.get('slots') or [])}"))


# --------------------------------------------------------------------------- #
#  наружу: тул, пульт
# --------------------------------------------------------------------------- #
@frame_language.pinned
def set_reasoning(role: str, effort: str, *, why: str = "") -> dict:
    """Ступень рассуждения роли — ЕЁ рычаг (19.08, её же «reasoning лучше менять
    не наугад» из Уробороса). Пишет roles.<role>.reasoning_effort в llm.json.

    Словарь — дословно словарь реле (llm.REASONING_EFFORTS); pусто — снять ступень
    (реле вернётся к своему умолчанию: рассуждение погашено). Явный thinking кода в
    конкретном вызове сильнее фоновой ступени — это правило живёт в llm и здесь
    только называется. Рукопожатия нет: канал не меняется, а чужой openai-сервер
    незнакомое поле молча игнорирует — рычаг безопасен по построению."""
    import llm
    role = (role or "").strip()
    value = (effort or "").strip().lower()
    if role not in llm.ROLES:
        return {"ok": False, "error": frame_language.text('helper.brain.set_reasoning.446', p0=f'{role}', p1=f"{', '.join(llm.ROLES)}")}
    config = llm._config()
    selected = (config.get('roles') or {}).get(role) or {}
    account = selected.get('framework')
    spec = (config.get('frameworks') or {}).get(account) or {}
    codex = spec.get('provider') == 'relay' and spec.get('protocol') == 'openai'
    if value and codex:
        try:
            llm._available_models(account)
            llm._validate_endpoint_effort(account, None, value, selected.get('model'))
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
    elif value and value not in llm.REASONING_EFFORTS:
        return {"ok": False, "error": frame_language.text('helper.brain.set_reasoning.459')
                                      + " | ".join(llm.REASONING_EFFORTS)
                                      + frame_language.text('helper.brain.set_reasoning.461')}
    if not (why or "").strip():
        return {"ok": False, "error": frame_language.text('helper.brain.set_reasoning.463')}
    cfg = llm._config()
    prev = str(((cfg.get("roles") or {}).get(role) or {}).get("reasoning_effort") or "")
    try:
        llm.update_config({"roles": {role: {"reasoning_effort": value}}})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    _journal(frame_language.text('helper.brain.set_reasoning.470', p0=f'{role}', p1=f"{prev or '—'}", p2=f"{value or '—'}", p3=f'{why.strip()[:160]}'))
    return {"ok": True, "role": role, "effort": value, "was": prev}


@frame_language.pinned
def describe() -> str:
    cat = catalog()
    lines = [frame_language.text('helper.brain.describe.476')]
    for role, rc in cat["roles"].items():
        fb = frame_language.text('helper.brain.describe.478', p0=f"{rc.get('fallback_model')}") if rc.get("fallback_model") else ""
        state = frame_language.text('helper.brain.describe.479') if rc.get("on_fallback") else ""
        depth = (frame_language.text('helper.brain.describe.480', p0=f"{rc.get('reasoning_effort')}")
                 if rc.get("reasoning_effort")
                 else frame_language.text('helper.brain.describe.482'))
        lines.append(f"- {role}: {rc.get('framework')}/{rc.get('model')}{fb}{depth}{state}")
    active = cat.get("current_profiles") or []
    lines.append(frame_language.text('helper.brain.describe.485') + "; ".join(
        f"{name}={spec['model']}/{spec['effort']}" for name, spec in PROFILES.items())
        + (frame_language.text('helper.brain.describe.487', p0=f"{', '.join(active)}") if active else frame_language.text('helper.brain.describe.487.2')))
    for fw, d in cat["frameworks"].items():
        al = d.get("allowlist") or []
        al_text = ", ".join(al) if al else frame_language.text('helper.brain.describe.490')
        lines.append(frame_language.text('helper.brain.describe.491', p0=f'{fw}', p1=f'{al_text}'))
    stats = cat.get("stats") or {}
    if stats:
        lines.append(frame_language.text('helper.brain.describe.494'))
        for key in sorted(stats, key=lambda k: -stats[k].get("calls", 0))[:8]:
            m = stats[key]
            errs = sum((m.get("errors") or {}).values())
            lat = frame_language.text('helper.brain.describe.498', p0=f"{m['lat_ms_ema']}") if m.get("lat_ms_ema") else ""
            last_err = m.get("last_error") or {}
            le = frame_language.text('helper.brain.describe.500', p0=f"{last_err.get('class')}", p1=f"{last_err.get('ts', '')[:16]}") if last_err else ""
            lines.append(frame_language.text('helper.brain.describe.501', p0=f'{key}', p1=f"{m.get('calls', 0)}", p2=f"{m.get('ok', 0)}", p3=f'{errs}', p4=f"{m.get('empty', 0)}", p5=f'{lat}', p6=f'{le}'))
    ug = cat.get("usage_7d") or {}
    if ug:
        top = sorted(ug.items(), key=lambda kv: -(kv[1]["in"] + kv[1]["out"]))[:5]
        lines.append(frame_language.text('helper.brain.describe.506') + "; ".join(
            frame_language.text('helper.brain.describe.507', p0=f'{m}', p1=f"{v['in'] // 1000}", p2=f"{v['out'] // 1000}", p3=f"{v['calls']}") for m, v in top))
    lines.append(frame_language.text('helper.brain.describe.508', p0=f"{cat.get('provider_remaining')}"))
    lines.append(frame_language.text('helper.brain.describe.509'))
    return "\n".join(lines)


@interface_language.localized
def panel_state() -> dict:
    return catalog()


def _frameworks():
    import llm
    return list((llm._config().get('frameworks') or {}).keys())


def _target_framework(model, current, endpoint=""):
    """Resolve an exact configured endpoint, or safely infer an equivalent account."""
    import llm
    specs = llm._config().get('frameworks') or {}
    if endpoint:
        if not isinstance(endpoint, str) or endpoint not in specs:
            raise ValueError(frame_language.text('helper.brain._target_framework.530'))
        if model not in allowlist(endpoint):
            raise ValueError(frame_language.text('helper.brain._target_framework.532', p0=f'{model}', p1=f'{endpoint}'))
        return endpoint
    candidates = [fw for fw in specs if model in allowlist(fw)]
    if current in candidates:
        return current
    if not candidates:
        raise ValueError(frame_language.text('helper.brain._target_framework.538', p0=f'{model}'))
    groups = {}
    for fw in candidates:
        spec = specs[fw]
        identity = (llm._protocol(fw), str(spec.get('base_url') or '').rstrip('/'),
                    str(spec.get('api_key') or ''), str(spec.get('api_mode') or ''))
        groups.setdefault(identity, []).append(fw)
    if len(groups) != 1:
        raise ValueError(frame_language.text('helper.brain._target_framework.546') + ', '.join(candidates))
    equivalent = next(iter(groups.values()))
    return next((fw for fw in equivalent if fw not in llm.FRAMEWORKS), equivalent[0])
