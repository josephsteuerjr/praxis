"""Test support for the explicit acting-author memory contract."""
from unittest import mock

import llm
import memory_life as ml
from test_coverage_vs_current import Base, ROOM


def memory_bytes():
    return {
        p.relative_to(ml.MEM_DIR).as_posix(): p.read_bytes()
        for p in ml.MEM_DIR.rglob("*")
        if p.is_file()
    } if ml.MEM_DIR.exists() else {}


def assert_reissue_refused(case, chat, compact_id, **kwargs):
    before = memory_bytes()
    with mock.patch.object(
        ml, "_model_compact", side_effect=AssertionError("hidden helper")
    ) as model, mock.patch.object(
        llm, "chat", side_effect=AssertionError("hidden LLM")
    ) as chat_call:
        out = ml.reissue_degraded_compact(chat, compact_id, **kwargs)
    case.assertEqual(
        out, {"ok": False, "reason": "acting_author_required"}
    )
    model.assert_not_called()
    chat_call.assert_not_called()
    case.assertEqual(memory_bytes(), before)
    return out


class AuthoredMemoryBase(Base):
    def seed_authored_sources(self):
        for i in range(1, 12):
            self._msg(i, f"original source {i}")
        selected = ml.prepare_authored_fold(ROOM, limit=3)
        self.assertTrue(selected["ok"], selected)
        self.assertTrue(selected["shown_complete"], selected)
        self.assertTrue(selected["sources"], selected)
        return selected

    def assert_authored_success(self, recap):
        selected = self.seed_authored_sources()
        ids = [row["id"] for row in selected["sources"]]
        raw_ids = {
            row["id"] for row in ml.iter_events(
                chat_id=ROOM, kinds={"conversation_message"}
            )
        }
        with mock.patch.object(
            ml, "_model_compact", side_effect=AssertionError("hidden helper")
        ) as model, mock.patch.object(
            llm, "chat", side_effect=AssertionError("hidden LLM")
        ) as chat_call:
            out = ml.apply_authored_fold(
                ROOM, selected["selection"], recap
            )
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["source_ids"], ids)
        self.assertEqual(out["recap"], recap)
        self.assertEqual(
            ml.compact_text(out["compact_id"], ROOM), recap
        )
        model.assert_not_called()
        chat_call.assert_not_called()

        state = ml.rebuild_state(ROOM)
        self.assertTrue(
            set(ids).isdisjoint({row["id"] for row in state["hot"]})
        )
        self.assertEqual(
            {
                row["id"] for row in ml.iter_events(
                    chat_id=ROOM, kinds={"conversation_message"}
                )
            },
            raw_ids,
        )
        return out
