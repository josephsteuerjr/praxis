# Hélène 2.2.13

Fixes the runner startup crash that caused repeated restarts and “Runner ownership heartbeat unconfirmed” during installation.

- Remove a late local import that hid the control module before speech setup. The active runner can now reach startup confirmation.
- Clarify setup wording: the update wizard combines code; independent reviewers check the result.

## Install

Extract the whole ZIP into a separate folder and open `helene.exe` (Windows), `Helene.app` (macOS Apple silicon), or `Start Helene.sh` (Debian/Ubuntu or Fedora x86_64). Choose your existing agent and data folder; do not uninstall the old application first.

## Evidence and licenses

See `release-evidence.json` for the Windows active-runner startup and migration proof, regression scope and limits. Other platforms are packaged from the same fix; their native runtime checks are separate. This does not claim a live external-provider call or acceptance on the affected user's computer.

Apache, relay MIT attribution and XTTS CPML terms remain included. Optional voice components retain the pinned 2.2.11 downloads. `SHA256SUMS` identifies the published files.
