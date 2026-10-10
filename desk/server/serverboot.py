# -*- coding: utf-8 -*-
"""Надзор за харнессом Hélène на сервере (Linux, Docker) — то, что на Windows
делает оболочка `helene.exe`: поднять канал и раннер, перезапускать упавших,
дать окну ключ.

Запуск: python server/serverboot.py --config /opt/helene/helene.json

Что делает:
  * ключ канала — `data/.serverboot/desk-token` (заводится, если нет; до 1.2.5 лежал в
    `data/memory/.state/`, оттуда переезжает) — уходит
    каналу в HELENE_TOKEN и печатается один раз строкой для окна:
    в helene.json на ПК владельца — {"mode": "remote", "base": "https://…", "key": "…"};
  * канал `app/deskapp.py <port>` слушает 0.0.0.0 внутри контейнера, наружу её
    выпускает compose на 127.0.0.1:<port>, а в мир — Caddy/Tailscale владельца;
  * раннер `app/localharness/runner.py --config helene.json` — тот же, что на
    Windows: ограды AppContainer здесь нет, границей служит сам контейнер
    (режим `interactive`), тела руки `computer` нет (`body.launch` на Linux
    говорит об этом сам);
  * реле подписки ChatGPT (`helene-relay`, Linux-бинарь рядом с конфигом) —
    третьим ребёнком, когда `relay.enabled`, ровно как оболочка на Windows:
    дом реле `data/relay` (там же приезжает `local_auth` из архива переноса),
    порт из `relay.port`, ключ петли из `model.key`. Реле живёт В ТОМ ЖЕ
    контейнере, что и харнесс, потому что слушает только `127.0.0.1` — и
    поэтому `model.base_url` вида `http://127.0.0.1:5011` из перенесённого
    агента верен на сервере БЕЗ правки;
  * упавший ребёнок поднимается с растущей паузой (1…60 с); код выхода 2/3
    раннера (нет дерева / кривой конфиг) перезапуском не лечится — надзор
    говорит это в лог и ждёт правки;
  * говорит о себе окну (`deskd/control.py`): каждые три секунды пишет записку
    «жив, вот дети» и разбирает просьбы владельца — перезапустить агента, реле
    или весь харнесс. Просьбу КЛАДЁТ канал, исполняет надзор: трогать процессы
    вправе только он. «Перезапустить всё» в контейнере — это выход надзора,
    контейнер поднимет его сам; вне контейнера выходить некуда, и дети
    перезапускаются на месте (расписка говорит, что именно вышло);
  * SIGTERM/SIGINT — гасит детей и выходит.

Вывод детей — `data/deskapp.log`, `data/runner.log` и `data/relay.log`, как на
Windows.

Права в контейнере (1.2.5, отчёт Йоны — агента Дмитрия, 29.09): «агент может исполнять команды
с правами root в том же контейнере, где находятся код, секреты и канал входящих сообщений
владельца». Так и было: всё жило под root, shell агента читал ключ окна и клал записки
«от владельца» сам. Теперь, если образ знает пользователей (`server/Dockerfile`):
  * раннер и руки агента — `helene` (не root): его дом `data/` и код `tree/` (самоправка
    остаётся), `app/` и `helene.json` — только чтение; ключа окна в его среде нет;
  * канал — `desk`: своя папка `data/.channel` (0700) — спаренные устройства и журнал
    записок владельца (`deskd/inbox_seal.py`), раннер сверяет с ним каждую записку;
    `helene.json` — его (Настройки окна);
  * надзор и реле — root: ключ окна в `data/.serverboot` (0700), дом реле `data/relay`
    (вход в ChatGPT) агенту и каналу не прочесть — видно только, есть ли вход, и журналы;
  * `data/` — root, sticky: чужие записи в нём агент не переименует и не подменит.
Образ старый (пользователей нет) или надзор не root — всё как раньше, с предупреждением.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


def _utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_config(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    cfg = json.loads(raw.decode("utf-8"))
    if not isinstance(cfg, dict):
        raise SystemExit(f"{path}: верхний уровень должен быть объектом")
    return cfg


def _tree(config: Path, cfg: dict) -> Path:
    tree = Path(str(cfg.get("tree") or "data"))
    return tree if tree.is_absolute() else (config.parent / tree).resolve()


#: Папка надзора (root, 0700): ключ окна. Папка канала (desk, 0700): устройства, журнал записок.
KEEP_DIR = ".serverboot"
CHANNEL_DIR = ".channel"
AGENT_USER, DESK_USER = "helene", "desk"


def _desk_token(tree: Path) -> str:
    """Ключ окна. С 1.2.5 — в `data/.serverboot/desk-token`, куда агенту хода нет; прежний
    `memory/.state/desk-token` (его читал shell агента) переезжает и удаляется."""
    path = tree / KEEP_DIR / "desk-token"
    old = tree / "memory" / ".state" / "desk-token"
    for source in (path, old):
        try:
            token = source.read_text("utf-8").strip()
        except OSError:
            continue
        if token:
            if source == old:
                _keep_token(path, token)
                try:
                    old.unlink()
                except OSError:
                    pass
            return token
    token = secrets.token_urlsafe(32)
    _keep_token(path, token)
    return token


def _keep_token(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)


def accounts() -> "dict | None":
    """Пользователи агента и канала, если образ их знает и надзор — root."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    try:
        import pwd
        agent, desk = pwd.getpwnam(AGENT_USER), pwd.getpwnam(DESK_USER)
    except (ImportError, KeyError):
        return None
    return {"agent": agent.pw_uid, "desk": desk.pw_uid, "gid": agent.pw_gid,
            "agent_home": agent.pw_dir, "desk_home": desk.pw_dir}


