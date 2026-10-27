# Hélène 2.2.7

The ordinary **Update** button now handles Python's normal startup bytecode cache. The bundled interpreter imports `sitecustomize` before the update helper can run;2.2.6 incorrectly treated the resulting `__pycache__` as unexpected data and stopped before source preparation.

The check now recognizes regular CPython bytecode caches paired with allowed source. They stay with the previous delivery view and are not promoted into real incoming source. Unknown/private files, orphan bytecode and linked paths still block replacement. Cache-bearing recovery trees are retained; source merge, independent frozen review, Stop/Pause and rollback fences remain unchanged.

A new2.2.7 filename avoids conflicting with a previously cached2.2.6 archive. No manual preparation launcher, cache deletion or installed-code patch is required. Existing supported2.1.1 installations use the normal Update button; first installations launch their ordinary platform entry.

All four portable platform archives, source ZIP, individual checksums, SHA256SUMS, voice-components and scoped release evidence are included. Native runtimes and interface bytes retain their qualified2.2.4 inputs. Optional voice references remain pinned to the existing2.1.1 assets.

Qualification includes causal failure before/fix after with normal bytecode startup, privacy/link/orphan refusal and recovery, an isolated copy using the **actual bundled Windows Python and its unchanged runtime._pth** with bytecode enabled, and the unmodified2.1.1 one-button custody/review/overlay/rollback integration against a deterministic localhost provider. Packaged-source gates run with ordinary site startup. All four archives receive full CRC, inventories, semanticA/realB and unchanged-runtime-record checks, with independent complete-byte reconstruction and provider digest verification.

This is scoped integration/lifecycle evidence. No owner installation/restart, real account model/Telegram/production action, fresh physical GUI/device acceptance or new public download retest is claimed. The previous226 public-byte gates did not cover normal startup cache creation; this release corrects that gap.
