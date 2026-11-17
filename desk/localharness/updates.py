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
from contextlib import nullcontext
from functools import wraps

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


#: Desktop requests hand the authorized clean update to its mechanical executor.
TOOL_DESK = {
    "name": TOOL_NAME,
    "description": "Обновление Hélène на этом компьютере. status — состояние; plan — предложить выпуск (version: latest или номер). Обычное Python-обновление по умолчанию сохраняет авторские правки кода и совмещает их с новым выпуском. Память, переписка, настройки и пауза сохраняются. Чистая замена кода — только при явном source_mode=clean. Передай точные owner_words из прямого поручения; уже разрешённое обновление не требует повторного согласия. confirm/decline — решение по предложению. При конфликте работай только с текущим изолированным кандидатом через source_update: статус, diff, patch, finish. Проверку точного кандидата и независимый review ведёт входящий исполнитель; наблюдай их реальные результаты и исправляй найденное. Не запускай обычный self_review вместо update review и не создавай личные напоминания. Установку, перезапуск и восстановление при сбое выполняет обновлятор. Проверь фактический результат после перезапуска; не заменяй программу другими инструментами в обход задания.",
    "input_schema": {"type":"object","properties":{"action":{"type":"string","enum":["status","plan","confirm","decline","cancel","resume"]},
        "source_mode":{"type":"string","enum":["preserve","clean"]},"version":{"type":"string"},
        "reason":{"type":"string"},"owner_words":{"type":"string"},"identity":{"type":"string"}},"required":["action"]},
}
TOOL_DESK['description'] += (' ИМПЕРАТИВ: прямое поручение владельца обновиться — это задача на завершение, а не на предложение плана. '
    'Сам выясни текущую стадию, выполни доступный следующий шаг и продолжай после результатов воркеров. '
    'Не завершай работу одним обещанием, не жди нового нажатия Update или согласия на уже разрешённую приёмку. '
    'Используй проверенный выпуск и точный кандидат с сохранёнными правками; обновление не ждёт твоей приёмки и не откатывается по молчанию. '
    'Если путь сломан, установи конкретную причину и способ продолжения, сохрани все результаты и сообщи препятствие; '
    'не подменяй выполнение бесконечными будильниками. Наблюдай состояние текущего задания и выполняй доступные действия. '
    'Stop/Pause, отмена и границы инструментов остаются обязательны.')



def on_server() -> bool:
    """Надзор — serverboot (он ставит метку детям) или мы в контейнере."""
    return os.environ.get("HELENE_SUPERVISOR") == "serverboot" or (
        os.name != "nt" and Path("/.dockerenv").exists())


def _control():
    """`deskd.control` — протокол, общий с каналом (у исполнителя — своя копия)."""
    app_dir = Path(__file__).resolve().parent.parent
    if str(app_dir) not in sys.path:
        sys.path.insert(1, str(app_dir))
    from deskd import control  # noqa: PLC0415
    return control



def _language(agent):
    return (getattr(agent,'frame_language',None)
            if getattr(agent,'FRAME_TEXT_BOUNDARIES_API',0)==1 else None)


def _words(agent,key,legacy,**values):
    language=_language(agent)
    return language.text(key,**values) if language else legacy.format_map(values)


def _pinned_to(agent,function):
    @wraps(function)
    def call(*args,**kwargs):
        language=_language(agent)
        with language.pin() if language else nullcontext():return function(*args,**kwargs)
    return call


def _pinned(function):
    @wraps(function)
    def call(*args,**kwargs):return _pinned_to(kwargs.get('agent'),function)(*args,**kwargs)
    return call


def _confirmation_label(agent,code):
    if code not in CONFIRMED_BY:return code
    return _words(agent,'native.updates.confirmation.'+code,CONFIRMED_BY[code])


