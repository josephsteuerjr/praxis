# 通过 MTProto 精确执行管理操作

文件名来自历史背景，并不授予管理任何群组的权限。操作前核实所有者的现行规则、准确的 peer、话题及账号权限。不要把一个群的规则套用到另一个群。

使用 `telegram_account` 时，先 `registry_search`，再 `describe`，最后用可用构造器的准确名称执行 `call`。`callable` 只说明技术上可调用，不代表所有者授权了任意用途。核实现行参数结构和所需实体。

- `functions.channels.GetAdminLogRequest` 可协助查明删除或封禁的执行者。核对事件、参与者、时间和对象；时间相近本身不能证明因果关系。
- `functions.channels.EditBannedRequest` 可以在消息已不可见时修改成员限制。先核对频道、成员、权限和期限，不默认永久封禁。
- `functions.channels.DeleteParticipantHistoryRequest` 会删除成员历史，这是需要相应授权的独立破坏性操作。

读取完整消息及上下文，触发摘要可能被截断。技术文章中的百分比或商业词汇并不必然表示垃圾信息。`prior_measures` 为空只代表没有找到既往措施，不代表此人首次出现。`already_deleted` 不构成再次删除或自动加重处罚的理由。

依据具体调用的回执说明结果。区分消息不可用、拒绝及结果未知；超时后不要盲目重复修改操作。不要凭群名猜测私人群地址。
