"""Same-room inbox batches at model boundaries. Files remain until checkpoint ACK.

One runner thread consumes inbox; this is not a second scheduler. A sidecar binds
claimed files to their run before checkpointing. Resume reuses that binding and
exact message bytes; unrelated rooms and unsealed bytes never enter the turn.
"""
from pathlib import Path
import json
import os


def _message(runner, name, text, messages, *, replay, current):
    prefix = '[Сообщение владельца; inbox ' + name + ']\n'
    if replay:
        # Only the run's checkpoint is authoritative for already prepared input.
        # Never trust derived text in the editable binding sidecar, and never
        # transcribe again after a crash between checkpoint and ACK.
        for message in messages:
            content = message.get('content')
            head = content if isinstance(content, str) else (
                content[0].get('text', '') if isinstance(content, list) and content
                and isinstance(content[0], dict) and content[0].get('type') == 'text' else '')
            if message.get('role') == 'user' and head.startswith(prefix):
                return message, head[len(prefix):]
    body, attached = runner._split_attachments(text)
    heard, remaining = runner._hear_attachments(attached) if attached else ([], [])
    if heard:
        body = (body + '\n' + '\n'.join(heard)).strip()
    # Слово владельца 01.10: вложения — папка хода. Зрение — только картинкам;
    # прочие файлы копируются в runs/<id>/files и называются путями, без
    # притворства, что модель их «видит».
    images = [p for p in remaining if Path(p).suffix.lower() in runner._IMAGE_EXT]
    files = [p for p in remaining if p not in images]
    if files:
        body = (body + '\n' + '\n'.join(
            runner._batch_files(files, run_id=current.run_id))).strip()
    if images:
        content, archive = runner._batch_images(
            prefix + body, images, room=str(current.delivery_chat_id),
            source_id='note:' + Path(name).stem, run_id=current.run_id)
        if archive.startswith(prefix):
            archive = archive[len(prefix):]
        return {'role': 'user', 'content': content}, archive
    content = body
    # The next turn reads life history: store the words we delivered, not just
    # the audio filename. Keep the original note on disk as source evidence.
    return {'role': 'user', 'content': prefix + content}, content if heard else text


def collect(runner, current, messages):
    if current is None:
        return [], lambda: None
    room = str(current.delivery_chat_id or '')
    if not runner.transport.is_room(room):
        return [], lambda: None
    inbox = runner._tree / 'memory' / '.control' / 'desk_inbox'
    processed = inbox / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    ready = []
    # Snapshot once: messages arriving later stay for the next boundary.
    paths = sorted(inbox.glob('*.md')) + sorted(processed.glob('*.md'))
    for path in paths:
        if path.name.startswith('.tmp-') or runner._inbox_target(path.stem) != room:
            continue
        target = processed / path.name
        if Path(str(target) + '.done').exists():
            continue
        binding = Path(str(target) + '.batch.json')
        if path.parent == processed and not binding.exists():
            continue  # ordinary idle ingress remains owned by its original flow
        if binding.exists():
            record = json.loads(binding.read_text(encoding='utf-8'))
            if record['run_id'] != current.run_id:
                continue
            blob = runner._note_bytes(target if target.exists() else path)
            valid, _why = runner._seal_claim(target, blob=blob)
            if valid is not True:
                continue
            text = runner._message_text(blob)
            message, _archive = _message(runner, path.name, text, messages, replay=True, current=current)
            if path.parent == inbox: os.replace(path, target)
        else:
            blob = runner._note_bytes(path)
            valid, _why = runner._seal_claim(path, blob=blob)
            if valid is not True:
                continue
            text = runner._message_text(blob)
            message, archive = _message(runner, path.name, text, messages, replay=False, current=current)
            # Exact source is durable before rename; attachment paths remain in source.
            record = {'run_id': current.run_id, 'message': message}
            temp = Path(str(binding) + '.tmp')
            temp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, binding)
            os.replace(path, target)
            desk = runner._room(room)
            desk.archive(archive, outgoing=False, now=runner._now(), source_id='note:' + path.stem)
            desk.life(archive, direction='in', actor=runner._speaker, source_id='note:' + path.stem, now=runner._now())
        ready.append((target, message))
    additions = [m for _, m in ready if m not in messages]
    def ack():
        for path, _ in ready:
            runner._mark_done(processed, path.name, 'batched into checkpoint of ' + current.run_id)
    return additions, ack
