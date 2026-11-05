"""Fresh Mini App entry consumed by the existing bot transport, no poller.

Only recently probed bot+owner-scoped origins are published. Old message
buttons are snapshots; /app resolves the current registration instead.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit


def identity(token: str, owner: str | int) -> str:
    return hashlib.sha256(f'{token.strip()}:{int(owner)}'.encode()).hexdigest()


def origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')
            or parsed.port not in (None, 443)):
        raise ValueError('Для миниаппа нужен HTTPS-адрес без пути и ключей.')
    return value.rstrip('/') + '/'


def current(tree: Path, config: dict, *, now: float | None = None) -> str:
    phone, tg = config.get('phone') or {}, config.get('telegram') or {}
    if phone.get('enabled') is not True or phone.get('mode') not in ('automatic', 'stable'):
        return ''
    try:
        row = json.loads((tree / 'memory/.state/phone-access/status.json').read_text(encoding='utf-8'))
        age = (time.time() if now is None else now) - float(row['at'])
        if (not 0 <= age <= 90 or row.get('state') != 'ready' or row.get('telegram') != 'ready'
                or row.get('entry_identity') != identity(str(tg.get('bot_token') or ''), tg.get('owner_id') or 0)):
            return ''
        return origin(str(row['url'])) + 'tg/'
    except (OSError, ValueError, KeyError, TypeError):
        return ''


def handle(transport, update: dict, message: dict) -> bool:
    text = str(message.get('text') or '').strip()
    command = re.fullmatch(r'/(app|start)(?:@([A-Za-z0-9_]+))?(?:\s+(helene))?', text, re.I)
    if not command or (command[1].lower() == 'app' and command[3]):
        return False
    config = getattr(transport, '_cfg', None)
    if not isinstance(config, dict) or (config.get('telegram') or {}).get('mode') == 'account':
        return False  # unconfigured transports and MTProto cannot publish bot buttons
    if command[2] and command[2].lower() != str(transport.username).lower():
        return True
    chat, sender = message.get('chat') or {}, message.get('from') or {}
    if (chat.get('type') != 'private' or str(sender.get('id') or '') != str(transport.owner_id)
            or str(chat.get('id') or '') != str(transport.owner_id)
            or not re.fullmatch(r'[1-9]\d*', str(transport.owner_id)) or update.get('_catchup') or update.get('_history')
            or update.get('_outgoing') or 'edited_message' in update or update.get('_edited')
            or not 0 <= time.time() - float(message.get('date') or 0) < 120):
        return True
    # Claim before send. Never automatically repeat an ambiguous send; the
    # owner can explicitly request another button with /app.
    from deskd import autonomy
    path = transport.tree / 'memory/.control/miniapp-telegram-seen.json'
    ident = identity(str((transport._cfg.get('telegram') or {}).get('bot_token') or ''), transport.owner_id)
    seen = autonomy._read(path)
    source = int(message.get('message_id') or 0)
    if source <= 0 or (seen.get('identity') == ident and source <= int(seen.get('message_id') or 0)):
        return True
    autonomy._write(path, {'identity': ident, 'message_id': source})
    url = current(transport.tree, transport._cfg)
    params = {'chat_id': transport.owner_id, 'text': 'Mini App сейчас недоступен. Проверь, что Hélène запущена, Mini App включён и есть связь. Затем отправь /app ещё раз.'}
    if url:
        name = str((transport._cfg.get('agent') or {}).get('name') or 'Hélène')[:48]
        params.update(text=f'{name} · свежий вход. При смене адреса открывай меню бота или отправляй /app.',
                      reply_markup={'inline_keyboard': [[{'text': f'Открыть {name}', 'web_app': {'url': url}}]]})
    try:
        transport.client.call('sendMessage', **params)
    except Exception:
        # Transport errors may include credentials; do not propagate to logs.
        pass
    return True
