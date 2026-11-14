# Forge 工作进程存活与托管

`supervisor.log` 为空、修改时间旧或模型回复很久，都不能证明卡死。
日志新、CPU 高或连接到 443，也不能证明有用进展或适合停止。

通过 `coding_agent` 及正式控制轮询准确对象。
核对任务、工作进程、代次、执行主机、事件和结果。
Windows 不用 `os.kill(pid, 0)` 探测存活。
一个 PID 或主进程消失，都不能描述整棵树。

工作结果与进程托管是不同状态。`status=unknown` 既不代表成功，也不必然代表仍活着。
不能把 `custody_recovered=false` 和 `continuation.safe=false` 改成虚构 stopped。
遇到 `next_boundary=summary_in_flight`，不能只因缺少终态记录就自动重跑步骤。

使用 `coding_control` 停止，并确认准确归属的整棵树结束。
受支持的 Windows 路径依据持久 Job 绑定证明，不靠猜进程名。
不能为释放 writer 删除锁或手改终态，必须保留工作副本、结果和历史。

缺少充分绑定的旧任务按正式恢复流程处理：由所有者确认原始 Windows 主机，
必要时执行流程要求的完整系统重启，不能擅自重启。
重启应用不构成同等证据。即使安全释放托管，未知工作结果仍然未知。
