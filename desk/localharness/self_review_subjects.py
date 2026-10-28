"""Explicit owner-only plan/change/task-result material for the shared review service.

No ambient home scan: authored fields and individually SHA-bound declared files
are frozen once. Reviewers see these bytes through the existing snapshot reader,
never the live locator or an executable worktree. Source-update panels do not use
this extension and cannot acquire authority from its dispositions.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import re
import stat

KINDS = ("plan", "change", "task_result")
ROOTS = ("workspace", "agent", "app")
TEXT_FIELDS = ("text", "assignment", "answer")
LIST_FIELDS = ("criteria", "observed_effects", "checks")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _text(value, label, *, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(label + " must be nonempty text" if required else label + " must be text")
    return value


def _relative(value):
    from .self_review import EXCLUDED, PRIVATE_NAMES
    value = _text(value, "material path", required=True)
    path = Path(value)
    if (path.is_absolute() or any(c in value for c in (":", "\\", "\x00"))
            or any(part in {".", ".."} or part.startswith(".")
                   or part.lower() in (EXCLUDED - {"workspace"}) | PRIVATE_NAMES
                   for part in value.split("/")) or not all(value.split("/"))):
        raise ValueError("material must be an explicit non-private relative path")
    return path.as_posix()


def normalize(value):
    """Validate the public subject descriptor without reading live material."""
    from .self_review import SECRET
    if not isinstance(value, dict) or value.get("kind") not in KINDS:
        raise ValueError("subject kind must be plan, change or task_result")
    allowed = {"kind", "task_id", "question", "materials", *TEXT_FIELDS, *LIST_FIELDS}
    if set(value) - allowed:
        raise ValueError("unknown typed subject field")
    result = {"kind": value["kind"], "question": _text(value.get("question"), "question", required=True)}
    if "task_id" in value:
        result["task_id"] = _text(value["task_id"], "task_id", required=True)
    for field in TEXT_FIELDS:
        if field in value:
            result[field] = _text(value[field], field)
    for field in LIST_FIELDS:
        items = value.get(field, [])
        if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
            raise ValueError(field + " must contain nonempty text")
        result[field] = list(items)
    if not result["criteria"]:
        raise ValueError("typed review needs explicit criteria")
    if result["kind"] == "plan" and not result.get("text", "").strip():
        raise ValueError("plan requires its exact authored text")
    if result["kind"] == "task_result" and any(not result.get(k, "").strip() for k in ("assignment", "answer")):
        raise ValueError("task_result requires the assignment and exact answer")
    rows = value.get("materials", [])
    if not isinstance(rows, list):
        raise ValueError("materials must be a list")
    materials, names = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"name", "side", "root", "path", "sha256", "exists"}:
            raise ValueError("invalid material reference")
        side = row.get("side", "context")
        if side not in {"base", "candidate", "context"} or row.get("root") not in ROOTS:
            raise ValueError("material needs workspace/agent/app root and base/candidate/context side")
        if result["kind"] != "change" and side != "context":
            raise ValueError("base/candidate material belongs to a change subject")
        entry = {"name": _relative(row.get("name")), "path": _relative(row.get("path")),
                 "side": side, "root": row["root"], "exists": row.get("exists", True)}
        if type(entry["exists"]) is not bool:
            raise ValueError("exists must be boolean")
        digest = row.get("sha256")
        if entry["exists"]:
            if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError("material needs its exact SHA-256")
        elif digest is not None:
            raise ValueError("absent material has no SHA-256")
        entry["sha256"] = digest
        key = side + "/" + entry["name"]
        if key in names:
            raise ValueError("duplicate frozen material name")
        names.add(key)
        materials.append(entry)
    if result["kind"] == "change":
        sides = {s: {row["name"] for row in materials if row["side"] == s} for s in ("base", "candidate")}
        if not sides["base"] or sides["base"] != sides["candidate"]:
            raise ValueError("change requires matching explicit base and candidate inventories; declare absences")
    result["materials"] = sorted(materials, key=lambda row: (row["side"], row["name"]))
    raw = _json(result)
    if len(raw) > 1024 * 1024:
        raise ValueError("typed subject exceeds the explicit text budget; use exact material references")
    texts = [result['question'], *[result[k] for k in TEXT_FIELDS if k in result],
             *[s for k in LIST_FIELDS for s in result[k]]]
    if any(SECRET.search(text) for text in texts):
        raise ValueError("typed subject contains a possible credential")
    return result


def capture(value, roots, destination=None):
    """Read and verify declared material; do not substitute current or prior text."""
    from .self_review import _linked, SECRET
    value = normalize(value)
    manifest, payloads, total = [], {}, 0
    for row in value["materials"]:
        root = Path(roots[row["root"]]).absolute()
        path = root / row["path"]
        # Check the declared lexical chain as well as resolve() containment:
        # a symlink resolving back inside is still not immutable material.
        if (not root.is_dir() or _linked(root) or not path.resolve().is_relative_to(root.resolve())
                or any(parent.exists() and _linked(parent) for parent in (path, *path.parents)
                       if parent == root or root in parent.parents)):
            raise ValueError("linked or unavailable material root/path")
        logical = "materials/" + row["side"] + "/" + row["name"]
        if not row["exists"]:
            if path.exists() or path.is_symlink():
                raise ValueError("declared absent material exists")
            manifest.append({"path": logical, "exists": False, "sha256": None, "size": 0})
            payloads[logical] = b""
            continue
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024 or total + info.st_size > 64 * 1024 * 1024:
            raise ValueError("material not regular or exceeds snapshot budget")
        raw = path.read_bytes()
        if _sha(raw) != row["sha256"]:
            raise ValueError("material SHA-256 changed: " + row["name"])
        text = raw.decode("utf-8-sig")
        if "\x00" in text or SECRET.search(text):
            raise ValueError("material is binary or contains a possible credential")
        total += len(raw)
        manifest.append({"path": logical, "size": len(raw), "sha256": _sha(raw)})
        payloads[logical] = raw
    # Authored claims, provenance and checks are exact text, not host attestations.
    authored = _json(value)
    payloads["authored.json"] = authored
    manifest.append({"path": "authored.json", "size": len(authored), "sha256": _sha(authored)})
    changes = []
    if value["kind"] == "change":
        for row in value["materials"]:
            if row["side"] != "base":
                continue
            names = ["materials/" + side + "/" + row["name"] for side in ("base", "candidate")]
            texts = [payloads[name].decode("utf-8-sig").splitlines(True) for name in names]
            changes.extend(difflib.unified_diff(*texts, fromfile=names[0], tofile=names[1]))
    else:
        changes.append("Explicit frozen " + value["kind"] + "; read authored.json and declared materials. No source baseline is claimed.\n")
    diff = "".join(changes).encode("utf-8")
    payloads["changes.diff"] = diff
    manifest.append({"path": "changes.diff", "size": len(diff), "sha256": _sha(diff)})
    if destination is not None:
        for name, raw in payloads.items():
            if name.startswith("materials/") and any(r["path"] == name and r.get("exists") is False for r in manifest):
                continue
            target = Path(destination) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    return {"schema": "helene.self-review.subject.v1", "subject_kind": value["kind"],
            "typed_subject": value, "manifest": sorted(manifest, key=lambda row: row["path"]), "gaps": [],
            "intent": value["question"], "criteria": value["criteria"],
            "scope": "owner-only frozen explicit materials; authored effects/checks are claims, not execution proof; no live memory"}


def roots(service):
    app = service.app_root / "app" if (service.app_root / "app").is_dir() else service.app_root
    return {"workspace": service.tree / "workspace", "agent": service.code_dir, "app": app}


def stale(service, subject):
    try:
        current = capture(subject["typed_subject"], roots(service))
        return current["manifest"] != subject["manifest"]
    except (OSError, ValueError, UnicodeError):
        return True


def brief(record):
    kind = record["subject_kind"]
    return (f"Review exact frozen {kind} subject {record['subject_sha256']}. "
            f"Depth={record['policy']['depth']}. Read subject.json, authored.json and every relevant declared material. "
            "For change compare the explicit base/candidate bytes in changes.diff and inspect context dependencies. "
            "For plan test the reasoning against its question/criteria. For task_result compare assignment, exact answer, "
            "artifacts, observed_effects and checks: these last two are author's claims, not host execution attestations. "
            "Do not replace missing evidence with live source, old reviews, or unstated assumptions. "
            "Only inspect is supported, no execution or live memory. Ordinary review is voluntary advice, not completion permission. "
            "Return ONLY JSON: {subject_sha256, verdict: clear|findings|insufficient, summary, "
            "findings:[{path,scenario,detail}], gaps:[]}. Missing evidence is insufficient, not clear. "
            "A clear material review is not installed/runtime verification and never opens a source-update gate.")
