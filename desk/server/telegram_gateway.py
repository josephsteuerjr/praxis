"""Authenticated, Telegram-only Python gateway, behind a TLS reverse proxy.

Run with HELENE_TG_GATEWAY_KEY_FILE pointing at an owner-only access-key file.
The HTTP listener binds to loopback. No credentials or Telegram payloads are logged.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import quote

from aiohttp import web, ClientSession, ClientTimeout, ClientError, WSMsgType

# Telegram's published IPv4/IPv6 network ranges; never permit arbitrary proxying.
# https://core.telegram.org/resources/cidr.txt
NETWORKS = tuple(map(ipaddress.ip_network, ('149.154.160.0/20', '91.108.4.0/22',
    '91.108.8.0/22', '91.108.12.0/22', '91.108.16.0/22', '91.108.20.0/22',
    '91.108.56.0/22', '91.105.192.0/23', '185.76.151.0/24', '2001:b28:f23d::/48',
    '2001:b28:f23f::/48', '2001:67c:4e8::/48', '2001:b28:f23c::/48', '2a0a:f280::/32')))


def telegram_target(host, port) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return int(port) in (80, 443, 5222) and any(ip in net for net in NETWORKS)
    except (ValueError, TypeError):
        return False


def application(key: str, prefix: str = '/helene/telegram') -> web.Application:
    @web.middleware
    async def authorize(request, handler):
        supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
        if not key or not hmac.compare_digest(supplied.encode(), key.encode()):
            raise web.HTTPUnauthorized()
        return await handler(request)

    app = web.Application(middlewares=[authorize], client_max_size=64 * 1024 * 1024)
    sessions = []

    async def start(app):
        sessions.append(ClientSession(timeout=ClientTimeout(total=360, sock_connect=15), trust_env=False))

    async def close(app):
        await sessions.pop().close()

    async def bot(request):
        method = request.match_info['method']
        token = request.headers.get('X-Telegram-Bot-Token', '')
        if not re.fullmatch(r'[A-Za-z]+', method) or not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
            raise web.HTTPBadRequest()
        try:
            async with sessions[0].post('https://api.telegram.org/bot' + token + '/' + method,
                     data=await request.read(), headers={'Content-Type': request.content_type
                     if not request.headers.get('Content-Type') else request.headers['Content-Type']}) as result:
                return web.Response(body=await result.read(), status=result.status,
                                    headers={'Content-Type': result.headers.get('Content-Type', 'application/json')})
        except (ClientError, OSError, asyncio.TimeoutError):
            raise web.HTTPBadGateway(text='Telegram connection unavailable')

    async def connect(request):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)
        writer = None
        tasks = []
        try:
            target = await asyncio.wait_for(ws.receive_json(), 10)
            if not telegram_target(target.get('host'), target.get('port')):
                await ws.send_json({'ok': False})
                return ws
            reader, writer = await asyncio.wait_for(asyncio.open_connection(target['host'], int(target['port'])), 15)
            await ws.send_json({'ok': True})

            async def upstream():
                async for msg in ws:
                    if msg.type != WSMsgType.BINARY:
                        break
                    writer.write(msg.data)
                    await writer.drain()

            async def downstream():
                while data := await reader.read(65536):
                    await ws.send_bytes(data)

            tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, asyncio.TimeoutError, ValueError, TypeError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await ws.close()
        return ws

    async def file(request):
        token=request.headers.get('X-Telegram-Bot-Token','')
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+',token): raise web.HTTPBadRequest()
        data=await request.post()
        ident=str(data.get('file_id') or '')
        if not ident or len(ident)>4096: raise web.HTTPBadRequest()
        try:
            async with sessions[0].post('https://api.telegram.org/bot'+token+'/getFile',json={'file_id':ident}) as response:
                info=await response.json()
                if response.status!=200 or info.get('ok') is not True: raise web.HTTPBadGateway(text='Telegram did not provide the attachment')
            details=info.get('result') or {}
            path=str(details.get('file_path') or '')
            if not re.fullmatch(r'[A-Za-z0-9_./-]+',path) or path.startswith('/') or '..' in path.split('/'):
                raise web.HTTPBadGateway(text='Telegram returned an invalid attachment path')
            if int(details.get('file_size') or 0)>20*1024*1024: raise web.HTTPRequestEntityTooLarge(max_size=20*1024*1024,actual_size=int(details['file_size']))
            async with sessions[0].get('https://api.telegram.org/file/bot'+token+'/'+quote(path,safe='/'),allow_redirects=False) as response:
                if response.status!=200: raise web.HTTPBadGateway(text='Telegram attachment unavailable')
                payload=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    payload.extend(chunk)
                    if len(payload)>20*1024*1024: raise web.HTTPRequestEntityTooLarge(max_size=20*1024*1024,actual_size=len(payload))
                return web.Response(body=bytes(payload),content_type='application/octet-stream',headers={'Cache-Control':'no-store'})
        except (ClientError,OSError,asyncio.TimeoutError,ValueError,TypeError):
            raise web.HTTPBadGateway(text='Telegram attachment connection unavailable')

    app.on_startup.append(start)
    app.on_cleanup.append(close)
    app.router.add_post(prefix + '/bot/{method}', bot)
    app.router.add_post(prefix + '/file',file)
    app.router.add_get(prefix + '/connect', connect)
    async def health(request): return web.json_response({'ok': True,'capabilities':['bot','file','mtproto']})
    app.router.add_get(prefix + '/health', health)
    return app


if __name__ == '__main__':
    key = Path(os.environ['HELENE_TG_GATEWAY_KEY_FILE']).read_text().strip()
    web.run_app(application(key), host='127.0.0.1', port=int(os.environ.get('HELENE_TG_GATEWAY_PORT', '8099')),
                access_log=None, print=None)
