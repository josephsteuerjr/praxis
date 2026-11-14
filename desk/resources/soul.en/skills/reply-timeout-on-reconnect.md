# Unknown delivery after reconnection

A reply `TimeoutError` may mean synchronous waiting ended while a
durable-outbox operation continues. The exception alone proves neither
reconnection as the cause nor eventual delivery.

Inspect receipt, outbox and exact destination through available `read_chat`.
If delivery is confirmed or visible, do not duplicate it. If unknown,
retain state and ID and use supported polling. A pending follow-up
is not automatically closed; check actual status and the recipient's
current response.

Investigate a long failure series in transport and code. Check background
computation separately: a send failure may end a turn without ending
its process. A new run does not inspect the first. Preserve unrelated
processes and tasks during diagnosis.
