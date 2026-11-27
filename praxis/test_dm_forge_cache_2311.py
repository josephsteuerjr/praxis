import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dm_continuation as dm


class DmContinuation(unittest.TestCase):
    def test_accepted_anchor_survives_sliding_seed_but_not_edits_or_folds(self):
        rows = [dict(id=str(i), line=f"line {i}") for i in range(8)]
        with tempfile.TemporaryDirectory() as tmp, patch.object(dm.frame_epoch, "STORE", Path(tmp)/"epoch"), \
                patch.dict(os.environ, PRAXIS_FRAME_CONTINUATION="on", PRAXIS_KEAT="off",
                           PRAXIS_KEAT_CAPTURE="off"):
            def call(items, state="live-v6", accept=True, room="dm1"):
                selected, source = dm.select(room, items, 3)
                messages = [dict(role="user", content=r["line"]) for r in selected[:-1]]
                messages.append(dict(role="user", content="ephemeral-tail"))
                with dm.bind(source):
                    value, receipt = dm.measure(room, state, messages, [], "voice")
                    if accept:
                        dm.accept(value)
                return selected, receipt["reused_messages"]

            call(rows[:4], accept=False)
            self.assertFalse(dm._path("dm1").exists())
            self.assertEqual(call(rows[:5]), (rows[2:5], 0))
            self.assertEqual(call(rows[:6]), (rows[2:6], 2))
            rows[3]["line"] = "corrected"
            self.assertEqual(call(rows[:7]), (rows[2:7], 1))
            self.assertEqual(call(rows[4:]), (rows[5:], 0))
            self.assertEqual(call(rows, state="different-system")[1], 0)
            self.assertEqual(call(rows, room="dm2"), (rows[-3:], 0))
            persisted = dm._path("dm1").read_text()
            self.assertNotIn("ephemeral-tail", persisted)
            self.assertNotIn("corrected", persisted)


class ForgeAddress(unittest.TestCase):
    def test_actual_dispatch_scopes_review_and_merge_with_archived_prompts(self):
        import forge_worker as worker
        import llm
        req = dict(id="review-1", task_id="task-1", role="reviewer")
        def address(request):
            return worker._chat_with_transport_retry(request, request_path=Path("request"),
                    system="old frozen prompt", messages=[], tools=[], logical_iteration=1)
        def transport(*args, system, **kwargs):
            return llm.cache_address("model", system)
        with patch.object(worker, "_control"), patch.object(llm, "chat", side_effect=transport), \
                patch.dict(os.environ, PRAXIS_CACHE_KEY="on"):
            first = address(req)
            self.assertEqual(address(req), first)
            self.assertNotEqual(address(dict(req, id="review-2")), first)
            self.assertNotEqual(address(dict(req, task_id="task-2")), first)
            self.assertIn(":forge_merger:", address(dict(req, role="worker", source_update={"id":"x"})))
            self.assertNotIn("forge_", llm.cache_address("model", "private owner channel"))