@_pinned
def receipt_line(receipt: dict, *, agent=None) -> str:
    """Одна строка о последнем плане — для руки, окна-записки и отчёта."""
    state = str(receipt.get("state") or "")
    words = {"checking": _words(agent, 'native.updates.1c828f25493b', 'исполнитель сверяет план'), "awaiting": _words(agent, 'native.updates.177703a6f6b1', 'ждёт «да» человека'),
             "confirmed": _words(agent, 'native.updates.0f80211f4aa6', '«да» получено, начинается'), "running": _words(agent, 'native.updates.36ae10a82cea', 'идёт обновление'),
             "trial": _words(agent, 'native.updates.52570a7a7cd7', 'испытание: новая версия поднята, ждёт слова агента'),
             "refused": _words(agent, 'native.updates.376ec3f93f4d', 'план не принят'), "declined": _words(agent, 'native.updates.7f72dbc56c4e', 'человек ответил «нет»'),
             "expired": _words(agent, 'native.updates.e91219ff5f60', 'план истёк без ответа'), "superseded": _words(agent, 'native.updates.e72fa1f46117', 'план заменён новым'),
             "done": _words(agent, 'native.updates.ac89821e5248', 'обновление прошло'), "rolled_back": _words(agent, 'native.updates.4976d750c137', 'не прошло — откачено'),
             "failed": _words(agent, 'native.updates.9f1050f48cb7', 'не прошло')}.get(state, state or "?")
    span = ""
    if receipt.get("from_version") or receipt.get("to_version"):
        span = f" ({receipt.get('from_version') or '?'} → {receipt.get('to_version') or '?'})"
    detail = str(receipt.get("note") or receipt.get("step") or "")
    plain = str(receipt.get("summary") or "")
    line = _words(agent, 'native.updates.4f715a2ed263', 'Последний план {p0}: {p1}{p2}. {p3}', p0=f"{receipt.get('id') or '?'}", p1=f'{words}', p2=f'{span}', p3=f'{detail}').strip()
    return line + (_words(agent, 'native.updates.f0e1027ea270', '\nКоротко для владельца: {p0}', p0=f'{plain}') if plain and plain != detail else "")


@_pinned
def status_text(state: dict, *, desktop: bool = False, agent=None) -> str:
    up = state.get("updater") or {}
    receipt = state.get("receipt") or {}
    lines = []
    if desktop:
        lines.append(_words(agent, 'native.updates.273830d8ab50', 'Обновление на этом компьютере ставит владелец кнопкой в окне (Настройки → Обновление); предложить ему можно словами.'))
        if up.get("alive") and up.get("busy"):
            lines.append(_words(agent, 'native.updates.4bcee3de1f1c', 'Установщик сейчас: {p0}.', p0=f"{up['busy']}"))
    elif up.get("ok"):
        latest = (up.get("latest") or {}).get("version") or ""
        lines.append(_words(agent, 'native.updates.009c68f3e9d6', 'Исполнитель обновлений на связи. Стоит {p0}; последняя в выпусках — {p1}', p0=f"{up.get('current') or '?'}", p1=f"{latest or _words(agent, 'native.updates.1219571733c1', 'ещё не проверял')}")
                     + (_words(agent, 'native.updates.51508d0105e8', ' — новее, можно планировать.') if up.get("newer") else "."))
        if up.get("busy"):
            lines.append(_words(agent, 'native.updates.27a73752858c', 'Сейчас он занят: {p0}.', p0=f"{up['busy']}"))
    else:
        lines.append(_words(agent, 'native.updates.e36ac7a6a200', 'Обновить сейчас нельзя: ') + str(up.get("why") or _words(agent, 'native.updates.e85f329f5c00', 'исполнителя нет')))
    if receipt:
        lines.append(receipt_line(receipt,agent=agent))
        if receipt.get("state") == "awaiting":
            lines.append(_words(agent, 'native.updates.dc4befe34d3e', 'Согласиться может только владелец: кнопкой «Обновить» в окне (Система → Обновление) или словами тебе — тогда action=confirm с его словами дословно.'))
        elif receipt.get("state") == "trial":
            lines.append(_words(agent, 'native.updates.7d03ee8b29f9', 'Это запись прежнего испытания. Новый установщик закроет её автоматически; ожидание проверки агентом отменено.'))
    return "\n".join(lines)


