"""Update requests and new-version inspection notices.

Execution belongs to the external updater; owner consent starts it. Mechanical
checks and previous-program snapshots remain. Automatic agent probation, proof
hooks, watcher respawn and acceptance verdicts were removed in 1.5.2.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable

import package_notice

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
        "проверяет. Ты на эти минуты остановишься; поднявшись, получишь известие о новой версии. "
        "Осмотри её и сообщи владельцу о найденных проблемах. Обновление не ждёт твоей приёмки.\n"
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
        "После обновления осмотри приложение и свои правки. Приёмки по сроку и отката по молчанию больше нет; проблемы сообщай владельцу обычным текстом."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string",
                   "enum": ["status", "plan", "confirm", "decline"],
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


#: Та же рука на ПК: только испытание и состояние — обновление ставит кнопка окна.
TOOL_DESK = {
    "name": TOOL_NAME,
    "description": "Состояние обновления Hélène на этом компьютере. Обновление ставит владелец кнопкой в окне; оно не ждёт твоей приёмки и не откатывается по молчанию. После новой версии осмотри приложение и свои правки, а найденные проблемы сообщи владельцу обычным текстом.",
    "input_schema": {"type":"object","properties":{"action":{"type":"string","enum":["status"]}},"required":["action"]},
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


def status_text(state: dict, *, desktop: bool = False) -> str:
    up = state.get("updater") or {}
    receipt = state.get("receipt") or {}
    lines = []
    if desktop:
        lines.append("Обновление на этом компьютере ставит владелец кнопкой в окне (Настройки → "
                     "Обновление); предложить ему можно словами.")
        if up.get("alive") and up.get("busy"):
            lines.append(f"Установщик сейчас: {up['busy']}.")
    elif up.get("ok"):
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
            lines.append("Это запись прежнего испытания. Новый установщик закроет её автоматически; ожидание проверки агентом отменено.")
    return "\n".join(lines)


def make_hand(tree: Path, owner_spoke: Callable[[], bool], chat_of: Callable[[], str], *,
              desktop: bool = False):
    tree = Path(tree)

    def update_request(action: str = "status", version: str = "latest", backup: str = "full",
                       reason: str = "", owner_words: str = "", report: str = "") -> str:
        try:
            control = _control()
            action = str(action or "status").strip().lower()
            state = control.update_state(tree)
            if action == "status":
                return status_text(state, desktop=desktop)
            if desktop and action in ("plan", "confirm", "decline"):
                return ("На этом компьютере обновление ставит владелец кнопкой в окне (Настройки → "
                        "Обновление). Предложи ему словами, если считаешь, что пора.")
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
                            "словами; поднявшись, получишь известие о новой версии.")
                return (str(got["note"]) + ". Скажи владельцу простыми словами, что просишь "
                        "обновиться и зачем. Согласится — кнопкой «Обновить» в окне или словами тебе "
                        "(тогда action=confirm с его словами).")
            receipt = state.get("receipt") or {}
            if action in ("accept", "reject"):
                return "Автоматической приёмки приложения больше нет. Обновление не ждёт слова агента. Если нашёл проблему, сообщи владельцу обычным текстом."
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
                        "несколько минут пропадёшь; поднявшись, получишь известие о новой версии.")
            return "update_request: action бывает status, plan, confirm или decline"
        except Exception as exc:                      # рука не роняет ход
            log.exception("update_request упал")
            return f"update_request: не вышло — {type(exc).__name__}: {exc}"

    return update_request


def install(agent_mod, tree: Path, cfg: dict | None = None, *,
            owner_spoke: Callable[[], bool] = lambda: False) -> bool:
    """Выдать агенту руку обновления: план на сервере, состояние на ПК; без приёмки."""
    desktop = not on_server()
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
        impl[TOOL_NAME] = make_hand(Path(tree), owner_spoke, chat_of, desktop=desktop)
        if not any(isinstance(t, dict) and t.get("name") == TOOL_NAME for t in tools):
            tools.append(dict(TOOL_DESK if desktop else TOOL))
        purposes = getattr(agent_mod, "HAND_PURPOSE", None)
        if isinstance(purposes, dict):
            purposes[TOOL_NAME] = (("обновление на этом компьютере: состояние "
                                    "после подъёма новой версии") if desktop else
                                   ("обновить себя на сервере: план исполнителю, «да» — у владельца, "
                                    "после подъёма — известие о новой версии"))
    STATE.update(hand=True, note="рука update_request выдана" + (" (ПК: состояние)" if desktop else ""))
    log.info("обновление: рука update_request выдана (%s)",
             "ПК — обновление без испытания" if desktop else "исполнитель — server/updater")
    return True


#: Испытание на ПК: состояние сторожа — в папке установки (его пишет установщик).
TRIAL_STATE = ("backups", "update-trial.json")
TRIAL_OPEN = ("starting", "trial", "accepting", "rollback")
#: Биение сторожа старше — сторожа нет (он бьётся раз в три секунды).
WATCHER_STALE = 30.0
#: Звать сторожа не чаще (он сам проверяет, нет ли другого).
WATCHER_RESPAWN = 120.0
_WATCHER_CALLED = [0.0]


def _setup_exe(install: Path) -> Path:
    if os.name == "nt":
        return install / "helene-setup.exe"
    return install / "Helene Setup.app" / "Contents" / "MacOS" / "helene-setup"


def ensure_watcher(install: Path | None, tree: Path | None, *, now: float | None = None, spawn=None) -> str:
    """Compatibility with older extensions; automatic probation no longer starts."""
    return ""


def pending_report(tree: Path) -> dict | None:
    """Испытание или итог, о котором агенту ещё не сказали (или None).

    Расписка ПК (её пишет установщик, `desktop: true`) — только на ПК, расписка
    исполнителя — только на сервере: архив переноса везёт `memory/.control` с собой, и
    агент, переехавший сразу после обновления, иначе получил бы записку чужого дома."""
    try:
        receipt = _control().update_unreported(Path(tree))
    except Exception:
        log.debug("итог обновления не прочитался", exc_info=True)
        receipt = None
    if receipt is None or bool(receipt.get("desktop")) == on_server():
        return None if on_server() else package_notice.pending(Path(tree))
    return receipt


def mark_reported(tree: Path, receipt: dict, **how) -> None:
    """Отметка по паре (план, состояние): done — рассказано; tries/noted/retry_at — ход ещё
    повторится (записка уже лежит, второй раз её не класть)."""
    if receipt.get('kind')=='package':
        package_notice.mark(Path(tree),receipt,**how)
        return
    _control().update_mark_reported(Path(tree), str(receipt.get("id") or ""),
                                    str(receipt.get("state") or "*"), **how)


def report_mark(tree: Path, receipt: dict | None = None) -> dict:
    """Нынешняя отметка «рассказано» (пусто, если её нет или не читается)."""
    if receipt and receipt.get('kind')=='package': return package_notice.reported(Path(tree))
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
    """Записка испытания — словами владельца (04.10): одна строка, без простыни.

    Было пятнадцать строк чек-листа, и агент (Йоно, сервер Дмитрия) читал их
    вместо дела. Что проверять и как ответить, рука знает из своего описания —
    записка будит, а не инструктирует.
    """
    return ("Системное сообщение: проверь обновление "
            f"{receipt.get('from_version') or '?'} -> {receipt.get('to_version') or '?'} "
            "на работоспособность и совместимость со своими правками.")


def report_note(receipt: dict, *, owner: str = "владелец") -> str:
    """Записка агенту: испытание (проверить себя) или итог (рассказать владельцу)."""
    state = str(receipt.get("state") or "")
    if receipt.get('kind')=='package':
        version=(f"Hélène обновлена с {receipt['from_version']} до {receipt.get('to_version') or '?'}" if receipt.get('from_version')
                 else f"В этой установке работает Hélène {receipt.get('to_version') or '?'}; прежняя версия здесь не записана")
        return (f"Системное сообщение: {version}. "
                "Осмотри новую версию: проверь доступные руки, память и совместимость со своими правками. "
                "Коротко расскажи владельцу, что проверил и какие проблемы нашёл. "
                "Это обновление системного пакета: автоматического испытания и отката здесь нет, решение об изменении пакета принимает владелец.")
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
        lines.append("На испытании ответа до срока не было — новая версия оставлена "
                    "работать, откат не принудительный (04.10, слово владельца).")
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
        lines.append("Осмотри новую версию: проверь доступные руки, память и совместимость со своими правками. Обновление не ждёт твоей приёмки; о проблемах сообщи владельцу.")
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