def _own(path: Path, uid: int, gid: int) -> int:
    """Дерево — во владение uid:gid, группе — чтение и запись; ссылки не разыменовываются.
    -> сколько записей поправлено."""
    fixed = 0

    def one(p: str, is_dir: bool) -> None:
        nonlocal fixed
        try:
            st = os.lstat(p)
        except OSError:
            return
        import stat as _st
        if _st.S_ISLNK(st.st_mode):
            if st.st_uid != uid or st.st_gid != gid:
                os.lchown(p, uid, gid)
                fixed += 1
            return
        want = (st.st_mode | (0o2070 if is_dir else 0o060)) & 0o7777
        if st.st_uid != uid or st.st_gid != gid:
            os.lchown(p, uid, gid)
            fixed += 1
        if (st.st_mode & 0o7777) != want:
            os.chmod(p, want)

    if not path.exists() and not path.is_symlink():
        return 0
    one(str(path), path.is_dir() and not path.is_symlink())
    if path.is_dir() and not path.is_symlink():
        for base, dirs, files in os.walk(path, followlinks=False):
            for name in dirs:
                one(os.path.join(base, name), not os.path.islink(os.path.join(base, name)))
            for name in files:
                one(os.path.join(base, name), False)
    return fixed


def _set(path: Path, uid: int, gid: int, dmode: int, fmode: int) -> None:
    """Дерево — во владение uid:gid ровно с такими правами; ссылки не разыменовываются."""
    import stat as _st

    def one(p: str) -> None:
        st = os.lstat(p)
        os.lchown(p, uid, gid)
        if not _st.S_ISLNK(st.st_mode):
            os.chmod(p, dmode if _st.S_ISDIR(st.st_mode) else fmode)

    one(str(path))
    if path.is_dir() and not path.is_symlink():
        for base, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                one(os.path.join(base, name))


def seal_relay(home: Path, gid: int) -> None:
    """Дом реле: всё — root и только root, кроме двух вещей для группы данных. Канал
    показывает, есть ли вход в ChatGPT (`local_auth/auth.json`) — папку входа можно пройти,
    но не прочесть и не перечислить; и журналы реле (`logs/`) — группе на чтение."""
    _set(home, 0, 0, 0o700, 0o600)
    os.chown(home, 0, gid)
    os.chmod(home, 0o750)
    auth = home / "local_auth"
    if auth.is_dir() and not auth.is_symlink():
        os.chmod(auth, 0o711)
    logs = home / "logs"
    if logs.is_dir() and not logs.is_symlink():
        _set(logs, 0, gid, 0o2750, 0o640)


