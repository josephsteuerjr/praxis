# -*- coding: utf-8 -*-
"""Один канал, лог в файл, быстрый опрос. Диагностика repro."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import build_opener, ProxyHandler

DESK = Path(__file__).resolve().parent
PY = sys.executable
OPENER = build_opener(ProxyHandler({}))

tmp = Path(tempfile.mkdtemp(prefix="helene-one-"))
(tmp / "soul").mkdir(parents=True)
(tmp / "soul" / "SOUL.md").write_text("# ОДИН", encoding="utf-8")
log = open(tmp / "channel.log", "w", encoding="utf-8")

env = {**os.environ, "PYTHONUTF8": "1", "HELENE_HOST": "127.0.0.1",
       "HELENE_TOKEN": "repro-token-0123456789abcdef",
       "HELENE_TREE": str(tmp)}
proc = subprocess.Popen([PY, "-u", str(DESK / "deskapp.py"), "18194"],
                        cwd=str(DESK), env=env, stdout=log, stderr=subprocess.STDOUT)
time.sleep(6)
alive = proc.poll() is None
print("жив:", alive, "код:", proc.returncode)
log.flush()
print("--- лог канала ---")
print((tmp / "channel.log").read_text(encoding="utf-8")[:2000])

if alive:
    url = "http://127.0.0.1:18194/api/md?path=soul/SOUL.md&key=repro-token-0123456789abcdef"
    try:
        with OPENER.open(url, timeout=5) as r:
            print("ответ:", r.read().decode("utf-8")[:200])
    except Exception as exc:
        print("ошибка чтения:", type(exc).__name__, exc)

proc.kill()
print("вр. папка:", tmp)
