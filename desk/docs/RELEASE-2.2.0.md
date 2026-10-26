# Hélène 2.2 — teams that can finish the job

Hélène 2.2 makes autonomous engineering work easier to coordinate, inspect and recover. Forge can run a team without confusing a worker's finished output with an accepted result; the desktop shows what that team is doing; and source updates resolve conflicts in a separate workspace instead of turning them into an ordinary conversation with the agent.

The application remains **portable and Python-owned**. Its window, agent lifecycle and update coordination run from the application folder. Your agent's memory, configuration, authored code and Git history remain separate from the release's clean source baseline.

## A team is more than a collection of workers

Forge now keeps queued work, running workers, results and coordinator decisions distinct. A dependent task waits for acceptance of the exact result it depends on. Rework and continuation create linked work units rather than erasing the previous attempt.

- **Roles have real boundaries.** Reading, editing, reviewing and verification do not all receive the same tool access. Editing workers sharing one source directory take turns; readers can run in parallel.
- **Progress survives the conversation.** Workers retain checkpoints, ordered tool calls, complete outputs and references to larger payloads. An interrupted effect is recorded as unknown, not silently repeated.
- **Control stays with the coordinator and the person.** Task and group controls remain separate from ordinary chat. Pause and Stop fence new work; a late result is not permission to restart a stopped task.

The **Plans / Forge panel** exposes the recorded task tree, participant details, steps, results and files. It reads the engineering records directly: opening it does not wake the main model or invent progress percentages.

## Source conflicts get their own workspace and review

When an update meets authored changes, Hélène retains four explicit states: **A**, the previous official baseline; **L**, the local authored code; **B**, the incoming clean release; and **M**, the proposed merge.

A restricted merger works on the isolated candidate. Independent reviewers inspect the exact inputs and result before that candidate can be applied. A syntax check alone is not approval. Changed inputs invalidate the old preparation, and missing review capability or unresolved material leaves the existing program in place. This replaces the old path that asked the ordinary agent turn to resolve an update conflict.

**Transition note:** the first update from 2.1.1 still starts in the old updater. The new conflict workflow becomes available once 2.2 is running. If preserved authored agent code lacks the required Forge adapter, Hélène reports that incompatibility rather than overwriting it or pretending the operation succeeded.

## Less copying, visible updates, intact recovery

Routine updates prepare essential settings and full program/code preimages without duplicating unchanged memory or voice models. Verified extraction results can be reused, but file changes invalidate that reuse. Ordinary preparation happens while the old window remains open; a small progress window follows the recorded update after handoff.

Recovery still keeps the previous program and selected authored code available if the replacement fails to start. This release also fixes Windows progress-file sharing during atomic publication and cleanup of owned staging folders containing read-only Git objects. Full profile backups remain a separate operation—not something every routine update should reproduce.

This is not a claim that every update is faster or visually uninterrupted. The measured first legacy transition remained slow; a later prepared update was substantially shorter in the tested case, and the rollback test retained a brief presentation gap.

## Voice and startup

XTTS synthesis is owned by the application, with a narrow speech adapter for compatible older authored code. WAV preview and Telegram Ogg/Opus voice-note output are distinct paths; codec support is checked instead of requiring another large PyAV download.

Configured profiles now open **Now** even with empty browser storage or an unavailable room/channel service. First-run setup and manually opened onboarding remain available.

**Whisper and XTTS remain optional.** Existing, hash-pinned component downloads are reused from **2.1.1**; that release is deliberately retained. Models and runtimes have not been rebuilt or uploaded again. The subscription relay remains a separate distribution. XTTS model weights retain their **CPML non-commercial terms**. CPU synthesis is memory-intensive and is not advertised as real-time conversation.

## Download and launch

Download the archive for your system from the assets below, extract the **whole `Helene` folder** into a permanent writable location, and keep it together.

| System | Application archive | Entry after extraction |
| --- | --- | --- |
| Windows x64 | `Helene-2.2.0.zip` | `Start Helene.vbs`; `Migrate Helene.vbs` for an existing installation |
| macOS Apple Silicon | `Helene-2.2.0-macos-arm64.zip` | `Helene.app` or `Migrate Helene.app` |
| Debian x86_64 | `Helene-2.2.0-linux-x86_64.zip` | `Start Helene.sh` — use the file manager's **Run as a program** action |
| Fedora x86_64 | `Helene-2.2.0-fedora-x86_64.zip` | `Start Helene.sh` — use the file manager's **Run as a program** action |

For an existing portable installation, use **Settings → Program → Update**. Do not extract a new archive over a running application. Back up irreplaceable authored files before migration. Operating-system trust and permission prompts remain yours to approve. There is no new setup EXE, DEB or RPM in this release.

Each archive has a `.sha256` sidecar. `SHA256SUMS`, `voice-components.json` and `release-evidence.json` identify the delivery, optional component locations and source bindings. The application ZIPs preserve the exact previously tested candidate bytes; their construction passports are historical provenance, not a fresh claim about publication or installation.

## What was checked

Packaged Windows, Debian and Fedora update/recovery tests exercised real processes and windows, including a deliberately broken next version, with synthetic memory, configuration, Git state and authored edits preserved. The final startup UI change was checked separately on those packages.

On standard **macOS 15.7.9 ARM64 GitHub Actions**, the exact final Mac packet passed **2.1.1 → 2.2 → broken next launch → rollback to 2.2**, preserving the synthetic profile, Git changes and Stop state. Native windows and the configured **Now** screen were inspected. [Mac run and evidence](https://github.com/josephsteuerjr/helene-mac-migration-2410/actions/runs/40311179966).

The engineering-team tests used real worker processes with deterministic model responses—not a completed live-model autonomous coding campaign. Windows XTTS produced real WAV and Opus audio; Telegram delivery used test transports. Earlier independent code reviews found real defects; the corrected final source was accepted by the author under Advisory, **not given a new whole-source panel PASS**.

There is no claim of full QA across all providers, authenticated accounts, physical microphones or phones, sign-in behavior, Wayland, macOS permission/Gatekeeper configurations, or arbitrary large profiles. These are bounded release checks, not universal compatibility guarantees. Publishing this release does not update a running user's agent or deploy the live Praxis service.
