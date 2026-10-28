import base64
import hashlib
import re

"""The acting tool loop reads selected episodes, authors them, and sees its receipt."""
import json
import types
import unittest
from unittest import mock
import agent
import llm
import memory_life as ml
from test_coverage_vs_current import Base as MemoryBase, ROOM
from test_layer7 import Base as AgentBase, FakeResp

class CurrentTurn(AgentBase):
    def test_prepare_author_apply_and_next_model_call(self):
        memory = MemoryBase()
        memory.setUp()
        self.addCleanup(memory.tearDown)
        for i in range(1, 32):
            memory._msg(i, 'selected source '+str(i))
        recap = 'I read the selected conversation; the remaining tail is not covered.'
        seen = []
        class Client:
            def __init__(self):
                self.messages = self
                self.step = 0
            def create(client, **kw):
                seen.append(kw)
                results = [b for m in kw['messages'] if isinstance(m.get('content'),list)
                           for b in m['content'] if isinstance(b,dict) and b.get('type')=='tool_result']
                if client.step == 0:
                    args = {'action':'prepare','place':ROOM,'limit':3}
                elif client.step == 1:
                    prepared, _ = json.JSONDecoder().raw_decode(next(b['content'] for b in results if b.get('tool_use_id')=='fold-1'))
                    self.assertTrue(prepared['shown_complete'])
                    self.assertEqual(len(prepared['sources']),3)
                    self.assertTrue(all('selected source' in r['line'] for r in prepared['sources']))
                    args = {'action':'fold','place':ROOM,'selection':prepared['selection'],'text':recap}
                else:
                    receipt, _ = json.JSONDecoder().raw_decode(next(b['content'] for b in results if b.get('tool_use_id')=='fold-2'))
                    self.assertTrue(receipt['ok'],receipt)
                    self.assertEqual(receipt['recap'],recap)
                    self.assertEqual(ml.compact_text(receipt['compact_id'],ROOM),recap)
                    self.assertEqual(receipt['surface'],'episodic_memory_not_current_tool_history')
                    self.assertGreater(receipt['hot'],0)
                    return FakeResp('The authored recap is retained; unselected sources remain.')
                client.step += 1
                return types.SimpleNamespace(stop_reason='tool_use',content=[types.SimpleNamespace(
                    type='tool_use',id='fold-'+str(client.step),name='call',
                    input={'name':'memory_compact','args_json':json.dumps(args)})],
                    usage=types.SimpleNamespace(input_tokens=5,output_tokens=1))
        llm.use_test_client(Client())
        with mock.patch.multiple(ml, GROUP_HOT_LO=2, GROUP_HOT_HI=1000, GROUP_HOT_HARD_HI=1001, GROUP_HOT_TOKEN_CAP=1_000_000, GROUP_TAPE_CHARS=0), \
             mock.patch.object(ml,'_model_compact',side_effect=AssertionError('hidden helper')), \
             mock.patch.object(agent.stewardship,'check',return_value=None), \
             mock.patch.object(agent,'_pre_shell_dirt',return_value=set()), \
             mock.patch.object(agent,'_autocommit_self_edit'), \
             mock.patch.object(agent.serverd_client,'state_line',return_value=None), \
             mock.patch.object(agent.serverd_client,'available',return_value=False), \
             mock.patch.object(agent.body_client,'available',return_value=False):
            agent.voice_turn(None,'Author a recap of the selected room now.',speaker='Owner',is_owner=True)
        self.assertEqual(len(seen),3)
        self.assertTrue(list(ml.iter_events(chat_id=ROOM,kinds={'conversation_message'})))



    def test_long_prepare_is_read_completely_before_current_turn_authorship(self):
        memory = MemoryBase()
        memory.setUp()
        self.addCleanup(memory.tearDown)

        for i in range(1, 32):
            memory._msg(
                i, f"selected source {i}: " + "x" * 1800 + f" END-SOURCE-{i}")

        errors = []
        seen = []
        chunks = []
        state = {
            "phase": "prepare",
            "read_calls": 0,
            "offset": 0,
        }
        recap = (
            "I read the complete selected sources through END-SOURCE-1, "
            "END-SOURCE-2 and END-SOURCE-3. The remaining tail is not covered."
        )

        def tool_response(call_id, name, args):
            return types.SimpleNamespace(
                stop_reason="tool_use",
                content=[types.SimpleNamespace(
                    type="tool_use",
                    id=call_id,
                    name="call",
                    input={
                        "name": name,
                        "args_json": json.dumps(args),
                    },
                )],
                usage=types.SimpleNamespace(
                    input_tokens=5, output_tokens=1),
            )

        def result_text(kw, call_id):
            return next(
                block["content"]
                for message in kw["messages"]
                if isinstance(message.get("content"), list)
                for block in message["content"]
                if isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") == call_id
            )

        def decode_result(text):
            # _result_for_model appends a ResultRef marker even to an
            # otherwise-complete JSON response.
            return json.JSONDecoder().raw_decode(text)[0]

        def read_request():
            state["read_calls"] += 1
            state["read_id"] = f"read-{state['read_calls']}"
            return tool_response(
                state["read_id"],
                "read_run_result",
                {
                    "result": state["result_id"],
                    "run_id": state["run_id"],
                    "byte_offset": state["offset"],
                    # Keep the reader's own JSON below 4000 characters:
                    # requesting the whole source would externalize it again.
                    "byte_limit": 384,
                },
            )

        class Client:
            def __init__(client):
                client.messages = client

            def create(client, **kw):
                seen.append(state["phase"])
                try:
                    return client.next_response(kw)
                except Exception as exc:
                    errors.append((exc, exc.__traceback__))
                    # Do not throw into the provider retry/fallback layer.
                    return FakeResp("Test client stopped after a captured failure.")

            def next_response(client, kw):
                if state["phase"] == "prepare":
                    state["phase"] = "prepared"
                    return tool_response(
                        "prepare", "memory_compact",
                        {"action": "prepare", "place": ROOM, "limit": 3},
                    )

                if state["phase"] == "prepared":
                    shown = result_text(kw, "prepare")
                    self.assertIn("chars externalized", shown, shown)
                    marker = re.search(
                        r"\[ResultRef run=(\S+) id=(\S+) "
                        r"path=\S+ sha256=([0-9a-f]{64}) size=(\d+) ",
                        shown,
                    )
                    self.assertIsNotNone(marker, shown)
                    state.update(
                        run_id=marker.group(1),
                        result_id=marker.group(2),
                        sha256=marker.group(3),
                        size=int(marker.group(4)),
                        phase="reading",
                    )
                    self.assertGreater(state["size"], 4000)
                    return read_request()

                if state["phase"] == "reading":
                    shown = result_text(kw, state["read_id"])
                    self.assertNotIn("chars externalized", shown, shown)
                    page = decode_result(shown)
                    self.assertEqual(page["run_id"], state["run_id"])
                    self.assertEqual(page["sha256"], state["sha256"])
                    self.assertEqual(page["offset"], state["offset"])
                    chunk = base64.b64decode(
                        page["data_base64"], validate=True)
                    self.assertEqual(len(chunk), page["bytes"])
                    self.assertEqual(
                        page["next_offset"], state["offset"] + len(chunk))
                    self.assertGreater(len(chunk), 0)
                    chunks.append(chunk)
                    state["offset"] = page["next_offset"]
                    if not page["eof"]:
                        return read_request()

                    raw = b"".join(chunks)
                    self.assertEqual(len(raw), state["size"])
                    self.assertEqual(
                        hashlib.sha256(raw).hexdigest(), state["sha256"])
                    prepared = json.loads(raw.decode("utf-8"))
                    self.assertTrue(prepared["ok"], prepared)
                    self.assertTrue(prepared["shown_complete"])
                    self.assertEqual(prepared["kind"], "hot")
                    self.assertEqual(len(prepared["sources"]), 3)
                    for i, source in enumerate(prepared["sources"], 1):
                        self.assertIn(f"selected source {i}:", source["line"])
                        self.assertIn(f"END-SOURCE-{i}", source["line"])
                    state["selected_ids"] = [
                        source["id"] for source in prepared["sources"]]
                    state["phase"] = "folded"
                    return tool_response(
                        "fold", "memory_compact",
                        {
                            "action": "fold",
                            "place": ROOM,
                            "selection": prepared["selection"],
                            "text": recap,
                        },
                    )

                self.assertEqual(state["phase"], "folded")
                receipt = decode_result(result_text(kw, "fold"))
                self.assertTrue(receipt["ok"], receipt)
                self.assertEqual(receipt["source_ids"], state["selected_ids"])
                self.assertEqual(receipt["recap"], recap)
                self.assertEqual(receipt["folded"], 3)
                self.assertEqual(receipt["hot"], 28)
                self.assertEqual(
                    ml.compact_text(receipt["compact_id"], ROOM), recap)
                state["receipt"] = receipt
                state["phase"] = "done"
                return FakeResp("Complete sources read; authored recap retained.")

        llm.use_test_client(Client())
        with mock.patch.multiple(
            ml,
            GROUP_HOT_LO=2,
            GROUP_HOT_HI=1000,
            GROUP_HOT_HARD_HI=1001,
            GROUP_HOT_TOKEN_CAP=1_000_000,
            GROUP_TAPE_CHARS=0,
            _FOLD_OFFERS=memory.tmp / "memory/.state/fold_offers.json",
        ), mock.patch.object(
            ml, "_model_compact",
            side_effect=AssertionError("hidden helper must not author"),
        ), mock.patch.object(
            agent.stewardship, "check", return_value=None,
        ), mock.patch.object(
            agent, "_pre_shell_dirt", return_value=set(),
        ), mock.patch.object(
            agent, "_autocommit_self_edit",
        ), mock.patch.object(
            agent.serverd_client, "state_line", return_value=None,
        ), mock.patch.object(
            agent.serverd_client, "available", return_value=False,
        ), mock.patch.object(
            agent.body_client, "available", return_value=False,
        ):
            agent.voice_turn(
                None, "Read every selected source, then author its recap.",
                speaker="Owner", is_owner=True,
            )

        if errors:
            exc, traceback = errors[0]
            raise exc.with_traceback(traceback)
        self.assertEqual(state["phase"], "done", state)
        self.assertGreater(state["read_calls"], 1)
        self.assertEqual(len(seen), state["read_calls"] + 3)
        self.assertEqual(
            {row["id"] for row in ml.rebuild_state(ROOM)["hot"]},
            {
                row["id"]
                for row in ml.iter_events(
                    chat_id=ROOM, kinds={"conversation_message"})
            } - set(state["selected_ids"]),
        )


