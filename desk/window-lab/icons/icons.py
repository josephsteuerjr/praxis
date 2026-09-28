#!/usr/bin/env python3
"""Значки Hélène и реле (выбор Егора 28.09: оба «А») — из render.html в файлы поставки.

    python window-lab/icons/icons.py

Рисует Edge без окна (тот же Chromium, что у окна), режет плитки по 512 px, ужимает под
каждый размер своей плиткой (своя толщина линий) и кладёт:
  shell/icons/  icon.png 256, 128x128.png, 32x32.png, icon.ico (16…256),
                tray-template.png 22 и @2x 44 (строка меню macOS), relay.ico, relay.png
  setup/icons/  icon.png, 128x128.png, 32x32.png, icon.ico — у мастера тот же значок
Прежние значки Praxis (shell/icons-praxis) не трогаются: это другой продукт.
"""
from __future__ import annotations

import io
import sys
import os
import struct
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
DESK = HERE.parent.parent
EDGE = next((p for p in (
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
) if p.exists()), None)
TILE = 512
SIZES = [256, 128, 64, 48, 32, 24, 16]  # порядок плиток в render.html


def shoot(which: str, tiles: int) -> list[Image.Image]:
    if EDGE is None:
        raise SystemExit("Нет Microsoft Edge — рисовать значки нечем")
    out = Path(tempfile.gettempdir()) / f"helene-icons-{which}.png"
    url = (HERE / "render.html").as_uri() + f"?icon={which}"
    subprocess.run([str(EDGE), "--headless=new", "--hide-scrollbars", "--allow-file-access-from-files",
                    "--default-background-color=00000000", "--force-device-scale-factor=1",
                    f"--window-size={TILE * tiles},{TILE}", "--virtual-time-budget=3000",
                    f"--screenshot={out}", url], check=True, capture_output=True)
    sheet = Image.open(out).convert("RGBA")
    return [sheet.crop((i * TILE, 0, (i + 1) * TILE, TILE)) for i in range(tiles)]


def png(img: Image.Image, size: int) -> bytes:
    buf = io.BytesIO()
    img.resize((size, size), Image.LANCZOS).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def write_ico(path: Path, frames: list[tuple[int, bytes]]) -> None:
    """ICO с PNG внутри — каждый размер своей плиткой (Windows Vista+ читает все)."""
    head = struct.pack("<HHH", 0, 1, len(frames))
    table = b""
    blobs = b""
    offset = 6 + 16 * len(frames)
    for size, data in sorted(frames):
        side = 0 if size >= 256 else size
        table += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    path.write_bytes(head + table + blobs)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    helene = dict(zip(SIZES, shoot("helene", len(SIZES))))
    relay = dict(zip(SIZES, shoot("relay", len(SIZES))))
    tmpl = shoot("template", 2)

    for folder in (DESK / "shell" / "icons", DESK / "setup" / "icons"):
        (folder / "icon.png").write_bytes(png(helene[256], 256))
        (folder / "128x128.png").write_bytes(png(helene[128], 128))
        (folder / "32x32.png").write_bytes(png(helene[32], 32))
        write_ico(folder / "icon.ico", [(s, png(helene[s], s)) for s in SIZES])
        print("значок Hélène →", folder)
    icons = DESK / "shell" / "icons"
    (icons / "tray-template.png").write_bytes(png(tmpl[0], 22))
    (icons / "tray-template@2x.png").write_bytes(png(tmpl[1], 44))
    write_ico(icons / "relay.ico", [(s, png(relay[s], s)) for s in SIZES])
    (icons / "relay.png").write_bytes(png(relay[256], 256))
    print("значок реле →", icons / "relay.ico")


if __name__ == "__main__":
    main()
