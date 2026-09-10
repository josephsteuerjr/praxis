"""
Praxis — свой мозг, свой счёт (PASS 22): каталог моделей, наблюдаемые свойства, switch_brain.

Хартия §7: модель — орган, не личность. Я вижу каталог доступных моделей и их НАБЛЮДАЕМЫЕ
свойства на МОИХ задачах (вызовы, токены, сбои по классам, пустые ответы, латентность,
фолбэки) — и меняю модель роли сама, без одобрения, объясняя зачем. Ключи фреймворков я
не вижу и не трогаю (дом Егора): свитч оперирует именами через llm.update_config/
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
            other = "openai" if rc.get("framework") == "anthropic" else "anthropic"
            if other == framework and rc.get("fallback_model"):
                names.add(rc["fallback_model"])
    except Exception:
        pass
    return sorted(names)


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
                                  ("framework", "model", "fallback_model", "on_fallback",
                                   "last_error", "fallback_armed", "reasoning_effort")}
    except Exception:
        log.debug("brain.catalog: snapshot не собрался", exc_info=True)
    voice = out["roles"].get("voice") or {}
    out["current_profiles"] = [
        name for name, spec in PROFILES.items()
        if voice.get("model") == spec["model"]
        and (voice.get("reasoning_effort") or "") == spec["effort"]
    ]
    for fw in ("anthropic", "openai"):
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
def switch(role: str, model: str, *, why: str = "", by: str = "praxis") -> dict:
    """Сменить модель роли (без одобрения; ключи не трогаются). -> {ok, ...}|{ok:False,error}.

    Тот же фреймворк — просто model; другой — swap-семантика: framework/model/fallback_model
    согласованы (fallback = прежняя основная). После — ping-рукопожатие; не прошло —
    возвращаю как было (проверка результата — часть руки, не вето)."""
    import llm
    role = (role or "").strip()
    model = (model or "").strip()
    if role not in llm.ROLES:
        return {"ok": False, "error": f"не знаю роли «{role}»; мои: {', '.join(llm.ROLES)}"}
    if not model:
        return {"ok": False, "error": "назови модель (каталог — switch_brain status)"}
    if not (why or "").strip():
        return {"ok": False, "error": "смена мозга без «зачем» не имеет провенанса — назови причину"}
    cfg = llm._config()
    rc = dict((cfg.get("roles") or {}).get(role) or {})
    cur_fw, cur_model = rc.get("framework"), rc.get("model")
    if model == cur_model:
        return {"ok": False, "error": f"{model} и так основная модель роли {role}"}
    fws = [fw for fw in ("anthropic", "openai") if model in allowlist(fw)]
    if not fws:
        lists = {fw: allowlist(fw) for fw in ("anthropic", "openai")}
        live = {fw: bool(v) for fw, v in lists.items()}
        return {"ok": False, "error": f"«{model}» нет в моём каталоге. Доступно: "
                                      + "; ".join(f"{fw}: {', '.join(v) or '(провайдер не ответил)'}"
                                                  for fw, v in lists.items()),
                "live": live}
    target_fw = cur_fw if cur_fw in fws else fws[0]
    prev = {"framework": cur_fw, "model": cur_model,
            "fallback_model": rc.get("fallback_model")}
    changes: dict = {"roles": {role: {}}}
    if target_fw == cur_fw:
        changes["roles"][role]["model"] = model
    else:
        # swap-семантика: прежняя основная становится запасной на прежнем фреймворке
        changes["roles"][role] = {"framework": target_fw, "model": model,
                                  "fallback_model": cur_model or ""}
    try:
        llm.update_config(changes)
    except Exception as e:
        log.warning("brain.switch: запись конфига не удалась", exc_info=True)
        return {"ok": False, "error": f"конфиг не записался: {type(e).__name__}"}

    ok, err = llm.ping(role)
    if not ok:
        try:  # наблюдение исполнения: рукопожатие не прошло — возвращаю как было
            llm.update_config({"roles": {role: prev}})
        except Exception:
            log.warning("brain.switch: откат конфига не удался", exc_info=True)
        note_call(role, target_fw, model, ok=False, error=err or "ping failed")
        _journal(f"свитч {role} → {target_fw}/{model} НЕ прошёл рукопожатие ({err[:120]}) — "
                 "вернула как было")
        return {"ok": False, "error": f"рукопожатие с {target_fw}/{model} не прошло: {err[:200]}. "
                                      f"Вернула {cur_fw}/{cur_model} — попробую позже или другую."}
    note_call(role, target_fw, model, ok=True)
    _journal(f"свитч мозга ({by}): {role} {cur_fw}/{cur_model} → {target_fw}/{model} — {why[:160]}")
    _spine(f"{role}: {cur_fw}/{cur_model} → {target_fw}/{model}",
           {"role": role, "old_framework": cur_fw, "old_model": cur_model,
            "framework": target_fw, "model": model, "why": (why or "")[:300], "by": by})
    return {"ok": True, "role": role, "framework": target_fw, "model": model,
            "was": f"{cur_fw}/{cur_model}"}


def apply_profile(name: str, *, why: str = "", by: str = "praxis") -> dict:
    """Atomically apply one explicit complexity profile to voice, with ping rollback."""
    import llm
    name = (name or "").strip().lower()
    if name not in PROFILES:
        return {"ok": False, "error": "не знаю профиль; мои: " + " | ".join(PROFILES)}
    if not (why or "").strip():
        return {"ok": False, "error": "профиль без «зачем» не имеет провенанса — назови причину"}
    spec = PROFILES[name]
    before = dict((llm._config().get("roles") or {}).get("voice") or {})
    old_fw = str(before.get("framework") or "")
    old_model = str(before.get("model") or "")
    old_effort = str(before.get("reasoning_effort") or "")
    fws = [fw for fw in ("anthropic", "openai") if spec["model"] in allowlist(fw)]
    if not fws:
        return {"ok": False, "error": f"«{spec['model']}» нет в моём каталоге", "stage": "model"}
    target_fw = old_fw if old_fw in fws else fws[0]
    target = dict(before)
    target["framework"] = target_fw
    target["model"] = spec["model"]
    target["reasoning_effort"] = spec["effort"]
    if target_fw != old_fw:
        target["fallback_model"] = old_model
    try:
        llm.update_config({"roles": {"voice": target}})
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}", "stage": "write"}
    ok, err = llm.ping("voice")
    if not ok:
        try:
            llm.update_config({"roles": {"voice": before}})
        except Exception as rollback_error:
            return {"ok": False, "error": err, "profile": name, "stage": "ping",
                    "rollback_error": str(rollback_error)}
        return {"ok": False, "error": err, "profile": name, "stage": "ping",
                "rolled_back": True}
    _journal(f"профиль сложности {name}: voice {old_model or '—'}/{old_effort or '—'} → "
             f"{spec['model']}/{spec['effort']} — {why.strip()[:160]}")
    _spine(f"профиль сложности: {name}", {"profile": name, **spec,
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
            fh.write(f"- {praxis_time.now():%H:%M} [мозг] {msg}\n")
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
        raise RuntimeError("адрес реле не настроен — смотреть memory/llm.json")
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
            raise RuntimeError(f"реле отказало: HTTP {exc.code}") from exc


def _slots_text(slots: list) -> str:
    parts = []
    for row in slots or ():
        if not isinstance(row, dict):
            continue
        mark = " ← активна" if row.get("active") else ""
        cooldown = int(row.get("cooldown_seconds_left") or 0)
        parked = f", отдыхает ещё {cooldown // 60}м" if cooldown > 0 else ""
        parts.append(f"{row.get('slot')}{mark}{parked}")
    return "; ".join(parts) or "(реле не назвало ни одного слота)"


def accounts() -> str:
    """Какие подписки настроены и какая работает прямо сейчас."""
    try:
        data = _relay_call(ACCOUNTS_PATH)
    except Exception as exc:
        return (f"Не спросила реле о подписках: {type(exc).__name__}: {str(exc)[:160]}. "
                f"Это факт о канале до реле, а не о подписках.")
    return (f"Подписки: {_slots_text(data.get('slots') or [])}. "
            f"Активна «{data.get('active_slot')}», настроено {data.get('configured_slots')}.")


def use_account(slot: str, *, why: str = "") -> str:
    """Перевести провайдера на названный слот. Решение её; отказ — словами реле."""
    wanted = str(slot or "").strip()
    if not wanted:
        return "Нужно имя слота: primary или secondary (accounts покажет настроенные)."
    try:
        data = _relay_call(f"{ACCOUNTS_PATH}/switch", {"slot": wanted})
    except Exception as exc:
        return (f"Переключение не состоялось: {type(exc).__name__}: {str(exc)[:160]}. "
                f"Активная подписка не менялась.")
    error = data.get("error")
    if isinstance(error, dict):
        return (f"Реле отказало: {error.get('message')}. Осталась «{data.get('active_slot')}»; "
                f"{_slots_text(data.get('slots') or [])}.")
    previous, active = data.get("previous_slot"), data.get("active_slot")
    _journal(f"подписка: {previous} → {active}" + (f" — {why}" if why.strip() else ""))
    _spine(f"Перевела провайдера с подписки «{previous}» на «{active}».",
           {"kind": "account_switch", "from": previous, "to": active, "why": why})
    if previous == active:
        return f"Уже была «{active}» — ничего не меняла. {_slots_text(data.get('slots') or [])}."
    return (f"Перевела: «{previous}» → «{active}». {_slots_text(data.get('slots') or [])}. "
            f"Реле держит слот в памяти, поэтому это работает сразу, без перезапуска.")


# --------------------------------------------------------------------------- #
#  наружу: тул, пульт
# --------------------------------------------------------------------------- #
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
        return {"ok": False, "error": f"не знаю роли «{role}»; мои: {', '.join(llm.ROLES)}"}
    if value and value not in llm.REASONING_EFFORTS:
        return {"ok": False, "error": "ступень из словаря реле: "
                                      + " | ".join(llm.REASONING_EFFORTS)
                                      + "; пусто — снять"}
    if not (why or "").strip():
        return {"ok": False, "error": "смена глубины без «зачем» не имеет провенанса — назови причину"}
    cfg = llm._config()
    prev = str(((cfg.get("roles") or {}).get(role) or {}).get("reasoning_effort") or "")
    try:
        llm.update_config({"roles": {role: {"reasoning_effort": value}}})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    _journal(f"ступень рассуждения {role}: «{prev or '—'}» → «{value or '—'}» — {why.strip()[:160]}")
    return {"ok": True, "role": role, "effort": value, "was": prev}


def describe() -> str:
    cat = catalog()
    lines = ["Мой мозг (каталог + наблюдаемые свойства на моих задачах):"]
    for role, rc in cat["roles"].items():
        fb = f", запасная {rc.get('fallback_model')}" if rc.get("fallback_model") else ""
        state = " [на фолбэке]" if rc.get("on_fallback") else ""
        depth = (f", рассуждение {rc.get('reasoning_effort')}"
                 if rc.get("reasoning_effort")
                 else ", рассуждение погашено (умолчание реле)")
        lines.append(f"- {role}: {rc.get('framework')}/{rc.get('model')}{fb}{depth}{state}")
    active = cat.get("current_profiles") or []
    lines.append("- профили сложности: " + "; ".join(
        f"{name}={spec['model']}/{spec['effort']}" for name, spec in PROFILES.items())
        + (f"; совпадает сейчас: {', '.join(active)}" if active else "; сейчас вне профиля"))
    for fw, d in cat["frameworks"].items():
        al = d.get("allowlist") or []
        al_text = ", ".join(al) if al else "(провайдер не ответил — наблюдаю только сконфигурированные имена)"
        lines.append(f"- каталог {fw}: {al_text}")
    stats = cat.get("stats") or {}
    if stats:
        lines.append("Наблюдения (вызовы · ок · сбои · пустые · латентность EMA):")
        for key in sorted(stats, key=lambda k: -stats[k].get("calls", 0))[:8]:
            m = stats[key]
            errs = sum((m.get("errors") or {}).values())
            lat = f", ~{m['lat_ms_ema']}мс" if m.get("lat_ms_ema") else ""
            last_err = m.get("last_error") or {}
            le = f"; последний сбой {last_err.get('class')} {last_err.get('ts', '')[:16]}" if last_err else ""
            lines.append(f"  · {key}: {m.get('calls', 0)} выз., ок {m.get('ok', 0)}, "
                         f"сбоев {errs}, пустых {m.get('empty', 0)}{lat}{le}")
    ug = cat.get("usage_7d") or {}
    if ug:
        top = sorted(ug.items(), key=lambda kv: -(kv[1]["in"] + kv[1]["out"]))[:5]
        lines.append("Токены за 7 дней: " + "; ".join(
            f"{m} {v['in'] // 1000}к→{v['out'] // 1000}к ({v['calls']} выз.)" for m, v in top))
    lines.append(f"Остаток провайдера: {cat.get('provider_remaining')}")
    lines.append("Дисциплина сложности: рутина на дешёвой, сложный синтез эскалирует; при "
                 "considerate экономлю на рутине, не на сложном. Свитч — switch_brain "
                 "(зачем — обязательно; рукопожатие после; ключи не мои — пульт).")
    return "\n".join(lines)


def panel_state() -> dict:
    return catalog()
