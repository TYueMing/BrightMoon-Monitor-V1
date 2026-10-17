# 发行检查记录（含历史准备记录）

以下为 2026-10-16 的历史准备记录，范围仅 `monitor_solo/`。当时状态为发行材料已准备、未打包。2026-10-17 的实际发行验收见文末。

## 已执行的验证

- `verify_dependencies.py`：19 个 wheel、两个工具安装器、许可证来源及中文翻译的 SHA256 通过；wheel ZIP 内容、Windows AMD64 / CPython 3.13 兼容标签、Requires-Python、运行和构建依赖闭包、固定 lock、发行资源及九尺寸 ICO 检查通过。
- pip `install --dry-run --ignore-installed --no-index --require-hashes --platform win_amd64 --python-version 3.13 --abi cp313`：从本地 wheelhouse 成功解析全部 19 个包，未安装。
- `verify_signatures.ps1`：Python 3.13.16 与 Inno Setup 6.7.3 官方安装器签名均为 Valid；发布者分别为 Python Software Foundation、Pyrsys B.V.。Inno 安装器 SHA256 另与官方 GitHub release digest 对比一致，Python 安装器来源为 python.org。
- Python 源码 AST、PyInstaller spec 语法、构建 PowerShell 脚本解析、DeepSeek JS 语法检查通过。**没有运行 PyInstaller Analysis、bootloader、ISCC 或安装程序；语法检查不代表冻结和安装验收。**
- Solo 原有 190 个测试及新增 6 个安装版目录测试，共 **196 个不同用例已验证通过**。具体过程：首次整套运行包含 195 个用例，193 passed / 2 skipped；两个因当前 Conda/Tk 初始化异常跳过的原生测试，在各自独立进程补跑均通过。之后新增安装目录变更后的历史保存用例，安装版目录六项测试一起运行，6 passed。
- 两个补跑用例为 `test_bad_read_and_stopped_task_hide_after_sixty_seconds_without_stopping_agent[invalid]`、`test_native_missing_selection_and_drag_do_not_leave_orphan`。没有通过删除测试或吞掉异常把跳过视为成功。
- DeepSeek 桥 `node --test monitor/deepseek_monitor/tests/bridge.test.mjs`：**12 passed**，使用本地测试实例与模拟事件，没有连接真实 Harness 或提交真实授权。
- 新增测试覆盖源码模式目录不变、安装版用户目录、中文路径、单实例互斥、默认卡通不覆盖用户选择、不复制私人文件、缺失 LocalAppData 的后备路径、应用升级后的历史持久化、发行资源隐私排除。
- 对照之前主项目基线：主 `monitor/` 共 2,274 个文件哈希 **0 变化**。对照本轮修改前 Solo 基线：原有 `data/` 文件 **0 变化**。不存在 `build/`、`dist/`、`.build-venv/`，未生成应用或安装 EXE。

## 运行环境和限制

源码测试使用机器已有的 Python 3.10.20 / Windows 环境；下载目标是官方 Python 3.13.16 x64。后者尚未安装运行，当前的 wheel 兼容性验证是元数据/哈希和离线解析验证，不能代替目标 Python 下的运行测试。

将来的 `prepare_build.ps1` 验证官方解释器和 Tcl/Tk；`build.ps1` 在目标环境重新执行测试，并在存在失败或跳过时停止。正式发行仍须完成指南中的干净机器启动、DLL/Tcl 路径、实际来源协议、音频、安装、升级、卸载与资源授权核对。

## 代码变更范围

- `monitor/src/paths.py`：新增源码/安装模式路径与默认资源初始化。
- `monitor/settings.py`、`monitor/src/lifecycle.py`、`monitor/opencode_monitor/config.py`：统一可写配置和锁目录。
- `monitor/app.py`：取得单实例锁后初始化安装版默认卡通。
- `monitor/deepseek_monitor/bridge_client.py`、`bridge/index.mjs`、`install_bridge.ps1`：安装版共享用户桥运行目录，源码路径保持兼容。
- `tests/test_release_layout.py`：新增六个安装目录/资源边界用例。
- `.gitignore`、`README.md`：发行准备入口和缓存/构建输出规则。
- `packaging/`：离线缓存、锁文件、来源清单、图标、默认资源、许可证、构建配置与脚本。
- `docs/WINDOWS_PACKAGING.md`、`docs/INSTALLED_USER_GUIDE.md`：构建者和安装版用户说明。

