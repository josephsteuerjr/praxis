# Hélène 2.1 platform source snapshots

The v2.1.0 tag and main desktop tree carry the Windows/common application source. Each archive is bound to its qualified platform snapshot below; the snapshots preserve the tested platform differences.

- **windows**: [`source/helene-2.1.0-windows`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.1.0-windows), commit `f12eeb144861f062926d84b9a486386a6c05a1cd`.
- **debian**: [`source/helene-2.1.0-debian`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.1.0-debian), commit `d399ece629557d035df49799614bbe0019d18a12`.
- **fedora**: [`source/helene-2.1.0-fedora`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.1.0-fedora), commit `e1f6029ff323234a354a431ea2c7d33639f71d4c`.
- **macos**: [`source/helene-2.1.0-macos`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.1.0-macos), commit `6756f4d5c4be9d6e44cc4cc3bc528c52439c5750`.

Each snapshot contains `helene/PLATFORM-SOURCE.json` with the exact application archive hash and packaged source inputs. Built frontend and reused runtime/native dependencies are identified separately; these source commits do not assert that unchanged binaries were rebuilt. Whisper/XTTS component maps retain pinned existing download URLs. The standalone relay is published in its own repository.

The live Praxis source and its existing CORE-SOURCE.json receipt are preserved from public main; publishing this desktop release does not deploy or change the live agent. Private profiles, memory, credentials and local work reports are excluded.
