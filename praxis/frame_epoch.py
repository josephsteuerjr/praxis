"""Эпоха комнаты E и лента A с якоря свёртки — K|E|A|T, шаги 2–3 (15.09.2026).

Рычаг `PRAXIS_FRAME_EPOCH` (умолчание ВЫКЛЮЧЕНО: кадр байт-в-байт прежний) и список комнат
`PRAXIS_FRAME_EPOCH_ROOMS` через запятую (`*` — все комнаты; личка и свои окна — никогда).
Пока комнаты нет в списке, для неё не меняется ни байта — это и есть staged (её §5.4 14.09:
«сначала личка, затем одна группа/canary»; Егор 15.09: абстракт — лучший живой тест).

Что делает, одной строкой: стабильный материал конверта (сводка, карта памяти, желания,
профиль комнаты, визитка, досье завсегдатаев, локаторы почты и адреса) замораживается
ФАЙЛОМ на границе свёртки memory_life и едет ПЕРВЫМ сообщением кадра байт-в-байт до
следующей границы; лента комнаты идёт с якоря свёртки в порядке архива, каждая запись
рендерится один раз, правки и удаления дописываются новыми строками; живое (STATE,
приватное владельца, поиск, досье того, кого нет в эпохе, справка «что изменилось после
заморозки») остаётся в последнем сообщении, как и сейчас.

Замер 15.09 (после шага 1, `desk-notes/frames/after-headstable-1509.txt`): голова кадра
группы стала одной на всех (1 отпечаток вместо 7), но первый вызов хода по-прежнему платил
~48k свежих токенов из ~70k — ленту и конверт, потому что окно ленты скользило и первое
сообщение менялось на 157-м байте. Голова — 20k из 70k; всё остальное лежало ПОЗАДИ
подвижного. Здесь порядок переворачивается: неизменное — впереди, подвижное — сзади.

Якорь = message_id первой записи горячего окна memory_life (`hot_records`): всё до него
уже свёрнуто её компактором в сводку, всё после — лента. Переворот эпохи = ЕЁ свёртка
(граница эпизода на паузе EPISODE_GAP_SEC или потолок горячего окна) — «одно холодное
вместо двух» из плана 17.08; на паузе кэш и так холоден. Ручной переворот той же комнаты —
`flip(chat_id, reason)`: следующий ход заморозит эпоху заново под номером n+1.

Эпоха НЕ замораживает полномочия: право проверяется при вызове руки, как и раньше;
`[private]` в эпоху не входит никогда (ярус «Приватное из досье» — живой хвост владельца).
Сводка в E — та, что была на границе; свёртка посреди эпохи E не трогает: лента с якоря и
есть канон разговора (её слово 09.08), а новая сводка приедет со следующей эпохой.

Приборы: расписка эпохи в frame_trace (зона `epoch`, первое сообщение), поля в снимке
`frame_layout` (epoch_n/anchor/drift) и живой ярус-справка в конверте; леджер llm_calls
показывает cached/in как раньше — им и мерить.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

log = logging.getLogger("praxis-epoch")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
STORE = BASE / "memory" / ".state" / "epoch"
SCHEMA = 1
LEVER = "PRAXIS_FRAME_EPOCH"
ROOMS = "PRAXIS_FRAME_EPOCH_ROOMS"
# Физический предохранитель ленты эпохи в знаках (0 = нет). Не режет молча: при превышении
# лента этого хода собирается прежним скользящим окном, и это НАЗЫВАЕТСЯ в логе и в строке
# ленты. Горячее окно memory_life само держит эпоху в 100–125 записей / 24k токенов, так что
# сюда попадает только расхождение архива с горячим слоем.
MAX_CHARS_ENV = "PRAXIS_FRAME_EPOCH_MAX_CHARS"
DEFAULT_MAX_CHARS = 400_000

_ON = {"1", "true", "yes", "on"}

# Заголовки ярусов конверта, которые едут в E. Сверка по ЗАГОЛОВКУ ярлыка (до скобочной
# оговорки сборщика, `frame_layout.split_title`), префиксом — как в `frame_layout._TIERS`.
# Живые ярусы с тем же корнем названы отдельно и проверяются ПЕРВЫМИ.
STABLE_PREFIXES = (
    "Canonical desire continuity",
    "Ранее в этом диалоге",
    "Мои досье на людей",
    "Карта памяти",
    "Почтовый ящик — ЛОКАТОР",
    "Куда здесь уходит ответ",
    "Эта комната",
)
# ⚑ ЕЁ §5.1, дословно (личка 15.09 11:26): «„Визитка“ и „Mutable operational continuity“ — в
# хвост, не разрез внутри E». Первая редакция кандидата держала визитку в E, а её живую
# фактуру (расход, окна, карточки) выносила отдельным ярусом — это и есть разрез, который
# она отклонила. Визитка целиком остаётся живым ярусом конверта; в E её нет.
LIVE_PREFIXES = (
    "Эпоха этой комнаты",
)
# Ярусы, которые под эпохой рождаются здесь. Ярлыки — литералы кадра: причины и слоты в
# `frame_layout._TIERS` по префиксу.
LIVE_TIER = ("Эпоха этой комнаты — живая справка (что заморожено, что изменилось после; "
             "в эпоху не входит)")

_BOUND: contextvars.ContextVar = contextvars.ContextVar("praxis_epoch_anchor", default=None)
_TAKEN: contextvars.ContextVar = contextvars.ContextVar("praxis_epoch_message", default=None)
_ACTIVE: contextvars.ContextVar = contextvars.ContextVar("praxis_epoch_active", default=False)


# ------------------------------------------------------------------------------ рычаги


def enabled() -> bool:
    return str(os.getenv(LEVER) or "").strip().lower() in _ON


def rooms() -> tuple[str, ...]:
    return tuple(x.strip() for x in str(os.getenv(ROOMS) or "").split(",") if x.strip())


def room_enabled(chat_id) -> bool:
    """Комната под эпохой: рычаг включён И комната названа (или `*`)."""
    if not enabled() or chat_id is None:
        return False
    listed = rooms()
    return "*" in listed or str(chat_id) in listed


def applies(ctx) -> bool:
    """Эпоха применима к этому ctx: комната (не личка, не свой ход) из списка."""
    return bool(ctx is not None
                and not getattr(ctx, "is_dm", True)
                and getattr(ctx, "chat_id", None) is not None
                and room_enabled(getattr(ctx, "chat_id", None)))


def max_chars() -> int:
    try:
        return max(0, int(os.getenv(MAX_CHARS_ENV, str(DEFAULT_MAX_CHARS)) or 0))
    except ValueError:
        return DEFAULT_MAX_CHARS


def stable_title(title: str) -> bool:
    """Ярус едет в E? Решается по заголовку ярлыка; живые с тем же корнем — первыми."""
    import frame_layout  # noqa: PLC0415 — только ради split_title, без цикла импорта
    head = frame_layout.split_title(title)[0]
    if head.startswith(LIVE_PREFIXES):
        return False
    return head.startswith(STABLE_PREFIXES)


# ------------------------------------------------------------------------------- якорь


def anchor_for(chat_id) -> int | None:
    """message_id первой записи горячего окна memory_life — граница последней свёртки.

    Правки в горячем слое лежат отдельными записями с source_id вида
    `<mid>:edit:<ts>:<hash>` — берётся ведущее число. Нет записей или числа — якоря нет,
    и эпоха на этот ход не собирается (вызывающий идёт прежним путём и говорит об этом)."""
    try:
        import memory_life  # noqa: PLC0415
        for row in memory_life.hot_records(chat_id):
            sid = row.get("source_id") or (row.get("meta") or {}).get("source_id")
            head = str(sid or "").split(":", 1)[0].strip()
            if head.isdigit():
                return int(head)
    except Exception:
        log.debug("якорь эпохи не прочитался [%s]", chat_id, exc_info=True)
    return None


@contextlib.contextmanager
def bind(chat_id, anchor):
    """Привязать якорь, с которого раннер собрал ленту, к ходу: сборщик кадра читает его
    отсюда, а не считает заново (между снимком и ходом могла пройти свёртка)."""
    token = _BOUND.set((str(chat_id), int(anchor)) if anchor is not None else None)
    try:
        yield
    finally:
        _BOUND.reset(token)


def bound_anchor(chat_id) -> int | None:
    value = _BOUND.get()
    if value is None or value[0] != str(chat_id):
        return None
    return int(value[1])


# ------------------------------------------------------------------------------ хранение


def _safe(chat_id) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]", "_", str(chat_id)) or "_"


def path_for(chat_id) -> Path:
    return STORE / f"{_safe(chat_id)}.json"


def load(chat_id) -> dict | None:
    try:
        raw = json.loads(path_for(chat_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        log.exception("эпоха комнаты [%s] нечитаема — перезаморозка", chat_id)
        return None
    return raw if isinstance(raw, dict) else None


def _write_atomic(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def _stamp(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ts if ts is not None else time.time()))


def _sha8(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:8]


def serve(chat_id, anchor: int, blocks: list[tuple[str, str, str]]) -> tuple[list[tuple[str, str, str]], dict]:
    """Замороженные блоки E для (комната, якорь) и расписка.

    `blocks` — (ярлык, kind, текст блока) такими, какими они собрались бы СЕЙЧАС. Если для
    комнаты есть эпоха с тем же якорем — возвращаются ЕЁ блоки байт-в-байт, а расхождение с
    нынешними считается по ярусам (drift) и едет в расписку. Иначе эпоха замораживается
    заново: первая, по сдвигу якоря или по ручному перевороту (`pending`)."""
    saved = load(chat_id)
    now_sha = {title: _sha8(block) for title, _kind, block in blocks}
    now_chars = {title: len(block) for title, _kind, block in blocks}
    if (saved and int(saved.get("anchor") or -1) == int(anchor) and not saved.get("pending")
            and isinstance(saved.get("blocks"), list)):
        frozen = [(str(t), str(k), str(b)) for t, k, b in saved["blocks"]
                  if isinstance(b, str)]
        old_sha = {t: _sha8(b) for t, _k, b in frozen}
        old_chars = {t: len(b) for t, _k, b in frozen}
        drift = {}
        for title in sorted(set(old_sha) | set(now_sha)):
            if old_sha.get(title) != now_sha.get(title):
                drift[title] = {"frozen_chars": old_chars.get(title, 0),
                                "current_chars": now_chars.get(title, 0)}
        receipt = {"n": int(saved.get("n") or 0), "anchor": int(anchor),
                   "frozen_at": str(saved.get("frozen_at") or ""),
                   "reason": "reused", "frozen_reason": str(saved.get("reason") or ""),
                   "drift": drift, "chars": sum(old_chars.values())}
        return frozen, receipt
    n = int((saved or {}).get("n") or 0) + 1
    if not saved:
        reason = "first"
    elif saved.get("pending"):
        reason = "flip: " + str(saved.get("pending"))
    else:
        reason = f"anchor_moved: #{saved.get('anchor')} → #{anchor}"
    record = {
        "v": SCHEMA, "chat": str(chat_id), "n": n, "anchor": int(anchor),
        "frozen_at": _stamp(), "reason": reason,
        "blocks": [[t, k, b] for t, k, b in blocks],
        "sha": {t: s for t, s in now_sha.items()},
        "chars": sum(now_chars.values()),
    }
    try:
        _write_atomic(path_for(chat_id), json.dumps(record, ensure_ascii=False, indent=1))
    except Exception:
        # Не записалось — эпоха всё равно едет (этот ход честный), но следующий ход
        # заморозит её снова под тем же номером: это видно в расписке по reason.
        log.exception("эпоха комнаты [%s] не записалась", chat_id)
    receipt = {"n": n, "anchor": int(anchor), "frozen_at": record["frozen_at"],
               "reason": reason, "frozen_reason": reason, "drift": {},
               "chars": record["chars"]}
    return list(blocks), receipt


def flip(chat_id, reason: str = "её слово") -> int:
    """Ручная граница: следующий ход заморозит эпоху заново под номером n+1.
    Возвращает номер будущей эпохи."""
    saved = load(chat_id) or {}
    saved = dict(saved, pending=str(reason or "её слово"))
    if "n" not in saved:
        saved["n"] = 0
    _write_atomic(path_for(chat_id), json.dumps(saved, ensure_ascii=False, indent=1))
    return int(saved.get("n") or 0) + 1


# --------------------------------------------------------------------------- кадр: тексты


_OPEN_RE = re.compile(r"^<praxis_epoch n=\d+ anchor=#\d+ frozen=[^>]+>$")


def head(receipt: dict) -> str:
    """Открывающая шапка E — наши строки (объявляются прибору как собранные нами)."""
    return (f"<praxis_epoch n={int(receipt.get('n') or 0)} anchor=#{int(receipt.get('anchor') or 0)} "
            f"frozen={receipt.get('frozen_at') or '?'}>\n"
            "# Эпоха этой комнаты — замороженный документ\n"
            "Собран на границе свёртки и не меняется до следующей границы; то, что стало известно "
            "позже, стоит ниже — в ленте разговора и в живом хвосте кадра. Права проверяются при "
            "вызове руки, а не этим документом.\n")


CLOSE = "</praxis_epoch>"


def message(receipt: dict, blocks: list[tuple[str, str, str]]) -> str:
    return head(receipt) + "".join(b for _t, _k, b in blocks) + CLOSE


def live_note(receipt: dict) -> str:
    """Тело живого яруса-справки: номер, якорь, время заморозки и что уехало с тех пор."""
    lines = [f"эпоха {int(receipt.get('n') or 0)} · заморожена {receipt.get('frozen_at') or '?'} UTC · "
             f"лента с сообщения #{int(receipt.get('anchor') or 0)} · "
             f"причина заморозки: {receipt.get('frozen_reason') or receipt.get('reason') or '?'}"]
    drift = receipt.get("drift") or {}
    if drift:
        import frame_layout  # noqa: PLC0415
        parts = []
        for title, change in drift.items():
            headline = frame_layout.split_title(title)[0]
            delta = int(change.get("current_chars", 0)) - int(change.get("frozen_chars", 0))
            parts.append(f"{headline} ({'+' if delta >= 0 else ''}{delta} зн.)")
        lines.append("после заморозки изменились: " + "; ".join(parts)
                     + ". В эпохе — версия на момент заморозки; свежее достаётся руками "
                       "(recall, describe, group_context) и приедет со следующей эпохой.")
    else:
        lines.append("после заморозки ничего из замороженного не менялось.")
    lines.append("граница эпохи — свёртка этого места (memory_life); принудительно — "
                 "frame_epoch.flip(<комната>, причина).")
    return "\n".join(lines)


def activate(flag: bool) -> None:
    """Флаг хода «эпоха применяется»: сборщик выставляет его, решив про якорь; ярусы,
    собираемые ниже (досье), читают его отсюда, не меняя своих сигнатур."""
    _ACTIVE.set(bool(flag))


def active() -> bool:
    return bool(_ACTIVE.get())


def take(text: str | None, receipt: dict | None) -> None:
    """Сборщик кадра кладёт сюда готовое первое сообщение; голос забирает его."""
    _TAKEN.set((text, receipt) if text else None)


def taken() -> tuple[str, dict] | None:
    value = _TAKEN.get()
    return value if value and value[0] else None


def reset() -> None:
    _TAKEN.set(None)
    _ACTIVE.set(False)


# ------------------------------------------------------------------------------- CLI


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "show":
        saved = load(argv[1])
        if not saved:
            print("эпохи нет:", path_for(argv[1]))
            return 1
        print(json.dumps({k: v for k, v in saved.items() if k != "blocks"}, ensure_ascii=False, indent=1))
        for t, _k, b in saved.get("blocks") or []:
            print(f"  - {t[:70]!r}: {len(b)} зн.")
        return 0
    if len(argv) >= 2 and argv[0] == "flip":
        n = flip(argv[1], " ".join(argv[2:]) or "рычаг")
        print(f"следующая эпоха комнаты {argv[1]}: {n}")
        return 0
    print("использование: frame_epoch.py show <chat> | flip <chat> [причина]")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
