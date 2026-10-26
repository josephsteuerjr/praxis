# Hélène 2.2.5 source and package bindings

Implementation `63ff66e4262cf5ea13d91bbd10831f7d8f973a49` on public 2.2.4 `63682382f50c0031f791fd31760502a0fd4d2dd9`. Platform snapshots are immutable tags.

- **windows**: tag `source/helene-2.2.5-windows`, commit `2434c1c35c6f053db64dd1f0edbe7b6ceaf69308`, archive `Helene-2.2.5.zip`.
- **macos**: tag `source/helene-2.2.5-macos`, commit `6f627585f7a387f9ce5633aa8546ebaeb275e1f9`, archive `Helene-2.2.5-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.5-debian`, commit `9eab382f24b63741d261e6e37a11c615d9f0db77`, archive `Helene-2.2.5-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.5-fedora`, commit `b92f6cdb818c5e6541b1f375f1d7622002d51f18`, archive `Helene-2.2.5-fedora-x86_64.zip`.

Snapshots contain exact packaged application inputs and pristine upstream source. Native runtimes, platform adaptations and interface bytes are unchanged from2.2.4. Optional Whisper/XTTS assets remain pinned to2.1.1. The Windows root preparation launcher is a UTF16 encoding of desk/resources/prepare-update.vbs; package manifests bind it.

The explicit incoming preparation entry lets an old2.1.1 updater pass its pre-helper conflict gate without editing or closing the old application. The owner then retries the normal button for the same release. See release-evidence.json for exact qualification and boundaries. The Praxis public source mirror is unchanged.
