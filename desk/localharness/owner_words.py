# -*- coding: utf-8 -*-
"""Имя владельца в текстах дерева: дерево говорит с Егором, издание — с любым владельцем.

Дерево — код Праксис, и его тексты честно называют её владельца по имени: «Yegor
trusts you», «Yegor's Windows computer», «только Егору», «Yegor asked you to answer
it». В издании владелец — тот, кого назвал мастер (`owner.name` в helene.json), и те же
строки читал бы Сергей или Мира: модель считала бы, что живёт у Егора. Живой случай
20.09; слово Егора — «трогай».

Как. Тем же приёмом, что `body.describe_for_mac`: правится ЗАГРУЖЕННЫЙ модуль дерева,
не файлы, подстрочно и идемпотентно. Только СТАТИЧЕСКИЕ тексты, написанные автором
дерева:
  * описания схем тулов (все списки схем + константы `*_TOOL`) и их английская
    проекция `tool_text_en.EN`;
  * указатели рук — `agent.HAND_PURPOSE` и `tool_text_en.POINTER_PURPOSE`;
  * шаблоны окон (`_HEARTBEAT_FRAME`, `_ABSENCE_FRAME`, …) — строки модуля;
  * авторские отрезки кадра — `contract.*` и `state.owner_place` — обёрткой
    `frame_trace.mark`, как это делает `body.owner_words_for_mac` для darwin;
  * рамка присутствия `_presence_frame` («with Yegor — your person») — обёрткой функции.
Динамика — досье, память, письма, реплики, содержимое комнат — НЕ трогается: там «Егор»
может быть настоящим третьим человеком, и подмена была бы ложью.

Отношения и история — разное. «Yegor trusts you» — про владельца, кто бы он ни был.
«because Yegor explicitly rejected that polluted diary» — факт о Егоре и Праксис,
приписать его Сергею нельзя: такие фразы `HISTORY_TEXT` переписывает без имени,
оставляя факт. Пример алиаса «'Yegor' to yegor-kosyrev» — про досье Егора, не трогаем.

Чего здесь нет (названо, чтобы не искать): тексты внутри рук дерева (отказы и
подсказки `tool_narrate`, `tool_home_note`, `tool_rest`, `tool_send_message`, …),
заголовок домашнего слоя памяти, `_mailbox_frame_block` и `outbound_privacy_frame` (в
издании этих механизмов нет), местоимения («his asks», «he says»). Стенд
`tests/t_owner_words.py` держит этот остаток ЯВНЫМ списком: новая строка с именем в
дереве краснит его по имени функции.

Если владелец — сам Егор (Yegor/Egor/Егор), не делается ничего: его издание байт в байт
как было.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("helene.owner_words")

#: Так дерево зовёт своего владельца. Владелец издания — он же → править нечего.
OWNER_ALIASES = frozenset({"yegor", "egor", "егор", "yegor kosyrev", "егор косырев",
                           "yegorkosyrev", "kosyrev", "косырев"})
#: Общие слова вместо имени: мастер оставил поле пустым (`boot.owner_name` → «владелец»).
GENERIC_NAMES = frozenset({"владелец", "owner", "the owner"})

#: Отрезки кадра с авторскими контрактами — их тексты правим. Всё остальное, что идёт
#: через `frame_trace.mark`, — содержимое (память, досье, реплики), его не трогаем.
OWNER_MARKS = frozenset({"state.owner_place"})
OWNER_MARK_PREFIXES = ("contract.",)

#: Шаблоны окон — строки модуля дерева; дерево читает их по имени на каждом окне.
WINDOW_TEMPLATES = ("_MAIL_COMPOSE_FRAME", "_ABSENCE_FRAME", "_HEARTBEAT_FRAME",
                    "_CODING_WINDOW_FRAME", "_REST_WINDOW_FRAME", "_TASK_WINDOW_BODY",
                    "_TASK_WINDOW_FRAME")

#: Функции дерева, чей вывод — авторская рамка без содержимого: заворачиваем целиком.
WRAPPED_FUNCTIONS = ("_presence_frame",)

#: Списки схем тулов у дерева — те же, что у `body._TOOL_LISTS`. Сверх них правятся все
#: константы модуля `*_TOOL` (DESCRIBE_TOOL/CALL_TOOL живут вне списков).
TOOL_LISTS = ("BASE_TOOLS", "OWNER_TOOLS", "PRAXIS_SELF_TOOLS", "SHARED_CONTEXT_TOOLS",
              "TOOLS", "ABSENCE_TOOLS", "FAMILY_TOOLS", "WORKSHOP_TOOLS", "FORGE_TOOLS")

#: Строки, где имя — не обращение к владельцу, а пример про досье Егора.
SKIP_IF_CONTAINS = ("yegor-kosyrev",)

#: История, а не отношения: снимаем имя, оставляя факт. Стенд сверяет каждую пару с
#: живым деревом — мёртвая пара краснеет по имени.
HISTORY_TEXT: tuple[tuple[str, str], ...] = (
    ("because Yegor explicitly rejected that polluted diary as a blueprint",
     "because that polluted diary was rejected as a blueprint"),
)

_EN_POSSESSIVE = re.compile(r"\b(?:Yegor|Egor)'s\b")
_EN_NAME = re.compile(r"\b(?:Yegor|Egor)\b")
#: Русские формы: именительный — имя владельца, косвенные — «владельца/владельцу/…»
#: (склонять чужое имя код не умеет и не пробует).
_RU_NAME = re.compile(r"(?<![А-Яа-яЁё])Егор(а|у|ом|е)?(?![А-Яа-яЁё])")
_RU_CASES = {"а": "владельца", "у": "владельцу", "ом": "владельцем", "е": "владельце"}


class OwnerWords:
    """Правила подстановки для одного владельца. Чистые, идемпотентные."""

    def __init__(self, name: str):
        self.name = " ".join(str(name or "").split())
        key = self.name.lower()
        self.active = bool(self.name) and key not in OWNER_ALIASES
        generic = key in GENERIC_NAMES
        self.en = "the owner" if generic else self.name
        self.ru = "владелец" if generic else self.name

    def say(self, text):
        """Текст словами этого владельца. Не строка или неактивно — как пришло."""
        if not self.active or not isinstance(text, str) or not text:
            return text
        if any(marker in text for marker in SKIP_IF_CONTAINS):
            return text
        for old, new in HISTORY_TEXT:
            text = text.replace(old, new)
        text = _EN_POSSESSIVE.sub(f"{self.en}'s", text)
        text = _EN_NAME.sub(self.en, text)
        return _RU_NAME.sub(self._ru, text)

    def _ru(self, m: "re.Match[str]") -> str:
        case = m.group(1)
        return self.ru if case is None else _RU_CASES[case]


def mark_is_owner_text(name) -> bool:
    """Этот отрезок кадра — авторский контракт (правим), а не содержимое (не трогаем)."""
    name = str(name or "")
    return name in OWNER_MARKS or any(name.startswith(p) for p in OWNER_MARK_PREFIXES)


def _wrapped_with(fn, flag: str) -> bool:
    """Стоит ли уже обёртка с этим флагом где-то в цепочке `__wrapped__`."""
    seen = 0
    while callable(fn) and seen < 16:
        if getattr(fn, flag, False):
            return True
        fn = getattr(fn, "__wrapped__", None)
        seen += 1
    return False


def _walk_descriptions(node, say, seen: set) -> int:
    """Подставить в каждом `description` схемы, на любой глубине. -> сколько строк изменено."""
    changed = 0
    if isinstance(node, dict):
        if id(node) in seen:
            return 0
        seen.add(id(node))
        for key, value in list(node.items()):
            if key == "description" and isinstance(value, str):
                new = say(value)
                if new != value:
                    node[key] = new
                    changed += 1
            else:
                changed += _walk_descriptions(value, say, seen)
    elif isinstance(node, list):
        for item in node:
            changed += _walk_descriptions(item, say, seen)
    return changed


def _walk_strings(node, say, seen: set) -> int:
    """Подставить во всех строковых значениях словаря/списка (любая глубина)."""
    changed = 0
    if isinstance(node, dict):
        if id(node) in seen:
            return 0
        seen.add(id(node))
        for key, value in list(node.items()):
            if isinstance(value, str):
                new = say(value)
                if new != value:
                    node[key] = new
                    changed += 1
            else:
                changed += _walk_strings(value, say, seen)
    elif isinstance(node, list):
        if id(node) in seen:
            return 0
        seen.add(id(node))
        for i, item in enumerate(node):
            if isinstance(item, str):
                new = say(item)
                if new != item:
                    node[i] = new
                    changed += 1
            else:
                changed += _walk_strings(item, say, seen)
    return changed


def _tool_dicts(agent_mod):
    """Все словари схем: из списков и из констант `*_TOOL` модуля."""
    for attr in TOOL_LISTS:
        lst = getattr(agent_mod, attr, None)
        if isinstance(lst, list):
            for tool in lst:
                if isinstance(tool, dict):
                    yield tool
    for attr in dir(agent_mod):
        if attr.endswith("_TOOL"):
            tool = getattr(agent_mod, attr, None)
            if isinstance(tool, dict):
                yield tool


def _wrap_marks(agent_mod, words: OwnerWords) -> bool:
    ft = getattr(agent_mod, "frame_trace", None)
    mark = getattr(ft, "mark", None)
    if not callable(mark) or _wrapped_with(mark, "_helene_owner"):
        return False

    def owner_mark(name, zone, kind, text, *args, **kwargs):
        if isinstance(text, str) and mark_is_owner_text(name):
            text = words.say(text)
        return mark(name, zone, kind, text, *args, **kwargs)

    owner_mark._helene_owner = True
    owner_mark.__wrapped__ = mark
    owner_mark.__name__ = getattr(mark, "__name__", "mark")
    owner_mark.__doc__ = getattr(mark, "__doc__", "")
    ft.mark = owner_mark
    return True


def _wrap_functions(agent_mod, words: OwnerWords) -> int:
    done = 0
    for fname in WRAPPED_FUNCTIONS:
        fn = getattr(agent_mod, fname, None)
        if not callable(fn) or _wrapped_with(fn, "_helene_owner"):
            continue

        def wrapped(*args, _fn=fn, **kwargs):
            out = _fn(*args, **kwargs)
            return words.say(out) if isinstance(out, str) else out

        wrapped._helene_owner = True
        wrapped.__wrapped__ = fn
        wrapped.__name__ = fname
        wrapped.__doc__ = getattr(fn, "__doc__", "")
        setattr(agent_mod, fname, wrapped)
        done += 1
    return done


def install(agent_mod, cfg: dict | None, name: str | None = None) -> dict:
    """Все авторские тексты дерева — словами владельца издания. -> что тронуто.

    Зовётся из раннера ПОСЛЕ импорта дерева (и после `body.install`, чья обёртка
    `frame_trace.mark` для darwin встаёт первой; порядок обёрток безразличен).
    Повтор безвреден: строки уже переведены, обёртки не удваиваются.
    """
    if name is None:
        import boot  # noqa: PLC0415 — соседний модуль харнесса
        name = boot.owner_name(cfg or {})
    words = OwnerWords(name)
    report = {"owner": words.name, "active": words.active, "schemas": 0, "dicts": 0,
              "templates": 0, "marks": False, "functions": 0}
    if not words.active:
        log.info("тексты дерева: владелец — %s, дерево зовёт его так же; не правлю",
                 words.name or "не назван")
        return report
    seen: set = set()
    for tool in _tool_dicts(agent_mod):
        report["schemas"] += _walk_descriptions(tool, words.say, seen)
    en_mod = getattr(agent_mod, "tool_text_en", None)
    for table in (getattr(agent_mod, "HAND_PURPOSE", None),
                  getattr(en_mod, "POINTER_PURPOSE", None),
                  getattr(en_mod, "EN", None)):
        if isinstance(table, dict):
            report["dicts"] += _walk_strings(table, words.say, seen)
    for attr in WINDOW_TEMPLATES:
        value = getattr(agent_mod, attr, None)
        if isinstance(value, str):
            new = words.say(value)
            if new != value:
                setattr(agent_mod, attr, new)
                report["templates"] += 1
    report["marks"] = _wrap_marks(agent_mod, words)
    report["functions"] = _wrap_functions(agent_mod, words)
    log.info("тексты дерева — владельцу по имени: %s", report)
    return report
