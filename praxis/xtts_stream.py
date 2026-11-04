"""Progressive PCM -> Opus -> authenticated UDS frames; no Telegram or jobs."""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading

from xtts_protocol import MAX_AUDIO, SCHEMA, XttsError, encoded


def respond(handler, request, wav_path, profile, stopped):
    import imageio_ffmpeg
    executable = os.environ.get('PRAXIS_XTTS_FFMPEG') or imageio_ffmpeg.get_ffmpeg_exe()
    process = subprocess.Popen([
        executable, '-nostdin', '-v', 'error', '-f', 's16le', '-ar', '24000',
        '-ac', '1', '-i', 'pipe:0', '-c:a', 'libopus', '-b:a', '48k',
        '-f', 'ogg', '-flush_packets', '1', '-page_duration', '20000', 'pipe:1',
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    errors = []
    checksum = hashlib.sha256()
    total = [0]
    write_lock = threading.Lock()

    def frame(value, chunk=b''):
        with write_lock:
            handler.wfile.write(encoded({'schema': SCHEMA, 'ok': True, **value}) + b'\n')
            if chunk:
                handler.wfile.write(chunk)
            handler.wfile.flush()

    def consume():
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 16 * 1024)
                if not chunk:
                    return
                if stopped():
                    raise XttsError('cancelled')
                total[0] += len(chunk)
                if total[0] > MAX_AUDIO:
                    raise XttsError('oversized_audio')
                checksum.update(chunk)
                frame({'chunk_bytes': len(chunk)}, chunk)
        except BaseException as exc:
            errors.append(exc)
            if process.poll() is None:
                process.kill()

    reader = threading.Thread(target=consume, name='xtts-opus-stream', daemon=True)

    def pcm(raw):
        if stopped() or errors:
            raise XttsError('cancelled' if stopped() else 'stream_disconnected')
        view = memoryview(raw)
        while view:
            count = process.stdin.write(view)
            if not count:
                raise XttsError('encoding_failed')
            view = view[count:]

    try:
        frame({'stream': True, 'request_id': request['request_id'], 'mime': 'audio/ogg',
               'model_id': request['model_id'], 'profile_id': request['profile_id'],
               'generation': handler.server.generation})
        reader.start()
        metadata = handler.server.engine.synthesize(
            request.get('text'), wav_path, model_id=request['model_id'],
            profile_id=request['profile_id'], stopped=stopped, on_pcm=pcm,
            **({'profile': profile} if profile is not None else {}))
        process.stdin.close()
        # Cancellation and I/O failures stay separate from an overall job TTL.
        while process.poll() is None:
            if stopped() or errors:
                raise XttsError('cancelled' if stopped() else 'stream_disconnected')
            try:
                process.wait(timeout=.1)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=5)
        if reader.is_alive() or errors or process.returncode or total[0] <= 32:
            raise XttsError('encoding_failed')
        if stopped():
            raise XttsError('cancelled')
        frame({'done': True, 'request_id': request['request_id'],
               'generation': handler.server.generation, 'bytes': total[0],
               'sha256': checksum.hexdigest(), 'mime': 'audio/ogg', **metadata})
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout):
            if stream and not stream.closed:
                stream.close()
        if reader.ident is not None:
            reader.join(timeout=5)
