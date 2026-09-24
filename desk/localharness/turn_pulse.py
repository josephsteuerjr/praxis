# -*- coding: utf-8 -*-
"""Пульс хода в Telegram: видно, что агент думает (25.09, поток F).

Было: один `sendChatAction typing` на весь ход — Telegram гасит индикатор через ~5 с, и
дальше собеседник видел тишину: «Пракс молчит, потому что фиксила», а человек не знает,
повисла она или занята (Сергей, 24.09). У аккаунта (MTProto) индикатора не было вовсе.

Теперь на время хода живёт отдельный поток:
  * «печатает…» каждые ~4 с, пока ход не закончился;
  * по желанию владельца (`telegram.status_message`, выключено по умолчанию) — через
    ~20 с хода пост «думаю (ЧЧ:ММ)…», раз в минуту правка «уже N мин», но только пока
    пост — последний в комнате (человек написал следом — молчим, не перебиваем);
    перед первым ответом пост удаляется; ход упал — пост правится в «не вышло: …».

Транспорт даёт четыре глагола: `typing`, `post_status`, `edit_status`, `delete_status`
и крючок `before_send` — его пульс ставит, чтобы снять пост ровно перед ответом.
Пульс никогда не роняет ход: любая ошибка транспорта — в debug-лог и дальше.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time

log = logging.getLogger("helene.pulse")

TYPING_EVERY = 4.0      # Telegram гасит «печатает…» через ~5 с
STATUS_AFTER = 20.0     # пост «думаю» — только у долгого хода
STATUS_EVERY = 60.0     # правка поста — раз в минуту


def _clock_words(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime("%H:%M")


class TurnPulse:
    def __init__(self, bot, chat_id: str, *, typing: bool = True, status: bool = False,
                 typing_every: float = TYPING_EVERY, status_after: float = STATUS_AFTER,
                 status_every: float = STATUS_EVERY, clock=time.monotonic):
        self.bot = bot
        self.chat_id = str(chat_id)
        self.typing = bool(typing)
        self.status = bool(status)
        self.typing_every = float(typing_every)
        self.status_after = float(status_after)
        self.status_every = float(status_every)
        self.clock = clock
        self.status_id = None
        self.typing_sent = 0
        self.edits = 0
        self.started_at = dt.datetime.now()
        self._t0 = 0.0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="turn-pulse", daemon=True)
        # Связанный метод — один объект на весь пульс: `bot.before_send is hook` иначе
        # никогда не истинно (каждое обращение к self.retire_status — новый объект).
        self._hook = self.retire_status

    # ------------------------------------------------------------------ жизнь
    def start(self) -> "TurnPulse":
        self._t0 = self.clock()
        if self.status:
            # Перед первым словом наружу пост «думаю» обязан исчезнуть: иначе ответ
            # придёт под ним, и в комнате останется «думаю» над готовым ответом.
            self.bot.before_send = self._hook
        if self.typing or self.status:
            self._thread.start()
        return self

    def stop(self, *, failed: str = "") -> None:
        """Ход закончился: гасим индикатор; пост — снять или переписать в «не вышло»."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(2.0)
        with self._lock:
            status_id = self.status_id
            if status_id is not None:
                if failed:
                    self._safely(self.bot.edit_status, self.chat_id, status_id,
                                 f"не вышло: {failed}"[:200])
                else:
                    self._safely(self.bot.delete_status, self.chat_id, status_id)
                self.status_id = None
        if getattr(self.bot, "before_send", None) is self._hook:
            self.bot.before_send = None

    def retire_status(self) -> None:
        """Снять пост «думаю» — зовётся транспортом перед первой отправкой наружу."""
        with self._lock:
            status_id = self.status_id
            self.status_id = None
        if status_id is not None:
            self._safely(self.bot.delete_status, self.chat_id, status_id)

    # ------------------------------------------------------------------ поток
    def _run(self) -> None:
        next_typing = self._t0
        next_status = self._t0 + self.status_after
        while True:
            now = self.clock()
            if self.typing and now >= next_typing:
                self._safely(self.bot.typing, self.chat_id)
                self.typing_sent += 1
                next_typing = now + self.typing_every
            if self.status and now >= next_status:
                self._status_tick(now)
                next_status = now + self.status_every
            waits = [t - self.clock() for t in
                     ([next_typing] if self.typing else []) + ([next_status] if self.status else [])]
            if self._stop.wait(max(0.05, min(waits) if waits else 1.0)):
                return

    def _status_tick(self, now: float) -> None:
        with self._lock:
            if self.status_id is None:
                if self._stop.is_set():
                    return
                posted = self._safely(self.bot.post_status, self.chat_id,
                                      f"думаю ({_clock_words(self.started_at)})…")
                if posted:
                    self.status_id = posted
                return
            status_id = self.status_id
        if not self._safely(self.bot.is_last_message, self.chat_id, status_id):
            return  # человек написал следом — не перебиваем правкой
        minutes = max(1, int((now - self._t0) // 60))
        if self._safely(self.bot.edit_status, self.chat_id, status_id,
                        f"думаю ({_clock_words(self.started_at)}), уже {minutes} мин…"):
            self.edits += 1

    @staticmethod
    def _safely(fn, *args):
        try:
            return fn(*args)
        except Exception:
            log.debug("пульс хода: транспорт отказал (%s)", getattr(fn, "__name__", fn),
                      exc_info=True)
            return None
