from pathlib import Path
from types import SimpleNamespace
import base64
import hashlib
import tempfile
import threading
import time
import unittest
import uuid

from praxis_voice_ui import VoiceUI
from xtts_protocol import XttsError


class PreviewUI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.calls=[]
        self.hold=threading.Event()
        outer=self
        class Client:
            def voices(self):return {'ok':True,'voices':[],'ready':True,'enabled':False}
            def preview(self,voice,text,stopped):
                outer.calls.append(('preview',voice,text))
                while outer.hold.is_set() and not stopped():time.sleep(.01)
                if stopped():raise XttsError('cancelled')
                path=outer.root/'cache'/('preview-'+uuid.uuid4().hex+'.ogg')
                raw=b'OggS'+b'\0'*24+b'OpusHead'+b'\0'*30
                path.write_bytes(raw)
                return path,{'sha256':hashlib.sha256(raw).hexdigest(),'profile_id':voice['profile_id'],'model_id':voice['model_id']}
            def command(self,*args,**kwargs):outer.calls.append(('command',args,kwargs));return {'ok':True}
            def add_voice(self,path,name,**kwargs):
                outer.calls.append(('add',Path(path).read_bytes(),name,kwargs))
                if len([c for c in outer.calls if c[0]=='add'])==1:raise XttsError('busy')
                return {'ok':True,'voice_id':'clone-'+kwargs['request_id']}
        self.ui=VoiceUI(self.root,self.root/'cache',client_factory=lambda timeout:Client())
        self.addCleanup(self.ui.close)

    def body(self,**extra):
        return {'kind':'preview','request_id':uuid.uuid4().hex,'voice_id':'builtin-'+'a'*20,
                'profile_id':'b'*64,'model_id':'c'*64,'text':'Exact text.',**extra}

    def wait(self,identity):
        until=time.monotonic()+4
        while time.monotonic()<until:
            row=self.ui.job(identity)
            if row['state'] in {'ready','cancelled','failed'}:return row
            time.sleep(.01)
        self.fail('UI job did not finish')

    def test_private_preview_audio_cannot_select_or_send_and_is_erased_on_cancel(self):
        body=self.body();self.ui.submit(body,'device')
        self.assertEqual(self.wait(body['request_id'])['state'],'ready')
        audio=self.ui.audio(body['request_id'])
        raw=base64.b64decode(audio['data'],validate=True)
        self.assertEqual(audio['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertNotIn('path',audio)
        self.assertEqual([call[0] for call in self.calls],['preview'])
        self.ui.cancel(body['request_id'])
        with self.assertRaises(XttsError):self.ui.audio(body['request_id'])
        self.assertFalse(list((self.root/'cache').glob('*.ogg')))

    def test_cancel_before_admission_never_starts_model(self):
        body=self.body();self.ui.cancel(body['request_id']);self.ui.submit(body,'owner')
        self.assertEqual(self.wait(body['request_id'])['state'],'cancelled')
        self.assertEqual(self.calls,[])

    def test_duplicate_id_is_same_intent_or_conflict(self):
        body=self.body();self.ui.submit(body,'owner');self.ui.submit(body,'owner')
        with self.assertRaisesRegex(XttsError,'voice_conflict'):
            self.ui.submit({**body,'text':'Different'},'owner')
        self.wait(body['request_id'])
        self.assertEqual(len(self.calls),1)

    def test_upload_retries_with_same_identity_and_new_transport_attempt(self):
        identity=uuid.uuid4().hex
        self.ui.submit({'kind':'add','request_id':identity,'name':'Voice','data':base64.b64encode(b'RIFF input').decode()},'device')
        self.assertEqual(self.wait(identity)['state'],'ready')
        calls=[c for c in self.calls if c[0]=='add']
        self.assertEqual(len(calls),2)
        self.assertEqual([c[3]['request_id'] for c in calls],[identity,identity])
        self.assertNotEqual(calls[0][3]['attempt_id'],calls[1][3]['attempt_id'])
        self.assertEqual(calls[0][3]['actor'],'owner-device:miniapp')
        self.assertFalse(list((self.root/'cache').glob('*.input')))

    def test_close_stops_inference_and_removes_private_temporaries(self):
        self.hold.set();body=self.body();self.ui.submit(body,'owner')
        self.ui.close()
        self.assertFalse(list((self.root/'cache').iterdir()))

    def test_unauthorized_body_and_upload_validation(self):
        self.assertEqual(self.ui.dispatch('status',{},None,'')['error'],'unauthorized')
        self.assertEqual(self.ui.dispatch('submit',{},[], 'device')['error'],'invalid_request')
        self.assertEqual(self.ui.dispatch('submit',{}, {'kind':'add','name':'X','data':'%%%'} ,'owner')['error'],'invalid_reference')
        self.assertEqual(self.ui.dispatch('enabled',{}, {'enabled':'false'},'owner')['error'],'invalid_request')

    def test_expired_preview_is_erased_but_queued_upload_is_retained(self):
        from unittest.mock import patch
        from praxis_voice_ui import TTL
        body=self.body();identity=body['request_id'];self.ui.submit(body,'owner');self.wait(identity)
        pending=uuid.uuid4().hex
        source=self.root/'cache'/(pending+'.input');source.write_bytes(b'pending input')
        self.ui.jobs[pending]={'state':'queued','updated':time.time(),'audio':None,'stop':threading.Event()}
        with patch('praxis_voice_ui.time.time',return_value=time.time()+TTL+1):self.ui.prune()
        self.assertNotIn(identity,self.ui.jobs)
        self.assertFalse(list((self.root/'cache').glob('*.ogg')))
        self.assertTrue(source.exists())
