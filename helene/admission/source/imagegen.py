"""Image generation/editing through the configured relay, independent of voice.

The model chooses this hand; the host calls the standalone Images endpoint.
Artifacts live in the existing scoped media spool. No image bytes are journaled.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from pathlib import Path

import httpx

DEFAULTS = {"enabled": False, "model": "gpt-image-2", "quality": "auto",
            "size": "auto", "background": "opaque"}
CHOICES = {"quality": ("auto", "low", "medium", "high"),
           "background": ("auto", "opaque", "transparent")}
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def normalize(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {**DEFAULTS, **{k: raw[k] for k in DEFAULTS if k in raw}}
    out["enabled"] = out["enabled"] is True
    for key in ("model", "quality", "size", "background"):
        out[key] = str(out[key] or DEFAULTS[key]).strip()
    return out


def validate(changes: dict) -> dict:
    if not isinstance(changes, dict) or set(changes) - set(DEFAULTS):
        raise ValueError("неизвестные настройки изображений")
    if "enabled" in changes and not isinstance(changes["enabled"], bool):
        raise ValueError("images.enabled: нужно true/false")
    for key, value in changes.items():
        if key != "enabled" and not isinstance(value, str):
            raise ValueError(f"images.{key}: нужно текстовое значение")
    out = {k: (v if k == "enabled" else str(v).strip()) for k, v in changes.items()}
    for key, values in CHOICES.items():
        if key in out and out[key] not in values:
            raise ValueError(f"images.{key}: {' | '.join(values)}")
    if "model" in out and (not out["model"] or len(out["model"]) > 200):
        raise ValueError("images.model: нужен идентификатор модели изображений")
    if "size" in out:
        import re
        if out["size"] != "auto" and not re.fullmatch(r"[1-9]\d{0,4}x[1-9]\d{0,4}", out["size"]):
            raise ValueError("images.size: auto или ШИРИНАxВЫСОТА")
    return out


def endpoint(base_url: str, operation: str) -> str:
    base = str(base_url or "").rstrip("/")
    if not base:
        raise ValueError("OpenAI-канал для изображений не настроен")
    return f"{base}/images/{operation}"


def _post(url: str, key: str, payload: dict, turn_id: str) -> tuple[dict, str]:
    headers = {"x-codex-image-turn-id": turn_id}
    key = str(key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    # httpx has no automatic retries here. Redirects are not followed with the
    # caller's credentials. Even a read timeout can mean a completed generation.
    try:
        with httpx.Client(timeout=httpx.Timeout(255, connect=20), follow_redirects=False) as client:
            with client.stream("POST", url, json=payload, headers=headers) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ValueError("ответ изображений слишком большой")
                    raw.extend(chunk)
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeError) as exc:
                    raise ValueError("канал изображений вернул некорректный JSON") from exc
                if not response.is_success:
                    error = body.get("error") if isinstance(body, dict) else None
                    code = str(error.get("code") or error.get("type") or "upstream_error") if isinstance(error, dict) else "upstream_error"
                    raise ValueError(f"канал изображений: HTTP {response.status_code}, {code}")
                return body, response.headers.get("x-codex-imagegen-request-id", "")
    except httpx.TransportError as exc:
        raise ValueError("связь с генератором прервалась; исход неизвестен, автоматического повтора не было") from exc


def generate(prompt: str, *, refs: list[Path], spool, scope: str, chat_id,
             config: dict, framework: dict, turn_id: str, caption: str = "") -> dict:
    cfg = normalize(config)
    if not cfg["enabled"]:
        raise ValueError("генерация изображений выключена в «Системе»")
    validate(cfg)
    prompt = str(prompt or "").strip()
    if not prompt or len(prompt) > 32000:
        raise ValueError("нужен запрос изображения длиной 1–32000 символов")
    if len(refs) > 5:
        raise ValueError("для правки доступны 1–5 исходных изображений")
    operation = "edits" if refs else "generations"
    payload = {k: cfg[k] for k in ("model", "quality", "size", "background")}
    payload.update(prompt=prompt, n=1)
    if refs:
        payload["images"] = []
        for path in refs:
            raw = path.read_bytes()
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("исходное изображение слишком большое")
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                mime = Image.MIME.get(image.format)
                image.verify()
            if mime not in ("image/png", "image/jpeg", "image/webp"):
                raise ValueError("для правки нужен PNG, JPEG или WebP")
            payload["images"].append({"image_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"})
    started = time.monotonic()
    body, request_id = _post(endpoint(framework.get("base_url", ""), operation),
                             framework.get("api_key", ""), payload, turn_id)
    items = body.get("data") if isinstance(body, dict) else None
    # Provider usage is a paid fact even if artifact decoding later fails.
    usage = body.get("usage") if isinstance(body, dict) else None
    if isinstance(usage, dict):
        import llm
        llm._usage_add("images", {"in": int(usage.get("input_tokens") or 0),
                                 "out": int(usage.get("output_tokens") or 0)},
                       model=cfg["model"])
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ValueError("генератор не вернул одно готовое изображение")
    encoded = items[0].get("b64_json")
    if not isinstance(encoded, str) or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError("генератор вернул некорректные данные изображения")
    try:
        raw = base64.b64decode(encoded, validate=True)
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            mime, dimensions = Image.MIME.get(image.format), image.size
            image.verify()
    except Exception as exc:
        raise ValueError("полученное изображение не декодируется") from exc
    if len(raw) > MAX_IMAGE_BYTES or mime not in ("image/png", "image/jpeg", "image/webp"):
        raise ValueError("генератор вернул неподдерживаемое изображение")
    suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
    ref = spool.ingest_bytes(raw, kind="photo", filename=f"image.{suffix}",
                             chat_id=chat_id, message_id=None, scope=scope, caption=caption)
    if ref.size != len(raw) or ref.sha256 != hashlib.sha256(raw).hexdigest():
        raise ValueError("сохранённое изображение не совпадает с результатом генерации")
    return {"ok": True, "path": str(ref.path), "mime": ref.mime, "size": ref.size,
            "sha256": ref.sha256, "width": dimensions[0], "height": dimensions[1],
            "model": cfg["model"], "operation": operation, "request_id": request_id,
            "generation_id": str(items[0].get("generation_id") or ""),
            "usage": body.get("usage") or {}, "seconds": round(time.monotonic() - started, 2)}
