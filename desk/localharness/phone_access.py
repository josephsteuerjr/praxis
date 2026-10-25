"""One supervised HTTPS tunnel for this channel; no second agent or VPS.

Only an explicitly saved phone.mode=automatic enables exposure. Telegram
keeps its existing signed initData/owner authorization; QR keys keep their
device scope. This module never stores or publishes bot/owner credentials.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

import botapi
import telegram_proxy
from phone_tunnel.cloudflare_tunnel import CloudflaredError, QuickTunnel, ensure_cloudflared
from phone_tunnel.platform_support import acquire_file_lock, release_file_lock


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def enabled(config: dict) -> bool:
    phone = config.get('phone') or {}
    return phone.get('enabled') is True and phone.get('mode') == 'automatic'


def migrate_config(path: Path, *, author: str) -> dict:
    """Explicit installer/operator migration, never a parse-time fallback."""
    raw = path.read_text(encoding='utf-8-sig')
    config = json.loads(raw)
    phone = config.get('phone') or {}
    if phone.get('mode') == 'automatic':
        return {'changed': False}
    before = dict(phone)
    phone = {**phone, 'mode': 'automatic'}
    phone.pop('external', None)
    config['phone'] = phone
    backup = path.with_name('helene.before-phone-1.5.1.json')
    if not backup.exists():
        backup.write_text(raw, encoding='utf-8')
    atomic_json(path, config)
    tree = Path(config.get('tree') or 'data')
    if not tree.is_absolute():
        tree = path.parent / tree
    atomic_json(tree / 'memory/.state/phone-migration.json', {
        'schema': 'helene.phone-migration.v1', 'at': time.time(), 'author': author,
        'before': before, 'after': phone,
        'reason': 'Телефон подключается через автоматический HTTPS-туннель; второй агент на сервере не нужен.',
        'rollback': str(backup),
    })
    return {'changed': True, 'backup': str(backup)}


class MenuOwner:
    """Update only our owner's button; keep its predecessor for disable/rollback."""
    def __init__(self, root: Path, client, chat_id: int, token: str):
        self.path = root / 'telegram-menu.json'
        self.client, self.chat_id = client, chat_id
        self.identity = hashlib.sha256(f'{token}:{chat_id}'.encode()).hexdigest()

    async def call(self, method: str, **params):
        return await asyncio.to_thread(self.client.call, method, _http_timeout=12, **params)

    def ledger(self) -> dict:
        try:
            row = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        if row.get('identity') != self.identity:
            raise ValueError('Бот или владелец изменился: сначала отключи прежний миниапп.')
        return row

    async def publish(self, url: str, label: str) -> None:
        current = await self.call('getChatMenuButton', chat_id=self.chat_id)
        row = self.ledger()
        if row and current not in (row.get('previous'), row.get('installed'), row.get('pending')):
            raise ValueError('Кнопка бота изменена отдельно: Hélène сохранила её и не перезаписывает.')
        if not row:
            chat = await self.call('getChat', chat_id=self.chat_id)
            if chat.get('type') != 'private' or int(chat.get('id') or 0) != self.chat_id:
                raise ValueError('Для миниаппа нужен личный чат владельца с ботом.')
            row = {'identity': self.identity, 'previous': current}
        target = {'type': 'web_app', 'text': label[:64] or 'Hélène', 'web_app': {'url': url + 'tg/'}}
        if current != target:
            row['pending'] = target
            atomic_json(self.path, row)  # reconcile even after an ambiguous timeout
            await self.call('setChatMenuButton', chat_id=self.chat_id, menu_button=target)
            if await self.call('getChatMenuButton', chat_id=self.chat_id) != target:
                raise ValueError('Telegram ещё не подтвердил кнопку — повторяю подключение.')
        row['installed'] = target
        row.pop('pending', None)
        atomic_json(self.path, row)

    async def restore(self) -> None:
        row = self.ledger()
        if not row:
            return
        current = await self.call('getChatMenuButton', chat_id=self.chat_id)
        if current in (row.get('installed'), row.get('pending')):
            await self.call('setChatMenuButton', chat_id=self.chat_id, menu_button=row['previous'])
            current = await self.call('getChatMenuButton', chat_id=self.chat_id)
        if current == row['previous'] or current not in (row.get('installed'),row.get('pending')):
            self.path.unlink(missing_ok=True)


