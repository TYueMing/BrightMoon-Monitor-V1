# Monitor Solo 第三方组件与资源

本目录保存下载的 Python 依赖原有许可证；安装版也携带这些文本，不能仅保留本说明而丢弃原许可证。

运行组件包括 CPython（PSF License 及其捆绑组件许可）、Tcl/Tk、Pillow 12.3.0（MIT-CMU 及 wheel 中所含图片编解码库许可）、python-zstandard（BSD-3-Clause 及 Zstandard 相关许可）、websocket-client（Apache-2.0）。准确文本见 `licenses/` 中对应版本目录。Python 安装器的官方 SBOM 保存在 `python-installer.spdx.json`；Tcl/Tk 8.6.15 的原文许可单独保留。

PyInstaller、其 hooks、pip、setuptools、pytest 及其他构建/测试依赖仅服务构建电脑；无需把它们的安装器和 wheel 缓存装到最终用户电脑。PyInstaller bootloader 的许可证包含分发例外，见随附原文。Inno Setup 使用随附 `Inno-Setup-LICENSE.txt`，当前官网另外请求商业使用者购买商业许可；正式商业发行前按官网现行条款核实。本准备工作未购买任何证书或许可。

安装器简体中文翻译取自 Inno Setup `is-6_7_3` 官方仓库的 `Files/Languages/Unofficial/ChineseSimplified.isl`，原作者说明保留在文件内。

九张默认悬挂卡通来自项目现有 `data/rope`，已原样复制并保留原来的 `readme.txt`；该说明指向 https://github.com/elysia395/dsh-wallpaper-engine/tree/main/assets/rope 。目前仅保留来源说明，**未据此确认图片的再分发授权**。正式对外分发前由发行者核实图像许可，必要时替换为自己的授权资源。应用 Logo、Agent 品牌 Logo 和提示音也应由发行者确认其使用与分发权；本准备工作没有改变或声称取得这些权利。

最终程序不得携带开发者的账户、API key、Agent 会话、个人壁纸、settings.json 或任务历史数据库。发行资源使用显式允许清单，不递归收集个人 data/。

手机只读 Web 服务新增 aiohttp 3.14.5，以及 aiohappyeyeballs、aiosignal、attrs、frozenlist、multidict、propcache、yarl、idna。各版本的原始许可证已从经过 PyPI SHA256 校验的 wheel 提取到 `licenses/`；aiohttp 的 llhttp 子组件许可证也完整保留。准确版本与哈希以 `dependency-manifest.json` 和 `requirements-runtime.lock` 为准。本次只准备依赖与资源，没有生成新的安装包。
