# Hélène 2.2.0 source and package bindings

The release implementation is `c9da1edc8fb8e26187e1fa05e27f36129c0710c7`, five commits on the existing public 2.1.1 baseline `2d9b9eb3428450c4b6fbadf90c8dc757e0a9d3ba`. The release documentation commit adds no executable change.

The four portable ZIPs are byte-identical to the prepared candidates; only their external filenames change. Historical `local_candidate` / `public_release` construction fields remain inside those exact bytes. `release-evidence.json` records the publication separately.

- **windows**: `source/helene-2.2.0-windows`, commit `7154c47aab639a62da229e1a2fa9f64945e597b4`, archive `Helene-2.2.0.zip`.
- **macos**: `source/helene-2.2.0-macos`, commit `f422c07b13b9b4e73fee698c985bda06db17e979`, archive `Helene-2.2.0-macos-arm64.zip`.
- **debian**: `source/helene-2.2.0-debian`, commit `146743d5ed2b002f695fe166b1b9923a71f8a723`, archive `Helene-2.2.0-linux-x86_64.zip`.
- **fedora**: `source/helene-2.2.0-fedora`, commit `12cf67f55da293c658f2b60ba8924425d69ea8ea`, archive `Helene-2.2.0-fedora-x86_64.zip`.

Each snapshot overlays the exact packaged desktop source-input hashes and the full pristine code-upstream tree on the common source. `helene/PLATFORM-SOURCE.json` binds those paths, modes and the archive digest. Built frontend assets are included where the package input inventory names them; native/runtime dependencies are reused, not claimed rebuilt. Existing unrelated Praxis source remains inherited; this publication performs no service deployment.

Optional Whisper and XTTS downloads remain pinned to the unchanged v2.1.1 release. The standalone relay is separately distributed. No personal profile, memory, credentials or task evidence is an input to this source publication.
