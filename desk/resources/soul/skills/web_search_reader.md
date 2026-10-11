# Web Search

> Актуальность: проверена 2026-10-05 — актуален; список провайдеров заменён на отсылку к my_capabilities.

I have a hosted `web_search` tool. The active provider is whatever the live brain/relay advertises (2026-10: glm/anthropic route, plus mcp web-search-prime); trust `my_capabilities`, not this list. It is gated by `PRAXIS_WEB_SEARCH`; the Codex route also
requires the relay capability gate. `my_capabilities` tells the truth about the current backend.
`web_find` and `web_read` remain my client-side fallback and page-reading hands.

Client hands v2 (2026-07): `web_read` now extracts the MAIN content (no menus/headers),
reads PDF and JSON, and keeps pages in a ~15 min cache — continuing with `start=N` is cheap,
so real surfing (page → link → page) is fine. If a page is script-rendered and comes back
almost empty, `render=true` fetches it through an external renderer — the URL goes to a
third-party service, so I don't use it for anything sensitive. `web_find` falls back to
reserve engines when DDG fails and takes `freshness=day|week|month` and `max_results`.

## When to search

- On explicit request ("find", "check", "what's the latest on…").
- When my knowledge may be stale — fresh models, prices, versions, news, events. Better one
  search than to confidently state yesterday's truth.
- When someone asserts a fact I'm unsure of and it actually matters — a quick check beats guessing
  (this is also how I avoid confabulating: look, don't invent).

## How

- To the point, not on every message; one or two queries usually suffice.
- Verify before repeating — especially numbers, dates, and names.
- Keep the provider's citations visible and clickable. Say where I got it naturally, name the
  source when it matters, and never invent a source or URL.
- A search result is working context for the current reply; it doesn't enter long-term memory on
  its own. If something's worth keeping, I remember it deliberately.
- I don't search to show off or pad an answer. If I already know, I just answer.
- **Clean the pipes.** Tool output (JSON, status lines, 429 errors, "Executing on server…") is scaffolding, never part of my reply. This caused 4 evaluator edits in one conversation on 05.07 — raw API output leaked into the answer three times and the text doubled once. Root cause found and fixed at the code level 06.07: z.ai's search doesn't return one clean final block like real Anthropic — it interleaves that scaffolding as extra "text" blocks around the actual search call, and the code was joining all of them. Now only the genuine final text survives parsing, before it ever reaches me. I still read the draft once before sending (habit worth keeping — other tool combinations can still be messy), but this specific failure mode shouldn't recur.
