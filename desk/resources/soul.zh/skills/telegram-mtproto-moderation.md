# MTProto 管理事实

需要权限或历史管理操作的事实时，核实当前账户、频道地址及可用的
`telegram_account` 注册表，不继承另一套安装的权限。

操作日志查看 `functions.channels.GetAdminLogRequest` 的准确频道、
范围/limit 和 events_filter，核对 event_id、执行者和时间。
一条删除通知本身，不能证明是谁删除或是否还进行了其他操作。

注册表可能提供 `functions.channels.EditBannedRequest` 和
`functions.channels.DeleteParticipantHistoryRequest`。
有构造器不等于有目标、权限或房间规则，须根据当前请求及实际情境确定。
参数和权限以实时模式为准，不能因旧触发条件再次出现而重复已完成或关闭的操作。

截断消息只是部分来源。条件允许时，读取完整消息和上下文再下结论。
行动结果用正式回执确认。
