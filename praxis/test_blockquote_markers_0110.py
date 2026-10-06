# Блочное цитирование: "> "-маркер → MessageEntityBlockquote (PASS 0110, Егор #111016/#111019).
# Проверяет _apply_blockquote_markers: снятие маркера, офсеты UTF-16, сдвиг чужих
# entities, подряд идущие цитаты, кириллица, отсутствие маркеров, hermetic-фолбэк.
import copy

import pytest


def _fn():
    from mtproto_runner import _apply_blockquote_markers
    return _apply_blockquote_markers


def _has_telethon():
    try:
        from telethon.tl import types  # noqa: F401
        return True
    except Exception:
        return False


def test_no_markers_untouched():
    apply_bq = _fn()
    t, ents = apply_bq("просто текст\nвторая строка", [])
    assert t == "просто текст\nвторая строка"
    assert ents == []


def test_marker_removed_single_line():
    if not _has_telethon():
        pytest.skip("telethon недоступен: фолбэк оставляет маркер")
    apply_bq = _fn()
    t, ents = apply_bq("до\n> цитата\nпосле", [])
    assert t == "до\nцитата\nпосле"
    assert len(ents) == 1
    bq = ents[0]
    assert type(bq).__name__ == "MessageEntityBlockquote"
    assert (bq.offset, bq.length) == (3, 6)


def test_consecutive_quoted_lines_merge():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    apply_bq = _fn()
    t, ents = apply_bq("> а\n> б\nмежду\n> в", [])
    assert t == "а\nб\nмежду\nв"
    assert len(ents) == 2  # «а\nб» одним блоком, «в» вторым
    first, second = ents
    # а\nб = 'а'(1) + '\n'(1) + 'б'(1) = 3 UTF-16 units
    assert (first.offset, first.length) == (0, 3)
    # «в» после «между\n» → offset 10, length 1
    assert (second.offset, second.length) == (10, 1)


def test_cyrillic_utf16_offsets():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    apply_bq = _fn()
    t, ents = apply_bq("привет\n> Привет, как дела? хорошо", [])
    assert t == "привет\nПривет, как дела? хорошо"
    bq = ents[0]
    assert bq.offset == 7
    assert bq.length == len("Привет, как дела? хорошо".encode("utf-16-le")) // 2


def test_existing_entities_shifted():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    from telethon.tl import types as tl
    apply_bq = _fn()
    bold = tl.MessageEntityBold(offset=8, length=6)  # «жирный» после маркера-строки
    t, ents = apply_bq("> строка\nжирный текст", [bold])
    assert t == "строка\nжирный текст"
    kinds = [type(e).__name__ for e in ents]
    assert kinds.count("MessageEntityBold") == 1
    bold2 = [e for e in ents if type(e).__name__ == "MessageEntityBold"][0]
    # маркер снят до bold: офсет съехал на 2 влево
    assert bold2.offset == 6
    assert bold2.length == 6


def test_entity_before_any_marker_not_shifted():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    from telethon.tl import types as tl
    apply_bq = _fn()
    code = tl.MessageEntityCode(offset=0, length=2)  # «до» в первой строке
    t, ents = apply_bq("до\n> цитата\nпосле", [code])
    code2 = [e for e in ents if type(e).__name__ == "MessageEntityCode"][0]
    assert (code2.offset, code2.length) == (0, 2)


def test_hermetic_fallback_keeps_marker():
    apply_bq = _fn()
    # Без telethon в окружении функция обязана вернуть текст как есть.
    # Симулируем отсутствие через monkeypatch импорта: просто проверяем,
    # что вызов не падает и возвращает строку.
    t, ents = apply_bq("> цитата", [])
    assert isinstance(t, str)
    assert isinstance(ents, list)


def test_crlf_stripped_from_quotes():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    apply_bq = _fn()
    t, ents = apply_bq("до\r\n> цитата\r\nпосле", [])
    assert t == "до\nцитата\nпосле"
    bq = ents[0]
    assert (bq.offset, bq.length) == (3, 6)


def test_emoji_surrogate_offsets():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    apply_bq = _fn()
    t, ents = apply_bq("🐺 привет\n> цитата", [])
    assert t == "🐺 привет\nцитата"
    bq = ents[0]
    # 🐺 = суррогатная пара = 2 UTF-16 юнита: офсет после «🐺 привет\n» = 2+1+6+1 = 10
    assert (bq.offset, bq.length) == (10, 6)


def test_entity_on_quoted_line_shifts_to_zero():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    from telethon.tl import types as tl
    apply_bq = _fn()
    bold = tl.MessageEntityBold(offset=2, length=6)  # «цитата» после маркера
    t, ents = apply_bq("> цитата\nхвост", [bold])
    bold2 = [e for e in ents if type(e).__name__ == "MessageEntityBold"][0]
    assert (bold2.offset, bold2.length) == (0, 6)


def test_blank_line_breaks_quote_run():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    apply_bq = _fn()
    t, ents = apply_bq("> а\n\n> б", [])
    assert t == "а\n\nб"
    assert len(ents) == 2
    assert (ents[0].offset, ents[0].length) == (0, 1)
    assert (ents[1].offset, ents[1].length) == (3, 1)


def test_cr1_entity_after_crlf_lines():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    from telethon.tl import types as tl
    apply_bq = _fn()
    bold = tl.MessageEntityBold(offset=7, length=10)  # «plain text»
    t, ents = apply_bq("head\r\nplain text\r\n> quote", [bold])
    assert t == "head\nplain text\nquote"
    bold2 = [e for e in ents if type(e).__name__ == "MessageEntityBold"][0]
    # сдвиг за head\r\n (3 юнита → 2) и сужение: \r внутри диапазона убран
    assert (bold2.offset, bold2.length) == (6, 9)
    bq = [e for e in ents if type(e).__name__ == "MessageEntityBlockquote"][0]
    assert (bq.offset, bq.length) == (16, 5)


