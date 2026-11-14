# 在 Forge 中开展工程工作

按当前提供的参数结构直接调用工具，通过 `my_capabilities` 核查能力。

1. 用目标、target 和适当隔离方式启动 `coding_session`。`target="self"` 表示应用自身代码，`isolation="auto"` 选择可用路径。核对返回的 source root、工作副本和模式；`direct` 会修改选定目录树。
2. 通过 `coding_inspect` 读取文件、符号、diff、历史和诊断，版本取自读取结果。
3. 用统一 `edit` 修改，传入 `expected_sha256`，逐文件检查结果并重新读取状态。`partial` 不保证回退；`in_doubt` 需要先查明结果再重试。
4. 使用 `coding_run` 或持久 `coding_process`，读取结果、退出状态和日志。前台 Python 可通过 `h` 调用工具，并检查代际及本地进程所有权。远端任务显式传入 `task_id`。
5. 仅在当前指令、权限和预算允许时使用 `coding_agent`、`coding_swarm`、`coding_review`。明确产出，检查每个参与者的日志和结果；通过 `coding_control` 管理。
6. 执行适当检查，查看 `coding_verify` 和 diff。结果经验证且证据保留后再结束。

结果未知、进程控制归属和任务结果是不同状态。恢复应从 status、历史和回执开始，不能删除锁或手动伪造终态。

不要保证每台电脑都有 root broker 或 systemd。获准重启前保存状态和恢复路径，之后核实新代际及实际响应。工作日志中不放秘密。
