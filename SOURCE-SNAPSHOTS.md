# Hélène 2.2.4 source and package bindings

Implementation `1dddd8dd30723336a85e3108a4a768dad27c4362` on public 2.2.3 `4fe8f971250763cb4f5fad32ee5dc4b9c47b2161`. The publication commit adds documentation and exact package bindings. Platform snapshots are immutable tags.

- **windows**: tag `source/helene-2.2.4-windows`, commit `2741b04b9fde063aaeb6a3c34982be46c796d679`, archive `Helene-2.2.4.zip`.
- **macos**: tag `source/helene-2.2.4-macos`, commit `0f92cd878848e66f332b076a7fd4b458a6b7f668`, archive `Helene-2.2.4-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.4-debian`, commit `e7c8604d0c3d1b5a605b824469e7d755816dcebf`, archive `Helene-2.2.4-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.4-fedora`, commit `5746e36e906bcbf455ae329066f48be151a19309`, archive `Helene-2.2.4-fedora-x86_64.zip`.

Snapshots contain exact packaged application inputs and the pristine code-upstream tree. Shared updater modules match the tested implementation on all four platforms. Native runtimes, platform-specific window entry points and built interface files are unchanged from 2.2.3. Optional Whisper and XTTS components remain pinned to v2.1.1. Personal profiles, memory and credentials are not publication inputs.

The Praxis public mirror is unchanged from the 2.2.3 source audit; no live service or profile was changed. Hélène edition source includes a narrow read-only model-config option for its incoming update coordinator; ordinary config migration behavior is unchanged. See release-evidence.json for qualification boundaries.
