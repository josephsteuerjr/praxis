"""Explicitly admitted voice delivery: growing private OGG -> MTProto parts.

Uploading is not publication. The existing media sender alone issues sendMedia,
with its canonical stable random_id, after final integrity and run admission.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import secrets

from xtts_protocol import MAX_AUDIO, XttsError

PART_SIZE = 32 * 1024


def verify_request(request, generation):
    if request.get('generation_id') != generation.get('generation_id'):
        raise XttsError('generation_conflict')
    source = generation['request']
    for key in ('text', 'model_id', 'profile_id', 'scope', 'target_chat_id', 'provider', 'delivery'):
        if source.get(key) != request.get(key):
            raise XttsError('generation_authority_mismatch')


async def upload(client, store, request, *, stopped=lambda: False, part_size=PART_SIZE):
    from telethon.tl import functions, types
    if part_size <= 0 or part_size % 1024 or 524288 % part_size:
        raise ValueError('invalid MTProto part size')
    identity = request['generation_id']
    file_id = secrets.randbits(63) or 1
    offset = 0
    index = 0
    attempt = None
    pending = bytearray()
    checksum = hashlib.sha256()

    def read_chunk(count):
        path = store.audio_path(identity)
        if path.is_symlink():
            raise XttsError('generation_integrity')
        try:
            with path.open('rb') as source:
                source.seek(offset)
                return source.read(count)
        except FileNotFoundError:
            return b''

    async def send_part(raw, total_parts):
        if stopped():
            raise XttsError('cancelled')
        accepted = await client(functions.upload.SaveBigFilePartRequest(
            file_id=file_id, file_part=index, file_total_parts=total_parts, bytes=raw))
        if not accepted:
            raise XttsError('stream_upload_refused')

    while True:
        if stopped():
            raise XttsError('cancelled')
        value = await asyncio.to_thread(store.read, identity)
        verify_request(request, value)
        state = value['state']
        if state in {'failed', 'cancel_requested', 'cancelled', 'unknown'}:
            raise XttsError('generation_' + state)
        if state in {'running', 'ready'} and attempt is None:
            attempt = value['attempt_id']
        if attempt is not None and value['attempt_id'] != attempt:
            # Never splice bytes from two synthesis attempts into one upload.
            raise XttsError('stream_restart')
        if state not in {'running', 'ready'}:
            await asyncio.sleep(.1)
            continue
        raw = await asyncio.to_thread(read_chunk, part_size - len(pending))
        if raw:
            offset += len(raw)
            if offset > MAX_AUDIO:
                raise XttsError('oversized_audio')
            checksum.update(raw)
            pending.extend(raw)
            if len(pending) == part_size:
                await send_part(bytes(pending), -1)
                index += 1
                pending.clear()
            continue
        if state != 'ready':
            await asyncio.sleep(.1)
            continue
        result = value['result']
        if (offset != result['bytes'] or checksum.hexdigest() != result['sha256']
                or offset <= 32):
            raise XttsError('generation_integrity')
        # Telegram requires a final empty part when EOF lies on a part boundary.
        count = math.ceil(offset / part_size)
        await send_part(bytes(pending), count)
        return types.InputMediaUploadedDocument(
            file=types.InputFileBig(id=file_id, parts=count, name='voice.ogg'),
            mime_type='audio/ogg', attributes=[
                types.DocumentAttributeFilename(file_name='voice.ogg'),
                types.DocumentAttributeAudio(duration=max(1, math.ceil(result.get('audio_seconds', 0))), voice=True),
            ])
