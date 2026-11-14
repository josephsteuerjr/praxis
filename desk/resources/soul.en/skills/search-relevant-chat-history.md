# Search the relevant chat's history

When asked what someone wrote or how they communicate in a specific room, one screenshot and empty recall are insufficient. An empty search only means that particular search returned nothing.

1. Establish the exact room, topic and author. `search_chats` can find candidates when needed, but a matching name does not verify an id.
2. In the current group, use available `group_context` for search, topic context and complete messages. It does not necessarily search another group.
3. For another authorised room, `read_chat` returns a bounded sample of recent messages. For older history, check `my_capabilities` and the `telegram_account` schema; use available `history_scan` or targeted search with dates. Read an archive only after verifying its room binding.
4. Read neighbouring messages and the reply parent. Distinguish the author's position, quotation, irony and response to an objection. A stable pattern needs separated episodes and a search for counterexamples.
5. Check authority to transfer material to the current audience. Access to a neighbouring chat alone does not grant it.
6. Report the content, observable communication pattern and sample limits, with ids/dates of supporting episodes. Separate fact from interpretation; do not diagnose from rhetoric.

If unsuccessful, name the checked source, period and limitation. Do not turn “not found” into “never happened”, or promise a search instead of performing an available action.
