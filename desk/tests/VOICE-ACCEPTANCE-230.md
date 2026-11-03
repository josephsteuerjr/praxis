# Speech / native acceptance (2.3)

## What changed and why

The application-owned speech bridge remains the only desktop adapter for a
preserved agent. Its registered `speak` entry uses the generation/deadline/lease
client and stages a guarded Ogg/Opus voice note. The standalone legacy XTTS
client deliberately remains a different protocol; do not point it directly at
the application worker or discard `Audio.check` by adapting only its Path.

A real embedded-runtime launch exposed a separate boot defect: Windows `._pth`
and isolated Python do not insert the worker script's directory. The direct
worker entry now bootstraps only its application-owned sibling directory.
Thread-import tests had not exercised that entry.

Windows ready/receipt readers can temporarily deny delete-sharing. Atomic IPC
publication and shared-file retirement retry only concrete Windows access /
sharing errors, for a bounded interval. Lasting denial and unrelated faults
remain errors; synthesis itself is never retried by this mechanism.

Opus qualification uses the actual libsndfile OPUS capability, not PyAV import
success, a renamed WAV, or a magic header alone. Media frame decoder helpers
carry the Windows no-console flag on both probes and each decode call.

Frameless WinForms windows need native MINIMIZEBOX/SYSMENU style bits for normal
Explorer taskbar behavior. The notification-icon installer restores those bits
without activation, ownership or geometry changes, including after handle
recreation. Tray Open uses native restore so maximized placement survives.
Explicit tray Exit requests session exit; hiding the window does not.

## Fast regressions

With the application/test Python and its ordinary declared dependencies:

```text
python -B -m unittest desk.tests.t_speech_application_2610 desk.tests.t_speech_protocol_230 desk.tests.t_speech_bootstrap_230 desk.tests.t_media_windowless_230 desk.tests.t_runner_startup_binding_2810 desk.tests.t_update_windowless_2610 desk.tests.t_taskbar_native_230 -v
```

The codec unit test is an explicit skip without the optional OPUS-capable
runtime. Windows sharing/native-window cases explicitly skip on other systems;
those skips do not qualify another platform. Existing assembly tests separately
check that the canonical model producer is actually included in the app bundle.

## Opt-in real voice consumer (Windows)

```text
<controller-python> -B desk/tests/voice_acceptance_230.py --python <existing-xtts-python> --model <existing-model-directory> --output <NEW-private-evidence-directory>
```

Use explicit existing runtime/model paths, read-only; no runtime/model copies or
installation are needed. Only the small shipped worker closure is assembled.
`--diagnostics` adds test-only stack sampling when diagnosing slow imports.

The script invokes the actual registered agent tool, actual app bridge/client,
actual model worker in a retained Windows Job, real PCM production and real
Opus encoding. It stages through the real agent media spool, fully decodes the
Ogg, and exercises the transport's `sendVoice` method against a local fake.
It restarts the worker, rejects a prior-generation Audio object, and checks
cancel/Pause/expired leases. Current negative triggers wait for a real partial
WAV producer; preserve the exact executed driver with each receipt, because an
earlier qualification used the weaker admitted-request trigger.

This is **not** Telegram network delivery, audible device playback, a real
Whisper recognition test, installed-profile migration, or four-platform QA.
The real-model run is opt-in, not silently part of unittest discovery. Stop
latches on the machine are respected and never cleared by the fixture.

## Native taskbar consumer (parent/operator)

```text
python -B desk/tests/t_taskbar_native_230.py --native
python -B desk/tests/t_taskbar_native_230.py --manual
```

These open only a blank isolated pywebview/WinForms window, not the application
or an agent profile. The automatic path checks real styles, owner, taskbar
visibility, native minimize/restore/maximize, tray Open and handle recreation.
The manual window is bounded to 180 seconds. Click its **active Explorer taskbar
button** to minimize, click again to restore, then repeat from maximized state.
Also check X → tray Open and explicit Exit. System-command injection is not a
substitute for the Explorer click. Inspect the actual visible window; GPU
WebView2 `PrintWindow` captures can be black and are not visual acceptance.

Keep raw native screenshots, model logs, local paths and synthetic authority
receipts in private run evidence, not in release payloads. A passing receipt
binds only the recorded sources/platform/consumer and its stated limitations.
