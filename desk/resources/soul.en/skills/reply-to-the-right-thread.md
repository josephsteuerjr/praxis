# Check the thread separately from the text

Source snapshot: `split_reply_directive` removes the leading `ОТВЕТ->#<id>` directive and accepts only ids from the supplied allowed set. An unknown id returns `None` but does not prohibit sending.

In `_route_and_reply`, the allowed set comes from `plan.context.origin_message_ids`. Without an accepted directive, a group uses `channel.address_message_id` when `channel.addressed`; otherwise it uses `route.topic_id`. An id visible in read history is not necessarily allowed.

The final delivery path in `mtproto_runner.py` instead supplies ids from `reply_targets`; fallback uses `addressed_mid` for an addressed group message, otherwise the topic root. Do not transfer guarantees between these sets.

Practice:

1. Distinguish room, topic and parent message.
2. Replying to the original addressed message does not require a directive just to obtain a reply.
3. For another message, use an available explicit transport with verifiable topic and parent, or a confirmed valid directive. Do not guess: reading a neighbouring thread does not change the turn's destination.
4. Check the actual receipt. An omitted topic means no verification, not confirmation.

Directive parser tests do not establish correct delivery or equal id sets. Source inspection, parser testing and real transport testing provide different levels of evidence. Revisit this snapshot when the code changes.
