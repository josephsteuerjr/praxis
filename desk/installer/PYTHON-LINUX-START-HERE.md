# Hélène 2.3.0 for Linux

Extract the complete archive to a permanent folder, then run `./helene` there.
Python is included. Keep the executable permissions when extracting.
Use a desktop Linux installation with its normal graphics driver and base
C++ runtime (`libstdc++6`/`libgcc-s1` on Debian). The archive keeps that driver
paired with the operating system's libraries.

The first window lets you create an agent or connect an existing data folder.
The default data folder is `~/Helene`, separate from the application. Removing
the application folder does not remove this profile.

For a ChatGPT subscription, the first launch installs the optional MIT
praxis-relay component. Sign in from Settings, then choose Continue. API keys
can be entered directly during agent creation.

To move the application, choose Exit in its menu first, move the complete
folder, then launch it again. Re-enable login startup after moving a folder
which had that option enabled. No system service is required.

The desktop automation component requires an X11 session. A Wayland desktop
may restrict screen capture, input and accessibility; those capabilities are
not claimed until separately verified on that desktop.

The application code is editable Python under `app/` and `tree/`. Ancient
versions migrate to clean upstream code. Preserving edits and resolving
conflicts requires a supported modern baseline and the update workflow.

Runtime and native dependency notices accompany the archive. Optional voice
components are offered only when a matching Linux payload has been qualified.
