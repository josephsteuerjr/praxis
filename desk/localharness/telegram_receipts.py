"""Bot API acceptance receipts. An ambiguous HTTP result is never resent blindly."""
import hashlib
import json
import os
from pathlib import Path
import time


class UnknownDelivery(RuntimeError):
    pass


class BotApiDeliveryRefused(RuntimeError):
    permanent = True
    def __init__(self, code):
        self.code = int(code)
        super().__init__(f'Telegram permanently refused delivery ({self.code}); original text is retained')


def once(tree, key, identity, send):
    from localharness.package_notice import write
    root = Path(tree)/'memory/.state/telegram-receipts'
    root.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(str(key).encode()).hexdigest()
    path, lease = root/(name+'.json'), root/(name+'.sending')

    def previous():
        try: row = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError: return None
        except (OSError, ValueError) as exc:
            raise UnknownDelivery('Telegram delivery receipt is unreadable; inspect '+name) from exc
        if not isinstance(row, dict) or row.get('identity') != identity:
            raise UnknownDelivery('Telegram delivery identity differs from its receipt; inspect '+name)
        return row

    def accepted(row):
        result = row.get('result')
        if not isinstance(result, dict) or not result.get('message_id'):
            raise UnknownDelivery('Telegram acceptance receipt has no message ID; inspect '+name)
        return result

    row = previous()
    if row and row.get('state') == 'accepted': return accepted(row)
    if row and row.get('state') == 'refused': raise BotApiDeliveryRefused(row['code'])
    if row and row.get('state') != 'retryable':
        raise UnknownDelivery('Telegram acceptance is unknown; automatic resend suspended; receipt '+name)
    try:
        fd = os.open(lease, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise UnknownDelivery('Telegram delivery is already claimed; inspect '+name) from exc
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as held:
            held.write(str(os.getpid()));held.flush();os.fsync(held.fileno())
        # Recheck after acquiring the claim: a competing sender may have finished.
        row = previous()
        if row and row.get('state') == 'accepted': return accepted(row)
        if row and row.get('state') == 'refused': raise BotApiDeliveryRefused(row['code'])
        if row and row.get('state') != 'retryable':
            raise UnknownDelivery('Telegram acceptance is unknown; inspect '+name)
        record = {'schema':1, 'identity':identity, 'state':'sending', 'at':time.time()}
        write(path,record)  # durable intent BEFORE the HTTP side effect
        try:
            result = send()
        except Exception as exc:
            code = getattr(exc, 'code', None)
            if code == 429:
                write(path,{**record,'state':'retryable'})
            elif isinstance(code, int) and 400 <= code < 500:
                write(path,{**record,'state':'refused','code':code})
            else:
                # Keep the sending receipt even if reporting the error is interrupted.
                raise UnknownDelivery('Telegram HTTP result is unknown; receipt '+name) from exc
            raise
        if not isinstance(result, dict) or not result.get('message_id'):
            raise UnknownDelivery('Telegram returned no acceptance ID; receipt '+name)
        receipt = {k:result[k] for k in ('message_id','date') if k in result}
        write(path,{**record,'state':'accepted','result':receipt})
        return receipt
    finally:
        lease.unlink(missing_ok=True)
