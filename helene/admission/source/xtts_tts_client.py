"""Light client for the separately supervised XTTS worker; no implicit fallback."""
from __future__ import annotations
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

_LOCK = threading.Lock()
_CANCEL = threading.Event()


def cancel():
    """Cancel this process's active synthesis, not neighboring agent tasks."""
    _CANCEL.set()


def synthesize(text: str, output_dir: Path, *, stopped=None, timeout=300) -> Path:
    if not text.strip() or len(text) > 4000:
        raise ValueError("XTTS expects 1..4000 characters")
    home = Path(os.environ.get("HELENE_XTTS_HOME", ""))
    if not os.environ.get("HELENE_XTTS_HOME") or not (home / "ready.json").is_file():
        raise RuntimeError("XTTS worker is not ready; install/enable XTTS in voice settings")
    with _LOCK:
        _CANCEL.clear()
        identity = uuid.uuid4().hex
        incoming, output = home / "inbox", home / "output"
        incoming.mkdir(parents=True, exist_ok=True)
        path = incoming / (identity + ".json")
        partial = path.with_suffix(".tmp")
        request = {"request_id":identity, "text":text,
                   "language":os.environ.get("HELENE_XTTS_LANGUAGE", "ru"),
                   "speaker":os.environ.get("HELENE_XTTS_SPEAKER", "Ana Florence"),
                   "reference":os.environ.get("HELENE_XTTS_REFERENCE", "")}
        partial.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        partial.replace(path)
        receipt = output / (identity + ".json")
        deadline = time.monotonic() + timeout
        while not receipt.exists():
            if _CANCEL.is_set() or (stopped and stopped()) or time.monotonic() >= deadline:
                (incoming / (identity + ".cancel")).touch()
                raise InterruptedError("XTTS synthesis cancelled or timed out; no audio sent")
            time.sleep(0.05)
        result = json.loads(receipt.read_text(encoding="utf-8"))
        if result.get("request_id") != identity or not result.get("ok"):
            raise RuntimeError(result.get("error") or "XTTS response identity mismatch")
        source = Path(result["path"]).resolve()
        if source != (output / (identity + ".wav")).resolve() or not source.is_file():
            raise RuntimeError("XTTS output is unavailable")
        if _CANCEL.is_set() or (stopped and stopped()):
            source.unlink(missing_ok=True)
            receipt.unlink(missing_ok=True)
            raise InterruptedError("XTTS synthesis cancelled; no audio sent")
        output_dir.mkdir(parents=True, exist_ok=True)
        final = output_dir / (identity + ".wav")
        staged = final.with_suffix(".part")
        shutil.copyfile(source, staged)
        staged.replace(final)
        source.unlink()
        receipt.unlink()
        return final
