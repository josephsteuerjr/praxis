"""
hands — пол её рук на чистом Python: снимок и публикация файла, поиск, скелет, запуск.

Здесь был мост к Rust-бинарю `praxis-hands` с откатом на питон. Откат и был дырой: после
потерянного ответа бинаря нельзя знать, совершилась ли запись, а `run` мог запустить ту же
команду вторым исполнителем. Теперь исполнитель один, и про файл есть только три ответа:
подтверждено чтением результата, отказ до действия, честное «исход неизвестен».

Поиск идёт отдельным процессом того же питона: он читает тысячи файлов, и ни его память,
ни его время не должны доставаться её основному процессу (сам поиск — hands_search.py).

Каждая операция оставляет расписку в memory/.state/hands.jsonl (что, где, чем кончилось).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("praxis-hands")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)

SKIP_DIRS = frozenset({".git", ".venv", "__pycache__", ".proposals", "node_modules", ".vectors"})
#: Сколько файлов поиск берёт за один вызов; усечение называет вызывающий.
SEARCH_FILE_BUDGET = 5000
#: Файл крупнее этого поиск не читает и называет его в ответе.
SEARCH_MAX_FILE_BYTES = 128 * 1024 * 1024
#: Срок поиска. Тот же рычаг, что и у питонового обхода мастерской; 0 снимает его.
SEARCH_SECONDS = float(os.getenv("PRAXIS_SEARCH_SECONDS", "45") or 0)
#: Запас родителя сверх срока ребёнка: ребёнок обязан ответить сам.
_SEARCH_PARENT_PAD = 30
_TEMP_PREFIX = ".praxis-write-"


def _base(base: str | Path | None = None) -> Path:
    """Дом, относительно которого судим. Вызывающий (мастерская) знает его точно —
    её BASE подменяется в тестах и в worktree предложений; глобал здесь только дефолт."""
    return Path(base) if base else BASE


def receipts_path(base: str | Path | None = None) -> Path:
    return _base(base) / "memory" / ".state" / "hands.jsonl"


def receipt(op: str, ok: bool, target: str, note: str = "", base: str | Path | None = None) -> None:
    """След операции, даже неудачной. Сбой записи расписки не роняет её ход."""
    row = json.dumps({"ts": int(time.time()), "op": op, "ok": bool(ok),
                      "target": str(target)[:200], "note": str(note)[:200]},
                     ensure_ascii=False, separators=(",", ":"))
    path = receipts_path(base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(row + "\n")
    except OSError:
        log.debug("расписка не записалась: %s", path, exc_info=True)


def _inside(root: Path, raw: str) -> Path | None:
    """Лексическое разрешение внутри `root`; None, если путь из него выходит."""
    base = os.path.normpath(str(root))
    joined = os.path.normpath(os.path.join(base, str(raw or ".")))
    return Path(joined) if joined == base or joined.startswith(base.rstrip(os.sep) + os.sep) else None


def resolve_read(path: str, base: str | Path | None = None) -> Path | None:
    """Resolved read within one home. Does not change execute's lexical cwd contract."""
    home = _base(base).resolve()
    try:
        target = (home / path).resolve()
        target.relative_to(home)
        return target
    except (OSError, ValueError, RuntimeError):
        return None


# --------------------------------------------------------------------------- #
#  Файловая мутация: снимок → проверка версии → новые байты → публикация → чтение
# --------------------------------------------------------------------------- #

def outcome(status: str, message: str, before: str | None = None, after: str | None = None) -> dict:
    """status: confirmed | refused | in_doubt. Больше исходов у правки не бывает."""
    return {"status": status, "message": message, "before_sha256": before, "after_sha256": after}


