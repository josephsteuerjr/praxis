# -*- coding: utf-8 -*-
"""Обновление Hélène на сервере глазами агента: рука `update_request` и отчёт после подъёма.

Жалоба Дмитрия К (26.09): его агент весь день готовил обновление и упирался в «нет
доступа» — изнутри контейнера, который надо заменить, у агента нет ни докера, ни путей
хоста, и каждый шаг шёл кругом через человека. Теперь рядом с контейнером живёт
исполнитель (`server/updater/updater.py`), а эта рука — единственный способ агента с ним
говорить: положить план, узнать состояние, передать «да» владельца.

Рука НЕ обновляет сама и не может: исполнение — у исполнителя, решение — у человека.
Её граница — в двух местах:
  * `plan` только кладёт план; исполнитель сверит официальный выпуск и спросит человека;
  * `confirm` передаёт «да» ТОЛЬКО в ходе, который начал сам владелец своими словами
    (окно или его личка), и записывает его слова дословно. Служебные ходы — рождение,
    будильник, этот же отчёт — владельцем не считаются: их повод пишет «Hélène».

Протокол файлами и его проверки — `deskd/control.py`, раздел «обновление на сервере».
Отчёт после подъёма: движок раз в полминуты смотрит, нет ли итога, о котором агент ещё
не рассказал, и если есть — кладёт записку в окно и даёт агенту ход, как при рождении.

Рука выдаётся только на сервере (надзор — serverboot): на Windows и Mac обновление —
кнопка окна «Проверить обновления», и обещать модели руку, которая всегда откажет, хуже,
чем не иметь её.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable

log = logging.getLogger("helene.updates")

TOOL_NAME = "update_request"
#: Кто пишет поводы служебных ходов (рождение, будильники, этот отчёт) — не владелец.
SYSTEM_SPEAKER = "Hélène"

STATE: dict = {"hand": False, "note": "рука обновления не выдавалась"}

#: Кто сказал «да» — словами для записки агенту (в расписке это коды протокола).
CONFIRMED_BY = {"window": "владелец кнопкой в окне", "owner-words": "владелец словами тебе"}

TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Обновить Hélène на этом сервере. Свой контейнер изнутри не заменить — там нет ни "
        "докера, ни путей хоста, и так задумано. Рядом живёт исполнитель обновлений: ты кладёшь "
        "ему план, он сверяет официальный выпуск (версия, архив, sha256), показывает его "
        "человеку и ждёт «да»; по «да» сам качает, собирает новый образ, откладывает копию, "
        "поднимает новую версию и проверяет её, а при провале откатывает. Ты на эти минуты "
        "остановишься; поднявшись, получишь записку об итоге и расскажешь владельцу.\n"
        "action=status — есть ли исполнитель, какая версия стоит, какая есть новее, что с "
        "последним планом.\n"
        "action=plan — положить план: version (номер 1.2.3 или latest), backup (full — копия "
        "кода и всей data/, code — без копии data/, если места мало), reason — зачем, "
        "одной-двумя фразами: это читает владелец.\n"
        "action=confirm — передать «да» владельца на план, который ждёт подтверждения. Только в "
        "ходе, который начал сам владелец своими словами, и только если он сказал «да» именно "
        "на этот план; owner_words — его слова дословно. Решать за него нельзя: он может "
        "подтвердить и сам — кнопкой в окне (Система → Управление → Обновление).\n"
        "action=decline — снять план, ждущий подтверждения; reason — почему."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "plan", "confirm", "decline"],
                   "description": "status | plan | confirm | decline"},
        "version": {"type": "string", "description": "для plan: 1.2.3 или latest (по умолчанию)"},
        "backup": {"type": "string", "enum": ["full", "code"],
                   "description": "для plan: full (по умолчанию) или code"},
        "reason": {"type": "string",
                   "description": "для plan и decline: зачем/почему — это читает владелец"},
        "owner_words": {"type": "string",
                        "description": "для confirm: слова владельца дословно, его «да»"},
    }, "required": ["action"]},
}


def on_server() -> bool:
    """Надзор — serverboot (он ставит метку детям) или мы в контейнере."""
    return os.environ.get("HELENE_SUPERVISOR") == "serverboot" or Path("/.dockerenv").exists()


def _control():
    """`deskd.control` — протокол, общий с каналом и исполнителем."""
    app_dir = Path(__file__).resolve().parent.parent
    if str(app_dir) not in sys.path:
        sys.path.insert(1, str(app_dir))
    from deskd import control  # noqa: PLC0415
    return control


def receipt_line(receipt: dict) -> str:
    """Одна строка о последнем плане — для руки, окна-записки и отчёта."""
    state = str(receipt.get("state") or "")
    words = {"checking": "исполнитель сверяет план", "awaiting": "ждёт «да» человека",
             "confirmed": "«да» получено, начинается", "running": "идёт обновление",
             "refused": "план не принят", "declined": "человек ответил «нет»",
             "expired": "план истёк без ответа", "superseded": "план заменён новым",
             "done": "обновление прошло", "rolled_back": "не прошло — откачено",
             "failed": "не прошло"}.get(state, state or "?")
    span = ""
    if receipt.get("from_version") or receipt.get("to_version"):
        span = f" ({receipt.get('from_version') or '?'} → {receipt.get('to_version') or '?'})"
    detail = str(receipt.get("note") or receipt.get("step") or "")
    return f"Последний план {receipt.get('id') or '?'}: {words}{span}. {detail}".strip()


def status_text(state: dict) -> str:
    up = state.get("updater") or {}
    receipt = state.get("receipt") or {}
    lines = []
    if up.get("ok"):
        latest = (up.get("latest") or {}).get("version") or ""
        lines.append(f"Исполнитель обновлений на связи. Стоит {up.get('current') or '?'}; "
                     f"последняя в выпусках — {latest or 'ещё не проверял'}"
                     + (" — новее, можно планировать." if up.get("newer") else "."))
        if up.get("busy"):
            lines.append(f"Сейчас он занят: {up['busy']}.")
    else:
        lines.append("Обновить сейчас нельзя: " + str(up.get("why") or "исполнителя нет"))
    if receipt:
        lines.append(receipt_line(receipt))
        if receipt.get("state") == "awaiting":
            lines.append("Подтвердить может только владелец: кнопкой в окне (Система → "
                         "Управление → Обновление) или словами тебе — тогда action=confirm с "
                         "его словами дословно.")
    return "\n".join(lines)


def make_hand(tree: Path, owner_spoke: Callable[[], bool], chat_of: Callable[[], str]):
    tree = Path(tree)

    def update_request(action: str = "status", version: str = "latest", backup: str = "full",
                       reason: str = "", owner_words: str = "") -> str:
        try:
            control = _control()
            action = str(action or "status").strip().lower()
            state = control.update_state(tree)
            if action == "status":
                return status_text(state)
            if action == "plan":
                got = control.update_plan(tree, version=str(version or "latest"),
                                          backup=str(backup or "full"), reason=str(reason or ""),
                                          by="agent", chat=chat_of())
                if not got.get("ok"):
                    return "План не положен: " + str(got.get("note") or "")
                return (str(got["note"]) + ". Скажи владельцу, что просишь обновление и зачем; "
                        "через несколько секунд action=status покажет, что сверил исполнитель "
                        "(версия, размер, сумма, копия) — это и передай ему. Подтверждает он: "
                        "кнопкой в окне или словами тебе.")
            receipt = state.get("receipt") or {}
            if action in ("confirm", "decline"):
                if receipt.get("state") != "awaiting":
                    return ("Подтверждать нечего: " + (receipt_line(receipt) if receipt
                                                       else "планов не было"))
                if action == "decline":
                    got = control.update_confirm(tree, receipt.get("id", ""), receipt.get("nonce", ""),
                                                 "no", by="agent", words=str(reason or ""))
                    return str(got.get("note") or "")
                if not owner_spoke():
                    return ("Не передаю: этот ход начал не владелец своими словами. «Да» на "
                            "обновление — его решение: пусть скажет его тебе сам или нажмёт "
                            "«Подтвердить» в окне.")
                words = " ".join(str(owner_words or "").split())
                if len(words) < 2:
                    return ("Нужны слова владельца дословно (owner_words) — они ложатся в "
                            "расписку рядом с его «да».")
                got = control.update_confirm(tree, receipt.get("id", ""), receipt.get("nonce", ""),
                                             "yes", by="owner-words", words=words)
                if not got.get("ok"):
                    return "Не передалось: " + str(got.get("note") or "")
                return (str(got["note"]) + ". Скажи владельцу, что начинается и что ты на "
                        "несколько минут пропадёшь; поднявшись, получишь записку об итоге.")
            return "update_request: action бывает status, plan, confirm или decline"
        except Exception as exc:                      # рука не роняет ход
            log.exception("update_request упал")
            return f"update_request: не вышло — {type(exc).__name__}: {exc}"

    return update_request


def install(agent_mod, tree: Path, cfg: dict | None = None, *,
            owner_spoke: Callable[[], bool] = lambda: False) -> bool:
    """Выдать агенту руку обновления — только на сервере. -> выдана ли."""
    if not on_server():
        STATE.update(hand=False, note="не сервер: обновление — кнопкой окна")
        return False
    impl = getattr(agent_mod, "TOOL_IMPL", None)
    tools = getattr(agent_mod, "BASE_TOOLS", None)
    if not isinstance(impl, dict) or not isinstance(tools, list):
        STATE.update(hand=False, note="у дерева нет TOOL_IMPL/BASE_TOOLS")
        log.warning("обновление: рука не выдана — %s", STATE["note"])
        return False

    def chat_of() -> str:
        try:
            ctx = agent_mod._TURN_CHANNEL.get()
        except Exception:
            return ""
        if ctx is None:
            return ""
        title = str(getattr(ctx, "title", "") or "")
        chat = str(getattr(ctx, "chat_id", "") or "")
        return f"{title} ({chat})" if title and chat else (title or chat)

    if impl.get(TOOL_NAME) is None:
        impl[TOOL_NAME] = make_hand(Path(tree), owner_spoke, chat_of)
        if not any(isinstance(t, dict) and t.get("name") == TOOL_NAME for t in tools):
            tools.append(dict(TOOL))
        purposes = getattr(agent_mod, "HAND_PURPOSE", None)
        if isinstance(purposes, dict):
            purposes[TOOL_NAME] = "обновить себя на сервере: план исполнителю, «да» — у владельца"
    STATE.update(hand=True, note="рука update_request выдана")
    log.info("обновление: рука update_request выдана (исполнитель — server/updater)")
    return True


def pending_report(tree: Path) -> dict | None:
    """Итог обновления, о котором агент ещё не рассказал (или None)."""
    if not on_server():
        return None
    try:
        return _control().update_unreported(Path(tree))
    except Exception:
        log.debug("итог обновления не прочитался", exc_info=True)
        return None


def mark_reported(tree: Path, receipt: dict) -> None:
    _control().update_mark_reported(Path(tree), str(receipt.get("id") or ""))


def report_note(receipt: dict, *, owner: str = "владелец") -> str:
    """Записка агенту после подъёма: что вышло, что проверено, кому и где рассказать."""
    state = str(receipt.get("state") or "")
    head = {"done": "Обновление прошло", "rolled_back": "Обновление не прошло и откачено",
            "failed": "Обновление не прошло"}.get(state, "Обновление закончилось")
    lines = [f"[Hélène · обновление] {head}: {receipt.get('from_version') or '?'} → "
             f"{receipt.get('to_version') or '?'}.",
             str(receipt.get("note") or "")]
    checks = receipt.get("checks") or []
    if checks:
        lines.append(f"Проверки {receipt.get('to_version') or 'новой версии'}: " + "; ".join(
            f"{'✓' if row.get('ok') else '✗'} {row.get('title') or row.get('name')} — "
            f"{row.get('note') or ''}" for row in checks if isinstance(row, dict)))
    rollback = receipt.get("rollback") or {}
    if rollback.get("notes"):
        lines.append("Откат: " + "; ".join(str(n) for n in rollback["notes"]) + "; "
                     + ("прежняя версия поднята и прошла проверки" if rollback.get("ok")
                        else "прежняя версия проверки НЕ прошла"))
    chat = str((receipt.get("plan") or {}).get("chat") or "")
    confirmed = receipt.get("confirmed") or {}
    if confirmed.get("by"):
        who = CONFIRMED_BY.get(str(confirmed.get("by")), str(confirmed.get("by")))
        lines.append(f"«Да» дал {who}"
                     + (f" — «{confirmed.get('words')}»" if confirmed.get("words") else ""))
    where = f" — план был положен из «{chat}», туда и напиши" if chat else ""
    lines.append(f"Владелец — {owner}: расскажи об итоге коротко, своими словами{where}. Если не прошло — "
                 "назови причину из этой записки и что стоит сейчас; чинить ничего не надо.")
    return "\n".join(line for line in lines if line)
