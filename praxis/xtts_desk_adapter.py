"""Small adapter installed as deskd.xtts_ui in the existing Praxis MiniApp.

The host owns the model and profiles. This process only handles authenticated,
temporary UI requests through the existing XTTS socket.
"""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
import sys
import threading

from . import readers, control

_instance = None
_fish_instance = None
_lock = threading.Lock()


def _get():
    global _instance
    with _lock:
        if _instance is None:
            base = readers.tree()
            source = str(base.resolve())
            if source not in sys.path:
                sys.path.append(source)
            module = importlib.import_module('praxis_voice_ui')
            state = Path(os.environ.get('HELENE_DESK_STATE') or '/desk-state')
            _instance = module.VoiceUI(base, state / 'xtts-preview')
        return _instance



def _get_fish():
    global _fish_instance
    with _lock:
        if _fish_instance is None:
            base = readers.tree()
            source = str(base.resolve())
            if source not in sys.path:
                sys.path.append(source)
            module = importlib.import_module('fish_voice_ui')
            state = Path(os.environ.get('HELENE_DESK_STATE') or '/desk-state')
            _fish_instance = module.FishVoiceUI(base, state / 'fish-preview')
        return _fish_instance

def _fish_handler(action):
    async def handle(call):
        try:
            capability = await asyncio.to_thread(_get_fish)
        except (ImportError, OSError):
            return {'ok': False, 'error': 'unavailable'}
        return await asyncio.to_thread(capability.dispatch, action, call.match, call.body, call.role)
    return handle


def _handler(action):
    async def handle(call):
        # Imports are lazy: an unavailable speech service must not break the app.
        try:
            capability = await asyncio.to_thread(_get)
        except (ImportError, OSError):
            return {'ok': False, 'error': 'unavailable'}
        return await asyncio.to_thread(capability.dispatch, action, call.match, call.body, call.role)
    return handle


def routes(route):
    async def service_state(call):
        return await asyncio.to_thread(control.xtts_service_state)
    async def service_action(call):
        return await asyncio.to_thread(control.xtts_service_action, call.body)
    definitions = (
        ('GET', '/api/xtts/status', 'status', 0),
        ('POST', '/api/xtts/jobs', 'submit', 12 * 1024 * 1024),
        ('GET', '/api/xtts/jobs/{voice_job}', 'job', 0),
        ('POST', '/api/xtts/jobs/{voice_job}/cancel', 'cancel', 4096),
        ('GET', '/api/xtts/jobs/{voice_job}/audio', 'audio', 0),
        ('POST', '/api/xtts/enabled', 'enabled', 4096),
        ('POST', '/api/xtts/voices/{voice_id}/delete', 'delete', 4096),
    )
    speech = (
        ('GET', '/api/speech/status', 'status', 0),
        ('GET', '/api/speech/fish/catalog/{catalog_page}', 'catalog', 0),
        ('POST', '/api/speech/fish/jobs', 'submit', 65536),
        ('GET', '/api/speech/fish/jobs/{voice_job}', 'job', 0),
        ('POST', '/api/speech/fish/jobs/{voice_job}/cancel', 'cancel', 4096),
        ('GET', '/api/speech/fish/jobs/{voice_job}/audio', 'audio', 0),
        ('POST', '/api/speech/selection', 'selection', 65536),
    )
    extra = tuple(route(method, path, _fish_handler(action), max_body_bytes=limit, private=True)
                  for method, path, action, limit in speech)
    return extra + tuple(route(method, path, _handler(action), max_body_bytes=limit, private=True)
                 for method, path, action, limit in definitions) + (
        route('GET', '/api/xtts/service', service_state, private=True),
        route('POST', '/api/xtts/service', service_action, max_body_bytes=4096, private=True),
    )


async def cleanup(_app):
    global _instance, _fish_instance
    with _lock:
        fish, _fish_instance = _fish_instance, None
        capability, _instance = _instance, None
    if fish is not None:
        await asyncio.to_thread(fish.close)
    if capability is not None:
        await asyncio.to_thread(capability.close)
