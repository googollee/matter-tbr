# matter-tbr

[![test](https://github.com/googollee/matter-tbr/actions/workflows/test.yml/badge.svg)](https://github.com/googollee/matter-tbr/actions/workflows/test.yml)

基于 ESP Thread Border Router v1.2 开发板（ESP32-S3 + ESP32-H2）的 **Matter Thread Border Router** 固件，以及配套的 Home Assistant 集成。

板子烧录固件后，同时是：

- **Thread Border Router**：Thread ↔ Wi-Fi/IP 桥接，转发和广播 Thread 网络。
- **Matter-over-Wi-Fi 设备**：实现 Matter 1.3 的 Thread Border Router Management cluster，可被 Home Assistant（matter-server）等 Matter 控制器配对，并通过该 cluster 接收 Thread 网络的 Operational Dataset。

它**不承担 Matter Controller 角色**。

配套的 **Home Assistant 自定义集成**（`homeassistant/`）让你在 HA 里查看边界路由器和 Thread 网络的状态、一键创建网络、不重建网络迁移信道、保持 HA 的 Thread 凭证与设备一致。

> 本项目不是 Matter 认证产品。使用 Matter SDK 不代表可以标称“Matter 兼容”或“已认证”；Matter 商标和 logo 归 CSA 联盟所有。

## 目录

- [快速开始](#快速开始)
- [日常操作](#日常操作)
- [Home Assistant 集成](#home-assistant-集成)
- [配置](#配置)
- [调试 shell](#调试-shell)
- [使用须知](#使用须知)
- [规划](#规划)
- [项目结构与硬件](#项目结构与硬件)

## 快速开始

从零到板子进入 Home Assistant，共四步：编译、烧录、配对、创建网络。

### 1. 编译

```bash
make build
```

`make build` 依次完成：准备依赖（下载 ESP-IDF 与 Matter SDK 到 `.deps/`、创建 `.venv/`、Matter bootstrap、Apple Silicon 修复）→ 编译 ESP32-H2 的 RCP 固件 → 根据 `config/` 生成配对数据 → 编译 ESP32-S3 主固件。每一步都可重复执行，已完成的会跳过。RCP 镜像会打包进主固件，首次启动时版本不同会自动刷给 H2，不用单独烧。

产物在 `firmware/build/`（固件）和 `out/`（factory 分区镜像、配对码）。

| 命令 | 作用 |
| --- | --- |
| `make help` | 列出所有命令 |
| `make build`（同 `make firmware`） | 编译完整固件 |
| `make deps` | 只准备依赖，不编译 |
| `make config` | 只根据 `config/` 生成 `out/` 和 `firmware/sdkconfig.device` |
| `make rcp` | 只编译 RCP |
| `make flash PORT=...` | 烧录主固件和 factory 分区 |
| `make flash-factory PORT=...` | 只烧录 factory 分区 |
| `make component` | 检查语法和 JSON，把 HA 集成打包到 `out/thread_br/` 和 `out/thread_br.zip`（不含 `__pycache__`），见 [安装与升级](#安装与升级) |
| `make enclosure` | 用 `config/`（含 `device.local.yaml`）的配对码渲染外壳，生成 `out/enclosure/base.stl` 和 `lid.stl`，见 [外壳](#外壳)；需要 OpenSCAD（`OPENSCAD=路径` 可指定），不需要 SDK |
| `make fw-test` | 固件逻辑（`ButtonLogic`）的主机端单元测试，不需要 ESP-IDF |
| `make ha-test` | 跑 HA 集成的全部测试（只准备 `.venv`，不下载 SDK）；`ARGS=...` 传给 pytest，如 `make ha-test ARGS="-k change -x"` |
| `make clean` | 删除 `firmware/` 和 RCP 目录下的 `build/`、`managed_components/`、`sdkconfig`、`sdkconfig.old`，以及 `firmware/sdkconfig.device`、`out/`；不动 `.deps/` 和 `.venv/` |

要点：

- 固件基于 Matter SDK 的 `thread-br-app`。esp-thread-br 仓库的 `basic_thread_border_router` 不含 Matter 协议栈，不能用。
- `make clean` 会删除 `firmware/sdkconfig`。要保留的编译选项写进 `firmware/sdkconfig.defaults`，调试开关写进 `config/device.local.yaml`。
- `sdkconfig.defaults` 或 `config/` 变化后，`make build` 会自动重新生成 `firmware/sdkconfig`，并触发一次全量重编（约 8 分钟）。
- ESP-IDF 的虚拟环境按安装时的 `python3` 版本命名。`export.sh` 报 “Python virtual environment … not found” 时，说明当前 shell 的 `python3` 与安装时不是同一个版本，调整 PATH 使二者一致。

#### Apple Silicon 的两处修复

`make build` 自动运行 `scripts/fix_on_mac.sh` 进行修复

- **zap-cli**：Matter SDK 会给所有 arm64 Mac 装 x86_64 版 zap-cli，直接编译报 `Bad CPU type in executable: 'zap-cli'`。脚本用官方原生 arm64 包替换。
- **cryptography / pyo3 冲突**：Pigweed 自带 Python 的 `sys.version` 带 `+chromium.29` 之类后缀，与 `cryptography` 43.0.0 不兼容，编译证书包时报 `Python version string has too many parts`。脚本把它降到 `cryptography==41.0.7`。

#### 手动编译命令（排查问题时用）

必须先 source ESP-IDF，再 source Matter 环境，顺序不能反：

```bash
# RCP（ESP32-H2）
(. .deps/esp-idf/export.sh && cd .deps/esp-idf/examples/openthread/ot_rcp \
  && idf.py set-target esp32h2 && idf.py build)

# 主固件（ESP32-S3），需先运行 .venv/bin/python scripts/gen_commissioning.py
. .deps/esp-idf/export.sh
cd .deps/connectedhomeip && . scripts/activate.sh   # 要在 SDK 目录里 source，否则会报 pathspec 错误
cd ../../firmware
idf.py set-target esp32s3
idf.py build
```

### 2. 烧录

USB 接 **S3 口**，用 `ls /dev/cu.usbmodem*` 确认串口：

```bash
make flash PORT=/dev/cu.usbmodemXXXXXXXX
```

它烧录主固件（含 RCP），再把 `out/factory_partition.bin` 写入 `fctry` 分区。

`fctry` 分区存着配对信息和证书，为空或损坏时设备无法配对。`idf.py erase-flash` 会把它擦掉，之后要重新 `make flash-factory`；单独的 `idf.py flash` 不会写这个分区。

### 3. 配对

默认（测试）配置的配对信息固定：

- QR 码: `MT:Y.K9042C00KA0648G00`
- 手动配对码: `34970112332`

改过 `config/device.local.yaml` 的话，值在开机日志和 `out/onboarding_codes.csv` 里：

```bash
idf.py -p /dev/cu.usbmodemXXXXXXXX monitor
```

```
chip[SVR]: SetupQRCode: [MT:Y.K9042C00KA0648G00]
chip[SVR]: Manual pairing code: [34970112332]
```

在 Home Assistant 手机 App 里添加 Matter 设备，扫 QR 或输入手动配对码。配对后 matter-server 里该节点的 `endpoint#1` 类型是 `ThreadBorderRouter (0x91)`。

- **手机必须连在 Border Router / matter-server 所在的网络（同一个 VLAN）。**
  - BLE 阶段的 commissioner 是手机；设备入网后，手机要经局域网 IPv6 重新连上设备发 `CommissioningComplete`。
  - 手机在别的网段时，BLE 和凭证下发都成功，最后报 pairing fail。
- matter-server 通常没有蓝牙硬件，BLE 配对只能在设备附近用手机完成。
- Wi-Fi 凭证在配对时由手机经 Matter 写入设备，不在固件配置里。

### 4. 创建 Thread 网络

配对后板子上还没有 Thread 网络，见 [创建网络](#创建网络)。

## 日常操作

- 创建 Thread 网络
  - HA 设备页点“创建网络”，或 `thread_br.create_network`
- 让这块板子加入 HA 的首选 Thread 网络（多边界路由器）
  - 设备页“采纳首选网络”按钮，或 `thread_br.adopt_network`
- 换 Thread 信道（不重建网络）
  - `thread_br.change_channel`
- 让 HA 的 Thread 凭证与设备一致
  - 通常自动；也可在“修复”页点提交，或 `thread_br.sync_network`
- 查看网络状态
  - HA 里 “Thread Border Router” 设备的传感器
- 重开配对窗口
  - 短按 BOOT 键
- 出厂复位（清 Wi-Fi、Thread、配对）
  - 长按 BOOT 键 ≥ 5 秒
- 只清除 Thread 网络（保留配对）
  - 串口 `matter otcli factoryreset`（需要 `debug_shell` 构建）
- 换配对码、VID/PID 等
  - 改 `config/device.local.yaml`，`make build` 后 `make flash`

### 创建网络

三种方式，效果相同：生成随机网络密钥和 PSKc、按环境自动选信道、通过 Matter 下发给设备、把凭证保存到 HA 的 Thread 凭证库并同步给 matter-server。

- **HA 设备页的“创建网络”按钮**：用默认参数。设备上已有网络或正在创建/迁移时不可用，创建期间“网络”传感器显示“创建中”。
- **`thread_br.create_network` 操作**（设置 → 开发者工具 → 操作，目标选“Thread BR Thread”设备）：可选网络名（1-16 字节）、信道（11-26）、密码短语（6-255 字节）。不填则网络名随机、信道自动选。
- **命令行**：`homeassistant/tools/create_network.py`，默认只演练，`--apply` 才真正执行，且必须同时指定 `--save-dataset 文件` 保存 dataset（文件权限 0600）。直接连 matter-server 的 WebSocket，不经过 HA，**不会**更新 HA 的凭证库，需要之后再运行 `thread_br.sync_network`。

创建需要 6-33 秒（设备要等 Thread 网络真正建立才返回）。新网络不会自动成为 HA 的**首选**网络（HA 里已有首选网络时）；要用它给 Thread 设备配网，请在 HA 的 Thread 页面设为首选。

**只能创建一次。** Matter 标准没有删除网络的命令，设备上已有网络时会拒绝新的 dataset（错误码 203）。要重建，先在设备上清除：串口 `matter otcli factoryreset`（只清 Thread，保留配对），或长按 BOOT 键（连配对一起清）。⚠️ `factoryreset` 没有二次确认，执行前确认连的是哪块板子。

**密码短语不保存。** 它只用于派生 PSKc；配对 Matter-over-Thread 设备用不到，只有外部 Thread commissioner 才需要。

### 采纳已有网络（多个边界路由器）

要让两块板子共用**同一个** Thread 网络（互为备份、设备可在两者之间漫游），第二块板子不能自己创建网络，而要加入第一块的网络。

- **设备页的“采纳首选网络”按钮**：让这块板子加入 HA 的**首选 Thread 网络**。设备页的“首选网络”传感器显示当前关系：`无首选网络`（按钮不可用）、`已采纳`（设备已在首选网络里，按钮不可用）、`与首选不同`（按钮可用）。
- **`thread_br.adopt_network` 操作**：同样的效果，另外可用 `source_device` 指定采纳哪个边界路由器当前所在的网络，用 `delay` 指定延迟（见下）。

按设备当前状态分两种情况：

| 设备状态 | 做法 |
| --- | --- |
| 没有网络 | 直接加入，几秒到一分钟，期间“网络”传感器显示“创建中”。 |
| 已经在**另一个**网络里 | Matter 没有删除网络的命令，只能把**整个旧网络**迁到目标网络：延迟（默认 120 秒，30-600）到期后，旧网络里**所有设备一起切换**。 |

⚠️ 第二种是覆盖，要知道：

- 迁走的是整个旧网络，不只是这块板子。旧网络里有 Thread 设备或另一块边界路由器时，它们会一起被带走。传感器在“与首选不同”时会在属性里显示这块板子的**邻居数**，邻居数大于 0 就说明不是孤零零的一块。
- **不能取消。** 提交后只能等延迟到期，之后再迁回去。
- 这块板子必须是旧网络的 **Leader**，否则会被拒绝。设备上已有一次待生效的迁移时也会被拒绝。
- 设备上最终的凭证与首选网络只差时间戳（必须比旧网络的新，否则网格会忽略），迁移完成后新凭证会自动存回 HA。
- 这期间“网络”传感器显示“迁移信道中”。

其他：

- 凭证和第一块板子共用同一条，不会多出一份。
- 第一块板子的网络若是用本集成创建的、且当时 HA 里没有首选网络，它就是首选网络，直接按按钮即可。
- 之后改信道要在 **Leader** 上发起；第二块板子作为路由器会随网络一起切换。
- 两块板子默认的 discriminator 和配对码相同，配对要一块一块来，或给第二块在 `config/device.local.yaml` 里改一个不同的 `discriminator`。

### 迁移信道

`thread_br.change_channel` 把现有网络迁到新信道，不重建、不用重新配对：网络名、PAN ID、密钥都不变，延迟到期后网络里所有设备同时切换。

| 参数 | 说明 |
| --- | --- |
| `channel` | 目标信道 11-26；不填则自动选 |
| `delay` | 延迟 30-600 秒，默认 120 |

- 只在边界路由器是网络 **Leader** 时可用。
- 新信道必须与当前信道不同，且在网络的信道掩码内（本项目创建的网络是 11-26）。
- 设备上已有一次迁移在等延迟计时器时，再次迁移会被拒绝（提示“已经有一次信道迁移在等待生效”），等它生效后再改。这期间“网络”传感器显示“迁移信道中”。
- 操作会等到设备报告已在新信道上，再保存新凭证；迁移期间“网络”传感器显示“迁移信道中”。
- 超时仍未切换时会提示“稍后再看”：设备已收到变更，之后仍会生效，生效后凭证自动同步。
- 命令行：`homeassistant/tools/change_channel.py`，默认只演练，`--apply` 才执行。

### 自动选信道

没指定信道时（创建、迁移都一样）按以下规则选，**这是启发式，不是射频测量**：

1. 优先顺序 **25 → 15 → 26**（夹在 Wi-Fi 1/6/11 的空隙里；26 在个别射频上会降低发射功率，放最后）。
2. 排除：
   - ZHA 正在用的 Zigbee 信道（自动读取）：同频直接排除，相邻一个信道尽量避开；
   - 你在选项里填的 Wi-Fi 信道所压住的 Thread 信道（Wi-Fi 1 → 11-14，6 → 16-19，11 → 21-24，13 → 23-26，14 → 25-26）；
   - 你在选项里填的“要避开的 Thread 信道”。
3. 首选信道都不行时，放宽“相邻信道”的限制；再不行，在所有信道里选离已知干扰最远的；全被排除则报错。
4. 显式指定的 `channel` 永远优先。选择过程写进 HA 日志（`自动选信道 …`）。

它只知道你告诉它的和 ZHA 里读到的：Zigbee2MQTT 的信道、邻居的 Wi-Fi 都看不到。

### 凭证保持一致

HA 的 Thread 凭证库里的 dataset 要和设备上的网络一致，否则给新 Thread 设备配网会拿到旧凭证。

- 创建网络、迁移信道时，操作自己保存凭证。
- 运行中观察到设备上的网络变了（别的工具创建或改了信道，或迁移超时后才生效），自动同步一次。
- 启动时发现凭证没存、或比设备上的旧：不会擅自写入（也许是你有意删掉的），而是在 设置 → 系统 → 修复 里给出提示（“Thread 凭证没有保存” / “Thread 凭证已过期”），点“提交”即从设备读回并保存。
- 创建、迁移进行中，自动同步和修复提示暂停。
- 手动同步：`thread_br.sync_network`。

“凭证”传感器的状态：`没有网络` / `已保存` / `未保存` / `已过期`。

### BOOT 键（GPIO0）

- 短按：重新打开配对窗口（重复短按不报错）。
- 长按 ≥ 5 秒：出厂复位，清空 Wi-Fi、Thread、配对信息并重启。

## Home Assistant 集成

### 安装与升级

1. 把 `homeassistant/custom_components/thread_br/` 整个目录（或 `make component` 生成的 `out/thread_br/`）复制到 HA 配置目录的 `custom_components/` 下（不要带 `__pycache__`）。
2. 重启 HA。
3. 设置 → 设备与服务 → 添加集成 → “Thread Border Router”。需要 Matter 集成已加载，且板子已配对。

升级时覆盖文件后必须**重启 HA**，只重载集成不够：HA 会把自定义集成的 Python 模块缓存在进程里，新模块、翻译、选项页都要重启才生效。`manifest.json` 声明的依赖（`matter`、`repairs`、`thread`、`python-otbr-api`）由 HA 自动处理。

重载或卸载 Matter 集成后，本集成会自动跟着重载。

针对 HA 2026.9 及以后版本（要求 Python ≥ 3.14.2）。

### 设备在哪里

集成为每个边界路由器注册**自己的设备**（名称形如 `Thread BR Thread`），不在 Matter 的设备页里——HA 2026.9 起一个集成不能往另一个集成的设备里加实体。它通过“连接到”关联到 Matter 设备，所以：

- 从 设置 → 设备与服务 → Thread Border Router 进入；
- 或在 Matter 设备页的“已连接设备”里找到它。

### 实体

| 实体 | 说明 |
| --- | --- |
| 网络（传感器） | `未创建` / `创建中` / `迁移信道中` / `已创建`；已创建时属性里有网络名、信道、PAN ID、Extended PAN ID（不含密钥） |
| 信道、角色 | 当前信道；Thread 角色（Leader、路由器、终端设备等） |
| 凭证 | HA 里是否保存了这个网络的凭证 |
| 邻居数、路由器数 | 邻居表和路由表条目数 |
| 分区 ID、Leader 路由器 ID | 排查用，**默认禁用**，在实体设置里启用 |
| 接口（二进制传感器） | Thread 接口是否在运行 |
| 网络故障（二进制传感器） | 链路断开、硬件故障、被干扰时为“开”，属性里列出具体故障 |
| 创建网络（按钮） | 见 [创建网络](#创建网络) |
| 首选网络（传感器） | 设备与 HA 首选 Thread 网络的关系：`无首选网络` / `已采纳` / `与首选不同`；与首选不同且设备在另一个网络里时，属性带邻居数 |
| 采纳首选网络（按钮） | 仅在“与首选不同”时可用，见 [采纳已有网络](#采纳已有网络多个边界路由器) |

状态每 30 秒向设备读取一次，收到相关属性变化通知时也会立即读取。读不到设备时实体显示“不可用”，恢复后自动恢复。

### 选项

设置 → 设备与服务 → Thread Border Router → 配置：

- **附近的 Wi-Fi 信道（2.4 GHz）**：1-14，多选。
- **要避开的 Thread 信道**：11-26，多选。

用于 [自动选信道](#自动选信道)。

### 四个操作

在 设置 → 开发者工具 → 操作 里调用，目标选 Thread 边界路由器的**设备**：

| 操作 | 说明 |
| --- | --- |
| `thread_br.create_network` | 创建网络，见 [创建网络](#创建网络) |
| `thread_br.adopt_network` | 让没有网络的边界路由器加入已有网络，见 [采纳已有网络](#采纳已有网络多个边界路由器) |
| `thread_br.change_channel` | 迁移信道，见 [迁移信道](#迁移信道) |
| `thread_br.sync_network` | 从设备读回凭证，保存到 HA 的 Thread 凭证库和 matter-server |

网络创建或迁移成功、但保存凭证失败时，会提示“凭证没能保存到 Home Assistant”；运行 `thread_br.sync_network` 补存（修复页里也会出现提示）。

### 隐私

凭证含网络密钥。HA 的 Thread 凭证库把它明文存在 `.storage/` 里（HA 自己的行为），集成不会把它写进日志。**matter-server 会把 `SetActiveDatasetRequest` 的完整 dataset 以 INFO 级别明文写进日志**（matter.js 的行为），请留意它的日志保留期限和访问权限，或调低日志级别。

开发、内部设计与测试见 [homeassistant/README.md](homeassistant/README.md)。

## 配置

配对信息和调试开关由 `config/` 统一管理，其余编译选项用 `firmware/sdkconfig.defaults` 的固定值。

```
config/device.yaml           默认值（SDK 测试值，产品名为 Thread BR；提交）
config/device.local.yaml     本机实际值（不提交，只写要覆盖的字段）
        │
scripts/gen_commissioning.py        （make build / make config 自动运行）
        ├─→ out/factory_partition.bin    factory 分区镜像（passcode 校验数据、discriminator、VID/PID、设备信息、证书）
        ├─→ out/onboarding_codes.csv     QR 配对串与手动配对码，供外壳使用
        └─→ firmware/sdkconfig.device    编译选项片段（调试 shell、VID/PID）
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `vendor_id` / `product_id` | `0xFFF1` / `0x8000` | 测试 VID/PID；正式 VID 需加入 CSA |
| `vendor_name` / `product_name` / `serial_number` | `TEST_VENDOR` / `Thread BR` / `TEST_SN` | 设备信息，写入 factory 分区 |
| `hardware_version` / `hardware_version_string` | `0` / `TEST_VERSION` | 同上 |
| `discriminator` | `3840` | 0-4095 |
| `passcode` | `20202021` | 1-99999998，不能是 `11111111` 等规范禁用值 |
| `debug_shell` | `false` | 打开串口 shell，发布版保持 `false`，见 [调试 shell](#调试-shell) |
| `attestation.mode` | `test` | `test` 使用 `config/attestation/test/` 的证书；`custom` 属阶段 2，未实现 |

在 `config/device.local.yaml` 里只写要改的字段：

```yaml
discriminator: 1234
debug_shell: true
```

然后 `make build`、`make flash PORT=...`。脚本会校验输入（范围、禁用 passcode、拼错的字段名、证书文件是否存在），默认配置下还会核对生成的 QR 与手动码必须是已知值。

### 测试证书

`config/attestation/test/` 里的 DAC、PAI、CD 是 Matter SDK 示例固件编译进代码的那一套（“Matter Dev”链），由 `scripts/extract_test_attestation.py` 从 SDK 源码提取并离线校验（DAC 私钥与公钥匹配、DAC 由该 PAI 签发、CD 含 VID/PID）。升级 `MATTER_COMMIT` 后重新运行该脚本，用 `diff` 看证书有无变化。

这套证书在 SDK 中公开，**仅限开发测试**，所有使用它的设备配对码和证书都相同。它不是 SDK `credentials/test/attestation/` 里的 “Matter Test” 链。

## 调试 shell

在 `config/device.local.yaml` 设 `debug_shell: true`，重新 `make build` 并烧录。串口 console 里会多出：

| 命令 | 作用 |
| --- | --- |
| `matter otcli <命令>` | OpenThread 命令行，如 `state`、`ifconfig`、`dataset active -x`、`factoryreset`、`dataset pskc -p` |
| `matter tasks` | 列出各 FreeRTOS 任务的状态与栈剩余（`vTaskList`），用来判断栈够不够 |

同时启用 FreeRTOS 任务统计选项。**发布版保持 `false`**，这些都不会编进去。

读栈水位的注意点：水位只减不增、重启归零，要在做完想测的操作之后再读；读数只覆盖当时走过的路径。

串口用法：

- 每次打开 `idf.py monitor` 都会复位板子，配对过程中不要随便连。
- `idf.py monitor` 需要交互式 TTY；脚本里用 `pyserial` 直接读串口（S3 走 USB-Serial/JTAG，波特率被忽略）。要连开机日志一起看，需模拟 `idf_monitor` 的复位序列（`setDTR(False); setRTS(True)` → `setDTR(True); setRTS(False)` → `setDTR(False)`）；只 `cat` 串口不会复位，读不到复位前的日志。

## 使用须知

- **matter-server 的实现要认准**：常见镜像 `ghcr.io/matter-js/matterjs-server` 是 matter.js（Node.js）实现，**不是** `home-assistant-libs/python-matter-server`，WS API 字段约定不同。握手信息 `sdk_version` 形如 `matter-server/x.y.z (matter.js/a.b.c)` 即是前者。其 `device_command` 里**字节字段要用 base64**，十六进制会被当成 base64 静默解码成乱码。
- **matter-server 缓存里的 Thread 网络诊断（0x35）属性不会更新**，所以集成直接向设备读取状态，不读缓存。
- **`SetActiveDatasetRequest` 要等 Thread 网络真正建立才返回**，耗时 6-33 秒，`interaction_timeout_ms` 建议 45-60 秒。客户端报 `Operation aborted` 不一定是失败，先别重试，去串口看 `matter otcli state` / `ifconfig`。它也可能是板子崩溃重启（matter-server 日志里同时有 `Peer reports shutdown` 和 `bootReason`，串口有 panic）。
- **下发网络必须走完整三步**：`ArmFailSafe` → `SetActiveDatasetRequest` → `CommissioningComplete`。漏了最后一步，fail-safe 超时后设备会撤销网络，这是 Matter 标准的事务保护。迁移信道用的 `SetPendingDatasetRequest` 不需要 fail-safe。
- **`CHIP` 任务栈为 16384 字节**（`sdkconfig.defaults`）。创建网络和采纳网络这两条路径在 `CHIP` 任务里激活 Thread，峰值约 9.3 KB，SDK 默认的 9216 会溢出重启（串口 `A stack overflow in task CHIP has been detected`）。
- **固件开着 `TestEventTrigger`，用 SDK 的公开默认 enable key**（`00112233445566778899aabbccddeeff`）。固件没有注册自己的触发器，影响有限；**发布前应关掉或换 key**。
- **HA 自带的 Matter 集成没有 Thread Border Router Management 的图形化入口**，所以有本项目的 `thread_br` 集成。
- **测试证书仅限开发**；对外分发前必须使用自己的证书（阶段 2）。

## 规划

状态：✅ 已实现　🚧 规划中

| 项目 | 状态 |
| --- | --- |
| 编译、烧录、配对、Thread 网络下发与持久化 | ✅ |
| BOOT 键 | ✅ |
| 配对信息统一配置 + factory 分区（test 证书模式） | ✅ |
| HA 集成：状态传感器、创建网络、迁移信道、凭证自动同步与修复提示 | ✅ |
| 阶段 2：自生成证书（PAA → PAI → DAC） | 🚧 |
| LED 状态灯（WS2812） | 🚧 |
| 外壳（OpenSCAD），印配对 QR 与配对码 | 🚧 只预留流程 |
| 清除 Thread 网络（Matter 没有标准命令，需固件配合） | 🚧 暂不做 |
| OpenThread 自动换信道（Channel Manager；有凭证一致性风险） | 🚧 暂不做 |

### 证书阶段 2

对应 `attestation.mode: custom`：

- VID 保持测试值 `0xFFF1`（正式 VID 需加入 CSA）。
- 用 `chip-cert` 生成 PAA → PAI → DAC 与 Certification Declaration，脚本放 `scripts/`，PKI 目录 `pki/` 不提交。
- 自签 PAA 不被手机和控制器默认信任：配对时可能提示“未认证设备”；matter-server 需把自己的 PAA 放进 `--paa-root-cert-dir`。
- PAA 私钥是“根”，泄露可被伪造设备，丢失则整套重做，务必本地备份。
- factory 分区里的私钥是明文；未启用 flash encryption 时，物理拿到板子可读出。

### LED 状态灯

板载红灯不可控，计划外接一颗 **WS2812** 可寻址 RGB LED。

- 硬件：一块自带去耦电容的 WS2812 模块，用 **2×3 排母**插在开发板 J4 排针最上面三行，三根线直接接模块，**板上不焊接，也没有分立元件**。供电用 J4 的 `3V3`（4 脚）和 `GND`（2 脚），不用 5V：WS2812 的数据线门限是 0.7×VDD，5V 供电时 S3 的 3.3V 输出不够。代价是 3.3V 低于标称的 3.5V，蓝色偏暗、黄色偏绿，要在固件里调色值，并限制亮度。接线、焊接、固定和尺寸见 [board/README.md](board/README.md)。
- 数据线：**GPIO16**（J4 的 6 脚，与 3V3 和 GND 排在一起，三线连续），备选 **GPIO14**（10 脚）。避开：0（BOOT）、1/2（I2C）、17/18（与 H2 通信的 UART1）、21/38/45（扩展排针 SPI2）、43/44（控制台 UART0）、45（strapping）、46（S3 上仅输入）。
- 驱动：ESP-IDF `led_strip`（RMT）。
- 挂接点：LED 任务周期读取状态快照（配对窗口、Wi-Fi、Thread 角色、边界路由等），不依赖事件回调，避免漏掉重启后已稳定的状态。

**显示规则**

- 颜色表示阶段：红＝板子自身，蓝＝配对和 Wi-Fi，黄＝Thread 和边界路由，绿＝全部正常。
- 节奏表示状态：**快闪**＝正在进行；**慢闪**＝在等你操作；**长亮**＝停在这一步，不会自己好，需要处理。
- 按红 → 蓝 → 黄 → 绿的顺序检查，第一个没到位的阶段决定显示什么。运行中某一层退化（如 Wi-Fi 断开），显示会退回到那一层。

**状态与处理**

| 阶段 | LED | 含义与处理 |
| --- | --- | --- |
| 启动中 | 红 · 快闪 | 正在启动，等待即可 |
| 启动异常或运行中严重故障 | 红 · 长亮 | 数据读不出、功能起不来、RCP（H2）通信中断、内存耗尽等。接串口看日志；`fctry` 分区为空或损坏时重新 `make flash-factory`；其余情况重新烧录 |
| 配对窗口开着 | 蓝 · 慢闪 | 等控制器配对（未配对时自动开，已配对时短按 BOOT 键开）；窗口超时后自动关闭 |
| 配对中，或连接 Wi-Fi 中 | 蓝 · 快闪 | 配对流程进行中，或正在连接（重连）Wi-Fi；Wi-Fi 一直连不上也是这个状态，见下 |
| 未配对且窗口已关 | 蓝 · 长亮 | 短按 BOOT 键重新开窗口，用手机配对 |
| Thread 没建网 | 黄 · 慢闪 | 在 HA 里[创建网络](#创建网络)，或[采纳首选网络](#采纳已有网络多个边界路由器) |
| Thread 连接中 | 黄 · 快闪 | 正在创建、加入网络，或掉线后重新附着；创建需要 6-33 秒 |
| Thread 已附着，边界路由没起来 | 黄 · 长亮 | 边界路由未初始化，或前缀未发布。刚附着的几秒内也会短暂出现；持续出现时检查 Wi-Fi 是否有 IPv6，并看串口日志 |
| 全部正常 | 绿 · 长亮 | Wi-Fi、Thread、边界路由都就绪 |

**异常处理**

- **Wi-Fi 一直蓝色快闪**：设备会无限重连，不会放弃，也不会清除凭证。AP 恢复后会自动连上；换了 Wi-Fi 环境，则短按 BOOT 键，用手机经 BLE 重新配对并写入新的 Wi-Fi 凭证（Thread 网络不受影响）。
- **一直红色快闪**：当前没有启动超时判断，启动卡死与正常启动都显示红色快闪。持续数分钟没变化时，接串口看日志。
- **长按 BOOT 键 5 秒的倒计时**没有 LED 反馈；出厂复位后设备重启，回到红色快闪。
- 蓝色慢闪（窗口开着）优先级高于黄色和绿色，窗口开着的这段时间看不到 Thread 和路由状态。

**局限**

- 绿色长亮只代表板子这边就绪，**不保证** HA 能连到 Thread 设备。Wi-Fi 接入点过滤组播、主机或容器不接受 RA 路由等问题，板子上看不到；要验证请在 HA 里确认 Thread 设备可达。
- WS2812 的黄色需要上板调色值，否则容易偏绿；红、绿色盲的用户请结合节奏判断。

### 外壳

用 OpenSCAD 设计，分两件打印，合并后用 4 颗 M3 沉头螺丝固定：

- **底壳**：开发板嵌在里面，底面凹刻 Matter 配对 QR 和手动配对码。
- **上盖**：外接的 WS2812 模块嵌在盖子里，LED 完全镂空，模块用 2 颗 M2 × 6 自攻螺丝（带 M2 垫圈）固定在盖子的立柱上；顶面完全平整，没有凸起，也不需要支撑。两个按键各是一根长导杆，直接顶到 S3 的 BOOT / RESET 键（盖子上刻 B / R）。

整机外形约 56.6 × 68.4 × 24 mm（底壳 6.6 mm，上盖 17.4 mm）。两个 USB-C 口在右侧开口，S3 天线和 H2 天线伸出的两端被外壳包住。

```
enclosure/
├── enclosure.scad       外壳，单文件，不引用别的 scad
├── stl/                 导出的打印文件
└── images/
```

用法：

```bash
make enclosure    # 生成 out/enclosure/base.stl（底壳，底面朝下）和 lid.stl（上盖，已翻面：外表面朝下，导杆已在孔里，无需支撑）
```

它先由 `scripts/gen_enclosure_data.py` 读取 `config/device.yaml` 和 `device.local.yaml`，把 passcode、discriminator、VID、PID 用 `-D` 参数传给 OpenSCAD 渲染，本机的配对码不会写进仓库里的文件。渲染后会把外壳自己算出的 QR 和手动配对码，与 `make config` 生成的 `out/onboarding_codes.csv` 比对；改过 `config/` 后要先 `make config`，否则会报不一致。

CI（`.github/workflows/enclosure.yml`）在外壳相关文件变化时用两个版本的 OpenSCAD 各跑一次 `make enclosure`：Ubuntu 源里的版本（24.04 是 2021.01，最老的要求）和官方最新的 snapshot。snapshot 失败只提醒，不挡 CI。

手动预览或调试时，直接用 OpenSCAD 打开 `enclosure/enclosure.scad`（用文件开头的默认测试值），改 `part`，或命令行传参：`openscad -o x.stl -D 'part="assembly"' enclosure/enclosure.scad`。`part` 可取 `base`、`lid`、`assembly`（整机，含开发板和 LED 模块的示意体）、`exploded`（爆炸图）、`check_base` / `check_lid` / `check_fit`（干涉检查，结果应为空）。

要点：

- **螺丝**：M3 × 10 沉头，从底壳底面穿入，拧进上盖立柱的 2.6 mm 孔（自攻）。立柱都在天线伸出的空角里，不占 PCB。
- **板子**：没有安装孔，靠底壳四周的墙和 PCB 下的支撑垫定位，PCB 底面离底板 2 mm，给 J4/J5 的针脚尾部留位。
- **按键**：导杆和上盖一起打印在孔里，靠圆环下面 3 根 0.9 mm 的断点柱固定。**装机前先把每个键的圆帽用力按到底**，断点柱会断开，之后导杆就能在孔里自由滑动：圆帽向下被台肩挡住（行程 0.8 mm），向上被圆环挡住，不会掉出来。杆尖离按键 0.35 mm，杆穿过一根长约 7 mm 的导向管，防止歪斜。按不断就把 `fuse_d` 改小，太容易断就改大。
- **估计值**：PCB 厚度（1.6）、2×3 排母的高度和尾针长度、LED 模块的外形和 LED 位置都没有来源数据，实测后改文件开头的参数。
- **二维码**：QR 编码在 `enclosure.scad` 里自己实现，与 SDK 的结果一致（默认值和一个非默认值都核对过）。底面的 QR 板按颜色分层（M600 换色），层高、位置等细节见文件里 `matter_label_plate` 的注释。
- 板子的尺寸和坐标来自官方 STEP 模型，见 [board/README.md](board/README.md)。

## 项目结构与硬件

```
matter-tbr/
├── Makefile                    make build / flash / clean
├── firmware/                   ESP-IDF 工程（ESP32-S3，基于 Matter SDK thread-br-app）
│   ├── sdkconfig.defaults      固定的编译选项
│   ├── sdkconfig.device        （生成，不提交）调试开关、VID/PID
│   ├── partitions.csv          fctry 分区存放 factory 数据
│   ├── third_party/connectedhomeip  → 符号链接到 ../../.deps/connectedhomeip
│   ├── test/                   主机端单元测试（make fw-test）
│   └── main/                   main.cpp、Button.cpp、DeviceCallbacks.cpp 等；DebugShell.cpp 仅 debug_shell 构建
├── scripts/
│   ├── setup.sh                下载/链接官方依赖到 .deps/，并调用 setup_venv.sh
│   ├── setup_venv.sh           创建/更新 .venv 并安装依赖（可单独运行）
│   ├── fix_on_mac.sh           Apple Silicon 修复
│   ├── gen_commissioning.py    从 config/ 生成 factory 分区镜像、配对码、固件配置片段
│   ├── gen_enclosure_data.py   从 config/ 生成外壳用的配对信息（-D 参数），并校验外壳算出的配对码
│   └── extract_test_attestation.py  从 SDK 源码提取内置测试证书
├── config/                     配对信息的唯一来源
├── homeassistant/              HA 自定义集成（custom_components/、tools/、tests/）
├── board/                      开发板改装：外接 WS2812 状态灯的接线与焊接说明
├── enclosure/                  外壳
├── .github/                    CI（workflows/：test.yml 跑 make fw-test、ha-test、component；enclosure.yml 跑 make enclosure）与 dependabot.yml
├── docs/reference/             本地参考资料（原理图、板子 3D 模型和照片等，不提交）
├── .deps/                      官方依赖（esp-idf、connectedhomeip），不提交
├── .venv/                      Python 环境（Python ≥ 3.14.2，项目脚本与 HA 集成测试共用，由 setup.sh 创建），不提交
└── out/                        生成产物，不提交
```

| 部分 | 说明 |
| --- | --- |
| 开发板 | ESP Thread Border Router v1.2：ESP32-S3-WROOM-1（主控，N8R2：8 MB Flash / 2 MB PSRAM）+ ESP32-H2-MINI-1（802.15.4 RCP） |
| 烧录口 | 两个 USB-C：**S3 口**用于烧录和运行主程序；H2 口仅在单独调试 RCP 时用 |
| 按键 | BOOT 键（GPIO0，S3 侧），见 [BOOT 键](#boot-键gpio0) |
| 指示灯 | 板载红灯是电源指示灯，没接 GPIO，软件无法控制；状态灯另外外接 WS2812，见 [board/README.md](board/README.md) |

macOS 下两个口各自出现 `/dev/cu.usbmodemXXXXXXXX`；同一个物理口路径不变，换口后用 `ls /dev/cu.usbmodem*` 重新确认。原理图（Espressif 提供）不随仓库分发，请自行放入 `docs/reference/`。

## 许可证

[Apache License 2.0](LICENSE)。`firmware/` 中部分文件源自 Matter SDK `examples/thread-br-app/esp32`（Copyright Project CHIP Authors, Apache-2.0），修改过的文件带有 “Modified by” 声明；详见 [NOTICE](NOTICE)。
