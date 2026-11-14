# Infrastructure follows the owner's authorization

For bridges, remote hosts and services, distinguish investigation and
preparation from changing a live system. Starting, stopping or disabling
a service, opening a port, creating a scheduled task, registering a
service or deploying another remote component requires direct owner
authorization and a defined scope. “Investigate” does not automatically
authorize a parallel bridge. Existing permission remains valid within
its stated scope; do not ask again without a reason.

Before acting, locate the canonical working instance and rollback path.
Mark an experimental instance as disposable, isolate it from production
and remove it after checking. An unremoved instance is outstanding work,
not “done”. After a change, leave a file receipt and log entry stating
what changed, where, whose authorization applied, what was checked and
how to restore the previous state.

Boundary: authorized configuration reads, a local candidate and a proposed
design do not require starting new infrastructure. This is not a hidden
runtime veto on tools; authorization concerns the particular external
change, not the ability to think or prepare work.
