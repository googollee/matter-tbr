# matter-tbr

基于 ESP Thread Border Router v1.2 开发板（ESP32-S3 + ESP32-H2）的 **Matter Thread Border Router** 固件与配套资料。

板子烧录固件后，同时具备两个身份：

- **Thread Border Router**：Thread ↔ Wi-Fi/IP 桥接，转发和广播 Thread 网络。
- **Matter-over-Wi-Fi 设备**：实现 Matter 1.3 的 Thread Border Router Management cluster，可被 Home Assistant（matter-server）等 Matter 控制器配对（commission），并通过该 cluster 下发 Thread 网络的 Active Operational Dataset。

它**不承担 Matter Controller 角色**。

> 本项目不是 Matter 认证产品。使用 Matter SDK 不代表可以标称“Matter 兼容”或“已认证”；Matter 商标和 logo 归 CSA 联盟所有。

## 目录结构

状态说明：✅ 已实现　🚧 规划中（尚未实现）

```
matter-tbr/
├── README.md
├── Makefile                    ✅ make build / flash / clean
├── LICENSE / NOTICE            ✅ Apache-2.0 与来源声明
├── .gitignore                  ✅
│
├── firmware/                   ✅ ESP-IDF 工程（ESP32-S3，基于 Matter SDK thread-br-app 示例）
│   ├── CMakeLists.txt
│   ├── sdkconfig.defaults
│   ├── sdkconfig.device        （生成，不提交：调试开关、VID/PID，见“配置”）
│   ├── partitions.csv          （fctry 分区存放 factory 数据）
│   ├── dependencies.lock
│   ├── third_party/connectedhomeip  → 符号链接到 ../../.deps/connectedhomeip
│   └── main/                   main.cpp、Button.cpp、DeviceCallbacks.cpp 等
│
├── scripts/
│   ├── setup.sh                ✅ 下载/链接官方依赖到 .deps/（已实测从零下载并编译通过）
│   ├── fix_on_mac.sh           ✅ Apple Silicon 修复（zap-cli、cryptography）
│   ├── gen_commissioning.py    ✅ 从 config/ 生成 factory 分区镜像、配对码、固件配置片段
│   ├── extract_test_attestation.py  ✅ 从 SDK 源码提取内置测试证书到 config/attestation/test/
│   └── requirements.txt        这两个脚本的 Python 依赖（装进 .venv）
│
├── config/                     ✅ 配对信息的唯一来源
│   ├── device.yaml             默认值（沿用原固件的 SDK 测试值，仅产品名改为 `Thread BR`，提交）
│   ├── device.local.yaml       本机实际值（不提交，只写要覆盖的字段）
│   └── attestation/test/       测试证书（DAC、PAI、CD，提交；由脚本生成，见其中 README）
│
├── homeassistant/              🚧 Home Assistant 侧配置（形式待定）
├── enclosure/                  🚧 OpenSCAD 外壳（预留流程，见下）
├── docs/reference/             本地参考资料（原理图等，不提交，见其中 README）
│
├── .deps/                      官方依赖（esp-idf、connectedhomeip），不提交
├── .venv/                      项目自己的 Python 环境，由 setup.sh 创建，不提交
├── out/                        ✅ 生成产物（factory_partition.bin、配对码），不提交
├── pki/                        🚧 阶段 2 自生成证书，不提交
```

官方依赖不放进仓库，由 `scripts/setup.sh` 下载到 `.deps/`（已存在则跳过）；脚本同时创建 `.venv/`。固件通过 `firmware/third_party/connectedhomeip` 引用 Matter SDK。

## 硬件

| 部分 | 说明 |
| --- | --- |
| 开发板 | ESP Thread Border Router v1.2：ESP32-S3-WROOM-1（主控）+ ESP32-H2-MINI-1（802.15.4 RCP） |
| 烧录口 | 两个 USB-C：**S3 口**用于烧录和运行主程序；H2 口仅在单独调试 RCP 时用 |
| 按键 | BOOT 键（GPIO0，S3 侧），已用作出厂复位/重开配对窗口，见“已实现功能” |
| 指示灯 | 板载红灯是硬件电源指示灯，没接到 GPIO，软件无法控制 |

模组为 ESP32-S3 **N8R2**（8MB Flash / 2MB PSRAM）。macOS 下两个口会各自出现 `/dev/cu.usbmodemXXXXXXXX`；线插在同一个物理口上路径不变，换口后用 `ls /dev/cu.usbmodem*` 重新确认。

