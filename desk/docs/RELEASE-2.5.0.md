# Hélène 2.5.0

Download the archive for your system, extract it completely and open `helene.exe`,
`Helene.app` or `./helene`. Python and the application runtime are included.
The release provides Windows x64, Apple-silicon macOS, Debian/Ubuntu x86_64 and
Fedora x86_64 archives.

## Voice delivery owned by the application

The agent calls `speak` once and receives an acceptance receipt. The application
owns synthesis and delivery from that point. Audio can arrive while the agent
continues another task or after its turn has finished. The agent needs no
polling, generated-audio inspection, transcription or second media-send call.

Fish, local XTTS on CPU and XTTS on CUDA use this asynchronous contract. Each
job retains its original run and recipient binding. Stop, pause and cancellation
are enforced at the application's delivery boundary. Ambiguous paid requests
retain their identity instead of triggering an automatic duplicate synthesis.

## Recording, playback and warm recognition

Releasing the recording hotkey submits the recording in both the foreground
and the background. Explicit pause retains the optional preview workflow.
Voice history uses compact replay controls. Playback can start while optional
waveform code and visual data are still loading.

Whisper uses the CPU capacity available to its process. Its unchanged warm model
is reused and remains resident for thirty idle minutes after use. The warmup
setting requests loading at application startup. The idle lifetime applies
after recognition independently of that startup preference. Windows inbox
notifications also remove the previous idle polling delay before a turn starts.

## Codex Fast mode and model-aware controls

Desktop, phone and Telegram Mini App settings expose provider-default, ordinary
and Fast processing choices for the Codex subscription route. Model catalog
metadata determines whether Fast is offered and which reasoning levels are
available. The interface includes advertised supported levels up to ultra.
Codex offers the model's reasoning default and omits the no-reasoning option.
Older saved `none` settings remain readable as the effective model default.

Fast mode remains independent of reasoning effort. A saved model change to an
unconfirmed Fast route clears the incompatible premium request at Save time.
Changing between eligible models retains it. Unrelated edits preserve legacy
values until their owner changes them.

The bundled standalone Praxis Relay forwards `service_tier` through the shared
desktop/headless service. Requested and actual processing tiers are separate
receipts. The interface labels the last observed response tier, including an
ordinary response to a Fast request. Provider availability, account entitlement
and higher subscription or credit consumption still apply. The owner's live
Windows acceptance route returned `default` for its `priority` requests.

## Clean source replacement

Clean updates use completed extraction, a restart into the selected new folder
and retirement of the exact old program. They omit program inventories,
per-file comparisons and the former trial period. Agent data, relay login and
compatible voice runtimes and models use their retained storage independently
of the program folder.

New clean updates stage in an independent program folder. A one-time POSIX
2.4 compatibility handoff copies the completed incoming program into a sibling
folder and keeps the original helper alive until the new helper accepts
custody. Agent data and retained voice/model stores follow their prepared
bindings.

The explicit preserve-edits choice continues to reconcile authored changes
through its existing tests and review workflow. Graceful Windows handover
recognizes both direct Python application launches and the clickable binary's
`windows_start.py` entry. Successful model closures also retain a successful
header when their trace contains an explanatory stop reason.

## Forge execution and preservation checks

Foreground Forge commands acquire durable intent and process identity before execution is
admitted. Stopping a selected worker follows its owned process and
verification jobs while preserving independent task jobs. Unconfirmed
termination retains custody. Finish checks Pause and writer custody before
surveying or integrating changes.

The Forge executor owns provider retries. An ambiguous call retains its
partial text, usage and provider identity. Another worker attempt requires
explicit evidence that the previous request was never admitted. Ordinary
conversation and voice retain their existing retry policies.

On Linux, validated reboot evidence can release an extinct execution
boundary while preserving its historical result and Stop records. The
desktop observer uses the same validator when deriving worker activity.

## Acceptance and evidence

The release's source, frontend and package manifests bind the shipped files.
Release evidence records native platform windows, installed runtime and
process-lifecycle checks, backend regressions, compiled browser scenarios and
independent reviews.

A live Windows owner session exercised a local chat reply, a read-only Computer
Use screenshot and one Fish synthesis job. The voice arrived automatically
about 3.3 seconds after acceptance and completed playback in the native window.
Controlled tests also hold synthesis until after the original turn closes,
then verify automatic delivery to the original recipient without audio review.

Physical microphone acoustics, CUDA hardware performance and provider Fast
entitlement have separate acceptance boundaries. The evidence distinguishes
controlled transport checks, native application execution and live provider
observations.

The inherited core battery retains pre-existing desktop/server contract
failures and undeclared legacy skips. Complete desktop and native platform
qualification are recorded separately. On POSIX, background commands and
local verification use process-group containment. The macOS foreground gate
also retains that containment after admission. A command that deliberately
starts a new session can outlive its group.
