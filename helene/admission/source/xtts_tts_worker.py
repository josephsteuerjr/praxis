"""Isolated XTTS worker. Started and stopped by the Python process-tree owner.

Protocol is a private filesystem inbox; request IDs also name cancellation and
atomic output. No chat transport or credentials enter the worker. The live-mode
consumer may use the same request/cancel identity without changing delivery.
"""
from __future__ import annotations
import argparse
import json
import os
import time
import wave
from pathlib import Path


def serve(home: Path, model: Path, device="cpu", stopped=lambda: False):
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts
    import numpy as np

    config = XttsConfig()
    config.load_json(str(model / "config.json"))
    engine = Xtts.init_from_config(config)
    engine.load_checkpoint(config, checkpoint_dir=str(model), eval=True)
    engine.to(device)
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
                    text = str(request["text"]).strip()
                    if not text or len(text) > 4000:
                        raise ValueError("XTTS expects 1..4000 characters")
                    reference = request.get("reference")
                    if reference:
                        conditioning, speaker = engine.get_conditioning_latents(audio_path=[reference])
                    else:
                        voice = engine.speaker_manager.speakers[str(request["speaker"])]
                        conditioning, speaker = voice.values()
                    # Upstream inference_stream yields tensors; cancellation is
                    # checked between chunks, before any completed file can deliver.
                    with wave.open(str(partial), "wb") as audio:
                        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
                        # XTTS's optional Spacy splitter is not in the CPU bundle.
                        # Bound each input ourselves without dropping text; long
                        # responses remain cancellable between streamed chunks.
                        remaining = text
                        while remaining:
                            end = min(len(remaining), 180)
                            if end < len(remaining):
                                boundary = remaining.rfind(' ', 0, end)
                                if boundary > 0: end = boundary
                            sentence, remaining = remaining[:end], remaining[end:].lstrip()
                            for chunk in engine.inference_stream(sentence, str(request.get("language") or "ru"), conditioning, speaker, enable_text_splitting=False):
                                if cancel.exists() or stopped():
                                    raise InterruptedError("cancelled")
                                pcm = np.clip(chunk.detach().cpu().numpy(), -1, 1)
                                audio.writeframes((pcm * 32767).astype("<i2").tobytes())
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
