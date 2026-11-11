"""
Praxis — мастерская: настоящие кодинг-руки вместо heredoc-увечий (PASS 8.5).

Всё в её контейнере — новых привилегий НЕТ, только точность:
  * проекты: workspace/projects/<slug>/ — СОБСТВЕННЫЙ git у каждого (main-репо их
    игнорирует: workspace/ в .gitignore), README с брифом, venv, квота 200 МБ;
  * файловые тулы: fs_read (номера строк), fs_write (только НОВЫЙ файл),
    fs_edit (точное уникальное вхождение — главный апгрейд против heredoc),
    fs_search / fs_ls;
  * зоны: читать весь дом; имя файла и gitignore сами по себе ничего не скрывают
    с shell); писать — workspace/** soul/** memory/**; файлы ЯДРА — только внутри
    worktree предложения (proposal_id), иначе отказ «ядро — через предложение»;
  * run/run_tests/pip_install — subprocess с потолками вывода и таймаутами;
  * send_file — документ в текущий Telegram-чат (мост _TELETHON);
  * code_map — ast-карта кода (проверяемое «как я устроена»), кэш по mtime;
  * code_outline — скелет одного файла (PASS 16.5).

Файловая мутация, поиск, скелет и запуск — чистый Python (`hands.py`), одна реализация
на операцию. Запись и правка: снимок байтов → сверка версии → публикация через временный
файл → чтение результата; ответ — подтверждено, отказ или «исход неизвестен».

Модель здесь не зовётся. agent импортируется лениво (тулы регистрируются в agent).
"""

from __future__ import annotations

import praxis_time
import ast
import datetime as _dt
import hashlib
import logging
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import hands
import rails
import tool_report
import selfdev
from self_model import FileLock

log = logging.getLogger("praxis-workshop")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
REPO = Path(__file__).resolve().parent
PROJECTS = BASE / "workspace" / "projects"
INBOX = BASE / "workspace" / "inbox"
INBOX_FILE_MB = int(os.getenv("PRAXIS_INBOX_FILE_MB", "2048") or 2048)
INBOX_TOTAL_MB = int(os.getenv("PRAXIS_INBOX_TOTAL_MB", "10240") or 10240)

QUOTA_MB = int(os.getenv("PRAXIS_PROJECT_QUOTA_MB", "200") or 200)
RUN_TIMEOUT = 120
PIP_TIMEOUT = 300
RUN_OUT_CAP = 8000          # потолок вывода run/pip (голова+хвост)
READ_CAP_LINES = 400        # потолок fs_read за раз
SEARCH_CAP = 60             # строк-совпадений fs_search
# 04.08: потолки ОБХОДА, а не только попаданий. fs_search("10 млн", glob="**/*",
# root="/app/memory") не вернулся 4.5 часа: SEARCH_CAP ограничивает совпадения, а обход
# дерева не ограничивал никто. Ход держал целое ядро, память росла, а прогон качался
# running<->paused 230 раз. Потолки читаются из окружения — это её рычаги, а не мой забор:
# 0 в любом из них снимает соответствующий предел совсем.
SEARCH_SCAN_CAP = int(os.getenv("PRAXIS_SEARCH_SCAN_CAP", "40000") or 0)   # путей за обход
SEARCH_SECONDS = float(os.getenv("PRAXIS_SEARCH_SECONDS", "45") or 0)      # секунд на поиск
MAP_CAP = 14000             # символов code_map

# Метасимволы, которые вправду делают строку регуляркой. `re.escape` с Python 3.7 экранирует
# ещё и ПРОБЕЛ (и `#`, `&`, `~`), поэтому проверка «литерал == re.escape(литерал)» объявляла
# нелитеральным любой запрос из двух слов — и он проваливался мимо быстрого бинарного пути
# с его потолками в медленный питоновский обход. Проверяем то, что действительно значимо.
_REGEX_META = re.compile(r"[\\^$.|?*+()\[\]{}]")

WRITE_ZONES = ("workspace", "soul", "memory")   # как у shell: её дом для записи
# После решения владельца имя файла больше никогда не делает его секретом.
_SECRET_NAME = re.compile(r"(?!)")
_SKIP_DIRS = hands.SKIP_DIRS


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", str(name or "").strip().lower(), flags=re.UNICODE)
    return re.sub(r"[\s-]+", "-", s) or "project"


def _is_secret(p: Path) -> bool:
    return False


def _resolve_read(path: str) -> Path | None:
    """Путь для чтения внутри дома (BASE или REPO — в проде это одно)."""
    raw = str(path or "").strip()
    if not raw:
        return None
    p = Path(raw)
    cand = p if p.is_absolute() else (BASE / p)
    try:
        cand = cand.resolve()
    except (OSError, RuntimeError):
        return None
    for root in (BASE, REPO):
        try:
            cand.relative_to(root.resolve())
            break
        except ValueError:
            continue
    else:
        return None
    return cand


def _resolve_write(path: str, proposal_id: str = "") -> tuple[Path | None, str]:
    """Путь для записи. С proposal_id корень = worktree предложения (правка ядра);
    без него — только workspace/soul/memory. -> (Path|None, причина отказа)."""
    raw = str(path or "").strip()
    if not raw:
        return None, "пустой путь"
    if proposal_id:
        wt = selfdev.worktree_path(str(proposal_id).strip())
        if not wt.exists():
            return None, f"нет worktree предложения {proposal_id} — открой proposal(action=start)"
        p = Path(raw)
        cand = p if p.is_absolute() else (wt / p)
        try:
            cand = cand.resolve()
            cand.relative_to(wt.resolve())
        except (OSError, ValueError):
            return None, f"путь вне worktree предложения {proposal_id}"
        return cand, ""
    p = Path(raw)
    cand = p if p.is_absolute() else (BASE / p)
    try:
        cand = cand.resolve()
        rel = cand.relative_to(BASE.resolve())
    except (OSError, ValueError):
        return None, "путь вне дома"
    first = rel.parts[0] if rel.parts else ""
    if first not in WRITE_ZONES:
        return None, ("ядро — через предложение: proposal(action=start) → правки в его worktree "
                      "(передай proposal_id) → proposal(action=submit). Без него пишу только в "
                      "workspace/, soul/, memory/.")
    return cand, ""


# --------------------------------------------------------------------------- #
#  Telegram inbox — все документы допущенных групп/личек, разложенные по чатам
# --------------------------------------------------------------------------- #

