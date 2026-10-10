"""Typed Telegram media, persisted before wake gates and available to vision."""
import gzip
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import time
import shutil
import uuid
import package_notice

LIMIT = 20 * 1024 * 1024


def outbound(tree: Path, source: Path, *, media_kind: str = 'document') -> dict:
    """Keep accepted outbound bytes after the transient delivery spool is gone."""
    source = Path(source)
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    suffix = source.suffix.lower()
    if not suffix or not suffix[1:].isalnum() or len(suffix) > 12:
        suffix = '.bin'
    path = Path(tree) / 'memory/telegram-media' / (digest.hexdigest() + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.part')
        try:
            shutil.copyfile(source, temp)
            if hashlib.sha256(temp.read_bytes()).hexdigest() != digest.hexdigest():
                raise ValueError('Файл изменился во время сохранения.')
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    mime = mimetypes.guess_type(source.name)[0] or 'application/octet-stream'
    kind = ('image' if suffix in {'.png', '.jpg', '.jpeg', '.webp', '.gif'} else
            'video' if mime.startswith('video/') else 'audio' if mime.startswith('audio/') else 'file')
    return {'media_path': path.relative_to(tree).as_posix(), 'media_kind': kind,
            'media_name': source.name, 'media_size': path.stat().st_size, 'media_mime': mime}


def item(message):
    for key in ('sticker','animation','video','video_note','voice','audio','document','photo'):
        value = message.get(key)
        if isinstance(value,list): value = max((v for v in value if isinstance(v,dict)),key=lambda v:int(v.get('width') or 0)*int(v.get('height') or 0) or int(v.get('file_size') or 0),default=None)
        if isinstance(value,dict): return key,value
    return None


def persist(client, tree: Path, peer: str, message: dict) -> dict:
    selected = item(message)
    if not selected: return {}
    kind, info = selected
    if int(info.get('file_size') or 0)>LIMIT: raise ValueError('Вложение больше 20 МБ — этот Telegram-канал не может его загрузить.')
    identity = f"{kind}:{info['file_unique_id']}" if info.get('file_unique_id') else f"{peer}:{message.get('message_id')}:{info.get('file_id') or kind}"
    folder = tree / 'memory/telegram-media'
    folder.mkdir(parents=True,exist_ok=True)
    mime = str(info.get('mime_type') or '')
    ext = Path(str(info.get('file_name') or '')).suffix.lower()
    if not ext: ext = '.webm' if info.get('is_video') else '.tgs' if info.get('is_animated') else '.webp' if kind=='sticker' else '.jpg' if kind=='photo' else mimetypes.guess_extension(mime) or '.bin'
    if ext not in {'.jpg','.jpeg','.png','.webp','.gif','.tgs','.webm','.mp4','.ogg','.mp3','.m4a','.wav','.pdf','.txt'}: ext='.bin'
    path = folder / (hashlib.sha256(identity.encode()).hexdigest()[:24]+ext)
    if not path.is_file():
        temp = path.with_suffix(path.suffix+'.part')
        try:
            client.download_media(message,temp)
            if not temp.is_file() or temp.stat().st_size>LIMIT or temp.stat().st_size==0: raise ValueError('Медиа пустое или больше доступного размера.')
            os.replace(temp,path)
        finally: temp.unlink(missing_ok=True)
    media_kind = 'image' if kind=='photo' or ext in {'.jpg','.jpeg','.png','.webp','.gif'} else 'animation' if kind in {'animation','sticker'} and ext in {'.mp4','.webm'} else 'video' if kind in {'video','video_note'} else 'audio' if kind in {'voice','audio'} else 'file'
    if ext=='.tgs':
        decoded = path.with_suffix('.json')
        if not package_notice.read(decoded):
            with gzip.open(path,'rb') as source: data=source.read(1024*1024+1)
            if len(data)>1024*1024: raise ValueError('Анимация стикера слишком большая.')
            animation=json.loads(data)
            if any(asset.get('u') or asset.get('p') for asset in animation.get('assets',[]) if isinstance(asset,dict)):
                raise ValueError('Стикер содержит внешние ресурсы.')
            package_notice.write(decoded,animation)
        original=path
        path,media_kind,mime=decoded,'sticker_tgs','application/json'
    result={'media_path':path.relative_to(tree).as_posix(),'media_kind':media_kind,
            'media_name':str(info.get('file_name') or ('Стикер '+str(info.get('emoji') or '') if kind=='sticker' else {'photo':'Фото','animation':'Анимация','video':'Видео','video_note':'Видеосообщение','voice':'Голосовое','audio':'Аудио'}.get(kind,'Файл'))).strip(),
            'media_size':path.stat().st_size,'media_mime':mime or mimetypes.guess_type(str(path))[0] or 'application/octet-stream'}
    preview=path if media_kind=='image' else None
    if media_kind=='sticker_tgs': result['media_original_path']=original.relative_to(tree).as_posix()
    if ext=='.gif':
        from PIL import Image
        preview=path.with_suffix('.first-frame.png')
        if not preview.is_file():
            temp=preview.with_suffix('.png.part')
            try:
                with Image.open(path) as picture: picture.convert('RGBA').save(temp,format='PNG')
                os.replace(temp,preview)
            finally: temp.unlink(missing_ok=True)
    thumb=info.get('thumbnail') or info.get('thumb')
    if preview is None and isinstance(thumb,dict) and thumb.get('file_id'):
        preview=path.with_suffix('.preview.jpg')
        temp=preview.with_suffix('.jpg.part')
        try:
            if not preview.is_file():
                client.download_file(thumb['file_id'],temp)
                if not temp.is_file() or not 0 < temp.stat().st_size <= LIMIT: raise ValueError('Превью недоступно.')
                os.replace(temp,preview)
        except Exception:
            preview=None
        finally: temp.unlink(missing_ok=True)
    if preview and preview.is_file(): result['media_preview_path']=preview.relative_to(tree).as_posix()
    return result


def source(message):
    selected=item(message)
    if not selected: return {}
    kind,info=selected
    return {'message_id':message.get('message_id'),'chat':message.get('chat') or {},kind:info}

def defer(tree: Path, peer: str, message: dict):
    path=tree/'memory/.state/telegram-media-pending.json'
    jobs=package_notice.read(path)
    key=f'{peer}:{message.get("message_id")}'
    jobs[key]={'peer':peer,'message':source(message),'retry_at':time.time()+10,'tries':0}
    package_notice.write(path,jobs)

def recover(tree: Path, client, rooms):
    path=tree/'memory/.state/telegram-media-pending.json'
    jobs=package_notice.read(path)
    for key,job in list(jobs.items()):
        if not isinstance(job,dict) or time.time()<float(job.get('retry_at') or 0): continue
        peer,message=str(job.get('peer') or ''),job.get('message') or {}
        try:
            payload=persist(client,tree,peer,message)
            rooms.attach_media(peer,str(message.get('message_id') or ''),source(message),payload)
            jobs.pop(key,None)
        except ValueError as exc:
            rooms.attach_media(peer,str(message.get('message_id') or ''),source(message),{'media_error':str(exc)})
            jobs.pop(key,None)
        except Exception:
            job['tries']=int(job.get('tries') or 0)+1
            job['retry_at']=time.time()+min(300,10*2**min(job['tries'],5))
        package_notice.write(path,jobs)
        break
