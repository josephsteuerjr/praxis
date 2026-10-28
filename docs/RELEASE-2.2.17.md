# Hélène 2.2.17

Fixes the normal Update button handoff from 2.1.1: the setup window now opens before expensive source activation and verification, within the old application's 15-second admission deadline. Verification runs after confirmation and before installation.

Source preparation no longer holds the installation lock needed by the previous Python runner's startup recovery. The same agent can reopen to resolve conflicts; setup rechecks the prepared code under the installation lock before stopping the previous application. Existing data and the explicit clean-code choice are retained.

## Install

Use **Update Hélène** in the app. For a first installation, extract the entire platform ZIP and open `helene.exe`, `Helene.app` (Apple silicon), or `Start Helene.sh` (Linux x86_64). Keep the previous installation until setup completes.

## Evidence and licenses

`release-evidence.json` records a real Windows setup-window handoff consumed by the original 2.1.1 admission function, targeted ordering/cancellation/lock regressions, and exact archive/source bindings. This window check does not claim installation on the owner's actual profile or external-provider acceptance. Native macOS/Linux tests were not rerun.

Apache, relay MIT attribution and XTTS CPML terms remain included. Optional voice downloads remain pinned to 2.2.11. See `SHA256SUMS`.
