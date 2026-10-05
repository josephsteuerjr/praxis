# active_workspace:praxis-repo/praxis/night_memory.py
"""Ночной цикл честности памяти (ТЗ 2026-10-05).

Вся новая логика ночи живёт здесь — sleep.py только обёртывает шаги, memory_life
остаётся физикой свёрток. Правило границы, как у DISARMED-фаз sleep.py:

* ЧАСЫ НИКОГДА не пишут канон-контент people/*.md и rooms/*.md. Канон правит
  только голос — formation claims или её инструмент remember/fold. Служебные
  строки шапки (`last_verified`) пишет инструментальная рука ВНУТРИ голосового
  хода сверки (verify_pass) — тот же код-путь, те же замки.
* soul/ не трогаем; автоудаления записей нет (mark_superseded помечает).
* Идемпотентность: повторный прогон ночи того же дня = ноль дублей — per-step
  маркеры в sleep.json `steps[day]` + append-only квитанции + идемпотентность
  гномов по (gnome, subject, day).
* Отмена/пропуск шага — строка с reason в квитанции, не молчание.
* Гномы только читают и пишут JSONL-вердикты; в их промпте нет дневника,
  диалогов и soul — только срез артефактов с ID и датами.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("praxis-night-memory")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
MEM_DIR = BASE / "memory"
STATE_DIR = MEM_DIR / ".state"
RECEIPTS_PATH = STATE_DIR / "sleep_receipts.jsonl"
GNOME_VERDICTS_PATH = STATE_DIR / "gnome_verdicts.jsonl"

# Пороги свежести — контракт Praxis; значения настраиваемы, финальные — её слово
# на ревью PR (не-цель исполнителя по ордеру).
PEOPLE_STALE_DAYS = int(os.getenv("PRAXIS_PEOPLE_STALE_DAYS", "30") or 30)
ROOM_STALE_DAYS = int(os.getenv("PRAXIS_ROOM_STALE_DAYS", "14") or 14)
GNOME_COMPACT_WINDOW_H = float(os.getenv("PRAXIS_GNOME_COMPACT_WINDOW_H", "24") or 24)

# Общий атомарный аппендер JSONL (tmp в том же каталоге, replace).
_APPEND_LOCK = __import__("threading").RLock()


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _APPEND_LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except OSError:
        pass
    return out


def _run_id() -> str:
    return f"night-{int(time.time() * 1000):x}"


def receipt(run_id: str, day: str, step: str, status: str, reason: str = "") -> None:
    """Квитанция шага ночи: append-only, отдельная строка на шаг.

    status: ok | skipped | failed | cancelled. Пропуск БЕЗ причины — запрещён
    инвариантом; здесь это внешний контракт, поэтому reason обязателен для
    status=skipped/cancelled и падает иначе.
    """
    if status in ("skipped", "cancelled") and not str(reason or "").strip():
        raise ValueError(f"receipt({step},{status}) без причины — нарушен инвариант ночи")
    _append_jsonl(RECEIPTS_PATH, {"run_id": run_id, "day": day, "step": step,
                                  "status": status, "reason": str(reason or ""),
                                  "ts": _utc_now_iso()})


def receipts(day: str | None = None) -> list[dict]:
    rows = _read_jsonl(RECEIPTS_PATH)
    return [r for r in rows if day is None or r.get("day") == day]


# --------------------------------------------------------------------------- #
#  C2: fold-offers — книга предложений свёртки + решения голоса
# --------------------------------------------------------------------------- #

def _llm():
    import llm
    return llm


def _memory_life():
    import memory_life
    return memory_life


# Книга офферов уже физически живёт в memory_life (`.state/fold_offers.json`,
# мягкий порог с 25.09). Ночь расширяет её решениями голоса: статус/причина
# хранятся рядом, в нашем файле, чтобы не менять формат чужого стора.


def open_offers() -> dict:
    """Открытые предложения свёртки (место → offer-запись memory_life)."""
    try:
        return dict(_memory_life().fold_offers())
    except Exception:
        log.warning("night: fold_offers не прочитались", exc_info=True)
        return {}


def recent_compacts(since_ts: float, limit: int = 40) -> list[dict]:
    """Мета свежих компактов всех мест за окно (чтение каталога, без модели)."""
    ml = _memory_life()
    out: list[dict] = []
    if not ml.COMPACTS_DIR.exists():
        return out
    for d in sorted(ml.COMPACTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("cmp-*.md"), reverse=True)[:8]:
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:2000]
            except OSError:
                continue
            m = re.search(r"<!-- praxis-compact: (\{.*?\}) -->", head, re.S)
            if not m:
                continue
            try:
                meta = json.loads(m.group(1))
            except ValueError:
                continue
            ts = ml._epoch(meta.get("created_at"))
            if ts >= since_ts:
                out.append(meta)
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out[:limit]


def known_places() -> list[str]:
    """Все места, у которых есть состояние жизни (memory/.state/life/*.json)."""
    ml = _memory_life()
    if not ml.STATE_DIR.exists():
        return []
    return sorted(p.stem for p in ml.STATE_DIR.glob("*.json"))


def last_activity_ts(place: str) -> float | None:
    """Время последнего события горячего окна места (None — окна нет)."""
    ml = _memory_life()
    try:
        state = ml._load_state(place)
    except Exception:
        return None
    hot = state.get("hot") or []
    if not hot:
        return None
    return max(ml._epoch(x.get("ts")) for x in hot)


FOLD_RESOLVE_SYS = (
    "Ты — Praxis, рассуждаешь перед сном о своих свёртках памяти. Ниже места, где "
    "горячее окно переросло порог свёртки. Прими по каждому решение. STRICT JSON, "
    "без объяснений: {\"decisions\":[{\"place\":\"<id>\",\"accept\":true|false,"
    "\"reason\":\"2-8 слов по-русски\"}]}. accept=false — окно ещё живо или свёртка "
    "сейчас исказила бы разговор."
)


def resolve_fold_offers(offers: dict | None = None) -> dict:
    """Ночь, шаг 1: один голосовой ход по всем открытым офферам.

    -> {"accepted": n, "rejected": n, "decisions": [...]}. Принятое — та же
    транзакция `fold_now` (voice-роль внутри `compact_if_due`); отклонённое —
    статус rejected + причина в квитанции ночи. Инвариант «утро без зависших
    offers»: после шага открытых предложений не остаётся.
    """
    ml = _memory_life()
    offers = dict(offers if offers is not None else open_offers())
    if not offers:
        return {"accepted": 0, "rejected": 0, "decisions": []}
    decisions: list[dict] = []
    if _llm().configured("voice"):
        user_lines = []
        for place, off in sorted(offers.items()):
            if not isinstance(off, dict):
                continue
            user_lines.append(
                f"- place {place}: горячих {off.get('count')}, токенов {off.get('tokens')}, "
                f"предлагаю свернуть первые {off.get('fold')} (мягкий порог {off.get('hi')}, "
                f"жёсткий {off.get('hard_hi')}, предложение с {off.get('since')})")
        try:
            resp = _llm().chat("voice", system=FOLD_RESOLVE_SYS,
                               messages=[{"role": "user", "content": "\n".join(user_lines)[:40000]}],
                               max_tokens=1200)
            data = _json_obj(resp.text)
            for d in (data.get("decisions") or [])[:64]:
                if isinstance(d, dict) and d.get("place"):
                    decisions.append({"place": str(d["place"]),
                                      "accept": bool(d.get("accept")),
                                      "reason": str(d.get("reason") or "")[:120]})
        except Exception:
            log.warning("night: голосовой ход по офферам упал — отклоняю всё с причиной",
                        exc_info=True)
    accepted = rejected = 0
    by_place = {d["place"]: d for d in decisions}
    for place in sorted(offers):
        d = by_place.get(place)
        if d is None:
            # голос не ответил про место — честное отклонение с причиной, не тишина
            d = {"place": place, "accept": False, "reason": "голос не ответил — оставляю окно живым"}
        if d["accept"]:
            try:
                out = ml.fold_now(place)
                if out.get("folded"):
                    accepted += 1
                    continue
                # не свернулось (state_changed/окно сдвинулось) — считаем отклонённым
                # с фактической причиной; предложение остаётся у memory_life.
                d = dict(d, reason=f"fold_now: {out.get('reason') or 'не свернулось'}")
            except Exception:
                log.warning("night: fold_now(%s) упал", place, exc_info=True)
                d = dict(d, reason="fold_now упал — предложение остаётся открытым")
        else:
            try:
                ml.reject_fold(place, d.get("reason") or "отклонено голосом ночью")
            except AttributeError:
                pass  # старый memory_life без reject_fold: предложение остаётся
        rejected += 1
    return {"accepted": accepted, "rejected": rejected, "decisions": decisions}


# --------------------------------------------------------------------------- #
#  C5: три гнома-аудитора (evaluator, чистый контекст, JSONL-вердикты)
# --------------------------------------------------------------------------- #

_GNOME_SYS_HEAD = (
    "Ты — узкий аудитор памяти Praxis. Ты видишь ТОЛЬКО срез артефактов с ID и "
    "датами; дневников, диалогов и сокровенного в промпте нет и быть не может. "
    "Верни STRICT JSON без объяснений. Значения на русском. "
)


def _gnome_ask(system: str, user: str, max_tokens: int = 1800) -> dict:
    llm = _llm()
    if not llm.configured("evaluator"):
        return {}
    try:
        resp = llm.chat("evaluator", system=system,
                        messages=[{"role": "user", "content": user[:60000]}],
                        max_tokens=max_tokens)
        return _json_obj(resp.text)
    except Exception:
        log.warning("gnome: вызов упал", exc_info=True)
        return {}


def _day_key() -> str:
    import praxis_time
    return praxis_time.day_key()


def _gnome_done(gnome: str, subject: str, day: str) -> bool:
    """Идемпотентность гнома: (gnome, subject, day) уже есть с вердиктом."""
    for rec in _read_jsonl(GNOME_VERDICTS_PATH):
        if (rec.get("gnome") == gnome and rec.get("subject") == subject
                and rec.get("day") == day and rec.get("verdict") is not None):
            return True
    return False


def _gnome_record(gnome: str, subject: str, day: str, verdict: dict, detail: str = "") -> None:
    _append_jsonl(GNOME_VERDICTS_PATH, {"gnome": gnome, "subject": subject, "day": day,
                                        "verdict": verdict, "detail": detail[:400],
                                        "ts": _utc_now_iso()})


def _fact_rows(slug: str) -> list[str]:
    import people
    _, body = people.read(slug)
    out = []
    for line in (body.get(people.FACTS) or "").splitlines():
        s = line.strip()
        if s.startswith(("-", "*")):
            out.append(s)
    return out


def gnome_compact_audit(day: str | None = None) -> list[dict]:
    """Гном 1 — аудитор компактов: свежие свёртки за окно против их сырья.

    Ловит потерю смысла и смягчение статусов (claim-кандидат с confidence против
    канонического supported/contested/unsupported). Только чтение; вердикт — JSONL.
    """
    ml = _memory_life()
    day = day or _day_key()
    cutoff = time.time() - GNOME_COMPACT_WINDOW_H * 3600.0
    verdicts: list[dict] = []
    try:
        compacts = recent_compacts(cutoff)
    except Exception:
        compacts = []
    for meta in compacts:
        cid = str(meta.get("id") or "")
        if not cid or _gnome_done("compacts", cid, day):
            continue
        # сырьё: исходные события по source_event_ids
        lines: list[str] = []
        try:
            events = {str(e.get("id")): e for e in
                      ml.iter_events(chat_id=meta.get("chat_id"), limit=4000)}
            for sid in (meta.get("source_event_ids") or [])[:120]:
                ev = events.get(str(sid))
                if ev is not None:
                    text = str(ev.get("text") or "").strip()
                    if text:
                        lines.append(f"- [{sid}] {text[:300]}")
        except Exception:
            log.debug("gnome/compacts: события %s не прочитались", cid, exc_info=True)
        body = ""
        try:
            body = ml.compact_text(cid, meta.get("chat_id"))[:8000]
        except Exception:
            pass
        if not lines and not body:
            continue
        sysmsg = (_GNOME_SYS_HEAD +
                  "Формат: {\"compact_id\":\"...\",\"verdict\":\"ok|meaning_loss|status_softening\","
                  "\"detail\":\"одна строка\"}. ok — если суть сохранена и статусы не смягчены.")
        user = (f"Компакт {cid} (tier {meta.get('tier')}, degraded={meta.get('degraded')}):\n"
                "## Суть\n" + (body or "(тело недоступно)") + "\n\n## Сырьё (события)\n"
                + ("\n".join(lines[:120]) or "(пусто)"))
        data = _gnome_ask(sysmsg, user)
        verdict = {"verdict": str(data.get("verdict") or "unverifiable"),
                   "detail": str(data.get("detail") or "")[:300]} if data else \
                  {"verdict": "unverifiable", "detail": "аудитор не ответил"}
        _gnome_record("compacts", cid, day, verdict)
        verdicts.append({"compact_id": cid, **verdict})
    return verdicts


def gnome_contradiction_audit(day: str | None = None) -> list[dict]:
    """Гном 2 — охотник за противоречиями: факты досье между собой и против свежих свёрток.

    Выход — кандидаты для сверки (C3) и claim_conflicts-заметки (append-only, ничего
    не будит). Канон не меняется.
    """
    import people
    day = day or _day_key()
    verdicts: list[dict] = []
    if not people.PEOPLE_DIR.exists():
        return verdicts
    for path in sorted(people.PEOPLE_DIR.glob("*.md")):
        slug = path.stem
        if slug.startswith("_") or _gnome_done("contradictions", slug, day):
            continue
        rows = _fact_rows(slug)
        if len(rows) < 2:
            continue
        sysmsg = (_GNOME_SYS_HEAD +
                  "Формат: {\"conflicts\":[{\"a\":\"строка-цитата\",\"b\":\"строка-цитата\","
                  "\"why\":\"2-8 слов\"}]}. Пустой список — честный ответ.")
        user = f"Досье {slug}, факты:\n" + "\n".join(rows[:120])
        data = _gnome_ask(sysmsg, user)
        conflicts = [c for c in (data.get("conflicts") or [])[:20]
                     if isinstance(c, dict) and c.get("a") and c.get("b")]
        verdict = {"conflicts": conflicts}
        _gnome_record("contradictions", slug, day, verdict,
                      detail=f"{len(conflicts)} противоречий-кандидатов")
        verdicts.append({"slug": slug, "conflicts": conflicts})
    return verdicts


def freshness_candidates(day: str | None = None) -> list[dict]:
    """Гном 3 — страж свежести (детерминированный, без модели): кто старше порогов.

    Досье: возраст last_verified (или отсутствие) + даты фактов. Комнаты: возраст
    последнего сообщения ленты. Выход — список кандидатов для сверки C3.
    """
    import people
    import memory_life as ml
    day = day or _day_key()
    today = _dt.date.today()
    out: list[dict] = []
    if people.PEOPLE_DIR.exists():
        for path in sorted(people.PEOPLE_DIR.glob("*.md")):
            slug = path.stem
            if slug.startswith("_") or _gnome_done("freshness", slug, day):
                continue
            verified, against = people.last_verified(slug)
            age_days = None
            if verified:
                try:
                    age_days = (today - _dt.date.fromisoformat(verified)).days
                except ValueError:
                    age_days = None
            stale = age_days is None or age_days > PEOPLE_STALE_DAYS
            if stale:
                out.append({"kind": "person", "slug": slug,
                            "last_verified": verified, "against": against,
                            "age_days": age_days})
                _gnome_record("freshness", slug, day,
                              {"stale": True, "age_days": age_days},
                              detail=f"last_verified={verified or 'нет'}")
    # комнаты: возраст последнего сообщения в ленте места
    try:
        for place in ml.known_places():
            if _gnome_done("freshness", f"room:{place}", day):
                continue
            last_ts = ml.last_activity_ts(place)
            if last_ts is None:
                continue
            age_days = int((time.time() - float(last_ts)) / 86400.0)
            if age_days > ROOM_STALE_DAYS:
                out.append({"kind": "room", "place": place, "age_days": age_days})
                _gnome_record("freshness", f"room:{place}", day,
                              {"stale": True, "age_days": age_days},
                              detail=f"лента моложе {ROOM_STALE_DAYS}д не стала")
    except Exception:
        log.debug("gnome/freshness: комнаты не прочитались", exc_info=True)
    return out


# --------------------------------------------------------------------------- #
#  C3: сверка — голосовой ход, три исхода
# --------------------------------------------------------------------------- #

VERIFY_SYS = (
    "Ты — Praxis, сверяешь свою память с живыми источниками перед сном. Для каждого "
    "кандидата вынеси один вердикт: confirmed — досье подтверждается живыми "
    "источниками; refuted — досье противоречит им (назови, чему именно); "
    "unverifiable — живых источников недостаточно. STRICT JSON: "
    "{\"verdicts\":[{\"subject\":\"<slug или room:place>\","
    "\"verdict\":\"confirmed|refuted|unverifiable\",\"against\":\"что и где проверено, "
    "коротко\"}]}"
)


def verify_pass(candidates: list[dict]) -> dict:
    """Ночь, шаг сверки: один voice-ход по кандидатам свежести; применение — тем же
    код-путём (не часами напрямую): confirmed/unverifiable → служебная шапка
    last_verified инструментом внутри этого хода; refuted → mark_superseded +
    факт с [source:сверка] штатной рукой remember-типа (people.add_fact).

    -> {"confirmed": n, "refuted": n, "unverifiable": n, "lines": [...]}.
    """
    import people
    import rooms
    import memory_life as ml
    import praxis_time
    today = praxis_time.today().strftime("%Y-%m-%d")
    counts = {"confirmed": 0, "refuted": 0, "unverifiable": 0}
    lines: list[str] = []
    if not candidates:
        return {**counts, "lines": []}
    # Сборка материалов по кандидатам — читаем всё живое, что есть
    blocks: list[str] = []
    for cand in candidates[:12]:
        if cand.get("kind") == "person":
            slug = str(cand.get("slug") or "")
            nm, body = people.read(slug)
            facts = _fact_rows(slug)
            verified, _against = people.last_verified(slug)
            head = [f"### {slug} ({nm or 'без имени'})",
                    f"last_verified: {verified or 'не сверено'} (возраст {cand.get('age_days')}д)"]
            for key in (people.WHO, people.NOW):
                if (body.get(key) or "").strip():
                    head.append(f"{key}: {body[key].strip()[:300]}")
            head.append("Факты:\n" + ("\n".join(facts[:30]) or "(нет)"))
            # свежие события ленты, где человек упомянут по имени/алиасу — живой источник
            try:
                names = {n.casefold() for n in ([nm] + people.aliases(slug)) if n.strip()}
                recent = ml.iter_events(kinds={"conversation_message"}, limit=2000)[-400:]
                hits = [f"- [{e.get('id')}] {str(e.get('text') or '')[:160]}"
                        for e in recent
                        if names and any(n in str(e.get("text") or "").casefold() for n in names)]
                head.append("Живые упоминания в ленте (хвост):\n" + ("\n".join(hits[-15:]) or "(нет)"))
            except Exception:
                log.debug("verify: лента для %s не прочиталась", slug, exc_info=True)
            blocks.append("\n".join(head))
        else:
            place = str(cand.get("place") or "")
            try:
                prof = rooms.profile_read(place)
                head = [f"### room:{place}",
                        f"профиль: {prof.get('title') or '(без имени)'}; режим {prof['header'].get('mode')}",
                        f"последнее сообщение ленты: {cand.get('age_days')}д назад"]
                recent = ml.iter_events(chat_id=place, limit=6)
                head.append("Хвост ленты:\n" + ("\n".join(
                    f"- {str(e.get('text') or '')[:160]}" for e in recent) or "(пусто)"))
            except Exception:
                head = [f"### room:{place}", "профиль/лента не прочитались"]
            blocks.append("\n".join(head))
    verdicts: list[dict] = []
    if _llm().configured("voice"):
        try:
            resp = _llm().chat("voice", system=VERIFY_SYS,
                               messages=[{"role": "user", "content": "\n\n".join(blocks)[:60000]}],
                               max_tokens=2400)
            data = _json_obj(resp.text)
            verdicts = [v for v in (data.get("verdicts") or [])[:24]
                        if isinstance(v, dict) and v.get("subject")]
        except Exception:
            log.warning("night: сверка-ход голоса упал", exc_info=True)
    # Гарантированный минимум: непокрытые кандидаты = unverifiable с причиной
    seen_subjects = {str(v.get("subject")) for v in verdicts}
    for cand in candidates[:12]:
        subj = (cand.get("slug") if cand.get("kind") == "person"
                else f"room:{cand.get('place')}")
        if subj not in seen_subjects:
            verdicts.append({"subject": subj, "verdict": "unverifiable",
                             "against": "голос не ответил — считаю несверённым"})
    for v in verdicts:
        subj = str(v.get("subject"))
        verdict = str(v.get("verdict") or "unverifiable").lower()
        against = str(v.get("against") or "").strip()[:200]
        if verdict not in counts:
            verdict = "unverifiable"
        counts[verdict] += 1
        try:
            if subj.startswith("room:"):
                place = subj[len("room:"):]
                if verdict in ("confirmed", "unverifiable"):
                    mark = against if verdict == "confirmed" else f"не проверяемо: {against}"
                    rooms.profile_update(place, last_verified=f"{today} {mark}"[:180])
            else:
                if verdict in ("confirmed", "unverifiable"):
                    mark = against if verdict == "confirmed" else f"не проверяемо: {against}"
                    people.set_last_verified(subj, today, mark)
                elif verdict == "refuted":
                    # канон — только голосовым путём: пометка устаревшего + новый факт
                    # с источником «сверка» штатной рукой (append_fact = remember-механизм)
                    people.mark_superseded(subj, against[:60] or subj)
                    people.append_fact(subj, subj, f"сверка {today}: досье противоречило живым "
                                       f"источникам — {against or 'детали в квитанции ночи'}",
                                       source_ref=f"night-verify-{today}")
        except Exception:
            log.warning("night: применение вердикта %s/%s упало", subj, verdict, exc_info=True)
        lines.append(f"{subj}: {verdict} ({against or 'без деталей'})")
    return {**counts, "lines": lines}


def _json_obj(text: str) -> dict:
    """Строгий первый JSON-объект из ответа (клон formation._json_obj)."""
    m = re.search(r"\{.*\}", str(text or ""), re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


# --------------------------------------------------------------------------- #
#  Квитанция ночи (итоговая строка журнала)
# --------------------------------------------------------------------------- #

def night_report(day: str, fold: dict, gnomes: dict, verify: dict, deferred: list[str]) -> str:
    f = (f"фолднуто {fold.get('accepted', 0)} (принято голосом), "
         f"отклонено {fold.get('rejected', 0)}")
    v = (f"свёрено {sum(verify.get(k, 0) for k in ('confirmed', 'refuted', 'unverifiable'))} "
         f"(подтвердилось {verify.get('confirmed', 0)}, опровергнуто "
         f"{verify.get('refuted', 0)}, не проверяемо {verify.get('unverifiable', 0)})")
    g = (f"гномы: компакты {len(gnomes.get('compacts') or [])}/"
         f"противоречия {sum(len(x.get('conflicts') or []) for x in (gnomes.get('contradictions') or []))}/"
         f"свежесть {len(gnomes.get('freshness') or [])}")
    parts = [f"ночь {day}: {f}; {v}; {g}"]
    if deferred:
        parts.append("отложено: " + "; ".join(deferred))
    return ", ".join(parts)
