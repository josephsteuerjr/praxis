# Checking facts about a person through MTProto

When ordinary tools lack a needed fact, check the available `telegram_account` schema: `registry_search` → `describe` → `call`. Use the exact constructor name with `functions.`, such as `functions.users.GetFullUserRequest`. Take parameters and entity resolution methods from the current schema; do not assume `access_hash` is available for every id.

A profile may contain a name, biography, status, shared chats and other fields depending on the API, privacy and availability. Do not promise access to music, exact online times or phone numbers. Being able to see a field does not authorise sharing it with another audience.

Identify the person through a verified id and source. If the id is unknown, record that verification was not performed. Matching names or old summaries do not establish identity. Update only task-relevant facts with date and source; follow the installation's storage and backup policy.

Before merging dossiers, find the original evidence for disputed claims and distinguish author from quoted speaker. Preserve old references or an explicit redirect and a rollback path; do not remove duplicates before checking integrity. Merging two files does not validate their conflicting claims.
