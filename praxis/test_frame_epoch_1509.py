# -*- coding: utf-8 -*-
"""Эпоха комнаты E и лента A с якоря (PRAXIS_FRAME_EPOCH, 15.09) — и стабильная голова её
собственных ходов (PRAXIS_FRAME_HEAD_STABLE_RUNS).

Замер 15.09 (`desk-notes/frames/after-headstable-1509.txt`): под стабильной головой комнаты
первый вызов хода по-прежнему платил ~48k свежих токенов из ~70k — ленту и конверт, потому
что окно ленты скользило и первое сообщение менялось на 157-м байте; её окна получали на
первом вызове 3,7k вместо ~21k из-за живых фактов STATE в system. Здесь закрепляется:

* рычаги выключены по умолчанию — ни эпохи, ни сдвига STATE в окнах;
* стабильные ярусы конверта едут в эпоху, эпоха замораживается файлом и между ходами с одним
  якорем отдаётся байт-в-байт, даже когда источник (сводка) уже изменился; дрейф называется в
  живой справке; сдвиг якоря и ручной переворот дают новую эпоху;
* лента эпохи: с якоря, в порядке архива, правки и удаления — новыми строками, уже отданное
  не меняется (append-only как префикс);
* в кадре эпоха — первое сообщение, зона `epoch` прибора опечатывается честно, лента не
  «течёт»; владелец и чужой делят одну эпоху, приватное остаётся в живом хвосте владельца;
* STATE её собственного хода под своим рычагом уезжает из головы в ярус «Состояние сейчас».

Запуск: python praxis_test.py test_frame_epoch_1509 -v
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PRAXIS_TEST", "1")

import agent  # noqa: E402
import frame_epoch  # noqa: E402
import frame_layout  # noqa: E402
import frame_trace  # noqa: E402
import group_context  # noqa: E402

ROOM_ID = "-1001240718803"
LEVER = {"PRAXIS_FRAME_HEAD_STABLE": "1", "PRAXIS_FRAME_EPOCH": "1",
         "PRAXIS_FRAME_EPOCH_ROOMS": ROOM_ID}
RUNS_LEVER = {"PRAXIS_FRAME_HEAD_STABLE_RUNS": "1"}


def room_ctx(*, owner: bool, known: bool = True, principal: str | None = None) -> agent.ChannelContext:
    return agent.ChannelContext(chat_id=ROOM_ID, room_id=ROOM_ID, is_dm=False,
                                owner=owner, known=known, principal_id=principal)


def own_ctx() -> agent.ChannelContext:
    return agent.ChannelContext(chat_id=None, principal_id=agent.PRAXIS_SELF_PRINCIPAL,
                                is_dm=True, _scope_override="owner")


class EpochStore(unittest.TestCase):
    """frame_epoch: рычаги, якорь, заморозка/подъём/дрейф/переворот."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(frame_epoch, "STORE", Path(self.tmp.name) / "epoch")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_levers_are_off_by_default_and_rooms_are_named(self):
        self.assertNotIn("PRAXIS_FRAME_EPOCH", os.environ)
        self.assertFalse(frame_epoch.enabled())
        self.assertFalse(frame_epoch.applies(room_ctx(owner=True)))
        with mock.patch.dict(os.environ, {"PRAXIS_FRAME_EPOCH": "1"}):
            # включён, но комната не названа — не применяется
            self.assertFalse(frame_epoch.applies(room_ctx(owner=True)))
        with mock.patch.dict(os.environ, LEVER):
            self.assertTrue(frame_epoch.applies(room_ctx(owner=True)))
            self.assertTrue(frame_epoch.applies(room_ctx(owner=False, known=False)))
            # личка и свой ход — никогда
            self.assertFalse(frame_epoch.applies(agent.ChannelContext(
                chat_id="809306689", room_id="809306689", is_dm=True, owner=True)))
            self.assertFalse(frame_epoch.applies(own_ctx()))
        with mock.patch.dict(os.environ, {"PRAXIS_FRAME_EPOCH": "1", "PRAXIS_FRAME_EPOCH_ROOMS": "*"}):
            self.assertTrue(frame_epoch.applies(room_ctx(owner=False)))

    def test_anchor_is_the_first_hot_record_and_edit_ids_are_parsed(self):
        rows = [{"source_id": "104999:edit:2026-09-14T22:26:25Z:431f50ec"},
                {"source_id": "105001"}]
        with mock.patch("memory_life.hot_records", return_value=rows):
            self.assertEqual(frame_epoch.anchor_for(ROOM_ID), 104999)
        with mock.patch("memory_life.hot_records", return_value=[]):
            self.assertIsNone(frame_epoch.anchor_for(ROOM_ID))
        with mock.patch("memory_life.hot_records", return_value=[{"source_id": "abc"}, {"meta": {"source_id": "77"}}]):
            self.assertEqual(frame_epoch.anchor_for(ROOM_ID), 77)

    def test_stable_titles_registry_names_epoch_and_live_tiers(self):
        for title in ("Ранее в этом диалоге (сводка)",
                      "Мои досье на людей — присутствующие целиком, остальные указателем (внутреннее)",
                      "Карта памяти — ВНУТРЕННЯЯ, не разрешение на раскрытие",
                      "Почтовый ящик — ЛОКАТОР, не индекс", "Эта комната",
                      "Куда здесь уходит ответ (прочти ДО того, как писать)",
                      "Canonical desire continuity"):
            self.assertTrue(frame_epoch.stable_title(title), title)
        for title in ("Визитка (о себе рассказывай отсюда и из проверяемого: STATE/receipts/git)",
                      frame_epoch.LIVE_TIER, agent._STATE_NOW_TIER,
                      agent._PRIVATE_TIER, "Mutable operational continuity",
                      "Почтовый ящик (свежий; действуй тулами mail_read / mail_draft_reply / send_email, поллить не можешь)",
                      "ВНУТРЕННЯЯ память, всплывшая по теме (проверь аудиторию перед раскрытием)",
                      "Актуальные меры и границы для адресата scheduled-намерения"):
            self.assertFalse(frame_epoch.stable_title(title), title)

    def test_serve_freezes_reuses_reports_drift_and_flips(self):
        recap = "Ранее в этом диалоге (сводка)"
        room = "Эта комната"
        first = [(recap, "json", "= сводка А =\n"), (room, "json", "= комната =\n")]
        frozen, receipt = frame_epoch.serve(ROOM_ID, 100, first)
        self.assertEqual(frozen, first)
        self.assertEqual((receipt["n"], receipt["anchor"], receipt["reason"]), (1, 100, "first"))
        saved = json.loads(frame_epoch.path_for(ROOM_ID).read_text(encoding="utf-8"))
        self.assertEqual((saved["n"], saved["anchor"]), (1, 100))
        # источник изменился, якорь тот же — едет ЗАМОРОЖЕННОЕ, дрейф назван
        second = [(recap, "json", "= сводка Б, длиннее =\n"), (room, "json", "= комната =\n")]
        frozen2, receipt2 = frame_epoch.serve(ROOM_ID, 100, second)
        self.assertEqual(frozen2, first)
        self.assertEqual(receipt2["reason"], "reused")
        self.assertEqual(set(receipt2["drift"]), {recap})
        self.assertIn("Ранее в этом диалоге", frame_epoch.live_note(receipt2))
        self.assertIn("+", frame_epoch.live_note(receipt2))
        # якорь сдвинулся — новая эпоха из нынешних байтов
        frozen3, receipt3 = frame_epoch.serve(ROOM_ID, 130, second)
        self.assertEqual(frozen3, second)
        self.assertEqual(receipt3["n"], 2)
        self.assertTrue(receipt3["reason"].startswith("anchor_moved"))
        # ручной переворот той же комнаты — следующий ход замораживает заново под n+1
        self.assertEqual(frame_epoch.flip(ROOM_ID, "стенд"), 3)
        frozen4, receipt4 = frame_epoch.serve(ROOM_ID, 130, second)
        self.assertEqual(frozen4, second)
        self.assertEqual(receipt4["n"], 3)
        self.assertEqual(receipt4["reason"], "flip: стенд")
        # после переворота — снова переиспользование
        self.assertEqual(frame_epoch.serve(ROOM_ID, 130, second)[1]["reason"], "reused")

    def test_message_and_head_carry_the_receipt(self):
        receipt = {"n": 4, "anchor": 555, "frozen_at": "2026-09-15T01:00Z", "reason": "first"}
        text = frame_epoch.message(receipt, [("Эта комната", "json", "BODY\n")])
        self.assertTrue(text.startswith("<praxis_epoch n=4 anchor=#555 frozen=2026-09-15T01:00Z>\n"))
        self.assertTrue(text.endswith("BODY\n" + frame_epoch.CLOSE))
        self.assertIn("Права проверяются при вызове руки", text)


