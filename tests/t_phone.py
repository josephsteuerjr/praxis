# -*- coding: utf-8 -*-
"""Стенд телефона и мини-аппа в канале (КОНТРАКТ-B→A §4, §5, §6, §9).

Запуск:  python tests/t_phone.py

Что проверяется без сети и без Telegram:
  * подпись `initData` Telegram Web Apps — считается тем же алгоритмом, что
    у Telegram (HMAC-SHA256 через секрет «WebAppData»), с любым из токенов;
    чужой токен, старый `auth_date`, отсутствие подписи — отказ словами;
  * вход мини-аппа даёт ключ устройства той же природы, что QR, одно
    устройство на пользователя Telegram (повторный вход не плодит строки);
  * область ключа устройства покрывает прогоны, ходы комнаты и пульс, а
    конституцию и режим — нет;
  * `/m/sw.js` и `/pair/telegram` открыты до ключа и зарегистрированы в
    роутере; страница `/m/` отдаётся с `no-store`;
  * имя агента без снимка анатомии — из конфига продукта или среды.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import deskapp  # noqa: E402
from deskd import readers  # noqa: E402

TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _init_data(user: dict, token: str = TOKEN, auth_date: int | None = None) -> str:
    fields = {"auth_date": str(auth_date or int(time.time())),
              "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
              "user": json.dumps(user, ensure_ascii=False, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class Signature(unittest.TestCase):
    def test_valid_signature_yields_user(self):
        user = {"id": 42, "first_name": "Егор", "username": "yegor"}
        got = deskapp._telegram_init_user(_init_data(user), ["другой:токен", TOKEN])
        self.assertEqual(got["id"], 42)
        self.assertEqual(got["first_name"], "Егор")

    def test_wrong_token_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            deskapp._telegram_init_user(_init_data({"id": 1}), ["чужой:токен"])
        self.assertIn("подпись", str(caught.exception))

    def test_stale_and_missing(self):
        old = _init_data({"id": 1}, auth_date=int(time.time()) - 8 * 86400)
        with self.assertRaises(ValueError) as caught:
            deskapp._telegram_init_user(old, [TOKEN])
        self.assertIn("устарел", str(caught.exception))
        with self.assertRaises(ValueError):
            deskapp._telegram_init_user("user=%7B%22id%22%3A1%7D&auth_date=1", [TOKEN])
        with self.assertRaises(ValueError):
            deskapp._telegram_init_user("", [TOKEN])
        # Подпись верна, но пользователя нет — тоже отказ.
        fields = {"auth_date": str(int(time.time()))}
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, ("auth_date=" + fields["auth_date"]).encode(), hashlib.sha256).hexdigest()
        with self.assertRaises(ValueError):
            deskapp._telegram_init_user(urlencode(fields), [TOKEN])


class Login(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        self.saved_tree = readers.tree
        readers.tree = lambda: self.tree
        deskapp._DEVICES_CACHE["stamp"] = None

    def tearDown(self):
        readers.tree = self.saved_tree
        self.tmp.cleanup()

    def test_one_device_per_telegram_user(self):
        first = deskapp._tg_login({"id": 42, "first_name": "Егор"}, "10.0.0.5")
        second = deskapp._tg_login({"id": 42, "first_name": "Егор"}, "10.0.0.6")
        rows = deskapp._devices()
        self.assertEqual(len(rows), 1, "повторный вход заменил строку, а не добавил")
        self.assertEqual(rows[0]["name"], "Telegram · Егор")
        self.assertEqual(rows[0]["pair"], "tg:42")
        self.assertTrue(deskapp._device_ok(second["key"]))
        self.assertFalse(deskapp._device_ok(first["key"]), "прежний ключ отозван заменой")
        other = deskapp._tg_login({"id": 7, "username": "guest"}, "")
        self.assertEqual(len(deskapp._devices()), 2)
        self.assertTrue(deskapp._device_ok(other["key"]))


class Scope(unittest.TestCase):
    def test_device_scope_matches_the_contract(self):
        for path in ("/api/runs", "/api/pulse", "/api/chats", "/api/say", "/api/rooms",
                     "/api/chat-turns/window", "/api/run/run-1", "/api/rooms/window-0123abcd",
                     "/api/chat/window", "/tunnel", "/events"):
            self.assertTrue(deskapp._scope_ok("device", path), path)
        for path in ("/api/md", "/api/md-tree", "/api/mode", "/api/anatomy", "/api/shadow",
                     "/pair/devices", "/pair/new"):
            self.assertFalse(deskapp._scope_ok("device", path), path)

    def test_open_paths_and_routes(self):
        self.assertIn("/m/sw.js", deskapp._OPEN_PATHS)
        self.assertIn("/pair/telegram", deskapp._OPEN_PATHS)
        self.assertNotIn("/pair/new", deskapp._OPEN_PATHS)
        app = deskapp.build_app()
        registered = set()
        for resource in app.router.resources():
            info = resource.get_info()
            path = info.get("path") or info.get("formatter") or ""
            for route in resource:
                registered.add((route.method, path))
        self.assertIn(("GET", "/m/sw.js"), registered)
        self.assertIn(("POST", "/pair/telegram"), registered)


class ConfigJs(unittest.TestCase):
    """`config.js` — имя агента и продукта для страницы. До 10.09 его клали на
    сервер руками рядом с каждой сборкой, и выкладка фронта уносила его с
    собой: `/m/config.js` отвечал 404, телефон Праксис звался «Агент»."""

    def setUp(self):
        self.saved = (readers.anatomy, readers.product_config)
        readers.anatomy = lambda: {}
        readers.product_config = lambda: {"agent_name": "Праксис"}
        self.addCleanup(self._restore)
        os.environ.pop("HELENE_PRODUCT", None)

    def _restore(self):
        readers.anatomy, readers.product_config = self.saved
        os.environ.pop("HELENE_PRODUCT", None)

    def test_channel_names_the_agent(self):
        text = deskapp._config_js()
        self.assertIn("window.PULT_CONFIG = ", text)
        self.assertIn('"agent": "Праксис"', text)
        # Имя продукта не выдумывается: пусто — страница возьмёт своё.
        self.assertNotIn("product", text)
        os.environ["HELENE_PRODUCT"] = "Praxis"
        self.assertIn('"product": "Praxis"', deskapp._config_js())

    def test_both_paths_are_registered(self):
        app = deskapp.build_app()
        paths = {(route.method, resource.get_info().get("path") or "")
                 for resource in app.router.resources() for route in resource}
        self.assertIn(("GET", "/config.js"), paths)
        self.assertIn(("GET", "/m/config.js"), paths)


class AgentName(unittest.TestCase):
    def test_name_falls_back_to_config_then_env(self):
        saved = (readers.anatomy, readers.product_config)
        readers.anatomy = lambda: {}
        try:
            readers.product_config = lambda: {"agent_name": "Праксис"}
            self.assertEqual(deskapp._agent_name(), "Праксис")
            readers.product_config = lambda: {}
            os.environ["HELENE_AGENT_NAME"] = "Мира"
            try:
                self.assertEqual(deskapp._agent_name(), "Мира")
            finally:
                os.environ.pop("HELENE_AGENT_NAME", None)
            self.assertEqual(deskapp._agent_name(), "Агент")
            readers.anatomy = lambda: {"agent_name": "Снимок"}
            self.assertEqual(deskapp._agent_name(), "Снимок")
        finally:
            readers.anatomy, readers.product_config = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
