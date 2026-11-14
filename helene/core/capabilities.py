"""
Praxis — честное знание себя в моменте (PASS 8.3).

Лечит класс «видимо, ты поднял лимит» — конфабуляции о собственной механике. Снимок
собирается КОДОМ, не сочиняется моделью: тулы по скоупам (с живыми гейтами web_search /
почта), мозг (llm.snapshot), лимиты, зоны автономии (selfdev.policy), список скиллов из
INDEX.md, разделы пульта, и «что закрыто и почему».

Выходы:
  * тул `my_capabilities()` (BASE, любой скоуп) — отвечает АУДИТОРИЯ-ЧЕСТНО: в группе
    описывает доступные read-only глаза и закрытые side effects, не светит ключи/мозг/панель;
  * `state_line()` — одна строка в STATE (owner tier-0);
  * кэш по mtime зависимостей (INDEX.md, llm.json, policy) + env-гейты — сборка дешёвая,
    модель здесь НЕ зовётся вообще.

agent импортируется лениво (внутри функций) — иначе цикл agent <-> capabilities.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import hands
import forge
import llm
import mailer
import rails
import resident_skills
import frame_language
import selfdev
import work_loop

log = logging.getLogger("praxis-capabilities")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
SKILLS_INDEX = BASE / "soul" / "skills" / "INDEX.md"

# Хаб пульта — КОД, лежащий рядом с нами, а не данные под PRAXIS_BASE: в любом
# окружении с отдельным каталогом данных (тестовая база, будущий split) BASE-путь дал бы
# «разделы не прочитались» на ровном месте, и она читала бы «не знаю» вместо правды.
PANEL_HUB = Path(__file__).resolve().parent / "panelapp.html"

_CACHE: dict = {"fp": None, "snap": None}


def _panel_sections() -> list[str]:
    """Разделы пульта — из живого хаба panelapp.html, а не из списка, набранного руками.

    ⚠ 28.07: здесь лежал статичный кортеж из 15 названий, и он отстал на пять плиток —
    «Обзор», «Граф памяти», «Я», «Восприятие» и «Комнаты». Последняя важнее прочих: это
    ровно тот раздел, где Егор одним нажатием переключает disclosure её комнаты, то есть
    меняет её собственный голос. Она читала строку «пульт Егора: …» и не знала, что такой
    раздел существует. Список, который надо помнить руками, врёт не «если», а «когда»;
    спрашиваем у файла, который этот хаб рисует.
    """
    import re
    try:
        text = PANEL_HUB.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    block = re.search(r"const\s+TILES\s*=\s*\[(.*?)\n\];", text, re.S)
    if not block:
        return []
    return [m.group(1).strip() for m in re.finditer(r'nm:\s*"([^"]+)"', block.group(1))]


def _perception_value(knob: str, fallback: float) -> float:
    """PASS 21: живые рычаги восприятия — честное значение, не замороженный env."""
    try:
        import perception
        return float(perception.value(knob))
    except Exception:
        return fallback


def _web_search_on() -> bool:
    """Тот же честный gate, по которому hosted tool прикладывается к voice."""
    return llm.web_search_available()


def _hostview_on() -> bool:
    """PASS 17.A: открыты ли её глаза на хост (токен обсерватории — рычаг Егора в .env)."""
    try:
        import hostview
        return hostview.available()
    except Exception:
        return False


def _hostview_state() -> str:
    try:
        import hostview
        return hostview.state_line()
    except Exception:
        return frame_language.text('owned.capabilities.be690716c266')


def _services_state() -> str:
    """PASS 17.B: до кого достают её руки на уровне машины (снимок кодом, не память)."""
    try:
        import services
        can = [s.name for s in services.SERVICES.values() if s.kind == "signal"]
        return (frame_language.text('owned.capabilities.469f996fc5c3', v0=', '.join(can), v1=services.RESTARTS_PER_HOUR))
    except Exception:
        return frame_language.text('owned.capabilities.2401207e7101')


def _hostops_state() -> str:
    """Retired request queue is not a capability; name the actual host routes."""
    return frame_language.text('owned.capabilities.447b1153ebc9')


def _serverd_state() -> str:
    """PASS 23.2: полная хозяйка хоста через root-демон (если смонтирован)."""
    try:
        import serverd_client
        if not serverd_client.available():
            return frame_language.text('owned.capabilities.957f9a4ac393')
        line = serverd_client.state_line()
        return (frame_language.text('owned.capabilities.a5353668bdde', v0=line or frame_language.text('owned.capabilities.69aadea8228b')))
    except Exception:
        return frame_language.text('owned.capabilities.0afc2a1775b1')


def _windows_body_state() -> str:
    """PASS 30 Этап 3: Windows — прямое тело, wcode-скважина deprecated."""
    try:
        import body_client
        if not body_client.available():
            return frame_language.text('owned.capabilities.7565407e8730')
        return (body_client.state_line() + frame_language.text('owned.capabilities.d61aff83807a'))
    except Exception:
        return frame_language.text('owned.capabilities.d372dc98467b')


def _room_tool_spec() -> dict:
    """Схема manage_room из тех же констант, что раздают руки в ходе (не по памяти)."""
    import agent
    for group in (getattr(agent, "PRAXIS_SELF_TOOLS", ()), getattr(agent, "OWNER_TOOLS", ()),
                  getattr(agent, "BASE_TOOLS", ())):
        for tool in group:
            if isinstance(tool, dict) and tool.get("name") == "manage_room":
                return tool
    return {}


def _room_levers_line() -> str:
    """Что я вправду выставляю в комнате — по СХЕМЕ тула, которым и зову.

    28.07, решение Егора «да, давать»: режим комнаты и disclosure перестают быть чужими
    рычагами. Но написать здесь прозой «у меня есть mode и disclosure» значит завести
    второй пересказ факта, который живёт в схеме тула, — а два пересказа расходятся, это
    в доме уже стоило манифесту двух переписываний за сутки. Поэтому строка собирается из
    самой схемы: чего в ней нет, того у меня нет, и строка скажет это вслух, а не промолчит.

    Почему disclosure вообще обязан быть здесь: он меняет МОЙ голос — в комнате с open к
    моей визитке подмешивается проверяемая фактура (agent._disclosure_open_extras), — а
    узнать о переключении мне было неоткуда: машинная шапка профиля в промпт не течёт,
    слова disclosure не было ни в capabilities, ни в манифесте рельсов.
    """
    try:
        props = dict(((_room_tool_spec().get("input_schema") or {}).get("properties") or {}))
        actions = [str(a) for a in ((props.get("action") or {}).get("enum") or [])]
    except Exception:
        log.debug("схема manage_room не прочиталась", exc_info=True)
        return (frame_language.text('owned.capabilities.20acd03db755'))
    if not props:
        return frame_language.text('owned.capabilities.04f6f6b69d4b')

    def _enum(name: str) -> str:
        vals = [str(v) for v in ((props.get(name) or {}).get("enum") or [])]
        return "/".join(vals)

    bits = [f"manage_room ({' | '.join(actions) or frame_language.text('owned.capabilities.d85b8d024d47')})"]
    if "mode" in props:
        bits.append(frame_language.text('owned.capabilities.ecf5316a79fc') + (f" ({_enum('mode')})" if _enum("mode") else "")
                    + frame_language.text('owned.capabilities.08fa1d6ed9dc'))
    else:
        bits.append(frame_language.text('owned.capabilities.aa39d5f66926'))
    if "disclosure" in props:
        bits.append("disclosure" + (f" ({_enum('disclosure')})" if _enum("disclosure") else "")
                    + frame_language.text('owned.capabilities.2ba7c181c407'))
    else:
        bits.append(frame_language.text('owned.capabilities.61ba23423295'))
    # Что из схемы — про глубину контекста, спрашиваем у rooms (единственное место, где
    # этот набор задан), а не делим поля на глаз: «всё остальное = контекст» уже назвало
    # бы контекстом reason и ttl_h, которые ходят при режиме.
    try:
        import rooms as _rooms
        policy_keys = set(_rooms.default_policy() or {})
    except Exception:
        log.debug("набор полей политики комнаты не прочитался", exc_info=True)
        policy_keys = set()
    deep = sorted(k for k in props if k in policy_keys)
    rest = sorted(k for k in props
                  if k not in policy_keys and k not in ("action", "chat_id", "mode", "disclosure"))
    if deep:
        bits.append(frame_language.text('owned.capabilities.1572168dacff') + ", ".join(deep))
    if rest:
        bits.append(frame_language.text('owned.capabilities.49877ff3a6fe') + ", ".join(rest))
    return frame_language.text('owned.capabilities.2f48e92f1280') + "; ".join(bits)


def _room_levers_brief() -> str:
    """Короткая форма тех же рычагов для НЕ-owner канала — «» если их в схеме нет.

    Знание о себе не должно зависеть от того, кто слушает: полный ответ owner-ветки
    в группе не показывают (там нет ни ключей, ни мозга, ни пульта), и из-за этого в
    комнате, где режим как раз и нужен, она о собственном рычаге не читала ничего.
    Пульт здесь не называем — граница публичной ветки остаётся прежней.
    """
    try:
        props = ((_room_tool_spec().get("input_schema") or {}).get("properties") or {})
    except Exception:
        log.debug("схема manage_room не прочиталась (краткая форма)", exc_info=True)
        return ""
    bits = []
    if "mode" in props:
        bits.append(frame_language.text('owned.capabilities.3eef8e64a7f5'))
    if "disclosure" in props:
        bits.append(frame_language.text('owned.capabilities.f15b814b14fa'))
    return "; ".join(bits)


# Гейт по АУДИТОРИИ в теле обработчика — «здесь нельзя, иди в ЛС Егора». Гейт по автору
# выглядит иначе и зовётся иначе (_is_sovereign_actor / _is_praxis_self), поэтому ищем
# именно первую форму и только когда второй в функции нет.
_AUDIENCE_GATE_MARKS = ('_active_scope() != "owner"', "_active_scope() != 'owner'")
_PRINCIPAL_MARKS = ("_is_sovereign_actor", "_is_praxis_self")
_TEMPO_TOOLS = ("manage_perception", "switch_brain")


def _audience_gated_branches(src: str) -> list[str]:
    """Ветки обработчика, где ключ стоит по АУДИТОРИИ. -> человеческие приметы этих веток.

    ⚠ Первая редакция считала по функции ЦЕЛИКОМ: `любой audience-маркер И ни одного
    принципал-маркера`. Тул обрабатывает четыре действия — достаточно вернуть гейт по
    скоупу в одну ветку (set), оставив принципала в другой (reset), и строка по-прежнему
    говорила бы мне «стоят по АВТОРУ хода». Строка, которая обещает читать код, а читает
    его наполовину, опаснее прозы: ей верят. Считаем по УСЛОВИЮ ветки.
    """
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(src))
    bad: list[str] = []
    inside_test = False
    for node in ast.walk(tree):
        test = getattr(node, "test", None)
        if test is None:
            continue
        try:
            expr = ast.unparse(test)
        except Exception:
            continue
        if not any(m in expr for m in _AUDIENCE_GATE_MARKS):
            continue
        inside_test = True
        if not any(m in expr for m in _PRINCIPAL_MARKS):
            bad.append(" ".join(expr.split())[:120])
    try:
        # Комментарии и старые формулировки в них — не код: в теле manage_perception
        # прямо описано, что здесь СТОЯЛ гейт по аудитории, и по сырому тексту строка
        # объявляла бы забор, которого в коде нет. Спрашиваем разобранный код.
        code_only = ast.unparse(tree)
    except Exception:
        code_only = src
    if not bad and not inside_test and any(m in code_only for m in _AUDIENCE_GATE_MARKS):
        # Маркер аудитории в теле, но ни в одном условии: прочитать ветку не вышло.
        # Молчать об этом нельзя — «не знаю» обязано выглядеть как «не знаю».
        bad.append(frame_language.text('owned.capabilities.dbb8611f2d18'))
    return bad


def _tempo_levers_line() -> str:
    """Мои рычаги темпа: заперты они по аудитории или стоят по автору — читаем обработчики.

    27–28.07: manage_perception и switch_brain лежат в BASE_TOOLS, то есть предлагаются мне
    в ЛЮБОМ канале, а внутри отказывали всему, что не owner-скоуп. Смысл выходил обратный
    нужному: замедлить себя я могла только там, где меня слушает Егор, — и не могла в
    группе, которая меня заливает. Решение Егора 28.07 — «да, давать»: ключ переводится на
    ПРИНЦИПАЛА (тот же, что уже сделан для manage_desire).

    Утверждать это прозой нельзя: правку делает владелец agent.py, и если она не доедет,
    строка станет ложью того же класса, что рельс conation_authorship, который сутки
    обещал мне право, которого в коде не было. Поэтому спрашиваем сами функции.
    """
    import inspect
    try:
        import agent
        gated: list[str] = []
        for name in _TEMPO_TOOLS:
            fn = (getattr(agent, "TOOL_IMPL", {}) or {}).get(name)
            if fn is None:
                gated.append(frame_language.text('owned.capabilities.0af9213840f2', v0=name))
                continue
            branches = _audience_gated_branches(inspect.getsource(fn))
            if branches:
                gated.append(f"{name} ({'; '.join(branches[:2])})")
    except Exception:
        log.debug("проверка гейтов темпа упала", exc_info=True)
        return (frame_language.text('owned.capabilities.4f978915a8e9'))
    if not gated:
        return (frame_language.text('owned.capabilities.c33100ea385b'))
    return (frame_language.text('owned.capabilities.7f0cd20ef723', v0=', '.join(gated)))


def _human_only_line(snap: dict | None = None) -> str:
    """Что из owner-набора НЕ моё: доверие человеку и раздача доступа к его ПК.

    ⚠ Первая редакция читала `agent._HUMAN_OWNER_ONLY_TOOL_NAMES` — и была права только
    наполовину: под mail-гейтом у owner был ещё и `send_email`, которого в этом frozenset
    нет. 03.08.2026 само расхождение снято решением Егора (send_email стал общей рукой),
    но правило остаётся в силе: считаем РАЗНИЦЕЙ двух наборов, снятых кодом в одном
    снимке. Копия объявленного множества однажды снова разойдётся с поведением —
    разница не может.
    """
    s = snap if snap is not None else snapshot()
    tools = s.get("tools") or {}
    if "self" not in tools or "owner" not in tools:
        return frame_language.text('owned.capabilities.38b98f58b6e2')
    extra = sorted(set(tools["owner"]) - set(tools["self"]))
    return ", ".join(extra) or frame_language.text('owned.capabilities.9ca0ffb64c9b')


def _rails_manifest_line() -> str:
    """Свежесть манифеста рельсов — дёшево, без единого RPC.

    soul/rails.md не входит в системный промпт и не индексируется в recall: он доходит
    до неё только по её инициативе. Значит хотя бы факт «манифест есть, и он (не)отстал»
    обязан лежать в STATE, который она видит всегда. Полный rails.state_line() сюда
    ставить нельзя — он читает значения рельсов и ходит к serverd, а тот имеет право
    молчать минутами прямо в сборке ответа.
    """
    try:
        count = len(rails.registry(with_values=False))
        return frame_language.text('owned.capabilities.cc5e1a8fd11a', v0=count, v1=rails.manifest_state(brief=True))
    except Exception:
        return ""


def _outbound_stop_word() -> str:
    """Короткая правда для STATE: что вправду может остановить её исходящее слово.

    Егор 10.11 («сними»): судьи приватности и пола приватных записей на выходе нет; если
    проверку вернут, это покажет рельс outbound_privacy (он считает код, а не пересказывает).
    """
    return frame_language.text('owned.capabilities.a2b907262703')


def _fingerprint() -> str:
    """mtime зависимостей + env-гейты: любая правка INDEX/llm.json/policy сбрасывает кэш."""
    web_search = _web_search_on()
    parts = []
    # panelapp.html здесь потому, что список разделов пульта теперь читается из него:
    # новая плитка у Егора обязана доехать до неё без рестарта, иначе снимок опять начнёт
    # отставать — только уже молча, через кэш.
    # Каталог навыков — тоже зависимость: новый или удалённый файл навыка не трогает mtime
    # INDEX, и без этой строки снимок мог показывать прежний список до правки индекса.
    deps = [SKILLS_INDEX, SKILLS_INDEX.parent, llm.CONFIG_PATH, selfdev.POLICY_FILE, PANEL_HUB]
    try:  # PASS 21: её живые рычаги восприятия — смена сбрасывает кэш возможностей
        import perception
        deps.append(perception.STATE_PATH)
    except Exception:
        pass
    for p in deps:
        try:
            parts.append(str(p.stat().st_mtime_ns))
        except OSError:
            parts.append("0")
    parts.append("res=" + resident_skills.fingerprint(SKILLS_INDEX.parent))
    parts.append("resmax=" + str(resident_skills.max_chars()))
    parts.append("ws=" + ("1" if web_search else "0"))
    parts.append("wsb=" + (llm.web_search_backend() or "none"))
    # Risk classification affects the snapshot; it never narrows merge authority.
    parts.append("fs=" + (os.getenv("PRAXIS_FLOOR_SKIP") or ""))
    parts.append("hands=" + ("1" if hands.available() else "0"))
    parts.append("eyes=" + ("1" if _hostview_on() else "0"))      # 17.A: глаза на сервер
    try:
        import webtool
        parts.append("wh=" + ("1" if webtool.enabled() else "0"))
    except Exception:
        parts.append("wh=0")
    try:
        parts.append("mail=" + ("1" if mailer.configured() else "0"))
    except Exception:
        parts.append("mail=0")
    try:  # PASS 30 Этап 3: доступность Windows-тела видна в прозе — флаг сбрасывает кэш
        import body_client
        parts.append("body=" + ("1" if body_client.available() else "0"))
    except Exception:
        parts.append("body=0")
    return "|".join(parts)


NO_PURPOSE = "назначение не написано"


def _skills_list(index_path: Path | None = None) -> list[dict]:
    """Список навыков = ДИСК. INDEX.md даёт только описания, и только там, где совпал адрес.

    ЕЁ РЕШЕНИЕ 13.08.2026, дословно: «Ни один навык не должен исчезать из capability
    snapshot из-за regex. Нужны равенство списка с диском, slug как адрес, и явное
    "назначение не написано" там, где я сама его ещё не сформулировала. Сами назначения
    навыков — мои слова; я напишу их, когда буду разбирать навыки содержательно, не когда
    чинится парсер.»

    ЧТО БЫЛО СЛОМАНО. Список собирался ДВУМЯ регулярками по `INDEX.md`: строка таблицы и
    буллит. Навык, чья строка написана иначе — или которого в индексе нет вовсе, — исчезал
    из снимка возможностей молча. То есть её самоописание зависело от формы markdown, а не
    от того, что у неё есть.

    ЧТО ТЕПЕРЬ. Имя навыка — `slug` файла: это его адрес, по нему он читается и находится
    recall. Описание берётся из индекса по ЦЕЛИ ССЫЛКИ (`skill.md`), а не по видимому
    тексту: подпись можно переписать, адрес — нет. Нет описания — так и написано, и это
    приглашение ей, а не дефект.

    Обратное расхождение (индекс называет то, чего на диске нет) не молчит тоже:
    `orphans` в снимке. Врать в обе стороны одинаково нельзя.
    """
    import re
    index = Path(index_path) if index_path is not None else SKILLS_INDEX
    described: dict[str, str] = {}
    titles: dict[str, str] = {}
    named: set[str] = set()
    try:
        text = index.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    # Обе прежние формы остаются понятными, но ключ теперь — файл, а не подпись.
    for pattern in (r"^\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|\s*([^|]+)\|",
                    r"^-\s*\[([^\]]+)\]\(([^)]+)\)(?:\s*[—–-]\s*(.*))?$"):
        for m in re.finditer(pattern, text, re.M):
            slug = str(m.group(2) or "").rsplit("/", 1)[-1].removesuffix(".md").strip()
            if not slug:
                continue
            named.add(slug)
            title = str(m.group(1) or "").strip()
            if title and slug not in titles:
                titles[slug] = title
            line = (m.group(3) or "").strip()
            if line and slug not in described:
                described[slug] = line
    try:
        on_disk = sorted(p.stem for p in index.parent.glob("*.md")
                         if p.name != index.name)
    except OSError:
        on_disk = []
    out = [{"name": slug, "title": titles.get(slug, slug),
            "line": described.get(slug) or frame_language.text('skills.no_purpose')} for slug in on_disk]
    for slug in sorted(named - set(on_disk)):
        out.append({"name": slug, "title": titles.get(slug, slug),
                    "line": frame_language.text('skills.orphan'),
                    "orphan": True})
    # 14.11: постоянная часть навыка (soul/skills/<slug>/resident.md) стоит в кадре каждого
    # хода, папка навыка — материалы по потребности. Ключи появляются только там, где это
    # правда, как и `orphan`: у обычного навыка строка прежняя.
    _, resident_rows = resident_skills.render(index.parent)
    by_name = {row["name"]: row for row in out}
    for res in resident_rows:
        row = by_name.get(res["slug"])
        if row is None:
            if res.get('source') == 'kit' and res.get('skill_file'):
                row = {'name': res['slug'], 'title': res['slug'], 'source': 'kit',
                       'path': res['skill_path'], 'line': frame_language.text('skills.kit')}
            else:
                row = {"name": res["slug"], "title": res["slug"],
                       "line": frame_language.text('skills.resident_orphan'), "orphan": True}
            out.append(row)
            by_name[res["slug"]] = row
        row["resident"] = {"path": res["path"], "chars": res.get("chars", 0),
                           "in_frame": bool(res.get("in_frame")),
                           **({"disabled": True} if res.get("disabled") else {}),
                           **({"source": res["source"]} if res.get("source") else {}),
                           **({"refused": res["refused"]} if res.get("refused") else {})}
        if res.get("materials"):
            parent = res['path'].rsplit('/', 1)[0]
            row["materials"] = [f"{parent}/{name}" for name in res["materials"]]
    return out


@frame_language.pinned
def skill_navigation(index_path: Path) -> str:
    """Owner-only locator over the actual files; INDEX supplies titles, never existence.

    This is an internal map, not the body of a skill or an outbound disclosure.
    The caller owns the audience check. Missing index text cannot erase disk paths.
    """
    index = Path(index_path)
    rows = _skills_list(index)
    if not index.parent.is_dir() and not rows:
        return frame_language.text('skills.unreadable')
    header = frame_language.text('skills.no_index' if not index.is_file() else 'skills.navigation')
    lines = [header]
    for row in rows:
        slug = row["name"]
        path = row.get('path') or f"soul/skills/{slug}.md"
        if row.get("orphan"):
            lines.append(frame_language.text('skills.orphan_row', title=row['title'], path=path))
        else:
            lines.append(f"- {row['title']} · {path} — {row['line']}")
    return "\n".join(lines)


@frame_language.pinned
def snapshot() -> dict:
    """Полный снимок возможностей (owner-взгляд). Кэш по отпечатку; модель не зовётся."""
    fp = _fingerprint()
    if _CACHE["fp"] == fp and _CACHE["snap"] is not None:
        return _CACHE["snap"]
    import agent  # лениво: агент импортирует нас
    base_tools = [t["name"] for t in (agent.BASE_TOOLS + agent.SHARED_CONTEXT_TOOLS)
                  if isinstance(t, dict) and t.get("name")]
    # ⚠ 15.08. Ровно та разница, о которой предупреждает комментарий ниже, — и я в неё
    # въехал в тот же день, когда его прочитал. Рука `reply` лежит в BASE_TOOLS, но при
    # опущенном рычаге `PRAXIS_CHAT_REPLY_HAND` она вырезается из набора, который реально
    # раздаётся ходу. Снимок при этом продолжал называть её моей — то есть моё же
    # самоописание обещало руку, которой у меня в этом ходе нет. Условие здесь обязано
    # быть тем же самым, что в раздаче рук, а не его копией по смыслу.
    if not work_loop.reply_hand_enabled():
        base_tools = [name for name in base_tools if name not in ("reply", "end_turn")]
    owner_tools = [t["name"] for t in agent.OWNER_TOOLS if isinstance(t, dict) and t.get("name")]
    # ⚠ Её собственные руки — ОТДЕЛЬНЫЙ набор, а не owner-набор с другой подписью.
    # `agent.offered_tools_for` в не-owner ходе выдаёт PRAXIS_SELF_TOOLS (= owner минус
    # admit/computer_access), и пока снимок знал только owner-список, самоописание
    # называло «моими в любом канале» ровно два тула, которых в таком ходе нет. Считаем
    # оба набора из тех же констант, что раздают руки, — иначе сюда снова заедет разница.
    self_tools = [t["name"] for t in agent.PRAXIS_SELF_TOOLS
                  if isinstance(t, dict) and t.get("name")]
    gated = {"web_search": _web_search_on(),
             "web_search_backend": llm.web_search_backend()}
    try:  # PASS 15: клиентские веб-руки (web_read/web_find) — не зависят от фреймворка
        import webtool
        gated["web_hands"] = bool(webtool.enabled())
    except Exception:
        gated["web_hands"] = False
    try:
        gated["mail"] = bool(mailer.configured())
    except Exception:
        gated["mail"] = False
    if gated["mail"]:
        # 03.08.2026 (решение Егора, вариант «а»): send_email больше не owner-эксклюзив.
        # Разбивка ровно та же, что раздаёт руки в ходе (agent.py, mail-гейт), — и это
        # обязано оставаться так: снимок описывает раздачу, а не повторяет её по памяти.
        mail_hands = ["send_email", "mail_read", "mail_draft_reply"]
        owner_tools = owner_tools + mail_hands
        self_tools = self_tools + mail_hands
    lim = llm.limits()
    snap = {
        "tools": {"base": base_tools, "owner": sorted(set(owner_tools)),
                  "self": sorted(set(self_tools)), "gates": gated},
        "brain": llm.snapshot(),
        "limits": {
            "max_tool_iters": lim.max_tool_iters,
            "cooldown_dm_sec": _perception_value("cooldown_dm", 8.0),
            "cooldown_group_sec": _perception_value("cooldown_group", 300.0),
            "history_turns": agent.HISTORY_TURNS,
            "context_budget_chars": int(os.getenv("PRAXIS_CONTEXT_BUDGET", "0") or 0),
        },
        "autonomy": {"auto": (selfdev.policy().get("auto") or []),
                     "protected": list(selfdev.floor()),
                     "merge_mode": selfdev.merge_mode()},
        # Снимок КОДОМ фактического outbound-конвейера.
        "reflexes": {
            # Егор 10.11 («сними»): судья приватности и пол приватных записей удалены;
            # на всех дверях наружу держит только механический кред-пол.
            "evaluator": frame_language.text('owned.capabilities.e0904c61ad36') + _outbound_stop_word(),
            "rewrite": frame_language.text('owned.capabilities.a730c69d1b4e'),
            "authorship": frame_language.text('owned.capabilities.9dc267aaa2f1'),
            "mine": frame_language.text('owned.capabilities.39ff5ce8f37a'),
            "room_policy": _room_levers_line(),  # 28.07: режим комнаты и disclosure — её же
            "tempo": _tempo_levers_line(),       # 28.07: рычаги темпа по автору, не по аудитории
            "hands": hands.state_line(),
            "coding": (frame_language.text('owned.capabilities.2a21694e274f')),
            "forge_events": (frame_language.text('owned.capabilities.3edd9e7587fc')),
            "narration": (frame_language.text('owned.capabilities.547c93f131cd')),
            "server_eyes": _hostview_state(),     # 17.A: первая ступень «хозяйки сервера»
            "server_hands": _services_state(),    # 17.B: вторая — свои сервисы
            "host_edits": _hostops_state(),       # 17.C: третья — заявки на правку хоста
            "host_master": _serverd_state(),      # 23.2: root-демон — полная хозяйка хоста
            "windows_body": _windows_body_state(),  # PASS 24: локальные руки без второго мозга
        },
        "skills": _skills_list(),
        "panel": _panel_sections(),
        "closed": {
            # 10.11 (Егор, «сними»): советника на голосе больше нет — граница раскрытия
            # чужого личного держится только её суждением, механически — один кред-пол.
            "group": (frame_language.text('owned.capabilities.10770afa621c')),
            # ⚠ Здесь стояло «в самом ходе нет произвольного shell/Forge/root…» — и это
            # проверяемое утверждение о РАНЕ, которое было ложным всякий раз, когда в
            # группе ко мне обращался Егор: руки в ход выдаются по actor (ctx.owner),
            # а этот текст описывал audience. Теперь строка говорит только то, о чём
            # действительно знает: чьи команды я исполняю в этом канале. Что у меня в
            # руках — отдельная строка, и она собирается из фактически предложенного
            # списка, а не из статических списков модуля.
            "non_owner": (frame_language.text('owned.capabilities.0f6923cb08c2')),
            "llm_json": frame_language.text('owned.capabilities.8592e5829baa'),
        },
    }
    _CACHE.update(fp=fp, snap=snap)
    return snap


def _resident_line(skills: list[dict]) -> str:
    """Строка о постоянных навыках: что стоит в кадре каждого хода и где материалы.

    Механизм называется и тогда, когда постоянных навыков нет: иначе о нём не узнать."""
    rows = [x for x in skills if isinstance(x.get("resident"), dict)]
    how = frame_language.text('skills.resident_how',
            cap=resident_skills.max_chars() or frame_language.text('skills.unlimited'))
    if not rows:
        return frame_language.text('skills.resident_none', how=how)
    shown = []
    for x in rows:
        res = x["resident"]
        state = (frame_language.text('skills.in_frame') if res.get("in_frame")
                 else frame_language.text('skills.disabled') if res.get('disabled')
                 else frame_language.text('skills.not_in_frame',
                      reason=res.get('refused') or frame_language.text('skills.over_cap')))
        bit = frame_language.text(
            'skills.resident_row',
            name=x['name'],
            state=state,
            chars=res.get('chars', 0),
            purpose=x.get('line') or frame_language.text('skills.no_purpose')
        )
        if x.get("materials"):
            bit += frame_language.text('skills.materials', paths=", ".join(x["materials"]))
        shown.append(bit)
    return frame_language.text('skills.resident_list', rows=" | ".join(shown), how=how)


@frame_language.pinned
def describe(scope: str = "owner", *, offered: list[str] | None = None) -> str:
    """Текст для my_capabilities: аудитория и руки — РАЗНЫЕ измерения (контракт A1).

    scope — кто слушает (граница раскрытия и чьи команды я исполняю).
    offered — имена тулов, фактически предложенных модели в ЭТОМ ходе
    (`agent.offered_tools_for`). Когда он передан, руки описываются по нему, а не по
    статическим спискам модуля: только так ответ относится к текущему рану.
    None — вызов вне хода (пульт, тесты): тогда о руках честно молчим."""
    s = snapshot()
    g = s["tools"]["gates"]
    if scope == "owner":
        # ⚠ 28.07: max_tokens лежал в снимке с самого начала и не печатался нигде — ни
        # здесь, ни в llm.state_line(). Это потолок ОДНОГО моего ответа (голос 1024), и
        # обрыв по нему llm называет постфактум — то есть про предел я узнавала только в
        # момент удара. Молчаливых пределов не бывает: называем заранее.
        def _brain_bit(role: str, b: dict) -> str:
            cap = b.get("max_tokens")
            line = f"{role}: {b['model']} ({b['framework']}, protocol={b.get('protocol') or llm._protocol(b['framework'])}"
            line += frame_language.text('owned.capabilities.7ce8c012f435', v0=int(cap)) if cap else frame_language.text('owned.capabilities.692282e3aa4b')
            fallback = str(b.get("fallback_framework") or "")
            route = (f"{fallback} (protocol={llm._protocol(fallback)})/"
                     f"{b.get('fallback_model') or '—'}") if fallback else frame_language.text('owned.capabilities.e7fceb4fc573')
            if b.get("on_fallback"):
                return line + frame_language.text('owned.capabilities.dc2af6dfb509', v0=route)
            if b.get("fallback_armed"):
                return line + frame_language.text('owned.capabilities.6f0d3bb8548b', v0=route)
            return line + frame_language.text('owned.capabilities.7eaa02e96ca2', v0=route)

        brain = ", ".join(_brain_bit(r, b) for r, b in s["brain"].items())
        lim = s["limits"]
        skills = ", ".join(x["name"] for x in s["skills"]) or "—"
        resident = _resident_line(s["skills"])
        try:  # 9.1: расход — живой, мимо кэша снапшота (меняется каждым вызовом)
            spent = llm.usage_line()
        except Exception:
            spent = ""
        return (
            frame_language.text('owned.capabilities.353a70ce9a31', v0=brain, v1=lim['max_tool_iters'])
            + (frame_language.text('owned.capabilities.9fb421a99bac', v0=spent) if spent else "") +
            frame_language.text(
                'owned.capabilities.32954586a892',
                v0=', '.join(s['tools']['base']),
                v1=', '.join(s['tools']['self']),
                v2=_human_only_line(s),
                v3=frame_language.text('owned.capabilities.c313708107b7') + str(g.get('web_search_backend')) + ')' if g['web_search'] else frame_language.text('owned.capabilities.b2a25ae548d2'),
                v4=frame_language.text('owned.capabilities.2923588c6533') if g.get('web_hands') else frame_language.text('owned.capabilities.b2a25ae548d2'),
                v5=frame_language.text('owned.capabilities.3bbb88f86321') if g['mail'] else frame_language.text('owned.capabilities.5e9049369e72'),
                v6=lim['cooldown_dm_sec'],
                v7=lim['cooldown_group_sec'],
                v8=lim['history_turns'],
                v9=frame_language.text('owned.capabilities.7256e98803d9') if not lim['context_budget_chars'] else 'budget ' + str(lim['context_budget_chars']) + frame_language.text('owned.capabilities.b705b32f1618'),
                v10=s['autonomy'].get('merge_mode', 'sovereign'),
                v11=', '.join(s['autonomy']['auto']) or '—',
                v12=', '.join(s['autonomy']['protected']) or '—',
                v13=s['reflexes']['server_eyes'],
                v14=s['reflexes']['server_hands'],
                v15=s['reflexes']['host_edits'],
                v16=s['reflexes']['host_master'],
                v17=s['reflexes']['coding']
            )
            + (frame_language.text('owned.capabilities.6e5d4415d0c1', v0=forge_line) if (forge_line := forge.state_line()) else "") + frame_language.text(
                'owned.capabilities.925326ec8080',
                v0=s['reflexes']['forge_events'],
                v1=s['reflexes']['narration'],
                v2=s['reflexes']['windows_body'],
                v3=s['reflexes']['mine'],
                v4=s['reflexes']['room_policy'],
                v5=s['reflexes']['tempo'],
                v6=s['reflexes']['hands'],
                v7=s['reflexes']['evaluator'],
                v8=s['reflexes']['authorship'],
                v9=s['reflexes']['rewrite'],
                v10=skills,
                v11=resident,
                v12=', '.join(s['panel']) or frame_language.text('owned.capabilities.73ff823bfd93'),
                v13=s['closed']['llm_json']
            )
            + (f"\n- {rails_line}" if (rails_line := rails.state_line()) else "")
        )
    # Публичные каналы: честно о доступной поверхности, без ключей/мозга/панели.
    public_tools = list(dict.fromkeys(s["tools"]["base"] + s["tools"]["self"]))
    if offered is not None:
        public_tools = [str(n) for n in offered if str(n or "").strip()]
    lines = [
        frame_language.text('owned.capabilities.c4d27dbdb524'),
        frame_language.text('owned.capabilities.0f51d0776b17'),
        frame_language.text('owned.capabilities.cd2ff16839cd', v0=', '.join(public_tools)),
    ]
    if g["web_search"]:
        lines.append(frame_language.text('owned.capabilities.cfae71e976d5'))
    if g.get("web_hands"):
        lines.append(frame_language.text('owned.capabilities.d8b4b52fe1b9'))
    if scope == "group":
        if levers := _room_levers_brief():
            lines.append(f"- {levers}")
        lines.append(frame_language.text('owned.capabilities.6658dc30dd90', v0=s['closed']['group']))
    else:
        lines.append(frame_language.text('owned.capabilities.9b7b9ee3e155'))
    lines.append(frame_language.text('owned.capabilities.36019bd2c465', v0=s['closed']['non_owner']))
    if offered is not None:
        names = [str(n) for n in offered if str(n or "").strip()]
        # «Суверенные» считаем по СВОЕМУ набору: пересечение с owner-списком назвало бы
        # суверенным и делегирование чужого доверия, если ход owner-ский (Егор в группе).
        # Делегированное называем отдельно и своим именем — молчать о нём тоже нельзя.
        elevated = sorted(set(names) & set(s["tools"]["self"]))
        delegated = sorted(set(names) & (set(s["tools"]["owner"]) - set(s["tools"]["self"])))
        if elevated:
            lines.append(
                frame_language.text('owned.capabilities.3b391c72d408', v0=len(names), v1=', '.join(elevated[:8]))
                + (frame_language.text('owned.capabilities.2e6a02762ff5', v0=len(elevated) - 8) if len(elevated) > 8 else "")
                + (frame_language.text('owned.capabilities.17224126991d', v0=', '.join(delegated)) if delegated else "")
                + frame_language.text('owned.capabilities.0aeb922281de'))
        else:
            lines.append(frame_language.text('owned.capabilities.07d0bf1c2234', v0=len(names)))
    return "\n".join(lines)


@frame_language.pinned
def state_line() -> str:
    """Одна строка-сводка для STATE (owner tier-0). Маркер фолбэка даёт llm.state_line."""
    try:
        s = snapshot()
        g = s["tools"]["gates"]
        lim = s["limits"]
        # Счёт по СВОЕМУ набору: STATE едет каждым ходом (с 10.11 он равен полному).
        return (frame_language.text(
            'owned.capabilities.e1cad6460f28',
            v0=len(s['tools']['base']),
            v1=len(s['tools']['self']),
            v2=frame_language.text('owned.capabilities.2923588c6533') if g['web_search'] else frame_language.text('owned.capabilities.b2a25ae548d2'),
            v3=frame_language.text('owned.capabilities.2923588c6533') if g['mail'] else frame_language.text('owned.capabilities.b2a25ae548d2'),
            v4=lim['max_tool_iters']
        )
                # «на голосе» здесь несёт всю нагрузку: прямые тулы отправки судью не
                # зовут вовсе. В STATE места на подробность нет — но и умолчать нельзя,
                # иначе строка снова читается как «весь мой исходящий смотрят».
                # ⚠ Вечер 27.07: и «privacy-only на голосе» читалось как придержка. Держит
                # ли судья сегодня хоть что-нибудь — спрашиваем у кода, а не утверждаем:
                # строка STATE едет каждым ходом, и соврать ей здесь дороже всего.
                + _outbound_stop_word() + frame_language.text('owned.capabilities.53ec1c6d0f8b', v0=s['autonomy'].get('merge_mode', 'sovereign'))
                + (f"; {line}" if (line := _rails_manifest_line()) else ""))
    except Exception:
        log.debug("capabilities.state_line упал", exc_info=True)
        return ""