def separate(tree: Path, code: Path, config: Path, who: dict) -> str:
    """Права по ролям (см. шапку). Идемпотентно; на каждом старте, до подъёма детей. -> итог."""
    gid, agent, desk = who["gid"], who["agent"], who["desk"]
    os.chown(tree, 0, gid)
    os.chmod(tree, 0o3775)
    keep = tree / KEEP_DIR
    keep.mkdir(exist_ok=True)
    os.chown(keep, 0, 0)
    os.chmod(keep, 0o700)
    channel = tree / CHANNEL_DIR
    channel.mkdir(exist_ok=True)
    # Устройства — данные канала: прежний `memory/.state/devices.json` переезжает к нему.
    old_devices, new_devices = tree / "memory" / ".state" / "devices.json", channel / "devices.json"
    if old_devices.is_file() and not new_devices.exists():
        os.replace(old_devices, new_devices)
    _own(channel, desk, gid)
    os.chmod(channel, 0o700)
    relay = tree / "relay"
    if relay.exists():
        seal_relay(relay, gid)
    fixed = 0
    for entry in tree.iterdir():
        if entry.name in (KEEP_DIR, CHANNEL_DIR, "relay", ".owner-stop") or entry.name.endswith((".log", ".log.1")):
            continue
        fixed += _own(entry, agent, gid)
    fixed += _own(code, agent, gid)
    # Настройки окна пишет канал; агент (раннер) читает через группу, но не пишет.
    os.chown(config, desk, gid)
    os.chmod(config, 0o640)
    return f"агент — {AGENT_USER}, канал — {DESK_USER}; поправлено записей: {fixed}"


#: Код выхода движка «перезапусти меня» — тот же, что в `localharness/runner.py`,
#: `shell/src/main.rs` и `svc/src/main.rs`.
RESTART_EXIT_CODE = 42

#: Имя Linux-бинаря реле в поставке (рядом с `helene-relay.exe` для Windows).
RELAY_NAME = "helene-relay"
RELAY_PORT_DEFAULT = 5011


def _relay_block(cfg: dict) -> dict:
    block = cfg.get("relay")
    return block if isinstance(block, dict) else {}


def _relay_port(cfg: dict) -> int:
    try:
        return int(_relay_block(cfg).get("port") or RELAY_PORT_DEFAULT)
    except (TypeError, ValueError):
        return RELAY_PORT_DEFAULT


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def looks_like_local_relay(cfg: dict) -> bool:
    """Смотрит ли мозг агента в локальное реле (адрес на петле с портом реле)."""
    url = str((cfg.get("model") or {}).get("base_url") or "")
    port = _relay_port(cfg)
    return any(f"//{host}:{port}" in url for host in ("127.0.0.1", "localhost"))