def make_hand(tree: Path, owner_spoke: Callable[[], bool], chat_of: Callable[[], str], *,
              desktop: bool = False, agent=None):
    tree = Path(tree)

    def update_request(action: str = "status", version: str = "latest", backup: str = "full",
                       reason: str = "", owner_words: str = "", report: str = "",source_mode=None,identity='') -> str:
        try:
            if desktop:
                if action in ('accept','reject'):
                    return _words(agent, 'native.updates.f6c04e316dee', 'Автоматической приёмки приложения больше нет. Обновление не ждёт слова агента. Если нашёл проблему, сообщи владельцу обычным текстом.')
                import json
                from localharness.desktop_update_request import request
                return json.dumps(request(tree,action,version=version,source_mode=source_mode,reason=reason,
                    owner_words=owner_words,owner_spoke=owner_spoke(),identity=identity),ensure_ascii=False)
            control = _control()
            action = str(action or "status").strip().lower()
            state = control.update_state(tree)
            if action == "status":
                return status_text(state, desktop=desktop,agent=agent)
            if desktop and action in ("plan", "confirm", "decline"):
                return (_words(agent, 'native.updates.37e06069222e', 'На этом компьютере обновление ставит владелец кнопкой в окне (Настройки → Обновление). Предложи ему словами, если считаешь, что пора.'))
            if action == "plan":
                words = " ".join(str(owner_words or "").split())
                if words and not owner_spoke():
                    return (_words(agent, 'native.updates.882110ae9332', 'Не передаю: этот ход начал не владелец своими словами, а «да» на обновление — решение владельца. Положи план без owner_words — согласие придёт кнопкой «Обновить» в окне или словами тебе.'))
                got = control.update_plan(tree, version=str(version or "latest"),
                                          backup=str(backup or "full"), reason=str(reason or ""),
                                          by="agent", chat=chat_of(),
                                          consent="owner-words" if words else "",
                                          consent_words=words)
                if not got.get("ok"):
                    return _words(agent, 'native.updates.673b75bd6afb', 'План не положен: ') + str(got.get("note") or "")
                if words:
                    return (_words(agent, 'native.updates.1727be305287', 'Обновление начинается — с «да» владельца. Исполнитель проверит выпуск и начнёт сам; на несколько минут ты пропадёшь. Скажи владельцу это простыми словами; поднявшись, получишь известие о новой версии.'))
                return (str(got["note"]) + _words(agent, 'native.updates.58ded101449e', '. Скажи владельцу простыми словами, что просишь обновиться и зачем. Согласится — кнопкой «Обновить» в окне или словами тебе (тогда action=confirm с его словами).'))
            receipt = state.get("receipt") or {}
            if action in ("accept", "reject"):
                return _words(agent, 'native.updates.f6c04e316dee', 'Автоматической приёмки приложения больше нет. Обновление не ждёт слова агента. Если нашёл проблему, сообщи владельцу обычным текстом.')
            if action in ("confirm", "decline"):
                if receipt.get("state") != "awaiting":
                    return (_words(agent, 'native.updates.6e0d65f9e4f4', 'Подтверждать нечего: ') + (receipt_line(receipt,agent=agent) if receipt
                                                       else _words(agent, 'native.updates.7cba69f73349', 'планов не было')))
                if action == "decline":
                    got = control.update_confirm(tree, receipt.get("id", ""), receipt.get("nonce", ""),
                                                 "no", by="agent", words=str(reason or ""))
                    return str(got.get("note") or "")
                if not owner_spoke():
                    return (_words(agent, 'native.updates.5a2dda5f6883', 'Не передаю: этот ход начал не владелец своими словами. «Да» на обновление — его решение: пусть скажет его тебе сам или нажмёт «Подтвердить» в окне.'))
                words = " ".join(str(owner_words or "").split())
                if len(words) < 2:
                    return (_words(agent, 'native.updates.ef2ccbf47240', 'Нужны слова владельца дословно (owner_words) — они ложатся в расписку рядом с его «да».'))
                got = control.update_confirm(tree, receipt.get("id", ""), receipt.get("nonce", ""),
                                             "yes", by="owner-words", words=words)
                if not got.get("ok"):
                    return _words(agent, 'native.updates.f6bac65de1f8', 'Не передалось: ') + str(got.get("note") or "")
                return (str(got["note"]) + _words(agent, 'native.updates.567784fe643d', '. Скажи владельцу, что начинается и что ты на несколько минут пропадёшь; поднявшись, получишь известие о новой версии.'))
            return _words(agent, 'native.updates.1ab88d057af9', 'update_request: action бывает status, plan, confirm или decline')
        except Exception as exc:                      # рука не роняет ход
            log.exception("update_request упал")
            return _words(agent, 'native.updates.5d89c98d0fec', 'update_request: не вышло — {p0}: {p1}', p0=f'{type(exc).__name__}', p1=f'{exc}')

    return _pinned_to(agent,update_request)


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
        impl[TOOL_NAME] = make_hand(Path(tree), owner_spoke, chat_of, desktop=desktop,agent=agent_mod)
        if not any(isinstance(t, dict) and t.get("name") == TOOL_NAME for t in tools):
            tools.append(dict(TOOL_DESK if desktop else TOOL))
        purposes = getattr(agent_mod, "HAND_PURPOSE", None)
        if isinstance(purposes, dict):
            purposes[TOOL_NAME] = (("обновление на этом компьютере: состояние "
                                    "после подъёма новой версии") if desktop else
                                   ("обновить себя на сервере: план исполнителю, «да» — у владельца, "
                                    "после подъёма — известие о новой версии"))
    STATE.update(hand=True, note="рука update_request выдана" + (" (ПК: состояние)" if desktop else ""))
    import update_inspection
    update_inspection.install(agent_mod, tree)
    if desktop:
        from localharness import source_update
        source_update.install(agent_mod, tree)
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


