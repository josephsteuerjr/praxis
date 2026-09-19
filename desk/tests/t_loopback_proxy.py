# -*- coding: utf-8 -*-
"""Стенд рычага «петля мимо прокси» (`helene/core/sitecustomize.py`).

Запуск:  python tests/t_loopback_proxy.py

ЗАЧЕМ. 15.09 у пользователя с Psiphon тело подключалось к мосту и тут же
числилось отключённым. Мост и тело говорят сырым TCP — прокси их не видит;
а контроллер (`body_client`) ходит к мосту обычным `urllib`, и тот на Windows
берёт прокси из реестра. Галочка «не использовать прокси для локальных
адресов» не спасает: в реестре она — `<local>`, а `proxy_bypass_registry`
понимает её как «имя без точки», и `127.0.0.1` под неё не подходит.

Стенд проверяет ОБА направления и потому красный без рычага: тот же скрипт
без `import sitecustomize` обязан провалиться на живом запросе. Всё — в
отдельном процессе: рычаг подменяет `builtins.open` и `proxy_bypass`
насовсем, и тащить это в процесс набора незачем.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAYER = HERE.parent.parent / "helene" / "core"

#: Дохлый адрес: 127.0.0.2:9 (discard) никем не слушается — если запрос ушёл
#: в прокси, он не доедет, и это ровно то, что случилось у пользователя.
PROBE = r'''
import os, sys, threading, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

LEVER = {lever!r}
if LEVER:
    sys.path.insert(0, {layer!r})
    import sitecustomize                      # noqa: F401 — рычаг ставится на импорте

os.environ["http_proxy"] = "http://127.0.0.2:9"
os.environ["https_proxy"] = "http://127.0.0.2:9"
os.environ.pop("no_proxy", None)
os.environ.pop("NO_PROXY", None)

if LEVER:
    for host in ("127.0.0.1:9473", "127.0.0.1", "localhost", "LOCALHOST:8094",
                 "[::1]:8094", "::1", "127.0.0.5", "app.localhost"):
        assert urllib.request.proxy_bypass(host), host
    # Чужой адрес мерку не меняет: прокси для него как был, так и остался.
    assert not urllib.request.proxy_bypass("example.com"), "example.com"
    assert not urllib.request.proxy_bypass("10.0.0.1"), "10.0.0.1"

class Hand(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a):
        pass

srv = HTTPServer(("127.0.0.1", 0), Hand)
threading.Thread(target=srv.serve_forever, daemon=True).start()
try:
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    with urllib.request.urlopen(url, timeout=5) as answer:
        assert answer.read() == b"ok"
finally:
    srv.shutdown()
print("OK")
'''


def probe(lever: bool) -> subprocess.CompletedProcess:
    return subprocess.run(
        # -B: слой издания — не наш кэш; без него каждый прогон оставлял бы
        # `helene/core/__pycache__` неотслеживаемым мусором в репозитории.
        [sys.executable, "-B", "-c", PROBE.format(lever=lever, layer=str(LAYER))],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)


class Loopback(unittest.TestCase):
    def test_lever_exists(self):
        self.assertTrue((LAYER / "sitecustomize.py").is_file(),
                        f"рычаг издания не на месте: {LAYER / 'sitecustomize.py'}")

    def test_loopback_goes_past_the_proxy(self):
        done = probe(lever=True)
        self.assertEqual(done.returncode, 0,
                         f"с рычагом запрос к 127.0.0.1 обязан доехать:\n{done.stderr}")
        self.assertIn("OK", done.stdout)

    @unittest.skipUnless(os.name == "nt",
                         "стенд про Windows-прокси из реестра: на macOS/Linux Python "
                         "берёт прокси из System Configuration, а не только из среды, "
                         "и запрос к 127.0.0.1 доходит напрямую даже без рычага")
    def test_without_the_lever_it_breaks(self):
        """Без рычага тот же запрос уезжает в прокси — стенд красный не зря.

        Только Windows: там прокси живёт в реестре и `urllib` берёт его из среды,
        так что дохлый `http_proxy` ломает даже петлю. На macOS прокси приходит из
        System Configuration мимо среды — положительный стенд ниже покрывает обе."""
        done = probe(lever=False)
        self.assertNotEqual(done.returncode, 0,
                            "без рычага запрос к 127.0.0.1 доехал — значит стенд "
                            "ничего не проверяет (прокси из среды не подхватился)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
