# Hélène 2.8.12 Beta

- **Network and proxy in two steps.** ① *Where the proxy comes from* lists your Hélène bridges. Each one says which local proxy it serves (127.0.0.1:PORT), its HTTPS address if it has one, and who goes through it. ② *Who goes through it* lets Telegram and Mini App, and the ChatGPT relay, each choose their own route: system settings, direct, one of your bridges, or a custom proxy.
- **No extra steps.** «Use for this agent» and «Refresh servers» are gone. A bridge is chosen in a route and saved like any other setting, and the list follows a connecting bridge by itself. The old «Telegram through server» gateway switch is shown only where it is still switched on.
- **The relay can use a bridge on its own.** A bridge chosen only by the ChatGPT relay is kept running, and the relay restarts by itself when its route changes.
- **Tray Bridge stays an external program.** «Fill in Tray Bridge · 127.0.0.1:8888» is under «Custom proxy» in each route and still checks that the port passes HTTPS.
- **Mini App HTTPS is chosen in the Mini App card**, and choosing it does not reroute Telegram. A bridge added on the same screen appears there and in both routes without reopening settings.
- **Onboarding** applies a checked bridge to Telegram, the relay or both.

Only the desktop application changed; the agent core and the subscription relay are the same as in 2.8.11.