class AuthoredTier(MemoryBase):
    def test_due_tier_with_overlapping_bounds_keeps_hot_tail_after_rebuild(self):
        children = []
        leaf_ids = set()

        # Disjoint events, overlapping time windows. The FIRST child ends
        # later than the LAST selected child: list-edge bounds are invalid.
        for k in range(8):
            first = self._msg(
                2 * k + 1, f"child {k} first", ts=100.0 + k * 10)
            last = self._msg(
                2 * k + 2, f"child {k} last",
                ts=10_000.0 if k == 0 else 200.0 + k * 10)
            children.append(self._compact(
                [first, last], summary=f"complete child recap {k}"))
            leaf_ids.update((first["id"], last["id"]))

        tail = [
            self._msg(1000 + k, f"unfolded hot tail {k}", ts=20_000.0 + k)
            for k in range(150)
        ]
        tail_ids = {row["id"] for row in tail}
        recap = (
            "I combined four selected child recaps, retaining their "
            "overlapping chronology. The hot tail is not covered."
        )

        # Explicit fixture policy, not a mocked plan or graph.
        with mock.patch.multiple(
            ml,
            TIER_LO=4,
            TIER_HI=8,
            GROUP_HOT_LO=150,
            GROUP_HOT_HI=250,
            GROUP_HOT_HARD_HI=300,
            GROUP_HOT_TOKEN_CAP=1_000_000,
            GROUP_TAPE_CHARS=0,
            _FOLD_OFFERS=self.tmp / "memory/.state/fold_offers.json",
        ), mock.patch.object(
            ml, "_model_compact",
            side_effect=AssertionError("hidden helper must not author"),
        ):
            before = ml.rebuild_state(ROOM)
            self.assertEqual(
                {row["id"] for row in before["hot"]}, tail_ids)
            self.assertEqual(
                {row["id"] for row in before["frontier"]},
                {child["id"] for child in children},
            )
            normal = ml.plan_hot_fold(
                before["hot"], force=False,
                tape_chars=ml.tape_chars_for(ROOM), place=ROOM,
            )
            self.assertFalse(normal["due"], normal)

            prepared = ml.prepare_authored_fold(ROOM, limit=50)
            self.assertTrue(prepared["ok"], prepared)
            self.assertEqual(prepared["kind"], "tier", prepared)
            self.assertTrue(prepared["shown_complete"])
            self.assertEqual(prepared["remaining_unselected"], 0)
            self.assertEqual(len(prepared["sources"]), 4)
            selected_ids = [row["id"] for row in prepared["sources"]]
            self.assertEqual(
                selected_ids, [child["id"] for child in children[:4]])
            self.assertEqual(
                [row["text"] for row in prepared["sources"]],
                [f"complete child recap {k}" for k in range(4)],
            )

            receipt = ml.apply_authored_fold(
                ROOM, prepared["selection"], recap)
            self.assertTrue(receipt["ok"], receipt)
            self.assertEqual(receipt["folded"], 4)
            self.assertEqual(receipt["source_ids"], selected_ids)
            self.assertEqual(receipt["hot"], 150)
            self.assertEqual(receipt["recap"], recap)
            self.assertEqual(
                ml.compact_text(receipt["compact_id"], ROOM), recap)

            # Reconstruct from canonical artifacts, not the state just saved
            # by apply_authored_fold.
            rebuilt = ml.rebuild_state(ROOM)
            self.assertEqual(
                {row["id"] for row in rebuilt["hot"]}, tail_ids)
            expected_frontier = {
                receipt["compact_id"],
                *(child["id"] for child in children[4:]),
            }
            self.assertEqual(
                {row["id"] for row in rebuilt["frontier"]},
                expected_frontier,
            )

            evidence = self._evidence()
            parent = evidence["compacts"][receipt["compact_id"]]
            self.assertEqual(parent["tier"], 2)
            self.assertEqual(parent["depth"], 2)
            self.assertEqual(parent["event_count"], 8)
            self.assertEqual(parent["source_event_ids"], [])
            self.assertEqual(parent["source_compact_ids"], selected_ids)
            self.assertEqual(
                parent["first_ts"],
                min(child["first_ts"] for child in children[:4]),
            )
            self.assertEqual(
                parent["last_ts"],
                max(child["last_ts"] for child in children[:4]),
            )
            self.assertNotEqual(
                parent["last_ts"], children[3]["last_ts"],
                "fixture must distinguish max bound from list edge",
            )
            resolved = ml.memory_provenance.compact_evidence(
                receipt["compact_id"], evidence)
            self.assertTrue(resolved["valid"], resolved)
            expected_leaves = {
                event_id
                for child in children[:4]
                for event_id in child["source_event_ids"]
            }
            self.assertEqual(set(resolved["leaves"]), expected_leaves)
            self.assertTrue(expected_leaves <= leaf_ids)
            self.assertTrue(expected_leaves.isdisjoint(tail_ids))
            self.assertIn(recap, ml.context_summary(ROOM))


if __name__=="__main__": unittest.main()
