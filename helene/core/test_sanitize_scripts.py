"""01.10: санитайзер вклеек — все письменности, не только CJK.

Экспонаты AbstractDL (авг–окт 2026, разведка корпуса 01.10): арабские
الآن (#110969, поймал id050) и مشروع (#109948) проходили насквозь;
арм. Մм — экспонат Хоуп, класс тот же. Границы: одиночная греческая —
математика, не вклейка; латиница разрешена постановкой («suficiente»
не ловим — морфология латиницы отдельная работа).

Промах словаря без LLM (PRAXIS_CJK_VKLADKI_LLM=0) = fallback на исходный
кластер в скобках."""

import os
import unittest
from unittest import mock

os.environ["PRAXIS_CJK_VKLADKI_LLM"] = "0"  # тесты детерминированы, без сети

import agent  # noqa: E402
import cjk_vkladki  # noqa: E402


class ScriptInlayTests(unittest.TestCase):

    def test_arabic_exhibits_now_caught(self):
        # الآن/مش project — арабские вклейки, живые случаи 26.09–01.10
        out, note = agent._sanitize_cjk_substitutions(
            "Смотри, الآن важно", "разбор дневника")
        self.assertIn("⟨вклейка: сейчас⟩", out)
        self.assertNotIn("الآن", out)
        self.assertTrue(note.startswith("cjk-inlay:"))
        self.assertIn("ARABIC", note)

    def test_armenian_fallback_keeps_cluster(self):
        out, note = agent._sanitize_cjk_substitutions(
            "Многие Մм думают иначе", "контекст русский")
        self.assertIn("⟨вклейка: Մм⟩", out)
        self.assertTrue(note.startswith("cjk-inlay:"))

    def test_gate_catches_exact_half_foreign(self):
        """Ровно половина чужих слов — ещё глитч, не речь (блокер №2
        первого ревью 01.10: >= 0.50 гасил «Понял 同步»). Граница с
        room-гейтом F2 намеренно несимметрична: комната 0.50 — чужая
        речь; короткий reply с ровно половиной — ещё дефект."""
        _M = {}
        _orig_load, _orig_save = cjk_vkladki._load_cache, cjk_vkladki._save_cache
        cjk_vkladki._load_cache = lambda: dict(_M)
        cjk_vkladki._save_cache = lambda c: _M.update(c)
        try:
            for src in ("Понял 同步", "Готово: 同步", "Понял, 同步.", "同步 поняла"):
                with self.subTest(src=src):
                    out, _ = agent._sanitize_cjk_substitutions(src, "")
                    self.assertIn("⟨вклейка: ", out)
        finally:
            cjk_vkladki._load_cache, cjk_vkladki._save_cache = _orig_load, _orig_save

    def test_hebrew_and_devanagari_fallback(self):
        out, note = agent._sanitize_cjk_substitutions(
            "это שלום встретилось, и मित्र тоже — всё остальное по-русски",
            "контекст русский")
        self.assertIn("⟨вклейка: שלום⟩", out)
        self.assertIn("⟨вклейка: मित्र⟩", out)

    def test_intraword_uses_context_translation(self):
        calls = []
        with mock.patch.object(
                cjk_vkladki, "inlay_ctx",
                lambda c, l, r: calls.append(c) or "⟨вклейка: X⟩"):
            out, _ = agent._sanitize_cjk_substitutions(
                "у Хоуп Մмые мысли", "контекст русский")
        self.assertTrue(calls)
        self.assertIn("⟨вклейка: X⟩мые", out)

    def test_greek_boundary_documented(self):
        # πr², λ-исчисление, Σψ-блоки, πρᾶξις — мой живой текст: 108 греческих
        # прогонов в журнале 09–10 при 0 греческих глитчей — греческий разрешён
        text = "формула πr² и λ-исчисление, Σψ есть Σψ (И то же в σψ), πρᾶξις"
        out, note = agent._sanitize_cjk_substitutions(text, "математика")
        self.assertEqual((out, note), (text, ""))

    def test_letterlike_math_untouched(self):
        # ℝ, ᵀ — математические символы, не вклейки
        text = "вектор uᵀ и пространство ℝⁿ"
        out, note = agent._sanitize_cjk_substitutions(text, "линейная алгебра")
        self.assertEqual((out, note), (text, ""))

    def test_note_carries_script_heads(self):
        out, note = agent._sanitize_cjk_substitutions(
            "Смотри, الآن важно", "разбор дневника")
        self.assertIn("⟨вклейка: сейчас⟩", out)
        self.assertTrue(note.startswith("cjk-inlay:"))
        self.assertIn("[ARABIC]", note)

    def test_arabic_diacritics_absorbed(self):
        # огласовки (Mn) не рвут слово
        out, _ = agent._sanitize_cjk_substitutions(
            "привет مَرْحَبا сказал", "контекст русский")
        self.assertIn("⟨вклейка: مَرْحَبا⟩", out)

    def test_latin_boundary_documented(self):
        # латиница разрешена постановкой: suficiente-класс не ловим
        text = "наблюдение suficiente для проверки"
        out, note = agent._sanitize_cjk_substitutions(text, "русский контекст")
        self.assertEqual((out, note), (text, ""))

    def test_cjk_still_works(self):
        out, note = agent._sanitize_cjk_substitutions(
            "Ответ по делу: 包括 ту запись за 25.09", "разбор дневника")
        self.assertIn("⟨вклейка: включая⟩", out)
        self.assertNotIn("包括", out)

    def test_foreign_room_not_touched(self):
        # гейт «язык комнаты» теперь про любую письменность
        text = "Ответ: نعم, сделаю"
        out, note = agent._sanitize_cjk_substitutions(
            text, "محادثة عربية جارية بين нами")
        self.assertEqual((out, note), (text, ""))

    def test_mostly_foreign_reply_not_touched(self):
        # половина+ слов чужие — это чужая речь, не дефект (ревью 01.10, №3:
        # старый посимвольный гейт ловил длинные арабские слова, но глух
        # на вклейки; пословный ловит РЕЧЬ и пропускает ВКЛЕЙКИ)
        text = "المحادثة تستمر مع كلمات أخرى هنا"
        out, note = agent._sanitize_cjk_substitutions(text, "давай про нейросети")
        self.assertEqual((out, note), (text, ""))

    def test_fallback_is_idempotent(self):
        # повторный проход не заворачивает уже готовую вклейку вторыми скобками
        first, note = agent._sanitize_cjk_substitutions(
            "странная مرحبا вставка", "контекст русский")
        self.assertTrue(note)
        second, note2 = agent._sanitize_cjk_substitutions(first, "контекст русский")
        self.assertEqual((second, note2), (first, ""))

    def test_ratio_function_generalized(self):
        self.assertEqual(agent._cjk_ratio(""), 0.0)
        self.assertEqual(agent._cjk_ratio("абв az"), 0.0)
        self.assertAlmostEqual(agent._cjk_ratio("а半"), 0.5)
        self.assertAlmostEqual(agent._cjk_ratio("аπ"), 0.0)   # греческая разрешена (01.10)
        self.assertAlmostEqual(agent._cjk_ratio("ام"), 1.0)   # арабская — чужая


