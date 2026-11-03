# Retained platform execution evidence

This packet supplies historical execution evidence for the native macOS fsops changes and
the Darwin/POSIX CommandCustody cases which cannot execute in the Windows update
battery. It does not replace the current complete Windows gate or independent
review, and does not approve a current macOS, Linux, or Windows package.

Files are under `qualification/retained-platform-tests/`. `index.json` records
their byte hashes and source bindings. Original log bytes are preserved under
`.txt` names because the source merger excludes `.log`; names and hashes are
recorded. Receipts have not been rewritten or relabelled as current runs.

## Native Rust/fsops: actual macOS execution

Read `native-macos/NATIVE-SOURCE.json` and `native-macos/native-unit.txt`.
The complete composed native build inputs (Praxis body followed by the Hélène
edition overlay) match the receipt's `source_files`, including Cargo.lock.
Compare the receipt with the current native source hashes. All actual merged
core native files also match; the source merger excludes Cargo.lock from core
M, so its matching build input comes from the full candidate source bundle.

The log records the Apple system-alias and native-Git patch tests passing,
alongside the other fsops cases. `native-release-build.txt` and
`rust-version.txt` retain the compilation evidence. The receipt identifies
signed standalone body and bridge outputs. It explicitly excludes shell,
tray, system-service and release-package acceptance.

## CommandProcess: actual Darwin execution

Read `darwin-command-process/verification-contracts.txt`, `machine.json`,
`retrieval.json`, and `verification-manifest.json` in that directory.
The manifest's `files` entries identify the historical bytes of
`helene/core/command_process.py` and `helene/core/test_command_process_2810.py`
(also their `live/` mirrors); their hashes are repeated in `index.json`.
The current concurrent-receipt fixture has changed since that execution.
The old whole-file test hash therefore does not qualify today's test file.
The computed retained-artifact report records the historical and current
hashes and each source mismatch; compare those with the actual final M.
The recorded CommandCustody cases include signal permission refusal, rapid
exit, loss of custody, unknown census and descendant completion. They passed
on the macOS machine recorded in the receipt, rather than being Windows skips.
The POSIX production/test closure uses the standard library; psutil is imported
only inside WindowsJob.attach_and_resume and is not part of these Darwin cases.

The Python CI receipt is from an earlier run than the standalone Rust receipt.
Its candidate.json contains earlier native binary hashes; those are NOT the
Rust/fsops qualification used above. These two evidence scopes are separate.
No Linux Forge execution or current whole-product macOS acceptance is claimed.

An independent reviewer must compare these hashes with the actual final M.
Any later code or dependency change invalidates the affected historical proof.

## Locations of the independent source bindings

In a frozen update-review subject, `distribution/python-package-files.json`
contains the incoming package's file digests. For every `source_files` entry in
`native-macos/NATIVE-SOURCE.json`, its composed verification input is
`verification/repository/live/body/` followed by that entry's relative path.
Compare the same entry with the final `resolved/body/` manifest metadata when
that file belongs to the merged core. `Cargo.lock` is excluded from the core
merge; its incoming digest is present under both
`verification/repository/helene/core/body/Cargo.lock` and
`verification/repository/live/body/Cargo.lock` in the package manifest. Thus its
binding is available without inferring it from this document.

The retained Darwin `verification-manifest.json` is the original single-line
JSON. Its `files` entries include `helene/core/command_process.py`,
`helene/core/test_command_process_2810.py` and both `live/` mirrors. Their recorded
digests can be compared with the final `resolved/` manifest entries. Literal
search character offsets locate these entries in the original bytes; the
manifest has not been rewritten into a new receipt. The retained index also
records the original manifest's digest, but that whole-file digest alone does
not establish any individual source binding.

Current Windows execution and declared skips remain in `battery/report.md` and
its per-lane diagnostic files. Those current results and historical component
logs have different scopes; matching source identities do not turn historical
component execution into current package or installed-runtime acceptance.

## Original Darwin execution chain

The following additional material addresses the historical execution-to-source
binding. It preserves the original receipts above unchanged. It is not a new
native run, a replacement test receipt, or approval of the current package.

The original GitHub Actions job 124815974770 belongs to run 41295697225 and
completed successfully. Its original log below records checkout commit
94407f1f6b5ddc3593dc1335c81ccf7fa820004d, execution of
`python3 desk/tests/build_macos_final.py prepare`, and upload of artifact
13078364189 with SHA-256
ed89bd6a5d081f6f4f440e79c3f6b393534e255bbfbbec75e134c90249586b8a.
The full original artifact was retrieved and checked against that digest; its
ZIP CRC checks passed. The retained test log, manifest, admission and machine
files are byte-identical to the members enumerated below. The member paths
distinguish the logs from the candidate archive included in the same artifact.

The complete original invocation sources follow, exported from that exact Git
commit. Every source digest also equals its entry in the original retained
verification manifest; these are historical scripts for inspection, not scripts
to execute from the current product. Read their functions directly:

1. The workflow runs the `prepare` entry point of build_macos_final.py.
2. `prepare` copies the admitted donor runtime to final-runtime, installs test
   dependencies there, then execs its Python with the `build` entry point.
3. `build` runs the seven recorded unittest cases with that `sys.executable`,
   `-B`, cwd REPO/desk and `check=True`, writing verification-contracts.log.
   The first test module adds REPO/helene/core to sys.path before unittest
   loads test_command_process_2810.CommandCustody. That test and its production
   module match the original manifest and the current resolved source bytes.
4. The same build process captures executor_lock, including sys.version and the
   SHA-256 of sys.executable, and builds the verification manifest from REPO.
   The Python executable inside the original candidate archive independently
   matches that recorded hash. Pip installation does not replace this executable.
5. The workflow uploads evidence-final, evidence-extended and the candidate
   archive in the same successful job. The original job log binds that upload
   to the full artifact digest; exact member comparisons bind the retained
   CommandCustody output and manifest to that artifact.

This establishes the historical invocation and input association. It does not
claim a new execution or hardware attestation. No current whole-product macOS,
Linux, or installed Windows acceptance follows from this historical evidence.
The current Windows gate and independent update review remain separate.

### Artifact and interpreter binding

```json
{
  "scope": "original historical Darwin CommandCustody execution, not current package acceptance",
  "run": 41295697225,
  "job": 124815974770,
  "conclusion": "success",
  "source_commit": "94407f1f6b5ddc3593dc1335c81ccf7fa820004d",
  "artifact": 13078364189,
  "artifact_sha256": "ed89bd6a5d081f6f4f440e79c3f6b393534e255bbfbbec75e134c90249586b8a",
  "artifact_crc_verified": true,
  "members_exact": {
    "praxis/praxis/evidence-final/verification-contracts.log": {
      "sha256": "95a12fb6119b70f27c3e39b3f818fde8b133abe341ee4fc5bebcc6b16dcb2ce1",
      "size": 1179,
      "crc32": "6b8aecf3"
    },
    "praxis/praxis/evidence-final/verification-manifest.json": {
      "sha256": "bc517ebe0dd0eaf19608a540cf56588e557e212000b83c1246dd2de3bb9d779a",
      "size": 865832,
      "crc32": "7dacf455"
    },
    "_temp/final-output/candidate.json": {
      "sha256": "a9fd0189dfd5591ac64ed08c6166b529a09735b0befbd88531aaae1e1b690a1d",
      "size": 837,
      "crc32": "a8185397"
    },
    "praxis/praxis/evidence-final/candidate.json": {
      "sha256": "a9fd0189dfd5591ac64ed08c6166b529a09735b0befbd88531aaae1e1b690a1d",
      "size": 837,
      "crc32": "a8185397"
    },
    "praxis/praxis/evidence-final/ADMISSION.json": {
      "sha256": "e47766fd31d5e533f0a0218550f1e0393ce9983c515bef80c85ff1e4d6c85054",
      "size": 914,
      "crc32": "d67d36f3"
    },
    "praxis/praxis/evidence-extended/machine.json": {
      "sha256": "54807883f24d68de057902aa8f1511cd5337f6b818d190d9cbdb260b560dbeb1",
      "size": 269,
      "crc32": "6cc72563"
    }
  },
  "interpreter": {
    "archive_member": "_temp/final-output/Helene-2.3.0-rc.18-macos-arm64.zip",
    "candidate_sha256": "43115b145684a98554d3adcbb129580ab9fd783502ac183dbc412576b3a67d88",
    "python_member": "Helene/runtime/bin/python3",
    "python_sha256": "01a840f3e077b6a6d2566d82450a1e73f9f95c782c33fde0048d8d40f5e1c5f6",
    "python_version": "3.14.7 (main, Sep  1 2026, 14:05:28) [Clang 22.1.3 ]"
  },
  "job_log_sha256": "ad8dcd564973abc80d64bfe97c01dbb4079d3390e479eb12009ebecd47dd587d",
  "invocation_source": {
    ".github/workflows/rc18-macos-final.yml": {
      "sha256": "9b0a808a4ce68f06025cd666c6f152824e690c53c6d691b40d366fc081cfc337",
      "size": 1867,
      "in_original_manifest": true
    },
    "desk/tests/build_macos_final.py": {
      "sha256": "981cb8fad1e0aceeae108cd95275eaca2fc391c69a721b9b2ad17605e56adb00",
      "size": 4771,
      "in_original_manifest": true
    },
    "desk/tests/extended_macos.py": {
      "sha256": "0de181e0e4ebc2eadeac0ef064a5a591a67323f162ca952dc3471e1c56ee1445",
      "size": 7396,
      "in_original_manifest": true
    },
    "desk/tests/t_source_update_battery_2810.py": {
      "sha256": "5935e4e9662b426613e4ec53bbc278fdc30c146afac25939335a62e4009bb693",
      "size": 24644,
      "in_original_manifest": true
    },
    "desk/localharness/source_verification_bundle.py": {
      "sha256": "e1e6e9641577af735d845efbe83f74253fa97a07ee184c19b0d410ccb62638f2",
      "size": 24264,
      "in_original_manifest": true
    }
  },
  "production_and_test_equal_current": true,
  "new_native_execution_performed": false
}
```

### Original successful GitHub job log

