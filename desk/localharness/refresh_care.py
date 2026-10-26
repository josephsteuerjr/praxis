"""Serialized compact-refresh care outside the turn and ingress threads.

Debt remains in canonical memory; this queue is only a best-effort trigger. Stop
rejects new requests and drops queued triggers, never claims to cancel a model
call already running. The daemon thread cannot hold process exit indefinitely.
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time

log = logging.getLogger(__name__)


class RefreshCare:
    def __init__(self, life, *, cooldown=600.0, clock=time.monotonic):
        self.life = life
        self.cooldown = float(cooldown)
        self.clock = clock
        self._condition = threading.Condition()
        self._pending = {}
        self._last = {}
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="edition-refresh", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def request(self, chat_id):
        if self.cooldown <= 0:
            return False
        place = str(self.life.place_key(chat_id))
        with self._condition:
            now = self.clock()
            if self._stopping or place in self._pending:
                return False
            if place in self._last and now - self._last[place] < self.cooldown:
                return False
            self._last[place] = now
            self._pending[place] = contextvars.copy_context()
            self._condition.notify()
            return True

    def stop(self, timeout=2.0):
        with self._condition:
            self._stopping = True
            self._pending.clear()
            self._condition.notify_all()
        if self._thread.ident is not None:
            self._thread.join(timeout)
        alive = self._thread.is_alive()
        if alive:
            log.warning("compact refresh still in flight at shutdown; canonical debt retained")
        return not alive

    def _pay(self, place):
        debt = self.life.refresh_debt(place)
        with self._condition:
            if self._stopping:
                return
        if int(debt.get("unresolved_group_count") or 0):
            result = self.life.refresh_compacts(place, max_chunks=1)
            log.info("compact refresh [%s]: %s", place, result)

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._stopping or self._pending)
                if self._stopping:
                    return
                place = next(iter(self._pending))
                context = self._pending.pop(place)
            try:
                context.run(self._pay, place)
            except Exception:
                log.exception("compact refresh failed [%s]; debt remains canonical", place)
