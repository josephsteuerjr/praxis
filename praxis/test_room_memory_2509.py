"""25.09 — память комнаты (слово Егора: «как следует поднять горячий контекст комнаты»,
«сделать приватные данные более доступными»).

Что здесь прибито:
* горячее окно КОМНАТ — свои пороги (`memory_life.hot_bounds`), личка на прежних;
* сводка места держит самый широкий по охвату блок (верхний ярус) и самые свежие, показ
  по хронологии — а не «свежее к старому» с потерей верхнего яруса первым;
* потолок кадра для сводки применяется целыми блоками (`agent.read_summary`);
* в комнатах записи досье `[private]` остаются в кадре как внутреннее знание; личка с
  чужим человеком и owner-DM — как прежде;
* recall в комнате называет совпадения из других комнат вместо скрытия.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent
import frame_layout
import memory_life as ml
import people
import rooms

ROOM = "-1001"


def _room_ctx(*, owner: bool, known: bool = True, principal: str | None = "42") -> agent.ChannelContext:
    return agent.ChannelContext(chat_id=ROOM, room_id=ROOM, is_dm=False, owner=owner,
                                known=known, principal_id=principal)


def _rows(n: int, *, gap_sec: int = 3600) -> list[dict]:
    base = 1_800_000_000
    out = []
    for i in range(n):
        stamp = base + i * gap_sec
        out.append({"id": f"evt-{i}", "line": f"кто-то: реплика {i}", "ts": float(stamp),
                    "direction": "out" if i % 2 else "in", "tokens": 5})
    return out


class GroupHotWindowHasItsOwnBounds(unittest.TestCase):
    def test_room_bounds_are_wider_than_the_dm_ones(self):
        lo, hi, hard, cap = ml.hot_bounds("-1001240718803")
        self.assertEqual((lo, hi, hard, cap),
                         (ml.GROUP_HOT_LO, ml.GROUP_HOT_HI, ml.GROUP_HOT_HARD_HI, ml.GROUP_HOT_TOKEN_CAP))
        self.assertGreater(lo, ml.HOT_LO)
        self.assertGreater(hi, ml.HOT_HI)
        self.assertGreater(hard, ml.HOT_HARD_HI)
        self.assertGreaterEqual(cap, ml.HOT_TOKEN_CAP)
        self.assertLess(lo, hi)
        self.assertLess(hi, hard)

    def test_dm_defaults_are_150_250_300(self):
        # слово Егора 25.09: «150–250 максимум»; рычаги PRAXIS_HOT_* остаются
        if any(os.getenv(k) for k in ("PRAXIS_HOT_LO", "PRAXIS_HOT_HI", "PRAXIS_HOT_HARD_HI",
                                     "PRAXIS_HOT_TOKEN_CAP")):
            self.skipTest("пороги заданы окружением")
        self.assertEqual((ml.HOT_LO, ml.HOT_HI, ml.HOT_HARD_HI, ml.HOT_TOKEN_CAP),
                         (150, 250, 300, 40_000))
        self.assertEqual((ml.GROUP_HOT_LO, ml.GROUP_HOT_HI, ml.GROUP_HOT_HARD_HI,
                          ml.GROUP_HOT_TOKEN_CAP), (250, 400, 500, 64_000))

    def test_dm_and_no_place_keep_the_old_bounds(self):
        old = (ml.HOT_LO, ml.HOT_HI, ml.HOT_HARD_HI, ml.HOT_TOKEN_CAP)
        self.assertEqual(ml.hot_bounds("809306689"), old)
        self.assertEqual(ml.hot_bounds(None), old)
        self.assertEqual(ml.hot_bounds(""), old)

    def test_bounds_follow_module_patches_at_call_time(self):
        with mock.patch.object(ml, "HOT_LO", 7), mock.patch.object(ml, "GROUP_HOT_LO", 70):
            self.assertEqual(ml.hot_bounds("42")[0], 7)
            self.assertEqual(ml.hot_bounds("-42")[0], 70)

    def test_plan_uses_the_place_bounds(self):
        # как зовёт живой путь: tape_chars — потолок ленты ЭТОГО места (группе 0)
        n = ml.HOT_HI + 10
        rows = _rows(n)
        dm = ml.plan_hot_fold(rows, tape_chars=ml.tape_chars_for("809306689"), place="809306689")
        self.assertTrue(dm["due"], dm)
        group = ml.plan_hot_fold(rows, tape_chars=ml.tape_chars_for("-1001240718803"),
                                 place="-1001240718803")
        self.assertFalse(group["due"], group)
        self.assertEqual(group["reason"], "within_window")
        big = ml.plan_hot_fold(_rows(ml.GROUP_HOT_HI + 10), tape_chars=0, place="-1001240718803")
        self.assertTrue(big["due"], big)
        # сколько остаётся горячим после свёртки комнаты — по GROUP_HOT_LO, не по HOT_LO
        self.assertGreaterEqual((ml.GROUP_HOT_HI + 10) - big["fold"], ml.GROUP_HOT_LO - 1)

    def test_no_place_is_the_old_call(self):
        rows = _rows(ml.HOT_HI + 10)
        self.assertEqual(ml.plan_hot_fold(rows)["due"], ml.plan_hot_fold(rows, place=None)["due"])
        self.assertTrue(ml.plan_hot_fold(rows)["due"])


class SummaryKeepsTheWidestBlockAndTheFreshest(unittest.TestCase):
    def setUp(self) -> None:
        self._graph = ml._canonical_compact_graph
        self._state = ml._state_path
        self._cdir = ml._compact_dir

    def tearDown(self) -> None:
        ml._canonical_compact_graph = self._graph
        ml._state_path = self._state
        ml._compact_dir = self._cdir

    def _install(self, specs: list[tuple[str, int, int, int]]) -> None:
        """specs: (id, tier, event_count, size); порядок — по first_ts (день = индекс)."""
        canonical = {}
        for i, (cid, tier, events, size) in enumerate(specs):
            meta = {"id": cid, "tier": tier, "depth": tier, "continued": False,
                    "degraded": False, "event_count": events,
                    "first_ts": f"2026-07-{10 + i:02d}T00:00:00Z",
                    "created_at": f"2026-07-{10 + i:02d}T00:00:00Z", "source_compact_ids": []}
            canonical[cid] = (meta, f"recap-{cid} " + "ю" * size)
        ml._canonical_compact_graph = lambda chat_id: (canonical, False)

        class _P:
            def exists(self_inner):
                return True
        ml._state_path = lambda chat_id: _P()
        ml._compact_dir = lambda chat_id: _P()

    def test_widest_block_survives_the_budget_and_order_is_chronological(self):
        self._install([("cmp-wide", 4, 6493, 900)] + [(f"cmp-{i}", 1, 60, 900) for i in range(1, 6)])
        limit = len(ml._CONTINUITY_WARNING) + 2 + 2400      # два блока по ~950, третий не влезает
        out = ml.context_summary("777", max_chars=limit)
        self.assertIn("cmp-wide", out, "верхний ярус обязан быть в кадре")
        self.assertIn("cmp-5", out, "самый свежий лист остаётся")
        for cid in ("cmp-1", "cmp-2", "cmp-3", "cmp-4"):
            self.assertNotIn(cid + " ", out)
        self.assertIn("СВОДКА ОБРЕЗАНА БЮДЖЕТОМ", out)
        self.assertIn("самый широкий по охвату", out)
        self.assertLess(out.index("cmp-wide"), out.index("cmp-5"), "показ — по хронологии")
        self.assertLessEqual(len(out), limit)

    def test_without_event_counts_the_newest_is_the_anchor(self):
        # старые свёртки без event_count: «самый широкий» не определён — ведёт себя как
        # прежняя упаковка (свежее остаётся, старое выпадает первым)
        self._install([(f"cmp-{i}", 1, 0, 900) for i in range(6)])
        limit = len(ml._CONTINUITY_WARNING) + 2 + 2400
        out = ml.context_summary("777", max_chars=limit)
        self.assertIn("cmp-5", out)
        self.assertIn("cmp-4", out)
        self.assertNotIn("cmp-0 ", out)

    def test_everything_fits_no_marker(self):
        self._install([("cmp-wide", 3, 900, 100), ("cmp-1", 1, 40, 100)])
        out = ml.context_summary("777", max_chars=7000)
        self.assertNotIn("СВОДКА ОБРЕЗАНА", out)
        self.assertIn("cmp-wide", out)
        self.assertIn("cmp-1", out)


class SummaryFrameCapAppliesInWholeBlocks(unittest.TestCase):
    def test_default_cap_is_forty_thousand(self):
        if os.getenv("PRAXIS_SUMMARY_FRAME_CHARS"):
            self.skipTest("рычаг задан окружением")
        self.assertEqual(agent.SUMMARY_FRAME_CHARS, 40_000)

    def test_read_summary_passes_the_smaller_of_room_budget_and_frame_cap(self):
        seen: list[int] = []

        def fake_summary(chat_id, max_chars=7000):
            seen.append(int(max_chars))
            return "сводка"

        with (mock.patch.object(agent, "_summary_budget", return_value=60_000),
              mock.patch.object(agent.memory_life, "has_life_memory", return_value=True),
              mock.patch.object(agent.memory_life, "context_summary", side_effect=fake_summary),
              mock.patch.object(agent, "SUMMARY_FRAME_CHARS", 40_000),
              mock.patch.dict(os.environ, {"PRAXIS_DOSSIER_ALL": ""})):
            self.assertEqual(agent.read_summary("-1001"), "сводка")
            self.assertEqual(seen[-1], 40_000)
        with (mock.patch.object(agent, "_summary_budget", return_value=60_000),
              mock.patch.object(agent.memory_life, "has_life_memory", return_value=True),
              mock.patch.object(agent.memory_life, "context_summary", side_effect=fake_summary),
              mock.patch.object(agent, "SUMMARY_FRAME_CHARS", 40_000),
              mock.patch.dict(os.environ, {"PRAXIS_DOSSIER_ALL": "1"})):
            agent.read_summary("-1001")
            self.assertEqual(seen[-1], 60_000, "без контракта досье потолок кадра не применяется")


class RoomDefaultsRaised(unittest.TestCase):
    def test_default_policy_and_maxima(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRAXIS_ROOM_CONTEXT_HOT", None)
            os.environ.pop("PRAXIS_ROOM_CONTEXT_CHARS", None)
            policy = rooms.default_policy()
        self.assertEqual(policy["context_hot"], 400)
        self.assertEqual(policy["context_summary_chars"], 40_000)
        self.assertEqual(rooms.CONTEXT_HOT_MAX, 1000)
        self.assertEqual(rooms.CONTEXT_SUMMARY_MAX, 80_000)


class PrivateDossierLinesStayInRooms(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.people = Path(self.tmp.name) / "people"
        self.people.mkdir()
        (self.people / "guest.md").write_text(
            "# Гость\n\ntelegram_id: 42\n\n- любит чай\n- [private] развёлся в марте\n"
            "  и просил не обсуждать\n- пишет по вечерам\n", encoding="utf-8")
        for patch in (mock.patch.object(people, "PEOPLE_DIR", self.people),
                      mock.patch.object(agent, "_present_by_transport", return_value={"42"}),
                      mock.patch.object(agent, "_mentioned_slugs", return_value=[]),
                      mock.patch.object(agent, "_epoch_lifted_dossier", return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(frame_layout.reset)

    def _block(self, ctx, env: dict | None = None) -> str:
        frame_layout.begin()
        with mock.patch.dict(os.environ, env or {}):
            return agent._participant_memory_block("Кто-то", ctx)

    def test_owner_and_guest_in_a_room_see_the_private_line_with_its_mark(self):
        for ctx in (_room_ctx(owner=True), _room_ctx(owner=False, known=False)):
            block = self._block(ctx, {"PRAXIS_DOSSIER_PRIVATE_IN_ROOMS": "on"})
            self.assertIn("развёлся в марте", block)
            self.assertIn("[private]", block, "пометка остаётся — она видит, что это приватное")
            self.assertIn("оставлены в кадре", block)
            self.assertIn("решение владельца, 25.09", block)
            self.assertNotIn("приватных записей снято", block)
            self.assertFalse(frame_layout.snapshot().get("head_stable_private"))

    def test_the_default_is_on(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRAXIS_DOSSIER_PRIVATE_IN_ROOMS", None)
            self.assertTrue(agent.dossier_private_in_rooms())
        for word in ("off", "0", "no", "false"):
            with mock.patch.dict(os.environ, {"PRAXIS_DOSSIER_PRIVATE_IN_ROOMS": word}):
                self.assertFalse(agent.dossier_private_in_rooms())

    def test_lever_off_restores_the_old_stripping(self):
        block = self._block(_room_ctx(owner=False, known=False),
                            {"PRAXIS_DOSSIER_PRIVATE_IN_ROOMS": "off"})
        self.assertNotIn("развёлся", block)
        self.assertIn("строк приватных записей снято 2", block)

    def test_the_body_is_the_same_for_every_speaker_in_the_room(self):
        # Стабильная голова (13.09) резала приватное «для всех», чтобы тело не зависело от
        # говорящего. Теперь оно не зависит от него по построению: не режется ни у кого.
        env = {"PRAXIS_DOSSIER_PRIVATE_IN_ROOMS": "on", "PRAXIS_FRAME_HEAD_STABLE": "1"}
        owner = self._block(_room_ctx(owner=True), env)
        guest = self._block(_room_ctx(owner=False, known=False), env)
        card = "— Гость · memory/people/guest.md —"
        self.assertIn(card, owner)
        self.assertEqual(owner.split(card, 1)[1].split("\n\n", 1)[0],
                         guest.split(card, 1)[1].split("\n\n", 1)[0])

    def test_a_stranger_dm_still_strips_its_own_private_lines(self):
        # Область рычага — комнаты. В личке с чужим человеком строки о нём снимаются
        # как прежде (та же граница, что у тени frame_shadow._lifted_source).
        ctx = agent.ChannelContext(chat_id="42", is_dm=True, owner=False, known=True,
                                   principal_id="42")
        block = self._block(ctx, {"PRAXIS_DOSSIER_PRIVATE_IN_ROOMS": "on"})
        self.assertNotIn("развёлся", block)
        self.assertIn("строк приватных записей снято 2", block)

    def test_owner_dm_gets_everything_as_before(self):
        ctx = agent.ChannelContext(chat_id="42", is_dm=True, owner=True, known=True,
                                   principal_id="42")
        block = self._block(ctx, {"PRAXIS_DOSSIER_PRIVATE_IN_ROOMS": "on",
                                  "PRAXIS_FRAME_HEAD_STABLE": "0"})
        self.assertIn("развёлся в марте", block)
        self.assertNotIn("снято", block)


class RecallNamesForeignRoomsInsteadOfHiding(unittest.TestCase):
    HITS = [
        {"path": "memory/self/rooms/-100777/turns-2026-09.jsonl", "text": "я сказала там про волка",
         "source": "Praxis"},
        {"path": "memory/self/rooms/-100999/turns-2026-09.jsonl", "text": "я сказала здесь про кота",
         "source": "Praxis"},
        {"path": "memory/people/x.md", "text": "факт о человеке", "source": "x"},
    ]

    def _recall(self, env: dict) -> str:
        ctx = agent.ChannelContext(chat_id="-100999", room_id="-100999", is_dm=False,
                                   owner=False, known=False, principal_id="7")
        token = agent._TURN_CHANNEL.set(ctx)
        try:
            with (mock.patch.object(agent.memory_index, "search", return_value=list(self.HITS)),
                  mock.patch.object(agent, "_active_chat", return_value="-100999"),
                  mock.patch.dict(os.environ, env)):
                return agent.tool_recall("волк")
        finally:
            agent._TURN_CHANNEL.reset(token)

    def test_default_shows_the_foreign_hit_with_its_room_named(self):
        out = self._recall({"PRAXIS_RECALL_CROSS_ROOM": "show"})
        self.assertIn("про волка", out)
        self.assertIn("из комнаты -100777 — внутреннее", out)
        self.assertIn("про кота", out)
        self.assertNotIn("из комнаты -100999", out, "своя комната не помечается «оттуда»")
        self.assertIn("1 совпадений из других комнат — внутреннее", out)
        self.assertNotIn("скрыто в этом канале", out)

    def test_hide_lever_restores_the_old_behaviour(self):
        out = self._recall({"PRAXIS_RECALL_CROSS_ROOM": "hide"})
        self.assertNotIn("про волка", out)
        self.assertIn("1 совпадений из других комнат скрыто в этом канале", out)
        self.assertIn("про кота", out)

    def test_default_is_show(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRAXIS_RECALL_CROSS_ROOM", None)
            self.assertTrue(agent.recall_cross_room())



class FoldIsOfferedNotForced(unittest.TestCase):
    """Слово Егора 25.09: «минимум 150, при 250 или 400 свёртка — отдельная задача, которая ей
    ПРЕДЛАГАЕТСЯ»."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(ml, "_FOLD_OFFERS", Path(self.tmp.name) / "fold_offers.json")
        p.start(); self.addCleanup(p.stop)

    def test_plan_names_whether_the_reason_is_hard(self):
        soft = ml.plan_hot_fold(_rows(ml.GROUP_HOT_HI + 10), tape_chars=0, place="-100")
        self.assertTrue(soft["due"]); self.assertFalse(soft["hard"], soft)
        hard = ml.plan_hot_fold(_rows(ml.GROUP_HOT_HARD_HI + 5), tape_chars=0, place="-100")
        self.assertTrue(hard["due"]); self.assertTrue(hard["hard"], hard)
        forced = ml.plan_hot_fold(_rows(ml.GROUP_HOT_HI + 10), tape_chars=0, place="-100", force=True)
        self.assertTrue(forced["hard"])

    def _patches(self, rows):
        import contextlib
        return (mock.patch.object(ml, "adopt_place", lambda cid: str(cid)),
                mock.patch.object(ml, "_state_write_guard", lambda place: contextlib.nullcontext()),
                mock.patch.object(ml, "_load_state", lambda cid, rebuild=True: {"hot": list(rows)}),
                mock.patch.object(ml, "_drop_unprovable_inputs", lambda rows_, cid: list(rows_)))

    def test_soft_threshold_offers_instead_of_folding(self):
        rows = _rows(ml.GROUP_HOT_HI + 10)
        with mock.patch.dict(os.environ, {"PRAXIS_FOLD_OFFER": "on"}):
            with contextlib_all(self._patches(rows)):
                with mock.patch.object(ml, "_model_compact", side_effect=AssertionError("свернула сама")):
                    out = ml.compact_if_due("-100777")
        self.assertTrue(out.get("offered"), out)
        self.assertEqual(out["folded"], 0)
        offers = ml.fold_offers()
        self.assertIn("-100777", offers)
        self.assertEqual(offers["-100777"]["hi"], ml.GROUP_HOT_HI)
        self.assertEqual(offers["-100777"]["keep"], ml.GROUP_HOT_LO)
        self.assertEqual(offers["-100777"]["hard_hi"], ml.GROUP_HOT_HARD_HI)
        self.assertTrue(offers["-100777"]["since"])
        # окно вернулось в норму — предложение снято (стенды идут с рычагом off, поэтому on — явно)
        with mock.patch.dict(os.environ, {"PRAXIS_FOLD_OFFER": "on"}):
            with contextlib_all(self._patches(_rows(5))):
                ml.compact_if_due("-100777")
        self.assertNotIn("-100777", ml.fold_offers())

    def test_lever_off_folds_as_before(self):
        rows = _rows(ml.GROUP_HOT_HI + 10)
        with mock.patch.dict(os.environ, {"PRAXIS_FOLD_OFFER": "off"}):
            with contextlib_all(self._patches(rows)):
                with mock.patch.object(ml, "_model_compact", side_effect=AssertionError("свернула сама")):
                    with self.assertRaises(AssertionError):
                        ml.compact_if_due("-100777")

    def test_hard_threshold_folds_without_asking(self):
        rows = _rows(ml.GROUP_HOT_HARD_HI + 5)
        with mock.patch.dict(os.environ, {"PRAXIS_FOLD_OFFER": "on"}):
            with contextlib_all(self._patches(rows)):
                with mock.patch.object(ml, "_model_compact", side_effect=AssertionError("свернула сама")):
                    with self.assertRaises(AssertionError):
                        ml.compact_if_due("-100777")

    def test_fold_now_is_forced_and_clears_the_offer(self):
        ml._note_fold_offer("-100777", {"count": 410, "tokens": 5, "fold": 160})
        self.assertIn("-100777", ml.fold_offers())
        with mock.patch.object(ml, "compact_if_due", return_value={"ok": True, "folded": 160}) as cid:
            out = ml.fold_now("-100777")
        cid.assert_called_once_with("-100777", force=True)
        self.assertEqual(out["folded"], 160)
        self.assertNotIn("-100777", ml.fold_offers())

    def test_state_evidence_shows_the_offer(self):
        ml._note_fold_offer("-100777", {"count": 410, "tokens": 5, "fold": 160})
        block = agent.build_state_evidence_block(self_only=True)
        self.assertIn("fold_offers", block)
        self.assertIn("-100777", block)
        self.assertIn("memory_compact(action=fold", block)