def inbox_safe_name(name: str) -> str:
    """Санитизация имени файла: только базовое имя, без путей/спецсимволов, с расширением."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip().strip(".")
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE).strip("._")
    return base[:80] or "file"


def _inbox_chat_directory(*, scope: str = "", chat_id: str | int | None = None,
                          chat_kind: str = "", chat_label: str = "") -> Path:
    """Stable, human-readable Telegram inbox directory.

    The first level is an audience kind (``groups`` or ``private``), never a
    privacy tier such as owner/family/unknown.  The second level identifies one
    concrete conversation, so files with the same name from different chats do
    not collide and a human can find them without reversing a hash.
    """
    if chat_id is None and not scope and not chat_kind:
        return INBOX  # legacy/local callers keep the flat inbox
    raw_kind = str(chat_kind or "").strip().lower()
    if raw_kind in ("group", "groups") or (not raw_kind and str(scope).lower() == "group"):
        bucket = "groups"
    else:
        bucket = "private"
    label = re.sub(r"[^\w.-]+", "_", str(chat_label or "").strip(), flags=re.UNICODE)
    label = label.strip("._")[:48]
    ident = re.sub(r"[^\w-]+", "_", str(chat_id or "unknown"), flags=re.UNICODE)
    ident = ident.strip("_")[:48] or hashlib.sha256(
        str(chat_id or "unknown").encode("utf-8")).hexdigest()[:12]
    room = f"{label}_{ident}" if label else f"chat_{ident}"
    return INBOX / bucket / room[:96]


def inbox_accept(name: str, size_bytes: int, day: str | None = None, *,
                 scope: str = "", chat_id: str | int | None = None,
                 chat_kind: str = "", chat_label: str = "") -> tuple[Path | None, str]:
    """Судьба входящего Telegram-файла: (путь для сохранения, '') или честная причина.

    По умолчанию поддерживает старый плоский inbox. Живой раннер передаёт тип/имя/chat id и
    получает ``groups/<чат>`` либо ``private/<личка>``: одноимённые файлы разных разговоров
    не смешиваются, а каталог остаётся понятным человеку.
    Физические дисковые капы широкие и настраиваемые env."""
    size_mb = max(0, int(size_bytes or 0)) / (1024 * 1024)
    if size_mb > INBOX_FILE_MB:
        return None, f"файл {size_mb:.0f}МБ больше лимита {INBOX_FILE_MB}МБ — не качаю"
    used = _du_mb(INBOX) if INBOX.exists() else 0.0
    if used + size_mb > INBOX_TOTAL_MB:
        return None, (f"квота inbox исчерпана ({used:.0f}МБ из {INBOX_TOTAL_MB}МБ) — "
                      "не качаю, пора прибраться в workspace/inbox")
    stamp = day or praxis_time.today().strftime("%Y%m%d")
    safe = inbox_safe_name(name)
    directory = _inbox_chat_directory(scope=scope, chat_id=chat_id,
                                      chat_kind=chat_kind, chat_label=chat_label)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}_{safe}"
    n = 2
    while path.exists():  # коллизии — честный суффикс, не перезапись
        stem, dot, ext = safe.partition(".")
        path = directory / f"{stamp}_{stem}_{n}{dot and '.'}{ext}"
        n += 1
    return path, ""


def _resolve_inbox(path: str) -> Path | None:
    """Resolve a read-only path strictly inside ``workspace/inbox``."""
    raw = str(path or "").strip()
    if not raw:
        return INBOX.resolve()
    p = Path(raw)
    # 13.09: описание руки говорит «папка внутри workspace/inbox», и она так и зовёт —
    # `inbox_list("from-helene")`, `inbox_list("groups/AbstractDL_Chat_<room-id>")`.
    # Резолвер клеил такое имя к дому (`/app/from-helene`), не находил его внутри ящика и
    # отвечал «Нет такой папки», хотя папка есть. Сегодня она так не нашла ни папку группы,
    # ни мою записку. Относительное имя сначала пробуем от самого ящика; полный путь
    # `workspace/inbox/…` и абсолютный — как раньше. Граница ящика не ослаблена: любой
    # результат всё так же обязан лежать внутри INBOX.
    try:
        inbox_root = INBOX.resolve()
    except OSError:
        return None

    def _inside(cand: Path) -> Path | None:
        try:
            cand = cand.resolve()
            cand.relative_to(inbox_root)
        except (OSError, ValueError):
            return None
        return cand

    if not p.is_absolute() and p.parts:
        from_inbox = _inside(INBOX / p)
        # имя от ящика берём, если оно там есть или хотя бы его первая папка есть
        # («from-helene/опечатка.md» → честное «Нет файла», а не «вне ящика»)
        if from_inbox is not None and (from_inbox.exists() or (INBOX / p.parts[0]).exists()):
            return from_inbox
    return _inside(p if p.is_absolute() else BASE / p)


def when(path: Path) -> str:
    """Когда файл менялся — ЕЁ часами, а не UTC и не «неизвестно».

    ⚠ 13.08.2026, дословно от Егора: «у тебя не отображается время создания и изменения
    файла по ходу, поэтому так». Он прислал архив, спросил про него минутой позже — а в
    её листинге стояли только имя и размер. Файл, положенный только что, выглядел ровно
    как файл из июля, и «самое свежее» приходилось угадывать по названию.

    Сегодняшнее печатается часами («сегодня 16:13»), вчерашнее — словом, остальное
    датой. Год добавляется только когда он не этот: иначе строка растёт на 250 строк
    листинга ради того, что и так известно.
    """
    try:
        moment = praxis_time.local_from(path.stat().st_mtime)
    except OSError:
        return ""
    today = praxis_time.today()
    day = moment.date()
    if day == today:
        return f"сегодня {moment:%H:%M}"
    if (today - day).days == 1:
        return f"вчера {moment:%H:%M}"
    if day.year == today.year:
        return f"{moment:%d.%m %H:%M}"
    return f"{moment:%d.%m.%Y}"


def inbox_list(path: str = "") -> str:
    """Read-only listing exposed in every Telegram channel."""
    p = _resolve_inbox(path)
    if p is None or not p.is_dir():
        return "Нет такой папки внутри workspace/inbox."
    rows = []
    for child in sorted(p.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
        mark = "/" if child.is_dir() else ""
        size = "" if child.is_dir() else f" · {child.stat().st_size}Б"
        stamp = when(child)
        rows.append(f"{child.relative_to(BASE).as_posix()}{mark}{size}"
                    + (f" · {stamp}" if stamp else ""))
    return "\n".join(rows[:250]) or "(пусто)"


def inbox_read(path: str, start: int = 0, end: int = 0) -> str:
    """Read a text-like Telegram attachment without granting general filesystem hands."""
    p = _resolve_inbox(path)
    if p is None:
        return "Не читаю: путь должен быть внутри workspace/inbox."
    if not p.is_file():
        return f"Нет файла {path}."
    try:
        sample = p.read_bytes()[:4096]
    except OSError as exc:
        return f"Не прочитала файл: {type(exc).__name__}."
    if b"\x00" in sample:
        return (f"{p.relative_to(BASE).as_posix()}: бинарный файл, {p.stat().st_size}Б. "
                "Текстовым просмотрщиком его не разбираю.")
    return fs_read(p.relative_to(BASE).as_posix(), start=start, end=end)


def _du_mb(path: Path) -> float:
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total / (1024 * 1024)


def _cap_output(out: str, cap: int = RUN_OUT_CAP) -> str:
    if len(out) <= cap:
        return out
    head, tail = out[: int(cap * 0.6)], out[-int(cap * 0.35):]
    return f"{head}\n… (вырезано {len(out) - len(head) - len(tail)} символов) …\n{tail}"


# --------------------------------------------------------------------------- #
#  Проекты
# --------------------------------------------------------------------------- #

def _project_dir(name: str) -> Path | None:
    slug = _slug(name)
    d = PROJECTS / slug
    return d if d.is_dir() else None


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)


def project_create(name: str, brief: str = "") -> str:
    slug = _slug(name)
    d = PROJECTS / slug
    if d.exists():
        return f"Проект {slug} уже есть — project_status(\"{slug}\")."
    d.mkdir(parents=True)
    (d / "README.md").write_text(
        f"# {name}\n\n{(brief or '').strip() or '(бриф не задан)'}\n\n"
        f"_создан {praxis_time.day_key()} мастерской Praxis_\n", encoding="utf-8")
    r = _git(d, "init", "-q")
    if r.returncode == 0:
        _git(d, "add", "-A")
        subprocess.run(["git", "-C", str(d), "-c", "user.name=Praxis", "-c", "user.email=praxis@local",
                        "commit", "-q", "-m", f"init: {name}"], capture_output=True, text=True, timeout=60)
    log.info("project_create %s", slug)
    return f"Проект создан: workspace/projects/{slug} (свой git, README с брифом)."


def project_list() -> str:
    if not PROJECTS.is_dir():
        return "Проектов пока нет — project_create(имя, бриф)."
    out = []
    for d in sorted(PROJECTS.iterdir()):
        if d.is_dir():
            out.append(f"- {d.name} · {_du_mb(d):.1f} МБ")
    return "Проекты:\n" + "\n".join(out) if out else "Проектов пока нет."


def project_status(name: str) -> str:
    d = _project_dir(name)
    if d is None:
        return f"Нет проекта {_slug(name)} — см. project_list()."
    size = _du_mb(d)
    st = _git(d, "status", "--porcelain", "-b")
    stat = st.stdout.strip() if st.returncode == 0 else "(git недоступен)"
    quota = f" ⚠ квота {QUOTA_MB} МБ превышена!" if size > QUOTA_MB else ""
    return f"{d.name}: {size:.1f} МБ{quota}\n{stat}"


# --------------------------------------------------------------------------- #
#  Файловые руки
# --------------------------------------------------------------------------- #

def sniff_image_mime(head: bytes) -> str | None:
    """Магия первых байтов → mime картинки (PNG/JPEG/WebP/GIF) или None."""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def fs_probe_image(path: str) -> tuple[str, str, int] | None:
    """(resolved, mime, size), если файл — картинка; None — не картинка или гарды против.

    PASS 30.0.c. Гард ровно тот же, что у fs_read: при любом отказе возвращаем None,
    чтобы вызвавший ушёл в обычный fs_read и получил честный текст отказа тем же путём.
    Никакого собственного решения о доступе здесь нет."""
    try:
        p = _resolve_read(path)
        if p is None or not p.is_file():
            return None
        with p.open("rb") as fh:
            head = fh.read(16)
        mime = sniff_image_mime(head)
        if mime is None:
            return None
        return str(p), mime, p.stat().st_size
    except Exception:
        log.debug("fs_probe_image упал — считаю не-картинкой", exc_info=True)
        return None


def fs_read(path: str, start: int = 0, end: int = 0) -> str:
    p = _resolve_read(path)
    if p is None:
        rails.deny("hands_floor", "fs_read", f"вне дома: {str(path)[:120]}")
        tool_report.note(outcome="refused", target=str(path))
        return "Не читаю: путь вне дома. Для хоста используй Forge scope='host'."
    if not p.is_file():
        tool_report.note(outcome="refused", target=str(path))
        return f"Нет файла {path}."
    raw = p.read_bytes()
    tool_report.note(outcome="ok", target=str(path))
    digest = hashlib.sha256(raw).hexdigest()
    if b"\0" in raw[:4096]:
        return f"Бинарный файл {path}; bytes={len(raw)}; sha256={digest}."
    try:
        text = raw.decode("utf-8")
        decoding = "utf-8 exact"
    except UnicodeDecodeError:
        # The digest still covers the WHOLE original byte stream. Replacement
        # characters are a display limitation, not evidence of its contents.
        text = raw.decode("utf-8", errors="replace")
        decoding = "utf-8 replacement: text is lossy; do not edit from this view"
    lines = text.splitlines()
    s = max(1, int(start) or 1)
    e = min(len(lines), int(end) or (s + READ_CAP_LINES - 1))
    body = "\n".join(f"{i:>5}\t{lines[i - 1]}" for i in range(s, e + 1))
    tail = f"\n… (в файле {len(lines)} строк; дальше — fs_read(start={e + 1}))" if e < len(lines) else ""
    return (body + tail if body else "(пустой файл)") + (
        f"\n[fs_read whole-file sha256={digest}; bytes={len(raw)}; {decoding}]"
    )


SHRINK_GUARD_RATIO = 0.7    # PASS 16.5: новый текст < 70% старого = похоже на усечение


def _mutation_target(path: str, proposal_id: str, op: str) -> tuple[Path | None, str]:
    """Resolve and check the actual target, rejecting symlink components altogether.

    A symlink to an allowed target still has a mutable authority boundary; do not
    make the Python and Rust interpretations of it a write policy.
    """
    p, err = _resolve_write(path, proposal_id)
    if p is None:
        rails.deny("hands_floor", op, f"{err}: {str(path)[:100]}")
        return None, err
    raw = Path(str(path).strip())
    raw = raw if raw.is_absolute() else (selfdev.worktree_path(str(proposal_id).strip()) / raw
                                           if proposal_id else BASE / raw)
    try:
        if any(part.is_symlink() for part in (raw, *raw.parents)):
            return None, "символическая ссылка в пути записи"
    except OSError:
        return None, "не удалось проверить компоненты пути"
    return p, ""



def _publish(p: Path, data: bytes, before: str | None, path: str, proposal_id: str, success: str) -> dict:
    def moved() -> str:
        again, err = _mutation_target(path, proposal_id, "edit")
        return "" if again == p else (err or "путь изменился")
    result = hands.publish(p, data, before, recheck=moved)
    if result["status"] == "confirmed":
        log.info("%s (%d байт)%s", success, len(data), f" [proposal {proposal_id}]" if proposal_id else "")
        result["message"] = success
    return result


def _fs_write_unlocked(path: str, content: str, proposal_id: str = "", overwrite: bool = False,
                       force: bool = False, expected_sha256: str = "") -> dict:
    """Создать файл; overwrite=True — осознанно переписать существующий.

    PASS 16.5: перезапись возможна, но под гардом усечения (новый текст меньше 70%
    старого → отказ, force=true снимает)."""
    p, err = _mutation_target(path, proposal_id, "edit")
    if p is None:
        rails.deny("hands_floor", "fs_write", f"{err}: {str(path)[:100]}")
        return hands.outcome("refused", f"Не пишу: {err}")
    try:
        raw, before = hands.snapshot(p)
        data = content.encode("utf-8")
    except OSError as exc:
        return hands.outcome("refused", f"Не пишу: {path} не читается ({type(exc).__name__}).")
    except (AttributeError, UnicodeError):
        return hands.outcome("refused", "Не пишу: content должен быть текстом, кодируемым в UTF-8.")
    conflict = hands.version_conflict(expected_sha256, before)
    if conflict:
        return hands.outcome("refused", conflict, before)
    if raw is not None:
        if not overwrite:
            return hands.outcome("refused", (f"Файл {path} уже существует — правь точечно через edit (action=replace), "
                                             "или передай overwrite=true, если правда хочешь переписать целиком."),
                                 before)
        old_len = len(raw.decode("utf-8", "replace"))
        if not force and old_len > 0 and len(content) < old_len * SHRINK_GUARD_RATIO:
            pct = round(len(content) / old_len * 100)
            rails.deny("hands_floor", "fs_write", f"shrink-guard {pct}%: {str(path)[:100]}")
            return hands.outcome("refused", (f"Стоп: новый текст {path} — {pct}% от старого ({old_len} → {len(content)} симв.). "
                                             "Похоже на случайное усечение. Точечно — edit с action=replace; если перезапись "
                                             "осознанная — force=true."), before)
    return _publish(p, data, before, path, proposal_id, f"Записала {path} ({len(content)} симв.).")


def _fs_edit_unlocked(path: str, old: str, new: str, proposal_id: str = "", expected_sha256: str = "", *, all: bool = False) -> dict:
    """Точная правка: `old` должен встречаться РОВНО один раз. 0 или >1 — отказ с подсказкой.

    PASS 16.5: при >1 совпадении называем номера строк первых вхождений — меньше слепых
    итераций."""
    p, err = _mutation_target(path, proposal_id, "edit")
    if p is None:
        rails.deny("hands_floor", "edit", f"{err}: {str(path)[:100]}")
        return hands.outcome("refused", f"Не правлю: {err}")
    try:
        raw, before = hands.snapshot(p)
    except OSError as exc:
        return hands.outcome("refused", f"Не правлю: {path} не читается ({type(exc).__name__}).")
    if raw is None:
        return hands.outcome("refused", f"Нет файла {path} — новый файл создаётся edit(action=write).")
    conflict = hands.version_conflict(expected_sha256, before)
    if conflict:
        return hands.outcome("refused", conflict, before)
    edited, refusal = hands.replace_once(raw, old, new, all=all)
    if edited is None:
        return hands.outcome("refused", refusal, before)
    count = raw.decode("utf-8").count(old) if all else 1
    count_text = "1 вхождение заменено" if count == 1 else f"{count} вхождений заменено"
    result = _publish(p, edited, before, path, proposal_id, f"Поправила {path}: {count_text}.")
    if result["status"] == "confirmed":
        result["replacements"] = count
    return result


def code_outline(path: str = "", scope: str = "") -> str:
    """Скелет python-файла: классы/функции со строками. Дешёвая ориентация вместо
    чтения 2000 строк ради одной функции (PASS 16.5)."""
    if scope:
        if path:
            from catalog_families import PreflightRefusal
            raise PreflightRefusal("Choose path OR scope")
        return code_map(scope)
    if not path:
        return code_map("self")
    target = _resolve_read(path)
    if target is None:
        return "путь вне дома (BASE/REPO)"
    if target.is_dir():
        return _map_for_root(target)
    r = hands.outline(str(target), base=target.parent)
    if not r.get("ok"):
        return r.get("msg") or "Не смотрю."
    items = r.get("items") or []
    if not items:
        return f"{path}: ни одного класса или функции."
    rows = ["  " * (int(i.get("indent", 0)) // 4) + f"{i['kind']} {i['name']}  · строка {i['line']}"
            for i in items]
    return f"{path} — скелет ({len(items)}):\n" + "\n".join(rows[:400])


#: Бюджет файлов на один вызов `hands.search`. Пол собирает не больше стольких файлов под маску
#: и ищет только в них; поле `capped` у него про совпадения, а не про файлы. Усечение называем сами.
FLOOR_FILE_BUDGET = hands.SEARCH_FILE_BUDGET
#: Прогоны в общий обход дома не входят: 100 тыс. файлов, ищутся только явным `root=memory/runs`.
_SEARCH_RUNS_REL = "memory/runs"
_SEARCH_FIRST = ("workspace", "soul", "docs", "skills")


def _search_roots(base: Path) -> list[str]:
    """Порядок обхода дома для `fs_search` без `root`: сначала её документы, потом память по
    папкам (без прогонов), потом остальные верхние папки. Каждая часть — отдельный вызов пола
    со своим бюджетом файлов, поэтому усечение одной части не прячет другую."""
    order: list[str] = [name for name in _SEARCH_FIRST if (base / name).is_dir() and not (base / name).is_symlink()]
    memory = base / "memory"
    if memory.is_dir() and not memory.is_symlink():
        order += sorted(f"memory/{c.name}" for c in memory.iterdir()
                        if c.is_dir() and not c.is_symlink() and not c.name.startswith(".") and c.name != "runs")
    for c in sorted(base.iterdir(), key=lambda x: x.name):
        if (c.is_dir() and not c.is_symlink() and c.name not in _SEARCH_FIRST and c.name != "memory"
                and not c.name.startswith(".") and c.name not in _SKIP_DIRS):
            order.append(c.name)
    return list(dict.fromkeys(order))


def _floor_limits(r: dict, where: str) -> list[str]:
    """Что поиск не досмотрел и сам об этом сказал: иначе «ничего не нашла» было бы ложью."""
    notes: list[str] = []
    if r.get("out_of_time"):
        notes.append(f"в {where} не успела за {hands.SEARCH_SECONDS:.0f}с — сузь маску или root")
    if r.get("too_large"):
        notes.append(f"в {where} пропустила файлов крупнее "
                     f"{hands.SEARCH_MAX_FILE_BYTES // (1024 * 1024)} МБ: {r['too_large']}")
    return notes


def _search_silent(where: str) -> str:
    """Поиск не дал ответа. Читать файлы здесь нельзя: ради этого он и отдельный процесс."""
    return (f"Не знаю: поиск по {where} не ответил в срок или упал, что там есть, неизвестно. "
            "Сузь маску или root, упрости выражение.")


def _floor_search_home(pattern: str, glob: str) -> str:
    """Поиск по дому целиком, по частям. Часть без ответа останавливает обход и называется."""
    found: list[str] = []
    silent = ""
    truncated: list[str] = []
    files_seen = 0
    mask = glob.replace("**/", "", 1)
    skipped: list[str] = []
    limits: list[str] = []
    recursive = glob.startswith("**/")
    parts = [(rel, glob) for rel in _search_roots(BASE)] if recursive else []
    if recursive and (BASE / "memory").is_dir() and not (BASE / "memory").is_symlink():
        # Direct memory files need their own budget, without descending into runs.
        parts.append(("memory", mask))
    # A plain name glob searches BASE only; recursive globs also include its files.
    parts.append((".", mask))
    for rel, part_glob in parts:
        r = hands.search(pattern, glob=part_glob, root=rel, cap=max(1, SEARCH_CAP - len(found)),
                         base=BASE, scope_root=BASE.resolve())
        if r is None:
            silent = rel
            break
        if not r.get("ok"):
            # отказ пола по одной части (симлинк наружу, права) не роняет весь обход
            skipped.append(f"{rel}: {r.get('msg') or 'пол отказал'}")
            continue
        seen = int(r.get("files_seen") or 0)
        files_seen += seen
        limits += _floor_limits(r, rel)
        if seen >= FLOOR_FILE_BUDGET:
            truncated.append(rel)
        found.extend(hit if rel == "." else f"{rel}/{hit}" for hit in (r.get("hits") or []))
        if len(found) >= SEARCH_CAP:
            break
    notes: list[str] = []
    if truncated:
        notes.append(f"в {', '.join(truncated)} пол обошёл только первые {FLOOR_FILE_BUDGET} "
                     f"файлов — там сузь маску или дай root")
    notes += limits
    if skipped:
        notes.append("пропустила " + "; ".join(skipped))
    if recursive and (BASE / _SEARCH_RUNS_REL).is_dir():
        notes.append(f"{_SEARCH_RUNS_REL} не искала — укажи root={_SEARCH_RUNS_REL}")
    if len(found) >= SEARCH_CAP:
        notes.insert(0, f"потолок {SEARCH_CAP} совпадений — сузь паттерн")
    if silent:
        notes.insert(0, f"поиск не ответил на {silent}: эта часть и следующие не просмотрены")
    tail = f"\n… ({'; '.join(notes)})" if notes else ""
    if not found:
        if silent:
            return f"Не знаю, просмотрела не всё ({files_seen} файлов по {glob}).{tail}"
        return f"Ничего не нашла ({files_seen} файлов по {glob}).{tail}"
    return "\n".join(found[:SEARCH_CAP]) + tail


def fs_search(pattern: str, glob: str = "**/*.py", root: str = "") -> str:
    # 10.11: the journal gets this search's outcome. A finished (also empty or truncated) walk
    # is ok, because its text names any truncation; a refused query or a silent walker is not.
    out, outcome = _fs_search(pattern, glob, root)
    tool_report.note(outcome=outcome, target=str(root or "."))
    return out


def _fs_search(pattern: str, glob: str, root: str) -> tuple[str, str]:
    try:
        re.compile(pattern)
    except re.error as e:
        return f"Плохой regex: {e}", "refused"
    # All routes select the same resolved BASE/REPO root before dispatch.
    selected = _resolve_read(root) if root else BASE.resolve()
    if selected is None or not selected.is_dir():
        return "Не ищу: плохой корень поиска.", "refused"
    # Чистый литерал (без regex-метасимволов) ищет отдельный процесс с бюджетом файлов.
    # Его диалект маски: `**/имя` или просто `имя`; поддиректорию в маске оставляем обходу ниже.
    mask = glob.replace("**/", "", 1)
    if (pattern and not _REGEX_META.search(pattern) and not pattern.startswith("-")
            and "/" not in mask and "\\" not in mask):
        if not root:
            # 13.09: дом = 17 ГБ, 233 тыс. файлов, из них .md — 29 928, а пол за вызов смотрит
            # 5 000 и молчал об этом: «Ничего не нашла (5000 файлов по **/*.md)» за 0,3 с при
            # лежащей в workspace/inbox записке. Без root ищем по частям и называем усечения.
            return _floor_search_home(pattern, glob), "ok"
        r = hands.search(pattern, glob=glob, root=".", cap=SEARCH_CAP, base=selected)
        if r is None:
            return _search_silent(root), "failed"
        if r.get("ok"):
            hits = r.get("hits") or []
            seen = int(r.get("files_seen") or 0)
            cut = (f"\n… (пол обошёл только первые {FLOOR_FILE_BUDGET} файлов под {root} — "
                   f"сузь маску или root)" if seen >= FLOOR_FILE_BUDGET else "")
            cut += "".join(f"\n… ({note})" for note in _floor_limits(r, root))
            if not hits:
                return f"Ничего не нашла ({seen} файлов по {glob}).{cut}", "ok"
            return "\n".join(hits) + (f"\n… (потолок {SEARCH_CAP} — сузь паттерн)"
                                      if r.get("capped") else "") + cut, "ok"
    base = selected
    if base is None or not base.is_dir():
        return "Не ищу: плохой корень поиска.", "refused"
    # Регулярка и составная маска — тот же отдельный процесс: тяжёлое выражение держало бы GIL
    # основного процесса, и прервать его изнутри нечем. Обход там ЛЕНИВЫЙ и ограниченный:
    # 04.08 `sorted(base.glob(glob))` материализовал всё дерево до первого чтения, и поиск
    # не вернулся 4.5 часа. Порядок выдачи сохранён: сортируем НАЙДЕННОЕ, а не всё дерево.
    r = hands.walk(pattern, glob, base, cap=SEARCH_CAP, scan_cap=SEARCH_SCAN_CAP,
                   seconds=SEARCH_SECONDS, skip_runs=not root, base=BASE)
    if r is None:
        return _search_silent(root or "дому"), "failed"
    if not r.get("ok"):
        return f"Не ищу: {r.get('msg')}.", "refused"
    found = sorted(r["rows"], key=lambda row: (row[0], row[1]))
    files_seen, scanned = r["files_seen"], r["scanned"]
    hits = [f"{rel}:{i}: {text}" for rel, i, text in found[:SEARCH_CAP]]
    stop = {"scan": f"обошла {SEARCH_SCAN_CAP} путей и остановилась",
            "time": f"искала {SEARCH_SECONDS:.0f}с и остановилась",
            "cap": f"потолок {SEARCH_CAP} совпадений"}.get(r["stop"], "")
    for limit in _floor_limits(r, root or "доме"):
        stop = (stop + "; " if stop else "") + limit
    if not root and (base / _SEARCH_RUNS_REL).is_dir():
        stop = (stop + "; " if stop else "") + f"{_SEARCH_RUNS_REL} не искала — укажи root={_SEARCH_RUNS_REL}"
    if not hits:
        tail = f" — {stop}" if stop else ""
        return f"Ничего не нашла ({files_seen} файлов по {glob}, путей обошла {scanned}{tail}).", "ok"
    # Усечение НАЗЫВАЕТСЯ. Молчаливое «ничего не нашла» после обрыва обхода — это ложь:
    # она бы решила, что искомого нет, а мы просто не дошли.
    return "\n".join(hits) + (f"\n… ({stop}; путей обошла {scanned}, файлов прочла "
                              f"{files_seen} — сузь маску или корень)" if stop else ""), "ok"


def fs_ls(path: str = "") -> str:
    p = _resolve_read(path or str(BASE))
    if p is None or not p.is_dir():
        tool_report.note(outcome="refused", target=str(path))
        return "Нет такой директории (или вне дома)."
    tool_report.note(outcome="ok", target=str(p))
    out = []
    for c in sorted(p.iterdir()):
        if _is_secret(c):
            continue
        mark = "/" if c.is_dir() else ""
        size = "" if c.is_dir() else f" · {c.stat().st_size}Б"
        stamp = when(c)
        out.append(f"{c.name}{mark}{size}" + (f" · {stamp}" if stamp else ""))
    return "\n".join(out[:200]) or "(пусто)"


# --------------------------------------------------------------------------- #
#  Запуск и зависимости
# --------------------------------------------------------------------------- #

def _venv_python(d: Path) -> Path | None:
    for cand in (d / ".venv" / "bin" / "python", d / ".venv" / "Scripts" / "python.exe"):
        if cand.exists():
            return cand
    return None


def run(command: str, project: str, timeout: int = RUN_TIMEOUT) -> str:
    """Запуск в проекте мастерской: тайм-аут, джейл cwd, потолок вывода и расписка в hands."""
    d = _project_dir(project)
    if d is None:
        return f"Нет проекта {_slug(project)} — для проекта используй shell/python с cwd его каталога."
    timeout = max(1, min(int(timeout or RUN_TIMEOUT), 600))
    log.info("workshop run [%s] $ %s", d.name, command[:120])
    tool_report.note(target=d.name)
    r = hands.execute(command, cwd=str(d), timeout=timeout, cap=RUN_OUT_CAP, base=BASE)
    receipt = {"kind": "process", "argv": ["bash", "-lc", command], "cwd": str(d)}
    if "out" not in r:
        tool_report.execution({**receipt, "status": "launch_failed", "error": str(r.get("msg"))[:200]})
        tool_report.note(outcome="failed")
        return f"[ошибка запуска] {r.get('msg')}"
    tool_report.execution({**receipt, "status": "timed_out" if r.get("killed") else "returned",
                           "exit_code": int(r.get("code", 0))})
    tool_report.note(outcome="ok" if int(r.get("code", 0)) == 0 and not r.get("killed") else "failed")
    out = (r.get("out") or "").strip() or "(пустой вывод)"
    code = int(r.get("code", 0))
    return out + (f"\n[exit {code}]" if code != 0 and not r.get("killed") else "")


def run_tests(project: str = "self") -> str:
    """self → selfdev.run_tests в АКТУАЛЬНОМ worktree предложения; проект → pytest||unittest."""
    if (project or "self").strip().lower() == "self":
        open_props = [t for t in selfdev.all_items(50) if t.get("status") == "building"]
        if not open_props:
            return ("Нет открытого предложения: тесты ядра гоняются в его worktree "
                    "(proposal(action=start) → правки → proposal(action=test) → proposal(action=submit)).")
        pid = open_props[-1]["id"]
        res = selfdev.run_tests(pid)
        return f"[предложение {pid}] {res['summary']}"
    d = _project_dir(project)
    if d is None:
        return f"Нет проекта {_slug(project)}."
    py = _venv_python(d) or Path(sys.executable)
    try:
        r = subprocess.run([str(py), "-m", "pytest", "-q"], cwd=str(d), capture_output=True,
                           text=True, timeout=RUN_TIMEOUT)
        out = (r.stdout or "") + (r.stderr or "")
        if "No module named pytest" in out:
            r = subprocess.run([str(py), "-m", "unittest", "discover", "-q"], cwd=str(d),
                               capture_output=True, text=True, timeout=RUN_TIMEOUT)
            out = (r.stdout or "") + (r.stderr or "")
        return _cap_output(out.strip()) + ("" if r.returncode == 0 else f"\n[exit {r.returncode}]")
    except subprocess.TimeoutExpired:
        return f"[тесты не уложились в {RUN_TIMEOUT}s]"


_PKG_RE = re.compile(r"^[A-Za-z0-9_.\[\]=<>!,~-]+$")


def pip_install(project: str, packages: str) -> str:
    d = _project_dir(project)
    if d is None:
        return f"Нет проекта {_slug(project)} — сначала project(action=create)."
    pkgs = [p for p in str(packages or "").split() if p]
    if not pkgs:
        return "Скажи, что ставить: pip_install(project, \"requests rich\")."
    bad = [p for p in pkgs if not _PKG_RE.match(p) or p.startswith("-")]
    if bad:
        return f"Подозрительные аргументы (не пакеты): {', '.join(bad)} — не ставлю."
    size = _du_mb(d)
    if size > QUOTA_MB:
        return (f"Квота проекта превышена ({size:.0f}/{QUOTA_MB} МБ) — сначала почисти "
                "(.venv пересоздать, артефакты удалить), потом ставь.")
    py = _venv_python(d)
    if py is None:
        try:
            r = subprocess.run([sys.executable, "-m", "venv", str(d / ".venv")],
                               capture_output=True, text=True, timeout=PIP_TIMEOUT)
            if r.returncode != 0:
                return f"venv не создался: {_cap_output((r.stderr or r.stdout or '').strip(), 500)}"
        except subprocess.TimeoutExpired:
            return "venv не создался (таймаут)."
        py = _venv_python(d)
        if py is None:
            return "venv не создался (python не нашёлся внутри)."
    try:
        r = subprocess.run([str(py), "-m", "pip", "install", "-q", *pkgs], cwd=str(d),
                           capture_output=True, text=True, timeout=PIP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"[pip не уложился в {PIP_TIMEOUT}s]"
    out = _cap_output(((r.stdout or "") + (r.stderr or "")).strip(), 1500)
    size = _du_mb(d)
    over = f"\n⚠ Квота: {size:.0f}/{QUOTA_MB} МБ — превышена, почисти проект!" if size > QUOTA_MB \
        else f"\nДиск проекта: {size:.0f}/{QUOTA_MB} МБ."
    if r.returncode != 0:
        return f"pip упал:\n{out}{over}"
    return f"Поставила: {' '.join(pkgs)} (в .venv проекта).{over}"


# --------------------------------------------------------------------------- #
#  Доставка и карта кода
# --------------------------------------------------------------------------- #

def send_file(path: str, caption: str = "", to: str = "") -> str:
    """Документ в текущий Telegram-чат; photo/audio идут через live-turn spool."""
    p = _resolve_read(path)
    if p is None:
        return "Не отправляю: путь вне дома."
    if not p.is_file():
        return f"Нет файла {path}."
    import agent  # лениво: мост живёт в agent._TELETHON, кладёт его раннер
    import media as media_core
    kind = media_core.media_kind(media_core.sniff_mime(p))
    explicit_to = bool(str(to or "").strip())
    live_turn = (agent._TURN_CHANNEL.get() is not None
                 and agent._TURN_OUTBOUND.get() is not None)
    execution = agent.current_tool_execution()
    execution_key = (str(execution.get("idempotency_key") or "")
                     if isinstance(execution, dict) else "")
    stage_owned = execution_key.startswith("turn-media-stage:")
    direct_owned = execution_key.startswith("telegram-outbox:")
    if not explicit_to and (stage_owned or (live_turn and not direct_owned)):
        if not live_turn:
            return "Медиа можно подготовить только из живого хода Telegram."
        if kind in ("photo", "audio"):
            return agent.tool_send_media(str(p), kind, caption=caption or "")
        # A normal document authored/requested in a live Telegram turn belongs to
        # that turn's guarded durable outbox.  Auxiliary task windows without a
        # live channel retain the explicit synchronous owner-delivery fallback.
        return agent.tool_send_media(str(p), "document", caption=caption or "")
    fn = agent._TELETHON.get("send_file")
    if not fn:
        return "Недоступно (нет связи с Telethon)."
    # 03.10: подпись прямого файла — текст модели; чистим тем же гардом, что и голос
    caption, _cap_note = agent._strip_generation_artifacts(str(caption or ""), "")
    try:
        if str(to or "").strip():
            # ⚠ 13.08.2026. Здесь адресное фото становилось документом: явный `to` уводил
            # мимо типизированного пути, а durable-намерение знало только «файл». Тип
            # теперь едет с намерением, поэтому «отправь ему этот скрин» приходит скрином.
            return str(fn(str(p), caption or "", str(to).strip(),
                          kind if kind in ("photo", "audio") else "document", False))
        return str(fn(str(p), caption or ""))
    except Exception as e:
        if isinstance(e, agent.DurableSideEffectPending):
            raise
        return f"Не отправился: {type(e).__name__}: {str(e)[:150]}"


_MAP_CACHE: dict = {"key": None, "text": None}


def _map_fingerprint(root: Path) -> str:
    parts = [str(root)]
    for p in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        try:
            parts.append(f"{p.name}:{p.stat().st_mtime_ns}")
        except OSError:
            pass
    return "|".join(parts)


def code_map(scope: str = "self") -> str:
    """ast-карта: модуль → классы/def (строки, первая строка докстринга). Кэш по mtime."""
    scope = (scope or "self").strip()
    if scope.lower() == "self":
        root = REPO
    else:
        d = _project_dir(scope)
        if d is None:
            return f"Нет проекта {_slug(scope)} (для своего кода — scope=\"self\")."
        root = d
    return _map_for_root(root)


def _map_for_root(root: Path) -> str:
    fp = _map_fingerprint(root)
    if _MAP_CACHE["key"] == fp and _MAP_CACHE["text"]:
        return _MAP_CACHE["text"]
    out = []
    for p in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        rel = p.relative_to(root)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError):
            out.append(f"{rel} — (не разобрался)")
            continue
        doc = (ast.get_docstring(tree) or "").strip().splitlines()
        out.append(f"{rel}" + (f" — {doc[0][:100]}" if doc else ""))
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                cdoc = (ast.get_docstring(n) or "").strip().splitlines()
                out.append(f"  class {n.name} :{n.lineno}" + (f" — {cdoc[0][:80]}" if cdoc else ""))
                for m in n.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.append(f"    def {m.name} :{m.lineno}")
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fdoc = (ast.get_docstring(n) or "").strip().splitlines()
                out.append(f"  def {n.name} :{n.lineno}" + (f" — {fdoc[0][:80]}" if fdoc else ""))
    text = "\n".join(out)[:MAP_CAP] or "(нет .py файлов)"
    _MAP_CACHE.update(key=fp, text=text)
    return text


def mutation_lock():
    """One short shared transaction for workshop writes and local Forge edits.

    A Forge patch can touch multiple files: a target-specific lock would let it
    race an fs_edit of one hunk. Keep the lock outside project worktrees so its
    own bytes cannot enter a proposal/commit.
    """
    from self_model import FileLock
    return FileLock(BASE / "memory" / ".state" / "workshop-mutations.lock",
                    timeout=10.0)


def _serialized_write(path,proposal_id,operation) -> dict:
    import forge
    import forge_roots
    target,error=_mutation_target(path,proposal_id,"edit")
    if target is None:return hands.outcome('refused','Не пишу: '+str(error))
    result=None
    try:
        # Same order as Forge: physical root -> short workshop transaction.
        with forge_roots.transaction(target.parent,forge.STATE_DIR,10):
            reason=forge_roots.foreign_writer_reason(forge,'',root=target.parent)
            if reason:return hands.outcome('refused','Write not admitted while Forge owns this root: '+reason)
            with mutation_lock() as lock:
                result=operation()
            if lock.lost_to_takeover:return hands.outcome('in_doubt','Write outcome unknown: transaction ownership changed; inspect before retry.')
            return result
    except (TimeoutError,OSError) as exc:
        # Refusal means "nothing was done": only true while the operation has not run.
        if result is not None:return hands.after_release_failure(result,exc)
        return hands.outcome('refused',f'Write not admitted: {type(exc).__name__}: {exc}')


def _reported(op: str, path: str, result: dict) -> str:
    """Текст для неё: сказанное действие подтверждено распиской, а не самим возвратом вызова."""
    confirmed = result["status"] == "confirmed"
    hands.receipt(op, confirmed, path, f"{result['status']}: {result['message']}", base=BASE)
    tool_report.note(outcome=result["status"], target=str(path))
    return f"{result['message']} [confirmed sha256={result['after_sha256']}]" if confirmed else result["message"]


def fs_write_result(path: str,content: str,proposal_id: str='',overwrite: bool=False,force: bool=False,expected_sha256: str='') -> dict:
    return _serialized_write(path,proposal_id,lambda:_fs_write_unlocked(path,content,proposal_id,overwrite,force,expected_sha256))


def fs_write(path: str,content: str,proposal_id: str='',overwrite: bool=False,force: bool=False,expected_sha256: str='') -> str:
    return _reported('write',path,fs_write_result(path,content,proposal_id,overwrite,force,expected_sha256))


def fs_edit_result(path: str='', old: str='', new: str='', proposal_id: str='', expected_sha256: str='',
                   files=None, mode: str='snapshot', *, all: bool=False) -> dict:
    if files is not None:
        if path or old or new or expected_sha256 or all:
            import edit_batch
            return edit_batch.refused(files, 'Do not combine files with single-file arguments.')
        return _serialized_batch(files, proposal_id, mode)
    if mode != 'snapshot':
        return hands.outcome('refused', 'mode applies only to files batches.')
    return _serialized_write(path,proposal_id,lambda:_fs_edit_unlocked(path,old,new,proposal_id,expected_sha256,all=all))


def fs_edit(path: str='', old: str='', new: str='', proposal_id: str='', expected_sha256: str='',
            files=None, mode: str='snapshot', *, all: bool=False) -> str:
    value = fs_edit_result(path, old, new, proposal_id, expected_sha256, files, mode, all=all)
    if files is not None:
        import edit_batch
        for row in value['files']:
            hands.receipt('edit', row['status'] == 'confirmed', row['path'], row['status'], base=BASE)
        tool_report.note(outcome=value['status'], target=', '.join(str(r['path']) for r in value['files']))
        return edit_batch.render(value)
    return _reported('edit', path, value)


def _serialized_batch(files, proposal_id, mode):
    import edit_batch
    import forge
    import forge_roots
    if not isinstance(files, list) or not files:
        return edit_batch.refused(files, 'files must be a nonempty list.')
    targets = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not item['path']:
            return edit_batch.refused(files, 'Each file needs a path.')
        target, error = _mutation_target(item['path'], proposal_id, 'edit')
        if target is None:
            return edit_batch.refused(files, str(error))
        targets.append(target)
    value = None
    try:
        # One enclosing physical-root lock avoids self-deadlock on overlapping parents.
        common = Path(os.path.commonpath([str(p.parent) for p in targets]))
        with forge_roots.transaction(common, forge.STATE_DIR, 10):
            for target in targets:
                reason = forge_roots.foreign_writer_reason(forge, '', root=target.parent)
                if reason:
                    return edit_batch.refused(files, 'Write not admitted while Forge owns this root: '+reason)
            with mutation_lock() as lock:
                value = edit_batch.apply(files, lambda path: _mutation_target(path, proposal_id, "edit"), mode)
            if lock.lost_to_takeover:
                return edit_batch.uncertain(value, 'Transaction ownership changed; inspect before retry.')
            return value
    except (OSError, ValueError) as exc:
        if value is not None:
            return edit_batch.uncertain(value, f'Lock release failed: {type(exc).__name__}.')
        return edit_batch.refused(files, f'Write not admitted: {type(exc).__name__}.')


def fs_read_result(path: str) -> dict:
    """Точный текст и версия файла для скрипта: те же границы дома, что у fs_read."""
    p = _resolve_read(path)
    if p is None:
        return {"status": "refused", "message": "Не читаю: путь вне дома."}
    if not p.is_file():
        return {"status": "refused", "message": f"Нет файла {path}."}
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"status": "refused", "message": f"Файл {path} не в UTF-8."}
    return {"status": "ok", "text": text, "sha256": hashlib.sha256(raw).hexdigest()}
