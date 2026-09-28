#!/usr/bin/env python3
"""Вшить значок (.ico) в готовый Windows-exe — без пересборки и без чужих инструментов.

    python desk/shell/icons/set_exe_icon.py <файл.exe> <значок.ico>

Зачем (28.09, Егор: «как и иконка реле»): `helene-relay.exe` собирается `cargo build`
из зеркала серверного исходника (installer/relay_src.py), и значка у него не было вовсе —
Windows рисовал пустой стандартный. Трогать исходник реле нельзя: зеркало сверяется с живым
сервером отпечатком. Поэтому значок кладётся в ресурсы уже собранного exe: RT_ICON на каждый
размер и RT_GROUP_ICON №1 — через BeginUpdateResource/UpdateResource самой Windows.
"""
from __future__ import annotations

import ctypes
import struct
import sys
from ctypes import wintypes
from pathlib import Path

RT_ICON = 3
RT_GROUP_ICON = 14
LANG_NEUTRAL = 0


def set_icon(exe: Path, ico: Path) -> int:
    data = ico.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1 or not count:
        raise ValueError(f"{ico} — не .ico")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    k32.BeginUpdateResourceW.restype = wintypes.HANDLE
    k32.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD, ctypes.c_void_p, wintypes.DWORD]
    k32.UpdateResourceW.restype = wintypes.BOOL
    k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    k32.EndUpdateResourceW.restype = wintypes.BOOL

    h = k32.BeginUpdateResourceW(str(exe), False)
    if not h:
        raise OSError(ctypes.get_last_error(), f"не открыть ресурсы {exe}")
    group = struct.pack("<HHH", 0, 1, count)
    try:
        for i in range(count):
            w, hgt, colors, _res, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
            blob = data[offset:offset + size]
            buf = ctypes.create_string_buffer(blob, len(blob))
            if not k32.UpdateResourceW(h, RT_ICON, i + 1, LANG_NEUTRAL, buf, len(blob)):
                raise OSError(ctypes.get_last_error(), f"RT_ICON {i + 1}")
            group += struct.pack("<BBBBHHIH", w, hgt, colors, 0, planes or 1, bits or 32, size, i + 1)
        gbuf = ctypes.create_string_buffer(group, len(group))
        if not k32.UpdateResourceW(h, RT_GROUP_ICON, 1, LANG_NEUTRAL, gbuf, len(group)):
            raise OSError(ctypes.get_last_error(), "RT_GROUP_ICON")
    except Exception:
        k32.EndUpdateResourceW(h, True)  # отменить — exe остаётся как был
        raise
    if not k32.EndUpdateResourceW(h, False):
        raise OSError(ctypes.get_last_error(), f"не записать ресурсы {exe}")
    return count


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    n = set_icon(Path(sys.argv[1]), Path(sys.argv[2]))
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"значок вшит: {sys.argv[1]} ← {sys.argv[2]} ({n} размеров)")