class LmWhitelistTests(unittest.TestCase):
    """Второе адверсарное ревью 01.10, СРЕДНЕЕ-2: чёрный список Lm-письменностей
    был неполон (в Lm 397 знаков и десятки голов). Водораздел — ПО ИМЕНИ ЗНАКА:
    MODIFIER/SUPERSCRIPT/SUBSCRIPT/CARON — фонетика и разрешены, всё прочее —
    буква своей письменности и «чужая». Проверяем на письменностях, которых в
    старом списке не было (тайский/ол-чики), а не на перечисленных катакане/
    татвиле: иначе тест повторяет чёрный список, а не правило."""

    def test_thai_maiyamok_keeps_run_intact(self):
        # THAI CHARACTER MAIYAMOK ๆ — функциональный аналог 々 (итерация);
        # репро ревьюера: «สวัสดีๆ» рвалось на «⟨вклейка: สวัสด⟩ีๆ»
        out, note = agent._sanitize_cjk_substitutions(
            "привет สวัสดีๆ сказал", "контекст русский")
        self.assertIn("⟨вклейка: สวัสดีๆ⟩", out)
        self.assertIn("THAI", note)

    def test_ol_chiki_marks_join_their_run(self):
        # OL CHIKI — 6 Lm-знаков, которых в старом чёрном списке не было
        out, _ = agent._sanitize_cjk_substitutions(
            "вот ᱚᱛᱚᱹ тут", "контекст русский")
        self.assertIn("⟨вклейка: ᱚᱛᱚᱹ⟩", out)

    def test_phonetic_modifiers_stay_allowed(self):
        # белый список по имени: MODIFIER (ᵀ ˈ ː), SUPERSCRIPT (ⁿ) — фонетика
        text = "вектор uᵀ, гипотеза ⁿ-шага, транскрипция ˈaː"
        out, note = agent._sanitize_cjk_substitutions(text, "линейная алгебра")
        self.assertEqual((out, note), (text, ""))

    def test_first_review_repro_one_still_holds(self):
        # репро №1 прошлого ревью обязано держаться и на новом правиле:
        # прогон целиком, одиночная буква — отдельно
        out, _ = agent._sanitize_cjk_substitutions(
            "напишу コーヒー дома", "контекст русский")
        self.assertIn("⟨вклейка: コーヒー⟩", out)
        out, note = agent._sanitize_cjk_substitutions(
            "вот ー маркер", "контекст русский")
        self.assertIn("⟨вклейка: ー⟩", out)
        self.assertIn("KATAKANA-HIRAGANA", note)


