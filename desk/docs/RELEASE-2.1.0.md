# Hélène 2.1 — a portable Python home for your agent

Hélène runs from a folder that contains its own Python runtime, desktop application and agent code. That folder becomes the agent's home: its profile, memory, configuration and authored source stay with it. Extract the archive to a permanent, writable location and launch the entry for your operating system. A separate Python installation or compiler is not required.

The agent can develop its own working code, while ordinary application updates keep using the same directory. Changes that belong in the shared product can be reviewed and included in later Hélène releases. The migration tools preserve authored code and Git state; publishing an application release does not publish an agent's personal memory or credentials.

This release completes the move to an archive-first desktop on Windows x64, Apple Silicon, Debian and Fedora x86_64. Python owns the window, agent lifecycle, migration and update coordination. Platform runtimes and the existing native tools for operating-system access are included where needed.

## What changed in 2.1

### A simpler voice conversation

- Voice messages appear as compact waveform strips in the composer and conversation, with playback controls alongside the message.
- Hold the configured shortcut to record. Releasing it while Hélène is focused pauses the draft so you can listen, continue recording or send it. Releasing it while another application is focused sends the recording.
- Hold **Shift when releasing** to keep a background recording as a draft. The next hold continues it. **Esc** cancels recording and prevents the subsequent release from sending it.
- Shortcut configuration and automatic playback live in **Settings**. A small hint beside the composer links to a help popup explaining voice controls.
- The separate “live mode” flow has been removed. Recording and playback use the same conversation controls, with saved preferences and protection against replaying the same automatic audio twice.

### A refreshed first run and appearance

The onboarding page has been refreshed, and voice help follows the same visual style. Appearance settings include Studio, Glass and Terminal treatments, typography, shape and background choices. The Windows migration window uses Hélène's custom chrome, branded content typography, a small regular title/version label and a sharper window/taskbar icon.

### Unpack once; migrate into that folder

The Windows archive now contains **Start Helene.vbs** and **Migrate Helene.vbs**. They launch the bundled Python application and migrator directly. Migration copies the previous profile and authored source into this unpacked application; it does not extract and copy the whole program into a second installation directory.

The previous installation is stopped before its final profile snapshot. The new window must confirm its own profile before retirement begins. Where the supported Windows native package is registered, its own uninstaller is invoked with `--uninstall --quiet`, without the data-purge switch. The command is bound to the selected installation, and a service belonging to another installation blocks that removal. Remaining shortcuts are retargeted to the new Python entry. Old Python windows are asked to exit through their authenticated control channel; a stale lock-file label alone is not treated as proof of a live owner.

On Debian and Fedora, the portable migration flow preserves the profile, working source and Git state before removing the identified native package. On macOS, use the bundled migration application to copy an existing profile; the previous application folder is retained. Ordinary updates then operate in the same portable folder, with recovery when the replacement fails to start.

### Optional voice components, independent relay

**Whisper and XTTS are the only optional large component downloads.** Their platform-specific runtime/model archives are pinned by size and SHA-256 and are attached to this release. They are downloaded only when selected. Existing Linux/macOS component asset objects were retained; the unchanged Windows components were consolidated here so retiring earlier tags cannot break voice installation. The native rebuild kit is no longer offered as a new component download. Existing optional installations are preserved during migration and updates.

The subscription relay is a separate product: [praxis-relay 1.1.0](https://github.com/josephsteuerjr/praxis-relay/releases/tag/v1.1.0). Hélène can use its independently distributed executable without installing a compiler. XTTS model use remains subject to the model's CPML non-commercial terms; bundled third-party notices and license files remain included.

## Downloads and first launch

| Platform | Archive | Entry after extracting the whole folder |
| --- | --- | --- |
| Windows x64 | [Helene-2.1.0.zip](https://github.com/josephsteuerjr/praxis/releases/download/v2.1.0/Helene-2.1.0.zip) | **Start Helene.vbs**; **Migrate Helene.vbs** for an existing installation |
| macOS Apple Silicon | [Helene-2.1.0-macos-arm64.zip](https://github.com/josephsteuerjr/praxis/releases/download/v2.1.0/Helene-2.1.0-macos-arm64.zip) | **Helene.app** or **Migrate Helene.app** |
| Debian x86_64 | [Helene-2.1.0-linux-x86_64.zip](https://github.com/josephsteuerjr/praxis/releases/download/v2.1.0/Helene-2.1.0-linux-x86_64.zip) | **Start Helene.sh**, using your file manager's “Run as a program” action |
| Fedora x86_64 | [Helene-2.1.0-fedora-x86_64.zip](https://github.com/josephsteuerjr/praxis/releases/download/v2.1.0/Helene-2.1.0-fedora-x86_64.zip) | **Start Helene.sh**, using your file manager's “Run as a program” action |

Keep the complete folder together, in a location you can write to. Use the in-app updater for subsequent updates; do not unpack a new archive over a running application. The Windows scripts use Windows Script Host and the bundled Python runtime. Linux desktops may ask you to trust a launcher. Operating-system permission prompts remain under your control.

There is no new Windows setup executable or DEB/RPM in this release. Each application archive has a `.sha256` sidecar; `SHA256SUMS` covers the attached delivery files. `voice-components.json` lists the pinned optional downloads, and `release-evidence.json` records the exact application bytes and source snapshots.

## Verification and remaining platform limits

The four application archives passed ZIP integrity, publisher-file hash and packaged-source checks. Windows testing covered the real Python migration window, both archive entries, fresh startup, a migrated profile with dirty Git/index/untracked files, shortcut registration and clean exit. Tests also cover the registered-uninstaller flags, ordering, failure handling and protection of another installation's service. The actual legacy Windows uninstaller was not exercised in a clean Windows VM.

Debian and Fedora were launched as an ordinary user on isolated native desktop stands. Package-to-portable migration, profile/Git preservation, native-package removal and reopening were checked; the final archives' native startup and shutdown were checked again. A minimal Debian desktop was also exercised. macOS ARM64 CI checked native startup, single-instance behavior, migration, in-place update and rollback, with profile/Git preservation.

Physical microphone use and hotkeys across real desktop applications still need device-level acceptance. macOS microphone and Accessibility permissions are not automatically granted; a local-network permission prompt also limited the native screenshot review. Real Wayland compositor behavior remains separate from the tested X11 and portal-protocol paths. Windows binaries are unsigned; the macOS distribution is not notarized. These checks do not claim that every historical repository test or every operating-system configuration is green.

## Source

The release tag carries the current desktop source and a [platform source index](https://github.com/josephsteuerjr/praxis/blob/v2.1.0/SOURCE-SNAPSHOTS.md). The four pinned platform snapshots retain their qualified differences. Runtime/dependency binaries and the compiled frontend remain separately identified in the artifact receipts; the publication does not claim that unchanged native dependencies were rebuilt.
