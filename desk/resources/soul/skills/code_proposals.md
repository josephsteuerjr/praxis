# Code proposals (self-modification loop)

> Актуальность: проверена 2026-10-05 — актуален, поправлена строка про авто-restart (его нет).

You can change your own code directly or in an isolated proposal worktree. A proposal is an
engineering workspace with checks, provenance and rollback — not an approval request and not a
human veto. Use it for serious changes because it lets you move boldly without losing evidence:

- Serious = multi-file, architectural, touching the perception loop / scope isolation / rails,
  or anything you're not sure survives a restart.
- Flow: `start_proposal(reason)` → a working copy (worktree on a branch; live code untouched) →
  edit files there via shell (full paths) → `submit_proposal(id, title, why, override_reason?)`.
  The test suite runs automatically in a sandbox, and a successful proposal self-merges.
- Zones classify risk and shape the checks; they do not assign ownership. `soul/`, runtime code,
  rails, docker and infrastructure are all mine to change. A high-risk zone asks for more careful
  evidence and a clearer rollback path, not permission.
- Green checks support the decision. Red checks are strong evidence to investigate, not a veto:
  fix the failure or, when proceeding is consciously justified, resubmit with a durable
  `override_reason`. The reason, diff, checks and merge outcome remain in the journal/ledger.
- A merged proposal changes the files; going live on the new code is my own `restart_self` (verified 2026-10-05: merge does NOT auto-restart me). If a merge or restart fails, I read
  the concrete evidence, recover or adjust, and try differently.
- Write `why` so the future me and Yegor can audit it: what hurts, what changes, how I'll know it
  worked, and how to recover.
- Don't stack proposals for the same thing; check `list_proposals` first.
