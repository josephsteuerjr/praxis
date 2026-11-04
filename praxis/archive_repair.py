"""Explicit helper compacts of exact selected archive occurrences.

These receipts do not assert conversation-event coverage. The original archive
and all residual receipts remain intact. A recap is projected only when EVERY
source occurrence is selected in the current scope and still byte-identical.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
from pathlib import Path

BASE = Path(os.environ.get("PRAXIS_BASE") or Path(__file__).resolve().parent)
log = logging.getLogger("praxis-archive-repair")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def directory(peer):
    if not re.fullmatch(r"-?\d+", str(peer)):
        raise ValueError("archive repair requires an exact numeric Telegram peer")
    return BASE / "memory/life/archive-repair" / str(peer)


def key(occurrence):
    return int(occurrence["offset"]), str(occurrence["sha256"])


def verify(peer, sources):
    import group_context
    if not sources:
        raise ValueError("empty archive selection")
    seen = set()
    with group_context.archive_path(peer).open("rb") as f:
        for source in sources:
            ident = key(source)
            if ident in seen or not 0 <= ident[0] < int(source["end"]):
                raise ValueError("duplicate or invalid archive occurrence")
            seen.add(ident)
            f.seek(ident[0]);raw = f.read(int(source["end"]) - ident[0])
            if (not raw.endswith(b"\n") or hashlib.sha256(raw).hexdigest() != ident[1]
                    or json.loads(raw) != source["row"]
                    or str(source["row"].get("peer_id")) != str(peer)):
                raise ValueError("archive source changed")


def prepare(selected, *, budget_chars=48000):
    if not selected.get("complete"):
        return {"ok": False, "reason": "incomplete_source"}
    represented = {key(r["archive_occurrence"]) for r in selected["rows"]
                   if r.get("archive_occurrence")}
    rows = [r for r in selected["residuals"] if "row" in r and key(r) in represented]
    sources, used = [], 0
    # Keep the most recent archive occurrence live, including an orphaned wake.
    for row in rows[:-1]:
        size = len(json.dumps(row, ensure_ascii=False))
        if used + size > budget_chars:
            if not sources:
                return {"ok": False, "reason": "repair_source_exceeds_input_budget"}
            break
        sources.append(copy.deepcopy(row));used += size
    if not sources:
        return {"ok": False, "reason": "no_archive_prefix"}
    peer = str(selected["scope"]["peer_id"])
    verify(peer, sources)
    payload = dict(schema="praxis.archive-repair.selection.v1", chat=str(selected["chat"]),
                   scope=copy.deepcopy(selected["scope"]), kind="archive", sources=sources)
    return dict(ok=True, selection=digest(payload), place=str(selected["chat"]),
                kind="archive", sources=sources, payload=payload, source_chars=used)


def _body(model, text, count):
    return ("# Техническая свёртка архива\n\n"
            f"Вспомогательная модель: {model}. Исходных записей: {count}. "
            "Это технический пересказ, не авторский текст Праксис. "
            "Исходные байты архива сохранены.\n\n" + text.strip() + "\n")


def commit(prepared, recap):
    from self_model import FileLock
    payload = prepared["payload"];selection = digest(payload)
    if prepared["selection"] != selection or recap["selection"] != selection:
        raise ValueError("archive selection mismatch")
    text = str(recap.get("text") or "").strip();model = str(recap.get("model") or "").strip()
    if (not text or not model or recap.get("stop_reason") not in {"end_turn", "stop", "completed"}
            or hashlib.sha256(text.encode()).hexdigest() != recap["output_sha256"]
            or not re.fullmatch(r"[0-9a-f]{64}", recap.get("prompt_sha256", ""))
            or re.search(r"^## ", text, re.M)):
        raise ValueError("incomplete or unattributed archive recap")
    peer = payload["scope"]["peer_id"];root = directory(peer)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with FileLock(root / "write.lock", timeout=30, stale_after=300, heartbeat=5):
        verify(peer, payload["sources"])
        body = _body(model, text, len(payload["sources"]))
        import group_context
        if len(body) >= sum(len(group_context.epoch_occurrence_row(s)["line"]) + 1
                            for s in payload["sources"]):
            raise ValueError("archive recap does not reduce selected source size")
        record = dict(schema="praxis.archive-repair.compact.v1", selection=selection,
                      payload=payload, model=model, prompt_sha256=recap["prompt_sha256"],
                      output_sha256=recap["output_sha256"],
                      body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                      author="owner-authorized maintenance model; not Praxis")
        path = root / (selection + ".json");md = path.with_suffix(".md")
        if path.exists():
            if json.loads(path.read_text()) != record or md.read_text() != body:
                raise ValueError("immutable archive receipt conflict")
            return {"ok": True, "folded": len(payload["sources"]), "compact_id": selection}
        for target, data in ((md, body), (path, json.dumps(record, ensure_ascii=False, sort_keys=True))):
            temporary = target.with_suffix(target.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as f:
                f.write(data);f.flush();os.fsync(f.fileno())
            temporary.chmod(0o600);temporary.replace(target)
        fd = os.open(root, os.O_DIRECTORY);os.fsync(fd);os.close(fd)
        import memory_life
        memory_life.append_event("maintenance_archive_compacted", source="archive-repair:" + selection,
            text="Owner-authorized technical archive recap; original bytes retained; not Praxis authorship",
            meta={"model": model, "selection": selection, "source_count": len(payload["sources"]),
                  "prompt_sha256": recap["prompt_sha256"], "body_sha256": record["body_sha256"]})
    return {"ok": True, "folded": len(payload["sources"]), "compact_id": selection}


def receipts(peer):
    """Fail closed to raw sources for malformed, changed or partial receipts."""
    result = []
    try:
        paths = sorted(directory(peer).glob("*.json"))
    except ValueError:
        return result
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"));payload = record["payload"]
            body = path.with_suffix(".md").read_text(encoding="utf-8")
            if (record["schema"] != "praxis.archive-repair.compact.v1"
                    or payload["schema"] != "praxis.archive-repair.selection.v1"
                    or str(payload["scope"]["peer_id"]) != str(peer)
                    or digest(payload) != record["selection"] or path.stem != record["selection"]
                    or not record["model"] or not body
                    or hashlib.sha256(body.encode()).hexdigest() != record["body_sha256"]):
                raise ValueError("invalid archive compact")
            verify(peer, payload["sources"])
            result.append(dict(record, body=body))
        except (OSError, ValueError, KeyError, TypeError):
            log.error("Invalid archive maintenance receipt; raw sources remain selected: %s", path.name)
    return result


def project(tape, records):
    """Replace only complete selected source sets; never expose another scope."""
    rows = tape["rows"]
    for record in records:
        wanted = {key(s) for s in record["payload"]["sources"]}
        indexes = [i for i,r in enumerate(rows) if r.get("archive_occurrence")
                   and key(r["archive_occurrence"]) in wanted]
        present = {key(rows[i]["archive_occurrence"]) for i in indexes}
        if present != wanted:
            continue
        first = min(indexes);removed = set(indexes)
        compact = dict(self=False, line=record["body"], role_line=record["body"],
                       archive_maintenance_compact=record["selection"])
        rows = [compact if i == first else row for i,row in enumerate(rows)
                if i not in removed or i == first]
    tape["rows"] = rows