```text
﻿2026-11-01T10:02:39.8552950Z Current runner version: '2.338.2'
2026-11-01T10:02:39.8586700Z ##[group]Runner Image Provisioner
2026-11-01T10:02:39.8588180Z Hosted Compute Agent
2026-11-01T10:02:39.8589190Z Version: 20261021.618
2026-11-01T10:02:39.8590270Z Commit: 21505ad2541d5be6135ed84fc33697146cf75b8e
2026-11-01T10:02:39.8591540Z Build Date: 2026-10-21T00:45:21Z
2026-11-01T10:02:39.8592720Z Worker ID: {4fed40b6-d4df-469a-8843-f0691e3fb060}
2026-11-01T10:02:39.8593970Z Region: westus
2026-11-01T10:02:39.8594900Z Cloud: MacCloud
2026-11-01T10:02:39.8595840Z ##[endgroup]
2026-11-01T10:02:39.8598880Z ##[group]Operating System
2026-11-01T10:02:39.8599970Z macOS
2026-11-01T10:02:39.8600860Z 15.8.2
2026-11-01T10:02:39.8601910Z 24H232
2026-11-01T10:02:39.8602810Z ##[endgroup]
2026-11-01T10:02:39.8603770Z ##[group]Runner Image
2026-11-01T10:02:39.8604790Z Image: macos-15-arm64
2026-11-01T10:02:39.8605790Z Version: 20261028.0489.1
2026-11-01T10:02:39.8608120Z Included Software: https://github.com/actions/runner-images/blob/macos-15-arm64/20261028.0489/images/macos/macos-15-arm64-Readme.md
2026-11-01T10:02:39.8611020Z Image Release: https://github.com/actions/runner-images/releases/tag/macos-15-arm64%2F20261028.0489
2026-11-01T10:02:39.8612790Z ##[endgroup]
2026-11-01T10:02:39.8614930Z ##[group]GITHUB_TOKEN Permissions
2026-11-01T10:02:39.8620070Z Actions: read
2026-11-01T10:02:39.8621090Z Contents: read
2026-11-01T10:02:39.8622050Z Metadata: read
2026-11-01T10:02:39.8623030Z ##[endgroup]
2026-11-01T10:02:39.8626990Z Secret source: Actions
2026-11-01T10:02:39.8628580Z Cache mode: write
2026-11-01T10:02:39.8630270Z Prepare workflow directory
2026-11-01T10:02:39.9079660Z Prepare all required actions
2026-11-01T10:02:39.9160950Z Getting action download info
2026-11-01T10:02:40.3278650Z Download action repository 'actions/checkout@v4' (SHA:11d5960a326750d5838078e36cf38b85af677262)
2026-11-01T10:02:40.9450700Z Download action repository 'actions/cache@v4' (SHA:0057852bfaa89a56745cba8c7296529d2fc39830)
2026-11-01T10:02:41.0896140Z Download action repository 'actions/upload-artifact@v4' (SHA:ea165f8d65b6e75b540449e92b4886f43607fa02)
2026-11-01T10:02:41.2922760Z Complete job name: candidate
2026-11-01T10:02:41.3583690Z ##[group]Run actions/checkout@v4
2026-11-01T10:02:41.3584440Z with:
2026-11-01T10:02:41.3584840Z   persist-credentials: false
2026-11-01T10:02:41.3585260Z   repository: josephsteuerjr/praxis
2026-11-01T10:02:41.3588340Z   token: ***
2026-11-01T10:02:41.3588750Z   ssh-strict: true
2026-11-01T10:02:41.3589120Z   ssh-user: git
2026-11-01T10:02:41.3589480Z   clean: true
2026-11-01T10:02:41.3589830Z   sparse-checkout-cone-mode: true
2026-11-01T10:02:41.3590250Z   fetch-depth: 1
2026-11-01T10:02:41.3590610Z   fetch-tags: false
2026-11-01T10:02:41.3590980Z   show-progress: true
2026-11-01T10:02:41.3591370Z   lfs: false
2026-11-01T10:02:41.3593550Z   submodules: false
2026-11-01T10:02:41.3594180Z   set-safe-directory: true
2026-11-01T10:02:41.3594610Z   allow-unsafe-pr-checkout: false
2026-11-01T10:02:41.3595780Z ##[endgroup]
2026-11-01T10:02:41.8735990Z Syncing repository: josephsteuerjr/praxis
2026-11-01T10:02:41.8738180Z ##[group]Getting Git version info
2026-11-01T10:02:41.8739100Z Working directory is '/Users/runner/work/praxis/praxis'
2026-11-01T10:02:41.8740330Z [command]/opt/homebrew/bin/git version
2026-11-01T10:02:41.9013160Z git version 2.56.0
2026-11-01T10:02:41.9034840Z ##[endgroup]
2026-11-01T10:02:41.9042670Z Copying '/Users/runner/.gitconfig' to '/Users/runner/work/_temp/2bf2857b-6556-46e5-afd9-f0a7640a5edc/.gitconfig'
2026-11-01T10:02:41.9047720Z Temporarily overriding HOME='/Users/runner/work/_temp/2bf2857b-6556-46e5-afd9-f0a7640a5edc' before making global git config changes
2026-11-01T10:02:41.9050630Z Adding repository directory to the temporary git global config as a safe directory
2026-11-01T10:02:41.9053010Z [command]/opt/homebrew/bin/git config --global --add safe.directory /Users/runner/work/praxis/praxis
2026-11-01T10:02:41.9156930Z Deleting the contents of '/Users/runner/work/praxis/praxis'
2026-11-01T10:02:41.9159290Z ##[group]Initializing the repository
2026-11-01T10:02:41.9162390Z [command]/opt/homebrew/bin/git init /Users/runner/work/praxis/praxis
2026-11-01T10:02:41.9380440Z hint: Using 'master' as the name for the initial branch. This default branch name
2026-11-01T10:02:41.9382980Z hint: will change to "main" in Git 3.0. To configure the initial branch name
2026-11-01T10:02:41.9385110Z hint: to use in all of your new repositories, which will suppress this warning,
2026-11-01T10:02:41.9386490Z hint: call:
2026-11-01T10:02:41.9387190Z hint:
2026-11-01T10:02:41.9388080Z hint: 	git config --global init.defaultBranch <name>
2026-11-01T10:02:41.9389230Z hint:
2026-11-01T10:02:41.9390230Z hint: Names commonly chosen instead of 'master' are 'main', 'trunk' and
2026-11-01T10:02:41.9391940Z hint: 'development'. The just-created branch can be renamed via this command:
2026-11-01T10:02:41.9393310Z hint:
2026-11-01T10:02:41.9393990Z hint: 	git branch -m <name>
2026-11-01T10:02:41.9395470Z hint:
2026-11-01T10:02:41.9396590Z hint: Disable this message with "git config set advice.defaultBranchName false"
2026-11-01T10:02:41.9398580Z Initialized empty Git repository in /Users/runner/work/praxis/praxis/.git/
2026-11-01T10:02:41.9407860Z [command]/opt/homebrew/bin/git remote add origin https://github.com/josephsteuerjr/praxis
2026-11-01T10:02:41.9479230Z ##[endgroup]
2026-11-01T10:02:41.9481850Z ##[group]Disabling automatic garbage collection
2026-11-01T10:02:41.9483080Z [command]/opt/homebrew/bin/git config --local gc.auto 0
2026-11-01T10:02:41.9556020Z ##[endgroup]
2026-11-01T10:02:41.9557220Z ##[group]Setting up auth
2026-11-01T10:02:41.9559670Z [command]/opt/homebrew/bin/git config --local --name-only --get-regexp core\.sshCommand
2026-11-01T10:02:41.9627950Z [command]/opt/homebrew/bin/git submodule foreach --recursive sh -c "git config --local --name-only --get-regexp 'core\.sshCommand' && git config --local --unset-all 'core.sshCommand' || :"
2026-11-01T10:02:42.0607000Z [command]/opt/homebrew/bin/git config --local --name-only --get-regexp http\.https\:\/\/github\.com\/\.extraheader
2026-11-01T10:02:42.0653980Z [command]/opt/homebrew/bin/git submodule foreach --recursive sh -c "git config --local --name-only --get-regexp 'http\.https\:\/\/github\.com\/\.extraheader' && git config --local --unset-all 'http.https://github.com/.extraheader' || :"
2026-11-01T10:02:42.1552720Z [command]/opt/homebrew/bin/git config --local --name-only --get-regexp ^includeIf\.gitdir:
2026-11-01T10:02:42.1611040Z [command]/opt/homebrew/bin/git submodule foreach --recursive git config --local --show-origin --name-only --get-regexp remote.origin.url
2026-11-01T10:02:42.3338620Z [command]/opt/homebrew/bin/git config --local http.https://github.com/.extraheader AUTHORIZATION: basic ***
2026-11-01T10:02:42.3605740Z ##[endgroup]
2026-11-01T10:02:42.3609350Z ##[group]Fetching the repository
2026-11-01T10:02:42.3615790Z [command]/opt/homebrew/bin/git -c protocol.version=2 fetch --no-tags --prune --no-recurse-submodules --depth=1 origin +94407f1f6b5ddc3593dc1335c81ccf7fa820004d:refs/remotes/origin/codex/rc18-macos-0111
2026-11-01T10:02:44.7786150Z From https://github.com/josephsteuerjr/praxis
2026-11-01T10:02:44.7788830Z  * [new ref]         94407f1f6b5ddc3593dc1335c81ccf7fa820004d -> origin/codex/rc18-macos-0111
2026-11-01T10:02:44.7792060Z ##[endgroup]
2026-11-01T10:02:44.7792730Z ##[group]Determining the checkout info
2026-11-01T10:02:44.7793450Z ##[endgroup]
2026-11-01T10:02:44.7794640Z [command]/opt/homebrew/bin/git sparse-checkout disable
2026-11-01T10:02:44.7867570Z [command]/opt/homebrew/bin/git config --local --unset-all extensions.worktreeConfig
2026-11-01T10:02:44.7926360Z ##[group]Checking out the ref
2026-11-01T10:02:44.7929410Z [command]/opt/homebrew/bin/git checkout --progress --force -B codex/rc18-macos-0111 refs/remotes/origin/codex/rc18-macos-0111
2026-11-01T10:02:45.2210490Z Switched to a new branch 'codex/rc18-macos-0111'
2026-11-01T10:02:45.2213040Z branch 'codex/rc18-macos-0111' set up to track 'origin/codex/rc18-macos-0111'.
2026-11-01T10:02:45.2219410Z ##[endgroup]
2026-11-01T10:02:45.2312890Z [command]/opt/homebrew/bin/git log -1 --format=%H
2026-11-01T10:02:45.2395820Z 94407f1f6b5ddc3593dc1335c81ccf7fa820004d
2026-11-01T10:02:45.2402340Z ##[group]Removing auth
2026-11-01T10:02:45.2406650Z [command]/opt/homebrew/bin/git config --local --name-only --get-regexp core\.sshCommand
2026-11-01T10:02:45.2466810Z [command]/opt/homebrew/bin/git submodule foreach --recursive sh -c "git config --local --name-only --get-regexp 'core\.sshCommand' && git config --local --unset-all 'core.sshCommand' || :"
2026-11-01T10:02:45.3342470Z [command]/opt/homebrew/bin/git config --local --name-only --get-regexp http\.https\:\/\/github\.com\/\.extraheader
2026-11-01T10:02:45.3404330Z http.https://github.com/.extraheader
2026-11-01T10:02:45.3408860Z [command]/opt/homebrew/bin/git config --local --unset-all http.https://github.com/.extraheader
2026-11-01T10:02:45.3634370Z [command]/opt/homebrew/bin/git submodule foreach --recursive sh -c "git config --local --name-only --get-regexp 'http\.https\:\/\/github\.com\/\.extraheader' && git config --local --unset-all 'http.https://github.com/.extraheader' || :"
2026-11-01T10:02:45.4858040Z [command]/opt/homebrew/bin/git config --local --name-only --get-regexp ^includeIf\.gitdir:
2026-11-01T10:02:45.5165050Z [command]/opt/homebrew/bin/git submodule foreach --recursive git config --local --show-origin --name-only --get-regexp remote.origin.url
2026-11-01T10:02:45.6132310Z ##[endgroup]
2026-11-01T10:02:45.6391700Z ##[group]Run python3 desk/tests/rc18_macos_pipeline.py key >> "$GITHUB_OUTPUT"
2026-11-01T10:02:45.6392290Z [36;1mpython3 desk/tests/rc18_macos_pipeline.py key >> "$GITHUB_OUTPUT"[0m
2026-11-01T10:02:45.6436240Z shell: /bin/bash -e {0}
2026-11-01T10:02:45.6436600Z ##[endgroup]
2026-11-01T10:02:46.2392480Z ##[group]Run actions/cache/restore@v4
2026-11-01T10:02:46.2392840Z with:
2026-11-01T10:02:46.2393160Z   path: /Users/runner/work/_temp/rc18-fronts
2026-11-01T10:02:46.2393690Z   key: helene-rc18-macos-fronts-bb4e2a14ca5b5786143b2f3740735f1dd5bbacbca7bc31a433047a2a7c14e96d
2026-11-01T10:02:46.2394650Z   enableCrossOsArchive: false
2026-11-01T10:02:46.2395390Z   fail-on-cache-miss: false
2026-11-01T10:02:46.2395650Z   lookup-only: false
2026-11-01T10:02:46.2395980Z ##[endgroup]
2026-11-01T10:02:46.4114820Z (node:1266) [DEP0040] DeprecationWarning: The `punycode` module is deprecated. Please use a userland alternative instead.
2026-11-01T10:02:46.4115950Z (Use `node --trace-deprecation ...` to show where the warning was created)
2026-11-01T10:02:46.6961790Z Cache hit for: helene-rc18-macos-fronts-bb4e2a14ca5b5786143b2f3740735f1dd5bbacbca7bc31a433047a2a7c14e96d
2026-11-01T10:02:46.7033500Z (node:1266) [DEP0169] DeprecationWarning: `url.parse()` behavior is not standardized and prone to errors that have security implications. Use the WHATWG URL API instead. CVEs are not issued for `url.parse()` vulnerabilities.
2026-11-01T10:02:47.7198710Z Received 1390503 of 1390503 (100.0%), 1.9 MBs/sec
2026-11-01T10:02:47.7232150Z Cache Size: ~1 MB (1390503 B)
2026-11-01T10:02:47.7235160Z [command]/opt/homebrew/bin/gtar -xf /Users/runner/work/_temp/c38426c6-94a1-4a2e-a51b-7b6a57b394b6/cache.tzst -P -C /Users/runner/work/praxis/praxis --delay-directory-restore --use-compress-program unzstd
2026-11-01T10:02:47.7717060Z Cache restored successfully
2026-11-01T10:02:47.8002730Z Cache restored from key: helene-rc18-macos-fronts-bb4e2a14ca5b5786143b2f3740735f1dd5bbacbca7bc31a433047a2a7c14e96d
2026-11-01T10:02:47.8096110Z ##[group]Run actions/cache/restore@v4
2026-11-01T10:02:47.8096360Z with:
2026-11-01T10:02:47.8096650Z   path: /Users/runner/work/_temp/rc18-verification-tools
2026-11-01T10:02:47.8099050Z   key: helene-rc18-macos-verification-tools-bb4e2a14ca5b5786143b2f3740735f1dd5bbacbca7bc31a433047a2a7c14e96d-v1
2026-11-01T10:02:47.8099670Z   fail-on-cache-miss: true
2026-11-01T10:02:47.8099860Z   enableCrossOsArchive: false
2026-11-01T10:02:47.8100060Z   lookup-only: false
2026-11-01T10:02:47.8100270Z ##[endgroup]
2026-11-01T10:02:47.9441210Z (node:1272) [DEP0040] DeprecationWarning: The `punycode` module is deprecated. Please use a userland alternative instead.
2026-11-01T10:02:47.9444960Z (Use `node --trace-deprecation ...` to show where the warning was created)
2026-11-01T10:02:48.2058070Z Cache hit for: helene-rc18-macos-verification-tools-bb4e2a14ca5b5786143b2f3740735f1dd5bbacbca7bc31a433047a2a7c14e96d-v1
2026-11-01T10:02:48.2086050Z (node:1272) [DEP0169] DeprecationWarning: `url.parse()` behavior is not standardized and prone to errors that have security implications. Use the WHATWG URL API instead. CVEs are not issued for `url.parse()` vulnerabilities.
2026-11-01T10:02:49.5023990Z Received 0 of 76125338 (0.0%), 0.0 MBs/sec
2026-11-01T10:02:50.5051070Z Received 71931034 of 76125338 (94.5%), 34.2 MBs/sec
2026-11-01T10:02:50.6773680Z Received 76125338 of 76125338 (100.0%), 33.3 MBs/sec
2026-11-01T10:02:50.6789760Z Cache Size: ~73 MB (76125338 B)
2026-11-01T10:02:50.6866080Z [command]/opt/homebrew/bin/gtar -xf /Users/runner/work/_temp/86ebb7f4-5ef7-49eb-89a3-fbecd1c8e0b7/cache.tzst -P -C /Users/runner/work/praxis/praxis --delay-directory-restore --use-compress-program unzstd
2026-11-01T10:02:51.4366240Z Cache restored successfully
2026-11-01T10:02:51.4481610Z Cache restored from key: helene-rc18-macos-verification-tools-bb4e2a14ca5b5786143b2f3740735f1dd5bbacbca7bc31a433047a2a7c14e96d-v1
2026-11-01T10:02:51.4612040Z ##[group]Run actions/cache/restore@v4
2026-11-01T10:02:51.4639840Z with:
2026-11-01T10:02:51.4640080Z   path: /Users/runner/work/_temp/rc18-evidence.zip
2026-11-01T10:02:51.4640420Z   key: helene-macos-runtime-6eb05a4b19951fa9
2026-11-01T10:02:51.4640750Z   fail-on-cache-miss: true
2026-11-01T10:02:51.4640960Z   enableCrossOsArchive: false
2026-11-01T10:02:51.4641210Z   lookup-only: false
2026-11-01T10:02:51.4641410Z ##[endgroup]
2026-11-01T10:02:51.6614010Z (node:1280) [DEP0040] DeprecationWarning: The `punycode` module is deprecated. Please use a userland alternative instead.
2026-11-01T10:02:51.6615060Z (Use `node --trace-deprecation ...` to show where the warning was created)
2026-11-01T10:02:51.9490860Z Cache hit for: helene-macos-runtime-6eb05a4b19951fa9
2026-11-01T10:02:51.9521670Z (node:1280) [DEP0169] DeprecationWarning: `url.parse()` behavior is not standardized and prone to errors that have security implications. Use the WHATWG URL API instead. CVEs are not issued for `url.parse()` vulnerabilities.
2026-11-01T10:02:53.2276270Z Received 4194304 of 114550348 (3.7%), 4.0 MBs/sec
2026-11-01T10:02:54.2331970Z Received 96468992 of 114550348 (84.2%), 46.0 MBs/sec
2026-11-01T10:02:55.2320900Z Received 113246208 of 114550348 (98.9%), 35.9 MBs/sec
2026-11-01T10:02:55.4733200Z Received 114550348 of 114550348 (100.0%), 33.6 MBs/sec
2026-11-01T10:02:55.5053300Z Cache Size: ~109 MB (114550348 B)
2026-11-01T10:02:55.6067580Z [command]/opt/homebrew/bin/gtar -xf /Users/runner/work/_temp/9db15132-cb1c-492b-9008-42905cc1d233/cache.tzst -P -C /Users/runner/work/praxis/praxis --delay-directory-restore --use-compress-program unzstd
2026-11-01T10:02:55.9353680Z Cache restored successfully
2026-11-01T10:02:55.9521000Z Cache restored from key: helene-macos-runtime-6eb05a4b19951fa9
2026-11-01T10:02:55.9691800Z ##[group]Run actions/cache/restore@v4
2026-11-01T10:02:55.9692330Z with:
2026-11-01T10:02:55.9692690Z   path: /Users/runner/work/_temp/rc18-qualified-input.zip
2026-11-01T10:02:55.9693120Z   key: helene-rc18-native-79ce64094f84499c
2026-11-01T10:02:55.9693500Z   fail-on-cache-miss: true
2026-11-01T10:02:55.9693900Z   enableCrossOsArchive: false
2026-11-01T10:02:55.9694230Z   lookup-only: false
2026-11-01T10:02:55.9694480Z ##[endgroup]
2026-11-01T10:02:56.2089300Z (node:1295) [DEP0040] DeprecationWarning: The `punycode` module is deprecated. Please use a userland alternative instead.
2026-11-01T10:02:56.2192980Z (Use `node --trace-deprecation ...` to show where the warning was created)
2026-11-01T10:02:56.4950230Z (node:1295) [DEP0169] DeprecationWarning: `url.parse()` behavior is not standardized and prone to errors that have security implications. Use the WHATWG URL API instead. CVEs are not issued for `url.parse()` vulnerabilities.
2026-11-01T10:02:56.4960300Z Cache hit for: helene-rc18-native-79ce64094f84499c
2026-11-01T10:02:57.7837750Z Received 0 of 124586262 (0.0%), 0.0 MBs/sec
2026-11-01T10:02:58.7851240Z Received 75497472 of 124586262 (60.6%), 36.0 MBs/sec
2026-11-01T10:02:59.6453580Z Received 124586262 of 124586262 (100.0%), 41.6 MBs/sec
2026-11-01T10:02:59.6601430Z Cache Size: ~119 MB (124586262 B)
2026-11-01T10:02:59.6955660Z [command]/opt/homebrew/bin/gtar -xf /Users/runner/work/_temp/42a05519-81eb-4504-a461-cde1b93314b1/cache.tzst -P -C /Users/runner/work/praxis/praxis --delay-directory-restore --use-compress-program unzstd
2026-11-01T10:03:00.2787250Z Cache restored successfully
2026-11-01T10:03:00.2964700Z Cache restored from key: helene-rc18-native-79ce64094f84499c
2026-11-01T10:03:00.3217590Z ##[group]Run python3 desk/tests/build_macos_final.py prepare
2026-11-01T10:03:00.3218590Z [36;1mpython3 desk/tests/build_macos_final.py prepare[0m
2026-11-01T10:03:00.3312390Z shell: /bin/bash -e {0}
2026-11-01T10:03:00.3313460Z ##[endgroup]
2026-11-01T10:04:00.3558140Z <unknown>:233: SyntaxWarning: "\w" is an invalid escape sequence. Such sequences will not work in the future. Did you mean "\\w"? A raw string is also an option.
2026-11-01T10:04:00.3573920Z <unknown>:233: SyntaxWarning: "\w" is an invalid escape sequence. Such sequences will not work in the future. Did you mean "\\w"? A raw string is also an option.
2026-11-01T10:04:06.2268600Z <unknown>:4: SyntaxWarning: "\s" is an invalid escape sequence. Such sequences will not work in the future. Did you mean "\\s"? A raw string is also an option.
2026-11-01T10:05:06.3838010Z /Users/runner/work/_temp/final-output/Helene/Helene.app: replacing existing signature
2026-11-01T10:05:36.6464270Z {"format": "helene-macos-candidate.v1", "version": "2.3.0-rc.18", "target": "macos-arm64", "source_commit": "94407f1f6b5ddc3593dc1335c81ccf7fa820004d", "archive": "Helene-2.3.0-rc.18-macos-arm64.zip", "size": 250303460, "sha256": "43115b145684a98554d3adcbb129580ab9fd783502ac183dbc412576b3a67d88", "files": 21254, "native_reused": {"runtime_donor_sha256": "5e4fe949b0ed44c95d9cdd8dbd20885e3ac2f887eaf41901cdbda8b46ec5a921", "evidence_sha256": "6eb05a4b19951fa9cc27caaca36de0024a5e73fcb5982503798f98959ddc1b91", "run_id": 40233694345, "launcher_sha256": "6d6dc719908d574289d4ac90ee25efdda45038c1ef563513fbb3c24cf4f7887f", "body_sha256": "c0c846d084c791359dd1a5ee06a12556b7598bc191feba80c0639b764715ea60", "bridge_sha256": "6d2b10fe253a47f82bd34f1b16860ed12473ea64b6a1b8ce3df4edf7264753c3"}}
2026-11-01T10:06:51.1001080Z ##[group]Run actions/upload-artifact@v4
2026-11-01T10:06:51.1001940Z with:
2026-11-01T10:06:51.1002340Z   name: helene-rc18-macos-complete
2026-11-01T10:06:51.1003210Z   path: /Users/runner/work/_temp/final-output/*.zip
/Users/runner/work/_temp/final-output/*.sha256
/Users/runner/work/_temp/final-output/candidate.json
evidence-final/
evidence-extended/
evidence-rc18/

2026-11-01T10:06:51.1004110Z   if-no-files-found: warn
2026-11-01T10:06:51.1010880Z   compression-level: 6
2026-11-01T10:06:51.1011650Z   overwrite: false
2026-11-01T10:06:51.1012840Z   include-hidden-files: false
2026-11-01T10:06:51.1106710Z ##[endgroup]
2026-11-01T10:06:51.9419860Z (node:7179) [DEP0040] DeprecationWarning: The `punycode` module is deprecated. Please use a userland alternative instead.
2026-11-01T10:06:51.9422160Z (Use `node --trace-deprecation ...` to show where the warning was created)
2026-11-01T10:06:51.9959940Z Multiple search paths detected. Calculating the least common ancestor of all paths
2026-11-01T10:06:51.9961410Z The least common ancestor is /Users/runner/work. This will be the root directory of the artifact
2026-11-01T10:06:51.9961870Z With the provided path, there will be 34 files uploaded
2026-11-01T10:06:51.9962530Z Artifact name is valid!
2026-11-01T10:06:51.9964410Z Root directory input is valid!
2026-11-01T10:06:52.4131650Z Beginning upload of artifact content to blob storage
2026-11-01T10:06:52.6628600Z (node:7179) [DEP0169] DeprecationWarning: `url.parse()` behavior is not standardized and prone to errors that have security implications. Use the WHATWG URL API instead. CVEs are not issued for `url.parse()` vulnerabilities.
2026-11-01T10:06:53.7177970Z Uploaded bytes 8388608
2026-11-01T10:06:53.9159990Z Uploaded bytes 16777216
2026-11-01T10:06:54.2213330Z Uploaded bytes 25165824
2026-11-01T10:06:54.2436680Z Uploaded bytes 33554432
2026-11-01T10:06:54.5076010Z Uploaded bytes 41943040
2026-11-01T10:06:54.5451980Z Uploaded bytes 50331648
2026-11-01T10:06:54.8409890Z Uploaded bytes 58720256
2026-11-01T10:06:55.0857070Z Uploaded bytes 67108864
2026-11-01T10:06:55.3834680Z Uploaded bytes 75497472
2026-11-01T10:06:55.7110120Z Uploaded bytes 83886080
2026-11-01T10:06:56.0352540Z Uploaded bytes 92274688
2026-11-01T10:06:56.3328980Z Uploaded bytes 100663296
2026-11-01T10:06:56.7061010Z Uploaded bytes 109051904
2026-11-01T10:06:57.2101950Z Uploaded bytes 117440512
2026-11-01T10:06:57.6250280Z Uploaded bytes 125829120
2026-11-01T10:06:57.8484610Z Uploaded bytes 134217728
2026-11-01T10:06:58.2090960Z Uploaded bytes 142606336
2026-11-01T10:06:58.5633290Z Uploaded bytes 150994944
2026-11-01T10:06:58.8809420Z Uploaded bytes 159383552
2026-11-01T10:06:59.1376300Z Uploaded bytes 167772160
2026-11-01T10:06:59.3318850Z Uploaded bytes 176160768
2026-11-01T10:06:59.6067260Z Uploaded bytes 184549376
2026-11-01T10:06:59.9097510Z Uploaded bytes 192937984
2026-11-01T10:07:00.1611010Z Uploaded bytes 201326592
2026-11-01T10:07:00.3736190Z Uploaded bytes 209715200
2026-11-01T10:07:00.7649810Z Uploaded bytes 218103808
2026-11-01T10:07:00.9904480Z Uploaded bytes 226492416
2026-11-01T10:07:01.1841420Z Uploaded bytes 234881024
2026-11-01T10:07:01.4248310Z Uploaded bytes 243269632
2026-11-01T10:07:01.6705170Z Uploaded bytes 246567136
2026-11-01T10:07:01.7590650Z Finished uploading artifact content to blob storage!
2026-11-01T10:07:01.7696280Z SHA256 digest of uploaded artifact zip is ed89bd6a5d081f6f4f440e79c3f6b393534e255bbfbbec75e134c90249586b8a
2026-11-01T10:07:01.7825110Z Finalizing artifact upload
2026-11-01T10:07:02.3902850Z Artifact helene-rc18-macos-complete.zip successfully finalized. Artifact ID 13078364189
2026-11-01T10:07:02.3904060Z Artifact helene-rc18-macos-complete has been successfully uploaded! Final size is 246567136 bytes. Artifact ID is 13078364189
2026-11-01T10:07:02.4067710Z Artifact download URL: https://github.com/josephsteuerjr/praxis/actions/runs/41295697225/artifacts/13078364189
2026-11-01T10:07:02.4555870Z Post job cleanup.
2026-11-01T10:07:02.6928390Z [command]/opt/homebrew/bin/git version
2026-11-01T10:07:02.6960480Z git version 2.56.0
2026-11-01T10:07:02.7268440Z Copying '/Users/runner/.gitconfig' to '/Users/runner/work/_temp/13ceac07-d40f-4a4e-81e5-428a283086e6/.gitconfig'
2026-11-01T10:07:02.7274040Z Temporarily overriding HOME='/Users/runner/work/_temp/13ceac07-d40f-4a4e-81e5-428a283086e6' before making global git config changes
2026-11-01T10:07:02.7275040Z Adding repository directory to the temporary git global config as a safe directory
2026-11-01T10:07:02.7276150Z [command]/opt/homebrew/bin/git config --global --add safe.directory /Users/runner/work/praxis/praxis
2026-11-01T10:07:02.7277770Z [command]/opt/homebrew/bin/git config --local --name-only --get-regexp core\.sshCommand
2026-11-01T10:07:02.7282360Z [command]/opt/homebrew/bin/git submodule foreach --recursive sh -c "git config --local --name-only --get-regexp 'core\.sshCommand' && git config --local --unset-all 'core.sshCommand' || :"
2026-11-01T10:07:02.9522960Z [command]/opt/homebrew/bin/git config --local --name-only --get-regexp http\.https\:\/\/github\.com\/\.extraheader
2026-11-01T10:07:02.9604980Z [command]/opt/homebrew/bin/git submodule foreach --recursive sh -c "git config --local --name-only --get-regexp 'http\.https\:\/\/github\.com\/\.extraheader' && git config --local --unset-all 'http.https://github.com/.extraheader' || :"
2026-11-01T10:07:03.1151930Z [command]/opt/homebrew/bin/git config --local --name-only --get-regexp ^includeIf\.gitdir:
2026-11-01T10:07:03.1456440Z [command]/opt/homebrew/bin/git submodule foreach --recursive git config --local --show-origin --name-only --get-regexp remote.origin.url
2026-11-01T10:07:03.2649290Z Cleaning up orphan processes
2026-11-01T10:07:05.1280320Z Terminate orphan process: pid (6029) (System Events)
2026-11-01T10:07:06.5821520Z ##[warning]Node.js 20 is deprecated. The following actions target Node.js 20 but are being forced to run on Node.js 24: actions/cache/restore@v4, actions/checkout@v4, actions/upload-artifact@v4. For more information see: https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/

```

