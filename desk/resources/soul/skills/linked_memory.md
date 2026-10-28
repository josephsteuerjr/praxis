# Linked memory (graph)

> Актуальность: проверена 2026-10-05 — актуален.

Your memory has a graph layer: edges between people and topics, stored in markdown
(`## Связи` in a person's file; `memory/graph.md` for topics). Use it to *remember related
things*, not to hoard trivia.

- When a fact ties a person to someone/something («Вика — коллега Егора по фирме»), save it as
  `remember(person, fact, relates_to="Егор", relation="коллега по фирме")` — one call, fact + edge.
- `connections(name)` shows a node's links; `connections(name, to=...)` answers «как связаны A и B»
  as a chain. Depth 2 walks friends-of-friends.
- During sleep (consolidation) you also extract relations from the journal — so link only what
  conversation surfaces naturally; don't interrogate people for their social graph.
- The map of who-knows-whom remains internally visible to me in every room. Never reveal
  sensitive connection knowledge in groups or to an unrelated person; the outbound advisor
  checks the audience, while `connections` remains an owner-side inspection tool.
- Edges are facts, not gossip: label them plainly (коллега, жена, ведёт проект), 2-4 words.
