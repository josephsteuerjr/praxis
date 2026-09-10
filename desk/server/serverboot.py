# -*- coding: utf-8 -*-
"""Надзор за харнессом Hélène на сервере (Linux, Docker) — то, что на Windows
делает оболочка `helene.exe`: поднять канал и раннер, перезапускать упавших,
дать окну ключ.

Запуск: python server/serverboot.py --config /opt/helene/helene.json

Что делает:
  * ключ канала — `data/memory/.state/desk-token` (заводится, если нет) — уходит
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


def _desk_token(tree: Path) -> str:
    path = tree / "memory" / ".state" / "desk-token"
    try:
        token = path.read_text("utf-8").strip()
        if token:
            return token
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    os.chmod(path, 0o600)
    return token


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
    def __init__(self, key: str, name: str, argv: list[str], env: dict, cwd: Path, log_path: Path):
        # `key` — то имя, которым ребёнка зовут снаружи (просьба из окна,
        # `deskd/control.py`): «раннер» переводится, `runner` — нет.
        self.key, self.name = key, name
        self.argv, self.env, self.cwd, self.log_path = argv, env, cwd, log_path
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
        out = open(self.log_path, "ab")
        self.proc = subprocess.Popen(self.argv, env=self.env, cwd=str(self.cwd),
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        out.close()
        self.since_utc = _utc()
        print(f"[serverboot] {self.name} поднят, pid {self.proc.pid}", flush=True)

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def relay_child(base: Path, cfg: dict, tree: Path, env: dict) -> "Child | None":
    """Реле подписки ChatGPT третьим ребёнком — то же, что делает оболочка.

    Молчания здесь быть не должно ни в одном исходе: агент с провайдером
    `chatgpt` без живого реле НЕМ, и до 0.5.2 сервер именно так его и принимал —
    архив переноса привозил `data/relay/local_auth` и `model.base_url` на
    петлю, а поднимать реле на той стороне было нечем.
    """
    if not _relay_block(cfg).get("enabled"):
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
    # Инструкции: без этого реле кладёт перед конституцией агента 23 КБ чужого
    # системного промпта («ты кодинг-агент Codex CLI») — то же значение, что
    # ставит оболочка (shell/src/main.rs::spawn_relay).
    instructions = str(_relay_block(cfg).get("instructions") or "").strip() or "minimal"
    relay_env["RELAY_INSTRUCTIONS"] = instructions
    key = str((cfg.get("model") or {}).get("key") or "").strip()
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
    env = dict(os.environ, HELENE_TREE=str(tree), HELENE_TOKEN=token, PYTHONUTF8="1",
               PYTHONUNBUFFERED="1", HELENE_HOST=os.environ.get("HELENE_HOST", "0.0.0.0"))
    env.pop("PRAXIS_DESK_TOKEN", None)
    children = []
    # Реле первым: пока оно не слушает, первый же ход агента с подпиской
    # ChatGPT уходит в никуда. Порядок тот же, что в плане оболочки.
    relay = relay_child(base, cfg, tree, env)
    if relay is not None:
        children.append(relay)
    children += [
        Child("channel", "канал", [sys.executable, "-u", str(app), str(port)], env, app.parent,
              tree / "deskapp.log"),
        Child("runner", "раннер", [sys.executable, "-u", str(runner), "--config", str(config)], env,
              runner.parent, tree / "runner.log"),
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
    for child in children:
        child.spawn()
    while not stopping:
        time.sleep(3)
        now = time.monotonic()
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
                code = child.proc.poll()
                child.proc = None
                if child.name == "раннер" and code in (2, 3):
                    child.halted = ("конфиг или раскладка папки данных" if code == 3
                                    else "нет папки с кодом агента (tree/)")
                    print(f"[serverboot] раннер вышел с кодом {code}: {child.halted} — "
                          f"перезапуск не поможет, правь и перезапусти контейнер", flush=True)
                    continue
                child.falls = [t for t in child.falls if now - t < 600] + [now]
                pause = min(60.0, 2.0 ** min(len(child.falls), 6))
                child.retry_at = now + pause
                print(f"[serverboot] {child.name} завершился (код {code}) — снова через {pause:.0f} с",
                      flush=True)
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