### Original source: .github/workflows/rc18-macos-final.yml

Original SHA-256: `9b0a808a4ce68f06025cd666c6f152824e690c53c6d691b40d366fc081cfc337`.

```text
name: Hélène RC18 complete macOS package and offline verification executor
on:
  push:
    branches: [codex/rc18-macos-0111]
    paths: [desk/tests/build_macos_final.py, .github/workflows/rc18-macos-final.yml]
permissions:
  contents: read
  actions: read
jobs:
  candidate:
    runs-on: macos-15
    timeout-minutes: 75
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - id: fronts
        run: python3 desk/tests/rc18_macos_pipeline.py key >> "$GITHUB_OUTPUT"
      - uses: actions/cache/restore@v4
        with:
          path: ${{ runner.temp }}/rc18-fronts
          key: helene-rc18-macos-fronts-${{ steps.fronts.outputs.key }}
      - uses: actions/cache/restore@v4
        with:
          path: ${{ runner.temp }}/rc18-verification-tools
          key: helene-rc18-macos-verification-tools-${{ steps.fronts.outputs.key }}-v1
          fail-on-cache-miss: true
      - uses: actions/cache/restore@v4
        with:
          path: ${{ runner.temp }}/rc18-evidence.zip
          key: helene-macos-runtime-6eb05a4b19951fa9
          fail-on-cache-miss: true
      - uses: actions/cache/restore@v4
        id: candidate
        with:
          path: ${{ runner.temp }}/rc18-qualified-input.zip
          key: helene-rc18-native-79ce64094f84499c
          fail-on-cache-miss: true
      - name: Build source-complete native candidate and exercise offline consumer
        run: python3 desk/tests/build_macos_final.py prepare
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: helene-rc18-macos-complete
          path: |
            ${{ runner.temp }}/final-output/*.zip
            ${{ runner.temp }}/final-output/*.sha256
            ${{ runner.temp }}/final-output/candidate.json
            evidence-final/
            evidence-extended/
            evidence-rc18/

```

