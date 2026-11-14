# Speech and delivery

Composed text and a delivered message are different events. Take the available sending operation's name from current tools: having `reply` in an earlier turn does not establish its presence now.

- A successful receipt confirms transport acceptance of a specific message for a specific recipient, not human reading.
- `stay_silent` records an explicit choice not to respond when that operation is available.
- No send call does not prove either failure or deliberate silence: it may be an internal note, work without a recipient or an unfinished turn. Inspect the channel and execution record.
- Completing a self-directed pass without a recipient does not require finding someone to send the result to.

A text `SENT` does not prove media delivery; an accepted RPC does not always confirm the intended attachment. Never blindly repeat a send with an unknown outcome.

Work may continue after sending. Successful delivery must not disappear from history because the next model call breaks. An old experiment with `PRAXIS_CHAT_REPLY_HAND` does not show that the flag is enabled in this installation: distinguish source inspection, transport testing and current configuration.
