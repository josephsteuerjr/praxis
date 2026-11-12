"""Authenticated MiniApp presentation of the existing desktop controller."""
from . import readers
from .phone_settings import _owner, SettingsError


def session(identity=None):
    path = readers.config_path()
    if path is None:
        raise SettingsError('Сеанс программы недоступен', 503)
    descriptor, current = _owner(path.resolve(), readers.tree().resolve())
    if identity is not None and (not isinstance(identity, str) or not identity or len(identity) > 100):
        raise SettingsError('Неверный агент')
    return descriptor, identity or current


def channel(identity=None, *, ready=False):
    from localharness import session_host
    descriptor, identity = session(identity)
    return session_host._rpc(descriptor, 'program_channel', {'agent': identity, 'ready': ready}, timeout=45)


def invoke(body):
    if not isinstance(body, dict) or not isinstance(body.get('command'), str) or not isinstance(body.get('arguments', {}), dict):
        raise SettingsError('Нужны команда программы и её параметры')
    from localharness import session_host
    descriptor, identity = session(body.get('agent'))
    command = body['command']
    arguments = body.get('arguments', {})
    if command in ('program_bootstrap', 'switch_agent'):
        selected = arguments.get('id') if command == 'switch_agent' else identity
        value = channel(selected, ready=True)
        # Host addresses and host keys never leave this server. The browser
        # uses a same-origin authenticated gateway for every selected agent.
        from urllib.parse import quote
        return {**{key: value[key] for key in value if key not in ('base', 'key')},
                'base': '/program/' + quote(value['agent_id'], safe='')}
    return session_host._rpc(descriptor, 'invoke',
        {'agent': identity, 'command': command, 'arguments': arguments, 'presentation': 'miniapp'}, timeout=180)


async def gateway(request):
    """Forward authenticated programme traffic to the registered agent only."""
    import asyncio
    from urllib.parse import urlsplit, urlencode
    from aiohttp import web, ClientSession, ClientTimeout, WSMsgType
    import deskapp
    if deskapp._role(request) != 'owner':
        raise web.HTTPForbidden(text='Нужен вход владельца в программу')
    tail = '/' + request.match_info['tail']
    if (not (tail.startswith('/api/') or tail in ('/tunnel', '/events', '/pair/devices', '/pair/revoke'))
            or any(part in ('.', '..') for part in tail.split('/')) or '\\' in tail
            or tail.startswith('/api/program/')):
        raise web.HTTPNotFound()
    try:
        target = await asyncio.to_thread(channel, request.match_info['agent'])
    except (OSError, ValueError, RuntimeError) as error:
        raise web.HTTPServiceUnavailable(text=str(error)) from error
    base = str(target.get('base') or '').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise web.HTTPBadGateway(text='Адрес канала программы недоступен')
    query = urlencode([(key, value) for key, value in request.query.items() if key != 'key'])
    url = base + tail + ('?' + query if query else '')
    headers = {'Authorization': 'Bearer ' + str(target.get('key') or '')}
    for name in ('Content-Type', 'Range', 'If-None-Match'):
        if name in request.headers: headers[name] = request.headers[name]
    async with ClientSession(timeout=ClientTimeout(total=None, sock_connect=20), trust_env=False) as client:
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            if tail != '/tunnel': raise web.HTTPBadRequest()
            async with client.ws_connect(url, headers=headers, max_msg_size=90*1024*1024) as upstream:
                downstream = web.WebSocketResponse(max_msg_size=90*1024*1024)
                await downstream.prepare(request)
                async def relay(source, sink):
                    async for message in source:
                        if message.type == WSMsgType.TEXT: await sink.send_str(message.data)
                        elif message.type == WSMsgType.BINARY: await sink.send_bytes(message.data)
                        else: break
                tasks = [asyncio.create_task(relay(downstream, upstream)), asyncio.create_task(relay(upstream, downstream))]
                try: await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks: task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await downstream.close()
                return downstream
        async with client.request(request.method, url, headers=headers, data=request.content, allow_redirects=False) as response:
            if 300 <= response.status < 400 and response.status != 304:
                raise web.HTTPBadGateway(text='Канал предложил перенаправление; проверь адрес агента')
            selected = {key: value for key, value in response.headers.items()
                        if key.lower() in ('content-type', 'content-disposition', 'content-range', 'accept-ranges', 'etag')}
            result = web.StreamResponse(status=response.status, headers={**selected, 'Cache-Control': 'no-store'})
            await result.prepare(request)
            async for chunk in response.content.iter_chunked(64*1024): await result.write(chunk)
            await result.write_eof()
            return result
