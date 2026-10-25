# 1009 Rust validation of f349dc1a

Isolated proposal `3013f577`, checkpoint
`f349dc1a0208c236e094486aaa089ffb76abf861`. Read all four existing
`1009-*.md` reviews and inspected the existing task-local toolchain/snapshot first.
The old snapshot was **not** the validation input: all commands below use the actual
`body/Cargo.toml` and current committed sources. No production source edits, dependency
changes, deployment, merge, Windows desktop actions, live binary replacement, KEAT,
or 1109 work. This note is the only tracked change.

## Results

| Gate | Result |
|---|---|
| Windows GNU production typecheck, praxis-body | **PASS**, exit 0 |
| Windows GNU production typecheck, praxis-bridge | **PASS**, exit 0 |
| Windows GNU test-target typecheck, both crates | **PASS**, exit 0 |
| Linux unit execution, praxis-body | **103 passed, 4 ignored**, exit 0 |
| Linux unit execution, praxis-bridge | **11 passed**, exit 0 |
| Linux Clippy `-D warnings`, both crates | **FAIL**, body has 39 diagnostics |
| Windows GNU Clippy `-D warnings`, both crates | **FAIL**, body has 6 diagnostics |
| Windows linking/execution | **NOT RUN** |
| Real COM/provider fault injection | **NOT RUN** |

The missing host `cc` was infrastructure, not a project failure. It is now resolved
locally. Real Windows-cfg Rust code, including the raw COM vtable calls and SetValue
binding, typechecks with the locked windows 0.62.2 dependency. This is not a Windows
executable/link receipt and does not establish runtime ABI/provider behavior.

Linux tests execute the pure selector/receipt and HRESULT/null/property decision
helpers, the repaired `the_module_declares_read_window_and_element_act` regression,
and non-Windows stub behavior. All three new action-provider *pure helper* tests pass.
Bridge tests execute HTTP admission/cache conflict and persistent spool binding
regressions, not merely Python simulations. No provider/COM object was invoked.
The four ignored body tests require desktop/window facilities and were not enabled.

Clippy is now installed task-locally, so these failures are real lint results rather
than missing-tool failures. Windows diagnostics: `element.rs:143,148` collapsible-if;
`fsops.rs:493` permissions_set_readonly_false; `uia.rs:494,781` collapsible-if and
`uia.rs:675` obfuscated-if-else. Linux additionally reports non-Windows dead-code
warnings in element/UIA helpers. These were not suppressed or fixed under this
validation-only authorization. No baseline lint comparison was performed, so not
all diagnostics are attributed to this proposal. Separate bridge lint success is
not claimed from the combined failed invocations.

## Local provisioning

No `cc`, GCC, Clang, ld, cargo, curl, wget or dpkg-deb was initially on PATH.
Existing Rust 1.94.0 and Windows GNU std were under `workspace/.rust-validation`;
Rust ships rust-lld, but the workspace also builds bundled SQLite and AWS-LC C
sources. A linker alone would not suffice. Downloaded/extracted Zig 0.13.0 locally
for its self-contained C compiler, linker, libc and cross headers (47,082,308 bytes).
No global package install or system changes. Download/extraction used Python
`urllib.request`, `tarfile` and SHA256 verification.

- Zig: `https://ziglang.org/download/0.13.0/zig-linux-x86_64-0.13.0.tar.xz`
  SHA256 `d45312e61ebcc48032b77bc4cf7fd6915c11fa16e4aad116b66c9468211230ea`.
- Debian package index: `https://deb.debian.org/debian/dists/trixie/main/binary-amd64/Packages.xz`.
  Package downloads were verified against its SHA256 fields, and their ar data
  archives extracted using Python (no maintainer scripts executed).
- `https://deb.debian.org/debian/pool/main/m/mingw-w64/mingw-w64-x86-64-dev_12.0.0-5_all.deb`
  SHA256 `0bf89cf7454cccb49cd2a46a6f7b33896f6ae3a1a2fe33e02c40be6155bbb385`.
- `https://deb.debian.org/debian/pool/main/m/mingw-w64/mingw-w64-common_12.0.0-5_all.deb`
  SHA256 `97dce5d0d8aff1cade4786ad4ef5078345477a598829afcac2e7f2fbc5399990`.

