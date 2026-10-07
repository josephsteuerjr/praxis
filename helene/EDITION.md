# HГ©lГЁne edition: source and package provenance

HГ©lГЁne is the desktop edition of the Praxis runtime. The current production mirror is
in `praxis/`; `helene/core/` declares the edition differences for the frozen package core.
Their revisions are recorded separately so that a newer production change is not silently
claimed to be inside an already built desktop package.

## HГ©lГЁne 1.4.1

- Production Praxis source: `4f86cc9fb44b0a7da1cfaf77b048f1475c9d6501`.
- Frozen full HГ©lГЁne core: `fb929656c4803ddfb04b1ef620bd9e709fbca348`.
- Windows package source: `d71054b0c575678ee9287ea86f7e8cf02fc1e4c6`.
- Linux package source: `6ff3c06d708136166b495c02026efbd0a9500f22`.
- macOS CI preparation source: `aad144b54ce9cb3e041abb27997df3414b9edf64`.

The Windows and Linux desk revisions differ only in the Windows installer relay merge
fix. The later CI preparation adds early matching-release validation; it does not
relabel the frozen Windows/Linux payload provenance.

| Composition check | Files |
|---|---:|
| Production core files | 623 |
| Frozen edition core files | 628 |
| Declared edition layer | 195 |
| Undeclared differences | 0 |
| Stale or drifted declarations | 0 |
| Production files not carried in the frozen edition | 22 |

`HELENE-SOURCE.json` records the frozen file hashes and the production files not carried
by that snapshot. Identity, live memory, accounts and private runtime files are excluded.
The publication retains executable equivalence; personal references in documentation
and comments are removed. The production mirror does not include private Git history.

macOS builds its agent tree from the matching Windows release archive and verifies that
archive's source passport. Windows/Linux archive hashes and package receipts are
published with the release. Native platform permissions and real-machine acceptance are
separate from source and package verification.

The finalized 1.4.1 snapshot restores the already committed terminal-stop and
same-context network-retry edition patch. The agent, LLM transport and model-watch
module are verified in the actual package tree; native desktop/body bytes are unchanged.
