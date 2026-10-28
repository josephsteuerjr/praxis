"""Outstanding Telegram inputs, not a historical done-ID ledger.

A pending record precedes archive publication. Recognition is a separate
receipt. Only the core's checkpoint-before-ACK boundary retires admitted input.
There is one runner consumer and the existing transport receiver; no scheduler
or second Telegram client is introduced.
"""
from contextlib import contextmanager
from copy import deepcopy
import datetime as dt
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import uuid

import owner_stop

log = logging.getLogger("helene.telegram-inputs")
SCHEMA = "helene.telegram-pending.v1"
LIMIT = 32
BYTE_LIMIT = 512 * 1024
RECORD_LIMIT = 256 * 1024
ROOM = re.compile(r"-?\d+(?:__topic__\d+)?\Z")
PRINCIPAL = re.compile(r"[1-9]\d*\Z")


def identity(record):
    return record["room"] + ":" + record["message_id"]


def marker(record):
    return ("[Telegram input " + identity(record) + "; sender "
            + record["sender_id"] + "; receipt " + record["nonce"] + "]\n")


def included(record, messages):
    prefix = marker(record)
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = (content[0].get("text", "")
                       if content and isinstance(content[0], dict)
                       and content[0].get("type") == "text" else "")
        if isinstance(content, str) and content.startswith(prefix):
            return True
    return False


def audio(record):
    row = record["row"]
    original = row.get("media_source") or {}
    return row.get("media_kind") == "audio" or any(
        kind in original for kind in ("voice", "audio"))


def source_path(tree, record):
    relative = str(record["row"].get("media_path") or "")
    if not relative:
        raise ValueError("Original Telegram audio is still downloading")
    tree = Path(tree).resolve()
    root = (tree / "memory/telegram-media").resolve()
    source = (tree / relative).resolve()
    if root not in source.parents or not source.is_file():
        raise ValueError("Original Telegram audio is unavailable")
    return source


