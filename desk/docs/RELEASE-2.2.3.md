Hélène 2.2.3 fixes Windows shell selection and automatic startup.

### Fixes

- Starting an agent no longer probes `bash` from the Windows system path, which could start WSL and consume memory unnecessarily.
- Shell commands use Hélène's own bundled `runtime/bash.exe`, including execution without the sandbox and fallback after a sandbox failure. A missing bundled shell produces an error instead of silently selecting the system WSL launcher. No separate Bash download is required.
- Windows automatic startup opens Hélène in the notification area. Open it from its tray icon; normal manual launches still show the window. If the tray icon cannot be created, the window opens so the app remains reachable.
- An explicit app restart after opening an automatically started window keeps the window visible.

The update improvements from 2.2.2 remain included: continuation after reviewed conflicts, windowless Windows helpers, themed frameless update progress, and opening **Now** at launch.

### Which asset to use

| System | Download |
| --- | --- |
| Windows x64 | `Helene-2.2.3.zip` |
| macOS Apple Silicon | `Helene-2.2.3-macos-arm64.zip` |
| Debian/Ubuntu x86_64 | `Helene-2.2.3-linux-x86_64.zip` |
| Fedora x86_64 | `Helene-2.2.3-fedora-x86_64.zip` |

Existing users: use **Update** inside Hélène to preserve the current profile. New users: extract the matching archive to a writable folder and run its Hélène launcher. Keep the entire extracted folder together. On macOS, use the included app bundle. Linux package users should follow the included migration flow.

`Helene-2.2.3-source.zip` is for source inspection/development, not installation. `SHA256SUMS` and the individual `.sha256` files let you check archive integrity. `release-evidence.json` records source/package bindings. `voice-components.json` lists unchanged optional voice components hosted with 2.1.1; the app downloads them when requested.

### Validation

54 focused checks and three real Windows tray/window scenarios passed. The bundled shell was executed successfully with a system-only PATH. All four archives passed CRC, source inventories, pristine agent-tree checks and unchanged runtime-record verification; built interface files are identical to 2.2.2. Qualified macOS/Linux window entry points and native runtimes are retained.

Physical microphone permission persistence was not qualified by this patch. Builds remain unsigned/unnotarized. Existing third-party component and optional voice-model terms still apply.
