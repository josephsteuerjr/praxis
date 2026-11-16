"""Keep the agent's personal Git small and quick (2.8.4).

16.11.2026, Jarvis: the move to a new programme folder put the owner's backup folder inside
the agent's data, and weekly 5 GB backups went into the personal repository. Its .git grew
to 21 GB (one 4.8 GB blob Git for Windows could not even read back), every self-commit ran a
foreground repack that never finished inside its 30-second limit, and the safety snapshot
before each command re-hashed the backup. Shell commands waited up to five minutes.

At start, before the runner uses Git, this module:
- turns off automatic gc/maintenance for the personal repository;
- deletes temporary pack files left by interrupted repacks;
- removes owner backups and service files from HEAD (the real index and files stay);
- if the repository is large and its history contains those paths, rewrites the history
  without them, verifies every ref's tree, packs what remains and replaces the old store.

The rewrite reads commits and trees only (`fast-export --no-data`), never blob contents, so a
damaged huge blob that is being dropped cannot stop it. Every agent edit is kept; only the
excluded paths leave the history. Nothing here deletes a working file.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

log = logging.getLogger("helene.git_hygiene")

#: Paths that never belong in the agent's own history.
EXCLUDED = ("retained-assets", "backups", ".python-desktop-status", ".desktop-owner.lock")
#: Rewrite only stores at least this large: a small repository is not worth a rewrite.
SHRINK_THRESHOLD = 256 * 1024 * 1024
#: Leftover temporary packs older than this are garbage (no live repack at start).
STALE_PACK_SECONDS = 10 * 60


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(git_dir: Path, *args: str, work: Path | None = None, timeout: int = 1800,
         stdin: bytes | None = None, text: bool = True, env: dict | None = None):
    argv = ["git", "--git-dir", str(git_dir)] + (["--work-tree", str(work)] if work else []) + list(args)
    return subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout, env=env,
                          creationflags=_flags(),
                          **({"text": True, "encoding": "utf-8", "errors": "replace"} if text else {}))


def _check(done, what: str):
    if done.returncode:
        err = done.stderr if isinstance(done.stderr, str) else done.stderr.decode("utf-8", "replace")
        raise OSError(f"git {what}: {err.strip()[:300]}")
    return done


def _excluded(path: str) -> bool:
    return any(path == item or path.startswith(item + "/") for item in EXCLUDED)


def store_size(git_dir: Path) -> int:
    total = 0
    for path in (git_dir / "objects").rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def quiet_maintenance(git_dir: Path) -> None:
    """No gc/maintenance inside the agent's commits (they ran in the foreground, unfinished)."""
    for key, value in (("gc.auto", "0"), ("maintenance.auto", "false")):
        done = _run(git_dir, "config", key, value, timeout=60)
        if done.returncode:
            log.warning("личный git: %s не выставлен: %s", key, done.stderr.strip()[:200])