原理图（Espressif 提供）不随仓库分发，请自行放入 `docs/reference/`。

## 环境搭建（macOS，Apple Silicon）

**一般不需要手动做这一节**：`make build`（或只准备依赖的 `make deps`）会按顺序自动运行 `setup.sh`、Matter bootstrap（仅缺失时）和 `fix_on_mac.sh`，三步都可重复执行。下面是各步骤的说明，需要单独执行或排查时参考。

### 1. 依赖

- **ESP-IDF v5.5.4**
- **Matter SDK**（`connectedhomeip`，版本固定在 `scripts/setup.sh` 的 `MATTER_COMMIT`）

```bash
./scripts/setup.sh
```

脚本会把两者下载到 `.deps/`（已存在则跳过），并建立 `firmware/third_party/connectedhomeip` 链接。

Matter SDK 下载后还需要 bootstrap：

```bash
cd .deps/connectedhomeip
source scripts/bootstrap.sh -p all,esp32
```

macOS 下 `install.sh` 默认不带 cmake/ninja，`setup.sh` 已用 `idf_tools.py install cmake ninja` 补装，装到 `~/.espressif/tools/`，`export.sh` 会自动加进 PATH。

> **Python 版本要一致**：ESP-IDF 的虚拟环境按安装时的 `python3` 版本命名（如 `idf5.5_py3.14_env`）。如果之后 `export.sh` 报 “Python virtual environment … not found”，说明当前 shell 里的 `python3` 与安装时不是同一个版本，调整 PATH 使两者一致。

### 2. 仅 Apple Silicon 需要的两处修复

bootstrap 之后运行（可重复执行，已修复会自动跳过）：

```bash
./scripts/fix_on_mac.sh
```

脚本做两件事：

- **zap-cli**：Matter SDK 的 `scripts/setup/zap.json` 会给所有 arm64 Mac 装 x86_64 版 zap-cli，直接编译会报 `OSError: Bad CPU type in executable: 'zap-cli'`。脚本用官方发布的原生 arm64 包替换。
- **cryptography / pyo3 冲突**：Pigweed 自带 Python 的 `sys.version` 带 `+chromium.29` 这类非标准后缀，与 `cryptography` 43.0.0 的 Rust 绑定不兼容，编译 mbedTLS 证书包时报 `pyo3_runtime.PanicException: Python version string has too many parts`。脚本把它降级到 `cryptography==41.0.7`。

## 编译与烧录

**固件必须基于 Matter SDK 的 `thread-br-app`**，不要用 esp-thread-br 仓库的 `basic_thread_border_router`——它不含 Matter 协议栈，烧进去的设备不会被 matter-server 发现。

### 1. 编译

```bash
make build
```

`make build` 依次完成：准备依赖（`make deps`）→ 编译 ESP32-H2 的 RCP 固件 → 根据 `config/` 生成配对数据（`make config`）→ 编译 ESP32-S3 主固件。主固件会自动检测并打包 RCP 镜像；首次启动时如果版本更新，会自动刷给 H2，不用单独烧。已编译过再运行是增量编译。

产物在 `firmware/build/`（固件）和 `out/`（factory 分区镜像、配对码）。RCP 的构建目录在 `.deps/esp-idf/examples/openthread/ot_rcp/`，这是主固件打包 RCP 的来源位置。

其他目标：

| 命令 | 作用 |
| --- | --- |
| `make deps` | 只准备依赖（含 `.venv`），不编译 |
| `make config` | 只根据 `config/` 生成 `out/` 和 `firmware/sdkconfig.device`，见“配置” |
| `make rcp` | 准备依赖并只编译 RCP |
| `make firmware` | 同 `make build` |
| `make flash PORT=...` | 烧录主固件和 factory 分区，见下一节 |
| `make flash-factory PORT=...` | 只烧录 factory 分区 |
| `make clean` | 删除 `firmware/` 和 RCP 目录下的 `build/`、`managed_components/`、`sdkconfig`、`sdkconfig.old`，以及 `firmware/sdkconfig.device`、`out/`；不动 `.deps/` 和 `.venv/` |

