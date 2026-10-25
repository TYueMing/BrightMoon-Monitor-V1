# Monitor Solo：Windows 安装版准备与构建指南

准备日期：2026-10-16。目标：**Windows 10 1809+ / Windows 11，原生 x64**。当前源码仍支持 Python 3.10+；发行构建固定使用 **官方 CPython 3.13.16 x64**。这不是绑定开发者的 Chroma/Conda 环境。

## 1. 现在准备到了什么程度

2026-10-17 已完成官方 Python 3.13.16 独立构建环境、PyInstaller 冻结和 Inno Setup 安装程序编译，并生成可分发 ZIP。285 项自动测试、冻结应用自检、本机安装/升级/卸载验收通过。未启动或配置真实 Agent；独立干净虚拟机验收仍未执行。准确产物及 SHA256 见 `dist/release-manifest.json`。

以后发行给用户的文件是 `MonitorSolo-<版本>-windows-x64-setup.exe`。用户安装后点击桌面或开始菜单图标启动，不需要在终端输入命令。桌面图标和程序 EXE 使用现有 `monitor/icon_asset/LOGO.png` 转换得到的多尺寸 ICO；原图没有改动。源码版的 `start.cmd` 仍然是源码启动入口，不能把它当作可独立运行的安装版。

统一品牌图源为 `monitor/icon_asset/LOGO.png`。执行 `python packaging/prepare_assets.py --icon-only` 可生成完全相同的 `monitor/icon_asset/MonitorSolo.ico`（源码运行与托盘）和 `packaging/assets/MonitorSolo.ico`（EXE 与安装程序），不复制个人数据。`build.ps1` 和直接运行 `MonitorSolo.spec` 都会自动生成图标。安装器、桌面/开始菜单快捷方式及卸载列表沿用现有 ICO/EXE 引用。已经生成的 EXE、安装包不会随 PNG 改动自动更新，需要重新构建后才会显示新图标。

采用 **PyInstaller onedir + Inno Setup**：前者把程序和 Python/Tk/DLL 集合放入一个目录，后者把目录制作为一个可安装 EXE。用户只收到一个安装程序；安装后是正常的多文件应用目录。不采用每次启动都解压运行环境的 onefile 模式。

## 2. 什么需要随应用分发

- **Python 解释器、标准库中实际用到的模块和 DLL**：由 PyInstaller 从专用构建环境收集。包括 sqlite3、ctypes、ssl、urllib、json、threading 等，不需要分别 pip 安装。
- **Tk/Tcl**：Solo 主控制面板、灵动岛和挂件使用原生 Tk。必须使用带 Tcl/Tk 的官方完整 Python；下载的不是缺少 Tk 的 embeddable ZIP。PyInstaller 的 Tk hook 收集对应 DLL 和脚本库。
- **Pillow 12.3.0**：Logo、壁纸、图像合成、悬挂卡通、ImageTk。
- **zstandard 0.25.0**：读取压缩的 Agent 会话；已下载 cp313/win_amd64 原生扩展 wheel，并显式保留 `zstandard.backend_c`。
- **websocket-client 1.9.2**：Codex 的本地实时连接。
- **应用资源**：来源 Logo、提示音、OpenCode 的提示音/图片、九张默认卡通、可选 DeepSeek 交互桥、用户使用说明和第三方许可证。

Python 安装器和 wheel 本身**不会**被装进用户应用目录，它们只是构建材料。最终包含的是收集后的运行环境和模块。

Windows 的 user32/gdi32/winmm、系统字体和 PowerShell 属于操作系统能力，不从开发电脑随意复制。音频使用 Windows 自带的 MCI MP3 播放能力，不需要额外下载 ffmpeg、播放器、pygame 或音频 Python 包。Windows N/KN 等缺少媒体组件的版本需单独验证，不能据普通 Windows 的结果承诺提示音可用。

## 3. 什么不需要携带，哪些条件仍由用户提供

