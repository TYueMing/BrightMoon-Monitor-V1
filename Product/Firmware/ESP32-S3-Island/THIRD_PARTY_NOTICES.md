# ESP32-S3 灵动岛固件：第三方组件与来源

本目录是 `firmware/ESP32-S3-Island` 的 Arduino sketch。以下列出该固件编译时依赖或内置的第三方代码及其许可证。第三方代码按原许可证使用，本项目不改变、也不重新授权这些代码。

## 1. Waveshare ESP32-S3-Touch-LCD-3.49 V2 Arduino BSP（Apache-2.0）

来源：Waveshare 官方示例仓库 <https://gitee.com/waveshare/esp32-s3-touch-lcd-3.49-v2>，取自该板的 ESP32-S3 Arduino demo。许可证：**Apache License 2.0**（<https://www.apache.org/licenses/LICENSE-2.0>）。其中若干驱动文件源自 Espressif Systems 的 `esp_lcd` / `esp_io_expander` 组件，文件头保留 `SPDX-License-Identifier: Apache-2.0` 与 Espressif 版权行。

原样引入或在本项目内适配的 BSP 文件（均在 `firmware/ESP32-S3-Island/` 下，编译进固件）：

| 路径 | 内容 |
| --- | --- |
| `src/axs15231b/esp_lcd_axs15231b.c`、`src/axs15231b/esp_lcd_axs15231b.h` | AXS15231B QSPI 面板驱动（原文件头为 Espressif Apache-2.0） |
| `src/touch/esp_lcd_touch.c`、`src/touch/esp_lcd_touch.h` | 触摸抽象层（原文件头为 Espressif Apache-2.0） |
| `src/tca9554/esp_io_expander.c`、`src/tca9554/esp_io_expander.h` | IO 扩展抽象层（原文件头为 Espressif Apache-2.0） |
| `src/tca9554/esp_io_expander_tca9554.c`、`src/tca9554/esp_io_expander_tca9554.h` | TCA9554 IO 扩展驱动（原文件头为 Espressif Apache-2.0） |
| `src/lcd_bl_bsp/lcd_bl_pwm_bsp.c`、`src/lcd_bl_bsp/lcd_bl_pwm_bsp.h` | 背光 PWM 初始化 |
| `i2c_bsp.c`、`i2c_bsp.h` | 触摸 I2C 与系统 I2C 双总线初始化 |
| `lvgl_port.c`、`lvgl_port.h` | QSPI 面板/触摸/LVGL 移植层（由 Waveshare demo 的 LVGL 移植代码裁剪；`island_ui_build()` 调用点为本项目改动） |
| `user_config.h`、`lv_conf.h` | 板级引脚与 LVGL 配置，基于 Waveshare demo / LVGL 模板为本项目修改 |

Apache-2.0 要求保留版权声明、许可证文本与修改说明。发布二进制或再分发源码时，请一并附带 Apache-2.0 全文；本仓库未复制该全文，可从上述上游仓库或 apache.org 获取。修改过的文件（`lvgl_port.c`、`user_config.h`、`lv_conf.h`、`src/tca9554/esp_io_expander.c`、`src/tca9554/esp_io_expander_tca9554.c`）已在文件注释中说明用途；`src/tca9554/*` 仅把 `TAG` 改为 `const char *` 以消除 `-Wdiscarded-qualifiers` 警告，未改变驱动行为；`lvgl_port.c` 调用 `island_ui_build()`。除这些适配点外未改动第三方驱动的行为。

## 2. LVGL 8.4.0（MIT）

- 来源：<https://github.com/lvgl/lvgl>，Arduino 库管理器安装，版本固定 **8.4.0**（本项目只兼容 8.x）。
- 许可证：**MIT**，版权归 Gábor Kiss-Vámosi 及 LVGL 贡献者所有。
- 使用方式：作为 Arduino 库链接，不复制进本仓库。`firmware/ESP32-S3-Island/lv_conf.h` 是基于 LVGL `lv_conf_template.h` 的项目配置（MIT 同一许可证），只调整了颜色深度、字体和裁剪项。

## 3. ArduinoJson 7.4.2（MIT）

- 来源：<https://arduinojson.org> / <https://github.com/bblanchon/ArduinoJson>，Arduino 库管理器安装，版本固定 **7.4.2**。
- 许可证：**MIT**，版权归 Benoit Blanchon 所有。
- 使用方式：作为 Arduino 库链接，不复制进本仓库；`net_client.cpp` 用它解析 `/api/esp32/v1/state` 的紧凑 JSON。

## 4. Espressif ESP32 Arduino core 3.3.10-cn（LGPL-2.1 / Apache-2.0）与 mbedTLS（Apache-2.0）