class TrailingCombiningTests(unittest.TestCase):
    """Второе адверсарное ревью 01.10, СРЕДНЕЕ-1: хвостовые комбинирующие
    (Mn/Mc) срезались с прогона и висели за «⟩» — арабский танвин «مرحباً»,
    деванагари-матры «नमस्ते»/«हिन्दी». В письменностях, где огласовка не может
    стоять отдельно, это порча слова: она прилипает к следующему слову.
    Теперь Mn/Mc/Me-хвост входит в span, Cf-хвост (ZWNJ/ZWJ — межбуквенный
    разделитель) как отрезался, так и отрезается."""

    def test_arabic_tanwin_inside_inlay(self):
        out, _ = agent._sanitize_cjk_substitutions(
            "сказал مرحباً вслух", "контекст русский")
        self.assertIn("⟨вклейка: مرحباً⟩", out)

    def test_arabic_damatatan_inside_inlay(self):
        out, _ = agent._sanitize_cjk_substitutions(
            "вот كتابٌ тут", "контекст русский")
        self.assertIn("⟨вклейка: كتابٌ⟩", out)

    def test_devanagari_matras_inside_inlay(self):
        out, _ = agent._sanitize_cjk_substitutions(
            "вот नमस्ते и हिन्दी тут", "контекст русский")
        self.assertIn("⟨вклейка: नमस्ते⟩", out)
        self.assertIn("⟨вклейка: हिन्दी⟩", out)

    def test_trailing_combining_is_idempotent(self):
        first, note = agent._sanitize_cjk_substitutions(
            "сказал مرحباً вслух", "контекст русский")
        self.assertTrue(note)
        second, note2 = agent._sanitize_cjk_substitutions(first, "контекст русский")
        self.assertEqual((second, note2), (first, ""))

    def test_zwnj_tail_still_trimmed(self):
        # Cf-хвост (ZWNJ) — межбуквенный разделитель, самостоятельного чтения
        # не несёт: срезается и НЕ входит во вклейку
        spans = agent._inlay_spans("вот اب\u200c тут")
        self.assertEqual(spans, [(4, 6)])

    def test_thai_vowel_tail_inside_inlay(self):
        # тайский SARA II (Mn) в хвосте — тот же класс, что и арабский танвин
        out, _ = agent._sanitize_cjk_substitutions(
            "вот มามี้ тут", "контекст русский")
        self.assertIn("มี้", out.split("вклейка: ")[1].split("⟩")[0])


