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

    def test_every_speaker_gets_the_identical_tool_schemas(self):
        # Егор 10.11: «ВЕЗДЕ ВСЕ ПРАВА» — owner-пары больше нет, поэтому схемы рук в комнате
        # совпадают байт-в-байт при любом говорящем, и кэш не делится на говорящих.
        owner = agent.catalog_tools_for(room_ctx(owner=True))
        for other in (agent.catalog_tools_for(room_ctx(owner=False)),
                      agent.catalog_tools_for(room_ctx(owner=False, known=False))):
            self.assertEqual(list(tool_offerings.offered_names(owner)),
                             list(tool_offerings.offered_names(other)))
            wire = lambda tools: json.dumps(llm.tools_to_openai(tools), ensure_ascii=False)
            self.assertEqual(wire(owner), wire(other))
        self.assertFalse({"admit", "computer_access"} & set(tool_offerings.offered_names(owner)))


if __name__ == "__main__":
    unittest.main()
