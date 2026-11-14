# Forum replies: check the destination separately

Correct text can reach the wrong thread. Before sending, distinguish the peer, topic and parent message; reading a neighbouring topic does not change the current turn's destination.

In the current code, `_route_and_reply` parses `ОТВЕТ->#<id>` against the allowed `origin_message_ids`. If the directive is not accepted, an addressed group turn uses `address_message_id`; otherwise it uses `route.topic_id`. An unknown id does not stop sending: fallback occurs. Reading a message does not itself establish that its id is allowed.

For another thread, first read its context through `group_context`. Then choose an available operation with an explicit, verifiable destination and `reply_to`, or a confirmed valid directive. Do not present an old address format as universal across transports.

Check the receipt: actual peer, topic and parent, when provided. If the receipt omits the topic, that part of delivery is unverified. Fix routing defects in code; a habit cannot compensate for an incorrect transport binding.
