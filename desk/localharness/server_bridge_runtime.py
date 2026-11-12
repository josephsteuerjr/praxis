"""Application-owned loopback SSH forwards, with bounded workers and reconnect.

Port/trust/custody principles from Jarvis's tray-bridge donor: never adopt an
occupied port, never relax a host pin, and only close connections owned here.
Paramiko runs inside the existing application so password/encrypted-key profiles
work on every desktop without a second tray or console process.
"""
from __future__ import annotations
import contextlib
import json
import select
import socket
import threading
import time
from pathlib import Path
import paramiko

from . import server_connections,server_transfer


def close_stream(stream):
    if stream is not None:
        with contextlib.suppress(OSError,EOFError,paramiko.SSHException):stream.close()


def ports(value):
    names=('local_proxy_port','remote_proxy_port','local_app_port','remote_app_port')
    result={}
    for name in names:
        raw=value.get(name)
        if raw is None:continue
        if isinstance(raw,bool) or not str(raw).isdigit():raise ValueError('Порт моста должен быть числом 1–65535.')
        try:port=int(raw)
        except (ValueError,TypeError):raise ValueError('Порт моста должен быть числом 1–65535.') from None
        if not 1<=port<=65535:raise ValueError('Порт моста должен быть числом 1–65535.')
        result[name]=port
    if bool(result.get('local_proxy_port'))!=bool(result.get('remote_proxy_port')):
        raise ValueError('Для прокси нужны локальный и серверный порты.')
    if bool(result.get('local_app_port'))!=bool(result.get('remote_app_port')):
        raise ValueError('Для HTTPS нужны локальный порт приложения и серверный порт моста.')
    if not result:raise ValueError('Выбери подключение через прокси или постоянный HTTPS.')
    if result.get('remote_proxy_port') and result.get('remote_proxy_port')==result.get('remote_app_port'):
        raise ValueError('На сервере прокси и HTTPS-мосту нужны разные порты.')
    return result


