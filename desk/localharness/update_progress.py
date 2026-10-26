"""One durable, operation-bound update receipt, shared by old UI and observer."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import threading
import time
from contextlib import contextmanager, nullcontext

# The native/browser observer and transaction share this module in one process.
# Windows replace can deny an open destination even with share-delete handles;
# close the complete read before publishing, not by retrying generic WinError 5.
# POSIX retains its existing concurrent open/rename semantics.
_publication_lock = threading.RLock() if os.name == 'nt' else nullcontext()

LABELS = {
    'downloading': 'Скачиваю обновление', 'unpacking': 'Распаковываю проверенный архив',
    'extensions': 'Проверяю совместимость расширений', 'code': 'Подготавливаю код',
    'backup': 'Сохраняю настройки для отката', 'preimages': 'Подготавливаю прежние файлы',
    'prepared': 'Подготовка завершена',
    'waiting_owner': 'Закрываю прежнее окно', 'freshness': 'Сверяю изменения после закрытия',
    'switching': 'Заменяю программные файлы', 'starting': 'Открываю новую Hélène',
    'rollback': 'Возвращаю прежнюю версию', 'reopening': 'Открываю прежнюю Hélène',
    'completed': 'Обновление завершено', 'failed': 'Обновление не завершено',
    'needs_resolution': 'Слияние кода ожидает независимой проверки',
}
TERMINAL = {'completed', 'failed', 'needs_resolution'}


def pointer(target):
    target = Path(target).resolve()
    identity = hashlib.sha256(os.path.normcase(str(target)).encode()).hexdigest()[:16]
    return target.parent / ('.helene-update-progress-' + identity + '.json')


@contextmanager
def _open_reader(path):
    """Keep the Windows publisher outside the entire open/read/close interval."""
    with _publication_lock, Path(path).open('r', encoding='utf-8') as stream:
        yield stream


def read_json(path):
    """Read one complete published snapshot and release its handle even on error."""
    with _open_reader(path) as stream:
        return json.load(stream)


def status(target):
    """Read a single target-bound operation, never scan an ever-growing folder."""
    target = Path(target).resolve()
    index = pointer(target)
    if not index.is_file():return None
    ref = read_json(index)
    path = Path(ref['receipt']).resolve()
    if path.parent != target.parent or not path.name.startswith('.helene-update-') or not path.name.endswith('.progress.json'):
        raise ValueError('Update progress receipt leaves installation parent')
    value = read_json(path)
    if value.get('target') != str(target) or value.get('id') != ref['id']:
        raise ValueError('Update progress identity differs')
    return value


def atomic_json(path, value):
    path = Path(path)
    part = path.with_suffix(path.suffix + '.tmp')
    with _publication_lock:
        with part.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        part.replace(path)
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


class Progress:
    """Only measured counters; durations refer to observed phases, not estimates."""
    def __init__(self, path, operation_id=None, target=None, event=None):
        self.path = Path(path).resolve()
        self.lock = threading.RLock()
        self.event = event
        self.last_write = 0.0
        if self.path.is_file():
            self.value = read_json(self.path)
            if operation_id and self.value['id'] != operation_id:
                raise ValueError('Update receipt operation differs')
            if target and self.value['target'] != str(Path(target).resolve()):
                raise ValueError('Update receipt target differs')
        else:
            if not operation_id or target is None:
                raise ValueError('New update receipt needs operation and target')
            self.value = {'format': 'helene-update-progress.v1', 'id': operation_id,
                          'target': str(Path(target).resolve()), 'created_at': time.time(),
                          'stage': '', 'stages': [], 'read_bytes': {},
                          'read_scope': 'download/extraction/update transaction streams; not OS or model I/O'}
            self.save()
        self.pin()

    def pin(self):
        target = Path(self.value['target']).resolve()
        if self.path.parent != target.parent:
            raise ValueError('Update progress must remain beside the installation')
        atomic_json(pointer(target), {'receipt': str(self.path), 'id': self.value['id']})

    def save(self):
        self.value['updated_at'] = time.time()
        atomic_json(self.path, self.value)
        self.last_write = time.monotonic()
        if self.event:
            self.event('package-progress', dict(self.value, receipt=str(self.path)))

    def phase(self, name, **details):
        with self.lock:
            now = time.monotonic()
            if self.value['stage'] != name:
                if self.value['stage'] and self.value['stages']:
                    prior = self.value['stages'][-1]
                    prior['seconds'] = max(0.0, now - prior['monotonic'])
                    prior['finished_at'] = time.time()
                self.value['stages'].append({'stage': name, 'started_at': time.time(), 'monotonic': now})
                self.value.update(stage=name, label=LABELS.get(name, name), bytes=0,
                                  total_bytes=None, files=0, total_files=None)
            self.value.update(details)
            if name in TERMINAL:
                self.value['finished_at'] = time.time()
            self.save()

    def add_read(self, category, size):
        with self.lock:
            totals = self.value['read_bytes']
            totals[category] = totals.get(category, 0) + size
            if time.monotonic() - self.last_write >= .25:self.save()

    def __call__(self, row):
        with self.lock:
            name = row.get('stage', self.value['stage'])
            if name != self.value['stage']:
                self.phase(name)
            self.value.update({k: v for k, v in row.items() if k != 'stage'})
            if time.monotonic() - self.last_write >= .2 or row.get('bytes') == row.get('total_bytes'):
                self.save()
