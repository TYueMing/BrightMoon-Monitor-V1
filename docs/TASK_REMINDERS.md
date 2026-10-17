# Solo 本地任务与灵动岛提醒

2026-10-17 补充：任务视图切换已改为纯图标，无底色色块。监控页新增默认关闭的「灵动岛常驻」，空闲展开时会展示下一条实际提醒时间（包含提前量与延后）；任务本身的到期、去重和声音语义不变。完整设计及测试见 [常驻与空闲状态](RESIDENT_ISLAND.md)。

## 界面设计

侧栏上方依次为「监控」「任务」，「个性化」固定在侧栏底部。任务页沿用主页照片、面板颜色、透明度和文字对比度，顶部是「任务」标题与「＋ 新增任务」，右侧图标切换列表/网格：列表显示 grid.png，网格显示 navigation.png，选择保存为 task_view。列表每页 4 条，显示标题、内容摘要、下一次时间、重复规则和提前量；网格每页 6 张，只显示标题、时间、编辑和开启。删除位于编辑弹窗，只删除本地待办，绝不终止 Agent。

新增/编辑弹窗使用与主页相同的壁纸、面板颜色和透明度，输入区保持可读。字段：标题（1–40 字符）、内容（最多 4000 字符）、首次日期、时间（HH:MM）、星期一至星期日复选框、间隔重复开关与小时输入、提前提醒分钟数（整数 2–10，默认 5）。每周和间隔互斥，两项都不选为单次。勾选间隔会清空星期，勾选任一星期会关闭间隔；存储层也拒绝同时选择。底部删除、取消、保存使用 6px 圆角按钮，保留键盘焦点和 Enter/Space 操作。输入错误在弹窗内提示且不丢失已填写内容。

只有一条待办且没有其他可见监控任务时：第一态左侧显示 20×20 逻辑像素的 LOGO_EXE.png，标题居中、最多 5 字且按可用宽度截断；第二态标题左侧同样增加 20px Logo，显示核心内容（最多 3 行）、预定时间，并排显示「忽略 · 5分钟后」「已知晓」；第三态保留操作按钮，增加重复规则、首次日期、提前量、本次自动收起时间和下次计划时间。长内容在任务编辑弹窗查看全文，灵动岛不无限扩高。

混合监控时使用 `monitor/icon_asset/LOGO_EXE.png`。待办按本轮提醒开始时间、预定时间、任务 ID 排序并置于 Agent 前面，总计最多 5 个任务圈，全部直接可点击，不显示「…」。Agent 未展示的任务继续后台监控。超过 5 个待办时前 5 个优先，其余在提醒有效期内候补，并在任务列表可见。显示顺序与详情焦点分开：仍有 Agent 槽位时保留正在处理的授权或用户已选详情；5 个槽位均被待办占用时，只能展示待办，Agent 继续后台监控。待办退场后自动恢复 Agent 槽位，不取消它们的执行或授权。

## 时间与交互语义

- 提前量 N 同时决定初始提醒窗口：从预定时间前 N 分钟出现，到预定时间自动收起；N 为 2–10 的整数。
- 「忽略」隐藏当前提醒，5 分钟后同一本次任务再次提示，展示 N 分钟；允许再次忽略。即使超过原预定时间也尊重这次明确的延后操作。
- 「已知晓」结束本次实例，不再提醒本轮；下一次所选星期或小时周期仍正常生成新实例。单次任务留在列表供编辑/删除。
- 间隔重复输入 1～8760 整数小时，以首次日期时间为锚点，按实际经过的秒数加 N×3600 计算；跨午夜不重置，同一天可有多个实例。忽略只移动当前实例，不改变下次计划。长时间关闭/休眠直接算当前下一次，不枚举补发错过的周期。
- 每次初始提醒/延后再提醒播放所选音频 3 遍，串行播放、不叠音；个性化「声音」页分别设置 Agent 完成音（1 遍）和待办提示音（3 遍），各自开关、选择、试听、持久化。点击已知晓不额外再播完成音。
- Solo 关闭时不调度，也不唤醒关机/睡眠电脑；恢复运行时仍在有效窗口的提醒可以显示，已完全错过的窗口不会补发一串历史提醒。星期重复按本地日历计算；小时重复按实际时间间隔计算，所以夏令时切换附近的墙上时刻可能变化。
- 「监控 → 任务提醒」默认开启；关闭立即撤下待办、取消声音并拒绝旧卡片操作，不更改任何任务的 enabled、revision 或已知晓状态。关闭期间仍维护到期状态但不领取声音，重新开启只展示当前有效实例；已领取过声音的本轮不补响。

