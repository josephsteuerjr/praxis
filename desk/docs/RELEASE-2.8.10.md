# Hélène 2.8.10 Beta

- **Network and proxy** groups the routes for Telegram and Mini App, the subscription relay, and the built-in SSH bridge. Each outgoing route can use a different proxy. If Tray Bridge is already running, its local HTTP address can be filled in for either route.
- The subscription relay can check its saved route to ChatGPT without signing in or generating a response. It reports connection failures, timeouts, redirects and unsupported relay versions separately. A response that asks for authentication confirms that the network route reached ChatGPT.
- Agent placement and management remain available under **Program → Agent placement**.

Desktop packages include the updated subscription relay. After saving a change to the Telegram and Mini App route, restart the engine to apply it.
