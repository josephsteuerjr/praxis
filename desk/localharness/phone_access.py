"""One supervised HTTPS tunnel for this channel; no second agent or VPS.

Only an explicitly saved automatic or stable phone mode enables exposure. Telegram
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
import phone_entry
from phone_tunnel.cloudflare_tunnel import CloudflaredError, QuickTunnel, ensure_cloudflared
from phone_tunnel.platform_support import acquire_file_lock, release_file_lock


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def enabled(config: dict) -> bool:
    phone = config.get('phone') or {}
    return phone.get('enabled') is True and phone.get('mode') in ('automatic','stable')


async def probe_origin(session,url,instance,*,proxy=None):
    """Distinguish a valid TLS gateway with no upstream from certificate errors."""
    import aiohttp
    try:
        async with session.get(url+'api/phone/probe',**({'proxy':proxy} if proxy else {})) as response:
            if response.status in (502,503,504):
                return False,f'HTTPS-сервер доступен, но туннель к этому компьютеру не отвечает (HTTP {response.status}).'
            if response.status!=200:return False,f'Адрес телефона отвечает HTTP {response.status}. Проверь настройку подключения.'
            if (await response.json()).get('instance')!=instance:
                return False,'Этот HTTPS-адрес ведёт к другой копии Hélène. Проверь адрес и локальный порт туннеля.'
            return True,''
    except aiohttp.ClientConnectorCertificateError:
        return False,'Не удалось подтвердить сертификат HTTPS-адреса. Проверь сертификат сервера.'
    except aiohttp.ClientConnectorDNSError:
        return False,'Не удалось найти HTTPS-адрес через DNS. Проверяю сеть и повторяю.'
    except asyncio.TimeoutError:
        return False,'HTTPS-адрес не ответил вовремя. Проверяю сеть и повторяю.'
    except (aiohttp.ClientError,ValueError):
        return False,'HTTPS-подключение пока не подтвердилось. Проверяю сеть и повторяю.'


def stable_origin(config:dict)->str:
    phone=config.get('phone') or {}
    if phone.get('mode')!='stable':return ''
    value=str(phone.get('external') or '').strip()
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('Укажи постоянный HTTPS-адрес телефона без пути и ключей.')
    return value.rstrip('/')+'/'


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
        url = phone_entry.origin(url)
        current = await self.call('getChatMenuButton', chat_id=self.chat_id)
        row = self.ledger()
        if row and current not in (row.get('previous'), row.get('installed'), row.get('pending')):
            raise ValueError('Кнопка бота изменена отдельно: Hélène сохранила её и не перезаписывает.')
        if not row:
            if current.get('type') == 'web_app':
                raise ValueError('У этого бота уже есть миниапп другой установки. Для каждого локального агента подключи отдельного бота.')
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
        self.retry_requested = False
        self.authorization = {'state':'unknown','message':'Вход владельца ещё не проверен открытием Mini App.'}

    def host(self) -> str:
        return urlsplit(self.url).hostname or ''

    def transfer_pending(self) -> bool:
        """A frozen local publisher cannot race the moved server's bot menu."""
        try:row=json.loads((self.root.parent/'server-transfer.json').read_text(encoding='utf-8'))
        except FileNotFoundError:return False
        except (OSError,ValueError):return True
        return not isinstance(row,dict) or row.get('phase')!='server-stopped'

    def state(self) -> dict:
        import psutil
        from deskd.telegram_http import connection_identity
        bot={'state':'unknown','message':'Приём сообщений движком ещё не подтверждён.'}
        try:
            row=json.loads((self.root.parent/'telegram-connection.json').read_text(encoding='utf-8'))
            if row.get('identity')==connection_identity(self.load_config()) and time.time()-float(row.get('at') or 0)<90:
                if abs(psutil.Process(int(row.get('pid') or 0)).create_time()-float(row.get('process_started') or 0))<0.01:
                    bot={**row,'message':row.get('error') or ('Приём сообщений работает.' if row.get('state')=='ready' else 'Подключение бота…')}
        except (OSError,ValueError,TypeError,psutil.Error): pass
        status=self.status
        https={'state':'ready' if status['state']=='ready' else status['state'],
               'message':'Публичный адрес отвечает и ведёт к этому агенту.' if status['state']=='ready' else status['message'],
               'last_attempt':status.get('at'), 'retry_at':status.get('retry_at',0)}
        menu={'state':status.get('telegram','waiting'),'message':status.get('menu_error') or
              ('Адрес кнопки прочитан обратно из Telegram.' if status.get('telegram')=='ready' else 'Кнопка ещё не подтверждена.'),
              'last_attempt':status.get('at'),'retry_at':status.get('menu_retry_at',0)}
        auth=self.authorization
        if auth.get('identity') and auth['identity']!=connection_identity(self.load_config()):
            auth={'state':'unknown','message':'Подключение изменилось; открой Mini App для проверки входа.'}
        return {**status, 'url': self.url, 'telegram_url': self.url + 'tg/' if self.url else '',
                'checks':{'bot':bot,'https':https,'menu':menu,'authorization':auth}}

    def record(self, state: str, message: str, **extra):
        now=time.time()
        self.status = {'state': state, 'message': message, 'at':now,
                       'retry_at':now+extra['retry_in'] if extra.get('retry_in') else 0, **extra}
        atomic_json(self.root / 'status.json', {**self.state(), 'pid': os.getpid(), 'at': time.time(),
                    'entry_identity': self.menu.identity if self.menu and extra.get('telegram') == 'ready' else ''})

    def retry(self):
        self.retry_requested = True
        atomic_json(self.root.parent/'telegram-retry.json',{'at':time.time()})
        self.retry_event.set()

    def authorized(self):
        from deskd.telegram_http import connection_identity
        self.authorization={'state':'ready','message':'Подпись Telegram и личность владельца проверены.',
                            'last_attempt':time.time(),'identity':connection_identity(self.load_config())}

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
        fixed=stable_origin(config)
        if not fixed:
            binary = await ensure_cloudflared(self.root,network_config=config)
            self.tunnel = QuickTunnel(binary, self.root, self.port,network_config=config)
        try:
            if fixed:self.url=fixed
            else:
                await self.tunnel.start()
                try:self.url = await self.tunnel.wait_url(45)
                except CloudflaredError:
                    if getattr(self.tunnel,'connection_error',''):raise ValueError(self.tunnel.connection_error) from None
                    raise
            import aiohttp
            from deskd.network_route import Route
            route=Route(config)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8), trust_env=False) as session:
                reason='HTTPS-подключение пока не подтвердилось.'
                for attempt in range(15):
                    confirmed,reason=await probe_origin(session,self.url,self.instance,proxy=route.proxy_for(self.url))
                    if confirmed:break
                    self.record('checking',reason,attempt=attempt+1)
                    await asyncio.sleep(2)
                else:
                    raise ValueError(getattr(self.tunnel,'connection_error','') or reason)
            self.record('ready', 'Телефон подключается из любой сети.', telegram='waiting')
            menu_retry = 0.0
            probe_at, probe_failures = self.clock() + 30, 0
            while enabled(self.load_config()) and not self.transfer_pending() and (self.tunnel is None or self.tunnel.returncode is None):
                latest = self.load_config()
                if self.retry_requested:
                    self.retry_requested=False
                    menu_retry=0.0
                    probe_at=0.0
                if latest.get('phone')!=config.get('phone'):break
                if latest.get('network')!=config.get('network'):break
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
                            raise ValueError(getattr(self.tunnel,'connection_error','') or 'Публичный HTTPS-адрес перестал отвечать. Повторяю подключение.')
                    probe_at = self.clock() + 20
                    if probe_failures:
                        await self.delay(2)
                        continue
                if self.clock() >= menu_retry:
                    telegram = config.get('telegram') or {}
                    token, owner = str(telegram.get('bot_token') or '').strip(), str(telegram.get('owner_id') or '')
                    if token and owner.isdigit() and int(owner) > 0:
                        if self.menu is None:
                            self.menu = MenuOwner(self.root, botapi.BotClient.from_config(config), int(owner), token)
                        try:
                            await self.menu.publish(self.url, str((config.get('agent') or {}).get('name') or 'Hélène'))
                            me = await self.menu.call('getMe')
                            import re
                            username = str(me.get('username') or '')
                            bot_url = 'https://t.me/' + username + '?start=helene' if re.fullmatch(r'[A-Za-z0-9_]+', username) else ''
                            self.record('ready', 'Миниапп подключён. Открой меню своего бота или отправь /app для свежей кнопки.', telegram='ready', bot_url=bot_url)
                            menu_retry = self.clock() + 60
                        except Exception as exc:
                            message = 'HTTPS готов. ' + str(exc) if isinstance(exc, ValueError) else 'HTTPS готов; Telegram пока не подтвердил кнопку. Повторяю автоматически.'
                            self.record('ready', message, telegram='retry',menu_error=message,menu_retry_at=time.time()+10)
                            menu_retry = self.clock() + 10
                    else:
                        self.record('ready', 'HTTPS готов. Для миниаппа укажи бота и id владельца в карточке Telegram.', telegram='unconfigured')
                        menu_retry = self.clock() + 30
                await self.delay(2)
            if self.tunnel is not None and self.tunnel.returncode is not None and enabled(self.load_config()):
                raise CloudflaredError('Tunnel disconnected')
        finally:
            self.url = ''
            self.record('checking', 'Подключение меняется. Открой меню бота после восстановления связи.', telegram='waiting')
            if self.menu:
                with contextlib.suppress(Exception):
                    await self.menu.restore()
            if self.tunnel is not None:
                await self.tunnel.stop()
                self.tunnel = None

    async def public_ready(self) -> bool:
        """Process liveness alone does not prove the advertised DNS/HTTPS origin."""
        import aiohttp
        from deskd.network_route import Route
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8), trust_env=False) as session:
                async with session.get(self.url + 'api/phone/probe',proxy=Route(self.load_config()).proxy_for(self.url) or None) as response:
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
                if not enabled(config) or self.transfer_pending():
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
            if self.menu:
                with contextlib.suppress(Exception):
                    await self.menu.restore()
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
