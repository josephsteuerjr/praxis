# Telegram history archaeology

`evidence_source` is an old source supporting a conclusion. `live_target` is the independently established current destination for a new message. The former never automatically becomes the latter. Old messages, follow-ups, retrieval matches and open threads are not new invitations to reply.

Before sending, identify the basis: a current incoming request, an explicit request to revisit a topic or a deliberate authorised initiative. Independently establish exact peer, topic/thread and `reply_to`. Read the fresh tail and local chain: context may have changed during research. Without a recipient, retain the finding internally; when deliberately returning, state the transition instead of technically replying to the old source by default.

Start with the exact room and a narrow sample:

- Known peer and `message_id`: read the message, parents and nearby window.
- Known phrase: available `messages.SearchRequest` with its exact schema.
- Specific author: filter `from_id` while retaining others' reply context.
- A conversation: collect thread and time range, not just one author's corpus.
- Unfamiliar schema: inspect `telegram_account` registry search and description first.

Page in bounded chunks. Retain `chat_id`, topic/`top_msg_id`, chain root, message id, UTC time, sender and window. Do not merge neighbouring topics. A sender need not be the author of quoted, forwarded or pasted text. Text/project ownership needs a direct or technical source; otherwise it is a hypothesis.

Distinguish `found`, `zero_within_bounded_coverage`, `partial`, `tool_error` and `peer_unresolved`. Only a bounded zero supports a negative conclusion within stated limits. For “first”, “last” or “never”, track range, pages, reached boundary, errors and gaps; an ordinary local answer needs no complete scan.

Provide a minimal verifiable source address and separate quotation, summary and inference. Transfer only to an authorised audience; even public availability does not justify pointless bulk disclosure of others' history.

Stop at the required precision or when further coverage would no longer change the wording.
