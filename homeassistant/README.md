# thread_br：设计与开发说明

Home Assistant 自定义集成 `thread_br` 的内部方案、固件侧资源占用和开发方法。使用方法（安装、操作、选项）见仓库根目录的 [README.md](../README.md)。

目标版本：HA 2026.9 及以后（要求 Python ≥ 3.14.2）。测试环境 `.venv` 装的是 2026.9.4，弃用警告是逐版本加的，所以另有按源码扫描的静态检查（见“测试”）。

## 1. 总体结构

```
HA 的 Matter 集成 ──(复用 matter_client)──┐
                                          ▼
              ThreadBrManager（每个已加载的 Matter 条目一个）
               ├─ 订阅 matter 客户端的 NODE_ADDED/UPDATED/REMOVED 事件发现边界路由器节点（有 0x452 cluster 的 endpoint）
               ├─ 每个节点一个 Coordinator ── 每 30 秒 read_attribute ──► 设备
               ├─ 操作：create / change / sync（互斥，busy 标志）
               ├─ 凭证对账：自动同步 + Repairs 提示
               └─ 实体：sensor / binary_sensor / button（经 Coordinator）
```

- `async_setup_entry` 找到已加载的 Matter 条目，取 `runtime_data.adapter.matter_client`，创建 `ThreadBrManager`，`entry.async_on_unload(manager.async_stop)`。Matter 条目状态变化时 `async_schedule_reload` 本条目。
- 条目是单实例（`single_config_entry`），依赖 `matter`、`repairs`、`thread`，要求 `python-otbr-api==2.10.0`（用于 PSKc 计算）。
- 设备注册：2026.9 起设备按配置条目各自独立，自定义集成不能把实体并进 Matter 的设备页。所以每个边界路由器注册自己的设备（名称模板 `{name} Thread`），用 `via_device_id` 指向 Matter 设备（子设备与父设备须属同一条目的前提下由 HA 校验）。
- 通过 `device.config_entry_id == entry.entry_id` 找节点；不使用已弃用的 `DeviceEntry.config_entries`（2027.10 失效）。

### 模块

| 模块 | 作用 | 依赖 HA |
| --- | --- | --- |
| `dataset.py` | 生成 dataset（随机密钥、Extended PAN ID、PAN ID、Mesh Local Prefix、PSKc）；构造迁移信道用的 Pending Dataset；TLV 编解码 | 否 |
| `channels.py` | 避开 Wi-Fi/Zigbee 选信道 | 否 |
| `create.py` | 创建网络流程；`read_border_router_state` 一次读完整状态 | 否 |
| `change.py` | 迁移信道流程 | 否 |
| `credentials.py` | 判断 HA 凭证库相对设备的状态 | 否 |
| `coordinator.py`、`entity.py` | 定期读状态、实体基类 | 是 |
| `manager.py` | 节点发现、操作编排、自动同步、修复提示 | 是 |
| `sensor.py`、`binary_sensor.py`、`button.py` | 平台 | 是 |
| `services.py`（+ `services.yaml`）、`repairs.py`、`config_flow.py` | 操作（创建、采纳、迁移信道、同步）、修复流程、选项流程 | 是 |
| `const.py` | 常量（见下） | 否 |

前五个纯逻辑模块只依赖一个 `send_command(command, require_schema=None, **kwargs)` 接口：HA 里是 matter 集成的 `MatterClient`，命令行工具里是 WebSocket 适配器，测试里是 `FakeBoard`。

主要常量（`const.py`）：

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `UPDATE_INTERVAL` | 30 s | 轮询周期 |
| `REFRESH_COOLDOWN` | 2 s | 事件触发刷新的去抖 |
| `COMMAND_TIMEOUT` | 30 s | 普通命令 |
| `SET_DATASET_TIMEOUT` | 75 s | `SetActiveDatasetRequest`（interaction 60 s + 15 s） |
| `FAILSAFE_SECONDS` | 120 | `ArmFailSafe` 时长 |
| `LINK_RETRY_DELAYS` | 2 / 10 / 60 s | 保存凭证后链接到 matter-server 的重试 |
| delay `MIN/MAX/DEFAULT` | 30 / 600 / 120 s | 迁移信道的延迟计时器 |
| `DEFAULT_CHANNEL` | 25 | 不做选择时的回退信道 |

