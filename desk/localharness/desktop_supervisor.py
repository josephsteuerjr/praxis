# -*- coding: utf-8 -*-
"""Opt-in Python-owned desktop supervisor (Windows foreground qualification).

Python owns child handles, one Windows Job per generation, restart decisions,
Stop and the per-tree single-instance hold. The native foreground entry can
launch *this one process* but never spawns channel/runner/relay beside it.
Default shell, session-host and SCM paths are unchanged until their own gates.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import json
import msvcrt
import os
from pathlib import Path
from queue import Empty, Queue
import secrets
import signal
import socket
import sys
from threading import Event, Thread
import time
from contextlib import contextmanager

if __package__:
    from .agents import BASE_ID, raisable, read_config
    from . import owner_stop
    from .desktop_process import CustodyFault, JobChild
    from .relay_network import environment as relay_environment
    from .xtts_install import runtime_for
else:
    from agents import BASE_ID, raisable, read_config
    import owner_stop
    from desktop_process import CustodyFault, JobChild
    from relay_network import environment as relay_environment
    from xtts_install import runtime_for


@dataclass
class ChildState:
    role: str
    falls: list[int] = field(default_factory=list)
    retry_at: int = 0
    halted: bool = False
    busy_tries: int = 0
    started_at: int | None = None


class Coordinator:
    """Pure child-exit decision over complete, host-observed snapshots."""
    def __init__(self, roles: dict[str, str]):
        if not roles or any(not isinstance(key, str) or not key or role not in
                            ("channel", "runner", "relay", "xtts") for key, role in roles.items()):
            raise ValueError("invalid child inventory")
        self.children = {key: ChildState(role) for key, role in roles.items()}
        self.last_seq = 0
        self.last_now = -1

    def tick(self, frame: dict) -> list[dict]:
        seq, now, observed = frame.get("seq"), frame.get("now_ms"), frame.get("children")
        if (type(seq) is not int or seq != self.last_seq + 1 or type(now) is not int or
                now < self.last_now or not isinstance(observed, dict) or
                set(observed) != set(self.children)):
            raise ValueError("stale or incomplete child snapshot")
        for value in observed.values():
            if not isinstance(value, dict) or value.get("state") not in ("absent", "alive", "exited", "failed", "waiting"):
                raise ValueError("invalid child observation")
            if value["state"] == "exited" and type(value.get("code")) not in (int, type(None)):
                raise ValueError("invalid child exit code")
        self.last_seq, self.last_now = seq, now
        actions: list[dict] = []
        restart_agents: set[str] = set()
        for key, data in observed.items():
            state = self.children[key]
            status = data["state"]
            if status == "alive":
                if state.started_at is None:
                    state.started_at = now
                continue
            if status == "waiting":
                state.retry_at = now + 20000  # another copy's relay port, not a crash
            elif status in ("exited", "failed"):
                lived = state.started_at is not None and now - state.started_at > 120000
                state.started_at = None
                code = data.get("code") if status == "exited" else None
                if state.role == "runner" and code == 42:
                    state.falls.clear()
                    state.busy_tries = 0
                    state.retry_at = now
                    restart_agents.add(key.split(":", 1)[0])
                elif state.role == "runner" and code in (2, 3):
                    state.halted = True
                elif state.role == "runner" and code == 4:
                    state.busy_tries += 1
                    if state.busy_tries <= 6:
                        state.retry_at = now + 10000
                    else:
                        state.halted = True
                else:
                    if lived:
                        state.falls.clear()
                    state.falls[:] = [at for at in state.falls if now - at < 600000]
                    state.falls.append(now)
                    if len(state.falls) > 5:
                        state.falls.clear()
                        state.retry_at = now + 600000
                    else:
                        state.retry_at = now + (1 << min(len(state.falls) - 1, 5)) * 1000
        for agent in restart_agents:
            for key, state in self.children.items():
                if key.startswith(agent + ":") and state.role == "channel":
                    if observed[key]["state"] == "alive":
                        actions.append({"id": key, "action": "stop"})
                    state.retry_at = now
                    state.halted = False
        for key, state in self.children.items():
            if observed[key]["state"] in ("absent", "exited", "failed") and not state.halted and now >= state.retry_at:
                actions.append({"id": key, "action": "spawn"})
        return actions


@dataclass(frozen=True)
class Spec:
    role: str
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    log: Path
    port: int


def _config(path: Path) -> dict:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("helene.json is not a JSON object")
    return data


def _resolve(base: Path, raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else base / p


def _token(tree: Path) -> str:
    path = tree / "memory/.state/desk-token"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if 16 <= len(value) <= 128 and value.isascii() and value.isalnum():
            return value
        raise ValueError("existing channel token is malformed; refusing to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(24)
    try:
        with path.open("x", encoding="ascii") as out:
            out.write(value)
            out.flush()
            os.fsync(out.fileno())
    except FileExistsError:
        return _token(tree)
    return value


def runner_required(cfg, agent_id):
    """One role-eligibility rule for launch and migration readiness."""
    if cfg.get('setup_complete') is False:return False
    model = cfg.get('model') or {}
    return bool(cfg.get('runner')) and (agent_id == BASE_ID or cfg.get('setup_complete') is True or (
        isinstance(model, dict) and isinstance(model.get('key'), str) and bool(model['key'].strip())))


def _specs(config: Path, *, session_host: bool = False,
           agent_id: str = BASE_ID, port_override: int | None = None,
           window_root: Path | None = None) -> tuple[Path, dict[str, Spec]]:
    cfg = _config(config)
    installed = cfg.get("installed") or {}
    if not isinstance(installed, dict):
        raise ValueError("installed settings are not an object")
    if cfg.get("mode", "local") != "local" or (not session_host and
            installed.get("service") is True):
        raise ValueError("opt-in Python supervisor needs local mode and session-host for a service installation")
    base = config.parent
    tree = _resolve(base, str(cfg.get("tree") or "data"))
    tree.mkdir(parents=True, exist_ok=True)
    # The native loader's literal "python" means PATH, not <config>/python.
    # Once this Python supervisor is running, its own executable is the
    # exact interpreter the native launcher resolved for that PATH request.
    selected_python = cfg.get("python")
    python = Path(sys.executable) if selected_python in (None, "", "python") else _resolve(base, str(selected_python))
    app = _resolve(base, str(cfg.get("app") or "deskapp.py"))
    runner = _resolve(base, str(cfg["runner"])) if cfg.get("runner") else None
    if not python.is_file() or not app.is_file() or (runner is not None and not runner.is_file()):
        raise ValueError("python, channel or runner file missing")
    port = port_override if port_override is not None else cfg.get("port") or 8094
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid channel port")
    token = _token(tree)
    common = dict(os.environ, HELENE_TREE=str(tree), PYTHONUTF8="1", PYTHONUNBUFFERED="1",
                  HELENE_TOKEN=token, HELENE_CONFIG=str(config), HELENE_BROKER_DISABLED='1')
    common.pop('HELENE_WINDOW_ROOT', None)
    if window_root is not None:common['HELENE_WINDOW_ROOT']=str(window_root.resolve())
    common.pop("PRAXIS_DESK_TOKEN", None)
    common.pop("RELAY_API_KEY", None)  # a sibling's ambient relay key is not this agent's authority
    phone = cfg.get("phone") or {}
    lan = isinstance(phone, dict) and phone.get("enabled") is True and phone.get("mode") != "automatic"
    channel_env = dict(common, HELENE_HOST="0.0.0.0" if lan else "127.0.0.1")
    specs = {f"{agent_id}:channel": Spec("channel", (str(python), "-u", str(app), str(port)),
                                  app.parent, channel_env, tree / "deskapp.log", port)}
    if runner:
        # A neighboring agent without a configured model may expose its channel
        # but not silently start a model process (the native roster contract).
        if runner_required(cfg, agent_id):
            specs[f"{agent_id}:runner"] = Spec("runner", (str(python), "-u", str(runner),
                                                "--config", str(config)), runner.parent,
                                    dict(common, HELENE_HOST="127.0.0.1"), tree / "runner.log", port)
    relay = cfg.get("relay") or {}
    images = cfg.get("images") or {}
    if not isinstance(relay, dict) or not isinstance(images, dict):
        raise ValueError("relay/images configuration is not an object")
    relay_on = relay.get("enabled") is True
    images_on = images.get("enabled") is True
    if relay_on or images_on:
        # Neighbor configs live under agents/<id>/, but the one packaged
        # relay executable belongs to the installation, not each data tree.
        install_base = base if agent_id == BASE_ID else base.parent.parent
        exe = install_base / ("helene-relay.exe" if os.name == "nt" else "helene-relay")
        if not exe.is_file():
            raise ValueError("relay requested but executable missing")
        relay_port = relay.get("port") or 5011
        if type(relay_port) is not int or not 1 <= relay_port <= 65535:
            raise ValueError("invalid relay port")
        home = tree / "relay"
        home.mkdir(parents=True, exist_ok=True)
        relay_env = dict(os.environ, RELAY_PORT=str(relay_port), RELAY_LOCAL="1", RELAY_AUTH_DIR=str(home/'local_auth'),
                         RELAY_INSTRUCTIONS=str(relay.get("instructions") or "minimal").strip(),
                         RELAY_TYPED_TERMINAL="field", RELAY_LOG_DIR=str(home / "logs"))
        # An ambient key cannot stand in for the configured key of this tree.
        # Image-only relay must not inherit a provider/channel credential.
        for secret in ("RELAY_API_KEY", "HELENE_TOKEN", "PRAXIS_DESK_TOKEN"):
            relay_env.pop(secret, None)
        # Same separation as common/relay_policy.rs: image-only relay cannot
        # borrow an unrelated model credential; text relay keeps legacy key.
        model = cfg.get("model") or {}
        key = relay.get("key")
        if not isinstance(key, str) or not key.strip():
            key = model.get("key") if relay_on and isinstance(model, dict) else ""
        if isinstance(key, str) and key:
            relay_env["RELAY_API_KEY"] = key
        relay_env["RELAY_PYTHON"] = str(python)
        relay_env.update(relay_environment(relay))
        relay_argv = (str(exe), "--minimized", "--start-api", "--config", str(config.resolve()))
        specs[f"{agent_id}:relay"] = Spec("relay", relay_argv, home,
                                   relay_env, home / "logs/relay-console.log", relay_port)
    # XTTS is a sibling owned by this same process tree, not an agent-spawned
    # model thread or a second daemon outside Stop/owner-death custody.
    voice_cfg = cfg.get("voice") or {}
    if isinstance(voice_cfg, dict) and voice_cfg.get("speak") is True:
        install_base = base if agent_id == BASE_ID else base.parent.parent
        speech_python = runtime_for(install_base) / ("python.exe" if os.name == "nt" else "bin/python3")
        model_home = tree / "models/xtts"
        code = _resolve(base, str(cfg.get("code") or cfg.get("tree") or "data"))
        worker = code / "xtts_tts_worker.py"
        if speech_python.is_file() and worker.is_file() and (model_home / "model.pth").is_file():
            home = tree / "memory/.state/xtts"
            specs[f"{agent_id}:xtts"] = Spec("xtts", (str(speech_python), "-u", str(worker),
                "--home", str(home), "--model", str(model_home), "--device",
                str(voice_cfg.get("tts_device") or "cpu")), tree,
                {"PATH":os.environ.get("PATH", ""), "SYSTEMROOT":os.environ.get("SYSTEMROOT", ""),
                 "PYTHONUTF8":"1"}, tree / "xtts.log", 0)
    return tree, specs


def _plans(config: Path, *, session_host: bool,
           desktop_roster: bool = False) -> dict[str, tuple[Path, dict[str, Spec]]]:
    """One complete desired generation from the installation's roster owner.

    A malformed/remote neighbor is never turned into a default local agent.
    Distinct IDs cannot claim the same physical data tree, even with distinct
    channel ports. Bad neighbor input refuses that neighbor, not the base.
    """
    base = config.parent
    window_root=base if desktop_roster else None
    plans = {BASE_ID: _specs(config, session_host=session_host,window_root=window_root)}
    if not (session_host or desktop_roster):
        return plans
    occupied = {plans[BASE_ID][0].resolve(strict=True)}
    for agent in raisable(base):
        if agent.base:
            continue
        cfg = read_config(agent.config)
        if cfg.get("mode", "local") != "local":
            continue  # Python controller owns its remote publisher, not a local brain.
        try:
            if any(cfg.get(section) is not None and not isinstance(cfg[section], dict)
                   for section in ("agent", "telegram")):
                raise ValueError("agent/telegram settings are not objects")
            tree, specs = _specs(agent.config, session_host=session_host, agent_id=agent.id,
                                 port_override=agent.port,window_root=window_root)
            physical = tree.resolve(strict=True)
            if physical in occupied:
                raise ValueError("another agent already owns this data tree")
            occupied.add(physical)
            plans[agent.id] = tree, specs
        except (OSError, ValueError, TypeError) as exc:
            print(f"[python-supervisor] agent {agent.id} refused: {exc}", file=sys.stderr, flush=True)
    return plans


def _port_busy(port: int) -> bool:
    # This Windows host drops unanswered TCP probes rather than returning
    # ECONNREFUSED: a connection timeout cannot tell free from occupied.
    # Exclusive bind measures the local listener slot instead. The tree lock
    # is an observation only; the shared tree record excludes native owners.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True


def _relay_login_generation(spec: Spec) -> tuple[int, int, bytes] | None:
    """Bounded private marker; relay must restart after a new account login."""
    path = spec.cwd / "local_auth/login-generation"
    try:
        info = path.stat()
        if info.st_size > 1024:
            raise ValueError("relay login marker too large")
        content = path.read_bytes()
        if len(content) != info.st_size:
            raise ValueError("relay login marker changed during read")
        return info.st_mtime_ns, info.st_size, content
    except FileNotFoundError:
        return None


@contextmanager
def _tree_hold(tree: Path):
    """Same exclusive Windows file/record as the native owners.

    The exclusive handle excludes live owners. Persisted words describe the
    previous teardown; they must not turn a crash into a permanent launch ban.
    """
    tree = tree.resolve(strict=True)
    path = tree / ".desktop-owner.lock"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.restype = wintypes.HANDLE
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    # OPEN_REPARSE_POINT refuses following a planted symlink to another file;
    # ordinary tree-local records retain the same share-exclusive handle.
    handle = create(str(path), 0xC0000000, 0, None, 4, 0x80 | 0x00200000, None)  # OPEN_ALWAYS, share=0
    if handle == ctypes.c_void_p(-1).value:
        raise RuntimeError(f"desktop tree owned or lock unavailable: {ctypes.WinError(ctypes.get_last_error())}")
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(wintypes.HANDLE(handle))
        raise
    with os.fdopen(fd, "r+b", buffering=0) as locked:
        if os.stat(fd).st_file_attributes & 0x400:
            raise RuntimeError("desktop tree lock is a reparse point")
        def record(word: bytes) -> None:
            locked.seek(0)
            locked.truncate()
            os.fsync(locked.fileno())
            locked.write(word)
            os.fsync(locked.fileno())
        record(b"active\n")
        yield lambda: record(b"clean\n")


def _desktop_status(tree: Path, nonce: str, seq: int, ack: str | None,
                    installed: dict[str, tuple[Path, dict[str, Spec]]],
                    active: dict[str, JobChild]) -> None:
    """One atomic generation-bound observation, not a promise based on config."""
    path = tree / ".python-desktop-status"
    data = {"schema": 1, "nonce": nonce, "seq": seq, "ack": ack,
            "agents": {agent: {"raised": f"{agent}:channel" in active,
                               "roles": sorted(spec.role for key, spec in specs.items() if key in active),
                               "pids": {spec.role:active[key].pid for key,spec in specs.items() if key in active}}
                       for agent, (_, specs) in installed.items()}}
    temporary = path.with_name(f"{path.name}.{nonce}.{os.getpid()}.{seq}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers briefly hold a handle that can deny replacement of
        # the old pathname (WinError 5/32). Keep the writer's exact temp bytes
        # and retry only that atomic replace for a short bounded interval;
        # a persistent denial remains an error, never an acknowledged status.
        deadline = time.monotonic() + 0.35
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.015)
    finally:
        temporary.unlink(missing_ok=True)


def run(config: Path, *, stop=None, session_host: bool = False,
        desktop_roster: bool = False, status_nonce: str | None = None,
        commands: Queue | None = None) -> int:
    """Run one owner generation; errors never fall back to another spawner."""
    if os.name != "nt":
        raise RuntimeError("Windows Job qualification only; other OSes need separate containment")
    config = config.resolve(strict=True)
    if (stop is not None and stop.is_set()) or owner_stop.stopped():
        raise RuntimeError("owner stop before supervisor preparation")
    if desktop_roster and (session_host or status_nonce is None or len(status_nonce) != 32 or
                           any(c not in "0123456789abcdef" for c in status_nonce)):
        raise ValueError("desktop owner requires a distinct 32-hex generation")
    desired = _plans(config, session_host=session_host, desktop_roster=desktop_roster)
    tree, specs = desired[BASE_ID]
    wake = stop if stop is not None else Event()
    def stopped():
        return wake.is_set() or owner_stop.stopped()
    with _tree_hold(tree) as mark_clean:
        if stopped():
            mark_clean()  # no child was created under this hold
            raise RuntimeError("owner stop is active")
        policy = Coordinator({key: spec.role for key, spec in specs.items()})
        active: dict[str, JobChild] = {}
        auth_seen: dict[str, tuple[int, int, bytes] | None] = {}
        # Keep every agent's OS lease until its last Job has been measured empty.
        # A returned lock handle alone cannot certify an old generation gone.
        holds = {BASE_ID: (None, mark_clean)}
        installed = {BASE_ID: (tree, specs)}
        started = time.monotonic()
        last_refresh = 0.0
        seq = 0
        receipt_seq = 0
        last_status = 0.0
        uncertain = False
        def retire(agent_id: str) -> None:
            for key in list(policy.children):
                if not key.startswith(agent_id + ":"):
                    continue
                child = active.get(key)
                if child is not None:
                    child.stop()  # unknown teardown leaves active marker and stops the owner
                    active.pop(key)
                auth_seen.pop(key, None)
                del policy.children[key]
            ctx, clean = holds[agent_id]
            clean()
            if ctx is not None:
                ctx.__exit__(None, None, None)
            del holds[agent_id]
            installed.pop(agent_id)
        try:
            while not stopped():
                request = None
                if commands is not None:
                    try:
                        request = commands.get_nowait()
                    except Empty:
                        pass
                refresh = session_host or desktop_roster
                if refresh and (request is not None or time.monotonic() - last_refresh >= 2.0):
                    last_refresh = time.monotonic()
                    # Re-read the actual roster/config, not a startup snapshot.
                    # Failure to read the base is terminal rather than an empty
                    # desired set that could release its ownership accidentally.
                    desired = _plans(config, session_host=session_host, desktop_roster=desktop_roster)
                    # Relay network/auth policy belongs to the relay child, not
                    # to the channel or an in-flight agent turn. Keep their Jobs
                    # and retry history when only relay specs were changed.
                    for agent_id in installed.keys() & desired.keys():
                        old_tree, old_specs = installed[agent_id]
                        new_tree, new_specs = desired[agent_id]
                        changed = {key for key in old_specs.keys() | new_specs.keys()
                                   if old_specs.get(key) != new_specs.get(key)}
                        if (not changed or old_tree.resolve(strict=True) != new_tree.resolve(strict=True)
                                or any(spec.role != 'relay' for key in changed
                                       for spec in (old_specs.get(key), new_specs.get(key)) if spec is not None)):
                            continue
                        for key in changed:
                            if key in active:
                                active[key].stop()
                                active.pop(key)
                            auth_seen.pop(key, None)
                            policy.children.pop(key, None)
                            if key in new_specs:
                                policy.children[key] = ChildState('relay')
                        installed[agent_id] = desired[agent_id]
                    for agent_id in list(installed):
                        if agent_id != BASE_ID and installed[agent_id] != desired.get(agent_id):
                            retire(agent_id)
                    if installed[BASE_ID] != desired[BASE_ID]:
                        if installed[BASE_ID][0].resolve(strict=True) != desired[BASE_ID][0].resolve(strict=True):
                            raise RuntimeError("base data tree moved; stop and restart the Python owner to rebind")
                        for key in list(policy.children):
                            if key.startswith(BASE_ID + ":"):
                                child = active.get(key)
                                if child is not None:
                                    child.stop()
                                    active.pop(key)
                                auth_seen.pop(key, None)
                                del policy.children[key]
                        installed[BASE_ID] = desired[BASE_ID]
                        policy.children.update({key: ChildState(spec.role)
                                                for key, spec in desired[BASE_ID][1].items()})
                    for agent_id, (agent_tree, agent_specs) in desired.items():
                        if agent_id in installed:
                            continue
                        ctx = _tree_hold(agent_tree)
                        try:
                            clean = ctx.__enter__()
                        except (OSError, RuntimeError) as exc:
                            print(f"[python-supervisor] agent {agent_id} admission waiting: {exc}",
                                  file=sys.stderr, flush=True)
                            continue
                        holds[agent_id] = ctx, clean
                        installed[agent_id] = agent_tree, agent_specs
                        policy.children.update({key: ChildState(spec.role)
                                                for key, spec in agent_specs.items()})
                specs = {key: spec for _, agent_specs in installed.values()
                         for key, spec in agent_specs.items()}
                for key, spec in specs.items():
                    if spec.role != "relay":
                        continue
                    generation = _relay_login_generation(spec)
                    if generation is not None and key in auth_seen and auth_seen[key] != generation and key in active:
                        active[key].stop()
                        active.pop(key)
                        policy.children[key] = ChildState("relay")
                    if generation is not None or key not in auth_seen:
                        auth_seen[key] = generation
                events = {}
                for key, spec in specs.items():
                    child = active.get(key)
                    if child is None:
                        events[key] = {"state": "absent"}
                    else:
                        code = child.poll()  # raises on unknown Job state, no next spawn
                        if code is None:
                            events[key] = {"state": "alive"}
                        else:
                            active.pop(key)
                            events[key] = {"state": "exited", "code": code}
                seq += 1
                actions = policy.tick({"seq": seq, "now_ms": int((time.monotonic() - started) * 1000),
                                       "children": events})
                for item in actions:
                    key, action = item["id"], item["action"]
                    if action == "stop":
                        child = active.get(key)
                        if child is not None:
                            child.stop()
                            active.pop(key)
                for item in actions:
                    if item["action"] != "spawn": continue
                    key = item["id"]
                    if key in active:
                        raise RuntimeError(f"duplicate live child {key}")
                    spec = specs[key]
                    if stopped(): break
                    # A rival channel on this port means its runner owns the
                    # tree. A relay port belongs to a separate local service.
                    own_channel = key.split(":", 1)[0] + ":channel"
                    if ((spec.role == "runner" and own_channel not in active and _port_busy(spec.port))
                            or (spec.role in ("channel", "relay") and _port_busy(spec.port))):
                        policy.children[key].retry_at = int((time.monotonic() - started) * 1000) + 20000
                        continue
                    try:
                        active[key] = JobChild.spawn(list(spec.argv), cwd=spec.cwd, env=spec.env,
                                                     log=spec.log, stopped=stopped)
                        print(f"[python-supervisor] {key} pid {active[key].pid}", flush=True)
                    except CustodyFault:
                        uncertain = True
                        raise  # unknown physical ownership forbids another generation
                    except (OSError, RuntimeError) as exc:
                        print(f"[python-supervisor] {key} launch refused: {exc}", file=sys.stderr, flush=True)
                        policy.children[key].retry_at = int((time.monotonic() - started) * 1000) + 5000
                if desktop_roster and (request is not None or time.monotonic() - last_status >= 1.0):
                    receipt_seq += 1
                    _desktop_status(tree, status_nonce, receipt_seq, request, installed, active)
                    last_status = time.monotonic()
                wake.wait(.5)
        finally:
            # Child Jobs close even when the Python supervisor itself is killed
            # by the OS (KILL_ON_JOB_CLOSE). Here we also wait for actual death.
            print("[python-supervisor] stopping child Jobs", flush=True)
            failures = []
            for key, child in active.items():
                try: child.stop()
                except (OSError, TimeoutError) as exc: failures.append(f"{key}: {exc}")
            if failures:
                raise RuntimeError("Job teardown unconfirmed: " + "; ".join(failures))
            if not uncertain:
                for agent_id, (ctx, clean) in list(holds.items()):
                    clean()
                    if ctx is not None:
                        ctx.__exit__(None, None, None)
            print("[python-supervisor] child Jobs empty", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="experimental Python-owned Hélène supervisor")
    parser.add_argument("--config", required=True)
    parser.add_argument("--control-stdin", action="store_true",
                        help="private native-launcher Stop pipe; EOF is not a Stop")
    parser.add_argument("--session-host", action="store_true",
                        help="opt-in user-session owner of all local agents; SCM remains broker")
    parser.add_argument("--desktop-roster", action="store_true",
                        help="opt-in foreground window owner of all local agents")
    parser.add_argument("--status-nonce", help="private window generation binding (32 lowercase hex)")
    args = parser.parse_args(argv)
    stop = Event()
    commands = Queue() if args.desktop_roster else None
    def _interrupt(_signal, _frame):
        stop.set()
    signal.signal(signal.SIGINT, _interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    if args.control_stdin:
        from threading import Thread
        def _control():
            while not stop.is_set():
                line = sys.stdin.buffer.readline(512)
                if line == b"stop\n":
                    stop.set()
                    break
                if not line:
                    break  # EOF is not Stop; outer Job still owns this process.
                if commands is None:
                    continue
                try:
                    message = json.loads(line)
                    if (message.get("action") == "refresh" and
                            message.get("nonce") == args.status_nonce and
                            isinstance(message.get("id"), str) and
                            len(message["id"]) == 32 and
                            all(c in "0123456789abcdef" for c in message["id"])):
                        commands.put(message["id"])
                except (ValueError, TypeError, AttributeError):
                    continue
        Thread(target=_control, daemon=True, name="native-stop-reader").start()
    try:
        return run(Path(args.config), stop=stop, session_host=args.session_host,
                   desktop_roster=args.desktop_roster, status_nonce=args.status_nonce,
                   commands=commands)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"[python-supervisor] refused: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
