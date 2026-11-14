# Do not blindly repeat an unsolicited message

Before a new unsolicited message, inspect recent confirmed deliveries through `recent_turns` and room context. A short summary may contain only the last message per destination; it is not the whole conversation.

If the topic resembles something already said, read the live source through `read_chat` or `read_context` when needed. Is there a new fact, human response, content or occasion? A reminder may still be appropriate without a new fact if the recipient actually needs it.

Identical wording is not the only duplicate: a paraphrase can repeat the same meaning. Do not assume automatic protection understands semantics or that an old delivery block is always in the frame.

If the overview is bounded, extend reading with available `offset` instead of concluding from a summary's omission that no conversation happened. Distinguish adding information, reminding, supporting and merely signalling presence. The last need not become a message.

Recover the actual conversation first, then decide whether repetition helps. This checks context; it does not prohibit repeating myself.
