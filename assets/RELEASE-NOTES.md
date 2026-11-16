# Hélène 2.8.0 Beta

**Windows prerelease. Stable Latest remains 2.7.2.** Download the Windows archive
from this release explicitly; the stable updater continues to offer 2.7.2.

The interface and agent context now have independent language settings: Russian,
English, French and Chinese. Tool explanations, operational messages, the factory
constitution and stock skills follow their own language setting. Existing authored
files remain yours. Resident skills can keep a short practice in the agent's
context; the neutral living-prose practice has a per-agent switch, enabled by default.

Praxis features now included in Hélène:

- `run_command` executes exact program arguments, with stdin, environment and file
  output receipts. `code_query` provides structural code navigation.
- Turn titles follow the message that started the turn. Generation progress and
  selected-turn reset expose the actual attempt and stop its owned work.
- Epoch tapes retain edits and deletions as evidence, retire superseded revisions
  and wait for journal admission when needed. Journal indexes and memory caches
  preserve revision identity and recorded provenance.
- Forge on Windows uses Job objects to keep custody of owned processes. Explicit
  recovery handles older tasks with unknown ownership without inventing a stopped
  result or rewriting their history. Finish and Stop use recorded machine outcomes.

Windows MCP is an optional component installed through Components. It supplies
native Windows tools locally or through a paired Windows desktop for a server
agent, subject to the existing Computer grants.

The bundled relay contains the quota failover candidate based on praxis-relay
1.2.0 (source commit `8ba23b89`). It can switch between saved ChatGPT subscription
slots after quota exhaustion. Its source is retained in the package; this release
does not add a separate relay updater. Browser and device-code sign-in retain the
2.7.2 supervisor fix.

Windows corrections include sandbox attachment permissions, clearer refusal when
a run id is supplied instead of a Forge task id, an explanation of `/dev/null` on
Windows, resizing from window edges, a keyless local llama.cpp endpoint and an
honest receipt for a completed turn without a reply. Lesson matching also handles
CJK and accented text. Ordinary Anthropic whitespace-only text is classified as
empty; opaque provider state continues to use the non-retryable partial path.

## Qualification limits

Only Windows assets are published in this prerelease. macOS and Linux artifacts
are not included. Earlier macOS signing limits remain: ad hoc signing without
notarization. Physical acceptance on Tim's Windows 10, owner work-PC/phone,
microphone, CUDA, public ACME and paid providers remains separate. Completing real
account consent requires the account owner.

The core suite is compared with published 2.7.2: its inherited failures remain
visible in release evidence. This release does not claim an entirely green core
suite or physical-device acceptance. Isolated package checks do not update or
restart the owner's installation or production Praxis.

## Release checks

Frozen source `d8bf6f2535fe776388d2b91f801ac44745c18d55`; all five frontends built. Core: 8003 tests, zero new failures against published 2.7.2; 163 inherited failures remain. Desk: 491 modules and the pytest lane passed. Node:176 tests; browser:32 independent UI/context combinations and bounded window-retention checks passed. Windows: actual embedded Python/bootstrap, 269 non-admin installation checks, prior 2.7.2 upgrade, real packaged MCP and relay sign-in survival passed in isolated fixtures. Native runtime checks bind the final archive by exact production byte identity; the last two changes are test fixtures and their integrity metadata. Anonymous public-byte verification follows publication.
