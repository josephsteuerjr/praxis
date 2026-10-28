# -*- coding: utf-8 -*-
"""Чей это мозг сейчас и кто его менял последним (1.2.5, слово Егора 29.09).

Живой случай, Джарвис на ПК Егора. Недельный лимит подписки кончился, Егор переключил
модель в Настройках на glm-5.3 — а агент не знал ни того, на чём работает (модели в кадре
не было: он полез рукой в снимок раннера), ни того, кто её сменил. Решил, что «конфиг
стёрло обновление», и дважды спросил, вернуть ли прежнюю: его же правило «когда неясно,
чья это воля, — не действую, а спрашиваю». Егор: «мои правки тебе не видны, провенанс
нарушен».

Мозг меняют четверо, и у каждого свой путь:
  * владелец — Настройки окна (или правка helene.json руками): движок видит новый блок
    модели и проецирует его в `memory/llm.json` (`boot.project_brain`);
  * установщик при обновлении — тоже через helene.json (слияние настроек);
  * закрепление владельца (`model.pinned`) на старте возвращает выбор владельца;
  * сам агент — рукой `switch_brain`.
Здесь каждая смена голоса ложится строкой в `memory/.state/brain-trail.jsonl` (кто, когда,
было → стало, зачем), а в кадр каждого хода идёт одна строка: на чём агент работает сейчас
и кто менял последним. Смена, которую не сделал никто из четверых (правка `llm.json`
руками или shell), так и называется — «никто из известных путей».
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("helene.brain_trail")

TRAIL = ("memory", ".state", "brain-trail.jsonl")
LLM = ("memory", "llm.json")
#: Смена старше — в кадре только «на чём сейчас», без «кто менял».
FRESH_DAYS = 7
#: Установщик пишет метку установки тем же махом, что и helene.json.
INSTALL_MARKER = "helene-install.json"
INSTALL_WINDOW = 180.0

BY_OWNER = "владелец в Настройках (helene.json)"
BY_AGENT = "ты сам (switch_brain)"
BY_PIN = "закрепление модели владельцем вернуло его выбор"
BY_UNKNOWN = "никто из известных путей — llm.json изменён мимо Настроек и switch_brain"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def snapshot(tree: Path) -> dict:
    """Голос сейчас: {model, framework, host} из `memory/llm.json` (ключи не читаются)."""
    try:
        data = json.loads(Path(tree).joinpath(*LLM).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    voice = (data.get("roles") or {}).get("voice") or {}
    framework = str(voice.get("framework") or "")
    base = str(((data.get("frameworks") or {}).get(framework) or {}).get("base_url") or "")
    return {"model": str(voice.get("model") or ""), "framework": framework,
            "host": urlparse(base).netloc or base}


def where(state: dict) -> str:
    """Откуда голос — словами: реле подписки, провайдер по ключу, адрес."""
    host = str(state.get("host") or "")
    if host.startswith(("127.0.0.1", "localhost")):
        return "подписка через реле на этом компьютере"
    if "z.ai" in host:
        return "z.ai"
    if "openai.com" in host:
        return "OpenAI по ключу"
    if "anthropic.com" in host:
        return "Anthropic по ключу"
    return host or "адрес не задан"


def _same(a: dict, b: dict) -> bool:
    return (a.get("model"), a.get("framework"), a.get("host")) == (b.get("model"), b.get("framework"), b.get("host"))


def last(tree: Path) -> dict | None:
    path = Path(tree).joinpath(*TRAIL)
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 16 * 1024))
            rows = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(rows):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            return row
    return None


def record(tree: Path, by: str, before: dict, after: dict, why: str = "") -> dict | None:
    """Строка следа, если голос и правда сменился. -> строка или None."""
    if not after or _same(before or {}, after):
        return None
    row = {"at": _now().isoformat(timespec="seconds"), "by": by,
           "from": {k: before.get(k, "") for k in ("model", "framework", "host")} if before else {},
           "to": {k: after.get(k, "") for k in ("model", "framework", "host")},
           "why": " ".join(str(why or "").split())[:300]}
    path = Path(tree).joinpath(*TRAIL)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        log.warning("след смены мозга не записался", exc_info=True)
    return row


def config_author(config_path: Path) -> str:
    """Кто последним писал helene.json: установщик (метка установки тем же махом) или владелец."""
    try:
        cfg_at = Path(config_path).stat().st_mtime
        marker_at = Path(config_path).with_name(INSTALL_MARKER).stat().st_mtime
    except OSError:
        return BY_OWNER
    if abs(cfg_at - marker_at) <= INSTALL_WINDOW:
        try:
            version = json.loads(Path(config_path).with_name(INSTALL_MARKER).read_text("utf-8-sig")).get("version")
        except (OSError, ValueError):
            version = ""
        return f"установщик при обновлении{(' до ' + str(version)) if version else ''} (слияние настроек)"
    return BY_OWNER


def projection_author(tree: Path, config_path: Path, cfg: dict) -> str:
    """Кто стоит за проекцией helene.json -> llm.json, которая сейчас будет. Зовётся ДО
    `boot.project_brain`: расписка проекции после неё уже новая."""
    receipt = Path(tree) / "memory" / ".state" / "brain_projection.json"
    try:
        changed = Path(config_path).stat().st_mtime > receipt.stat().st_mtime
    except OSError:
        changed = True
    if not changed and (cfg.get("model") or {}).get("pinned"):
        return BY_PIN
    return config_author(config_path)


def project(tree: Path, config_path: Path, cfg: dict, project_brain) -> str:
    """`boot.project_brain` со следом: кто сменил голос, если сменил. -> строка лога."""
    by = projection_author(tree, config_path, cfg)
    before = snapshot(tree)
    said = project_brain(tree, cfg)
    try:
        if before:
            record(tree, by, before, snapshot(tree))
    except Exception:
        log.debug("след проекции мозга не записался", exc_info=True)
    return said


def reconcile(tree: Path) -> None:
    """Голос сменился, а следа нет — назвать это как есть (правка руками или shell)."""
    now = snapshot(tree)
    row = last(tree)
    if not now or row is None:
        if now and row is None:
            # Первый след — точка отсчёта, без «кто»: прошлого здесь не знает никто.
            record(tree, "так было, когда след начал вестись", {}, now)
        return
    if not _same(row.get("to") or {}, now):
        record(tree, BY_UNKNOWN, row.get("to") or {}, now)


def orient_line(tree: Path, now: dt.datetime | None = None) -> str:
    """Строка кадра: на чём ты сейчас и кто менял последним (если недавно)."""
    try:
        reconcile(tree)
    except Exception:
        log.debug("след мозга не сверился", exc_info=True)
    state = snapshot(tree)
    if not state.get("model"):
        return ""
    line = f"Твой мозг сейчас: {state['model']} ({where(state)}, протокол {state.get('framework') or '?'})."
    row = last(tree)
    if not row or not row.get("from"):
        return line
    try:
        at = dt.datetime.fromisoformat(str(row.get("at")))
    except ValueError:
        return line
    now = now or _now()
    if (now - at).days >= FRESH_DAYS:
        return line
    before = (row.get("from") or {}).get("model") or "?"
    why = f"; зачем: «{row['why']}»" if row.get("why") else ""
    return (line + f" Последняя смена — {row.get('by')}, {at.strftime('%d.%m %H:%M')} UTC; "
            f"было {before}{why}.")


def install_switch_hook(agent_mod, tree: Path) -> bool:
    """Рука `switch_brain`: смена агентом — строкой следа с его «зачем»."""
    impl = getattr(agent_mod, "TOOL_IMPL", None)
    original = impl.get("switch_brain") if isinstance(impl, dict) else None
    if not callable(original) or getattr(original, "_brain_trail", False):
        return False

    def switch_brain(*args, **kwargs):
        before = snapshot(tree)
        out = original(*args, **kwargs)
        try:
            record(tree, BY_AGENT, before, snapshot(tree), why=str(kwargs.get("why") or ""))
        except Exception:
            log.debug("след switch_brain не записался", exc_info=True)
        return out

    switch_brain._brain_trail = True  # type: ignore[attr-defined]
    switch_brain.__wrapped__ = original  # type: ignore[attr-defined]
    switch_brain.__name__ = getattr(original, "__name__", "switch_brain")
    switch_brain.__doc__ = getattr(original, "__doc__", "")
    impl["switch_brain"] = switch_brain
    return True