## 2. 协议要点

### 设备端接口

| 对象 | 说明 |
| --- | --- |
| TBR Management `0x452`（endpoint 1，device type `0x91`） | 属性 0 BorderRouterName、1 BorderAgentID、2 ThreadVersion、3 InterfaceEnabled、4 ActiveDatasetTimestamp、5 PendingDatasetTimestamp |
| 命令 | 0 GetActive、1 GetPending、3 SetActive（timed；需先 `ArmFailSafe`、结束要 `CommissioningComplete`；已有网络返回 203）、4 SetPending（timed；**不需要 fail-safe**，固件 `GetPanChangeSupported()` 为 true） |
| Thread Network Diagnostics `0x35` | 属性 0 Channel、1 RoutingRole（6=Leader）、2 NetworkName、3 PanId、4 ExtendedPanId、7 NeighborTable、8 RouteTable、9 PartitionId、13 LeaderRouterId、62 ActiveNetworkFaultsList；该 cluster 没有命令（无 ResetCounts）和计数器属性 |

没有删除网络的命令。

### matter-server（matter.js）的约定

- `device_command` 字段：`node_id`、`endpoint_id`、`cluster_id`、`command_name`、`payload`、`timed_request_timeout_ms`、`interaction_timeout_ms`。**字节字段用 base64**；十六进制会被静默当 base64 解码成乱码。
- 错误以 `{"error_code": 0, "details": "..."}` 返回。设备端明确拒绝的错误信息里带 `(code N)`。
- `send_command` 没有客户端超时，集成用 `asyncio.timeout` 自己包。
- 缓存里的 0x35 属性不会随设备更新（网络建好后仍是旧值或 Null），0x452 的 `ActiveDatasetTimestamp` 会被推送。**所以状态一律用 `read_attribute` 直读设备**。
- 空属性在 HA 客户端里是 `NullValue`，在 `read_attribute` 的 JSON 里是 `None`；直读后只需处理 `None`。

### Thread dataset

- Channel Mask TLV：信道 c 对应 32 位字的第 `31 - c` 位（11~26 → `001fffe0`）。OpenThread 命令行显示的 `0x07fff800` 是相反的约定，不要混用。
- PSKc：`python_otbr_api.pskc.compute_pskc(ext_pan_id, network_name, passphrase)`。
- 迁移信道 = 取当前 Active Dataset，换新信道，Active Timestamp 加 1 秒，附 Pending Timestamp 和 Delay Timer（毫秒，30~600 s），作为 Pending Dataset 提交。OpenThread 在计时到期时让整网同时切换。只有 Leader 才这样处理，因此集成只在 Leader 时放行。

## 3. 状态读取

`read_border_router_state` 一次 `read_attribute`，路径为 `1106/3`、`1106/4`、`1106/5`（即 0x452 的 InterfaceEnabled、ActiveDatasetTimestamp、PendingDatasetTimestamp）和 `53/{0,1,2,3,4,7,8,9,13,62}`（0x35 的 10 个属性），共 13 条。Coordinator 每 30 秒执行一次；matter 集成推送的相关属性变化触发的刷新经 2 秒去抖合并（`REFRESH_COOLDOWN`）。设备读不到时实体 `unavailable`，恢复后自动恢复。

