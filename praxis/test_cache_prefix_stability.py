# -*- coding: utf-8 -*-
"""The cache prefix must not split merely because a different room participant spoke."""
from __future__ import annotations

import json
import unittest
from unittest import mock

import agent
import llm
import tool_offerings


ROOM_ID = "-1001240718803"


def room_ctx(*, owner: bool, known: bool = True) -> agent.ChannelContext:
    return agent.ChannelContext(chat_id=ROOM_ID, room_id=ROOM_ID, is_dm=False,
                                owner=owner, known=known)


def system_for(ctx: agent.ChannelContext) -> str:
    # Frame construction normally observes the live server body.  This regression is
    # about deterministic audience/tool structure, so isolate it from that body.
    with (mock.patch.object(agent, "build_state_block", return_value=""),
          mock.patch.object(agent, "build_state_evidence_block", return_value="")):
        persona, dynamic, _ = agent._build_prompt_parts(
            speaker="speaker", query="hello", ctx=ctx)
    return persona + dynamic


def first_seam(left: list[str], right: list[str]) -> int:
    return next((index for index, pair in enumerate(zip(left, right))
                 if pair[0] != pair[1]), min(len(left), len(right)))


class CachePrefixStability(unittest.TestCase):
    def test_every_speaker_in_one_room_uses_its_room_cache_address(self):
        frames = [system_for(room_ctx(owner=True)),
                  system_for(room_ctx(owner=False)),
                  system_for(room_ctx(owner=False, known=False))]
        self.assertTrue(all("audience_key=room" in frame for frame in frames))
        self.assertEqual(
            {llm.cache_address("gpt-5.6-terra", frame) for frame in frames},
            {f"praxis:gpt-5.6-terra:room:{ROOM_ID}"},
        )

    def test_owner_only_tools_are_after_the_shared_schema_prefix(self):
        owner = agent.offered_tools_for(room_ctx(owner=True))
        other = agent.offered_tools_for(room_ctx(owner=False))
        owner_names = list(tool_offerings.offered_names(owner))
        other_names = list(tool_offerings.offered_names(other))
        shared = [name for name in owner_names if name in set(other_names)]

        self.assertGreaterEqual(first_seam(owner_names, other_names), len(shared) - 1)
        owner_only = [name for name in owner_names if name not in set(other_names)]
        self.assertEqual(owner_only, ["admit", "computer_access"])

        wire = lambda tools: json.dumps(llm.tools_to_openai(tools), ensure_ascii=False)
        owner_wire, other_wire = wire(owner), wire(other)
        common = next((i for i, pair in enumerate(zip(owner_wire, other_wire))
                       if pair[0] != pair[1]), min(len(owner_wire), len(other_wire)))
        self.assertGreater(common / len(owner_wire), 0.95)


if __name__ == "__main__":
    unittest.main()