> `make clean` 会删除 `firmware/sdkconfig`，其中的本地改动会丢失。需要保留的配置请写进 `firmware/sdkconfig.defaults`，或者调试开关写进 `config/device.local.yaml`（`debug_shell`）。

<details>
<summary>等价的手动命令（排查问题时用）</summary>

**必须先 source ESP-IDF，再 source Matter 环境，顺序不能反**：

```bash
# RCP（ESP32-H2）
(. .deps/esp-idf/export.sh && cd .deps/esp-idf/examples/openthread/ot_rcp \
  && idf.py set-target esp32h2 && idf.py build)

# 主固件（ESP32-S3），需先运行 .venv/bin/python scripts/gen_commissioning.py
. .deps/esp-idf/export.sh
. .deps/connectedhomeip/scripts/activate.sh
cd firmware
idf.py set-target esp32s3
idf.py build
```

</details>

### 2. 烧录

USB 接 **S3 口**，用 `ls /dev/cu.usbmodem*` 确认串口：

```bash
make flash PORT=/dev/cu.usbmodemXXXXXXXX
```

它会烧录主固件（含 RCP），再把 `out/factory_partition.bin` 写入 `fctry` 分区。只更新 factory 数据用 `make flash-factory PORT=...`。

> **`fctry` 分区里存着配对信息和证书。** 固件启用了 factory data provider，这个分区为空或损坏时设备无法正常配对。`idf.py erase-flash` 会把它一并擦掉，之后必须重新 `make flash-factory`。单独用 `idf.py flash` 不会写这个分区。

### 3. 看开机日志拿配对信息

```bash
idf.py -p /dev/cu.usbmodemXXXXXXXX monitor
```

日志里会打印（测试配置下为固定值）：

```
chip[SVR]: SetupQRCode: [MT:Y.K9042C00KA0648G00]
chip[SVR]: Manual pairing code: [34970112332]
```

拿 QR code 或手动配对码去 Home Assistant 添加设备。配对成功后，matter-server 能看到 `endpoint#1` 类型为 `ThreadBorderRouter (0x91)`，`threadBorderRouterManagement` 等 behavior 正常暴露；日志里的 `Skipping NetworkCommissioning steps because the device is already on IP network` 符合“板子不需要 HA 帮它配网”的设计。

## 下发 Thread 网络

配对成功后，通过 `ThreadBorderRouterManagement` cluster（Matter 标准 cluster，HA 前端暂无图形化入口，见“重要提醒”）把 Active Operational Dataset 下发给板子，让它建立 Thread 网络。

- Cluster ID：`0x0452`
- `SetActiveDatasetRequest`（命令码 `0x03`）：仅当板子当前没有 active dataset 时可用，需要 timed invoke
- 调用前必须先对同一个 fabric 武装 `GeneralCommissioning.ArmFailSafe`（cluster `0x0030`，命令 `0x00`）

**生成 dataset**：用板子自己的 OpenThread 协议栈生成，格式一定合法。打开 `CONFIG_ENABLE_CHIP_SHELL`（默认关闭）重新编译烧录，在串口 console 里：

```
matter otcli dataset init new
matter otcli dataset commit active
matter otcli dataset active -x
```

**下发**：安装 `matter-python-client`（PyPI，由 `matter-js/matterjs-server` 维护），连接 matter-server，按顺序发三个命令。**第三步 `CommissioningComplete` 不能省**：

```python
import os
from matter_server.client import MatterClient
from chip.clusters.cluster_defs import GeneralCommissioning, ThreadBorderRouterManagement

NODE_ID = ...                                    # 板子在 matter-server 里的 node id
DATASET_HEX = os.environ["THREAD_DATASET_HEX"]   # 从环境变量读取，不要写进代码

await client.send_device_command(node_id=NODE_ID, endpoint_id=0,
    command=GeneralCommissioning.Commands.ArmFailSafe(expiryLengthSeconds=120, breadcrumb=1))

await client.send_device_command(node_id=NODE_ID, endpoint_id=1,
    command=ThreadBorderRouterManagement.Commands.SetActiveDatasetRequest(activeDataset=bytes.fromhex(DATASET_HEX)),
    timed_request_timeout_ms=10000,
    interaction_timeout_ms=45000)    # 建议 45–60 秒，见“重要提醒”

await client.send_device_command(node_id=NODE_ID, endpoint_id=0,
    command=GeneralCommissioning.Commands.CommissioningComplete())
```

