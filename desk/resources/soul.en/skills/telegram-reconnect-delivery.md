# Telegram timeouts and actual delivery

During Telethon reconnection, sending can outlive a synchronous-wrapper
timeout and later complete through a durable outbox. Distinguish the
wait error from the send outcome.

First inspect receipt/outbox and history at the exact destination.
If a real Telegram message_id exists or the message is visible, do not
send again. If the outcome is unknown, preserve and investigate that
uncertainty. TimeoutError proves neither delivery nor its absence.

Check background computation separately: an interrupted turn and a live
process are different. Use the action's supported idempotency for retries.
A long error series requires transport and code investigation; reconnecting
does not explain every fault in advance.