@_pinned
def _code_lines(receipt: dict, *, agent=None) -> list[str]:
    """Что стало с правками агента в его коде — словами для записки."""
    code = receipt.get("agent_code") or {}
    if not code:
        return []
    if not code.get("mounted"):
        return [_words(agent, 'native.updates.7e6471282d4a', 'Твой код в этом контейнере жил не на диске сервера — если в нём были твои правки, при обновлении они не переехали.')] if code.get("note") else []
    if code.get("no_base"):
        return [_words(agent, 'native.updates.d85a7a671d6d', 'Сравнить твой код с чистой прежней версией было не с чем ({p0}) — твой прежний код целиком лежит в {p1}/old-code/; сравни его с новым сам и перенеси своё.', p0=f"{code['no_base']}", p1=f"{code.get('folder') or 'workspace'}")]
    if not code.get("edited"):
        return [_words(agent, 'native.updates.ba080942c9e8', 'Своих правок в коде у тебя не было — переносить было нечего.')]
    lines = [_words(agent, 'native.updates.128087f06ee8', 'Твои правки кода ({p0}): перенесено {p1}, слито с выпуском {p2}, не легло {p3}.', p0=f"{len(code['edited'])}", p1=f"{len(code.get('carried') or [])}", p2=f"{len(code.get('merged') or [])}", p3=f"{len(code.get('conflicts') or [])}")]
    if code.get("conflicts"):
        lines.append(_words(agent, 'native.updates.170cc02ce3ab', 'Не легло (в новой версии стоит вариант выпуска): ') + "; ".join(
            f"{row.get('path')} — {row.get('why')}" for row in code["conflicts"][:10]
            if isinstance(row, dict)) + _words(agent, 'native.updates.a9d14eb780ad', '. Стороны и объяснение — {p0}/README.md.', p0=f"{code.get('folder')}"))
    if code.get("materials_error"):
        lines.append(code["materials_error"])
    return lines


@_pinned
def trial_note(receipt: dict, *, owner: str = "владелец", agent=None) -> str:
    """Записка испытания — словами владельца (04.10): одна строка, без простыни.

    Было пятнадцать строк чек-листа, и агент (Йоно, сервер Дмитрия) читал их
    вместо дела. Что проверять и как ответить, рука знает из своего описания —
    записка будит, а не инструктирует.
    """
    return (_words(agent, 'native.updates.eb5741147762', 'Системное сообщение: проверь обновление {p0} -> {p1} на работоспособность и совместимость со своими правками.', p0=f"{receipt.get('from_version') or '?'}", p1=f"{receipt.get('to_version') or '?'}"))


