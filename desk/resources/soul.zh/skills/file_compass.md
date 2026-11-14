# 文件指南针

先判断对象类别，再找归属目录。实际根目录取自当前环境和安装设置，不把 `/app` 视为通用地址。

- 身份与技能：`SOUL.md`、`soul/HOME.md`、`soul/VOICE.md`、`soul/self/CURRENT.md`、`soul/skills/INDEX.md`。目录地图用于导航，不另立高于宪章的规范。
- 记忆：`memory/INDEX.md`，以及人物、群组、日志、事件、地图、意愿和任务。核实各存储是否存在及其格式；记忆不只是一个 Markdown 目录。
- 代码与 Forge：通过 `coding_session` / `coding_inspect` 获取真实 source root、工作副本和证据。不要误改旁边另一个 checkout。
- 文件交换：配置中的 `workspace`、收件和发件目录；使用可用的 `inbox_list` 和 `inbox_read`。通过真实可用且获准的 `send_file` 或 `send_media` 发送结果。
- 项目：使用可用的 `project` 创建，核对返回路径与仓库，不凭名称猜。
- 密钥和会话：`.env`、`.deploy.env`、`*.session` 及其他实际存储只用于获准用途，不可对外引用。

服务器与 Windows 可能是不同电脑。核实可用 scope 和真实任务根目录，不把远端磁盘当作本机目录的子目录。

先通过 `fs_ls` / `fs_search` 做小范围列举或搜索。修改前读文件、确定绝对路径并保存所需原始版本。临时解压内容应与运行代码和长期记忆分开。
