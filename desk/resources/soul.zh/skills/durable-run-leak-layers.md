# 已接受的任务控制未成为终态

若活动列表显示 paused/blocked，而 `events.jsonl` 含有已接受的 `task_control`，不要手动关闭 manifest。先核查是否确实接受、对应代际和 checkpoint，以及之后发生了什么。

两个历史诊断层次：

1. 执行后，输出 guard 比较旧回执和新草稿。不匹配可能产生 `DurableExecutionError` 与 `process_recovery`，覆盖已接受控制。检查 `_AgentResumeRuntime` 中 `_land_addressee_free_work_control` 与 `_prepare_authored_delivery` 的顺序。
2. Preflight 阻止进入执行。反复出现 `resume_attempt_idle` 不证明有进展。检查 `plan_resume`，以及无收件人、无待完成调用、无 checkpoint 输出的工作是否可用 `checkpoint_control`。

读取最新 `run_checkpoint`、`work_loop.control` 和对应控制结果。保留 lease/cursor 与完整性校验，不能把任意日志文字当作终止授权。

通过受支持路径检查聚焦回归及实际 `status_changed` / `recap_written` 事件。成功解析控制不证明子进程退出，也不证明 Forge writer 已释放。

旧 wait 停放、所有者暂停、checkpoint 丢失和进程控制归属是不同类别。不可批量关闭，也不可恢复所有者明确暂停的工作。某项目历史上合并过修复，不证明每个已安装版本都包含它。