def digest(source):
    with Path(source).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class PendingInputs:
    """An in-memory view of outstanding-only, individually atomic records.

    Loading is once per transport start. Selection is count/byte bounded.
    A rotating view prevents failed heads from starving independent later input.
    """
    def __init__(self, tree):
        self.tree = Path(tree)
        self.root = self.tree / "memory/.state/telegram-input-pending"
        self.lock = threading.RLock()
        self.items = {}
        self.cursor = 0
        self.last_tick = 0.0
        for path in sorted(self.root.glob("*.json")):
            raw = path.read_bytes()
            if len(raw) > RECORD_LIMIT:
                raise ValueError("Telegram pending record exceeds its format limit")
            value = json.loads(raw)
            self._validate(value)
            if path != self.path(identity(value)):
                raise ValueError("Telegram pending identity/path mismatch")
            self.items[identity(value)] = value

    def path(self, key):
        return self.root / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    @staticmethod
    def _validate(value):
        if (not isinstance(value, dict) or value.get("schema") != SCHEMA
                or not ROOM.fullmatch(str(value.get("room") or ""))
                or not PRINCIPAL.fullmatch(str(value.get("message_id") or ""))
                or not PRINCIPAL.fullmatch(str(value.get("sender_id") or ""))
                or not re.fullmatch(r"[0-9a-f]{32}", str(value.get("nonce") or ""))
                or not isinstance(value.get("row"), dict)
                or value["row"].get("outgoing")
                or str(value["row"].get("source_message_id") or "") != value["message_id"]):
            raise ValueError("Invalid Telegram pending record")

    def _save(self, value):
        self._validate(value)
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(raw) > RECORD_LIMIT:
            raise ValueError("Telegram pending record exceeds its format limit")
        target = self.path(identity(value))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("wb") as sink:
                sink.write(raw)
                sink.flush()
                os.fsync(sink.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        self.items[identity(value)] = deepcopy(value)

    def get(self, key):
        with self.lock:
            value = self.items.get(key)
            return deepcopy(value) if value is not None else None

    def capture(self, row, *, room, sender_id, owner_id, is_dm, title, input_text):
        """Called under Rooms' source-ID decision, BEFORE its archive write."""
        with owner_stop.LOCK:
            if owner_stop.paused():
                return None
            value = {
                "schema": SCHEMA, "room": str(room),
                "message_id": str(row.get("source_message_id") or ""),
                "sender_id": str(sender_id), "owner_id": str(owner_id),
                "is_dm": bool(is_dm), "title": str(title),
                "input_text": str(input_text), "row": deepcopy(row),
                "nonce": uuid.uuid4().hex, "created_at": time.time(),
                "published": False, "run_id": "", "run_path": "",
                "speech_sha256": "",
            }
            with self.lock:
                old = self.items.get(identity(value))
                if old is None:
                    self._save(value)
                else:
                    value = deepcopy(old)
            key, nonce = identity(value), value["nonce"]
            return lambda: self.published(key, nonce)

    def published(self, key, nonce):
        with self.lock:
            value = self.items.get(key)
            if value is None or value["nonce"] != nonce:
                return
            value = deepcopy(value)
            value["published"] = True
            self._save(value)

    def media_update(self, room, message_id, expected, payload):
        """Pending source first, archive second; stale edits cannot swap originals."""
        key = str(room) + ":" + str(message_id)
        with self.lock:
            old = self.items.get(key)
            if old is None or old["row"].get("media_source") != expected:
                return None
            value = deepcopy(old)
            value["row"].pop("media_error", None)
            value["row"].update(payload)
            value["published"] = False
            self._save(value)
            nonce = value["nonce"]
        return lambda: self.published(key, nonce)

    def recover_archive(self, rooms):
        """Finish capture->archive publication before accepting new ingress."""
        with self.lock:
            values = deepcopy(list(self.items.values()))
        for value in values:
            if value["published"]:
                continue
            row = value["row"]
            rooms.describe(value["room"], title=value["title"], is_dm=value["is_dm"],
                           sender=(str(row.get("sender_name") or "?"), value["sender_id"]))
            timestamp = dt.datetime.fromisoformat(
                str(row["timestamp"]).replace("Z", "+00:00")).timestamp()
            # The canonical saved row also preserves reply/media metadata.
            rooms.record(
                value["room"], str(row.get("text") or ""), outgoing=False,
                sender=str(row.get("sender_name") or "?"),
                sender_id=value["sender_id"], source_id=value["message_id"],
                source=str(row.get("source") or "botapi"), ts=timestamp,
                media=deepcopy(row))
            self.published(identity(value), value["nonce"])

    def batch(self, *, room=None, principal=None, run_id=None, rotate=False):
        with self.lock:
            values = sorted(self.items.values(),
                            key=lambda v: (v["created_at"], identity(v)))
            if room is not None:
                values = [v for v in values if v["room"] == str(room)]
            if principal is not None:
                values = [v for v in values if v["sender_id"] == str(principal)]
            if run_id is not None:
                values = [v for v in values if v["run_id"] in ("", str(run_id))]
                values.sort(key=lambda v: (v["run_id"] != str(run_id),
                                           v["created_at"], identity(v)))
            elif rotate and values:
                start = self.cursor % len(values)
                values = values[start:] + values[:start]
                self.cursor += min(LIMIT, len(values))
            result, size = [], 0
            for value in values:
                count = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
                if result and size + count > BYTE_LIMIT:
                    break
                result.append(deepcopy(value))
                size += count
                if len(result) == LIMIT:
                    break
            return result

    def bind(self, prepared, current):
        old = prepared["record"]
        with self.lock:
            value = self.items.get(identity(old))
            if value is None or value["nonce"] != old["nonce"]:
                raise ValueError("Telegram input changed before binding")
            if value["run_id"] not in ("", current.run_id):
                raise ValueError("Telegram input belongs to another run")
            if (str(current.delivery_chat_id) != value["room"]
                    or str(current.origin_chat_id) != value["room"]
                    or str(current.principal_id) != value["sender_id"]):
                raise ValueError("Telegram run changed room or principal")
            month = dt.datetime.fromisoformat(
                str(current.created_at).replace("Z", "+00:00")).strftime("%Y-%m")
            value = deepcopy(value)
            value.update(run_id=current.run_id,
                         run_path=month + "/" + current.run_id,
                         speech_sha256=prepared.get("speech_sha256") or "")
            self._save(value)
            return value

    def unbind(self, key, run_id):
        with self.lock:
            old = self.items.get(key)
            if old is None or old["run_id"] != run_id:
                return
            value = deepcopy(old)
            value.update(run_id="", run_path="", speech_sha256="")
            self._save(value)

    def ack(self, key, nonce, run_id):
        with self.lock:
            value = self.items.get(key)
            if value is None:
                return
            if value["nonce"] != nonce or value["run_id"] != run_id:
                raise ValueError("Telegram ACK differs from the exact input binding")
            self.path(key).unlink(missing_ok=True)
            self.items.pop(key, None)

    def discard_unbound(self):
        """Resume explicitly retires old automatic wakes, not existing runs."""
        with self.lock:
            for key, value in list(self.items.items()):
                if not value["run_id"]:
                    self.path(key).unlink(missing_ok=True)
                    self.items.pop(key, None)


def permitted(bot, record):
    # Changing the local Telegram owner must not inherit an old owner's inputs.
    return (str(bot.owner_id) == record["owner_id"]
            and bot.is_allowed(record["sender_id"]))


def prepare(runner, record, *, bound=False):
    body = str(record["input_text"])
    speech_sha = ""
    if audio(record):
        source = source_path(runner._tree, record)
        if not bound:
            runner._transcribe_path(source)
        receipt = runner.voice_admission.state(source)
        speech_sha = digest(source)
        if (receipt.get("status") != "ready"
                or receipt.get("sha256") != speech_sha
                or not str(receipt.get("transcript") or "").strip()
                or (bound and speech_sha != record.get("speech_sha256"))):
            raise runner.voice_admission.VoicePending(
                "Bound original has no matching recognized-speech receipt")
        body = (body + "\n[Транскрипт голосового]: "
                + str(receipt["transcript"])).strip()
    if not body.strip():
        raise ValueError("Telegram input has no admissible words")
    return {
        "record": record, "speech_sha256": speech_sha,
        "message": {"role": "user", "content": marker(record) + body},
    }


def select(runner, room):
    """Choose ready IDs, not whichever sender/row most recently changed RAM."""
    owner_stop.require_admission()
    ready, principal = [], None
    for record in runner._bot.inputs.batch(room=room, rotate=True):
        if record["run_id"] or not record["published"] or not permitted(runner._bot, record):
            continue
        if principal is not None and record["sender_id"] != principal:
            continue
        try:
            item = prepare(runner, record)
        except (runner.voice_admission.VoicePending, OSError, ValueError):
            continue
        principal = record["sender_id"]
        ready.append(item)
    return ready


@contextmanager
def activation(runner, batch):
    if runner._continuity is None:
        raise RuntimeError("Telegram source admission requires durable continuity")
    def before(current):
        if current.kind != "chat_turn":
            raise ValueError("Telegram activation attempted a different run kind")
        for item in batch:
            if not permitted(runner._bot, item["record"]):
                raise ValueError("Telegram sender policy changed before activation")
            runner._bot.inputs.bind(item, current)
    with runner._continuity.activation(before):
        yield


def collect(runner, current, messages):
    """The existing core hook checkpoints returned additions before calling ACK."""
    if current is None or runner._bot is None:
        return [], lambda: None
    room, principal = str(current.delivery_chat_id or ""), str(current.principal_id or "")
    store = runner._bot.inputs
    if owner_stop.paused():
        return [], lambda: None
    ready, additions = [], []
    for record in store.batch(room=room, principal=principal, run_id=current.run_id):
        if not record["published"] or not permitted(runner._bot, record):
            continue
        if record["run_id"] == current.run_id and included(record, messages):
            ready.append(record)  # Exact checkpoint replay: do not recognize again.
            continue
        bound = record["run_id"] == current.run_id
        try:
            item = prepare(runner, record, bound=bound)
        except (runner.voice_admission.VoicePending, OSError, ValueError):
            if bound:
                # Initial selected work must not silently vanish before model input.
                raise runner.voice_admission.VoicePending(
                    "A bound Telegram original is not recoverable yet")
            continue  # Independent failed input cannot veto ready text/voice.
        if not bound:
            with owner_stop.LOCK:
                owner_stop.require_admission()
                if not permitted(runner._bot, record):
                    continue
                record = store.bind(item, current)
        ready.append(record)
        additions.append(item["message"])

    def acknowledge():
        for record in ready:
            store.ack(identity(record), record["nonce"], current.run_id)
    return additions, acknowledge


def _checkpoint(agent, manager, current):
    """Read an exact hash-verified checkpoint of THIS run, never a history scan."""
    import run_resume
    for event in manager.iter_events(current.run_id, reverse=True, strict=True):
        if event.get("kind") == "run_checkpoint":
            value = run_resume._checkpoint_value(
                manager, current.run_id, event, max_result_bytes=64 * 1024 * 1024)
            return value["messages"]
    return []


def reconcile(runner, records):
    """Unknown ACK stays bound until exact checkpoint/core orphan evidence exists."""
    manager = runner._agent._runs()
    evidence = {}
    for record in records:
        run_id = record["run_id"]
        if not run_id or not permitted(runner._bot, record):
            continue
        try:
            if run_id not in evidence:
                from run_manager import RunNotFound
                try:
                    current = manager.context(run_id)
                except RunNotFound:
                    # Before manager.create: only the exact intended directory
                    # can prove that creation never began. Partial/unreadable
                    # run evidence is a hold, not success or permission to replay.
                    relative = Path(record["run_path"])
                    if (len(relative.parts) != 2
                            or relative.parts[1] != run_id
                            or not re.fullmatch(r"\d{4}-\d{2}", relative.parts[0])):
                        raise ValueError("Invalid intended Telegram run path")
                    intended = manager.root / relative
                    try:
                        intended.stat()
                    except FileNotFoundError:
                        evidence[run_id] = ("absent", None, None)
                    else:
                        raise ValueError("Partial Telegram run retained for inspection")
                else:
                    channel, _snapshot = runner._agent._load_exact_run_channel(manager, current)
                    if (str(current.delivery_chat_id) != record["room"]
                            or str(current.origin_chat_id) != record["room"]
                            or str(current.principal_id) != record["sender_id"]
                            or bool(channel.is_dm) != record["is_dm"]
                            or bool(channel.owner) != (
                                record["sender_id"] == str(runner._bot.owner_id))):
                        raise ValueError("Telegram pending/run authority differs")
                    evidence[run_id] = (
                        "present", current, _checkpoint(runner._agent, manager, current))
            kind, current, messages = evidence[run_id]
            if kind == "absent":
                runner._bot.inputs.unbind(identity(record), run_id)
            elif included(record, messages):
                runner._bot.inputs.ack(identity(record), record["nonce"], run_id)
            elif runner._continuity.closed_without_effects(run_id):
                runner._bot.inputs.unbind(identity(record), run_id)
            # Otherwise core resume/control owns this run. Even done/failed is
            # not proof that this exact input reached a checkpoint.
        except Exception:
            log.exception("Telegram input binding retained [%s]", identity(record))


def tick(runner):
    """Called once per outer runner tick, never recursively in pop_pending()."""
    if runner._bot is None or runner._continuity is None or owner_stop.paused():
        return
    store = runner._bot.inputs
    now = time.monotonic()
    if now - store.last_tick < 2:
        return
    store.last_tick = now
    records = store.batch(rotate=True)
    reconcile(runner, [record for record in records if record["run_id"]])
    # Re-read these exact IDs because reconciliation may have unbound/ACKed them.
    for old in records:
        record = store.get(identity(old))
        if (record is not None and not record["run_id"] and record["published"]
                and permitted(runner._bot, record)):
            runner._bot._enqueue(record["room"])
    # Failed ASR may be reconsidered at a later outer tick; voice_admission's
    # source/signature/error receipt prevents repeating failed inference.


def original_kind(tree, relative, source):
    """Authorize status/retry only for exact originals behind the existing route."""
    tree, source = Path(tree).resolve(), Path(source).resolve()
    try:
        canonical = source.relative_to(tree).as_posix()
    except ValueError:
        return ""
    if canonical != str(relative):
        return ""
    window = (tree / "memory/.control/desk_inbox/attachments").resolve()
    if window in source.parents:
        return "window"
    telegram = (tree / "memory/telegram-media").resolve()
    if telegram not in source.parents:
        return ""
    # A separate HTTP process reads current outstanding records, not the
    # receiver's RAM and not an archive-sized message history.
    store = PendingInputs(tree)
    with store.lock:
        for record in store.items.values():
            if audio(record) and record["row"].get("media_path") == canonical:
                return "telegram"
    return ""
