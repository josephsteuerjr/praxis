# -*- coding: utf-8 -*-
"""Воспроизведение: два агента, два дерева, два канала — чей md читает /api/md.

Запуск: python ..\scratch_repro_md.py  (из desk/)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import build_opener, ProxyHandler

DESK = Path(__file__).resolve().parent
PY = sys.executable
OPENER = build_opener(ProxyHandler({}))          # никакого системного прокси

tmp = Path(tempfile.mkdtemp(prefix="helene-md-repro-"))
install = tmp / "Helene"
(install / "agents").mkdir(parents=True)

# корневой агент
(install / "helene.json").write_text(json.dumps({
    "mode": "local", "tree": "data", "port": 18094, "agent": {"name": "Корневой"},
}), encoding="utf-8")
# второй агент mira: свой дом
mira = install / "agents" / "mira"
mira.mkdir()
(mira / "helene.json").write_text(json.dumps({
    "mode": "local", "tree": "data", "port": 18095, "agent": {"name": "Мира"},
}), encoding="utf-8")

# деревья с различимым soul/SOUL.md
(install / "data" / "soul").mkdir(parents=True)
(install / "data" / "soul" / "SOUL.md").write_text("# Конституция КОРНЕВОГО", encoding="utf-8")
(mira / "data" / "soul").mkdir(parents=True)
(mira / "data" / "soul" / "SOUL.md").write_text("# Конституция МИРЫ", encoding="utf-8")


def channel(port: int, tree: Path, config: Path, with_tree: bool) -> subprocess.Popen:
    env = {**os.environ, "PYTHONUTF8": "1", "HELENE_HOST": "127.0.0.1",
           "HELENE_TOKEN": "repro-token-0123456789abcdef"}
    if with_tree:
        env["HELENE_TREE"] = str(tree)
    env["HELENE_CONFIG"] = str(config)
    return subprocess.Popen(
        [PY, "-u", str(DESK / "deskapp.py"), str(port)],
        cwd=str(DESK), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def read_md(port: int) -> str:
    url = f"http://127.0.0.1:{port}/api/md?path=soul/SOUL.md&key=repro-token-0123456789abcdef"
    last = ""
    for _ in range(60):
        try:
            with OPENER.open(url, timeout=2) as r:
                return r.read().decode("utf-8")
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.25)
    return f"<канал не ответил: {last}>"


def stop(*kids: subprocess.Popen) -> None:
    for c in kids:
        c.kill()
        try:
            out = c.communicate(timeout=5)[0] or ""
        except Exception:
            out = ""
        tail = "\n".join(out.strip().splitlines()[-6:])
        if tail:
            print("  лог канала (хвост):", tail)


print("=== СЦЕНАРИЙ 1: оба канала с HELENE_TREE (как спавнит оболочка) ===")
c1 = channel(18094, install / "data", install / "helene.json", True)
c2 = channel(18095, mira / "data", mira / "helene.json", True)
try:
    print("канал корневого:", read_md(18094)[:160])
    print("канал Миры:     ", read_md(18095)[:160])
finally:
    stop(c1, c2)

print()
print("=== СЦЕНАРИЙ 2: канал Миры БЕЗ HELENE_TREE, но с HELENE_CONFIG ===")
c3 = channel(18095, mira / "data", mira / "helene.json", False)
try:
    print("канал Миры:     ", read_md(18095)[:240])
finally:
    stop(c3)

print()
print("временная установка:", tmp)