- 不需要 BrightMoon 后端、登录账户、Chroma/Anaconda、浏览器引擎、Node.js 或 Electron 来运行 Solo 本身。
- Codex、OpenCode、DeepSeek Harness 是被监控的独立应用，用户仍要自行安装、配置并运行；Solo 不含它们的模型、API key、账户或 Agent 本体。
- 日志监控与交互授权是不同能力。能读取日志不等于能授权；来源的实时协议可用、且连接到正在运行的同一服务时，才提供对应按钮。
- Harness 可选桥由 **Harness 自带 Electron/Node** 运行，其 package.json 没有外部 npm 依赖。不需要给 Solo 再下载一套 Node。
- pytest、PyInstaller、其 hooks 和 Inno Setup 只供开发者测试、构建使用，最终应用不需要这些工具。
- 不自动安装外部 Agent 插件、不开放远程监听端口、不修改系统 PATH、不自动启用开机启动。

## 4. 构建材料与目录

以下为构建机准备完成后的目录，不表示 GitHub 克隆包含全部下载缓存。公开源码排除了 `packaging/vendor/`、`.build-tools/` 和 `.build-venv/`，开发者需另行准备；源码运行见 [源码指南](SOURCE_GUIDE.md)。

```text
monitor_solo/
  packaging/
    release.json                    产品名、发行占位版本 0.1.0、构建 Python
    runtime.in / build.in            顶层依赖版本
    requirements-runtime.lock       三个运行库，固定版本和 SHA256
    requirements-build.lock         19 个 wheel 的完整固定清单
    dependency-manifest.json        下载 URL、版本、大小、SHA256、目标平台
    vendor/
      wheels/                       运行库、构建库、测试库的离线 wheel
      tools/
        python-3.13.16-amd64.exe     官方完整 Python 安装器，含 Tk/Tcl
        innosetup-6.7.3.exe          Inno Setup 开发工具安装器
    assets/
      MonitorSolo.ico               16/20/24/32/40/48/64/128/256px 图标
      rope/                         九张明确选择的默认图片及原来源说明
      installed-layout.json         仅在安装版加入的桥接目录标识
    languages/ChineseSimplified.isl 简体中文安装界面
    licenses/                      原始依赖许可证及 Python 官方 SBOM
    THIRD_PARTY_NOTICES.md          第三方组件与图像来源说明
    MonitorSolo.spec               PyInstaller 资源白名单、图标、无控制台配置
    MonitorSolo.iss                用户级安装、快捷方式、卸载、升级配置
    release_files.py               资源清单与 Windows 文件版本信息
    download_dependencies.py       在线重新下载材料；不安装、不打包
    prepare_assets.py              更新发行图标与九张默认卡通
    verify_dependencies.py         离线哈希/依赖完整性/平台/资源检查
    verify_signatures.ps1          检查两个安装器的签名及发布者
    prepare_build.ps1              以后创建专用离线构建环境
    build.ps1                      以后运行测试、冻结程序、可选生成安装包
  docs/
    WINDOWS_PACKAGING.md            本说明
    INSTALLED_USER_GUIDE.md         安装版用户说明，会随应用携带
```

下载缓存为 **55,050,395 字节，约 52.50 MiB**（19 个 wheel + 两个安装器；不含图片和许可证）。构建锁包含部分旧 Python 的兼容辅助库，校验器按目标 Python 3.13 检查实际依赖条件；这些是开发环境材料，不因此进入发行程序。

依赖从 PyPI 官方源取得，逐文件比对 PyPI SHA256。Python 从 python.org 下载；Inno 从 jrsoftware 官方仓库的 `is-6_7_3` release 下载，并核对 release 公布的 SHA256。两个 EXE 的 Authenticode 状态均为 `Valid`，发布者分别为 **Python Software Foundation** 和 **Pyrsys B.V.**。下载清单保留来源，支持离线复核。

`vendor/` 被 Git 忽略，避免把二进制缓存提交到源码历史；它们**确实已在当前 Solo 文件夹中**。复制整个文件夹可以携带它们，只做 Git clone 不会得到缓存。需要离线转移时，请额外复制 `packaging/vendor/` 和整个 packaging 配置/资源目录。

## 5. 安装目录与用户数据

安装程序默认按当前用户安装，无需管理员权限：

