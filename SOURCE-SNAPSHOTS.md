# Hélène 2.1 platform source snapshots

The v2.1.0 tag and main desktop tree carry the Windows/common application source. Each archive is bound to its qualified platform snapshot below; the snapshots preserve the tested platform differences.

- **windows**: [`source/helene-2.1.0-windows`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.1.0-windows), commit `971c61de72ec4c2ab773fbc7706443de1be4e74b`.
- **debian**: [`source/helene-2.1.0-debian`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.1.0-debian), commit `d8a4fa72df8c98408ece456e61fb626efaca0aec`.
- **fedora**: [`source/helene-2.1.0-fedora`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.1.0-fedora), commit `314c8318c632dadaf4c1e774ca0970e545773c1d`.
- **macos**: [`source/helene-2.1.0-macos`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.1.0-macos), commit `db005741064ab75466bd7be831b9e63f8e154aaf`.

Each snapshot contains `helene/PLATFORM-SOURCE.json` with the exact application archive hash and packaged source inputs. Built frontend and reused runtime/native dependencies are identified separately; these source commits do not assert that unchanged binaries were rebuilt. Whisper/XTTS component maps pin the final v2.1.0 release URLs; deleting obsolete version tags does not remove their storage. The standalone relay is published in its own repository.

The live Praxis source and its existing CORE-SOURCE.json receipt are preserved from public main; publishing this desktop release does not deploy or change the live agent. Private profiles, memory, credentials and local work reports are excluded.