“首选网络”传感器和“采纳首选网络”按钮：协调器每次刷新时用 `dataset_store.async_get_preferred_dataset` 取 HA 首选网络的 Extended PAN ID（`preferred_ext_pan`），`preferred_status` 比较它与设备的 Extended PAN ID：没有首选 → `no_preferred`；相同 → `adopted`；否则 `different`；设备有网络但读不到它的 Extended PAN ID 时为未知（按钮不可用）。按钮只在 `different` 且不繁忙时可用。HA 里首选网络变化没有通知，最多滞后一个轮询周期（30 秒）。

“网络”传感器状态由 Manager 叠加：设备上无网络 → `none`；创建中 → `creating`；本集成正在迁移，或设备上有尚未生效的 Pending Dataset（`PendingDatasetTimestamp` 非空，包括别的工具发起的迁移、本集成超时后仍在等待的迁移）→ `changing`；否则 `active`（属性：网络名、信道、PAN ID、Extended PAN ID，不含密钥）。

## 4. 操作流程

所有操作互斥（`busy`），按钮在已有网络或 busy 时 `unavailable`。操作结束时 `_async_finish_operation` 先 `async_refresh()`（仍 busy），再解除 busy、发通知、重新评估凭证状态，避免实体在状态未刷新时闪回。

### 结果判定

下发命令后的失败分两类（`_is_definitive_rejection`）：

- **确定拒绝**：错误信息含 `(code N)`，立即失败，不轮询。
- **结果不明**（超时、连接断开、`Operation aborted`）：**不重试下发**，改为轮询设备状态判断是否已生效。重试可能打乱设备上正在进行的操作。

### 创建网络（`create.py`）

1. 读状态，已有网络则 `network_exists`。
2. 选信道（`channels.choose_channel`）、生成 dataset。
3. `ArmFailSafe(120 s)` → `SetActiveDatasetRequest`（timed，interaction 60 s，整体 `SET_DATASET_TIMEOUT`）→ `CommissioningComplete`。
4. 结果不明时每 2 秒读一次状态，最多 60 秒。
5. 保存凭证：写入 HA Thread 凭证库，没有首选网络时才设为首选；并链接到 matter-server（`LINK_RETRY_DELAYS` 重试）。保存失败不回滚网络，只提示并留给 `sync_network`/修复页。

### 采纳网络（`manager.async_adopt_network`）

让没有网络的边界路由器加入一个已有网络（多边界路由器）。dataset 的来源：`source_device` 指定的边界路由器（`GetActiveDatasetRequest` 读出；它没有网络报 `source_no_network`），否则 HA 的首选 Thread 网络（`dataset_store.async_get_preferred_dataset`；没有则报 `no_preferred_dataset`）。

1. 来源设备不能是目标自己；`validate_dataset` 校验 dataset（Matter 要求的 TLV 齐全）。
2. 用协调器里的数据预检查目标：已有网络且 Extended PAN ID 相同报 `already_on_network`；**不同则走整网迁移**（见下），没有网络则往下走。
3. 复用 `create.create_network` 下发（`ArmFailSafe` → `SetActiveDatasetRequest` → `CommissioningComplete`），结果不明的处理、轮询、撤销 fail-safe 都与创建一致。dataset 是传入的，不再生成。
4. `_async_save_credentials(allow_existing=True)`：HA 凭证库里通常已有这个网络，`async_add_dataset` 对相同数据返回 `STORED`；库里若已有**更新**的同一网络凭证则返回 `DISCARDED`，此时板子已经加入，不能当作失败，所以采纳允许它。随后同样链接到 matter-server。
5. 繁忙标记复用创建的（`_creating`），期间网络传感器显示 `creating`，按钮不可用。

**目标已在另一个网络里**：Matter 不能删除网络，所以 `_async_migrate_to` 用 `change.migrate_network` 做整网迁移：

