"""Durable agent inspection, independent of package installation or rollback."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time

NAME = "update_inspection"
STATUSES = ("passed", "problem", "unverified")
_REQUEST_LOCK = threading.RLock()
TOOL = {"name": NAME, "description": "Осмотр установленного обновления. status показывает просьбу, цели выпуска и названные баги. report сохраняет твой фактический отчёт: по каждому пункту passed/problem/unverified и проверяемый след evidence; не проверял — unverified с причиной. После report расскажи владельцу итог рукой reply. Эта рука не принимает установку и не включает откат.", "input_schema": {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["status", "report"]}, "request_id": {"type": "string"},
    "summary": {"type": "string"}, "checks": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "status": {"type": "string", "enum": list(STATUSES)}, "evidence": {"type": "string"}}, "required": ["id", "status", "evidence"], "additionalProperties": False}}}, "required": ["action"], "additionalProperties": False}}


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError): return {}


def write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as dst:
            json.dump(value, dst, ensure_ascii=False, indent=2); dst.write("\n"); dst.flush(); os.fsync(dst.fileno())
        os.replace(name, path)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass


def path(tree): return Path(tree) / "memory/.state/update-inspection.json"


def valid_goals(goals):
    return (isinstance(goals, list) and bool(goals) and len(goals) <= 100 and all(
        isinstance(g, dict) and isinstance(g.get('id'), str) and bool(g['id'].strip())
        and isinstance(g.get('title'), str) and bool(g['title'].strip()) for g in goals)
        and len({g['id'] for g in goals}) == len(goals))


def valid_request(value):
    if not valid_goals(value.get('goals')) or value.get('state') not in ('pending', 'needs_attention', 'reported'): return False
    if value.get('state') != 'reported': return True
    checks = value.get('checks')
    return (isinstance(value.get('summary'), str) and bool(value['summary'].strip())
        and isinstance(checks, list) and len(checks) == len(value['goals']) and all(
        isinstance(c, dict) and isinstance(c.get('id'), str) and c.get('status') in STATUSES
        and isinstance(c.get('evidence'), str) and bool(c['evidence'].strip()) for c in checks)
        and {c['id'] for c in checks} == {g['id'] for g in value['goals']})


def ensure(tree, receipt: dict, goals_path: Path | None = None) -> dict:
    with _REQUEST_LOCK:
        return _ensure(tree, receipt, goals_path)


def _ensure(tree, receipt: dict, goals_path: Path | None = None) -> dict:
    target = path(tree)
    key = str(receipt.get("id") or "")
    if not key:
        key = hashlib.sha256(json.dumps(receipt, sort_keys=True, default=str).encode()).hexdigest()[:24]
    previous = read(target)
    if (previous.get("id") == key and previous.get('version') == receipt.get('to_version')
        and previous.get('from_version') == receipt.get('from_version') and valid_request(previous)): return previous
    if previous:
        archive_key = hashlib.sha256(json.dumps(previous, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        write(target.parent / "update-inspections" / f"{archive_key}.json", previous)
    manifest = read(goals_path or Path(__file__).resolve().parents[1] / "resources/update-inspection.json")
    goals = manifest.get("goals") if manifest.get("version") == receipt.get("to_version") else None
    if not valid_goals(goals):
        goals = [{"id": "runtime", "title": "Работоспособность, память, доступные руки и совместимость со своими правками"}]
    else:
        notes = (manifest.get('platform_notes') or {}).get(receipt.get('platform')) if isinstance(manifest.get('platform_notes'), dict) else None
        if isinstance(notes, dict):
            goals = [{**goal, 'title': goal['title'] + ('; ' + notes[goal['id']] if isinstance(notes.get(goal['id']), str) else '')} for goal in goals]
    request = {"id": key, "state": "pending", "version": receipt.get("to_version"), "from_version": receipt.get("from_version"),
               "at": time.time(), "platform": receipt.get('platform'), "goals": goals, "summary": "", "checks": []}
    write(target, request)
    return request


def report(tree, request_id: str, summary: str, checks) -> dict:
    with _REQUEST_LOCK:
        return _report(tree, request_id, summary, checks)


def _report(tree, request_id: str, summary: str, checks) -> dict:
    current = read(path(tree))
    if not current or request_id != current.get("id"): raise ValueError("просьба изменилась — перечитай status")
    if not valid_goals(current.get('goals')): raise ValueError('просьба повреждена — осмотр будет назначен заново')
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 6000: raise ValueError("нужен короткий итог осмотра")
    if not isinstance(checks, list): raise ValueError("checks должен содержать проверенные пункты")
    needed = {g["id"] for g in current["goals"]}
    got = []
    for item in checks:
        if not isinstance(item, dict) or item.get("id") not in needed or item.get("status") not in STATUSES:
            raise ValueError("неизвестный пункт или статус")
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 6000: raise ValueError("у каждого пункта нужен след проверки или причина unverified")
        got.append({"id": item["id"], "status": item["status"], "evidence": evidence.strip()})
    if len(got) != len(needed) or {g["id"] for g in got} != needed: raise ValueError("отчёт должен покрывать каждый пункт ровно один раз")
    current.update(state="reported", summary=summary.strip(), checks=got, reported_at=time.time())
    write(path(tree), current)
    return current


def attention(tree, request_id):
    with _REQUEST_LOCK:
        current = read(path(tree))
        if current.get('id') == request_id and current.get('state') == 'pending':
            current.update(state='needs_attention')
            write(path(tree), current)
        return current


def install(agent, tree):
    impl, tools = getattr(agent, "TOOL_IMPL", None), getattr(agent, "BASE_TOOLS", None)
    if not isinstance(impl, dict) or not isinstance(tools, list): return False
    def hand(action="status", request_id="", summary="", checks=None):
        try:
            ctx = agent._TURN_CHANNEL.get()
            if ctx is None or not getattr(ctx, "owner", False): return "Осмотр обновления доступен только в диалоге владельца."
            if action == "status": return json.dumps(read(path(tree)), ensure_ascii=False)
            if action != "report": return "action: status или report"
            return json.dumps(report(tree, str(request_id), summary, checks), ensure_ascii=False)
        except (OSError, ValueError, AttributeError) as exc: return f"Отчёт не сохранён: {exc}"
    impl[NAME] = hand
    if not any(t.get("name") == NAME for t in tools if isinstance(t, dict)): tools.append(TOOL)
    if isinstance(getattr(agent, "HAND_PURPOSE", None), dict): agent.HAND_PURPOSE[NAME] = "осмотреть цели и баги установленного выпуска и сохранить фактический отчёт"
    return True
