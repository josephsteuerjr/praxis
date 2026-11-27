# -*- coding: utf-8 -*-
"""Характеристические тесты bufstore: заморозка ПОВЕДЕНИЯ старой версии.

Написаны до переписывания модуля (этап 1 задачи code-473f9dd3). Каждый тест —
реальное поведение из docstring/кода с живыми прецедентами:

  - атомарная запись буфера; байт-в-байт идентичная запись НЕ трогает файл
    (честный mtime, Шаг 6 25.08: рестарты не переписывали 693 файла);
  - фолбэки битых/отсутствующих файлов: load -> [], load_all пропускает;
  - PASS 9.0 мета: last_ts монотонен (старый исполнитель не отменяет нового),
    is_dm фолбэк по знаку ключа, name/author перезаписываются;
  - PASS 23.09: мета с last_author=praxis исключает ЛС из кандидатов, даже
    если файл буфера ещё не видел ответа (кейс «шесть ответов на одно
    входящее» 21–23.09);
  - missed_dm_candidates: только DM, свежесть, suppresses_replay.

Тесты обязаны проходить И на старой, И на новой версии модуля.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bufstore  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Изолированные каталоги буферов/меты + подавление живого step_control."""
    buf_dir = tmp_path / "buffers"
    state_dir = tmp_path / "state"
    monkeypatch.setattr(bufstore, "BASE", tmp_path)
    monkeypatch.setattr(bufstore, "BUF_DIR", buf_dir)
    monkeypatch.setattr(bufstore, "STATE_DIR", state_dir)
    monkeypatch.setattr(bufstore, "META_PATH", state_dir / "buf_meta.json")

    import step_control
    calls: list[tuple] = []
    monkeypatch.setattr(
        step_control, "suppresses_replay",
        lambda cid, ts, base=None: calls.append((cid, ts)) and False,
    )
    store.calls = calls  # type: ignore[attr-defined]
    return bufstore


class TestSafeAndPath:
    def test_safe_sanitizes_non_word_chars(self):
        assert bufstore._safe("a b/c") == "a_b_c"
        assert bufstore._safe(-100123) == "-100123"

    def test_safe_empty_falls_back_to_chat(self):
        # фактическое поведение старой версии: фолбэк "chat" — только для ПУСТОЙ
        # строки; "///" превращается в "___" (истинное значение, not empty)
        assert bufstore._safe("") == "chat"
        assert bufstore._safe("///") == "___"

    def test_path_for_uses_buf_dir(self, store):
        p = bufstore.path_for(-1003701205730)
        assert p.parent == bufstore.BUF_DIR
        assert p.name == "-1003701205730.json"


class TestSaveLoad:
    def test_save_writes_json_list_of_strings(self, store):
        bufstore.save(123, ["Anna: привет", "Praxis: здравствуй"])
        raw = (bufstore.BUF_DIR / "123.json").read_text(encoding="utf-8")
        assert json.loads(raw) == ["Anna: привет", "Praxis: здравствуй"]
        assert isinstance(raw, str) and "привет" in raw  # ensure_ascii=False

    def test_save_coerces_non_str_lines(self, store):
        bufstore.save("c1", [42, None, "x"])
        assert bufstore.load("c1") == ["42", "None", "x"]

    def test_save_identical_payload_keeps_mtime(self, store):
        bufstore.save("c1", ["a: 1"])
        p = bufstore.BUF_DIR / "c1.json"
        before = p.stat().st_mtime_ns
        time.sleep(0.02)
        bufstore.save("c1", ["a: 1"])
        assert p.stat().st_mtime_ns == before  # идентичная запись — не изменение

    def test_save_changed_payload_rewrites(self, store):
        bufstore.save("c1", ["a: 1"])
        time.sleep(0.02)
        bufstore.save("c1", ["a: 2"])
        assert bufstore.load("c1") == ["a: 2"]

    def test_load_missing_returns_empty_list(self, store):
        assert bufstore.load("nope") == []

    def test_load_broken_json_returns_empty(self, store):
        bufstore.BUF_DIR.mkdir(parents=True, exist_ok=True)
        (bufstore.BUF_DIR / "bad.json").write_text("{not json", encoding="utf-8")
        assert bufstore.load("bad") == []

    def test_load_non_list_json_returns_empty(self, store):
        bufstore.BUF_DIR.mkdir(parents=True, exist_ok=True)
        (bufstore.BUF_DIR / "dict.json").write_text('{"a": 1}', encoding="utf-8")
        assert bufstore.load("dict") == []

    def test_load_all_skips_broken_and_keeps_rest(self, store):
        bufstore.save("ok1", ["x: 1"])
        bufstore.save("ok2", [])
        (bufstore.BUF_DIR / "broken.json").write_text("[1,", encoding="utf-8")
        (bufstore.BUF_DIR / "notlist.json").write_text("{}", encoding="utf-8")
        all_loaded = bufstore.load_all()
        assert all_loaded == {"ok1": ["x: 1"], "ok2": []}

    def test_load_all_empty_dir(self, store):
        assert bufstore.load_all() == {}


