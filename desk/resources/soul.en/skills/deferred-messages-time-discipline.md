# Timing and text of a deferred message

Before scheduling, check the current date, recipient's time zone and parsed deadline. `parse_when("today 09:45")` may produce a past time; “today” does not mean “the next suitable morning”. For ambiguous day boundaries, use an explicit date and supported time-zone format.

An overdue task may run on the next tick. A past-deadline warning calls for correcting an error, not ignoring it.

With `kind=message`, put only recipient-facing text in `goal`. Internal “write to participant…” notes, priorities and instructions may be sent verbatim. If reasoning is needed before delivery, choose the appropriate available task type.

After creation, inspect `my_agenda`: intended future deadline, correct destination and clean text. For multiple recipients, verify each delivery and creation result.

If a technical draft was already sent, acknowledge the specific mistake and correct its meaning at an appropriate time. Do not wake someone at night to repair a non-urgent “morning” message; an explicitly urgent wake-up request is different. Do not repeat a send with an unknown outcome before checking its receipt.
