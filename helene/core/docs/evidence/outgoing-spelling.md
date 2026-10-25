# Outgoing spelling preflight — proposal, not activation

## Measurement before implementation

Synthetic samples only; no dialogues, credentials or private corpus read. Raw reproducible
results and exact samples: `outgoing-spelling-benchmark.json`. Linux task environment,
40 fresh subprocesses per engine/sample, elapsed wall time including dictionary loading;
p50 median, p95 nearest rank (38/40). Hunspell `/usr/bin/hunspell` with available
`ru_RU,en_US` dictionaries; Aspell available via PATH with `ru`, `en_US` dictionaries.

| Engine | p50 range ms | p95 range ms |
| --- | ---: | ---: |
| Hunspell ru_RU,en_US | 275.90–297.07 | 316.31–327.40 |
| Aspell ru | 10.20–10.58 | 14.34–16.39 |
| Aspell en_US | 7.92–8.75 | 10.10–14.37 |

Clean RU / EN have no flags with the corresponding dictionary (English Aspell silently
ignores Cyrillic: this is NOT Russian coverage). Four synthetic typos detected by
Hunspell; Aspell split by script detects the same four. Technical clean sample flags
6 false positives with Hunspell and 7 with English Aspell; Russian-only Aspell rejects
English prose too. No claim of production precision from this small corpus.

Decision budget: total local advice deadline **100 ms**, not a transport timeout.
Hunspell fresh-process latency exceeds this materially: do not enable it. Aspell split
by script is viable as an optional limited mode, not a default behavior change.
`PRAXIS_OUTGOING_SPELLING=off` is the explicit deployment setting; unset also stays off.
`limited` opts into Aspell with two subprocesses at most sharing the 100 ms deadline.
No environment/profile changes or activation performed. Owner must explicitly choose
activation with their usual behavioral migration/receipt; this proposal is not deployment.

## Noise and limits

Limited mode masks fenced/inline code, handles, URLs, paths, numbers/underscores,
capitalized names and mixed-case identifiers. Only lowercase RU/EN prose words (>=3 chars)
are considered. This deliberately misses sentence-initial typos and proper-name mistakes.
8 findings max, 400 words / 8000 chars max; larger drafts are skipped with a visible note.
Unknown technical lowercase vocabulary can still be noisy. Turn off or explicitly retain
the exact authored text, rather than repeatedly asking for repair. No persistent personal
dictionary, text storage, suggestions, auto-correction or automatic resend.

Post-implementation end-to-end limited-mode probe (40 fresh runs per sample): p50 / p95
RU clean 11.30 / 15.40 ms, EN clean 9.99 / 12.64 ms, technical 8.86 / 13.03 ms,
typos 22.49 / 29.20 ms. Technical noise drops to two flags (`pytest`, `dedup`),
not zero. The original typo sample yields only `mesage` because the other three
are capitalized: a deliberate coverage tradeoff, not full spell checking.

## Integration boundary

`reply`: preflight before outgoing guard, spoken-text dedup and Telethon callback.
`send_message`: preflight before existing artifact cleanup and transport callback.
A structured JSON result has `status=not_sent`, `reason=review_required`; it is not a
transport error. No callback means no outbox admission, random-id allocation, dedup
consumption or delivery. Repeat corrected text normally; leave authored text with
`spelling_confirm_text` byte-for-byte (Python string) equal to the **raw input text**.
No token expiry/cache or implicit bypass on a repeat. Confirmation mismatch never sends.
Spelling does not rewrite text; pre-existing outgoing guards/artifact cleanup remain.
Exact confirmation bypasses spelling only, not privacy/credential guards or existing
cleanup. Failure/missing utility or dictionary/timeout skips advice and adds a fail-open
receipt note; no partial review on failure of the second dictionary.

The existing callback owns delivery and outbox policy (`telegram_outbox.py`,
`mtproto_runner.py`); neither is modified. `DurableSideEffectPending` still propagates,
unknown transport outcomes still follow `_direct_send_outcome`, with no new attempt.

## Reproduction inventory / commands

Task execution shell: `/app/.proposals/448f0ba5`, Linux 6.8.0-142-generic x86_64,
Python 3.12.13. Hunspell 1.7.2 at `/usr/bin/hunspell`; Aspell 0.60.8.1 at
`/usr/bin/aspell`. `command -v hunspell aspell` printed only Hunspell in this shell
(despite Aspell invocation working); direct `ls -l` and `aspell --version` verify both.
Reviewer's separate shell reported Hunspell absent: runtime availability is host-dependent,
hence no assumption of installed tools at delivery time, and failure is fail-open.

Inventory commands: `hunspell -D`, `aspell dump dicts`, `hunspell -v`,
`aspell --version`. Hunspell available `/usr/share/hunspell/ru_RU` and `en_US`;
Aspell lists `ru` and `en_US` among English variants.

Raw probe commands, input UTF-8 synthetic text plus newline, capture stdout/stderr:
`hunspell -d ru_RU,en_US -l`, `aspell --lang=ru list`,
`aspell --lang=en_US list`. 40 invocations per sample with
`subprocess.run(..., input=text+'\n', text=True, capture_output=True, timeout=2)`;
wall time via `time.perf_counter()`. Limited-mode probe invokes `preflight(text)`
40 times with `PRAXIS_OUTGOING_SPELLING=limited`, using the same JSON sample texts.

## Verification

Final targeted command:
`python -m pytest -q test_outgoing_spelling.py test_send_message_cjk_0310.py test_tools_en_1509.py test_reply_hand.py test_direct_telegram_outbox.py test_agent_resume_runtime.py`
— **115 passed**, outbound network attempts **0**. Includes corrected retry, repeated
review without delivery, exact confirmation/mismatch, reply bypass and timeout,
missing binary/dictionary, second-dictionary failure and shared deadline, no silent
spelling rewrite, no new transport attempt after unknown outcome, refusal subclass preservation.
`python -m py_compile agent.py tool_text_en.py outgoing_spelling.py test_outgoing_spelling.py`
and `git diff --check` pass.

Full gate after description compaction: `python praxis_test.py discover -q`, durable
process `proc-32317556`, log `/tmp/spelling-manifest-full-gate.log`: **6284 tests,
OK (skipped=5)**, 553.532 seconds; supervisor exit 0. The first full run failed only
on the unchanged tool manifest size threshold; descriptions were compacted without
changing runtime or relaxing that threshold. Final manifest: 29,942 < 30,000
(58 characters of headroom: small, not a durable capacity margin).
Independent full-proposal reviewer `agent-38d484e4`: NO BLOCKERS for disabled opt-in
acceptance, including the description compaction. Activation remains separate.
This records pre-integration verification; no default activation performed.
