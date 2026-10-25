"""Expose the body's input-and-read command without a second model round trip."""
import json
import hashlib
import os
from pathlib import Path
import sys
import threading
import time

NOTE = (" For computer control, step performs one known input events batch and reads the resulting window in ONE call. "
        "Use action=step with a stable idempotency_key, expected_foreground (or hwnd), expected_pid, events, optional settle_ms and read limits. "
        "Check ok/input and observed separately: failed observation is not failed input. Do not repeat input after a lost response. "
        "Use separate observations when the next action depends on what appears.")


def install(agent, backend, *, platform=None, device_id=lambda: "local", tree=None):
    platform = platform or sys.platform
    if platform != "win32" and platform != "darwin" and not platform.startswith("linux"): return False
    impl = getattr(agent, "TOOL_IMPL", None)
    original = impl.get("computer") if isinstance(impl, dict) else None
    if not callable(original) or getattr(original, "_helene_step", False): return False
    bindings = {}
    binding_lock = threading.Lock()

    def bind_intent(identity, payload):
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        with binding_lock:
            if tree is None:
                previous = bindings.setdefault(identity, fingerprint)
            else:
                path = Path(tree) / 'memory/.state/computer-steps' / (identity + '.json')
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with path.open('x', encoding='utf8') as file:
                        json.dump({'fingerprint': fingerprint}, file)
                        file.flush(); os.fsync(file.fileno())
                    previous = fingerprint
                except FileExistsError:
                    # A partial binding after a crash fails closed. Never guess
                    # whether its input was already dispatched.
                    previous = json.loads(path.read_text(encoding='utf8')).get('fingerprint')
            return previous == fingerprint

    def hand(*args, **kwargs):
        action = str(kwargs.get("action") or (args[0] if args else "")).lower().strip()
        if action != "step":
            result = original(*args, **kwargs)
            return result + NOTE if action == "help" and isinstance(result, str) else result
        if args and (len(args) != 1 or 'action' in kwargs):
            return 'step: лишние или повторные позиционные аргументы; действие не выполнено'
        allowed = {'action', 'execution', 'idempotency_key', 'expected_foreground', 'hwnd',
                   'expected_pid', 'events', 'read', 'settle_ms', 'inter_event_delay_ms'}
        unknown = sorted(set(kwargs) - allowed)
        if unknown: return 'step: неизвестные поля ' + ', '.join(unknown) + '; действие не выполнено'
        if str(kwargs.get("execution") or "interactive") != "interactive": return "step: нужен интерактивный рабочий стол"
        if not all(agent._computer_allowed(scope) for scope in ("computer.apps", "computer.read")):
            return "step: владелец должен выдать оба права — управление вводом и чтение компьютера"
        key = kwargs.get("idempotency_key")
        if not isinstance(key, str) or not key.strip() or len(key) > 2048:
            return "step: нужен собственный стабильный idempotency_key; при повторе того же намерения сохрани ключ и аргументы"
        actor = agent._computer_actor()
        if not actor: return "step: действующий участник не опознан"
        identity = hashlib.sha256(json.dumps(['helene-step', actor, device_id(), key], ensure_ascii=False).encode()).hexdigest()
        ids = {"request_id": f"step-{identity}", "operation_id": f"step-op-{identity}"}
        target = kwargs.get("expected_foreground") or kwargs.get("hwnd")
        if not target: return "step: нужен hwnd/expected_foreground из свежего списка окон"
        events = kwargs.get("events")
        if not isinstance(events, list) or not events: return "step: events — непустой массив известных действий"
        read = kwargs.get("read")
        if read is not None and not isinstance(read, dict): return "step: read — объект пределов чтения"
        read = dict(read or {})
        read.setdefault("hwnd", target)
        read_fields = {'hwnd', 'shape', 'max_nodes', 'max_depth', 'max_children_per_node',
                       'max_text_chars', 'timeout_ms', 'backend', 'text_contains', 'visible_only'}
        if set(read) - read_fields: return 'step: неизвестные поля read; действие не выполнено'
        for name in ('max_nodes', 'max_depth', 'max_children_per_node', 'max_text_chars', 'timeout_ms'):
            if name in read and (type(read[name]) is not int or not 0 <= read[name] < 2**64):
                return f'step: read.{name} — неотрицательное целое; действие не выполнено'
        if 'shape' in read and read['shape'] not in ('tree', 'flat'): return 'step: read.shape — tree или flat'
        if 'backend' in read and read['backend'] not in ('auto', 'win32'): return 'step: read.backend — auto или win32'
        if 'text_contains' in read and not isinstance(read['text_contains'], str): return 'step: read.text_contains — строка'
        if 'visible_only' in read and type(read['visible_only']) is not bool: return 'step: read.visible_only — boolean'
        try:
            value = read['hwnd']
            handle = int(value, 16 if value.lower().startswith('0x') else 10) if isinstance(value, str) else value
            if type(handle) is not int or not 0 < handle < 2**64: raise ValueError
        except (TypeError, ValueError, OverflowError, AttributeError):
            return 'step: read.hwnd — положительный идентификатор окна; действие не выполнено'
        if platform != 'win32' and read.get('backend') == 'win32':
            return 'step: backend=win32 недоступен на этой системе; действие не выполнено'
        delay = kwargs.get('inter_event_delay_ms', 0)
        if type(delay) is not int or not 0 <= delay <= 30_000: return 'step: inter_event_delay_ms — целое от 0 до 30000'
        pid = kwargs.get('expected_pid')
        if pid is not None and (type(pid) is not int or not 0 < pid < 2**32): return 'step: expected_pid — положительное целое'
        settle = kwargs.get("settle_ms", 120)
        if isinstance(settle, bool) or not isinstance(settle, int) or not 0 <= settle <= 5000:
            return "step: settle_ms — целое число от 0 до 5000"
        payload = {"input": {"events": events, "expected_foreground": target,
                             "inter_event_delay_ms": delay}, "read": read}
        if kwargs.get("expected_pid"): payload["input"]["expected_pid"] = kwargs["expected_pid"]
        payload["settle_ms"] = settle
        try:
            if not bind_intent(identity, payload):
                return 'step: ключ уже связан с другим полным намерением; действие не выполнено'
        except (OSError, ValueError, TypeError, AttributeError) as error:
            return f'step: привязка намерения не подтверждена ({type(error).__name__}); действие не выполнено'
        import owner_stop
        if owner_stop.stopped(): return 'step: аварийный стоп включён; действие не выполнено'
        # Keep the transport timeout strictly above the body's admitted read/settle budgets.
        if platform == "win32":
            result = backend("desktop.input.perform_and_read", payload, timeout=95.0, **ids)
        else:
            # Native POSIX adapters already expose these two primitives; keep one
            # model call even when there is no combined command in that driver.
            input_ids = {k: v + "-input" for k, v in ids.items()}
            input_result = backend("desktop.input.perform", payload["input"], timeout=60.0, **input_ids)
            if not input_result.get("ok"):
                return json.dumps(input_result, ensure_ascii=False, separators=(",", ":"))
            if settle: time.sleep(settle / 1000)
            if owner_stop.stopped() or not agent._computer_allowed('computer.read'):
                return json.dumps({'ok': True, 'input': input_result, 'observed': False,
                    'observation': {'error': 'Чтение отменено: стоп или право отозвано. Ввод уже выполнен.'}}, ensure_ascii=False)
            read_ids = {k: v + "-read" for k, v in ids.items()}
            read_timeout = max(30.0, min(read.get('timeout_ms') or 5000, 30000) / 1000 + 5.0)
            observation = backend("desktop.window.read", read, timeout=read_timeout, **read_ids)
            result = {"ok": True, "input": input_result, "observed": observation.get("ok") is True,
                      "observation": {"kind": "window_read", "read": observation}, "settled_ms": settle,
                      "local_calls": 2, "semantics": "Input and reading are separate operations inside one model tool call. Failed observation does not undo or repeat input."}
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    hand._helene_step = True
    hand._helene_body = True
    hand.__wrapped__ = original
    impl["computer"] = hand
    if getattr(agent, "tool_computer", None) is original: agent.tool_computer = hand
    seen = set()
    for attr in ("BASE_TOOLS", "OWNER_TOOLS", "PRAXIS_SELF_TOOLS", "TOOLS"):
        for tool in getattr(agent, attr, []) or []:
            if not isinstance(tool, dict) or tool.get("name") != "computer" or id(tool) in seen: continue
            seen.add(id(tool))
            tool["description"] = str(tool.get("description") or "") + NOTE
            props = tool.get("params") or (tool.get("input_schema") or {}).get("properties")
            if not isinstance(props, dict): continue
            if isinstance(props.get("action"), dict) and isinstance(props["action"].get("enum"), list):
                if "step" not in props["action"]["enum"]: props["action"]["enum"].append("step")
            props["settle_ms"] = {"type": "integer", "description": "step: explicit settling delay, 0..5000ms; default 120"}
            props["idempotency_key"] = {"type": "string", "description": "act_element/step: your stable key. Same intent retry must reuse this key and exact arguments; a new intent needs a new key."}
            props["read"] = {"type": "object", "description": "step: result window and native accessibility read limits; defaults to the input target", "properties": {
                "hwnd": {"type": "string"}, "shape": {"type": "string", "enum": ["tree", "flat"]},
                "max_nodes": {"type": "integer"}, "max_depth": {"type": "integer"}, "max_text_chars": {"type": "integer"},
                "max_children_per_node": {"type": "integer"}, "text_contains": {"type": "string"}, "visible_only": {"type": "boolean"},
                "timeout_ms": {"type": "integer"}, "backend": {"type": "string", "enum": ["auto", "win32"] if platform == 'win32' else ["auto"]}}, "additionalProperties": False}
    return True
