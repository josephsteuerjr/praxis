# Hélène 2.8.6 Beta — all platforms, Latest

This release fixes silent supervision during slow engine startup, found while enabling the sandbox to check an existing Telegram attachment.

The desktop process owner now publishes supervision and accepts restart requests independently of the engine. Sandbox permission preparation and recovery of interrupted turns no longer postpone supervision until startup has finished. The window shows the current phase and elapsed time, and Stop remains available during startup. A fresh heartbeat from the owned inbox reader is still required before the engine is called ready.

Explicit Resume allows up to ten minutes for slow initialization. Cancellation, programme Exit and owner Stop remain authoritative. Restart targets only the selected agent; uncertain cleanup prevents another generation. A temporary status-file sharing denial does not retire healthy processes or execute the same restart request again. Startup metadata is included in the support bundle without configuration credentials.

An actual Windows AppContainer process was also used to enumerate an existing Telegram media directory and read an attachment created before the upgrade: the SHA-256 matched and every ZIP entry passed CRC. The same process could not read the owner or model configuration files. This confirms that concrete Windows case; it is not a claim about every external mount or every operating system’s sandbox.

This release includes all 2.8.5 changes. The core, bundled runtime and relay are unchanged; their prior qualification remains explicitly bound to identical inputs. Changed desktop supervision, lifecycle behavior and packaged native interfaces are checked again for this release. Detailed fresh checks and inherited limitations are recorded in release-evidence. The previously recorded core failures remain visible; this release does not claim an entirely green core suite.

macOS packages are ad hoc signed and are not notarized. The ARM64 server retains relay 1.2.0; desktop packages include subscription quota failover.
