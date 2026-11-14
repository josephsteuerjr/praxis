# 记忆层次与上下文整理

不同层次具有不同效力：

1. `memory_life` 以仅追加方式保存规范事件及近期尾部。较早前缀可变为不可变 compact 和带 `source_event_ids` / `source_compact_ids` 的片段。Compact frontier 被带回框架。更深层提高保留优先级，不提高真实性。因预算省略不代表从未存在，原始事件是否可读取应另查。
2. Compact 是模型综合，不是个人声音或已确立的长期事实。`formation` 可读取原始来源，在冻结证据集中核查 claim。`supported`、`contested` 和 `unsupported` 是不同结果。投射到人物档案或关系图，是独立的信任转换。图边缺乏可见范围时，私人关系可能只能保留为 claim。Self/topic claim 不会自动改写身份或规则。
3. `memory/journal/` 保存显式笔记和部分运行事件。原始日志是片段性来源，不是自动宪章、人物档案或意愿清单。不要假设每次对话都会自动另写一条。
4. `consolidate_context` 是人工后备路径。现行代码要求角色历史 `list[dict]` 至少八条，摘要以 `[контекст]` 写入日志。直接 `respond` 中修改调用者持有的列表，可能缩短未来上下文来源。Telegram 中 `voice_turn_envelope` 提供的是快照：局部修改不会缩短运行器历史，也不会释放下一帧。应读取结果，不能仅因写了摘要就保证窗口已腾空。

查连续性看事件/frontier/片段及 source ids；查长期事实看已支持 claim 和投射；查行动看 run、回执、git 或产物。日志行先视作线索，除非来源证明其有更高权重。`tool_consolidate_context` 变化后重新核查该机制记录。
