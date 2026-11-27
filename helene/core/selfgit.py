"""
Praxis — git-рельсы для её само-изменений.

Любая правка её файлов (soul/skills, код) авто-коммитится плоским сообщением. Это
даёт обратимость и аудит — фундамент безопасности «полного» шелла: не «уровни
доверия» и песочницы, а git + лог + owner-gate.

Робастно: если репо/идентичности нет или git недоступен — тихо ничего не делаем.
"""

from __future__ import annotations

import os

import logging
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger("praxis-git")

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
TIMEOUT = 30
SNAPSHOT_REF_NS = "refs/praxis-snapshots"
SNAPSHOT_KEEP = 40


def _git(*args: str):
    return subprocess.run(["git", "-C", str(BASE), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=TIMEOUT)


#: Никогда не класть в её историю файл больше этого. 16.11: бэкап владельца на 4,8 ГБ
#: оказался в рабочем дереве, и `add -A` перед каждой командой хэшировал его до таймаута
#: (у агента команды ждали по пять минут), а история выросла до 21 ГБ.
MAX_FILE_BYTES = max(1, int(os.environ.get("PRAXIS_SELFGIT_MAX_FILE_MB", "50") or 50)) * 1024 * 1024
#: Сколько путей страхует один снимок: огромный неотслеживаемый каталог — не её работа.
SNAPSHOT_MAX_PATHS = 2000


def _status_entries() -> list[tuple[str, str]]:
    """(XY, путь) из `git status --porcelain -z --untracked-files=all`.

    Статус идёт по кэшу настоящего индекса (stat), поэтому неизменённые файлы не
    хэшируются; для переименования берётся новый путь.
    """
    r = _git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    items = r.stdout.split("\0")
    out, k = [], 0
    while k < len(items):
        entry = items[k]
        k += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if xy[0] in "RC":
            k += 1  # следом идёт прежний путь
        out.append((xy, path))
    return out


def _admissible(paths: list[str]) -> tuple[list[str], list[str]]:
    """Какие пути можно класть в историю: не под .gitignore (даже если когда-то попали
    в учёт) и не больше MAX_FILE_BYTES. -> (годные, пропущенные)."""
    if not paths:
        return [], []
    r = subprocess.run(["git", "-C", str(BASE), "check-ignore", "--no-index", "-z", "--stdin"],
                       input="\0".join(paths) + "\0", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=TIMEOUT)
    ignored = {x for x in r.stdout.split("\0") if x}
    keep, skipped = [], []
    for path in paths:
        if path in ignored:
            skipped.append(path)
            continue
        try:
            target = BASE / path
            if target.is_file() and target.stat().st_size > MAX_FILE_BYTES:
                skipped.append(path)
                continue
        except OSError:
            pass
        keep.append(path)
    if skipped:
        log.info("в историю не кладём (игнор или больше %d МБ): %s", MAX_FILE_BYTES // (1024 * 1024),
                 ", ".join(skipped[:5]) + (" …" if len(skipped) > 5 else ""))
    return keep, skipped


def _stage(paths: list[str], env: dict | None = None) -> bool:
    """`add -A` ровно по этим путям (буквально, без глобов): правки, новые и удаления."""
    if not paths:
        return True
    r = subprocess.run(["git", "--literal-pathspecs", "-C", str(BASE), "add", "-A",
                        "--pathspec-from-file=-", "--pathspec-file-nul"],
                       input="\0".join(paths) + "\0", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=TIMEOUT, env=env)
    if r.returncode != 0:
        log.warning("add по списку не удался: %s", (r.stderr or "")[:200])
        return False
    return True


def repo_ready() -> bool:
    return (BASE / ".git").exists()


def changed_paths() -> list[str]:
    """Пути изменённых/новых файлов (git status --porcelain), без gitignored."""
    if not repo_ready():
        return []
    try:
        r = _git("status", "--porcelain")
    except Exception:
        return []
    out = []
    for line in r.stdout.splitlines():
        p = line[3:].strip().strip('"')
        if p:
            out.append(p)
    return out


def recent(n: int = 3) -> list[str]:
    """PASS 5: последние коммиты одной строкой (для STATE) — «sha дата: сообщение»."""
    if not repo_ready():
        return []
    try:
        r = _git("log", f"-{max(1, int(n))}", "--pretty=format:%h %ad: %s",
                 "--date=format:%d.%m %H:%M")
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def _prune_snapshot_refs(keep: int = SNAPSHOT_KEEP) -> None:
    try:
        r = _git("for-each-ref", "--format=%(refname)", "--sort=refname", SNAPSHOT_REF_NS + "/")
        refs = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        for ref in (refs[:-keep] if keep > 0 else refs):
            _git("update-ref", "-d", ref)
    except Exception:
        log.debug("prune снапшот-рефов не удался", exc_info=True)


def list_safety_points(n: int = 10) -> list[str]:
    """Последние страховочные снапшоты: «ref sha» (для глаз/восстановления)."""
    if not repo_ready():
        return []
    try:
        r = _git("for-each-ref", "--format=%(refname:short) %(objectname:short)",
                 "--sort=-refname", SNAPSHOT_REF_NS + "/")
        return [l.strip() for l in r.stdout.splitlines() if l.strip()][:max(1, int(n))]
    except Exception:
        return []


def safety_point(message: str) -> str | None:
    """PASS 30 Этап 1: страховочный снапшот ВНЕ ветки — master не получает коммит.

    Состояние рабочего дерева (включая untracked, минус gitignored) сохраняется
    commit-объектом под refs/praxis-snapshots/<ts>-<sha> через ВРЕМЕННЫЙ индекс:
    настоящий индекс и рабочее дерево не меняются (её незакоммиченная работа и её
    staged-выбор остаются ровно как были — она коммитит в прод сама, осмысленно).
    Восстановление: `git checkout <sha> -- <путь>`; список — list_safety_points().
    Хранится последних SNAPSHOT_KEEP, старые рефы прунятся. -> короткий sha | None."""
    if not repo_ready():
        return None
    git_dir = _git("rev-parse", "--absolute-git-dir").stdout.strip()
    if not git_dir:
        return None  # и linked worktree (.git-файл) резолвится честно, не падает
    tmp_index = Path(git_dir) / f"praxis-snapshot-index-{os.getpid()}"
    try:
        head = _git("rev-parse", "HEAD").stdout.strip()
        if not head:
            return None  # репо без коммитов — не к чему цеплять родителя
        env = dict(os.environ, GIT_INDEX_FILE=str(tmp_index))

        def _giti(*args: str):
            return subprocess.run(["git", "-C", str(BASE), *args],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=TIMEOUT, env=env)

        # Коды возврата проверяются: усечённый снапшот, молча притворяющийся полной
        # страховкой, хуже честного отсутствия снапшота.
        # 2.8.4: не `add -A` по всему дереву с пустым индексом. Статус настоящего индекса
        # называет изменённое, в снимок идёт только оно (без игнорируемого и огромного),
        # а временный индекс начинается с копии настоящего — с его stat-кэшем.
        changed, _ = _admissible([path for _, path in _status_entries()])
        if not changed:
            return None  # дерево чистое — страховать нечего
        changed = changed[:SNAPSHOT_MAX_PATHS]
        real_index = Path(git_dir) / "index"
        try:
            shutil.copyfile(real_index, tmp_index)
        except OSError:
            if _giti("read-tree", "HEAD").returncode != 0:
                log.warning("safety_point: read-tree не удался")
                return None
        if not _stage(changed, env=env):
            return None
        tree = _giti("write-tree").stdout.strip()
        head_tree = _git("rev-parse", "HEAD^{tree}").stdout.strip()
        if not tree or tree == head_tree:
            return None  # дерево чистое — страховать нечего
        c = subprocess.run(
            ["git", "-C", str(BASE),
             "-c", "user.name=Praxis", "-c", "user.email=praxis@local",
             "commit-tree", tree, "-p", head, "-m", message],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, env=env)
        sha = (c.stdout or "").strip()
        if c.returncode != 0 or not sha:
            log.warning("safety_point commit-tree не удался: %s", (c.stderr or "")[:200])
            return None
        ref = f"{SNAPSHOT_REF_NS}/{time.strftime('%Y%m%dT%H%M%S')}-{sha[:8]}"
        _git("update-ref", ref, sha)
        _prune_snapshot_refs()
        log.info("safety-point %s: %s", sha[:8], message)
        return sha[:8]
    except Exception:
        log.warning("safety_point упал", exc_info=True)
        return None
    finally:
        try:
            tmp_index.unlink(missing_ok=True)
        except OSError:
            pass


def snapshot_paths(paths: list[str], message: str) -> str | None:
    """Закоммитить ТОЛЬКО названные пути (pathspec-коммит): чужая пред-существующая
    грязь и её staged-выбор по другим путям остаются нетронутыми. -> sha | None."""
    paths = [p for p in (paths or []) if p]
    if not repo_ready() or not paths:
        return None
    try:
        # Каталог из статуса (`?? dir/`) раскрывается в файлы, чтобы большой файл внутри
        # не проскочил проверку размера; игнорируемое не коммитится никогда — иначе
        # `commit -- путь` вернул бы в учёт уже снятый служебный файл.
        wanted = [p.rstrip("/") for p in paths]
        files = [path for _, path in _status_entries()
                 if any(path == w or path.startswith(w + "/") for w in wanted)]
        files, _ = _admissible(files or paths)
        files = files[:200]
        if not files or not _stage(files):
            return None
        c = subprocess.run(
            ["git", "--literal-pathspecs", "-C", str(BASE),
             "-c", "user.name=Praxis", "-c", "user.email=praxis@local",
             "commit", "-q", "-m", message, "--", *files],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
        )
        if c.returncode != 0:
            log.warning("pathspec-коммит не удался: %s", (c.stderr or "")[:200])
            return None
        sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
        log.info("self-commit %s: %s", sha, message)
        return sha
    except Exception:
        log.warning("snapshot_paths упал", exc_info=True)
        return None


def snapshot(message: str) -> str | None:
    """Закоммитить текущие изменения плоским сообщением. -> sha или None, если коммитить нечего."""
    if not repo_ready():
        return None
    try:
        files, _ = _admissible([path for _, path in _status_entries()])
        if not files or not _stage(files[:SNAPSHOT_MAX_PATHS]):
            return None
        if _git("diff", "--cached", "--quiet").returncode == 0:
            return None  # нечего коммитить
        c = subprocess.run(
            ["git", "-C", str(BASE),
             "-c", "user.name=Praxis", "-c", "user.email=praxis@local",
             "commit", "-q", "-m", message],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
        )
        if c.returncode != 0:
            log.warning("self-commit не удался: %s", (c.stderr or "")[:200])
            return None
        sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
        log.info("self-commit %s: %s", sha, message)
        return sha
    except Exception:
        log.warning("snapshot упал", exc_info=True)
        return None
