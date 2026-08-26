"""
Реестр маршрутов (пункт 5, теневая половина).

Главное, что здесь проверяется: реестр не переписывает прошлое. Момент превращения
чата в форум из истории Telegram невосстановим — служебного сообщения об этом нет, —
поэтому всё до первого наблюдения обязано остаться `unknown`.

Запуск:  python praxis_test.py test_route_registry -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import telegram_routes as tr


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_routes_"))
        self._orig = tr.DIR
        tr.DIR = self.tmp / "memory" / ".state" / "group_context"

    def tearDown(self):
        tr.DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestEvidence(Base):
    def test_unknown_until_something_is_observed(self):
        self.assertEqual(tr.current("-100")["forum_status"], tr.UNKNOWN)
        self.assertEqual(tr.status_at("-100"), (tr.UNKNOWN, 0))

    def test_direct_telegram_answers_are_authoritative(self):
        tr.observe("-100", kind="channel_forum_missing", message_id=500)
        self.assertEqual(tr.current("-100")["forum_status"], tr.FALSE)
        tr.observe("-200", kind="get_forum_topics_ok", message_id=10)
        self.assertEqual(tr.current("-200")["forum_status"], tr.TRUE)

    def test_topic_opener_proves_a_real_forum(self):
        tr.observe("-300", kind="topic_opener_seen", message_id=7849,
                   detail="Открытые вопросы (швы)")
        self.assertEqual(tr.current("-300")["forum_status"], tr.TRUE)

    def test_weak_evidence_never_overrides_strong(self):
        """Объект с флагом min документирован как ненадёжный: он не имеет права
        отменять прямой ответ Telegram на GetForumTopics."""
        tr.observe("-100", kind="channel_forum_missing", message_id=1)
        tr.observe("-100", kind="update_min_entity", forum=True, message_id=2)
        self.assertEqual(tr.current("-100")["forum_status"], tr.FALSE)
        self.assertEqual(tr.current("-100")["epoch"], 1, "новой эпохи быть не должно")
        self.assertEqual(tr.current("-100")["contested"], 1, "но спор записан")

    def test_transient_failure_says_nothing(self):
        tr.observe("-100", kind="get_forum_topics_ok", message_id=1)
        tr.observe("-100", kind="rpc_unavailable", detail="FloodWaitError")
        self.assertEqual(tr.current("-100")["forum_status"], tr.TRUE,
                         "сеть упала — это не свидетельство о природе комнаты")

    def test_repeat_observation_does_not_open_an_epoch(self):
        for mid in (10, 20, 30):
            tr.observe("-100", kind="get_forum_topics_ok", message_id=mid)
        cur = tr.current("-100")
        self.assertEqual(cur["epoch"], 1)
        self.assertEqual(cur["observations"], 3)
        self.assertEqual(cur["until_message_id"], 30)


class TestGateIsDeterministic(Base):
    """⚠ Гейт зависел от ПОРЯДКА ЗАГРУЗКИ, и из-за этого слой B был мёртв в проде.

    Авторитетное свидетельство от Telegram писалось БЕЗ идентификатора сообщения,
    живое — с ним. Кто пришёл первым, тот и ставил границу эпохи; граница никогда не
    опускалась. Если первым было живое сообщение с высоким номером, все исторические
    ветки оказывались раньше границы и получали «не знаю».
    """

    HIST, LIVE = 93707, 93900

    def _live_then_backfill(self):
        tr.observe("-100", kind="entity_forum_flag", forum=False, message_id=self.LIVE)
        tr.observe("-100", kind="no_topic_openers_in_range",
                   since_message_id=89000, until_message_id=self.LIVE)

    def _backfill_then_live(self):
        tr.observe("-100", kind="no_topic_openers_in_range",
                   since_message_id=89000, until_message_id=self.LIVE)
        tr.observe("-100", kind="entity_forum_flag", forum=False, message_id=self.LIVE)

    def test_same_evidence_either_order_gives_the_same_answer(self):
        self._live_then_backfill()
        a = tr.status_at("-100", self.HIST)
        self.tearDown()
        self.setUp()
        self._backfill_then_live()
        b = tr.status_at("-100", self.HIST)
        self.assertEqual(a, b, "ответ гейта не имеет права зависеть от порядка загрузки")
        self.assertEqual(a[0], tr.FALSE, "историческая ветка обязана получить ответ")

    def test_evidence_at_an_earlier_id_widens_the_epoch_backwards(self):
        tr.observe("-100", kind="entity_forum_flag", forum=False, message_id=93900)
        self.assertEqual(tr.status_at("-100", 89500), (tr.UNKNOWN, 0))
        tr.observe("-100", kind="no_topic_openers_in_range",
                   since_message_id=89000, until_message_id=93900)
        self.assertEqual(tr.status_at("-100", 89500)[0], tr.FALSE,
                         "узнали, что режим действовал и раньше — эпоха расширяется назад")

    def test_observation_without_a_range_cannot_relabel_the_past(self):
        tr.observe("-100", kind="topic_opener_seen", message_id=500)   # тут был форум
        tr.observe("-100", kind="channel_forum_missing")               # а сейчас — нет
        self.assertEqual(tr.status_at("-100", 500)[0], tr.TRUE,
                         "наблюдение без диапазона не стирает настоящую эпоху форума")
        self.assertEqual(tr.status_at("-100", 400), (tr.UNKNOWN, 0))

    def test_gap_between_epochs_is_honest_unknown(self):
        tr.observe("-100", kind="no_topic_openers_in_range",
                   since_message_id=100, until_message_id=500)
        tr.observe("-100", kind="topic_opener_seen", message_id=900)
        self.assertEqual(tr.status_at("-100", 300)[0], tr.FALSE)
        self.assertEqual(tr.status_at("-100", 900)[0], tr.TRUE)
        self.assertEqual(tr.status_at("-100", 700), (tr.UNKNOWN, 0),
                         "смена случилась где-то в дыре, но где — мы не знаем")

    def test_newer_than_everything_observed_keeps_the_last_regime(self):
        tr.observe("-100", kind="no_topic_openers_in_range",
                   since_message_id=100, until_message_id=500)
        self.assertEqual(tr.status_at("-100", 9999)[0], tr.FALSE,
                         "режимы держатся, пока не сменятся, а смену мы бы увидели")


class TestEpochsDoNotRewriteThePast(Base):
    def test_conversion_opens_a_new_epoch_and_the_old_one_stands(self):
        # Свидетельство отвечает за ДИАПАЗОН, а не за точку: точное наблюдение на
        # одном сообщении и говорит только о нём. Историю покрывает бэкфилл, который
        # проходит диапазон целиком.
        tr.observe("-100", kind="no_topic_openers_in_range",
                   since_message_id=100, until_message_id=800)
        tr.observe("-100", kind="get_forum_topics_ok", message_id=900)
        self.assertEqual(tr.current("-100")["epoch"], 2)
        # ключ, выданный до превращения, обязан читаться в СВОЁМ режиме
        self.assertEqual(tr.status_at("-100", 150), (tr.FALSE, 1))
        self.assertEqual(tr.status_at("-100", 950), (tr.TRUE, 2))

    def test_history_before_the_first_observation_stays_unknown(self):
        """Служебного сообщения о превращении в форум в истории нет, значит момент
        невосстановим. Делать вид, что сегодняшнее состояние действовало всегда, —
        значит переписать прошлое."""
        tr.observe("-100", kind="get_forum_topics_ok", message_id=1000)
        self.assertEqual(tr.status_at("-100", 999), (tr.UNKNOWN, 0))
        self.assertEqual(tr.status_at("-100", 1000), (tr.TRUE, 1))

    def test_observation_without_a_range_answers_only_about_now(self):
        """⚠ Раньше наблюдение без границы отвечало ЗА ВСЁ — и именно поэтому ответ
        зависел от порядка загрузки. Теперь оно говорит только про «сейчас»."""
        tr.observe("-100", kind="channel_forum_missing")
        self.assertIsNone(tr.current("-100")["since_message_id"])
        self.assertEqual(tr.status_at("-100")[0], tr.FALSE, "про сейчас — отвечает")
        self.assertEqual(tr.status_at("-100", 5), (tr.UNKNOWN, 0),
                         "про конкретное сообщение без диапазона — не отвечает")


class TestItIsShadowOnly(Base):
    def test_registry_is_not_read_by_routing(self):
        """Реестр пока НИЧЕГО не решает: маршрутизация обязана вести себя так же."""
        import telegram_topics
        src = Path(telegram_topics.__file__).read_text(encoding="utf-8")
        self.assertNotIn("telegram_routes", src,
                         "маршрутизация не должна читать реестр до отдельного решения")

    def test_store_is_an_instrument_not_memory(self):
        import memory_fts
        tr.observe("-100", kind="channel_forum_missing", message_id=1)
        path = tr._path("-100")
        self.assertTrue(path.exists())
        self.assertIn(".state", path.as_posix())
        self.assertIsNone(
            memory_fts._selected_jsonl(path, self.tmp / "memory"),
            "реестр не должен попадать в её recall")

    def test_describe_is_human_readable(self):
        tr.observe("-1001240718803", kind="channel_forum_missing", message_id=1)
        self.assertIn("обычная супергруппа", tr.describe("-1001240718803"))
        tr.observe("-1004301095307", kind="topic_opener_seen", message_id=5)
        self.assertIn("форум", tr.describe("-1004301095307"))


class TestTopicCatalogue(Base):
    """Durable-карта настоящих тем: то, что Telegram отдавал бесплатно и что
    выбрасывалось в локальную переменную все эти месяцы."""

    def test_unobserved_catalogue_is_none_not_empty(self):
        self.assertIsNone(tr.confirmed_topics("-300"),
                          "«не наблюдали» и «тем нет» — разные ответы")
        self.assertEqual(tr.topics_of("-300"), {})
        self.assertEqual(tr.topics_seen_at("-300"), "")

    def test_complete_sweep_turns_the_catalogue_on(self):
        tr.observe_topics("-300", {
            7849: {"title": "Открытые вопросы", "top_message": 7849},
            1: {"title": "General", "top_message": 1},
        }, complete=True)
        self.assertEqual(tr.confirmed_topics("-300"), frozenset({1, 7849}))
        self.assertEqual(tr.topic_title("-300", 7849), "Открытые вопросы")
        self.assertNotEqual(tr.topics_seen_at("-300"), "")

    def test_incremental_opener_does_not_claim_completeness(self):
        """Один живой опенер не смеет объявить все остальные темы несуществующими."""
        tr.observe_topics("-300", {9100: {"title": "Новая тема"}},
                          source="topic_opener", complete=False)
        self.assertIsNone(tr.confirmed_topics("-300"),
                          "инкремент не включает каталог: полного свипа не было")
        self.assertEqual(tr.topic_title("-300", 9100), "Новая тема",
                         "но имя уже durable")
        tr.observe_topics("-300", {7849: {"title": "Открытые вопросы"}}, complete=True)
        self.assertEqual(tr.confirmed_topics("-300"), frozenset({7849, 9100}),
                         "после свипа известное ранее не пропадает")

    def test_knowledge_is_append_only_and_titles_refresh(self):
        """Тема, однажды подтверждённая, остаётся известной: старые ключи легитимны,
        а количество тем в минус не меняется (слово владельца 21.08.2026)."""
        tr.observe_topics("-300", {700: {"title": "Старое имя"}}, complete=True)
        tr.observe_topics("-300", {800: {"title": "Другая"}}, complete=True)
        self.assertEqual(tr.confirmed_topics("-300"), frozenset({700, 800}))
        tr.observe_topics("-300", {700: {"title": "Переименована"}}, complete=False)
        self.assertEqual(tr.topic_title("-300", 700), "Переименована")
        self.assertEqual(int(tr.topics_of("-300")[700]["top_message"]), 700,
                         "id темы равен id её корневого сообщения")

    def test_invented_names_never_cross_into_durable_knowledge(self):
        tr.observe_topics("-300", {24255: {"title": "topic #24255"}}, complete=True)
        self.assertEqual(tr.topic_title("-300", 24255), "",
                         "выдуманное «topic #N» — не имя; пустота честнее")
        self.assertIn(24255, tr.confirmed_topics("-300"),
                      "сама тема при этом подтверждена — без имени, но местом")

    def test_junk_ids_are_skipped_one_by_one(self):
        tr.observe_topics("-300", {"мусор": {"title": "x"}, 0: {}, -5: {},
                                   777: {"title": "Живая"}}, complete=True)
        self.assertEqual(tr.confirmed_topics("-300"), frozenset({777}))

    def test_malformed_stored_rows_are_repaired_not_confirmed(self):
        """Её регрессия 7: malformed-записи в файле не подтверждаются как темы и не
        ломают следующий append — чинятся по одной."""
        tr.DIR.mkdir(parents=True, exist_ok=True)
        tr._path("-300").write_text(json.dumps({
            "schema": tr.SCHEMA, "peer_id": "-300", "epochs": [],
            "topics_seen_at": "2026-08-20T00:00:00Z",
            "topics": {
                "junk": "не запись",
                "0": {},
                "700": {"title": 123, "top_message": "мусор"},
                "800": "тоже не запись",
                "900": {"title": "Живая", "top_message": 901},
            },
        }, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(tr.confirmed_topics("-300"), frozenset({700, 900}),
                         "мусорные ключи не подтверждаются, кривые записи — чинятся")
        self.assertEqual(tr.topics_of("-300")[700]["top_message"], 700)
        # следующий append не падает и не теряет живое
        tr.observe_topics("-300", {950: {"title": "Новая"}}, complete=False)
        after = tr.confirmed_topics("-300")
        self.assertEqual(after, frozenset({700, 800, 900, 950}))
        # «800» после починки — валидная тема без имени: ключ был числом, запись мусором
        self.assertEqual(tr.topic_title("-300", 800), "")

    def test_commit_failure_raises_instead_of_pretending(self):
        """Её блокер 3: неудача durable-записи — исключение, не тихий успех."""
        with mock.patch.object(tr, "_save", lambda *a, **k: False):
            with self.assertRaises(OSError):
                tr.observe_topics("-300", {700: {"title": "Тема"}}, complete=True)
        self.assertIsNone(tr.confirmed_topics("-300"),
                          "незаписанное знание не существует")

    def test_legacy_invented_title_is_hidden_on_read(self):
        """Старый файл мог хранить «topic #N» — читатели его не показывают именем."""
        tr.DIR.mkdir(parents=True, exist_ok=True)
        tr._path("-300").write_text(json.dumps({
            "schema": tr.SCHEMA, "peer_id": "-300", "epochs": [],
            "topics_seen_at": "2026-08-20T00:00:00Z",
            "topics": {"24255": {"title": "topic #24255", "top_message": 24255}},
        }, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(tr.topic_title("-300", 24255), "")
        self.assertIn(24255, tr.confirmed_topics("-300"))

    def test_two_processes_keep_the_union(self):
        """Её регрессия 5: два процесса одновременно добавляют разные темы — на диске
        остаётся union. Без межпроцессного замка read-modify-write теряет добавления."""
        base = str(self.tmp)
        script = (
            "import os, sys, time\n"
            "os.environ['PRAXIS_BASE'] = sys.argv[1]\n"
            "sys.path.insert(0, sys.argv[3])\n"
            "import telegram_routes as tr\n"
            "start = int(sys.argv[2])\n"
            "for i in range(40):\n"
            "    tr.observe_topics('-777', {start + i: {'title': f'T{start + i}'}},\n"
            "                      complete=True)\n"
            "    time.sleep(0.001)\n"
        )
        here = str(Path(tr.__file__).resolve().parent)
        procs = [
            subprocess.Popen([sys.executable, "-c", script, base, str(offset), here])
            for offset in (1000, 5000)
        ]
        for proc in procs:
            self.assertEqual(proc.wait(timeout=120), 0)
        got = tr.confirmed_topics("-777")
        expected = frozenset(range(1000, 1040)) | frozenset(range(5000, 5040))
        self.assertEqual(got, expected,
                         f"потеряно {sorted(expected - (got or frozenset()))[:8]}…")


if __name__ == "__main__":
    unittest.main()
