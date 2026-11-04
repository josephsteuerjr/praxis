"""Owner/device speech API. Persistent preview identity, no Telegram transport."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

from fish_tts_client import FishError, FishProfile, FishTTSClient
from speech_backend import SpeechStore, describe
from xtts_generations import Generations, process_owner
from xtts_protocol import REQUEST_ID, XttsError, digest, file_digest


class FishVoiceUI:
    def __init__(self,base,root,*,client_factory=None):
        self.base,self.root=Path(base),Path(root)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.root.is_symlink():raise XttsError('unavailable')
        self.generations=Generations(self.root/'previews')
        self.settings=SpeechStore(self.base/'memory/.state/speech')
        self.client_factory=client_factory or (lambda profile:FishTTSClient(self.root,profile,
            key_file=self.base/'memory/.secrets/fish/api-key'))
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='fish-preview')
        self.closed=False
        self.generations.recover_lost()

    @staticmethod
    def _id(identity):
        if not isinstance(identity,str) or not REQUEST_ID.fullmatch(identity):raise XttsError('invalid_request_id')
        return digest({'run_id':'fish-ui-preview','call_id':identity})[:32]

    def status(self):
        # describe() uses the same PRAXIS_BASE as the agent; adapter passes it
        # through existing readers.tree() and sets no second voice configuration.
        result=describe(store=self.settings,client=self.client_factory(None))
        result['ok']=True
        result['checked_at']=time.time()
        return result

    def catalog(self,page=1,model='drama-3'):
        probe=self.client_factory(None)
        result=probe.voices(own=False,language='ru',page=page)
        rows=[]
        for value in result['voices']:
            if not isinstance(value.get('_id'),str):continue
            profile=FishProfile(value['_id'],model=model)
            rows.append({'id':profile.reference_id,'name':str(value.get('title') or profile.reference_id),
                         'provider':'fish','model':model,'kind':'cloud',**profile.selection(),
                         'languages':value.get('languages',[])})
        return {'ok':True,'voices':rows,'page':page,'has_more':result['has_more'],'model':model}

    def submit(self,body,role):
        if self.closed:raise XttsError('unavailable')
        identity=body.get('request_id');self._id(identity)
        profile=FishProfile(body.get('voice_id'),model=body.get('model','drama-3'))
        text=body.get('text')
        delivery=body.get('delivery','')
        if not isinstance(text,str) or not 1<=len(text.strip())<=400:raise XttsError('invalid_text')
        if not isinstance(delivery,str):raise XttsError('fish_invalid_delivery')
        value=self.generations.submit(text,run_id='fish-ui-preview',call_id=identity,
            channel={'chat_id':'owner-preview','scope':'owner','is_dm':True},
            selection=profile.selection(),delivery=delivery)
        if value['state']=='queued':self.pool.submit(self._run,value['generation_id'],profile)
        return self.job(identity)

    def _run(self,identity,profile):
        client=self.client_factory(profile)
        if self.closed:return
        self.generations.execute(identity,client,owner=process_owner())
        # No voice_generation_result event: this is an explicit UI preview,
        # not an agent turn or a second outbound path.

    def job(self,identity):
        self.generations.recover_lost()
        value=self.generations.read(self._id(identity))
        return {'ok':True,'id':identity,'state':value['state'],'error':value.get('error',''),
                'provider':'fish','audio_ready':value['state']=='ready',
                'delivery':'preview_only_no_telegram',
                'model_id':value['request']['model_id'],'profile_id':value['request']['profile_id']}

    def cancel(self,identity):
        self.generations.cancel(self._id(identity))
        result=self.job(identity)
        result['remote_cancel_confirmed']=False
        return result

    def audio(self,identity):
        value=self.generations.read(self._id(identity))
        if value['state']!='ready':raise XttsError('unavailable')
        path=self.generations.audio_path(value['generation_id'])
        if path.is_symlink() or file_digest(path)!=value['result']['sha256']:raise XttsError('generation_integrity')
        raw=path.read_bytes()
        return {'ok':True,'mime':'audio/ogg','data':base64.b64encode(raw).decode('ascii'),
                'bytes':len(raw),'sha256':value['result']['sha256']}

    def selection(self,body,role):
        provider=body.get('provider')
        profile=(FishProfile(body.get('voice_id'),model=body.get('model','drama-3'))
                 if provider=='fish' and body.get('voice_id') else None)
        return self.settings.mutate(provider=provider,profile=profile,enabled=body.get('enabled',True),
            expected_revision=body.get('expected_revision'),request_id=body.get('request_id'),
            actor='owner:miniapp' if role=='owner' else 'owner-device:miniapp')

    def dispatch(self,action,params,body,role):
        try:
            if role not in {'owner','device'}:raise XttsError('unauthorized')
            if body is not None and not isinstance(body,dict):raise XttsError('invalid_request')
            body=body or {}
            if action=='status':return self.status()
            if action=='catalog':return self.catalog(int(params.get('catalog_page','1')))
            if action=='submit':return self.submit(body,role)
            if action=='selection':return self.selection(body,role)
            if action=='job':return self.job(params.get('voice_job'))
            if action=='audio':return self.audio(params.get('voice_job'))
            if action=='cancel':return self.cancel(params.get('voice_job'))
            raise XttsError('invalid_request')
        except XttsError as exc:return {'ok':False,'error':exc.code}
        except (ValueError,TypeError):return {'ok':False,'error':'invalid_request'}
        except OSError:return {'ok':False,'error':'unavailable'}

    def close(self):
        self.closed=True
        for value in self.generations.list(states={'running'}):
            self.generations.cancel(value['generation_id'])
        # Canonical records survive shutdown; a missing producer becomes unknown.
        self.pool.shutdown(wait=False,cancel_futures=True)
