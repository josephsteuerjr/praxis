# -*- coding: utf-8 -*-
"""Порт на Windows: два рычага — LF на записях и петля мимо прокси.

Python на Windows переводит "\n" в "\r\n" при текстовой записи без явного
newline. Дерево Praxis меряет себя байт-в-байт (кадр, отпечатки, LCP, гейт) и
живёт на LF; из ~760 текстовых записей newline закреплён у пяти критических.
Свип всех вызовов — чудовищный дифф против «порт как есть», поэтому порт
закрепляет LF одним рычагом: sitecustomize импортируется интерпретатором
автоматически (site.py, sys.path; в поставке Элен его зовёт `runner.py` явно —
дерево кладётся в sys.path позже старта). Сам LF-рычаг на POSIX не встаёт: он
целиком под `if sys.platform == "win32"`.

Читается всё как раньше: universal newlines на чтении переваривают любой хвост.
csv-писатели по докам обязаны передавать newline="" явно — их поведение не
трогаем (явный аргумент всегда побеждает).

Второй рычаг — прокси. Он ниже и НЕ под win32: причина у него не в переводе
строк, а в том, что на этой машине половина «сети» — это своя же петля.
"""
import functools
import ipaddress
import sys
import urllib.request

# ── Рычаг 2: 127.0.0.1 не ходит через прокси ─────────────────────────────────
#
# ЖИВОЙ СЛУЧАЙ 15.09. У пользователя поднят Psiphon; в настройках Windows стоит
# галочка «не использовать прокси-сервер для локальных адресов». Тело
# подключалось к мосту (мост и тело говорят сырым TCP и прокси не видят), а вот
# КОНТРОЛЛЕР — `body_client` — ходит к мосту обычным `urllib`, и тот на Windows
# берёт прокси из реестра. Проба `body.status` уезжала в Psiphon, не доезжала
# никуда, и окно честно показывало «тело не подключилось» — при живом теле.
# Лечилось руками: дописать `127.0.0.1;localhost` в список исключений Windows.
#
# Почему галочка не помогает. В реестре она — строка `<local>`, и
# `urllib.request.proxy_bypass_registry` понимает её буквально: «имя без точки».
# У `127.0.0.1` точки есть — значит не локальный. Ровно та же мерка у
# `requests` (он зовёт этот же `proxy_bypass`).
#
# Через петлю здесь ходят: мост тела (127.0.0.1:9473), реле мозга
# (127.0.0.1:5011) и его счета, проба моделей, локальные эмбеддинги. Ни одному
# из них прокси не нужен никогда — это соседний процесс на этой же машине.
# Поэтому рычаг отвечает «мимо прокси» на петлю ДО того, как спросят реестр или
# среду; для всех остальных адресов ответ прежний, и `http_proxy`/`no_proxy`
# продолжают работать как работали. Обратной надобности — гонять петлю через
# прокси — не существует.
_LOOPBACK_HOSTS = frozenset({"localhost", "localhost.localdomain"})


def _is_loopback(host) -> bool:
    """«Этот же компьютер»? Принимает и `127.0.0.1:9473`, и `[::1]`, и имя."""
    text = str(host or "").strip().strip(".").lower()
    if not text:
        return False
    if text.startswith("["):                     # [::1]:9473 — адрес в скобках
        text = text[1:text.index("]")] if "]" in text else text[1:]
    elif text.count(":") == 1:                   # 127.0.0.1:9473 — хвост порта
        text = text.rsplit(":", 1)[0]
    if text in _LOOPBACK_HOSTS or text.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


_raw_proxy_bypass = urllib.request.proxy_bypass


@functools.wraps(_raw_proxy_bypass)
def _proxy_bypass(host):
    return True if _is_loopback(host) else _raw_proxy_bypass(host)


# Подменяем атрибут МОДУЛЯ: `ProxyHandler.proxy_open` берёт `proxy_bypass` из
# глобалей `urllib.request` на каждом вызове, а `requests` импортирует его к
# себе — но позже, чем этот файл (рычаг стоит до `import agent`, runner.py).
urllib.request.proxy_bypass = _proxy_bypass

if sys.platform == "win32":
    import builtins
    import io
    import pathlib

    def _pin_open(raw_open):
        @functools.wraps(raw_open)
        def opened(file, mode="r", *args, **kwargs):
            if (isinstance(mode, str) and "b" not in mode
                    and any(flag in mode for flag in ("w", "a", "x", "+"))
                    and "newline" not in kwargs and len(args) < 4):
                kwargs["newline"] = "\n"
            return raw_open(file, mode, *args, **kwargs)
        return opened

    builtins.open = _pin_open(builtins.open)
    io.open = _pin_open(io.open)

    _raw_write_text = pathlib.Path.write_text

    @functools.wraps(_raw_write_text)
    def _write_text(self, data, encoding=None, errors=None, newline=None):
        if newline is None:
            newline = "\n"
        return _raw_write_text(self, data, encoding=encoding, errors=errors,
                               newline=newline)

    pathlib.Path.write_text = _write_text
