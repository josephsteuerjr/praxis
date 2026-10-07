# Telegram transport contract

Received messages are recorded before the address and sender-admission gates. An ordinary group message becomes context without starting a model turn. The current agent name, authenticated Telegram display name and username are matched with word boundaries. A reply can wake only when the original sender ID is the authenticated agent's own ID. Admission policy remains separate.

Startup and reconnection run in one receiving thread. Initial network failure does not disable Telegram for the lifetime of the window. The Bot API offset advances only after intake succeeds; recording errors retain the update for retry. Model readiness does not indicate Telegram health.

Archives store real Telegram message IDs. Replayed posts update one row; edits update the mirrored row. MTProto also records outgoing posts made from another device and processes deletion updates. After a reconnect, MTProto reconciles the gap from persisted IDs before processing pending turns. The first import uses `telegram.history_initial_limit` (default 100 recent messages per dialogue); later gap reads have no such limit. Historical imports never launch the model. Telegram deletion updates without a peer can be reconciled safely only in private dialogues.

Bot API reconnects consume the retained `getUpdates` queue. Telegram does not provide bots with arbitrary chat history or deletion updates; changing client code cannot remove these upstream limits. The local archive therefore mirrors updates actually available to that bot. Group privacy settings must permit ordinary human messages to reach the transport.

Optional proxy configuration lives in the active agent's `helene.json`:

```json
{"telegram":{"proxy":{"enabled":true,"url":"https://your-server/helene/telegram","key":"your-private-access-key"}}}
```

The Python gateway carries Bot API requests over HTTPS and opaque MTProto bytes over WSS. The client verifies TLS and never falls back silently to direct Telegram when the selected proxy fails. Keys are local configuration, never release contents. Save proxy settings before account login; changing the route applies when the agent restarts.

Server recipe: install `desk/server/telegram_gateway.py` in a separate Python environment with aiohttp, place a restricted access key in `/opt/helene-telegram/access.key`, and install `helene-telegram-gateway.service`. Expose `/helene/telegram/*` through the existing TLS reverse proxy to the loopback listener on port 8099. Both HTTP and WebSocket endpoints require the key. TCP targets are limited to Telegram's published address ranges and transport ports.

Candidate acceptance: passive human text in the actual group appears in the window archive; the next addressed turn sees it; current name, exact username and own reply wake an admitted sender; renaming removes old address aliases; VPN loss and offline startup recover independently; the proxy works with VPN disabled; the window retains its existing design. Synthetic tests and a successful gateway handshake do not replace this installed acceptance.
