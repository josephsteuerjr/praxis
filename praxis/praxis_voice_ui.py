"""Owner MiniApp voice controls, backed by the existing local XTTS capability.

Preview jobs are short-lived UI requests. They have no Telegram transport and
never become a second delivery outbox, agent, scheduler or model owner.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from xtts_profiles import MAX_REFERENCE, VOICE_ID
from xtts_protocol import HEX, REQUEST_ID, XttsError, digest
from xtts_tts_client import XttsTTSClient

TTL=15*60
PREVIEW_MAX_AUDIO=2*1024*1024
ROUTES=(
    ('GET','/api/xtts/status','status',0),
    ('POST','/api/xtts/jobs','submit',12*1024*1024),
    ('GET','/api/xtts/jobs/{voice_job}','job',0),
    ('POST','/api/xtts/jobs/{voice_job}/cancel','cancel',4096),
    ('GET','/api/xtts/jobs/{voice_job}/audio','audio',0),
    ('POST','/api/xtts/enabled','enabled',4096),
    ('POST','/api/xtts/voices/{voice_id}/delete','delete',4096),
)


class VoiceUI:
    def __init__(self,base:Path,root:Path,*,client_factory=None):
        self.base,self.root=Path(base),Path(root)
        if self.root.is_symlink():raise XttsError('unavailable')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.client_factory=client_factory or (lambda timeout:XttsTTSClient(self.root,
            socket_path=os.environ.get('PRAXIS_XTTS_SOCKET') or self.base/'memory/.state/xtts/xtts.sock',
            token_file=os.environ.get('PRAXIS_XTTS_TOKEN_FILE') or self.base/'memory/.secrets/xtts-client.token',timeout=timeout))
        self.jobs={}
        self.early_cancel={}
        self.lock=threading.RLock()
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='voice-preview')
        self.closed=False
        self.prune_stop=threading.Event()
        self.prune()
        self.pruner=threading.Thread(target=self._prune_loop,name='voice-preview-cleanup',daemon=True)
        self.pruner.start()

    def _prune_loop(self):
        while not self.prune_stop.wait(60):
            try:self.prune()
            except OSError:pass

    def prune(self):
        with self.lock:
            now=time.time()
            self.early_cancel={k:v for k,v in self.early_cancel.items() if now-v<TTL}
            for key,row in list(self.jobs.items()):
                if row['state'] in {'ready','failed','cancelled'} and now-row['updated']>TTL:
                    if row.get('audio'):Path(row['audio']).unlink(missing_ok=True)
                    self.jobs.pop(key)
            retained={str(Path(row['audio'])) for row in self.jobs.values() if row.get('audio')}
            retained.update(str(self.root/(key+'.input')) for key,row in self.jobs.items()
                            if row['state'] in {'queued','working'})
            for path in self.root.iterdir():
                if path.is_symlink() or not path.is_file() or str(path) in retained:continue
                if path.suffix not in {'.ogg','.input'}:continue
                if now-path.stat().st_mtime>TTL:path.unlink(missing_ok=True)

    def status(self):
        self.prune()
        try:
            value=self.client_factory(3).voices()
            return {**value,'available':True}
        except XttsError as exc:
            return {'ok':True,'available':False,'phase':exc.code,'ready':False,
                    'model_ready':False,'enabled':False,'voices':[],'selected_voice':'','selection_revision':''}

    @staticmethod
    def actor(role):return 'owner:miniapp' if role=='owner' else 'owner-device:miniapp'

    def _row(self,identity):
        if not REQUEST_ID.fullmatch(str(identity or '')):raise XttsError('unknown_voice')
        row=self.jobs.get(identity)
        if row is None:raise XttsError('unknown_voice')
        return row

    def job(self,identity):
        self.prune()
        with self.lock:
            row=self._row(identity)
            return {'ok':True,'id':identity,'kind':row['kind'],'state':row['state'],
                    'phase':row.get('phase',row['state']),'error':row.get('error',''),
                    'voice_id':row.get('voice_id',''),'profile_id':row.get('profile_id',''),
                    'model_id':row.get('model_id',''),'audio_ready':row.get('audio') is not None}

    def _update(self,identity,**values):
        with self.lock:self._row(identity).update(updated=time.time(),**values)

    def submit(self,body,role):
        self.prune()
        if self.closed:raise XttsError('unavailable')
        kind=body.get('kind')
        if kind not in {'preview','add','select','enable','delete'}:raise XttsError('invalid_request')
        identity=body.get('request_id') or uuid.uuid4().hex
        if not REQUEST_ID.fullmatch(str(identity)):raise XttsError('invalid_request')
        data={'kind':kind,'actor':self.actor(role)}
        reference=None
        if kind=='add':
            name=body.get('name')
            encoded=body.get('data')
            if not isinstance(name,str) or not 1<=len(name.strip())<=80:raise XttsError('invalid_voice_name')
            if not isinstance(encoded,str) or len(encoded)>((MAX_REFERENCE+2)//3)*4:raise XttsError('reference_too_large')
            try:reference=base64.b64decode(encoded,validate=True)
            except (ValueError,UnicodeError) as exc:raise XttsError('invalid_reference') from exc
            if not 0<len(reference)<=MAX_REFERENCE:raise XttsError('reference_too_large')
            data.update(name=name.strip(),reference_sha256=hashlib.sha256(reference).hexdigest())
        elif kind in {'preview','select','delete'}:
            key=body.get('voice_id')
            if not isinstance(key,str) or not VOICE_ID.fullmatch(key):raise XttsError('unknown_voice')
            data['voice_id']=key
            for key in ([] if kind=='delete' else ['model_id','profile_id']):
                value=body.get(key)
                if not HEX.fullmatch(str(value or '')):raise XttsError('profile_changed')
                data[key]=value
            if kind=='preview':
                text=body.get('text')
                if not isinstance(text,str) or not 1<=len(text.strip())<=400:raise XttsError('invalid_text')
                data['text']=text
        if kind in {'select','enable'}:
            revision=body.get('expected_revision')
            if not isinstance(revision,str) or not 1<=len(revision)<=64:raise XttsError('config_changed')
            data['expected_revision']=revision
        fingerprint=digest(data)
        with self.lock:
            existing=self.jobs.get(identity)
            if existing is not None:
                if existing['fingerprint']!=fingerprint:raise XttsError('voice_conflict')
                return self.job(identity)
            if sum(r['state'] in {'queued','working'} for r in self.jobs.values())>=8:raise XttsError('busy')
            if reference is not None:
                source=self.root/(identity+'.input')
                with source.open('xb') as stream:stream.write(reference)
                source.chmod(0o600)
                data['input']=str(source)
            self.jobs[identity]={'kind':kind,'state':'queued','phase':'queued','updated':time.time(),
                                  'fingerprint':fingerprint,'stop':threading.Event(),'audio':None}
            if kind=='preview' and identity in self.early_cancel:
                self.jobs[identity].update(state='cancelled',phase='cancelled')
                return self.job(identity)
            self.pool.submit(self._run,identity,data)
        return self.job(identity)

    def _run(self,identity,data):
        with self.lock:stop=self._row(identity)['stop']
        deadline=time.monotonic()+620
        stopped=lambda:stop.is_set() or self.closed or time.monotonic()>=deadline
        client=self.client_factory(600)
        path=None
        try:
            while not stopped():
                self._update(identity,state='working',phase='preparing')
                try:
                    if data['kind']=='preview':
                        path,meta=client.preview({'id':data['voice_id'],'profile_id':data['profile_id'],
                                                  'model_id':data['model_id']},data['text'],stopped=stopped)
                        if path.stat().st_size>PREVIEW_MAX_AUDIO:raise XttsError('oversized_audio')
                        if stopped():raise XttsError('cancelled')
                        path.chmod(0o600)
                        self._update(identity,state='ready',phase='ready',audio=str(path),sha256=meta['sha256'],
                                     voice_id=data['voice_id'],profile_id=meta['profile_id'],model_id=meta['model_id'])
                        path=None
                    elif data['kind']=='add':
                        answer=client.add_voice(data['input'],data['name'],stopped=stopped,request_id=identity,
                                                attempt_id=uuid.uuid4().hex,actor=data['actor'])
                        self._update(identity,state='ready',phase='ready',voice_id=answer['voice_id'])
                    elif data['kind']=='delete':
                        client.command('voice_delete',{'voice_id':data['voice_id']},stopped=stopped,actor=data['actor'])
                        self._update(identity,state='ready',phase='ready',voice_id=data['voice_id'])
                    else:
                        values={k:data[k] for k in ['voice_id','profile_id','model_id','expected_revision'] if k in data}
                        values['operation_id']=identity
                        if data['kind']=='enable':values['enabled']=True
                        answer=client.command('voice_select' if data['kind']=='select' else 'voice_enabled',values,
                                               stopped=stopped,request_id=uuid.uuid4().hex,actor=data['actor'])
                        self._update(identity,state='ready',phase='ready',voice_id=answer.get('voice_id',''),profile_id=answer.get('profile_id',''))
                    return
                except XttsError as exc:
                    if exc.code not in {'busy','warming','awaiting_voice','unavailable'}:raise
                    self._update(identity,phase='busy' if exc.code=='busy' else 'warming')
                    stop.wait(.5)
            raise XttsError('cancelled' if stop.is_set() or self.closed else 'timeout')
        except XttsError as exc:
            self._update(identity,state='cancelled' if exc.code=='cancelled' else 'failed',phase='failed',error=exc.code)
        except Exception:
            self._update(identity,state='failed',phase='failed',error='unavailable')
        finally:
            if path is not None:path.unlink(missing_ok=True)
            if data.get('input'):Path(data['input']).unlink(missing_ok=True)

    def cancel(self,identity):
        with self.lock:
            if not REQUEST_ID.fullmatch(str(identity or '')):raise XttsError('unknown_voice')
            if identity not in self.jobs:
                if len(self.early_cancel)>=128:self.early_cancel.pop(next(iter(self.early_cancel)))
                self.early_cancel[identity]=time.time()
                return {'ok':True,'id':identity,'state':'cancelled'}
            row=self._row(identity)
            if row['kind']!='preview':raise XttsError('invalid_request')
            row['stop'].set()
            if row['state']=='ready':
                if row.get('audio'):Path(row['audio']).unlink(missing_ok=True)
                row.update(state='cancelled',phase='cancelled',audio=None,updated=time.time())
        return self.job(identity)

    def audio(self,identity):
        self.prune()
        with self.lock:
            row=self._row(identity)
            if row['state']!='ready' or not row.get('audio'):raise XttsError('unavailable')
            path=Path(row['audio'])
            if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):raise XttsError('unavailable')
            raw=path.read_bytes()
            if not 32<len(raw)<=PREVIEW_MAX_AUDIO or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                raise XttsError('unavailable')
            return {'ok':True,'mime':'audio/ogg','data':base64.b64encode(raw).decode('ascii'),
                    'bytes':len(raw),'sha256':row['sha256']}

    def enabled(self,body,role):
        if type(body.get('enabled')) is not bool:raise XttsError('invalid_request')
        if body['enabled']:return self.submit({**body,'kind':'enable'},role)
        self.client_factory(3).command('voice_enabled',{'enabled':False,'operation_id':body.get('request_id') or uuid.uuid4().hex},actor=self.actor(role))
        return {'ok':True,'enabled':False}

    def delete(self,identity,role,body=None):
        if not isinstance(identity,str) or not VOICE_ID.fullmatch(identity):raise XttsError('unknown_voice')
        return self.submit({'kind':'delete','voice_id':identity,'request_id':(body or {}).get('request_id') or uuid.uuid4().hex},role)

    def dispatch(self,action,params,body,role):
        try:
            if role not in {'owner','device'}:raise XttsError('unauthorized')
            if body is not None and not isinstance(body,dict):raise XttsError('invalid_request')
            body=body or {}
            if action=='status':return self.status()
            if action=='submit':return self.submit(body,role)
            if action=='job':return self.job(params.get('voice_job'))
            if action=='cancel':return self.cancel(params.get('voice_job'))
            if action=='audio':return self.audio(params.get('voice_job'))
            if action=='enabled':return self.enabled(body,role)
            if action=='delete':return self.delete(params.get('voice_id'),role,body)
            raise XttsError('invalid_request')
        except XttsError as exc:return {'ok':False,'error':exc.code}

    def close(self):
        self.closed=True
        self.prune_stop.set()
        self.pruner.join(2)
        with self.lock:
            for row in self.jobs.values():row['stop'].set()
        self.pool.shutdown(wait=True,cancel_futures=True)
        with self.lock:
            for identity,row in self.jobs.items():
                (self.root/(identity+'.input')).unlink(missing_ok=True)
                if row.get('audio'):Path(row['audio']).unlink(missing_ok=True)
