# -*- coding: utf-8 -*-
"""Порт на Windows: LF по умолчанию для всех текстовых записей дерева.

Python на Windows переводит "\n" в "\r\n" при текстовой записи без явного
newline. Дерево Praxis меряет себя байт-в-байт (кадр, отпечатки, LCP, гейт) и
живёт на LF; из ~760 текстовых записей newline закреплён у пяти критических.
Свип всех вызовов — чудовищный дифф против «порт как есть», поэтому порт
закрепляет LF одним рычагом: sitecustomize импортируется интерпретатором
автоматически (site.py, sys.path), а на POSIX этот файл — no-op по построению.

Читается всё как раньше: universal newlines на чтении переваривают любой хвост.
csv-писатели по докам обязаны передавать newline="" явно — их поведение не
трогаем (явный аргумент всегда побеждает).
"""
import sys

if sys.platform == "win32":
    import builtins
    import functools
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
