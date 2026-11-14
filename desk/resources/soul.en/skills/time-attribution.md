# Which event owns this time?

Before “yesterday”, “recently” or “just now”, open the source for that
exact event. `read_log`, `recent_turns` and `fs_ls` may show different
times: incoming message, reply, file write or metadata change. Do not
substitute one for another.

Match message_id, path, log entry and timezone. Do not borrow the nearest
similar message's time. Convert UTC using the current zone's rules,
not a permanent +4 offset. File copying or mtime need not date its contents.

An exact duration between the wrong events is still wrong. Without
the right link, state the limitation; sequence memory is not an exact
date. “Checked now” also needs an actual source check, not an old conclusion retold.
