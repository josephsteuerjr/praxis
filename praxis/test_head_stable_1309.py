# -*- coding: utf-8 -*-
"""Стабильная голова комнаты (PRAXIS_FRAME_HEAD_STABLE, 13.09).

Замер 13.09 на 40 парах соседних кадров AbstractDL: при одном ключе кэша system рвался на
17 053-м знаке, потому что блок аудитории и две руки владельца менялись с говорящим; первый
вызов хода кэшировался на 3,8k вместо 19,5k токенов в 35 из 89 ходов. Под рычагом system и
набор рук комнаты одинаковы для владельца, знакомого и чужого; полномочия хода уезжают в
строку «говорит» зоны «СЕЙЧАС», STATE и `[private]` — ярусами живого конверта. Ничего не
исчезает — меняется место. Рычаг выключен по умолчанию: без него кадр байт-в-байт прежний.

Запуск: python praxis_test.py test_head_stable_1309 -v
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
import computer_access  # noqa: E402
import frame_layout  # noqa: E402
import frame_trace  # noqa: E402
import llm  # noqa: E402
import people  # noqa: E402
import tool_offerings  # noqa: E402

ROOM_ID = "-1001240718803"
LEVER = {"PRAXIS_FRAME_HEAD_STABLE": "1"}


def room_ctx(*, owner: bool, known: bool = True, family: bool = False,
             principal: str | None = None) -> agent.ChannelContext:
    return agent.ChannelContext(chat_id=ROOM_ID, room_id=ROOM_ID, is_dm=False,
                                owner=owner, known=known, family=family,
                                principal_id=principal)


def dm_ctx(*, owner: bool) -> agent.ChannelContext:
    return agent.ChannelContext(chat_id="809306689", room_id="809306689", is_dm=True,
                                owner=owner, known=True, principal_id="809306689")


def frame_for(ctx: agent.ChannelContext) -> tuple[str, str, str]:
    """(persona, dynamic, evidence) без живого тела сервера — как в test_cache_prefix_stability."""
    with (mock.patch.object(agent, "build_state_block", return_value='{"fact":"probe"}'),
          mock.patch.object(agent, "build_state_evidence_block", return_value="")):
        return agent._build_prompt_parts(speaker="speaker", query="hello", ctx=ctx)


def names(ctx: agent.ChannelContext) -> list[str]:
    return list(tool_offerings.offered_names(agent.catalog_tools_for(ctx)))


def dispatch(ctx: agent.ChannelContext, tool_name: str, **arguments):
    """Use the live pointer catalog and turn-principal binding, not a mocked owner bit."""
    turn = agent._TURN_CHANNEL.set(ctx)
    try:
        with agent._bind_tool_catalog(ctx):
            return agent.tool_call(tool_name, json.dumps(arguments, ensure_ascii=False))
    finally:
        agent._TURN_CHANNEL.reset(turn)


class HeadIsByteStableAcrossSpeakers(unittest.TestCase):
    def test_lever_off_keeps_the_old_frame_and_its_speaker_dependence(self):
        # Регрессия на умолчание: без рычага владелец и чужой по-прежнему дают РАЗНЫЕ system,
        # и у чужого нет `admit` — ровно сегодняшний прод.
        self.assertNotIn("PRAXIS_FRAME_HEAD_STABLE", os.environ)
        owner = frame_for(room_ctx(owner=True))
        guest = frame_for(room_ctx(owner=False, known=False))
        self.assertNotEqual(owner[1], guest[1])
        self.assertIn("The human owner is the actor in this public room", owner[1])
        self.assertIn("Authority fact: this interlocutor is not in the known set", guest[1])
        self.assertIn("members=", owner[1])
        self.assertIn("admit", names(room_ctx(owner=True)))
        self.assertNotIn("admit", names(room_ctx(owner=False, known=False)))

    def test_lever_on_system_is_identical_for_owner_known_and_unknown(self):
        with mock.patch.dict(os.environ, LEVER):
            owner = frame_for(room_ctx(owner=True))
            known = frame_for(room_ctx(owner=False, known=True))
            guest = frame_for(room_ctx(owner=False, known=False))
        self.assertEqual(owner[0], known[0])
        self.assertEqual(owner[0], guest[0])
        self.assertEqual(owner[1], known[1], "system[1] владельца и знакомого расходится")
        self.assertEqual(owner[1], guest[1], "system[1] владельца и чужого расходится")
        dynamic = owner[1]
        self.assertIn("This is a public room; CURRENT_SITUATION names", dynamic)
        self.assertIn("`admit`, `computer_access` (both owner-only", dynamic)
        self.assertIn("Appetite contract", dynamic)
        self.assertNotIn("members=", dynamic)
        self.assertNotIn("Authority fact", dynamic)
        self.assertNotIn("The human owner is the actor", dynamic)
        self.assertIn("audience_key=room", dynamic)
        # адрес кэша один — как и раньше
        self.assertEqual(llm.cache_address("gpt-5.6-terra", owner[0] + owner[1]),
                         f"praxis:gpt-5.6-terra:room:{ROOM_ID}")

    def test_lever_on_tools_are_identical_for_every_speaker_and_gate_stays_at_call(self):
        with mock.patch.dict(os.environ, LEVER):
            owner = names(room_ctx(owner=True))
            guest = names(room_ctx(owner=False, known=False))
        self.assertEqual(owner, guest)
        self.assertIn("admit", guest)
        self.assertIn("computer_access", guest)
        # право — при вызове: не-владельцу `admit` отказывает словами, схема этого не решает
        with mock.patch.object(agent, "_is_human_owner", return_value=False):
            self.assertTrue(str(agent.tool_admit("Кто-то", id="123")).startswith("Отказ"))

    def test_lever_on_state_block_moves_to_the_owner_evidence_tier(self):
        with mock.patch.dict(os.environ, LEVER):
            owner = frame_for(room_ctx(owner=True))
            guest = frame_for(room_ctx(owner=False, known=False))
        self.assertNotIn('{"fact":"probe"}', owner[1], "STATE не должен стоять в голове")
        # заголовок яруса в кадре печатается заглавными (frame_layout.section)
        self.assertIn("СОСТОЯНИЕ СЕЙЧАС", owner[2])
        self.assertIn('{"fact":"probe"}', owner[2])
        # у владельца без приватных строк ярус досье не шумит счётчиком «вынесено: 0»
        self.assertNotIn("вынесено в «Приватное из досье»", owner[2])
        # чужому STATE не показывался и раньше — адресат не меняется
        self.assertNotIn("СОСТОЯНИЕ СЕЙЧАС", guest[2])
        self.assertNotIn('{"fact":"probe"}', guest[2])

    def test_lever_on_does_not_touch_the_owner_dm(self):
        before = frame_for(dm_ctx(owner=True))
        with mock.patch.dict(os.environ, LEVER):
            after = frame_for(dm_ctx(owner=True))
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[1], after[1])
        self.assertEqual(names(dm_ctx(owner=True)),
                         names(dm_ctx(owner=True)))

    def test_moved_is_a_named_reason_and_the_roster_sees_every_name_once(self):
        self.assertIn("moved", frame_trace.REASONS)
        with mock.patch.dict(os.environ, {**LEVER, frame_trace.ENV_LEVER: "on"}):
            token = frame_trace.start()
            try:
                frame_for(room_ctx(owner=False, known=False))
                trace = frame_trace.current()
                self.assertIsNotNone(trace)
                seen: dict[str, list] = {}
                for rec in trace._by_zone.get("dynamic", ()):
                    seen.setdefault(rec.name, []).append(rec)
            finally:
                frame_trace.finish(token)
        for wanted in ("state.owner_place", "contract.owner_tools", "contract.appetite",
                       "state.state_block", "contract.family_audience",
                       "contract.unknown_authority", "state.channel_facts"):
            self.assertEqual(len(seen.get(wanted, [])), 1, wanted)
        self.assertEqual(seen["state.state_block"][0].reason, "moved")
        self.assertEqual(seen["contract.unknown_authority"][0].reason, "moved")
        self.assertTrue(seen["contract.owner_tools"][0].included)


class AuthorityTravelsInTheSituationLine(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(frame_layout.reset)

    def _line(self, authority: str, *, owner: bool) -> str:
        """Строка «говорит» вместе с её переносами (значение длиннее 110 знаков переносится
        с отступом), до следующего ярлыка зоны."""
        ctx = room_ctx(owner=owner, known=(authority != "unknown"), principal="42")
        frame_layout.begin(authority=authority)
        zone = frame_layout.situation(ctx, speaker="Кто-то", tooled=False, home=True)
        lines = zone.splitlines()
        start = next(i for i, line in enumerate(lines) if line.lstrip().startswith("говорит"))
        out = [lines[start]]
        for line in lines[start + 1:]:
            if not line.startswith(" " * 12):
                break
            out.append(line)
        return "\n".join(out)

    def test_unknown_owner_and_family_get_their_authority_facts(self):
        self.assertIn("не в известном наборе", self._line("unknown", owner=False))
        self.assertIn("впустить в «свои» может только Егор", self._line("unknown", owner=False))
        self.assertIn("полномочия владельца", self._line("owner", owner=True))
        self.assertIn("FAMILY", self._line("family", owner=False))
        self.assertIn("знакомый", self._line("known", owner=False))

    def test_real_room_family_field_reaches_the_end_to_end_prompt_situation(self):
        # ChannelContext.scope is deliberately ``group`` for every non-DM. Exercise the real
        # _voice_impl -> prompt parts -> snapshot -> CURRENT_SITUATION seam: the candidate's
        # scope-based derivation labelled this actor merely "known".
        ctx = room_ctx(owner=False, known=True, family=True, principal="42")
        self.assertEqual(ctx.scope, "group")
        seen: dict = {}

        def capture(*, system, messages, tools, max_iters=None, tool_trace=None):
            seen["system"] = system
            seen["prompt"] = messages[-1]["content"]
            return "ok"

        with (mock.patch.dict(os.environ, LEVER),
              mock.patch.object(agent, "_terminal_tool_loop", capture),
              mock.patch.object(agent, "build_state_block", return_value=""),
              mock.patch.object(agent, "build_state_evidence_block", return_value="")):
            self.assertEqual(agent._voice_impl("hello", [], "Кто-то", ctx=ctx), "ok")
        prompt = seen["prompt"] if isinstance(seen["prompt"], str) else "".join(
            str(block.get("text", "")) for block in seen["prompt"] if isinstance(block, dict))
        self.assertNotIn("FAMILY", seen["system"])
        self.assertIn("FAMILY", prompt)
        self.assertNotIn(frame_layout.AUTHORITY["known"], prompt)

    def test_without_authority_in_the_snapshot_the_line_is_the_old_one(self):
        ctx = room_ctx(owner=False, known=True, principal="42")
        frame_layout.begin()
        zone = frame_layout.situation(ctx, speaker="Кто-то", tooled=False, home=True)
        line = next(l for l in zone.splitlines() if l.lstrip().startswith("говорит"))
        for word in frame_layout.AUTHORITY.values():
            self.assertNotIn(word[:20], line)


class RuntimeAuthorizationDispatch(unittest.TestCase):
    """Stable offering is broad; actual turn principals still gate delegation at runtime."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            **LEVER, "PRAXIS_BASE": self.tmp.name, "PRAXIS_OWNER_ID": "101",
            # `dispatch()` intentionally exercises the pointer-only `call(name, args_json)`
            # route. Make that prerequisite local to this test: the live process may run with
            # PRAXIS_TOOLS_POINTERS=off, which must not turn this regression into call→call
            # recursion or a thread-exhaustion probe.
            "PRAXIS_TOOLS_POINTERS": "on",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.owner = room_ctx(owner=True, principal="101")
        self.guest = room_ctx(owner=False, known=False, principal="202")

    def test_admit_dispatch_uses_verified_turn_principal_not_catalog_presence(self):
        self.assertIn("admit", names(self.owner))
        self.assertIn("admit", names(self.guest))
        self.assertIn("сам", dispatch(self.owner, "admit", name="Егор", id="101").lower())
        self.assertIn("только владелец",
                      dispatch(self.guest, "admit", name="Друг", id="303").lower())

    def test_computer_access_list_grant_revoke_dispatch_owner_vs_guest(self):
        self.assertIn("computer_access", names(self.owner))
        self.assertIn("computer_access", names(self.guest))
        self.assertIn("только владелец",
                      dispatch(self.guest, "computer_access", action="list").lower())
        self.assertIn("только владелец", dispatch(
            self.guest, "computer_access", action="grant", telegram_id="303",
            name="Друг", scopes=["computer.files"],
        ).lower())
        self.assertFalse(computer_access.allowed("303", "computer.files"))

        with mock.patch.object(agent, "tool_journal"):
            granted = dispatch(
                self.owner, "computer_access", action="grant", telegram_id="303",
                name="Друг", scopes=["computer.files"],
            )
        self.assertIn("grant: telegram:303", granted)
        self.assertTrue(computer_access.allowed("303", "computer.files"))
        self.assertIn("telegram:303", dispatch(self.owner, "computer_access", action="list"))

        self.assertIn("только владелец", dispatch(
            self.guest, "computer_access", action="revoke", telegram_id="303",
        ).lower())
        self.assertTrue(computer_access.allowed("303", "computer.files"))
        with mock.patch.object(agent, "tool_journal"):
            revoked = dispatch(
                self.owner, "computer_access", action="revoke", telegram_id="303",
            )
        self.assertIn("revoke: telegram:303", revoked)
        self.assertFalse(computer_access.allowed("303", "computer.files"))