Arduino 板级包 `<https://github.com/espressif/arduino-esp32>`（本工作区使用 3.3.10-cn）提供 WiFi、WebServer、HTTPClient、Preferences、SD_MMC、WiFiClientSecure、FreeRTOS 等 API。它不复制进本仓库，按 board manager 安装；其自带库沿用各自文件头中的 LGPL-2.1 或 Apache-2.0 声明。随固件一起发布时按 ESP32 core 的许可证要求处理。

HTTPS 模式下固件用 core 捆绑的 **mbedTLS**（Apache-2.0，随 esp32 core 的工具链库提供，源码 <https://github.com/Mbed-TLS/mbedtls>）在本机手工校验证书链：`WiFiClientSecure::setInsecure()` 完成握手后，用 `mbedtls_x509_crt_parse()` / `mbedtls_x509_crt_verify()` 对配置中固定的 CA 做证书链校验，只忽略有效期标志（ESP32 无可靠时钟、桌面端每次启动会重新生成本地叶子证书）。mbedTLS 不复制进本仓库，按 core 的许可证处理。

## 5. Noto Sans SC 子集字体 `island_cjk_16.c`（SIL Open Font License 1.1）

- 来源：Noto Sans SC（Google / Adobe Noto CJK 项目，<https://github.com/notofonts/noto-cjk>）的 `Sans/SubsetOTF/SC/NotoSansSC-Regular.otf`。
- 许可证：**SIL Open Font License 1.1**；许可证全文见 [licenses/NotoSansSC-OFL.txt](licenses/NotoSansSC-OFL.txt)。字体作为固件的一部分被编译，未单独销售；字体名称保留（Noto / Noto Sans SC 是 Google 的保留字体名称）。
- 生成方式：`firmware/tools/make_island_cjk_font.ps1` 用 `lv_font_conv` 生成 16 px / bpp4 / LVGL 8 压缩字体，字符集为 ASCII 0x20–0x7E、GB2312 标点/全角/假名与 GB2312 一、二级汉字（约 7445 字形），变量名 `island_cjk_16`。仅包含字形数据与度量，不包含原字体的其他部分。
- 修改说明：只做了子集化与位图化（选择码位、16 px 光栅、LVGL 格式），未修改字形设计；生成脚本与命令保留在仓库中，可复现。

## 6. QMI8658 初始化序列参考（MIT）

`imu_bsp.cpp` 的 QMI8658 寄存器序列（WHO_AM_I、CTRL1 地址自增、CTRL2 4 g/250 Hz、CTRL5 LPF、CTRL7 使能、0x35 起 6 字节加速度突发）参照 QMI8658 数据手册以及 **lewisxhe/SensorLib**（<https://github.com/lewisxhe/SensorLib>，**MIT** 许可证）与 Waveshare 官方 demo 中的驱动写法；代码为本项目按 Arduino I2C API 重写，不直接复制上游实现。传感器本身、其固件/数据手册不随本仓库分发。

## 7. 本项目原创代码

以下文件为 BrightMoon Monitor 仓库为 ESP32 灵动岛镜像新写，不属于上述第三方组件（版权归本项目贡献者，遵循本仓库的授权说明；仓库未另行声明开源许可证）：

- `ESP32-S3-Island.ino` —— 启动顺序、配网门禁、IMU 翻转、轮询与 UI 更新循环
- `island_model.h` —— 桌面端与固件之间的紧凑状态结构
- `island_ui.h` / `island_ui.cpp` —— LVGL 灵动岛界面
- `net_client.h` / `net_client.cpp` —— WiFi/HTTP(S) 轮询、ETag、TLS 手工链校验、JSON 解码、状态互斥
- `config_portal.h` / `config_portal.cpp` —— NVS/SD 配置、TLS/CA 字段与 SoftAP 配网网页
- `serial_client.h` / `serial_client.cpp` —— USB 串口双向传输、审核命令与结果、USB-only 握手
- `imu_bsp.h` / `imu_bsp.cpp` —— QMI8658 初始化、基线标定、横屏/翻转方向判定与 BOOT 手动翻转
- `THIRD_PARTY_NOTICES.md` —— 本文件

界面中文来自第 5 节的 Noto Sans SC 子集字体；数字与完成符号使用 LVGL 随附字体。2026-10-29 新增 `island_icons.c`，由本仓库 `monitor/icon_asset/` 中现有 Agent/项目图标缩小、圆形裁剪后生成，原图标权利及商标归其各自权利人，不因格式转换获得新的授权。桌面串口依赖新增 **pyserial 3.5（BSD-3-Clause）**，来源 https://github.com/pyserial/pyserial 。SD 配置与 CA 由用户提供，不是第三方组件。

新增 `power_control.*`、`power_hold.h` 为本项目实现；电源 GPIO 电平与保持控制依据 Waveshare 官方 `07_BATT_PWR_Test`，未复制其按钮库。`lvgl_port.c` 的修改增加了共享 IO 扩展器上的 EXIO6 电源保持及背光关闭入口。