First Windows body attempt exposed cc-rs passing Rust's vendor-bearing target to
Zig; wrapper translates only target spelling, preserving OS/architecture/ABI.
Second exposed Zig's missing `sched.h` for AWS-LC jitterentropy. Added authentic
MinGW `sched.h`, `pthread.h`, `semaphore.h` copied byte-for-byte from the common
package into a dedicated include directory. No entropy disabling, dependency
patching, generated-binding substitution, or source-code workaround. Initial dev
package contains symlinks into common, so both were extracted. Final check passed.

Provisioning receipts under `/app/memory/.forge/tasks/code-63bc65ae/runs/`:
`20260910-232611-9f1d.log` (Zig download/hash/tool versions),
`20260910-232709-8c9a.log` (target wrapper),
`20260910-232922-3b4a.log`, `20260910-232954-b384.log` (MinGW packages),
`20260910-233006-8717.log` (header selection). Full package metadata and archives
remain task-local. Existing stale snapshot is unchanged.

## Exact validation commands and logs

Working directory `/app/.proposals/3013f577`; source environment below before each
command. Full stdout/stderr logs are under `workspace/.rust-validation/logs/`, with
`EXIT=<status>` appended. Commands actually run:

```sh
. workspace/.rust-validation/env.sh
cargo check --locked --manifest-path body/Cargo.toml -p praxis-body --target x86_64-pc-windows-gnu
# windows-body.log: initial target-spelling failure
# windows-body-final.log: missing sched.h failure
# windows-body-headers.log: final PASS after authentic headers
cargo check --locked --manifest-path body/Cargo.toml -p praxis-bridge --target x86_64-pc-windows-gnu
# windows-bridge.log: PASS
cargo test --locked --manifest-path body/Cargo.toml -p praxis-body -p praxis-bridge
# linux-tests.log: PASS
rustup component add clippy
# clippy-install.log: installed in task-local RUSTUP_HOME
cargo clippy --locked --manifest-path body/Cargo.toml -p praxis-body -p praxis-bridge -- -D warnings
# linux-clippy.log: exit 101
cargo check --locked --manifest-path body/Cargo.toml -p praxis-body -p praxis-bridge --tests --target x86_64-pc-windows-gnu
# windows-tests-check.log: PASS
cargo clippy --locked --manifest-path body/Cargo.toml -p praxis-body -p praxis-bridge --target x86_64-pc-windows-gnu -- -D warnings
# windows-clippy.log: exit 101
```

Final environment (absolute paths intentionally identify this proposal):

```sh
export RUSTUP_HOME=/app/.proposals/3013f577/workspace/.rust-validation/rustup
export CARGO_HOME=/app/.proposals/3013f577/workspace/.rust-validation/cargo
export PATH=/app/.proposals/3013f577/workspace/.rust-validation/bin:/app/.proposals/3013f577/workspace/.rust-validation/cargo/bin:$PATH
export CARGO_TARGET_DIR=/app/.proposals/3013f577/workspace/.rust-validation/target-current
export CC=/app/.proposals/3013f577/workspace/.rust-validation/bin/cc
export AR=/app/.proposals/3013f577/workspace/.rust-validation/bin/ar
export CC_x86_64_pc_windows_gnu=/app/.proposals/3013f577/workspace/.rust-validation/bin/windows-cc
export AR_x86_64_pc_windows_gnu=/app/.proposals/3013f577/workspace/.rust-validation/bin/ar
export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=/app/.proposals/3013f577/workspace/.rust-validation/bin/cc
export CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=/app/.proposals/3013f577/workspace/.rust-validation/bin/windows-cc
export ZIG_GLOBAL_CACHE_DIR=/app/.proposals/3013f577/workspace/.rust-validation/zig-cache
export ZIG_LOCAL_CACHE_DIR=/app/.proposals/3013f577/workspace/.rust-validation/zig-local-cache
export CARGO_BUILD_JOBS=2
export CFLAGS_x86_64_pc_windows_gnu="-I/app/.proposals/3013f577/workspace/.rust-validation/winpthread-headers"
```

`bin/cc` wrapper (windows-cc is identical except default target is
`x86_64-windows-gnu`):

