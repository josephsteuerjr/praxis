"""Deferred speech in the existing MediaSpool ledger, never a second outbox.

The immutable queue item stays an intent throughout delivery/recovery. Its
prepared attachment is committed into that same record. All outbound/checkpoint
identities keep naming the original exact text/profile/address digest.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import time
import uuid

from xtts_protocol import INTENT_MIME, SCHEMA, XttsError, encoded, read_intent, validate_intent


def is_intent(item):
    return item.kind == "audio" and item.mime == INTENT_MIME


def validate_ref(spool, ref, *, expected_scope=None, expected_chat_id=None):
    # Validate storage magic, size, digest, path and audience using the existing
    # document boundary before interpreting this private preparation payload.
    raw_ref = replace(ref, kind="document", mime="application/octet-stream")
    spool._validate_ref_locked(raw_ref, expected_scope=expected_scope,
                               expected_chat_id=expected_chat_id)
    request = read_intent(ref.path)
    if request["scope"] != ref.scope or request["target_chat_id"] != str(ref.chat_id):
        raise XttsError("intent_authority_mismatch")
    return ref


def stage(spool, text, *, target_chat_id, scope, caption, run_id, health, generation_id=None, queue_id=None, voice_options=None):
    if not health.get("ready"):
        raise XttsError(str(health.get("phase") or "unavailable"))
    identity = queue_id or uuid.uuid4().hex
    value = validate_intent({"schema": SCHEMA, "request_id": identity,
                             "text": text, "model_id": health.get("model_id"),
                             "profile_id": health.get("profile_id"), "run_id": run_id,
                             "target_chat_id": str(target_chat_id), "scope": scope,
                             **({'generation_id': generation_id} if generation_id else {}),
                             **{k:v for k,v in (voice_options or {}).items() if k in ('provider','delivery')}})
    with spool._lock:
        ref = spool._store_bytes(encoded(value), direction="outbound", kind="document",
                                  filename="speech-intent.json", chat_id=target_chat_id,
                                  message_id=None, scope=scope, caption=caption)
        from media import OutboundMedia
        item = OutboundMedia(kind="audio", path=ref.path, mime=INTENT_MIME,
                             size=ref.size, target_chat_id=ref.chat_id, scope=ref.scope,
                             caption=ref.caption, voice_note=True, queue_id=identity,
                             run_id=run_id, sha256=ref.sha256)
        try:
            return spool.validate_outbound(item)
        except BaseException:
            ref.path.unlink(missing_ok=True)
            raise


def prepared(spool, item):
    """Return the first committed attachment, retaining immutable queue identity."""
    with spool._lock, spool._ledger_guard():
        record_path = spool._record_path(item.queue_id)
        if not record_path.is_file():
            return None
        record = spool._read_record_locked(record_path)
        if record.get("state") != "pending":
            return None
        if record.get("item") != spool._item_payload(item):
            raise XttsError("intent_conflict")
        value = record.get("preparation")
        if value is None:
            return None
        if value.get("intent_sha256") != item.sha256:
            raise XttsError("intent_conflict")
        result = spool._item_from_payload(value.get("item"))
        request = read_intent(item.path)
        if (result.queue_id != item.queue_id or result.run_id != item.run_id
                or str(result.target_chat_id) != str(item.target_chat_id)
                or result.scope != item.scope or result.caption != item.caption
                or result.reply_to_message_id != item.reply_to_message_id
                or result.kind != "audio" or not result.voice_note or is_intent(result)
                or value.get("model_id") != request["model_id"]
                or value.get("profile_id") != request["profile_id"]):
            raise XttsError("prepared_authority_mismatch")
        return result


def defer(spool, item, code):
    """Record a retry on the same media intent without storing private text."""
    with spool._lock, spool._ledger_guard():
        record = spool._read_record_locked(spool._record_path(item.queue_id))
        if record.get("state") != "pending":
            return
        previous = int(record.get("preparation_attempts") or 0)
        record["preparation_attempts"] = previous + 1
        record["preparation_retry_at"] = time.time() + min(60, 5 * (previous + 1))
        record["preparation_error"] = code if code in {"busy", "unavailable", "timeout", "cancelled"} else "preparation_failed"
        spool._write_record_locked(record)


def retry_ready(spool, item):
    with spool._lock, spool._ledger_guard():
        path = spool._record_path(item.queue_id)
        if not path.is_file():
            raise XttsError("intent_not_admitted")
        record = spool._read_record_locked(path)
        if record.get("item") != spool._item_payload(item):
            raise XttsError("intent_conflict")
        return record.get("state") == "pending" and time.time() >= float(record.get("preparation_retry_at") or 0)


def commit(spool, item, source: Path, metadata: dict, *, stopped=lambda: False):
    """Atomically attach first successful bytes to an admitted pending intent."""
    with spool._lock, spool._ledger_guard():
        record_path = spool._record_path(item.queue_id)
        record = spool._read_record_locked(record_path)
        if record.get("state") != "pending" or record.get("item") != spool._item_payload(item):
            raise XttsError("intent_no_longer_pending")
        # No recursive ledger acquisition. A racing worker may have committed
        # while this request was computing; the original attachment wins.
        if record.get("preparation") is not None:
            return False
        request = read_intent(item.path)
        if (metadata.get("model_id") != request["model_id"]
                or metadata.get("profile_id") != request["profile_id"]
                or metadata.get("request_id") != item.queue_id):
            raise XttsError("profile_changed")
        if stopped():
            raise XttsError("cancelled")
        audio = spool.resolve_outbound(source, kind="audio", target_chat_id=item.target_chat_id,
                                        scope=item.scope, caption=item.caption, voice_note=True)
        audio = replace(audio, queue_id=item.queue_id, run_id=item.run_id,
                         reply_to_message_id=item.reply_to_message_id)
        try:
            if stopped():
                raise XttsError("cancelled")
            record["preparation"] = {"schema": SCHEMA, "intent_sha256": item.sha256,
                                      "model_id": request["model_id"],
                                      "profile_id": request["profile_id"],
                                      "generation": str(metadata.get("generation") or ""),
                                      "engine": request.get("provider", "xtts"), "at": time.time(),
                                      "item": spool._item_payload(audio)}
            record["updated_at"] = time.time()
            spool._write_record_locked(record)
        except BaseException:
            audio.path.unlink(missing_ok=True)
            raise
        return True


def prepare(spool, item, client, *, stopped=lambda: False):
    if prepared(spool, item) is not None:
        return
    if not retry_ready(spool, item):
        raise XttsError("intent_no_longer_pending")
    spool.validate_outbound(item)
    request = read_intent(item.path)
    if request["request_id"] != item.queue_id or request["run_id"] != item.run_id:
        raise XttsError("intent_authority_mismatch")
    if stopped():
        raise XttsError("cancelled")
    # Cancellation addresses one transport attempt. Resuming the same durable
    # intent must not inherit the cancelled attempt's tombstone.
    request = {**request, 'attempt_id':uuid.uuid4().hex}
    path, metadata = client.synthesize_intent(request, stopped=stopped)
    try:
        commit(spool, item, path, metadata, stopped=stopped)
    finally:
        path.unlink(missing_ok=True)


def prepare_generation(spool, item, store, *, stopped=lambda: False):
    """Attach an independently generated result to this distinct send intent."""
    from xtts_stream_upload import verify_request
    request = read_intent(item.path)
    value = store.read(request['generation_id'])
    verify_request(request, value)
    if value['state'] != 'ready':
        raise XttsError('generation_' + value['state'])
    metadata = {**value['result'], 'request_id': item.queue_id, 'generation': value['attempt_id']}
    commit(spool, item, store.audio_path(value['generation_id']), metadata, stopped=stopped)


def existing_generation_delivery(spool, value, caption, queue_id):
    """Find this exact send intent in the one canonical outbox, across event retries."""
    from xtts_protocol import digest
    with spool._lock, spool._ledger_guard():
        path = spool._record_path(queue_id)
        if not path.is_file():
            return None
        record = spool._read_record_locked(path)
        payload = record['item']
        source = value['request']
        expected = {**source, 'request_id': queue_id, 'run_id': payload['run_id'],
                    'generation_id': value['generation_id']}
        if (payload.get('sha256') != digest(expected)
                or payload.get('scope') != source['scope']
                or str(payload.get('target_chat_id')) != source['target_chat_id']
                or payload.get('caption') != caption):
            raise XttsError('generation_delivery_conflict')
        return {'queue_id': queue_id, 'state': record['state'], 'run_id': payload['run_id']}


def attachment_paths(record, root):
    """Canonical terminal records retain both intent and prepared-file identity."""
    preparation = record.get("preparation") or {}
    payload = preparation.get("item") or {}
    path = payload.get("path")
    if path:
        from media import contained_path
        return (contained_path(root, path),)
    return ()


def cleanup_terminal(spool):
    for state in ("delivered", "failed", "expired"):
        for record in spool.outbox_results(state):
            for path in attachment_paths(record, spool.root):
                path.unlink(missing_ok=True)
