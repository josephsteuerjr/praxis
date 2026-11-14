# Hélène 紧急停止

工作危险时，可用的 `panic(reason)` 设置持久安装级停止，并启动终止智能体工作。
关闭窗口不等于停止，后台会话可能仍然存活。

所有者可用窗口、托盘或手机应用中的紧急停止、已安装紧急快捷方式，
或向自己的机器人发送 `/panic`。`/panic@BOT_USERNAME` 需要该机器人的真实用户名。
Telegram 路径在模型回合前核实当前配置的所有者。

Windows 先确定实际程序路径。
CMD：`"<absolute-program-path>\helene.exe" --panic`。
PowerShell：`& "<absolute-program-path>\helene.exe" --panic`。
发行包包含服务工具时，可从其目录运行 `helene-svc.exe panic`。
不能假设安装在 Program Files。

打开窗口、重启或更新，不等于同意解除停止。
恢复需要所有者明确操作，或支持的 `helene-svc.exe resume` 命令。
智能体不能借延后进程绕过所有者决定。

标志禁止新启动，具体进程结果仍需单独检查，可能保持未知。
不能把标志当成整棵进程树结束，或所有已发外部行动取消的证据。
