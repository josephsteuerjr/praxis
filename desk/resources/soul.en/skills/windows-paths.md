# A Windows path is one argument, even with spaces

Before running a command, registering a service or writing configuration,
check the exact path the program will receive. In a command string,
quote a spaced path as a whole. With an argument-array API, pass the
path as one argument rather than constructing a shell string. Pay
particular attention to the service executable and its arguments:
a parsing error there survives the current turn.

Do not create helper files in the drive root. After a script, inspect
actual outputs and the root for an unexpected ghost file prefixed
`C:\Program`, without mistaking the normal `C:\Program Files` directory
for junk or deleting by a wildcard alone. Error 193 / `0x800700C1`
(“not a valid Win32 application”) warrants checking path parsing and
a possible ghost file before automatically blaming the binary.

Boundary: an argument-array API needs no extra quote characters around
an unspaced path. Confirm error 193's cause by inspection; do not treat
it with blind deletion.
