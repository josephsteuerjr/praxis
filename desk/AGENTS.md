# Interface and native product text

Before changing product wording, follow [the localization guide](docs/LOCALIZATION.md). New visible captions need reviewed Russian, English, French and Chinese entries in the existing UI/native registries. Model-context text belongs to the frame catalog instead; interface language and agent-context language remain independent.

Review new visible labels and reachable helper messages explicitly: arbitrary raw sentences and dynamic keys are not all detectable by the static scanner. Preserve authored text, file contents, commands, canonical status codes, hashes and raw external evidence. Use the producer's recorded outcome to describe effects; translated prose cannot prove success.

From the repository root, run `node desk/qa/localize.mjs check all` and the relevant existing behavioral checks. For a new surface, verify independent interface/context choices and exact payload or effect preservation. The guide covers tool schemas, factory resources, native captions and all five frontend build prechecks. Packaged-platform checks remain part of release qualification.
