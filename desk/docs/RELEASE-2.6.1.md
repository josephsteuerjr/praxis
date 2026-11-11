# Hélène 2.6.1 Beta

Windows installation and updates use the signed-in user's existing permissions.
This corrects the administrator prompt reported after the 2.6.0 Beta release.

- A clean first launch no longer treats its newly created profile as an old
  installation or scans system startup entries to stop a nonexistent previous app.
- Unreadable services, scheduled-task folders and registry views remain untouched.
  Readable entries are still checked against the exact previous installation.
- Stopping an old app, migrating a readable profile and retiring old startup
  entries never trigger an automatic UAC retry or grant new filesystem permissions.
- Protected old program files and registrations are retained and reported without
  preventing ordinary installation or update. Cleanup does not terminate unrelated
  file holders. A changed folder or registration is still rejected.

Download the archive for your platform, extract the complete folder and run Hélène.
This remains a Beta release, published as Latest for the built-in updater.

Windows cannot transfer unreadable data or stop a process the user has no right to
stop. Hélène preserves that state and reports the specific obstruction without
requesting administrator rights or pretending the data transfer succeeded.
The reported corporate work PC has not been rechecked remotely.

This correction retains the features and platform limitations described in
[2.6.0 Beta](RELEASE-2.6.0.md). Actual package qualification is recorded separately
in the release evidence; source tests alone are not device acceptance.
