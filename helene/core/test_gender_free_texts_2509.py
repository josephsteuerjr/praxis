"""Тексты, которые читает модель, — без рода агента (издание, 25.09).

Ревью A3/A12: «агент больше не «она»» держалось на замене конкретных строк; откат любой не
пришпиленной строки был тихим. Этот стенд — сканер: описания всех рук, назначения рук и
строковые литералы выбранных модулей (расписки рук, заметки рабочего хода, промпты) не
содержат женских форм первого лица и «she/her/mistress». Докстринги и комментарии не
считаются — их модель не читает. Имена классов пропусков perception («не_увидела») —
данные, не речь; строки о вещах («очередь закрыла», «удалёнка ушла») — не про агента.

Запуск:  python praxis_test.py test_gender_free_texts_2509 -v
"""
from __future__ import annotations

import io
import re
import tokenize
import unittest
from pathlib import Path

import agent

HERE = Path(__file__).resolve().parent
FEM = re.compile(
    r"(?<![а-яё])(записала|закрыла|сняла|поставила|просила|сказала|нашла|запарковала|разбудила|ушла|"
    r"свела|подготовила|обрезала|заморозила|разморозила|ревизовала|попросила|написала|напечатала|"
    r"звала|ждала|упёрлась|делала|отправила|ответила|решила|прочитала|сделала|поняла|заметила|выбрала|"
    r"переписала|запустила|вернула|откатилась|отменила|включила|выключила|свернула|перевыпустила|"
    # 26.09 (ревью W3 S5): формы, с которыми в издание пришёл сон
    r"пропустила|согласна|пережёвывала)"
    r"(?![а-яё])", re.I)
ENG = re.compile(r"\b(she|her|herself|mistress)\b")
# Строки, где женская форма — не про агента (о вещах, цитата чужих слов, имена классов).
ALLOW = (
    "очередь закрыла", "удалёнка ушла", "ревизия сама", "не_увидела", "отложила", "наррация не ушла",
    "«свела» было бы", "не могла", "Очередь дошлёт сама", "(сама)",
    # существительные женского рода, не агент:
    "Прошлая попытка написала", "граница свёртки из окна ушла", "сделала эвристика",
)


def _literals(path: Path):
    src = path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING:
            if tok.string.lstrip("rRbBfFuU").startswith(('"""', "'''")):
                continue
            yield tok.start[0], tok.string
        elif tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
            yield tok.start[0], tok.string


class HandTextsHaveNoGender(unittest.TestCase):
    def test_tool_descriptions_and_purposes(self):
        seen = set()
        for tool in agent.BASE_TOOLS + agent.OWNER_TOOLS + agent.SHARED_CONTEXT_TOOLS + agent.PRAXIS_SELF_TOOLS:
            name = tool.get("name")
            if name in seen:
                continue
            seen.add(name)
            desc = str(tool.get("description") or "")
            if any(a in desc for a in ALLOW):
                continue
            hit = FEM.search(desc)
            self.assertIsNone(hit, f"{name}: {hit and hit.group(0)!r} в описании")
            for pname, spec in ((tool.get("input_schema") or {}).get("properties") or {}).items():
                text = str((spec or {}).get("description") or "")
                self.assertIsNone(FEM.search(text), f"{name}.{pname}: {text[:80]!r}")
        for name, purpose in agent.HAND_PURPOSE.items():
            self.assertIsNone(FEM.search(str(purpose)), f"HAND_PURPOSE[{name}]: {purpose!r}")

    def test_receipts_and_notes_in_modules(self):
        hits = []
        for mod in ("agent.py", "work_loop.py", "forge.py", "rooms.py", "stewardship.py", "tool_text_en.py",
                    "mtproto_runner.py", "memory_life.py", "frame_shadow.py", "brain.py",   # V4 F5
                    # 26.09, порт КЕАТ: тексты, которые уезжают в модель из новых модулей
                    # (сид будильника, шапка эпохи, пометка кадра v6, заглушка экономии).
                    "keat_live.py", "frame_epoch.py", "frame_serve.py", "keat_economy.py",
                    "frame_layout.py",
                    # 26.09 (ревью W3 S5): сон издания 1.0.1 впервые пустил в модель промпты
                    # ночной ревизии, РЕМ, жвачки и консолидации — и строки своего дневника.
                    "sleep.py", "identity.py", "formation.py", "consolidate.py"):
            for line, text in _literals(HERE / mod):
                if any(a in text for a in ALLOW):
                    continue
                m = FEM.search(text) or ENG.search(text)
                if m:
                    hits.append(f"{mod}:{line}: [{m.group(0)}] {text.strip()[:90]}")
        self.assertEqual(hits, [], "\n" + "\n".join(hits))

    def test_sleep_prompts_do_not_name_the_agent_praxis(self):
        """26.09 (ревью W3 S5): «Ты — Praxis» в ночной ревизии, РЕМ, жвачке и консолидации
        говорил агенту издания, что он — чужая личность. Метка актора событий — данные."""
        hits = []
        for mod in ("sleep.py", "identity.py", "formation.py", "consolidate.py"):
            for line, text in _literals(HERE / mod):
                if text.strip("\"'") == "Praxis":
                    continue          # actor="Praxis" — метка событий, не текст модели
                if re.search(r"\bPraxis\b|Праксис", text):
                    hits.append(f"{mod}:{line}: {text.strip()[:90]}")
        self.assertEqual(hits, [], "\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