class TestMeta:
    def test_meta_load_missing_returns_empty_dict(self, store):
        assert bufstore.meta_load() == {}

    def test_meta_load_broken_returns_empty_dict(self, store):
        bufstore.STATE_DIR.mkdir(parents=True, exist_ok=True)
        bufstore.META_PATH.write_text("~~", encoding="utf-8")
        assert bufstore.meta_load() == {}

    def test_meta_update_creates_entry_with_dm_fallback_by_sign(self, store):
        bufstore.meta_update(809306689, author="Yegor", name="Yegor")
        bufstore.meta_update(-100123, author="GR")
        m = bufstore.meta_load()
        assert m["809306689"]["is_dm"] is True
        assert m["-100123"]["is_dm"] is False
        assert m["809306689"]["last_author"] == "Yegor"
        assert m["809306689"]["name"] == "Yegor"

    def test_meta_update_monotonic_ts(self, store):
        t0 = 1_000_000.0
        t1 = 1_000_100.0
        bufstore.meta_update("u1", author="late", ts=t1)
        bufstore.meta_update("u1", author="early", ts=t0)  # старый исполнитель
        m = bufstore.meta_load()
        assert m["u1"]["last_ts"] == t1
        assert m["u1"]["last_author"] == "late"

    def test_meta_update_preserves_dm_and_name_when_omitted(self, store):
        bufstore.meta_update("u1", author="a", is_dm=True, name="Ann", ts=10.0)
        bufstore.meta_update("u1", author="b", ts=20.0)
        e = bufstore.meta_load()["u1"]
        assert e["is_dm"] is True and e["name"] == "Ann" and e["last_author"] == "b"

    def test_meta_update_explicit_flip(self, store):
        bufstore.meta_update("u1", author="a", is_dm=False, ts=10.0)
        bufstore.meta_update("u1", author="b", is_dm=True, ts=20.0)
        assert bufstore.meta_load()["u1"]["is_dm"] is True

    def test_meta_empty_author_clears_previous(self, store):
        # [1] ревьюера: пустой автор — тоже сигнал; стирает прежнего
        bufstore.meta_update("u1", author="praxis", ts=10.0)
        bufstore.meta_update("u1", author="", ts=20.0)
        assert bufstore.meta_load()["u1"]["last_author"] == ""

    def test_meta_name_and_isdm_coerced(self, store):
        # [2] ревьюера: name/is_dm приводятся к str/bool, как в старой версии
        bufstore.meta_update("u1", author="a", name=123, is_dm=1, ts=10.0)
        e = bufstore.meta_load()["u1"]
        assert e["name"] == "123" and isinstance(e["name"], str)
        assert e["is_dm"] is True and isinstance(e["is_dm"], bool)


