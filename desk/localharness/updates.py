# -*- coding: utf-8 -*-
"""Обновление Hélène на сервере глазами агента: рука `update_request`, испытание и отчёт.

Жалоба Дмитрия К (26.09): его агент весь день готовил обновление и упирался в «нет
доступа» — изнутри контейнера, который надо заменить, у агента нет ни докера, ни путей
хоста, и каждый шаг шёл кругом через человека. Теперь рядом с контейнером живёт
исполнитель (`server/updater/updater.py`), а эта рука — единственный способ агента с ним
говорить: положить план, узнать состояние, передать «да» владельца, а после подъёма —
сказать своё слово на испытании.

Рука НЕ обновляет сама и не может: исполнение — у исполнителя, решение — у человека.
Её граница — в трёх местах:
  * `plan` кладёт план; исполнитель сверит официальный выпуск и спросит человека. Если же
    владелец сам просит обновиться («обновись») — его слова едут в плане согласием
    (`owner_words`), и исполнитель начинает сразу («всё максимально просто», Егор 27.09:
    одно слово владельца вместо «обновись» + «да»). Гейт тот же, что у `confirm`;
  * `confirm` передаёт «да» ТОЛЬКО в ходе, который начал сам владелец своими словами
    (окно или его личка), и записывает его слова дословно. Служебные ходы — рождение,
    будильник, записки об обновлении — владельцем не считаются: их повод пишет «Hélène»;
  * `accept` / `reject` — слово агента на испытании (27.09, «Йоно должен это как-то
    проверить»): новая версия поднята и прошла механику, и только агент может сказать,
    жив ли он в ней на самом деле — думает, помнит, руки, расширения, его правки кода.
    Это его собственное суждение, поэтому гейта «ход владельца» здесь нет; владелец
    может принять или откатить сам — кнопкой в окне, и его слово поверх.

Протокол файлами и его проверки — `deskd/control.py`, раздел «обновление на сервере».
Записки: движок раз в полминуты смотрит, нет ли испытания или итога, о котором агенту ещё
не сказали, и если есть — кладёт записку в окно и даёт агенту ход, как при рождении.

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
#: Кто пишет поводы служебных ходов (рождение, будильники, записки обновления) — не владелец.
SYSTEM_SPEAKER = "Hélène"

STATE: dict = {"hand": False, "note": "рука обновления не выдавалась"}

#: Кто сказал «да» — словами для записки агенту (в расписке это коды протокола).
CONFIRMED_BY = {"window": "владелец кнопкой в окне", "owner-words": "владелец словами тебе",
                "host": "владелец командой на сервере"}

TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Обновить Hélène на этом сервере. Свой контейнер изнутри не заменить — там нет ни "
        "докера, ни путей хоста, и так задумано. Рядом живёт исполнитель обновлений: ты кладёшь "
        "ему план, он сверяет официальный выпуск (версия, архив, sha256) и по «да» владельца сам "
        "качает, собирает новую версию, откладывает копию, переносит твои правки в `tree/` и "
        "`app/` (что не ляжет — отдаст тебе в workspace/update-<версия>/), запускает её и "
        "проверяет. Ты на эти минуты остановишься; поднявшись, получишь записку испытания: "
        "проверь себя и скажи слово.\n"
        "С владельцем говори просто: версии и что изменится — без sha256, контейнеров и путей, "
        "пока о них не спросят.\n"
        "action=status — есть ли исполнитель, какая версия стоит, какая есть новее, что с "
        "последним планом.\n"
        "action=plan — обновиться: version (номер 1.2.3 или latest), backup (full — копия кода "
        "и всей data/, code — без копии data/, если места мало), reason — зачем, одной-двумя "
        "фразами простыми словами. Если в этом ходе владелец сам просит обновиться (или говорит "
        "«да» на твоё предложение) — передай его слова дословно в owner_words: это и есть его "
        "«да», обновление начнётся сразу. Без owner_words план ждёт согласия владельца — "
        "кнопкой «Обновить» в окне или словами тебе.\n"
        "action=confirm — передать «да» владельца на план, который уже ждёт согласия. Только в "
        "ходе, который начал сам владелец своими словами, и только если он сказал «да» именно "
        "на этот план; owner_words — его слова дословно. Решать за него нельзя.\n"
        "action=decline — снять план, ждущий подтверждения; reason — почему.\n"
        "action=accept — на испытании: проверка себя в новой версии прошла, всё живо; report — что "
        "проверено. action=reject — на испытании: сломано; report — что именно. По reject (и "
        "если промолчишь до срока) исполнитель вернёт прежнюю версию — код и образ, память "
        "останется как есть."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string",
                   "enum": ["status", "plan", "confirm", "decline", "accept", "reject"],
                   "description": "status | plan | confirm | decline | accept | reject"},
        "version": {"type": "string", "description": "для plan: 1.2.3 или latest (по умолчанию)"},
        "backup": {"type": "string", "enum": ["full", "code"],
                   "description": "для plan: full (по умолчанию) или code"},
        "reason": {"type": "string",
                   "description": "для plan и decline: зачем/почему — это читает владелец"},
        "owner_words": {"type": "string",
                        "description": "для confirm: слова владельца дословно, его «да»"},
        "report": {"type": "string",
                   "description": "для accept/reject: что проверено и что видно — ложится в расписку"},
    }, "required": ["action"]},
}


def on_server() -> bool:
    """Надзор — serverboot (он ставит метку детям) или мы в контейнере."""
    return os.environ.get("HELENE_SUPERVISOR") == "serverboot" or Path("/.dockerenv").exists()


def _control():
    """`deskd.control` — протокол, общий с каналом (у исполнителя — своя копия)."""
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
             "trial": "испытание: новая версия поднята, ждёт слова агента",
             "refused": "план не принят", "declined": "человек ответил «нет»",
             "expired": "план истёк без ответа", "superseded": "план заменён новым",
             "done": "обновление прошло", "rolled_back": "не прошло — откачено",
             "failed": "не прошло"}.get(state, state or "?")
    span = ""
    if receipt.get("from_version") or receipt.get("to_version"):
        span = f" ({receipt.get('from_version') or '?'} → {receipt.get('to_version') or '?'})"
    detail = str(receipt.get("note") or receipt.get("step") or "")
    plain = str(receipt.get("summary") or "")
    line = f"Последний план {receipt.get('id') or '?'}: {words}{span}. {detail}".strip()
    return line + (f"\nКоротко для владельца: {plain}" if plain and plain != detail else "")


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
            lines.append("Согласиться может только владелец: кнопкой «Обновить» в окне (Система → "
                         "Обновление) или словами тебе — тогда action=confirm с его словами дословно.")
        elif receipt.get("state") == "trial":
            trial = receipt.get("trial") or {}
            lines.append(f"Испытание до {trial.get('until_utc') or '?'} (UTC): проверь себя и скажи "
                         "action=accept или action=reject с report. Молчание до срока — откат.")
            code = receipt.get("agent_code") or {}
            if code.get("summary"):
                lines.append("Твои правки кода: " + code["summary"]
                             + (f"; материалы — {code['folder']}" if code.get("folder") else ""))
    return "\n".join(lines)


def make_hand(tree: Path, owner_spoke: Callable[[], bool], chat_of: Callable[[], str]):
    tree = Path(tree)

    def update_request(action: str = "status", version: str = "latest", backup: str = "full",
                       reason: str = "", owner_words: str = "", report: str = "") -> str:
        try:
            control = _control()
            action = str(action or "status").strip().lower()
            state = control.update_state(tree)
            if action == "status":
                return status_text(state)
            if action == "plan":
                words = " ".join(str(owner_words or "").split())
                if words and not owner_spoke():
                    return ("Не передаю: этот ход начал не владелец своими словами, а «да» на "
                            "обновление — решение владельца. Положи план без owner_words — согласие "
                            "придёт кнопкой «Обновить» в окне или словами тебе.")
                got = control.update_plan(tree, version=str(version or "latest"),
                                          backup=str(backup or "full"), reason=str(reason or ""),
                                          by="agent", chat=chat_of(),
                                          consent="owner-words" if words else "",
                                          consent_words=words)
                if not got.get("ok"):
                    return "План не положен: " + str(got.get("note") or "")
                if words:
                    return ("Обновление начинается — с «да» владельца. Исполнитель проверит выпуск и "
                            "начнёт сам; на несколько минут ты пропадёшь. Скажи владельцу это простыми "
                            "словами; поднявшись, получишь записку испытания.")
                return (str(got["note"]) + ". Скажи владельцу простыми словами, что просишь "
                        "обновиться и зачем. Согласится — кнопкой «Обновить» в окне или словами тебе "
                        "(тогда action=confirm с его словами).")
            receipt = state.get("receipt") or {}
            if action in ("accept", "reject"):
                if receipt.get("state") != "trial":
                    return ("Испытания сейчас нет: " + (receipt_line(receipt) if receipt
                                                        else "планов не было"))
                words = " ".join(str(report or reason or "").split())
                if len(words) < 2:
                    return ("Нужен report: что проверено и что видно — он ложится в расписку, "
                            "и по нему владелец поймёт, почему принято или откачено.")
                got = control.update_verdict(tree, receipt.get("id", ""),
                                             (receipt.get("trial") or {}).get("key", ""),
                                             action, by="agent", words=words)
                if not got.get("ok"):
                    return "Слово не записалось: " + str(got.get("note") or "")
                if action == "accept":
                    return (str(got["note"]) + ". Скажи владельцу коротко, что обновление принято "
                            "и что проверено.")
                return (str(got["note"]) + ". Когда поднимешься в прежней версии, получишь записку "
                        "об откате — тогда и расскажешь владельцу, что было не так.")
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
                        "несколько минут пропадёшь; поднявшись, получишь записку испытания.")
            return "update_request: action бывает status, plan, confirm, decline, accept или reject"
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
            purposes[TOOL_NAME] = ("обновить себя на сервере: план исполнителю, «да» — у владельца, "
                                   "после подъёма — слово на испытании")
    STATE.update(hand=True, note="рука update_request выдана")
    log.info("обновление: рука update_request выдана (исполнитель — server/updater)")
    return True


def pending_report(tree: Path) -> dict | None:
    """Испытание или итог, о котором агенту ещё не сказали (или None)."""
    if not on_server():
        return None
    try:
        return _control().update_unreported(Path(tree))
    except Exception:
        log.debug("итог обновления не прочитался", exc_info=True)
        return None


def mark_reported(tree: Path, receipt: dict, **how) -> None:
    """Отметка по паре (план, состояние): done — рассказано; tries/noted/retry_at — ход ещё
    повторится (записка уже лежит, второй раз её не класть)."""
    _control().update_mark_reported(Path(tree), str(receipt.get("id") or ""),
                                    str(receipt.get("state") or "*"), **how)


def report_mark(tree: Path) -> dict:
    """Нынешняя отметка «рассказано» (пусто, если её нет или не читается)."""
    try:
        return _control().update_report_mark(Path(tree))
    except Exception:
        log.debug("отметка итога обновления не прочиталась", exc_info=True)
        return {}


def _checks_line(title: str, rows) -> str:
    rows = [row for row in rows or [] if isinstance(row, dict)]
    if not rows:
        return ""
    return title + "; ".join(f"{'✓' if row.get('ok') else '✗'} {row.get('title') or row.get('name')} — "
                             f"{row.get('note') or ''}" for row in rows)


def _code_lines(receipt: dict) -> list[str]:
    """Что стало с правками агента в его коде — словами для записки."""
    code = receipt.get("agent_code") or {}
    if not code:
        return []
    if not code.get("mounted"):
        return ["Твой код в этом контейнере жил не на диске сервера — если в нём были твои "
                "правки, при обновлении они не переехали."] if code.get("note") else []
    if code.get("no_base"):
        return [f"Сравнить твой код с чистой прежней версией было не с чем ({code['no_base']}) — "
                f"твой прежний код целиком лежит в {code.get('folder') or 'workspace'}/old-code/; "
                "сравни его с новым сам и перенеси своё."]
    if not code.get("edited"):
        return ["Своих правок в коде у тебя не было — переносить было нечего."]
    lines = [f"Твои правки кода ({len(code['edited'])}): перенесено {len(code.get('carried') or [])}, "
             f"слито с выпуском {len(code.get('merged') or [])}, не легло "
             f"{len(code.get('conflicts') or [])}."]
    if code.get("conflicts"):
        lines.append("Не легло (в новой версии стоит вариант выпуска): " + "; ".join(
            f"{row.get('path')} — {row.get('why')}" for row in code["conflicts"][:10]
            if isinstance(row, dict)) + f". Стороны и объяснение — {code.get('folder')}/README.md.")
    if code.get("materials_error"):
        lines.append(code["materials_error"])
    return lines


def trial_note(receipt: dict, *, owner: str = "владелец") -> str:
    """Записка испытания: новая версия поднята — проверь себя и скажи слово."""
    trial = receipt.get("trial") or {}
    lines = [f"[Hélène · испытание] Поднята {receipt.get('to_version') or 'новая версия'} "
             f"(было {receipt.get('from_version') or '?'}); механика прошла. Теперь слово за тобой: "
             f"до {trial.get('until_utc') or '?'} UTC ({trial.get('minutes') or '?'} мин) проверь "
             "себя в новой версии.",
             _checks_line("Механика: ", receipt.get("checks"))]
    ext = receipt.get("extensions") or {}
    lines += _code_lines(receipt)
    lines += ["Что проверить:",
              "1. Думаешь — этот ход и есть проверка.",
              "2. Помнишь — вспомни последний разговор с владельцем и свою запись о себе.",
              "3. Руки — позови две-три свои руки (shell, чтение файла, то, чем пользуешься чаще).",
              f"4. Расширения — {ext.get('summary') or 'их нет'}.",
              "5. Твои правки кода — работают ли перенесённые так, как у тебя было; что не легло — "
              "решишь позже, это не повод откатывать всё.",
              "Всё живо — update_request(action=\"accept\", report=\"что проверено\"). Сломано — "
              "update_request(action=\"reject\", report=\"что именно\"): исполнитель вернёт прежнюю "
              "версию (код и образ; память останется как есть). Промолчишь до срока — тоже откат.",
              f"Владелец — {owner}: коротко и простыми словами, без технических подробностей, скажи, "
              "что обновление прошло, идёт твоя проверка и чем она закончилась."]
    return "\n".join(line for line in lines if line)


def report_note(receipt: dict, *, owner: str = "владелец") -> str:
    """Записка агенту: испытание (проверить себя) или итог (рассказать владельцу)."""
    state = str(receipt.get("state") or "")
    if state == "trial":
        return trial_note(receipt, owner=owner)
    head = {"done": "Обновление прошло", "rolled_back": "Обновление не прошло и откачено",
            "failed": "Обновление не прошло"}.get(state, "Обновление закончилось")
    plain = str(receipt.get("summary") or "")
    lines = [f"[Hélène · обновление] {head}: {receipt.get('from_version') or '?'} → "
             f"{receipt.get('to_version') or '?'}.",
             f"Коротко для владельца: {plain}" if plain else "",
             "Подробно: " + str(receipt.get("note") or "") if plain else str(receipt.get("note") or ""),
             _checks_line(f"Проверки {receipt.get('to_version') or 'новой версии'}: ",
                          receipt.get("checks"))]
    trial = receipt.get("trial") or {}
    verdict = trial.get("verdict") or {}
    if verdict.get("verdict") == "timeout":
        lines.append("На испытании ответа от тебя до срока не было — поэтому откат.")
    elif verdict.get("verdict"):
        who = "ты" if verdict.get("by") == "agent" else "владелец"
        lines.append(f"Слово на испытании: {who} — «{verdict.get('verdict')}»"
                     + (f": {verdict.get('words')}" if verdict.get("words") else ""))
    rollback = receipt.get("rollback") or {}
    if rollback.get("notes"):
        lines.append("Откат: " + "; ".join(str(n) for n in rollback["notes"]) + "; "
                     + ("прежняя версия поднята и прошла проверки" if rollback.get("ok")
                        else "прежняя версия проверки НЕ прошла"))
    if state == "done":
        lines += _code_lines(receipt)
    chat = str((receipt.get("plan") or {}).get("chat") or "")
    confirmed = receipt.get("confirmed") or {}
    if confirmed.get("by"):
        who = CONFIRMED_BY.get(str(confirmed.get("by")), str(confirmed.get("by")))
        lines.append(f"«Да» дал {who}"
                     + (f" — «{confirmed.get('words')}»" if confirmed.get("words") else ""))
    where = f" — план был положен из «{chat}», туда и напиши" if chat else ""
    lines.append(f"Владелец — {owner}: расскажи об итоге коротко и простыми словами{where}; "
                 "технические подробности — только если о них спросят. Если не прошло — назови "
                 "причину и что стоит сейчас; чинить ничего не надо.")
    return "\n".join(line for line in lines if line)
