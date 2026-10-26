"""Detached verification-matrix runner for Praxis Forge.

The parent process writes a request, this supervisor runs independent checks with bounded
parallelism, and every check keeps a complete log.  The matrix therefore survives a Telegram
turn or a restart and can be inspected without trusting a summary.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import process_liveness
from forge_process import _terminate_tree


# Общий срок матрицы: он применяется к КАЖДОЙ проверке, которая пришла без своего.
# ⚠ 27.07, ревью. Здесь стоял отдельный `PRAXIS_VERIFY_HOST_DEADLINE_SEC` «на случай
# проверки совсем без предела» — и оказался рычагом, которого у неё нет: forge.verify
# всегда кладёт в запрос число, а `request.get("timeout") or 900` ниже добивает и ноль.
# Ветка не достигалась из её рук ни разу; зато настоящий предел — вот этот литерал 900 —
# именем не назывался вообще и молча переписывал её `coding_verify(timeout=0)`.
# Поэтому лишний рычаг убран, а действующий назван и вынесен в окружение (закон 2).
try:
    MATRIX_DEADLINE_SEC = int(float(os.getenv("PRAXIS_VERIFY_TIMEOUT_SEC") or 900))
except (TypeError, ValueError):
    MATRIX_DEADLINE_SEC = 900
if MATRIX_DEADLINE_SEC <= 0:
    MATRIX_DEADLINE_SEC = 900

# Совместимость на один шаг: имя ещё читает test_serverd_timeout.py (файл соседнего
# владельца). Убрать вместе с той ссылкой — своей роли у него больше нет.
DETACHED_HOST_DEADLINE_SEC = MATRIX_DEADLINE_SEC

# SIGTERM is a request to stop each separately supervised check, not a reason
# to exit while private check groups are still running. A parent can also write
# stop-request.json to avoid a signal race before check processes are registered.
_STOP = threading.Event()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _safe_id(value: str, fallback: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in str(value or ""))
    return clean.strip("-.")[:80] or fallback


def _stop_requested(directory: Path) -> bool:
    return _STOP.is_set() or (directory / "stop-request.json").exists()


def _wait_local(proc: subprocess.Popen, timeout: int, directory: Path) -> tuple[int, str, str]:
    """Poll a local command so SIGTERM and the durable stop request can be obeyed.

    Returning unknown deliberately leaves a stop.json receipt to block finish.
    """
    deadline = time.monotonic() + timeout if timeout else None
    while True:
        if _stop_requested(directory):
            stopped, reason = _terminate_tree(proc)
            return -1, "stopped" if stopped else "unknown", reason
        wait_for = .1 if deadline is None else min(.1, max(0, deadline - time.monotonic()))
        try:
            code = proc.wait(timeout=wait_for)
            return code, "passed" if code == 0 else "failed", ""
        except subprocess.TimeoutExpired:
            if deadline is None or time.monotonic() < deadline:
                continue
            stopped, reason = _terminate_tree(proc)
            return -1, "timed_out" if stopped else "unknown", reason


def _run_one(index: int, check: dict, root: Path, directory: Path, default_timeout: int,
             state: dict, lock: threading.Lock, scope: str = "self", matrix_id: str = "",
             observation: dict | None = None) -> dict:
    cid = _safe_id(check.get("id", ""), f"check-{index + 1}")
    command = str(check.get("command") or "").strip()
    if _stop_requested(directory):
        result = {"id": cid, "status": "stopped", "exit": -1, "duration_s": 0,
                  "command": command, "custody": {"spawned": False,
                                                   "group_exit_verified": True}}
        with lock:
            state["checks"][cid] = result
            _write(directory / "state.json", state)
        return result
    # 06.10, дедуп (PR oro/forge-contract-0610): forge.verify уже пометил команду зелёной
    # НА ЭТОМ ЖЕ ДЕРЕВЕ — запускать её повторно значит платить за то же доказательство.
    # Пишем расписку со ссылкой на прежний юнит, процесс не спавним.
    if check.get("skip"):
        skip_status = str(check.get("skip_status") or "skipped_already_green")
        result = {"id": cid, "status": skip_status, "exit": 0, "duration_s": 0,
                  "command": command, "kind": check.get("kind", "check"),
                  "source": check.get("source", ""), "scope": check.get("scope", ""),
                  "dedup": True,
                  "skip_reference": str(check.get("skip_reference") or ""),
                  "tree_sha": str((observation or {}).get("tree_sha") or "")}
        with lock:
            state["checks"][cid] = result
            _write(directory / "state.json", state)
        return result
    rel_cwd = str(check.get("cwd") or ".")
    cwd = root if scope == "windows" else (root / rel_cwd).resolve()
    if scope not in {"host", "windows"}:
        try:
            cwd.relative_to(root)
        except ValueError:
            return {"id": cid, "status": "error", "exit": -1, "duration_s": 0,
                    "error": f"cwd leaves task root: {rel_cwd}"}
    own_timeout = max(0, int(check.get("timeout") or 0))
    timeout = own_timeout or max(0, int(default_timeout or 0))
    log_path = directory / "logs" / f"{index + 1:02d}-{cid}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline_s = timeout
    with lock:
        state["checks"][cid] = {"status": "running", "command": command, "started": _now(),
                                "log": str(log_path)}
        _write(directory / "state.json", state)
    unresolved_note = ""
    custody: dict = {}
    try:
        if scope == "host":
            import serverd_client
            deadline_s = timeout or MATRIX_DEADLINE_SEC
            wait_s = max(60, deadline_s + 60)
            result = serverd_client.call(
                "op.run", {"root_value": str(root), "command": command, "cwd": rel_cwd,
                           "timeout": deadline_s}, timeout=wait_s,
                request_id=f"verify-{matrix_id}-{cid}",
            )
            body = str(result.get("body") or result.get("error") or "")
            if not own_timeout:
                # Свой срок проверки не назначала — значит подставлен общий, и он обязан
                # быть виден в её же логе, а не только в коде (закон 2).
                body += (f"\n[срок] у этой проверки своего предела не было — применён общий "
                         f"срок матрицы {deadline_s}с (PRAXIS_VERIFY_TIMEOUT_SEC); демон "
                         f"остановит команду сам, клиент ждёт {wait_s}с.")
            log_path.write_text(body, encoding="utf-8", errors="replace")
            code = int(result.get("exit") if result.get("exit") is not None else -1)
            status = "passed" if result.get("ok") and code == 0 else (
                "timed_out" if result.get("status") == "timed_out" else "failed"
            )
            remote_log = str(result.get("log") or "")
        elif scope == "windows":
            import body_client
            task = dict(observation or {})
            task.setdefault("root", str(root))
            with body_client.observation_context(task):
                result = body_client.run_result(str(root), command, cwd=rel_cwd, timeout=timeout)
            body = str(result.get("body") or result.get("error") or "")
            log_path.write_text(body, encoding="utf-8", errors="replace")
            terminal = result.get("result") or {}
            code = int(terminal.get("exit_code") if terminal.get("exit_code") is not None else -1)
            status = "passed" if result.get("ok") and code == 0 else (
                "timed_out" if result.get("status") == "timed_out" else "failed"
            )
            remote_log = str(terminal.get("stdout_log") or "")
        else:
            remote_log = ""
            with log_path.open("w", encoding="utf-8", errors="replace") as log:
                kwargs = {"shell": True, "cwd": str(cwd), "stdout": log,
                          "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "text": True}
                if os.name == "nt":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                else:
                    # Each check owns a group: never kill sibling checks with the supervisor.
                    kwargs["start_new_session"] = True
                proc = subprocess.Popen(command, **kwargs)
                custody = {"spawned": True, "child_pid": proc.pid,
                           "child_started_at": process_liveness.process_started_at(proc.pid),
                           "group_exit_verified": False}
                with lock:
                    state["checks"][cid].update(custody)
                    _write(directory / "state.json", state)
                code, status, reason = _wait_local(proc, timeout, directory)
                if status in {"timed_out", "stopped", "unknown"}:
                    custody.update(group_exit_verified=(status != "unknown"), note=reason)
                if status == "unknown":
                    unresolved_note = f"check {cid} stop/timeout unconfirmed; {reason}"
        result = {"id": cid, "status": status, "exit": code,
                  "duration_s": round(time.monotonic() - started, 3), "command": command,
                  "kind": check.get("kind", "check"), "source": check.get("source", ""),
                  "scope": check.get("scope", ""), "log": str(log_path),
                  # Срок, под которым проверка реально шла: «0» = предела не было. Без этого
                  # `timed_out` в отчёте не отличить от «упало само».
                  "deadline_s": deadline_s,
                  "remote_log": remote_log}
        if unresolved_note:
            result["liveness"] = unresolved_note
        if custody:
            result["custody"] = custody
    except Exception as exc:  # noqa: BLE001
        result = {"id": cid, "status": "error", "exit": -1,
                  "duration_s": round(time.monotonic() - started, 3), "command": command,
                  "error": f"{type(exc).__name__}: {exc}", "log": str(log_path)}
        if custody.get("spawned"):
            try:
                stopped, note = _terminate_tree(proc)
            except Exception as stop_exc:  # do not turn an unproven kill into error/terminal
                stopped, note = False, f"{type(stop_exc).__name__}: {stop_exc}"
            custody.update(group_exit_verified=stopped, note=note)
            result["custody"] = custody
            if not stopped:
                result.update(status="unknown", liveness=(
                    f"check {cid} supervisor error after spawn; group stop unconfirmed: {note}"))
    with lock:
        state["checks"][cid] = result
        _write(directory / "state.json", state)
    return result


def run(request_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    directory = request_path.parent
    _STOP.clear()
    scope = str(request.get("scope") or "self")
    root = Path(request["root"]) if scope == "windows" else Path(request["root"]).resolve()
    checks = [row for row in (request.get("checks") or []) if str(row.get("command") or "").strip()]
    parallel = max(1, min(int(request.get("max_parallel") or 4), 8))
    timeout = max(0, int(request.get("timeout") or MATRIX_DEADLINE_SEC))
    # Номер супервизора без метки рождения — это приглашение убить постороннего:
    # forge.verify(action="stop") шлёт killpg по нему, а номера в контейнере
    # переиспользуются первым же спавном.
    state = {"status": "running", "started": _now(), "supervisor_pid": os.getpid(),
             "supervisor_started_at": process_liveness.process_started_at(os.getpid()),
             "matrix_deadline_s": timeout,
             "deadline_note": (f"проверке без своего срока даётся {timeout}с "
                               f"(PRAXIS_VERIFY_TIMEOUT_SEC)"),
             "checks": {}}
    _write(directory / "state.json", state)
    lock = threading.Lock()
    started = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = [pool.submit(_run_one, i, row, root, directory, timeout, state, lock,
                                   scope, str(request.get("id") or directory.name), request)
                       for i, row in enumerate(checks)]
            results = [future.result() for future in futures]
        failed = [row for row in results if row.get("status") in {"failed", "error"}]
        unknown = [row for row in results if row.get("status") == "unknown"]
        skipped = [row for row in results if row.get("status") == "timed_out"]
        passed = [row for row in results if row.get("status") == "passed"]
        # 06.10, дедуп: зелёное-на-том-же-дереве из прошлой матрицы — это зелёное,
        # просто доказанное раньше. Считается в passed, а не в skips: иначе матрица с
        # полностью дедупнутыми проверками выглядела бы «с пропусками» при нуле гонявших.
        deduped = [row for row in results if row.get("status") == "skipped_already_green"]
        final_status = "failed" if failed else ("passed_with_skips" if skipped else "passed")
        if unknown:
            # _unit_state checks unresolved stop before result. A timed-out
            # check without proven tree exit cannot become terminal matrix evidence.
            note = "; ".join(str(row.get("liveness") or row.get("id")) for row in unknown)
            _write(directory / "stop.json", {"state": "unresolved", "note": note, "at": _now()})
            with lock:
                state["status"] = "unknown"
                state["liveness"] = note
                _write(directory / "state.json", state)
            return 2
        if _stop_requested(directory):
            final_status = "stopped"
        # The request's HEAD tree is a reuse key only if the same clean worktree
        # still exists after all checks. A check (or another worker) may edit
        # source while it runs. Do not publish green reusable evidence for a
        # candidate that moved during the matrix.
        tree_sha = str(request.get("tree_sha") or "")
        if tree_sha and scope == "self" and final_status != "stopped":
            import forge

            if forge._task_tree_sha(root) != tree_sha:
                final_status = "stale"
        final = {"status": final_status, "finished": _now(),
                 "duration_s": round(time.monotonic() - started, 3), "checks": results,
                 "passed": len(passed) + len(deduped), "skipped": len(skipped),
                 "failed": len(failed),
                 "tree_sha": tree_sha if final_status != "stale" else "",
                 "tree_changed_during_checks": final_status == "stale",
                 # Срок виден в самом отчёте, а не только в логах отдельных проверок:
                 # `timed_out` без названного предела читается как «упало само».
                 "matrix_deadline_s": timeout,
                 "supervisor_pid": os.getpid(),
                 "supervisor_started_at": state.get("supervisor_started_at", "")}
        _write(directory / "result.json", final)
        if final_status == "stopped":
            # A parent can have written stop.json unresolved while its bounded
            # wait elapsed. This producer owns the final readback: only after
            # every check has returned without unknown custody may it heal it.
            _write(directory / "stop.json", {"state": "settled", "at": _now(),
                                             "note": "all checks settled after stop request",
                                             "result_status": "stopped"})
        return 0 if final_status in {"passed", "passed_with_skips"} else 1
    except BaseException as exc:  # noqa: BLE001
        live = [cid for cid, row in state["checks"].items()
                if row.get("status") in {"starting", "running", "unknown"}]
        if live:
            note = f"supervisor aborted with {type(exc).__name__}: {exc}; check custody unknown: {live}"
            _write(directory / "stop.json", {"state": "unresolved", "note": note, "at": _now()})
            state["status"] = "unknown"
            state["liveness"] = note
            _write(directory / "state.json", state)
            return 2
        final = {"status": "error", "finished": _now(),
                 "duration_s": round(time.monotonic() - started, 3),
                 "error": f"{type(exc).__name__}: {exc}"}
        _write(directory / "result.json", final)
        return 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: _STOP.set())
    raise SystemExit(run(Path(args.request)))