```python
#!/usr/local/bin/python
import os,sys
args=[a.replace('--target=x86_64-pc-windows-gnu','--target=x86_64-windows-gnu').replace('--target=x86_64-unknown-linux-gnu','--target=x86_64-linux-gnu') for a in sys.argv[1:]]
os.execv('/app/.proposals/3013f577/workspace/.rust-validation/zig-linux-x86_64-0.13.0/zig',['zig','cc','-target','x86_64-linux-gnu']+args)
```

`bin/ar` executes `zig ar "$@"`. Cache, tools, downloaded packages and all build
outputs are task-local/ignored, not part of the proposed diff. Linux tests began
before Windows CFLAGS were added; that target-specific variable does not affect
Linux compilation.

Results also recorded in task receipts `20260910-233103-3a83.log` (Linux tests),
`20260910-233401-4643.log` (Windows body), `20260910-233505-5f27.log`
(Windows test-target check/Linux Clippy), `20260910-233553-0b78.log`
(Windows Clippy), and process full logs for windows-bridge.

## Integrity and remaining gates

`current-manifest.json` records SHA256 for all 32 current Rust source/manifest/lock
files. Rechecked all 32 byte-identical after validation. `body/Cargo.lock` SHA256
remains `966d0c77d8b0373ab2480da836885c179f131ea437991071a7660f885c42dbff`.
Integrity receipt `20260910-233520-4a4a.log`; `git diff --check` passes.

Next: Praxis review of isolated checkpoint and this evidence, then separately
authorized lint repairs/baseline triage. Native Windows linking and safe unit
execution remain open, as do synthetic real COM/provider fault-injection gates
from `1009-uia-repair.md` (failed HRESULT, successful null, required property read
failure and no mutation, COM lifetimes). No live desktop test is authorized by this
validation. The full Python pre-merge gate was not repeated here. These results
close the missing-tool and Windows typecheck gaps, **not** all acceptance gates;
there is no merge/deployment recommendation based solely on them.

### Full-log fingerprints

```text
08cc3e5ddf8fdac43e355fa0e3215ff51d62897f38c2b9748679f1984a32d562  clippy-install.log
9e3d76786a5eb7375514678a3c91468bbda7d1f91366278eee11aaab249a79bf  linux-clippy.log
0a34fc8ab7037820b805f80a2e6737862af47b53ae8d22e828caf832caa8bb55  linux-tests.log
6c5ebb98e1d533ac49553afe7ba84919664026dc90ac7c8bae340cb8211a894d  windows-body-final.log
712da7d43e07ef1b7633f86d88717b14060e2ef1fbeb99c68cc635a591d2df0f  windows-body-headers.log
33fece45b5a886d5bdf46c9595c2e94428788dfde4a9ba31b781015ba5341d55  windows-body.log
bf3bdf33679d591a4b9448ed8ded083b53ad03d47a7b6b969c3393a10ac8f570  windows-bridge.log
bb78d9530d5371339c754fb81dae6fea3ad3271f801c9383e36136faaaa1155b  windows-clippy.log
73c0bacbca042e1b3c2af65041a892571795ded971bcb44ba6f5931b367e77cb  windows-tests-check.log
```

## Post-fix appendix (Praxis, 11.09 00:0x)

Owner re-review of the final tree applied five mechanical clippy fixes in the NEW
code (3× collapsible-if in element.rs:143,148/uia.rs:494,781; obfuscated-if-else
uia.rs:675; let-chain uia.rs children_of). Receipts: runs 20260910-235354-4ac0.log
(Linux clippy post-fix: only baseline dead-code/fsops remain),
20260910-235702-c73d.log (Windows GNU clippy post-fix: the ONLY error left is the
pre-existing fsops.rs:493 permissions_set_readonly_false, which this commit does
not touch — file unchanged since before HEAD), 20260911-000026-086a.log
(baseline comparison: fsops not in commit), 20260911-000322-faf0.log (frame_trace
65/65 after rails.md judge-address refresh 15553→15623, single-line surgical edit,
not a full re-render: full render would bake sandbox witness values into prod
manifest). Windows clippy delta introduced by this proposal: 0 diagnostics.
