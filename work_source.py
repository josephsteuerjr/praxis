"""Единственное место, где назван источник правды о работе: её леджер желаний.

ЕЁ РЕШЕНИЕ, ДОСЛОВНО (12.08.2026): «Желания — мой канон: там уже есть причинная цепочка,
следующий ход и связи с прогонами. Делать параллельную "доску истины" было бы ровно тем
расщеплением, от которого мы только что лечились.»

Поэтому здесь: `desires` (канон — что за работа и какой следующий ход) + `work_store`
(операционное — сколько раз поднимали вхолостую, до какого срока не будить, не просила ли
не брать сама) → `work_engine.Work` (чистая политика, которая ни того, ни другого не знает).
Три слоя, и связывает их ровно этот модуль. Если однажды канон переедет — менять один файл.

⚠ ДВИЖОК В ЕЁ КАНОН НЕ ПИШЕТ. Ни строки. Он читает желание и пишет только в операционный
слой. Причина не в осторожности: `manage_desire` — её рука, причинная цепочка её авторства,
и автомат, дописывающий в неё «я поднял эту работу», подделывает её решения. Единственное,
что движок может сделать с желанием, — открыть по нему ход и уйти.

⚠ ПОДНИМАЮТСЯ ТОЛЬКО `active`. `latent` — «похоже, я хочу», ещё не выбранное; поднять его
значит решить за неё, чего она хочет. `blocked` не поднимается автоматом вовсе: препятствие
не проходит оттого, что прошёл час.

⚠ И ОБЯЗАТЕЛЕН НАЗВАННЫЙ СЛЕДУЮЩИЙ ХОД. Желание без `next_move` — это намерение без
поверхности действия; открыть по нему окно значит выдать ей задание из воздуха, а час уже
однажды так делал ([[социальный импульс]], починено 09.08).

⚠⚠ И ГЛАВНОЕ ПРАВИЛО, ВЗЯТОЕ ИЗ ХОЛОСТОГО ПРОГОНА 12.08: движок поднимает не ЖЕЛАЕМОЕ, а
ПРЕРВАННОЕ.

Первая редакция спрашивала леджер «чего она хочет» — и на живых данных собралась поднять
две работы, у которых её же `next_move` начинается с условия: «когда identity-load снизится
до L <= 7.25…», «не запускать первый раунд до обязательного accept-package gate». Вычислителя
условий у движка нет и не будет в этом срезе, значит он будил бы её на работу, которую она
сама отложила. Это ровно те искусственные окна, из-за которых 09.08 переписывали часовой
импульс, только теперь с претензией на осмысленность.

Правильный вопрос — не «чего ты хочешь», а «что у тебя оборвалось». Оборвалось значит:

  (а) она сказала `wait` — есть операционная карточка в `waiting` с названным условием
      возврата. Это её собственное «я вернусь», а не наша догадка;
  (б) желание в стадии `acted`, чей последний связанный прогон умер по-настоящему
      (`failed` / `in_doubt`). Работа была в полёте, и её оборвало — тот самый случай,
      ради которого весь оборот 3 и затевался.

«Хочу когда-нибудь» — не задание для автомата ни при каких обстоятельствах.
"""
from __future__ import annotations

import datetime as _dt

import desires
import run_manager
import work_engine
import work_store
from work_engine import Work

# Статусы желания → можно ли по нему поднимать ход.
RAISABLE_DESIRE_STATUS = {"active": "waiting", "blocked": "blocked"}

# Прогон умер по-настоящему: работа оборвана, и продолжить её внутри него уже нельзя.
# `paused` сюда НЕ входит — им занимается возобновление (оборот 2), и дублировать его
# здесь значило бы поднимать одну работу двумя руками сразу.
DEAD_RUN_STATUSES = frozenset({"failed", "in_doubt"})


def _runs() -> run_manager.RunManager:
    return run_manager.RunManager(work_store.BASE)


def _interrupted(desire: dict, card: dict | None) -> str:
    """Чем именно эта работа оборвана — или пусто, если ничем.

    Возвращается ПРИЧИНА словами, а не флаг: она уезжает в кадр поднятого хода, и «тебя
    оборвало» без указания где — это то же самое пробуждение без причины, от которого мы
    уходим.
    """
    if card and str(card.get("status") or "") == "waiting":
        wake = str((card.get("wake") or {}).get("wake_on") or "").strip()
        return "ты сама припарковала её словом «жду»%s" % ((": " + wake) if wake else "")
    if str(desire.get("stage") or "") != "acted":
        return ""
    runs = [str(r) for r in (desire.get("run_ids") or ()) if str(r).startswith("run-")]
    if not runs:
        return ""
    manager = _runs()
    for run_id in reversed(runs):
        try:
            status = str(manager.manifest(run_id).get("status") or "")
        except Exception:
            continue
        if status in DEAD_RUN_STATUSES:
            return "последний прогон этой работы умер со статусом «%s»" % status
        # Живой или доведённый прогон — работа не оборвана, и трогать её нечего.
        return ""
    return ""