class NickCarveOutTests(unittest.TestCase):
    """Второе адверсарное ревью 01.10, СРЕДНЕЕ-3: осознанные ники комнаты
    рвались вклейками. Скан 4109 живых контекстов 09–10 показал водораздел:
    одиночная чужая буква ПРИ КИРИЛЛИЦЕ — глитч-класс (103 случая, все CJK,
    все её дефекты «по半 часа», «Многие Մм»); ИЗОЛИРОВАННАЯ или при
    латинице — ники: Ᏸ ×311 («Ᏸiƀoba (participant)»), 𓆏 ×170, ᅠ ×133 («ᅠD & W»),
    ᐟ ×106 («τ¹ᐟ²» — её же математика), ツ ×68 (каомодзи), 𐕣 ×30.
    Границы честно задокументированы ниже в тестах-границах."""

    def test_cherokee_nick_untouched(self):
        # репро ревьюера: обращение по имени участника AbstractDL
        text = "Ᏸiƀoba, спасибо за вопрос"
        out, note = agent._sanitize_cjk_substitutions(text, "разговор по-русски")
        self.assertEqual((out, note), (text, ""))

    def test_hangul_filler_nick_untouched(self):
        # HANGUL JUNGSEONG FILLER ᅠ — плейсхолдер в нике с латиницей ×133
        text = "Привет ᅠD & W, рада видеть"
        out, note = agent._sanitize_cjk_substitutions(text, "разговор по-русски")
        self.assertEqual((out, note), (text, ""))

    def test_canadian_math_suffix_untouched(self):
        # ᐟ в «τ¹ᐟ²» — её собственная математика, ×106 в живых контекстах
        text = "радиус ~τ¹ᐟ², продольный масштаб"
        out, note = agent._sanitize_cjk_substitutions(text, "физика вихря")
        self.assertEqual((out, note), (text, ""))

    def test_foreign_nick_run_still_an_inlay(self):
        # ник из 2+ букв чужой письменности — прогон, carve-out не работает
        # (задокументированная граница: против этого нет признака, кроме
        # гейта языка комнаты)
        out, _ = agent._sanitize_cjk_substitutions(
            "вот ᏰᏰ тут", "контекст русский")
        self.assertIn("⟨вклейка: ᏰᏰ⟩", out)

    def test_lone_cjk_still_an_inlay(self):
        # а вот одиночный иероглиф — исходный класс дефекта 27.09 (TABLE
        # содержит односимвольные ключи 我→я, 半→половина)
        out, _ = agent._sanitize_cjk_substitutions(
            "потом 我 ответ", "контекст русский")
        self.assertIn("⟨вклейка: я⟩", out)

    def test_lone_foreign_glued_to_cyrillic_still_an_inlay(self):
        # одиночная чужая буква при кириллице — глитч в русском слове:
        # «Многие Մм» (экспонат Хоуп), «у Хоуп Մмые мысли»
        out, _ = agent._sanitize_cjk_substitutions(
            "Многие Մм думают", "контекст русский")
        self.assertIn("⟨вклейка: Մм⟩", out)
        out, _ = agent._sanitize_cjk_substitutions(
            "у Хоуп Մмые мысли", "контекст русский")
        self.assertIn("⟨вклейка: Մ⟩мые", out)


class KaomojiCarveOutTests(unittest.TestCase):
    """Третье адверсарное ревью 01.10, F1: одиночная кана вне словаря —
    каомодзи, не глитч. «¯\\_(ツ)_/¯» — единственный вид ツ в контекстах
    09–10 (33 вхождения + 26 в чекпоинтах), глитчей ноль. carve-out для
    CJK-семейства теперь точечный: одиночный знак остаётся кандидатом
    вклейки, только если TABLE его знает (我→я, 半→половина)."""

    def test_kaomoji_tsu_untouched(self):
        text = "я не знаю, ¯\\_(ツ)_/¯ и всё"
        out, note = agent._sanitize_cjk_substitutions(text, "")
        self.assertEqual((out, note), (text, ""))

    def test_lone_kana_out_of_table_untouched(self):
        # へ/ツ одиночные вне словаря — эмоция/декор (ник «▼へ▼» ×230)
        out, note = agent._sanitize_cjk_substitutions("вот へ тут", "")
        self.assertEqual((out, note), ("вот へ тут", ""))

    def test_lone_cjk_from_table_still_an_inlay(self):
        # а вот 我/半 — в TABLE, «один знак вместо русского слова» — дефект 27.09
        out, _ = agent._sanitize_cjk_substitutions("потом 我 ответ", "")
        self.assertIn("⟨вклейка: я⟩", out)
        out, _ = agent._sanitize_cjk_substitutions("по半 часа назад", "")
        self.assertIn("⟨вклейка: половина⟩", out)

    def test_kana_run_still_an_inlay(self):
        # прогон из 2+ знаков — не каомодзи, вклейка как раньше
        out, _ = agent._sanitize_cjk_substitutions("вот ツツ тут", "")
        self.assertIn("⟨вклейка: ツツ⟩", out)