class PrivateDossierLinesMoveToTheOwnerTier(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.people = Path(self.tmp.name) / "people"
        self.people.mkdir()
        (self.people / "guest.md").write_text(
            "# Гость\n\ntelegram_id: 42\n\n- любит чай\n- [private] развёлся в марте\n"
            "  и просил не обсуждать\n- пишет по вечерам\n", encoding="utf-8")
        # 25.09: решение Егора — приватные записи в комнатах ОСТАЮТСЯ в кадре
        # (PRAXIS_DOSSIER_PRIVATE_IN_ROOMS=on по умолчанию). Этот класс проверяет
        # прежнее снятие под рычагом «off»; новое поведение — test_room_memory_2509.
        env_off = mock.patch.dict(os.environ, {"PRAXIS_DOSSIER_PRIVATE_IN_ROOMS": "off"})
        env_off.start()
        self.addCleanup(env_off.stop)
        for patch in (mock.patch.object(people, "PEOPLE_DIR", self.people),
                      mock.patch.object(agent, "_present_by_transport", return_value={"42"}),
                      mock.patch.object(agent, "_mentioned_slugs", return_value=[]),
                      mock.patch.object(agent, "_epoch_lifted_dossier", return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(frame_layout.reset)

    def test_split_returns_exactly_the_removed_lines(self):
        text = "- a\n- [private] b\n  cont\n- c\n"
        kept, hidden, removed = agent._split_participant_private_blocks(text)
        self.assertEqual(kept, "- a\n- c\n")
        self.assertEqual(hidden, 2)
        self.assertEqual(removed, "- [private] b\n  cont\n")

    def test_lever_on_owner_in_a_room_gets_the_same_filtered_body_no_tier(self):
        # 17.09: «or ctx.owner» убран из сборки досье. Ход владельца в ГРУППЕ — тот же
        # отфильтрованный корпус, что у гостя: реплика, сочинённая в группе, публична.
        # Ярус «ПРИВАТНОЕ ИЗ ДОСЬЕ» в группах больше не существует вовсе.
        frame_layout.begin()
        with mock.patch.dict(os.environ, LEVER):
            block = agent._participant_memory_block("Кто-то", room_ctx(owner=True, principal="42"))
        self.assertNotIn("развёлся", block)
        self.assertIn("строк приватных записей снято 2", block)
        self.assertFalse(frame_layout.snapshot().get("head_stable_private"))

    def test_lever_on_guest_sees_neither(self):
        frame_layout.begin()
        with mock.patch.dict(os.environ, LEVER):
            block = agent._participant_memory_block("Кто-то", room_ctx(owner=False, known=False,
                                                                        principal="42"))
        self.assertNotIn("развёлся", block)
        self.assertIn("строк приватных записей снято 2", block)
        self.assertFalse(frame_layout.snapshot().get("head_stable_private"))

    def test_lever_off_owner_in_room_gets_filtered_body_too(self):
        # 17.09: и без рычага стабильной головы владелец в ГРУППЕ видит отфильтрованное
        # тело — приватное возвращается только в owner-DM, независимо от рычага.
        frame_layout.begin()
        block = agent._participant_memory_block("Кто-то", room_ctx(owner=True, principal="42"))
        self.assertNotIn("развёлся", block)
        self.assertIn("строк приватных записей снято 2", block)
        self.assertFalse(frame_layout.snapshot().get("head_stable_private"))

    def test_hermetic_18200_budget_keeps_the_public_dossier_atomic(self):
        # 17.09: приватного яруса в группах больше нет — «логическое досье атомарно»
        # теперь значит: публичное тело доезжает целиком и у владельца, и у гостя,
        # приватный сентинел не появляется нигде (в т.ч. в голове), бюджет соблюдён.
        sentinel = "развёлся в марте"
        public = "любит чай"
        budget = "18200"
        with (mock.patch.object(agent, "_persona_text", return_value="P" * 9000),
              mock.patch.object(agent, "hands_pointer_text", return_value=""),
              mock.patch.dict(os.environ, {"PRAXIS_CONTEXT_BUDGET": budget})):
            baseline = frame_for(room_ctx(owner=True, principal="42"))
        self.assertNotIn(sentinel, baseline[0] + baseline[1] + baseline[2])
        self.assertIn(public, baseline[2])

        with (mock.patch.object(agent, "_persona_text", return_value="P" * 9000),
              mock.patch.object(agent, "hands_pointer_text", return_value=""),
              mock.patch.dict(os.environ, {**LEVER, "PRAXIS_CONTEXT_BUDGET": budget,
                                           frame_trace.ENV_LEVER: "on"})):
            token = frame_trace.start()
            try:
                owner_first = frame_for(room_ctx(owner=True, principal="42"))
                owner_budget = dict(frame_trace.current().budget)
            finally:
                frame_trace.finish(token)
            guest = frame_for(room_ctx(owner=False, known=False, principal="42"))
            owner_second = frame_for(room_ctx(owner=True, principal="42"))

        for frame in (owner_first, owner_second, guest):
            self.assertNotIn(sentinel, frame[0] + frame[1] + frame[2])
            self.assertIn(public, frame[2])
        self.assertEqual(owner_first[1], guest[1])
        self.assertEqual(guest[1], owner_second[1])
        self.assertLessEqual(owner_budget["used_final"], owner_budget["limit"])

    def test_18200_budget_huge_private_lines_never_enter_and_public_survives(self):
        # 17.09: приватные строки в группе не собираются вовсе, поэтому гигантское
        # [private]-поле не может выбить публичное тело из бюджета: атомарная пара
        # «публичное + приватное» больше не существует, приватное просто отрезано
        # до любого бюджетного решения.
        public = "PUBLIC-DOSSIER-SENTINEL"
        private = "PRIVATE-DOSSIER-SENTINEL-" + ("X" * 50000)
        (self.people / "guest.md").write_text(
            f"# Гость\n\ntelegram_id: 42\n\n- {public}\n- [private] {private}\n",
            encoding="utf-8",
        )
        with (mock.patch.object(agent, "_persona_text", return_value="P" * 9000),
              mock.patch.object(agent, "hands_pointer_text", return_value=""),
              mock.patch.dict(os.environ, {**LEVER, "PRAXIS_CONTEXT_BUDGET": "18200",
                                           frame_trace.ENV_LEVER: "on"})):
            token = frame_trace.start()
            try:
                owner = frame_for(room_ctx(owner=True, principal="42"))
                accounting = dict(frame_trace.current().budget)
            finally:
                frame_trace.finish(token)

        physical_frame = "".join(owner)
        self.assertIn(public, physical_frame,
                      "public body was lost although private never entered the budget")
        self.assertNotIn("PRIVATE-DOSSIER-SENTINEL", physical_frame)
        self.assertLessEqual(accounting["used_final"], accounting["limit"])
        self.assertEqual(accounting["limit"], 18200)

    def test_constrained_budget_owner_guest_owner_share_filtered_body_no_leak(self):
        # 17.09: сентинел приватной строки не появляется ни у кого в группе — ни при
        # каком бюджете. Ищем границу, где публичное тело доезжает целиком, и проверяем
        # инвариант: у владельца и гостя одна голова, приватного нигде нет.
        sentinel = "развёлся в марте"
        public = "любит чай"
        budget = None
        for limit in range(16000, 40001, 100):
            with mock.patch.dict(os.environ, {**LEVER, "PRAXIS_CONTEXT_BUDGET": str(limit)}):
                candidate = frame_for(room_ctx(owner=True, principal="42"))
            if public in candidate[2]:
                budget = str(limit)
                break
        self.assertIsNotNone(budget, "could not establish constrained stable dossier boundary")

        with mock.patch.dict(os.environ, {**LEVER, "PRAXIS_CONTEXT_BUDGET": budget}):
            owner_first = frame_for(room_ctx(owner=True, principal="42"))
            guest = frame_for(room_ctx(owner=False, known=False, principal="42"))
            owner_second = frame_for(room_ctx(owner=True, principal="42"))
        for frame in (owner_first, owner_second, guest):
            self.assertNotIn(sentinel, frame[0] + frame[1] + frame[2])
        self.assertIn(public, owner_first[2])
        self.assertEqual(owner_first[1], guest[1])
        self.assertEqual(guest[1], owner_second[1])

    def test_full_frames_owner_guest_owner_never_see_private_sentinel_in_a_room(self):
        # 17.09: в группе приватного яруса нет ни у кого — у владельца тоже. Сентинел
        # не появляется ни в голове, ни в живом конверте; головы всех троих совпадают.
        sentinel = "развёлся в марте"
        with mock.patch.dict(os.environ, LEVER):
            owner_first = frame_for(room_ctx(owner=True, principal="42"))
            guest = frame_for(room_ctx(owner=False, known=False, principal="42"))
            owner_second = frame_for(room_ctx(owner=True, principal="42"))

        for frame in (owner_first, guest, owner_second):
            self.assertNotIn(sentinel, frame[0] + frame[1] + frame[2],
                             "private data appeared in a group frame")
        self.assertEqual(owner_first[1], guest[1])
        self.assertEqual(guest[1], owner_second[1])


if __name__ == "__main__":
    unittest.main()
