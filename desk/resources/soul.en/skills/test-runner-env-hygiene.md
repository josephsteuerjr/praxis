# Test runner environment hygiene

A test launched from a live process may inherit operational variables after `load_dotenv(override=True)`. A tested module not reading dotenv itself does not clear an already populated environment.

Use the repository's prescribed runner, such as `praxis_test.py`, with its isolation, test BASE and network restrictions. Check the current runner's actual contract; its name does not make results infallible.

For discrepancies, retain commands, HEAD, test roster and relevant non-secret variables. Compare an isolated run with expected values, including `PRAXIS_TEST`, `PRAXIS_FRAME_HEAD_STABLE` and `PRAXIS_FRAME_EPOCH` where relevant. Do not blindly erase the environment or mistake removing one flag for providing a missing sandbox.

Run long or process-spawning checks under managed execution with appropriate deadlines and file logs. On Windows, hide test child consoles; do not use a runner that loses detached processes.

`rails.sync_md()` from an incompletely initialised diagnostic process can write a degraded manifest. Do not regenerate live documents as a test side effect. Preserve a preimage, check required initialisation and review the diff. Repair only the specific owned change, retaining neighbouring edits.

Line numbers and old merge hooks do not guarantee freshness. Inspect current code, events and manifest contents.
