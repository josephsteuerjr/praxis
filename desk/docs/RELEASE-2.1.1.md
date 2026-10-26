# Hélène 2.1.1 — updates that keep your agent's code

Hélène is a portable Python desktop application. Its application directory carries the runtime, interface and migration tools; your agent can keep changing its own source between official releases. Whisper and XTTS remain the only optional voice downloads.

## Merge upstream changes into your own code

Updates now compare three complete source states: the previous official release, your agent's current code, and the incoming release. Git combines independent edits, including edits to different parts of the same file, and handles renames and deletions. Uncommitted and newly created source files are included. Existing agent Git history is preserved.

The clean incoming release is retained separately from the merged result. The next update therefore starts from the correct official baseline instead of treating your agent's previous customizations as upstream code. A private local Git record retains the three input commits and the resulting merge.

When changes really conflict, an already updated Hélène prepares an isolated candidate and gives the agent a bounded `source_update` tool to inspect the three sides and resolve the requested paths. It cannot use that tool to overwrite running code or unrelated files. The application stays open; after the agent finishes, press Update again. Paused agents remain paused. Unresolved conflicts, changed inputs and invalid Python prevent application of the candidate.

## Keep the profile where it is

An in-place update retains the existing memory, settings and model locations. It backs up essential state and prepares a separate code candidate, without migrating the whole profile or duplicating voice models. Failed startup uses the existing recovery journal to restore the prior program and configuration, including selection of the previous authored code.

## Compatibility and delivery

The download manifest is readable by the original Python 2.0 updater again. A legacy, unavailable build-tools entry exists only for that older parser; it contains no compiler archive and introduces no new optional download. Existing Whisper and XTTS payloads are reused.

The first transition from an older application uses its retained pristine source tree when that is separate from the agent's working code. An installation that edited its only stock tree needs an exact original baseline; the updater will not invent one from file hashes. The automatic conflict-resolution tool becomes available after this version is installed.

Windows x64, macOS Apple Silicon, Debian x86_64 and Fedora x86_64 remain portable archives. This release changes Python update logic and the update status interface; the previously qualified native runtimes and voice binaries are reused.
