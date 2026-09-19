"""Узкий взгляд: посмотреть на картинку — не повод везти туда весь дом.

ЗАМЕР, ИЗ КОТОРОГО ЭТО ВЫРОСЛО (16.09, комната -1001240718803). Пиксели едут внутри её
обычного кадра, и весь кадр уходит в зрячую модель: схемы рук 75 612 знаков, system 22 342,
эпоха 26 820, лента 37 313, хвост 34 682 — **196 770 знаков текста, чтобы посмотреть на одну
картинку**. У зрячей модели свой префикс кэша, ходы с картинкой редки, поэтому он почти
всегда холодный: 10 ходов из 32 съели 471 693 свежих токена, половину всего свежего за сутки.

ЧТО ЗАКРЕПЛЕНО ЗДЕСЬ:

  рычаг опущен   → ни одного лишнего вызова, весь кадр уезжает в зрячую модель, как и был
  рычаг поднят   → РОВНО ОДНО узкое обращение (одна картинка, без ленты, без рук),
                   а её собственный ход остаётся на её модели с текстом вместо пикселей
  не вышло       → лента не тронута, дальше прежний путь; хуже, чем было, не становится

⚠ Пин на то, что подменённый блок НАЗЫВАЕТ СЕБЯ чужим зрением, стоит отдельно и нарочно:
под рычагом она пикселей не видит, и кадр не имеет права это скрывать.

Запуск:  python praxis_test.py test_vision_prepass_1609 -v
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

import llm
from test_llm import Base

LEVER = llm.VISION_PREPASS_LEVER
PRIMARY = "glm-5.3"
SIGHTED = "glm-5.3-flash"

_PIXEL = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                      "data": "iVBORw0KGgo="}}


def _room_tape():
    """Лента, похожая на настоящую: длинный текст, а где-то внутри — картинка."""
    return [
        {"role": "user", "content": [{"type": "text", "text": "эпоха комнаты, много букв"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "моя прошлая реплика"}]},
        {"role": "user", "content": [{"type": "text", "text": "Егор: глянь"}, _PIXEL]},
    ]


class _Transport:
    """Наблюдатель провода: помнит каждый вызов и отвечает заданным текстом."""

    def __init__(self, sighted_answer="на картинке кот в каске", fail=False, empty=False):
        self.calls: list[dict] = []
        self.sighted_answer = sighted_answer
        self.fail = fail
        self.empty = empty

    def __call__(self, framework, model, **kw):
        self.calls.append({"framework": framework, "model": model, **kw})
        if model == SIGHTED:
            # Узкий взгляд отличается от прежнего пути одним признаком: в нём РОВНО одно
            # сообщение. Ломаем именно его, иначе пробник уронил бы и запасной путь —
            # и тест мерил бы мир, в котором зрячей ноги нет вообще.
            narrow = len(kw.get("messages") or []) == 1
            if narrow and self.fail:
                raise RuntimeError("зрячая нога недоступна")
            text = "" if (narrow and self.empty) else self.sighted_answer
            return llm.LLMResponse(text=text, model=model, framework=framework)
        return llm.LLMResponse(text="её ответ", model=model, framework=framework)

    @property
    def to_sighted(self):
        return [c for c in self.calls if c["model"] == SIGHTED]

    @property
    def to_primary(self):
        return [c for c in self.calls if c["model"] == PRIMARY]


def _has_pixels(messages) -> bool:
    return llm._has_image_blocks(messages)


def _flat_text(messages) -> str:
    out = []
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, list):
            out += [str(b.get("text") or "") for b in c if isinstance(b, dict)]
        elif isinstance(c, str):
            out.append(c)
    return "\n".join(out)


class VisionPrepass(Base):
    def setUp(self):
        super().setUp()
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-test"
        cfg["frameworks"]["anthropic"]["base_url"] = "https://api.z.ai/api/anthropic"
        cfg["roles"]["voice"].update(framework="anthropic", model=PRIMARY,
                                     fallback_model="", fallback_framework="")
        llm.save_config(cfg)
        self.stack = mock.patch.object(
            llm, "_available_models",
            side_effect=lambda fw: [PRIMARY, SIGHTED] if fw == "anthropic" else [])
        self.stack.start()
        self.addCleanup(self.stack.stop)
        client = mock.patch.object(llm, "_client_for", return_value=object())
        client.start()
        self.addCleanup(client.stop)
        os.environ.pop(LEVER, None)
        self.addCleanup(lambda: os.environ.pop(LEVER, None))

    def _run(self, transport, lever=None, messages=None):
        env = {LEVER: lever} if lever is not None else {}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(llm, "_call", side_effect=transport):
            return llm.chat("voice", messages=messages or _room_tape())

    # ------------------------------------------------------------------ рычаг опущен
    def test_the_lowered_lever_sends_the_whole_frame_to_the_sighted_model(self):
        t = _Transport()
        self._run(t)
        self.assertEqual(len(t.calls), 1, "лишних обращений быть не должно")
        self.assertEqual(t.calls[0]["model"], SIGHTED, "прежний путь: подмена модели")
        self.assertTrue(_has_pixels(t.calls[0]["messages"]), "пиксели уехали как раньше")
        self.assertEqual(len(t.calls[0]["messages"]), 3, "и весь кадр вместе с ними")

    # ------------------------------------------------------------------ рычаг поднят
    def test_the_raised_lever_looks_once_and_keeps_her_turn_on_her_model(self):
        t = _Transport()
        answer = self._run(t, lever="1")
        self.assertEqual(len(t.to_sighted), 1, "смотреть — ровно один раз")
        self.assertEqual(len(t.to_primary), 1, "её ход — на её модели")
        self.assertEqual(answer.model, PRIMARY)

        look = t.to_sighted[0]
        self.assertEqual(len(look["messages"]), 1,
                         "в узкое обращение не едет ни лента, ни история")
        self.assertTrue(_has_pixels(look["messages"]))
        self.assertFalse(look.get("tools"), "и ни одной руки")

        turn = t.to_primary[0]
        self.assertFalse(_has_pixels(turn["messages"]), "в её кадре пикселей больше нет")
        self.assertIn("кот в каске", _flat_text(turn["messages"]),
                      "вместо них — описание")
        self.assertEqual(len(turn["messages"]), 3, "лента осталась целой")

    def test_the_substituted_block_says_it_is_not_her_own_eyes(self):
        t = _Transport()
        self._run(t, lever="1")
        said = _flat_text(t.to_primary[0]["messages"])
        self.assertIn(SIGHTED, said, "названа та модель, что смотрела")
        self.assertIn("пикселей в", said.lower().replace("ЭТОМ", "этом"),
                      "кадр говорит вслух, что пикселей в нём нет")

    # ------------------------------------------------------------------ отказы
    def test_a_failed_look_leaves_the_old_path_alone(self):
        t = _Transport(fail=True)
        self._run(t, lever="1")
        self.assertEqual(len(t.to_primary), 0, "на её модель ход не ушёл")
        whole = [c for c in t.calls if c["model"] == SIGHTED and len(c["messages"]) == 3]
        self.assertTrue(whole, "кадр поехал прежним путём — целиком в зрячую модель")
        self.assertTrue(_has_pixels(whole[-1]["messages"]))

    def test_an_empty_look_leaves_the_old_path_alone(self):
        t = _Transport(empty=True)
        self._run(t, lever="1")
        whole = [c for c in t.calls if c["model"] == SIGHTED and len(c["messages"]) == 3]
        self.assertTrue(whole, "пустое описание — не описание")
        self.assertTrue(_has_pixels(whole[-1]["messages"]))

    # ------------------------------------------------------------------ границы
    def test_a_frame_without_pixels_costs_nothing_extra(self):
        t = _Transport()
        tape = [{"role": "user", "content": [{"type": "text", "text": "просто слова"}]}]
        self._run(t, lever="1", messages=tape)
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(t.calls[0]["model"], PRIMARY)

    def test_a_natively_sighted_voice_is_left_alone(self):
        cfg = llm._config()
        cfg["roles"]["voice"]["model"] = SIGHTED
        llm.save_config(cfg)
        t = _Transport()
        self._run(t, lever="1")
        self.assertEqual(len(t.calls), 1, "её модель и так видит — смотреть отдельно незачем")
        self.assertTrue(_has_pixels(t.calls[0]["messages"]))

    def test_the_narrow_look_does_not_look_again_inside_itself(self):
        """Защита от рекурсии: узкий вызов идёт через тот же `chat`."""
        t = _Transport()
        self._run(t, lever="1")
        self.assertEqual(len(t.to_sighted), 1, "второго взгляда внутри взгляда не бывает")


if __name__ == "__main__":       # pragma: no cover
    unittest.main(verbosity=2)
