# -*- coding: utf-8 -*-
"""Рисовалка иконки: бежевый квадрат тем же цветом бумаги, что и окно.

ЗАПУСКАЕТСЯ РУКАМИ, при пересборке продукта. Установщик её НЕ зовёт вовсе:
режим «с буквой» — персональная иконка агента на ярлыках, в окне, у часов и в
уведомлениях — ОТМЕНЁН владельцем 03.09.2026 («имя агента не подменяет имя
продукта», docs/PLAN.md). В setup/src/install.rs иконка ярлыков жёстко None, и
кода, который звал бы этот файл, там больше нет. Если читаешь это как агент:
персональной иконки с твоим инициалом на этой машине нет и при установке она
не рисовалась — значок продукта один для всех.

Без буквы — иконка продукта (рамка внутри квадрата): установщик, exe, ярлыки.
С буквой — отменённый режим, оставлен только для ручного запуска. Шрифт —
Constantia из Windows (в ней есть кириллица), запасной — Cambria, дальше
стандартный.

    python make_icon.py --out <папка>/icon
    python make_icon.py --out <папка>/icon --letter H   # режим не используется

Пишет <out>.ico (16…256), <out>.png (256) и <out>-32.png.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PAPER = (253, 252, 250, 255)     # та же бумага, что фон окна (--ground); слово владельца
EDGE = (42, 38, 34, 46)          # едва заметный край, чтобы не растворялась на белом
INK = (42, 38, 34, 255)          # чернила, как --ink
FRAME = (42, 38, 34, 150)        # рамка продукта, полупрозрачная

FONTS = [
    r"C:\Windows\Fonts\constan.ttf",
    r"C:\Windows\Fonts\cambria.ttc",
    r"C:\Windows\Fonts\georgia.ttf",
    r"C:\Windows\Fonts\times.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONTS:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def render(size: int, letter: str | None) -> Image.Image:
    scale = 4  # рисуем крупнее и сжимаем: ровные края и у 16 px
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(s * 0.22)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, fill=PAPER, outline=EDGE, width=max(2, int(s * 0.012)))
    if letter:
        # Имя целиком, мелким шрифтом (слово владельца): кегль подбирается так,
        # чтобы слово легло в квадрат с полями; одна буква выходит крупной сама.
        size_pt = int(s * 0.56)
        while size_pt > int(s * 0.12):
            font = _font(size_pt)
            left, top, right, bottom = d.textbbox((0, 0), letter, font=font)
            if right - left <= s * 0.68:
                break
            size_pt = int(size_pt * 0.92)
        w, h = right - left, bottom - top
        x = (s - w) / 2 - left
        y = (s - h) / 2 - top - s * 0.02
        d.text((x, y), letter, font=font, fill=INK)
    else:
        pad = int(s * 0.24)
        width = max(2, int(s * 0.05))
        d.rounded_rectangle((pad, pad, s - pad - 1, s - pad - 1), radius=int(s * 0.08),
                            outline=FRAME, width=width)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="путь без расширения")
    ap.add_argument("--letter", default="", help="имя агента целиком; пусто — иконка продукта")
    args = ap.parse_args()
    letter = args.letter.strip() or None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = {n: render(n, letter) for n in sizes}
    frames[256].save(str(out) + ".png")
    frames[32].save(str(out) + "-32.png")
    frames[128].save(str(out) + "-128.png")
    # ICO: каждый размер отрисован отдельно, а не ужат из одного.
    frames[256].save(
        str(out) + ".ico",
        format="ICO",
        sizes=[(n, n) for n in sizes],
        append_images=[frames[n] for n in sizes if n != 256],
    )
    print(f"иконка: {out}.ico ({'имя ' + letter if letter else 'продукт'})")


if __name__ == "__main__":
    main()
