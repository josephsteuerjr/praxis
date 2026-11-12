"""Portable archive inventory. Standard library only, shared by both directions.

The outer SHA binds a transfer receipt to one archive; this inventory binds each
member and its metadata before an importer changes the destination. Legacy carry
archives remain readable, but never acquire an invented integrity receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import stat
import unicodedata

NAME = 'helene-inventory.json'
FORMAT = 'helene.inventory.v1'
MAX_MANIFEST = 64 * 1024 * 1024


def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name or name.startswith('/'):
        return False
    parts = name.rstrip('/').split('/')
    return all(part not in ('', '.', '..') and ':' not in part for part in parts)


def checked_name(name,*,windows=None):
    windows=os.name=='nt' if windows is None else windows
    if not safe_name(name):raise ValueError('Архив содержит недопустимый путь: '+repr(name))
    name=name.rstrip('/')
    if windows:
        for part in name.split('/'):
            if (part.endswith((' ', '.')) or any(ord(c)<32 or c in '<>"|?*' for c in part)
                    or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part)):
                raise ValueError('Путь из архива нельзя сохранить на Windows: '+name)
    return unicodedata.normalize('NFC',name).casefold() if windows else name


def members(archive, *, windows=None):
    """Reject aliases and file/parent collisions before any extraction begins."""
    result = {}; files = set()
    for info in archive.infolist():
        if not safe_name(info.orig_filename):raise ValueError('Неканоническое имя в архиве: '+repr(info.orig_filename))
        name = info.filename.rstrip('/')
        key = checked_name(info.filename,windows=windows)
        if key in result:
            raise ValueError('Архив содержит повторяющиеся пути или имена, неразличимые на этом компьютере: ' + name)
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError('Архив содержит неподдерживаемую ссылку или специальный файл: ' + name)
        if stat.S_ISDIR(mode) and not info.is_dir():
            raise ValueError('Тип записи архива не совпадает с её именем: ' + name)
        result[key] = info
        if not info.is_dir(): files.add(key)
    for key in result:
        if any(str(parent) in files for parent in PurePosixPath(key).parents if str(parent) != '.'):
            raise ValueError('Файл в архиве одновременно является папкой: ' + key)
    return list(result.values())


def file_sha256(stream):
    """hashlib.file_digest is 3.11+; server hosts may run 3.9/3.10."""
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def record(archive, info):
    digest = hashlib.sha256(); size = 0
    with archive.open(info) as source:
        while block := source.read(1024 * 1024):
            size += len(block); digest.update(block)
    if size != info.file_size:
        raise ValueError('Размер файла не совпадает с архивом: ' + info.filename)
    return {'path': info.filename, 'kind': 'directory' if info.is_dir() else 'file',
            'bytes': size, 'sha256': digest.hexdigest(), 'mode': (info.external_attr >> 16) & 0o777}


def seal(archive):
    """Called after every payload member (including the passport) was written."""
    entries = members(archive, windows=False)
    if any(info.filename == NAME for info in entries):
        raise ValueError('Опись архива уже существует; повторная запись запрещена.')
    rows = [record(archive, info) for info in entries]
    manifest = {'format': FORMAT, 'members': rows}
    data = (json.dumps(manifest, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    if len(data) > MAX_MANIFEST:
        raise ValueError('Опись слишком велика. Архив не завершён; исходные данные сохранены.')
    archive.writestr(NAME, data)
    return {'members': len(rows), 'bytes': sum(row['bytes'] for row in rows),
            'sha256': hashlib.sha256(data).hexdigest()}


def verify(archive, *, required=False, windows=None):
    entries = members(archive, windows=windows)
    index = {info.filename: info for info in entries}
    info = index.pop(NAME, None)
    if info is None:
        if required: raise ValueError('В архиве отсутствует обязательная опись. Данные не заменены.')
        return {'verified': False, 'legacy': True}
    if info.file_size > MAX_MANIFEST: raise ValueError('Опись архива слишком велика.')
    value = json.loads(archive.read(info))
    if not isinstance(value, dict) or value.get('format') != FORMAT or not isinstance(value.get('members'), list):
        raise ValueError('Неподдерживаемая опись архива.')
    rows = value['members']; seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str):
            raise ValueError('Повреждена запись описи архива.')
        name = row['path']
        if name in seen or name not in index:
            raise ValueError('Опись и содержимое архива различаются: ' + name)
        seen.add(name)
        if record(archive, index[name]) != row:
            raise ValueError('Контрольная сумма или свойства файла не совпали с описью: ' + name)
    if seen != set(index):
        raise ValueError('В архиве есть файлы, отсутствующие в описи.')
    return {'verified': True, 'members': len(rows), 'bytes': sum(row['bytes'] for row in rows)}
