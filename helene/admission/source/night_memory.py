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

    -> {"accepted": n, "rejected": n, "failed": n, "decisions": [...]}. Принятое —
    та же транзакция `fold_now` (voice-роль внутри `compact_if_due`); отклонённое
    голосом — reject_fold; НЕ отклоняем без голоса: если voice не настроен или
    ход упал, офферы остаются открытыми до следующей ночи (failed-счётчик,
    причина в решении). «Утро без зависших offers» держит голос, не враньё.
    """
    ml = _memory_life()
    offers = dict(offers if offers is not None else open_offers())
    if not offers:
        return {"accepted": 0, "rejected": 0, "failed": 0, "decisions": []}
    if not _llm().configured("voice"):
        return {"accepted": 0, "rejected": 0, "failed": len(offers), "decisions": [],
                "reason": "voice не настроен — предложения свёртки оставлены открытыми"}
    decisions: list[dict] = []
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
                           max_tokens=1200, reasoning_effort="low")
        data = _json_obj(resp.text)
        for d in (data.get("decisions") or [])[:64]:
            if isinstance(d, dict) and d.get("place"):
                decisions.append({"place": str(d["place"]),
                                  "accept": bool(d.get("accept")),
                                  "reason": str(d.get("reason") or "")[:120]})
    except Exception:
        log.warning("night: голосовой ход по офферам упал — предложения остаются открытыми",
                    exc_info=True)
        return {"accepted": 0, "rejected": 0, "failed": len(offers), "decisions": [],
                "reason": "голосовой ход по офферам упал — оставлены открытыми до следующей ночи"}
    accepted = rejected = failed = 0
    by_place = {d["place"]: d for d in decisions}
    for place in sorted(offers):
        d = by_place.get(place)
        if d is None:
            # голос промолчал про место — НЕ отклоняем (окно могло ещё жить):
            # предложение остаётся открытым, вернётся следующей ночью
            failed += 1
            decisions.append({"place": place, "accept": False,
                              "reason": "голос не ответил — предложение остаётся открытым"})
            continue
        if d["accept"]:
            try:
                out = ml.fold_now(place)
                if out.get("folded"):
                    accepted += 1
                    continue
                # не свернулось (state_changed/окно сдвинулось) — честный failed,
                # не «отклонено»: предложение остаётся у memory_life
                failed += 1
                decisions[decisions.index(d)] = dict(
                    d, reason=f"fold_now не свернул: {out.get('reason') or 'без причины'}")
                continue
            except Exception:
                log.warning("night: fold_now(%s) упал", place, exc_info=True)
                failed += 1
                decisions[decisions.index(d)] = dict(
                    d, reason="fold_now упал — предложение остаётся открытым")
                continue
        else:
            try:
                ml.reject_fold(place, d.get("reason") or "отклонено голосом ночью")
            except AttributeError:
                log.warning("night: reject_fold отсутствует в memory_life — "
                            "предложение %s остаётся открытым", place)
                failed += 1
                continue
        rejected += 1
    return {"accepted": accepted, "rejected": rejected, "failed": failed,
            "decisions": decisions}


# --------------------------------------------------------------------------- #
#  C5: три гнома-аудитора (evaluator, чистый контекст, JSONL-вердикты)
# --------------------------------------------------------------------------- #

_GNOME_SYS_HEAD = (
    "Ты — узкий аудитор памяти Praxis. Ты видишь ТОЛЬКО срез артефактов с ID и "
    "датами; сырых исповедей и переписок в этом промпте нет и быть не может. "
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
        # сырьё — только МЕТАДАННЫЕ исходных событий (id/salience/тип/дата):
        # сырые тексты переписок гному не дают (B4); судит по сводке компакта
        lines: list[str] = []
        try:
            events = {str(e.get("id")): e for e in
                      ml.iter_events(chat_id=meta.get("chat_id"), limit=4000)}
            for sid in (meta.get("source_event_ids") or [])[:120]:
                ev = events.get(str(sid))
                if ev is not None:
                    try:
                        ts = time.strftime("%Y-%m-%d %H:%M",
                                           time.gmtime(float(ev.get("ts") or 0)))
                    except (TypeError, ValueError):
                        ts = "?"
                    lines.append(f"- [{sid}] salience s{ev.get('salience')} · "
                                 f"{str(ev.get('kind') or 'event')} · {ts}")
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
        user = (f"Компакт {cid} (tier {meta.get('tier')}, degraded={meta.get('degraded')}, "
                f"событий {meta.get('event_count')}):\n"
                "## Суть (сводка компакта — главный предмет аудита)\n"
                + (body or "(тело недоступно)")
                + "\n\n## Метаданные исходных событий (текстов в этой вырезке нет)\n"
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
    day = day or _day_key()
    import praxis_time
    today = praxis_time.today()
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
            if age_days is None:
                # нет служебной сверки — прокси контакта: mtime досье (обновляется,
                # когда человек в кадре). Иначе первая ночь заливала бы кандидатами
                # всех, включая вчерашних собеседников.
                try:
                    age_days = (today - _dt.date.fromtimestamp(path.stat().st_mtime)).days
                except OSError:
                    age_days = None
            stale = age_days is None or age_days > PEOPLE_STALE_DAYS
            if stale:
                out.append({"kind": "person", "slug": slug,
                            "last_verified": verified, "against": against,
                            "age_days": age_days})
                _gnome_record("freshness", slug, day,
                              {"stale": True, "age_days": age_days},
                              detail=(f"last_verified={verified}" if verified
                                      else "сверки нет, mtime-прокси"))
    # комнаты: две стрелки — возраст ленты и возраст сверки профиля (rooms.last_verified).
    # Кандидат — тишина дольше порога ПОСЛЕ последней сверки: подтверждённая тихая
    # комната не возвращается в кандидаты каждую ночь, тихая несверенная — кандидат.
    try:
        import rooms as _rooms
        for place in known_places():
            if _gnome_done("freshness", f"room:{place}", day):
                continue
            last_ts = last_activity_ts(place)
            if last_ts is None:
                continue
            feed_age = int((time.time() - float(last_ts)) / 86400.0)
            if feed_age <= ROOM_STALE_DAYS:
                continue
            try:
                header = (_rooms.profile_read(place) or {}).get("header") or {}
            except Exception:
                header = {}
            lv = str(header.get("last_verified") or "").strip()
            m = re.match(r"(\d{4}-\d{2}-\d{2})", lv)
            vdate = None
            if m:
                try:
                    vdate = _dt.date.fromisoformat(m.group(1))
                except ValueError:
                    vdate = None
            if vdate is not None and (today - vdate).days <= ROOM_STALE_DAYS:
                continue  # сверена недавно: тихая комната не кандидат
            age_days = feed_age  # возраст тишины ленты; дата сверки — отдельным полем
            out.append({"kind": "room", "place": place, "age_days": age_days,
                        "last_verified": lv or None})
            _gnome_record("freshness", f"room:{place}", day,
                          {"stale": True, "age_days": age_days},
                          detail=f"лента {feed_age}д, сверка {lv or 'не сверена'}")
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
    "коротко\",\"old_fragments\":[\"дословные фрагменты строк фактов, которые "
    "опровергнуты — только при verdict=refuted\"]}}"
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
        return {**counts, "failed": 0, "deferred": 0, "lines": []}
    # B5: кандидаты сверх лимита не исчезают молча — квитанция + отложенный счёт
    cands = candidates[:12]
    deferred = len(candidates) - len(cands)
    if deferred > 0:
        receipt(_run_id(), _day_key(), "verify_candidates", "skipped",
                reason=f"кандидатов {len(candidates)}, обработано {len(cands)} — "
                       f"остаток {deferred} вернётся следующей ночью")
    # Сборка материалов по кандидатам — читаем всё живое, что есть
    blocks: list[str] = []
    for cand in cands:
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
    data_parsed_ok = True
    voice_ok = _llm().configured("voice")
    if voice_ok:
        try:
            resp = _llm().chat("voice", system=VERIFY_SYS,
                               messages=[{"role": "user", "content": "\n\n".join(blocks)[:60000]}],
                               max_tokens=2400, reasoning_effort="low")
            data = _json_obj(resp.text)
            if not re.search(r"\{.*\}", str(resp.text or ""), re.S):
                data_parsed_ok = False   # ответ вообще не JSON — голос не ответил
            verdicts = [v for v in (data.get("verdicts") or [])[:24]
                        if isinstance(v, dict) and v.get("subject")]
        except Exception:
            log.warning("night: сверка-ход голоса упал — канон не трогаю", exc_info=True)
            voice_ok = False
    if not voice_ok:
        # B3: без голосового хода ничего не применяется к канону (ни шапок, ни
        # superseded) — честный failed, кандидаты вернутся следующей ночью
        reason = ("voice не настроен" if not _llm().configured("voice")
                  else "голосовой ход сверки упал")
        receipt(_run_id(), _day_key(), "verify", "failed",
                reason=f"{reason} — {len(cands)} кандидатов не сверены, канон не тронут")
        return {**counts, "failed": len(cands), "deferred": deferred,
                "lines": [f"{c.get('slug') or c.get('place')}: не сверено ({reason})"
                          for c in cands]}
    # Скоуп ночи (судейский фикс): голос решает только по предъявленным
    # кандидатам — вердикт о субъекте вне списка не применяется, иначе одна
    # ночь получала бы право писать в любые досье, включая фантомы.
    allowed = {(cand.get("slug") if cand.get("kind") == "person"
                else f"room:{cand.get('place')}") for cand in cands}
    stray = [v for v in verdicts if str(v.get("subject")) not in allowed]
    if stray:
        receipt(_run_id(), _day_key(), "verify_scope", "skipped",
                reason=(f"голос ответил по {len(stray)} субъектам вне списка "
                        f"кандидатов — не применено"))
        verdicts = [v for v in verdicts if str(v.get("subject")) in allowed]
    if not verdicts and data_parsed_ok is False:
        # Мусорный ответ голоса (JSON не разобрался / ни одного вердикта) — честный
        # failed: «не проверяемо» здесь лгало бы, сбрасывая таймер свежести
        # несверённых досье на месяц без единой проверки.
        receipt(_run_id(), _day_key(), "verify", "failed",
                reason=f"голос не дал ни одного вердикта — {len(cands)} кандидатов "
                       f"не сверены, канон не тронут")
        return {**counts, "failed": len(cands), "deferred": deferred,
                "lines": [f"{c.get('slug') or c.get('place')}: не сверено (голос промолчал)"
                          for c in cands]}
    # Гарантированного синтеза «unverifiable» для непокрытых кандидатов НЕТ:
    # пометка сверки — решение голоса; кандидат без явного вердикта остаётся
    # несверённым и вернётся следующей ночью (канон не трогаем).
    covered = {str(v.get("subject")) for v in verdicts}
    uncovered = [cand for cand in cands
                 if ((cand.get("slug") if cand.get("kind") == "person"
                      else f"room:{cand.get('place')}") not in covered)]
    if uncovered:
        receipt(_run_id(), _day_key(), "verify_uncovered", "skipped",
                reason=f"голос не ответил по {len(uncovered)} из {len(cands)} кандидатов — "
                       f"они не сверены и вернутся следующей ночью")
        lines = [f"{c.get('slug') or c.get('place')}: не сверено (голос не ответил)"
                 for c in uncovered]
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
                    rooms.profile_update(place, last_verified=f"{today} {mark}")
            else:
                if verdict in ("confirmed", "unverifiable"):
                    mark = against if verdict == "confirmed" else f"не проверяемо: {against}"
                    people.set_last_verified(subj, today, mark)
                elif verdict == "refuted":
                    # канон — только голосовым путём: пометка устаревшего + новый факт
                    # с источником «сверка» штатной рукой (append_fact = remember-механизм).
                    # B2: помечаем ТОЛЬКО дословные фрагменты из old_fragments —
                    # свободный текст against не матчится к строкам фактов.
                    marked = 0
                    for frag in (v.get("old_fragments") or []):
                        if not isinstance(frag, str):
                            continue
                        frag = frag.strip()
                        if len(frag) >= 8 and people.mark_superseded(subj, frag):
                            marked += 1
                    if not (v.get("old_fragments") or []):
                        log.warning("night: refuted без old_fragments (%s) — строки не "
                                    "помечены, факт-запись с against зафиксирует суть", subj)
                    people.append_fact(subj, subj, f"сверка {today}: досье противоречило живым "
                                       f"источникам — {against or 'детали в квитанции ночи'}"
                                       + (f" (помечено устаревшим: {marked})"
                                          if marked else ""),
                                       source_ref=f"night-verify-{today}")
        except Exception:
            log.warning("night: применение вердикта %s/%s упало", subj, verdict, exc_info=True)
        lines.append(f"{subj}: {verdict} ({against or 'без деталей'})")
    return {**counts, "failed": 0, "deferred": deferred, "lines": lines}


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
         f"отклонено {fold.get('rejected', 0)}, не решено {fold.get('failed', 0)}")
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