1. 预检查与改信道相同（有网络、是 Leader、没有待生效的 Pending），再加“目标网络的 Extended PAN ID 与设备相同 → `same_network`”。
2. `dataset.build_network_migration(当前 Active, 目标)`：目标的完整 dataset + Pending Timestamp + Delay Timer，Active Timestamp 取目标的与“当前 + 1 秒”中较大的（网格会静默忽略不比当前新的 pending）。所以设备最终的 dataset 与首选只差时间戳。
3. 下发、结果不明的处理、等待切换都与改信道共用 `_submit_pending_and_wait`；判定条件是设备的 Extended PAN ID 等于目标的（改信道是信道等于目标）。
4. 成功后读回设备上的 dataset 存回 HA（`allow_existing=True`），繁忙标记用 `_changing`（传感器显示 `changing`）。

迁走的是整个旧网络：Leader 把 Pending 分发给网络里的所有设备，它们一起切换；无法取消。没有验证过的：改 PAN/Extended PAN ID 的整网迁移在真实板子上的表现（固件 `GetPanChangeSupported()` 为 true，之前只迁过信道），以及非 Leader 提交。

### 迁移信道（`change.py`）

预检查：设备有网络、是 Leader、**没有待生效的 Pending Dataset**（`pending_in_progress`）、目标信道与当前不同。待生效检查排在“同信道”之前，因为此时当前信道马上会变。随后 `GetActiveDatasetRequest` 取当前 dataset → `dataset.build_channel_change` 构造 Pending Dataset（校验目标信道在信道掩码内、`delay` 在 30~600 秒）→ `SetPendingDatasetRequest`（timed，无 fail-safe）。下发失败分两类：带状态码的**确定拒绝**立即报 `rejected`；超时、断开等**结果不明**不重发，先读设备状态——有待生效的 Pending（或信道已是目标）就当作已收下并继续等待，没有就报失败（可重试），状态也读不到时报失败并提示看传感器（若其实已收下，再次迁移会被待生效检查拒绝）。收下后每 5 秒读一次状态，直到信道变为目标，最长等 `delay` + 120 秒。超时则报 `not_switched`（界面错误键 `change_not_switched`）（Pending 已交给设备，之后仍可能生效，由自动同步接手）。成功后读回 Active Dataset 保存凭证。

### 同步（`sync_network`）

`GetActiveDatasetRequest` 读回 dataset，写入 HA Thread 凭证库（按 Extended PAN ID 存，`async_add_dataset` 返回 `STORED`/`DISCARDED`）并链接到 matter-server。设备没有网络时报 `no_network`。

## 5. 凭证对账

`credentials.credentials_status`：

| 条件 | 状态 |
| --- | --- |
| 设备上无网络 | `no_network` |
| HA 凭证库没有同 Extended PAN ID 的 dataset | `missing` |
| HA 的 Active Timestamp 比设备旧 | `out_of_sync` |
| HA 的时间戳更新或相等（或读不到 Extended PAN ID） | `saved` |

`ThreadBrManager._on_coordinator_update` 在每次刷新后：

1. 首次刷新不自动同步（启动时的差异只提示，不擅自写，可能是用户有意删掉的）。
2. busy 期间什么都不做。
3. 观察到 ActiveDatasetTimestamp 变化且 `(node, timestamp)` 没尝试过 → 后台自动同步一次，每个变化只尝试一次，失败不重试。
4. 否则 `missing`/`out_of_sync` → 创建 Repairs issue（`credentials_missing` / `credentials_out_of_sync`，`is_fixable=True`，`data={"node_id": N}`）；`saved`/`no_network` → 删除 issue。

Repairs 由 `repairs.py` 里的 `ConfirmRepairFlow` 子类处理，提交即执行同步；issue 只在内存里，每次启动重新评估。

## 6. 信道选择（`channels.choose_channel`）

输入：`zigbee`（ZHA 当前信道，`get_zha_gateway(hass).state.network_info.channel`；读不到就当没有）、`wifi`（选项里的 Wi-Fi 1~14）、`avoid`（选项里的 Thread 信道）。

