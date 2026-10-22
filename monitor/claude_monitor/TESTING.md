# Claude 监控验证记录

日期：2026-10-22。

## 2026-10-22 发布副本复验

- 独立导出仓库全套 Python 3.13.16：**368 通过，0 失败，0 跳过**。
- Python 3.10.20 Claude 专项与启动恢复：**28 通过，0 失败，0 跳过**。
- Node 交互桥与远程 UI 模型：**17 通过，0 失败，0 跳过**。
- 相比下面首次实现记录，加入启动快照、缺少时区依赖及恢复流程的回归。发布只包含源码与文档，不包含测试运行数据或新安装包。

## 首次实现的自动化结果

- SOLO 全套：365 项收集，364 通过、1 跳过、0 失败。
- 跳过项是 `test_island_recovery.py::test_bad_read_and_stopped_task_hide_after_sixty_seconds_without_stopping_agent[paused]`，原因是当前测试进程的 Tcl/Tk 初始化不可用。随后独立进程补验：1 通过、0 跳过。
- Claude 新增专项：25 项，在最终全套运行中均通过。
- Python 编译检查通过。
- 独立复制 SOLO 后导入所有模块、来源与详情面板注册检查通过，未依赖 BrightMoon 主工程。

机器可读结果保存在 SOLO 本地 `.artifacts/claude-final-tests.xml` 与 `.artifacts/claude-supplement-tests.xml`；这些测试数据不应包含在源码发布中。

## 验证的实际链路

1. 独立 Python Hook 子进程接收模拟官方 stdin JSON，产生本地事件和授权请求。
2. ClaudeSource 读取并投影到统一任务，原生 Tk 灵动岛显示请求。
3. SourceHub 路由同意/拒绝，事务邮箱仅接收一次决定。
4. Hook 消费决定并从 stdout 输出官方 `hookSpecificOutput` JSON。
5. 关闭监控、超时、过期、错误任务或版本、重复点击均不能产生错误批准。

另验证了阶段与工具结果、模型与环境字段、子 Agent、压缩次数、任务摘要、连续追问、真实继续执行、进程退出、历史不重播、并发写入、设置安装修复卸载、原生 UI 展开收起与授权框稳定、手机只读投影和已有多电脑权限投影。

## 实际宿主验收尚待进行

自动化使用隔离 Claude 配置目录和模拟 Hook 载荷，没有修改真实 Claude 配置，没有使用账户/API，没有在已登录的 Claude Desktop 中运行真实模型任务。Code 内置版本和组织策略可能影响 Hook 是否加载。请按 README 在设置中安装后，新建 Code 会话完成真实读文件、写文件、执行命令、授权、拒绝、中断与并行任务验收。

首次实现时未打包、未发布；本次源码上传检查见 `docs/SOURCE_REVIEW.md` 的 2026-10-22 章节。安装版仍须另行构建验收。
