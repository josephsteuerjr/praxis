# Praxis Relay

A local OpenAI-compatible API backed by your ChatGPT subscription. A native
**Tauri desktop app** and a separate **headless executable** share one Rust
service for login, logout, quotas and account switching.

The relay is an independent program. The displayed Codex version identifies the
upstream protocol; Codex CLI is not an executable dependency.

## Desktop

Run `praxis-relay-desktop.exe`. The Russian interface provides two account slots,
browser sign-in/cancellation, replacement sign-in, logout, deliberate switching,
per-account quotas, API start/stop, port settings and session activity.
Sign in and start the API, then copy the base URL into your client.

Minimize and close hide the native window from the taskbar into its tray icon.
Click the icon to restore it. Its **Exit and stop the relay** item quits the app.
There is no companion console window. A failed tray initialization cannot hide
the window and strand the running program.

## Headless

`praxis-relay-headless` has no Tauri, WebView, tray or display dependency.

```text
praxis-relay-headless serve --headless
praxis-relay-headless status
praxis-relay-headless login primary
praxis-relay-headless login secondary
praxis-relay-headless switch secondary
praxis-relay-headless limits primary
praxis-relay-headless logout primary
praxis-relay-headless logout all
praxis-relay-headless cancel_login
praxis-relay-headless stop
```

Use a second terminal for controls while the service runs. Applicable commands
also work offline. `login` defaults to `primary`. Put both executables in the
same directory to share storage; run one server mode per installation at a time.
`--help` and `--version` require no credentials. Startup without an account keeps
management available after the last logout.

Headless control requires a random per-run capability from `local_auth/control.json`.
The control route rejects missing/wrong capabilities and browser Origin headers.
Do not share this file or the data directory.

For a Linux service, use a writable persistent directory for every command:

```sh
RELAY_AUTH_DIR=/var/lib/praxis-relay ./praxis-relay-headless serve --headless
```

OAuth requires a browser and a loopback callback on port 1455. A remote headless
host needs that callback forwarded to the browser's computer. Serving requests
requires neither a browser nor Python.

## Login and storage

Python **3.10+** is needed only for login. Windows discovery probes `python.exe`,
`py -3`, then `python3.exe` and verifies a real interpreter with SSL. Microsoft
Store aliases are rejected. `RELAY_PYTHON` is an explicit executable override;
an invalid override is reported rather than silently ignored.

The data directory defaults to `local_auth` next to the executable.
`RELAY_AUTH_DIR` overrides it. Embedding hosts with `RELAY_LOCAL=1` retain the
working-directory `local_auth` contract. Login, serving, refresh and logout all
use the same directory. The relay does not discover, borrow or modify credentials
from `~/.codex` or `~/.opencode`.

Both legacy `local_auth/auth.json` and existing
`local_auth/accounts/primary/auth.json` layouts are supported. The second slot is
`local_auth/accounts/secondary/auth.json`. A failed or cancelled sign-in preserves
the previous account; a successful, validated sign-in replaces it atomically.
Logout removes the selected relay slot. Existing requests may finish their leased
turn; new requests re-read current credentials. It does not remotely revoke an
upstream session or sign out another application.

## Client connection and API

Default base URL: `http://127.0.0.1:5011/v1`. Copy the client API key from the
desktop connection card or headless `status`. A new installation generates a
47-character key starting with `sk-` and stores it in `local_auth/api-key.txt`.
The same key survives restarts, account changes and switching between desktop
and headless. Missing or incorrect client keys are refused for generation.
`RELAY_API_KEY` explicitly overrides it for embedding hosts without modifying
the saved default. This local credential is separate from subscription tokens.
The listener always binds to loopback; do not share the key or data directory.

- `POST /v1/chat/completions` (also `/chat/completions`): final JSON with `stream`
  false/absent, SSE with true; tool calls and vision. Omitted tool `strict` stays
  false, explicit schemas are preserved, successful completion closes the choice,
  and interrupted visible output is not silently replayed.
