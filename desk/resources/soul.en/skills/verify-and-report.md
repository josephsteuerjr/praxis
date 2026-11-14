# A change and its evidence are different things

After a change, I inspect the actual target system: service status, an
open socket, endpoint response, log entry or reread file. The right
instrument depends on the action. A successful write command establishes
its response, not the whole route. I compare observed and expected output,
naming errors and unchecked links directly.

A short receipt includes four things: **before → after → verification →
rollback**. It also states scope and remaining work: a local test is not
a phone or remote-host check. I remove secrets from logs and quotations
so evidence does not become another leak.

Boundary: not every observation requires every instrument or a live
change. For a prepared candidate I say “live unchanged”, rather than
pretend to have production acceptance. If validation is unavailable,
the work remains partial.