**验证**：串口 `matter otcli state` 应为 `leader`，`matter otcli ifconfig` 应为 `up`；matter-server 侧该 node 的 `InterfaceEnabled` 应为 `True`，`ActiveDatasetTimestamp` 应非空。复位后应保持一致（已验证持久化）。

如果希望 matter-server 之后用这个网络配对 Thread 设备，还需要把 dataset 补进它的 Thread 凭证库（直接操作 cluster 下发时，它并不知道这个网络）：

```python
await client.set_thread_operational_dataset(DATASET_HEX)
```

⚠️ dataset 里包含 Network Key / PSKc，相当于这个 Thread 网络的密码，不要提交到仓库或发到公开的地方。

## 已实现功能

### 按键（BOOT 键，GPIO0）

不用重新烧录或断电，就能手动触发出厂复位或重开配对窗口。代码见 `firmware/main/Button.cpp`、`firmware/main/include/Button.h`，使用原生 GPIO 中断 + FreeRTOS 任务去抖（不依赖 iot_button 组件）。

- 长按 ≥ 5 秒：出厂复位，清空 Wi-Fi/Thread/fabric 凭证并重启
- 短按：重新打开配对窗口（重复短按幂等，不报错）
- 已现场实测通过

### Thread 网络下发与持久化

已按上节三步流程验证：板子成为 Thread Leader，复位后能自动恢复。

### 全链路验证

已用一个 Matter-over-Thread 设备验证 HA → 本 Border Router → Thread 设备这条链路可用（matter-server 发 `onOff.on/off` 均返回 Success）。

## 路线图

| 项目 | 状态 |
| --- | --- |
| 编译、烧录、配对、Thread 网络下发与持久化 | ✅ |
| BOOT 键：长按出厂复位 / 短按重开配对窗口 | ✅ |
| 迁移为独立仓库，固件可独立编译 | ✅ 依赖由 `setup.sh` 管理，`make build` / `make flash` 已验证 |
| **阶段 1**：配对信息统一配置 + factory 分区（test 证书模式） | ✅ 已上板验证（QR 不变、Thread 恢复为 leader、`debug_shell` 开关生效） |
| **阶段 2**：自生成证书（PAA → PAI → DAC） | 🚧 |
| LED 状态灯（WS2812） | 🚧 |
| Home Assistant 侧配置 | 🚧 形式待定 |
| 外壳（OpenSCAD），印配对 QR 与配对码 | 🚧 只预留流程 |

## 配置

配对信息和调试开关由 `config/` 统一管理，其余编译选项使用 `firmware/sdkconfig.defaults` 的固定值，开发和发布都不需要改。

