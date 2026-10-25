# Legacy update entry

While legacy desktop versions are supported, each release includes the thin
migration wizard as `Helene/helene-setup.exe` inside `Helene-VERSION.zip`, as well
as the human-named standalone `helene-VERSION-setup.exe` asset. Build the wizard
with the release's Cargo package version and run `installer/add_migration_entry.py`
after assembling the archive. Upload both files and their regenerated SHA-256
sidecars together. Do not run the older broker-removal repacker afterwards: it
removes the setup entry.

The old button extracts the archive to `Downloads/Helene-VERSION/Helene` and
launches setup with `--update --dir OLD`. The wizard confirms the transition,
locates the original ZIP one level above the extraction folder (or accepts
`--archive ZIP`), checks its published SHA-256 and checks extracted member bytes.
It reuses the adjacent Python package rather than downloading it a second time.
Missing component ZIPs come from the same versioned release, are SHA-256 checked
before extraction and remain cached across preparation retries. Only completed
component extractions receive a completion marker. Preparation can retry;
once migration starts, a failure requires inspection of its retained receipt.
The old service and executables bound to the selected old installation stop
before final data/code capture. No process is stopped during mere downloading.

A standalone wizard downloads the package into Downloads. The ordinary nested
legacy-update extraction is retained after success because it contains the running
wizard; automatic deletion of that running directory is not promised here.
The new Python application's later updates use `python_updates.py` and
`python_source_updater.py`, not this one-time migration path. A fake next-version
exercise is a separate local test, never a public release announcement.