除独立 Solo 外未改动其他项目功能；未修改用户配置、任务历史、个人壁纸，未全局安装任何库或工具。


## 正式构建与本机验收（2026-10-17）

已生成 Windows x64 0.1.0 应用、安装程序及含安装说明和校验值的 ZIP。构建 Python 和 Inno 工具安装在 Solo `.build-tools/`，Python 环境在 `.build-venv/`，未修改全局 PATH。官方工具签名、19 个依赖 wheel 的哈希、闭包和 88 个发行资源校验通过。托盘与自启使用标准库 ctypes/winreg，没有新增运行库。

- 官方 CPython 3.13.16 下 **285 passed / 0 failed / 0 skipped**。报告 `.artifacts/release-tests.xml`。
- 冻结 EXE 的运行时、图片/声音资源存在性、设置持久化、原生托盘、窗口 × 保持灵动岛、实例唤回、自启注册/移除、真正退出六组自检通过：`.artifacts/frozen-smoke.json`。
- 实际安装包在本机中文/空格路径安装；确认桌面快捷方式、卸载程序和 Windows 卸载登记生成。
- 安装后的 EXE 自检通过；使用独立数据目录、仅含 Windows 系统目录的 PATH，移除 Python/Conda/Tcl 环境变量后正常启动。
- `--background` 启动不弹出控制面板；重复启动唤回原窗口、不产生第二实例；点击 × 隐藏但进程保持运行。
- 运行中升级会先正常退出旧实例；任务保留时间、个人数据标记和开机自启命令保留。
- 运行中卸载会退出 Solo，删除桌面快捷方式、程序、卸载登记与自启项；静默卸载保留测试用户数据。
- 最终安装验收报告 `.artifacts/installer-acceptance-03/validation.json`，状态 passed；该目录保留安装、升级和卸载日志。测试安装已卸载，临时自启值已移除。
- ZIP CRC 与其中安装程序 SHA256 均已校验，只含安装程序、安装说明、SHA256SUMS，没有账号、会话、私人壁纸、设置或任务库。

本轮修复了原生托盘回调直接调用 Tk 导致的线程状态崩溃、壁纸临时文件名在长路径下超限、原有安装脚本说明入口引号错误，以及卸载注册表视图明确化。挂件延迟启动测试曾在整套执行中超时，原断言未放宽；单独四种场景及最后一次完整测试均通过。测试脚本对卸载启动器提前返回的情况，改为等待程序、快捷方式和注册表清理全部完成。

可查看 `preview/solo-startup-settings.png`，展示个性化 → 通用的开机自启与后台运行说明。

### 实际验收边界

未在独立干净 Windows 虚拟机中验收，未执行系统重启/重新登录，不声称已覆盖所有 Windows 版本、N/KN 媒体组件、ARM64、不同 DPI 或不同 Agent 服务版本。本次验证自启注册和后台启动机制，没有重启用户电脑；未执行真实 Agent 审批，没有用真实账号/API。交互卸载的“删除个人数据”选项未对真实用户数据执行，已验证默认保留路径。声音资源有打包校验，未以主观听感验收所有提示音。安装程序未签名。

### 产物

- 安装程序：`dist/installer/MonitorSolo-0.1.0-windows-x64-setup.exe`，21680870 bytes。
- 安装程序 SHA256：`74af29cd2b961d5f965ccc0b5ab6988f0ffd58ef5a2752c1718e100baf8aecb7`。
- 分发 ZIP：`dist/MonitorSolo-0.1.0-windows-x64.zip`，21172320 bytes。
- ZIP SHA256：`49089c56b03364ff2716423c64835fdbada50a2a6d0df9fb4d3742f79f9384d4`。
