"""Move personal voice samples, never host pins or executable runtimes."""
import copy
import hashlib
import zlib
from pathlib import Path

AUDIO = {'.wav', '.flac', '.mp3', '.ogg', '.m4a'}
MAX_SAMPLE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024


def retain_sample(tree, source):
    """Detach one selected personal sample from a programme being retired."""
    source = Path(source)
    if source.suffix.lower() not in AUDIO or not 0 < source.stat().st_size <= MAX_SAMPLE:
        raise ValueError('Unsupported personal voice sample')
    data = source.read_bytes()
    if not 0 < len(data) <= MAX_SAMPLE: raise ValueError('Personal voice sample changed during migration')
    target = Path(tree) / 'voice-references' / (hashlib.sha256(data).hexdigest()+source.suffix.lower())
    if target.exists():
        if target.is_symlink() or target.read_bytes() != data:raise ValueError('Personal voice sample collision')
    else:
        from .private_files import write_private
        write_private(target, data)
    return target


def export(zf, tree, config, *, program=None):
    from localharness import component_store as store
    config = copy.deepcopy(config)
    tree = Path(tree)
    # Validate source pins before excluding their host-specific addresses.
    store.export_references(tree)
    model = store.model_path(tree, 'xtts')
    selected = (config.get('voice') or {}).get('tts_reference')
    chosen = store.reference_path(tree, selected, program=program).resolve() if selected else None
    samples = set()
    if model.is_dir():
        for path in model.rglob('*'):
            if path.is_file() and path.suffix.lower() in AUDIO:
                if path.is_symlink() or not path.resolve().is_relative_to(model.resolve()):
                    raise ValueError('Linked personal voice sample')
                samples.add(path.resolve())
    if chosen:
        if not chosen.is_file():
            raise ValueError('Selected voice reference is unavailable for transfer')
        samples.add(chosen)
    written = {}; total = 0; added = 0; added_bytes = 0
    for path in sorted(samples):
        if not 0 < path.stat().st_size <= MAX_SAMPLE:
            raise ValueError('Personal voice sample is too large or empty')
        data = path.read_bytes()
        if not 0 < len(data) <= MAX_SAMPLE:
            raise ValueError('Personal voice sample changed during transfer')
        suffix = path.suffix.lower()
        if suffix not in AUDIO: raise ValueError('Unsupported personal voice sample')
        name = 'voice-references/' + hashlib.sha256(data).hexdigest() + suffix
        if name not in written:
            total += len(data)
            if total > MAX_TOTAL: raise ValueError('Personal voice samples exceed transfer limit')
            if 'data/' + name in zf.namelist():
                info = zf.getinfo('data/' + name)
                if info.file_size != len(data) or info.CRC != zlib.crc32(data):
                    raise ValueError('Personal voice sample archive collision')
            else:
                zf.writestr('data/' + name, data)
                added += 1; added_bytes += len(data)
            written[name] = len(data)
        if chosen == path:
            config['voice']['tts_reference'] = name
    return config, added, added_bytes
