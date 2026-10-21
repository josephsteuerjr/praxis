# -*- coding: utf-8 -*-
"""Private, correlated pipe to Hélène's existing native control organ.

Python owns the application lifetime; the host retains OS integration and its
outer crash fuse. --python-supervisor selects the existing Python process-tree
owner instead of starting a competing native retry loop.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from concurrent.futures import Future
from pathlib import Path


class HostClient:
    def __init__(self, executable: Path, root: Path, *, agent: str = "",
                 python_supervisor: bool = True, on_event=None, window_origin: str = ""):
        self.ready = Future()
        self._pending: dict[int, Future] = {}
        self._lock = threading.RLock()
        self._seq = 0
        self._closed = False
        self.on_event = on_event or (lambda name, data: None)
        argv = [str(executable), "--root", str(root), "--python-app"]
        if agent:
            argv += ["--agent", agent]
        if python_supervisor:
            argv += ["--python-supervisor"]
        self._log = open(root / "python-window-host.log", "ab", buffering=0)
        env = dict(os.environ)
        if window_origin:
            env["HELENE_ORIGINS"] = window_origin
        try:
            self.child = subprocess.Popen(argv, cwd=root, stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=self._log,
                                          env=env,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except BaseException:
            self._log.close()
            raise
        self._reader = threading.Thread(target=self._read, name="helene-host-pipe", daemon=True)
        self._reader.start()

    def _read(self):
        try:
            for raw in self.child.stdout:
                message = json.loads(raw)
                if "event" in message:
                    if message["event"] == "ready" and not self.ready.done():
                        self.ready.set_result(message["data"])
                    self.on_event(message["event"], message.get("data"))
                    continue
                with self._lock:
                    future = self._pending.pop(message.get("id"), None)
                if future is not None:
                    if message.get("error") is not None:
                        future.set_exception(RuntimeError(message["error"]))
                    else:
                        future.set_result(message.get("result"))
        except (OSError, ValueError) as exc:
            self._fail(exc)
        finally:
            self._fail(RuntimeError("Движок оболочки завершился; окно не запускает его повторно."))

    def _fail(self, error):
        with self._lock:
            self._closed = True
            if not self.ready.done():
                self.ready.set_exception(error)
            pending, self._pending = self._pending, {}
        for future in pending.values():
            future.set_exception(error)

    def invoke(self, command: str, args: dict | None = None, timeout: float = 120):
        with self._lock:
            if self._closed:
                raise RuntimeError("Движок оболочки не работает")
            self._seq += 1
            identity = self._seq
            raw = (json.dumps({"id": identity, "command": command, "args": args or {}},
                              ensure_ascii=False) + "\n").encode("utf-8")
            bound = ((64 * 1024 * 1024 + 2) // 3 * 4 + 65536
                     if command == "local_files_save" else 16 * 1024 * 1024)
            if len(raw) > bound:
                raise ValueError("Слишком большой запрос к оболочке")
            future = Future()
            self._pending[identity] = future
            try:
                self.child.stdin.write(raw)
                self.child.stdin.flush()
            except OSError:
                self._pending.pop(identity, None)
                raise
        # A timeout is unknown, not permission to repeat a mutating command.
        return future.result(timeout=timeout)

    def close(self):
        try:
            if not self._closed:
                self.invoke("host_shutdown", timeout=5)
        finally:
            if self.child.stdin:
                self.child.stdin.close()  # EOF is the host's normal teardown path
            self.child.wait(timeout=30)
            self._reader.join(timeout=2)
            self.child.stdout.close()
            self._log.close()
