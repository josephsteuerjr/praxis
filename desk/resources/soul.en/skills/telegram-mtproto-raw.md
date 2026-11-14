# Exact facts through MTProto

When ordinary Telegram tools are insufficient, check `my_capabilities`
and the `telegram_account` registry. This route needs a connected user
Telethon account; Bot API alone does not provide MTProto.

Use `registry_search` → `describe` → `call`. Supply the exact constructor
prefixed with `functions.`, such as `functions.users.GetFullUserRequest`.
Short `users.GetFullUserRequest` is not a replacement. Read parameters
from the returned schema; establish the person's ID or username independently.
Transport converts the entity to InputUser/InputPeer with access_hash.

A profile may include name, description, status, common chats and more.
Retain check date and source; do not turn a field into speculation about
the person. Without an exact address, verification is incomplete.

Reading and disclosure are separate. Phone numbers, private forwards
and other private conversations retain their access scope. Before merging
person records, find the disputed fact's source and distinguish message
author, quoted voice and claim author.
