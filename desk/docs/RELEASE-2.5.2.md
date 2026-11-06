# Hélène 2.5.2

Download the archive for your platform, extract it into a permanent program
folder, and open Hélène. Existing update choices and data storage are unchanged.

## Motion

Turn details open at their measured content height; fast reversals and delayed
content continue from the visible position. Reduced-motion preferences remain
respected.

Scrolling and rubber-band return use the full elapsed frame time, including
time spent inside the previous frame callback. The spring settings are
preserved. Disclosure animations are owned per element and retarget from their
current rendered size when interrupted or populated.

## Intel Mac

The separate macOS x86_64 archive contains Python and its libraries, a Finder
application and the existing native system components. No system Python is
required. The package targets macOS 14 or later; native qualification runs on
Intel macOS Tahoe. Local Whisper and XTTS are unavailable in this package;
the included Python has no compatible ONNX Runtime wheel for macOS Intel.
Remote speech and Fish API do not require those local engines. The application
uses an ad hoc signature without Apple notarization.

## Reusing an extracted folder name

A fresh archive no longer adopts an orphan clean-update record left beside a
previous folder with the same name. The old record is retained before preparing
the selected current installation. Existing or interrupted preparations keep
their binding checks and recovery behavior.

## Verification and scope

Regression coverage checks the scrolling clock at 60, 120 and 144 Hz with
simulated rendering work, as well as edge return, momentum and input handling. Browser checks exercise interrupted expansion, collapse, content
growth, scaled layouts and reduced motion. These checks use controlled local
fixtures, not an owner's agent or model provider.

This patch changes shared interface motion. It carries the runtime, updater and
platform limitations documented for [2.5.1](RELEASE-2.5.1.md); it does not claim
a new full-core qualification or macOS notarization. Platform package receipts
and owner acceptance are recorded separately.