### Original source: desk/tests/build_macos_final.py

Original SHA-256: `981cb8fad1e0aceeae108cd95275eaca2fc391c69a721b9b2ad17605e56adb00`.

```text
"""Native final package, source admission and real offline executor checks."""
from pathlib import Path
import json,os,shutil,subprocess,sys
REPO=Path(__file__).resolve().parents[2];TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
sys.path[:0]=[str(REPO/'desk'),str(REPO/'desk/installer'),str(Path(__file__).parent)]
import extended_macos as common
OUT=REPO/'evidence-final';OUT.mkdir(exist_ok=True)
RUNTIME=TEMP/'final-runtime'
TOOLS=TEMP/'rc18-verification-tools'

def command(argv,name,**kwargs):
    with (OUT/name).open('wb') as output:
        subprocess.run(list(map(str,argv)),stdout=output,stderr=subprocess.STDOUT,check=True,start_new_session=True,**kwargs)

def prepare():
    program,python=common.prepare()
    shutil.copytree(program/'runtime',RUNTIME,symlinks=False,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    python=RUNTIME/'bin/python3'
    command([python,'-m','pip','install','--disable-pip-version-check','--no-compile','pytest==8.4.2','aiogram','soundfile','cryptography>=43,<49'],'runtime-dependencies.log',timeout=900)
    os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),'build'])

def build():
    from rc18_macos_pipeline import fronts
    from localharness import source_verification_bundle as bundle,source_verification_executor as executor,source_update_host
    from build_python_macos_rc import build as package
    fronts()
    command([sys.executable,'-m','pip','check'],'pip-check.log',timeout=120)
    # Other72 cases passed and3 exact Windows cases skipped on source0a388b1/run41295075384;
    # Only native custody cases affected by the bounded exit observation repeat.
    command([sys.executable,'-B','-m','unittest','tests.t_source_update_battery_2810.Battery.test_output_limit_has_no_passing_coverage','test_command_process_2810.CommandCustody','-v'],
        'verification-contracts.log',cwd=REPO/'desk',timeout=600)
    # Contract tests above include native output-limit custody diagnostics.
    # Materialize only public, immutable cache/tool inputs. The npm cache in
    # each operation will be a separate owned mutable copy.
    for p in TOOLS.rglob('*'):
        if p.is_symlink():raise ValueError('Linked cached executor input')
        p.chmod(p.stat().st_mode & 0o755)
    lock=bundle.executor_lock(tools={'node':str(TOOLS/'bin/node'),'npm':str(TOOLS/'bin/npm')})
    lock['tools'].pop('cargo',None)  # Native organs unchanged; no compiler claim.
    verification=TEMP/'final-verification'
    result=bundle.build(REPO,verification,'2.3.0-rc.18',{'darwin':lock})
    manifest=json.loads((verification/'manifest.json').read_text())
    manifest['bundled_executors']={'darwin':executor.describe(TOOLS)}
    (verification/'manifest.json').write_bytes(bundle.encoded(manifest))
    bundle_sha=bundle.digest(verification/'manifest.json')
    bundle.load(verification,bundle_sha,'2.3.0-rc.18')
    program=package(TEMP/'rc18-evidence.zip',TEMP/'final-build-work',TEMP/'final-output',
        verification=verification,verification_tools=TOOLS,runtime=RUNTIME)
    binding=source_update_host.verified_runtime(program)
    operation=TEMP/'native-executor-probe';operation.mkdir()
    selected=executor.provision(program,manifest,operation)
    # The exact selected npm must install all locked projects with networking
    # disabled by npm itself. This uses no user/system/global package cache.
    source=operation/'source'
    shutil.copytree(REPO,source,ignore=shutil.ignore_patterns('.git','node_modules','__pycache__','*.pyc','evidence-*'))
    env=dict(os.environ,PATH=str(Path(selected['node']).parent)+os.pathsep+os.environ.get('PATH',''),
        npm_config_offline='true',npm_config_userconfig=str(operation/'empty.rc'),npm_config_globalconfig=str(operation/'global.rc'))
    for name in ('app','mobile','miniapp','remote'):
        command([selected['npm'],'ci','--ignore-scripts','--offline','--cache',selected['npm_config_cache'],'--audit=false','--fund=false'],
            'offline-'+name+'.log',cwd=source/'desk'/name,env=env,timeout=300)
    shutil.copy2(TEMP/'final-output/candidate.json',OUT/'candidate.json')
    shutil.copy2(verification/'manifest.json',OUT/'verification-manifest.json')
    (OUT/'ADMISSION.json').write_text(json.dumps({'runtime':binding,'executor':{k:str(v) for k,v in selected.items()},
        'offline_all4_npm_projects':True,'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()},indent=2))
    # Keep a native handoff/GUI/lifecycle smoke on the actual new archive after
    # its final bytes were sealed; further update/matrix jobs reuse this SHA.
    import rc18_macos_pipeline as native
    native.PROGRAM=program
    native.native()

if __name__=='__main__':{'prepare':prepare,'build':build}[sys.argv[1]]()

```

### Original source: desk/tests/extended_macos.py

Original SHA-256: `0de181e0e4ebc2eadeac0ef064a5a591a67323f162ca952dc3471e1c56ee1445`.

```text
"""Extended public native RC qualification; disposable runner/profile only."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import ast,hashlib,importlib.metadata,json,os,shutil,subprocess,sys,time,zipfile

REPO=Path(__file__).resolve().parents[2];TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
EVIDENCE=REPO/'evidence-extended';EVIDENCE.mkdir(exist_ok=True)
sys.path[:0]=[str(REPO/'desk'),str(REPO/'desk/installer')]
from build_python_macos_rc import unpack

def write(name,value):
    p=EVIDENCE/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,indent=2,ensure_ascii=True),encoding='utf-8')

def prepare():
    archive=TEMP/'rc18-qualified-input.zip'
    with archive.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='91d198b1b226ed2c630c57c6602cab963f45f7a678eee314bb06ae32c7dfcf58'
    with zipfile.ZipFile(archive) as z:
        i=next(i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip')
        candidate=TEMP/'base-candidate.zip';candidate.write_bytes(z.read(i))
    with candidate.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='79ce64094f84499c1225feeb498b5310131d9122dfa37f2766279d8d9de66255'
    unpack(candidate,TEMP/'base')
    program=TEMP/'base/Helene';python=program/'runtime/bin/python3'
    write('machine.json',{'os':subprocess.check_output(['sw_vers'],text=True),'arch':os.uname().machine,'cpus':os.cpu_count(),
        'base_sha256':'79ce64094f84499c1225feeb498b5310131d9122dfa37f2766279d8d9de66255','source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()})
    return program,python

def execute(command,log,*,cwd=REPO,env=None,timeout=300):
    p=EVIDENCE/log;p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('wb') as f:
        try:
            r=subprocess.run(list(map(str,command)),cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout,start_new_session=True)
            return r.returncode
        except subprocess.TimeoutExpired:return 124

def broad():
    program,python=prepare()
    from rc18_macos_pipeline import fronts
    fronts()
    # Test dependencies live in this one task's auxiliary directory. Never
    # install into the candidate, system Python or a publisher's runtime.
    deps=TEMP/'qa-deps'
    assert execute([python,'-m','pip','install','--disable-pip-version-check','--no-compile','--target',deps,'pytest==8.4.2','aiogram','soundfile'],
        'qa-dependencies.log',timeout=600)==0
    from localharness.source_verification_bundle import python_framework,executor_lock
    core=TEMP/'composed-core';core.mkdir()
    for folder in ('praxis','helene/core'):
        for p in (REPO/folder).rglob('*'):
            rel=p.relative_to(REPO/folder)
            if not p.is_file() or any(n in ('.git','__pycache__','node_modules','memory','soul','workspace','data') for n in rel.parts):continue
            q=core/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
    runner=REPO/'desk/localharness/source_verification_runner.py'
    lanes=[]
    for shard in range(os.cpu_count() or 3):lanes.append({'id':f'core-{shard}','root':core,'shard':shard,'count':os.cpu_count() or 3})
    for p in sorted((REPO/'desk/tests').glob('*.py')):
        if not p.name.startswith(('t_','test')):continue
        if p.name=='t_owner_stop_seed.py':lanes.append({'id':p.stem,'script':p});continue
        lanes.append({'id':p.stem,'root':REPO/'desk','source':p,'framework':python_framework(p)})
    for p in core.glob('test*.py'):
        if python_framework(p)=='pytest':lanes.append({'id':'pytest-'+p.stem,'root':core,'source':p,'framework':'pytest'})
    write('collection.json',[{k:str(v) for k,v in row.items()} for row in lanes])
    def run(row):
        identity=row['id'];home=TEMP/'broad-homes'/identity;home.mkdir(parents=True)
        # Several canonical suites create temp fixtures beside their source.
        # Each lane therefore owns its complete clean source copy INSIDE the
        # write fence. Sharing a read-only checkout was an invalid test setup.
        source=home/'source'
        shutil.copytree(REPO,source,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc','node_modules','evidence-*'))
        localcore=source/'live';shutil.copytree(core,localcore)
        localdesk=source/'desk'
        env=dict(os.environ,HOME=str(home),CFFIXED_USER_HOME=str(home),USERPROFILE=str(home),TMPDIR=str(home),TMP=str(home),TEMP=str(home),
            PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PRAXIS_TEST='1',HELENE_BODY_DIR=str(program),
            HELENE_TEST_SOURCE_ROOT=str(source),HELENE_TREE_SRC=str(localcore),PRAXIS_TEST_FORGE_PROCESS='1',
            PYTHONPATH=os.pathsep.join([str(deps),str(localcore),str(localdesk),str(localdesk/'localharness')]))
        result=EVIDENCE/'reports'/(identity+'.json');result.parent.mkdir(exist_ok=True)
        if row.get('script'):command=[python,'-B',source/row['script'].relative_to(REPO)]
        else:
            root=localcore if row['root']==core else localdesk
            command=[python,'-B',runner,'--root',root,'--report',result,'--sandbox',home,'--dependencies',deps]
            if row.get('source'):
                original=row['source'];relative=original.relative_to(core) if original.is_relative_to(core) else original.relative_to(REPO/'desk')
                command+=['--source',root/relative,'--framework',row['framework']]
            if 'shard' in row:command+=['--shard-index',row['shard'],'--shard-count',row['count']]
        code=execute(command,'logs/'+identity+'.log',cwd=localcore if identity.startswith(('core-','pytest-')) else localdesk,env=env,timeout=900 if identity.startswith('core-') else 240)
        report=json.loads(result.read_text()) if result.exists() else {}
        shutil.rmtree(source)
        return identity,{'exit':code,'report':str(result.relative_to(EVIDENCE)) if result.exists() else None,
            'tests':report.get('tests_run',0),'collected':len(report.get('collected',[])),'complete':report.get('complete'),
            'failures':[x for x in report.get('outcomes',[]) if x['status'] not in ('passed','skipped','expected-failure')],
            'skips':[x for x in report.get('outcomes',[]) if x['status']=='skipped'],'error':report.get('error')}
    results={}
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 3) as pool:
        futures=[pool.submit(run,lane) for lane in lanes]
        for f in as_completed(futures):
            name,row=f.result();results[name]=row;write('broad-progress.json',{'completed':len(results),'total':len(lanes),'results':results})
            print(json.dumps({'completed':len(results),'total':len(lanes),'lane':name,'exit':row['exit'],'tests':row['tests']}),flush=True)
    result={'complete':len(results)==len(lanes),'results':results,'tests':sum(r['tests'] for r in results.values()),
            'failed_lanes':[n for n,r in results.items() if r['exit']], 'zero_case_lanes':[n for n,r in results.items() if not r['tests']]}
    write('BROAD.json',result)
    # This discovery run records ALL failures for audit; it never narrows the
    # collection to turn a red complete sweep into a green product gate.
    assert not result['failed_lanes'],'Broad native sweep found failures; inspect BROAD.json'

if __name__=='__main__':{'broad':broad}[sys.argv[1]]()

```

