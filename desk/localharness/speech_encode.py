"""Real Telegram Ogg/Opus through the optional XTTS runtime's libsndfile.

Soundfile is an existing XTTS dependency. Encoding is enabled only when its
actual library advertises OPUS; neither a renamed WAV nor import success is proof.
"""
from __future__ import annotations
from pathlib import Path


def available() -> bool:
    try:
        import soundfile
        return 'OPUS' in soundfile.available_subtypes('OGG')
    except (ImportError, OSError, ValueError, RuntimeError):
        return False


def opus(source: Path, destination: Path, *, stopped=lambda: False) -> Path:
    import soundfile
    if stopped():
        raise InterruptedError('Speech cancelled before encoding')
    partial = destination.with_suffix('.part.ogg')
    try:
        with soundfile.SoundFile(str(source)) as wav:
            if wav.channels != 1 or wav.subtype != 'PCM_16':
                raise ValueError('XTTS encoder expects mono PCM16 WAV')
            with soundfile.SoundFile(str(partial), mode='w', samplerate=wav.samplerate,
                                     channels=1, format='OGG', subtype='OPUS') as sink:
                for block in wav.blocks(blocksize=4096, dtype='float32'):
                    if stopped():
                        raise InterruptedError('Speech cancelled during encoding')
                    sink.write(block)
        if stopped():
            raise InterruptedError('Speech cancelled after encoding')
        with partial.open('rb') as stream:
            header = stream.read(512)
        if not header.startswith(b'OggS') or b'OpusHead' not in header:
            raise ValueError('Encoder did not produce Ogg/Opus')
        partial.replace(destination)
        if stopped():
            raise InterruptedError('Speech cancelled before encoded output publication')
        return destination
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        partial.unlink(missing_ok=True)
