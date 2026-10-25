# Releasing portable Hélène

The current desktop delivery is a portable archive with bundled Python. Reuse the qualified runtime and unchanged native OS tools. For a Python/UI change, update the selected source, build only changed frontend assets, refresh the program inventories and package the matching platform archive. Windows uses `installer/windows_archive_entry.py` and the Python migration window; a Rust launcher/setup rebuild is not part of ordinary portable publication.

Optional large components are Whisper and XTTS only. Keep their immutable, size/SHA-pinned release asset objects available. Reuse their existing release record when consolidating a sole-version publication, and bind all component URLs to its final tag. The standalone relay has its own repository and release lifecycle. Existing installed components and authored code are preserved.

Use the platform snapshots in `SOURCE-SNAPSHOTS.md`. The prepared source for each platform must match its packaged `source-inputs.json`; do not infer parity from a shared version number. Preserve profiles, dirty source and Git state when checking migration or update behavior. Check only the behavior affected by a change, then perform the necessary native platform smoke checks. Healthy builds and CI jobs should be reused unless their inputs changed.

Before publication, bind the exact archives to sizes, SHA-256 values and the source snapshots. Upload them to one draft, inspect uploaded identities, publish that draft and independently verify anonymous downloads. Preserve a recoverable backup before removing old Git tags. GitHub automatically turns a release into a draft when its Git tag is deleted, making its downloads unavailable. Retag the release that owns the retained voice assets and verify the final component URLs BEFORE deleting obsolete Git tags. Back up any superseded program assets and source refs before pruning them.

Write factual English release notes explaining user-visible changes, startup/migration steps and material platform limits. Keep source checks, isolated runtime checks, public-byte verification and physical owner acceptance distinct.

Historical Rust shell/service and native package builders remain in the repository for their corresponding editions. They do not define the 2.1 portable installation path.
