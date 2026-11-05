# Hélène 2.4.0

Download the archive for your system, extract it into a permanent writable
folder and open Hélène. Python and the application runtime are included.
Existing users can choose **Clean release code** or **Keep my changes** in
Settings. Both modes retain the selected agent's memory, conversations and
settings. The previous program remains available for rollback.

This release brings local Fish speech, persistent voice components, Telegram
Mini App access and the restored desktop-to-VPS transfer into one application.
It builds on the [2.3.0 desktop and update work](RELEASE-2.3.0.md).

## Fish for local agents

Fish is an independent alternative to XTTS for an agent running on your
computer. It uses your Fish API access directly; a Praxis voice server is not
required. Whisper remains a separate local speech-recognition choice.

Choose the synthesis provider, model and voice explicitly. Words to speak and
delivery instructions have separate fields. The key is stored in the agent's
private secrets directory, outside public source and exported configuration.
Provider availability, costs and voice permissions remain those of your account.

Each paid preview has a durable request identity. Interrupted or uncertain
requests remain visible; the application does not silently retry a paid POST,
switch providers or play a late result after cancellation. Tests use controlled
HTTP responses and do not establish paid-provider or acoustic acceptance.

## Voice files survive program updates

Optional XTTS/Whisper components and model files move into a persistent,
content-addressed store outside versioned program folders. Each installation
keeps explicit references to compatible components. Repeated updates reuse
existing content; preparing a candidate does not change the running program's
selection. Rollback retains the previous selection.

Personal voice samples remain agent data. Local migration preserves both
agent-relative references and older program-relative samples. Cross-host
transfer carries the selected sample and rebinds it on the destination;
machine-specific executable pins and large host runtimes are not copied as
agent data. Executable modes are preserved when a filesystem requires copying.

## Telegram Mini App

Open the web interface from your own bot. When cloudflared changes the host
address, the bot menu is updated and a fresh `/app` command uses the current
address. The application uses Telegram's fullscreen API where supported and
accounts for safe areas, the keyboard and viewport changes.

The separate installable PWA flow for temporary tunnel addresses has been
removed. The shared mobile renderer remains part of the Mini App. Launches
validate signed Telegram initData and owner identity even when an older session
cookie exists. One intake owns Telegram polling; the Mini App does not add a
second poller. Bot-menu ownership is retained across transfer without exporting
ephemeral tunnel readiness.

## Transfer an agent to a VPS

The connection guide can prepare a supported Ubuntu/Debian x64 server, transfer
the agent and attach the desktop to its server channel. Installation names,
directory and port are explicit. The SSH host fingerprint must be confirmed
before credentials are used.

Transfer holds the agent's data lease and records progress durably. An
interrupted operation can continue without importing the same memory twice.
The local agent is stopped before the server copy is started. Stop remains
binding on the server; resuming the agent requires a separate owner action.
Recovery verifies the exact server deployment before stopping it for a local
return, and never automatically resumes the local agent.

Server voice files and request receipts live in persistent mounts shared only
by the application's service roles. Fish can use the transferred private
configuration. A bare Linux server kit needs a compatible native XTTS runtime
and model before XTTS can run; a Windows executable component is refused.
This release does not qualify XTTS model inference on a bare Linux VPS.

## Clear update choices and private configuration

Two equally sized, keyboard-accessible cards replace the source-mode dropdown.
The selected and locked modes are visible. Clean selects verified release
source; Preserve retains authored changes through conflict resolution, checks
and independent review. Neither mode silently falls back to the other.

Windows private-file writes use the effective token SID and verify the protected
DACL before writing secrets. Failures keep a sanitized diagnostic instead of
claiming success from an `icacls` invocation alone. POSIX private staging also
handles an inherited setgid directory before any secret is written.

This addresses the private-write path involved in the reported RC26 migration
failure. That support archive did not contain the original ACL command's exit
status, and the other person's machine has not been retested. Separate relay,
SDK and filesystem-argument failures in that archive are not claimed fixed here.

## Interface and acceptance boundaries

Speech-provider forms, update choices and the connection guide use the shared
paper controls and spacing. Peer cards align on wide windows and stack at phone
widths; fingerprints and long values wrap. Light and dark layouts are covered
by browser acceptance against the built interface.

Source tests, packaged native application checks, paid-provider acceptance and
physical-device acceptance are distinct. The release evidence records the
source and archive hashes and the checks actually executed. Synthetic Telegram
viewport tests do not establish acceptance on every Telegram client, and native
macOS window checks do not grant physical-device or TCC permissions.