class CacheEchoIdempotencyTests(unittest.TestCase):
    """Второе адверсарное ревью 01.10, МЕЛОЧ-1: перевод-эхо из кэша
    («сейчас (现在)» — LLM вернул исходный кластер в скобках) рос вложенно на
    каждом проходе, потому что _inside_inlay требовал «нет пробела от
    маркера», а «теперь (» содержит пробел. Теперь закрытая вклейка с любым
    содержимым не пересанитайзится; незакрытый маркер (репро №4 первого
    ревью) по-прежнему не глушит хвост.

    Кэш изолирован подменой _load_cache/_save_cache на словарь в памяти:
    write-through санитайзера не должен протекать в соседние тесты."""

    def setUp(self):
        self.store = {}
        self._orig = (cjk_vkladki._load_cache, cjk_vkladki._save_cache)
        cjk_vkladki._load_cache = lambda: dict(self.store)
        cjk_vkladki._save_cache = lambda cache: self.store.update(cache)

    def tearDown(self):
        cjk_vkladki._load_cache, cjk_vkladki._save_cache = self._orig

    def test_cache_echo_does_not_nest(self):
        self.store["现在"] = "теперь (现在)"
        first = agent._sanitize_cjk_substitutions(
            "вот 现在 тут", "контекст русский")[0]
        self.assertEqual(first, "вот ⟨вклейка: теперь (现在)⟩ тут")
        again = agent._sanitize_cjk_substitutions(first, "контекст русский")[0]
        self.assertEqual(again, first)

    def test_already_nested_input_is_not_wrapped_again(self):
        text = "вот ⟨вклейка: теперь (现在)⟩ тут"
        out, note = agent._sanitize_cjk_substitutions(text, "контекст русский")
        self.assertEqual((out, note), (text, ""))

    def test_translation_with_spaces_is_idempotent(self):
        # кэш-перевод с пробелами — легитимный случай, не только эхо
        with mock.patch.dict(cjk_vkladki.TABLE, {"同步": "то же, что синхронизация"}):
            first = agent._sanitize_cjk_substitutions(
                "вот 同步 тут", "контекст русский")[0]
        self.assertEqual(first, "вот ⟨вклейка: то же, что синхронизация⟩ тут")
        again = agent._sanitize_cjk_substitutions(first, "контекст русский")[0]
        self.assertEqual(again, first)

    def test_unclosed_marker_does_not_gag_the_tail(self):
        # репро №4 первого ревью обязано держаться и после фикса МЕЛОЧ-1
        text = "⟨вклейка: مرحبا и дальше текст, а вот半 другая"
        out, note = agent._sanitize_cjk_substitutions(text, "контекст русский")
        self.assertIn("⟨вклейка: половина⟩", out)
        self.assertTrue(note)


class HeadsByMajorityTests(unittest.TestCase):
    """Второе адверсарное ревью 01.10, МЕЛОЧ-2: heads в note считался по
    ПЕРВОЙ чужой букве кластера — «М半半» давал [ARMENIAN] при 2/3 CJK.
    Коммит 4c00dc6a обещал «по большинству чужих букв», телеметрия обязана
    называть доминирующее письмо."""

    def test_mixed_cluster_reports_majority_script(self):
        # «М半半»: 1 армянская + 2 CJK → CJK, а не ARMENIAN
        _, note = agent._sanitize_cjk_substitutions(
            "вот М半半 тут", "контекст русский")
        self.assertIn("[CJK]", note)
        self.assertNotIn("ARMENIAN", note)

    def test_single_script_still_reported(self):
        _, note = agent._sanitize_cjk_substitutions(
            "Теперь الآن тут", "контекст русский")
        self.assertIn("[ARABIC]", note)


