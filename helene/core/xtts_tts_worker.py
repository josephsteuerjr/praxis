"""Isolated XTTS worker. Started and stopped by the Python process-tree owner.

The legacy server protocol is a private filesystem inbox. The desktop ships an
exact application-owned copy of this module for load_engine/synthesize_wav;
localharness.speech_worker owns its generation/lease/cancellation protocol. No
chat transport or credentials enter either model worker.
"""
from __future__ import annotations
import argparse
from collections import OrderedDict
import hashlib
import inspect
import json
import os
import re
import tempfile
import time
import wave
from pathlib import Path


def model_identity(model: Path):
    files={}
    for name in ('config.json','model.pth','vocab.json','speakers_xtts.pth'):
        with (Path(model)/name).open('rb') as stream:files[name]=hashlib.file_digest(stream,'sha256').hexdigest()
    return hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()


class VoiceProfiles:
    """Bounded conditioning cache for one loaded, immutable model instance."""
    def __init__(self,engine,model='loaded-instance',limit=8):
        self.engine,self.model,self.limit=engine,model,limit
        self.cache=OrderedDict();self.conditioning_calls=0;self.hits=0

    def get(self,request,stopped=lambda:False):
        if stopped():raise InterruptedError('cancelled before conditioning')
        reference=request.get('reference');parameters=dict(request.get('conditioning') or {})
        # Include effective installed API defaults; no hidden processing option
        # may make a previous reference profile eligible for different input.
        defaults={}
        if reference:
            signature=inspect.signature(self.engine.get_conditioning_latents)
            for name,p in signature.parameters.items():
                if name!='audio_path' and p.default is not inspect.Parameter.empty:defaults[name]=p.default
            if any(name not in signature.parameters or name=='audio_path' for name in parameters):
                raise ValueError('Unsupported XTTS conditioning parameter')
            defaults.update(parameters)
            path=Path(reference)
            if path.suffix.lower()!='.wav' or path.stat().st_size>64*1024*1024:
                raise ValueError('XTTS reference must be a local WAV up to 64 MiB')
            with path.open('rb') as stream:content=stream.read(64*1024*1024+1)
            if len(content)>64*1024*1024:raise ValueError('XTTS reference exceeds 64 MiB')
            reference_hash=hashlib.sha256(content).hexdigest()
            source={'reference_sha256':reference_hash,'parameters':defaults}
        else:
            source={'speaker':str(request.get('speaker') or 'Ana Florence')}
        key=hashlib.sha256(json.dumps({'model':self.model,**source},sort_keys=True).encode()).hexdigest()
        if key in self.cache:
            if stopped():raise InterruptedError('cancelled before cached conditioning')
            self.hits+=1;self.cache.move_to_end(key)
            return key,self.cache[key]
        if stopped():raise InterruptedError('cancelled before conditioning')
        if reference:
            # The model sees the exact bytes hashed above, even if the owner
            # replaces the original path during conditioning. No durable copy.
            with tempfile.TemporaryDirectory(prefix='helene-voice-reference-') as folder:
                snapshot=Path(folder)/'reference.wav';snapshot.write_bytes(content)
                profile=self.engine.get_conditioning_latents(audio_path=[str(snapshot)],**parameters)
            self.conditioning_calls+=1
        else:
            voice=self.engine.speaker_manager.speakers[source['speaker']]
            if not isinstance(voice,dict) or not {'gpt_cond_latent','speaker_embedding'}<=set(voice):
                raise ValueError('XTTS speaker profile lacks named conditioning fields')
            profile=(voice['gpt_cond_latent'],voice['speaker_embedding'])
        if stopped():raise InterruptedError('cancelled after conditioning')
        self.cache[key]=profile;self.cache.move_to_end(key)
        while len(self.cache)>self.limit:self.cache.popitem(last=False)
        return key,profile


def profiles(engine):
    cache=getattr(engine,'_helene_voice_profiles',None)
    if cache is None:
        cache=VoiceProfiles(engine);engine._helene_voice_profiles=cache
    return cache


def sentences(engine,text,language):
    """Prefer sentence/word boundaries, checking the actual tokenizer budget."""
    tokenizer=getattr(engine,'tokenizer',None)
    limits=getattr(tokenizer,'char_limits',{}) or {}
    char_limit=max(1,int(limits.get(language.split('-')[0],180)))
    token_limit=int(getattr(getattr(engine,'args',None),'gpt_max_text_tokens',402))-1
    def fits(value):
        # XTTS normalizes this exact way immediately before encoding.
        return tokenizer is None or len(tokenizer.encode(value.strip().lower(),lang=language.split('-')[0]))<=token_limit
    remaining=text.strip()
    while remaining:
        end=min(len(remaining),char_limit)
        if not fits(remaining[:end]):
            lo,hi=1,end
            while lo<hi:
                mid=(lo+hi+1)//2
                if fits(remaining[:mid]):lo=mid
                else:hi=mid-1
            end=lo
        if not fits(remaining[:end]):raise ValueError('XTTS token budget cannot encode this text')
        if end<len(remaining):
            boundaries=[m.end() for m in re.finditer(r'[.!?…](?:["»”)]*)\s+',remaining[:end])]
            boundary=boundaries[-1] if boundaries else remaining.rfind(' ',0,end)
            if boundary>0:end=boundary
        sentence=remaining[:end].strip();remaining=remaining[end:].lstrip()
        if sentence:yield sentence