```text
%LOCALAPPDATA%\Programs\MonitorSolo\
  MonitorSolo.exe
  _internal\                       Python/Tk/DLL、模块和只读资源

%LOCALAPPDATA%\MonitorSolo\data\
  settings.json                    来源、外观、声音等设置
  display_history.sqlite3          任务显示去重记录
  backgrounds\                    用户导入壁纸
  rope\                           首次启动补齐的默认卡通与自定义卡通
  runtime\                        单实例锁
    deepseek\                     安装版交互桥描述文件与桥安装缓存
```

`monitor/src/paths.py` 统一区分源码版与冻结版，**只在 `sys.frozen` 的安装版使用 LocalAppData**。源码版继续使用本项目 data/。新路径也覆盖设置、OpenCode 兼容配置、单实例锁和 DeepSeek 桥，不能让这些模块继续写 `_internal`。

首次运行只补齐发行清单中的默认卡通，已有同名文件不覆盖；不会导入开发者设置、壁纸、任务历史。升级使用同一个 AppId 和用户目录，保持设置与已显示任务记录；正常卸载保留用户数据。应用安装目录可以更换，用户数据目录不跟着版本变化。桌面、开始菜单快捷方式都指向 EXE，并设置正确工作目录。

资源白名单不收集整个 `monitor/` 或 `data/`：不包含个人壁纸、账号、会话、API 配置、运行描述 token、SQLite 数据库、测试、预览图和离线下载缓存。九张默认卡通已复制到单独的 `packaging/assets/rope/`，避免递归复制个人 data。

源码版与安装版不会自动共享设置。需要迁移自己的配置时，先退出两者，备份后把自己的 data 内容复制到安装版用户目录；不要将个人 data 放入面向其他用户的安装包。

## 6. 现在可以做的检查：不会打包

在 `monitor_solo` 目录打开 PowerShell，用现有 Python 3.10+ 执行：

```powershell
python -I .\packaging\verify_dependencies.py
& .\packaging\verify_signatures.ps1
```

第一个命令只读取已下载的文件，验证 SHA256、ZIP 完整性、Python/Windows wheel 标签、依赖闭包、锁文件和资源。第二个命令检查安装器的 Windows 签名；Windows 验证证书信任/吊销时可能需要网络，它不是下载或安装操作。

`requirements-*.lock` 是 **cp313/win_amd64 发行专用**，不要直接装进当前 Python 3.10 的 Chroma 环境。源码开发继续使用根目录 requirements.txt。也不要把依赖 lock 与任意版本的全局 Python 混用。

## 7. 以后真正构建时怎么操作（本次未执行）

### 7.1 一次性准备构建电脑

1. 使用 `packaging/vendor/tools/python-3.13.16-amd64.exe` 安装专用官方 Python。保留 **pip、Tcl/Tk、venv/标准库**，安装到当前用户可写目录即可，不必加入 PATH，不使用 Chroma。安装器已下载，不需要另找下载网站。
2. 只有要生成安装 EXE 时才安装 `innosetup-6.7.3.exe`；只验证 onedir 程序时不需要 Inno。记下其 `ISCC.exe` 完整路径。
3. 按自己的实际路径执行以下命令；参数不是某台开发电脑的固定路径：

```powershell
& .\packaging\prepare_build.ps1 -PythonExe 'C:\实际路径\Python313\python.exe'
```

脚本核对 Python 版本/架构、校验缓存及签名、建立 **Solo 内部 `.build-venv/`**，通过 `--no-index --require-hashes` 安装完整固定依赖，再检查 Tcl/Tk、Pillow、zstd、WebSocket 与 SQLite。不会调用 PyInstaller 或 Inno，不修改系统 PATH，不装入现有 Conda。

如果 PowerShell 执行策略阻止脚本，且已检查脚本内容，可对该次调用使用 `powershell -NoProfile -ExecutionPolicy Bypass -File ...`；无需修改机器的长期执行策略。

### 7.2 先构建可运行目录并验证

```powershell
& .\packaging\build.ps1
```

