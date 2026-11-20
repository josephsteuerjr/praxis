# Hélène 2.8.11 Beta

- **The SSH bridge sets up with one button.** «Connect and check» saves the SSH connection, proves the proxy through a temporary bridge and only then saves the bridge. If the server fingerprint has to be confirmed, the same action continues after the confirmation.
- **No more collision with Tray Bridge.** An empty local port takes a free one between 18888 and 18999; 8888, where Tray Bridge and manual tunnels usually listen, is no longer the default. An explicitly chosen busy port is refused before anything is saved.
- **Bridges can be removed or repaired.** A saved bridge can be deleted or moved to a free local port; a saved SSH connection can be deleted once no bridge uses it. Files and services on the server are not touched. A bridge selected for an agent must first be replaced in **Network and proxy**.
- **Clear choices and answers.** «Server already configured» and «Configure server automatically» are a visible choice. **Fill in Tray Bridge** says what it filled, that saving applies it, and whether 127.0.0.1:8888 actually passes HTTPS.
- **Keys as people copy them.** A key path copied with quotes is accepted. The key chooser says it is opening. A lost SSH handshake or proxy request is retried once before an error is shown.

Only the desktop application changed; the agent core and the subscription relay are the same as in 2.8.10.
