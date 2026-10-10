"""Python Telegram gateway transport. Configuration belongs to the active agent.

The gateway carries Bot API HTTPS requests and opaque MTProto bytes over WSS.
It never owns the Telegram session or the local message archive.
"""
from __future__ import annotations

import asyncio
import urllib.parse
import urllib.request


def settings(cfg: dict) -> tuple[str, str]:
    proxy = (cfg.get('telegram') or {}).get('proxy') or {}
    if not proxy.get('enabled'):
        return '', ''
    url = str(proxy.get('url') or '').strip().rstrip('/')
    key = str(proxy.get('key') or '').strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password \
            or parsed.query or parsed.fragment or not key:
        raise ValueError('Telegram proxy needs an HTTPS address and an access key')
    return url, key


def bot_request(req: urllib.request.Request, url: str, key: str) -> urllib.request.Request:
    path = urllib.parse.urlsplit(req.full_url).path
    token, sep, method = path.removeprefix('/bot').partition('/')
    if not sep or not method.isalpha():
        raise ValueError('Invalid Telegram method')
    return urllib.request.Request(url + '/bot/' + method, data=req.data,
               headers={'Content-Type': req.get_header('Content-type') or 'application/x-www-form-urlencoded',
                        'Authorization': 'Bearer ' + key, 'X-Telegram-Bot-Token': token})


def file_request(file_id: str, token: str, url: str, key: str) -> urllib.request.Request:
    return urllib.request.Request(url+'/file',data=urllib.parse.urlencode({'file_id':file_id}).encode(),
        headers={'Content-Type':'application/x-www-form-urlencoded','Authorization':'Bearer '+key,
                 'X-Telegram-Bot-Token':token})

def public_address(url: str) -> str:
    parsed=urllib.parse.urlsplit(url)
    if not parsed.hostname: return 'адрес не задан'
    host=parsed.hostname
    if ':' in host: host='['+host+']'
    if parsed.port: host+=':'+str(parsed.port)
    return urllib.parse.urlunsplit((parsed.scheme,host,parsed.path,'',''))

def connection_type(url: str, key: str):
    """Telethon's framed TCP stream, with its bytes carried through WSS."""
    import aiohttp
    from telethon.network.connection.tcpfull import ConnectionTcpFull

    class Reader:
        def __init__(self, ws):
            self.ws, self.buffer = ws, bytearray()

        async def readexactly(self, size):
            while len(self.buffer) < size:
                msg = await self.ws.receive()
                if msg.type != aiohttp.WSMsgType.BINARY:
                    raise asyncio.IncompleteReadError(bytes(self.buffer), size)
                self.buffer.extend(msg.data)
            result = bytes(self.buffer[:size])
            del self.buffer[:size]
            return result

    class Writer:
        def __init__(self, ws, session):
            self.ws, self.session, self.buffer = ws, session, bytearray()
            self.closing = None

        def write(self, data):
            self.buffer.extend(data)

        async def drain(self):
            if self.buffer:
                data = bytes(self.buffer)
                self.buffer.clear()
                await self.ws.send_bytes(data)

        def close(self):
            if self.closing is None:
                self.closing = asyncio.create_task(self._close())

        async def wait_closed(self):
            if self.closing is not None:
                await self.closing
            else:
                await self._close()

        async def _close(self):
            await self.ws.close()
            await self.session.close()

    class ConnectionGateway(ConnectionTcpFull):
        async def _connect(self, timeout=None, ssl=None):
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=timeout or 15),
                                            trust_env=False)
            try:
                ws = await session.ws_connect(url.replace('https://', 'wss://', 1) + '/connect',
                          headers={'Authorization': 'Bearer ' + key}, heartbeat=20,
                          max_msg_size=16 * 1024 * 1024)
                await ws.send_json({'host': self._ip, 'port': self._port})
                response = await asyncio.wait_for(ws.receive_json(), timeout or 15)
                if not response.get('ok'):
                    raise ConnectionError('Telegram gateway refused the connection')
                self._reader, self._writer = Reader(ws), Writer(ws, session)
                self._codec = self.packet_codec(self)
                self._init_conn()
                await self._writer.drain()
            except BaseException:
                await session.close()
                raise

    return ConnectionGateway
