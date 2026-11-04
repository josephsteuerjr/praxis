"""Voice configuration/preview operations inside the resident capability."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time

from xtts_profiles import MAX_REFERENCE, write_config
from xtts_protocol import HEX, REQUEST_ID, XttsError


def actor(request):
    value=request.get('actor')
    return value if value in {'owner:miniapp','owner-device:miniapp','praxis:self','operator'} else 'operator'


def describe(server):
    store=server.voice_store
    if store is None:
        raise XttsError('unavailable')
    health=server.engine.health()
    rows=[]
    if health.get('model_ready'):
        store.set_builtins(health.get('speakers') or [])
        for row in store.records():
            spec=store.spec(row['id'],verify=False)
            rows.append({**row,'model_id':health['model_id'],
                         'profile_id':server.engine._profile_identity(spec,verify_reference=False)})
    chosen=store.selection()
    return {'ok':True,'voices':rows,'enabled':chosen['enabled'],
            'selection_revision':chosen.get('revision','legacy'),
            'selected_voice':server.engine.profile.get('voice_id',''),
            'generation':server.generation,'busy':server.inference_lock.locked(),
            **{k:v for k,v in health.items() if k!='speakers'}}


def resolve_profile(server, request):
    engine,store=server.engine,server.voice_store
    if not engine.weights_ready:
        raise XttsError(engine.error or engine.phase)
    store.set_builtins((engine.health().get('speakers') or []))
    if request.get('model_id') != engine.model_id:
        raise XttsError('profile_changed')
    identity=request.get('voice_id')
    if identity:
        spec=store.spec(identity)
        if engine._profile_identity(spec)!=request.get('profile_id'):
            raise XttsError('profile_changed')
        return spec
    # Previously admitted speech keeps its pinned voice after a new selection.
    for row in store.records():
        spec=store.spec(row['id'],verify=False)
        if engine._profile_identity(spec,verify_reference=False)==request.get('profile_id'):
            return store.spec(row['id'])
    if request.get('profile_id')==engine.profile_id:
        return dict(engine.profile)
    raise XttsError('profile_changed')


@contextmanager
def inference(server, request):
    if not server.inference_lock.acquire(blocking=False):
        raise XttsError('busy')
    identity=request['request_id']
    event=threading.Event()
    try:
        duration=request.get('timeout',300)
        if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not 0<duration<=600:
            raise XttsError('invalid_deadline')
        deadline=time.monotonic()+duration
        with server.active_lock:
            server.active[identity]=(event,deadline,time.monotonic())
            server.active_ops[identity]=request['operation']
            if identity in server.cancelled:event.set()
        stopped=lambda:event.is_set() or server.stopping.is_set() or time.monotonic()>=deadline
        if stopped():raise XttsError('cancelled')
        yield stopped
    finally:
        with server.active_lock:
            server.active.pop(identity,None)
            server.active_ops.pop(identity,None)
        server.inference_lock.release()


def disable(server, request):
    if server.voice_store is None:raise XttsError('unavailable')
    with server.config_lock:
        server.voice_store.disable(actor=actor(request),operation_id=request.get('operation_id') or request['request_id'])
        with server.active_lock:
            for identity,(event,_deadline,_started) in server.active.items():
                if server.active_ops.get(identity) in {'synthesize', 'synthesize_stream', 'voice_select', 'voice_enabled'}:
                    server.cancelled.setdefault(identity,time.monotonic())
                    event.set()
    return {'ok':True,'enabled':False}


def control(handler, request):
    server=handler.server
    store=server.voice_store
    if store is None:raise XttsError('unavailable')
    operation=request['operation']
    who=actor(request)
    with inference(server,request) as stopped:
        if operation=='voice_add':
            size=request.get('bytes')
            checksum=request.get('sha256')
            if type(size) is not int or not 0<size<=MAX_REFERENCE:
                raise XttsError('reference_too_large')
            if not HEX.fullmatch(str(checksum or '')):
                raise XttsError('invalid_reference')
            fd,name=tempfile.mkstemp(prefix='voice-upload-',suffix='.bin',dir=server.scratch)
            os.close(fd)
            source=Path(name)
            try:
                handler.send({'ok':True,'upload':True})
                received=0
                sha=hashlib.sha256()
                handler.request.settimeout(5)
                with source.open('wb') as output:
                    while received<size:
                        if stopped():raise XttsError('cancelled')
                        raw=handler.rfile.read(min(64*1024,size-received))
                        if not raw:raise XttsError('invalid_reference')
                        output.write(raw);sha.update(raw);received+=len(raw)
                    output.flush();os.fsync(output.fileno())
                if sha.hexdigest()!=checksum:raise XttsError('invalid_reference')
                canonical=request.get('voice_key_id') or request['request_id']
                if not REQUEST_ID.fullmatch(str(canonical)):raise XttsError('invalid_request_id')
                identity=store.add(source,identity=canonical,name=request.get('name'),
                                   upload_sha256=checksum,stopped=stopped,actor=who)
                return {'ok':True,'voice_id':identity}
            finally:source.unlink(missing_ok=True)
        if operation in {'voice_select','voice_enabled'}:
            if operation=='voice_select':
                spec=resolve_profile(server,request)
            else:
                key=server.engine.profile.get('voice_id')
                if not key:raise XttsError('voice_not_configured')
                spec=store.spec(key)
            identity=server.engine.prepare_voice(spec,stopped)
            with server.config_lock:
                if stopped():raise XttsError('cancelled')
                store.activate(spec,{'model_id':server.engine.model_id,'profile_id':identity},actor=who,
                               operation_id=request.get('operation_id') or request['request_id'],
                               expected_revision=request.get('expected_revision'))
                server.engine.set_active_voice(spec,identity)
            return {'ok':True,'voice_id':spec['voice_id'],'enabled':True,'profile_id':identity}
        if operation=='voice_delete':
            identity=request.get('voice_id')
            with server.config_lock:
                current=server.engine.profile.get('voice_id','')
                if identity==current:
                    if store.selection()['enabled']:raise XttsError('active_voice')
                    blank={'speaker':'','reference':'','language':'ru'}
                    write_config(store.prefix/'profile.json',blank,actor=who)
                    server.engine.cache.pop(server.engine.profile_id,None)
                    server.engine.warmed_profiles.discard(server.engine.profile_id)
                    server.engine.set_active_voice(blank,'')
                    current=''
                store.delete(identity,current,actor=who)
            return {'ok':True,'deleted':identity}
    raise XttsError('invalid_operation')
