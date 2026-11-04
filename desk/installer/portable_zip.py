"""Bounded-memory ZIP refresh primitives; unchanged local records stay compressed.

Only regular Helene/ distribution members are accepted. No installed directory,
profile, extractor, interpreter or native build is invoked by this module.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

PREFIX = 'Helene/'
CHUNK = 1024 * 1024
STAMP = (2026, 1, 1, 0, 0, 0)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(source):
    h = hashlib.sha256()
    with Path(source).open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2) + '\n').encode('utf-8')


def relative(name):
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name
            or p.as_posix() != name or any(ord(c) < 32 for c in name)):
        raise ValueError('Unsafe package path: ' + repr(name))
    return p


def mode(info):
    raw = info.external_attr >> 16
    if stat.S_IFMT(raw) not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ValueError('Nonregular archive member: ' + info.filename)
    return '100755' if raw & 0o111 else '100644'


def info_for(name, executable='100644'):
    relative(name)
    info = zipfile.ZipInfo(name, STAMP)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = int(executable, 8) << 16
    return info


@dataclass(frozen=True)
class Entry:
    data: bytes
    mode: str = '100644'

    def identity(self):
        return {'sha256': sha(self.data), 'size': len(self.data), 'mode': self.mode}


class Package:
    def __init__(self, reader):
        self.reader = reader
        self.infos = {i.filename: i for i in reader.infolist()}
        if len(self.infos) != len(reader.infolist()):
            raise ValueError('Duplicate ZIP member')
        for name, info in self.infos.items():
            relative(name.rstrip('/'))
            mode(info)
            if not name.startswith(PREFIX) or info.flag_bits & 1:
                raise ValueError('Expected an unencrypted Helene/ distribution ZIP')
        self.replacements = {}
        self.removed = set()

    def names(self):
        return (set(self.infos) - self.removed) | set(self.replacements)

    def get(self, name):
        if name in self.removed:
            return None
        if name in self.replacements:
            return self.replacements[name]
        item = self.infos.get(name)
        if item is not None and not item.is_dir():
            if item.file_size > 128 * CHUNK:
                raise ValueError('Source-sized read exceeded: ' + name)
            return Entry(self.reader.read(item), mode(item))
        return None

    def put(self, name, entry):
        relative(name)
        if not name.startswith(PREFIX):
            raise ValueError('Replacement outside payload')
        if entry is None:
            self.removed.add(name)
            self.replacements.pop(name, None)
        else:
            if entry.mode not in ('100644', '100755'):
                raise ValueError('Invalid source mode')
            self.removed.discard(name)
            self.replacements[name] = entry

    def replace_tree(self, prefix, entries):
        self.removed.update(n for n in self.names() if n.startswith(prefix))
        for name, entry in entries.items():
            self.put(prefix + name, entry)

    def value(self, name, default=None):
        entry = self.get(PREFIX + name)
        return json.loads(entry.data) if entry else copy.deepcopy(default)

    def put_value(self, name, value):
        self.put(PREFIX + name, Entry(encoded(value)))

    def hashes(self):
        result = {}
        for name in sorted(self.names()):
            if name.endswith('/'):
                continue
            if name in self.replacements:
                result[name] = sha(self.replacements[name].data)
            else:
                h = hashlib.sha256()
                with self.reader.open(name) as stream:
                    for block in iter(lambda: stream.read(CHUNK), b''):
                        h.update(block)
                result[name] = h.hexdigest()
        return result


def snapshot(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, entry in sorted(entries.items()):
            z.writestr(info_for(name, entry.mode), entry.data)
    return stream.getvalue()


def copy_records(source, target, replacements, removed,*,windows_visibility=False):
    """Copy local headers+compressed streams, preserving attrs and data descriptors."""
    copied = {}
    with zipfile.ZipFile(source) as reader, Path(source).open('rb') as raw, \
            zipfile.ZipFile(target, 'w', allowZip64=True) as writer:
        infos = sorted(reader.infolist(), key=lambda i: i.header_offset)
        remaining = dict(replacements)
        def visible_entry(info):
            if windows_visibility and info.filename.startswith(PREFIX):
                name=info.filename[len(PREFIX):].rstrip('/')
                if name and '/' not in name:
                    info.external_attr=(info.external_attr&~2) if name.lower()=='helene.exe' else (info.external_attr|2)
            return info
        if windows_visibility:
            final_names=(set(reader.namelist())-set(removed))|set(replacements)
            folders={PREFIX+name[len(PREFIX):].split('/')[0]+'/' for name in final_names
                     if name.startswith(PREFIX) and '/' in name[len(PREFIX):]}
            for name in sorted(folders-set(reader.namelist())):
                info=zipfile.ZipInfo(name,STAMP);info.create_system=3
                info.external_attr=(0o40755<<16)|0x12
                writer.writestr(info,b'')
        for index, item in enumerate(infos):
            if item.filename in removed:
                continue
            if item.filename in remaining:
                entry = remaining.pop(item.filename)
                writer.writestr(visible_entry(info_for(item.filename, entry.mode)), entry.data, compresslevel=6)
                continue
            end = infos[index + 1].header_offset if index + 1 < len(infos) else reader.start_dir
            left = end - item.header_offset
            if left <= 0:
                raise ValueError('Overlapping/truncated local ZIP records')
            raw.seek(item.header_offset)
            cloned = copy.copy(item)
            visible_entry(cloned)
            cloned.header_offset = writer.fp.tell()
            h = hashlib.sha256()
            while left:
                block = raw.read(min(CHUNK, left))
                if not block:
                    raise ValueError('Truncated local ZIP record')
                writer.fp.write(block)
                h.update(block)
                left -= len(block)
            copied[item.filename] = h.hexdigest()
            writer.filelist.append(cloned)
            writer.NameToInfo[cloned.filename] = cloned
            writer.start_dir = writer.fp.tell()
            writer._didModify = True
        for name, entry in sorted(remaining.items()):
            writer.writestr(visible_entry(info_for(name, entry.mode)), entry.data, compresslevel=6)
    return copied


def record_hashes(path, selected):
    result = {}
    with zipfile.ZipFile(path) as reader, Path(path).open('rb') as raw:
        infos = sorted(reader.infolist(), key=lambda i: i.header_offset)
        for index, item in enumerate(infos):
            if item.filename not in selected:
                continue
            end = infos[index + 1].header_offset if index + 1 < len(infos) else reader.start_dir
            left, h = end - item.header_offset, hashlib.sha256()
            raw.seek(item.header_offset)
            while left:
                block = raw.read(min(CHUNK, left))
                if not block:
                    raise ValueError('Truncated copied ZIP record')
                h.update(block)
                left -= len(block)
            result[item.filename] = h.hexdigest()
    return result
