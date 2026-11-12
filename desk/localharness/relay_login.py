"""Owner-facing subscription operations. Never return the relay's raw status."""
import re
import time
from urllib.parse import urlsplit

SLOTS = ('primary', 'secondary')
DEVICE_URL = 'https://auth.openai.com/codex/device'


def login_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        return None
    try:
        url = urlsplit(value)
        if (url.scheme == 'https' and url.netloc == 'auth.openai.com'
                and not url.username and not url.password and not url.fragment):
            return value
    except ValueError:
        pass
    return None


def public_state(state, *, browser=True):
    """Allowlist only; device_auth_id, credentials and control key stay private."""
    raw = state.get('login') or {}
    pending = raw.get('pending') is True and raw.get('slot') in SLOTS
    method = raw.get('method') if raw.get('method') in ('browser', 'device_code') else 'browser'
    login = {'pending': pending, 'slot': raw.get('slot') if raw.get('slot') in SLOTS else None,
             'method': method, 'id': None, 'url': None, 'device_code': None, 'expired': False,
             'error': str(raw.get('error') or '')[:1000] or None}
    if pending:
        ident = raw.get('id')
        if isinstance(ident, str) and re.fullmatch(r'[a-fA-F0-9-]{32,36}', ident):
            login['id'] = ident
        if method == 'browser':
            login['url'] = login_url(state.get('login_url'))
        else:
            prompt = state.get('device_code') or {}
            code, expiry = prompt.get('user_code'), prompt.get('expires_at')
            login['expired'] = type(expiry) in (int, float) and expiry <= time.time()
            if (prompt.get('verification_url') == DEVICE_URL and isinstance(code, str)
                    and re.fullmatch(r'[A-Za-z0-9 -]{1,64}', code)
                    and type(expiry) in (int, float) and time.time() < expiry <= time.time() + 960):
                login['device_code'] = {'user_code': code, 'expires_at': expiry, 'verification_url': DEVICE_URL}
                login['url'] = DEVICE_URL
    return {'available': True, 'installed': True, 'browser_login': browser,
            'port': state.get('port'), 'login': login,
            'slots': [{'slot': name, 'stored': row.get('configured') is True,
                       'active': row.get('active') is True}
                      for name in SLOTS
                      for row in [next((a for a in state.get('accounts', []) if a.get('slot') == name), {})]]}


def action(agent, body, *, browser=True, timeout_sec=20):
    from .python_relay import control
    if not isinstance(body, dict):
        raise ValueError('Нужен объект действия подписки')
    name, slot = body.get('action'), body.get('slot', 'primary')
    if slot not in SLOTS:
        raise ValueError('Неизвестный слот подписки')
    if name not in ('status', 'login', 'logout', 'switch', 'cancel_login'):
        raise ValueError('Недоступное действие подписки')
    args = {'slot': slot}
    if name == 'login':
        method = body.get('method', 'device_code')
        if method not in ('device_code', 'browser'):
            raise ValueError('Выбери вход по коду или в браузере')
        if method == 'browser' and not browser:
            raise ValueError('На сервере используй вход по коду; подтвердить его можно на телефоне или ПК')
        name = 'login_device' if method == 'device_code' else 'login'
    elif name == 'cancel_login':
        ident = body.get('login_id')
        if not isinstance(ident, str) or not re.fullmatch(r'[a-fA-F0-9-]{32,36}', ident):
            raise ValueError('Перечитай состояние входа перед отменой')
        name, args = 'cancel_login_id', {'slot': ident}
    elif name == 'status':
        args = {}
    state = control(agent, name, timeout_sec=timeout_sec, **args)
    return public_state(state, browser=browser)


def account_view(state):
    login = state.get('login') or {}
    return {'reachable': state.get('available') is True, 'port': state.get('port'),
            'note': state.get('note'), 'browser_login': state.get('browser_login', True), 'login': login,
            'active_slot': next((a['slot'] for a in state.get('slots', []) if a.get('active')), None),
            'slots': [dict(slot=a['slot'], stored=a['stored'], loaded=a['stored'], serving=a['active'],
                           pending=bool(login.get('pending') and login.get('slot') == a['slot']),
                           login_error=login.get('error') if login.get('slot') == a['slot'] else None)
                      for a in state.get('slots', [])]}
