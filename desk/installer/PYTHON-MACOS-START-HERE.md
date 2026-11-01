# Hélène RC18 for Apple silicon

Unpack the complete archive into a permanent writable folder and open
**Helene.app** inside it. Python, the interface and the agent code are included.
Do not move Helene.app out of the surrounding Helene folder. No system service,
administrator installation, Tauri shell or global Python installation is used.

The first window creates an agent or connects an existing data folder. The new
profile defaults to **~/Helene**, outside the program folder. API connections do
not install a relay. Choosing a ChatGPT subscription installs the separate pinned
MIT praxis-relay component, then opens its sign-in settings. The agent remains
paused until you sign in and explicitly continue it.

The **app/** and **tree/** folders contain editable Python. Close the application
before moving its complete program folder; the separate profile keeps its
conversations, queued work, device bindings and phone pairing ledger. A fixed
phone origin also needs its existing external HTTPS route to remain available;
temporary automatic tunnel addresses can change.

Use the application's update controls to preserve authored changes or choose
clean code with the current profile. An interrupted update has a recovery path;
retain its receipts until startup is confirmed. Connecting another data folder
does not delete the previous one.

Optional voice components download separately. Their terms and the base native
dependency attribution texts are under **licenses/**; Python dependency licenses
remain beside their packages in **runtime/**. The application bundle is signed
ad hoc for this candidate, without Apple notarization. Native CI evidence does
not replace acceptance of microphone, screen recording and Accessibility
permissions on the owner's Mac.
