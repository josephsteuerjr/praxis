"""Exercise the role choice and real model-step worker against a local API."""
import contextlib
import http.server
import json
import threading

import llm
import pytest
import model_step
from openai import OpenAI


def test_service_tier_reaches_physical_request(tmp_path, monkeypatch):
    seen = []
    state = {'advertised': True, 'served': 'default', 'fail': None, 'gets': 0}

    class Endpoint(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            assert self.path == '/v1/models'
            state['gets'] += 1
            payload = json.dumps({'data': [{'id': 'gpt-6.2-sol'}]}).encode()
            self.send_response(200)
            if state['advertised']:
                self.send_header('x-praxis-relay-service-tier', '1')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            expected_path = '/v1/praxis-speed-v1/chat/completions' if 'service_tier' in body else '/v1/chat/completions'
            assert self.path == expected_path
            seen.append(body)
            if state['fail'] == '404':
                self.send_error(404)
                return
            body = {'id': 'speed-probe', 'object': 'chat.completion.chunk',
                    'created': 0, 'model': 'gpt-6.2-sol',
                    'choices': [{'index': 0, 'delta': {'content': 'ok'}, 'finish_reason': 'stop'}]}
            if state['fail'] == 'upstream_error':
                body['choices'][0].update(delta={'content': 'relay diagnosis'}, finish_reason='upstream_error')
            if state['served']:
                body['service_tier'] = state['served']
            packets = [body]
            if isinstance(state['fail'], tuple):
                finish, prefix = state['fail']
                body['relay_terminal'] = {'code': 'upstream_torn', 'message': 'relay diagnosis'}
                body['choices'][0].update(delta={'content': 'relay diagnosis'}, finish_reason=finish)
                if prefix:
                    packets.insert(0, {**body, 'relay_terminal': None, 'choices': [
                        {'index': 0, 'delta': {'content': 'half'}, 'finish_reason': None}]})
            payload = (''.join('data: ' + json.dumps(packet) + '\n\n' for packet in packets)
                       + 'data: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    endpoint = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{endpoint.server_port}/v1'
    config = {'frameworks': {'openai': {'base_url': base, 'api_key': 'test-only'}}}
    monkeypatch.delenv('PRAXIS_TEST', raising=False)
    monkeypatch.setenv('NO_PROXY', '127.0.0.1')
    monkeypatch.setattr(llm, '_MODELS_CACHE', {})
    monkeypatch.setattr(llm, '_config', lambda: config)
    request = dict(system='fixture', messages=[{'role': 'user', 'content': 'hi'}],
                   tools=None, max_tokens=20, thinking=None, reasoning_effort='low')
    try:
        assert llm._available_models('openai') == ['gpt-6.2-sol']
        attempts = (('', None, 'default'), ('default', None, 'default'), ('fast', 'fast', 'default'),
                    ('ultrafast', 'ultrafast', 'ultrafast'), ('ultrafast', None, None))
        for i, (selected, expected, served) in enumerate(attempts):
            role = {'model': 'gpt-6.2-sol', 'service_tier': selected}
            kwargs = llm._primary_service_tier(role, 'openai', role['model'])
            assert not llm._primary_service_tier(role, 'anthropic', role['model'])
            assert not llm._primary_service_tier(role, 'openai', 'gpt-6.2-astra')
            assert not llm._primary_service_tier({**role, 'model': 'gpt-old'}, 'openai', 'gpt-old')
            if i == 4:
                # Same route address, different credential after preparation.
                config['frameworks']['openai']['api_key'] = 'new-test-only'
            state['served'] = served
            result = model_step.call('openai', role['model'], {**request, **kwargs}, config,
                                     run_id='run-speed-test', base=tmp_path, single_attempt=True)
            assert result.text == 'ok'
            assert result.provider_observation.get('service_tier') == served
            assert seen[-1].get('service_tier') == expected
            assert seen[-1]['reasoning_effort'] == 'low'
        llm._available_models('openai')
        config['roles'] = {'voice': {**role, 'framework': 'openai'}}
        monkeypatch.setattr(llm, 'vision_prepass_enabled', lambda: False)
        def worker(framework, model, *, retries, _resolved, **kwargs):
            reply = model_step.call(framework, model, kwargs, config,
                                    run_id='run-speed-test', base=tmp_path, single_attempt=True)
            return reply, 0
        monkeypatch.setattr(llm, '_call_retrying_empty', worker)
        gets = state['gets']
        state['fail'] = 'upstream_error'
        with pytest.raises(RuntimeError) as denied:
            llm.chat('voice', messages=request['messages'])
        assert type(denied.value).__name__ == 'SpeedRequestRejected'
        assert llm._endpoint_hold('openai') is None
        state['fail'] = '404'
        with pytest.raises(RuntimeError) as missing:
            llm.chat('voice', messages=request['messages'])
        assert missing.value.status_code == 404
        assert 'openai' not in llm._MODELS_CACHE
        assert len(seen) == 7
        state['fail'] = None
        state['advertised'] = False
        assert llm.chat('voice', messages=request['messages']).text == 'ok'
        assert state['gets'] == gets + 1
        assert 'service_tier' not in seen[-1]
        assert not llm._primary_service_tier(role, 'openai', role['model'])
        config['frameworks']['openai']['base_url'] = 'https://api.openai.com/v1'
        assert llm._primary_service_tier({**role, 'service_tier': 'fast'}, 'openai', role['model'])['service_tier'] == 'fast'
        assert not llm._primary_service_tier(role, 'openai', role['model'])
        assert len(seen) == 8
        config['frameworks']['openai']['base_url'] = base
        state['advertised'] = True
        llm._MODELS_CACHE.clear()
        llm._available_models('openai')
        state['fail'] = '404'
        calls_before = len(seen)
        discoveries_before = state['gets']
        captured_b = []

        def delayed_a(framework, model, *, retries, _resolved, **kw):
            try:
                return worker(framework, model, retries=retries, _resolved=_resolved, **kw)
            except Exception as exc:
                assert exc.status_code == 404
                # The real worker has returned A's 404. Warm B before the parent handles it.
                config['frameworks']['openai']['api_key'] = 'rotated-slot-B-fixture'
                state['fail'] = None
                llm._available_models('openai')
                captured_b.append(llm._MODELS_CACHE['openai'])
                raise

        monkeypatch.setattr(llm, '_call_retrying_empty', delayed_a)
        with pytest.raises(RuntimeError) as late:
            llm.chat('voice', messages=request['messages'])
        assert late.value.status_code == 404
        assert len(seen) == calls_before + 1
        assert len(captured_b) == 1 and llm._MODELS_CACHE['openai'] is captured_b[0]
        assert state['gets'] == discoveries_before + 1
        monkeypatch.setattr(llm, '_call_retrying_empty', worker)
        assert llm.chat('voice', messages=request['messages']).text == 'ok'
        assert len(seen) == calls_before + 2
        assert seen[-1]['service_tier'] == 'ultrafast'
        assert state['gets'] == discoveries_before + 1
        config['roles']['voice'].update(fallback_framework='openai', fallback_model='gpt-6.2-sol')

        def worker_with_retries(framework, model, *, retries, _resolved, **kwargs):
            reply = model_step.call(framework, model, kwargs, config,
                                    run_id='run-speed-test', base=tmp_path, single_attempt=False)
            return reply, 0

        monkeypatch.setattr(llm, '_call_retrying_empty', worker_with_retries)
        for finish in ('error', 'upstream_torn'):
            for prefix in (False, True):
                state['fail'] = (finish, prefix)
                before = len(seen)
                expected = llm.SpeedStreamRejected if prefix else llm.SpeedRequestRejected
                with pytest.raises(expected) as failure:
                    llm.chat('voice', messages=request['messages'])
                assert not llm._fallbackable(failure.value)
                assert len(seen) == before + 1
                assert seen[-1]['service_tier'] == 'ultrafast'
                assert llm._endpoint_hold('openai') is None
                if prefix:
                    assert failure.value.partial.text == 'half'
                    assert failure.value.partial.stop_reason == 'error'
        state['fail'] = None
        config['frameworks']['openai']['base_url'] = 'https://api.openai.com/v1'
        config['frameworks']['openai']['api_key'] = 'new-test-only'
        standard = {**role, 'service_tier': 'default'}
        wire = []
        import httpx
        def direct(request):
            wire.append(json.loads(request.content))
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"service_tier":"default"}\n\ndata: [DONE]\n\n', headers={'Content-Type':'text/event-stream'})
        cli = OpenAI(api_key='new-test-only', base_url=config['frameworks']['openai']['base_url'],
                     http_client=httpx.Client(transport=httpx.MockTransport(direct)))
        try:
            for role_config in (standard, {'model': role['model']}):
                kwargs = llm._primary_service_tier(role_config, 'openai', role['model'])
                assert llm._call_openai(cli, role['model'], **request, **kwargs).text == 'ok'
        finally:
            cli.close()
        assert [body.get('service_tier') for body in wire] == ['default', None]
    finally:
        with contextlib.suppress(Exception):
            model_step.close_run('run-speed-test', tmp_path)
        endpoint.shutdown()
        endpoint.server_close()
        thread.join(timeout=2)


def test_normalization_preserves_only_recognized_speed():
    for tier in ('default', 'fast', 'ultrafast', '', 'ultra', ['fast']):
        cfg = llm._normalize({'roles': {'voice': {'service_tier': tier, 'reasoning_effort': 'high'}}})
        assert cfg['roles']['voice'].get('service_tier') == (tier if tier in ('default', 'fast', 'ultrafast') else None)
        assert cfg['roles']['voice']['reasoning_effort'] == 'high'


def test_model_override_rotating_back_does_not_purchase_role_tier(monkeypatch):
    model = 'gpt-6.2-sol'
    role = {'framework': 'openai', 'model': model, 'service_tier': 'fast'}
    config = {'roles': {'voice': role}, 'frameworks': {'openai': {'base_url': 'https://api.openai.com/v1'}}}
    sent = []
    class Captured(BaseException):
        pass
    def capture(*a, **kw):
        sent.append(kw)
        raise Captured
    monkeypatch.setattr(llm, '_config', lambda: config)
    monkeypatch.setattr(llm, '_resolve_requested_model', lambda *a: ('openai', model))
    monkeypatch.setattr(llm, '_endpoint_hold', lambda *a: None)
    monkeypatch.setattr(llm, 'vision_prepass_enabled', lambda: False)
    monkeypatch.setattr(llm, '_call_retrying_empty', capture)
    for override, expected in [('formerly-listed', None), (model, 'fast')]:
        with pytest.raises(Captured):
            llm.chat('voice', messages=[{'role':'user', 'content':'hi'}], model=override)
        assert sent[-1].get('service_tier') == expected


@pytest.mark.parametrize('code', ['upstream_error', 'speed_request_rejected', 'upstream_torn'])
def test_speed_terminal_without_choices_keeps_partial_in_worker_envelope(code):
    from types import SimpleNamespace as Packet
    prefix = Packet(choices=[Packet(delta=Packet(content='half'), finish_reason=None)])
    terminal = Packet(choices=[], relay_terminal={'code': code})
    with pytest.raises(llm.SpeedStreamRejected) as error:
        llm._openai_from_stream(iter([prefix, terminal]), 'fixture', speed_route=True)
    with pytest.raises(llm.SpeedStreamRejected) as result:
        model_step._raise(model_step._error(error.value))
    assert result.value.partial.text == 'half'
    assert not llm._fallbackable(result.value)


def test_speed_models_dump_written_on_real_import(tmp_path):
    """Импорт llm в чистом процессе пишет дамп скоростных списков.

    Свежий PRAXIS_BASE без существующего memory/.state: дамп обязан появиться
    при самом импорте (mkdir по пути), совпадать с живыми константами и не
    оставлять мусорного .tmp. Это контракт кнопок «Система» в миниаппе."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    env = {"PRAXIS_BASE": str(tmp_path), "PYTHONPATH": str(Path(llm.__file__).parent),
           "PYTHONDONTWRITEBYTECODE": "1", "PATH": os.environ.get("PATH", ""),
           "HOME": str(tmp_path)}
    subprocess.run([sys.executable, "-c", "import llm"], env=env, check=True,
                   cwd=str(tmp_path))
    state = tmp_path / "memory" / ".state"
    payload = json.loads((state / "speed-models.json").read_text(encoding="utf8"))
    assert payload["schema"] == 1
    assert payload["source"] == "llm.py"
    assert set(payload["fast"]) == set(llm._FAST_MODELS)
    assert set(payload["ultrafast"]) == set(llm._ULTRAFAST_MODELS)
    assert payload["fast"] == sorted(payload["fast"])
    assert not list(state.glob(".tmp-speed-models.json"))


def test_speed_models_dump_idempotent_rewrite(tmp_path, monkeypatch):
    """Повторный вызов (рестарт мозга) перезаписывает тот же дамп без остатка."""
    state = tmp_path / "memory" / ".state"
    state.mkdir(parents=True)
    monkeypatch.setattr(llm, "USAGE_PATH", state / "usage.json")
    first = llm._speed_models_dump()
    second = llm._speed_models_dump()
    assert first is second is None
    dump = state / "speed-models.json"
    payload = json.loads(dump.read_text(encoding="utf8"))
    assert set(payload["ultrafast"]) == set(llm._ULTRAFAST_MODELS)
    assert not list(state.glob(".tmp-speed-models.json"))


def test_speed_models_dump_environment_failure_is_quiet_with_trace(tmp_path, monkeypatch, caplog):
    """Мягкий отказ среды не роняет импорт, но оставляет след и чистит .tmp."""
    import logging
    state = tmp_path / "memory" / ".state"
    state.mkdir(parents=True)
    monkeypatch.setattr(llm, "USAGE_PATH", state / "usage.json")
    def read_only(*args, **kwargs):
        raise OSError("read-only mount")
    monkeypatch.setattr(llm.os, "replace", read_only)
    with caplog.at_level(logging.DEBUG):
        llm._speed_models_dump()  # не поднимает
    assert any("speed-models" in record.message for record in caplog.records)
    assert not list(state.glob(".tmp-speed-models.json"))


def test_speed_models_dump_does_not_swallow_guard_or_interrupts(tmp_path, monkeypatch):
    """Отказ стража живого дерева (RuntimeError) и прерывания проходят наружу.

    Ревью 2a989425 (F1): `except Exception: pass` превращал громкий сигнал
    _sandbox о порядке импорта в вечную тишину. Теперь глотается только OSError."""
    from unittest import mock
    state = tmp_path / "memory" / ".state"
    state.mkdir(parents=True)
    monkeypatch.setattr(llm, "USAGE_PATH", state / "usage.json")
    class GuardRefusal(RuntimeError):
        pass
    for failure in (GuardRefusal("live tree write refused"), KeyboardInterrupt()):
        with mock.patch.object(llm.Path, "write_text", side_effect=failure):
            with pytest.raises((GuardRefusal, KeyboardInterrupt)):
                llm._speed_models_dump()


def test_call_trace_records_service_tiers(tmp_path, monkeypatch):
    """След вызова несёт тиры скоростной линии — как их читает deskctl «Системы».

    Развёрнутый deskctl показывает «подтверждение режима» из хвоста
    llm_calls.jsonl (service_tier_requested / service_tier_actual). Тест
    закрепляет контракт: оба поля пишутся после вызова с тирами, обычный
    вызов не несёт ни одного, значения обрезаны до 16 знаков."""
    state = tmp_path / "memory" / ".state"
    state.mkdir(parents=True)
    trace = state / "llm_calls.jsonl"
    monkeypatch.setattr(llm, "USAGE_PATH", state / "usage.json")
    monkeypatch.setattr(llm, "_CALL_TRACE", trace)

    llm._call_trace("evaluator", "gpt-6.1-sol", ok=True, cached=0, prompt=10,
                    out_tokens=2, latency_ms=12.5,
                    tier_requested="fast", tier_actual="priority")
    rows = [json.loads(line) for line in trace.read_text(encoding="utf8").splitlines()]
    assert rows[-1]["service_tier_requested"] == "fast"
    assert rows[-1]["service_tier_actual"] == "priority"

    # Обычный вызов: ни одного tier-ключа в строке.
    llm._call_trace("voice", "glm-5.3", ok=True, cached=0, prompt=10,
                    out_tokens=2, latency_ms=12.5)
    rows = [json.loads(line) for line in trace.read_text(encoding="utf8").splitlines()]
    assert "service_tier_requested" not in rows[-1]
    assert "service_tier_actual" not in rows[-1]

    # Терминальная нога (end_after_spoken): запрошенный есть, наблюдённого нет.
    llm._call_trace("evaluator", "gpt-6.1-sol", ok=True, cached=0, prompt=0,
                    out_tokens=0, latency_ms=3.0, error="end_after_spoken",
                    tier_requested="ultrafast")
    rows = [json.loads(line) for line in trace.read_text(encoding="utf8").splitlines()]
    assert rows[-1]["service_tier_requested"] == "ultrafast"
    assert "service_tier_actual" not in rows[-1]

    # Чудовищно длинное значение не раздувает строку следа.
    llm._call_trace("evaluator", "gpt-6.1-sol", ok=True, cached=0, prompt=1,
                    out_tokens=1, latency_ms=1.0,
                    tier_requested="f" * 99, tier_actual="p" * 99)
    rows = [json.loads(line) for line in trace.read_text(encoding="utf8").splitlines()]
    assert rows[-1]["service_tier_requested"] == "f" * 16
    assert rows[-1]["service_tier_actual"] == "p" * 16


def test_chat_writes_tiers_to_trace_only_when_actually_sent(tmp_path, monkeypatch):
    """Интеграция chat() → _call_trace: тир в следе только по факту провода.

    Ревью a5a6942c F1: если binding клиента сменился после выбора тира, запрос
    уходит БЕЗ тира — и след не имеет права называть его запрошенным.
    Интеграция на локальном relay-сервере, как первый тест файла."""
    import http.server
    import threading

    state = {'served': 'priority'}
    seen = []

    class Endpoint(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            payload = json.dumps({'data': [{'id': 'gpt-6.2-sol'}]}).encode()
            self.send_response(200)
            self.send_header('x-praxis-relay-service-tier', '1')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            packet = {'id': 'trace-probe', 'object': 'chat.completion.chunk',
                      'created': 0, 'model': 'gpt-6.2-sol',
                      'choices': [{'index': 0, 'delta': {'content': 'ok'}, 'finish_reason': 'stop'}],
                      'service_tier': state['served']}
            payload = ('data: ' + json.dumps(packet) + '\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    endpoint = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    threading.Thread(target=endpoint.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{endpoint.server_port}/v1'
    config = {'frameworks': {'openai': {'base_url': base, 'api_key': 'test-only'}}}

    state_dir = tmp_path / "memory" / ".state"
    state_dir.mkdir(parents=True)
    trace = state_dir / "llm_calls.jsonl"
    monkeypatch.delenv('PRAXIS_TEST', raising=False)
    monkeypatch.setenv('NO_PROXY', '127.0.0.1')
    monkeypatch.setattr(llm, '_MODELS_CACHE', {})
    monkeypatch.setattr(llm, '_config', lambda: config)
    monkeypatch.setattr(llm, 'USAGE_PATH', state_dir / 'usage.json')
    monkeypatch.setattr(llm, '_CALL_TRACE', trace)
    monkeypatch.setattr(llm, 'vision_prepass_enabled', lambda: False)
    config['roles'] = {'voice': {'framework': 'openai', 'model': 'gpt-6.2-sol',
                                 'service_tier': 'ultrafast'}}

    def trace_rows():
        return [json.loads(line) for line in trace.read_text(encoding='utf8').splitlines()]

    # 1. Тир ушёл на провод: оба поля в следе, actual от провайдера.
    assert llm.chat('voice', messages=[{'role': 'user', 'content': 'hi'}]).text == 'ok'
    assert seen[-1].get('service_tier') == 'ultrafast'
    assert trace_rows()[-1]['service_tier_requested'] == 'ultrafast'
    assert trace_rows()[-1]['service_tier_actual'] == 'priority'

    # 2. Binding сменился ПОСЛЕ подготовки тира, ДО создания клиента (ревью F1):
    #    kwargs несут service_tier_binding старого ключа, а клиент уже с новым —
    #    mismatch, тир не уходит на провод, в следе НИ ОДНОГО tier-поля.
    def rotating_binding(framework, model, *, retries, _resolved, **kwargs):
        assert 'service_tier' in kwargs and 'service_tier_binding' in kwargs
        config['frameworks']['openai']['api_key'] = 'rotated-slot'
        return model_step.call(framework, model, kwargs, config,
                               run_id='run-trace-f1', base=tmp_path, single_attempt=True), 0
    monkeypatch.setattr(llm, '_call_retrying_empty', rotating_binding)
    assert llm.chat('voice', messages=[{'role': 'user', 'content': 'hi'}]).text == 'ok'
    assert seen[-1].get('service_tier') is None, "тир не должен дойти до провода при смене binding"
    row = trace_rows()[-1]
    assert 'service_tier_requested' not in row, (
        "тир не ушёл на провод — след не имеет права называть его запрошенным (F1)")
    assert 'service_tier_actual' not in row
    endpoint.shutdown()