## 数据结构与持久化

独立 `data/tasks.sqlite3`，安装版同样位于用户 LocalAppData 的 Solo 数据目录，不需要登录，不修改 Agent 数据。SQLite 使用事务与外键，schema 版本检查，显式失败提示，不在损坏数据库上悄悄创建空记录。

`tasks` 保存 id、title、body、start_date、time_of_day、weekdays(JSON 0–6)、interval_hours（0 为关闭）、lead_minutes、enabled、revision、created_at、updated_at。改变定义/单任务开关时 revision 增加，并撤销旧版未结束实例，防止旧卡片操作到新定义。schema 2 从旧版 1 通过事务新增 interval_hours 默认 0，不改已有定义、实例 ID、已知晓或声音领取记录。

`occurrences` 保留 task_id + revision + due_date 唯一键；单次/星期的 due_date 为本地日期，小时重复为 interval:<从首次时间起的周期序号>，避免同一天多次提醒撞键。其余为 due_at、remind_at、expires_at、state(active/snoozed/acknowledged/expired)、cycle、sound_cycle。忽略更新提醒窗口并递增 cycle；已知晓与播放领取记录都在事务中保存。UI 操作携带实例 ID 和 cycle，过期/重复/旧界面操作拒绝，不发送到 Agent。提醒实例由自己的存储管理，不套用 Agent 的失联或完成保留时间。

提醒音采用先持久领取、再异步播放，避免轮询/重启重复响；这保证至多领取一次，崩溃发生在落盘后实际出声前可能漏播，无法承诺声音与数据库的跨进程精确一次。关闭声音时当前周期也记为已处理，重新开启不补播旧提醒。忽略、知晓、关闭开关、删除或退出会取消未结束的提醒音。

## 执行与验证

1. 完成本地任务校验、日历调度和 SQLite 事务，测试跨午夜、星期顺序、单次/每周、休眠错过、重启、重复点击、编辑撤销、数据错误。
2. 通过本地快照合并和命令路由接入灵动岛，Agent 适配器不感知待办。提醒音独立异步播放器，测试每轮三遍与取消。
3. 实现侧栏、分页任务页、背景一致的编辑弹窗、独立详情面板和混合任务头部。
4. 使用临时数据/模拟 Agent 验证五任务限额、全部圈可点击、授权不串线、三态、长文本、深浅色背景、声音选择保存及关闭清理。预览只捕获测试窗口，存入 `preview/`。
5. 完整 Solo 回归及主 `monitor/` 文件哈希核对，不运行真实 Agent 操作、不修改个人任务和设置。

## 已实现的代码边界

- `monitor/UI/tasks/page.py`：任务列表、按下一次时间排序、分页、按实际像素宽度截断，及个性化的声音页面。
- `monitor/UI/tasks/editor.py`：跟随主页背景的新增/编辑弹窗、输入校验、星期选择、删除确认；保存失败保留输入。
- `monitor/UI/tasks/detail_panel.py`：待办专用第二、第三态，摘要最多 3 行，忽略/已知晓命令只操作本地实例。
- `monitor/tasks/schedule.py`：本地日期与星期计算、参数校验和下次计划；不依赖 LLM、网络或 Agent 状态。
- `monitor/tasks/store.py`：SQLite 任务及实例事务、版本/周期校验、延后与去重。暂停、编辑和删除撤销旧卡片。
- `monitor/tasks/service.py`：向通用 UI 提供 `solo_task` 快照；前 5 个有效提醒才领取声音。所有 Agent 来源关闭时同样可提醒，待办由自己的任务监控开关控制。
- `monitor/music/play/reminder.py`：独立后台线程串行播放三遍，支持撤销，不阻塞 UI，也不改变原 Agent 完成音逻辑。
- `monitor/app.py`：合并来源与本地快照、路由本地命令；某个 Agent 读取失败不会阻断本地提醒。
- `monitor/UI/task_selection.py` / `task_strip.py` / `task_lifetime.py`：五槽优先级、标题胶囊和独立到期规则；已有 Agent 专属阶段/详情不移入待办面板。
- `monitor/settings.py`：`task_reminder_sound` 和 `task_sound_enabled` 独立持久化；沿用现有 MP3 动态扫描、试听与播放后端，无新增第三方运行库。

