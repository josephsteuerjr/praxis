"""Application-owned speech; the submitting agent never waits for production or delivery."""
from __future__ import annotations
from contextvars import copy_context
import hashlib
import json
import logging
from pathlib import Path
import queue
import threading
import time
import uuid
from . import speech_client, speech_protocol as ipc
from .private_files import write_private
from .owner_stop import pause_generation

log = logging.getLogger(__name__)
TERMINAL = {'delivered', 'cancelled', 'failed', 'unknown'}

def selection(cfg):
    return hashlib.sha256(json.dumps({'voice': cfg.get('voice'), 'epoch': cfg.get('_speech_epoch')}, sort_keys=True).encode()).hexdigest()

class SpeechJobs:
    def __init__(self, tree, *, configuration, cancelled, delivery, preflight=None, failed=None):
        self.tree=Path(tree); self.home=self.tree/'memory/.state/speech-jobs'
        self.configuration=configuration; self.cancelled=cancelled; self.delivery=delivery
        self.preflight=preflight; self.failed=failed; self.instance=uuid.uuid4().hex
        self.pending=queue.Queue(); self.closed=threading.Event(); self.thread=None
        self.lock=threading.RLock(); self.admissions=set()

    def _write(self, value):
        write_private(self.home/(value['id']+'.json'), json.dumps(value).encode())

    def _update(self, identity, **fields):
        with ipc._queue_lock(self.home):
            value=ipc.read(self.home/(identity+'.json'))
            value.update(fields, updated_at=time.time()); self._write(value)
            return value

    def start(self):
        with self.lock:
            if self.thread is not None:return
            self.home.mkdir(parents=True, exist_ok=True)
            # A crash is not permission to repeat a paid POST or a possibly
            # accepted delivery. Keep an explicit terminal ambiguity receipt.
            with ipc._queue_lock(self.home):
                for path in self.home.glob('*.json'):
                    if not ipc.identity(path.stem):continue
                    value=ipc.read(path)
                    if value.get('status') not in TERMINAL:
                        value.update(status='unknown', error='application_restarted', updated_at=time.time())
                        self._write(value)
            self.thread=threading.Thread(target=self._loop,name='application-speech',daemon=True)
            self.thread.start()

    def submit(self, text, caption, *, identity, run_id, chat_id):
        if not ipc.identity(identity) or not run_id or not chat_id:raise ValueError('Stable speech request and recipient required')
        if not isinstance(text,str) or not text.strip() or len(text)>4000:raise ValueError('Speech expects 1..4000 characters')
        if not isinstance(caption,str) or len(caption)>1024:raise ValueError('Speech caption exceeds 1024 characters')
        control = pause_generation(self.tree)
        cfg=self.configuration()
        if self.closed.is_set() or ipc.blocked(self.tree) or (cfg.get('voice') or {}).get('speak') is not True:
            raise InterruptedError('Speech disabled or stopped')
        self.start()
        binding=hashlib.sha256(json.dumps([run_id,chat_id,text,caption],ensure_ascii=False).encode()).hexdigest()
        with ipc._queue_lock(self.home):
            path=self.home/(identity+'.json')
            if path.exists():
                previous=ipc.read(path)
                if previous.get('binding')!=binding:raise ValueError('Speech identity belongs to another request')
                self.admissions.add(run_id); return previous
            if self.pending.qsize() >= 16:
                raise RuntimeError('Speech queue is full')
            value={'schema':1,'id':identity,'run_id':run_id,'chat_id':chat_id,'text':text,'caption':caption,
                'binding':binding,'selection':selection(cfg),'control':(control or {}).get('id'),
                'status':'queued','accepted_at':time.time(),'instance':self.instance}
            self._write(value)
        self.admissions.add(run_id); self.pending.put((value,copy_context())); return value

    def accepted_for_run(self, run_id):return bool(run_id and run_id in self.admissions)

    def _check(self, job):
        cfg=self.configuration()
        control = pause_generation(self.tree)
        if (self.closed.is_set() or ipc.blocked(self.tree) or self.cancelled(job['run_id'])
                or (control or {}).get('id') != job.get('control')
                or selection(cfg)!=job['selection'] or (cfg.get('voice') or {}).get('speak') is not True):
            raise InterruptedError('Speech cancelled')

    def _loop(self):
        while not self.closed.is_set():
            try:job,context=self.pending.get(timeout=.2)
            except queue.Empty:continue
            try:context.run(self._perform,job)
            except Exception:log.error('Speech job receipt unavailable: %s',job['id'])
            finally:self.pending.task_done()

    def _perform(self, job):
        audio=None; delivering=False
        try:
            self._check(job); self._update(job['id'],status='preparing')
            # Existing privacy authority applies to committed text BEFORE
            # synthesis. Audio is never handed back to an agent or model review.
            if self.preflight is not None:self.preflight(job)
            self._check(job); self._update(job['id'],status='generating')
            audio=speech_client.synthesize(job['text'],self.tree,self.tree/'media/tts',
                configuration=self.configuration,stopped=lambda:self._stopped(job),request_id=job['id'])
            self._check(job); self._update(job['id'],status='delivering'); delivering=True
            receipt=self.delivery(job,audio,lambda:self._check(job))
            if not receipt:raise RuntimeError('Speech delivery acceptance unavailable')
            self._update(job['id'],status='delivered',receipt=str(receipt)[:1000])
        except InterruptedError:self._update(job['id'],status='cancelled',error='cancelled')
        except Exception as exc:
            state='unknown' if delivering else 'failed'
            self._update(job['id'],status=state,error=type(exc).__name__)
            if self.failed is not None:self.failed(job,state)
        finally:
            if audio is not None:audio.path.unlink(missing_ok=True)

    def _stopped(self, job):
        try:self._check(job); return False
        except InterruptedError:return True

    def close(self):
        self.closed.set()
        if self.thread is not None:self.thread.join(2)
