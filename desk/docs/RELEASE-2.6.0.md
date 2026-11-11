# Hélène 2.6.0 Beta

This is a beta release, published as Latest so the built-in updater can find it.
Keep a backup of your agent before updating. Existing agents keep their data and
authored source changes through the application's normal update workflow.

## Changes

- Canonical editing and Python tools, live nested tool calls from Python, explicit
  execution outcomes, and native process ownership on Windows, macOS and Linux.
- The right-hand action panel and phone interface show arguments, actual execution,
  request JSON and response JSON. Phone history loads beyond forty turns. Subagents
  remain closed until opened, below the conversation turns.
- Install a new server agent or update an existing server from the application over
  SSH. The connection verifies the server fingerprint before sending credentials.
  A new server agent is found through its Telegram bot's menu.
- A separate Linux ARM64 server archive includes a native headless subscription
  relay. It is a Docker server delivery, not an ARM Linux desktop application.
- Cleanup of old programme files no longer requests administrator privileges.
  Protected files are retained and reported while the new application keeps running.
  Process and data ownership checks still apply during the actual handoff.
- macOS launches recover the original complete folder when App Translocation moves
  the Finder application. The launcher preserves its signed process identity.
- Windows startup, backup-proxy routing, lock recovery, Telegram archive durability
  and bounded Forge view polling include the previously qualified local fixes.
- The private-output evaluator and extra confirmations for critical Telegram
  actions are removed as requested. Credential protection and redaction remain.

## Beta qualification limits

The new server installer has not been accepted on a user's production server, and
discovery through a real Telegram bot still needs end-to-end acceptance. ARM64
Docker runtime checks are separate from acceptance on a particular Debian host.
The macOS launch fix was reproduced on Apple Silicon with macOS 15.8.2; Ivan's
physical M4 on 15.8.1 has not yet been rechecked. macOS packages are ad hoc signed,
not notarized. Translocated computer-control binaries, TCC, physical microphones,
CUDA and paid providers remain separate checks. Local Whisper and XTTS remain
unavailable on Intel Mac Python 3.14.

Focused and platform results are documented with the release artifacts. Historical
whole-core failures remain explicitly separate; this release does not claim a
clean whole-core test run or physical-device acceptance.