这一步才会运行回归测试和 PyInstaller，产生 `dist/MonitorSolo/MonitorSolo.exe` 及 `_internal/`。运行时需要整个目录，不能只复制 EXE。构建过程不启用 UPX，不出现控制台黑窗，使用原有 Logo 和统一版本信息。测试报告保存到 `.artifacts/release-tests.xml`；任何失败或跳过都会中止构建。原生界面测试跳过时，先检查专用 Python 的 Tcl/Tk 和桌面环境，再重新验证，不把 pytest 退出码 0 当作所有桌面功能已验收。

使用干净 Windows 测试账户检查下一节项目后，再生成安装程序。不要向最终用户分发尚未验收的构建。

### 7.3 再生成安装器

```powershell
& .\packaging\build.ps1 -CreateInstaller -InnoCompiler 'C:\实际路径\Inno Setup 6\ISCC.exe'
```

该命令会重新运行测试和冻结，再编译安装器到 `dist/installer/`。安装器提供简体中文/英文界面，桌面快捷方式默认勾选，并创建开始菜单入口、Windows 卸载记录，安装后可选择启动。构建后发布的只有该 setup EXE。以后修改版本只需调整 `packaging/release.json`；直接手工调用 ISCC 时须同时传 `/DAppVersion=...`，推荐使用脚本保持版本一致。

当前尚未配置开发者代码签名证书。正式发行可在生成目录后签名 `MonitorSolo.exe`，再编译、签名 setup EXE；不要把证书私钥写入仓库。未签名发行可能出现 Windows 未知发布者/SmartScreen 提示，这不等于缺依赖。

### 7.4 更新依赖或图片

普通构建完全使用当前下载缓存，不访问 PyPI。只有主动维护版本时才更新 `runtime.in` / `build.in`、下载脚本中的固定工具版本和哈希、release.json，再在空的缓存目录执行下载脚本（先备份旧缓存，不要混放多个版本），检查并提交新的 lock 和 manifest。

```powershell
python .\packaging\download_dependencies.py
python .\packaging\prepare_assets.py
python -I .\packaging\verify_dependencies.py
```

前两条仅用于维护发行材料；不会构建应用。新增提示音/来源 Logo 会按指定资源子目录的 MP3/PNG 清单收集；默认卡通是明确的九张，如有变化同步 `prepare_assets.py` 与 `release_files.py`，避免把用户后来添加的私人图片自动放进发行包。

## 8. 安装版 DeepSeek 交互桥

不改变已有的人工启用方式。若需要授权交互，先完全退出 Harness，然后使用安装版自带脚本：

```powershell
$bridgeScript = Join-Path $env:LOCALAPPDATA 'Programs\MonitorSolo\_internal\monitor\deepseek_monitor\install_bridge.ps1'
powershell -NoProfile -ExecutionPolicy Bypass -File $bridgeScript -HarnessDirectory 'D:\实际路径\DeepSeek Harness'
```

若安装时改了目录，应按实际目录修改。桥使用 `installed-layout.json` 判别安装版，JS 服务、Python 客户端和桥安装脚本统一把运行数据放在 `%LOCALAPPDATA%\MonitorSolo\data\runtime\deepseek`。源码版没有该标识，保留原路径。桥包本体只读；不将 token 或运行状态打包。

桥适配 Harness 0.2.0-rc.2，并不承诺任意未来版本。曾安装桥的用户，在卸载 Solo 前先退出 Harness，再运行同一脚本加 `-Uninstall`，移除它的链接。常规 Solo 卸载不擅自修改其他应用的插件配置。

## 9. 发布前必须实际验证的内容

当前只是发行准备，以下安装验收需要**以后真的构建出安装程序**才能做，不能用源码测试替代：