class Bridge:
    def __init__(self,connection,configuration,*,state_dir=None):
        self.connection=dict(connection);self.ports=ports(configuration)
        self.state_dir=Path(state_dir) if state_dir else None
        self.stop_event=threading.Event();self.retry_event=threading.Event()
        self.thread=None;self.lock=threading.RLock();self.client=None;self.listener=None
        self.streams=set();self.workers=set();self.semaphore=threading.BoundedSemaphore(32)
        self._state={'state':'off','message':'Мост выключен.'}

    def state(self):
        with self.lock:return dict(self._state)

    def record(self,state,message,**extra):
        with self.lock:self._state={'state':state,'message':message,'at':time.time(),**extra}

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():return
            self.stop_event.clear();self.thread=threading.Thread(target=self.run,name='helene-server-bridge',daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set();self.retry_event.set();self.disconnect()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(25)
            if self.thread.is_alive():raise RuntimeError('Остановка серверного моста ещё не подтверждена.')

    def retry(self):self.retry_event.set()

    def disconnect(self):
        with self.lock:
            client,self.client=self.client,None
            listener,self.listener=self.listener,None
            streams=list(self.streams)
        if listener:
            close_stream(listener)
        for stream in streams:
            close_stream(stream)
        close_stream(client)
        with self.lock:workers=list(self.workers)
        deadline=time.monotonic()+2
        for worker in workers:
            if worker is not threading.current_thread():worker.join(max(0,deadline-time.monotonic()))

    def _worker(self,stream,opener):
        if self.stop_event.is_set() or not self.semaphore.acquire(blocking=False):
            close_stream(stream);return
        def transfer():
            other=None
            try:
                with self.lock:self.streams.add(stream)
                if self.stop_event.is_set():return
                other=opener()
                with self.lock:self.streams.add(other)
                while not self.stop_event.is_set():
                    ready,_,_=select.select([stream,other],[],[],1)
                    for source in ready:
                        packet=source.recv(65536)
                        target=other if source is stream else stream
                        if not packet:return
                        target.sendall(packet)
            except (OSError,EOFError,paramiko.SSHException):pass
            finally:
                for peer in (stream,other):
                    if peer:
                        close_stream(peer)
                        with self.lock:self.streams.discard(peer)
                with self.lock:self.workers.discard(threading.current_thread())
                self.semaphore.release()
        thread=threading.Thread(target=transfer,name='helene-bridge-stream',daemon=True)
        with self.lock:self.workers.add(thread)
        try:thread.start()
        except RuntimeError:
            with self.lock:self.workers.discard(thread)
            close_stream(stream);self.semaphore.release();raise

    def connect(self):
        # Bind before authenticating, so a foreign local listener is never used
        # as evidence of this bridge or displaced while a connection is built.
        listener=None;client=None
        try:
            if 'local_proxy_port' in self.ports:
                listener=socket.socket()
                if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
                else:
                    # POSIX keeps accepted connections in TIME_WAIT after our
                    # listener closes. Rebind our loopback address on retry;
                    # SO_REUSEPORT is deliberately absent, so a live listener
                    # on the same address still refuses before SSH auth.
                    listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                listener.bind(('127.0.0.1',self.ports['local_proxy_port']));listener.listen(16);listener.settimeout(.25)
            def own(client):
                with self.lock:
                    if self.stop_event.is_set():raise OSError('bridge stopped')
                    self.client=client
            client=server_transfer.connect(self.connection,on_client=own)
            transport=client.get_transport();transport.set_keepalive(15)
            if 'remote_app_port' in self.ports:
                def incoming(channel,_origin,_server):
                    self._worker(channel,lambda:socket.create_connection(('127.0.0.1',self.ports['local_app_port']),timeout=10))
                allocated=transport.request_port_forward('127.0.0.1',self.ports['remote_app_port'],handler=incoming)
                if allocated!=self.ports['remote_app_port']:raise RuntimeError('SSH подтвердил другой порт HTTPS-моста.')
            with self.lock:
                if self.stop_event.is_set():raise OSError('bridge stopped')
                self.listener=listener;self.client=client
            return transport
        except BaseException:
            close_stream(listener);close_stream(client)
            raise

    def run(self):
        lease=None;attempt=0
        try:
            if self.state_dir:
                from .phone_tunnel.platform_support import acquire_file_lock
                self.state_dir.mkdir(parents=True,exist_ok=True)
                lease=acquire_file_lock(self.state_dir/'runtime.lock',timeout_sec=.2)
            while not self.stop_event.is_set():
                self.record('connecting','Подключаю серверный мост…')
                try:
                    transport=self.connect()
                    self.record('connected','SSH-мост подключён; доступ через прокси и HTTPS ещё нужно проверить.')
                    while not self.stop_event.is_set() and transport.is_active():
                        listener=self.listener
                        if listener:
                            try:peer,address=listener.accept()
                            except socket.timeout:continue
                            self._worker(peer,lambda address=address,transport=transport:transport.open_channel('direct-tcpip',
                                ('127.0.0.1',self.ports['remote_proxy_port']),address,timeout=10))
                        else:self.stop_event.wait(.25)
                    attempt=0
                except Exception as error:
                    # Error objects may contain credentials or server output.
                    message=('Локальный порт занят или недоступен. Выбери свободный порт; чужое подключение не изменено.'
                        if isinstance(error,OSError) and getattr(error,'errno',None) in (48,98,10048,10013)
                        else 'Мост не подключился. Проверь SSH-доступ, отпечаток и разрешённые пробросы.')
                    self.record('retry',message,error_type=type(error).__name__,retry_at=time.time()+min(30,2**min(attempt,5)))
                finally:self.disconnect()
                if self.stop_event.is_set():break
                self.retry_event.wait(min(30,2**min(attempt,5)));self.retry_event.clear();attempt+=1
        except Exception as error:
            self.record('error','Этот мост уже используется другой копией приложения или его состояние недоступно.',error_type=type(error).__name__)
        finally:
            self.disconnect()
            if lease is not None:
                from .phone_tunnel.platform_support import release_file_lock
                release_file_lock(lease)
            if self.stop_event.is_set():self.record('off','Мост остановлен.')