源码版数据为 `monitor_solo/data/tasks.sqlite3`；安装版为 `%LOCALAPPDATA%/MonitorSolo/data/tasks.sqlite3`。两者都由 Solo 自己的数据目录决定，没有 BrightMoon 账户依赖。提醒记录不会写入 Agent 的 `display_history.sqlite3`。安装包资源白名单已包含 `LOGO_EXE.png`；新模块通过 Python 导入收集，不把开发者个人任务打进安装包。

## 最终核验记录（2026-10-16）

- Python 3.10.20 / Windows 原生 Tk 环境，完整 Solo 回归 **234 passed，0 skipped，0 failed**。本次任务专项共 **32** 个用例，覆盖跨午夜、单次/每周、重启、已知晓、延后五分钟、时间回拨、数据库损坏/占用恢复、旧卡片拒绝、声音领取/三遍/取消、五槽排序、UI 表单保存与独立路由。
- 使用临时用户目录和模拟 Agent 完成原生窗口截图，检查列表、背景适配、三态、五圈混排、声音配置。测试静音，声音三遍及中途取消通过替代音频后端计数验证；没有向真实 Agent 发审批或停止请求，没有把测试任务写入个人数据目录。
- 依赖/资源验证通过：19 个离线 wheel 的版本兼容、依赖闭包与 SHA256，2 个工具安装器哈希，84 个资源及 9 种图标尺寸均通过。没有打包或增加依赖。
- 与已有基线对照，主 BrightMoon 的 **2274 个文件哈希均未改变**。
- 回归报告：`monitor_solo/.artifacts/tasks-final-regression.xml`。测试命令：`python -m pytest tests -q -rs -p no:cacheprovider`；预览命令：`python tests/render_tasks_preview.py`。

## 界面细化核验（2026-10-16）

新增 20 项测试，完整回归 253 passed、1 skipped；跳过项为本机 Tk 初始化偶发失败，独立进程补跑该项及两个编辑器检查，3 passed。累计 **254 个不同用例均已通过**。报告为 `.artifacts/task-refine-final.xml` 和 `.artifacts/task-refine-supplement.xml`。

本次重点覆盖旧库真实结构迁移、同日多轮与跨午夜间隔、重启去重、忽略不改变周期、星期/间隔互斥、监控开关立即撤销声音及旧操作、列表/网格分页与编辑、6px 圆角和键盘保存、挂件左右 20px 边界/预览/持久化。浅色、深色、照片背景原生窗口均生成预览检查；无真实 Agent 命令。依赖校验为 19 个 wheel、86 个资源（含新增的 grid.png 和 navigation.png）、2 个工具安装器均通过；主 BrightMoon 2274 个基线文件未改动。

### 可审查的界面预览

- [任务列表](../preview/solo-tasks-list.png)
- [新增/编辑弹窗](../preview/solo-task-editor.png)
- [第一态：Logo 与居中标题](../preview/solo-task-capsule.png)
- [第二态：内容与两项操作](../preview/solo-task-progress.png)
- [第三态：重复与时间详情](../preview/solo-task-details.png)
- [待办与 Agent 的五圈混排](../preview/solo-task-mixed-five.png)
- [独立提示音设置](../preview/solo-task-sounds.png)
- [网格视图](../preview/solo-tasks-grid.png)
- [深色编辑弹窗与间隔选项](../preview/solo-task-editor-dark.png)
- [任务监控开关](../preview/solo-task-monitor-toggle.png)
- [外观页](../preview/solo-appearance-refined.png)
- [挂件左侧位置](../preview/solo-rope-position-left.png)、[居中位置](../preview/solo-rope-position-center.png)、[右侧位置](../preview/solo-rope-position-right.png)
