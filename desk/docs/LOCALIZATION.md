# Adding product text

UI language and agent-context language are independent. An English interface with a Russian agent context is supported. Check the relevant boundary before choosing a catalog.

| Text owner | Source and renderer |
| --- | --- |
| Window, MiniApp, installer, tray and product notices | `desk/lang/{ru,en,fr,zh}.json`; existing UI `t`/`uiText` helpers and native UI-caption codes |
| Model context, tool result explanations, prompts | `helene/core/frame_text/{ru,en,fr,zh}.json`; `frame_language.text` |
| Readonly API captions explicitly scoped to the interface | `interface_language.text` or the existing interface wrapper; never use the UI preference to select the model frame |
| Tool descriptions and purposes | Existing general/native/MCP/private-worker language registries, source fingerprints and their tests |
| Factory constitution, stock skills and resident materials | Existing four-language stock resources and reviewed predecessor hashes; preserve edited owner files during upgrades |

Put the whole owned sentence into a named catalog entry. Pass filenames, IDs, user names, code and raw errors as values. Keep those values exact. Do not translate a complete tool result, agent answer, authored Markdown, command, enum, JSON status or digest. Do not parse translated prose to decide whether an effect succeeded: use producer metadata and preserve uncertain outcomes.

For one or several new captions, prepare a UTF-8 JSON file:

```json
{
  "feature.caption": {
    "ru": "Файл {path} готов.",
    "en": "File {path} is ready.",
    "fr": "Le fichier {path} est prêt.",
    "zh": "文件 {path} 已就绪。"
  }
}
```

From the repository root:

```sh
node desk/qa/localize.mjs set ui reviewed-translations.json
# Use "frame" for owned model-context/tool-result captions.
node desk/qa/localize.mjs check all
```

The setter requires all four translations and matching parameters before it writes any catalog. It stages temporary files and replaces each catalog atomically; the batch is a source edit, not a multi-file filesystem transaction. Review the Git diff. The UI review stamps follow the Russian source; frame review stamps bind all four values. A manual catalog edit without renewed review fails the ordinary gates and names the stale key. Do not refresh stamps to hide an unreviewed translation.

Pin the frame language around an entire tool/prompt producer with `@frame_language.pinned`. Nested producers inherit that choice; a saved language change belongs to the next call/turn. Include language in a cache key if the cached value contains owned prose. Authored files are localized only when initially seeded, then remain owned by their author.

Existing guards check catalog completeness, parameters, review stamps, tool/schema coverage and stock provenance. The UI build also scans direct `t`/`uiText`/`tn` calls with literal keys across the five frontends and shared UI kit, using the installed TypeScript 7 JavaScript scanner without starting a compiler worker. Dynamic/conditional keys, arbitrary hardcoded sentences, code inside complex templates and translation quality still need review: inspect new visible labels and reachable helper leaves explicitly. For a new surface, add one meaningful behavioral case with independent UI/frame choices, exact literal payloads and effect/receipt assertions. Use the relevant existing tests rather than copying a test for each phrase.

The normal frontend build runs the UI catalog check. The ordinary Node suite also checks frame reviews; the core frame-language suite verifies them without requiring Node. Before a release, run the existing full product gates from one frozen source. Source checks alone do not establish packaged-platform or owner acceptance.
