# -*- coding: utf-8 -*-
"""Keep post-birth distribution Markdown outside the agent's authored soul.

A proposal is evidence, not approval: only the agent's own identity/skill write
may apply it. The immutable candidate bytes and current-file digest let that
reader compare an exact source without treating stock-byte equality as consent.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

_SEEN: set[tuple[str, str]] = set()  # A read in this running agent, not a review verdict.


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_markdown(actual: bytes | None, candidate: bytes) -> bool:
    if actual is None:
        return False
    # Windows Path.write_text can translate LF into CRLF. A deliberate choice
    # cannot reappear as a new proposal solely because of line endings.
    return actual.decode("utf-8").replace("\r\n", "\n").rstrip("\n") == \
        candidate.decode("utf-8").replace("\r\n", "\n").rstrip("\n")


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _folder(tree: Path, *, create: bool = False) -> Path:
    """Private preimages must not follow an existing link out of the home."""
    tree = Path(tree)
    path = tree
    for part in ("memory", ".state", "kit-proposals"):
        path = path / part
        if _linked(path):
            raise OSError("kit proposal store is symlinked or junctioned")
    if create:
        path.mkdir(parents=True, exist_ok=True)
        if any(_linked(tree.joinpath(*("memory", ".state", "kit-proposals")[:i]))
               for i in range(1, 4)):
            raise OSError("kit proposal store changed to a link")
    return path


def stage(tree: Path, target: str, candidate: bytes, *, source: str) -> str | None:
    """Retain an exact candidate and its observed target identity, never edit soul.

    A refused/unreadable target is not absence. Fail closed, leaving any previous
    proposal untouched; the caller may report the failure without starting a
    write to the target. Same bytes and same target are idempotent on replay.
    """
    tree = Path(tree)
    rel = Path(target)
    if (rel.is_absolute() or ".." in rel.parts or len(rel.parts) < 2
            or rel.parts[0] != "soul" or rel.suffix != ".md"):
        raise ValueError("kit proposal target must be a relative soul Markdown path")
    path = tree / rel
    if any(_linked(tree.joinpath(*rel.parts[:i]))
           for i in range(1, len(rel.parts))):
        raise OSError("kit target parent is a symlink")
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        if _linked(path):
            raise OSError("kit target is a broken link")
        current = None
    if _linked(path):
        raise OSError("kit target is a link")
    # Preserve the preimage inside the private agent tree; a later source-kit
    # change cannot take away the exact bytes the agent compared. Oversized or
    # non-text soul needs manual inspection, never a clipped comparison.
    if len(candidate) > 250_000 or (current is not None and len(current) > 250_000):
        raise ValueError("kit Markdown too large to retain an exact review candidate")
    candidate_text = candidate.decode("utf-8")
    previous_text = current.decode("utf-8") if current is not None else None
    if _same_markdown(current, candidate):
        return None
    before = _sha(current) if current is not None else None
    source_sha = _sha(candidate)
    name = _sha(json.dumps([target.replace("\\", "/"), source, source_sha, before],
                           separators=(",", ":")).encode("utf-8"))
    folder = _folder(tree, create=True)
    record = folder / f"{name}.json"
    if _linked(record):
        raise OSError("kit proposal record is a link")
    payload = {"schema": 1, "id": name, "target": target.replace("\\", "/"),
               "source": source, "source_sha256": source_sha,
               "current_sha256": before, "candidate": candidate_text,
               "previous": previous_text}
    # Never replace a retained proposal, even when a later run changes the kit.
    # Atomic exclusive publication avoids a partially visible candidate when two
    # old runners enter ensure_layout together. A conflict is a retained fact.
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    if record.exists():
        if record.read_bytes() != encoded:
            raise OSError("kit proposal id collision or torn retained record")
        return name
    fd, temp_name = tempfile.mkstemp(prefix=".kit-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as sink:
            sink.write(encoded)
            sink.flush()
            os.fsync(sink.fileno())
        # Replace is atomic: a crash before this leaves only a private scratch
        # file, never a partial candidate that could look ready for review.
        if record.exists() and record.read_bytes() != encoded:
            raise OSError("kit proposal id collision")
        os.replace(temp_name, record)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return name


def _record(tree: Path, proposal_id: str) -> tuple[Path, dict]:
    if len(proposal_id) != 64 or any(ch not in "0123456789abcdef" for ch in proposal_id):
        raise ValueError("invalid kit proposal id")
    folder = _folder(tree)
    if _linked(folder / f"{proposal_id}.json"):
        raise OSError("kit proposal record is a link")
    row = json.loads((folder / f"{proposal_id}.json").read_text(encoding="utf-8"))
    if not isinstance(row, dict) or row.get("id") != proposal_id:
        raise ValueError("kit proposal identity mismatch")
    rel = Path(str(row.get("target") or ""))
    if (rel.is_absolute() or ".." in rel.parts or len(rel.parts) < 2
            or rel.parts[0] != "soul" or rel.suffix != ".md"):
        raise ValueError("invalid kit proposal target")
    if _sha(row["candidate"].encode("utf-8")) != row.get("source_sha256"):
        raise ValueError("kit proposal candidate changed")
    prior = row.get("previous")
    if (prior is not None and not isinstance(prior, str)) or (
            _sha(prior.encode("utf-8")) if prior is not None else None
    ) != row.get("current_sha256"):
        raise ValueError("kit proposal preimage changed")
    target = tree / rel
    if _linked(target) or any(_linked(tree.joinpath(*rel.parts[:i]))
                              for i in range(1, len(rel.parts))):
        raise ValueError("kit target is a symlink")
    return target, row


def review(tree: Path, action: str, proposal_id: str = "", reason: str = "") -> dict:
    """Agent-facing exact read and decision; never applies a proposal itself.

    Accept is a post-write acknowledgement after the agent uses its own
    manage_identity/write_skill route. Reject preserves the candidate and
    preimage but removes it from the active orientation on later turns. A
    changed target refuses a stale rejection; a written proposal can still
    be acknowledged because its preimage and identity were retained.
    """
    tree = Path(tree)
    action = str(action or "").lower().strip()
    if action == "list":
        return {"pending": [{"id": row["id"], "target": row["target"],
                             "source": row["source"]} for row in active(tree)]}
    target, row = _record(tree, proposal_id)
    folder = _folder(tree)
    if _linked(folder / "active") or _linked(folder / "decisions"):
        raise OSError("kit proposal active or decision path is a link")
    if proposal_id not in (folder / "active").read_text(encoding="ascii").splitlines():
        raise ValueError("proposal is not from the current kit")
    decision = folder / "decisions" / f"{proposal_id}.json"
    if _linked(decision):
        raise OSError("kit proposal decision is a link")
    if action == "read":
        _SEEN.add((str(tree.resolve()), proposal_id))
        return {"id": proposal_id, "target": row["target"], "source": row["source"],
                "candidate": row["candidate"], "previous": row["previous"],
                "current_sha256": row["current_sha256"], "source_sha256": row["source_sha256"],
                "decision": json.loads(decision.read_text(encoding="utf-8")) if decision.exists() else None}
    if action not in ("accept", "reject"):
        raise ValueError("action must be list, read, accept or reject")
    if not str(reason or "").strip():
        raise ValueError("the agent must state a reason for its decision")
    if (str(tree.resolve()), proposal_id) not in _SEEN:
        raise ValueError("read the full candidate and previous text before deciding")
    if decision.exists():
        saved = json.loads(decision.read_text(encoding="utf-8"))
        if saved.get("decision") == action:
            return saved
        raise ValueError("proposal already decided differently")
    try:
        current = target.read_bytes()
    except FileNotFoundError:
        current = None
    if _linked(target):
        raise ValueError("kit target is a link")
    actual = _sha(current) if current is not None else None
    expected = row["source_sha256"] if action == "accept" else row["current_sha256"]
    matches = actual == expected
    if action == "accept" and not matches and current is not None:
        # Identity.revise adds its own trailing LF, and compact self wraps the
        # chosen text in revision metadata. Admit only its validated body, not
        # a guessed substring of the file. Keep the EFFECTIVE digest distinct.
        if row["target"] == "soul/self/CURRENT.md":
            from self_model import current_prompt_info
            info = current_prompt_info(tree)
            matches = (info.source == "current" and actual != row["current_sha256"]
                       and info.text.strip("\n") == row["candidate"].strip("\n"))
        else:
            matches = _same_markdown(current, row["candidate"].encode("utf-8"))
    if not matches:
        raise ValueError("target bytes do not match this decision; re-read current soul")
    saved = {"schema": 1, "id": proposal_id, "decision": action,
             "reason": str(reason).strip(), "by": "agent_tool",
             "target": row["target"], "observed_sha256": actual,
             "source_sha256": row["source_sha256"], "exact_bytes": actual == row["source_sha256"]}
    decision.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".decision-", dir=decision.parent)
    try:
        with os.fdopen(fd, "wb") as sink:
            sink.write((json.dumps(saved, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
            sink.flush()
            os.fsync(sink.fileno())
        # Exclusive publication: exists()+replace would let a competing turn
        # overwrite the first decision between those operations. No soul is
        # touched if this filesystem cannot publish the receipt exclusively.
        os.link(temp_name, decision)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return saved


def publish_active(tree: Path, ids: set[str]) -> None:
    """One boot's exact candidates; retained history never becomes active by age."""
    folder = _folder(tree, create=True)
    if _linked(folder / "active"):
        raise OSError("kit proposal active path is a link")
    fd, temp_name = tempfile.mkstemp(prefix=".active-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as sink:
            sink.write("\n".join(sorted(ids)) + ("\n" if ids else ""))
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(temp_name, folder / "active")
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def active(tree: Path) -> list[dict]:
    """Current, not merely retained, candidates; unreadable evidence refuses."""
    tree = Path(tree)
    folder = _folder(tree)
    if _linked(folder / "active") or _linked(folder / "decisions"):
        raise OSError("kit proposal active or decision path is a link")
    try:
        names = (folder / "active").read_text(encoding="ascii").splitlines()
    except FileNotFoundError:
        return []
    results = []
    for name in names:
        if len(name) != 64 or any(ch not in "0123456789abcdef" for ch in name):
            raise ValueError("invalid kit proposal id")
        target, row = _record(tree, name)
        decision = folder / "decisions" / f"{name}.json"
        if _linked(decision):
            raise OSError("kit proposal decision is a link")
        if decision.exists():
            decided = json.loads(decision.read_text(encoding="utf-8"))
            if not isinstance(decided, dict) or decided.get("id") != name or \
                    decided.get("decision") not in ("accept", "reject"):
                raise ValueError("kit decision receipt unreadable or mismatched")
            continue
        try:
            current = target.read_bytes()
        except FileNotFoundError:
            current = None
        if _same_markdown(current, row["candidate"].encode("utf-8")):
            continue                     # the agent has already made these bytes live
        if (_sha(current) if current is not None else None) != row.get("current_sha256"):
            continue                     # changed since boot, never a live offer
        results.append(row)
    return results


def pending_line(tree: Path, birth_receipt: Path | None = None) -> str:
    """A pointer, not the proposed prose: never load it into persona by default."""
    try:
        rows = active(tree)
        birth = (json.loads(Path(birth_receipt).read_text(encoding="utf-8"))
                 if birth_receipt is not None and Path(birth_receipt).exists() else {})
        gaps = birth.get("gaps") if isinstance(birth, dict) and birth.get("state") == "partial" else []
        gaps = gaps if isinstance(gaps, list) else []
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return "Реестр предложений kit не читается; это не доказательство, что предложений нет."
    partial = ("Первое рождение было ЧАСТИЧНЫМ (" + ", ".join(str(x) for x in gaps[:8])
               + (") и другие пропуски." if len(gaps) > 8 else ").")
               + " Это историческая отметка; проверь текущие файлы и capability." if gaps else "")
    if not rows:
        return partial
    first = ", ".join(f"memory/.state/kit-proposals/{row['id']}.json" for row in rows[:3])
    rest = f" (+{len(rows)-3} через fs_ls)" if len(rows) > 3 else ""
    return (partial + " " if partial else "") + (f"Поставка предлагает {len(rows)} отдельн(ых) правок soul Markdown; "
            f"это НЕ применённые файлы личности. Кандидаты: {first}{rest}; "
            "вызови review_kit(read), сравни полный текст с прежним файлом, "
            "затем сам реши: reject с причиной либо собственная ревизия через "
            "manage_identity (SOUL/VOICE/self), write_skill (навык) или fs_write с expected_sha256 "
            "(INDEX), затем review_kit(accept). Stock-совпадение не согласие.")


_TOOL = {
    "name": "review_kit",
    "description": ("Review a distribution proposal to my SOUL/VOICE/self/skill Markdown. "
                    "list shows IDs, read returns complete proposed and previous text. "
                    "reject records my refusal; accept ONLY acknowledges bytes I have already "
                    "written using manage_identity/write_skill, or fs_write with CAS for INDEX. "
                    "This tool never edits my soul."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "read", "accept", "reject"]},
        "proposal_id": {"type": "string"}, "reason": {"type": "string"},
    }, "required": ["action"]},
}


def install(agent_mod, tree: Path) -> None:
    """Attach one explicit choice to the agent's ordinary tools, not a deploy knob."""
    def handler(action: str, proposal_id: str = "", reason: str = "") -> str:
        if agent_mod._active_scope() != "owner":
            return "Не здесь: предложения моей личности читаю и решаю только в ЛС владельца или своём окне."
        try:
            return json.dumps(review(tree, action, proposal_id, reason), ensure_ascii=False)
        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            return f"Предложение kit не решено: {exc}"

    agent_mod.TOOL_IMPL["review_kit"] = handler
    for attr in ("OWNER_TOOLS", "PRAXIS_SELF_TOOLS"):
        tools = getattr(agent_mod, attr)
        if not any(t.get("name") == "review_kit" for t in tools):
            tools.append(dict(_TOOL))
