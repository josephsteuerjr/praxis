# Replies in channel comments

Telegram channel comments belong to a discussion group thread attached
to a particular post.

- Reply to the post's message in the discussion group or to a message
  within its thread so the reply appears under that post.
- Do not send a separate root message to the group's general stream;
  it will sit outside the post's comments.
- Check `reply_to` and the current thread selector before sending.
- If already sent elsewhere, do not blindly duplicate it: locate the
  correct post or thread and deliver the correction there.

An ordinary discussion group may use a reply thread rather than a forum
topic. Use `top_msg_id`, the reply-target map and actual `reply_to`,
not just the word “topic” in an interface or internal description.