### Original source: desk/tests/t_source_update_battery_2810.py

Original SHA-256: `5935e4e9662b426613e4ec53bbc278fdc30c146afac25939335a62e4009bb693`.

```text
"""Native isolated verification consumers; no production battery/models/network.

Python/Node fixture cases really execute. Stubbed native build results below test
ONLY receipt validation, never establish a platform or toolchain PASS.
"""
from pathlib import Path
from collections import Counter
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[2] / 'helene/core')]
from localharness import source_merge as merge, source_update_battery as battery
from localharness import source_verification_bundle as bundle
from localharness.package_notice import read, write

TEST = '''import unittest, os
class T(unittest.TestCase):
 def test_value(self):
  self.assertNotIn("PRIVATE_OWNER_VALUE", os.environ)
  self.assertEqual(1, 1)
'''


class Battery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='verification-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'source'
        for name, text in {
            'praxis/praxis_test.py': '# canonical hermetic bootstrap fixture\n',
            'praxis/test_first.py': TEST,
            'praxis/test_second.py': TEST,
            'helene/core/test_second.py': TEST.replace('test_value', 'test_overlay'),
            'desk/tests/t_one.py': TEST,
            'desk/tests/test_two.py': TEST,
            'desk/app/test/one.mjs': 'import assert from "node:assert/strict"; assert.equal(1,1);',
        }.items():
            self.put(self.repo / name, text)
        for root in bundle.NATIVE:
            prefix = 'praxis/' + root[5:] if root.startswith('live/') else root
            self.put(self.repo / prefix / 'Cargo.toml', '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n')
            self.put(self.repo / prefix / 'Cargo.lock', '# fixture lock\n')
            self.put(self.repo / prefix / 'src/lib.rs', '#[test] fn fixture() {}\n')
        for root in bundle.NPM_PROJECTS:
            self.put(self.repo / root / 'package.json', '{"scripts":{"build":"fixture"}}')
            self.put(self.repo / root / 'package-lock.json', '{"lockfileVersion":3,"packages":{}}')
        self.lock = bundle.executor_lock()
        self.bundle = self.root / 'verification'
        self.binding = bundle.build(self.repo, self.bundle, '2.3.0', {sys.platform: self.lock}, native=bundle.NATIVE)
        self.manifest = read(self.bundle / 'manifest.json')
        self.directory = self.root / 'plan'
        self.core = self.directory / 'result'
        # Real packaged code normally omits tests; the bundle supplies all tests.
        self.put(self.core / 'praxis_test.py', '# canonical hermetic bootstrap fixture\n')
        self.put(self.core / 'logic.py', 'VALUE = 1\n')
        for name in ('body', 'relay', 'hands'):
            shutil.copytree(self.bundle / 'repository/live' / name, self.core / name)
        self.app = self.root / 'app'
        self.put(self.app / 'seam.py', 'VALUE = 1\n')
        self.runtime = self.root / 'runtime'
        self.put(self.runtime / 'runtime.lock', 'pinned-runtime-fixture\n')
        self.operation = {'candidate_sha256': merge.candidate_signature(self.directory),
            'input_sha256': 'input-fixture', 'version': '2.3.0',
            'verification_bundle': self.binding}
        npm_cache = self.root / 'owned-empty-npm-cache'; npm_cache.mkdir()
        self.operation['verification_executor'] = {'npm_config_cache': str(npm_cache)}
        self.patch = mock.patch.object(merge, 'validate_inputs', side_effect=lambda *a: {'files': {'incoming': bundle.inventory(self.core)}})
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.comp = mock.patch.object(battery, 'composition', side_effect=self.composition)
        self.comp.start(); self.addCleanup(self.comp.stop)

    def put(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf8', newline='')

    def composition(self, *args):
        value = {name: {'root': str(root), 'files': bundle.inventory(root)}
                 for name, root in [('core', self.core), ('app', self.app), ('runtime', self.runtime)]}
        value['manifests'] = {'package': 'fixture-package'}
        # Explicit fixture runtime membership, not a product runtime claim.
        value['runtime']['files']['python.exe'] = {'sha256': bundle.digest(sys.executable), 'mode': '100755'}
        value['runtime']['root'] = str(Path(sys.executable).parent)
        value['sha256'] = merge.signature({k: v for k, v in value.items()})
        return value

    def prepare(self):
        with mock.patch.dict(os.environ, {'PRIVATE_OWNER_VALUE': 'not-for-test'}):
            sources = battery.prepare(self.operation, self.directory, {}, lambda: False)
        cache = Path(sources['battery']['root']).parent
        return cache, read(cache / 'report.json')

    def fake_native(self, lane, *args):
        if lane['kind'] in {'unittest', 'unittest-file'}:
            return self.real_lane(lane, *args)
        unit = lane['id'] + ':stubbed-native-unit'
        return {'id': lane['id'], 'kind': lane['kind'], 'status': 'passed', 'exit': 0,
            'observation': {'collected': [unit], 'executed': [unit], 'complete': True,
                'coverage_complete': True, 'tests_run': 1,
                'outcomes': [{'id': unit, 'status': 'passed'}]}}

    def green_fixture(self):
        # A Python suite is real. This synthetic native executor only exercises
        # protocol validation and must never be mistaken for product full QA.
        tools, observed, _ = battery._tools(self.operation, self.manifest)
        self.real_lane = battery._run_lane
        with mock.patch.object(battery, '_tools', return_value=(tools, observed, {})), \
             mock.patch.object(battery, '_run_lane', side_effect=self.fake_native):
            cache, report = self.prepare()
            verified = battery.validate(self.operation, self.directory)
        return cache, report, verified

    def test_full_recipe_is_derived_from_both_core_layers_and_all_desk_suites(self):
        self.assertIn('live/test_first.py', next(r for r in self.manifest['lanes'] if r['id'] == 'core')['sources'])
        self.assertIn('live/test_second.py', next(r for r in self.manifest['lanes'] if r['id'] == 'core')['sources'])
        self.assertIn('test_overlay', (self.bundle / 'repository/live/test_second.py').read_text())
        ids = [r['id'] for r in self.manifest['lanes']]
        self.assertIn('desk/tests/t_one.py', ids)
        self.assertIn('desk/tests/test_two.py', ids)
        self.assertTrue(set(bundle.NATIVE) <= set(ids))
        self.assertNotIn('selected', self.manifest['reuse'])
        bundle.load(self.bundle, self.binding['sha256'], '2.3.0')

    def test_focused_core_consumer_runs_hermetic_bootstrap_before_its_import(self):
        self.put(self.core/'praxis_test.py', 'import os\nos.environ["FIXTURE_CORE_BOOTSTRAPPED"]="yes"\n')
        source=self.core/'test_focus.py'
        self.put(source, 'import os,unittest\nassert os.environ.get("FIXTURE_CORE_BOOTSTRAPPED")=="yes"\n'
                         'class Focus(unittest.TestCase):\n def test_bootstrap(self):self.assertTrue(True)\n')
        env=battery._environment(self.root/'home',self.core,self.app)
        observation=self.root/'focused-observation.json'
        command=[sys.executable,'-B','-s',str(self.bundle/'runner/source_verification_runner.py'),
                 '--root',str(self.core),'--source',str(source),'--report',str(observation),'--sandbox',str(self.root)]
        result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        observed=read(observation)
        self.assertTrue(observed['complete']);self.assertTrue(observed['coverage_complete'])
        self.assertEqual([row['status'] for row in observed['outcomes']],['passed'])

    def test_actual_full_python_node_and_missing_native_fail_honestly(self):
        cache, report = self.prepare()
        core = next(r for r in report['results'] if r['id'] == 'core')
        self.assertEqual(core['status'], 'passed', core)
        self.assertEqual(core['observation']['tests_run'], 2)
        self.assertTrue(report['complete'])
        self.assertFalse(report['full_pass'])
        self.assertIn('battery', self.operation)
        native = next(r for r in report['results'] if r['id'] == 'live/body')
        self.assertNotEqual(native['status'], 'platform-skip')
        with self.assertRaises(ValueError):
            battery.validate(self.operation, self.directory)
        self.assertTrue(list(cache.glob('*.output')))

    def test_exact_whole_composition_cache_and_changed_candidate_full_rerun(self):
        cache, report, verified = self.green_fixture()
        self.assertTrue(verified['full_pass'])
        self.assertEqual(next(r for r in report['results'] if r['id'] == 'core')['observation']['tests_run'], 2)
        with mock.patch.object(battery, '_execute', side_effect=AssertionError('duplicate execution')):
            self.assertEqual(self.prepare()[0], cache)
        self.put(self.core / 'test_added.py', TEST)
        self.operation['candidate_sha256'] = merge.candidate_signature(self.directory)
        with self.assertRaisesRegex(ValueError, 'exact current composition'):
            with mock.patch.object(battery, '_tools', return_value=battery._tools(self.operation, self.manifest)[:2] + ({},)):
                battery.validate(self.operation, self.directory)
        next_cache, next_report, _ = self.green_fixture()
        self.assertNotEqual(cache, next_cache)
        self.assertEqual(next(r for r in next_report['results'] if r['id'] == 'core')['observation']['tests_run'], 3)

    def test_cleanup_failure_preserves_complete_evidence_and_never_repeats_lanes(self):
        actual_rmtree = shutil.rmtree
        def denied(path, *args, **kwargs):
            if Path(path).name.startswith('helene-update-tests-'):
                self.assertTrue(list(self.directory.glob('test-battery/*/receipt.json')),
                                'Completed evidence must be committed before cleanup')
                raise PermissionError('fixture: locked Git object')
            return actual_rmtree(path, *args, **kwargs)
        with mock.patch.object(battery.shutil,'rmtree',side_effect=denied):
            cache,report,_=self.green_fixture()
        cleanup=read(cache/'cleanup.json')
        work=Path(cleanup['work'])
        self.addCleanup(lambda: actual_rmtree(work) if work.exists() else None)
        self.assertEqual(cleanup['state'],'pending')
        self.assertIn('locked Git object',cleanup['error'])
        self.assertTrue(report['complete']);self.assertTrue(report['full_pass'])
        with mock.patch.object(battery,'_execute',side_effect=AssertionError('duplicate execution')):
            self.assertEqual(self.prepare()[0],cache)

    def test_cleanup_failure_does_not_promote_red_lanes(self):
        actual_rmtree=shutil.rmtree
        def denied(path,*args,**kwargs):
            if Path(path).name.startswith('helene-update-tests-'):raise PermissionError('fixture cleanup')
            return actual_rmtree(path,*args,**kwargs)
        with mock.patch.object(battery.shutil,'rmtree',side_effect=denied):
            cache,report=self.prepare()
        work=Path(read(cache/'cleanup.json')['work'])
        self.addCleanup(lambda:actual_rmtree(work) if work.exists() else None)
        self.assertFalse(report['full_pass'])
        self.assertTrue((cache/'receipt.json').is_file())
        with self.assertRaises(ValueError):battery.validate(self.operation,self.directory)

    @unittest.skipUnless(os.name=='nt','Windows read-only Git objects')
    def test_owned_readonly_git_file_cleanup_leaves_no_work_tree(self):
        import stat
        work=Path(tempfile.mkdtemp(prefix='helene-update-tests-'))
        obj=work/'.git/objects/01/object';obj.parent.mkdir(parents=True);obj.write_bytes(b'fixture')
        obj.chmod(stat.S_IREAD)
        result=battery._cleanup_work(work,self.root/'cleanup-cache')
        self.assertEqual(result['state'],'removed');self.assertFalse(work.exists())

    def test_app_and_runtime_changes_invalidate_receipt_not_only_core(self):
        cache, _, _ = self.green_fixture()
        for path in (self.app / 'seam.py', self.runtime / 'runtime.lock'):
            original = path.read_bytes()
            path.write_bytes(original + b'# changed\n')
            with self.assertRaises(ValueError):
                battery.validate(self.operation, self.directory)
            path.write_bytes(original)
        self.assertTrue(cache.is_dir())

    def test_mutated_raw_or_frozen_evidence_never_reused(self):
        cache, _, _ = self.green_fixture()
        report = cache / 'evidence/report.md'
        report.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.prepare()

    def test_stop_before_cache_and_owner_cancellation(self):
        self.green_fixture()
        with self.assertRaises(InterruptedError):
            battery.prepare(self.operation, self.directory, {}, lambda: True)
        self.put(self.directory / 'cancel.json', '{}')
        with self.assertRaisesRegex(ValueError, 'Stopped/paused'):
            battery.validate(self.operation, self.directory)

    def test_tampered_bundle_or_lane_list_rejected_before_execution(self):
        manifest = read(self.bundle / 'manifest.json')
        manifest['lanes'].pop(0)
        write(self.bundle / 'manifest.json', manifest)
        self.operation['verification_bundle']['sha256'] = bundle.digest(self.bundle / 'manifest.json')
        with self.assertRaisesRegex(ValueError, 'omits a full discovery lane'):
            self.prepare()

    def test_zero_collected_not_promoted_to_full_by_complete_flag(self):
        row = {'id': 'fixture', 'kind': 'unittest', 'status': 'passed', 'exit': 0,
               'observation': {'complete': True, 'coverage_complete': True, 'collected': [], 'executed': []}}
        with self.assertRaisesRegex(ValueError, 'Nonzero exact'):
            battery._check_results({'lanes': [{'id': 'fixture', 'kind': 'unittest'}], 'results': [row]}, {})

    def test_exact_platform_skip_allowlist_not_missing_dependency_skip(self):
        lane = {'id': 'fixture', 'kind': 'unittest'}
        case = {'id': 'test.T.test_native', 'status': 'skipped', 'reason': 'Windows only'}
        row = {'id': 'fixture', 'status': 'passed', 'exit': 0, 'observation': {
            'complete': True, 'coverage_complete': True, 'collected': [case['id']],
            'executed': [case['id']], 'tests_run': 1, 'outcomes': [case]}}
        report = {'lanes': [lane], 'results': [row]}
        with self.assertRaisesRegex(ValueError, 'Undeclared test skip'):
            battery._check_results(report, {})
        policy = {'allowed_test_skips': {sys.platform: {'fixture::' + case['id']: 'Windows only'}}}
        battery._check_results(report, policy)
        case['reason'] = 'Dependency not installed'
        with self.assertRaisesRegex(ValueError, 'Undeclared test skip'):
            battery._check_results(report, policy)

    def test_failed_case_and_partial_execution_refuse_full(self):
        _, report, _ = self.green_fixture()
        current = copy.deepcopy(report)
        next(r for r in current['results'] if r['id'] == 'core')['observation']['executed'].pop()
        with self.assertRaisesRegex(ValueError, 'collected/executed'):
            battery._check_results(current, self.manifest)
        current = copy.deepcopy(report)
        next(r for r in current['results'] if r['id'] == 'core')['observation']['outcomes'][0]['status'] = 'failed'
        with self.assertRaisesRegex(ValueError, 'not passed'):
            battery._check_results(current, self.manifest)

    def test_actual_test_failure_remains_readable_and_no_gate_pass(self):
        self.put(self.core / 'test_changed.py', TEST.replace('assertEqual(1, 1)', 'assertEqual(1, 2)'))
        self.operation['candidate_sha256'] = merge.candidate_signature(self.directory)
        _, report = self.prepare()
        self.assertEqual(next(r for r in report['results'] if r['id'] == 'core')['status'], 'failed')
        self.assertFalse(report['full_pass'])
        self.assertTrue(any(row['status'] == 'failed' for row in next(r for r in report['results'] if r['id'] == 'core')['observation']['outcomes']))

    def test_unconfirmed_custody_keeps_work_but_never_complete(self):
        captured = []
        def broken(lane, repository, *args):
            captured.append(repository.parent)
            raise battery.CustodyUnconfirmed('fixture custody unknown')
        with mock.patch.object(battery, '_run_lane', side_effect=broken):
            with self.assertRaises(battery.CustodyUnconfirmed):
                self.prepare()
        self.assertTrue(captured[0].is_dir())
        self.addCleanup(shutil.rmtree, captured[0])
        self.assertFalse(list((self.directory / 'test-battery').glob('*/receipt.json')))
        self.assertTrue(list((self.directory / 'test-battery').glob('*/custody-unconfirmed.json')))

    def test_actual_stop_terminates_synthetic_child_without_complete_receipt(self):
        self.put(self.core / 'test_sleep.py', 'import time\ntime.sleep(60)\n')
        self.operation['candidate_sha256'] = merge.candidate_signature(self.directory)
        calls = 0
        def stopped():
            nonlocal calls
            calls += 1
            return calls > 10
        with self.assertRaises(InterruptedError):
            battery.prepare(self.operation, self.directory, {}, stopped)
        self.assertFalse(list((self.directory / 'test-battery').glob('*/receipt.json')))

    def test_output_limit_has_no_passing_coverage(self):
        self.put(self.core / 'test_loud.py', 'print("large-output" * 10000)\n')
        self.operation['candidate_sha256'] = merge.candidate_signature(self.directory)
        with mock.patch.object(battery, 'MAX_OUTPUT', 1000):
            _, report = self.prepare()
        self.assertEqual(next(r for r in report['results'] if r['id'] == 'core')['status'], 'output-limit')
        self.assertFalse(report['full_pass'])


    def test_explicit_script_contract_runs_supported_cli_not_zero_unittest_cases(self):
        repository = self.root / 'script-repository'
        script = repository / 'desk/tests/t_cli.py'
        self.put(script, 'import argparse\np=argparse.ArgumentParser()\np.add_argument("--value",required=True)\na=p.parse_args()\nassert a.value == "checked"\nprint("SCRIPT_CONSUMER_CHECKED")\n')
        files = dict(self.manifest['files'])
        files['desk/tests/t_cli.py'] = {'sha256': bundle.digest(script)}
        scripts = {'desk/tests/t_cli.py': {'kind': 'script', 'argv': ['--value', 'checked']}}
        lane = next(r for r in bundle.recipe(files, scripts=scripts) if r['id'] == 'desk/tests/t_cli.py')
        cache = self.root / 'script-cache'; cache.mkdir()
        env = battery._environment(self.root / 'script-home', self.core, repository / 'desk')
        row = battery._run_lane(lane, repository, None, {'python': sys.executable}, env, cache, lambda: False)
        self.assertEqual(row['status'], 'passed')
        self.assertEqual(row['observation']['checks_run'], 1)
        self.assertNotIn('tests_run', row['observation'])
        battery._check_results({'lanes': [lane], 'results': [row]}, {})
        self.assertIn('SCRIPT_CONSUMER_CHECKED', next(cache.glob('*.output')).read_text())
        with self.assertRaisesRegex(ValueError, 'coverage identity'):
            bundle.recipe(files, scripts={'desk/tests/t_cli.py': {'kind': 'helper', 'id': 'not-the-full-suite', 'reason': 'bad'}})

    def test_pytest_lane_executes_with_python_when_cargo_is_unavailable(self):
        self.put(self.repo / 'desk/tests/test_functions.py',
                 'def test_actual_fixture(tmp_path):\n assert tmp_path.is_dir()\n')
        self.bundle = self.root / 'pytest-verification'
        self.binding = bundle.build(self.repo, self.bundle, '2.3.0', {sys.platform: self.lock})
        self.manifest = read(self.bundle / 'manifest.json')
        self.operation['verification_bundle'] = self.binding
        tools, observed, errors = battery._tools(self.operation, self.manifest)
        errors = {**errors, 'cargo': 'No Cargo supplied for this Python-only lane',
                  'node': 'Node deliberately unavailable in this focused Python fixture'}
        with mock.patch.object(battery, '_tools', return_value=(tools, observed, errors)):
            _, report = self.prepare()
        row = next(r for r in report['results'] if r['id'] == 'desk/tests/test_functions.py')
        self.assertEqual(row['status'], 'passed', row)
        self.assertEqual(len(row['observation']['collected']), 1)
        self.assertEqual(row['observation']['collected'], row['observation']['executed'])
        self.assertFalse(report['full_pass'])  # The unrelated missing Node is not waived.

    def test_current_python_product_has_no_obsolete_rust_prerequisite(self):
        lanes = bundle.recipe(self.manifest['files'])
        ids = {r['id'] for r in lanes}
        self.assertFalse(any(n in ids for n in ('desk/shell', 'desk/svc', 'desk/setup')))
        self.assertFalse(any(r['kind'] == 'cargo' for r in lanes))
        self.assertFalse(any(r['kind'] == 'npm-build' for r in lanes))
        before = bundle.inventory(self.core)
        self.put(self.core / 'body/src/lib.rs', '#[test] fn changed_native() {}\n')
        value = self.composition()
        with mock.patch.object(merge, 'validate_inputs', return_value={'files': {'incoming': before}}):
            native, rebuild = battery._obligations(self.directory, value, {'native_lanes': [], 'build_lanes': []})
        self.assertEqual(native, ['live/body'])
        self.assertEqual(rebuild, [])

    def test_deleted_composed_app_file_does_not_execute_old_bundle_copy(self):
        repository = self.root / 'isolated-repository'
        bundle.copy_files(self.bundle / 'repository', self.manifest['files'], repository)
        self.put(repository / 'desk/deleted.py', 'raise AssertionError("old app leaked")\n')
        base = {'deleted.py': {'sha256': bundle.digest(repository / 'desk/deleted.py')}}
        value = self.composition()
        battery._copy_composition(value, repository, self.manifest, base)
        self.assertFalse((repository / 'desk/deleted.py').exists())
        self.assertNotIn('desk/deleted.py', battery._verification_files(value, self.manifest, base))

    def test_pinned_offline_wheel_provisions_private_target_and_is_rechecked(self):
        wheels = self.root / 'wheels'
        wheels.mkdir()
        wheel = wheels / 'tiny_verification_fixture-1.0-py3-none-any.whl'
        dist = 'tiny_verification_fixture-1.0.dist-info/'
        with zipfile.ZipFile(wheel, 'w') as z:
            z.writestr('tiny_verification_fixture.py', 'VALUE = "offline fixture"\n')
            z.writestr(dist + 'METADATA', 'Metadata-Version: 2.1\nName: tiny-verification-fixture\nVersion: 1.0\n')
            z.writestr(dist + 'WHEEL', 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            z.writestr(dist + 'RECORD', '')
        package = {'version': '1.0', 'filename': wheel.name, 'sha256': bundle.digest(wheel)}
        self.put(wheels / 'wheels.json', json.dumps({'packages': {'tiny-verification-fixture': package}}))
        lock = copy.deepcopy(self.lock)
        lock['python_packages']['tiny-verification-fixture'] = '1.0'
        target_bundle = self.root / 'offline-bundle'
        binding = bundle.build(self.repo, target_bundle, '2.3.0', {sys.platform: lock}, wheelhouses={sys.platform: wheels})
        target = self.root / 'offline-site'
        executor = bundle.provision(target_bundle, binding['sha256'], '2.3.0', sys.executable, target)
        self.assertTrue((target / 'tiny_verification_fixture.py').is_file())
        operation = {**self.operation, 'verification_bundle': binding, 'verification_executor': executor}
        manifest = read(target_bundle / 'manifest.json')
        self.assertNotIn('python', battery._tools(operation, manifest)[2])
        (target / 'tiny_verification_fixture.py').write_text('CHANGED = True\n')
        self.assertIn('python', battery._tools(operation, manifest)[2])


if __name__ == '__main__':
    unittest.main()

```

