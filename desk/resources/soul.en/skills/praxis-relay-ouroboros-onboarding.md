# Using a ChatGPT subscription through Relay

This is a connection route, not a permanent release snapshot. Establish product, OS and version first: Hélène's embedded relay and standalone praxis-relay for Ouroboros have different controls and distribution.

In Hélène, use its subscription settings and offered account sign-in. Do not install a second relay or stop the embedded one to follow an old menu. Take options, supported builds and sign-in methods from the current application.

For a separate client:

1. Check the current official release and documentation. Download the build matching OS/architecture; do not promise missing artifacts.
2. Complete available OAuth/device sign-in to your own account. Never share credentials; check external Python requirements by version rather than treating them as universal.
3. Start the relay and keep it running for the client.
4. In Ouroboros, choose the supported OpenAI-compatible provider and actual local base URL. `http://127.0.0.1:5011/v1` is an example standalone relay default, not another computer's address or a guaranteed Hélène setting.
5. Check key requirements and model prefixes in the client/relay. Do not use another live token as a decorative string. Check available models through `/v1/models`, not an old list.

Diagnose when needed: `/health` checks the local server; `/v1/limits` provides available quota information. Healthy status does not prove provider acceptance. On quota exhaustion, supported switching among already authorised accounts may help; never replay a partially completed request or promise infinite capacity. If every suitable quota is spent, show the cause and recovery time when known.

Before stopping, distinguish your standalone process from the application's relay and other services. Revoke access through the supported flow; do not erase an arbitrary auth directory from an old path.

This is an unofficial bridge to your own ChatGPT account, not an OpenAI API account or a conversion of a Claude subscription into Anthropic API. Plans and upstream behaviour can change; check current primary sources when helping someone.
