"""Observed agent usage and provider allowance. Never expose credentials or infer money."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import math
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


def number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _count(value):
    value = number(value)
    return max(0, int(value)) if value is not None else 0


def _row(rows):
    rows = [row for row in rows if isinstance(row, dict)]
    known = all((number(row.get("schema")) or 1) >= 2 for row in rows)
    sums = {key: sum(_count(row.get(key)) for row in rows)
            for key in ("in", "out", "cache_read", "cache_creation", "calls", "fallback")}
    incoming = sums["in"] + sums["cache_read"] + sums["cache_creation"] if known else None
    return {"calls": sums["calls"], "output_tokens": sums["out"], "input_tokens": incoming,
            "total_tokens": incoming + sums["out"] if incoming is not None else None,
            "cache_read_tokens": sums["cache_read"], "cache_creation_tokens": sums["cache_creation"],
            "fresh_input_tokens": sums["in"] if known else None,
            "cache_percent": round(sums["cache_read"] * 100 / incoming, 1) if incoming else None,
            "fallback_calls": sums["fallback"], "legacy_semantics": not known}


def _clock(tree):
    # Use the agent's small stdlib-only clock module, not a second timezone default.
    path = tree / "praxis_time.py"
    if not path.is_file():
        path = tree.parent / "tree" / "praxis_time.py"
    if path.is_file():
        spec = importlib.util.spec_from_file_location("helene_usage_clock", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.today(), "agent"
    return dt.datetime.now().astimezone().date(), "host"


def statistics(tree: Path):
    path = tree / "memory" / ".state" / "usage.json"
    today, clock = _clock(tree)
    result = {"schema": "helene.usage.v1", "today": today.isoformat(), "clock": clock,
              "source": "memory/.state/usage.json", "status": "unavailable", "days": [],
              "today_total": None, "week_total": None, "models": [], "roles": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a day map")
        result["updated_at"] = path.stat().st_mtime
    except FileNotFoundError:
        result["reason"] = "Счётчик ещё не создан"
        return result
    except (OSError, ValueError):
        result["reason"] = "Не удалось прочитать счётчик"
        return result
    by_role, by_model, week = {}, {}, []
    for offset in range(6, -1, -1):
        day = (today - dt.timedelta(days=offset)).isoformat()
        daily = data.get(day, {})
        if not isinstance(daily, dict):
            result["reason"] = "Повреждена запись дня"
            return result
        rows = [r for r in daily.values() if isinstance(r, dict)]
        result["days"].append({"day": day, "recorded": bool(rows), **_row(rows)})
        week.extend(rows)
        for role, row in daily.items():
            if not isinstance(row, dict):
                continue
            by_role.setdefault(role, []).append(row)
            models = row.get("models") or {}
            if isinstance(models, dict):
                for model, model_row in models.items():
                    by_model.setdefault(model, []).append(model_row)
    result.update(status="ok", today_total=_row((data.get(today.isoformat()) or {}).values()),
                  week_total=_row(week),
                  roles=[{"name": name, **_row(rows)} for name, rows in by_role.items()],
                  models=[{"name": name, **_row(rows)} for name, rows in by_model.items()])
    result["models"].sort(key=lambda row: row["calls"], reverse=True)
    return result


def _window(label, used_percent, duration, reset, **extra):
    used = number(used_percent)
    duration = number(duration)
    reset = number(reset)
    return {"label": label, "used_percent": used,
            "remaining_percent": max(0, min(100, 100-used)) if used is not None else None,
            "window_seconds": duration, "resets_at": reset, **extra}


def normalize_glm(raw):
    if raw.get("success") is False or raw.get("code", 200) not in (200, "200"):
        raise ValueError("provider rejected quota query")
    data = raw.get("data") or {}
    limits = data.get("limits", []) if isinstance(data, dict) else []
    windows = []
    for row in limits:
        if not isinstance(row, dict):
            continue
        unit, amount = row.get("unit"), number(row.get("number"))
        duration = {3: 3600, 6: 604800}.get(unit)
        duration = duration * amount if duration and amount else None
        label = ("Неделя" if duration == 604800 else f"{amount:g} ч" if unit == 3 and amount
                 else "MCP · месяц" if row.get("type") == "TIME_LIMIT" else "Квота сервиса")
        reset = number(row.get("nextResetTime"))
        windows.append(_window(label, row.get("percentage"), duration, reset / 1000 if reset else None,
                               unit="credits" if row.get("type") == "CREDIT_LIMIT" else "provider_units",
                               limit=number(row.get("usage")), used=number(row.get("currentValue")),
                               remaining=number(row.get("remaining"))))
    return windows


def normalize_codex(raw):
    windows = []
    buckets = raw.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) and isinstance(raw.get("rateLimits"), dict):
        buckets = {"Codex": raw["rateLimits"]}
    if isinstance(buckets, dict):
        for bucket, row in buckets.items():
            if not isinstance(row, dict):
                continue
            for which in ("primary", "secondary"):
                window = row.get(which)
                if isinstance(window, dict) and any(number(window.get(k)) is not None for k in
                        ("usedPercent", "windowDurationMins", "resetsAt")):
                    minutes = number(window.get("windowDurationMins"))
                    duration = minutes * 60 if minutes else None
                    windows.append(_window("Неделя" if duration == 604800 else bucket,
                        window.get("usedPercent"), duration, window.get("resetsAt"), bucket=bucket))
        return windows
    limits = [("", raw.get("rate_limit") or {})]
    for item in raw.get("additional_rate_limits") or []:
        if isinstance(item, dict):
            limits.append((str(item.get("limit_name") or item.get("metered_feature") or ""),
                           item.get("rate_limit") or {}))
    for bucket, limit in limits:
        for which in ("primary_window", "secondary_window"):
            window = limit.get(which) if isinstance(limit, dict) else None
            if not isinstance(window, dict) or not any(number(window.get(k)) is not None for k in
                    ("used_percent", "limit_window_seconds", "reset_at")):
                continue
            duration = number(window.get("limit_window_seconds"))
            label = "Неделя" if duration == 604800 else f"{duration / 3600:g} ч" if duration else "Квота сервиса"
            windows.append(_window((bucket + " · " if bucket else "") + label,
                                   window.get("used_percent"), duration, window.get("reset_at"), bucket=bucket))
    return windows


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward a provider key to a redirect destination.


_CACHE = {}
_LOCK = threading.Lock()


def allowances(tree: Path):
    try:
        cfg = json.loads((tree / "memory" / "llm.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"providers": [], "reason": "Нет настроенного провайдера"}
    providers = []
    seen = set()
    for framework, block in (cfg.get("frameworks") or {}).items():
        base, secret = str(block.get("base_url") or "").rstrip("/"), str(block.get("api_key") or "")
        if not base or not secret:
            continue
        url = urlsplit(base)
        if url.hostname in {"api.z.ai", "open.bigmodel.cn"}:
            name, normalize = "GLM", normalize_glm
            endpoint = f"https://{url.hostname}/api/monitor/usage/quota/limit"
            authorization = secret
        elif framework == "openai" and url.hostname not in {"api.openai.com", None}:
            name, normalize = "Codex · реле", normalize_codex
            endpoint = (base if base.endswith("/v1") else base + "/v1") + "/limits"
            authorization = "Bearer " + secret
        else:
            continue
        cache_key = (str(tree.resolve()), endpoint, hashlib.sha256(secret.encode()).hexdigest())
        if cache_key in seen:
            continue
        seen.add(cache_key)
        with _LOCK:
            now = time.time()
            cached = _CACHE.get(cache_key)
            if cached and now - cached[0] < 60:
                providers.append(cached[1])
                continue
            row = {"name": name, "status": "unavailable", "windows": [], "observed_at": now,
                   "source": "provider", "scope": "account"}
            try:
                request = Request(endpoint, headers={"Authorization": authorization, "Accept": "application/json"})
                with build_opener(_NoRedirect()).open(request, timeout=8) as response:
                    body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    raise ValueError("quota response too large")
                windows = normalize(json.loads(body))
                row.update(status="ok" if windows else "unavailable", windows=windows)
                if not windows:
                    row["reason"] = "Сервис не вернул квоты подписки"
            except Exception as exc:
                # Never expose raw upstream bodies, exception URLs or key material.
                row["reason"] = "Квоты сейчас недоступны у сервиса"
                if isinstance(getattr(exc, "code", None), int):
                    row["http_status"] = exc.code
                if cached and cached[1].get("windows"):
                    row.update(windows=cached[1]["windows"], status="stale", observed_at=cached[1]["observed_at"])
            _CACHE[cache_key] = (now, row)
            providers.append(row)
    return {"providers": providers}
