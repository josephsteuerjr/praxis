# Hélène 2.2.14

Fixes Windows installation failing with “Program destination leaves target” while copying into a new folder.

Windows can return the same destination with an extended-path prefix. Setup now compares the two representations correctly while still rejecting paths outside the selected folder. The active-runner startup fix from 2.2.13 is retained.

## Install

Extract the entire ZIP into a separate folder and open `helene.exe` (Windows), `Helene.app` (macOS Apple silicon), or `Start Helene.sh` (Debian/Ubuntu or Fedora x86_64). Select your existing agent and data folder. Keep the old installation until setup completes.

## Evidence and licenses

`release-evidence.json` records the reproduced Windows copy failure, regression checks and corrected real-payload copy. Other platforms are packaged from the same source; no new native macOS/Linux runtime acceptance is claimed. External-provider access and acceptance on the affected user's computer remain separate.

Apache, relay MIT attribution and XTTS CPML terms are included. Optional voice components retain the pinned 2.2.11 downloads. `SHA256SUMS` identifies the published files.
