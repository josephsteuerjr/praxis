# Portable Python lifecycle — owner decision, 30 October 2026

The application is its extracted folder. Agent data live in
`%USERPROFILE%/Helene/data`, independently of application versions.

Opening a fresh upstream archive adopts those data and runs that upstream.
It does not reconcile authored source changes. Another Python application
folder with a valid first-start receipt is preserved. Legacy native copies
and migration remnants without a receipt follow the first-start retirement
policy. A completed receipt makes subsequent starts ordinary starts.

Source reconciliation is available only through Update in the running old
application. That old agent owns the work: compare Git base, authored source
and incoming upstream in a temporary third tree; resolve conflicts; run the
required tests and independent review; apply the accepted diff to the new
upstream folder. The incoming agent does not resolve its own admission.

Restart into that new folder, rather than overlaying the old application.
Retain the old folder and temporary work until five continuous minutes of
confirmed operation by the admitted new tree. A living launcher, an elapsed
wall timer or an open window alone is not proof. A failure or generation
change invalidates the observation interval. Cleanup belongs only to this
explicit in-app update and removes its old application and temporary work.
Manual archive launches never acquire that cleanup authority.

Implementation status is recorded in the task checkpoint; this document is
the accepted contract and does not itself assert qualification.
