# -*- coding: utf-8 -*-
"""Локальный харнесс — руннер v1: в окне (и в Telegram-боте) идёт ЕЁ ход.

Порт как есть. Руннер не воспроизводит её ход, а запускает её собственный —
`agent.voice_turn_envelope`, тот самый, которым живёт агент на сервере:

  * руки — все её (recall, дневник, желания, файлы, мастерская, shell, коддинг…),
    выданные её же сборщиком `offered_tools_for` (контракт A1: список один на всех);
  * тул-цикл — её: расписки, чекпойнты, exact-once, потолки, честные ошибки рук;
  * кадр — её `frame_shadow` (K‖E‖A стабильны и кэшируются, T — подвижный хвост);
  * память — её (`memory_life`, recall-индекс, дневник, досье, леджер желаний);
  * прожитый ход, `runs/`, `turns.jsonl`, `llm_calls.jsonl` — пишет она сама, в тех
    же форматах, которые окно уже читает.

Наша работа — три шва, ни строчки её кода:
  1. ОКНО (`transport.py`) — окно вложено в `agent._TELETHON` вместо Telethon;
  2. БОТ (`botapi.py`, опция) — Telegram Bot API поверх: один токен от BotFather
     вместо номера/api_id/api_hash, маршрутизатор по адресу (window → окно,
     числовые id → бот);
  3. КОНФИГ (`boot.py`) — helene.json кладётся туда, где дерево его читает.

Организм ОДИН: у окна и бота общая память, общий кадр, общие желания. Ходы идут
строго по одному — очередь здесь, а не в её дереве.

Запуск (оболочкой): python runner.py --config <путь к helene.json>
Дерево данных — из PRAXIS_DESK_TREE (кладёт оболочка) или из `tree` конфига.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import body
import boot
import botapi
import broker
import modes
import transport
import continuity
import voice
import alarm_clock
import forge_events

# Уровень лога — ручкой, а не константой: две главные глухоты продукта (квитанция
# читателя не пишется; сторож живых файлов сдох) диагностировались строками
# log.debug при жёстко прибитом INFO, то есть в собранном владельцем архиве логов
# от них не оставалось ни строки.
_LOG_LEVEL = (os.environ.get("HELENE_LOG") or "INFO").strip().upper()
if _LOG_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
    _LOG_LEVEL = "INFO"
logging.basicConfig(level=_LOG_LEVEL,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("frame.runner")

STREAM = "window"          # комната окна: ключ архива memory/groups/window.jsonl
_POLL_SEC = 1.0
_HEARTBEAT_SEC = 10.0

_agent = None          # её дерево, загруженное в этот процесс
_life = None           # memory_life — память дерева
_refresh_care = None   # serialized background refresh, owned by main lifecycle
_desk: transport.Desk | None = None      # комната окна по умолчанию (`window`)
_desks: transport.Desks | None = None    # все комнаты окна (задача A §3)
_bot = None            # botapi.BotTransport | None
_status_message = False  # telegram.status_message: пост «думаю…» у долгого хода (turn_pulse)
_speaker = "владелец"
_title = "Hélène"
_agent_name = "Агент"
_tree: Path | None = None
#: Старт процесса — её `sleep.due` не будит сон раньше десяти минут после него.
_STARTED_AT = time.time()
#: Как часто спрашивать её `sleep.due`. У неё тик — 30 минут; в издании сон ещё ждёт
#: тишины владельца (`_SLEEP_IDLE_SEC`), и в двухчасовом окне тишину надо успеть поймать.
_SLEEP_CHECK_SEC = float(os.getenv("PRAXIS_SLEEP_CHECK_SEC", "300") or 300)
#: Сон начинается, только если владелец молчит столько (ревью 26.09, W3 S1): пока идёт
#: сон, агент не отвечает — ни в окне, ни в Telegram, записки ждут его конца.
_SLEEP_IDLE_SEC = float(os.getenv("PRAXIS_SLEEP_IDLE_MIN", "20") or 20) * 60.0
#: Когда владелец в последний раз был рядом: начало или конец любого хода, кроме сна.
_LAST_ACTIVITY = [time.time()]
#: Как часто обслуживать заявки на пересборку индекса памяти (`_recall_care_forever`).
_RECALL_CARE_SEC = float(os.getenv("PRAXIS_RECALL_CARE_SEC", "900") or 900)
#: Слышит ли этот процесс (`voice.apply` на старте): голосовое из окна расшифровывается
#: только когда слух поднят, иначе — строка с причиной, а не тихая потеря.
_voice_state: dict = {"ready": False, "why": "голос ещё не поднимался"}
#: Код выхода «перезапусти меня» (26.09): надзор — окно или служба — поднимает движок
#: сразу и с перечитанными настройками, без лестницы пауз. Тот же номер знают
#: `shell/src/main.rs` и `svc/src/main.rs`.
RESTART_EXIT_CODE = 42
_restart_wanted = [False]
_STARTED_UTC = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
_continuity = None
_alarms = None
_forge_events = None
_busy: dict = {"busy": False, "run": "", "since": 0.0, "chat_id": ""}
_mode: dict = {}       # картина режима (modes.resolve) — едет в анатомию
_deliver_unspoken = True   # agent.deliver_unspoken в helene.json; см. _turn_in_window


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _load_tree(code_dir: Path, tree: Path, cfg: dict):
    """Поднять дерево агента в этом процессе.

    Порядок здесь не косметический:
      1. PRAXIS_BASE и ручки — ДО импорта: половина модулей читает среду на импорте;
      2. sitecustomize — LF-шим порта (на POSIX no-op), до любых записей;
      3. судьба `.env` рядом с кодом — тоже до импорта: на импорте `agent` зовёт
         `load_dotenv(override=True)`, и чужой файл перекрыл бы конфиг продукта;
      4. импорт `agent`;
      5. ручки ПОВТОРНО — страховка на случай, когда `.env` читать всё же попросили.
    Разбор ловушки — в шапке `boot.py`.
    """
    global _agent, _life
    # С деревом — чтобы КЕАТ личного потока получил абсолютные пути своей политики
    # (boot.keat_knobs); без владельца в Telegram его ручек нет вовсе.
    knobs = boot.env_knobs(cfg, tree=tree)
    os.environ["PRAXIS_BASE"] = str(tree)
    # 26.09: рабочая папка руки shell — `workspace/` дома, как и обещает навык
    # home-map. Без этой переменной дерево брало корень дома, а busybox из поставки
    # на `bash -l` и вовсе уходил в профиль владельца (см. agent.tool_shell).
    workspace = Path(tree) / "workspace"
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.debug("workspace не завёлся — shell пойдёт из корня дома", exc_info=True)
    os.environ.setdefault("PRAXIS_WORKDIR", str(workspace))
    boot.apply_env(knobs, where="до импорта")
    sys.path.insert(0, str(code_dir))
    try:
        import sitecustomize  # noqa: F401 — win32 LF-шим, no-op на POSIX
    except Exception:
        log.warning("sitecustomize дерева не загрузился — записи пойдут с CRLF", exc_info=True)
    boot.dotenv_gate(code_dir, cfg)
    boot.ensure_shell()
    import agent
    import memory_life
    boot.apply_env(knobs, where="после импорта")
    _agent, _life = agent, memory_life
    return agent, memory_life


def _dialogue(chat_id: str, sidecar: dict | None = None) -> tuple[list[dict], str]:
    """Разговор ролями: (история, то-на-что-она-отвечает-сейчас).

    Правило ЕЁ раннера, дословно (`_turns_to_dialogue` в mtproto_runner): граница
    проходит по её последней реплике; подряд идущие реплики одного автора склеиваются
    в один блок; она здесь ещё не говорила — ролей нет, и ход идёт сплошным текстом.
    Своя копия правила здесь потому, что живой раннер тащит за собой Telethon целиком,
    а правило — двадцать строк.

    `sidecar` (1.0.0, КЕАТ): сюда кладутся расписки захвата тех же записей теми же
    группами — `history` по блокам ролей, `current` по репликам «сейчас». Только если
    захвачена ВСЯ лента этого хода: сужать её до захваченного хвоста значило бы
    повторить её прод 25.09, где обслуженный вызов нёс одну реплику вместо разговора.
    Не всё захвачено — `sidecar` остаётся пустым, и ход идёт прежним путём с историей.
    """
    if sidecar is not None:
        sidecar.clear()
    try:
        records = _life.hot_records(chat_id, _life.HOT_HARD_HI)
        # ⚠ ПОТОЛОК ЛЕНТЫ В ЗНАКАХ (КЕАТ, 12.09). Сто двадцать пять записей — это
        # потолок ПАМЯТИ, а не кадра: замер ядра дал 43,5 тыс. знаков ленты при
        # договорённых 5 500. Свёртка подтянется следом сама (`compact_if_due`), но
        # кадр этого хода обязан уместиться СЕЙЧАС. Для группы потолок свой: там
        # лента — это и есть разговор, и общий порог оставлял от неё десять реплик.
        records = _life.tape_window(records, _life.tape_chars_for(chat_id))
    except Exception:
        log.warning("горячий слой не прочитался [%s] — иду сплошным текстом",
                    chat_id, exc_info=True)
        return [], ""
    admitted = [r for r in records if str(r.get("line") or "").strip()]
    rows = [(r.get("direction") == "out", str(r.get("line") or "")) for r in admitted]
    if not rows:
        return [], ""
    last_self = -1
    for i, (is_self, _text) in enumerate(rows):
        if is_self:
            last_self = i
    if last_self < 0:
        return [], ""
    history: list[dict] = []
    run_role, run_lines = "", []

    def flush() -> None:
        if run_lines:
            history.append({"role": run_role,
                            "content": (chr(10) * 2).join(run_lines).strip()})

    for is_self, text in rows[:last_self + 1]:
        role = "assistant" if is_self else "user"
        if role != run_role:
            flush()
            run_role, run_lines = role, []
        run_lines.append(text)
    flush()
    current = "\n".join(text for _is_self, text in rows[last_self + 1:])
    if not history or not current.strip():
        return [], ""
    if sidecar is not None:
        refs = [(r.get("meta") or {}).get("keat_occurrence") for r in admitted]
        if all(isinstance(ref, dict) and ref for ref in refs):
            groups: list[list[dict]] = []
            role = None
            for (is_self, _text), ref in zip(rows[:last_self + 1], refs[:last_self + 1]):
                if is_self is not role:
                    groups.append([])
                    role = is_self
                groups[-1].append(dict(ref))
            sidecar.update(history=groups,
                           current=[dict(ref) for ref in refs[last_self + 1:]])
    return history, current


def _last_n() -> int:
    try:
        return max(1, int(os.getenv("PRAXIS_LAST_N", "80") or 80))
    except ValueError:
        return 80


# Подсказка в кадр (одобрена владельцем 02.09): слабые модели пишут ответ текстом,
# а под поднятым рычагом reply текст без руки — заметка себе, и слово теряется.
# Идёт в блок runtime continuity кадра через параметр orient, дерево не правится.
_WINDOW_ORIENT = ("Это окно Hélène на компьютере владельца. Слово владельцу уходит "
                  "ТОЛЬКО рукой reply; текст без руки и заметка end_turn — не ответ, "
                  "а запись себе.")
# Кадр дерева обещает агенту автокоммит и автооткат правок в git. В поставке git
# нет вовсе, и обещание — ложь ровно там, где на неё опираются, решаясь править
# свои файлы. Правку кадра дерева делать нельзя, а сказать правду можно здесь:
# ориентир едет в тот же блок runtime continuity.
_NO_GIT_ORIENT = ("В этой сборке нет git: автокоммита правок и автоотката при падении "
                  "НЕТ. Правка файла необратима — снимай копию заранее, если жалко.")
_ORIENT_EXTRA = ""      # заполняется на старте по факту (см. main)


def _room(key: str) -> "transport.Desk":
    """Комната окна по ключу; без реестра комнат — комната по умолчанию."""
    if _desks is not None and transport.is_room(key):
        return _desks.get(key)
    return _desk


def _inbox_target(stem: str) -> str:
    """Адрес записки композера по имени файла: `<stamp>__to__<ключ>.md`.

    Без суффикса и старым ключом (до 10.09.2026) — комната окна по умолчанию; `window-<hex>`
    — другая комната окна; всё прочее — Telegram-комната."""
    if "__to__" not in stem:
        return STREAM
    target = stem.split("__to__", 1)[1].strip()
    return STREAM if target in ("", STREAM, transport.ROOM_LEGACY) else target


_ATTACH_MARK = "[вложения]"


def _split_attachments(message: str) -> tuple[str, list[str]]:
    """Подвал `[вложения]` записки окна -> (текст без подвала, пути файлов).

    Канал (deskapp._say) пишет вложения файлами в `desk_inbox/attachments/<stamp>/`
    и называет их в подвале строками `- attachments/<stamp>/<имя> · <mime> · <байт>`.
    Подвал — последний блок записки; всё до него — реплика владельца как есть.
    """
    text = str(message or "")
    idx = text.rfind("\n" + _ATTACH_MARK + "\n") if not text.startswith(_ATTACH_MARK) else 0
    if idx < 0 and text.rstrip() != _ATTACH_MARK:
        return text.strip(), []
    head = text[:idx] if idx > 0 else ""
    tail = text[idx:].split(_ATTACH_MARK, 1)[1]
    paths: list[str] = []
    for line in tail.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        rel = line[2:].split(" · ", 1)[0].strip()
        if rel.startswith("attachments/") and ".." not in rel.split("/"):
            paths.append(rel)
    return head.strip(), paths


_AUDIO_EXT = frozenset({".webm", ".ogg", ".oga", ".opus", ".m4a", ".mp3", ".wav"})


def _hear_attachments(paths: list[str]) -> tuple[list[str], list[str]]:
    """Голосовые из окна (0.6.0) -> строки расшифровки; остальные пути — обратно.

    Голосовое — не картинка: модели нечего показывать, ей нужен текст. Поэтому
    запись не едет в медиа-спул, а расшифровывается здесь тем же `media_audio`
    дерева, которым слушаются голосовые из Telegram (переменные ему ставит
    `voice.apply` на старте). Слух не поднят — в реплике остаётся строка с
    причиной: владелец должен видеть, что его не услышали, и почему.
    """
    heard: list[str] = []
    rest: list[str] = []
    for rel in paths:
        if Path(rel).suffix.lower() in _AUDIO_EXT:
            heard.append(_transcribe_note(rel))
        else:
            rest.append(rel)
    return heard, rest


def _transcribe_note(rel: str) -> str:
    name = Path(rel).name
    if _tree is None:
        return f"[голосовое не расшифровано: {name} — дерево ещё не загружено]"
    inbox = (Path(_tree) / "memory" / ".control" / "desk_inbox").resolve()
    src = (inbox / rel).resolve()
    if inbox not in src.parents or not src.is_file():
        return f"[голосовое не найдено: {name}]"
    if not _voice_state.get("ready"):
        return f"[голосовое не расшифровано: {_voice_state.get('why') or 'слух не поднят'} — Настройки → Голос]"
    try:
        import importlib
        with _low_priority():
            text = str(importlib.import_module("media_audio").transcribe(src) or "").strip()
    except Exception as exc:  # noqa: BLE001 — любая причина называется словами
        log.warning("голосовое из окна не расшифровалось [%s]", rel, exc_info=True)
        return f"[голосовое не расшифровано: {type(exc).__name__}: {str(exc)[:160]}]"
    return f"[голосовое]: {text}" if text else "[голосовое: расшифровка пустая — тишина или не разобрать]"


def _ingest_attachments(paths: list[str], *, chat_id: str, message_id: str) -> tuple[list, list[str]]:
    """Файлы окна -> медиа-спул дерева (`ingest_path`, перенос) -> ссылки для кадра.

    Дерево кладёт картинку в кадр само (`_media_prompt`: блок `image` рядом с текстом)
    и переключает модель на зрячую до вызова (`llm.vision_model`) — руннеру остаётся
    только положить файл туда, откуда дерево его примет: в спул, с областью `owner`
    (окно — дверь владельца) и адресом этой комнаты. Что не принялось — словами в
    текст хода, а не молча.
    """
    if not paths or _tree is None:
        return [], []
    # Спул спрашиваем у САМОГО дерева (`agent._media_spool`) — тот же объект, что
    # ведёт медиа живого раннера, с его квотами и леджером. Свой `MediaSpool()` —
    # только если дерево его не отдаёт: два спула на одно дерево спорили бы за
    # леджер.
    try:
        if hasattr(_agent, "_media_spool"):
            spool = _agent._media_spool()
        else:
            import importlib
            spool = importlib.import_module("media").MediaSpool()
    except Exception:
        log.warning("медиа-спул недоступен — вложения окна не поедут", exc_info=True)
        return [], [f"[вложение не прочитано: {Path(p).name} — медиа-спул недоступен]" for p in paths]
    inbox = (Path(_tree) / "memory" / ".control" / "desk_inbox").resolve()
    refs, notes = [], []
    for rel in paths:
        src = (inbox / rel).resolve()
        if inbox not in src.parents or not src.is_file():
            notes.append(f"[вложение не найдено: {Path(rel).name}]")
            continue
        try:
            refs.append(spool.ingest_path(src, kind="photo", chat_id=chat_id,
                                          message_id=message_id, scope="owner", move=True))
        except Exception as exc:  # MediaValidationError и родня — словами
            log.warning("вложение окна отвергнуто спулом [%s]", rel, exc_info=True)
            notes.append(f"[вложение не прочитано: {src.name} — {type(exc).__name__}: {exc}]")
    return refs, notes


def _orient(chat_id: str) -> str:
    bits = [_WINDOW_ORIENT] if transport.is_room(chat_id) else []
    if _ORIENT_EXTRA:
        bits.append(_ORIENT_EXTRA)
    # Расширения владельца (25.09, K): что подключено и что не загрузилось и почему —
    # агент видит выключенное расширение как строку, а не как исчезновение тула.
    try:
        import extensions
        line = extensions.state_line()
        if line:
            bits.append(line)
    except Exception:
        pass
    return " ".join(bits)


def _read_passport(home: Path) -> str:
    """Версия программы из паспорта поставки (`helene-build.json` в корне) или ''."""
    try:
        data = json.loads((Path(home) / "helene-build.json").read_text(encoding="utf-8-sig"))
        return str(data.get("version") or "")
    except Exception:
        return ""


def _run_turn(chat_id: str, convo: str, speaker: str, ctx, media_refs: tuple = ()) -> "object | None":
    """Один её ход с уже собранным контекстом. -> envelope или None (упал).

    `media_refs` — картинки из окна (0.5.0); без них вызов дерева тот же, что и
    раньше (аргумент не передаётся вовсе — стенды с заглушкой дерева его не знают).
    """
    # КЕАТ (1.0.0): расписки захвата ленты — только личному потоку владельца и только
    # когда захват поднят; остальным ходам сайдкар не нужен, дерево его не ждёт.
    keat_stream = (os.environ.get("PRAXIS_KEAT_CAPTURE") == "on"
                   and bool(getattr(ctx, "is_dm", False)) and bool(getattr(ctx, "owner", False)))
    sidecar: dict | None = {} if keat_stream else None
    history, current = (_dialogue(chat_id, sidecar) if sidecar is not None
                        else _dialogue(chat_id))
    orient = _orient(chat_id)
    extra = {"media_refs": tuple(media_refs)} if media_refs else {}
    if sidecar:
        extra["occurrence_sidecar"] = sidecar
    # 25.09 (G): ход в этом чате начался — его уведомления из накопителя сняты (агент
    # видит всю комнату сам). Слова о других комнатах остаются до их ходов.
    try:
        from core import notices as core_notices
        core_notices.clear_chat(chat_id)
    except Exception:
        log.debug("накопитель уведомлений: снятие для [%s] не удалось", chat_id, exc_info=True)
    _ext_hook("before_turn", chat_id)
    envelope = None
    try:
        envelope = _agent.voice_turn_envelope(
            chat_id, convo, speaker, ctx=ctx, history=history, current_text=current,
            orient=orient, **extra)
        return envelope
    except Exception:
        log.exception("ход упал в дереве [%s]", chat_id)
        return None
    finally:
        _ext_hook("after_turn", chat_id, envelope)


def _note_incoming(*, chat_id: str, text: str, sender: str, sender_id: str, is_dm: bool,
                   title: str = "", message_id: str = "", ts: float | None = None) -> None:
    """Входящее из Telegram — в накопитель уведомлений дерева (25.09, G).

    Зовётся из потока приёма бота/аккаунта до хода: пока идёт ход в другом чате, строка
    покажется агенту в ближайшем вводе модели (изменчивый хвост кадра и итерации
    тул-цикла), а ход этого чата снимет её сам (`clear_chat` в `_run_turn`). В издании
    окон-долгожителей нет, поэтому `owner_line` не пишется: реплика владельца в ЛС и так
    едет событием комнаты, с содержимым — аудитория владельческая.
    """
    try:
        from core import notices as core_notices
    except Exception:
        return
    try:
        core_notices.note_incoming(
            kind="dm" if is_dm else "mention", chat_id=str(chat_id),
            chat_title=str(title or sender or ""), who=str(sender or ""),
            message_id=str(message_id or ""), gist=str(text or ""), private=bool(is_dm),
            ts=ts, is_owner_dm=False)
    except Exception:
        log.debug("накопитель уведомлений: запись [%s] не легла", chat_id, exc_info=True)


def _ext_hook(event: str, *args) -> None:
    """Крючки расширений владельца (25.09, K): сбой — в журнал, ход не рвётся."""
    try:
        import extensions
        extensions.run_hook(event, *args)
    except Exception:
        log.debug("крючки расширений (%s) не отработали", event, exc_info=True)


def _compact(chat_id: str) -> None:
    """Свернуть горячий слой, если пора. ПОСЛЕ хода, чтобы не удлинять ответ.

    ⚠ Продукт написал свой раннер и не перенёс из ядра этот вызов — единственный,
    который применяет потолок горячего слоя В ТОКЕНАХ (HOT_TOKEN_CAP=24000).
    Чтение слоя режет только по ЧИСЛУ записей (125), поэтому лента, уезжающая в
    модель, росла без предела: владелец, вставляющий в окно логи и куски файлов,
    набирал десятки тысяч токенов за вечер, и они платились заново на каждой
    итерации каждого следующего хода.

    ЧЕМ ЭТО ПЛАТИТСЯ, ЧЕСТНО. Свёртка зовёт модель сама — роль `evaluator`
    (`live/memory_life.py:847`), промпт до 48 000 знаков и мимо кэша, плюс
    рекурсивные тиры на верхних слоях. Зовётся она не на каждый ход, а по
    давлению слоя (число записей или 24 000 токенов), и «дешёвой» эта модель
    становится не сама: пока владелец не назвал модель свёрток
    (`evaluator.model` или `model.compact_model` в helene.json — см.
    `boot._brain_config`), она ТА ЖЕ ФЛАГМАНСКАЯ, что и голос. Прежняя строчка
    здесь обещала «один дешёвый вызов»; дешёвого в нём не было ничего.
    """
    if _life is None:
        return
    try:
        result = _life.compact_if_due(chat_id)
    except Exception:
        log.warning("свёртка горячего слоя не прошла [%s]", chat_id, exc_info=True)
        return
    finally:
        if _refresh_care is not None:
            _refresh_care.request(chat_id)
    folded = int((result or {}).get("folded") or 0)
    if folded:
        log.info("свёртка [%s]: свёрнуто %d записей", chat_id, folded)


def _close_run(envelope, chat_id: str, *, delivered_text: str = "",
               spoken_by_hand: int = 0, media_count: int = 0) -> None:
    """Закрыть durable-прогон так, как это делает живой раннер на границе.

    Без этого карточка хода вечно оставалась `running`, и КАЖДЫЙ старт руннера
    «восстанавливал» вчерашний успешный ход в paused. Ровно те же вызовы, что у
    mtproto: доставили текст мы — started → text_accepted → finalize; текста на
    границе нет (рука уже сказала, или она промолчала) — completed(silent=True),
    редьюсер сам сведёт расписки. deferred/failed не трогаем: их состояние
    принадлежит durable-механике и закрывается её путями.
    """
    run_id = str(getattr(envelope, "run_id", "") or "")
    if not run_id or getattr(envelope, "deferred", False) \
            or getattr(envelope, "failed", False):
        return
    try:
        if delivered_text:
            _agent.run_delivery_started(run_id, chat_id=chat_id,
                                        text_chars=len(delivered_text),
                                        media_count=int(media_count))
            _agent.run_delivery_text_accepted(run_id, text=delivered_text)
            _agent.run_delivery_finalize_recovered(run_id)
        else:
            reason = (f"reply hand delivered {spoken_by_hand} message(s); "
                      "boundary carries no text") if spoken_by_hand else \
                "agent chose silence"
            _agent.run_delivery_completed(run_id, silent=True,
                                          silent_reason=reason)
    except Exception:
        log.exception("запуск не закрылся расписками [%s]", run_id)


def deliver_one_media(item, chat_id: str) -> str:
    """Один спуленный файл на свою поверхность. -> расписка приёма.

    Выделено из `_deliver_outbound` 17.09, чтобы тем же путём мог ходить и ВОЗОБНОВЛЁННЫЙ
    ход: у него конверта нет, есть предмет очереди. Тело одно на оба пути — иначе живая и
    восстановленная доставка разъехались бы, а это ровно тот класс расхождений, из-за
    которого файл однажды пропал молча. Исключения наружу: решает вызывающий, повторять
    ему или гасить долг.
    """
    target = str(getattr(item, "target_chat_id", "") or chat_id)
    if (_bot is not None and target != STREAM
            and botapi.is_telegram_key(target)):
        return str(_bot.deliver_file(
            Path(item.path), chat_id=target,
            caption=str(getattr(item, "caption", "") or ""),
            media_kind=str(getattr(item, "kind", "document") or "document"),
            voice_note=bool(getattr(item, "voice_note", False))))
    note = f"[файл] {Path(item.path).name} — {item.path}"
    caption = str(getattr(item, "caption", "") or "").strip()
    return str(_room(target).deliver(note + ("\n" + caption if caption else "")))


def _deliver_outbound(envelope, chat_id: str) -> int:
    """Медиа, спуленное ходом (`send_media`): документы/фото/аудио этого чата.

    В живом раннере это делает mtproto на исходящей границе; здесь — мы, тем же
    правилом: только то, что адресовано этому чату, и с распиской в лог.
    """
    delivered = 0
    for item in getattr(envelope, "outbound", ()) or ():
        target = str(getattr(item, "target_chat_id", "") or chat_id)
        try:
            receipt = deliver_one_media(item, chat_id)
            delivered += 1
            log.info("медиа хода доставлено: %s", str(receipt)[:120])
            _ext_hook("on_delivery", chat_id, item, receipt)
        except Exception:
            log.exception("медиа хода не доставилось [%s]", target)
    return delivered


def handle_desk(message: str, room: str = STREAM, attachments: list[str] | tuple[str, ...] = (),
                 *, ingress_id: str = "") -> None:
    """Одна записка из окна — один ход агента в комнате `room`.

    `attachments` — пути картинок из подвала записки (0.5.0): они уезжают в
    медиа-спул и кладутся в кадр рядом с текстом; в памяти комнаты реплика
    владельца получает строку `[изображение: имя]` на каждую, чтобы прожитое
    не расходилось с тем, что видела модель.

    `ingress_id` — СТАБИЛЬНАЯ идентичность входа: штамп имени записки из
    inbox (20.09). Раньше source_id рождался из «сейчас», и повтор записи
    после рестарта создавал другую identity — дубли в памяти и второй ход
    по тому же поводу. Память жизни дедуплицирует по
    `window:{source_id}:{direction}`, так что стабильный id делает replay
    идемпотентным: тот же повод — та же строка жизни, не новая.
    """
    now = _now()
    source_id = str(ingress_id or "").strip() or f"{room}-{int(now.timestamp() * 1000)}"
    desk = _room(room)
    heard, pictures = _hear_attachments(list(attachments or ()))
    refs, notes = _ingest_attachments(pictures, chat_id=room, message_id=source_id)
    labels = heard + [f"[изображение: {Path(r.path).name}]" for r in refs] + notes
    if labels:
        message = (message + "\n" + "\n".join(labels)).strip()
    # Восприятие пишет память ДО кадра — как в живом раннере: кадр читает горячий
    # слой, и текущая реплика обязана быть в нём, иначе она отвечала бы на пустоту.
    desk.archive(message, outgoing=False, now=now)
    desk.life(message, direction="in", actor=_speaker, source_id=source_id, now=now)
    _turn_in_window(source_id, speaker=_speaker, room=room, origin_text=message,
                    media_refs=tuple(refs))


def _turn_in_window(source_id: str, *, speaker: str, birth: bool = False,
                    room: str = STREAM, origin_text: str = "",
                    media_refs: tuple = ()) -> str:
    """Ход в комнате окна по уже записанному в память входящему.

    `origin_text` — точный текст повода (записка владельца, текст будильника):
    он ложится в неизменяемый authority-снимок прогона и в кадр как «настоящая
    реплика» (recall по ней, а не по всей ленте). Без него читалка окна брала
    повод из журнала ходов, где `in` режется до 200 знаков, — карточка
    напоминания 08.09 показывала обрубок, раскрывать было нечего.

    -> исход хода: "spoken" (слово доехало), "silent" (её решение молчать),
    "deferred" (чекпойнт), "failed" (ход не состоялся). Исход НУЖЕН наверху:
    отметка рождения пишется только по факту состоявшегося хода, а раньше она
    писалась безусловно — и провалившийся первый ход навсегда лишал агента
    возможности представиться (born.json уже есть, рождение не повторится).

    `room` — комната окна (задача A §3): свой архив, свой горячий слой памяти
    (`_dialogue(room)`), своё имя в кадре; память жизни — общая.
    """
    desk = _room(room)
    convo = "\n".join(desk.lines(_last_n()))
    ctx = _agent.ChannelContext(chat_id=room, is_dm=True, owner=True, known=True,
                                addressed=True, title=desk.title,
                                origin_text=str(origin_text or ""))
    # Флаг ядру ставится ЗДЕСЬ, перед каждым ходом: при разборе конфига дерево ещё не
    # загружено (`_agent is None`), и выставленный там флаг молча пропадал — первая
    # проверка 08.09 показала прогон всё ещё с «without a reply hand».
    if hasattr(_agent, "BOUNDARY_DELIVERS_UNSPOKEN"):
        _agent.BOUNDARY_DELIVERS_UNSPOKEN = bool(_deliver_unspoken or birth)
    desk.sent.clear()
    started = time.time()
    _set_busy(True, chat_id=room)
    envelope = None
    try:
        envelope = _run_turn(room, convo, speaker, ctx, media_refs=media_refs)
    finally:
        _set_busy(False, str(getattr(envelope, "run_id", "") or ""))
    if envelope is None:
        desk.deliver("⚠ ход не дошёл до конца — подробности в логе движка.",
                     source_id=source_id, system=True)
        return "failed"
    import control_watch
    with control_watch.delivery(lambda: _agent._runs(), str(getattr(envelope, "run_id", "") or "")) as permitted:
        if not permitted:
            log.info("boundary suppressed by durable cancellation")
            return "failed"
        spoken = list(desk.sent)
        text = str(getattr(envelope, "text", "") or "").strip()
        run_id = str(getattr(envelope, "run_id", "") or "")
        media_count = _deliver_outbound(envelope, room)
        ending, word = ("", "")
        if not spoken and not text:
            # Рука reply молчала и конверт пуст. Медиа этого НЕ отменяет: 06.09 ход
            # со снимком экрана отдал в окно один «[файл]», а весь отчёт остался в
            # заметке хода — прежнее условие `and not media_count` считало файл
            # словом. Читаем запись хода: там либо её слово (заметка), либо её
            # решение молчать, либо ничего.
            ending, word = boundary_word(_turn_record(room))
            if ending == WORD and (birth or _deliver_unspoken):
                # Слово написано текстом, а не рукой reply: под поднятым рычагом это
                # заметка себе, и до окна она не дошла бы. Продукт по умолчанию
                # доставляет её на границе (agent.deliver_unspoken в helene.json),
                # потому что слабые модели теряют так каждое третье слово; при
                # рождении — всегда.
                text = word
                log.info("%s: слово пришло заметкой хода — доставляю на границе",
                         "рождение" if birth else "ход")
        if getattr(envelope, "deferred", False):
            # Durable-чекпойнт придержал ход до подтверждения побочного эффекта. Молчать
            # об этом нельзя: окно выглядело бы зависшим, а ход на самом деле жив.
            desk.deliver("⏸ ход приостановлен на чекпойнте и ждёт подтверждения "
                         f"(запуск {run_id}).", source_id=source_id, system=True)
        elif getattr(envelope, "failed", False):
            desk.deliver(f"⚠ ход не состоялся (запуск {run_id or 'без id'}) — "
                         "подробности в карточке хода.", source_id=source_id, system=True)
        elif not spoken and text:
            # Рычаг речи опущен (или ход закрылся текстом): реплика — возврат хода,
            # доставляем её мы. Под поднятым рычагом сюда не попадаем: слово ушло рукой.
            desk.deliver(text, source_id=source_id)
        elif not spoken and not text:
            # Владелец обязан видеть либо слово, либо явную пометку — пустое окно
            # после долгого хода читается как поломка. Пометка — плашка продукта
            # (`system`, вид `silence`): окно её показывает серым, память агента её
            # не получает, авторство ей не приписывается.
            if ending == SILENCE:
                log.info("ход %s: молчание по её решению%s", run_id,
                         f" ({word})" if word else "")
                desk.deliver(f"⋯ молчание по решению {_agent_name}"
                             + (f": {word}" if word else " (это выбор, не сбой)"),
                             source_id=source_id, system=True, kind="silence")
            elif not birth:
                # При рождении плашку кладёт _maybe_birth — со своими словами.
                log.info("ход %s: закрыт без слова для окна%s", run_id,
                         " (только файл)" if media_count else "")
                desk.deliver("⋯ ход закрыт без реплики: слова для окна в нём не было"
                             + (", только файл" if media_count else ""),
                             source_id=source_id, system=True, kind="silence")
        _close_run(envelope, room,
                   delivered_text=(text if not spoken else ""),
                   spoken_by_hand=len(spoken), media_count=media_count)
    log.info("ход %s [окно%s]: %.1f с, реплик рукой %d%s", run_id or "—",
             "" if room == STREAM else f" {room}", time.time() - started, len(spoken),
             "" if not media_count else f", медиа {media_count}")
    _compact(room)
    if getattr(envelope, "deferred", False):
        return "deferred"
    if getattr(envelope, "failed", False):
        return "failed"
    return "spoken" if (spoken or text or media_count) else "silent"


def handle_owner_note(chat_id: str, message: str) -> None:
    """Реплика владельца ИЗ ОКНА в telegram-комнату (записка `__to__` композера).

    Окно — ещё одна дверь владельца в любую его комнату (слово владельца 31.08):
    реплика записывается его именем в память комнаты, ход идёт там же, ответ
    уезжает в Telegram. В Telegram сама реплика не отправляется — бот не имеет
    права говорить чужими словами, и расписки это отличие сохраняют (source
    события жизни = window, не botapi).
    """
    if _bot is None:
        log.warning("записка адресована «%s», а бота нет — некуда везти", chat_id)
        return
    now = _now()
    _bot.rooms.describe(chat_id, title=_room_title(chat_id),
                        is_dm=bool(_bot.rooms.meta(chat_id).get("is_dm", True)),
                        sender=(_speaker, _bot.owner_id))
    _bot.rooms.record(chat_id, message, outgoing=False, sender=_speaker,
                      source_id=f"desk-{int(now.timestamp() * 1000)}",
                      ts=now.timestamp(), source="window")
    handle_bot(chat_id)


def _room_title(chat_id: str) -> str:
    """Имя комнаты, переживающее рестарт: карта в RAM → контакт-бук бота (диск).

    Без этого личка после перезапуска руннера звалась числом id — в заголовке,
    панели и записи хода, — пока Telegram не приносил новое сообщение.
    """
    known = str(_bot.rooms.meta(chat_id).get("title") or "")
    if known:
        return known
    peer = botapi.peer_thread(chat_id)[0]
    label = _bot.contacts.label(peer)
    return label if label != peer else ""


def handle_bot(chat_id: str) -> None:
    """Ход в бот-чате: сообщение уже в памяти (его записал поток приёма)."""
    meta = _bot.rooms.meta(chat_id)
    is_dm = bool(meta.get("is_dm", True))
    sender_name, sender_id = meta.get("last_sender") or ("кто-то", "")
    owner = bool(_bot.owner_id) and str(sender_id) == str(_bot.owner_id)
    convo = "\n".join(_bot.rooms.lines(chat_id, _last_n()))
    if not convo.strip():
        return
    # ⚠ `principal_id` раньше не передавался вовсе, и кадр на КАЖДОМ ходе из
    # Telegram писал «личность транспортом НЕ подтверждена» — при том, что id
    # отправителя пришёл транспортом и досье вяжется именно по нему.
    # `known` тоже перестал быть константой: человек, которого владелец не
    # заводил, не «известный» (гейт входящих — в botapi._ingest).
    ctx = _agent.ChannelContext(chat_id=chat_id, is_dm=is_dm, owner=owner,
                                principal_id=str(sender_id or "") or None,
                                known=bool(owner or _bot.is_allowed(sender_id)),
                                addressed=True,
                                title=_room_title(chat_id) or str(chat_id))
    _bot.sent_now.clear()
    started = time.time()
    _set_busy(True, chat_id=chat_id)
    # 25.09 (F): видно, что агент думает, — «печатает…» всё время хода (раньше один
    # sendChatAction на весь ход гас через ~5 с), а по желанию владельца
    # (telegram.status_message) у долгого хода — пост «думаю (ЧЧ:ММ)…».
    import turn_pulse
    pulse = turn_pulse.TurnPulse(_bot, chat_id, typing=True,
                                 status=bool(_status_message)).start()
    failed = ""
    try:
        envelope = _run_turn(chat_id, convo, sender_name, ctx)
        if envelope is None:
            failed = "ход не дошёл до конца"
    except BaseException as exc:
        failed = type(exc).__name__
        raise
    finally:
        _set_busy(False)
        pulse.stop(failed=failed)
    if envelope is None:
        if is_dm and owner:
            try:
                _bot.deliver_text(chat_id, "⚠ ход не дошёл до конца — "
                                           "подробности в логе движка.")
            except Exception:
                log.exception("не доложила владельцу о падении хода")
        return
    import control_watch
    with control_watch.delivery(lambda: _agent._runs(), str(getattr(envelope, "run_id", "") or "")) as permitted:
        if not permitted:
            log.info("boundary suppressed by durable cancellation")
            return
        spoken = [t for c, t in _bot.sent_now if c == str(chat_id)]
        text = str(getattr(envelope, "text", "") or "").strip()
        run_id = str(getattr(envelope, "run_id", "") or "")
        media_count = _deliver_outbound(envelope, chat_id)
        if getattr(envelope, "deferred", False) or getattr(envelope, "failed", False):
            # Чужим людям внутренности не выкладываем — как в живом раннере: сбой
            # виден в карточке хода и логе, владельцу в личке — словами.
            state = "приостановлен" if getattr(envelope, "deferred", False) else "не состоялся"
            log.warning("ход %s [бот %s]: %s", run_id or "—", chat_id, state)
            if is_dm and owner:
                try:
                    _bot.deliver_text(chat_id, f"⚠ ход {state} (запуск {run_id}).")
                except Exception:
                    log.exception("не доложила владельцу о сбое хода")
        if not spoken and not text:
            # ⚑ 17.09. ТО ЖЕ ВОССТАНОВЛЕНИЕ, ЧТО У ОКНА (см. `_turn_in_window`), которого у
            # бота не было — и из-за этого «бот молчит» выглядело её решением.
            #
            # Как это происходило. Под поднятым контрактом руки ядро возвращает конверт БЕЗ
            # текста: последний текст хода — заметка себе, наружу он не идёт. Окно на этом
            # месте читает запись хода и, если слово там всё-таки есть, доставляет его
            # границей. Бот же падал прямиком в строку «она промолчала (это её решение, не
            # сбой)» и писал в расписку `silent_reason="agent chose silence"` — то есть
            # называл её решением ровно то, чего она не решала: слово было написано, просто
            # не рукой. На слабых моделях так терялось каждое третье слово.
            ending, word = boundary_word(_turn_record(chat_id))
            if ending == WORD and _deliver_unspoken:
                text = word
                log.info("ход %s [бот %s]: слово пришло заметкой хода — доставляю на границе",
                         run_id or "—", chat_id)
        delivered_boundary = ""
        if not spoken and text:
            try:
                _bot.deliver_text(chat_id, text)
                delivered_boundary = text
            except Exception:
                log.exception("возврат хода не доставился [%s]", chat_id)
        elif not spoken and not text and not media_count:
            log.info("ход %s [бот %s]: она промолчала (это её решение, не сбой)",
                     run_id, chat_id)
        _close_run(envelope, chat_id, delivered_text=delivered_boundary,
                   spoken_by_hand=len(spoken), media_count=media_count)
    log.info("ход %s [бот %s]: %.1f с, реплик рукой %d%s", run_id or "—", chat_id,
             time.time() - started, len(spoken),
             "" if not media_count else f", медиа {media_count}")
    _compact(chat_id)


def _write_anatomy(tree: Path, cfg: dict) -> None:
    """Снимок устройства для вкладки «Устройство» в окне — КОДОМ, не пересказом.

    Прозрачность — стержень продукта (слово владельца 31.08: «ясный список тулов,
    что они делают, чтение скиллов и их место, условие вызова, нутрянка
    простыми словами»). Список рук берётся у ЕЁ сборщика offered_tools_for —
    того же, что собирает руки модели (контракт A1: один ответ на вопрос «что
    у неё есть»). Описания — те же байты, что читает модель: это и есть
    «условие вызова» руки, других условий нет.
    """
    try:
        ctx = _agent.ChannelContext(chat_id=STREAM, is_dm=True, owner=True,
                                    known=True, addressed=True, title=_title)
        rows = []
        for tool in _agent.offered_tools_for(ctx):
            schema = tool.get("input_schema") or {}
            rows.append({
                "name": tool.get("name") or f"[{tool.get('type') or 'hosted'}]",
                "desc": str(tool.get("description") or ""),
                "params": sorted((schema.get("properties") or {}).keys()),
                "required": list(schema.get("required") or ()),
            })
        skills_index = ""
        try:
            skills_index = (tree / "soul" / "skills" / "INDEX.md").read_text(
                encoding="utf-8")
        except OSError:
            pass
        telegram = dict(cfg.get("telegram") or {})
        anatomy = tree / "memory" / ".state" / "anatomy.json"
        _write_json(anatomy, {
            "written_at": _now().isoformat(timespec="seconds"),
            "agent_name": boot.agent_name(cfg),
            "owner_name": boot.owner_name(cfg),
            # ⚠ Здесь стоял список ИМЁН: `if k not in ("key", "api_key")`. Окно
            # завело рядом новое поле `model.keys` = {api, anthropic, chatgpt} —
            # фильтр его не знал, и все боевые ключи владельца поехали в этот
            # файл, то есть в GET /api/anatomy и на экран «Система». Перечисление
            # имён всегда проигрывает соседу, который заводит поле; `public_model`
            # чистит по имени И по форме значения, рекурсивно.
            "model": boot.public_model(cfg),
            # Запрос инженера установщика (доска, repair/setup.md): окно считало
            # мозг настроенным по одному лишь ИМЕНИ модели и рисовало «На связи»
            # над ходом, который упадёт без ключа. Ключ показывать нельзя, а факт
            # «ключ есть» и приговор самого дерева — можно и нужно.
            "has_key": bool(str((cfg.get("model") or {}).get("key")
                                or (cfg.get("model") or {}).get("api_key") or "").strip()),
            "brain_ready": _brain_ready(),
            # ⚠ Здесь стояло `boot.env_knobs(cfg)` — ручки среды ДОСЛОВНО, вместе
            # с тем, что владелец в них кладёт: OPENAI_API_KEY, токен бота, пароль
            # SMTP. Этот файл отдаётся по GET /api/anatomy, уезжает на телефон и
            # РИСУЕТСЯ ТАБЛИЦЕЙ на экране «Система» — то есть виден на скриншоте и
            # при демонстрации экрана. Строкой выше ключ из блока model вырезался,
            # а тут печатался: закрыта была ровно половина.
            "knobs": boot.safe_knobs(cfg, tree),
            "git": _git_state(tree),
            "transports": (["окно Hélène"]
                           + (["Telegram-аккаунт"] if str(telegram.get("mode") or "bot") == "account"
                              and telegram.get("api_id") else
                              ["Telegram-бот"] if telegram.get("bot_token") else [])),
            # Режим виден и владельцу (экран «Система»), и АГЕНТУ: анатомия —
            # его же снимок устройства. Знать, заперт ли он в своей папке и
            # доступны ли ему окна, он обязан из прибора, а не из догадки.
            "mode": modes.describe(_mode) if _mode else None,
            "sandbox": _sandbox_state(),
            # Тело руки `computer`: включено ли владельцем, какие права, порт
            # моста и — если сторож уже спросил — подключено ли. Снимок на
            # старте; живое состояние окно берёт из memory/.state/body.json.
            "computer": _computer_state(),
            # Брокер: есть ли у агента рука, слушает ли просьбы окно, сколько
            # их ждёт ответа. Снимок пишется на старте — «ждущих 0» здесь
            # значит «столько было при запуске», а не «сейчас».
            "broker": broker.state(),
            "tools": rows,
            "skills_index": skills_index,
        })
        # Вторая дверь к тому же файлу: он лежит в `memory`, которая выдана
        # контейнеру рекурсивно на изменение. Права ставим ПОСЛЕ записи — запись
        # идёт через os.replace, и новый файл наследует права папки, то есть
        # прежнее сужение теряется на каждом старте.
        _shut_anatomy(anatomy)
        log.info("устройство: %d рук записано для окна", len(rows))
    except Exception:
        log.exception("анатомия не записалась (продукт работает дальше)")


def _shut_anatomy(path: Path) -> None:
    """Закрыть снимок устройства от песочницы (если она поднята)."""
    try:
        import fence
        if fence.state().get("container"):
            # Секретов в этом файле больше нет, поэтому чтение остаётся всем
            # вошедшим в систему: под службой руннер работает от LocalSystem, и
            # сужение «на себя» отняло бы анатомию у окна владельца.
            fence.shut_out_container(path, share_read=True)
    except Exception:
        log.warning("анатомия не закрыта от песочницы", exc_info=True)


def _say_tree_is_busy(tree: Path, cfg: dict, holder: dict) -> None:
    """Сказать владельцу, что дерево занято другой копией. И замолчать до смены копии.

    ⚠ Молчание здесь и есть худшая половина дефекта. Адверсарий поднял двух
    руннеров на одном дереве: рождение случилось дважды, ходы перемешались в
    ленте, записка владельца досталась одной копии и осталась без ответа у
    другой — и НИ ОДИН лог не сказал о второй копии ни слова. Владелец видит не
    «два агента», а «агент сломался», и назвать причину ему нечем.

    Оболочка поднимает упавшего ребёнка снова (с растущей паузой), поэтому
    плашка пишется ОДИН РАЗ на владельца замка: иначе лента владельца забилась
    бы повторами быстрее, чем он успел бы прочесть первую.
    """
    pid = holder.get("pid")
    log.error("дерево %s занято другой копией кода агента (pid %s, %s) — не поднимаюсь",
              tree, pid, holder.get("host") or "этот компьютер")
    said = tree / "memory" / ".state" / "harness_busy.json"
    try:
        seen = json.loads(said.read_text(encoding="utf-8"))
        if isinstance(seen, dict) and seen.get("pid") == pid:
            return                       # об этой копии уже сказано
    except (OSError, ValueError):
        pass
    try:
        desk = transport.Desk(tree, STREAM, boot.owner_name(cfg),
                             str((cfg.get("owner") or {}).get("room") or "Hélène"),
                             agent_name=boot.agent_name(cfg))
        desk.deliver(
            f"⚠ Вторая копия {boot.agent_name(cfg)} не запустилась: этой памятью уже "
            f"занят другой запущенный экземпляр (процесс {pid}). Так бывает, когда "
            "программа открыта дважды или агента держит служба Windows. Работай в том "
            "окне, которое уже открыто; если оно закрыто — сними службу на экране "
            "«Система» или заверши процесс и запусти снова.", system=True)
        _write_json(said, {"pid": pid, "at": time.time()})
    except Exception:
        log.exception("не сказал владельцу, что дерево занято")


_CONFIG_WATCH_SEC = float(os.getenv("HELENE_CONFIG_WATCH_SEC", "5") or 5)


def _config_watch_forever(config_path: Path, tree: Path) -> None:
    """helene.json — на лету (25.09, C.1): смена мозга применяется без снятия службы.

    Раньше мозг проецировался в её llm.json один раз на старте (`project_brain` в
    main), и «Сохранено» в окне честно значило «после перезапуска»; под службой это
    выливалось в «сними и поставь службу заново» (Сергей, Mac). Ядро при этом
    перечитывает llm.json само на каждом вызове (llm._config по mtime) — не хватало
    только проекции. Тик — по mtime файла; проекция идемпотентна (отпечаток блока
    модели в расписке), и её собственный выбор `switch_brain` не затирается.
    """
    global _deliver_unspoken
    try:
        seen = config_path.stat().st_mtime_ns
    except OSError:
        seen = None
    while True:
        time.sleep(_CONFIG_WATCH_SEC)
        try:
            now = config_path.stat().st_mtime_ns
        except OSError:
            continue
        if now == seen:
            continue
        seen = now
        try:
            cfg = json.loads(boot.read_config_text(config_path))
            if not isinstance(cfg, dict):
                raise ValueError("верхний уровень должен быть объектом {…}")
        except (OSError, ValueError) as exc:
            log.warning("helene.json изменился, но не читается (%s) — мозг не трогаю", exc)
            continue
        try:
            log.info("helene.json изменился — %s", boot.project_brain(tree, cfg))
        except Exception:
            log.exception("мозг из изменённого helene.json не спроецировался")
        _deliver_unspoken = bool((cfg.get("agent") or {}).get("deliver_unspoken", True))
        if _agent is not None and hasattr(_agent, "BOUNDARY_DELIVERS_UNSPOKEN"):
            _agent.BOUNDARY_DELIVERS_UNSPOKEN = bool(_deliver_unspoken)
        # Ревью 25.09 (A6 F11): галочка «Пост «думаю…»» тоже читается на тике — расписка
        # окна обещает применение без перезапуска, и движок обязан это держать.
        global _status_message
        _status_message = bool((cfg.get("telegram") or {}).get("status_message", False))


_LOW_PRIORITY = {"depth": 0, "before": 0}
_LOW_PRIORITY_LOCK = None


def _low_priority():
    """На время тяжёлой работы процессора (расшифровка, прогрев модели) — ниже обычного.

    26.09, у мамы Егора: четыре потока whisper на домашней машине забирали процессор
    целиком, и окно дёргалось. Ниже обычного — значит окно и всё, что делает человек,
    идут первыми, а расшифровка берёт остаток; на её длине это почти не сказывается.
    Только Windows: на POSIX nice обратно не поднять. Вложенные входы считаются —
    восстанавливает приоритет последний вышедший.
    """
    import contextlib
    import threading
    global _LOW_PRIORITY_LOCK
    if _LOW_PRIORITY_LOCK is None:
        _LOW_PRIORITY_LOCK = threading.Lock()

    @contextlib.contextmanager
    def scope():
        if os.name != "nt":
            yield
            return
        import ctypes
        kernel = ctypes.windll.kernel32
        # Псевдо-хендл процесса — 64-битный (-1): без явных типов ctypes режет его до
        # 32 бит, и Get/SetPriorityClass молча отвечают 0.
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
        kernel.GetPriorityClass.restype = ctypes.c_uint32
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.SetPriorityClass.restype = ctypes.c_int
        handle = kernel.GetCurrentProcess()
        below_normal = 0x4000
        with _LOW_PRIORITY_LOCK:
            _LOW_PRIORITY["depth"] += 1
            if _LOW_PRIORITY["depth"] == 1:
                before = int(kernel.GetPriorityClass(handle) or 0)
                _LOW_PRIORITY["before"] = before if before and before != below_normal else 0
                if _LOW_PRIORITY["before"]:
                    kernel.SetPriorityClass(handle, below_normal)
        try:
            yield
        finally:
            with _LOW_PRIORITY_LOCK:
                _LOW_PRIORITY["depth"] -= 1
                if _LOW_PRIORITY["depth"] == 0 and _LOW_PRIORITY["before"]:
                    kernel.SetPriorityClass(handle, _LOW_PRIORITY["before"])
                    _LOW_PRIORITY["before"] = 0
    return scope()


def _warm_voice_models(cfg: dict) -> None:
    """Модели голоса — в память со старта, если владелец велел держать слух между голосовыми.

    Иначе первое голосовое дня ждало загрузки полутора гигабайт (у мамы Егора 26.09 —
    14 с на семь секунд речи). Ниже обычного приоритета и в своём потоке: старт движка
    и первый ход этого не ждут. Голос ответа (piper) греется там же, если включён, —
    через загрузчик голоса, а не через синтез: пробный файл в media/tts не нужен.
    """
    import threading
    voice_cfg = dict(cfg.get("voice") or {})
    stt = bool(_voice_state.get("ready")) and bool(voice_cfg.get("keep_loaded"))
    tts = bool(os.environ.get("PRAXIS_PIPER_MODEL"))
    if not (stt or tts):
        return

    def run() -> None:
        said: dict = {}
        try:
            import importlib
            media_audio = importlib.import_module("media_audio")
            with _low_priority():
                if stt:
                    said.update(media_audio.warm(stt=True, tts=False))
                if tts:
                    speaker = media_audio.get_default_backend().tts
                    loader = getattr(speaker, "_get_voice", None) or getattr(
                        getattr(speaker, "primary", None), "_get_voice", None)
                    if callable(loader):
                        loader()
                        said["tts"] = "loaded"
                    else:
                        said["tts"] = "no-loader"
        except Exception:
            log.debug("прогрев голоса не прошёл — загрузится при первом голосовом", exc_info=True)
            return
        log.info("голос: прогрев со старта — %s",
                 ", ".join(f"{k}: {v}" for k, v in said.items()) or "нечего греть")

    threading.Thread(target=run, name="voice-warm", daemon=True).start()


def _desk_control():
    """`deskd.control` — файловый протокол надзора, общий с каналом и serverboot."""
    app_dir = Path(__file__).resolve().parent.parent
    if str(app_dir) not in sys.path:
        sys.path.insert(1, str(app_dir))
    from deskd import control  # noqa: PLC0415
    return control


def _supervisor_forever(tree: Path) -> None:
    """Движок — исполнитель просьбы «перезапустить» на этой машине (26.09).

    До этого просьба из окна (`/api/supervisor/restart`) на Windows и Mac получала отказ
    «надзора нет — перезапуск делается кнопкой окна», а кнопка окна под службой была
    нулевым действием: окно детей службы не держит. Теперь движок сам бьётся в записке
    надзора, берёт просьбу и выходит кодом RESTART_EXIT_CODE между ходами; поднимает
    его тот, кто держит, — окно или служба, — с перечитанными настройками. Реле движок
    не держит: его оболочка и служба перечитывают на лету сами.
    """
    try:
        control = _desk_control()
    except Exception:
        log.exception("протокол надзора не загрузился — просьбы о перезапуске не читаются")
        return
    while True:
        try:
            _supervisor_tick(tree, control)
        except Exception:
            log.debug("тик надзора не прошёл", exc_info=True)
        time.sleep(2.0)


def _supervisor_tick(tree: Path, control) -> bool:
    """Один тик: записка о себе, просьба со стола, расписка. -> взведён ли перезапуск."""
    control.beat(tree, "engine", _STARTED_UTC,
                 [{"role": "runner", "pid": os.getpid(), "busy": bool(_busy["busy"])}])
    request = control.take_request(tree)
    if not request or str(request.get("action") or "") != "restart":
        return False
    target = str(request.get("target") or "")
    if target in ("runner", "all"):
        _restart_wanted[0] = True
        control.receipt(tree, request, True,
                        "движок выйдет между ходами и поднимется заново с перечитанными "
                        "настройками")
        log.warning("надзор: просьба перезапустить (%s, от %s) — выйду между ходами",
                    target, request.get("by") or "owner")
        return True
    if target == "relay":
        control.receipt(tree, request, False,
                        "реле держит не движок: оболочка и служба применяют его настройки "
                        "на лету сами")
    else:
        control.receipt(tree, request, False, f"так перезапускать нечего: {target}")
    return False


def _heartbeat_forever(inbox: Path) -> None:
    """Квитанция читателя — фоном и с занятостью.

    deskapp по ней решает две вещи: жив ли руннер (возраст `at`) и думает ли агент
    прямо сейчас (`busy`). Раньше квитанция писалась из главного цикла — и во
    время долгого хода старела, так что окно считало живой руннер мёртвым.
    """
    while True:
        try:
            # Тем же ударом подтверждаем замок на дереве: живой владелец замка
            # обязан отмечаться, иначе брошенный замок держал бы дерево вечно.
            boot.refresh_tree_lock()
            _write_json(inbox / ".reader.json", {
                "pid": os.getpid(), "at": time.time(),
                "busy": bool(_busy["busy"]), "run": str(_busy["run"] or ""),
                "since": float(_busy["since"] or 0.0), "chat_id": _busy["chat_id"]})
        except Exception:
            # ⚠ Было log.debug при жёстко прибитом уровне INFO: окно рисовало
            # красное «Не запущен» над живым руннером, а в собранном владельцем
            # логе не было НИ ОДНОЙ строки о причине. Частота ограничена
            # интервалом сердцебиения — залива не будет.
            log.warning("квитанция читателя не записалась", exc_info=True)
        time.sleep(_HEARTBEAT_SEC)


def _retention_forever() -> None:
    """Ретенция прогонов — суточным тиком, потому что вешать её больше некуда.

    ⚠ В ЯДРЕ ЭТОГО МЕСТА НЕТ. Там ретенцию зовёт `sleep.run_scheduled`, а в издании
    `run_scheduled` не зовёт никто из работающего кода: он есть только в
    `mtproto_runner.py`, которого продуктовый харнесс не запускает. То есть перенос
    модуля без своей точки запуска дал бы «ретенция есть», а на диске — ничего.
    Замер прода 12.09: 29,5 ГБ в прогонах, из них 28,7 ГБ — снимки `results/`,
    +1 ГБ в сутки.

    Тик — сутки, и первый проход через минуту после старта: окно чаще всего
    открывают и закрывают, и ретенция, назначенная «ночью», не случилась бы никогда.
    """
    first = True
    while True:
        time.sleep(60.0 if first else 24 * 3600.0)
        first = False
        try:
            if str(os.getenv("PRAXIS_RUNS_RETENTION", "on") or "on").strip().lower() in {
                    "0", "off", "false", "no"}:
                continue
            import runs_prune
            budget = float(os.getenv("PRAXIS_RUNS_RETENTION_BUDGET", "600") or 600)
            report = runs_prune.prune(Path(_tree) if _tree else None, budget_seconds=budget)
            line = runs_prune.report_line(report)
            # Молчать нельзя в обе стороны: и когда сняли, и когда не тронули
            # ничего. Именно молчание держало незамеченным то, что на Windows
            # ретенция не работала вовсе.
            log.info("ретенция запусков: %s", line)
            for err in (report.get("errors") or [])[:3]:
                log.warning("ретенция запусков: %s", err)
        except Exception:
            log.warning("ретенция запусков не прошла", exc_info=True)


def _keat_care_forever() -> None:
    """Ротация эпох КЕАТ (1.0.0) — раз в час, первый проход через минуту после старта.

    Снимки обслуженных вызовов пишутся на КАЖДЫЙ вызов модели; суточного тика ретенции
    прогонов им мало (у неё в проде это 8,5 ГБ за две недели). Сама ротация —
    `boot.prune_keat_epochs`: эпоха целиком, свежие и идущие не трогаются.
    """
    first = True
    while True:
        time.sleep(60.0 if first else 3600.0)
        first = False
        if _tree is None:
            continue
        try:
            report = boot.prune_keat_epochs(Path(_tree))
            if report.get("removed") or report.get("errors"):
                log.info("КЕАТ: эпох снято %d (%.1f МБ), оставлено %d%s",
                         report.get("removed", 0), report.get("freed_bytes", 0) / 1048576,
                         report.get("kept", 0),
                         f"; ошибки: {report['errors'][:3]}" if report.get("errors") else "")
        except Exception:
            log.warning("КЕАТ: ротация эпох не прошла", exc_info=True)


def _recall_care_forever() -> None:
    """Заявки на пересборку индекса памяти — раз в 15 минут, первый проход через 90 с.

    ⚠ С 1.0.1 (её memory_fts v8, 13.09) явный recall больше НЕ пересобирает индекс сам:
    отсутствующая, занятая или отставшая база оставляет заявку `recall_refresh.json` и
    отвечает тем, что есть, а исполняет заявку обслуживание — `memory_index.build` под
    замком сборщика. У неё это ночь; в издании ночь редкость (окно на ночь закрывают),
    и без этого потока вопрос к памяти после сбоя базы отвечал бы пустотой до ближайшего
    сна. База свежей установки строится здесь же, а не в первом вопросе к памяти.
    Поток, а не шаг главного цикла: пересборка — минуты на большом корпусе, ходы ждать
    её не должны; двух сборщиков не бывает — это держит замок сборщика в `memory_fts`.
    """
    first = True
    while True:
        time.sleep(90.0 if first else _RECALL_CARE_SEC)
        first = False
        _recall_care_once()


def _recall_care_once() -> str:
    """Один проход обслуживания индекса памяти. -> idle | built | busy | failed."""
    if _agent is None:
        return "idle"
    try:
        import memory_fts
        import memory_index
        mem = Path(memory_index.MEM_DIR)
        database = mem / ".state" / "recall.sqlite3"
        if database.exists() and not memory_fts.refresh_requested(memory_dir=mem):
            return "idle"
        started = time.monotonic()
        built = memory_index.build()
        fts = built.get("fts") or {}
        if built.get("fts_error"):
            # Ревью 26.09 (W3 S10, W2 S2): сбой пересборки — не «другой сборщик».
            log.warning("память: пересборка индекса упала (%s) — заявка сохранена, повтор "
                        "через %d мин", built["fts_error"], int(_RECALL_CARE_SEC // 60))
            return "failed"
        if not fts:
            log.info("память: пересборку индекса держит другой сборщик — повторю "
                     "через %d мин", int(_RECALL_CARE_SEC // 60))
            return "busy"
        log.info("память: индекс пересобран по заявке за %.1f с — источников %s, "
                 "фрагментов %s", time.monotonic() - started, fts.get("sources"),
                 fts.get("chunks"))
        return "built"
    except Exception:
        log.warning("память: заявка на пересборку индекса не обслужена (повтор через "
                    "%d мин)", int(_RECALL_CARE_SEC // 60), exc_info=True)
        return "failed"


def _sleep_cycle_on() -> bool:
    return str(os.getenv("PRAXIS_SLEEP_CYCLE", "on") or "on").strip().lower() not in {
        "0", "off", "false", "no"}


def _sleep_due() -> None:
    """Её ночной цикл (`sleep.run_scheduled`) — шагом главного цикла, МЕЖДУ ходами.

    ⚠ ДО 1.0.1 СНА В ИЗДАНИИ НЕ БЫЛО ВОВСЕ. В ядре его заводят часы `mtproto_runner`
    (`_sleep_once` → `sleep.run_scheduled` под общим замком `_ONE_MIND`), а продуктовый
    харнесс этот раннер не запускает. 17.09 отсюда достали только ретенцию прогонов
    (`_retention_forever`); консолидация дня, формирование, ночная ревизия характера,
    мётла inbox, сводка прожитого дня, карта памяти с её индексом и инвентаризация
    компьютера не случались никогда — и нигде не было решения, что так надо.

    Шаг главного цикла, а не поток: сон переписывает то, из чего она думает, и обязан
    идти между ходами — ровно как у неё под `_ONE_MIND`. Цена та же, что у неё: пока идёт
    сон, ответ ждёт его конца. Когда — решает её `sleep.due`: окно PRAXIS_SLEEP_WINDOW
    (4–6 по часам машины — `boot.local_tz_name`) и не чаще раза в ~20 ч; если компьютер в
    это время спал или программа была выключена — догон через 48 ч, но не раньше десяти
    минут после старта. Пауза фона (`appetite.background_hold`) откладывает сон её же
    правилом внутри `run_scheduled`. Выключатель — PRAXIS_SLEEP_CYCLE=off.

    ⚠ Ревью 26.09 (W3 S1): сверх её правил сон издания ждёт тишины владельца
    (`_SLEEP_IDLE_SEC`, 20 минут без ходов) — иначе догон через 48 ч приходился на
    середину разговора, и агент немел на время сна, пока владелец за компьютером.
    """
    if _agent is None or not _brain_ready() or not _sleep_cycle_on():
        return
    import sleep as tree_sleep
    if not tree_sleep.due(None, _STARTED_AT):
        return
    quiet = time.time() - _LAST_ACTIVITY[0]
    if quiet < _SLEEP_IDLE_SEC:
        log.info("сон: пора, но владелец был рядом %d мин назад — жду %d мин тишины",
                 int(quiet // 60), int(_SLEEP_IDLE_SEC // 60))
        return
    log.info("сон: пора — ночной цикл начинается")
    _set_busy(True, "sleep")
    try:
        summary = tree_sleep.run_scheduled()
    finally:
        _set_busy(False)
    log.info("сон: %s", str(summary or "")[:1200])


def _sleep_seed_once() -> None:
    """Отсчёт сна — с первого запуска этой установки, а не «сна не было никогда».

    26.09 (ревью W3 S1): до 1.0.1 сна в издании не было, `sleep.json` нет, её `last_run_ts`
    — ноль, и у каждого обновившегося первый сон начинался через полчаса после старта —
    днём, посреди работы. Теперь первый сон — в ближайшее окно по часам машины."""
    if _agent is None or not _sleep_cycle_on():
        return
    try:
        import sleep as tree_sleep
        if tree_sleep.STATE_PATH.exists():
            return
        now = time.time()
        tree_sleep._state_save(last_run_ts=now, seeded_at=now,
                               seeded_by="helene: отсчёт с первого запуска")
        log.info("сон: отсчёт начат с этого запуска — первый сон в ближайшее окно")
    except Exception:
        log.warning("сон: отсчёт не засеялся — первый сон решит её умолчание", exc_info=True)


def _set_busy(on: bool, run: str = "", *, chat_id: str = "") -> None:
    # Тишина владельца для сна: начало любого хода и конец НАСТОЯЩЕГО хода. Холостое
    # `_set_busy(False)` (его зовёт `_resume_due` на каждом проходе) тишину не нарушает.
    if (on and str(run or "") != "sleep") or (
            not on and _busy["busy"] and _busy["run"] != "sleep"):
        _LAST_ACTIVITY[0] = time.time()
    _busy["busy"], _busy["run"] = bool(on), str(run or "")
    _busy["since"] = time.time() if on else 0.0
    _busy["chat_id"] = str(chat_id) if on else ""
    if _tree is None:
        return
    try:
        inbox = Path(_tree) / "memory" / ".control" / "desk_inbox"
        _write_json(inbox / ".reader.json", {
            "pid": os.getpid(), "at": time.time(),
            "busy": bool(_busy["busy"]), "run": _busy["run"], "since": _busy["since"],
            "chat_id": _busy["chat_id"]})
    except Exception:
        # Голое `pass` здесь означало, что «окно считает руннер мёртвым» —
        # диагноз без единой строки в логе. Молчать об этом нельзя.
        log.warning("квитанция занятости не записалась", exc_info=True)


def _resume_due() -> None:
    """Следующий шаг уже существующих задач — в том же потоке, что окно и бот."""
    if _continuity is None or not _brain_ready():
        return
    try:
        _continuity.resume_due()
    finally:
        _set_busy(False)


_BIRTH_NOTE = (
    "Это твой первый запуск {where}. Ты — {agent}, твой владелец — {owner}, "
    "и {owner} говорит с тобой в этом окне.\n\n"
    "Где ты: твой дом — папка {home}. В ней soul/ (конституция и навыки), memory/ "
    "(память: пока пустая), workspace/ (рабочая папка; здесь начинается рука shell). "
    "Рядом — tree/ (твой код) и app/ (программа, которая тебя поднимает). Сервера, "
    "докера и папки /app здесь нет: если описание руки или навык говорят о них — это "
    "наследство твоего кода, а не этот компьютер.\n\n"
    "Осматриваться командами не нужно: карта уже перед тобой, а подробности — в "
    "навыке home-map и в КАК-УСТРОЕН-HELENE.md. Поздоровайся с {owner} в двух-трёх "
    "фразах: кто ты, что умеешь здесь, — и спроси, с чего начать.")
# Исходы `end_turn` (done | wait | blocked) — машинная строка конца хода, а не речь.
_OUTCOME_PREFIXES = ("done", "wait", "blocked")


def _tail_lines(path: Path, count: int) -> list[str]:
    """Последние `count` строк файла, не читая его целиком.

    ⚠ `read_text().splitlines()` на turns.jsonl — это чтение всего файла ради
    пяти строк (замер: 38 МБ за 131 мс против 12 мс у хвоста), и оно же роняло
    доставку слова UnicodeDecodeError на одном битом байте В НАЧАЛЕ файла.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return []
    chunk = min(size, max(65536, count * 16384))
    try:
        with path.open("rb") as src:
            src.seek(max(0, size - chunk))
            raw = src.read()
    except OSError:
        return []
    # Ревью 26.09 (W3 S6): только "\n" — U+2028 в тексте реплики не рвёт запись хода, и
    # граница хода не берётся у прошлого хода.
    lines = [ln.rstrip("\r") for ln in raw.decode("utf-8", "replace").split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines[-max(1, int(count)):]


def _turn_record(chat_id: str) -> dict:
    """Запись только что состоявшегося хода этой комнаты из её же turns.jsonl.

    Дерево пишет запись (`turns.record`) ДО того, как вернуть конверт, поэтому на
    границе она уже лежит последней строкой этой комнаты. Хвост в 12 строк — с
    запасом на ходы других комнат, которые могли лечь между стартом и концом.
    """
    if _tree is None:
        return {}
    lines = _tail_lines(Path(_tree) / "memory" / ".state" / "turns.jsonl", 12)
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and str(row.get("chat_id") or "") == str(chat_id):
            return row
    return {}


# Как ход кончился, если рука reply не говорила и конверт пуст (см. boundary_word).
WORD = "word"          # у неё было слово — доставляем его как её реплику
SILENCE = "silence"    # позвала stay_silent: молчание — её решение
BLANK = "blank"        # ход закрылся без слова для окна — факт, не решение


def boundary_word(row: dict) -> tuple[str, str]:
    """Что показать владельцу, когда ход кончился без реплики рукой. -> (исход, текст).

    Правило дерева (agent.py, «три исхода, а не два»): `held=voice` — она позвала
    stay_silent, это её слово; `held=unspoken` — ход кончился без руки reply, и это
    факт, а не решение. Во втором случае её текст обычно лежит в `note`: под
    поднятым рычагом reply финальный текст модели — заметка себе, и слабые модели
    кладут туда ВЕСЬ ответ («done: проверила руку computer по шагам… всё удалось»).
    Оставить владельца без этого текста — значит показать пустое окно после
    двухминутного хода; так и было 06.09 (два хода из четырёх).

    ⚠ НО заметкой бывает и МАШИННАЯ строка исхода: `end_turn(done)` кладёт в то
    же поле «done», «done: представилась», «wait: <условие>». Прежнее правило
    глотало ВСЮ заметку, если она начиналась с исхода и в следе был end_turn, —
    и вместе с «done» глотало отчёт после двоеточия (а след рук режется
    потолком записи, и end_turn в нём не всегда виден). Теперь исход снимается
    как префикс (и повторно: дерево пишет «done: done: …»), а решает остаток:
    есть в нём хотя бы два слова — это речь, доставляем; одно слово или пусто —
    машинная строка, владельцу показывается плашка, а не «done».
    """
    held = str(row.get("held") or "").strip().lower()
    if held == "voice":
        return SILENCE, " ".join(str(row.get("why") or "").split())
    if held != "unspoken":
        return BLANK, ""
    note = " ".join(str(row.get("note") or "").split())
    label = ""
    for _ in range(3):
        head, sep, rest = note.partition(":")
        if not sep or head.strip().lower() not in _OUTCOME_PREFIXES:
            break
        label = label or head.strip().lower()
        note = rest.strip()
    # A short answer without an outcome prefix is still speech ("Спасибо",
    # a code, a number). Only actual outcome markers and their one-word
    # machine recap keep the pre-existing plaque behavior.
    if not note or note.lower() in _OUTCOME_PREFIXES or (label and len(note.split()) < 2):
        return BLANK, ""
    if label == "wait":
        note = "Жду: " + note
    elif label == "blocked":
        note = "Препятствие: " + note
    return WORD, note


_BIRTH_TRIES = 5           # столько попыток первого хода, дальше — словами владельцу


def _birth_note(*, note_written: bool = False) -> str:
    """Записка первого запуска в память комнаты. -> source_id для хода.

    Это обычный ход в комнате окна, только записка приходит не от владельца, а от
    Hélène: так она честно лежит в памяти как событие первого запуска, а ответ
    идёт тем же путём, что и любой другой (рука reply в окно; возврат хода —
    доставляем мы).

    `note_written=True` — записка уже в памяти с прошлой, недоведённой попытки:
    класть её второй раз нельзя, иначе новорождённый читает в своей ленте, что он
    рождается второй раз (живой прогон: три записки на три попытки).
    """
    now = _now()
    source_id = f"birth-{int(now.timestamp() * 1000)}"
    if not note_written:
        # Предлог — внутри подстановки: при пустом platform.node() шаблон
        # «на устройстве {device}» давал «на устройстве этот компьютер».
        device = platform.node()
        # 26.09: паспорт вместо «осмотрись». Первый ход у мамы Егора ушёл на поиски
        # своей папки по чужим адресам (135 с, семь команд, `/app`): агент офигевал,
        # кто он и где. Теперь дом, имена и границы названы в самой записке.
        note = _BIRTH_NOTE.format(where=f"на устройстве {device}" if device
                                  else "на этом компьютере",
                                  agent=_agent_name, owner=_speaker,
                                  home=str(_tree) if _tree else "рядом с программой")
        _desk.archive(note, outgoing=False, now=now, sender="Hélène")
        _desk.life(note, direction="in", actor="Hélène", source_id=source_id, now=now)
    return source_id


def _maybe_birth(tree: Path) -> None:
    """Рождение — ровно один РАЗ и ровно по факту состоявшегося хода.

    ⚠ Здесь было два зеркальных дефекта одного шва.
      * Отметка писалась БЕЗУСЛОВНО после `handle_birth()`, а провал хода наверх
        не уходит никогда (`_run_turn` ловит всё сам). Реле на холодной машине не
        успевало подняться -> APIConnectionError -> в окне «⚠ ход не состоялся» и
        в логе «рождение: первый ход состоялся». Перезапуск не помогал: born.json
        уже написан, представиться агент не мог никогда, и лечилось это только
        удалением файла руками, о чём нигде не сказано.
      * Записка рождения ложилась в память ДО хода, а отметка после: убитый
        посреди первого хода руннер рождал агента заново, со второй запиской.
    Теперь отметка — это состояние: `in_progress` до хода (защищает от второй
    записки), `done` — только когда ход СОСТОЯЛСЯ, `gave_up` после пяти попыток,
    и тогда владельцу говорится словами, что делать.
    """
    marker = tree / "memory" / ".state" / "born.json"
    state: dict = {}
    if marker.exists():
        try:
            loaded = json.loads(marker.read_text(encoding="utf-8"))
            state = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            state = {}
        # Старая отметка (без поля state) — рождение состоялось по прежним правилам.
        if str(state.get("state") or "done") in ("done", "gave_up"):
            return
    try:
        import llm
        if not llm.configured():
            log.info("рождение отложено: мозг не настроен — представлюсь при первом "
                     "запуске с ключом")
            return
    except Exception:
        log.warning("рождение отложено: модуль llm не опросился", exc_info=True)
        return
    tries = int(state.get("tries") or 0) + 1
    outcome = "failed"
    try:
        # Порядок важен: записка ложится в память, и ТОЛЬКО ПОСЛЕ этого отметка
        # говорит «записка есть». Убитый между этими шагами руннер положит записку
        # заново — это дешевле, чем ход по пустому разговору.
        source_id = _birth_note(note_written=bool(state.get("note_written")))
        _write_json(marker, {"state": "in_progress", "tries": tries,
                             "note_written": True,
                             "at": _now().isoformat(timespec="seconds"),
                             "agent": _agent_name, "owner": _speaker})
        outcome = _turn_in_window(source_id, speaker="Hélène", birth=True)
    except Exception:
        log.exception("первый ход при рождении упал")
    if outcome in ("spoken", "silent", "deferred"):
        try:
            _write_json(marker, {"state": "done", "tries": tries,
                                 "at": _now().isoformat(timespec="seconds"),
                                 "agent": _agent_name, "owner": _speaker})
        except Exception:
            # Ход СОСТОЯЛСЯ (деньги потрачены, слово сказано) — отметка не легла.
            # Повтор рождения на следующем старте дешевле, чем падение руннера.
            log.exception("отметка рождения не записалась")
        log.info("рождение: первый ход состоялся (%s)", outcome)
        if outcome == "silent":
            # Ход был, слова не было (агент закрыл его `end_turn` без реплики —
            # его право). Но окно первого запуска, где вообще ничего нет, читается
            # как поломка, а машинную строку исхода выдавать за его слово нельзя.
            # Молчание по решению (`stay_silent`) плашку уже получило в
            # _turn_in_window; здесь — только ход без слова.
            if not _desk.rows(1) or not _desk.rows(1)[-1].get("system"):
                _desk.deliver(f"⋯ {_agent_name}: первый ход закрыт без слова — это "
                              "решение, а не сбой. Напиши первым.", system=True,
                              kind="silence")
        return
    if tries >= _BIRTH_TRIES:
        try:
            _write_json(marker, {"state": "gave_up", "tries": tries,
                                 "at": _now().isoformat(timespec="seconds"),
                                 "agent": _agent_name, "owner": _speaker})
        except Exception:
            log.exception("отметка «рождение не состоялось» не записалась")
        log.error("рождение не состоялось за %d попыток — больше не пробую", tries)
        try:
            _desk.deliver(
                f"⚠ Первый ход агента не состоялся {tries} раз подряд — обычно это "
                "значит, что мозг недоступен: проверь ключ и адрес модели в "
                "Настройках. Подробности — в data/runner.log. Чтобы попробовать "
                "знакомство заново, удали файл data/memory/.state/born.json.",
                system=True)
        except Exception:
            log.exception("не сказала владельцу о несостоявшемся рождении")
        return
    log.warning("рождение: ход не состоялся (попытка %d из %d) — повторю на "
                "следующем запуске", tries, _BIRTH_TRIES)


_ALARM_EVERY_SEC = 30.0
_ALARM_PER_TICK = 3          # больше трёх ходов подряд без спроса — это уже не будильник
# Предохранитель на деньги владельца. Будильник — первый способ агента ходить БЕЗ
# спроса, и ход по нему стоит столько же, сколько ответ владельцу. Агент, который
# в ходе по будильнику заводит себе новый будильник «на сейчас», получил бы петлю
# на всю ночь; такая петля у этого дерева в истории уже была (руминация 06.07).
# Шесть ходов в час — потолок, за которым это уже не будильник, а петля.
_ALARM_HOUR_CAP = 6
_ALARM_FIRED: list[float] = []


def _alarm_note(task: dict) -> str:
    """Чем продукт будит агента. Его же словами о его же намерении, без машинных скобок."""
    kind = str(task.get("kind") or "")
    goal = str(task.get("goal") or "").strip()
    target = str(task.get("target") or "").strip()
    if kind == "message" and target:
        return (f"Сработал твой будильник: намечено сказать «{target}» — {goal}\n"
                "Отправить за тебя продукт не может: если это ещё нужно, скажи это "
                "рукой send_message.")
    if kind == "email" and target:
        return (f"Сработал твой будильник: намечено письмо на {target} — {goal}\n"
                "Продукт писем сам не шлёт: если это ещё нужно, отправь рукой.")
    if goal:
        return f"Сработал твой будильник. Намечено было вот что: {goal}"
    return "Сработал твой будильник на это время (чем именно — не записано)."


def _fire_due_tasks() -> None:
    """Адресное срабатывание: claim -> durable run -> погашение намерения."""
    if _desk is None or _life is None or _alarms is None or not _brain_ready():
        return
    tasks = _alarms.tasks
    _alarms.reconcile_claims()
    for held in tasks.after_run_holds():
        run_id = str(held.get("after_run") or "")
        if run_id and _agent.run_is_terminal(run_id):
            tasks.clear_after_run(str(held.get("id") or ""))
    for task in list(tasks.due())[:_ALARM_PER_TICK]:
        now_ts = time.time()
        _ALARM_FIRED[:] = [t for t in _ALARM_FIRED if now_ts - t < 3600]
        if len(_ALARM_FIRED) >= _ALARM_HOUR_CAP:
            log.warning("будильники: достигнут прежний часовой предел; намерения ждут")
            return
        # Ревью 26.09 (W4 S3): её правило паузы фона — и здесь. «Попроси умерить фон — новый
        # фон не начнёт» держалось только у неё на сервере (`mtproto_runner`); в издании
        # повторяющиеся пробуждения поднимали ход модели и на паузе. Вхождение расписания
        # гасится без хода (сдвигается на следующее); разовое — текущее дело, пауза его не
        # держит, как и у неё.
        if task.get("recur"):
            try:
                import appetite
                hold = appetite.background_hold()
            except Exception:
                hold = None
            if hold:
                log.info("намерение #%s [расписание] пропущено: %s", task.get("id"), hold)
                try:
                    tasks.mark_fired(task["id"])
                except Exception:
                    log.exception("пропуск намерения #%s на паузе фона не записался",
                                  task.get("id"))
                continue

        def invoke(room):
            now = _now()
            source_id = f"alarm-{task['id']}-{int(now.timestamp() * 1000)}"
            note = _alarm_note(task)
            desk = _room(room)
            desk.archive(note, outgoing=False, now=now, sender="Hélène")
            desk.life(note, direction="in", actor="Hélène", source_id=source_id, now=now)
            _turn_in_window(source_id, speaker="Hélène", room=room, origin_text=note)

        try:
            if _alarms.fire(task, invoke):
                _ALARM_FIRED.append(now_ts)
        except Exception:
            log.exception("будильник #%s не завершил передачу владения", task.get("id"))


def _forge_events_due() -> None:
    if _forge_events is None or not _brain_ready():
        return
    try:
        _forge_events.tick()
    finally:
        _set_busy(False)


def _name_the_owner(owner: str) -> None:
    """Назвать собеседника кадра ИМЕНЕМ ВЛАДЕЛЬЦА, а не «Егор».

    ⚠ В самой свежей части кадра — зоне «СЕЙЧАС» user-сообщения, той единственной
    строке, что отвечает на вопрос «кто передо мной», — стоял литерал
    `frame_layout.py:960: bits.append(own("это Егор"))`. У владельца по имени Иван
    агент на каждом ходе читал «говорит «Иван» … это Егор»: одна строка называла
    собеседника двумя разными людьми, и ни один из них не был владельцем.

    Дерево править нельзя, поэтому подменяем сборщик слота — тем же приёмом, каким
    песочница подменяет `agent.TOOL_IMPL`. Замена точечная: литерал стоит последним
    в слоте, а чужие значения приезжают в кавычках «…», поэтому хвост строки
    ни с чем не спутать.
    """
    if not owner or owner == "Егор":
        return
    try:
        import frame_layout
        import gutter
    except Exception:
        log.warning("кадр: имя владельца не подставлено — модуль слоя не загрузился",
                    exc_info=True)
        return
    original = getattr(frame_layout, "_speaker", None)
    if original is None or getattr(original, "_helene_owner", ""):
        return
    tail = "это Егор"

    def _speaker(ctx, speaker, snap):
        text, kind = original(ctx, speaker, snap)
        body = str(text)
        if body.endswith(tail):
            text = gutter.Own(body[:-len(tail)] + "это " + owner,
                              getattr(text, "guest", False))
        return text, kind

    _speaker._helene_owner = owner
    frame_layout._speaker = _speaker
    log.info("кадр: собеседник назван «%s» (в дереве стоял литерал «Егор»)", owner)


def _announce_git(tree: Path) -> None:
    """Сказать вслух, есть ли обратимость правок, которую обещает кадр дерева."""
    global _ORIENT_EXTRA
    import shutil
    state = _git_state(tree)
    if state["repo"] and state["exe"]:
        log.info("откат правок: git есть (%s), репозиторий в дереве данных есть — "
                 "автокоммит правок и страховочные снимки живы", shutil.which("git"))
        return
    _ORIENT_EXTRA = _NO_GIT_ORIENT
    log.warning("откат правок: git %s, репозитория в дереве %s — автокоммита и "
                "автоотката НЕТ (агенту сказано в ориентире кадра)",
                "есть" if state["exe"] else "нет",
                "нет" if not state["repo"] else "есть")


def _sweep_processed(processed: Path, days: int = 14) -> None:
    """Разобранные записки окна старше `days` — удалить.

    ⚠ Каждое сообщение владельца после обработки оставалось отдельным .md
    НАВСЕГДА: ни читателя, ни уборки. За год переписки это десятки тысяч файлов и
    ещё одна полная копия разговора открытым текстом — из-за неё удалить одну
    неудачную реплику (пароль, ключ) было невозможно без сноса всей data/.
    Возраст, а не немедленное удаление: сутки-две это ещё и следствие, по которому
    можно понять, что именно съел упавший ход.
    """
    cutoff = time.time() - max(1, int(days)) * 86400
    removed = 0
    try:
        entries = (list(processed.glob("*.md")) + list(processed.glob("*.md.done"))
                   + list(processed.glob("*.md.tries")))
    except OSError:
        return
    for stale in entries:
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        log.info("уборка: удалено разобранных записок окна: %d", removed)
    _sweep_attachments(processed.parent, days)


def _sweep_attachments(inbox: Path, days: int = 14) -> None:
    """Папки вложений: пустые — сразу, залежавшиеся с файлами — по возрасту.

    Перенос в спул (`move=True`) оставляет пустой каталог `attachments/<stamp>/`, а
    непрочитанное вложение (ход упал, записка не дошла) осталось бы там навсегда —
    это картинка владельца открытым текстом мимо всякой уборки.
    """
    root = inbox / "attachments"
    cutoff = time.time() - max(1, int(days)) * 86400
    removed = 0
    try:
        folders = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return
    for folder in folders:
        try:
            files = list(folder.iterdir())
            if files and folder.stat().st_mtime >= cutoff:
                continue
            for stale in files:
                stale.unlink()
            folder.rmdir()
            removed += 1
        except OSError:
            continue
    if removed:
        log.info("уборка: удалено папок вложений окна: %d", removed)


def _read_message(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if lines and lines[0].startswith("#"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _computer_state() -> dict | None:
    """Тело руки `computer` как оно есть (body.STATE), без второй правды.

    None — тела в этой сборке нет (прочие POSIX; на Windows и macOS оно есть):
    секции тела в снимке нет вовсе, и окно её не рисует — а не рисует «нет»
    словами.
    """
    try:
        if not body.HAS_BODY:
            return None
        return body.state()
    except Exception:
        return {"enabled": False, "available": False,
                "reason": "модуль тела не ответил — смотри runner.log"}


def _sandbox_state() -> dict:
    """Ограда как она есть — плюс режим, который её и включил.

    Строку про окна (`windows`) пишет сама ограда, а слова для неё берёт у
    тела (`body.windows_truth()`): одна строка на анатомию, настройки и агента.
    До 06.09 тела в поставке не было, и строка честно говорила «водить нечем»;
    теперь она говорит, включил ли тело владелец и подключилось ли оно.
    """
    try:
        import fence
        state = dict(fence.state())
        # Поимённо: какая рука в ограде, какая нет и почему. До 10.09 снимок
        # говорил «shell в AppContainer» и молчал о том, что рядом стоят руки,
        # исполняющие команды мимо неё, — владелец читал молчание как «накрыто
        # всё». Отчёт снимается ЗДЕСЬ, потому что руку `computer` выдаёт
        # `body.install` уже после ограды.
        state["hands"] = fence.hands_report(_agent)
    except Exception:
        state = {"enabled": False, "container": False, "reason": "модуль недоступен",
                 "windows": "окна: спросить не у кого — модуль ограды не загрузился",
                 "hands": []}
    if _mode:
        state["mode"] = _mode.get("name")
        state["mode_title"] = _mode.get("title")
    return state


def _brain_ready() -> bool:
    """Приговор самого дерева: есть ли чем думать. Не «имя модели непусто»."""
    try:
        import llm
        return bool(llm.configured())
    except Exception:
        return False


def _git_state(tree: Path) -> dict:
    """Есть ли на самом деле обратимость правок, которую обещает кадр.

    Дерево говорит агенту (contract.living_documents) «правка автокоммитится в
    git», а привратник правок кода прямо ослаблен словами «у неё есть
    git-автокоммит и автооткат при падении — НЕ будь параноиком». До 06.09 в
    поставке не было ни git.exe, ни репозитория в дереве данных:
    `selfgit.repo_ready()` всегда False, и все снимки — тихий no-op. Теперь
    поставка везёт MinGit (runtime/git, `boot.arm_git`), а `boot.seed_git`
    заводит личный репозиторий агента при рождении дома — и обещание стало
    правдой. Проверка осталась: сборка без git (чужой запуск, снесённая
    папка) обязана сказать это в анатомию и в ориентир кадра (`_orient`).
    """
    # `boot.git_ready`, а не голый `which`: на macOS без Command Line Tools
    # `/usr/bin/git` находится, но это заглушка Apple, и «git есть» над ней —
    # неправда в анатомии и в ориентире кадра.
    return {"repo": (Path(tree) / ".git").exists(),
            "exe": boot.git_ready() is not None}


# Ровно одна копия атомарной записи на весь харнесс: три разошедшиеся копии одной
# функции — тот самый класс, из которого выросла гонка временного имени.
_write_json = transport._write_json


def _settle_mode(cfg: dict, config_path: Path) -> dict:
    """Разложить режим по ручкам ДО того, как их прочтут, и записать его явно.

    Порядок важен: `sandbox.enabled` читает `fence.install`, а `service.session0`
    и `service.firewall` — служба. Режим обязан выставить их раньше, иначе он был
    бы словом на экране над ручками, живущими своей жизнью.

    ⚠ Служба здесь НЕ режим и ручкой режима не управляется (04.09): ставится она
    поверх любой ограды и ограду не снимает. Раскладка трогает ровно ограду и две
    галочки службы — сам факт установки спрашивается у SCM.

    Первый запуск на старом конфиге (режима в файле нет) — это и есть «первое
    сохранение»: миграция выводит режим из того, что в файле уже есть, и
    записывает его словом. Своей неудачей запись продукт не роняет: в памяти
    режим всё равно разложен, а причина названа в логе.
    """
    picture = modes.apply(cfg)
    modes.journal(picture, where=config_path.name)
    if picture.get("needs_write"):
        try:
            written = modes.ensure_written(config_path)
            picture["written"] = written.get("written")
        except Exception:
            log.exception("режим не записался в конфиг (работаю по выведенному)")
    return picture


def _mark_done(processed: Path, name: str, word: str) -> None:
    """Парная метка `.done`: записка разобрана (ход состоялся, пустая, усыновлена)."""
    try:
        (processed / (name + ".done")).write_text(f"{word} {time.time():.0f}\n", encoding="utf-8")
    except OSError:
        log.debug("метка .done не записалась [%s]", name, exc_info=True)


def _handle_note(path: Path, message: str, processed: Path) -> None:
    """Ход по записке окна и метка `.done` ПОСЛЕ него (ревью 25.09, A6 F1).

    Раньше обычный путь метку не ставил вовсе: replay на том же тике переигрывал
    каждую записку второй раз (два хода, два ответа в окно), а после обновления —
    записки прошлых недель. Упавший ход метки не получает: его подберёт replay по
    тому же ingress id (память не дублируется).

    `<stamp>__to__<комната>.md` — адресная записка композера: реплика владельца в
    telegram-комнату или в другую комнату окна (`window-<hex>`); без суффикса —
    комната окна по умолчанию. Штамп записки — ingress id хода.
    """
    if not message:
        _mark_done(processed, path.name, "empty")
        return
    target = _inbox_target(path.stem)
    message, attached = _split_attachments(message)
    ingress_id = f"note:{path.stem}"
    try:
        if transport.is_room(target):
            handle_desk(message, room=target, attachments=attached,
                        ingress_id=ingress_id)
        else:
            if attached:
                # Канал отказывает таким запискам сам; если файл всё же
                # приехал — не терять молча.
                log.warning("вложения окна в Telegram-комнату не едут [%s]: %s",
                            target, ", ".join(attached))
            handle_owner_note(target, message)
    except Exception:
        log.exception("ход окна упал [%s] — записка без .done, replay повторит", target)
        return
    _mark_done(processed, path.name, "done")


def _adopt_stale_processed(processed: Path, older_than_sec: int = 1800) -> int:
    """На старте: записки в processed старше получаса без `.done` — усыновить, не переигрывать.

    Метка `.done` на обычном пути появилась 25.09; у обновившихся установок в
    processed лежат записки прошлых недель без метки, и replay поднял бы по каждой
    полный ход. Свежие (моложе получаса) — настоящие кандидаты на replay после падения.
    """
    cutoff = time.time() - max(60, int(older_than_sec))
    adopted = 0
    try:
        entries = list(processed.glob("*.md"))
    except OSError:
        return 0
    for path in entries:
        try:
            if path.stat().st_mtime < cutoff and not (processed / (path.name + ".done")).exists():
                _mark_done(processed, path.name, "adopted")
                adopted += 1
        except OSError:
            continue
    if adopted:
        log.info("processed: усыновлено старых записок без метки: %d (replay их не тронет)", adopted)
    return adopted


#: Replay processed-записок: проход при старте и раз в полминуты (листинг папки — дёшево),
#: а САМИ ПОВТОРЫ записки — по нарастающей паузе `_REPLAY_BACKOFF_SEC` и не больше трёх
#: (см. `_replay_unclaimed_notes`). Ревью 26.09 (W3 S3): при проходе раз в пять минут
#: первый повтор упавшей записки ждал до пяти минут, а владелец не видел ни ответа, ни
#: плашки — исключение было выше `_turn_in_window`.
_REPLAY_EVERY_SEC = 30.0
_REPLAY_MAX_TRIES = 3
#: Пауза перед повтором по числу уже сделанных повторов: сразу, через минуту, через пять.
_REPLAY_BACKOFF_SEC = (0.0, 60.0, 300.0)


def _note_tries(processed: Path, name: str) -> int:
    try:
        return int((processed / (name + ".tries")).read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return 0


def _note_last_try(processed: Path, name: str) -> float:
    try:
        return float((processed / (name + ".tries")).read_text(encoding="utf-8").split()[1])
    except (OSError, ValueError, IndexError):
        return 0.0


def _note_gave_up(path: Path, tries: int) -> None:
    """Сдавшаяся записка — вслух, в комнату, куда её писали (ревью 26.09, W3 S3).

    Прежде оставались только `.done` с «gave-up» и строка в логе: сообщение владельца
    пропадало без единого слова. Записка для Telegram-чата пишется окну: владелец
    оставлял её в окне и там же ждёт ответа."""
    target = _inbox_target(path.stem)
    room = target if transport.is_room(target) else STREAM
    stamp = path.stem.split("__", 1)[0]
    try:
        _room(room).deliver(
            f"⚠ Записка {stamp} так и не дошла до агента: {tries} попыток хода упали. "
            f"Текст сохранён в {path}; причина — в логе движка. Напиши ещё раз, когда "
            f"причина уйдёт.", source_id=f"note-gave-up:{path.stem}", system=True)
    except Exception:
        log.exception("плашка о сдавшейся записке не легла [%s]", path.name)


def _note_tries_bump(processed: Path, name: str, tries: int, at: float | None = None) -> None:
    try:
        stamp = time.time() if at is None else float(at)
        (processed / (name + ".tries")).write_text(f"{int(tries)} {stamp:.0f}\n",
                                                   encoding="utf-8")
    except OSError:
        log.debug("счёт попыток replay не записался [%s]", name, exc_info=True)


def _replay_unclaimed_notes(processed: Path, limit: int = 5,
                            now: float | None = None) -> list[str]:
    """Replay processed-записок, чей ход не дошёл до модели (срез 20.09).

    Записка попадает в processed ДО хода; краш в окне между ними раньше означал
    молчаливую потерю — стартовый цикл processed не возвращал. Здесь: у каждой
    записки без парной `.done`-метки ход повторяется ПО ТОМУ ЖЕ ingress id
    (штампу имени), поэтому память не дублируется. Успешный ход (handle_desk
    вернулся без исключения) ставит `.done`; упавший ждёт следующего прохода (при
    старте и раз в пять минут), но не больше `_REPLAY_MAX_TRIES` раз — потом записка
    помечается сдавшейся. Возвращаются имена отыгранных записок.
    """
    replayed: list[str] = []
    # Фильтр по `.done` — ДО среза: иначе пять старейших разобранных записок
    # закрывали бы дорогу настоящим кандидатам (A6 F1г).
    unclaimed = [path for path in sorted(processed.glob("*.md"))
                 if not (processed / (path.name + ".done")).exists()]
    for path in unclaimed[:max(0, int(limit or 5))]:
        # ⚠ 1.0.1: попыток не больше _REPLAY_MAX_TRIES. Записка, чей ход падает всякий
        # раз (детерминированная ошибка), переигрывалась бы вечно — а replay звался
        # КАЖДУЮ СЕКУНДУ главного цикла: каждая попытка — полный ход, новый прогон на
        # диске и снимок запроса, иногда вызов модели. Жалоба тестера на Windows «Элен
        # в простое грузит диск» — ровно этот класс. Счёт попыток — файлом рядом, чтобы
        # переживал рестарт; сдавшаяся записка помечается честно и остаётся в processed.
        tries = _note_tries(processed, path.name)
        if tries >= _REPLAY_MAX_TRIES:
            _mark_done(processed, path.name, f"gave-up after {tries} replays")
            log.warning("replay записки %s: %d попыток упали — больше не повторяю",
                        path.name, tries)
            _note_gave_up(path, tries)
            continue
        moment = time.time() if now is None else float(now)
        pause = _REPLAY_BACKOFF_SEC[min(tries, len(_REPLAY_BACKOFF_SEC) - 1)]
        if tries and moment - _note_last_try(processed, path.name) < pause:
            continue
        try:
            message = _read_message(path)
        except OSError:
            continue          # не прочиталась (занята) — это не попытка хода
        if not message:
            _mark_done(processed, path.name, "empty")
            continue
        # Попытка засчитывается, когда ход действительно начинается (ревью 26.09, W3 S3).
        _note_tries_bump(processed, path.name, tries + 1, at=moment)
        target = _inbox_target(path.stem)
        message, attached = _split_attachments(message)
        log.warning("replay записки без хода: %s [%s]", path.name, target)
        try:
            if transport.is_room(target):
                handle_desk(message, room=target, attachments=attached,
                            ingress_id=f"note:{path.stem}")
            else:
                handle_owner_note(target, message)
        except Exception:
            log.exception("replay хода упал [%s] — записка осталась без .done", path.name)
            continue
        _mark_done(processed, path.name, "replayed")
        replayed.append(path.name)
    return replayed


def main() -> None:
    global _desk, _desks, _bot, _speaker, _title, _agent_name, _tree, _deliver_unspoken, _mode, _continuity, _alarms, _forge_events
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    # Репетиция обновления (25.09, K): `--check-extensions --data <папка данных>` —
    # манифесты расширений владельца под ЭТИМ движком, без агента, без замка дерева.
    # Отчёт JSON в stdout; код 0 — грузятся все, 2 — хоть одно нет. Зовёт мастер из
    # новой поставки до подмены папок; годится и человеку из терминала.
    parser.add_argument("--check-extensions", action="store_true")
    parser.add_argument("--data", default="")
    # Версия ПРОГРАММЫ, под которую репетируем: мастер передаёт её из паспорта ПОСТАВКИ
    # (ревью 25.09, A5 F1: паспорт в корне установки — это старая версия, и `requires`
    # сверялся бы не с тем). Без флага — паспорт этого движка (лежит на два уровня выше
    # `app/localharness`), потом уже паспорт рядом с конфигом.
    parser.add_argument("--host-version", default="")
    args = parser.parse_args()
    if args.check_extensions:
        import contextlib
        import extensions
        data_dir = Path(args.data or (Path(args.config).resolve().parent / "data" if args.config else "data"))
        passport = (args.host_version.strip()
                    or _read_passport(Path(__file__).resolve().parents[2])
                    or _read_passport(Path(args.config).resolve().parent if args.config else data_dir.parent))
        # 25.09 (ревью V3 F4): та же среда, что при живой загрузке — дерево движка в sys.path
        # (расширение вправе `import memory_life`), иначе совместимое расширение = отказ.
        tree_dir = Path(__file__).resolve().parents[2] / "tree"
        if tree_dir.is_dir():
            sys.path.insert(0, str(tree_dir))
        # Код расширений печатает в stdout что хочет (отладочный print на импорте —
        # обычное дело); отчёт обязан остаться единственным JSON в stdout (A5 F2).
        with contextlib.redirect_stdout(sys.stderr):
            report = extensions.check(data_dir, host_version=passport)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        raise SystemExit(0 if report.get("ok") else 2)
    if not args.config:
        parser.error("--config обязателен (кроме --check-extensions)")
    config_path = Path(args.config).resolve()
    # POSIX: SIGTERM — мягкий выход (atexit, замок дерева), и сторож родителя:
    # умерла оболочка — уходим вслед, а не живём сиротой с замком на дереве.
    # На Windows обе строки — no-op: там детей держит job-объект оболочки.
    boot.arm_soft_exit("движок")
    boot.watch_parent("движок")
    # ⚠ ПЕРВЫЙ-ЗАПУСК.md зовёт владельца править helene.json руками, и любая
    # опечатка роняла руннер голым трейсом: оболочка перезапускала его с растущей
    # паузой и говорила «падает раз за разом», не имея чем назвать причину.
    # Код выхода 3 = «конфиг/раскладка», его можно отличить от случайной смерти.
    try:
        cfg = json.loads(boot.read_config_text(config_path))
        if not isinstance(cfg, dict):
            raise ValueError("верхний уровень должен быть объектом {…}")
    except (OSError, ValueError) as exc:
        log.error("helene.json не читается (%s): %s — поправь файл и запусти снова",
                  config_path, exc)
        raise SystemExit(3)

    try:
        _mode = _settle_mode(cfg, config_path)
    except Exception:
        log.exception("режим не разобрался — иду по ручкам как есть")
        _mode = {}

    tree = Path(os.environ.get("HELENE_TREE") or os.environ.get("PRAXIS_DESK_TREE")
                or cfg.get("tree") or "data")
    if not tree.is_absolute():
        tree = (config_path.parent / tree).resolve()
    _tree = tree
    try:
        # git поставки — в PATH ДО раскладки: seed_git заводит репозиторий агента,
        # а selfgit и _git_state ниже зовут голое `git`.
        git_from = boot.arm_git()
        log.info("git: %s", git_from or "не найден — снимков правок не будет")
        boot.ensure_layout(tree, cfg)
    except boot.LayoutError as exc:
        log.error("папка данных не готова: %s", exc)
        raise SystemExit(3)

    # Замок на ДЕРЕВО — до всего тяжёлого: вторая копия не должна ни рождать
    # агента заново, ни разбирать записки владельца, ни писать в ту же память.
    # `role` — подпись в замке для диагностики. Стояло `cfg["mode"]`, то есть
    # местожительство харнесса ("local"): в замке всегда было одно и то же
    # слово. Режим агента здесь говорит больше — видно, чей замок нашли.
    busy = boot.claim_tree(tree, agent=boot.agent_name(cfg),
                           role=str(_mode.get("name") or cfg.get("mode") or "окно"))
    if busy:
        _say_tree_is_busy(tree, cfg, busy)
        raise SystemExit(3)

    code_raw = str(cfg.get("code") or "../../live")
    code_dir = Path(code_raw)
    if not code_dir.is_absolute():
        code_dir = (config_path.parent / code_dir).resolve()
    if not (code_dir / "agent.py").is_file():
        # Дерево — это и есть харнесс. Без него нечего запускать, и подменять её ход
        # своим («кадр-лайт») значило бы держать вторую реализацию продукта.
        log.error("дерева агента нет: %s — движок не поднимется", code_dir)
        raise SystemExit(2)

    owner = cfg.get("owner") or {}
    _speaker = boot.owner_name(cfg)
    _title = str(owner.get("room") or "Hélène")
    _agent_name = boot.agent_name(cfg)
    _deliver_unspoken = bool((cfg.get("agent") or {}).get("deliver_unspoken", True))
    # Ядру — знать, что слово без руки reply доставит граница окна: иначе оно закрывает
    # прогон молчанием до нас, и наши расписки доставки бьют в терминальный прогон
    # (карточка не видела ни слова, ни доставки — 08.09).
    if hasattr(_agent, "BOUNDARY_DELIVERS_UNSPOKEN"):
        _agent.BOUNDARY_DELIVERS_UNSPOKEN = bool(_deliver_unspoken)

    log.info("%s", boot.project_brain(tree, cfg))
    # Тело для руки `computer` — ДО импорта дерева: импорт занимает секунды, а
    # тело за них успевает подключиться к мосту. Опция владельца
    # (`computer.enabled`); выключена — только строка в журнал. Токены живут
    # в памяти этого процесса, в среду не кладутся (шапка body.py).
    try:
        body.launch(config_path.parent, tree, cfg)
    except Exception:
        log.exception("тело не поднялось — рука окон откажет словами")
    # Голос — ДО импорта дерева: `media_audio` читает `PRAXIS_STT_*` из среды,
    # и переменные, проставленные позже, оно уже не увидит. Нет библиотеки или
    # модели — переменных не ставим вовсе: пусть дерево скажет о голосовом само,
    # а не притворяется глухим над полусобранной коробкой.
    global _voice_state
    try:
        heard = voice.apply(tree, cfg)
        _voice_state = {"ready": bool(heard.get("ready")), "why": str(heard.get("why") or "")}
        if heard["ready"]:
            log.info("голос: модель %s (%s)%s", heard["model"],
                     heard["installed"].get("path", "?"),
                     " — " + heard["why"] if heard["why"] else "")
        else:
            log.info("голос: не слышу — %s", heard["why"])
    except Exception:
        log.exception("голос не поднялся — голосовые останутся нерасшифрованными")
    agent, memory_life = _load_tree(code_dir, tree, cfg)
    _name_the_owner(_speaker)
    _announce_git(tree)
    _desks = transport.Desks(tree, _speaker, _title, memory_life=memory_life,
                             agent_name=_agent_name)
    _desk = _desks.default
    transport.install(agent, _desks)
    _continuity = continuity.Continuity(
        agent, _desks, config_path,
        lambda run, chat: _set_busy(True, run, chat_id=chat),
        media_sender=deliver_one_media)
    _continuity.install()
    import tasks
    import forge
    import perception
    from core import events as core_events
    _alarms = alarm_clock.AlarmClock(_continuity, tasks)
    _alarms.install()
    _forge_events = forge_events.ForgeEvents(_continuity, core_events, forge, perception)
    # Песочница: shell в AppContainer, файловые руки — в папке Hélène, плюс
    # смонтированные владельцем папки (их список ограда перечитывает из
    # `config_path` на ходу — потому он сюда и передаётся). Не вышло — причина в
    # анатомии, продукт работает дальше.
    try:
        import fence
        fence.install(agent, tree, cfg, config_path=config_path)
    except Exception:
        log.exception("песочница не поднялась — руки без ограды")
    # Рука окон — ПОСЛЕ ограды: тело живёт снаружи контейнера, а разрешение
    # владельца (четыре права) перечитывается из того же helene.json на ходу.
    try:
        body.install(agent, tree, cfg, config_path=config_path)
    except Exception:
        log.exception("рука computer не подключена к телу")
    # Имя владельца в текстах дерева: дерево говорит с Егором, издание — с тем, кого
    # назвал мастер. Правятся только авторские тексты (схемы, указатели, окна,
    # контракты кадра); память и реплики не трогаются.
    try:
        import owner_words
        owner_words.install(agent, cfg)
    except Exception:
        log.exception("тексты дерева остались с именем владельца Праксис")
    # Имя агента — «я» и для свёртки памяти (25.09): memory_life метит его строки [Я]
    # по direction=out, а ограда чужого «я» знает только имена прода. Своё имя агент
    # тоже вправе называть «я» — добавляем его в ограду, иначе «Я — Феофан» читалось бы
    # как чужое «я» и сводка уходила бы в перезаход.
    try:
        import memory_life as _memory_life
        own = boot.agent_name(cfg).strip().casefold()
        if own and isinstance(getattr(_memory_life, "_OWN_NAMES", None), set):
            _memory_life._OWN_NAMES.add(own)
    except Exception:
        log.debug("имя агента не легло в ограду свёртки", exc_info=True)
    # Расширения владельца (25.09, K): свои тулы и крючки из data/extensions/<имя>/ —
    # модули с манифестом и версией API, а не патчи дерева. ПОСЛЕ тела и имён: тул
    # расширения встаёт в тот же список рук, что и штатные, и видит уже собранный
    # агент. Сбой одного — причина в журнал и в снимок, остальное живёт.
    try:
        import extensions
        extensions.install(agent, tree, cfg, host_version=_read_passport(config_path.parent))
    except Exception:
        log.exception("расширения не поднялись — агент без своих тулов владельца")
    # Рука брокера — ПОСЛЕ ограды: она закрывает свои файлы обмена от контейнера,
    # а поднят он или нет, решает предыдущий шаг. Без этой руки тексты продукта
    # обещали агенту брокера, которого у него не было.
    try:
        broker.install(agent, tree, cfg)
    except Exception:
        log.exception("рука брокера не выдана — просить права агенту нечем")
    tg = dict(cfg.get("telegram") or {})
    global _status_message
    _status_message = bool(tg.get("status_message", False))
    if str(tg.get("mode") or "bot") == "account" and tg.get("api_id") and tg.get("api_hash"):
        # Свой аккаунт агента по MTProto: сессия после входа в настройках.
        try:
            import mtproto
            _bot = mtproto.MtprotoTransport(agent, tree, memory_life, cfg)
            _bot.on_incoming = _note_incoming
            _bot.start()
            botapi.install(agent, _desks, _bot)
        except Exception:
            _bot = None
            log.exception("аккаунт Telegram не поднялся — работаю только окном")
    elif str(tg.get("bot_token") or "").strip():
        # Бот — опция: нет токена, нет и попытки. Ошибка старта бота не роняет
        # окно: продукт остаётся рабочим локально, а причина названа в логе.
        try:
            _bot = botapi.BotTransport(agent, tree, memory_life, cfg)
            _bot.on_incoming = _note_incoming
            _bot.start()
            botapi.install(agent, _desks, _bot)
        except Exception:
            _bot = None
            log.exception("Telegram-бот не поднялся — работаю только окном")
    try:
        import llm
        log.info("мозг: %s", "готов" if llm.configured() else "НЕ настроен (нет ключа)")
        # 25.09 (ревью V3 F8): удержания подписки живут в памяти процесса, а quota.json
        # переживал перезапуск — шапка окна врала «исчерпана до …» до самого `until`.
        # На старте память пуста — пусть и файл говорит то же.
        if hasattr(llm, "_write_quota_state"):
            llm._write_quota_state()
    except Exception:
        log.exception("мозг не опросился")
    _write_anatomy(tree, cfg)
    try:
        # Здесь только структурное восстановление WAL. Модель и доставка
        # продолжаются ниже, отдельным шагом того же последовательного цикла.
        recovered = agent.recover_durable_state()
        if recovered:
            log.warning("восстановлено прерванных ходов: %d", len(recovered))
    except Exception:
        log.exception("восстановление прерванных ходов не прошло (работаю дальше)")

    inbox = tree / "memory" / ".control" / "desk_inbox"
    processed = inbox / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    _sweep_processed(processed)
    _adopt_stale_processed(processed)
    swept_at = time.time()
    log.info("локальный код агента: дерево данных %s · код %s · транспорты: окно%s",
             tree, code_dir, "" if _bot is None else " + бот @" + _bot.username)
    import threading
    threading.Thread(target=_heartbeat_forever, args=(inbox,), name="heartbeat",
                     daemon=True).start()
    threading.Thread(target=_retention_forever, name="retention", daemon=True).start()
    threading.Thread(target=_keat_care_forever, name="keat-care", daemon=True).start()
    threading.Thread(target=_recall_care_forever, name="recall-care", daemon=True).start()
    threading.Thread(target=_config_watch_forever, args=(config_path, tree),
                     name="config-watch", daemon=True).start()
    # Control must run independently: the main loop is inside the model/tool turn.
    import atexit
    import control_watch
    control_stop, _control_thread = control_watch.start(
        tree, agent._runs(), agent.run_manager.NONTERMINAL_STATUSES)
    atexit.register(control_stop.set)
    import refresh_care
    global _refresh_care
    _refresh_care = refresh_care.RefreshCare(
        memory_life, cooldown=float(os.getenv("PRAXIS_REFRESH_COOLDOWN_SEC", "600") or 600)).start()
    atexit.register(_refresh_care.stop)
    threading.Thread(target=_supervisor_forever, args=(tree,), name="supervisor",
                     daemon=True).start()
    _warm_voice_models(cfg)
    # Рождение — после того, как всё поднято и квитанция читателя уже пишется:
    # окно видит «думает», а не мёртвый руннер, пока идёт первый ход.
    _maybe_birth(tree)
    _sleep_seed_once()
    alarms_at = 0.0
    resume_at = 0.0
    replay_at = 0.0          # первый проход replay — на первом же тике после старта
    sleep_at = time.time()
    while True:
        if _restart_wanted[0]:
            # Между ходами: текущий ход дошёл до конца, новый не начат. Дальше — надзор.
            log.warning("перезапуск по просьбе владельца: выхожу кодом %d, надзор поднимет "
                        "движок заново", RESTART_EXIT_CODE)
            _set_busy(False)
            sys.exit(RESTART_EXIT_CODE)
        for path in sorted(inbox.glob("*.md")):
            if path.name.startswith(".tmp-"):
                continue
            try:
                message = _read_message(path)
                os.replace(path, processed / path.name)
            except OSError:
                continue
            _handle_note(processed / path.name, message, processed)
        # 20.09: replay processed-записок, чей ход не дошёл до модели (краш между
        # переносом в processed и ходом). `.done` — по факту завершения handle_desk без
        # исключения; упавшая записка ждёт следующего прохода. ⚠ 1.0.1: проход — при
        # старте и раз в полминуты, а не каждую секунду; повторы записки — по нарастающей
        # паузе и не больше трёх: ежесекундный обход processed и вечный повтор упавшей
        # записки грели диск в простое. Порядок — по имени файла, то есть по времени записи.
        if time.time() - replay_at > _REPLAY_EVERY_SEC:
            replay_at = time.time()
            try:
                _replay_unclaimed_notes(processed)
            except Exception:
                log.exception("восстановление processed-записок не прошло (повтор на следующем "
                              "проходе)")
        while _bot is not None:
            chat_id = _bot.pop_pending()
            if chat_id is None:
                break
            try:
                handle_bot(chat_id)
            except Exception:
                log.exception("ход бота упал [%s]", chat_id)
        if time.time() - resume_at > 45:
            resume_at = time.time()
            try:
                _resume_due()
            except Exception:
                log.exception("продолжение задач не прошло (повтор на следующем тике)")
        # Часы агента: записка владельца и очередь бота разобраны — теперь его
        # собственные будильники. Порядок не случаен: живое слово владельца
        # вперёд, будильник подождёт полминуты.
        if time.time() - alarms_at > _ALARM_EVERY_SEC:
            alarms_at = time.time()
            try:
                _fire_due_tasks()
            except Exception:
                log.exception("тик будильников упал (продукт работает дальше)")
        try:
            _forge_events_due()
        except Exception:
            log.exception("события Forge ждут следующего тика")
        # Её сон — последним: живое слово, продолжение задач и будильники вперёд.
        if time.time() - sleep_at > _SLEEP_CHECK_SEC:
            sleep_at = time.time()
            try:
                _sleep_due()
            except Exception:
                log.exception("сон не прошёл (повтор при следующей проверке)")
        if time.time() - swept_at > 6 * 3600:
            swept_at = time.time()
            _sweep_processed(processed)
        time.sleep(_POLL_SEC)


if __name__ == "__main__":
    main()