- 频率：Thread 信道 c 的中心 `2405 + 5(c-11)` MHz；Wi-Fi 信道 w 的中心 `2407 + 5w`，14 号为 2484。
- 硬冲突：Thread 中心频率与 Wi-Fi 中心相差 ≤ 11 MHz 的信道（统一规则，Wi-Fi 13 → 23~26，14 → 25~26）；与 Zigbee 同频；用户指定避开。
- 软冲突：紧邻 Zigbee 信道的两侧。
- 三档依次尝试，返回第一个有结果的：
  1. `PREFERRED = (25, 15, 26)` 中无硬冲突且无软冲突；
  2. `PREFERRED` 中无硬冲突；
  3. 所有信道中无硬冲突、离已知干扰频率最远的（距离相同取号大的，结果确定）。
- 全部被排除抛 `NoChannelAvailable`。返回值带 `tier` 和被排除信道及原因，用于日志。

输入越界（如 Wi-Fi 15）立即抛 `ValueError`。

## 7. 选项与翻译

`config_flow.py`：单实例 ConfigFlow（无输入，只检查 Matter 条目已加载）+ OptionsFlow，字段 `wifi_channels`（1~14 多选）、`avoid_channels`（11~26 多选）。

`translations/en.json`、`zh-Hans.json` 覆盖：device、entity（含各状态）、services（操作名与字段）、exceptions（错误键）、issues、options。`test_translations.py` 校验两种语言键一致、代码里用到的键都存在。服务目前只接受 `device_id` 目标（不接受实体）。

## 8. 固件侧资源占用

### 任务栈

单位字节。“观测峰值”是 FreeRTOS 高水位（`matter tasks`），只覆盖测量时走过的路径。

| 任务 | 配置栈 | 观测峰值用量 | 说明 |
| --- | --- | --- | --- |
| `CHIP`（Matter 主任务） | 16384 | 创建网络、采纳网络（都是 `SetActiveDatasetRequest`）约 9270~9280；BLE 配对约 6730；开机恢复 + 迁移信道约 6420 | 默认 9216 在创建/采纳网络时溢出；`CONFIG_CHIP_TASK_STACK_SIZE` |
| `ot_task` | 10240 | 作为 Leader 创建网络并多次迁移约 5600~5770；**加入已有网络（child → router）约 6600** | 采纳后剩 3632 字节，余量约 35%；`CONFIG_THREAD_TASK_STACK_SIZE`（见下） |
| `Tmr Svc` | 3072 | 约 680~1560 | `CONFIG_FREERTOS_TIMER_TASK_STACK_DEPTH`；HA/matter-server 重启后曾观察到比平时多约 880 字节，原因未确认，故留 3072 |
| `tiT`（lwIP） | 3072 | 约 1860~2020 | |
| `mdns` | 4096 | 约 2130~2490 | |
| `wifi` | 6656 | 约 3320 | |
| `button` | 3072 | 约 800 | |
| `esp_timer` | 5120 | 约 570 | `CONFIG_ESP_TIMER_TASK_STACK_SIZE` |
| `ipc0` / `ipc1` | 1280 | 约 900 / 800 | |
| `IDLE0` / `IDLE1` | 1536 | 约 825 | |
| `sys_evt` | 7200 | — | `CONFIG_ESP_SYSTEM_EVENT_TASK_STACK_SIZE`；读到的剩余值大于该配置，实际栈可能更大 |
| `chip_cli`、`otcli_trans` | — | — | `otcli_trans` 仅 `debug_shell` 构建存在，剩余约 230 字节；跑过 `router table` 之类的表格命令后降到约 80（命令行任务，只在调试时运行） |

测量覆盖的负载：BLE 配对、创建网络、采纳网络（含 child → router 升级）、多次迁移信道、HA 每 30 秒读取、HA 与 matter-server 重启（会话重建）。**未覆盖**：OTA、有 Thread 子设备入网或经过本路由器的流量、整网迁移（采纳时设备已在另一个网络里）。`ESP_MAIN_TASK_STACK_SIZE` 为 5120（`app_main` 任务启动后退出）。

