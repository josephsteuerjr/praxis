# Hélène 2.5.1

Download the archive for your system, extract it completely and open `helene.exe`,
`Helene.app` or `./helene`. Python and the application runtime are included.

## Automatic prepared-update handoff

Opening a new program folder that already contains a prepared clean update
now resumes the handoff before the application claims its data. It requests
a graceful exit from the selected previous installation, waits for its
processes and data locks to be released, completes pending data moves and
opens the new window.

The Windows bootstrap allows the bounded handoff to finish while it waits for
the real window's readiness acknowledgement.

Settings are refreshed from the selected old installation after it becomes
quiescent. Previously staged settings are retained in a private backup before
replacement. The selected data paths remain bound to the prepared update.
Hidden Windows setting files are replaced atomically during this refresh.

If a prepared update selects different data while the original agent data
remains inside the previous program folder, automatic handoff stays deferred.
The previous installation and its data are retained for recovery.

The transition uses the prepared update's installation and data bindings.
Concurrent startups share the launch lock, and a retry preserves the
selected update. Unknown owners, failed shutdowns and explicit Stop intent
retain their existing admission checks.

The regression checks cover normal startup with an active previous
installation, interrupted handoff, concurrent starts, helper-owned startup
and preservation of independent data and voice components.

## Windows update correction

An actual clean update exposed a Windows shortcut-reading error after the new
application had started. The system's File Explorer shortcut returned the
successful COM status `S_FALSE` and an empty filesystem target. The updater
treated that status as an error and left the old program folder in place.

The reader now accepts nonnegative HRESULT values. A link with no filesystem
target remains unowned and unchanged, including when its arguments resemble an
application launch. Negative HRESULT values still raise their original errors.
The normal ownership, update-directory and retained-data checks remain active.

The focused regression checks cover the successful status, foreign empty-target
links, propagation of genuine COM failures and normal shortcut cleanup. A
read-only check of 145 existing Windows shortcuts reproduced the old failure,
then read all of them with the corrected implementation while preserving their
bytes.

## Included application features

This patch includes the asynchronous Fish/XTTS delivery, recording and warm
Whisper improvements, model-aware Codex Fast controls, clean source replacement
and Forge custody work from [2.5.0](RELEASE-2.5.0.md).

The published evidence distinguishes desktop tests, native platform execution
and live owner acceptance. The inherited core battery retains its reviewed
baseline failures. Physical microphone acoustics, CUDA hardware performance and
provider Fast entitlement retain their separate acceptance boundaries. The
owner's live Fast requests were observed as the provider's `default` tier.

Native components and platform runtimes are reused where their inputs are
unchanged. The macOS native wrapper retains its older 2.3.0 bundle label and is
ad-hoc signed, with no notarization. The application reports version 2.5.1.