class Child:
    def __init__(self, key: str, name: str, argv: list[str], env: dict, cwd: Path, log_path: Path,
                 user: "int | None" = None, group: "int | None" = None,
                 umask: "int | None" = None):
        # `key` — то имя, которым ребёнка зовут снаружи (просьба из окна,
        # `deskd/control.py`): «раннер» переводится, `runner` — нет.
        self.key, self.name = key, name
        self.argv, self.env, self.cwd, self.log_path = argv, env, cwd, log_path
        self.user, self.group, self.umask = user, group, umask
        self.proc: subprocess.Popen | None = None
        self.falls: list[float] = []
        self.retry_at = 0.0
        self.halted = ""
        self.since_utc = ""

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def spawn(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.log_path.stat().st_size > 5 * 1024 * 1024:
                os.replace(self.log_path, self.log_path.with_suffix(".log.1"))
        except OSError:
            pass
        # Журнал открывает root, а папка — агента: без O_NOFOLLOW подложенная ссылка на месте
        # журнала повела бы запись root куда угодно.
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o644)
        out = os.fdopen(fd, "ab")
        extra = {}
        if self.user is not None:
            extra = {"user": self.user, "group": self.group, "extra_groups": [], "umask": 0o002}
        elif self.umask is not None:
            extra = {"umask": self.umask}
        self.proc = subprocess.Popen(self.argv, env=self.env, cwd=str(self.cwd),
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                     start_new_session=True, **extra)
        out.close()
        self.since_utc = _utc()
        print(f"[serverboot] {self.name} поднят, pid {self.proc.pid}", flush=True)

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        os.killpg(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()


def child_env(base: dict, tree: Path, token: str) -> dict:
    """Среда детей: дерево, ключ канала — и метка «надзор здесь я».

    HELENE_SUPERVISOR: движок с 1.1.0 сам берёт просьбы окна о перезапуске (на Windows и
    Mac надзор — окно или служба), и в контейнере это были две руки на одном столе:
    «перезапусти реле» брал движок и отвечал «реле держит не движок». По метке движок
    стол не трогает; рука обновления по ней же понимает, что она на сервере.
    """
    env = dict(base, HELENE_TREE=str(tree), HELENE_TOKEN=token, PYTHONUTF8="1",
               PYTHONUNBUFFERED="1", HELENE_HOST=base.get("HELENE_HOST", "0.0.0.0"),
               HELENE_SUPERVISOR="serverboot")
    env.pop("PRAXIS_DESK_TOKEN", None)
    return env


def settle(child: Child, now: float) -> str:
    """Ребёнок вышел — решить, что дальше. -> как это названо в журнале.

    Три исхода: код 42 от раннера — его собственная просьба «перезапусти меня» (так он
    выходит между ходами), поднимаем сразу и без счёта падений; код 2/3 раннера — нет
    дерева или кривой конфиг, перезапуск не лечит, надзор ждёт правки; всё прочее —
    падение, и пауза перед подъёмом растёт.
    """
    code = child.proc.poll() if child.proc is not None else None
    child.proc = None
    if child.key == "runner" and code == RESTART_EXIT_CODE:
        child.falls, child.retry_at = [], 0.0
        said = f"раннер попросил перезапуска (код {code}) — поднимаю"
    elif child.key == "runner" and code in (2, 3):
        child.halted = ("конфиг или раскладка папки данных" if code == 3
                        else "нет папки с кодом агента (tree/)")
        said = (f"раннер вышел с кодом {code}: {child.halted} — перезапуск не поможет, "
                f"правь и перезапусти контейнер")
    else:
        child.falls = [t for t in child.falls if now - t < 600] + [now]
        pause = min(60.0, 2.0 ** min(len(child.falls), 6))
        child.retry_at = now + pause
        said = f"{child.name} завершился (код {code}) — снова через {pause:.0f} с"
    print(f"[serverboot] {said}", flush=True)
    return said


def relay_child(base: Path, cfg: dict, tree: Path, env: dict) -> "Child | None":
    """Реле подписки ChatGPT третьим ребёнком — то же, что делает оболочка.

    Молчания здесь быть не должно ни в одном исходе: агент с провайдером
    `chatgpt` без живого реле НЕМ, и до 0.5.2 сервер именно так его и принимал —
    архив переноса привозил `data/relay/local_auth` и `model.base_url` на
    петлю, а поднимать реле на той стороне было нечем.
    """
    if not (_relay_block(cfg).get("enabled") or (cfg.get("images") or {}).get("enabled") is True):
        if looks_like_local_relay(cfg):
            print("[serverboot] ⚠ мозг агента смотрит в локальное реле, но relay.enabled "
                  "не стоит — реле не поднимаю, и модель отвечать не будет", flush=True)
        return None
    exe = Path((os.environ.get("HELENE_RELAY") or "").strip() or (base / RELAY_NAME))
    if not exe.is_file():
        print(f"[serverboot] ⚠ relay.enabled, но реле рядом нет ({exe}) — агент с подпиской "
              "ChatGPT будет нем. Собери Linux-бинарь реле в поставку "
              "(installer/build_dist.py) и пересобери образ", flush=True)
        return None
    port = _relay_port(cfg)
    if _port_busy(port):
        # Своё реле в петлю перезапусков, а весь мозг — в ЧУЖОЕ реле: ровно то,
        # от чего оболочка отказывается на Windows.
        print(f"[serverboot] ⚠ порт реле {port} уже занят — своё реле не поднимаю; "
              f"освободи порт или смени relay.port", flush=True)
        return None
    home = tree / "relay"
    home.mkdir(parents=True, exist_ok=True)
    relay_env = dict(env)
    relay_env["RELAY_PORT"] = str(port)
    relay_env["RELAY_LOG_DIR"] = str(home / "logs")
    relay_env["RELAY_LOCAL"] = "1"
    # Инструкции: без этого реле кладёт перед конституцией агента 23 КБ чужого
    # системного промпта («ты кодинг-агент Codex CLI») — то же значение, что
    # ставит оболочка (shell/src/main.rs::spawn_relay).
    instructions = str(_relay_block(cfg).get("instructions") or "").strip() or "minimal"
    relay_env["RELAY_INSTRUCTIONS"] = instructions
    key = str(_relay_block(cfg).get("key") or ((cfg.get("model") or {}).get("key") if _relay_block(cfg).get("enabled") else "") or "").strip()
    if key:
        # Ключ мозга = ключ петли: реле требует его Bearer-ом на /chat/completions.
        relay_env["RELAY_API_KEY"] = key
    return Child("relay", "реле", [str(exe), "serve"], relay_env, home, tree / "relay.log")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = Path(args.config).resolve()
    cfg = _read_config(config)
    base = config.parent
    tree = _tree(config, cfg)
    tree.mkdir(parents=True, exist_ok=True)
    import emergency_stop
    emergency_stop.prepare(tree)
    port = int(cfg.get("port") or 8094)
    app = base / str(cfg.get("app") or "app/deskapp.py")
    runner = base / str(cfg.get("runner") or "app/localharness/runner.py")
    if not app.is_file() or not runner.is_file():
        raise SystemExit(f"нет канала или раннера: {app} / {runner}")
    token = _desk_token(tree)
    # Замок дерева прошлого контейнера. В свежем контейнере живого раннера нет по
    # построению (его поднимает только этот надзор, и он ещё ничего не поднял),
    # а pid в замке — из ДРУГОГО пространства процессов: новый раннер получает
    # тот же pid 8, `boot.claim_tree` видит «живого» владельца и выходит с кодом
    # 3. Найдено на VPS автора 06.09 при пересоздании контейнера. Снимаем
    # замок здесь, до подъёма детей; сам харнесс с 0.3.1 тоже считает замок с
    # чужим именем хоста брошенным.
    stale = tree / "memory" / ".state" / "harness.lock"
    if stale.exists():
        try:
            stale.unlink()
            print(f"[serverboot] снят замок дерева прошлого контейнера: {stale}", flush=True)
        except OSError as exc:
            print(f"[serverboot] замок дерева не снят ({exc}) — раннер может отказаться", flush=True)
    env = child_env(os.environ, tree, token)
    code = base / str(cfg.get("code") or "tree")
    who = accounts()
    if who is not None:
        try:
            print(f"[serverboot] права по ролям: {separate(tree, code, config, who)}", flush=True)
        except OSError as exc:
            who = None
            print(f"[serverboot] ⚠ права по ролям не встали ({exc}) — всё под root, как раньше; "
                  "записки владельца в приёмной не сверяются", flush=True)
    else:
        print("[serverboot] ⚠ образ без пользователей helene/desk (или надзор не root) — агент "
              "работает под root, как до 1.2.5; пересобери образ из поставки 1.2.5+", flush=True)
    channel_env, runner_env = dict(env), dict(env)
    channel_env["HELENE_SERVER_AUTH_IMPORT"] = "1"
    runner_env.pop("HELENE_TOKEN", None)   # ключ окна агенту не нужен, а код агента читает среду
    channel_user = runner_user = group = None
    if who is not None:
        channel_env.update(HELENE_DESK_STATE=str(tree / CHANNEL_DIR), HELENE_INBOX_SEALED="1",
                           HOME=who["desk_home"], USER=DESK_USER)
        runner_env.update(HELENE_INBOX_SEALED="1", HOME=who["agent_home"], USER=AGENT_USER)
        channel_user, runner_user, group = who["desk"], who["agent"], who["gid"]
    children = []
    # Реле первым: пока оно не слушает, первый же ход агента с подпиской
    # ChatGPT уходит в никуда. Порядок тот же, что в плане оболочки.
    relay = relay_child(base, cfg, tree, env)
    if relay is not None:
        if who is not None:
            # Вход в ChatGPT, который реле перепишет, не должен стать читаемым для группы:
            # новые файлы реле — без чтения для других (журналы группе отдаёт setgid logs/).
            seal_relay(tree / "relay", who["gid"])
            relay.umask = 0o027
        children.append(relay)
    children += [
        Child("channel", "канал", [sys.executable, "-u", str(app), str(port)], channel_env, app.parent,
              tree / "deskapp.log", user=channel_user, group=group),
        Child("runner", "раннер", [sys.executable, "-u", str(runner), "--config", str(config)], runner_env,
              runner.parent, tree / "runner.log", user=runner_user, group=group),
    ]
    # Управление из окна: протокол и обе его стороны живут в `deskd/control.py`,
    # рядом с каналом — иначе они разъезжаются молча. Импорт поздний, потому что
    # путь к нему знает конфиг (`app`), а не эта папка.
    sys.path.insert(0, str(app.parent))
    try:
        from deskd import control  # noqa: PLC0415 — путь известен только здесь
    except ImportError as exc:
        control = None
        print(f"[serverboot] ⚠ управление из окна недоступно: {exc}", flush=True)

    public = os.environ.get("HELENE_PUBLIC_URL", "").strip()
    print("[serverboot] ключ окна для helene.json на ПК владельца:", flush=True)
    print(json.dumps({"mode": "remote", "base": public or f"http://<хост>:{port}", "key": token},
                     ensure_ascii=False), flush=True)
    started_utc = _utc()
    stopping = False

    def restart(child: Child) -> str:
        """Поднять ребёнка заново по просьбе владельца, забыв прежние падения.

        Счётчик падений обнуляется намеренно: пауза перед подъёмом растёт,
        чтобы не молотить упавшего в петле, а нажатая кнопка — это новое
        обстоятельство, и ждать минуту после неё было бы враньём про «сейчас».
        """
        child.stop()
        child.proc = None
        child.falls = []
        child.halted = ""
        child.retry_at = 0.0
        try:
            child.spawn()
            return f"{child.name} перезапущен, pid {child.proc.pid}"
        except OSError as exc:
            child.retry_at = time.monotonic() + 10
            return f"{child.name} не поднялся: {exc}"

    def serve_request(request: dict) -> bool:
        """Исполнить просьбу окна. True — надзору пора выйти (перезапуск всего)."""
        target = str(request.get("target") or "")
        action = str(request.get("action") or "")
        if action != "restart":
            control.receipt(tree, request, False, f"такого действия надзор не знает: {action}")
            return False
        if target == "all":
            if Path("/.dockerenv").exists():
                # Расписка пишется ДО выхода: канал сейчас умрёт вместе со всеми,
                # и написать её будет некому и некуда.
                control.receipt(tree, request, True,
                                "гашу харнесс целиком — контейнер поднимет его заново")
                print("[serverboot] просьба владельца: перезапустить всё — выхожу, "
                      "контейнер поднимется сам", flush=True)
                return True
            notes = [restart(child) for child in children]
            control.receipt(tree, request, True,
                            "надзор запущен не в контейнере, выходить некуда — "
                            "перезапустил детей на месте: " + "; ".join(notes))
            return False
        picked = next((child for child in children if child.key == target), None)
        if picked is None:
            control.receipt(tree, request, False,
                            f"здесь нет такого ребёнка: {target} "
                            f"(есть: {', '.join(child.key for child in children)})")
            return False
        note = restart(picked)
        print(f"[serverboot] просьба владельца: {note}", flush=True)
        control.receipt(tree, request, True, note)
        return False

    def _stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    from deskd import relay_auth
    relay_auth.prepare(tree, who["desk"] if who else None, who["gid"] if who else None)
    was_stopped = emergency_stop.stopped(tree)
    for child in children:
        if not was_stopped or child.key == "channel": child.spawn()

    def activate_auth(save):
        picked = next((child for child in children if child.key == "relay"), None)
        if picked is None:
            save()
            return False
        picked.stop()
        picked.proc = None
        try:
            save()
        finally:
            restart(picked)
        return picked.alive()

    next_cycle = 0.0
    while not stopping:
        time.sleep(.5)
        stopped_now = emergency_stop.consume(tree)
        if stopped_now:
            if not was_stopped:
                print("[serverboot] аварийный стоп сохранён; гашу агентские процессы", flush=True)
                for child in children:
                    if child.key != "channel": child.stop(); child.proc = None
                if Path("/.dockerenv").exists():
                    # PID1 exit kills even detached descendants in this one
                    # installation namespace. Docker restarts into the latch;
                    # only the authenticated channel is brought back.
                    for child in children: child.stop()
                    return 5
            was_stopped = True
            if time.monotonic() < next_cycle: continue
            next_cycle = time.monotonic() + 3
            if control is not None:
                request = control.take_request(tree)
                if request: control.receipt(tree, request, False, "Аварийный стоп включён; перезапуск не снимает его")
                control.beat(tree, "serverboot", started_utc,
                    [{"id": child.key, "name": child.name, "alive": child.alive(),
                      "pid": child.proc.pid if child.proc is not None else None,
                      "halted": child.key != "channel"} for child in children])
            channel = next((child for child in children if child.key == "channel"), None)
            if channel is not None and not channel.alive(): channel.spawn()
            continue
        if was_stopped:
            print("[serverboot] стоп снят владельцем; возвращаю надзор", flush=True)
            was_stopped = False
        if time.monotonic() < next_cycle: continue
        next_cycle = time.monotonic() + 3
        now = time.monotonic()
        reader = tree / "memory/.control/desk_inbox/.reader.json"
        try:
            state = json.loads(reader.read_text(encoding="utf8"))
            idle = state.get("busy") is False and time.time() - reader.stat().st_mtime < 45
        except (OSError, ValueError):
            idle = not any(child.key == "runner" and child.alive() for child in children)
        relay_auth.apply_pending(tree, idle=idle, activate=activate_auth,
                                 uid=who["desk"] if who else None,
                                 gid=who["gid"] if who else None,
                                 running=lambda: any(child.key == "relay" and child.alive() for child in children))
        if control is not None:
            # Сначала просьба, потом записка о себе — и в записке уже виден
            # новый pid перезапущенного ребёнка. В обратном порядке окно ещё
            # три секунды показывало бы старого, сразу после своей же кнопки.
            request = control.take_request(tree)
            if request and serve_request(request):
                stopping = True
                continue
            # Записка — единственный признак «надзор жив» для окна: ни pid, ни
            # имя хоста в контейнере для этого не годятся.
            control.beat(tree, "serverboot", started_utc,
                         [{"id": child.key, "name": child.name, "alive": child.alive(),
                           "pid": child.proc.pid if child.proc is not None else None,
                           "since_utc": child.since_utc, "falls": len(child.falls),
                           "halted": child.halted} for child in children])
        for child in children:
            if child.alive() or child.halted:
                continue
            if child.proc is not None:
                settle(child, now)
                continue
            if now >= child.retry_at:
                try:
                    child.spawn()
                except OSError as exc:
                    child.retry_at = now + 30
                    print(f"[serverboot] {child.name} не поднялся: {exc}", flush=True)
    for child in children:
        child.stop()
    print("[serverboot] остановлен", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
