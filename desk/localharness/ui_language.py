"""Product-owned native captions; interface language never selects agent stock."""
from __future__ import annotations

import ctypes
from functools import lru_cache
import json
import locale
import os
from pathlib import Path
import re
import sys

from .language_settings import LANGUAGES, interface_language

CATALOGS = Path(__file__).resolve().parents[1] / 'lang'
PARAMETER = re.compile(r'\{([A-Za-z_][A-Za-z_0-9]*)\}')

# Only these application-owned failures cross the native bridge as captions.
# External exception messages never acquire ownership by matching their text.
NATIVE_ERRORS = {
    'channel_missing': (),
    'call_unavailable': (),
    'restore_confirm': (),
    'restore_session': (),
    'linux_migration': (),
    'build_tools': (),
    'agent_missing': (),
    'components_first': (),
    'migration_identity': (),
    'shutdown_incomplete': ('detail',),
    'session_admission': (),
    'root_mismatch': (),
    'window_assets': ('path',),
    'interrupted_migration': (),
    'layout_seed': ('path', 'detail'),
    'layout_candidate': ('path', 'detail'),
    'layout_marker_link': ('path',),
    'layout_marker_tree': ('path',),
    'layout_birth_incomplete': ('path',),
    'layout_marker_file': ('path',),
    'layout_birth_pending': ('path',),
    'layout_birth_state': ('path', 'detail'),
    'layout_folder': ('path', 'detail'),
    'layout_constitution': ('path', 'detail'),
    'layout_skills': ('path', 'detail'),
    'layout_transfer_unreadable': (),
    'layout_transfer_active': (),
    'layout_custody_held': (),
    'layout_custody_previous': (),
    'layout_custody_unconfirmed': (),
    'first_ready': (),
    'first_start_unconfirmed': (),
    'first_action': (),
}


class _NativeCaption:
    def __init__(self, code, legacy, **values):
        if code not in NATIVE_ERRORS or set(values) != set(NATIVE_ERRORS[code]):
            raise ValueError('Invalid native caption parameters')
        if any(type(value) not in (str, int, float) for value in values.values()):
            raise ValueError('Invalid native caption value')
        super().__init__(legacy)
        self.native_caption = {'schema':'helene.native-error.v1','code':code,'values':dict(values)}


class NativeValueError(_NativeCaption, ValueError):
    pass


class NativeRuntimeError(_NativeCaption, RuntimeError):
    pass


class NativeFileNotFoundError(_NativeCaption, FileNotFoundError):
    pass


def native_error(error):
    if not isinstance(error, (NativeValueError, NativeRuntimeError, NativeFileNotFoundError)):
        return None
    value=error.native_caption
    return dict(value, values=dict(value['values']))


def error_text(error, root):
    value=native_error(error)
    if value:
        try:return text('native.app.error.'+value['code'],for_root(root),value['values'])
        except (OSError, ValueError, KeyError, RuntimeError):pass
    return str(error)


def system_tags() -> tuple[str, ...]:
    """Read UI preference order without starting a shell or changing locale."""
    tags = []
    if sys.platform == 'win32':
        try:
            from ctypes import wintypes
            api = ctypes.WinDLL('kernel32', use_last_error=True).GetUserPreferredUILanguages
            api.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.ULONG),
                            wintypes.LPWSTR, ctypes.POINTER(wintypes.ULONG)]
            api.restype = wintypes.BOOL
            count, size = wintypes.ULONG(), wintypes.ULONG()
            if api(8, ctypes.byref(count), None, ctypes.byref(size)) and 0 < size.value <= 32768:
                buffer = ctypes.create_unicode_buffer(size.value)
                if api(8, ctypes.byref(count), buffer, ctypes.byref(size)):
                    tags.extend(part for part in buffer[:size.value].split('\0') if part)
        except (AttributeError, OSError, ValueError):
            pass
    elif sys.platform == 'darwin':
        try:
            from Foundation import NSLocale
            tags.extend(str(tag) for tag in NSLocale.preferredLanguages())
        except (ImportError, AttributeError, TypeError):
            pass
    for key in ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        tags.extend(part.split('.')[0].split('@')[0] for part in os.environ.get(key, '').split(':') if part)
    try:
        current = locale.getlocale()[0]
        if current:
            tags.append(current)
    except (ValueError, TypeError):
        pass
    return tuple(dict.fromkeys(tags))


