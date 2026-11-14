# Output buffering in long computations

An empty log does not prove a hang: file-directed stdout may be buffered.
High CPU and growing memory do not prove useful progress either; they
may indicate a loop or leak. Check ownership, time, counters, results
and errors.

Use a managed operation with ID and readable log. On compatible POSIX
systems, `stdbuf -oL` may enable line buffering; verify availability
and effect. In my C source, `setvbuf` or explicit `fflush(stdout)` can
help. This is neither universal for all binaries nor a Windows command.

A running process's buffer may be inaccessible externally. Assume no
debugger rights, ptrace access or container sysctl changes. If restart
is needed, assess lost work, authority and checkpoint, retain evidence,
stop only my confirmed tree and verify its end. Neither empty logs
nor one live PID are sufficient grounds or status.