class TestMissedDmCandidates:
    def _seed_dm(self, store, cid="809306689", lines=None, name="Yegor",
                 last_author="Yegor", last_ts=None):
        lines = lines if lines is not None else ["Yegor: вопрос?"]
        bufstore.save(cid, lines)
        bufstore.meta_update(cid, author=last_author, is_dm=True, name=name,
                             ts=last_ts if last_ts is not None else time.time() - 600)

    def test_fresh_unanswered_dm_is_candidate(self, store):
        self._seed_dm(store)
        out = bufstore.missed_dm_candidates(max_age_hours=48)
        assert len(out) == 1
        assert out[0]["chat_id"] == "809306689"
        assert out[0]["name"] == "Yegor"
        assert 0.0 <= out[0]["age_hours"] < 1.0

    def test_group_chat_never_candidate(self, store):
        # мета не пишем: is_dm из меты отсутствует → фолбэк по знаку ключа "-..."
        bufstore.save("-100123", ["GR: эй"])
        assert bufstore.missed_dm_candidates(max_age_hours=48) == []

    def test_meta_is_dm_true_beats_negative_sign(self, store):
        # а если мета говорит is_dm=True для отрицательного ключа — верим мете
        self._seed_dm(store, cid="-100123", lines=["GR: эй"])
        out = bufstore.missed_dm_candidates(max_age_hours=288)
        assert len(out) == 1 and out[0]["chat_id"] == "-100123"

    def test_last_line_praxis_skips(self, store):
        self._seed_dm(store, lines=["Yegor: вопрос?", "Praxis: ответ"],
                      last_author="Praxis")
        assert bufstore.missed_dm_candidates(max_age_hours=48) == []

    def test_meta_author_praxis_beats_stale_file(self, store):
        # PASS 23.09: файл ещё не видел ответа, но мета знает — не кандидат.
        self._seed_dm(store, lines=["Yegor: вопрос?"], last_author="praxis")
        assert bufstore.missed_dm_candidates(max_age_hours=48) == []

    def test_meta_author_praxis_casefold(self, store):
        self._seed_dm(store, lines=["Yegor: вопрос?"], last_author="  PRAXIS ")
        assert bufstore.missed_dm_candidates(max_age_hours=48) == []

    def test_stale_dm_not_candidate(self, store):
        self._seed_dm(store, last_ts=time.time() - 72 * 3600)
        assert bufstore.missed_dm_candidates(max_age_hours=48) == []

    def test_boundary_age_excluded(self, store):
        self._seed_dm(store, last_ts=time.time() - 48 * 3600)
        assert bufstore.missed_dm_candidates(max_age_hours=48) == []

    def test_empty_buffer_not_candidate(self, store):
        bufstore.save("u2", [])
        bufstore.meta_update("u2", author="x", is_dm=True, ts=time.time() - 60)
        assert bufstore.missed_dm_candidates(max_age_hours=48) == []

    def test_is_dm_fallback_by_sign_without_meta(self, store):
        bufstore.save("809306689", ["Yegor: вопрос?"])
        # мета не пишем: is_dm определяется знаком ключа, ts — mtime файла
        out = bufstore.missed_dm_candidates(max_age_hours=48)
        assert len(out) == 1 and out[0]["chat_id"] == "809306689"

    def test_suppresses_replay_filters(self, store, monkeypatch):
        import step_control
        monkeypatch.setattr(step_control, "suppresses_replay",
                            lambda cid, ts, base=None: True)
        self._seed_dm(store)
        assert bufstore.missed_dm_candidates(max_age_hours=48) == []

    def test_no_mutation_of_disk(self, store):
        self._seed_dm(store)
        before = (bufstore.BUF_DIR / "809306689.json").read_bytes()
        bufstore.missed_dm_candidates(max_age_hours=48)
        after = (bufstore.BUF_DIR / "809306689.json").read_bytes()
        assert before == after

    def test_garbage_last_ts_migration(self, store):
        # [5] ревьюера — карта миграции, НЕ equivalence: старая версия кидала
        # ValueError из meta_update и исключала чат из sweep; новая чинит порчу
        # (коэрсит в 0.0) и восстанавливает кандидата по mtime файла.
        bufstore.save("u1", ["Y: q"])
        bufstore.STATE_DIR.mkdir(parents=True, exist_ok=True)
        bufstore.META_PATH.write_text(json.dumps(
            {"u1": {"last_ts": "garbage", "last_author": "Y", "is_dm": True}}),
            encoding="utf-8")
        bufstore.meta_update("u1", author="Z", ts=1e9)  # не бросает, чинит
        out = bufstore.missed_dm_candidates(max_age_hours=48,
                                            now=1e9 + 3600)
        assert len(out) == 1 and out[0]["chat_id"] == "u1"
