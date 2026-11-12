"""Pinned cloudflared adapter: quick-service HTTP and opaque edge TCP via the app proxy.

cloudflared 2026.7.2 does not use ProxyFromEnvironment in either path. Keep TLS
between cloudflared and Cloudflare; these listeners never terminate that TLS.
Only fixed Cloudflare destinations are reachable. All listeners are loopback.
"""
import asyncio
import base64
import contextlib
import secrets
import ssl
from urllib.parse import urlsplit,unquote

QUICK_SERVICE='https://api.trycloudflare.com/tunnel'
EDGES=('region1.v2.argotunnel.com','region2.v2.argotunnel.com')


class CloudflareProxy:
    def __init__(self,route):
        self.route=route
        self.servers=[]
        self.tasks=set()
        self.runner=None
        self.session=None
        self.arguments=[]
        self.error=''

    async def start(self):
        import aiohttp
        from aiohttp import web
        self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),trust_env=False)
        secret=secrets.token_urlsafe(24)
        app=web.Application(client_max_size=1024)
        app.router.add_post('/'+secret+'/tunnel',self.bootstrap)
        self.runner=web.AppRunner(app,access_log=None)
        try:
            await self.runner.setup()
            site=web.TCPSite(self.runner,'127.0.0.1',0);await site.start()
            port=site._server.sockets[0].getsockname()[1]
            self.arguments=['--quick-service',f'http://127.0.0.1:{port}/{secret}','--no-prechecks']
            for host in EDGES:
                server=await asyncio.start_server(lambda r,w,target=host:self.edge(r,w,target),'127.0.0.1',0,limit=16384)
                self.servers.append(server)
                self.arguments+=['--edge',f'127.0.0.1:{server.sockets[0].getsockname()[1]}']
        except BaseException:
            await self.stop();raise

    async def bootstrap(self,request):
        from aiohttp import web
        if request.content_length not in (None,0) or request.headers.get('Transfer-Encoding'):
            raise web.HTTPBadRequest()
        try:
            async with self.session.post(QUICK_SERVICE,data=b'',allow_redirects=False,
                    headers={'Content-Type':'application/json','User-Agent':'Helene-Cloudflared'},
                    proxy=self.route.proxy_for(QUICK_SERVICE) or None) as response:
                body=bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    body.extend(chunk)
                    if len(body)>65536: raise ValueError('oversized quick-service response')
                return web.Response(body=body,status=response.status,content_type='application/json')
        except asyncio.CancelledError: raise
        except Exception:
            self.error='Не удалось получить автоматический HTTPS-адрес через выбранное подключение.'
            raise web.HTTPBadGateway(text=self.error) from None

    async def connect(self,host):
        proxy=self.route.proxy_for('https://'+host+':7844')
        if not proxy: return await asyncio.open_connection(host,7844)
        parsed=urlsplit(proxy)
        tls=ssl.create_default_context() if parsed.scheme=='https' else None
        reader,writer=await asyncio.open_connection(parsed.hostname,parsed.port or (443 if tls else 80),ssl=tls,limit=16384)
        try:
            authorization=''
            if parsed.username is not None:
                plain=unquote(parsed.username)+':'+unquote(parsed.password or '')
                authorization='Proxy-Authorization: Basic '+base64.b64encode(plain.encode()).decode('ascii')+'\r\n'
            writer.write((f'CONNECT {host}:7844 HTTP/1.1\r\nHost: {host}:7844\r\n'+authorization+'\r\n').encode('ascii'))
            await writer.drain()
            header=await reader.readuntil(b'\r\n\r\n')
            status=header.split(b'\r\n',1)[0].split()
            if len(status)<2 or status[1]!=b'200':
                self.error='Прокси не разрешил подключение туннеля Cloudflare. Проверь CONNECT к порту 7844 на сервере.'
                raise ConnectionError('Cloudflare proxy CONNECT refused')
            return reader,writer
        except BaseException:
            writer.close()
            with contextlib.suppress(Exception): await asyncio.wait_for(writer.wait_closed(),2)
            raise

    async def edge(self,reader,writer,host):
        task=asyncio.current_task()
        upstream=None
        pumps=[]
        if len(self.tasks)>=16:
            writer.close();return
        self.tasks.add(task)
        try:
            remote,upstream=await asyncio.wait_for(self.connect(host),20)
            async def copy(source,target):
                while block:=await source.read(65536):
                    target.write(block);await target.drain()
            pumps=[asyncio.create_task(copy(reader,upstream)),asyncio.create_task(copy(remote,writer))]
            await asyncio.wait(pumps,return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError: raise
        except Exception:
            if not self.error: self.error='Соединение Cloudflare через прокси прервалось; повторяю подключение.'
        finally:
            for pump in pumps: pump.cancel()
            await asyncio.gather(*pumps,return_exceptions=True)
            for stream in (writer,upstream):
                if stream:
                    stream.close()
                    with contextlib.suppress(Exception): await asyncio.wait_for(stream.wait_closed(),2)
            self.tasks.discard(task)

    async def stop(self):
        for server in self.servers: server.close()
        running=list(self.tasks)
        for task in running: task.cancel()
        await asyncio.gather(*running,return_exceptions=True)
        # Python 3.13+ wait_closed also waits for accepted connections. Close
        # those before awaiting the listeners, or stopping an active edge hangs.
        for server in self.servers: await server.wait_closed()
        self.servers=[]
        if self.runner: await self.runner.cleanup();self.runner=None
        if self.session: await self.session.close();self.session=None