def load_engine(model: Path, device="cpu"):
    """Canonical model loading, also used by the application's private worker."""
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts

    model=Path(model)
    stamps={name:(model/name).stat() for name in ('config.json','model.pth','vocab.json','speakers_xtts.pth')}
    identity=model_identity(model)
    config = XttsConfig()
    config.load_json(str(model / "config.json"))
    engine = Xtts.init_from_config(config)
    engine.load_checkpoint(config, checkpoint_dir=str(model), eval=True)
    engine.to(device)
    if any((before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=
            ((after:=(model/name).stat()).st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
            for name,before in stamps.items()):raise ValueError('XTTS model changed during loading')
    engine._helene_voice_profiles=VoiceProfiles(engine,identity)
    return engine


def warm_voice(engine,selection,stopped=lambda:False):
    """Condition once and exercise the selected voice silently, with no delivery."""
    key,_=profiles(engine).get(selection,stopped)
    language=str(selection.get('language') or 'ru')
    warmed=getattr(engine,'_helene_warmed_profiles',None)
    if warmed is None:warmed=OrderedDict();engine._helene_warmed_profiles=warmed
    warm_key=(key,language)
    if warm_key not in warmed:
        with tempfile.TemporaryDirectory(prefix='helene-xtts-warmup-') as folder:
            synthesize_wav(engine,{**selection,'text':'Готово.' if language=='ru' else 'Ready.'},Path(folder)/'warm.wav',stopped)
        if stopped():raise InterruptedError('cancelled during voice warmup')
        warmed[warm_key]=True
        while len(warmed)>profiles(engine).limit:warmed.popitem(last=False)
    else:warmed.move_to_end(warm_key)
    if stopped():raise InterruptedError('cancelled during voice warmup')
    return {'model_identity':profiles(engine).model,'voice_identity':key,'language':language,
        'conditioning_calls':profiles(engine).conditioning_calls,'profile_cache_hits':profiles(engine).hits,
        'device':str(getattr(engine,'device','unknown'))}


def synthesize_wav(engine, request: dict, partial: Path, stopped=lambda: False):
    """One canonical PCM producer; no IPC, credentials or chat transport here."""
    import numpy as np
    if stopped():
        raise InterruptedError('cancelled')
    text = str(request['text']).strip()
    if not text or len(text) > 4000:
        raise ValueError('XTTS expects 1..4000 characters')
    voice_identity,(conditioning,speaker)=profiles(engine).get(request,stopped)
    if stopped():
        raise InterruptedError('cancelled')
    with wave.open(str(partial), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        language=str(request.get('language') or 'ru')
        for sentence in sentences(engine,text,language):
            if stopped():raise InterruptedError('cancelled before inference')
            mode=request.get('inference_mode','stream')
            if mode=='full':
                chunks=[engine.inference(sentence,language,conditioning,speaker,enable_text_splitting=False)['wav']]
            elif mode=='stream':
                chunks=engine.inference_stream(sentence,language,conditioning,speaker,enable_text_splitting=False)
            else:raise ValueError('Unsupported XTTS inference mode')
            for chunk in chunks:
                if stopped():
                    raise InterruptedError('cancelled')
                pcm = np.clip(chunk.detach().cpu().numpy() if hasattr(chunk,'detach') else np.asarray(chunk), -1, 1)
                audio.writeframes((pcm * 32767).astype('<i2').tobytes())
    if stopped():
        raise InterruptedError('cancelled')
    return {'voice_identity':voice_identity,'conditioning_calls':profiles(engine).conditioning_calls,
        'profile_cache_hits':profiles(engine).hits}


def serve(home: Path, model: Path, device="cpu", stopped=lambda: False):
    engine = load_engine(model, device)
    incoming, output = home / "inbox", home / "output"
    incoming.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    (home / "ready.json").write_text(json.dumps({"pid":os.getpid(), "ready":True}), encoding="utf-8")
    try:
        while not stopped():
            if (home / 'stop').exists():
                break
            for path in sorted(incoming.glob("*.json")):
                identity = path.stem
                if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
                    continue
                cancel = incoming / (identity + ".cancel")
                receipt = output / (identity + ".json")
                partial = output / (identity + ".part.wav")
                final = output / (identity + ".wav")
                try:
                    request = json.loads(path.read_text(encoding="utf-8"))
                    if cancel.exists() or stopped():
                        raise InterruptedError("cancelled")
                    synthesize_wav(engine, request, partial, lambda: cancel.exists() or stopped())
                    if cancel.exists() or stopped():
                        raise InterruptedError("cancelled")
                    partial.replace(final)
                    result = {"ok":True, "request_id":identity, "path":str(final)}
                except Exception as exc:
                    partial.unlink(missing_ok=True)
                    final.unlink(missing_ok=True)
                    result = {"ok":False, "request_id":identity, "error":str(exc),
                              "cancelled":isinstance(exc, InterruptedError)}
                if cancel.exists():
                    # The caller already abandoned this request: retain no late
                    # audio or error receipt that no consumer can acknowledge.
                    partial.unlink(missing_ok=True); final.unlink(missing_ok=True)
                    path.unlink(missing_ok=True); cancel.unlink(missing_ok=True)
                    continue
                temporary = receipt.with_suffix(".tmp")
                temporary.write_text(json.dumps(result), encoding="utf-8")
                temporary.replace(receipt)
                path.unlink(missing_ok=True)
                cancel.unlink(missing_ok=True)
            time.sleep(0.1)
    finally:
        (home / "ready.json").unlink(missing_ok=True)


def main():
    import signal
    import threading
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--device',default='cpu')
    args=parser.parse_args()
    stop=threading.Event()
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    serve(args.home,args.model,args.device,stop.is_set)


if __name__ == '__main__':main()