### Original source: desk/localharness/source_verification_bundle.py

Original SHA-256: `e1e6e9641577af735d845efbe83f74253fa97a07ee184c19b0d410ccb62638f2`.

```text
"""Version-bound, source-only verification material; no installer or network effects.

The publisher supplies this directory beside the program, and binds manifest.json
in the incoming package inventory. The candidate cannot choose a smaller recipe.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import site
from functools import lru_cache

FORMAT = 'helene-verification-bundle.v1'
EXCLUDED = {'.git', '.ouroboros', '__pycache__', '.pytest_cache', '.venv', 'venv',
            'node_modules', 'target', '.cache', '_state'}
PRIVATE = {'.env', 'helene.json', 'auth.json', 'credentials.json', 'local-build.json'}
TREES = ('praxis', 'helene/core', 'desk')
CONTEXT_TREES = ('remote', 'arete-contract', 'swarm-base', '.github')
CONTEXT_FILES = ('LICENSE', 'NOTICE', 'README.md', 'CORE-SOURCE.json', 'HELENE-SOURCE.json',
                 'RELAY-SOURCE.json', 'SOURCE-SNAPSHOTS.md', 'helene/EDITION.md', 'helene/PLATFORM-SOURCE.json')
RUNNERS = ('source_verification_runner.py',)
# Current Python application has no legacy shell/svc/setup Rust prerequisite.
# Native source repairs request the actual affected component, not every old crate.
NATIVE = ('live/body', 'live/relay', 'live/hands')
# app imports desk/remote/src/settings-server, which resolves its own qrcode.
# These are the existing locked projects, not a general workspace installer.
NPM_PROJECTS = ('desk/app', 'desk/mobile', 'desk/miniapp', 'desk/remote')
SHARED_NATIVE_INPUTS=frozenset({'HELENE_BODY_DIR','HELENE_BODY_BIN','DISPLAY',
                              'DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR'})


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf8')


def digest(path):
    from .validation_read import checkpoint,digest as measured
    checkpoint()
    with Path(path).open('rb') as stream:
        return measured(stream)


def relative(name):
    from .source_merge import relative as checked
    return checked(name)


def python_framework(path):
    """Select pytest only for actual top-level test functions (including fixtures)."""
    path=Path(path)
    if path.suffix!='.py' or not path.name.startswith(('test','t_')):return 'unittest'
    return _python_framework_content(path.read_bytes())


def python_native_inputs(path):
    path=Path(path)
    return _native_input_content(path.read_bytes()) if path.suffix=='.py' else None


@lru_cache(maxsize=2048)
def _native_input_content(content):
    """A test must expressly request the shared desktop capability it uses."""
    try:module=ast.parse(content.decode('utf-8-sig'))
    except (SyntaxError,UnicodeError):return None  # Its canonical import lane fails.
    found=[]
    for node in module.body:
        targets=node.targets if isinstance(node,ast.Assign) else [node.target] if isinstance(node,ast.AnnAssign) else []
        if any(isinstance(target,ast.Name) and target.id=='HELENE_TEST_NATIVE_INPUTS' for target in targets):
            try:value=ast.literal_eval(node.value)
            except (ValueError,TypeError):raise ValueError('Test native inputs must be a literal tuple')
            if (not isinstance(value,tuple) or any(not isinstance(key,str) or key not in SHARED_NATIVE_INPUTS for key in value)
                    or len(value)!=len(set(value))):
                raise ValueError('Invalid test native input declaration')
            if 'HELENE_BODY_BIN' in value and 'HELENE_BODY_DIR' not in value:
                raise ValueError('Native body capability requires its declared directory')
            found.append(sorted(value))
    if len(found)>1:raise ValueError('Duplicate test native input declaration')
    return found[0] if found else None


def native_lane_inputs(files,names):
    result=set()
    for name in names:
        value=files.get(name,{}).get('native_inputs',[])
        if (not isinstance(value,list) or any(not isinstance(key,str) or key not in SHARED_NATIVE_INPUTS for key in value)
                or value!=sorted(set(value))):
            raise ValueError('Invalid frozen native input metadata: '+name)
        result.update(value)
    return sorted(result)


@lru_cache(maxsize=2048)
def _python_framework_content(content):
    # Classification depends on exact bytes. Twelve private, hash-verified
    # copies need not parse the same test AST twelve more times under the GIL.
    try:
        module=ast.parse(content.decode('utf-8-sig'))
    except (SyntaxError,UnicodeError):
        # The canonical import lane reports the real error, never a helper skip.
        return 'unittest'
    return 'pytest' if any(isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test')
                           for node in module.body) else 'unittest'


def inventory(root):
    """Exact regular files including lockfiles; unlike source_merge, no .lock filter."""
    from .validation_read import checkpoint
    checkpoint()
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Verification directory unavailable: ' + str(root))
    rows = {}
    for directory, dirs, names in os.walk(root):
        checkpoint()
        here = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in [*dirs, *sorted(names)]:
            checkpoint()
            p = here / name
            rel = p.relative_to(root).as_posix()
            relative(rel)
            mode = p.lstat()
            if stat.S_ISLNK(mode.st_mode) or getattr(mode, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Linked verification input: ' + rel)
            if p.is_file():
                rows[rel] = {'sha256': digest(p), 'size': mode.st_size,
                             'mode': '100755' if mode.st_mode & 0o111 and os.name != 'nt' else '100644'}
                if python_framework(p)=='pytest':rows[rel]['framework']='pytest'
                native=python_native_inputs(p)
                if native is not None:rows[rel]['native_inputs']=native
    return rows


def copy_files(source, files, destination):
    source, destination = Path(source).resolve(), Path(destination)
    for name, row in files.items():
        relative(name)
        p = source / name
        if p.is_symlink() or not p.resolve().is_relative_to(source) or digest(p) != row['sha256']:
            raise ValueError('Verification input changed: ' + name)
        q = destination / name
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, q)
        if os.name != 'nt':
            q.chmod(0o755 if row.get('mode') == '100755' else 0o644)


def recipe(files, native=(), rebuild=(), scripts=None):
    """Re-derived from ALL source suites, not a hand-picked affected subset."""
    names = set(files)
    core = sorted(n for n in names if n.startswith('live/') and Path(n).name.startswith('test') and n.endswith('.py'))
    py = sorted(n for n in names if n.startswith('desk/tests/') and n.count('/') == 2
                and (Path(n).name.startswith('t_') or Path(n).name.startswith('test')) and n.endswith('.py'))
    node = sorted(n for n in names if n.startswith('desk/app/test/') and n.count('/') == 3 and n.endswith('.mjs'))
    if not core or not py or not node or 'live/praxis_test.py' not in names:
        raise ValueError('Full verification sources missing (core, desk Python, Node or praxis_test)')
    lanes = []
    for root in NPM_PROJECTS:
        inputs = [root + '/package.json', root + '/package-lock.json']
        if not any(name in names for name in inputs):
            continue
        if not all(name in names and files[name].get('sha256') for name in inputs):
            raise ValueError('Incomplete locked npm project (package.json/package-lock.json): ' + root)
        lanes.append({'id': root + '/prepare', 'kind': 'npm-prepare', 'root': root,
                      'sources': inputs, 'input_sha256': {name: files[name]['sha256'] for name in inputs}})
    lanes.append({'id': 'core', 'kind': 'unittest', 'root': 'live', 'pattern': 'test*.py', 'sources': core})
    for name in core:
        if files[name].get('framework')=='pytest':
            lanes.append({'id':'pytest:'+name,'kind':'pytest-file','root':'live','source':name,'sources':[name]})
    scripts = scripts or {}
    if set(scripts) - set(py):
        raise ValueError('Script contract names a missing/noncanonical desk suite')
    for name in py:
        contract = scripts.get(name, {'kind': 'script'} if name == 'desk/tests/t_owner_stop_seed.py' else
                               {'kind':'pytest-file' if files[name].get('framework')=='pytest' else 'unittest-file'})
        if not isinstance(contract, dict) or set(contract) - {'kind', 'argv', 'reason', 'platform_skip'}:
            raise ValueError('Script contract may not replace its source/coverage identity')
        kind = contract.get('kind')
        if kind not in {'script', 'unittest-file', 'pytest-file', 'helper'}:
            raise ValueError('Unsupported declared Python suite entry: ' + name)
        if kind == 'helper' and not str(contract.get('reason', '')).strip():
            raise ValueError('A support-only Python module needs its declared collection role')
        if (not isinstance(contract.get('argv', []), list)
                or any(not isinstance(arg, str) or '\x00' in arg for arg in contract.get('argv', []))):
            raise ValueError('Script argv must be a literal argument vector')
        skips = contract.get('platform_skip', {})
        if (not isinstance(skips, dict) or set(skips) - {'win32', 'linux', 'darwin'}
                or any(not isinstance(reason, str) or not reason.strip() for reason in skips.values())):
            raise ValueError('Platform skips require exact declared applicability reasons')
        lanes.append({'id': name, 'kind': kind, 'source': name, 'sources': [name], **contract})
    lanes.append({'id': 'desk-node', 'kind': 'node', 'sources': node})
    # Only explicit current-release or changed-native obligations. Unchanged
    # supplied binaries retain separate release qualification; no legacy rebuild.
    for root in native:
        if root not in NATIVE:
            raise ValueError('No declared native verification executor for: ' + root)
        lane = {'id': root, 'kind': 'cargo', 'root': root, 'sources': sorted(
            n for n in names if n.startswith(root + '/') and (n.endswith('.rs') or Path(n).name in {'Cargo.toml', 'Cargo.lock'}))}
        lanes.append(lane)
    for root in rebuild:
        if root not in ('desk/app', 'desk/mobile', 'desk/miniapp'):
            raise ValueError('No declared generated UI verification source: ' + root)
        if root + '/prepare' not in {lane['id'] for lane in lanes}:
            raise ValueError('Build requires its locked npm project: ' + root)
        lanes.append({'id': root + '/build', 'kind': 'npm-build', 'root': root,
                      'input_sha256': {name: files[name]['sha256'] for name in
                                       (root + '/package.json', root + '/package-lock.json')},
                      'sources': sorted(n for n in names if n.startswith(root + '/')
                                        and '/dist/' not in n and '/test/' not in n)})
    for lane in lanes:
        native=native_lane_inputs(files,lane['sources'])
        if native:lane['native_inputs']=native
    return lanes


def executor_lock(tools=None, packages=None):
    """Capture an explicitly provisioned native executor; never install packages."""
    supplied = tools or {}
    paths = {name: supplied.get(name) or (sys.executable if name == 'python' else shutil.which(name))
             for name in ('python', 'node', 'cargo', 'npm')}
    return {'platform': sys.platform, 'python_version': sys.version,
            'tools': {name: {'sha256': digest(p)} for name, p in paths.items() if p and Path(p).is_file()},
            'python_packages': dict(sorted((d.metadata['Name'].lower().replace('_', '-'), d.version)
                for d in importlib.metadata.distributions(path=site.getsitepackages())
                if d.metadata['Name'] and (packages is None or d.metadata['Name'].lower().replace('_', '-') in packages)))}


def build(source, destination, version, locks, *, allowed_skips=None, native=(), rebuild=(),
          wheelhouses=None, scripts=None, max_bytes=256 * 1024 * 1024):
    """Build a small source bundle. Locks are per-OS native executor facts.

    Code is composed from the publisher's praxis + helene/core, including tests.
    Never pass an installed profile as source. Output must be a new directory.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or source == destination or destination.is_relative_to(source):
        raise ValueError('Bundle output must be new and outside its source')
    if not str(version).strip() or not locks or any(k not in {'win32', 'linux', 'darwin'} for k in locks):
        raise ValueError('Version and explicit native executor locks are required')
    selected = {}
    for tree in (*TREES, *(n for n in CONTEXT_TREES if (source / n).is_dir())):
        base = source / tree
        for name, row in inventory(base).items():
            parts = Path(name).parts
            if (parts[0] in {'runtime', 'models'} or Path(name).name in PRIVATE or Path(name).name.endswith(('.pyc', '.session', '.log'))
                    or (tree != 'desk' and any(p in {'memory', 'soul', 'workspace', 'data', 'AGENTS.md'} for p in parts))):
                continue
            selected[tree + '/' + name] = row
    for name in CONTEXT_FILES:
        if (source / name).is_file():
            selected[name] = {'sha256': digest(source / name), 'size': (source / name).stat().st_size, 'mode': '100644'}
    total = sum(r['size'] for r in selected.values())
    if total > max_bytes:
        raise ValueError('Verification sources exceed declared small-bundle byte budget: ' + str(total))
    destination.mkdir(parents=True)
    repo = destination / 'repository'
    copy_files(source, selected, repo)
    live = repo / 'live'
    core = {}
    for tree in ('praxis', 'helene/core'):
        for name, row in inventory(repo / tree).items():
            # Edition overlay wins, matching the product's composition rule.
            core[name] = (repo / tree, row)
    for name, (base, row) in core.items():
        copy_files(base, {name: row}, live)
    helpers = destination / 'runner'
    helpers.mkdir()
    for name in RUNNERS:
        shutil.copyfile(Path(__file__).with_name(name), helpers / name)
    dependencies = {}
    for platform, wheelhouse in (wheelhouses or {}).items():
        if platform not in locks:
            raise ValueError('Wheelhouse has no matching provisioned native lock')
        wheelhouse = Path(wheelhouse)
        metadata = json.loads((wheelhouse / 'wheels.json').read_text(encoding='utf8'))
        packages = metadata.get('packages', {})
        if not packages:
            raise ValueError('Verification wheelhouse has no pinned dependency closure')
        requirements = []
        wheels = {}
        for package, row in sorted(packages.items()):
            name = row['filename']
            if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', package)
                    or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', row['version'])
                    or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])
                    or Path(name).name != name or not name.endswith('.whl') or digest(wheelhouse / name) != row['sha256']):
                raise ValueError('Verification dependency wheel changed: ' + name)
            if locks[platform]['python_packages'].get(package.lower().replace('_', '-')) != row['version']:
                raise ValueError('Wheel version differs from the executor dependency lock: ' + package)
            requirements.append(package + '==' + row['version'] + ' --hash=sha256:' + row['sha256'])
            wheels[name] = {'sha256': row['sha256'], 'size': (wheelhouse / name).stat().st_size, 'mode': '100644'}
        if total + sum(r['size'] for r in wheels.values()) > max_bytes:
            raise ValueError('Verification material plus wheels exceeds its byte budget; supply an explicit executor instead')
        total += sum(r['size'] for r in wheels.values())
        target = destination / 'dependencies' / platform
        copy_files(wheelhouse, wheels, target)
        (target / 'requirements.txt').write_text('\n'.join(requirements) + '\n', encoding='utf8')
        dependencies[platform] = {'files': inventory(target), 'packages': packages}
    files = inventory(repo)
    total = sum(row['size'] for row in files.values()) + sum(
        row['size'] for dep in dependencies.values() for row in dep['files'].values())
    if total > max_bytes:
        raise ValueError('Complete composed verification material exceeds its byte budget')
    manifest = {'format': FORMAT, 'version': str(version), 'files': files,
                'runners': inventory(helpers), 'lanes': recipe(files, native, rebuild, scripts),
                'native_lanes': list(native), 'build_lanes': list(rebuild),
                'script_contracts': scripts or {},
                'dependencies': dependencies,
                'executor_locks': locks, 'allowed_test_skips': allowed_skips or {},
                'reuse': 'only-identical-whole-composition; no lane reuse'}
    (destination / 'manifest.json').write_bytes(encoded(manifest))
    return {'root': str(destination), 'sha256': digest(destination / 'manifest.json'),
            'version': str(version), 'source_bytes': total, 'lanes': len(manifest['lanes'])}


def load(root, expected, version):
    root = Path(root).resolve()
    if root.is_symlink() or digest(root / 'manifest.json') != expected:
        raise ValueError('Verification bundle manifest identity changed')
    value = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
    if value.get('format') != FORMAT or value.get('version') != str(version):
        raise ValueError('Verification bundle belongs to another version')
    if inventory(root / 'repository') != value.get('files') or inventory(root / 'runner') != value.get('runners'):
        raise ValueError('Verification bundle source/runner changed')
    if recipe(value['files'], value.get('native_lanes', []), value.get('build_lanes', []), value.get('script_contracts',{})) != value.get('lanes'):
        raise ValueError('Verification bundle omits a full discovery lane')
    for platform, row in value.get('dependencies', {}).items():
        if platform not in {'win32', 'linux', 'darwin'} or inventory(root / 'dependencies' / platform) != row['files']:
            raise ValueError('Verification dependency wheel material changed')
    for platform,row in value.get('bundled_executors',{}).items():
        if platform not in value.get('executor_locks',{}):raise ValueError('Bundled tools have no native lock')
        from .source_verification_executor import declaration
        declaration(row)
    return value


def provision(root, expected, version, python, target, stopped=lambda: False):
    """Explicit offline acquisition into a new private target, not global Python.

    Publisher may include hash-pinned wheels (including pip when the embedded
    runtime lacks it). No network, model, global pip or copied runtime is used.
    A failed/cancelled install remains as evidence; this never admits apply.
    """
    from . import source_update_battery
    value = load(root, expected, version)
    declared = value.get('dependencies', {}).get(sys.platform)
    if not declared:
        raise ValueError('No offline verification dependency closure for this native platform; select a provisioned executor')
    if digest(python) != value['executor_locks'][sys.platform]['tools'].get('python', {}).get('sha256'):
        raise ValueError('Dependency installer Python differs from the provisioned lock')
    target = Path(target).resolve()
    if target.exists():
        raise ValueError('Dependency target must be new; keep previous attempts separately')
    target.mkdir(parents=True)
    home = target / '.installer-home'
    env = source_update_battery._environment(home, Path(root) / 'repository/live', Path(root) / 'repository/desk')
    wheelhouse = Path(root).resolve() / 'dependencies' / sys.platform
    pip = [str(wheelhouse / n) for n in declared['files'] if n.startswith('pip-') and n.endswith('.whl')]
    env['PYTHONPATH'] = os.pathsep.join(pip)
    env['PIP_CONFIG_FILE'] = os.devnull
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    command = [str(python), '-B', '-s', '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-compile',
               '--require-hashes', '--find-links', str(wheelhouse), '--target', str(target),
               '-r', str(wheelhouse / 'requirements.txt')]
    log = target.with_name(target.name + '.install.log')
    result = source_update_battery._execute(command, wheelhouse, env, log, stopped)
    receipt = {'format': 'helene-verification-dependencies.v1', 'bundle_sha256': expected,
               'platform': sys.platform, 'python_sha256': digest(python), 'result': result,
               'log_sha256': digest(log), 'files': inventory(target)}
    receipt_path = target.with_name(target.name + '.receipt.json')
    receipt_path.write_bytes(encoded(receipt))
    if result['status'] != 'passed':
        raise ValueError('Verification dependency provisioning failed; retained log: ' + str(log))
    return {'python_dependencies': str(target), 'dependency_receipt': str(receipt_path),
            'dependency_receipt_sha256': digest(receipt_path)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    capture = sub.add_parser('capture-executor')
    capture.add_argument('--output', type=Path, required=True)
    capture.add_argument('--packages', type=Path, help='JSON list of provisioned verification dependency names')
    create = sub.add_parser('build')
    for key in ('source', 'output', 'locks'):
        create.add_argument('--' + key, type=Path, required=True)
    create.add_argument('--version', required=True)
    create.add_argument('--allowed-skips', type=Path)
    create.add_argument('--native-lanes', type=Path, help='JSON list of current-release native consumer source roots')
    create.add_argument('--build-lanes', type=Path, help='JSON list of current-release generated UI source roots')
    create.add_argument('--wheelhouses', type=Path, help='JSON platform -> hash-pinned offline wheelhouse path')
    create.add_argument('--script-contracts', type=Path, help='JSON exact suite path -> supported script argv/helper collection role')
    install = sub.add_parser('provision')
    for key in ('bundle', 'python', 'output'):
        install.add_argument('--' + key, type=Path, required=True)
    install.add_argument('--sha256', required=True)
    install.add_argument('--version', required=True)
    a = p.parse_args()
    if a.command == 'capture-executor':
        packages = json.loads(a.packages.read_text(encoding='utf8')) if a.packages else None
        a.output.write_bytes(encoded(executor_lock(packages=packages)))
        print('EXECUTOR_LOCK_WRITTEN')
    elif a.command == 'build':
        locks = json.loads(a.locks.read_text(encoding='utf8'))
        skips = json.loads(a.allowed_skips.read_text(encoding='utf8')) if a.allowed_skips else None
        native = json.loads(a.native_lanes.read_text(encoding='utf8')) if a.native_lanes else []
        rebuild = json.loads(a.build_lanes.read_text(encoding='utf8')) if a.build_lanes else []
        wheels = json.loads(a.wheelhouses.read_text(encoding='utf8')) if a.wheelhouses else None
        scripts = json.loads(a.script_contracts.read_text(encoding='utf8')) if a.script_contracts else None
        print(json.dumps(build(a.source, a.output, a.version, locks, allowed_skips=skips, native=native, rebuild=rebuild, wheelhouses=wheels, scripts=scripts)))
    else:
        print(json.dumps(provision(a.bundle, a.sha256, a.version, a.python, a.output)))


if __name__ == '__main__':
    main()

```


