# Hélène 2.8.9 Beta

**Latest Beta for Windows, macOS Apple silicon and Intel, Debian/Ubuntu, Fedora and Linux ARM64 server.**

- Idle desktop windows use file-change notifications on Windows to refresh appearance settings. Acknowledged revisions keep appearance changes visible across interrupted polls.
- Attached windows read session events from memory. The Python controller still observes engine startup and Stop while idle observations avoid rewriting the desktop status file. Command acknowledgements retain atomic publication and an explicit disk flush.
- Cold context preparation validates memory timestamps with less parsing and formatting work. Run status and cancellation checks look up the exact manifest in each run bucket, preserving the handling of ambiguous and damaged records.

The Windows idle fixture observed zero preference and roster configuration reads after 40 unchanged polls, down from 40 of each. A read-only Jarvis comparison preserved the rendered summary byte for byte. The reported HDD setup still needs a follow-up trace after upgrading.

The release packages include source, platform evidence and SHA-256 checksums. macOS packages are ad hoc signed and are not notarized.
