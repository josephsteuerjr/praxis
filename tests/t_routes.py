# -*- coding: utf-8 -*-
"""Стенд таблицы маршрутов канала (задача A п. 1.11).

Запуск:  python tests/t_routes.py

Класс ошибки «ручка есть в одном канале из двух» уже стрелял дважды
(/api/home, /api/mode). Здесь проверяется, что HTTP-роутер aiohttp и
диспетчер канала строятся из ОДНОЙ таблицы: каждая строка ROUTES есть в
роутере, каждая ручка роутера под /api и /pair (кроме /pair/redeem —
телефон приходит за ключом по HTTP) есть в таблице, и диспетчер канала
находит каждую строку по методу и пути. Плюс поведение переводчиков:
Fail -> {status, error, code} в канале и текст с кодом по HTTP.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import deskapp  # noqa: E402
from aiohttp import web  # noqa: E402


class Table(unittest.TestCase):
    def test_router_is_built_from_the_table(self):
        app = deskapp.build_app()
        registered = set()
        for resource in app.router.resources():
            info = resource.get_info()
            path = info.get("path") or info.get("formatter") or ""
            for route in resource:
                if route.method in ("GET", "POST", "DELETE"):
                    registered.add((route.method, path))
        table = {(r.method, r.path) for r in deskapp.ROUTES}
        missing = table - registered
        self.assertFalse(missing, f"строки таблицы без HTTP-ручки: {missing}")
        api_only = {(m, p) for m, p in registered
                    if (p.startswith("/api/") or p.startswith("/pair/")) and p != "/pair/redeem"}
        stray = api_only - table
        self.assertFalse(stray, f"HTTP-ручки мимо таблицы: {stray}")

    def test_tunnel_finds_every_row(self):
        for route in deskapp.ROUTES:
            sample = route.path.replace("{peer}", "-100123").replace("{stream}", "s1") \
                .replace("{run_id}", "run-20260907T000000000000Z-deadbeef")
            found, params = deskapp.match_route(route.method, sample)
            self.assertIs(found, route, f"{route.method} {route.path} не найден каналом")
            for name in ("peer", "stream", "run_id"):
                if "{" + name + "}" in route.path:
                    self.assertIn(name, params)
        self.assertEqual(deskapp.match_route("GET", "/api/nope"), (None, {}))
        # Метод — часть ключа: GET /api/md и POST /api/md — разные строки.
        self.assertIsNot(deskapp.match_route("GET", "/api/md")[0],
                         deskapp.match_route("POST", "/api/md")[0])
        self.assertIsNone(deskapp.match_route("DELETE", "/api/say")[0])

    def test_no_duplicate_rows(self):
        keys = [(r.method, r.path) for r in deskapp.ROUTES]
        self.assertEqual(len(keys), len(set(keys)))


class Dispatch(unittest.TestCase):
    """Переводчики: один и тот же обработчик даёт согласованные ответы."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        (self.tree / "memory" / ".state").mkdir(parents=True)
        self.saved_tree = deskapp.readers.tree
        deskapp.readers.tree = lambda: self.tree

    def tearDown(self):
        deskapp.readers.tree = self.saved_tree
        self.tmp.cleanup()

    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_home_answers_in_both_channels(self):
        reply = self._run(deskapp._tunnel_dispatch("GET", "/api/home", None, local=True))
        self.assertEqual(reply["status"], 200)
        self.assertEqual(reply["body"]["tree"], str(self.tree.resolve()))

    def test_404_and_403_are_words(self):
        self.assertEqual(self._run(deskapp._tunnel_dispatch("GET", "/api/nope", None))["status"], 404)
        remote = self._run(deskapp._tunnel_dispatch("POST", "/pair/new", None, local=False))
        self.assertEqual(remote["status"], 403)
        device = self._run(deskapp._tunnel_dispatch("GET", "/api/anatomy", None, role="device"))
        self.assertEqual(device["status"], 403)

    def test_fail_carries_code_in_the_channel(self):
        async def failing(call):
            return deskapp.Fail(409, "устарело", "conflict")
        route = deskapp.Route("POST", "/api/test-fail", failing)
        saved = deskapp._ROUTE_INDEX
        deskapp._ROUTE_INDEX = saved + ((route, deskapp._route_regex(route.path)),)
        try:
            reply = self._run(deskapp._tunnel_dispatch("POST", "/api/test-fail", {}, local=True))
        finally:
            deskapp._ROUTE_INDEX = saved
        self.assertEqual(reply, {"status": 409, "error": "устарело", "code": "conflict"})

    def test_http_errors_become_status_and_text(self):
        async def not_found(call):
            raise web.HTTPNotFound(text="нет такого прогона")
        route = deskapp.Route("GET", "/api/test-404", not_found)
        saved = deskapp._ROUTE_INDEX
        deskapp._ROUTE_INDEX = saved + ((route, deskapp._route_regex(route.path)),)
        try:
            reply = self._run(deskapp._tunnel_dispatch("GET", "/api/test-404", None, local=True))
        finally:
            deskapp._ROUTE_INDEX = saved
        self.assertEqual(reply["status"], 404)
        self.assertEqual(reply["error"], "нет такого прогона")

    def test_query_reaches_the_handler(self):
        seen = {}

        async def echo(call):
            seen.update(call.query)
            seen["match"] = call.match
            return {"ok": True}
        route = deskapp.Route("GET", "/api/test-echo/{peer}", echo)
        saved = deskapp._ROUTE_INDEX
        deskapp._ROUTE_INDEX = saved + ((route, deskapp._route_regex(route.path)),)
        try:
            reply = self._run(deskapp._tunnel_dispatch("GET", "/api/test-echo/abc?n=5&x=y", None))
        finally:
            deskapp._ROUTE_INDEX = saved
        self.assertEqual(reply["status"], 200)
        self.assertEqual(seen, {"n": "5", "x": "y", "match": {"peer": "abc"}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
