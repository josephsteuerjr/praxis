# MTProto moderation facts

For rights or past moderation actions, check the current account,
channel address and available `telegram_account` registry rather than
inherit another installation's authority.

For logs, inspect `functions.channels.GetAdminLogRequest`: exact channel,
bounds/limit and events_filter. Match event_id, actor and time. A deletion
notice alone establishes neither who deleted nor another action.

The registry may offer `functions.channels.EditBannedRequest` and
`functions.channels.DeleteParticipantHistoryRequest`. A constructor grants
no goal, rights or room policy: establish these from current request
and actual context. Use live schemas for parameters and permissions.
Do not repeat a closed or completed action because an old trigger reappears.

A truncated message is only part of the source. Read the full message
and context where available before concluding. Confirm actions with
their supported receipts.
