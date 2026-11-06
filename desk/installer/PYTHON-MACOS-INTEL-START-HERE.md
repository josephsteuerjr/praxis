# Hélène for Intel Mac

Unpack the complete archive into a writable folder and open **Helene.app**
inside it. Keep the app, runtime and other files together in the Helene folder.
Python and the interface are included. No system Python or administrator
installation is needed. This package targets **macOS 14 or later, Intel x86_64**.

The first window creates an agent or connects an existing data folder.
The default profile is **~/Helene**, separate from the program. Choosing a
ChatGPT subscription installs the pinned Universal praxis-relay component;
direct API connections do not need it. Sign in and explicitly continue the agent.

Use the application's update controls for clean updates or preserving authored
changes. Keep recovery receipts if an update is interrupted. Source-preserving
updates require Git on PATH; Apple's Command Line Tools provide it.

Local **Whisper and XTTS are unavailable in this Intel package**. In particular,
ONNX Runtime does not publish a macOS Intel wheel for the included Python 3.14.
Remote speech and the Fish API do not require those local engines. Previously
installed model data is retained; retaining data is not engine compatibility.

The app uses an ad hoc signature, without Apple notarization. If macOS blocks
the first launch, use its Privacy & Security settings to allow this app.
Microphone, Screen Recording and Accessibility permissions must be granted
on the user's Mac when those features are needed. Native CI does not verify
physical microphone input or the user's permission choices.

Dependency licenses are in **licenses/** and beside Python packages in
**runtime/**. The package's qualification receipt records the macOS version
actually used for native testing; a declared minimum is not an execution test
on every older macOS release.
