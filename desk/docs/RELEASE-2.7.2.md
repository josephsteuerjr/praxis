# Hélène 2.7.2 Beta

**Beta — published as Latest for the built-in updater.**

This patch fixes ChatGPT subscription sign-in in the desktop application.

In 2.7.1, starting a browser or device-code sign-in also marked the relay's
accounts as changed. Hélène's supervisor restarts the relay when that marker
moves, so the sign-in that had just begun was stopped within a millisecond.
Settings returned to "no saved login", which looked as if sign-in was not
implemented. The relay signs in inside its own process and installs the new
account itself; starting a sign-in no longer touches the marker. Signing out
and switching accounts behave as before.

A new regression test exercises the real relay control call against a local
endpoint and reads the marker the same way the supervisor does. The
qualification of 2.7.1 had replaced that call with a stub.

Nothing else changes: bundled praxis-relay 1.2.0, saved accounts, settings,
agents and the platform limits documented for 2.7.0 and 2.7.1 remain as they
were. macOS builds are ad hoc signed without notarization, Intel Mac local
Whisper/XTTS are unavailable, and physical phone/work-PC, public ACME, TCC,
microphone, CUDA and paid-provider acceptance remain separate. Completing real
account consent remains an owner interaction.
