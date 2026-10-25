# Windows 源码启动与发布完整性

## 问题核对

收到的 Windows 修复报告指出三种启动问题。当前源码确认存在未保护的 `ISO_Left_Tab` 绑定、运行时依赖未完整检查、源码发布缺少托盘 ICO。另确认源码模式的自检只修改 `LOCALAPPDATA`，未改变实际数据目录，不能保证隔离。

`ISO_Left_Tab` 并非所有 Windows Tk 都不支持：本机 Windows/Tk 8.6.15 可以注册。修复按能力兼容，不据此降低 Tk 版本或删除反向键盘导航。报告中的其他电脑 Tk 8.6.12 故障没有在本机同版本重现，测试通过模拟明确拒绝该 keysym 的构建覆盖此分支。

## 修改

- `monitor/UI/settings_panel.py`：仅捕获可选 `ISO_Left_Tab` 绑定的 `TclError`，保留标准 `Shift-Tab`。
- `monitor/icon_asset/MonitorSolo.ico`：运行资源随源码一起发布，复用既有应用图标；没有复制个人数据。
- `monitor/app.py`：源码与冻结安装版统一读取运行资源中的 ICO，缺失时明确提示。不再依赖构建目录。
- 既有打包流程已经将构建 ICO 放入 `monitor/icon_asset`，无需改变安装包布局。源码新增的 ICO 与原构建图标字节一致；本次不发布构建缓存或安装程序。
- `monitor/runtime_check.py`、`run.py`：GUI 导入前检查实际导入能力及 `requirements.txt` 中的版本范围。冻结程序检查导入能力，不依赖源码 requirements 或第三方元数据。诊断命令明确使用当前解释器，支持路径空格和单引号。没有控制台时通过原生 Windows 消息显示。Hook 入口不运行 GUI 依赖检查。
- `start.cmd`：`.venv`、`py`、PATH 三种入口均走统一检查，转发参数并保留退出码。
- `monitor/release_check.py`、`monitor/src/paths.py`：自检显式使用临时数据目录；预置图片只能复制到指定临时目录；模拟 Agent、禁用手机/电脑服务与提醒监控。成功和失败时先清理窗口、数据库及独立启动项，再移除临时目录。取消对特定可选卡通图片的要求。

## 使用

请使用运行 SOLO 的同一解释器执行以下命令；不要混用裸 `pip` 与另一套 Conda/Python：

```powershell
& 'D:\Anaconda3\envs\chroma\python.exe' -m pip install -r .\requirements.txt
& 'D:\Anaconda3\envs\chroma\python.exe' .\run.py --check-runtime
& 'D:\Anaconda3\envs\chroma\python.exe' .\run.py --self-test .\new-self-test-report.json
```

示例路径应替换为自己的解释器。自检会临时注册并删除独立的 `MonitorSoloSelfTest-*` 当前用户启动项，以验证 Windows 集成；不会启用应用自身的开机自启。报告输出文件由命令指定。

## 验证要求

除本机完整源码回归，还从不含 `packaging/`、构建环境、测试和个人数据的源码副本运行自检。验证应包括缺少/过低依赖、导入失败、不支持 keysym、ICO 原生加载、临时数据清理、服务不启动、真实数据哨兵文件不变，以及失败路径清理。

GitHub 的源码上传清单必须包含 `monitor/runtime_check.py` 和 `monitor/icon_asset/MonitorSolo.ico`。不要将运行资源误认为安装包而过滤。新增测试保留本地，不上传测试生成的数据；验收结果在完成后记录于本文件。

## 2026-10-25 验收结果

- Chroma Python 3.10.20 / Windows Tk 8.6.15：最终启动与资源隔离相关测试 **13 passed**，包含模拟不支持 keysym、版本/导入诊断、冻结环境不依赖元数据、自检失败清理以及安装资源显式写入临时目录。
- SOLO 全量运行 **392 passed、2 failed**。失败分别为 Tk 读取 `clamTheme.tcl` 时初始化失败、窗口焦点断言未取得焦点；独立进程复测各 **1 passed**。复测中同进程连续创建 Tk 也曾出现 `init.tcl` 读取异常。没有跳过或删除这些测试，也不将全量结果写成一次性全部通过。
- 直接运行实际 GitHub 发布副本的 `run.py --self-test`：**7/7 passed**。不依赖 `packaging/` 或本机个人数据。独立源码副本测试另用已有设置和二进制哨兵确认自检前后内容不变。
- `start.cmd` 的 `.venv` 分支使用临时虚拟环境通过 `--check-runtime`，退出码 0；`py` 分支在缺依赖的解释器下显示其准确路径与安装命令，退出码 1。没有替用户安装依赖。
- 运行 ICO 与既有构建 ICO 的 SHA256 一致；自检实际加载了 Windows 托盘。没有重新生成安装包。