@_pinned
def report_note(receipt: dict, *, owner: str | None = None, agent=None) -> str:
    """Записка агенту: испытание (проверить себя) или итог (рассказать владельцу)."""
    if owner is None:owner=_words(agent,'native.updates.owner','владелец')
    state = str(receipt.get("state") or "")
    if receipt.get('kind')=='package':
        version=(_words(agent, 'native.updates.581c62b42f98', 'Hélène обновлена с {p0} до {p1}', p0=f"{receipt['from_version']}", p1=f"{receipt.get('to_version') or '?'}") if receipt.get('from_version')
                 else _words(agent, 'native.updates.778da321ac7b', 'В этой установке работает Hélène {p0}; прежняя версия здесь не записана', p0=f"{receipt.get('to_version') or '?'}"))
        return (_words(agent, 'native.updates.036179868fd3', 'Системное сообщение: {p0}. Осмотри новую версию: проверь доступные руки, память и совместимость со своими правками. Коротко расскажи владельцу, что проверил и какие проблемы нашёл. Это обновление системного пакета: автоматического испытания и отката здесь нет, решение об изменении пакета принимает владелец.', p0=f'{version}'))
    if state == "trial":
        return trial_note(receipt, owner=owner,agent=agent)
    head = {"done": _words(agent, 'native.updates.15d6718b600d', 'Обновление прошло'), "rolled_back": _words(agent, 'native.updates.bba1e965c145', 'Обновление не прошло и откачено'),
            "failed": _words(agent, 'native.updates.582f5ab61e6b', 'Обновление не прошло')}.get(state, _words(agent, 'native.updates.cad67be4ec64', 'Обновление закончилось'))
    plain = str(receipt.get("summary") or "")
    lines = [_words(agent, 'native.updates.0981e96433cc', '[Hélène · обновление] {p0}: {p1} → {p2}.', p0=f'{head}', p1=f"{receipt.get('from_version') or '?'}", p2=f"{receipt.get('to_version') or '?'}"),
             _words(agent, 'native.updates.1b72116244e1', 'Коротко для владельца: {p0}', p0=f'{plain}') if plain else "",
             _words(agent, 'native.updates.2bc061d0d9ba', 'Подробно: ') + str(receipt.get("note") or "") if plain else str(receipt.get("note") or ""),
             _checks_line(_words(agent, 'native.updates.5bbcc74110aa', 'Проверки {p0}: ', p0=f"{receipt.get('to_version') or _words(agent, 'native.updates.41b20eafb43a', 'новой версии')}"),
                          receipt.get("checks"))]
    trial = receipt.get("trial") or {}
    verdict = trial.get("verdict") or {}
    if verdict.get("verdict") == "timeout":
        lines.append(_words(agent, 'native.updates.46934808ebeb', 'На испытании ответа до срока не было — новая версия оставлена работать, откат не принудительный (04.10, слово владельца).'))
    elif verdict.get("verdict"):
        who = _words(agent, 'native.updates.bca5722b5743', 'ты') if verdict.get("by") == "agent" else _words(agent, 'native.updates.ff608915fa75', 'владелец')
        lines.append(_words(agent, 'native.updates.1883407b6ae6', 'Слово на испытании: {p0} — «{p1}»', p0=f'{who}', p1=f"{verdict.get('verdict')}")
                     + (f": {verdict.get('words')}" if verdict.get("words") else ""))
    rollback = receipt.get("rollback") or {}
    if rollback.get("notes"):
        lines.append(_words(agent, 'native.updates.12b269cfa8ee', 'Откат: ') + "; ".join(str(n) for n in rollback["notes"]) + "; "
                     + (_words(agent, 'native.updates.db52eac1d420', 'прежняя версия поднята и прошла проверки') if rollback.get("ok")
                        else _words(agent, 'native.updates.55e9580f2dda', 'прежняя версия проверки НЕ прошла')))
    if state == "done":
        lines += _code_lines(receipt,agent=agent)
        lines.append(_words(agent, 'native.updates.e02fa9aff734', 'Осмотри новую версию: проверь доступные руки, память и совместимость со своими правками. Обновление не ждёт твоей приёмки; о проблемах сообщи владельцу.'))
    chat = str((receipt.get("plan") or {}).get("chat") or "")
    confirmed = receipt.get("confirmed") or {}
    if confirmed.get("by"):
        who = _confirmation_label(agent,str(confirmed.get("by")))
        lines.append(_words(agent, 'native.updates.88bf1499aa61', '«Да» дал {p0}', p0=f'{who}')
                     + (f" — «{confirmed.get('words')}»" if confirmed.get("words") else ""))
    where = _words(agent, 'native.updates.d8e302ab93a3', ' — план был положен из «{p0}», туда и напиши', p0=f'{chat}') if chat else ""
    lines.append(_words(agent, 'native.updates.8487457612e1', 'Владелец — {p0}: расскажи об итоге коротко и простыми словами{p1}; технические подробности — только если о них спросят. Если не прошло — назови причину и что стоит сейчас; чинить ничего не надо.', p0=f'{owner}', p1=f'{where}'))
    return "\n".join(line for line in lines if line)