class EpochRows(unittest.TestCase):
    """group_context.epoch_rows: с якоря, в порядке архива, правки новыми строками, префикс цел."""

    PEER = "-1001"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        for name, value in (("BASE", base), ("MEM_DIR", base / "memory"),
                            ("GROUPS_DIR", base / "memory" / "groups"),
                            ("STATE_DIR", base / "memory" / ".state" / "group_context"),
                            ("_KEY_CACHE", {})):
            patcher = mock.patch.object(group_context, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add(self, mid: int, text: str, *, sender=10, name="Alice", outgoing=False,
            edited_at=None, revision_order=None, reply=None):
        return group_context.observe_message(
            peer_id=self.PEER, topic_id=None, message_id=mid, sender_id=sender,
            sender_name=name, reply_to_message_id=reply,
            timestamp=f"2026-09-15T00:{mid:02d}:00Z", text=text, outgoing=outgoing,
            edited_at=edited_at, revision_order=revision_order)

    def rows(self, since: int):
        return group_context.epoch_rows(self.PEER, since_message_id=since, topic_id=None,
                                        whole_room=True)

    def lines(self, since: int) -> list[str]:
        return [row["line"] for row in self.rows(since)]

    def test_rows_start_at_the_anchor_in_archive_order_with_edits_and_deletions_appended(self):
        for mid in (1, 2, 3, 4, 5):
            self.assertTrue(self.add(mid, f"msg {mid}"))
        self.assertTrue(self.add(3, "msg 3 v2", edited_at="2026-09-15T00:07:00Z", revision_order=2))
        self.assertTrue(group_context.observe_deletion(
            peer_id=self.PEER, message_id=4, timestamp="2026-09-15T00:04:00Z",
            sender_id=10, sender_name="Alice", topic_id=None))
        lines = self.lines(2)
        self.assertTrue(lines[0].startswith("…[ЛЕНТА ЭПОХИ: всё сказанное здесь с сообщения #2"))
        body = lines[1:]
        self.assertEqual([int(line.split("message #")[1].split(";")[0]) for line in body],
                         [2, 3, 4, 5, 3, 4])
        self.assertNotIn("msg 1", "\n".join(lines))
        self.assertIn("msg 3", body[1])
        self.assertIn("edited=2026-09-15T00:07:00Z", body[4])
        self.assertIn("msg 3 v2", body[4])
        self.assertIn("[message deleted in Telegram]", body[5])
        # исходная строка #4 осталась как была: правка не переписала прошлое
        self.assertIn("msg 4", body[2])
        self.assertTrue(all(not row.get("service") for row in self.rows(2)[1:]))

    def test_already_delivered_lines_are_a_byte_prefix_of_the_next_render(self):
        long_text = "д" * (group_context.EPOCH_TEXT_CHARS + 500)
        self.add(1, "first")
        self.add(2, long_text)
        before = self.lines(1)
        self.assertIn("…[ОБРЕЗАНО: показано", before[2])
        self.add(3, "third", outgoing=True, name="Praxis")
        self.add(2, long_text + "!", edited_at="2026-09-15T00:09:00Z", revision_order=2)
        after = self.lines(1)
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(len(after), len(before) + 2)
        # второй рендер того же сообщения — той же шириной, что и первый
        self.assertIn("…[ОБРЕЗАНО: показано", after[-1])

    def test_own_messages_keep_the_archive_line_and_lose_the_envelope_in_the_role(self):
        self.add(1, "вопрос")
        self.add(2, "ответ", outgoing=True, sender=777, name="Praxis")
        rows = self.rows(1)
        own = rows[2]
        self.assertTrue(own["self"])
        self.assertIn("Praxis [id 777]", own["line"])
        self.assertIn("message #2", own["line"])
        self.assertEqual(own["role_line"], "ответ", "в роль уехало не только её тело")
        self.assertFalse(rows[1]["self"])
        self.assertEqual(rows[1]["line"], rows[1]["role_line"])

    def test_nothing_since_the_anchor_is_an_empty_tape(self):
        self.add(1, "one")
        self.assertEqual(self.rows(2), [])
        self.assertEqual(self.rows(999), [])


def frame_for(ctx: agent.ChannelContext, *, summary: str = "сводка эпохи") -> tuple[str, str, str]:
    with (mock.patch.object(agent, "build_state_block", return_value='{"fact":"probe"}'),
          mock.patch.object(agent, "build_state_evidence_block", return_value=""),
          mock.patch.object(agent, "read_summary", return_value=summary),
          mock.patch.object(agent, "_recall_block", return_value="")):
        return agent._build_prompt_parts(speaker="speaker", query="hello", ctx=ctx)


class EpochInTheFrame(unittest.TestCase):
    """Стабильные ярусы уезжают в первое сообщение, живые остаются в конверте."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patcher in (mock.patch.object(frame_epoch, "STORE", Path(self.tmp.name) / "epoch"),
                        mock.patch.object(frame_epoch, "anchor_for", return_value=100)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(frame_layout.reset)
        self.addCleanup(frame_epoch.reset)

    def test_lever_off_keeps_the_envelope_and_no_epoch_message(self):
        with mock.patch.dict(os.environ, {"PRAXIS_FRAME_HEAD_STABLE": "1"}):
            _persona, _dynamic, evidence = frame_for(room_ctx(owner=False))
        self.assertIn("РАНЕЕ В ЭТОМ ДИАЛОГЕ", evidence)
        self.assertIn("сводка эпохи", evidence)
        self.assertIsNone(frame_epoch.taken())
        self.assertFalse(frame_epoch.path_for(ROOM_ID).exists())

    def test_stable_tiers_freeze_into_the_first_message_and_survive_source_changes(self):
        with mock.patch.dict(os.environ, LEVER):
            _p, _d, evidence = frame_for(room_ctx(owner=False))
            taken = frame_epoch.taken()
            self.assertIsNotNone(taken)
            e_text, receipt = taken
            self.assertTrue(e_text.startswith("<praxis_epoch n=1 anchor=#100 frozen="))
            self.assertTrue(e_text.endswith(frame_epoch.CLOSE))
            self.assertIn("РАНЕЕ В ЭТОМ ДИАЛОГЕ", e_text)
            self.assertIn("сводка эпохи", e_text)
            self.assertIn("ЭТА КОМНАТА", e_text)
            # её §5.1: визитка — в хвост целиком, в эпохе её нет
            self.assertNotIn("ВИЗИТКА", e_text)
            self.assertNotIn("РАНЕЕ В ЭТОМ ДИАЛОГЕ", evidence)
            self.assertNotIn("ЭТА КОМНАТА", evidence)
            self.assertIn("ЭПОХА ЭТОЙ КОМНАТЫ", evidence)
            self.assertIn("эпоха 1", evidence)
            self.assertIn("ничего из замороженного не менялось", evidence)
            self.assertEqual(receipt["reason"], "first")
            self.assertEqual(frame_layout.snapshot().get("epoch_n"), 1)
            # источник поменялся, якорь тот же: E байт-в-байт прежняя, дрейф назван в хвосте
            _p, _d, evidence2 = frame_for(room_ctx(owner=False), summary="сводка новая")
            e_text2, receipt2 = frame_epoch.taken()
            self.assertEqual(e_text2, e_text)
            self.assertEqual(receipt2["reason"], "reused")
            self.assertIn("после заморозки изменились: Ранее в этом диалоге", evidence2)
            self.assertNotIn("сводка новая", e_text2)
            # якорь сдвинулся — эпоха 2 из свежих байтов
            with mock.patch.object(frame_epoch, "anchor_for", return_value=130):
                frame_for(room_ctx(owner=False), summary="сводка новая")
            e_text3, receipt3 = frame_epoch.taken()
            self.assertTrue(e_text3.startswith("<praxis_epoch n=2 anchor=#130"))
            self.assertIn("сводка новая", e_text3)
            self.assertTrue(receipt3["reason"].startswith("anchor_moved"))
            # привязанный раннером якорь важнее пересчёта
            with frame_epoch.bind(ROOM_ID, 130):
                frame_for(room_ctx(owner=False), summary="сводка новая")
            self.assertEqual(frame_epoch.taken()[1]["reason"], "reused")

    def test_owner_and_guest_share_one_epoch_private_and_state_stay_live(self):
        with mock.patch.dict(os.environ, LEVER):
            _p, _d, owner_evidence = frame_for(room_ctx(owner=True))
            owner_epoch = frame_epoch.taken()[0]
            _p, _d, guest_evidence = frame_for(room_ctx(owner=False, known=False))
            guest_epoch = frame_epoch.taken()[0]
        self.assertEqual(owner_epoch, guest_epoch)
        self.assertIn("СОСТОЯНИЕ СЕЙЧАС", owner_evidence)
        self.assertIn('{"fact":"probe"}', owner_evidence)
        self.assertNotIn("СОСТОЯНИЕ СЕЙЧАС", guest_evidence)
        self.assertNotIn("probe", owner_epoch)

    def test_without_an_anchor_the_envelope_is_the_old_one(self):
        with (mock.patch.dict(os.environ, LEVER),
              mock.patch.object(frame_epoch, "anchor_for", return_value=None)):
            _p, _d, evidence = frame_for(room_ctx(owner=False))
        self.assertIsNone(frame_epoch.taken())
        self.assertIn("РАНЕЕ В ЭТОМ ДИАЛОГЕ", evidence)
        self.assertNotIn("ЭПОХА ЭТОЙ КОМНАТЫ", evidence)

    def test_epoch_is_the_first_message_and_the_trace_seals_it_honestly(self):
        seen: dict = {}

        def capture(*, system, messages, tools, max_iters=None, tool_trace=None):
            seen["messages"] = messages
            seen["assay"] = frame_layout.assay_tape(messages)
            trace = frame_trace.current()
            seen["zones"] = {row["zone"]: row for row in (trace.zones if trace else [])}
            seen["honesty"] = dict(trace.honesty) if trace else None
            return "ok"

        history = [{"role": "user", "content": "> [root; message #101] раньше"},
                   {"role": "assistant", "content": "[root; message #102] отвечала"}]
        with mock.patch.dict(os.environ, {**LEVER, frame_trace.ENV_LEVER: "on"}):
            token = frame_trace.start()
            try:
                with (mock.patch.object(agent, "_terminal_tool_loop", capture),
                      mock.patch.object(agent, "build_state_block", return_value=""),
                      mock.patch.object(agent, "build_state_evidence_block", return_value=""),
                      mock.patch.object(agent, "read_summary", return_value="сводка"),
                      mock.patch.object(agent, "_recall_block", return_value="")):
                    self.assertEqual(agent._voice_impl("hello", history, "Кто-то",
                                                       ctx=room_ctx(owner=False)), "ok")
            finally:
                frame_trace.finish(token)
        messages = seen["messages"]
        self.assertEqual(messages[0]["role"], "user")
        self.assertTrue(messages[0]["content"].startswith("<praxis_epoch n=1 anchor=#100"))
        self.assertEqual(len(messages), 4)  # эпоха, две реплики истории, текущий ход
        self.assertIn("<praxis_context_evidence>", messages[-1]["content"])
        self.assertIn("ЭПОХА ЭТОЙ КОМНАТЫ", messages[-1]["content"])
        self.assertNotIn("РАНЕЕ В ЭТОМ ДИАЛОГЕ", messages[-1]["content"])
        self.assertEqual(seen["assay"]["tape_leaked_lines"], 0, seen["assay"])
        self.assertTrue(seen["honesty"]["ok"], seen["honesty"])
        self.assertEqual(seen["zones"]["epoch"]["container"], "messages[0]")
        self.assertTrue(seen["zones"]["epoch"]["honest"])
        self.assertEqual(seen["zones"]["epoch"]["chars"], len(messages[0]["content"]))


class EpochDossiers(unittest.TestCase):
    """Тела досье — в эпохе составом на момент заморозки; «кто передо мной» и досье
    пришедшего после заморозки — живым хвостом."""

    def setUp(self) -> None:
        import people
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        folk = base / "people"
        folk.mkdir()
        (folk / "guest.md").write_text("# Гость\n\ntelegram_id: 42\n\n- любит чай\n"
                                       "- [private] развёлся в марте\n", encoding="utf-8")
        (folk / "newcomer.md").write_text("# Новичок\n\ntelegram_id: 43\n\n- пришёл позже\n",
                                          encoding="utf-8")
        for patcher in (mock.patch.object(frame_epoch, "STORE", base / "epoch"),
                        mock.patch.object(frame_epoch, "anchor_for", return_value=100),
                        mock.patch.object(people, "PEOPLE_DIR", folk),
                        mock.patch.object(agent, "_present_by_transport", return_value={"42"}),
                        mock.patch.object(agent, "_mentioned_slugs", return_value=[]),
                        mock.patch.object(agent, "_epoch_lifted_dossier", return_value=None),
                        mock.patch.object(agent, "dossier_contract_enabled", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(frame_layout.reset)
        self.addCleanup(frame_epoch.reset)

    def test_bodies_freeze_who_is_live_and_a_newcomer_rides_in_the_tail(self):
        with mock.patch.dict(os.environ, LEVER):
            _p, _d, first = frame_for(room_ctx(owner=False, principal="42"))
            epoch1, _r = frame_epoch.taken()
            self.assertIn("memory/people/guest.md", epoch1)
            self.assertIn("любит чай", epoch1)
            self.assertNotIn("передо мной", epoch1)
            self.assertIn("КТО ПЕРЕДО МНОЙ", first)
            self.assertIn("передо мной: guest", first)
            self.assertNotIn("ДОСЬЕ ГОВОРЯЩЕГО", first)
            # заговорил новичок: эпоха та же байт-в-байт, его досье — живым хвостом
            _p, _d, second = frame_for(room_ctx(owner=False, principal="43"))
            epoch2, receipt2 = frame_epoch.taken()
            self.assertEqual(epoch2, epoch1)
            self.assertEqual(receipt2["reason"], "reused")
            self.assertIn("передо мной: newcomer", second)
            self.assertIn("ДОСЬЕ ГОВОРЯЩЕГО", second)
            self.assertIn("пришёл позже", second)
            self.assertNotIn("пришёл позже", epoch2)
            # а тот, кто в эпохе есть, вторым телом не едет
            _p, _d, third = frame_for(room_ctx(owner=False, principal="42"))
            self.assertNotIn("ДОСЬЕ ГОВОРЯЩЕГО", third)

    def test_epoch_body_does_not_depend_on_who_speaks_first(self):
        # Первым заговорил тот, кого транспорт присутствующим не называл: в эпоху он не
        # попадает (иначе состав документа зависел бы от первого говорящего), едет живым.
        with mock.patch.dict(os.environ, LEVER):
            _p, _d, evidence = frame_for(room_ctx(owner=False, principal="43"))
            epoch, _r = frame_epoch.taken()
        self.assertIn("memory/people/guest.md", epoch)
        self.assertNotIn("memory/people/newcomer.md", epoch)
        self.assertIn("ДОСЬЕ ГОВОРЯЩЕГО", evidence)
        self.assertIn("пришёл позже", evidence)

    def test_owner_and_guest_share_the_dossier_body_private_stays_in_owner_dm(self):
        # 17.09: «or ctx.owner» убран из сборки досье. Ход владельца В ГРУППЕ получает тот же
        # отфильтрованный корпус, что гость: реплика, сочинённая в этом ходе, публична.
        # Дважды за два дня приватный ярус утекал из групповых ходов владельца в текст.
        with mock.patch.dict(os.environ, LEVER):
            _p, _d, guest = frame_for(room_ctx(owner=False, known=False, principal="42"))
            guest_epoch = frame_epoch.taken()[0]
            _p, _d, owner = frame_for(room_ctx(owner=True, principal="42"))
            owner_epoch, receipt = frame_epoch.taken()
        self.assertEqual(guest_epoch, owner_epoch)
        self.assertEqual(receipt["reason"], "reused")
        self.assertNotIn("Мои досье", "".join(receipt.get("drift") or {}))
        self.assertNotIn("развёлся", guest_epoch)
        self.assertNotIn("развёлся", guest)
        self.assertIn("приватных записей снято 1", guest)
        # владелец в группе: замороженное тело то же (reason=reused, досье не в drift),
        # живой кадр — без приватного яруса и без приватных строк; вейл как у гостя
        self.assertNotIn("ПРИВАТНОЕ ИЗ ДОСЬЕ", owner)
        self.assertNotIn("развёлся", owner)
        self.assertIn("приватных записей снято 1", owner)

    def test_private_lines_travel_inline_in_owner_dm(self):
        # 17.09: в owner-DM (личка, scope=owner) приватные строки и раньше не резались —
        # они едут телом досье (режим not-stable не разделяет), ярус «ПРИВАТНОЕ ИЗ ДОСЬЕ»
        # был артефактом стабильной головы групп. Проверяю действующую семантику DM:
        # строка в теле, вейл-строки «снято N» нет.
        with mock.patch.dict(os.environ, LEVER):
            _p, _d, dm = frame_for(agent.ChannelContext(
                chat_id="809306689", room_id="809306689", is_dm=True, owner=True,
                principal_id="809306689", _scope_override="owner"))
            frame_epoch.taken()
        self.assertIn("развёлся", dm, "в личке владельца приватные строки едут телом досье")
        self.assertNotIn("приватных записей снято", dm)

    def test_without_the_lever_who_stays_inside_the_dossier_tier(self):
        with mock.patch.dict(os.environ, {"PRAXIS_FRAME_HEAD_STABLE": "1"}):
            _p, _d, evidence = frame_for(room_ctx(owner=False, principal="42"))
        self.assertIn("передо мной: guest", evidence)
        self.assertNotIn("КТО ПЕРЕДО МНОЙ", evidence)
        self.assertNotIn("ДОСЬЕ ГОВОРЯЩЕГО", evidence)


class OwnRunHeadStable(unittest.TestCase):
    """PRAXIS_FRAME_HEAD_STABLE_RUNS: STATE окна — в ярусе, голова окна одна на все окна."""

    def setUp(self) -> None:
        self.addCleanup(frame_layout.reset)

    def frame(self, fact: str) -> tuple[str, str, str]:
        with (mock.patch.object(agent, "build_state_block", return_value=fact),
              mock.patch.object(agent, "build_state_evidence_block", return_value=""),
              mock.patch.object(agent, "_recall_block", return_value="")):
            return agent._build_prompt_parts(speaker=None, query="", ctx=own_ctx())

    def test_lever_off_keeps_state_in_the_head_and_two_runs_differ(self):
        self.assertNotIn("PRAXIS_FRAME_HEAD_STABLE_RUNS", os.environ)
        a = self.frame('{"fact":"loops","open":3}')
        b = self.frame('{"fact":"loops","open":4}')
        self.assertIn('"open":3', a[1])
        self.assertNotEqual(a[1], b[1])
        self.assertNotIn("СОСТОЯНИЕ СЕЙЧАС", a[2])

    def test_lever_on_moves_state_to_the_tier_and_the_head_is_shared(self):
        with mock.patch.dict(os.environ, RUNS_LEVER):
            a = self.frame('{"fact":"loops","open":3}')
            b = self.frame('{"fact":"loops","open":4}')
        self.assertEqual(a[0] + a[1], b[0] + b[1], "голова окна зависит от живого STATE")
        self.assertNotIn('"open":3', a[1])
        self.assertIn("This is your own run", a[1])
        self.assertIn("СОСТОЯНИЕ СЕЙЧАС", a[2])
        self.assertIn('"open":3', a[2])
        self.assertIn('"open":4', b[2])

    def test_runs_lever_does_not_touch_the_room_frame(self):
        with (mock.patch.object(agent, "build_state_block", return_value='{"fact":"probe"}'),
              mock.patch.object(agent, "build_state_evidence_block", return_value=""),
              mock.patch.object(agent, "_recall_block", return_value="")):
            before = agent._build_prompt_parts(speaker="s", query="q", ctx=room_ctx(owner=True))
            with mock.patch.dict(os.environ, RUNS_LEVER):
                after = agent._build_prompt_parts(speaker="s", query="q", ctx=room_ctx(owner=True))
        self.assertEqual(before[1], after[1])
        self.assertEqual(before[2], after[2])

    def test_moved_is_named_to_the_instrument(self):
        with mock.patch.dict(os.environ, {**RUNS_LEVER, frame_trace.ENV_LEVER: "on"}):
            token = frame_trace.start()
            try:
                self.frame('{"fact":"loops","open":3}')
                trace = frame_trace.current()
                seen = {rec.name: rec for rec in trace._by_zone.get("dynamic", ())}
            finally:
                frame_trace.finish(token)
        self.assertEqual(seen["state.state_block"].reason, "moved")


if __name__ == "__main__":
    unittest.main()
