# Compare test identifiers, not an old count

A test count needs HEAD, runner, discovery scope and skip conditions. Historical counts from another run are not the standard for the current build.

`praxis_test.py`, `unittest discover` and `pytest` may collect different scopes. Inspect current discovery: nested directories, packages, function-style tests and separate platform checks. Do not call a difference a gap or a success without the roster.

1. Obtain actual rosters, for example through `--collect-only` and discovery.
2. Normalise `file.py::Class::test` to `file.Class.test`; compare sets and duplicates separately.
3. Match skips, failures and errors by full id and reason.
4. Check collector hypotheses with AST or a small script. If counts disagree, revise the hypothesis, not the data.
5. Retain expected counts with command, HEAD and platform; do not carry them into another release from memory.

A green exit with unexpectedly reduced collection requires a coverage check. Extra tests also mean the runs cannot simply be called equivalent. Check environment inheritance separately.