1. 干净 Win10/Win11 x64，没有 Python、Conda、Node、开发目录；普通用户离线安装、首次启动、重启后桌面/开始菜单再次启动。
2. 中文用户名、带空格路径、安装目录只读、多个 Windows 用户，各自设置和任务记录互不串用；重复点击图标不出现两个岛。
3. 三种来源分别检查只读日志；实时协议可用时验证真实审批/拒绝/回答/停止回执。未连接时不显示虚假的授权能力。
4. PNG/JPG/WebP 壁纸、历史壁纸、全部默认卡通、挂件点击/动画、三形态图像展开、提示音和关闭提示音、DPI 100/150/200%、多屏与屏幕边缘。
5. 完成/取消/失败/失联退场；退出后重启不重播历史任务；杀死 Solo 进程后锁会释放，正常关闭无孤儿窗口。
6. 已安装旧版时升级，保留设置、壁纸、卡通选择和去重记录；卸载只移除程序及快捷方式，再安装仍可读取个人设置。
7. 已启用 Harness 桥的安装版路径验证、桥移除、Solo 更换安装目录后的重新连接；安装/卸载都不删除 Agent 会话。
8. 检查 PyInstaller 构建警告和最终 DLL/资源；用 Process Monitor 或故障日志确认没有回到构建电脑路径寻找 Python/Tcl，最终安装目录中没有个人数据和凭据。

## 10. 本次验证与已知边界

已经完成：依赖元数据与 SHA256、Windows cp313 兼容性、依赖闭包、离线 pip 解析（dry-run）、官方安装器签名、图标九个尺寸、资源白名单与 PowerShell 语法检查；Solo 源码回归和安装版路径模拟测试、DeepSeek 桥单元测试。具体结果见 `packaging/VALIDATION.md`。

早期准备阶段仅在 Python 3.10.20 下测试。2026-10-17 已在官方 CPython 3.13.16 下完成 285 项测试、冻结 EXE 自检及本机安装/升级/卸载验收，见本文开头与 `packaging/VALIDATION.md` 后半部分；独立干净机器验收仍未完成。`build.ps1` 在重新构建时会再次测试。

第三方库的原文许可证已经收集。九张卡通目前只有原项目来源说明，不等于已核实再分发授权；应用图标、品牌 Logo 和声音也应由发行者确认分发权。详情见 `packaging/THIRD_PARTY_NOTICES.md`。这是发布前资源核对事项，不影响当前源码版继续使用。

## 11. 官方参考

- Python 3.13.16 下载文件：https://www.python.org/ftp/python/3.13.16/
- Python Windows 文档（安装器、Tk 与 embedded 发行区别）：https://docs.python.org/3.13/using/windows.html
- PyInstaller 运行时路径：https://pyinstaller.org/en/stable/runtime-information.html
- PyInstaller spec 配置：https://pyinstaller.org/en/stable/spec-files.html
- Inno Setup 下载：https://jrsoftware.org/isdl.php
- 固定 Inno release：https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3
- 依赖的具体 PyPI 文件来源：`packaging/dependency-manifest.json`。


## 发行版 0.1.0 的桌面生命周期

`monitor/src/startup.py` 管理当前用户开机自启。`monitor/src/desktop.py` 使用 Windows 原生托盘和隐藏消息窗口，不增加第三方依赖。原生回调只入队，Tk 定时器处理打开、退出，避免 ctypes/Tk 线程状态重入崩溃。常规关闭收到托盘；第二实例发送唤回消息；`--background` 后台启动；`--quit` 等待原实例关闭，供安装升级和卸载使用。

个性化的「通用」页读取真实系统启动项，并提供显式退出入口。安装脚本在 64 位 HKCU Run 视图管理升级/卸载，保持与 64 位 Python 一致；卸载默认保留个人数据，交互卸载可由用户明确选择清除。

`build.ps1` 使用专用临时测试目录，完整测试不允许跳过；生成 EXE 后先运行 `--self-test`，通过才编译安装程序。冻结自检会临时创建独立数据目录和唯一测试自启值，并在结束后清理，不接触真实 Agent。

安装验收命令（仅适用于尚未安装 Solo 且没有同名快捷方式/自启项的测试账户）：

```powershell
./.build-venv/Scripts/python.exe -I packaging/validate_installer.py dist/installer/MonitorSolo-0.1.0-windows-x64-setup.exe .artifacts/installer-new-check
```

验收使用中文/空格安装路径、独立数据目录和不含 Python/Conda 的 PATH；实际执行安装、后台启动、重复启动唤回、关闭到托盘、运行中升级和卸载。它不等同于干净虚拟机测试。验收日志和报告保存在传入的目录；再次运行必须使用新的空目录。
