# Hélène 2.1.1 source snapshots

The public main branch contains the shared source and Windows desktop implementation. Platform snapshots preserve their existing launch and recovery adaptations. Native runtimes are reused from the qualified 2.1.0 archives.

- windows: `source/helene-2.1.1-windows` — `0bad32defc8193d97dd8e2fe10271c09a136eb94`; `Helene-2.1.1.zip` SHA256 `ca67191b64983f586ecc528b4041072f15718e7b9e6c8f1aa3535194ac9e6ab9`.
- debian: `source/helene-2.1.1-debian` — `c9f5e6acacd9b4774c52de217af3cdd10ae3305e`; `Helene-2.1.1-linux-x86_64.zip` SHA256 `ec50fa2cb1cf673304579b0fe44bc9805dd9772d5c0e080622da53dd89f9b441`.
- fedora: `source/helene-2.1.1-fedora` — `b9cf654b555e67c918f64acf7d9de6264dbb30ab`; `Helene-2.1.1-fedora-x86_64.zip` SHA256 `f1537223a60e4e089d06f746a2ddf12c7ffc2ec7de2278530b130def97ec7332`.
- macos: `source/helene-2.1.1-macos` — `a72dfb54fde61fa0b589dc9c862bc9aae6dd1fde`; `Helene-2.1.1-macos-arm64.zip` SHA256 `8d32a67c42d750d095a724912308705020426b3b0332883b5cae9962a558ec07`.

Each archive has an exact source-input manifest, application file hashes and a pristine code-upstream snapshot. The source merge, conflict resolution and two successive update/rollback paths were tested on Windows and on a separate Linux server workspace. Actual owner installation remains a separate acceptance step.
