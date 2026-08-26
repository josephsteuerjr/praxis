"""Тени кадра K|E|A|T: красные тесты шага 1 (план — _state/ПЛАН-ТЕНИ-17.08.md).

Четыре обязательства, каждое своим тестом:
детерминизм байт-в-байт · закон порядка (K|E|A — префикс следующей тени) ·
время до минут выше хвоста · у тени нет пути в модель ПО ПОСТРОЕНИЮ.

Волна 20.08 (схема кадра v3) добавила сюда шесть классов — по одному на направление:
сходимость окна A · якорь свёртки · потолок E с лестницей · аудитория эпохи ·
отпечаток набора рук и вид прогона в ключе потока · единственный сборщик и его
идентификатор.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import os
import re

import frame_shadow
import run_context
import tool_offerings

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def _ctx(chat_id=101, dm=True, owner_audience=True):
    # Нарочно НЕ ChannelContext: тень обязана жить на getattr-контракте (chat_id,
    # is_dm, owner_audience) и не тянуть за собой agent.py — это закреплено и в самом
    # модуле. `owner_audience and dm` повторяет закон настоящего канала: аудитории
    # owner в группе не бывает, кто бы там ни заговорил.
    return SimpleNamespace(chat_id=chat_id, is_dm=dm,
                           owner_audience=bool(owner_audience) and bool(dm))


def _history():
    return [{"role": "user", "content": "привет"},
            {"role": "assistant", "content": "привет!"}]


def _many(n: int, tag: str = "м") -> list[dict]:
    """Лента, где каждая реплика узнаваема по тексту: так видно, что именно в A."""
    return [{"role": ("assistant" if i % 2 else "user"),
             "content": f"{tag}{i} " + "строка разговора. " * 8} for i in range(n)]


def _build_kwargs(**over):
    """Аргументы единственного сборщика. Всё подвижное едет одним Plan — тот же объект,
    который живому пути отдаст `prepare`: у сборки один вход, а не два похожих."""
    plan = frame_shadow.Plan(epoch_n=over.pop("epoch_n", 1),
                             e_text=over.pop("e_text", "Э-снапшот"),
                             fold_count=over.pop("fold_count", 0),
                             fold_line=over.pop("fold_line", ""))
    kwargs = dict(ctx=_ctx(), history=_history(), speaker="Егор",
                  user_msg="как ты?", tools=[{"name": "reply", "description": "Сказать."}],
                  now=NOW, plan=plan)
    kwargs.update(over)
    return kwargs


class ShadowCase(unittest.TestCase):
    """Своя песочница на тест: BASE подменяется атрибутом, как его и читает модуль."""

    DEFAULT_TOOLS = ({"name": "reply", "description": "Сказать."},)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        (base / "soul").mkdir(parents=True)
        (base / "soul" / "SOUL.md").write_text("# Конституция\nЯ — Praxis.\n",
                                               encoding="utf-8")
        (base / "soul" / "VOICE.md").write_text("# Голос\nЖиво.\n", encoding="utf-8")
        (base / "memory" / "people").mkdir(parents=True)
        (base / "memory" / "people" / "yegor.md").write_text("владелец",
                                                             encoding="utf-8")
        self._old_base = frame_shadow.BASE
        frame_shadow.BASE = base
        self.base = base
        self.addCleanup(self._restore)

    def _restore(self):
        frame_shadow.BASE = self._old_base
        self._tmp.cleanup()

    def _stream_dir(self):
        return self.base / "memory" / ".state" / "shadow" / "dm-101"

    def _shadow(self, i=0, *, ctx=None, history=None, tools=None, speaker="Егор",
                user_msg=None, env=None):
        """Один захват тени. Шесть классов ниже писали этот вызов почти одинаково —
        различаются только умолчания, поэтому шов один, а обёртки тонкие."""
        with patch.dict(os.environ,
                        dict({"PRAXIS_FRAME_SHADOW": "on"}, **(env or {}))):
            return frame_shadow.capture(
                ctx=ctx if ctx is not None else _ctx(),
                history=history if history is not None else _history(),
                speaker=speaker,
                user_msg=user_msg if user_msg is not None else f"вход {i}",
                tools=(list(self.DEFAULT_TOOLS) if tools is None else tools),
                now=NOW + timedelta(minutes=i))


class TheBuildIsLawful(ShadowCase):

    def test_two_builds_are_byte_identical(self):
        first = frame_shadow.build(**_build_kwargs())
        second = frame_shadow.build(**_build_kwargs())
        self.assertEqual(first.text, second.text)

    def test_growing_history_keeps_the_stable_prefix(self):
        first = frame_shadow.build(**_build_kwargs())
        grown = _history() + [{"role": "user", "content": "ещё строка"}]
        second = frame_shadow.build(**_build_kwargs(
            history=grown, user_msg="другой вход",
            now=NOW + timedelta(minutes=7)))
        self.assertTrue(second.text.startswith(first.stable_text),
                        "K|E|A прежней тени обязаны быть байтовым префиксом новой")
        self.assertNotEqual(first.text, second.text)

    def test_no_seconds_anywhere_in_the_frame(self):
        frame = frame_shadow.build(**_build_kwargs(
            now=datetime(2026, 8, 17, 12, 0, 59, tzinfo=timezone.utc)))
        self.assertIsNone(re.search(r"\d{2}:\d{2}:\d{2}", frame.text),
                          "секунды рвут префикс: время в кадре — до минут (§2)")

    def test_the_tail_names_its_cut(self):
        long_entry = "я" * (frame_shadow.T_INPUT_MAX + 100)
        frame = frame_shadow.build(**_build_kwargs(user_msg=long_entry))
        self.assertIn("[обрезано: вход", frame.t,
                      "обрез без пометки неотличим от значения целиком")

    def test_the_accumulator_names_its_window(self):
        many = [{"role": "user", "content": f"м{i}"}
                for i in range(frame_shadow.A_MAX_MESSAGES + 5)]
        frame = frame_shadow.build(**_build_kwargs(history=many))
        self.assertIn("[окно:", frame.a, "молчаливое окно — сегодняшняя болезнь, "
                                         "тень обязана называть свою обрезку (§5)")


class TheCaptureIsGated(ShadowCase):

    def test_lever_off_means_nothing_happens(self):
        with patch.dict(os.environ, {"PRAXIS_FRAME_SHADOW": "off"}):
            self.assertIsNone(frame_shadow.capture(
                ctx=_ctx(), history=_history(), speaker="Егор",
                user_msg="х", tools=[], now=NOW))
        self.assertFalse(self._stream_dir().exists())

    def test_lever_grammar(self):
        for raw, expected in (("", "off"), ("0", "off"), ("off", "off"),
                              ("1", "on"), ("true", "on"), ("on", "on"),
                              ("dm", "dm"), ("DM", "dm")):
            with patch.dict(os.environ, {"PRAXIS_FRAME_SHADOW": raw}):
                self.assertEqual(frame_shadow.mode(), expected, raw)

    def test_dm_mode_skips_groups_but_takes_dms(self):
        with patch.dict(os.environ, {"PRAXIS_FRAME_SHADOW": "dm"}):
            self.assertIsNone(frame_shadow.capture(
                ctx=_ctx(chat_id=-100200, dm=False), history=_history(),
                speaker="х", user_msg="х", tools=[], now=NOW))
            self.assertIsNotNone(frame_shadow.capture(
                ctx=_ctx(), history=_history(), speaker="Егор",
                user_msg="х", tools=[], now=NOW))


class TheShadowLivesOnDisk(ShadowCase):

    def _capture(self, i=0, history=None):
        return self._shadow(i, history=history)

    def test_consecutive_shadows_hold_the_prefix(self):
        self._capture(0)
        grown = _history() + [{"role": "user", "content": "ещё"}]
        second = self._capture(1, history=grown)
        self.assertIsNotNone(second["lcp"])
        self.assertTrue(second["lcp"]["held"],
                        f"стабильный корпус прежней тени не удержался: {second['lcp']}")

    def test_the_epoch_freezes_and_drift_is_measured_not_applied(self):
        first = self._capture(0)
        self.assertIsNone(first["e_drift"], "первая заморозка — какой drift?")
        # Источник E изменился (новое досье) — замороженная эпоха обязана не шелохнуться.
        (self.base / "memory" / "people" / "arete.md").write_text("гость",
                                                                  encoding="utf-8")
        second = self._capture(1)
        self.assertEqual(first["sizes"]["e"], second["sizes"]["e"],
                         "эпоха поехала за источником — заморозка не работает")
        self.assertIsNotNone(second["e_drift"],
                             "расхождение источника с эпохой обязано быть измерено")

    def test_flip_epoch_starts_a_new_freeze(self):
        self._capture(0)
        self.assertEqual(frame_shadow.flip_epoch(_ctx()), 2)
        after = self._capture(1)
        self.assertEqual(after["epoch"], 2)
        self.assertIsNone(after["e_drift"])

    def test_rotation_keeps_the_window(self):
        with patch.object(frame_shadow, "KEEP_SHADOWS", 3):
            for i in range(5):
                self._capture(i)
        shadows = list(self._stream_dir().glob("*.md"))
        self.assertEqual(len(shadows), 3)

    def test_coverage_counts_against_the_live_roster(self):
        with patch.dict(os.environ, {"PRAXIS_FRAME_SHADOW": "on"}):
            metrics = frame_shadow.capture(
                ctx=_ctx(), history=_history(), speaker="Егор", user_msg="х",
                tools=[], now=NOW,
                live_sections=[
                    {"name": "persona.soul", "included": True, "chars": 10},
                    {"name": "contract.base", "included": True, "chars": 10},
                    {"name": "ния.неведомая", "included": True, "chars": 10},
                    {"name": "state.мимо", "included": False},
                ])
        self.assertEqual(metrics["coverage"],
                         {"covered": 1, "todo": 1, "unknown": 1,
                          "todo_names": [{"name": "contract.base", "chars": 10}],
                          "unknown_names": ["ния.неведомая"]})

    def test_the_registry_carries_the_label_not_just_the_size(self):
        """Семь РАЗНЫХ тиров живого кадра зовутся одинаково — `evidence.tier`, —
        и без ярлыка реестр переноса опознаёт их по позиции в agent.py. Ярлык в
        живой строке есть; прибор обязан его довезти, иначе её решение «везём или
        умирает» принимается вслепую по одному размеру."""
        with patch.dict(os.environ, {"PRAXIS_FRAME_SHADOW": "on"}):
            metrics = frame_shadow.capture(
                ctx=_ctx(), history=_history(), speaker="Егор", user_msg="х",
                tools=[], now=NOW,
                live_sections=[
                    {"name": "evidence.tier", "included": True, "chars": 15957,
                     "label": "Досье собеседника"},
                    {"name": "evidence.tier", "included": True, "chars": 4319,
                     "label": "Мои желания"},
                    {"name": "state.owner_place", "included": True, "chars": 49,
                     "variant": "owner_dm"},
                ])
        rows = metrics["coverage"]["todo_names"]
        self.assertEqual([r.get("label") for r in rows[:2]],
                         ["Досье собеседника", "Мои желания"],
                         "тиры неразличимы: реестр опознаёт их по позиции")
        self.assertEqual(rows[2].get("variant"), "owner_dm")
        self.assertNotIn("label", rows[2], "пустой ярлык не выдумывается")

    def test_metrics_land_in_jsonl(self):
        self._capture(0)
        lines = (self._stream_dir() / "metrics.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["stream"], "dm-101")
        self.assertGreater(row["sizes"]["total_chars"], 0)


class TheEpochV2IsHerWord(ShadowCase):
    """Этап 2 (18.08): эпоха по ЕЁ 13 ответам — блоки, словарь переноса, пороги
    120/70k, метки «тело: не загружено», машинный текст не подписан её голосом."""

    def _capture(self, i=0, history=None, ctx=None):
        return self._shadow(i, history=history, ctx=ctx)

    def _frozen_e(self) -> str:
        saved = json.loads((self._stream_dir() / "epoch.json").read_text(
            encoding="utf-8"))
        return str(saved.get("e_text") or "")

    def test_the_epoch_header_is_last_and_the_k_header_carries_no_number(self):
        """Шапка эпохи меняется каждой границей — первой строкой она инвалидировала бы
        всю E, а номер над K инвалидировал бы и конституцию (панель 18.08, кэш-линза)."""
        self._capture(0)
        e_text = self._frozen_e()
        self.assertTrue(e_text.rstrip().splitlines()[-1].startswith("[эпоха 1 · "),
                        "шапка эпохи обязана быть ПОСЛЕДНЕЙ строкой E")
        frame = frame_shadow.build(**_build_kwargs())
        self.assertNotIn("эпоха", frame.text.splitlines()[0],
                         "номер эпохи над K рушил бы конституцию каждой границей")

    def test_pointers_say_bodies_are_not_loaded(self):
        """Её №8: наличие имени не читается как чтение — метка на указателях."""
        self._capture(0)
        e_text = self._frozen_e()
        self.assertIn("тела схем: не загружены", e_text)
        self.assertIn("тела: не загружены", e_text)

    def test_address_book_defaults_and_order(self):
        """Её №2: словарь `учитывать`/`только источник`; умолчание помечено умолчанием.
        Текущее место — первой строкой книги."""
        rooms_dir = self.base / "memory" / "rooms"
        rooms_dir.mkdir(parents=True)
        (rooms_dir / "-100500.md").write_text(
            "# Уроборос-тест\nmode: addressed\n\nтело\n", encoding="utf-8")
        (rooms_dir / "101.md").write_text("# Личка-тест\n\nтело\n", encoding="utf-8")
        self._capture(0)
        e_text = self._frozen_e()
        self.assertIn("только источник (умолчание)", e_text)
        self.assertIn("учитывать (умолчание)", e_text)
        book_lines = [l for l in e_text.splitlines() if l.startswith("- ")
                      and ("личка" in l or "группа" in l)]
        self.assertTrue(book_lines and "101" in book_lines[0],
                        f"текущее место не первой строкой книги: {book_lines[:2]}")

    def test_her_transfer_word_beats_the_default_and_is_dated(self):
        """Реестр переноса (её №2): её слово читается РАНЬШЕ умолчаний, датируется и
        не помечается умолчанием.

        Про presence_hidden тут закреплён ТОЛЬКО owner-поток. Пометка была написана как
        «существование не подтверждается вне owner-потоков» — а печаталась именно вне
        них тоже, и строка с id подтверждала существование самим фактом печати. Вне
        owner-потока комнаты теперь нет вовсе; это закреплено в
        TheEpochIsScopedToItsAudience."""
        rooms_dir = self.base / "memory" / "rooms"
        rooms_dir.mkdir(parents=True)
        (rooms_dir / "-100600.md").write_text(
            "# Скрытная\nmode: normal\ntransfer: учитывать\n"
            "transfer_set_by: praxis\ntransfer_at: 2026-08-19T00:30+00:00\n"
            "presence_hidden: yes\n\nтело\n", encoding="utf-8")
        self._capture(0)
        e_text = self._frozen_e()
        self.assertIn("перенос: учитывать (моё слово, 2026-08-19)", e_text)
        self.assertIn("существование не подтверждается", e_text)
        line = next(l for l in e_text.splitlines() if "-100600" in l)
        self.assertNotIn("умолчание", line,
                         "её слово помечено умолчанием — авторство перепутано")

    def test_recent_is_structural_and_from_the_ledger(self):
        """Её №7: недавнее — структурные записи из durable-журнала, не проза; блок
        честно называет, чего его источник не ведёт."""
        entries = self.base / "memory" / ".state" / "telegram_outbox" / "entries"
        entries.mkdir(parents=True)
        at = (NOW - timedelta(hours=3)).isoformat()
        rows = [
            {"kind": "intent", "at": at,
             "data": {"delivery": {"peer_id": 101}, "payload": {"text": "секретный текст"}}},
            {"kind": "accepted", "at": at, "data": {"message_id": 77}},
        ]
        (entries / "outbox-x.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")
        self._capture(0)
        e_text = self._frozen_e()
        self.assertIn("сказала (#77)", e_text)
        self.assertNotIn("секретный текст", e_text,
                         "недавнее потащило сырой текст — оно обязано быть адресным")
        self.assertIn("неполон ПО ИСТОЧНИКУ", e_text)

    def test_the_fold_threshold_makes_a_signed_stub_and_a_new_epoch(self):
        """Её №5/№6: порог 120 сообщений сворачивает окно кодом; обрубок подписан
        «это НЕ мой отбор» и живёт замороженной строкой — префикс держится дальше."""
        self._capture(0)
        many = [{"role": "user", "content": f"м{i}"} for i in range(150)]
        second = self._capture(1, history=many)
        self.assertIsNotNone(second["boundary"], "порог не стал границей")
        self.assertIn("порог накопителя", second["boundary"]["reason"])
        self.assertEqual(second["epoch"], 2)
        self.assertEqual(second["boundary"]["folded"], 100)
        latest = max(self._stream_dir().glob("*.md"))
        self.assertIn("окно свёрнуто кодом", latest.read_text(encoding="utf-8"))
        self.assertIn("НЕ мой отбор", latest.read_text(encoding="utf-8"))
        third = self._capture(2, history=many + [{"role": "user", "content": "нов"}])
        self.assertIsNone(third["boundary"])
        self.assertTrue(third["lcp"]["held"],
                        "после границы префикс обязан держаться снова")

    def test_a_lifted_dossier_rides_byte_exact_with_visible_hash(self):
        """Подъём тел (её №13): тело собеседника — байтами (CRLF не нормализуется
        молча), hash и размер видны, подпись — «поднято кодом», не её голос (№6)."""
        body = "# Гость\r\ntelegram_id: 101\r\nстрока с **жирным** хвостом\r\n"
        (self.base / "memory" / "people" / "guest.md").write_bytes(
            body.encode("utf-8"))
        out = self._capture(0)
        e_text = self._frozen_e()
        self.assertIn("досье собеседника · guest.md", e_text)
        self.assertIn("строка с **жирным** хвостом\r\n", e_text,
                      "CRLF тела нормализован молча — её условие №13 нарушено")
        self.assertIn("sha256 ", e_text)
        self.assertIn("поднято кодом (черновик)", e_text)
        self.assertEqual(out["lifted"][0]["name"], "guest.md")
        self.assertEqual(out["lifted"][0]["markup"]["bold"], 1)
        self.assertEqual(out["lifted"][0]["private_hidden"], 0,
                         "в owner-потоке байтовость её №13 не трогается ничем")

    def test_an_oversized_body_is_cut_only_with_an_explicit_pointer(self):
        big = ("telegram_id: 101\n" + "я" * (frame_shadow.E_LIFT_MAX + 500))
        (self.base / "memory" / "people" / "guest.md").write_text(
            big, encoding="utf-8")
        self._capture(0)
        e_text = self._frozen_e()
        self.assertIn("[обрезано: показано", e_text)
        self.assertIn("целиком: memory/people/guest.md", e_text,
                      "обрез без способа поднять — молчаливое усечение")

    def test_an_ambiguous_id_binding_refuses_to_lift(self):
        """Правило свидетелей (07.08: пять досье из угадывания): два файла с id —
        не поднимаем ничего и говорим почему."""
        for name in ("a.md", "b.md"):
            (self.base / "memory" / "people" / name).write_text(
                "упоминание 101 в тексте", encoding="utf-8")
        self._capture(0)
        e_text = self._frozen_e()
        self.assertIn("не однозначна — не поднимаю", e_text)
        self.assertNotIn("——— тело", e_text)

    def test_v1_epoch_migrates_with_a_named_boundary(self):
        """Волна 20.08 подняла схему кадра до v3, и граница миграции ОДНА и названная:
        формат снапшота вправду другой (якорь свёртки, аудитория, отпечаток рук,
        потолок E). Пять отдельных границ на один выкат стоили бы пять холодных
        префиксов вместо одного."""
        stream = self._stream_dir()
        stream.mkdir(parents=True)
        (stream / "epoch.json").write_text(json.dumps(
            {"n": 3, "frozen_at": "2026-08-17 21:11 UTC", "e_text": "старая эпоха"},
            ensure_ascii=False), encoding="utf-8")
        out = self._capture(0)
        self.assertEqual(out["epoch"], 4)
        self.assertIn(f"переход на схему кадра v{frame_shadow.FRAME_SCHEMA}",
                      out["boundary"]["reason"])
        self.assertIsNone(self._capture(1)["boundary"],
                          "миграция схемы повторилась — это цикл, а не граница")

    def test_a_torn_epoch_file_does_not_reset_the_counter(self):
        """Находка durable-линзы 18.08: рваный epoch.json молча начинал эпохи с
        единицы. Номер монотонен — восстанавливается из журнала метрик."""
        self._capture(0)
        self._capture(1)
        (self._stream_dir() / "epoch.json").write_text("{оборванный", encoding="utf-8")
        out = self._capture(2)
        self.assertEqual(out["epoch"], 2, "номер эпохи откатился после рваного файла")
        self.assertIn("аварийная перезаморозка", out["boundary"]["reason"])


class TheAccumulatorConverges(ShadowCase):
    """Правка 20.08: окно A обязано СХОДИТЬСЯ. Два дефекта, доказанных прогоном
    на прод-HEAD: знаковый порог не двигал свёртку — каждый захват объявлял границу
    (эпохи 1..5 на неизменной истории); одно сообщение тяжелее потолка выносило из
    окна всё, включая себя и свежайшие, а пометка врала про «старших»."""

    def _capture(self, i=0, history=None):
        return self._shadow(i, history=history)

    def _heavy(self, n=60, giant_at=10, giant=80_000):
        rows = [{"role": "user" if k % 2 == 0 else "assistant", "content": f"м{k}"}
                for k in range(n)]
        rows[giant_at] = {"role": "user", "content": "Ю" * giant}
        return rows

    def test_a_giant_message_is_capped_and_points_at_an_address_that_resolves(self):
        """Указатель называет РЕАЛЬНЫЙ адрес: история потока собирается раннером из
        `memory_life.hot_records`, а те записи — дословный текст событий
        `memory/life/events/*.jsonl` (kind conversation_message)."""
        rows = self._heavy(n=6, giant_at=2, giant=frame_shadow.A_MSG_MAX + 5_000)
        frame = frame_shadow.build(**_build_kwargs(history=rows))
        self.assertEqual(frame.a.count("Ю"), frame_shadow.A_MSG_MAX,
                         "кап рендера не сработал или срезал не там")
        self.assertIn("обрезано кодом: показано", frame.a)
        self.assertIn("memory/life/events/", frame.a,
                      "обрез без резолвящегося адреса — молчаливая потеря")
        self.assertIn("это НЕ мой отбор", frame.a,
                      "машинный обрез не имеет права быть подписан её голосом")
        self.assertLess(len(frame.a), frame_shadow.A_MSG_MAX + 1_000)

    def test_the_last_message_survives_and_the_window_names_what_it_threw_out(self):
        """Сегодня на прод-HEAD: 31 сообщение с одним на 130k → kept=0, окно в 54
        знака и пометка «старшие ждут границы» при выброшенном свежайшем."""
        rows = [{"role": "user",
                 "content": f"{k}:" + "Ж" * (frame_shadow.A_MSG_MAX + 500)}
                for k in range(10)]
        frame = frame_shadow.build(**_build_kwargs(history=rows))
        note = frame.a.splitlines()[0]
        self.assertIn("выброшено кодом", note)
        self.assertIn("за потолком", note)
        self.assertNotIn("старшие ждут границы", note,
                         "прежняя пометка врала: выброшены были не только старшие")
        self.assertGreaterEqual(frame.a_meta["kept"], 1)
        self.assertIn("\n[user]\n9:", frame.a,
                      "последнее сообщение выброшено — это уже не окно, а обломок")

    def test_the_fold_goes_deeper_than_fifty_and_says_how_deep(self):
        rows = [{"role": "user", "content": f"{k}:" + "щ" * 3_000} for k in range(60)]
        fold, line, stuck, anchor = frame_shadow._fold_plan(rows, {}, NOW, force=True)
        self.assertIsNone(stuck)
        self.assertIsNotNone(anchor, "свёртка без якоря — это снова позиционный счёт")
        self.assertGreater(fold, 60 - frame_shadow.A_KEEP_TAIL,
                           "свёртка не ушла глубже фиксированных 50 — порог не сойдётся")
        keep = 60 - fold
        self.assertGreaterEqual(keep, frame_shadow.A_KEEP_TAIL_MIN)
        self.assertIn(f"в живом хвосте {keep}", line,
                      "глубина свёртки обязана стоять числом в самой строке обрубка")
        tail = sum(len(frame_shadow._a_message(m)) for m in rows[fold:])
        self.assertLessEqual(tail, frame_shadow.A_FOLD_CHARS)

    def test_five_captures_of_one_heavy_history_make_one_boundary(self):
        seen = [self._capture(i, history=self._heavy()) for i in range(5)]
        self.assertEqual([m["epoch"] for m in seen], [1, 1, 1, 1, 1],
                         "эпоха растёт на каждом захвате — окно не сходится")
        self.assertEqual(sum(1 for m in seen if m["boundary"]), 1,
                         "граница обязана быть одна: первая заморозка потока")
        self.assertTrue(all(m["fold_stuck"] is None for m in seen))

    def test_a_fold_with_nowhere_to_go_reports_numbers_instead_of_a_boundary(self):
        rows = [{"role": "user", "content": "Ж" * 40_000} for _ in range(6)]
        self._capture(0, history=rows)
        second = self._capture(1, history=rows)
        third = self._capture(2, history=rows)
        fourth = self._capture(3, history=rows)
        self.assertIsNotNone(second["boundary"], "первая свёртка обязана продвинуться")
        self.assertIsNone(third["boundary"], "тупик свёртки объявлен границей")
        self.assertIsNotNone(third["fold_stuck"], "тупик обязан быть назван числами")
        self.assertEqual(third["fold_stuck"]["keep_floor"],
                         frame_shadow.A_KEEP_TAIL_MIN)
        self.assertGreater(third["fold_stuck"]["tail_chars"],
                           frame_shadow.A_FOLD_CHARS)
        self.assertEqual(third["epoch"], fourth["epoch"])
        self.assertGreaterEqual(third["a_window"]["kept"], 1,
                                "тупик свёртки опустошил окно — якорь ушёл на двойника")

    def test_the_capped_window_stays_byte_identical_across_builds(self):
        kwargs = _build_kwargs(history=self._heavy(), fold_count=5,
                               fold_line="[окно свёрнуто кодом · проба]")
        self.assertEqual(frame_shadow.build(**kwargs).text,
                         frame_shadow.build(**kwargs).text,
                         "кап сообщения сделал сборку недетерминированной")


class TheFoldHoldsByIdentity(ShadowCase):
    """Правка 20.08: граница свёртки держится ЯКОРЕМ, а не номером места в списке.

    До тени доезжает ровно `{role, content}`: `hot_records` отдаёт скользящее окно,
    `compact_if_due` срезает его фронт (`state["hot"] = state["hot"][fold:]`), а склейка
    раннера `_turns_to_dialogue` сшивает подряд идущие реплики одного автора в один блок
    и ts/source_id не доносит вовсе (замер: 125 горячих записей → 107 блоков истории).
    Позиционный fold_count после любого из этих сдвигов указывал на другие реплики.
    """

    def _capture(self, i=0, history=None):
        return self._shadow(i, history=history)

    def _latest_shadow(self) -> str:
        return max(self._stream_dir().glob("*.md")).read_text(encoding="utf-8")

    def _folded(self, history):
        """Довести поток до свёрнутого состояния и вернуть метрики границы."""
        self._capture(0, history=history)
        return self._capture(1, history=history)

    def test_a_trimmed_front_no_longer_empties_the_accumulator(self):
        """Сценарий аудита P0-4: свёртка, потом свёртка места срезала фронт на 60.

        На позиционном счёте зона A пустела молча — она не видела разговор вовсе.
        """
        long = _many(150)
        boundary = self._folded(long)
        self.assertEqual(boundary["boundary"]["folded"], 100)
        self.assertEqual(boundary["a_window"]["kept"], 50)
        after = self._capture(2, history=long[60:])
        self.assertEqual(after["a_window"]["kept"], 50,
                         "накопитель опустел: позиционный fold_count указал за конец "
                         "срезанного окна — она не видит разговор вовсе")
        self.assertIn("м149 ", self._latest_shadow(),
                      "самая свежая реплика пропала из накопителя")
        self.assertIsNone(after["boundary"],
                          "срез фронта — не повод взводить границу")
        self.assertEqual(after["fold"], {"count": 40, "by": "якорь",
                                         "at": "2026-08-17 12:01 UTC"})

    def test_the_fold_follows_the_anchor_when_the_window_slides(self):
        """Окно едет вперёд: старое срезано, новое пришло. Свёрнутым остаётся ровно
        то же самое, а не «первые сто по счёту»."""
        long = _many(150)
        self._folded(long)
        moved = long[20:] + [{"role": "user", "content": "новое слово"}]
        after = self._capture(2, history=moved)
        self.assertEqual(after["a_window"]["kept"], 51,
                         "окно уехало, а счёт остался — свёрнутым назван чужой кусок")
        self.assertIn("новое слово", self._latest_shadow())
        self.assertEqual(after["fold"]["count"], 80, "якорь уехал вместе с окном")

    def test_a_lost_anchor_claims_no_count_and_says_so(self):
        """Якорь срезан вместе с фронтом. Значит всё оставшееся НОВЕЕ его: честный
        ответ — ноль, и обрубок про счёт больше ничего не утверждает."""
        long = _many(150)
        self._folded(long)
        after = self._capture(2, history=long[105:])
        self.assertEqual(after["a_window"]["kept"], 45,
                         "якоря нет в окне — значит свёрнуто ноль, а не «сто»")
        shadow = self._latest_shadow()
        self.assertIn("граница свёртки из окна ушла", shadow)
        self.assertIn("сколько свёрнуто, здесь не утверждается", shadow)
        self.assertEqual(after["fold"]["count"], 0)
        self.assertEqual(after["fold"]["by"], "якорь вне окна")

    def test_the_stub_carries_the_time_of_the_boundary_not_a_rotting_count(self):
        """Счёт свёрнутого меряется в блоках ПОСЛЕ склейки, а склейка пересобирается
        каждым ходом: замороженное число протухало бы молча. Время не протухает.

        Глубина ЖИВОГО хвоста, наоборот, считается здесь и сейчас — её число в строке
        стоит по праву (требование сходимости окна)."""
        self._folded(_many(150))
        saved = json.loads((self._stream_dir() / "epoch.json").read_text(
            encoding="utf-8"))
        self.assertIn("окно свёрнуто кодом · до 2026-08-17 12:01 UTC",
                      saved["fold_line"])
        self.assertIn("НЕ мой отбор", saved["fold_line"])
        self.assertIsNone(re.search(r"\d+ сообщени", saved["fold_line"]),
                          "обрубок снова утверждает счёт, который некому проверить")

    def test_the_anchor_is_a_chain_so_a_repeated_line_cannot_move_it(self):
        """Одного отпечатка мало: «да.» повторяется. Цепочка из трёх — нет."""
        dup = _many(150)
        dup[141] = dict(dup[99])  # та же роль, тот же текст — двойник в живом хвосте
        self._folded(dup)
        after = self._capture(2, history=dup)
        self.assertEqual(after["a_window"]["kept"], 50,
                         "свёртка прыгнула на двойника и спрятала живые реплики")
        self.assertEqual(after["fold"]["count"], 100)

    def test_a_v2_epoch_grafts_the_anchor_without_a_boundary(self):
        """Снимок, несущий ТОЛЬКО число свёртки (без якоря), приживляется к отпечаткам
        БЕЗ границы и без единого нового байта в E.

        Схему здесь не трогаем нарочно: снимки прежней схемы (v2) волна 20.08 уводит
        своей единственной именованной границей миграции, и это проверяется отдельно
        (test_v1_epoch_migrates_with_a_named_boundary). Приживление — это про снимок
        УЖЕ текущей схемы, у которого якоря почему-то нет."""
        long = _many(150)
        self._folded(long)
        path = self._stream_dir() / "epoch.json"
        numbered = json.loads(path.read_text(encoding="utf-8"))
        numbered.pop("fold_anchor", None)
        path.write_text(json.dumps(numbered, ensure_ascii=False, indent=1),
                        encoding="utf-8", newline="\n")
        after = self._capture(2, history=long)
        self.assertIsNone(after["boundary"], "приживление якоря — не граница эпохи")
        self.assertEqual(after["epoch"], 2)
        self.assertEqual(after["a_window"]["kept"], 50)
        self.assertEqual(after["fold"]["count"], 100)
        self.assertEqual(after["fold"]["by"], "якорь приживлён")
        grafted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(grafted["v"], frame_shadow.FRAME_SCHEMA)
        self.assertEqual(len(grafted["fold_anchor"]["chain"]),
                         frame_shadow.FOLD_ANCHOR_CHAIN)
        self.assertEqual(grafted["e_text"], numbered["e_text"],
                         "миграция тронула замороженную эпоху")
        self.assertTrue(after["lcp"]["held"],
                        f"миграция сломала байтовый префикс: {after['lcp']}")
        # И приживлённый якорь работает как настоящий: срез фронта больше не пустошит A.
        trimmed = self._capture(3, history=long[60:])
        self.assertEqual(trimmed["a_window"]["kept"], 50)
        self.assertEqual(trimmed["fold"], {"count": 40, "by": "якорь",
                                           "at": "2026-08-17 12:02 UTC"})

    def test_the_anchor_never_reaches_the_frame_and_the_build_stays_deterministic(self):
        """Якорь — служебная запись эпохи: в кадр едут только строка обрубка и реплики.
        Две сборки на одних входах — байт в байт (инвариант шага 1)."""
        long = _many(150)
        self._folded(long)
        saved = json.loads((self._stream_dir() / "epoch.json").read_text(
            encoding="utf-8"))
        shadow = self._latest_shadow()
        for sha in (saved.get("fold_anchor") or {}).get("chain") or ["якоря нет"]:
            self.assertNotIn(sha, shadow, "отпечаток якоря просочился в кадр")
        plan = frame_shadow.Plan(epoch_n=2, e_text="Э", fold_count=100,
                                 fold_line=saved["fold_line"])
        first = frame_shadow.build(
            ctx=_ctx(), history=long, speaker="Егор", user_msg="как ты?",
            tools=[], now=NOW, plan=plan)
        second = frame_shadow.build(
            ctx=_ctx(), history=long, speaker="Егор", user_msg="как ты?",
            tools=[], now=NOW, plan=plan)
        self.assertEqual(first.text, second.text)


class TheEpochHasACeiling(ShadowCase):
    """Правка 20.08: у E есть реальный enforced потолок и детерминированная
    лестница деградации. До неё числа 40000 в модуле не было вовсе, а пер-блочные
    потолки мерили ТЕЛО: живьём lifted 15579 при 15000 и recent 5214 при 5000 —
    обвязка шла мимо счёта, и admission считал не те величины, что блоки."""

    # Указатель памяти растёт длиной имён, а не числом файлов: так надувается тот же
    # блок, а гейт не платит за тысячи мелких файлов.
    #
    # ⚠ Длина имени упирается в ФАЙЛОВУЮ СИСТЕМУ, а не во вкус: на ext4 предел имени
    # 255 БАЙТ, а кириллица весит два байта на знак. Двенадцать повторов (176 знаков,
    # ~358 байт) проходили на Windows и падали OSError [Errno 36] в линуксовом
    # контейнере, где гейт и живёт. Восемь повторов дают 120 знаков / 239 байт с
    # суффиксом — и число файлов поднято, чтобы блок надувался как прежде.
    LONG = "человек-" + "-длинноимённый" * 8

    def _inflate(self, *, people=0, dossier=0, rooms=0, sends=0):
        ppl = self.base / "memory" / "people"
        for i in range(people):
            (ppl / f"{self.LONG}-{i:04d}.md").write_text("никого", encoding="utf-8")
        if dossier:
            (ppl / "собеседник.md").write_text(
                "telegram_id: 101\n" + "строка досье. " * (dossier // 14),
                encoding="utf-8")
        if rooms:
            rm = self.base / "memory" / "rooms"
            rm.mkdir(parents=True, exist_ok=True)
            for i in range(rooms):
                (rm / f"-100{i:04d}.md").write_text(
                    f"# Комната номер {i} с достаточно длинным именем\n"
                    "mode: normal\n\nтело\n", encoding="utf-8")
        if sends:
            ent = self.base / "memory" / ".state" / "telegram_outbox" / "entries"
            ent.mkdir(parents=True, exist_ok=True)
            at = (NOW - timedelta(hours=1)).isoformat()
            for i in range(sends):
                rows = [{"kind": "intent", "at": at,
                         "data": {"delivery": {"peer_id": -1000000 - i}}},
                        {"kind": "accepted", "at": at,
                         "data": {"message_id": 20000 + i}}]
                (ent / f"outbox-{i:04d}.jsonl").write_text(
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")

    def _tools(self, n):
        return [{"name": f"рука_номер_{i:03d}",
                 "description": "Делает нечто полезное и описывает это длинной строкой, "
                                "которую указатель режет на сотне знаков."}
                for i in range(n)]

    def _capture(self, hands=1):
        return self._shadow(0, tools=self._tools(hands), user_msg="х")

    def _blocks(self) -> dict:
        return json.loads((self._stream_dir() / "epoch.json").read_text(
            encoding="utf-8"))["blocks"]

    def _overflowing(self):
        """Заведомый перерасход: указатель памяти + досье + руки + места + отправки."""
        self._inflate(people=250, dossier=frame_shadow.E_LIFT_MAX * 2,
                      rooms=60, sends=140)
        return self._capture(hands=100)

    def test_block_ceilings_measure_the_finished_block_not_the_body(self):
        """Потолок блока обязан считать обвязку — иначе он врёт на её величину."""
        self._inflate(dossier=frame_shadow.E_LIFT_MAX * 2, rooms=60, sends=140)
        metrics = self._capture()
        sizes = metrics["e_blocks"]
        self.assertLessEqual(sizes["lifted"], frame_shadow.E_LIFT_MAX, sizes)
        self.assertLessEqual(sizes["recent"], frame_shadow.E_RECENT_MAX, sizes)
        self.assertLessEqual(sizes["address_book"], frame_shadow.E_BOOK_MAX, sizes)
        self.assertEqual(metrics["e_overflow"]["degraded"], [],
                         "лестница сработала там, где хватило пер-блочных потолков")

    def test_the_ladder_holds_the_hard_ceiling_and_names_every_cut(self):
        """Каждый срез — с явным указателем и числом, каждая ступень — поимённо."""
        metrics = self._overflowing()
        total = sum(metrics["e_blocks"].values())
        self.assertLessEqual(total, frame_shadow.E_TOTAL_MAX, metrics["e_blocks"])
        self.assertEqual(metrics["e_overflow"]["total"], total)
        self.assertEqual(metrics["e_overflow"]["over"], 0)
        names = [row["block"] for row in metrics["e_overflow"]["degraded"]]
        self.assertGreater(len(names), 1, f"лестница остановилась на первой: {names}")
        self.assertEqual(names, [n for n in frame_shadow.E_DEGRADE_ORDER if n in names],
                         f"порядок лестницы поехал: {names}")
        blocks = self._blocks()
        for row in metrics["e_overflow"]["degraded"]:
            self.assertLess(row["стало"], row["было"], row)
            marks = [l for l in blocks[row["block"]].splitlines()
                     if l.startswith(("… ещё", "[обрезано", "[описания"))]
            self.assertTrue(marks, f"срез «{row['block']}» молчит о себе")
            self.assertTrue(any(re.search(r"\d", m) for m in marks),
                            f"указатель «{row['block']}» без числа: {marks}")

    def test_her_two_blocks_are_never_degraded(self):
        """Её слово: полнота указателя памяти бьёт бюджет (обрезанный список имён —
        неявный белый список, корень конфабуляции 13.08); «кто я сейчас» — ядро."""
        metrics = self._overflowing()
        names = [row["block"] for row in metrics["e_overflow"]["degraded"]]
        self.assertTrue(names)
        self.assertNotIn("memory_index", names)
        self.assertNotIn("self", names)
        self.assertNotIn("memory_index", frame_shadow.E_DEGRADE_ORDER)
        self.assertNotIn("self", frame_shadow.E_DEGRADE_ORDER)
        self.assertIn(f"{self.LONG}-0249", self._blocks()["memory_index"],
                      "указатель памяти обрезан — это неявный белый список")

    def test_hand_names_survive_when_descriptions_are_dropped(self):
        """Снять описание руки — сузить подсказку; снять имя — соврать о себе."""
        metrics = self._overflowing()
        hands = self._blocks()["hands"]
        if "hands" in [r["block"] for r in metrics["e_overflow"]["degraded"]]:
            self.assertIn("[описания", hands)
            self.assertNotIn(" — Делает нечто", hands)
        self.assertIn("- рука_номер_099", hands,
                      "имя руки исчезло — это ложь о собственных способностях")

    def test_an_incompressible_epoch_freezes_anyway_and_shouts(self):
        """Указатель памяти без капа — её слово, и он в пределе один перерастает
        потолок. Тогда: заморозить ВСЁ РАВНО, крикнуть в лог, назвать перерасход."""
        self._inflate(people=590, dossier=frame_shadow.E_LIFT_MAX * 2,
                      rooms=60, sends=140)
        with self.assertLogs("praxis.frame_shadow", level="ERROR") as caught:
            metrics = self._capture(hands=100)
        self.assertTrue(any("выше потолка" in line for line in caught.output),
                        caught.output)
        self.assertGreater(metrics["e_overflow"]["over"], 0)
        self.assertGreater(metrics["e_overflow"]["total"], frame_shadow.E_TOTAL_MAX)
        self.assertTrue((self._stream_dir() / "epoch.json").exists(),
                        "отказ заморозить дороже перерасхода: номер эпохи откатится")
        self.assertEqual([r["block"] for r in metrics["e_overflow"]["degraded"]],
                         list(frame_shadow.E_DEGRADE_ORDER),
                         "перед отказом обязаны быть пройдены ВСЕ ступени")

    def test_the_ceiling_is_byte_deterministic(self):
        """Два прогона на одних входах — байт-в-байт: лестница детерминирована."""
        import shutil
        self._overflowing()
        first = (self._stream_dir() / "epoch.json").read_bytes()
        shutil.rmtree(self._stream_dir())
        self._capture(hands=100)
        self.assertEqual(first, (self._stream_dir() / "epoch.json").read_bytes())

    def test_living_sizes_do_not_trip_the_ladder(self):
        """Пин на прод-числа эпохи 2 (19.08) с починенными пер-блочными потолками:
        лестница обязана быть тождеством, а мягкая цель — не превышена."""
        # `hands` перемерен 20.08 живьём ПОСЛЕ камерного потолка: тот же набор в
        # 101 руку давал 11 470 зн. и даёт 5 987. Пин обязан ехать за реальностью,
        # иначе он сторожит уже несуществующий кадр.
        live = {"self": 2935, "hands": 5987, "memory_index": 1468,
                "address_book": 1762, "recent": 4967, "lifted": 15000}
        blocks = {name: "я" * size for name, size in live.items()}
        before = dict(blocks)
        touched = []
        degraded = frame_shadow._apply_e_ceiling(
            blocks, rebuild=lambda name, target: touched.append(name))
        self.assertEqual(degraded, [])
        self.assertEqual(touched, [], "лестница тронула блоки живого размера")
        self.assertEqual(blocks, before)
        self.assertLessEqual(sum(live.values()), frame_shadow.E_TOTAL_TARGET)

    def test_over_target_is_a_signal_not_an_action(self):
        """Мягкая цель сигналит и не режет: между целью и потолком блоки нетронуты."""
        self._inflate(dossier=frame_shadow.E_LIFT_MAX * 2, rooms=60, sends=140)
        with patch.object(frame_shadow, "E_TOTAL_TARGET", 10_000):
            metrics = self._capture(hands=60)
            total = sum(metrics["e_blocks"].values())
            self.assertGreater(total, frame_shadow.E_TOTAL_TARGET)
            self.assertEqual(metrics["e_over_target"],
                             total - frame_shadow.E_TOTAL_TARGET)
        self.assertLessEqual(total, frame_shadow.E_TOTAL_MAX)
        self.assertEqual(metrics["e_overflow"]["degraded"], [],
                         "мягкая цель порезала блоки — она обязана только сигналить")


class TheHandPointerFitsHerChamberSet(ShadowCase):
    """Её слово 18.08, п.1: «срезать указатель рук до камерного набора ≤6k;
    растягивать его нельзя». Живьём блок весил 11 470 знаков на 101 руку — почти
    вдвое выше её числа, и это было не наше умолчание, а неисполненное её решение.

    Режется УКАЗАТЕЛЬ, не набор: состав камерного набора — её слово (проект §3.2),
    и ни одна ступень здесь не убирает руку из кадра. Имя не сокращается никогда:
    имя руки это способность, и спрятать имя значит соврать о себе."""

    LONG = ("Делает нечто полезное и описывает это длинной строкой, "
            "которую указатель режет на сотне знаков.")

    def _tools(self, n, desc=None):
        return [{"name": f"рука_номер_{i:03d}",
                 "description": self.LONG if desc is None else desc}
                for i in range(n)]

    def _hands(self, tools):
        meta = {}
        return frame_shadow._block_hands(tools, meta), meta

    def test_the_pointer_never_exceeds_her_ceiling(self):
        """Потолок держится на любом наборе, где имена вообще в него помещаются."""
        for n in (1, 12, 101, 300):
            text, meta = self._hands(self._tools(n))
            self.assertLessEqual(len(text), frame_shadow.HANDS_MAX,
                                 f"{n} рук: указатель {len(text)} зн.")
            self.assertEqual(meta["chars"], len(text))
            self.assertEqual(meta["over"], 0)

    def test_no_hand_name_is_ever_lost(self):
        """На каждой ступени — все имена целиком: срезаются только описания."""
        stages = set()
        for n in (1, 101, 400):
            text, meta = self._hands(self._tools(n))
            stages.add(meta["stage"])
            for i in range(n):
                self.assertIn(f"- рука_номер_{i:03d}", text,
                              f"{n} рук, ступень «{meta['stage']}»: имя исчезло")
        self.assertGreater(len(stages), 1, f"ступени не разошлись: {stages}")

    def test_short_lines_survive_whole_and_long_ones_pay(self):
        """Порог — max-min: короткая подсказка доживает целой, платит длинная."""
        tools = self._tools(90)
        tools[0]["description"] = "Снять напоминание по id"
        tools[1]["description"] = ("Развёрнутое описание руки, в котором нет ни "
                                   "точки, ни двоеточия, ни скобки, поэтому "
                                   "головная фраза равна всей строке целиком")
        text, meta = self._hands(tools)
        self.assertEqual(meta["stage"], "порог")
        self.assertIn("- рука_номер_000 — Снять напоминание по id\n", text,
                      "короткое описание срезано вместе с длинными")
        long_line = [l for l in text.splitlines()
                     if l.startswith("- рука_номер_001")][0]
        self.assertTrue(long_line.endswith("…"), long_line)
        self.assertLessEqual(len(long_line.split(" — ", 1)[1]),
                             meta["trim_at"] + 1, long_line)

    def test_the_clause_cut_stops_at_the_end_of_a_thought(self):
        """Головная фраза — то, что руку называет, без перечисления подрежимов:
        режем по первому знаку конца мысли, а не по счётчику знаков."""
        cases = {
            "Запомнить факт о человеке. visibility='private' прячет тело":
                "Запомнить факт о человеке",
            "PASS 21, мои рычаги восприятия: list — рычаги, set — правка":
                "PASS 21, мои рычаги восприятия",
            "Поискать в собственной памяти и навыках (и в чужих, если открыты)":
                "Поискать в собственной памяти и навыках",
            "Ответить собеседнику этого разговора":
                "Ответить собеседнику этого разговора",
        }
        for line, head in cases.items():
            self.assertEqual(frame_shadow._hand_clause(line), head)

    def test_the_trim_stops_on_a_word_boundary(self):
        """Обрубок посреди слова — это байты, а не смысл. Исключение названо: если
        ближайший пробел слишком далеко, режем по счётчику, иначе слово-гигант
        съело бы всю подсказку."""
        line = "Ответить собеседнику этого разговора вежливо и по делу"
        for limit in range(12, len(line)):
            cut = frame_shadow._hand_trim(line, limit)
            self.assertLessEqual(len(cut), limit + 1, (limit, cut))
            if not cut.endswith("…"):
                self.assertEqual(cut, line)
                continue
            body = cut[:-1]
            self.assertTrue(line.startswith(body), (limit, cut))
            rest = line[len(body):]
            self.assertTrue(rest.startswith(" "), (limit, cut))
        giant = "A" * 200
        self.assertEqual(frame_shadow._hand_trim(giant, 20), "A" * 20 + "…")

    def test_the_cut_is_named_with_a_number_and_disowned(self):
        """Каждый срез — видимой строкой с числом и с прямой оговоркой, что отбор
        не её: молчаливое сокращение подсказки читается как её выбор."""
        text, meta = self._hands(self._tools(101))
        marks = [l for l in text.splitlines() if l.startswith("[описания")]
        self.assertEqual(len(marks), 1, text.splitlines()[-3:])
        self.assertIn("это НЕ мой отбор", marks[0])
        self.assertIn(str(frame_shadow.HANDS_MAX), marks[0])
        self.assertRegex(marks[0], r"−\d+ зн\.")
        self.assertIn("тела схем — по требованию", marks[0])

    def test_a_set_that_fits_keeps_its_full_lines_untouched(self):
        """Потолок не переписывает то, что и так влезает: иначе каждый маленький
        набор платил бы срезом ни за что, а байты кадра менялись бы без причины."""
        text, meta = self._hands(self._tools(12))
        self.assertEqual(meta["stage"], "полный")
        self.assertNotIn("[описания", text)
        cap = frame_shadow.POINTER_HAND_LINE
        whole = (self.LONG if len(self.LONG) <= cap
                 else self.LONG[:cap - 1] + "…")
        self.assertIn(f"- рука_номер_000 — {whole}\n", text)

    def test_names_above_the_ceiling_stay_whole_and_shout(self):
        """Вырожденный случай: одни имена выше потолка. Имя не сокращается даже
        здесь — превышение честно видно числом и кричит в лог."""
        tools = [{"name": "рука_" + "длинноимённая_" * 4 + f"{i:03d}",
                  "description": "описание"} for i in range(120)]
        with self.assertLogs("praxis.frame_shadow", level="ERROR") as caught:
            text, meta = self._hands(tools)
        self.assertTrue(any("камерного набора" in l for l in caught.output),
                        caught.output)
        self.assertEqual(meta["stage"], "имена")
        self.assertGreater(meta["over"], 0)
        self.assertEqual(meta["over"], len(text) - frame_shadow.HANDS_MAX)
        for t in tools:
            self.assertIn(f"- {t['name']}", text)

    def test_the_measure_separates_useful_bytes_from_bloat(self):
        """Её условие: «отдельно измерять полезные байты и раздувание каждого
        блока — укладывание в потолок само по себе не успех»."""
        text, meta = self._hands(self._tools(101))
        self.assertEqual(meta["tools"], 101)
        self.assertEqual(meta["cap"], frame_shadow.HANDS_MAX)
        self.assertGreater(meta["full_chars"], meta["chars"],
                           "раздувание не измерено: без него срез непроверяем")
        self.assertGreater(meta["names_chars"], 0)
        self.assertGreater(meta["desc_chars"], 0)
        self.assertLess(meta["names_chars"], meta["chars"])

    def test_the_pointer_is_byte_deterministic(self):
        """Один вход — одни байты: иначе набор рук давал бы новую эпоху сам собой."""
        for n in (101, 400):
            first = self._hands(self._tools(n))[0]
            self.assertEqual(first, self._hands(self._tools(n))[0])

    def test_the_measure_rides_into_the_metrics_line(self):
        """Мера едет в журнал рядом с размерами блока и сходится с ними."""
        metrics = self._shadow(0, tools=self._tools(101), user_msg="х")
        pointer = metrics["hands_pointer"]
        self.assertEqual(pointer["chars"], metrics["e_blocks"]["hands"])
        self.assertLessEqual(metrics["e_blocks"]["hands"], frame_shadow.HANDS_MAX)
        self.assertEqual(pointer["tools"], 101)
        self.assertIn(pointer["stage"], ("полный", "фразы", "порог", "имена"))

    def test_the_e_ladder_still_owns_the_last_word(self):
        """Камерный потолок не отменяет лестницу E: если эпоха всё равно не влезла,
        описания снимаются целиком, а имена и там остаются."""
        text = frame_shadow._block_hands(self._tools(101))
        stripped = frame_shadow._strip_hand_lines(text)
        self.assertLess(len(stripped), len(text))
        self.assertIn("- рука_номер_100", stripped)
        self.assertNotIn(" — Делает нечто", stripped)


class TheEpochIsScopedToItsAudience(ShadowCase):
    """Правка 20.08 (аудит 19.08): E собирается ПОД АУДИТОРИЮ потока.

    Живой артефакт тени показывал 12 комнат с id, 46 имён людей и события всех мест
    в эпохе ЛЮБОГО потока — включая чужую личку. `presence_hidden` при этом лишь
    аннотировал строку: та же строка, которой обещано «существование не подтверждается
    вне owner-потоков», это существование и подтверждала — id-ом, вслух.
    """

    def setUp(self):
        super().setUp()
        rooms = self.base / "memory" / "rooms"
        rooms.mkdir(parents=True)
        (rooms / "101.md").write_text("# Личка Егора\n\nтело\n", encoding="utf-8")
        (rooms / "202.md").write_text("# Личка гостя\n\nтело\n", encoding="utf-8")
        (rooms / "-100500.md").write_text("# Общая группа\nmode: normal\n\nтело\n",
                                          encoding="utf-8")
        (rooms / "-100600.md").write_text(
            "# Скрытная\nmode: normal\npresence_hidden: yes\n\nтело\n",
            encoding="utf-8")
        people = self.base / "memory" / "people"
        (people / "гость.md").write_text("# Гость\ntelegram_id: 202\n",
                                         encoding="utf-8")
        (people / "третий-человек.md").write_text("# Третий\ntelegram_id: 303\n",
                                                  encoding="utf-8")
        entries = self.base / "memory" / ".state" / "telegram_outbox" / "entries"
        entries.mkdir(parents=True)
        at = (NOW - timedelta(hours=2)).isoformat()
        for peer in (101, 202, -100500, -100600):
            rows = [{"kind": "intent", "at": at,
                     "data": {"delivery": {"peer_id": peer}}},
                    {"kind": "accepted", "at": at, "data": {"message_id": abs(peer)}}]
            (entries / f"outbox-{abs(peer)}.jsonl").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                encoding="utf-8")

    def _capture(self, ctx, i=0, history=None):
        return self._shadow(i, ctx=ctx, history=history, speaker="кто-то")

    def _frozen(self, stream: str) -> dict:
        path = self.base / "memory" / ".state" / "shadow" / stream / "epoch.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _book_rows(e_text: str) -> list[str]:
        return [l for l in e_text.splitlines()
                if l.startswith("- ") and (" личка " in l or " группа " in l)]

    def test_a_strangers_dm_carries_only_its_own_place(self):
        """Не аннотация — отсутствие: в чужой личке в книге ровно одно место, это."""
        self._capture(_ctx(chat_id=202, owner_audience=False))
        e_text = self._frozen("dm-202")["e_text"]
        rows = self._book_rows(e_text)
        self.assertEqual(len(rows), 1, f"чужая личка несёт чужие места: {rows}")
        self.assertIn("личка 202", rows[0])
        for foreign in ("-100500", "Общая группа", "Личка Егора"):
            self.assertNotIn(foreign, e_text, f"в чужую личку уехало «{foreign}»")

    def test_a_hidden_room_leaves_no_byte_in_a_strangers_stream(self):
        """presence_hidden обязан быть отсутствием, а не пометкой: ни id, ни названия,
        ни самой пометки (её текст — тоже подтверждение, что скрывать есть что)."""
        self._capture(_ctx(chat_id=202, owner_audience=False))
        e_text = self._frozen("dm-202")["e_text"]
        for byte_of_it in ("-100600", "100600", "Скрытная",
                           "существование не подтверждается"):
            self.assertNotIn(byte_of_it, e_text)

    def test_the_owner_stream_keeps_the_whole_book_with_its_marks(self):
        """Сужение — только вниз: у Егора книга остаётся полной, с метками и умолчаниями."""
        self._capture(_ctx(chat_id=101, owner_audience=True))
        e_text = self._frozen("dm-101")["e_text"]
        rows = self._book_rows(e_text)
        self.assertEqual(len(rows), 4, f"книга владельца поредела: {rows}")
        self.assertIn("существование не подтверждается", e_text)
        self.assertIn("список мест полный", e_text)

    def test_the_index_of_people_is_not_a_roster_for_a_stranger(self):
        """Указатель памяти — это перечень ИМЁН живых людей. В чужой личке остаётся
        имя самого собеседника (привязка по свидетелю telegram_id) и названная граница
        вместо остальных: число «ещё N» — тоже сведение о третьих людях."""
        self._capture(_ctx(chat_id=202, owner_audience=False))
        e_text = self._frozen("dm-202")["e_text"]
        self.assertIn("люди: гость · остальные имена не приводятся", e_text)
        self.assertNotIn("третий-человек", e_text)
        self.assertNotIn("yegor", e_text)
        self.assertNotIn("список полный", e_text)
        self.assertIn("не приводятся вне owner-потока — ни списком, ни числом", e_text)
        # Навыки — мои способности, а не чужие данные: остаются в обеих аудиториях.
        self.assertIn("тела: не загружены", e_text)

    def test_recent_in_a_strangers_dm_names_only_this_place(self):
        """Структурность записи не спасает: «место -100500: сказала» называет чужую
        комнату id-ом ровно так же, как это сделала бы проза."""
        self._capture(_ctx(chat_id=202, owner_audience=False))
        e_text = self._frozen("dm-202")["e_text"]
        self.assertIn("сказала (#202)", e_text)
        for foreign in ("(#101)", "(#100500)", "(#100600)"):
            self.assertNotIn(foreign, e_text)
        self.assertIn("вне owner-потока — только это место", e_text)

    def test_the_lifted_body_drops_private_lines_for_a_stranger(self):
        """Механический пол живого кадра (agent._people_index) обязан стоять и в тени:
        досье собеседника едет ему же, но строки [private] — это её заметки о нём."""
        (self.base / "memory" / "people" / "гость.md").write_text(
            "# Гость\ntelegram_id: 202\nоткрытая строка\n- [private] моя оценка\n",
            encoding="utf-8")
        out = self._capture(_ctx(chat_id=202, owner_audience=False))
        e_text = self._frozen("dm-202")["e_text"]
        self.assertIn("открытая строка", e_text)
        self.assertNotIn("моя оценка", e_text)
        self.assertIn("снято строк с пометкой [private]: 1", e_text)
        self.assertIn("sha256 ", e_text, "обрезка обязана оставаться сверяемой")
        self.assertEqual(out["lifted"][0]["private_hidden"], 1)

    def test_the_ladder_cuts_a_body_that_privacy_has_already_filtered(self):
        """Порядок двух ножей: приватность режет ПЕРВОЙ, потолок — по уже
        отфильтрованному. Иначе на полу лестницы в кадр уехало бы ровно то, что
        аудитория сняла: срез по сырым байтам не знает, где кончилось её приватное."""
        secret = "\n".join(f"- [private] тайна {i}" for i in range(200))
        (self.base / "memory" / "people" / "гость.md").write_text(
            "# Гость\ntelegram_id: 202\n" + secret + "\nоткрытый хвост\n",
            encoding="utf-8")
        header, src = frame_shadow._lifted_source(
            _ctx(chat_id=202, owner_audience=False), "other")
        self.assertEqual(src["private_hidden"], 200)
        self.assertNotIn("тайна", src["body"])
        block, meta = frame_shadow._render_lifted(header, src,
                                                  frame_shadow.E_LIFT_FLOOR)
        self.assertLessEqual(len(block), frame_shadow.E_LIFT_FLOOR)
        self.assertNotIn("тайна", block, "лестница резала СЫРОЕ тело, а не снятое")
        self.assertIn("снято строк с пометкой [private]: 200", block)
        self.assertEqual(meta[0]["private_hidden"], 200)

    def test_a_group_does_not_jump_when_a_different_person_speaks(self):
        """Аудитория внутри потока не гуляет ПО ПОСТРОЕНИЮ: `owner_audience` требует
        is_dm, а is_dm вшит в ключ потока. Владелец, пишущий в группе, — сырой `owner`,
        и аудиторию owner он там не создаёт."""
        speaks_owner = SimpleNamespace(chat_id=-100500, is_dm=False, owner=True,
                                       owner_audience=False)
        speaks_guest = SimpleNamespace(chat_id=-100500, is_dm=False, owner=False,
                                       owner_audience=False)
        self.assertEqual(frame_shadow._audience(speaks_owner), "other")
        self.assertEqual(frame_shadow._audience(speaks_guest), "other")
        first = self._capture(speaks_owner, 0)
        before = self._frozen("chat--100500")["e_text"]
        second = self._capture(speaks_guest, 1)
        self.assertIsNone(second["boundary"], "смена говорящего стала границей эпохи")
        self.assertIsNone(second["e_drift"], "E поехала за говорящим")
        self.assertEqual(before, self._frozen("chat--100500")["e_text"])
        self.assertEqual(first["stream"], second["stream"])

    def test_a_wider_audience_does_not_widen_a_frozen_epoch(self):
        """Храповик односторонний. Расширение обратно не происходит само — иначе один
        ход с owner-аудиторией переписал бы узкую эпоху широкой, и E прыгнула бы."""
        stranger = _ctx(chat_id=202, owner_audience=False)
        self._capture(stranger, 0)
        narrow = self._frozen("dm-202")["e_text"]
        second = self._capture(_ctx(chat_id=202, owner_audience=True), 1)
        self.assertIsNone(second["boundary"])
        self.assertIsNone(second["e_drift"])
        self.assertEqual(narrow, self._frozen("dm-202")["e_text"])
        self.assertEqual(second["audience"], "other")

    def test_an_epoch_frozen_wider_than_the_stream_is_refrozen(self):
        """Снимок без ключа аудитории собран полной книгой. Читаем его как `owner`:
        в чужом потоке это сужение, то есть граница, а в owner-потоке — ничего."""
        self._capture(_ctx(chat_id=202, owner_audience=True), 0)
        path = (self.base / "memory" / ".state" / "shadow" / "dm-202" / "epoch.json")
        legacy = json.loads(path.read_text(encoding="utf-8"))
        legacy.pop("audience")
        path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        out = self._capture(_ctx(chat_id=202, owner_audience=False), 1)
        self.assertIsNotNone(out["boundary"], "широкий снапшот дожил до чужого хода")
        self.assertIn("сужение аудитории потока", out["boundary"]["reason"])
        self.assertEqual(out["epoch"], 2)
        self.assertEqual(out["audience"], "other")
        self.assertEqual(len(self._book_rows(self._frozen("dm-202")["e_text"])), 1)

    def test_her_flip_is_the_only_way_back_to_the_full_book(self):
        """Расширение возвращает её рука, и только она (её слово — граница по праву)."""
        self._capture(_ctx(chat_id=101, owner_audience=False), 0)
        self.assertEqual(len(self._book_rows(self._frozen("dm-101")["e_text"])), 1)
        frame_shadow.flip_epoch(_ctx(chat_id=101))
        self._capture(_ctx(chat_id=101, owner_audience=True), 1)
        self.assertEqual(len(self._book_rows(self._frozen("dm-101")["e_text"])), 4)

    def test_a_missing_field_reads_as_a_stranger(self):
        """Умолчание прибора закрывающее: канал без `owner_audience` — не owner."""
        self.assertEqual(frame_shadow._audience(
            SimpleNamespace(chat_id=202, is_dm=True)), "other")
        self.assertEqual(frame_shadow._audience(SimpleNamespace()), "other")

    def test_every_signature_defaults_to_the_narrow_frame(self):
        """Забытый проброс обязан давать УЗКИЙ кадр, а не широкий: умолчание прибора
        закрывающее во ВСЕХ сигнатурах, а не только в той, где о нём вспомнили."""
        import inspect
        for name in ("_epoch_blocks", "_zone_e_current", "_freeze_epoch",
                     "_epoch_drift", "_block_address_book", "_block_recent",
                     "_block_memory_index", "_lifted_source"):
            sig = inspect.signature(getattr(frame_shadow, name))
            self.assertEqual(sig.parameters["audience"].default, "other",
                             f"{name}: умолчание аудитории раскрывающее")


class TheToolsetIsAnEpochBoundary(ShadowCase):
    """§2 контракта кадра: смена набора предложенных рук — всегда переворот эпохи.

    Схемы рук едут ВЫШЕ system и в `prompt_cache_key` не входят (`llm.cache_address`,
    пин test_cache_address.py) — без отпечатка флап набора убивает байтовый префикс
    молча, а замороженный блок «руки» врёт о руках хода.
    """

    DEFAULT_TOOLS = ({"name": "recall", "description": "Вспомнить."},)

    def _capture(self, i=0, tools=None, ctx=None, history=None):
        return self._shadow(i, tools=tools, ctx=ctx, history=history)

    def _saved(self) -> dict:
        return json.loads((self._stream_dir() / "epoch.json").read_text(
            encoding="utf-8"))

    def test_a_changed_toolset_turns_the_epoch_and_a_repeat_does_not(self):
        """Три захвата подряд: [A] · [A,B] · [A,B]. Граница ровно между вторым и
        первым — и ни одной лишней на повторе."""
        a = [{"name": "recall", "description": "Вспомнить."}]
        ab = a + [{"name": "task_control", "description": "Закрыть работу."}]
        first = self._capture(0, tools=a)
        self.assertEqual(first["epoch"], 1)
        second = self._capture(1, tools=ab)
        self.assertIsNotNone(second["boundary"], "смена набора рук не стала границей")
        self.assertEqual(second["boundary"]["reason"], "смена набора рук")
        self.assertEqual(second["epoch"], 2)
        third = self._capture(2, tools=ab)
        self.assertIsNone(third["boundary"],
                          "тот же набор породил вторую границу — эпоха на каждый ход")
        self.assertEqual(third["epoch"], 2)

    def test_the_frozen_hands_block_follows_the_new_set(self):
        """Граница нужна не ради счётчика: после неё блок «руки» обязан говорить
        о руках ЭТОГО хода, а не соседнего."""
        self._capture(0, tools=[{"name": "recall", "description": "Вспомнить."}])
        self._capture(1, tools=[{"name": "recall", "description": "Вспомнить."},
                                {"name": "task_control", "description": "Закрыть."}])
        saved = self._saved()
        self.assertIn("- task_control", saved["blocks"]["hands"])
        self.assertIn("граница: смена набора рук", saved["header"])

    def test_reordering_the_same_names_is_a_boundary_too(self):
        """Её слово 18.08: `end_turn` — последний. У Anthropic на последнем описании
        висит cache_control breakpoint, у OpenAI схемы едут выше system: реордер
        стоит ровно столько же, сколько смена состава, и обязан быть виден."""
        one = [{"name": "recall", "description": "Вспомнить."},
               {"name": "end_turn", "description": "Закрыть ход."}]
        self._capture(0, tools=one)
        out = self._capture(1, tools=list(reversed(one)))
        self.assertIsNotNone(out["boundary"], "реордер рук прошёл мимо отпечатка")
        self.assertEqual(out["boundary"]["reason"], "смена набора рук")

    def test_a_nameless_hosted_tool_is_named_by_its_type(self):
        """Hosted-поиск в OpenAI-форме приходит БЕЗ имени (agent.OPENAI_WEB_SEARCH_TOOL).
        Он не имеет права ни выпасть из отпечатка, ни исчезнуть из блока «руки»:
        это ровно та рука, которая появляется и пропадает переключением мозга."""
        bare = [{"name": "recall", "description": "Вспомнить."}]
        hosted = bare + [{"type": "web_search", "search_context_size": "medium"}]
        self.assertNotEqual(tool_offerings.fingerprint(bare),
                            tool_offerings.fingerprint(hosted))
        self._capture(0, tools=bare)
        out = self._capture(1, tools=hosted)
        self.assertIsNotNone(out["boundary"], "hosted-рука выпала из отпечатка")
        self.assertIn("- [web_search]", self._saved()["blocks"]["hands"])

    def test_an_epoch_frozen_before_the_fingerprint_gets_it_without_a_boundary(self):
        """Эпоха со старого выката отпечатка не несёт. Назвать это сменой набора —
        соврать о собственном возрасте прибора: доклеиваем молча, ровно один раз."""
        self._capture(0)
        path = self._stream_dir() / "epoch.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved.pop("tools_sha")
        path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        out = self._capture(1)
        self.assertIsNone(out["boundary"], "доклейка отпечатка выдана за смену набора")
        self.assertEqual(out["epoch"], 1)
        self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["tools_sha"])
        self.assertIsNone(self._capture(2)["boundary"], "доклейка повторилась")

    def test_the_digest_rides_in_the_metrics_line(self):
        """Атрибуция промаха префикса: отпечаток рядом с размерами, в том же виде,
        что и в телеметрии llm-вызова."""
        out = self._capture(0)
        self.assertEqual(
            out["tools_sha"],
            tool_offerings.fingerprint(
                [{"name": "recall", "description": "Вспомнить."}])[:16])


class TheRunKindSplitsTheNoChatStream(ShadowCase):
    """Замер 15–19.08 (memory/.state/turns.jsonl, 162 хода без чата за 4,64 суток):
    heartbeat · wake · task_window · forge_event делили ОДИН поток «no-chat», а
    `task_control` даётся по виду хода — состав рук чередовался 48 раз, то есть одна
    склейка стоила бы 10,3 переворота эпохи в сутки. Вид хода в ключе потока —
    и чередований внутри потока ноль."""

    def _run(self, kind: str):
        return run_context.RunContext.create(
            kind=kind, goal="проба вида хода", principal_id="probe", scope="owner")

    def test_a_run_kind_gives_the_stream_its_own_name(self):
        ctx = _ctx(chat_id=None)
        self.assertEqual(frame_shadow._stream_key(ctx), "no-chat")
        with run_context.bind_run(self._run("wake")):
            self.assertEqual(frame_shadow._stream_key(ctx), "no-chat-wake")
        with run_context.bind_run(self._run("coding_window")):
            self.assertEqual(frame_shadow._stream_key(ctx), "no-chat-coding_window")

    def test_a_chat_stream_key_is_untouched_by_the_run(self):
        """Разделение живёт РОВНО там, где у хода нет собеседника. У места ключ —
        место: иначе одна комната расползлась бы на потоки по видам ходов."""
        with run_context.bind_run(self._run("task_window")):
            self.assertEqual(frame_shadow._stream_key(_ctx(chat_id=101)), "dm-101")
            self.assertEqual(frame_shadow._stream_key(_ctx(chat_id=-100, dm=False)),
                             "chat--100")

    def test_two_kinds_no_longer_share_one_epoch(self):
        """Сценарий аудита 19.08: захват в пробуждении морозит эпоху без
        `task_control`, через час окно приходит с ним — и раньше это была одна эпоха,
        врущая о руках половины своих ходов."""
        ctx = _ctx(chat_id=None)
        wake_tools = [{"name": "recall", "description": "Вспомнить."}]
        window_tools = wake_tools + [{"name": "task_control",
                                      "description": "Закрыть работу."}]
        with patch.dict(os.environ, {"PRAXIS_FRAME_SHADOW": "on"}):
            with run_context.bind_run(self._run("wake")):
                frame_shadow.capture(ctx=ctx, history=_history(), speaker=None,
                                     user_msg="пробуждение", tools=wake_tools, now=NOW)
            with run_context.bind_run(self._run("task_window")):
                out = frame_shadow.capture(
                    ctx=ctx, history=_history(), speaker=None, user_msg="окно",
                    tools=window_tools, now=NOW + timedelta(hours=1))
        self.assertEqual(out["stream"], "no-chat-task_window")
        self.assertEqual(out["epoch"], 1, "чужой вид хода перевернул эпоху окна")
        self.assertIn("первая заморозка потока", out["boundary"]["reason"])
        root = self.base / "memory" / ".state" / "shadow"
        self.assertEqual(sorted(p.name for p in root.iterdir()),
                         ["no-chat-task_window", "no-chat-wake"])


class TheBuilderIsOneAndSaysWhoItIs(ShadowCase):
    """Правка 20.08: сборщик один, и у состояния сборки есть короткое имя из шести полей.

    Церемония приёмки (git-тег, аннотация, sha отчёта, длинный манифест) здесь нарочно
    не строится: личному проекту хватает строки идентификатора, рядом с которой
    записано её «да».
    """

    def setUp(self):
        super().setUp()
        # sha исходника кэшируется на процесс — тест подменяет __file__, и хвост от
        # него не имеет права уехать в соседние тесты.
        self.addCleanup(frame_shadow._serializer_sha.cache_clear)

    def _capture(self, i=0, history=None, tools=None):
        return self._shadow(i, history=history, tools=tools,
                            env={"PRAXIS_HEAD_SHA": "deadbee"})

    def test_the_captured_file_is_exactly_what_the_builder_returns(self):
        """Golden сегодняшнего дня: у тени два входа — capture и build — и оба обязаны
        дать один байт в байт файл. Это та же проверка, что станет проверкой switch,
        только вторым путём пока выступает не живой кадр, а прямой вызов сборщика."""
        metrics = self._capture(0)
        on_disk = (self._stream_dir() / metrics["file"]).read_bytes()
        plan = frame_shadow.Plan(
            epoch_n=metrics["epoch"],
            e_text=json.loads((self._stream_dir() / "epoch.json").read_text(
                encoding="utf-8"))["e_text"])
        direct = frame_shadow.build(
            ctx=_ctx(), history=_history(), speaker="Егор", user_msg="вход 0",
            tools=[{"name": "reply", "description": "Сказать."}], now=NOW, plan=plan)
        self.assertEqual(on_disk, direct.text.encode("utf-8"),
                         "два входа в один сборщик разошлись — значит сборок две")

    def test_prepare_and_build_split_the_io_from_the_bytes(self):
        """Контракт switch: диск трогает prepare, байты собирает build. Второй build
        на том же Plan обязан быть побайтно тем же — даже когда источники E уехали."""
        with patch.dict(os.environ, {"PRAXIS_FRAME_SHADOW": "on"}):
            plan = frame_shadow.prepare(ctx=_ctx(), history=_history(),
                                        tools=[], now=NOW)
        first = frame_shadow.build(ctx=_ctx(), history=_history(), speaker="Егор",
                                   user_msg="х", tools=[], now=NOW, plan=plan)
        (self.base / "memory" / "people" / "arete.md").write_text("гость",
                                                                  encoding="utf-8")
        second = frame_shadow.build(ctx=_ctx(), history=_history(), speaker="Егор",
                                    user_msg="х", tools=[], now=NOW, plan=plan)
        self.assertEqual(first.text.encode("utf-8"), second.text.encode("utf-8"))
        self.assertEqual(first.e, plan.e_text, "E в кадр едет только из Plan")

    def test_the_identity_has_six_fields_and_holds_still(self):
        with patch.dict(os.environ, {"PRAXIS_HEAD_SHA": "deadbee"}):
            first = frame_shadow.identity(tools=[{"name": "reply"}],
                                          e_text="Э", epoch_n=2)
            second = frame_shadow.identity(tools=[{"name": "reply"}],
                                           e_text="Э", epoch_n=2)
        self.assertEqual(set(first), {"git", "schema", "serializer", "tools",
                                      "epoch", "golden"})
        self.assertEqual(first, second, "идентификатор обязан быть функцией входов")
        self.assertEqual(first["git"], "deadbee")
        self.assertEqual(first["schema"], frame_shadow.FRAME_SCHEMA)
        self.assertEqual(first["golden"], "shadow-only",
                         "пока живой путь собирает кадр сам, поле обязано это говорить")

    def test_the_identity_lands_in_the_metrics_line(self):
        row = self._capture(0)
        self.assertEqual(row["identity"]["git"], "deadbee")
        self.assertEqual(row["identity"]["epoch"].split(":")[0], str(row["epoch"]))
        self.assertIn("golden=shadow-only",
                      frame_shadow.identity_line(row["identity"]))
        on_disk = json.loads((self._stream_dir() / "metrics.jsonl").read_text(
            encoding="utf-8").splitlines()[0])
        self.assertEqual(on_disk["identity"], row["identity"])

    def test_a_reordered_hand_moves_the_tools_digest(self):
        """Состав и порядок рук у провайдера стоят в кэшируемом префиксе: молчаливый
        реордер — это «тающий лимит» через руки, и идентификатор обязан его назвать."""
        a = frame_shadow._tools_digest([{"name": "reply"}, {"name": "end_turn"}])
        b = frame_shadow._tools_digest([{"name": "end_turn"}, {"name": "reply"}])
        c = frame_shadow._tools_digest([{"name": "reply"}, {"name": "end_turn"}])
        self.assertNotEqual(a, b, "порядок рук не виден идентификатору")
        self.assertEqual(a, c)

    def test_the_head_override_is_read_every_time_not_cached(self):
        """Перекрытие, которое закэшировалось, ведёт себя по-разному первым и вторым
        вызовом — это уже не перекрытие, а лотерея."""
        with patch.dict(os.environ, {"PRAXIS_HEAD_SHA": "aaaaaaa"}):
            self.assertEqual(frame_shadow._git_sha(), "aaaaaaa")
        with patch.dict(os.environ, {"PRAXIS_HEAD_SHA": "bbbbbbb"}):
            self.assertEqual(frame_shadow._git_sha(), "bbbbbbb")

    def test_the_frozen_epoch_moves_the_epoch_digest(self):
        one = frame_shadow.identity(e_text="эпоха один", epoch_n=1)["epoch"]
        two = frame_shadow.identity(e_text="эпоха два", epoch_n=1)["epoch"]
        self.assertNotEqual(one, two, "подменённая эпоха обязана менять идентификатор")

    def test_one_changed_byte_in_the_builder_moves_the_identifier(self):
        """Самое дешёвое и самое важное свойство: если сборщик отредактировали, её «да»
        относилось к другому сборщику — и это видно ДО первого хода."""
        src = Path(frame_shadow.__file__).resolve().read_bytes()
        twin = self.base / "frame_shadow_twin.py"
        changed = src.replace(b"A_KEEP_TAIL = 50", b"A_KEEP_TAIL = 51", 1)
        self.assertNotEqual(src, changed, "фикстура правки байта промахнулась")
        twin.write_bytes(changed)
        with patch.dict(os.environ, {"PRAXIS_HEAD_SHA": "deadbee"}):
            frame_shadow._serializer_sha.cache_clear()
            before = frame_shadow.identity(tools=[], e_text="Э", epoch_n=1)
            with patch.object(frame_shadow, "__file__", str(twin)):
                frame_shadow._serializer_sha.cache_clear()
                after = frame_shadow.identity(tools=[], e_text="Э", epoch_n=1)
            frame_shadow._serializer_sha.cache_clear()
        self.assertNotEqual(before["serializer"], after["serializer"])
        self.assertEqual({k: v for k, v in before.items() if k != "serializer"},
                         {k: v for k, v in after.items() if k != "serializer"},
                         "поехало не то поле: сдвинулся один байт сборщика")

    @unittest.skip("заготовка switch: живой путь ещё не зовёт сборщик. Снять skip в том "
                   "же коммите, где _voice_impl начнёт строить system из "
                   "frame_shadow.prepare()/build(), и GOLDEN станет 'live==shadow'.")
    def test_the_live_frame_equals_the_shadow_byte_for_byte(self):
        """Обязательство на момент switch, дословно.

        На ОДНОЙ фикстуре (ctx, history, speaker, user_msg, tools, now) живой путь и
        тень обязаны дать один и тот же байтовый кадр — не «эквивалентный», а тот же,
        потому что источник байтов один:

            plan   = frame_shadow.prepare(ctx=…, history=…, tools=…, now=NOW)
            shadow = frame_shadow.build(…, plan=plan).text
            live   = agent.frame_for(…)      # шов, который появится при switch и
                                             # внутри зовёт ту же пару prepare+build
            assert live.encode("utf-8") == shadow.encode("utf-8")

        И вторым утверждением — что источник вправду один: identity() живого и
        теневого совпадает по всем шести полям, а поле golden читается «live==shadow».
        Пока этого шва нет, тест обязан стоять со skip и НЕ считаться зелёным: молчащая
        заготовка честнее зелёного теста, который ничего не сравнивает.
        """
        import agent
        tools = [{"name": "reply", "description": "Сказать."}]
        with patch.dict(os.environ, {"PRAXIS_FRAME_SHADOW": "on"}):
            plan = frame_shadow.prepare(ctx=_ctx(), history=_history(),
                                        tools=tools, now=NOW)
        shadow = frame_shadow.build(ctx=_ctx(), history=_history(), speaker="Егор",
                                    user_msg="как ты?", tools=tools, now=NOW, plan=plan)
        live = agent.frame_for(ctx=_ctx(), history=_history(), speaker="Егор",
                               user_msg="как ты?", tools=tools, now=NOW)
        self.assertEqual(live.encode("utf-8"), shadow.text.encode("utf-8"))
        self.assertEqual(frame_shadow.GOLDEN, "live==shadow",
                         "поле golden обязано смениться ровно этим коммитом")


class TheShadowNeverReachesTheModel(unittest.TestCase):
    """Пины на исходник: путь тени в кадр отрезан ПО ПОСТРОЕНИЮ, и это проверяемо."""

    def setUp(self):
        self.src = Path(frame_shadow.__file__).resolve().parent

    def test_llm_does_not_know_the_shadow_exists(self):
        self.assertNotIn("frame_shadow",
                         (self.src / "llm.py").read_text(encoding="utf-8"))

    def test_the_hook_is_gated_swallowed_and_ignored(self):
        agent_src = (self.src / "agent.py").read_text(encoding="utf-8")
        hook = "if frame_shadow.enabled():"
        self.assertIn(hook, agent_src, "крюк тени в _voice_impl исчез")
        block = agent_src.split(hook, 1)[1][:700]
        self.assertIn("frame_shadow.capture(", block)
        self.assertIn("except Exception:", block,
                      "ошибка прибора обязана гаситься на месте, а не ронять ход")
        self.assertIn("log.exception", block)
        self.assertNotIn("= frame_shadow.capture", agent_src,
                         "возврат тени никому не присваивается: у неё нет читателя "
                         "в пути кадра")


if __name__ == "__main__":
    unittest.main()
