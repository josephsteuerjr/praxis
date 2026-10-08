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
import time

from aiohttp import web
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

    def test_pairing_is_owner_only_but_not_machine_only(self):
        """Пару выдаёт владелец — и с этой машины, и из окна к серверу.

        `local_only` на /pair/* делал QR невозможным ровно там, где он нужен:
        окно к серверу стоит не на той машине, где канал, и получало 403.
        Ключу устройства сюда по-прежнему нельзя.
        """
        for path in ("/pair/new", "/pair/devices", "/pair/revoke"):
            route, _ = deskapp.match_route(
                "GET" if path == "/pair/devices" else "POST", path)
            self.assertIsNotNone(route, path)
            self.assertFalse(route.local_only, f"{path}: снова только с этой машины")
            self.assertFalse(deskapp._scope_ok("device", path), f"{path}: телефону нельзя")
            self.assertTrue(deskapp._scope_ok("owner", path))
            self.assertFalse(deskapp._open_path(path), f"{path}: без ключа нельзя")

    def test_device_keys_live_where_the_channel_can_write(self):
        """Ключи телефонов — данные КАНАЛА, а не агента.

        На сервере дерево смонтировано на чтение, и запись в него падала
        `Read-only file system`: телефон получал 503 и не подключался вовсе.
        `HELENE_DESK_STATE` уводит файл в свою папку; без переменной — как было,
        рядом с состоянием агента (на Windows канал и дерево живут вместе).
        """
        saved = readers.tree
        try:
            with tempfile.TemporaryDirectory() as tmp:
                readers.tree = lambda: Path(tmp) / "tree"
                os.environ.pop("HELENE_DESK_STATE", None)
                self.assertEqual(deskapp._devices_path(),
                                 Path(tmp) / "tree" / "memory" / ".state" / "devices.json")
                os.environ["HELENE_DESK_STATE"] = str(Path(tmp) / "state")
                self.assertEqual(deskapp._devices_path(), Path(tmp) / "state" / "devices.json")
                # И запись туда действительно идёт — с созданием папки.
                deskapp._save_devices([{"id": "x"}])
                self.assertTrue((Path(tmp) / "state" / "devices.json").is_file())
        finally:
            readers.tree = saved
            os.environ.pop("HELENE_DESK_STATE", None)
            deskapp._DEVICES_CACHE["stamp"] = None

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


class ForeignPair(unittest.TestCase):
    """07.10, слово владельца: «при любом раскладе пишется "код устарел"».

    Пары живут в памяти КАНАЛА: QR, выданный одной копией, другой не известен.
    Раньше оба случая несли один текст, телефон не мог отличить «протух» от
    «выдан другим адресом» и вечно советовал перерисовать QR. Теперь
    неизвестный каналу токен — отдельный отказ.
    """

    def test_unknown_token_is_foreign_not_spent(self):
        deskapp._PAIRS.pop("no-such-token", None)
        with self.assertRaises(web.HTTPForbidden) as caught:
            deskapp._redeem("no-such-token", "iPhone", "10.0.0.5")
        self.assertIn("не выдавал", str(caught.exception.text))

    def test_expired_pair_keeps_the_old_words(self):
        deskapp._PAIRS["dead-token"] = {"expires": time.time() - 1, "uses": 3}
        try:
            with self.assertRaises(web.HTTPForbidden) as caught:
                deskapp._redeem("dead-token", "iPhone", "10.0.0.5")
            self.assertIn("устарел", str(caught.exception.text))
        finally:
            deskapp._PAIRS.pop("dead-token", None)


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
        self.assertIn("window.DESK_CONFIG = ", text)
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
        # До ключа: страница читает config.js первым тегом, ключа у неё ещё нет.
        self.assertIn("/m/config.js", deskapp._OPEN_PATHS)
        self.assertIn("/config.js", deskapp._OPEN_PATHS)


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


