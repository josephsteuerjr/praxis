# Hélène 2.5.3

This hotfix reduces idle CPU use in the desktop runtime and Forge status views.

- Unchanged runner state no longer writes a receipt that wakes its own inbox watcher. Periodic heartbeats still publish freshness; newly published messages wake the Windows consumer immediately.
- Source-update maintenance stops repeatedly reading plans owned by another coordinator. Ownership transfers are rediscovered through request publication, with separate observation state for each coordinator.
- Local session RPC uses an HTTP-only client instead of loading the Windows TLS certificate store on every observation. Loopback validation and redirect refusal remain enforced.
- Forge reuses loaded recovery-validation code while checking current custody evidence on every read. Redundant parent-directory validation is removed.
- Hidden Forge panels stop polling. Idle Forge and review discovery use a ten-second interval; active work and owner controls retain the faster interval, and returning to the panel refreshes immediately.

On one Windows machine with twelve logical processors, idle Python CPU time fell from 17.08% over 15 seconds before the fix to 2.32% over 30 seconds after startup settled. This is a measured local result, not a cross-platform performance guarantee. Global hotkeys, voice features, Stop/Pause and update ownership are retained.

The Python core, native engines and voice models are unchanged. The existing macOS ad hoc signing, Intel local Whisper/XTTS availability, physical-device and historical whole-core qualification limits still apply. Final release assets include the actual platform test results and checksums.