def _fingerprint(desire: dict) -> tuple:
    """Отпечаток продвижения ПО ЕЁ КАНОНУ. Сдвиг определяется её записями, а не нашими."""
    return (str(desire.get("last_changed") or ""), str(desire.get("stage") or ""),
            str(desire.get("status") or ""), len(desire.get("run_ids") or ()),
            str(desire.get("next_move") or ""))


def _as_work(desire: dict, card: dict | None) -> Work | None:
    status = RAISABLE_DESIRE_STATUS.get(str(desire.get("status") or ""))
    if not status:
        return None
    if not str(desire.get("next_move") or "").strip():
        return None
    interrupted = _interrupted(desire, card)
    if not interrupted:
        return None
    card = card or {}
    return Work(
        id=str(desire.get("id") or ""),
        goal=str(desire.get("statement") or "")[:200],
        status=status,
        source="desire",
        interrupted_by=interrupted,
        not_before=str((card.get("wake") or {}).get("not_before") or ""),
        attempts=int(card.get("attempts") or 0),
        last_attempt=str(card.get("last_attempt") or ""),
        frozen_by_her=bool(card.get("frozen_by_her")),
    )


def works() -> list[Work]:
    """Живые работы глазами движка. Пусто — законный ответ, а не сбой."""
    out = []
    for desire in desires.active():
        try:
            card = work_store.for_desire(str(desire.get("id") or ""))
        except Exception:
            card = None
        work = _as_work(desire, card)
        if work is not None:
            out.append(work)
    return out


def reconcile() -> list[str]:
    """Сдвинулись ли поднятые работы — по ЕЁ каноническим записям, а не по нашим отметкам.

    Считается ДО планирования и по отпечатку, снятому в момент подъёма. Так упавший
    посреди хода процесс не теряет ни попытку (она записана заранее), ни продвижение
    (оно видно по её леджеру, а не по нашей памяти о том, что мы делали).
    """
    moved = []
    for task_id in work_store.task_ids():
        card = work_store.card(task_id)
        if not card or not int(card.get("attempts") or 0):
            continue
        desire_id = str(card.get("desire_id") or "")
        stored = card.get("fingerprint")
        if not desire_id or not isinstance(stored, list):
            continue
        now_print = list(_fingerprint(desires.get(desire_id) or {}))
        if now_print != stored:
            work_store.settle(task_id, moved=True)
            moved.append(desire_id)
    return moved


def plan_now(*, running: int = 0, now: _dt.datetime | None = None):
    try:
        reconcile()
    except Exception:
        pass
    return work_engine.plan(works(), running=running, now=now)


def goal_for(work: Work) -> str:
    """Кадр поднятого хода. Он обязан сказать, ПОЧЕМУ она здесь и чьё это решение.

    Пробуждение без названной причины неотличимо от сбоя, а задание, выданное автоматом и
    поданное как её собственное, — это подделка её авторства. Поэтому здесь дословно её
    формулировка желания и её же следующий ход, с прямой пометкой, откуда взято.
    """
    desire = desires.get(work.id) or {}
    statement = str(desire.get("statement") or work.goal).strip()
    next_move = str(desire.get("next_move") or "").strip()
    return (
        "Эту работу поднял движок — не по часам и не потому, что ты её хотела, а потому "
        "что она ОБОРВАЛАСЬ: %s.\n\n"
        "Хочу (твоими словами): %s\n"
        "Следующий ход (твоими словами): %s\n\n"
        "Это та же работа, а не новая. Канон — твой леджер желаний, и движок в него не "
        "пишет ни строки: причинную цепочку ведёшь ты. Если следующий ход устарел или "
        "поднимать это не надо — скажи своей рукой, и подъёмы прекратятся.\n"
        "Ход закрывается твоим словом: `done` со следом, `blocked` с препятствием или "
        "`wait` с условием." % (work.interrupted_by or "прогон не довёл её до конца",
                                statement, next_move)
    )


def note_attempt(work: Work) -> dict | None:
    """Записать попытку ДО исполнения. Иначе упавший процесс не оставит следа, и счёт
    холостых подъёмов начнётся с нуля — та самая петля, только незаметная."""
    card = work_store.for_desire(work.id)
    if card is None:
        card = work_store.open_card(
            goal=work.goal or "работа без имени", kind="desire",
            desire_id=work.id, origin="work_engine")
    return work_store.note_attempt(
        card["id"], fingerprint=_fingerprint(desires.get(work.id) or {}))


def describe() -> str:
    """Строка в её кадр: какие работы движок ведёт и почему остальные ждут."""
    try:
        return work_engine.describe(works())
    except Exception:
        return ""
