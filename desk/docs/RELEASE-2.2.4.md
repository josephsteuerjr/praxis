Hélène 2.2.4 fixes a legacy update failure caused by an older, preserved Forge lacking the source-update coordinator API.

### Fixes

- Legacy update preparation can use the verified incoming distribution's coordinator, so updating from 2.1.1 no longer depends on a new API already being present in the old agent's code.
- Previously resolved plans blocked solely by that missing capability can continue automatically. Cancelled, stopped or failed worker attempts are not silently restarted.
- Conflict merging still requires an independent review of the exact candidate. The updater runs only the restricted merger and source reviewers; it does not execute authored code or start an ordinary agent conversation.
- Update reviews have their own ownership and cancellation boundaries. The normal agent cannot pick up their reviewers, and cancellation targets the specific update plan.
- Reading older model settings during review no longer rewrites `llm.json` before installation.
- Exact published source files containing short test placeholders no longer produce false credential blockers. Changed local credentials and recognizable full tokens remain withheld.

The 2.2.3 bundled Windows Bash and tray-startup fixes remain included, along with the themed frameless updater, windowless helpers, automatic continuation after review, and opening **Now** at launch. No separate Bash download is needed.

### Which asset to use

| System | Download |
| --- | --- |
| Windows x64 | `Helene-2.2.4.zip` |
| macOS Apple Silicon | `Helene-2.2.4-macos-arm64.zip` |
| Debian/Ubuntu x86_64 | `Helene-2.2.4-linux-x86_64.zip` |
| Fedora x86_64 | `Helene-2.2.4-fedora-x86_64.zip` |

Existing users: use **Update** inside Hélène. Keep your existing profile and authored code. New users: extract the matching archive to a writable folder and run its Hélène launcher; keep the entire extracted folder together. On macOS, open the included app bundle. Linux package users should follow the included migration flow.

`Helene-2.2.4-source.zip` is for development/source inspection, not installation. `SHA256SUMS` and the individual `.sha256` files verify archive integrity. `release-evidence.json` records exact source/package bindings. `voice-components.json` lists unchanged optional voice assets hosted with 2.1.1; the app downloads them when requested.

### Validation

Focused update, review, cancellation, rollback and configuration checks passed. An isolated 2.1.1 fixture with an old Forge completed the real coordinator/worker/reviewer process chain, installation overlay and rollback while preserving original authored code, model settings and profile data. That fixture used a deterministic local provider, not a live model or the owner's installation.

All four archives passed full CRC, source inventories and unchanged runtime-record checks. Native runtimes, platform entry points and built interface files are retained from 2.2.3. Physical microphone permissions and a fresh native GUI upgrade were not tested in this patch. Builds remain unsigned/unnotarized; existing third-party and optional voice-model terms apply.
