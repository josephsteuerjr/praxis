# Checking a forum topic address

Before sending to a group with topics:

1. Name the intended topic and establish its exact identifier. “Here” is insufficient when several threads are open.
2. Locate the source message. Answering in a neighbouring topic is a separate deliberate delivery, not an ordinary reply.
3. Check the current operation's schema: `reply` may inherit the turn's room, while `send_message` may accept an explicit destination. Do not guess topic syntax from an old skill.
4. Inspect the actual destination in the receipt. Check `chat_id`, topic and parent separately; transport success does not establish their correctness.

After a mistake, stop the reply chain at the wrong destination, briefly acknowledge it to the affected audience and correct delivery within existing authority. Do not delete others' messages or multiply sends with unknown outcomes.

Repeated misses call for checking transport, context and where this check belongs in the workflow. Counting incidents should help fix the cause, not collect promises to be more attentive.