```
config/device.yaml           默认值（沿用原固件的 SDK 测试值，仅产品名为 Thread BR）
config/device.local.yaml     本机实际值（不提交，只写要覆盖的字段）
        │
scripts/gen_commissioning.py        （make build / make config 会自动运行）
        ├─→ out/factory_partition.bin    factory 分区镜像（passcode 校验数据、discriminator、VID/PID、设备信息、证书）
        ├─→ out/onboarding_codes.csv     QR 配对串与手动配对码，供外壳使用
        └─→ firmware/sdkconfig.device    编译选项片段（调试 shell、VID/PID）
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `vendor_id` / `product_id` | `0xFFF1` / `0x8000` | 测试 VID/PID；正式 VID 需加入 CSA |
| `vendor_name` / `product_name` / `serial_number` | `TEST_VENDOR` / `Thread BR` / `TEST_SN` | 设备信息，写入 factory 分区 |
| `hardware_version` / `hardware_version_string` | `0` / `TEST_VERSION` | 同上 |
| `discriminator` | `3840` | 0~4095 |
| `passcode` | `20202021` | 1~99999998，不能是 `11111111` 等规范禁用值 |
| `debug_shell` | `false` | 打开串口 console（`CONFIG_ENABLE_CHIP_SHELL`），发布版保持 `false` |
| `attestation.mode` | `test` | `test` 使用 `config/attestation/test/` 的证书；`custom`（自生成证书）属阶段 2，尚未实现 |

使用方式：在 `config/device.local.yaml` 里只写要改的字段，例如

```yaml
discriminator: 1234
debug_shell: true
```

然后 `make build` 并 `make flash PORT=...`。脚本会校验输入（范围、禁用 passcode、拼写错误的字段名、证书文件是否存在），并在默认配置下核对生成的 QR 与手动码必须是已知值（`MT:Y.K9042C00KA0648G00` / `34970112332`）。

**Wi-Fi 不在配置里**：Wi-Fi 凭证由 Matter 配对时（BLE 阶段，经手机 HA App）写入设备，不需要也不应该编进固件。

**配置变更会自动生效**：`sdkconfig.defaults` 或 `config/` 变化后，`make build` 会删掉旧的 `firmware/sdkconfig` 并重新生成（IDF 只在 `sdkconfig` 不存在时才应用 defaults），这时 `set-target` 会触发一次全量重编，耗时较长。

### 测试证书

`config/attestation/test/` 里的 DAC、PAI、CD 是 Matter SDK 示例固件（`GetExampleDACProvider()`）编译进代码的那一套（“Matter Dev”链），由 `scripts/extract_test_attestation.py` 从 SDK 源码提取，并离线校验（DAC 私钥与公钥匹配、DAC 由该 PAI 签发、CD 含 VID/PID）。所以切换到 factory 分区后设备的证书链与之前相同，已有的配对不受影响。升级 `MATTER_COMMIT` 后重新运行该脚本，用 `diff` 查看证书有无变化。

这套证书在 SDK 中公开，**仅限开发测试**。注意它不是 SDK `credentials/test/attestation/` 目录里的 “Matter Test” 链，两者不同。

### 配对信息与外壳

外壳上要印 Matter 配对 QR 和配对码，必须与固件内的值一致，所以二者都由同一份 `config/` 生成。

- 提交到仓库的只有默认测试值；用本地值生成的产物在被忽略的 `out/` 里。
- 暂按单块板设计，配置结构保留以后扩展到批量的余地。

### 证书：阶段 2（规划）

阶段 1（`attestation.mode: test`）已完成，见“配置”。阶段 2 对应 `attestation.mode: custom`：

- VID 保持测试值 `0xFFF1`（正式 VID 需加入 CSA）。
- 阶段 2 用 `chip-cert` 生成 PAA → PAI → DAC 与 Certification Declaration，生成脚本放 `scripts/`，PKI 目录 `pki/` 不提交。
- 自签 PAA 不被手机和控制器默认信任：手机配对时可能提示“未认证设备”；matter-server 需要把自己的 PAA 放进 `--paa-root-cert-dir`。
- PAA 私钥相当于“根”，泄露可被伪造设备，丢失则整套重做，务必本地备份。
- factory 分区里的私钥是明文；未启用 flash encryption 时，物理拿到板子可读出。
- **量产或对外分发前，必须使用自己的证书，不能用测试证书。**

### LED 状态灯（规划）

板载红灯不可控，所以外接一颗 **WS2812** 可寻址 RGB LED，用颜色区分状态。

- 硬件：WS2812/WS2812B 模块 + 300~500Ω 电阻（串在数据线上）
- 供电：左侧排针的 `3V3` / `GND`（WS2812 标称 5V，3.3V 也能点亮，颜色稍暗，做状态指示够用）
- 数据线：**GPIO14**，备选 **GPIO40**（排针有引出，当前未被板载外设占用）
- 避开的 GPIO：0（BOOT）、1/2（I2C）、17/18（与 H2 通信的 UART1）、21/38/45（扩展排针 SPI2）、43/44（控制台 UART0）、45（strapping）、46（S3 上仅输入）
- 驱动：ESP-IDF `led_strip` 组件（RMT 外设），与 Matter 官方示例的 `LEDWidget` 同一套路
- 状态映射：慢闪蓝＝CHIPoBLE 广播中；快闪＝配对中（PASE/CASE）；常亮绿＝配对成功且 Thread 网络已建立；红＝出错
- 挂接点：Matter commissioning 状态回调（`OnCommissioningWindowOpened`、`OnCommissioningSessionStarted`、`OnCommissioningComplete` 等）

### Home Assistant（规划）

形式尚未确定（YAML 配置、仪表盘、自动化、辅助脚本，或将来的集成）。HA 官方已有同类工作（home-assistant/core PR [#179102](https://github.com/home-assistant/core/pull/179102)，“Network Infrastructure Manager”系列，截至调研时未合并），所以暂不自己写 custom_component。

### 外壳（规划）

用 OpenSCAD 设计。`enclosure/` 先预留：

```
enclosure/
├── src/          *.scad（include generated/qr_data.scad）
├── generated/    qr_data.scad（默认值版本，提交）；local/ 为本机值，不提交
├── stl/          导出的打印文件
└── images/
```

QR 作为独立零件，换色或分色打印；模块边长建议 0.8–1.2 mm 以上（0.4 喷嘴），四周留静区，注意打印面是否镜像。板子外形尺寸和接口位置需依据原理图和实物测量，写进 `enclosure/README.md`。

## 重要提醒

- **用手机 HA App 配对 Thread 的 Matter 设备时，手机必须连在 Border Router / matter-server 所在的网络（同一个 VLAN）。** BLE 阶段的 commissioner 是手机；设备加入 Thread 后，手机要通过局域网 IPv6（经 Border Router 的 OMR 路由）重新连上设备发 `CommissioningComplete`。手机在别的网段会连不上，表现为 BLE 和凭证下发都成功，最后报 pairing fail。
- **每次打开 `idf.py monitor` 都会复位板子**，不只是烧录才会。除非要观察日志或发命令，不要随便连 monitor，尤其是配对过程中。
- `idf.py monitor` 需要交互式 TTY，非交互环境（脚本、agent）下会报 `Monitor requires standard input to be attached to TTY`。这时用 Python `pyserial` 直接读串口（S3 走 USB-Serial/JTAG，波特率参数被忽略）。要连开机日志一起看，需自己模拟 `idf_monitor` 的复位序列（`setDTR(False); setRTS(True)` → `setDTR(True); setRTS(False)` → `setDTR(False)`）；只 `cat` 串口不会复位，也读不到复位前的历史日志。
- **matter-server 要认准实现**：常见部署镜像 `ghcr.io/matter-js/matterjs-server` 是 matter.js（Node.js）实现，**不是** `home-assistant-libs/python-matter-server`，两者 WS API 字段约定不同，查文档/源码要认准 `matter-js/matterjs-server`。握手信息里 `sdk_version` 显示为 `matter-server/x.y.z (matter.js/a.b.c)` 即可确认。
- **matter-server 通常没有蓝牙硬件**（握手信息 `bluetooth_enabled: false`），CHIPoBLE 配对必须靠手机 App 做蓝牙中继，只能在设备附近用手机操作。
- **HA 前端目前没有 `ThreadBorderRouterManagement` 的图形化入口**：HA core 的 Matter 集成里 service 全是针对具体 domain 硬编码的，没有类似 ZHA 的通用 cluster 命令入口。
- **Thread 网络会持久化，前提是下发走完整三步**（`ArmFailSafe` → `SetActiveDatasetRequest` → `CommissioningComplete`）。漏了最后一步，fail-safe 超时后会自动撤销，看起来像“不会持久化”，其实是 Matter 标准的事务保护。依据在 Matter SDK 的 `ThreadBorderRouterManagementCluster.cpp`：`kFailSafeTimerExpired` 触发 `RevertActiveDataset()`，`kCommissioningComplete` 触发 `CommitActiveDataset()`。
- **`SetActiveDatasetRequest` 要等 Thread 网络真正建立才返回**，实测耗时 6–33 秒，`interaction_timeout_ms` 建议 45–60 秒。客户端报 `UnknownError('Operation aborted')` 不一定代表失败，先别重试，去串口看 `matter otcli state` / `ifconfig`，重试可能打乱正在进行的操作。
- **`ActiveDatasetTimestamp` 非空时，`SetActiveDatasetRequest` 会被拒绝**（错误码 203 “Invalid in state”），哪怕 Thread 接口实际没启用。清理用 `matter otcli factoryreset`：只清 OpenThread 自己的持久化状态，不碰 Matter fabric，比走 Matter 层 `ScheduleFactoryReset()`（连配对一起清）更精准。⚠️ 该命令没有二次确认，使用前确认连的是哪块板子。
- **测试证书仅限开发**：配对码 `34970112332` 和 DAC 都是 SDK 内置的测试值，所有使用测试证书的设备都一样。

## 许可证

[Apache License 2.0](LICENSE)。`firmware/` 中部分文件源自 Matter SDK `examples/thread-br-app/esp32`（Copyright Project CHIP Authors, Apache-2.0），修改过的文件带有 “Modified by” 声明；详见 [NOTICE](NOTICE)。