class ExternalHostAndPairUses(unittest.TestCase):
    """Внешний адрес канала (`phone.external`, 06.10) и лимит пары.

    Телефон приходит по внешнему имени через сервер с белым IP ровно за тем
    ключом, которого у него ещё нет: Host-гейт обязан пускать РОВНО это имя
    и ничего сверх него. Пара живёт три захода (слово владельца 06.10: с
    Firefox по умолчанию двух не хватало — браузер и «Установить» съедали
    оба ещё до значка на «Домой»).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "helene.json"
        self.saved_config_path = readers.config_path
        readers.config_path = lambda: self.cfg if self.cfg.is_file() else None
        deskapp._EXTERNAL_HOST_CACHE["stamp"] = None
        deskapp._EXTERNAL_HOST_CACHE["host"] = ""
        self.addCleanup(self._restore)

    def _restore(self):
        readers.config_path = self.saved_config_path
        deskapp._EXTERNAL_HOST_CACHE["stamp"] = None
        deskapp._EXTERNAL_HOST_CACHE["host"] = ""
        self.tmp.cleanup()

    def _write_cfg(self, payload: dict) -> None:
        self.cfg.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        # Кэш живёт по отпечатку файла: новое содержание обязано его сбить.
        deskapp._EXTERNAL_HOST_CACHE["stamp"] = None

    @staticmethod
    def _request_with_host(name: str):
        return type("R", (), {"headers": {"Host": name}})()

    def test_external_host_from_url_and_bare(self):
        self._write_cfg({"phone": {"external": "https://helene.209.222.251.74.nip.io"}})
        self.assertEqual(deskapp._phone_external_host(), "helene.209.222.251.74.nip.io")
        self._write_cfg({"phone": {"external": "helene.example.com"}})
        self.assertEqual(deskapp._phone_external_host(), "helene.example.com")
        self._write_cfg({"phone": {}})
        self.assertEqual(deskapp._phone_external_host(), "")
        self._write_cfg({})
        self.assertEqual(deskapp._phone_external_host(), "")

    def test_external_host_ignores_garbage(self):
        for junk in ("*", "*.*", "  ", "://", "http://", "ftp://tunnel.example.com"):
            self._write_cfg({"phone": {"external": junk}})
            self.assertEqual(deskapp._phone_external_host(), "", repr(junk))

    def test_external_host_rejects_foreign_schemes(self):
        """Схема — только http/https: фронт строит QR по тем же правилам, и
        ftp:// в гейте открыл бы имя, на котором ссылка битая (ревью 06.10)."""
        self._write_cfg({"phone": {"external": "ftp://helene.example.com"}})
        self.assertEqual(deskapp._phone_external_host(), "")
        self.assertFalse(deskapp._host_ok(self._request_with_host("helene.example.com")))

    def test_host_gate_admits_exactly_the_external_name(self):
        self._write_cfg({"phone": {"external": "https://helene.example.com"}})
        self.assertTrue(deskapp._host_ok(self._request_with_host("helene.example.com")))
        self.assertTrue(deskapp._host_ok(self._request_with_host("helene.example.com:8094")))
        # Чужие имена рядом с разрешённым — не пускаются: гейт не расширился.
        self.assertFalse(deskapp._host_ok(self._request_with_host("evil.example.com")))
        self.assertFalse(deskapp._host_ok(self._request_with_host("helene.example.com.evil.net")))
        # А то, что пускалось и раньше, продолжает пускаться.
        self.assertTrue(deskapp._host_ok(self._request_with_host("192.168.1.5:8094")))
        self.assertTrue(deskapp._host_ok(self._request_with_host("localhost:8094")))

    def test_pair_lives_three_redemptions(self):
        saved_tree = readers.tree
        readers.tree = lambda: Path(self.tmp.name) / "tree"
        try:
            pair = deskapp._new_pair()
            self.assertEqual(pair["uses"], 3, "слово владельца 06.10: три захода")
            keys = [deskapp._redeem(pair["token"], "Android Firefox", "10.0.0.7")
                    for _ in range(3)]
            self.assertTrue(all(g.get("key") for g in keys))
            self.assertEqual(keys[0]["key"], keys[-1]["key"], "тот же токен — тот же ключ")
            with self.assertRaises(Exception):
                deskapp._redeem(pair["token"], "Android", "10.0.0.7")
        finally:
            readers.tree = saved_tree
            deskapp._DEVICES_CACHE["stamp"] = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
