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
SIGHTED = "glm-4.6v"

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
        # 05.10: кэш описаний изолирован и в базовом классе — иначе прогон всего файла
        # нёс бы описание одной картинки из чужого теста в чужой ход.
        import tempfile, shutil as _sh
        from pathlib import Path as _P
        _d = tempfile.mkdtemp(prefix="praxis-vp-base-")
        self.addCleanup(_sh.rmtree, _d, True)
        _cp = mock.patch.object(llm, "VISION_PREPASS_CACHE",
                                _P(_d) / "vision_prepass_cache.json")
        _cp.start()
        self.addCleanup(_cp.stop)
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


class VisionPrepassCacheAndEffort(Base):
    """05.10: кэш описаний по контенту, явный effort low, гейт кольца на обрыв описания."""

    def setUp(self):
        super().setUp()
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-test"
        cfg["roles"]["voice"].update(framework="anthropic", model=PRIMARY,
                                     fallback_model="", fallback_framework="")
        cfg["roles"]["voice"]["reasoning_effort"] = "high"   # как в проде
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
        # изолированный кэш на тест
        self.cache_path = mock.patch.object(
            llm, "VISION_PREPASS_CACHE",
            self.tmp_path() / "vision_prepass_cache.json")
        self.cache_path.start()
        self.addCleanup(self.cache_path.stop)

    def tmp_path(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="praxis-vp-test-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        from pathlib import Path
        return Path(d)

    def _run(self, transport, messages=None):
        with mock.patch.dict(os.environ, {LEVER: "1"}), \
                mock.patch.object(llm, "_call", side_effect=transport):
            return llm.chat("voice", messages=messages or _room_tape())

    def test_the_narrow_look_carries_explicit_low_effort(self):
        """glm-«high» роли voice не наследуется вспомогательным вызовом: за вечер 04.10
        мышление съедало потолок 1200 изнутри и 26 описаний резались до нуля/полуслова."""
        t = _Transport()
        self._run(t)
        narrow = t.to_sighted
        self.assertEqual(len(narrow), 1)
        self.assertEqual(narrow[0].get("reasoning_effort"), "low",
                         "узкий взгляд обязан идти с явным low, не high роли")

    def test_the_same_picture_is_described_once_across_calls(self):
        """Одна и та же картинка в ленте пересматривалась КАЖДОЙ итерацией хода
        (100 описаний одних стикеров за вечер). Теперь — кэш по контенту."""
        t = _Transport()
        self._run(t)
        self._run(t)   # второй ход, та же лента
        self.assertEqual(len(t.to_sighted), 1,
                         "вторая поездка той же картинки обязана брать кэш")

    def test_a_truncated_description_is_not_cached_and_falls_back(self):
        """Обрезанное о потолок описание не кэшируется и не подставляется:
        полукадр «на картинке слева виден фрагм» хуже честной поездки прежним путём."""
        t = _Transport(sighted_answer="на картинке кот в каске и длинный-длинный текст",
                       fail=False)
        # transport отвечает end_turn; подменим на обрыв
        def cut_answer(framework, model, **kw):
            resp = t.__call__(framework, model, **kw)
            if model == SIGHTED and len(kw.get("messages") or []) == 1:
                resp = llm.LLMResponse(text=resp.text[:40], model=model,
                                       framework=framework, stop_reason="max_tokens")
            return resp
        with mock.patch.dict(os.environ, {LEVER: "1"}), \
                mock.patch.object(llm, "_call", side_effect=cut_answer):
            resp = llm.chat("voice", messages=_room_tape())
        # описание не встало в ленту: ход поехал прежним путём — модели видно
        self.assertTrue(any(c["model"] == SIGHTED and len(c["messages"]) > 1
                            for c in t.calls),
                        "обрезанное описание обязано отступить к прежнему пути")

    def test_a_truncated_description_does_not_become_her_cut_phrase(self):
        """Обрыв описания не пишется в кольцо ходов как «её фраза оборвана»."""
        recorded = []
        import turns as turns_mod
        with mock.patch.object(turns_mod, "note_truncated",
                               side_effect=lambda **kw: recorded.append(kw)):
            def cut(framework, model, **kw):
                if model == SIGHTED and len(kw.get("messages") or []) == 1:
                    return llm.LLMResponse(text="фраг", model=model, framework=framework,
                                           stop_reason="max_tokens")
                return llm.LLMResponse(text="её ответ", model=model, framework=framework)
            with mock.patch.dict(os.environ, {LEVER: "1"}), \
                    mock.patch.object(llm, "_call", side_effect=cut):
                llm.chat("voice", messages=_room_tape())
        self.assertFalse(recorded, "обрыв вспомогательного взгляда — не её фраза")

    def test_path_form_pictures_do_not_share_a_description(self):
        """Гном-ревью ffb58ba4 (блокер): ключ кэша читал только block["source"], а
        живой прод-путь подаёт картинки в path-форме (фото из чата, computer-observe).
        Все path-блоки коллапсировали в ОДИН ключ — вторая картинка получала чужое
        описание, ни разу не увидев зрячую модель. Хуже, чем до рычага.

        Здесь — прод-форма ленты и ДВА разных файла."""
        d = self.tmp_path()

        def pic(content: bytes, name: str) -> dict:
            p = d / name
            p.write_bytes(content)
            return {"type": "image", "path": str(p), "mime": "image/png",
                    "detail": "auto"}

        def tape(block: dict):
            return [{"role": "user", "content": [
                {"type": "text", "text": "Егор: глянь"}, block]}]

        # один файл — одно описание за TTL (кэш и в path-форме работает)
        same = _Transport()
        self._run(same, messages=tape(pic(b"\x89PNG\r\n\x1a\nAAAA", "a.png")))
        self._run(same, messages=tape(pic(b"\x89PNG\r\n\x1a\nAAAA", "a.png")))
        self.assertEqual(len(same.to_sighted), 1,
                         "та же картинка в path-форме обязана брать кэш")

        # ДРУГОЙ файл — СВОЁ узкое обращение, а не чужое описание из кэша
        other = _Transport()
        self._run(other, messages=tape(pic(b"\x89PNG\r\n\x1a\nBBBB", "b.png")))
        self.assertEqual(len(other.to_sighted), 1,
                         "другая картинка обязана сама попасть к зрячей модели")



def _narrow(t):
    """Узкие вызовы: узнаются по их system (узкая просьба), не по длине ленты."""
    return [c for c in t.calls if c.get("system") == llm._VISION_PREPASS_SYS]


class VisionPrepassGnomeBlockers(Base):
    """05.10 (гном-ревью agent-7028ce55): кэш не врёт — четыре блокера, четыре испытания.

    Б1: слепая нога узкого вызова («NO pixels…») не становится описанием и не кэшируется.
    Б2: описание подписано моделью, которая ФАКТИЧЕСКИ смотрела (resp.model).
    Б3: недоступный контент — общий сентинел; под него никто не пишет и не читает.
    Б4: битая ts в кэше — промах, а не падение хода.
    """

    def setUp(self):
        super().setUp()
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-test"
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
        import tempfile, shutil as _sh
        from pathlib import Path as _P
        _d = tempfile.mkdtemp(prefix="praxis-vp-gnome-")
        self.addCleanup(_sh.rmtree, _d, True)
        _cp = mock.patch.object(llm, "VISION_PREPASS_CACHE",
                                _P(_d) / "vision_prepass_cache_v2.json")
        _cp.start()
        self.addCleanup(_cp.stop)

    def tmp_path(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="praxis-vp-gnome-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        from pathlib import Path
        return Path(d)

    def _run(self, transport, messages=None):
        with mock.patch.dict(os.environ, {LEVER: "1"}), \
                mock.patch.object(llm, "_call", side_effect=transport):
            return llm.chat("voice", messages=messages or _room_tape())

    def test_blind_leg_answer_is_not_a_description_and_not_cached(self):
        """Б1: зрячая нога пустеет → без фолбэка; «NO pixels» не пишется в кэш.
        Ход 2 обязан СНОВА позвать зрячую модель, а не кормить отравой из кэша."""
        # нога 1: узкий вызов падает (зрячая недоступна) — без фолбэка сразу наверх
        t1 = _Transport(fail=True)
        resp = self._run(t1)
        # узкий вызов был (ровно один, с одним сообщением) и не породил фолбэк-ноги
        self.assertEqual(len(_narrow(t1)), 1, "узкий вызов был, ровно один")
        self.assertEqual(len(t1.calls), 2,
                         "без описания лента едет прежним путём: узкий + подменённый ход")
        self.assertNotIn("NO pixels",
                         _flat_text([m for c in t1.calls for m in (c.get("messages") or [])]),
                         "отрава не попала ни в одну ленту")
        self.assertNotIn("NO pixels", resp.text, "отрава не попала в ответ роли")
        # нога 2: та же картинка, каналы живы — зрячая зовётся ЗАНОВО, кэш чист
        t2 = _Transport()
        self._run(t2)
        self.assertEqual(len(_narrow(t2)), 1,
                         "после провала кэш пуст: зрячая модель зовётся снова")
        self.assertIn("на картинке кот в каске",
                      _flat_text(t2.to_primary[0]["messages"]),
                      "описание встало в ленту главного хода (он остался на её модели)")

    def test_description_is_signed_by_the_model_that_actually_looked(self):
        """Б2: transport отвечает от имени ДРУГОЙ модели (ротация) — маркер и ключ
        обязаны называть её, не запрошенную."""
        answers = {"model": None}

        def rotating(framework, model, **kw):
            t.calls.append({"framework": framework, "model": model, **kw})
            if kw.get("system") == llm._VISION_PREPASS_SYS:
                answers["model"] = "glm-4.7v"   # ротация: ответила не запрошенная
                return llm.LLMResponse(text="на картинке морж в берете", model="glm-4.7v",
                                       framework=framework)
            return llm.LLMResponse(text="её ответ", model=model, framework=framework)

        t = _Transport()
        t.__class__ = type("Rot", (_Transport,), {"__call__": staticmethod(rotating)})
        self._run(t)
        # маркер едет в ленту главного вызова, не в ответ роли
        self.assertTrue(t.to_primary, "главный ход состоялся")
        self.assertIn("glm-4.7v", _flat_text(t.to_primary[0]["messages"]),
                      "маркер называет фактически ответившую модель")

    def test_unavailable_content_is_one_shared_sentinel(self):
        """Б3: контент недоступен (файл исчез) — все такие картинки получают ОДИН
        сентинел-ключ; под него ничего не пишется, зрячая не зовётся зря."""
        d = self.tmp_path()
        gone = d / "gone.png"

        def pic(exists: bool) -> dict:
            p = d / ("here.png" if exists else "gone.png")
            if exists:
                p.write_bytes(b"\x89PNG\r\n\x1a\nQQQ")
            return {"type": "image", "path": str(p), "mime": "image/png",
                    "detail": "auto"}

        t = _Transport()
        # обе картинки: файл второй отсутствует
        tape = [{"role": "user", "content": [
            {"type": "text", "text": "Егор: глянь"}, pic(True)]}]
        tape_gone = [{"role": "user", "content": [
            {"type": "text", "text": "Егор: глянь"}, pic(False)]}]
        self._run(t, messages=tape)        # живая картинка — описана, кэш написан
        self._run(t, messages=tape_gone)   # мёртвая — сентинел, не описание
        import json as _json
        cache = _json.loads(llm.VISION_PREPASS_CACHE.read_text(encoding="utf-8"))
        self.assertEqual(len(cache), 1, "под сентинел ничего не пишется")
        self.assertEqual(len(_narrow(t)), 1,
                         "мёртвый контент не зовёт зрячую УЗКИМ вызовом (прежний путь — можно)")

    def test_broken_ts_entry_is_a_miss_not_a_crash(self):
        """Б4: запись с "ts": "2026-10-05" (валидный JSON, битая ts) — промах.
        Ход обязан дожить до зрячей модели и получить свежее описание."""
        import json as _json
        d = self.tmp_path()
        key = llm._vision_block_key(_PIXEL, SIGHTED)
        llm.VISION_PREPASS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        llm.VISION_PREPASS_CACHE.write_text(
            _json.dumps({key: {"text": "отравленное старое описание", "ts": "2026-10-05"}}),
            encoding="utf-8")
        t = _Transport()
        self._run(t)
        self.assertEqual(len(_narrow(t)), 1,
                         "битая запись = промах: зрячая модель зовётся заново")
        self.assertIn("на картинке кот в каске", _flat_text(t.to_primary[0]["messages"]),
                      "свежее описание встало в ленту главного вызова")
        fresh = _json.loads(llm.VISION_PREPASS_CACHE.read_text(encoding="utf-8"))
        self.assertEqual(fresh[key]["text"], "на картинке кот в каске",
                         "свежее описание затирает битую запись")

    def test_honest_description_quoting_the_sentinel_is_not_rejected(self):
        """Б5 (гном-3, блокер-1): честное описание скриншота, ЦИТИРУЮЩЕЕ сентинел
        в середине (промпт сам требует «дословно перепиши весь текст»), обязано
        пройти фильтр. Краснеет, если фильтр вернули к подстрочному поиску:
        тогда ход уезжает целым кадром в зрячую модель — её УЗКИЙ вызов
        отбракован, и в ленте главного вызова нет описания с цитатой."""
        quoted = ("на скриншоте лога: 'Error: "
                  + llm._NO_PIXELS_RESPONSE.rstrip(".") + "; check the cable' "
                  "и рядом кот в каске")
        t = _Transport(sighted_answer=quoted)
        self._run(t)
        self.assertEqual(len(_narrow(t)), 1, "узкий вызов состоялся")
        # описание ПРИНЯТО: в ленте главного вызова (на её модели) есть текст описания,
        # а не полный кадр с пикселями в зрячей модели
        self.assertTrue(t.to_primary, "главный ход на её модели состоялся")
        self.assertIn("check the cable", _flat_text(t.to_primary[0]["messages"]),
                      "честное описание с цитатой сентинела обязано попасть в ленту")
        import json as _json
        cache = _json.loads(llm.VISION_PREPASS_CACHE.read_text(encoding="utf-8"))
        self.assertTrue(any("check the cable" in str(v.get("text") or "")
                            for v in cache.values()),
                        "честное описание с цитатой кэшируется")

    def test_narrow_call_never_falls_back_to_a_foreign_leg(self):
        """Б6 (гном-3, мутация М2): узкий вызов, упавший фолбэк-классной ошибкой
        (RateLimitError), НЕ уходит на фолбэк-ногу — падение поднимается наверх,
        _look_once отступает, картинка едет прежним путём. Краснеет, если re-raise
        узкого вызова в except chat() выкачан: тогда слепая/чужая фолбэк-нога
        «опишет» картинку и лжет в маркере."""
        cfg = llm._config()
        cfg["roles"]["voice"].update(fallback_model=SIGHTED,
                                     fallback_framework="anthropic")
        llm.save_config(cfg)

        class RateLimitError(Exception):
            pass
        llm._FALLBACK_ERRORS = set(llm._FALLBACK_ERRORS) | {"RateLimitError"}

        def leg(framework, model, **kw):
            t.calls.append({"framework": framework, "model": model, **kw})
            narrow = (model == SIGHTED and len(kw.get("messages") or []) == 1)
            if narrow:
                raise RateLimitError("зрячая нога удержана лимитом")
            return llm.LLMResponse(text="чужой ответ фолбэк-ноги", model=model,
                                   framework=framework)

        t = _Transport()
        t.__class__ = type("Leg", (_Transport,), {"__call__": staticmethod(leg)})
        self._run(t)
        # узких вызовов РОВНО ОДИН: re-raise поднял падение до фолбэк-логики, и
        # фолбэк-нога узкому НЕ отвечала. Без re-raise chat() уводил бы узкий вызов
        # на fallback_model=SIGHTED (второй вызов с одним сообщением).
        narrow_calls = [c for c in t.calls
                        if c["model"] == SIGHTED and len(c.get("messages") or []) == 1]
        self.assertEqual(len(narrow_calls), 1,
                         "узкий вызов один; фолбэк узкому не отвечал")
        # и ни в одной ленте нет «чужого описания» от фолбэк-ноги
        self.assertNotIn("чужой ответ фолбэк-ноги",
                         _flat_text([m for c in t.calls for m in (c.get("messages") or [])]),
                         "фолбэк-нога не описывала картинку")


if __name__ == "__main__":       # pragma: no cover
    unittest.main(verbosity=2)