测量方法：用 `debug_shell: true` 构建（打开 `CONFIG_FREERTOS_USE_TRACE_FACILITY` 和 `CONFIG_FREERTOS_USE_STATS_FORMATTING_FUNCTIONS`），在做完要测的操作之后，串口执行 `matter tasks`。水位只减不增、重启归零。`DebugShell.cpp` 用 `vTaskList` 并逐行输出，因为 shell 的 `streamer_printf` 行缓冲约 256 字节，整表一次输出会被截断。

**`ot_task` 的余量。** 加入已有网络时，设备要做 MLE 附着、密钥派生和路由器升级，比自己当 Leader 创建网络多用约 800~1000 字节。采纳后的干净读数（复位后只执行 `matter tasks`，没有跑过其他 `otcli` 命令）：栈 8192 时剩 1596 字节（余量约 19.5%）；提到 10240 后同样的路径用量仍约 6600，剩 3632 字节（余量约 35%）。这条路径之外还没测过有 Thread 子设备入网、SRP/DNS-SD 注册或大量转发流量的情况，它们可能再增加用量，所以多留了这一档。代价是约 2.4 KB 内部 RAM（栈从内部堆分配，稳定运行时内部 RAM 空闲从约 105 KB 降到约 102.7 KB）。栈不在固件镜像里，不影响镜像大小和 OTA 分区。

### 堆与存储

| 项 | 数值 |
| --- | --- |
| 内部 RAM | 总计约 285~288 KB；开机刚完成约 66 KB 空闲，稳定运行（含 Wi-Fi、Thread 已入网）后约 103~120 KB 空闲 |
| PSRAM | 总计约 2.08 MB；空闲约 1.84~1.87 MB |
| Flash | 8 MB；`ota_0` / `ota_1` 各 2500 KB（当前 app 镜像约 1.76 MB）、`fctry` 24 KB（偏移 0x511000）、`rcp_fw` 640 KB（H2 的 RCP 镜像）、`nvs` 48 KB |

### HA 侧开销

每个边界路由器一个 Coordinator，每 30 秒一次 13 路径的 `read_attribute`，事件触发的刷新经 2 秒去抖合并；没有缓存大对象，凭证只在操作和同步时短暂在内存里。HA 进程内存没有专门测量。

## 9. 设计取舍

- **直读设备，不读缓存**：matter-server 缓存里的 0x35 不更新。代价是每 30 秒一次读取，换取状态正确。
- **已有待生效迁移时拒绝，不覆盖**：新 Pending 的 Active Timestamp 是“当前 Active + 1”，与前一个 Pending 相同，网格会静默忽略它，设备却照样接受写入；强行覆盖又会与各设备的延迟计时器竞争，可能把网络分裂。所以读到 `PendingDatasetTimestamp` 非空就拒绝，等它生效后再改。
- **不确定时不重试下发**：创建/迁移的结果不明时，先读设备状态再决定，避免重复下发打乱设备上的事务。
- **启动时不自动写凭证**：只在运行中观察到时间戳变化才自动同步；启动时发现差异走 Repairs，由用户确认。
- **独立设备 + `via_device_id`**：受 HA 2026.9 设备模型限制，不能把实体放进 Matter 设备页。
- **只做创建，不做删除**：Matter 标准没有删除网络的命令。
- **不使用 OpenThread Channel Manager**：它在设备端自动换信道，HA 凭证库无法同步得知，会破坏凭证一致性；迁移信道由 HA 主动发起，凭证保存是同一个流程的一部分。
- **自动选信道只是启发式**：不做射频测量，只避开已知的 ZHA/Wi-Fi/用户指定；需要真实测量要在固件里加能量扫描并通过 Matter 暴露。
- **凭证不进日志**：dataset、PSKc、网络密钥、密码短语都不输出。

## 10. 当前限制

