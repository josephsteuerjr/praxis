"""Application seam for service tiers in preserved authored LLM modules."""
from contextvars import ContextVar
from functools import wraps
import json
import re
from pathlib import Path
import time
import threading
from types import SimpleNamespace
from .private_files import write_private

_CALL = ContextVar('helene_service_tier', default=None)
_STATUS_LOCK = threading.Lock()


def normalize(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ValueError('service_tier must be auto, default or priority')
    value = value.strip().lower()
    value = 'priority' if value == 'fast' else value
    if value not in ('auto', 'default', 'priority'):
        raise ValueError('service_tier must be auto, default or priority')
    return value


def codex(spec):
    return (str(spec.get('provider') or '').casefold() == 'relay'
            and str(spec.get('protocol') or 'openai') == 'openai')


def observed(value):
    tier = value.get('service_tier') if isinstance(value, dict) else getattr(value, 'service_tier', None)
    return tier if isinstance(tier, str) else None


def present(value):
    if isinstance(value, dict):
        return 'service_tier' in value
    fields = getattr(value, 'model_fields_set', None)
    if fields is not None:
        return 'service_tier' in fields or 'service_tier' in (getattr(value, 'model_extra', None) or {})
    return 'service_tier' in getattr(value, '__dict__', {})


class _Stream:
    def __init__(self, stream, call):
        self.stream, self.call = stream, call

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __iter__(self):
        for chunk in self.stream:
            if present(chunk):
                self.call['actual'] = observed(chunk)
            response = chunk.get('response') if isinstance(chunk, dict) else getattr(chunk, 'response', None)
            if present(response):
                self.call['actual'] = observed(response)
            yield chunk


class _Endpoint:
    def __init__(self, endpoint, spec):
        self.endpoint, self.spec = endpoint, spec

    def __getattr__(self, name):
        return getattr(self.endpoint, name)

    def create(self, *args, **kwargs):
        call = _CALL.get()
        if call is None:
            return self.endpoint.create(*args, **kwargs)
        tier = call['selected']
        if tier == 'priority' and not codex(self.spec):
            raise ValueError('Fast mode requires a configured Codex subscription route')
        if str(self.spec.get('provider') or '').casefold() not in ('', 'relay', 'openai'):
            tier = None
        if tier is not None:
            kwargs['service_tier'] = tier
        call.update(requested=tier, actual=None, attempted=True)
        response = self.endpoint.create(*args, **kwargs)
        call['actual'] = observed(response)
        if getattr(response, 'choices', None) is not None or getattr(response, 'output', None) is not None:
            return response
        return _Stream(response, call)


class _ForeignEndpoint:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def __getattr__(self, name):
        method = getattr(self.endpoint, name)
        if name not in ('create', 'stream'):
            return method

        def invoke(*args, **kwargs):
            call = _CALL.get()
            if call is not None:
                call.update(requested=None, actual=None, attempted=True)
            return method(*args, **kwargs)
        return invoke


class _Client:
    def __init__(self, client, spec):
        self.client, self.spec = client, spec

    def __getattr__(self, name):
        if name == 'chat':
            return SimpleNamespace(completions=_Endpoint(self.client.chat.completions, self.spec))
        if name == 'responses':
            return _Endpoint(self.client.responses, self.spec)
        if name == 'messages':
            return _ForeignEndpoint(self.client.messages)
        return getattr(self.client, name)


def install(module, tree):
    """Older modules keep their own routing, retries, tools and input history."""
    if getattr(module, 'LLM_SERVICE_TIER_API', 0) >= 1 or getattr(module, '_helene_fast_mode', False):
        return False
    required = ('_normalize', '_config', '_client_for', 'chat')
    if not all(callable(getattr(module, name, None)) for name in required):
        raise RuntimeError('Authored LLM service-tier seam is unavailable')
    original_normalize, original_client, original_chat = module._normalize, module._client_for, module.chat

    @wraps(original_normalize)
    def normalize_config(config):
        result = original_normalize(config)
        for name, value in (config.get('frameworks') or {}).items():
            if name in result.get('frameworks', {}) and isinstance(value, dict):
                for field in ('protocol', 'provider', 'api_mode'):
                    if field in value:
                        result['frameworks'][name][field] = value[field]
        for role, value in (config.get('roles') or {}).items():
            if role not in result.get('roles', {}) or not isinstance(value, dict):
                continue
            tier = normalize(value.get('service_tier'))
            if tier is not None:
                result['roles'][role]['service_tier'] = tier
            framework = result['roles'][role].get('framework')
            effort = value.get('reasoning_effort')
            if codex(result['frameworks'].get(framework) or {}):
                if effort == 'none':
                    result['roles'][role].pop('reasoning_effort', None)
                elif isinstance(effort, str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', effort):
                    result['roles'][role]['reasoning_effort'] = effort
        return result

    def client_for(framework):
        spec = (module._config().get('frameworks') or {}).get(framework) or {}
        call = _CALL.get()
        protocol = spec.get('protocol') or framework
        if call is not None and call['selected'] == 'priority' and not codex(spec):
            raise ValueError('Fast mode requires a configured Codex subscription route')
        client = original_client(framework)
        return _Client(client, spec) if client is not None and protocol in ('openai', 'anthropic') else client

    @wraps(original_chat)
    def chat(role, *args, **kwargs):
        selected = normalize(kwargs.pop('service_tier', None))
        if selected is None:
            selected = normalize(((module._config().get('roles') or {}).get(role) or {}).get('service_tier'))
        cfg = module._config()
        account = kwargs.get('framework') or (cfg.get('roles', {}).get(role) or {}).get('framework')
        spec = (cfg.get('frameworks') or {}).get(account) or {}
        effort = kwargs.get('reasoning_effort')
        if effort is None:
            effort = (cfg.get('roles', {}).get(role) or {}).get('reasoning_effort')
        if codex(spec) and effort and (effort == 'none' or effort not in module.REASONING_EFFORTS):
            raise ValueError('Codex reasoning effort is unsupported by this transport')
        call = {'selected': selected, 'requested': None, 'actual': None, 'attempted': False}
        token = _CALL.set(call)
        try:
            response = original_chat(role, *args, **kwargs)
            response.service_tier_requested = call['requested']
            response.service_tier = call['actual']
            return response
        finally:
            abandoned = getattr(module, 'call_abandoned', lambda: False)()
            if call['attempted'] and not abandoned:
                path = Path(tree) / 'memory/.state/service-tiers.json'
                try:
                    with _STATUS_LOCK:
                        current = json.loads(path.read_text(encoding='utf8')) if path.is_file() else {}
                        if not isinstance(current, dict):
                            current = {}
                        current[role] = {'requested': call['requested'], 'actual': call['actual'], 'at': time.time()}
                        write_private(path, json.dumps(current).encode('utf8'))
                except (OSError, ValueError):
                    pass
            _CALL.reset(token)

    module._normalize, module._client_for, module.chat = normalize_config, client_for, chat
    if hasattr(module, 'REASONING_EFFORTS'):
        module.REASONING_EFFORTS = tuple(dict.fromkeys((*module.REASONING_EFFORTS, 'ultra')))
    if hasattr(module, 'EMBEDDED_RELAY_EFFORTS'):
        module.EMBEDDED_RELAY_EFFORTS = tuple(value for value in getattr(module, 'REASONING_EFFORTS', ()) if value != 'none')
    if isinstance(getattr(module, '_CACHE', None), dict):
        module._CACHE.update(cfg=None, mtime=None)
    module._helene_fast_mode = True
    return True
