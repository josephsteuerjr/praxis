# Hélène 2.2.12

Fixes an update that stopped when the merge agent reported unfinished work.

- An early final answer now returns the agent to unresolved conflicts within the same turn budget. Only a successfully validated candidate proceeds to independent review.
- Interrupted clean-code setup now resumes when both recovery copies exactly match the shipped source; authored changes remain protected.
- Corrected tool mistakes no longer reject an otherwise validated merge. Cancellation and exhausted budgets still stop the update safely.
- Setup now says “Continue with my agent” and shows code combination and independent review as separate stages.

## Install

Download and extract the ZIP for Windows x64, macOS Apple silicon, Debian/Ubuntu x86_64 or Fedora x86_64. Open `helene.exe`, `Helene.app` or `Start Helene.sh`. To keep your agent, select its existing data folder and choose to preserve your code changes or install clean code.

## Evidence and licenses

`release-evidence.json` records focused Windows regression tests, including the original premature-final failure and bounded continuation. Other operating systems were packaged without rerunning their runtime tests, as requested. This is not a claim of a completed live-model merge on the owner's installation.

All archives include Apache, relay MIT attribution and XTTS CPML terms. Optional voice downloads retain the pinned components from 2.2.11. `SHA256SUMS` identifies the published files.
