# Hélène 2.8.2 Beta

**Windows prerelease. Stable Latest remains 2.7.2.**

This release fixes defects found on Windows after 2.8.0/2.8.1, by the owner's own agent on his machine and in Tim's Windows 10 feedback.

**Window resizing.** The 2.8.0 edge and corner handles for the frameless pywebview window were installed only for the platform names `win32` and `linux`, but the window bridge reports `windows`. On Windows no handle existed, so dragging an edge changed nothing. The renderer now uses the bridge's own names, and a regression binds the two.

**Windows MCP control.** Before a new period of AI mouse/keyboard control, Windows-MCP asks the person at the desk for consent through a Tk child process. Hélène's component runs Python's embeddable runtime, which has no tkinter: the child exited, the answer was "error", and every first Move/Click/Type failed with `CONTROL_UNAVAILABLE` while the status still said `ready`. Hélène now shows the same prompt as a native Windows message box with allow, deny and timeout answers, in the owner's interface language. "No" is the default button and any failure denies.

**Live model output.** While a model call runs, the steps show the words generated so far: the reasoning (only when the provider streams it) as a fold, the text argument of a reply call as it is written, and plain text, all marked "not sent yet". The preview lives in a small file next to the run, scrubbed like the durable model records, and is replaced by the durable output when the call ends. Without a window watching, provider calls are unchanged.

**Honest stop notices.** A turn stopped by the model's `max_tokens` limit, by an unknown step outcome or by a cancel used to say it was paused at a checkpoint awaiting confirmation, which nobody could give. Each case now has its own notice; only a real checkpoint waits for confirmation.

**Smaller fixes.** A call with an unsupported argument names the extra field and the schema's required ones instead of asking for all required arguments again. The images card's ChatGPT sign-in buttons form their own group. The usage note explains that input sums the whole context of every call over seven days, and each model row shows its uncached part.

The attachment fix from 2.8.0 is now also confirmed in a real AppContainer with the bundled shell: a run folder made by 2.8 is readable, one made by 2.7.2 is denied.

All features of 2.8.0 and the 2.8.1 startup hotfix remain included. Only Windows assets are published. Physical acceptance on Tim's Windows 10 and the owner's devices remains separate. The inherited core failures recorded for 2.8.0 remain visible; this release does not claim an entirely green core suite.

## Release checks

Full core gate on the release core: 8019 tests, the 163 failures inherited from 2.7.2/2.8.0 and none new among common tests. One newly added test (`test_edition_workers_1411.test_parallel_reset`) is timing-sensitive on a single CPU: run alone it passes 1 of 5 times without this release's changes and 2 of 5 with them, so it is recorded as flaky, not as a regression. All five frontends rebuilt (127 outputs); 178 Node tests passed. The native launcher was rebuilt (its embedded catalogs changed) and passed four kernel tests on the actual embedded Python.

On the actual package: Windows MCP started through the new entry with 21 tools, read a fixture, honoured permission revocation and refused an unknown caller; relay sign-in survives; component transfer passed; nine owner-stop/resume cases passed in both import modes; 52 touched desk tests passed on the embedded Python 3.14.5. A real 2.7.2 installation upgraded to 2.8.2 under an ordinary medium-integrity token, keeping history, authored instructions, workspace code and a locked old file. The native consent prompt was shown on a real desktop: a timeout returned "timeout" and a click on Yes returned "allow". The attachment fix was confirmed in a real AppContainer with the bundled shell. Source privacy scanned all 3745 files against the actual local and server secret values.

Physical acceptance of window resizing and the consent prompt on the owner's and Tim's machines is still separate.
