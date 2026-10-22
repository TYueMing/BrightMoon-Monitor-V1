# Bright Moon · APP UI 设计

三端范围：Windows、iOS、Android。统一页面与操作语义，布局按屏幕适配。

**先体验：** 双击 [index.html](index.html)，顶部切换平台，左侧或底部切换页面。无需安装依赖，所有操作均为模拟。

**完整设计：** [APP_UI_design.md](APP_UI_design.md) 包含页面地图、首次进入、灵动岛三态、设备双向权限、操作回执、通信接口、平台边界和实施验收。

**实际检查：** [REVIEW.md](REVIEW.md)。

## 桌面总览

![Windows 总览](preview/01-windows-overview.png)

## 手机总览

![iOS 总览](preview/02-ios-overview.png)

## 设备协同

![Android 设备页](preview/04-android-devices.png)

## 个性化

![雾蓝主题与减少动画](preview/05-windows-settings.png)

其他预览： [授权回执](preview/03-ios-receipt.png)、[远程第三态](preview/06-remote-detail.png)。

可点击内容：三态展开、来源切换、审批确认与回执、断线/只读场景、配对向导、提醒新增与开关、主题切换、减少动态效果。刷新或点击“重置演示”会恢复初始模拟数据。

文件说明：`index.html` 是入口，`styles.css` 是视觉与响应式布局，`app.js` 是模拟交互，`assets/` 仅包含已有公开界面图标的副本，`preview/` 是浏览器实际渲染图。`serve.py` 是可选的本机静态预览服务，绑定回环地址，不开放给局域网。

本目录没有接入真实 Agent，没有更改 SOLO 的用户配置、网络服务或 BrightMoon 主干，没有生成安装包。
