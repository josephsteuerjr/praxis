"""Incoming speech admission, shared by idle and active-turn inbox consumers.

Original audio remains playable. A failed transcription is product state, never
owner speech. Local inference may repeat after a crash before a ready receipt;
already-ready text is reused. Delivery still belongs to inbox/checkpoint ACK.
"""
from pathlib import Path
import hashlib,json,os,time,uuid

class VoicePending(RuntimeError):
    pass

from localharness.media_metadata import path_for

def record_path(source):return path_for(source,'transcription.json')
def retry_path(source):return path_for(source,'retry')

def state(source):
    path=record_path(source)
    value=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'status':'pending'}
    value['retry_requested']=retry_path(source).exists()
    return value

def _write(source,value):
    target=record_path(source);target.parent.mkdir(parents=True,exist_ok=True)
    part=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        part.write_text(json.dumps(dict(value,updated_at=time.time()),ensure_ascii=False),encoding='utf-8');os.replace(part,target)
    finally:part.unlink(missing_ok=True)

def retry(source):
    value=state(source)
    if value['status'] in ('pending','error'):
        target=retry_path(source);target.parent.mkdir(parents=True,exist_ok=True);target.touch()
    return state(source)

def resolve(source, transcribe, *, signature=''):
    source=Path(source)
    with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    old=state(source);same=old.get('sha256')==digest and old.get('signature')==signature
    if same and old['status']=='ready':return '[Транскрипт голосового]: '+old['transcript']
    if same and old['status']=='error' and not old['retry_requested']:raise VoicePending(old['error'])
    retry_path(source).unlink(missing_ok=True)
    base={'sha256':digest,'signature':signature}
    _write(source,dict(base,status='transcribing'))
    try:
        text=str(transcribe(source) or '').strip()
        if not text:raise ValueError('Расшифровка пустая: тишина или речь не распознана')
    except Exception as exc:
        error=f'{type(exc).__name__}: {exc}'[:500]
        _write(source,dict(base,status='error',error=error))
        raise VoicePending(error) from exc
    _write(source,dict(base,status='ready',transcript=text))
    return '[Транскрипт голосового]: '+text

def is_audio(source):
    source=Path(source);meta=path_for(source,'mime.json')
    if meta.is_file():return str(json.loads(meta.read_text(encoding='utf-8')).get('mime','')).startswith('audio/')
    # Old recorder output was saved without its audio MIME. Do not reinterpret
    # arbitrary .webm video; retain this one historical recorder naming case.
    return source.suffix.lower() in ('.ogg','.oga','.opus','.m4a','.mp3','.wav') or (source.suffix.lower()=='.webm' and source.name.startswith('voice-'))
