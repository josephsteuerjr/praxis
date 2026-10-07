# Python client, supervisor and computer use

Owner direction, 07.10.2026: the client must move to Python; the supervisor should move too. The service should remain a minimal privileged broker that communicates with the Python supervisor/client. Process containment must remain strict, as in Ouroboros. Computer use is a candidate for the same migration.

Keep the existing design system, agent identity, data layout, IPC contracts and owner stop semantics. Telegram intake, addressing, history reconciliation and the optional server gateway are Python components. They must not depend on a native shell implementation.

The future supervisor should own policy, lifecycle, restart decisions and durable state. The broker should expose a narrow authenticated protocol for OS operations and privileged resource handles. Process containment remains an OS responsibility: Windows Job Objects and Linux cgroups. Creating a child, assigning containment and admitting execution must be one supervised operation; owner stop must revoke admission and terminate the whole owned process tree.

Computer use can keep the current request/response protocol while migrating platform adapters one at a time. Windows UI Automation uses COM; window/input/screenshot APIs can be called through Python bindings. Acceptance requires actual applications, DPI, focus, cancellation and timeout tests. A native helper can remain where a proven OS limitation requires it; it should not grow into another supervisor.

This is a future migration requirement, not a claim that the current native client or supervisor has already been replaced. The Telegram candidate introduces no Rust logic.
