"""Руки над комнатой, а не над одним сообщением — и обратный ход к каждой из них."""
from __future__ import annotations

import asyncio
import datetime
import importlib
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import mtproto_runner
import telegram_admin

ABSTRACTDL = telegram_admin.TARGET_PEER_ID


class _LedgerCase(unittest.TestCase):
    """Журнал — файл, поэтому каждому тесту свой каталог, а не общий прод."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self._saved = telegram_admin.LEDGER
        telegram_admin.LEDGER = Path(self._dir.name) / "telegram_admin.jsonl"
        self.addCleanup(lambda: setattr(telegram_admin, "LEDGER", self._saved))

    def receipt(self, action="slow_mode", subject=None, status="completed",
                before=None, after=None, error=""):
        subject = {"seconds": 30} if subject is None else subject
        return telegram_admin.append_receipt({
            "idempotency_key": telegram_admin.operation_key(ABSTRACTDL, action, subject),
            "actor": "praxis", "peer_id": ABSTRACTDL, "action": action,
            "subject": subject, "status": status,
            "before": before or {}, "after": after or {}, "error": error,
        })


class TheFenceIsTheSameFenceAsModeration(unittest.TestCase):
    """Одна комната, названная в коде. Расширять её — отдельное решение, не опечатка."""

    def test_another_chat_is_refused_even_with_a_perfect_request(self):
        with self.assertRaises(PermissionError):
            telegram_admin.normalize(-1001111111111, "slow_mode", {"seconds": 30})

    def test_a_peer_that_is_not_an_exact_integer_is_refused(self):
        for bogus in ("-1001240718803", True, 1.0, None):
            with self.assertRaises(TypeError):
                telegram_admin.normalize(bogus, "slow_mode", {"seconds": 30})

    def test_an_action_outside_the_four_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            telegram_admin.normalize(ABSTRACTDL, "delete_everything", {})
        self.assertIn("slow_mode", str(caught.exception),
                      "отказ должен называть, что МОЖНО, иначе она гадает")


class SlowModeTakesOnlyTheLadderTelegramAccepts(unittest.TestCase):
    """Телеграм молча подгоняет произвольное число к своей лестнице. Тогда журнал
    сказал бы одно, а комната делала другое — поэтому отказ здесь, а не подгон там."""

    def test_every_rung_is_accepted(self):
        for seconds in telegram_admin.SLOW_MODE_SECONDS:
            _, subject = telegram_admin.normalize(ABSTRACTDL, "slow_mode",
                                                  {"seconds": seconds})
            self.assertEqual(subject, {"seconds": seconds})

    def test_a_value_between_rungs_is_refused_and_the_ladder_is_shown(self):
        with self.assertRaises(ValueError) as caught:
            telegram_admin.normalize(ABSTRACTDL, "slow_mode", {"seconds": 45})
        self.assertIn("3600", str(caught.exception))

    def test_zero_is_a_rung_because_switching_it_off_is_a_measure_too(self):
        _, subject = telegram_admin.normalize(ABSTRACTDL, "slow_mode", {"seconds": 0})
        self.assertEqual(subject, {"seconds": 0})

    def test_an_unexpected_parameter_is_refused_not_ignored(self):
        with self.assertRaises(ValueError):
            telegram_admin.normalize(ABSTRACTDL, "slow_mode",
                                     {"seconds": 30, "forever": True})


class DefaultRightsNameWhatEverybodyMayDo(unittest.TestCase):
    """968 человек за одним вызовом. Почти-верный запрос здесь опаснее неверного."""

    def test_a_misspelled_right_is_refused_and_the_known_ones_are_listed(self):
        with self.assertRaises(ValueError) as caught:
            telegram_admin.normalize(ABSTRACTDL, "default_rights",
                                     {"allow": ["send_documents"]})
        message = str(caught.exception)
        self.assertIn("send_documents", message, "не названо, что именно не понято")
        self.assertIn("send_docs", message, "не показано, как надо")

    def test_allowing_and_denying_the_same_right_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            telegram_admin.normalize(ABSTRACTDL, "default_rights",
                                     {"allow": ["send_docs"], "deny": ["send_docs"]})
        self.assertIn("send_docs", str(caught.exception))

    def test_an_empty_request_is_refused_rather_than_treated_as_no_change(self):
        with self.assertRaises(ValueError):
            telegram_admin.normalize(ABSTRACTDL, "default_rights",
                                     {"allow": [], "deny": []})

    def test_the_room_shape_is_out_of_reach_of_this_hand(self):
        """`change_info` и `pin_messages` — не поведение участников, а устройство
        комнаты. Плюс права менять инфо у неё и нет: рука не должна обещать больше,
        чем Telegram ей отдаст."""
        for right in ("change_info", "pin_messages"):
            with self.assertRaises(ValueError):
                telegram_admin.normalize(ABSTRACTDL, "default_rights",
                                         {"deny": [right]})

    def test_files_and_voices_are_exactly_what_yegor_asked_for(self):
        _, subject = telegram_admin.normalize(
            ABSTRACTDL, "default_rights",
            {"allow": ["send_docs", "send_voices"], "deny": ["embed_links"]})
        self.assertEqual(subject, {"allow": ["send_docs", "send_voices"],
                                   "deny": ["embed_links"]})


class ARestrictionIsTemporaryAndUndoingItIsNot(unittest.TestCase):
    """Главное свойство этих рук. Сегодня выше «удалить» у неё только вечный бан,
    который она сама снять не может; здесь мера со сроком и рука отката без срока."""

    def test_a_bounded_restriction_is_accepted(self):
        _, subject = telegram_admin.normalize(ABSTRACTDL, "restrict",
                                              {"user_id": 4242, "seconds": 3600})
        self.assertEqual(subject, {"user_id": 4242, "seconds": 3600})

    def test_permanence_is_refused_and_the_refusal_says_where_it_lives(self):
        with self.assertRaises(ValueError) as caught:
            telegram_admin.normalize(ABSTRACTDL, "restrict",
                                     {"user_id": 4242, "seconds": 0})
        self.assertIn("delete_and_ban", str(caught.exception),
                      "отказ должен назвать руку, которой это делается по-настоящему")

    def test_a_restriction_longer_than_a_month_is_refused(self):
        with self.assertRaises(ValueError):
            telegram_admin.normalize(
                ABSTRACTDL, "restrict",
                {"user_id": 4242, "seconds": telegram_admin.MAX_RESTRICT_SECONDS + 1})

    def test_undoing_carries_no_duration_at_all(self):
        _, subject = telegram_admin.normalize(ABSTRACTDL, "unrestrict",
                                              {"user_id": 4242})
        self.assertEqual(subject, {"user_id": 4242},
                         "у отката не должно быть срока: снимать всегда можно")

    def test_undoing_refuses_a_duration_offered_by_mistake(self):
        with self.assertRaises(ValueError):
            telegram_admin.normalize(ABSTRACTDL, "unrestrict",
                                     {"user_id": 4242, "seconds": 60})


class TheKeyRemembersTheWholeRequest(unittest.TestCase):
    """Durable-прогон может повторить вызов. Повтор той же меры обязан быть
    ничем, а другой срок — другой мерой, иначе ограничение молча продлевается."""

    def test_the_same_request_gets_the_same_key(self):
        first = telegram_admin.operation_key(ABSTRACTDL, "restrict",
                                             {"user_id": 7, "seconds": 60})
        second = telegram_admin.operation_key(ABSTRACTDL, "restrict",
                                              {"user_id": 7, "seconds": 60})
        self.assertEqual(first, second)

    def test_a_different_duration_is_a_different_measure(self):
        hour = telegram_admin.operation_key(ABSTRACTDL, "restrict",
                                            {"user_id": 7, "seconds": 3600})
        minute = telegram_admin.operation_key(ABSTRACTDL, "restrict",
                                              {"user_id": 7, "seconds": 60})
        self.assertNotEqual(hour, minute,
                            "повтор с другим сроком прошёл бы как уже сделанный")

    def test_restricting_and_unrestricting_are_never_the_same_key(self):
        self.assertNotEqual(
            telegram_admin.operation_key(ABSTRACTDL, "restrict", {"user_id": 7}),
            telegram_admin.operation_key(ABSTRACTDL, "unrestrict", {"user_id": 7}))


class TheLedgerRefusesToBeEdited(_LedgerCase):
    """Тот же приём, что в модерации: цепочка хэшей, и чтение её проверяет."""

    def test_a_receipt_chains_onto_the_previous_one(self):
        first = self.receipt(subject={"seconds": 30})
        second = self.receipt(subject={"seconds": 60})
        self.assertEqual(second["previous_sha256"], first["receipt_sha256"])
        self.assertEqual(len(telegram_admin.history()), 2)

    def test_an_edited_row_is_caught_on_read(self):
        self.receipt(subject={"seconds": 30})
        self.receipt(subject={"seconds": 3600})
        rows = [json.loads(line) for line in
                telegram_admin.LEDGER.read_text(encoding="utf-8").splitlines()]
        rows[0]["subject"] = {"seconds": 0}          # «я этого не делала»
        telegram_admin.LEDGER.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":"))
                      for r in rows) + "\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            telegram_admin.history()

    def test_a_completed_measure_is_seen_as_already_done(self):
        subject = {"user_id": 4242, "seconds": 3600}
        key = telegram_admin.operation_key(ABSTRACTDL, "restrict", subject)
        self.receipt(action="restrict", subject=subject, status="intent")
        self.assertIsNone(telegram_admin.prior(key),
                          "намерение — ещё не сделанное дело")
        self.receipt(action="restrict", subject=subject, status="completed")
        self.assertIsNotNone(telegram_admin.prior(key))

    def test_the_receipt_records_what_the_room_was_before(self):
        """Мера над комнатой обратима только если записано, что было ДО неё."""
        row = self.receipt(action="default_rights",
                           subject={"allow": ["send_docs"], "deny": []},
                           before={"send_docs": False}, after={"send_docs": True})
        self.assertEqual(row["before"], {"send_docs": False})
        self.assertEqual(row["after"], {"send_docs": True})

    def test_unrestrict_after_a_new_restriction_is_a_new_decision(self):
        """Живой репро адверсарки 28.08: unrestrict был исполним ОДИН раз на человека
        за всю жизнь журнала. Забанили заново — второй unrestrict шёл replayed=true,
        ноль RPC, человек оставался забанен. Одноразовой оказалась ровно та рука,
        ради обратимости которой модуль построен.

        Теперь replay допустим, только пока никакой более поздний completed-чек не
        менял тот же предмет: новый restrict делает старый ключ unrestrict историей."""
        undo = {"user_id": 4242}
        undo_key = telegram_admin.operation_key(ABSTRACTDL, "unrestrict", undo)
        self.receipt(action="restrict", subject={"user_id": 4242, "seconds": 3600},
                     status="completed")
        self.receipt(action="unrestrict", subject=undo, status="completed")
        self.assertIsNotNone(telegram_admin.prior(undo_key),
                             "свежий completed без новых мер обязан повторяться")
        self.receipt(action="restrict", subject={"user_id": 4242, "seconds": 600},
                     status="completed")
        self.assertIsNone(telegram_admin.prior(undo_key),
                          "unrestrict после нового бана снова replayed — рука одноразовая")

    def test_slow_mode_back_to_a_previous_rung_is_executed(self):
        """Второй репро: 30 -> 0 -> 30. Журнал говорил «30 completed», комната
        стояла на нуле. Возврат на прежнюю ступень — новое решение, не повтор."""
        key_30 = telegram_admin.operation_key(ABSTRACTDL, "slow_mode", {"seconds": 30})
        self.receipt(subject={"seconds": 30}, status="completed")
        self.receipt(subject={"seconds": 0}, status="completed")
        self.assertIsNone(telegram_admin.prior(key_30),
                          "ступень из прошлого реплеится — комната останется на нуле")

    def test_a_measure_of_another_member_does_not_break_the_replay(self):
        """Предмет — участник, а не весь журнал: чужая мера повтору не мешает."""
        undo = {"user_id": 4242}
        undo_key = telegram_admin.operation_key(ABSTRACTDL, "unrestrict", undo)
        self.receipt(action="unrestrict", subject=undo, status="completed")
        self.receipt(action="restrict", subject={"user_id": 7777, "seconds": 600},
                     status="completed")
        self.assertIsNotNone(telegram_admin.prior(undo_key),
                             "мера над другим участником стёрла законный повтор")

    def test_a_stale_completed_receipt_does_not_replay(self):
        """Журнал не видит ручных действий админов: старый чек — не факт о комнате."""
        subject = {"seconds": 30}
        key = telegram_admin.operation_key(ABSTRACTDL, "slow_mode", subject)
        self.receipt(subject=subject, status="completed")
        self.assertIsNotNone(telegram_admin.prior(key))
        real_time = telegram_admin.time.time
        with mock.patch.object(telegram_admin.time, "time",
                               lambda: real_time() + telegram_admin.REPLAY_WINDOW_SECONDS + 1):
            self.assertIsNone(telegram_admin.prior(key),
                              "чек старше окна повторился — ручной дрейф комнаты невидим")


class AdminActuatorUsesLiveTelethonTypes(unittest.TestCase):
    def test_default_rights_preserves_datetime_until_date(self):
        """A normal deserialized ChatBannedRights must reach the RPC without int()."""
        until = datetime.datetime(2026, 8, 27, 20, 45, tzinfo=datetime.timezone.utc)
        entity = types.SimpleNamespace(
            default_banned_rights=types.SimpleNamespace(
                until_date=until, send_docs=True, send_messages=False))
        captured = {}

        class Client:
            async def __call__(self, request):
                captured["request"] = request

        with mock.patch.object(mtproto_runner, "client", Client()):
            asyncio.run(mtproto_runner._admin_apply(
                entity, "default_rights", {"allow": ["send_docs"], "deny": []}))
        rights = captured["request"].banned_rights
        self.assertEqual(rights.until_date, until)
        self.assertFalse(rights.send_docs)

    def test_default_rights_preserves_unmanaged_telethon_flags(self):
        """Changing send_docs must not silently lift unrelated room-wide bans."""
        until = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)
        from telethon.tl import types as tl_types
        entity = types.SimpleNamespace(
            default_banned_rights=tl_types.ChatBannedRights(
                until_date=until, view_messages=True, manage_topics=True,
                send_docs=True, send_plain=True))
        captured = {}

        class Client:
            async def __call__(self, request):
                captured["request"] = request

        with mock.patch.object(mtproto_runner, "client", Client()):
            asyncio.run(mtproto_runner._admin_apply(
                entity, "default_rights", {"allow": ["send_docs"], "deny": []}))
        rights = captured["request"].banned_rights
        self.assertTrue(rights.view_messages)
        self.assertTrue(rights.manage_topics)
        self.assertTrue(rights.send_plain)
        self.assertFalse(rights.send_docs)
        self.assertEqual(rights.until_date, until)


class RestrictReachesTheWireTemporary(unittest.TestCase):
    """«На 30 секунд» обязано доехать до Telegram временны́м, а не вечным.

    Telegram трактует until_date ближе ~30 с от СВОИХ часов как «навсегда».
    Прежний `now + seconds` при seconds=30 проигрывал эту гонку в 10 000 пробах
    из 10 000 (RTT, очередь, часы) — минимальная мера уходила вечным баном,
    неотличимым от 30-секундного ни в одном её артефакте."""

    def test_restrict_wire_carries_a_margin_above_the_floor(self):
        import time as _time
        captured = {}

        class Client:
            async def __call__(self, request):
                captured["request"] = request

        entity = types.SimpleNamespace(id=1240718803)
        with mock.patch.object(mtproto_runner, "client", Client()):
            wire = asyncio.run(mtproto_runner._admin_apply(
                entity, "restrict", {"user_id": 4242, "seconds": 30}))
        until = captured["request"].banned_rights.until_date
        floor = _time.time() + 30 + mtproto_runner._RESTRICT_WIRE_MARGIN - 2
        self.assertGreaterEqual(until, floor,
                                "запас на провод исчез — 30 секунд снова гонка с вечным")
        self.assertEqual(wire, {"until_date_sent": until,
                                "wire_margin_seconds": mtproto_runner._RESTRICT_WIRE_MARGIN},
                         "что ушло на провод, обязано вернуться фактом для чека")

    def test_the_participant_snapshot_names_the_deadline(self):
        """Вечное и временное ограничения обязаны различаться в чеке."""
        until = datetime.datetime(2026, 9, 1, 12, 0, tzinfo=datetime.timezone.utc)
        participant = types.SimpleNamespace(
            participant=types.SimpleNamespace(
                banned_rights=types.SimpleNamespace(until_date=until, send_messages=True)))

        class Client:
            async def __call__(self, request):
                return participant

        with mock.patch.object(mtproto_runner, "client", Client()):
            snapshot = asyncio.run(mtproto_runner._admin_state(
                types.SimpleNamespace(id=1240718803), "restrict",
                {"user_id": 4242, "seconds": 30}))
        self.assertIn("2026-09-01", str(snapshot.get("until_date")),
                      "срок ограничения не попал в снимок — вечное неотличимо от 30 с")

    def test_unrestrict_wire_still_clears_everything(self):
        captured = {}

        class Client:
            async def __call__(self, request):
                captured["request"] = request

        with mock.patch.object(mtproto_runner, "client", Client()):
            wire = asyncio.run(mtproto_runner._admin_apply(
                types.SimpleNamespace(id=1240718803), "unrestrict", {"user_id": 4242}))
        self.assertEqual(captured["request"].banned_rights.until_date, 0)
        self.assertIsNone(wire)


class TheLedgerIsNotTheModerationLedger(unittest.TestCase):
    def test_two_separate_files(self):
        import telegram_moderation
        self.assertNotEqual(telegram_admin.LEDGER, telegram_moderation.LEDGER,
                            "общий файл сломал бы проверку цепочки модерации: там "
                            "ключ выводится из message_id/sender_id, которых здесь нет")

    def test_the_two_schemas_do_not_collide(self):
        import telegram_moderation
        moderation = telegram_moderation.operation_key(ABSTRACTDL, 1, 2, "delete")
        admin = telegram_admin.operation_key(ABSTRACTDL, "slow_mode", {"seconds": 30})
        self.assertNotEqual(moderation, admin)


if __name__ == "__main__":
    unittest.main()
