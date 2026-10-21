"""Retry explicit Telegram rate refusals; never replay an accepted chunk."""
import threading
import time

class DeliveryCancelled(RuntimeError):
    pass

class Cooldown:
    def __init__(self, *, guard=lambda: None, notify=lambda *args: None,
                 wait=time.sleep, clock=time.monotonic):
        self.guard, self.notify, self.wait, self.clock = guard, notify, wait, clock
        self.deadlines = {}
        self.peers = {}
        self.lock = threading.RLock()

    def snapshot(self, peer):
        with self.lock:
            deadline = self.deadlines.get(peer)
            remaining = max(0.0, deadline - self.clock()) if deadline is not None else None
        return {"peer": str(peer), "state": "waiting" if remaining else "not_waiting" if remaining == 0 else "unknown",
                "remaining_seconds": remaining, "margin_seconds": 2,
                "source": "observed_telegram_rate_refusal" if deadline is not None else "no_observed_deadline"}

    def call(self, peer, method, operation):
        with self.lock:
            lock = self.peers.setdefault(peer, threading.RLock())
        with lock:
            return self._call(peer, method, operation)

    def _call(self, peer, method, operation):
        retries = 0
        def guard():
            try: self.guard()
            except Exception as error:
                self.notify(peer, method, -2 if isinstance(error, DeliveryCancelled) else -1, retries)
                raise
        while True:
            guard()
            with self.lock:
                delay = max(0.0, self.deadlines.get(peer, 0.0) - self.clock())
            while delay > 0:
                guard()
                try: self.wait(min(delay, 1.0))
                except Exception as error:
                    self.notify(peer, method, -2 if isinstance(error, DeliveryCancelled) else -1, retries)
                    raise
                with self.lock:
                    delay = max(0.0, self.deadlines.get(peer, 0.0) - self.clock())
            guard()
            try:
                result = operation()
            except Exception as error:
                if getattr(error, 'code', None) != 429:
                    self.notify(peer, method, -1, retries)
                    raise
                retries += 1
                explicit = float(getattr(error, 'retry_after', 0) or 0)
                delay = max(.01, explicit if explicit > 0 else min(60, 2 ** min(retries, 5))) + (2 if explicit > 0 else .5)
                with self.lock:
                    self.deadlines[peer] = max(self.deadlines.get(peer, 0), self.clock() + delay)
                self.notify(peer, method, delay, retries)
                continue
            self.notify(peer, method, 0, retries)
            return result, retries
