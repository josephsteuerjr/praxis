"""Pure child-exit policy shared by the Python supervisor's process owner.

No process handle, file, clock read or network operation belongs here. The caller
observes exit and applies this decision while retaining exclusive custody of the
child. Desktop ownership may reuse the policy only after its OS containment and
single-spawner handoff are independently proved.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitDecision:
    falls: tuple[float, ...]
    retry_at: float
    halted: str
    note: str


def decide_exit(*, key: str, name: str, code: int | None, now: float,
                falls: tuple[float, ...], retry_at: float, halted: str,
                restart_code: int) -> ExitDecision:
    """Project the existing serverboot retry/refusal rules without effects.

    Caller-supplied time is monotonic. A normal runner self-restart resets the
    crash history; runner configuration/layout errors wait for correction.
    Other exits (including a relay's restart code) use bounded backoff.
    """
    if key == "runner" and code == restart_code:
        return ExitDecision((), 0.0, halted,
                            f"раннер попросил перезапуска (код {code}) — поднимаю")
    if key == "runner" and code in (2, 3):
        reason = ("конфиг или раскладка папки данных" if code == 3
                  else "нет папки с кодом агента (tree/)")
        return ExitDecision(falls, retry_at, reason,
                            f"раннер вышел с кодом {code}: {reason} — перезапуск не поможет, "
                            "правь и перезапусти контейнер")
    recent = tuple(t for t in falls if now - t < 600) + (now,)
    pause = min(60.0, 2.0 ** min(len(recent), 6))
    return ExitDecision(recent, now + pause, halted,
                        f"{name} завершился (код {code}) — снова через {pause:.0f} с")