- `GET /v1/models`: backend-discovered catalog with bounded caching and fallback.
- `GET /v1/limits`: active-account usage. Authoritative exhaustion may cause
  failover. Desktop per-account inspection is read-only and spends no model turn.
- `GET /v1/account`, `POST /v1/account/switch` with `{"slot":"secondary"}`:
  inspect/select slots. Browser-origin account mutations are refused.
- `POST /v1/images/generations`, `POST /v1/images/edits` (also without `/v1`):
  final image JSON through the same subscription, initially `gpt-image-2`.
  An interrupted image request is not automatically retried.
- `GET /v1/images/models`, `GET /health`: capability metadata and liveness.

A listed model is not proof of account entitlement. Quota exhaustion may move
new requests to a distinct configured account. Additional model quota buckets
remain separate; switching accounts does not merge or transfer them.

## Desktop preferences and headless configuration

The frameless desktop uses its own window controls; close and minimize hide it
in the tray. Ctrl+1/2/3 and Ctrl+, open sections; Ctrl+Q exits. Paper palettes,
light/dark/system themes, six accents, text size, density, handwriting, grain
and reduced motion apply immediately. The client key is masked until revealed.

`local_auth/settings.json` also stores account labels, optional quota polling
while the window is visible, start-in-tray and API-on-launch preferences, port,
and request defaults (model, reasoning, parallel tools and instructions).
Existing port-only settings migrate without changing credentials or the key.
Changed API defaults are applied on the next API start; a running desktop
shows a restart action. Appearance changes do not restart the server.
Explicit environment variables take priority and their fields are marked read-only.

The headless CLI uses the same validated settings, offline or through the
authenticated controls of an already running process:

```sh
praxis-relay-headless configure patch.json
```

Example patch (unspecified fields are preserved):

```json
{"appearance":{"theme":"dark"},"labels":{"primary":"Work"},"defaults":{"reasoning":"high"}}
```

A missing/blank chat model uses the saved model, or the first listed catalog
model when no default was chosen. Explicit request values retain priority.
Additional instructions accompany either built-in instruction mode.
An existing nonempty `instructions.txt` / `RELAY_INSTRUCTIONS_FILE` remains a
complete per-request override; the desktop explains this and disables the
superseded instruction fields. No provider entitlement is inferred from a setting.

## Build

Rust 1.94+. The desktop also needs the Tauri platform prerequisites:
https://v2.tauri.app/start/prerequisites/
Its HTML/CSS/JavaScript frontend is embedded; no Node build or package install.

```sh
cargo test --locked -- --test-threads=1
cargo build --release --locked --bin praxis-relay-headless
cargo build --manifest-path desktop/Cargo.toml --release --locked --target-dir target
```

Outputs: `target/release/praxis-relay-headless` and
`target/release/praxis-relay-desktop` (`.exe` on Windows). Headless builds do not
compile desktop dependencies. Both lockfiles are committed. Docker builds only
the headless service; use host networking for its loopback listener.

Configuration: `RELAY_PORT`, `RELAY_AUTH_DIR`, `RELAY_LOCAL`, `RELAY_PYTHON`,
`RELAY_API_KEY`, `RELAY_CODEX_VERSION`, `RELAY_DEFAULT_MODEL`, `RELAY_EXTRA_MODELS`,
`RELAY_MODELS_TTL`, `RELAY_MODEL_DISCOVERY`, `RELAY_CONTEXT_LENGTH`,
`RELAY_INSTRUCTIONS`, `RELAY_INSTRUCTIONS_FILE`, `RELAY_REASONING_EFFORT`,
`RELAY_PARALLEL_TOOL_CALLS`, `RELAY_ACCOUNT_STATE_FILE`,
`RELAY_ACCOUNT_COOLDOWN_SECONDS`, `RUST_LOG`.

The protocol version is resolved at startup, with a built-in fallback and an
explicit override. Subscription access is confirmed by actual upstream requests,
separately from local build and UI checks. MIT license; vendored OpenAI login
components retain Apache-2.0 notices. See `LICENSE`, `LICENSE-APACHE`, `NOTICE`.