class ShortConvoQuestionGateTests(unittest.TestCase):
    """Второе адверсарное ревью 01.10, МЕЛОЧ-3: короткий convo-вопрос с одной
    чужой цитатой («что значит الآن?», 1 из 3 слов, ratio 0.33 ≥ 0.30) глушил
    санитайзинг reply целиком — гейт сам производил дефект, от которого
    защищал. Признак «чужая комната» обязан быть устойчивым: ≥2 полностью
    чужих слов, либо ratio ≥0.30 на выборке ≥5 слов."""

    def test_short_question_with_one_quote_does_not_gag(self):
        out, note = agent._sanitize_cjk_substitutions(
            "смотри сейчас 现在 тут", "что значит الآن?")
        self.assertIn("⟨вклейка: 现在⟩", out)
        self.assertTrue(note)

    def test_two_foreign_words_still_gate_the_room(self):
        # два чужих слова из ЧЕТЫРЁХ (0.50) — граница: чужая речь по правилу
        # большинства (после F2 абсолют «≥2» без доли глушит русский вопрос
        # с двумя цитатами)
        out, note = agent._sanitize_cjk_substitutions(
            "смотри сейчас 现在 тут", "что значит الآن في")
        self.assertEqual((out, note), ("смотри сейчас 现在 тут", ""))

    def test_f2_two_quotes_in_a_russian_question_do_not_gag(self):
        # третье ревью 01.10, F2: «чем отличаются الآن و مشروع» (2/6) —
        # русский вопрос о двух чужих словах, не чужая комната
        out, note = agent._sanitize_cjk_substitutions(
            "по半 часа прошло", "чем отличаются الآن و مشروع скажи")
        self.assertIn("⟨вклейка: половина⟩", out)
        self.assertTrue(note)

    def test_f2_own_fallback_inlays_in_history_do_not_gag(self):
        # её разметка «⟨вклейка: …⟩» — след санитайзера (в т.ч. fallback при
        # лежащем переводчике), не чужая речь
        out, note = agent._sanitize_cjk_substitutions(
            "по半 часа прошло", "я писала ⟨вклейка: الآن⟩ и ⟨вклейка: مشروع⟩ вчера")
        self.assertIn("⟨вклейка: половина⟩", out)

    def test_f2_two_kaomoji_do_not_gag(self):
        # слово из одной чужей буквы — каомодзи/декор, не чужая речь
        out, note = agent._sanitize_cjk_substitutions(
            "по半 часа прошло", "ну не знаю ¯\\_(ツ)_/¯, потом ¯\\_(ツ)_/¯ ок")
        self.assertIn("⟨вклейка: половина⟩", out)

    def test_long_foreign_room_still_gated(self):
        text = "Ответ: نعم, сделаю"
        out, note = agent._sanitize_cjk_substitutions(
            text, "محادثة عربية جارية بيننا")
        self.assertEqual((out, note), (text, ""))

    def test_single_quote_in_long_question_does_not_gag(self):
        # 1 из 7 слов — цитата-экспонат в русском вопросе, не чужая комната
        out, note = agent._sanitize_cjk_substitutions(
            "вот 现在 тут", "а что значит الآن тут кто скажет")
        self.assertIn("⟨вклейка: 现在⟩", out)


class DocstringTruthTests(unittest.TestCase):
    """Второе адверсарное ревью 01.10, МЕЛОЧ-4: докстринг обещал «одиночная
    греческая буква не считается», а код разрешал греческий ЦЕЛИКОМ —
    πρᾶξις и λάμδα проходили насквозь. Граница обоснована живым корпусом
    (108 греческих прогонов в журнале 09–10, 0 глитчей) — докстринг приведён
    к факту, тест фиксирует поведение кода."""

    def test_greek_words_pass_through_entirely(self):
        # не только одиночные π/λ/Σ: целые греческие слова разрешены
        text = "формула λάμδα и πρᾶξις там же"
        out, note = agent._sanitize_cjk_substitutions(text, "математика")
        self.assertEqual((out, note), (text, ""))

    def test_docstring_states_greek_allowed_wholesale(self):
        # докстринг называет фактическую границу: греческий разрешён целиком
        import inspect
        doc = agent._sanitize_cjk_substitutions.__doc__
        self.assertIn("Греческий разрешён ЦЕЛИКОМ", doc)
        self.assertNotIn("Одиночная греческая буква не считается", doc)