## Canonical verification of retained archive bytes

The historical records above are accompanied by the actual original archives
in the version-bound verification bundle, under `verification/evidence-inputs`.
They are input data, separate from the source copies used by parallel test
workers. No historical program or binary is executed during this verification.

When these inputs are declared, `source_verification_bundle.load` validates their
complete inventory against the incoming bundle manifest. Before running its
normal full test recipe, `source_update_battery.prepare` invokes
`localharness.retained_artifacts` on those bound inputs and the actual composed
verification source. The checker reads the archives itself; it does not accept
the `members_exact` or success flags in an authored explanation as evidence.

The generated `battery/retained-artifact-verification.md` records:

- Original archive SHA256 values compared with the retained provider metadata,
  CRC verification, and unchanged archive bytes across member inspection.
- Exact archive-member equality with the retained protocols in the composed
  source, including the complete historical manifests.
- The nested Darwin candidate and Python executable identities, and the native
  body/bridge output identities, compared with their original archived receipts.
- Historical production/test hashes compared with composed verification source,
  the complete native source-file map, and original invocation source compared
  with its archived manifest or independently retrieved Git blob identity.

The proof's `documents` entries point to exact original CI job records, logs and
invocation sources frozen as `battery/retained-invocation-*.md`. In particular,
the native producer records the cargo test/build commands, unchanged source
inventory, and checked signing commands before hashing the archived organs.
The Darwin producer records the interpreter handoff and exact test invocation.