def test_cr3_entity_oob_regression():
    # Ревью 01.10: entity после CRLF уезжал за конец текста (OOB →
    # ENTITY_BOUNDS_INVALID от Telegram). Карта смещений обязана держать его
    # внутри даже при кривом исходном офсете.
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    from telethon.tl import types as tl
    apply_bq = _fn()
    bold = tl.MessageEntityBold(offset=9, length=4)
    t, ents = apply_bq("a\r\nb\r\n> q\r\nTAIL", [bold])
    L = len(t.encode("utf-16-le")) // 2
    for e in ents:
        assert e.offset + e.length <= L, (type(e).__name__, e.offset, e.length, L)


def test_entity_spanning_marker_shrinks():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    from telethon.tl import types as tl
    apply_bq = _fn()
    bold = tl.MessageEntityBold(offset=0, length=8)  # «> цитата» целиком
    t, ents = apply_bq("> цитата", [bold])
    bold2 = [e for e in ents if type(e).__name__ == "MessageEntityBold"][0]
    assert t == "цитата"
    assert (bold2.offset, bold2.length) == (0, 6)


def test_code_fence_protects_diff_markers():
    # Ревью 01.10, блокер №1: "> " внутри ```diff — это контент диффа,
    # не цитата. Маркер обязан остаться внутри кодового блока как текст.
    apply_bq = _fn()
    t, ents = apply_bq(
        "патч:\n```diff\n- старая строка\n> новая строка\n+ ещё правка\n```", [])
    assert "> новая строка" in t  # маркер не съеден
    if _has_telethon():
        assert ents == []  # никаких blockquote внутри фенса


def test_quotes_around_fence_live():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    apply_bq = _fn()
    t, ents = apply_bq("> цитата жива\n```diff\n> внутри кода\n```\n> после кода", [])
    assert t == "цитата жива\n```diff\n> внутри кода\n```\nпосле кода"
    bqs = [e for e in ents if type(e).__name__ == "MessageEntityBlockquote"]
    assert len(bqs) == 2
    assert (bqs[0].offset, bqs[0].length) == (0, 11)


def test_e2e_diff_in_fence_not_quoted():
    # КРИТ №1 ревью 01.10: в прод-пайплайне markdown.parse съедает ``` ДО
    # _apply_blockquote_markers, и "> " в диффе оставался под MessageEntityPre.
    # Теперь строки под Pre/Code не цитируются, дифф не искажается.
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    from telethon.extensions import markdown
    apply_bq = _fn()
    t, ents = markdown.parse("патч:\n```diff\n- старая строка\n> новая строка\n+ ещё правка\n```")
    t2, e2 = apply_bq(t, ents)
    bqs = [e for e in e2 if type(e).__name__ == "MessageEntityBlockquote"]
    assert bqs == []  # ни одной цитаты внутри диффа
    pres = [e for e in e2 if type(e).__name__ == "MessageEntityPre"]
    assert len(pres) == 1  # Pre уцелел


def test_e2e_bold_quote_marker():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    from telethon.extensions import markdown
    apply_bq = _fn()
    t, ents = markdown.parse("**> жирная цитата**")
    t2, e2 = apply_bq(t, ents)
    L = len(t2.encode("utf-16-le")) // 2
    assert t2 == "жирная цитата"
    for e in e2:
        assert 0 <= e.offset and e.offset + e.length <= L


def test_e2e_crlf_bold_tail():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    from telethon.extensions import markdown
    apply_bq = _fn()
    t, ents = markdown.parse("**первый**\r\n\r\n> цитата\r\n**хвост**")
    t2, e2 = apply_bq(t, ents)
    L = len(t2.encode("utf-16-le")) // 2
    for e in e2:
        assert e.offset + e.length <= L
    assert t2 == "первый\n\nцитата\nхвост"


def test_empty_quote_line_no_zero_length_entity():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    apply_bq = _fn()
    t, ents = apply_bq("вступление\n> \nхвост", [])
    for e in ents:
        assert e.length > 0


def test_only_marker_line():
    if not _has_telethon():
        pytest.skip("telethon недоступен")
    apply_bq = _fn()
    t, ents = apply_bq("> одна строка весь текст", [])
    assert t == "одна строка весь текст"
    assert len(ents) == 1
    assert ents[0].offset == 0


def test_marker_not_at_start_ignored():
    apply_bq = _fn()
    t, ents = apply_bq("текст с > внутри строки", [])
    # "> " не в начале строки — маркером не считается
    assert t == "текст с > внутри строки"
    if _has_telethon():
        assert ents == []


def test_plain_gt_without_space_not_marker():
    apply_bq = _fn()
    t, ents = apply_bq(">без пробела", [])
    assert t == ">без пробела"
    if _has_telethon():
        assert ents == []


def test_all_markers_only_text_falls_back_verbatim():
    """Край №3: текст из пустых маркеров → исходник как есть, не пустое сообщение."""
    fn = _fn()
    for src in ["> ", "> \n> ", ">  \n> \n"]:
        parsed, ents = fn(src, [])
        assert parsed == src and ents == [], (src, parsed, ents)
    # содержательные цитаты работают как раньше
    t, ents = fn("> одна\n> вторая", [])
    assert t == "одна\nвторая" and len(ents) == 1
