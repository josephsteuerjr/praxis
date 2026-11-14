# Git worktrees across path namespaces

A host and container may see one repository under different absolute
paths. `git worktree prune` in one environment can treat another's
live working copy as missing and delete its metadata.

Before cleanup, inspect `git worktree list --porcelain` in the creation
environment, ownership, uncommitted and untracked files. Preserve results
and original changes. Remove only my completed copy with `git worktree remove`,
without ending the recipe in global cleanup of unrelated or invisible copies.

After a severed link and `not a git repository`, first preserve tree,
diff and hashes. Do not blindly rebuild `.git/worktrees/*` or infer
process termination from broken metadata. Use supported migration to
healthy isolation and terminal-state handling, retaining history and
unknown outcomes.