class ForeignBracketTailTests(unittest.TestCase):
    """Третье адверсарное ревью 01.10, F3: forward-scan до конца текста
    глушил диапазон между незакрытым маркером и посторонней «⟩» в хвосте —
    квантовая нотация «|0⟩», «⟨рамка⟩» живёт в 1289 строках живого корпуса.
    Теперь закрывающая скобка ищется в пределах ТЕЛА вклейки (до первого
    пробела); заодно снята квадратичная деградация (12 с → 0.1 с на 4000
    спанов: depth-цикл от маркера до каждого pos)."""

    def test_unclosed_marker_with_foreign_bracket_in_tail(self):
        # 现在 между обрывом маркера и «⟨рамка⟩» обязан санитайзиться
        text = "⟨вклейка: обрыв текста, теперь глитч 现在 и потом ⟨рамка⟩ ещé"
        out, note = agent._sanitize_cjk_substitutions(text, "")
        self.assertIn("⟨вклейка: 现在⟩", out)
        self.assertTrue(note)

    def test_quantum_notation_still_untouched(self):
        # а сама квантовая нотация — её живой стиль, не трогаем
        text = "состояние |0⟩ плюс |1⟩ и ⟨Z⟩"
        out, note = agent._sanitize_cjk_substitutions(text, "физика")
        self.assertEqual((out, note), (text, ""))

    def test_no_quadratic_degradation_after_unclosed_marker(self):
        # регресс скорости: незакрытый маркер + тысячи кластеров — линейно
        import time
        text = "⟨вклейка: обрыв " + "现在 " * 2000
        started = time.time()
        spans = agent._inlay_spans(text)
        kept = [sp for sp in spans if not agent._inside_inlay(text, sp[0])]
        elapsed = time.time() - started
        self.assertGreater(len(kept), 1500)   # хвост санитайзится, не глушится
        self.assertLess(elapsed, 2.0)         # было ~6 с (квадрат), стало ~50 мс