- 迁移信道**无法取消**：Matter 的 TBR cluster 没有清除 Pending Dataset 的命令，提交后只能等延迟（最长 600 秒）到期生效，再迁回去。
- 采纳网络在设备已有别的网络时是整网迁移：旧网络里的所有设备一起走，不能取消，需要 Leader；改 PAN 的整网迁移没有在真实板子上验证。
- 只能创建一次；重建要先在设备上清除（串口 `matter otcli factoryreset` 或长按 BOOT 键）。
- 密码短语不保存。
- 迁移信道只在 Leader 时放行。网络里有 Thread 设备时全部设备是否都能跟上，没有专门测过。
- ZHA 信道读取在 HA 里只用假对象测试；接口 `get_zha_gateway(hass).state.network_info.channel` 在 HA 源码里存在。
- 修复页、选项页、中文文案没有自动化的端到端界面测试，只有单元/集成层面的测试。
- 服务只接受设备目标；`source_device` 同样是设备。

## 11. 开发与测试

```bash
# 环境由 scripts/setup.sh（make build / make deps）创建：.venv，Python >= 3.14.2，装有 HA 2026.9.4
# 单独准备：scripts/setup.sh；.venv 删除后重新运行即可重建
.venv/bin/python -m pytest homeassistant/tests -o asyncio_mode=auto
```

结果：全部通过（257 项）。

| 文件 | 内容 |
| --- | --- |
| `conftest.py` | 把 `thread_br` 目录登记为包 `thread_br_pkg`；提供 `ds/cr/ch/chg` 模块 fixture |
| `fakes.py` | `FakeBoard`（模拟设备的命令与状态；可配置 `SetActive` 成功、超时但已生效、超时未生效、错误、203，以及 `SetPending` 成功、被拒绝、收下但不切换）、`Clock`（虚拟时钟） |
| `test_dataset/create/change/channels/credentials.py` | 纯逻辑，含 Channel Mask 逐位校验、结果不明路径、确定拒绝路径 |
| `test_integration.py` | 在真实 HA 里加载集成：实体、操作、自动同步、Repairs、选项、设备注册 |
| `test_translations.py` | 两种语言键一致、代码用到的键存在 |
| `test_static_checks.py` | 按源码扫描已知的弃用用法（如 `DeviceEntry.config_entries`），与测试环境 HA 版本无关；发现新弃用用法就加进它的表里 |

测试环境注意点：真实 Matter 集成的卸载逻辑在 hass 拆除时会报错，所以用模块级 patch 夹具；`thread`、`zeroconf` 的 `async_setup` 在测试里 patch 掉；ZHA 用假条目并在退出时还原状态；等待操作完成用轮询并 `wait_background_tasks=True`，不要 `async_block_till_done`（它会等到被测流程结束）；虚拟时钟下的 sleep 需要 `await asyncio.sleep(0)` 让出控制权。

关键保护逻辑都有对应的反向验证（故意破坏逻辑，确认测试会失败），例如：busy 期间跳过自动同步和修复提示、确定拒绝不轮询、Channel Mask 位序、弃用用法扫描。

### 命令行工具

`tools/create_network.py` 和 `tools/change_channel.py` 是同一套流程的命令行版，直接连 matter-server 的 WebSocket（如 `kubectl port-forward` 出来的 `ws://127.0.0.1:5580/ws`），不经过 HA，因此**不更新 HA 的凭证库**。默认演练，`--apply` 才真正操作；创建的 `--apply` 必须配 `--save-dataset 文件`（0600，`O_EXCL` 创建，不覆盖已有文件）。创建支持 `--zigbee-channel`、`--wifi-channel`、`--avoid-channel` 参与自动选信道。

### 部署

把 `custom_components/thread_br/` 复制到 HA 的 `custom_components/`（不带 `__pycache__`），然后**重启 HA**。HA 把自定义集成的 Python 模块缓存在进程里，只重载集成不会加载新增模块、翻译和选项页。