@lru_cache(maxsize=4)
def _catalog(language: str) -> dict[str, str]:
    if language not in LANGUAGES:
        raise ValueError('Unsupported interface language')
    path = CATALOGS / (language + '.json')
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise RuntimeError('Interface catalog unavailable: ' + language)
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k,v in value.items()):
        raise ValueError('Invalid interface catalog: ' + language)
    return value


def text(key: str, language: str, values: dict | None = None) -> str:
    language = language if language in LANGUAGES else 'en'
    result = next((_catalog(code).get(key) for code in dict.fromkeys((language, 'en', 'ru'))
                   if _catalog(code).get(key)), None)
    if result is None:
        raise KeyError('Unknown interface caption: ' + key)
    if values is None:
        return result
    required = set(PARAMETER.findall(result))
    if required != set(values):
        raise ValueError('Interface caption parameters differ: ' + key)
    return PARAMETER.sub(lambda match: str(values[match[1]]), result)


def _settings(root: Path) -> dict:
    """A failed preference read grants no authority; preparation reports it."""
    path = Path(root) / 'helene.json'
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def for_root(root: Path) -> str:
    """Resolve the current preference on each native gesture, without caching it."""
    return interface_language(_settings(root), system_tags())


PROGRESS_CAPTIONS = [
  {
    "stage": "starting",
    "text": "Подготовка",
    "key": "native.progress.5ea5c68f20"
  },
  {
    "stage": "ready",
    "text": "Открываем агента",
    "key": "native.progress.9f67948724"
  },
  {
    "stage": "error",
    "text": "Не удалось завершить подготовку",
    "key": "native.progress.0cb4762ac4"
  },
  {
    "stage": "cleanup",
    "text": "Убираем старые копии и ярлыки",
    "key": "native.progress.6332e18941"
  },
  {
    "stage": "removing",
    "text": "Удаляем прежние программы",
    "key": "native.progress.85f25b6c99"
  },
  {
    "stage": "stopping",
    "text": "Завершаем прежние установки",
    "key": "native.progress.70547b874c"
  },
  {
    "stage": "reset",
    "text": "Освобождаем данные от прежних запусков",
    "key": "native.progress.f0f5fc27d4"
  },
  {
    "stage": "cleanup",
    "text": "Программа готова · остались прежние файлы",
    "key": "native.progress.7d7de9531f"
  },
  {
    "stage": "stopping",
    "text": "Завершаем прежнюю Hélène",
    "key": "native.progress.055b1fcef0"
  },
  {
    "stage": "completed",
    "text": "Готово",
    "key": "native.progress.ef05d57959"
  },
  {
    "stage": "preparing",
    "text": "Подключаем данные профиля",
    "key": "native.progress.36bcba7dd5"
  },
  {
    "stage": "preparing_relay",
    "text": "Подготовка подключения",
    "key": "native.progress.d15ed962e7"
  },
  {
    "stage": "ready",
    "text": "Готово",
    "key": "native.progress.ef05d57959"
  },
  {
    "stage": "create",
    "text": "Готово",
    "key": "native.progress.ef05d57959"
  }
]


def bootstrap_data(root: Path, *, config=None, tags=None) -> dict:
    config = _settings(root) if config is None else config
    tags = system_tags() if tags is None else tags
    chosen = config.get('lang', '')
    preference = chosen if chosen in ('', *LANGUAGES) else ''
    agent = config.get('agent')
    frame = agent.get('frame_language') if isinstance(agent, dict) else None
    return {'lang': interface_language(config, tags),
            'native_errors': {code:list(fields) for code,fields in NATIVE_ERRORS.items()},
            'preference': preference,
            'system_language': interface_language({}, tags),
            'frame_language': frame if frame in LANGUAGES else None,
            'progress': [dict(row) for row in PROGRESS_CAPTIONS],
            'catalogs': {language: {key:value for key,value in _catalog(language).items()
                                   if key.startswith(('native.', 'language.'))}
                         for language in LANGUAGES}}


def bootstrap_script(root: Path) -> str:
    encoded = json.dumps(bootstrap_data(root), ensure_ascii=False)
    encoded = encoded.replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    return 'window.HELENE_UI = ' + encoded + ';'
