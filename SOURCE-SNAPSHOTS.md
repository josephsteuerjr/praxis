# Hélène 2.2.3 source and package bindings

Implementation `f9b1e8ccdf159915897fced1a587ba853a19d0dd` on public 2.2.2 `912b798161b74be6f0c2920779c6d873822b449b`. The publication commit adds documentation and exact package bindings. Platform snapshots are immutable tags, avoiding temporary review branches.

- **windows**: tag `source/helene-2.2.3-windows`, commit `be7ec3ee676a2cf96c40f4981977bef4d46267f7`, archive `Helene-2.2.3.zip`.
- **macos**: tag `source/helene-2.2.3-macos`, commit `c457bb2358768403fff24f8b99b51f3766a6db5d`, archive `Helene-2.2.3-macos-arm64.zip`.
- **debian**: tag `source/helene-2.2.3-debian`, commit `28602503ba422d318ff19e0362465c0ccfcadebf`, archive `Helene-2.2.3-linux-x86_64.zip`.
- **fedora**: tag `source/helene-2.2.3-fedora`, commit `0b0d92320d35ecbe3b200c4c4b91d8ba19c6b9ad`, archive `Helene-2.2.3-fedora-x86_64.zip`.

Snapshots overlay exact packaged source-inputs and the pristine code-upstream tree. Native runtimes and built interfaces are unchanged from 2.2.2. Windows tray changes are confined to Windows; qualified macOS/Linux window entry points and platform adaptations remain intact. Optional Whisper and XTTS components stay pinned to v2.1.1. Personal profiles, memory and credentials are not publication inputs.

The 653 publishable Praxis source files were compared with the current server tree on 26 October 2026. Executable source matches; two existing comment/docstring anonymizations are retained. No live services, profiles or configuration were changed. The public mirror and the packaged Hélène edition remain separate source boundaries. Historical 2.2.2 platform snapshot refs are now preserved as same-name tags.
