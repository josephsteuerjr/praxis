# Current source snapshots

The desktop release targets Windows x64, macOS Apple Silicon, Debian and Fedora.

`main/praxis` mirrors the captured live Praxis code. `main/desk` remains the common Debian-derived 2.0.0 source. Desktop platform snapshots are separate branches, because their final migration fixes differ; syncing the live server does not silently rewrite those editions.

- **windows**: [`source/helene-2.0.0-windows`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.0.0-windows), commit `4aa53a710fa33737113eee42dafc14cdf66f1c92`.
- **debian**: [`source/helene-2.0.0-debian`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.0.0-debian), commit `df74da3827cb971fa0cd5890e46bf9bf191da5b5`.
- **fedora**: [`source/helene-2.0.0-fedora`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.0.0-fedora), commit `9cb7b6d9fbd261d35b78389763d78035c841c8fa`.
- **macos**: [`source/helene-2.0.0-macos`](https://github.com/josephsteuerjr/praxis/tree/source/helene-2.0.0-macos), commit `0d84261a670ef72d83a8b24f4ff6c34671d65350`.

Each platform branch carries `helene/PLATFORM-SOURCE.json`, binding original package hashes and the public source projection (existing comment-only anonymization retained). It retains existing build recipes from the public release parent; it does not claim all native binaries were rebuilt from that branch. Dependency/model archives and built UI remain release assets, not additional Git source.

The snapshots preserve platform differences instead of guessing a common merge. Use the matching branch when inspecting a shipped platform. They do not fix the reported Linux package upgrade issue or the Fedora update selector. Release tags and assets are unchanged.

Praxis export excludes private memory, authored soul, workspace, credentials and incidental work reports. Public-only mirror files remain available; absence from live capture alone is not deletion authority. `CORE-SOURCE.json` gives the live commit, capture time and exact included file hashes. No live installation, service or configuration was modified by this synchronization.
