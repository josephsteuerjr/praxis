# -*- coding: utf-8 -*-
"""Живой канарей совместного зрения: сколько изображений НОГА принимает за один запрос.

Проба 21.09 доказала одну картинку; эта проверяет НЕСКОЛЬКО в одном messages
(соответствие и порядок), чтобы бюджет изображений на ногу был числом из факта.

    python desk/installer/vision_canary.py 3          # один запрос с 3 картинками
    python desk/installer/vision_canary.py 3 10       # два запроса: 3, затем 10

Ключ читается из живого llm.json (только чтение); в рецепте ключа нет.
Картинки — синтетические одноцветные PNG, вопрос о порядке цветов; проверка
требует полного правильного списка, «примерно угадал» не засчитывается.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path

LIVE_LLM_JSON = Path(os.environ.get("CANARY_LLM_JSON")
                     or r"C:\Program Files\Helene\data\memory\llm.json")
RECEIPT_DIR = Path(r"C:\Users\yegor\Downloads\Praxis\desk-notes\evidence\UI-01.10")

# Палитра различимых для модели названий цветов (ru-слова ждём в ответе).
PALETTE = [
    ("красный", (220, 30, 30)),
    ("синий", (30, 60, 220)),
    ("зелёный", (30, 170, 60)),
    ("жёлтый", (230, 220, 40)),
    ("фиолетовый", (140, 40, 200)),
    ("оранжевый", (240, 140, 30)),
    ("розовый", (240, 120, 170)),
    ("голубой", (90, 190, 230)),
    ("коричневый", (130, 80, 40)),
    ("серый", (130, 130, 130)),
]
PROMPT = ("Перед тобой {n} изображений в одном сообщении, по порядку. "
          "Перечисли цвет КАЖДОГО по порядку номеров, строго в формате "
          "«1: цвет 2: цвет …». Никаких других слов.")


def png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    """Настоящий квадратный PNG без PIL: одинаково честен для любой ноги."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))


def probe(count: int, *, model: str, client, seed: int = 0) -> dict:
    colors = list(PALETTE[:count])
    if seed:
        import random
        random.Random(seed).shuffle(colors)
    content: list[dict] = [{"type": "text", "text": PROMPT.format(n=count)}]
    for _name, rgb in colors:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": base64.b64encode(png(rgb)).decode("ascii")}})
    started = time.time()
    msg = client.messages.create(model=model, max_tokens=400, messages=[
        {"role": "user", "content": content}])
    took = round(time.time() - started, 1)
    text = "".join(getattr(b, "text", "") for b in msg.content
                   if getattr(b, "type", None) == "text")
    lowered = text.lower().replace("ё", "е")
    import re
    correct = []
    for n, name in [(i + 1, name.replace("ё", "е")) for i, (name, _rgb) in enumerate(colors)]:
        m = re.search(rf"(?:^|\s){n}:\s*([^\n]+?)(?=\s+\d+:|$)", lowered)
        if m and name in m.group(1):
            correct.append(n)
    return {"model": model, "images": count, "seconds": took,
            "stop_reason": getattr(msg, "stop_reason", None),
            "usage": getattr(msg, "usage", None) and {
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens},
            "answer": text.strip(), "correct_positions": correct,
            "pass": len(correct) == count}


def main(argv: list[str]) -> int:
    counts = [int(a) for a in argv[1:]] or [3]
    cfg = json.loads(LIVE_LLM_JSON.read_text(encoding="utf8"))
    fw = cfg["frameworks"]["anthropic"]
    import anthropic
    client = anthropic.Anthropic(base_url=fw["base_url"], api_key=fw["api_key"])
    model = "glm-5.3-flash"
    receipt = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "probe": "joint vision, multiple images in ONE request",
               "model": model, "endpoint": fw["base_url"], "results": []}
    for count in counts:
        result = probe(count, model=model, client=client, seed=int(os.environ.get("CANARY_SEED", "0")))
        receipt["results"].append(result)
        mark = "PASS" if result["pass"] else "FAIL"
        print(f"{mark}  {count} изображений за {result['seconds']}с: "
              f"{result['correct_positions']}/{count} позиций")
        print(f"     ответ: {result['answer'][:200]}")
    receipt["pass"] = all(r["pass"] for r in receipt["results"])
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    out = RECEIPT_DIR / ("vision-canary-glm-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + ".json")
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf8")
    print("рецепт:", out)
    return 0 if receipt["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