This proof is emitted by the current verification process and hash-bound in its
immutable full-battery receipt. A changed archive, retained member, source,
interpreter, binary, invocation file or declared input fails verification.
All prior raw records remain byte-for-byte intact, including the older partial
retrieval record; they have not been rewritten as later executions.

This establishes the recorded historical artifact/member/source connections.
It is not a new Darwin execution, hardware attestation, current macOS package
acceptance, or installed-agent acceptance. The ordinary complete applicable
battery, independent source review, authorized switch and live tool checks
remain separate requirements.


## RC41 exclusive-create correction and interface refresh

RC40's complete canonical receipt contains one error in the concurrent same-key
binding test. During lock creation Windows resolved the disappearing final file
to its internal deleted-file namespace. The verifier then refused the resolved
path as outside the isolated directory. Original failed receipt and raw output
are retained; they have not been reclassified as passed.

The new write-fence rule applies entry semantics only when BOTH O_CREAT and
O_EXCL are present. Such an open must fail if the final entry already exists,
including a symlink. Every parent is still resolved and checked. Ordinary
opens, writes, truncation, descriptor rules, test inventory and acceptance
criteria are unchanged. t_verification_exclusive_0311.py runs actual opens in
fresh processes: a deterministic resolver fixture reproduces the observed
deleted-leaf condition, while existing files and an external symlink are not
opened/truncated. External parents, traversal, and non-exclusive opens refuse.
The deleted-leaf case fails with RC40's runner and passes with this correction.
This focused comparison does not replace the full M gate or independent review.

The shared interface now has visual appearance presets, grouped theme/text/
palette/detail controls, keyboard navigation and an appearance reset. Initial
window setup no longer overwrites the saved theme with system mode. Palette
selection clears its corresponding custom override. Existing preference
ownership and scoped reset are retained. Appearance styling is imported with
the shared component for both desktop and phone.

Onboarding now starts with a compact introduction, concrete first steps and a
map to actual sections. Draft templates still require the owner's input and
are not sent automatically. Telegram setup explicitly says Save then Restart
now. Context/cache explanations no longer promise exact provider input or
unmeasured cache discounts. Section headings, settings navigation and empty
states share a quieter visual hierarchy.

Observed UI acceptance used the built product in an isolated localhost fixture
with disposable Chrome profiles and synthetic records, without owner data or
model calls. Desktop checks opened all ten sections and all onboarding links,
exercised six presets, Arrow/Home/End navigation, theme persistence on reload,
palette/custom-color replacement, reset, 70/100/150 percent scale, 760/1024/1440
pixel windows and light/dark themes. No page errors or submitted messages were
observed. The actual mobile build at 390 pixels rendered the shared appearance
card without horizontal overflow; preset and dark-theme changes worked. The
primary agent inspected screenshots. This does not claim physical-device,
native Mac, public-release or actual owner-installation acceptance.

## Update acceptance policy: test evidence informs independent review

The owner clarified on 2026-11-03 that tests must not be blocking because
authored mutations can be substantial. This supersedes the earlier requirement
for a completely green test battery before independent review.

The full version-bound attempt and its original outcomes remain recorded.
Failed assertions, errors, skipped or unavailable checks and execution limits
are handed to the independent reviewers. They assess the intended authored and
upstream behavior, source changes, diagnostics and gaps together. An obsolete
assertion does not require reverting an intentional mutation. A real regression
can still produce review findings. There is no automatic source-repair loop
before the reviewers have made that assessment.

Canonical receipt/composition identity, untampered evidence, owner Stop/Pause,
and clear independent review remain enforced. A non-passing report stays
non-passing even when reviewers accept the change. Current installation health
and native-tool acceptance remain separate observations.

The earlier RC41 full attempt had three non-passing tests and was stopped before
any independent review. Its original failed receipt and diagnostics are retained
unchanged. This policy correction does not retroactively approve that attempt
or claim that those tests passed.

## Historical evidence and later source mutations

The retained original archives, logs, provider digests, archive members and
invocation sources remain authenticated and unchanged. Current source hashes
are observations, not constraints that force the candidate back to an old
version. The computed v2 report records both hashes, explicit source removal,
per-archive matching state and a complete list of differences. Historical
execution does not qualify changed or removed sources. Such differences go to
the current full test attempt and independent review; they do not prevent that
attempt from starting. Current platform and installed-runtime acceptance are
never inferred from these retained records. No new macOS execution is claimed.
