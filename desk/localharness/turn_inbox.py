"""Same-room inbox batches at model boundaries. Files remain until checkpoint ACK.

One runner thread consumes inbox; this is not a second scheduler. A sidecar binds
claimed files to their run before checkpointing. Resume reuses that binding and
exact message bytes; unrelated rooms and unsealed bytes never enter the turn.
"""
from pathlib import Path
import json
import os


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
            body, attached = runner._split_attachments(text)
            message = {'role': 'user', 'content': '[Сообщение владельца; inbox ' + path.name + ']\n' + body
                       + ('\nВложения сохранены: ' + ', '.join(attached) if attached else '')}
            if path.parent == inbox: os.replace(path, target)
        else:
            blob = runner._note_bytes(path)
            valid, _why = runner._seal_claim(path, blob=blob)
            if valid is not True:
                continue
            text = runner._message_text(blob)
            body, attached = runner._split_attachments(text)
            # Exact source is durable before rename; attachment paths remain in source.
            message = {'role': 'user', 'content': '[Сообщение владельца; inbox ' + path.name + ']\n' + body
                       + ('\nВложения сохранены: ' + ', '.join(attached) if attached else '')}
            record = {'run_id': current.run_id, 'message': message}
            temp = Path(str(binding) + '.tmp')
            temp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, binding)
            os.replace(path, target)
            desk = runner._room(room)
            desk.archive(text, outgoing=False, now=runner._now(), source_id='note:' + path.stem)
            desk.life(text, direction='in', actor=runner._speaker, source_id='note:' + path.stem, now=runner._now())
        ready.append((target, message))
    additions = [m for _, m in ready if m not in messages]
    def ack():
        for path, _ in ready:
            runner._mark_done(processed, path.name, 'batched into checkpoint of ' + current.run_id)
    return additions, ack