def contextlib_all(patches):
    import contextlib
    stack = contextlib.ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


class PrivateRecordFloor(unittest.TestCase):
    RECORD = "- [private] развёлся в марте и просил никому не рассказывать об этом\n  и ещё строка"

    def _with(self, records, text):
        token = agent._PRIVATE_IN_FRAME.set(tuple(records))
        try:
            return agent.private_record_floor(text)
        finally:
            agent._PRIVATE_IN_FRAME.reset(token)

    def test_verbatim_piece_of_a_private_record_is_held(self):
        self.assertTrue(self._with([self.RECORD], "Он ведь развёлся в марте и просил никому не рассказывать!"))
        self.assertTrue(self._with([self.RECORD], "…РАЗВЁЛСЯ   В МАРТЕ И ПРОСИЛ никому…"))

    def test_paraphrase_and_owner_frame_pass(self):
        self.assertEqual(self._with([self.RECORD], "у него был развод, не спрашивай"), "")
        self.assertEqual(self._with([], "развёлся в марте и просил никому не рассказывать"), "")
        self.assertEqual(self._with([self.RECORD], "короткое"), "")

    def test_room_frame_remembers_its_private_records(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        people_dir = Path(tmp.name) / "people"; people_dir.mkdir()
        (people_dir / "guest.md").write_text(
            "# Гость\n\ntelegram_id: 42\n\n- любит чай\n- [private] развёлся в марте и просил никому не говорить\n",
            encoding="utf-8")
        patches = [mock.patch.object(people, "PEOPLE_DIR", people_dir),
                   mock.patch.object(agent, "_present_by_transport", return_value={"42"}),
                   mock.patch.object(agent, "_mentioned_slugs", return_value=[])]
        if hasattr(agent, "_epoch_lifted_dossier"):
            patches.append(mock.patch.object(agent, "_epoch_lifted_dossier", return_value=None))
        with contextlib_all(patches), mock.patch.dict(os.environ, {"PRAXIS_DOSSIER_PRIVATE_IN_ROOMS": "on"}):
            frame_layout.begin()
            self.addCleanup(frame_layout.reset)
            agent._participant_memory_block("Кто-то", _room_ctx(owner=False, known=False))
            self.assertTrue(agent.private_record_floor("да, развёлся в марте и просил никому не говорить"))
            self.assertIn("Не отправила", agent.tool_send_message("42", "он развёлся в марте и просил никому не говорить"))
            agent._participant_memory_block("Кто-то", agent.ChannelContext(
                chat_id="42", is_dm=True, owner=True, known=True, principal_id="42"))
            self.assertEqual(agent.private_record_floor("развёлся в марте и просил никому не говорить"), "")


class OwnerDmTurnsAreLabelledInRecall(unittest.TestCase):
    HITS = [
        {"path": "memory/self/turns-2026-09.jsonl", "text": "вошло: «Егор: у меня диагноз»", "source": "Praxis"},
        {"path": "memory/people/x.md", "text": "факт", "source": "x"},
    ]

    def _recall(self, ctx, env):
        token = agent._TURN_CHANNEL.set(ctx)
        try:
            with (mock.patch.object(agent.memory_index, "search", return_value=list(self.HITS)),
                  mock.patch.object(agent, "_active_chat", return_value=str(ctx.chat_id)),
                  mock.patch.dict(os.environ, env)):
                return agent.tool_recall("диагноз")
        finally:
            agent._TURN_CHANNEL.reset(token)

    def test_in_a_room_the_owner_dm_hit_is_named_or_hidden(self):
        room = agent.ChannelContext(chat_id="-100999", room_id="-100999", is_dm=False, owner=False,
                                    known=False, principal_id="7")
        out = self._recall(room, {"PRAXIS_RECALL_CROSS_ROOM": "show"})
        self.assertIn("из личной переписки с владельцем — внутреннее", out)
        out = self._recall(room, {"PRAXIS_RECALL_CROSS_ROOM": "hide"})
        self.assertNotIn("диагноз»", out)
        self.assertIn("скрыто в этом канале", out)

    def test_owner_dm_sees_its_own_turns_plain(self):
        owner = agent.ChannelContext(chat_id="809306689", is_dm=True, owner=True, known=True,
                                     principal_id="809306689")
        out = self._recall(owner, {"PRAXIS_RECALL_CROSS_ROOM": "show"})
        self.assertIn("диагноз", out)
        self.assertNotIn("из личной переписки", out)


class RoomTurnsAreInTheRecallCorpus(unittest.TestCase):
    def test_self_rooms_are_walked_unless_switched_off(self):
        import memory_fts
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        mem = Path(tmp.name)
        (mem / "self" / "rooms" / "-100777").mkdir(parents=True)
        (mem / "self" / "rooms" / "-100777" / "turns-2026-09.jsonl").write_text("{}\n", encoding="utf-8")
        (mem / "self" / "turns-2026-09.jsonl").write_text("{}\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"PRAXIS_INDEX_ROOMS": "1"}):
            got = {p.relative_to(mem).as_posix() for p in memory_fts._memory_files(mem, "*.jsonl", include_runs=False)}
        self.assertIn("self/rooms/-100777/turns-2026-09.jsonl", got)
        self.assertIn("self/turns-2026-09.jsonl", got)
        with mock.patch.dict(os.environ, {"PRAXIS_INDEX_ROOMS": "0"}):
            got = {p.relative_to(mem).as_posix() for p in memory_fts._memory_files(mem, "*.jsonl", include_runs=False)}
        self.assertNotIn("self/rooms/-100777/turns-2026-09.jsonl", got)
        self.assertIn("self/turns-2026-09.jsonl", got)


if __name__ == "__main__":
    unittest.main()