def snapshot(path: Path) -> tuple[bytes | None, str | None]:
    """Точные байты файла и их SHA-256; (None, None) — файла нет."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return None, None
    return data, hashlib.sha256(data).hexdigest()


def _digest_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def version_conflict(expected: str, current: str | None) -> str:
    """Текст отказа, если ожидаемая версия не совпала; '' — можно править.

    Принимает полный 64-hex и старый 16-hex префикс: Forge выдаёт оба, и молча ломать
    тех, кто держит короткий отпечаток, нельзя."""
    wanted = str(expected or "").strip().lower()
    if not wanted:
        return ""
    if len(wanted) not in (16, 64) or not re.fullmatch(r"[0-9a-f]+", wanted):
        return "expected_sha256: нужен полный 64-hex хеш или legacy 16-hex prefix."
    if wanted == (current or "")[:len(wanted)]:
        return ""
    return (f"Конфликт версии: ожидался sha256={wanted}, сейчас {current or 'файла нет'}. "
            "Перечитай файл и наложи правку на актуальную версию.")


def replace_once(raw: bytes, old: str, new: str, *, all: bool = False) -> tuple[bytes | None, str]:
    """`old` → `new` ровно в одном месте. -> (новые байты, '') либо (None, текст отказа).

    Файл обязан быть валидным UTF-8: иначе замена молча подменила бы или потеряла чужие
    байты. Переводы строк не нормализуются — всё вне `old` остаётся побайтно."""
    if not isinstance(all, bool):
        return None, "all must be a boolean; no bytes changed."
    if not isinstance(old, str) or not old or not isinstance(new, str):
        return None, "Пустой old — так не правлю (для нового файла есть edit(action=write))."
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, (f"Файл не в UTF-8 (байт {exc.start}) — точечная правка потеряла бы его байты. "
                      "Нужна полная перезапись или патч.")
    # Count overlapping occurrences too: 'aa' in 'aaa' is ambiguous, not unique.
    count, cursor = 0, 0
    while True:
        index = text.find(old, cursor)
        if index < 0:
            break
        count += 1
        cursor = index + 1
    if count == 0:
        hint = (" В файле переводы строк CRLF, а в old — LF."
                if "\r\n" in text and "\n" in old and "\r\n" not in old else "")
        return None, ("Вхождение не найдено (0 совпадений). Проверь точный текст через fs_read "
                      "(task_id выбирает Forge): пробелы/табуляция/кавычки должны совпадать "
                      "байт в байт." + hint)
    if all and count != text.count(old):
        return None, "Overlapping matches are ambiguous even with all=true; widen old."
    if count > 1 and not all:
        positions, cursor = [], 0
        for _ in range(min(count, 5)):
            index = text.index(old, cursor)
            positions.append(f"строка {text[:index].count(chr(10)) + 1}")
            cursor = index + 1
        return None, (f"Неоднозначно: {count} совпадений (первые: {', '.join(positions)}). Расширь "
                      "old несколькими строками контекста вокруг нужного места, чтобы вхождение "
                      "стало уникальным.")
    try:
        return text.replace(old, new, -1 if all else 1).encode("utf-8"), ""
    except UnicodeEncodeError:
        return None, "new содержит символы, которые не кодируются в UTF-8."


def _open_temp(directory: Path, fresh: bool) -> tuple[int, str]:
    """Временный файл рядом с целью: права, с которыми он потом опубликуется.

    Для нового файла режим назначает ядро, как при обычном создании: 0o666 минус umask, плюс
    ACL и setgid каталога. Поэтому umask не читается и не меняется: он общий на весь процесс,
    а пишут здесь из разных потоков. Заменяющий файл приватен, пока не получит права прежнего."""
    if not fresh:
        return tempfile.mkstemp(prefix=_TEMP_PREFIX, dir=directory)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(64):
        name = str(directory / f"{_TEMP_PREFIX}{secrets.token_hex(8)}")
        try:
            return os.open(name, flags, 0o666), name
        except FileExistsError:
            continue
    raise FileExistsError(f"не нашлось свободного имени в {directory}")


def _copy_attributes(temp: str, target: Path) -> None:
    """Права и владелец как у файла, который заменяем."""
    info = target.stat()
    os.chmod(temp, stat.S_IMODE(info.st_mode))
    if hasattr(os, "chown"):
        try:
            os.chown(temp, info.st_uid, info.st_gid)
        except PermissionError:
            log.debug("владельца %s сохранить не удалось", target, exc_info=True)


def publish(target: Path, data: bytes, previous: str | None,
            recheck: Callable[[], str] | None = None) -> dict:
    """Опубликовать полные байты одного файла и подтвердить их чтением результата.

    `previous` — SHA-256 содержимого, из которого посчитаны `data` (None — файла не было).
    Сравнение-и-замену гарантирует только общий замок писателей: его держит вызывающий
    (fs_*, forge.edit). Писателя вне замка (shell) перепроверка ловит лишь до последнего чтения:
    между ней и os.replace его байты будут затёрты, а ответ всё равно confirmed, обычный rename
    этого окна не закрывает. Создание нового файла через link от этого свободно.
    `recheck` -> текст отказа или ''. Что бы ни случилось после попытки публикации — это
    in_doubt: второго исполнителя и слепого повтора нет."""
    temp: str | None = None
    attempted = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = _open_temp(target.parent, fresh=previous is None)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if previous is not None:
            _copy_attributes(temp, target)
        refusal = recheck() if recheck else ""
        if refusal:
            return outcome("refused", f"Не пишу: {refusal}.", previous)
        _, current = snapshot(target)
        if current != previous:
            return outcome("refused", "Не пишу: файл изменился до записи (conflict); перечитай его.",
                           current)
        attempted = True
        if previous is None:
            # link — атомарное «создать, если нет»; replace затёр бы параллельного создателя.
            try:
                os.link(temp, target)
            except FileExistsError:
                attempted = False
                return outcome("refused", "Не пишу: файл появился до записи (conflict); перечитай его.")
        else:
            os.replace(temp, target)
            temp = None
        observed = _digest_of(target)
        if observed != hashlib.sha256(data).hexdigest():
            return outcome("in_doubt", "Исход неизвестен: записанное не совпало с намеченным; "
                                       "перечитай файл, не повторяй слепо.", previous, observed)
        return outcome("confirmed", "Записано и подтверждено чтением.", previous, observed)
    except OSError as exc:
        if attempted:
            return outcome("in_doubt", f"Исход неизвестен: {type(exc).__name__}; перечитай файл, "
                                       "не повторяй слепо.", previous)
        return outcome("refused", f"Не пишу: {type(exc).__name__}; файл не тронут.", previous)
    finally:
        if temp is not None:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
            except OSError:
                log.warning("остался временный файл записи: %s", temp)


def after_release_failure(result: dict, exc: BaseException) -> dict:
    """Операция отработала, а замок не отпустился: что отвечать.

    Отказ остаётся отказом, файл не тронут. Записанное же нельзя назвать отказом до действия,
    но и «подтверждено» нельзя: единоличное владение до конца не доказано, как и при перехвате."""
    log.warning("замок не отпустился после операции: %s", exc, exc_info=exc)
    if result["status"] == "refused":
        return result
    return outcome("in_doubt", f"Исход неизвестен: замок не отпустился после записи ({type(exc).__name__}); "
                               "перечитай файл, не повторяй слепо.",
                   result["before_sha256"], result["after_sha256"])


# --------------------------------------------------------------------------- #
#  Скелет python-файла
# --------------------------------------------------------------------------- #

_OUTLINE = re.compile(r"(class|def|async def)\s+(\w+)")


def outline(path: str, base: str | Path | None = None) -> dict:
    """Классы и функции построчно: работает и на файле, который не разбирается как python."""
    root = _base(base)
    target = resolve_read(path, base=root)
    if target is None:
        return {"ok": False, "msg": "путь вне дома"}
    try:
        text = target.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"ok": False, "msg": f"не читается: {type(exc).__name__}"}
    items: list[dict] = []
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.lstrip()
        found = _OUTLINE.match(stripped)
        if found:
            items.append({"kind": found.group(1), "name": found.group(2), "line": number,
                          "indent": len(line) - len(stripped)})
            if len(items) >= 400:
                break
    receipt("outline", True, path, f"{len(items)} символов кода", base=root)
    return {"ok": True, "items": items}


# --------------------------------------------------------------------------- #
#  Поиск: родитель спрашивает, отдельный процесс читает
# --------------------------------------------------------------------------- #

def _ask_search_process(request: dict, seconds: float) -> dict | None:
    """Запрос JSON в stdin, ответ JSON из stdout. None — ответа нет.

    Срок держит сам ребёнок. Родитель ждёт с запасом и убивает молчащего: тяжёлую регулярку
    изнутри не прервать, а процесс можно. При seconds=0 срока нет, и ждём сколько нужно."""
    script = str(Path(__file__).resolve().with_name("hands_search.py"))
    wait = seconds + _SEARCH_PARENT_PAD if seconds > 0 else None
    try:
        proc = subprocess.run([sys.executable, "-I", "-S", "-B", script],
                              input=json.dumps(request), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=wait)
        return json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        log.warning("поиск не ответил", exc_info=True)
        return None


def walk(pattern: str, glob: str, folder: Path, *, cap: int, scan_cap: int, seconds: float,
         skip_runs: bool, base: str | Path | None = None) -> dict | None:
    """Регулярка или составная маска, в том же отдельном процессе.

    -> {ok, rows: [[файл, строка, текст]], files_seen, scanned, stop, too_large}; None — не ответил."""
    home = _base(base)
    answer = _ask_search_process({"mode": "walk", "root": str(folder), "pattern": pattern, "glob": glob,
                                  "cap": cap, "scan_cap": scan_cap, "seconds": seconds,
                                  "max_bytes": SEARCH_MAX_FILE_BYTES, "skip_dirs": sorted(SKIP_DIRS),
                                  "skip_runs": skip_runs}, seconds)
    if answer is not None:
        receipt("search", True, pattern, f"{len(answer.get('rows') or [])} совпадений", base=home)
    return answer


def search(literal: str, glob: str = "**/*.py", root: str = "", cap: int = 60,
           base: str | Path | None = None, *, scope_root: Path | None = None) -> dict | None:
    """-> {ok, files_seen, capped, hits, out_of_time, too_large}; None — процесс не ответил."""
    if not literal:
        return {"ok": False, "msg": "нужен непустой литерал"}
    home = _base(base)
    folder = resolve_read(root or ".", base=home)
    if folder is None or not folder.is_dir():
        return {"ok": False, "msg": "плохой корень поиска"}
    # Scope may span budget partitions, but never grant authority beyond home.
    scope = resolve_read(str(scope_root), base=home) if scope_root is not None else folder
    if scope is None or not scope.is_dir() or not folder.is_relative_to(scope):
        return {"ok": False, "msg": "плохая область поиска"}
    request = {"root": str(folder), "scope_root": str(scope), "literal": literal, "glob": glob, "cap": cap,
               "budget": SEARCH_FILE_BUDGET, "seconds": SEARCH_SECONDS,
               "max_bytes": SEARCH_MAX_FILE_BYTES, "skip_dirs": sorted(SKIP_DIRS)}
    answer = _ask_search_process(request, SEARCH_SECONDS)
    if answer is None:
        return None
    receipt("search", True, literal, f"{len(answer.get('hits') or [])} совпадений", base=home)
    return answer


# --------------------------------------------------------------------------- #
#  Запуск
# --------------------------------------------------------------------------- #

def _cap_output(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head, tail = text[: int(cap * 0.6)], text[-int(cap * 0.35):]
    return f"{head}\n… (вырезано {len(text) - len(head) - len(tail)} символов) …\n{tail}"


def _read_capped(stream, cap: int) -> str:
    """Голова и хвост вывода, не поднимая в память гигабайты зациклившейся команды."""
    from command_execution import output_decoder as decode
    size = os.fstat(stream.fileno()).st_size
    window = max(cap, 1) * 4  # символ UTF-8 — до четырёх байт
    stream.seek(0)
    if size <= window * 2:
        return _cap_output(decode(stream.read()), cap)
    head = decode(stream.read(window))
    stream.seek(-window, os.SEEK_END)
    tail = decode(stream.read(window))
    return (f"{head[: int(cap * 0.6)]}\n… (вырезано около {size - 2 * window} байт) …\n"
            f"{tail[-int(cap * 0.35):]}")


def execute_argv(argv: list[str], cwd: str, timeout: int = 120, cap: int | None = 8000,
                 base: str | Path | None = None, *, confined: bool = True, stdin=None,
                 label: str = "") -> dict:
    """One launch with file output; normal exit leaves background descendants alive.

    A timeout terminates the private process group. `confined=False` retains the
    sovereign shell's existing ability to use any directory. stdin is an already
    opened byte stream, so Python source never passes through shell quoting.
    """
    home = _base(base)
    place = _inside(home, cwd) if confined else Path(cwd).resolve()
    if place is None or not place.is_dir():
        return {"ok": False, "msg": "рабочий каталог вне дома или не существует"}
    started = time.monotonic()
    label = label or str(argv)
    import command_execution
    if command_execution.home_prepare:
        argv, place = command_execution.home_prepare(argv, place)
    if command_execution.home_runner:
        import hand_bridge
        try:
            with command_execution.observe_home() as observed:
                text, code, killed = command_execution.home_runner(
                    argv, place, timeout, stdin=stdin, env=hand_bridge.child_environment(None))
        except (OSError, RuntimeError) as exc:
            receipt('exec', False, label, str(exc), base=home)
            return {'ok': False, 'outcome': 'in_doubt', 'msg': f'contained command outcome unknown: {exc}'}
        if cap is not None:
            text = _cap_output(text, cap)
        if killed:
            text += f"\n⏱ прервано по тайм-ауту ({timeout}с)"
        ok = code == 0 and not killed
        receipt('exec', ok, label, f'code={code} killed={killed}', base=home)
        return {'ok': ok, 'code': code, 'killed': killed, 'out': text,
                'execution': dict(observed)}
    with tempfile.TemporaryFile() as output:
        try:
            import command_execution
            proc = command_execution.launch(argv, cwd=place, stdin=stdin, stdout=output)
        except OSError as exc:
            receipt("exec", False, label, f"не запустилось: {exc}", base=home)
            return {"ok": False, "msg": f"не запустилось: {exc}"}
        killed = False
        try:
            code = proc.wait(timeout if timeout and timeout > 0 else None)
        except subprocess.TimeoutExpired:
            killed, code = True, -1
            proc.stop_tree()
        if cap is None:
            output.seek(0)
            text = command_execution.output_decoder(output.read())
        else:
            text = _read_capped(output, cap)
    if killed:
        text += f"\n⏱ прервано по тайм-ауту ({timeout}с)"
    ok = code == 0 and not killed
    receipt("exec", ok, label, f"code={code} killed={killed} "
                              f"dur_ms={int((time.monotonic() - started) * 1000)}", base=home)
    return {"ok": ok, "code": code, "killed": killed, "out": text,
            "execution": {"argv": list(proc.args), "cwd": str(place), "pid": proc.pid}}


def execute(shell: str, cwd: str, timeout: int = 120, cap: int = 8000,
            base: str | Path | None = None) -> dict:
    """The project shell uses the same launcher with its existing home boundary."""
    from command_execution import shell_argv
    return execute_argv(shell_argv(str(shell)), cwd, timeout, cap, base,
                        label=str(shell))


# --------------------------------------------------------------------------- #
#  Что она видит о своих руках
# --------------------------------------------------------------------------- #

def available() -> bool:
    """Пол рук всегда на месте: он и есть этот модуль."""
    return True


def state_line() -> str:
    """Строка для my_capabilities/STATE."""
    return "руки: чистый питон; файл — сверка версии, публикация и проверка записанного"
