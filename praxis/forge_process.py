"""Out-of-line supervisor for a long-running Praxis Forge command."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import process_liveness


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _group_members(pgid: int) -> bool | None:
    """Live members of OUR private POSIX group; None means census unavailable.

    killpg(pgid, 0) also sees zombies, so cannot prove group exit on Linux.
    macOS has no /proc; ps is a bounded read-only census, not a signal.
    """
    if os.path.isdir("/proc"):
        try:
            for entry in os.scandir("/proc"):
                if not entry.name.isdigit():
                    continue
                try:
                    raw = (Path(entry.path) / "stat").read_text(encoding="utf-8", errors="replace")
                    fields = raw.rsplit(")", 1)[1].split()
                    if int(fields[2]) == pgid and fields[0] not in {"Z", "X"}:
                        return True
                except FileNotFoundError:
                    continue  # process exited during the census
            return False
        except (OSError, ValueError, IndexError):
            return None
    try:
        rows = subprocess.run(["ps", "-A", "-o", "pgid=", "-o", "stat="],
                              capture_output=True, text=True, timeout=3)
        if rows.returncode:
            return None
        return any(int(parts[0]) == pgid and not parts[1].startswith(("Z", "X"))
                   for row in rows.stdout.splitlines() if len(parts := row.split()) >= 2)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _wait_group_gone(pgid: int, seconds: float) -> bool | None:
    end = time.monotonic() + seconds
    while True:
        live = _group_members(pgid)
        if live is not True or time.monotonic() >= end:
            return live
        time.sleep(.05)


def _terminate_tree(proc: subprocess.Popen) -> tuple[bool, str]:
    """Stop the private command tree; return proof or an explicit custody gap.

    A shell can exit on TERM while its children ignore it. Waiting on Popen alone
    does not prove their death. Never fall back to killing only the leader and then
    report a terminal timeout. Windows taskkill's failure likewise is not proof.
    """
    if os.name == "nt":
        # A Popen handle refers to the original process, unlike its recyclable
        # PID. If that shell has already exited, taskkill could hit a stranger.
        if proc.poll() is not None:
            return False, "command shell exited before taskkill; descendants unproven"
        try:
            killed = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=15)
            if killed.returncode:
                return False, f"taskkill /T /F failed ({killed.returncode}): {killed.stderr[-300:]}"
            proc.wait(timeout=3)
            return True, "taskkill /T /F succeeded and command exited"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"taskkill or command-exit confirmation failed: {exc}"
    pgid = proc.pid  # caller MUST start_new_session=True, never the supervisor group
    try:
        live = _group_members(pgid)
        if live is None:
            return False, "private process-group census unavailable before signal"
        if not live:
            return True, "private process group already empty"
        if proc.poll() is not None:
            # We know the group's original PGID only while the shell or its
            # descendants hold it. After leader exit a census alone cannot
            # distinguish a surviving descendant from a recycled group ID.
            return False, "shell exited before first group signal; group identity unproven"
        # The group is still occupied by our command or its descendants; its PGID
        # cannot have been recycled while a member holds it.
        os.killpg(pgid, signal.SIGTERM)
        live = _wait_group_gone(pgid, 1)
        if live is None:
            return False, "private process-group census unavailable after TERM"
        if live:
            os.killpg(pgid, signal.SIGKILL)
            live = _wait_group_gone(pgid, 2)
        if live is False:
            return True, "private process group empty after stop"
        return False, "private process group still live or census unavailable after KILL"
    except (OSError, ValueError) as exc:
        return False, f"private process-group stop unconfirmed: {exc}"


def _unresolved(directory: Path, state: dict, note: str) -> None:
    _write(directory / "stop.json", {"state": "unresolved", "note": note, "at": _now()})
    _write(directory / "state.json", {**state, "status": "unknown", "liveness": note,
                                      "finished": _now()})


def run(request_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    directory = request_path.parent
    result_path = directory / "result.json"
    state_path = directory / "state.json"
    log_path = directory / "command.log"
    started = time.monotonic()
    state: dict = {}
    try:
        with log_path.open("a", encoding="utf-8") as log:
            kwargs = {"shell": True, "cwd": str(request["cwd"]), "stdout": log,
                      "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "text": True}
            if os.name == "nt":
                kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                           | getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(str(request["command"]), **kwargs)
            # Preserve the birth mark captured while this child is still ours:
            # later a recycled PID is not a safe target for an external stop.
            state = {"status": "running", "child_pid": proc.pid,
                     "child_started_at": process_liveness.process_started_at(proc.pid),
                     "supervisor_pid": os.getpid(),
                     "supervisor_started_at": process_liveness.process_started_at(os.getpid()),
                     "started": _now()}
            _write(state_path, state)
            timeout = int(request.get("timeout") or 0)
            try:
                code = proc.wait(timeout=(timeout or None))
                status = "done" if code == 0 else "failed"
            except subprocess.TimeoutExpired:
                stopped, note = _terminate_tree(proc)
                if not stopped:
                    _unresolved(directory, state, f"command timed out; {note}")
                    return 2
                code, status = -1, "timed_out"
            # Keep the PID and mark as forensic evidence, but do not leave a
            # finished command looking live in state.json.
            _write(state_path, {**state, "status": status, "child_running": False,
                                "finished": _now()})
        _write(result_path, {"status": status, "code": code, "finished": _now(),
                            "duration_s": round(time.monotonic() - started, 3),
                            "log": str(log_path)})
        return 0 if status == "done" else 1
    except Exception as exc:
        if state:
            _unresolved(directory, state, f"supervisor exception after spawn: {type(exc).__name__}: {exc}")
        else:
            _write(result_path, {"status": "error", "error": f"{type(exc).__name__}: {exc}",
                                "finished": _now(), "duration_s": round(time.monotonic() - started, 3)})
        return 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    raise SystemExit(run(Path(args.request)))
