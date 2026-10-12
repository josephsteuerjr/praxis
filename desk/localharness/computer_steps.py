"""Expose the body's input-and-read command without a second model round trip."""
import json
import hashlib
import sys
import time

NOTE = (" For computer control, step performs one known input events batch and reads the resulting window in ONE call. "
        "Use action=step with a stable idempotency_key, expected_foreground (or hwnd), expected_pid, events, optional settle_ms and read limits. "
        "Check ok/input and observed separately: failed observation is not failed input. Do not repeat input after a lost response. "
        "Use separate observations when the next action depends on what appears.")


def install(agent, backend, *, platform=None, device_id=lambda: "local"):
    platform = platform or sys.platform
    if platform != "win32" and platform != "darwin" and not platform.startswith("linux"): return False
    impl = getattr(agent, "TOOL_IMPL", None)
    original = impl.get("computer") if isinstance(impl, dict) else None
    if not callable(original) or getattr(original, "_helene_step", False): return False

    def hand(*args, **kwargs):
        action = str(kwargs.get("action") or (args[0] if args else "")).lower().strip()
        if action != "step":
            result = original(*args, **kwargs)
            return result + NOTE if action == "help" and isinstance(result, str) else result
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
        identity = hashlib.sha256(f"helene-step:{actor}:{device_id()}:{key}".encode()).hexdigest()
        ids = {"request_id": f"step-{identity}", "operation_id": f"step-op-{identity}"}
        target = kwargs.get("expected_foreground") or kwargs.get("hwnd")
        if not target: return "step: нужен hwnd/expected_foreground из свежего списка окон"
        events = kwargs.get("events")
        if not isinstance(events, list) or not events: return "step: events — непустой массив известных действий"
        read = kwargs.get("read")
        if read is not None and not isinstance(read, dict): return "step: read — объект пределов чтения"
        read = dict(read or {})
        read.setdefault("hwnd", target)
        settle = kwargs.get("settle_ms", 120)
        if isinstance(settle, bool) or not isinstance(settle, int) or not 0 <= settle <= 5000:
            return "step: settle_ms — целое число от 0 до 5000"
        payload = {"input": {"events": events, "expected_foreground": target,
                             "inter_event_delay_ms": kwargs.get("inter_event_delay_ms") or 0}, "read": read}
        if kwargs.get("expected_pid"): payload["input"]["expected_pid"] = kwargs["expected_pid"]
        payload["settle_ms"] = settle
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
            read_ids = {k: v + "-read" for k, v in ids.items()}
            observation = backend("desktop.window.read", read, timeout=30.0, **read_ids)
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
            props["read"] = {"type": "object", "description": "step: result window and UIA read limits; defaults to the input target", "properties": {
                "hwnd": {"type": "string"}, "shape": {"type": "string", "enum": ["tree", "flat"]},
                "max_nodes": {"type": "integer"}, "max_depth": {"type": "integer"}, "max_text_chars": {"type": "integer"},
                "timeout_ms": {"type": "integer"}, "backend": {"type": "string", "enum": ["auto", "win32"]}}, "additionalProperties": False}
    return True
