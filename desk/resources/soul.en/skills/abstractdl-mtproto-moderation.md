# Precise moderation through MTProto

The filename is historical: it grants no authority to moderate any room. Before acting, check the owner's current policy, exact peer, topic and account permissions. Do not carry one group's rules into another.

For `telegram_account`, use `registry_search`, then `describe`, and only then `call` with the exact available constructor. `callable` means technical availability, not the owner's consent to every use. Check the current schema and required entities.

- `functions.channels.GetAdminLogRequest` helps identify who deleted a message or banned someone. Match the event, participant, time and object; nearby actions alone do not prove a causal chain.
- `functions.channels.EditBannedRequest` can change restrictions without a live message. Verify channel, participant, rights and duration first; do not default to a permanent ban.
- `functions.channels.DeleteParticipantHistoryRequest` deletes a participant's history: a separate destructive action requiring appropriate authority.

Read the complete message and context: a trigger may be truncated. Technical posts containing percentages or commercial words are not necessarily spam. Empty `prior_measures` means no measures were found, not that the person is new. `already_deleted` is no reason to delete again or automatically escalate punishment.

Support the outcome with the specific call's receipt. Distinguish an unavailable message, refusal and unknown outcome; never blindly repeat a mutation after a timeout. Do not guess a private room address from its name.