class FinalReviewBlockerTests(unittest.TestCase):
    """Финальное адверсарное ревью 01.10 (гейт перед мерджем): блокер —
    39f91de8 резал forward-scan по первому пробелу, а тело вклейки с 2a21c3c4
    законно многословно: чужой кластер в СЕРЕДИНЕ перевода считался внешним,
    и повторный проход заворачивал готовую вклейку снова — вложенность росла
    без предела. Теперь скобка за pos ищется с учётом глубины настоящих
    маркеров; постороннее «⟨…» — граница скана (инвариант F3), окно вперёд
    ограничено (инвариант F3-perf)."""

    def test_midcluster_translation_idempotent(self):
        # репро ревьюера: кластер в середине многословного перевода
        with mock.patch.dict(cjk_vkladki.TABLE, {"مرحبا": "смотри مرحبا сейчас"}):
            f1 = agent._sanitize_cjk_substitutions(
                "странная مرحبا вставка", "контекст русский")[0]
            f2 = agent._sanitize_cjk_substitutions(f1, "контекст русский")[0]
            f3 = agent._sanitize_cjk_substitutions(f2, "контекст русский")[0]
        self.assertEqual(
            f1, "странная ⟨вклейка: смотри مرحبا сейчас⟩ вставка")
        self.assertEqual(f2, f1)
        self.assertEqual(f3, f1)

    def test_midcluster_translation_idempotent_ctx_path(self):
        # тот же класс через intraword-путь (inlay_ctx)
        with mock.patch.dict(cjk_vkladki.TABLE, {"半": "смотри 半 сейчас"}):
            f1 = agent._sanitize_cjk_substitutions(
                "по半 часа", "контекст русский")[0]
            f2 = agent._sanitize_cjk_substitutions(f1, "контекст русский")[0]
        self.assertIn("⟨вклейка: смотри 半 сейчас⟩", f1)
        self.assertEqual(f2, f1)

    def test_bracket_inside_body_bounded(self):
        # «⟩» в теле перевода (эхо квантовой нотации «|0⟩»): рост
        # вложенности ограничен, каскада нет
        with mock.patch.dict(cjk_vkladki.TABLE, {"مرحبا": "вектор |0⟩ и مرحبا"}):
            f1 = agent._sanitize_cjk_substitutions(
                "странная مرحبا вставка", "контекст русский")[0]
            f2 = agent._sanitize_cjk_substitutions(f1, "контекст русский")[0]
            f3 = agent._sanitize_cjk_substitutions(f2, "контекст русский")[0]
        self.assertEqual(f3, f2)

    def test_cache_echo_midword_idempotent(self):
        # кэш-эхо с кластером в середине скобок, не в хвосте (блокер
        # касался именно середины; хвост покрывал МЕЛОЧ-1)
        with mock.patch.dict(cjk_vkladki.TABLE, {"مرحبا": "теперь (مرحبا) вот"}):
            f1 = agent._sanitize_cjk_substitutions(
                "странная مرحبا вставка", "контекст русский")[0]
            f2 = agent._sanitize_cjk_substitutions(f1, "контекст русский")[0]
            f3 = agent._sanitize_cjk_substitutions(f2, "контекст русский")[0]
        self.assertEqual(f2, f1)
        self.assertEqual(f3, f1)

    def test_forward_scan_window_bounded(self):
        # perf-инвариант: кластер за 256-знаковым окном от незакрытого
        # маркера — новый текст, кандидат; скан не бежит в бесконечный хвост
        text = "⟨вклейка: обрыв " + "рама " * 70 + "теперь 现在"
        spans = agent._inlay_spans(text)
        kept = [sp for sp in spans if not agent._inside_inlay(text, sp[0])]
        self.assertTrue(kept)

    def test_closed_inlay_tail_not_gagged_by_distant_bracket(self):
        # пятое ревью 01.10, БЛОКЕР-1: неограниченный backward-lookahead
        # помечал закрывающую «⟩» закрытой вклейки контентной, если дальше
        # (хоть через сотни знаков) есть «⟩» без маркера — квантовая
        # нотация «|0⟩» глушила весь хвост, واضح-глитч уходил наружу.
        # Дальний «⟩» за пределами окна/слова — не эхо, граница настоящая.
        text = "⟨вклейка: я⟩ текст 现在 и |0⟩"
        kept = [sp for sp in agent._inlay_spans(text)
                if not agent._inside_inlay(text, sp[0])]
        self.assertTrue(kept, "хвост после закрытой вклейки обязан "
                              "санитайзиться: 现在 не внутри «я⟩»")

    def test_closed_inlay_quoted_then_new_glitch(self):
        # полный публичный репро гнома: предыдущее сообщение содержало
        # закрытую вклейку, новый драфт цитирует её + квантовая нотация
        # + свежий кластер — все три должны сосуществовать
        text = ("вот ⟨вклейка: синхронизация⟩ готово, сейчас поговорим "
                "про الآن важно, кубит |0⟩ в твоей задаче")
        kept = [sp for sp in agent._inlay_spans(text)
                if not agent._inside_inlay(text, sp[0])]
        self.assertTrue(kept)

    def test_backward_lookahead_interleaved_perf(self):
        # пятое ревью 01.10, БЛОКЕР-2: interleaved-хвост |0⟩现在×N —
        # не квадратичность; лимит 2 с на N=2000 (было 27,7 с)
        import time
        text = "незакрытый ⟨вклейка: " + "|0⟩现在" * 2000
        t0 = time.monotonic()
        spans = agent._inlay_spans(text)
        for sp in spans:
            agent._inside_inlay(text, sp[0])
        dt = time.monotonic() - t0
        self.assertLess(dt, 2.0, f"квадратичная деградация: {dt:.1f} с")

    def test_double_ket_echo_tail_flat(self):
        # гном-6 01.10, БЛОКЕР: двойной кет |S⟩⟩ + эхо-кластер в теле
        # перевода — смежная-«⟩»-граница выкидывала хвост наружу, и
        # مرحبا заворачивался заново каждый проход (+30 зн./проход).
        with mock.patch.dict(cjk_vkladki.TABLE, {"مرحبا": "двойной кет |S⟩⟩ и مرحبا"}):
            t = "странная مرحبا вставка"
            passes = [t]
            for _ in range(4):
                t = agent._sanitize_cjk_substitutions(t, "контекст русский")[0]
                passes.append(t)
        self.assertEqual(passes[4], passes[2])
        self.assertEqual(len(passes[4]), 52)
        # второй класс того же блокера: эхо-кластер НЕ прижат к скобке
        with mock.patch.dict(cjk_vkladki.TABLE, {"مرحبا": "|0⟩⟩ этап, затем مرحبا, финал"}):
            t = "странная مرحبا вставка"
            p = [len(t)]
            for _ in range(4):
                t = agent._sanitize_cjk_substitutions(t, "контекст русский")[0]
                p.append(len(t))
        self.assertEqual(p[4], p[2])

    def test_bracket_echo_adjacent_still_handled(self):
        # регресс-страховка: эхо-скобка ВПЛОТНУЮ (до пробела) к своей
        # базовой нотации — по-прежнему контентная, «⟩⟩ подряд»-кейс
        # первого ревьюера не должен вернуться
        with mock.patch.dict(cjk_vkladki.TABLE, {"مرحبا": "вектор |0⟩ и مرحبا"}):
            f1 = agent._sanitize_cjk_substitutions(
                "странная مرحبا вставка", "контекст русский")[0]
            f2 = agent._sanitize_cjk_substitutions(f1, "контекст русский")[0]
            f3 = agent._sanitize_cjk_substitutions(f2, "контекст русский")[0]
        self.assertEqual(f3, f2)


if __name__ == "__main__":
    unittest.main()