def drop_garbage_packs(git_dir: Path, now: float | None = None) -> int:
    """Delete temporary packs of interrupted repacks. -> bytes freed."""
    now = time.time() if now is None else now
    freed = 0
    for path in (git_dir / "objects" / "pack").glob("*"):
        name = path.name
        if not (name.startswith("tmp_pack_") or name.startswith("tmp_idx_") or name.startswith(".tmp-")):
            continue
        try:
            info = path.stat()
            if now - info.st_mtime < STALE_PACK_SECONDS:
                continue
            os.chmod(path, stat.S_IWRITE)
            path.unlink()
            freed += info.st_size
        except OSError as exc:
            log.warning("личный git: %s не удалён: %s", name, exc)
    if freed:
        log.info("личный git: удалены незаконченные упаковки, %d МБ", freed // (1024 * 1024))
    return freed


def drop_from_head(tree: Path, git_dir: Path) -> str:
    """Commit only the removal of the excluded paths from HEAD, through a temporary index.

    Untracking in the real index is not enough: the safety snapshot starts from HEAD, and an
    auto-commit of a staged deletion with `commit -- path` re-adds a file that still exists.
    The real index and the working files are untouched. -> new sha or "".
    """
    index = git_dir / f"helene-untrack-index-{os.getpid()}"
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    try:
        listed = _run(git_dir, "ls-tree", "-r", "--name-only", "HEAD", "--", *EXCLUDED, timeout=120)
        if listed.returncode or not listed.stdout.strip():
            return ""
        head = _check(_run(git_dir, "rev-parse", "HEAD", timeout=60), "rev-parse").stdout.strip()
        _check(_run(git_dir, "read-tree", "HEAD", env=env, timeout=300), "read-tree")
        _check(_run(git_dir, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", *EXCLUDED,
                    work=tree, env=env, timeout=300), "rm")
        written = _check(_run(git_dir, "write-tree", env=env, timeout=300), "write-tree").stdout.strip()
        sha = _check(_run(git_dir, "commit-tree", written, "-p", head, "-m",
                          "служебное: бэкапы и служебные файлы сняты с учёта", timeout=120),
                     "commit-tree").stdout.strip()
        _check(_run(git_dir, "update-ref", "-m", "helene: untrack backups", "HEAD", sha, head, timeout=60),
               "update-ref")
        # The real index follows HEAD for these paths, so they read as ignored, not deleted.
        _run(git_dir, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", *EXCLUDED, work=tree, timeout=300)
        log.info("личный git: служебные пути сняты и с HEAD (%s)", sha[:8])
        return sha
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("личный git: снять бэкапы с HEAD не удалось: %s", exc)
        return ""
    finally:
        index.unlink(missing_ok=True)


def _unquote(raw: bytes) -> str:
    """A path as fast-export writes it: plain, or C-quoted with octal escapes."""
    if not raw.startswith(b'"'):
        return raw.decode("utf-8", "surrogateescape")
    body, out, k = raw[1:-1], bytearray(), 0
    simple = {b"n": 10, b"t": 9, b'"': 34, b"\\": 92, b"a": 7, b"b": 8, b"f": 12, b"r": 13, b"v": 11}
    while k < len(body):
        ch = body[k:k + 1]
        if ch != b"\\":
            out += ch
            k += 1
            continue
        nxt = body[k + 1:k + 2]
        if nxt in simple:
            out.append(simple[nxt])
            k += 2
        else:
            out.append(int(body[k + 1:k + 4], 8))
            k += 4
    return bytes(out).decode("utf-8", "surrogateescape")


def filter_stream(stream: bytes) -> tuple[bytes, int]:
    """Drop file commands for excluded paths from a `fast-export --no-data` stream.

    Only `M`, `D`, `C` and `R` lines inside commits name paths; data blocks carry commit
    messages and are copied byte for byte by their declared length. -> (stream, dropped).
    """
    out, k, dropped = bytearray(), 0, 0
    while k < len(stream):
        end = stream.find(b"\n", k)
        end = len(stream) if end < 0 else end
        line = stream[k:end]
        k = end + 1
        if line.startswith(b"data "):
            size = int(line[5:])
            out += line + b"\n" + stream[k:k + size]
            k += size
            continue
        path = None
        if line.startswith(b"M "):
            parts = line.split(b" ", 3)
            path = parts[3] if len(parts) == 4 else None
        elif line.startswith(b"D "):
            path = line[2:]
        elif line.startswith((b"C ", b"R ")):
            path = line[2:]  # either side excluded drops the line (fast-export emits none by default)
        if path is not None and (_excluded(_unquote(path)) or
                                 any(_excluded(_unquote(p)) for p in path.split(b" ") if p)):
            dropped += 1
            continue
        out += line + b"\n"
    return bytes(out), dropped


def _tree_listing(git_dir: Path, rev: str) -> list[bytes]:
    done = _check(_run(git_dir, "ls-tree", "-r", "-z", "--full-tree", rev, text=False, timeout=600), "ls-tree")
    rows = []
    for row in done.stdout.split(b"\0"):
        if not row:
            continue
        name = row.split(b"\t", 1)[1].decode("utf-8", "surrogateescape")
        if not _excluded(name):
            rows.append(row)
    return rows


def _refs(git_dir: Path) -> dict[str, str]:
    done = _check(_run(git_dir, "for-each-ref", "--format=%(objectname) %(refname)", timeout=120), "for-each-ref")
    return {line.split(" ", 1)[1]: line.split(" ", 1)[0] for line in done.stdout.splitlines() if line.strip()}


def _remove_tree(path: Path) -> None:
    def onexc(function, target, _error):
        os.chmod(target, stat.S_IWRITE)
        function(target)
    shutil.rmtree(path, onexc=onexc)


def shrink_history(tree: Path, git_dir: Path, threshold: int | None = None) -> dict:
    """Rewrite the history without the excluded paths when the store is large. -> receipt."""
    threshold = SHRINK_THRESHOLD if threshold is None else threshold
    receipt: dict = {"before_bytes": store_size(git_dir)}
    if receipt["before_bytes"] < threshold:
        return dict(receipt, skipped="small")
    touched = _run(git_dir, "rev-list", "-n", "1", "--all", "--", *EXCLUDED, timeout=600)
    if touched.returncode or not touched.stdout.strip():
        return dict(receipt, skipped="history clean")
    new = tree / ".git-helene-shrink"
    if new.exists():
        _remove_tree(new)
    started = time.monotonic()
    try:
        _check(subprocess.run(["git", "init", "--bare", "-q", str(new)], capture_output=True, text=True,
                              creationflags=_flags(), timeout=120), "init")
        (new / "objects" / "info" / "alternates").write_bytes(
            str((git_dir / "objects").resolve()).replace("\\", "/").encode("utf-8") + b"\n")
        exported = _check(_run(git_dir, "fast-export", "--all", "--no-data", "--signed-tags=strip",
                               "--tag-of-filtered-object=drop", "--reencode=no", "--use-done-feature",
                               text=False, timeout=3600), "fast-export").stdout
        stream, dropped = filter_stream(exported)
        _check(_run(new, "fast-import", "--quiet", "--force", stdin=stream, text=False, timeout=3600), "fast-import")
        before, after = _refs(git_dir), _refs(new)
        if set(before) != set(after):
            raise OSError("rewritten refs differ: %s" % sorted(set(before) ^ set(after))[:5])
        for ref in before:
            if _tree_listing(git_dir, before[ref]) != _tree_listing(new, after[ref]):
                raise OSError(f"rewritten tree differs for {ref}")
        _check(_run(new, "repack", "-a", "-d", "-f", "-q", timeout=3600), "repack")
        (new / "objects" / "info" / "alternates").unlink()
        _check(_run(new, "fsck", "--full", "--no-dangling", timeout=3600), "fsck")
        head = _run(git_dir, "symbolic-ref", "HEAD", timeout=60).stdout.strip() or "refs/heads/main"
        _check(_run(new, "symbolic-ref", "HEAD", head, timeout=60), "symbolic-ref")
        for name in ("config", "description"):
            if (git_dir / name).is_file():
                shutil.copy2(git_dir / name, new / name)
        if (git_dir / "info" / "exclude").is_file():
            (new / "info").mkdir(exist_ok=True)
            shutil.copy2(git_dir / "info" / "exclude", new / "info" / "exclude")
        _check(_run(new, "config", "core.bare", "false", timeout=60), "config")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        log.warning("личный git: история не переписана, прежняя оставлена: %s", exc)
        try:
            _remove_tree(new)
        except OSError:
            pass
        return dict(receipt, failed=str(exc)[:300])
    old = tree / f".git-helene-before-shrink-{int(time.time())}"
    try:
        git_dir.rename(old)
    except OSError as exc:
        _remove_tree(new)
        log.warning("личный git: прежнее хранилище занято, переписывание отложено: %s", exc)
        return dict(receipt, failed="store busy")
    new.rename(git_dir)
    _run(git_dir, "read-tree", "HEAD", work=tree, timeout=600)
    _run(git_dir, "update-index", "-q", "--refresh", work=tree, timeout=600)
    _remove_tree(old)
    receipt.update(after_bytes=store_size(git_dir), dropped_paths=dropped, refs=len(after),
                   seconds=round(time.monotonic() - started, 1))
    log.info("личный git: история переписана без бэкапов и служебных файлов: %d МБ -> %d МБ за %s с",
             receipt["before_bytes"] // (1024 * 1024), receipt["after_bytes"] // (1024 * 1024), receipt["seconds"])
    return receipt


def heal(tree: Path) -> dict:
    """All of the above for an existing home, in order. Never raises."""
    git_dir = tree / ".git"
    result: dict = {}
    if not git_dir.is_dir():
        return result
    try:
        quiet_maintenance(git_dir)
        result["garbage_bytes"] = drop_garbage_packs(git_dir)
        result["head"] = drop_from_head(tree, git_dir)
        result["shrink"] = shrink_history(tree, git_dir)
    except Exception as exc:  # hygiene must never stop the home from opening
        log.warning("личный git: уборка не завершена: %s", exc)
        result["error"] = str(exc)[:300]
    return result
