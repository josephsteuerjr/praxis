"""Explicit speech provider selection, immutable profiles and per-request routing.

The provider has no Telegram authority. Absent new configuration preserves the
existing XTTS selection; switching providers is a recorded, explicit mutation.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import threading
import time
import uuid

from fish_tts_client import FishError, FishProfile, FishTTSClient
from xtts_protocol import HEX, REQUEST_ID, XttsError, digest, encoded

_LOCK = threading.RLock()


def root():
    base=Path(os.environ.get('PRAXIS_BASE') or Path(__file__).resolve().parent)
    return base/'memory/.state/speech'


def _read(path):
    if path.is_symlink():raise XttsError('invalid_selection')
    try:
        value=json.loads(path.read_bytes())
        if not isinstance(value,dict):raise XttsError('invalid_selection')
        return value
    except (OSError,ValueError,UnicodeError):raise XttsError('invalid_selection') from None


def _write(path,value):
    if path.is_symlink():raise XttsError('invalid_selection')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=path.parent/('.speech-'+uuid.uuid4().hex)
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(encoded(value));stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
        if os.name=='posix':
            directory=os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
            try:os.fsync(directory)
            finally:os.close(directory)
    finally:temporary.unlink(missing_ok=True)


class SpeechStore:
    def __init__(self,base=None):self.root=Path(base) if base is not None else root()

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.root.is_symlink():raise XttsError('invalid_selection')
        if (self.root/'.lock').is_symlink():raise XttsError('invalid_selection')
        with _LOCK,(self.root/'.lock').open('a+b') as lock:
            if os.name=='posix':
                import fcntl
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            try:yield
            finally:
                if os.name=='posix':fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

    def _config(self):
        path=self.root/'selection.json'
        if not path.exists() and not path.is_symlink():return None
        value=_read(path)
        if (value.get('schema')!='praxis.speech.selection.v1' or
                value.get('provider') not in {'fish','xtts'} or type(value.get('enabled')) is not bool or
                not isinstance(value.get('revision'),str)):
            raise XttsError('invalid_selection')
        last=value.get('last_operation')
        if last is not None and (not isinstance(last,dict) or
                not REQUEST_ID.fullmatch(str(last.get('request_id') or '')) or
                not HEX.fullmatch(str(last.get('fingerprint') or '')) or
                not isinstance(last.get('result'),dict)):
            raise XttsError('invalid_selection')
        return value

    def state(self):
        config=None
        with self.locked():
            config=self._config()
            if config is not None:
                self._recover_operation(config)
                if config['provider']=='fish':return {k:v for k,v in config.items() if k!='last_operation'}
        from xtts_tts_client import selected as xtts_selected
        legacy=xtts_selected()
        return {**{k:v for k,v in (legacy or {}).items() if k not in {'schema','revision'}},
                'schema':'praxis.speech.selection.v1','provider':'xtts',
                'enabled':legacy is not None and (config is None or config['enabled']),
                'revision':config['revision'] if config else 'legacy-xtts'}

    def _directory(self,name):
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.root.is_symlink():raise XttsError('invalid_selection')
        path=self.root/name
        if path.is_symlink():raise XttsError('invalid_selection')
        path.mkdir(exist_ok=True,mode=0o700)
        return path

    def profile(self,identity):
        if not isinstance(identity,str) or not HEX.fullmatch(identity):raise XttsError('invalid_profile')
        profile=FishProfile.load(self._directory('profiles')/(identity+'.json'))
        if profile.profile_id!=identity:raise XttsError('profile_changed')
        return profile

    def retain_profile(self,profile):
        path=self._directory('profiles')/(profile.profile_id+'.json')
        if path.exists():
            if _read(path)!=asdict(profile):raise XttsError('profile_changed')
        else:_write(path,asdict(profile))

    def _recover_operation(self,config):
        last=config.get('last_operation')
        if last:
            path=self._directory('operations')/(last['request_id']+'.json')
            if not path.exists():_write(path,last)
            self._audit(config,last['request_id'])

    def _audit(self,config,identity):
        path=self.root/'events.jsonl'
        if path.is_symlink():raise XttsError('invalid_selection')
        if path.exists():
            with path.open('rb') as source:
                for line in source:
                    try:event=json.loads(line)
                    except ValueError:raise XttsError('invalid_selection') from None
                    if event.get('request_id')==identity:return
        value={k:v for k,v in config.items() if k!='last_operation'}
        with path.open('ab') as stream:
            stream.write(encoded({'kind':'speech_selection','request_id':identity,
                                  'actor':value['actor'],'at':value['at'],'result':value})+b'\n')
            stream.flush();os.fsync(stream.fileno())

    def mutate(self,*,provider=None,profile=None,enabled=True,expected_revision=None,
               request_id,actor):
        if not isinstance(request_id,str) or not REQUEST_ID.fullmatch(request_id):raise XttsError('invalid_request_id')
        if type(enabled) is not bool or provider not in {None,'fish','xtts'}:raise XttsError('invalid_selection')
        if not isinstance(actor,str) or not actor:raise XttsError('missing_generation_provenance')
        intent={'provider':provider,'profile':asdict(profile) if profile else None,'enabled':enabled,
                'expected_revision':expected_revision,'actor':actor}
        fingerprint=digest(intent)
        with self.locked():
            current=self._config()
            if current is not None:self._recover_operation(current)
            receipt_path=self._directory('operations')/(request_id+'.json')
            if receipt_path.exists():
                receipt=_read(receipt_path)
                if receipt['fingerprint']!=fingerprint:raise XttsError('voice_conflict')
                return receipt['result']
            revision=current['revision'] if current else 'legacy-xtts'
            if expected_revision is not None and expected_revision!=revision:raise XttsError('config_changed')
            chosen=provider or (current['provider'] if current else 'xtts')
            new={'schema':'praxis.speech.selection.v1','provider':chosen,'enabled':enabled,
                 'revision':uuid.uuid4().hex,'at':time.time(),'actor':actor}
            if chosen=='fish':
                if profile is None:
                    if not current or current['provider']!='fish':raise XttsError('voice_not_configured')
                    profile=self.profile(current['profile_id'])
                self.retain_profile(profile)
                new.update(profile.selection(),reference_id=profile.reference_id,model=profile.model)
            result={**new,'ok':True}
            receipt={'request_id':request_id,'fingerprint':fingerprint,'result':result}
            # The last operation is part of the same atomic configuration write.
            # Every reader/mutator repairs its receipt before accepting new work.
            _write(self.root/'selection.json',{**new,'last_operation':receipt})
            _write(receipt_path,receipt)
            self._audit(new,request_id)
            return result


def selected():
    store=SpeechStore()
    with store.locked():config=store._config()
    if config is None or config['provider']=='xtts':
        if config is not None and not config['enabled']:return None
        from xtts_tts_client import selected as xtts_selected
        return xtts_selected()
    if not config['enabled']:return None
    profile=store.profile(config['profile_id'])
    if profile.model_id!=config.get('model_id'):raise XttsError('profile_changed')
    return {**profile.selection(),'reference_id':profile.reference_id,'model':profile.model}


class RoutedClient:
    def __init__(self,client):self.client=client
    def __getattr__(self,name):return getattr(self.client,name)
    def stream_intent(self,intent,destination,*,stopped=lambda:False):
        return self.client.stream_intent(intent,destination,
            stopped=lambda:stopped() or selected() is None)


def client_for(request,output_dir,*,timeout=None):
    provider=request.get('provider','xtts')
    if provider=='xtts':
        from xtts_tts_client import XttsTTSClient
        return RoutedClient(XttsTTSClient(output_dir,timeout=timeout))
    if provider!='fish':raise XttsError('invalid_selection')
    profile=SpeechStore().profile(request['profile_id'])
    if profile.model_id!=request.get('model_id'):raise XttsError('profile_changed')
    return RoutedClient(FishTTSClient(output_dir,profile,timeout=30 if timeout is None else timeout))


def describe(*,with_catalog=False,page=1,language='ru',store=None,client=None):
    state=(store or SpeechStore()).state()
    client=client or FishTTSClient(root())
    result={'selection':state,'providers':['xtts','fish'],
            'fish':{'configured':client.key_file.is_file(),'models':['drama-3','s2.1-pro'],
                    'directions':'Free-form delivery. Inline [directions] and Drama 3 paired word tags.',
                    'remote_cancel_supported':False}}
    if result['fish']['configured']:
        try:
            result['fish'].update(client.balance(),authenticated=True)
            if with_catalog:result['fish']['catalog']=client.voices(own=False,language=language,page=page)
        except FishError as exc:result['fish'].update(authenticated=False,error=exc.code)
    return result
