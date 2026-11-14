# From a useful tool to completed work

Open this when adding a tool, changing a workflow or repeatedly guessing
the next step. A tool description explains a call; a skill explains
how to get a verified result with it.

Descriptions cover arguments, limits, side effects and response meaning.
Skills help choose paths and branches. Mandatory checks, secret protection,
versions and locks remain in code; prose replaces none of them and
grants no authority.

## Begin with a scenario

Choose a complete recurring task: schedule a return, test connectivity,
move a project. Do not create one skill per button. Extend an existing
scenario's branch when appropriate, retaining one source of truth.

State the trigger, desired result and when to choose a different path.
Use existing tools and available data only: mentioning a planned feature
does not make it operational.

Check names and signatures against complete tool schemas; no separate
expansion is needed before calling. A script can inspect schemas through
`h.describe`. If a tool is not offered, check `my_capabilities` rather
than invent its name or arguments. Actual state and schema outrank an old skill.

## Make the next step clear

1. Which facts are needed before acting, and where to get them. Do not
   repeat an existing check whose result still applies.
2. What to do and which observation chooses the next branch. Name,
   recipient, instance and authority must belong to the current work.
3. How to confirm the result: call receipt, new state or actual behavior.
   “Accepted” does not always mean “completed”.
4. What remains: result, storage location and unfinished step.

## Refusal, partial completion, unknown outcome

If refusal precedes mutation, fix its cause and repeat the needed step.
A skill grants no missing authority: name the capability that is absent.

For partial completion, retain successful receipts and continue the rest.
Repeating a whole batch can duplicate effects. After a timeout, inspect
actual state by operation ID or address: a missing response does not
prove no effect. If the outcome cannot be established, record uncertainty
rather than success or refusal. Describe rollback only where it is real.

## Short routes

For a file: read content and version → call `edit` with the expected
version → check receipt and new contents. With `in_doubt`, reread state
and journal first; blind rewriting may erase a successful edit. Get
actual arguments from the schema, not this example.

For engineering work, open [coding_hands](coding_hands.md), bind the
actual directory, inspect, change, verify behavior and diff, then finish
with evidence. One successful call does not replace the route; the skill
does not authorize self-code edits outside the current task and authority.

Trivial reading needs no separate multi-step skill. A new scenario needs
at least a short example and boundary. Label a project example as such,
not as a feature already verified.

## Check the skill itself

Walk through a normal request, refusal, partial result and disconnection
after sending. Every observation should have a clear next step. Add a
missing branch where guessing is required. If prose just repeats the
schema, shorten reference material and explain the decision. Do not
collect generic prohibitions instead of repairing an awkward tool.

Link the skill to the [index](INDEX.md) and existing scenarios. Writing
and replacement rules are in [write-your-skills](write-your-skills.md).
Choosing between open reflection and a concrete return is illustrated
in [tasking](tasking.md).
