# 通过 MTProto 核实准确事实

普通 Telegram 工具不够时，先看 `my_capabilities` 和 `telegram_account` 注册表。
这条路径需要已连接的用户 Telethon 账户，Bot API 本身不提供 MTProto。

顺序为 `registry_search` → `describe` → `call`。
使用带 `functions.` 前缀的精确构造器，例如 `functions.users.GetFullUserRequest`，
简写 `users.GetFullUserRequest` 不能替代它。参数来自返回模式，
人物标识或 username 需独立核实。传输层将实体转换为带 access_hash 的 InputUser/InputPeer。

资料可能包含姓名、说明、状态、共同群等。保留检查日期和来源，
不凭单个字段推测人物。没有准确地址，核实就未完成。

读取与披露分别处理。电话号码、私人转发和其他私聊保持其访问范围。
合并人物记录前，找到争议事实原始来源，区分消息作者、被引用者和陈述作者。
