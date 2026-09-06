# -*- coding: utf-8 -*-
"""Надзор за харнессом Hélène на сервере (Linux, Docker) — то, что на Windows
делает оболочка `helene.exe`: поднять трубу и раннер, перезапускать упавших,
дать окну ключ.

Запуск: python server/serverboot.py --config /opt/helene/helene.json

Что делает:
  * ключ трубы — `data/memory/.state/desk-token` (заводится, если нет) — уходит
    трубе в HELENE_TOKEN и печатается один раз строкой для окна:
    в helene.json на ПК владельца — {"mode": "remote", "base": "https://…", "key": "…"};
  * труба `app/deskapp.py <port>` слушает 0.0.0.0 внутри контейнера, наружу её
    выпускает compose на 127.0.0.1:<port>, а в мир — Caddy/Tailscale владельца;
  * раннер `app/localharness/runner.py --config helene.json` — тот же, что на
    Windows: ограды AppContainer здесь нет, границей служит сам контейнер
    (режим `interactive`), тела руки `computer` нет (`body.launch` на Linux
    говорит об этом сам);
  * упавший ребёнок поднимается с растущей паузой (1…60 с); код выхода 2/3
    раннера (нет дерева / кривой конфиг) перезапуском не лечится — надзор
    говорит это в лог и ждёт правки;
  * SIGTERM/SIGINT — гасит детей и выходит.

Вывод детей — `data/deskapp.log` и `data/runner.log`, как на Windows.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path


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


class Child:
    def __init__(self, name: str, argv: list[str], env: dict, cwd: Path, log_path: Path):
        self.name, self.argv, self.env, self.cwd, self.log_path = name, argv, env, cwd, log_path
        self.proc: subprocess.Popen | None = None
        self.falls: list[float] = []
        self.retry_at = 0.0
        self.halted = ""

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
        print(f"[serverboot] {self.name} поднят, pid {self.proc.pid}", flush=True)

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


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
        raise SystemExit(f"нет трубы или раннера: {app} / {runner}")
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
    children = [
        Child("труба", [sys.executable, "-u", str(app), str(port)], env, app.parent, tree / "deskapp.log"),
        Child("раннер", [sys.executable, "-u", str(runner), "--config", str(config)], env,
              runner.parent, tree / "runner.log"),
    ]
    public = os.environ.get("HELENE_PUBLIC_URL", "").strip()
    print("[serverboot] ключ окна для helene.json на ПК владельца:", flush=True)
    print(json.dumps({"mode": "remote", "base": public or f"http://<хост>:{port}", "key": token},
                     ensure_ascii=False), flush=True)
    stopping = False

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