class PhoneAccess:
    def __init__(self, root: Path, load_config, port: int, *, owner_token: bool):
        self.root, self.load_config, self.port = root, load_config, port
        self.owner_token = owner_token
        self.url = ''
        self.instance = secrets.token_hex(16)
        self.menu: MenuOwner | None = None
        self.tunnel = None
        self.task = None
        self.status = {'state': 'off', 'message': 'Подключение телефона выключено.'}
        self.retry_event = asyncio.Event()
        self.clock = time.monotonic

    def host(self) -> str:
        return urlsplit(self.url).hostname or ''

    def state(self) -> dict:
        return {**self.status, 'url': self.url, 'telegram_url': self.url + 'tg/' if self.url else ''}

    def record(self, state: str, message: str, **extra):
        self.status = {'state': state, 'message': message, **extra}
        atomic_json(self.root / 'status.json', {**self.state(), 'pid': os.getpid(), 'at': time.time()})

    def retry(self):
        self.retry_event.set()

    async def delay(self, seconds: float):
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.retry_event.wait(), seconds)
        self.retry_event.clear()

    async def cycle(self, config: dict):
        telegram=config.get('telegram') or {}
        owner=str(telegram.get('owner_id') or '')
        owner=str(int(owner)) if owner.isdigit() else owner
        identity=hashlib.sha256(f"{str(telegram.get('bot_token') or '').strip()}:{owner}".encode()).hexdigest()
        if self.menu is not None and self.menu.identity != identity:
            await self.menu.restore()
            self.menu=None
        if not self.owner_token:
            raise ValueError('Канал без ключа окна не открывается наружу. Перезапусти Hélène.')
        if os.name != 'nt' and os.getpgrp() != os.getpid():
            raise ValueError('Надзор не выделил каналу отдельную группу процессов — перезапусти Hélène.')
        self.record('preparing', 'Подготавливаю защищённое подключение телефона…')
        binary = await ensure_cloudflared(self.root)
        self.tunnel = QuickTunnel(binary, self.root, self.port)
        try:
            await self.tunnel.start()
            self.url = await self.tunnel.wait_url(45)
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8), trust_env=False) as session:
                for attempt in range(15):
                    try:
                        async with session.get(self.url + 'api/phone/probe') as response:
                            if response.status == 200 and (await response.json()).get('instance') == self.instance:
                                break
                    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                        pass
                    await asyncio.sleep(2)
                else:
                    raise CloudflaredError('HTTPS origin readiness not confirmed')
            self.record('ready', 'Телефон подключается из любой сети.', telegram='waiting')
            menu_retry = 0.0
            probe_at, probe_failures = self.clock() + 30, 0
            while enabled(self.load_config()) and self.tunnel.returncode is None:
                latest = self.load_config()
                if latest.get('telegram') != config.get('telegram'):
                    if self.menu:
                        await self.menu.restore()
                        self.menu = None
                    break
                if self.clock() >= probe_at:
                    if await self.public_ready():
                        probe_failures = 0
                        menu_retry = 0.0  # restore a verified readiness status now
                    else:
                        probe_failures += 1
                        self.record('checking', 'Проверяю HTTPS-адрес телефона; связь ещё не подтверждена.', telegram='waiting')
                        if probe_failures >= 3:
                            raise CloudflaredError('Public origin stopped answering')
                    probe_at = self.clock() + 20
                    if probe_failures:
                        await self.delay(2)
                        continue
                if self.clock() >= menu_retry:
                    telegram = config.get('telegram') or {}
                    token, owner = str(telegram.get('bot_token') or '').strip(), str(telegram.get('owner_id') or '')
                    if token and owner.isdigit() and int(owner) > 0:
                        if self.menu is None:
                            proxy_url, proxy_key = telegram_proxy.settings(config)
                            self.menu = MenuOwner(self.root, botapi.BotClient(token, proxy_url=proxy_url, proxy_key=proxy_key), int(owner), token)
                        try:
                            await self.menu.publish(self.url, str((config.get('agent') or {}).get('name') or 'Hélène'))
                            me = await self.menu.call('getMe')
                            bot_url = 'https://t.me/' + me['username'] if me.get('username') else ''
                            self.record('ready', 'Телефон подключён. Миниапп доступен кнопкой в твоём боте.', telegram='ready', bot_url=bot_url)
                            menu_retry = self.clock() + 60
                        except Exception as exc:
                            message = 'HTTPS готов. ' + str(exc) if isinstance(exc, ValueError) else 'HTTPS готов; Telegram пока не подтвердил кнопку. Повторяю автоматически.'
                            self.record('ready', message, telegram='retry')
                            menu_retry = self.clock() + 10
                    else:
                        self.record('ready', 'HTTPS готов. Для миниаппа укажи бота и id владельца в карточке Telegram.', telegram='unconfigured')
                        menu_retry = self.clock() + 30
                await self.delay(2)
            if self.tunnel.returncode is not None and enabled(self.load_config()):
                raise CloudflaredError('Tunnel disconnected')
        finally:
            self.url = ''
            if self.tunnel is not None:
                await self.tunnel.stop()
                self.tunnel = None

    async def public_ready(self) -> bool:
        """Process liveness alone does not prove the advertised DNS/HTTPS origin."""
        import aiohttp
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8), trust_env=False) as session:
                async with session.get(self.url + 'api/phone/probe') as response:
                    return response.status == 200 and (await response.json()).get('instance') == self.instance
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return False

    async def run(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = await asyncio.to_thread(acquire_file_lock, self.root / 'runtime.lock', timeout_sec=1)
        attempt = 0
        try:
            while True:
                config = self.load_config()
                if not enabled(config):
                    if self.menu:
                        try:
                            await self.menu.restore()
                        except Exception:
                            self.record('off','HTTPS выключен. Возвращаю прежнюю кнопку Telegram, когда появится связь.')
                            await self.delay(10)
                            continue
                        self.menu = None
                    self.record('off', 'Подключение телефона выключено.')
                    await self.delay(2)
                    continue
                try:
                    await self.cycle(config)
                    attempt = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    attempt += 1
                    reason = str(exc) if isinstance(exc, ValueError) else 'Не удалось открыть HTTPS-подключение. Проверяю сеть и повторяю.'
                    self.record('retry', reason, retry_in=min(60, 2 ** min(attempt, 6)))
                    await self.delay(min(60, 2 ** min(attempt, 6)))
        finally:
            self.url = ''
            self.record('off', 'Канал остановлен; телефон подключится после запуска Hélène.')
            release_file_lock(lock)

    async def context(self, app):
        self.task = asyncio.create_task(self.run())
        def stopped(task):
            if not task.cancelled() and task.exception():
                self.url = ''
                self.status = {'state': 'error', 'message': 'Подключение телефона остановилось. Перезапусти канал Hélène.'}
        self.task.add_done_callback(stopped)
        try:
            yield
        finally:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
