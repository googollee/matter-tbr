# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""通过 Matter 在 Thread 边界路由器上创建 Thread 网络。

不依赖 Home Assistant:只需要一个提供 send_command() 的对象,
HA 里就是 matter 集成的 MatterClient,命令行工具里是 WebSocket 适配器。
命令的格式与 matter-server 的 WebSocket API 一致。

dataset 含网络密钥,本模块不会把它写进日志或异常消息。
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
import re
import time
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)

GENERAL_COMMISSIONING_CLUSTER = 0x0030
THREAD_BR_MANAGEMENT_CLUSTER = 0x0452
ATTR_INTERFACE_ENABLED = 3
ATTR_ACTIVE_DATASET_TIMESTAMP = 4
ATTR_PENDING_DATASET_TIMESTAMP = 5
DEFAULT_ENDPOINT = 1

# Thread Network Diagnostics cluster 的属性(与边界路由器在同一个端点)
THREAD_NETWORK_DIAGNOSTICS_CLUSTER = 0x0035
DIAG_CHANNEL = 0
DIAG_ROUTING_ROLE = 1
DIAG_NETWORK_NAME = 2
DIAG_PAN_ID = 3
DIAG_EXTENDED_PAN_ID = 4
DIAG_NEIGHBOR_TABLE = 7
DIAG_ROUTE_TABLE = 8
DIAG_PARTITION_ID = 9
DIAG_LEADER_ROUTER_ID = 13
DIAG_ACTIVE_NETWORK_FAULTS = 62
ROLE_LEADER = 6

# SetActiveDatasetRequest 要等 Thread 网络真正建立才返回,实测 6~33 秒
SET_DATASET_INTERACTION_TIMEOUT_MS = 60_000
TIMED_REQUEST_TIMEOUT_MS = 10_000
FAILSAFE_SECONDS = 120
POLL_INTERVAL = 2.0
POLL_TIMEOUT = 60.0
# matter 客户端的 send_command 没有自带超时,服务器不回应就会一直等
COMMAND_TIMEOUT = 30.0
SET_DATASET_TIMEOUT = SET_DATASET_INTERACTION_TIMEOUT_MS / 1000 + 15  # 设备侧上限再加余量


class MatterApi(Protocol):
    """matter-server 的命令接口。HA 的 MatterClient 和 WebSocket 适配器都满足它。"""

    async def send_command(self, command: str, **args: Any) -> Any: ...


class CreateNetworkError(Exception):
    """创建网络失败。code 是稳定的错误标识,供界面映射成提示。"""

    ALREADY_EXISTS = "already_exists"
    FAILSAFE = "failsafe"
    SET_DATASET = "set_dataset"
    COMMISSIONING_COMPLETE = "commissioning_complete"
    NOT_CONFIRMED = "not_confirmed"
    PROBE = "probe"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NetworkState:
    active_timestamp: int | None
    interface_enabled: bool | None

    @property
    def has_network(self) -> bool:
        return self.active_timestamp is not None


@dataclass
class CreateResult:
    dry_run: bool
    state: NetworkState
    steps: list[str] = field(default_factory=list)


async def _send(api: MatterApi, timeout: float, command: str, **args: Any) -> Any:
    """发送命令并限制等待时间;超时抛 asyncio.TimeoutError。"""
    return await asyncio.wait_for(api.send_command(command, **args), timeout)


@dataclass(frozen=True)
class BorderRouterState:
    """边界路由器当前的完整状态,一次 read_attribute 直接从设备读出。

    不用 matter-server 缓存的节点数据:实测 Thread 网络诊断(0x35)的属性在缓存里不会随设备更新,
    网络建立之后仍停留在旧值;只有 TBR 管理 cluster 的属性会被推送。
    """

    interface_enabled: bool | None
    active_timestamp: int | None
    pending_timestamp: int | None
    channel: int | None
    role: int | None
    network_name: str | None
    pan_id: int | None
    extended_pan_id: int | None
    neighbor_count: int | None
    route_count: int | None
    partition_id: int | None
    leader_router_id: int | None
    faults: tuple[int, ...]

    @property
    def has_network(self) -> bool:
        return self.active_timestamp is not None

    @property
    def has_pending(self) -> bool:
        """设备上有一个还没生效的 Pending Dataset(迁移信道的延迟计时器还在走)。"""
        return self.pending_timestamp is not None

    @property
    def is_leader(self) -> bool:
        return self.role == ROLE_LEADER


def _count(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


async def read_border_router_state(api: MatterApi, node_id: int, endpoint_id: int = DEFAULT_ENDPOINT) -> BorderRouterState:
    """一次读取边界路由器的全部状态。"""
    br = f"{endpoint_id}/{THREAD_BR_MANAGEMENT_CLUSTER}"
    diag = f"{endpoint_id}/{THREAD_NETWORK_DIAGNOSTICS_CLUSTER}"
    paths = [
        f"{br}/{ATTR_INTERFACE_ENABLED}",
        f"{br}/{ATTR_ACTIVE_DATASET_TIMESTAMP}",
        f"{br}/{ATTR_PENDING_DATASET_TIMESTAMP}",
        *(f"{diag}/{a}" for a in (DIAG_CHANNEL, DIAG_ROUTING_ROLE, DIAG_NETWORK_NAME, DIAG_PAN_ID, DIAG_EXTENDED_PAN_ID,
                                  DIAG_NEIGHBOR_TABLE, DIAG_ROUTE_TABLE, DIAG_PARTITION_ID, DIAG_LEADER_ROUTER_ID,
                                  DIAG_ACTIVE_NETWORK_FAULTS)),
    ]
    result = await _send(api, COMMAND_TIMEOUT, "read_attribute", node_id=node_id, attribute_path=paths)
    result = result or {}
    faults = result.get(f"{diag}/{DIAG_ACTIVE_NETWORK_FAULTS}")
    return BorderRouterState(
        interface_enabled=result.get(f"{br}/{ATTR_INTERFACE_ENABLED}"),
        active_timestamp=result.get(f"{br}/{ATTR_ACTIVE_DATASET_TIMESTAMP}"),
        pending_timestamp=result.get(f"{br}/{ATTR_PENDING_DATASET_TIMESTAMP}"),
        channel=result.get(f"{diag}/{DIAG_CHANNEL}"),
        role=result.get(f"{diag}/{DIAG_ROUTING_ROLE}"),
        network_name=result.get(f"{diag}/{DIAG_NETWORK_NAME}"),
        pan_id=result.get(f"{diag}/{DIAG_PAN_ID}"),
        extended_pan_id=result.get(f"{diag}/{DIAG_EXTENDED_PAN_ID}"),
        neighbor_count=_count(result.get(f"{diag}/{DIAG_NEIGHBOR_TABLE}")),
        route_count=_count(result.get(f"{diag}/{DIAG_ROUTE_TABLE}")),
        partition_id=result.get(f"{diag}/{DIAG_PARTITION_ID}"),
        leader_router_id=result.get(f"{diag}/{DIAG_LEADER_ROUTER_ID}"),
        faults=tuple(faults) if isinstance(faults, list) else (),
    )


async def read_state(api: MatterApi, node_id: int, endpoint_id: int = DEFAULT_ENDPOINT) -> NetworkState:
    """读取边界路由器当前是否已有 Thread 网络。"""
    prefix = f"{endpoint_id}/{THREAD_BR_MANAGEMENT_CLUSTER}"
    result = await _send(
        api,
        COMMAND_TIMEOUT,
        "read_attribute",
        node_id=node_id,
        attribute_path=[f"{prefix}/{ATTR_INTERFACE_ENABLED}", f"{prefix}/{ATTR_ACTIVE_DATASET_TIMESTAMP}"],
    )
    return NetworkState(
        active_timestamp=result.get(f"{prefix}/{ATTR_ACTIVE_DATASET_TIMESTAMP}"),
        interface_enabled=result.get(f"{prefix}/{ATTR_INTERFACE_ENABLED}"),
    )


async def read_active_dataset(api: MatterApi, node_id: int, endpoint_id: int = DEFAULT_ENDPOINT) -> str | None:
    """从设备读回当前的 Active Dataset(十六进制 TLV);设备上没有网络时返回 None。

    用于创建成功但 HA 没能保存凭证时恢复:凭证含密钥,调用方不要写日志。
    """
    result = await _send(
        api,
        COMMAND_TIMEOUT,
        "device_command",
        node_id=node_id,
        endpoint_id=endpoint_id,
        cluster_id=THREAD_BR_MANAGEMENT_CLUSTER,
        command_name="GetActiveDatasetRequest",
        payload={},
    )
    encoded = (result or {}).get("dataset")
    if not encoded:
        return None
    return base64.b64decode(encoded).hex()


def find_border_router_endpoint(attributes: dict[str, Any]) -> int | None:
    """在节点的属性表(键形如 '1/1106/4')里找出带 Thread BR 管理 cluster 的端点。"""
    marker = f"/{THREAD_BR_MANAGEMENT_CLUSTER}/"
    for key in attributes:
        if marker in key:
            return int(key.split("/", 1)[0])
    return None


async def _arm_fail_safe(api: MatterApi, node_id: int, seconds: int) -> None:
    try:
        result = await _send(
            api,
            COMMAND_TIMEOUT,
            "device_command",
            node_id=node_id,
            endpoint_id=0,
            cluster_id=GENERAL_COMMISSIONING_CLUSTER,
            command_name="ArmFailSafe",
            payload={"expiryLengthSeconds": seconds, "breadcrumb": 1 if seconds else 0},
        )
    except Exception as err:  # noqa: BLE001
        raise CreateNetworkError(CreateNetworkError.FAILSAFE, f"ArmFailSafe({seconds}) 失败:{_describe(err)}") from err
    code = (result or {}).get("errorCode", 0)
    if code != 0:
        raise CreateNetworkError(CreateNetworkError.FAILSAFE, f"ArmFailSafe({seconds}) 被拒绝,errorCode={code}")


async def _disarm_quietly(api: MatterApi, node_id: int) -> None:
    """尽力撤销 fail-safe;失败也只记日志(fail-safe 到期会自动撤销)。"""
    try:
        await _arm_fail_safe(api, node_id, 0)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("撤销 fail-safe 失败,它会在到期后自动撤销:%s", err)


def _describe(err: Exception) -> str:
    """异常的简短描述;超时的异常消息通常为空。"""
    return str(err) or type(err).__name__


def _set_dataset_args(node_id: int, endpoint_id: int, tlv_hex: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "endpoint_id": endpoint_id,
        "cluster_id": THREAD_BR_MANAGEMENT_CLUSTER,
        "command_name": "SetActiveDatasetRequest",
        # matter-server 的字节字段用 base64,不是十六进制
        "payload": {"activeDataset": base64.b64encode(bytes.fromhex(tlv_hex)).decode()},
        "timed_request_timeout_ms": TIMED_REQUEST_TIMEOUT_MS,
        "interaction_timeout_ms": SET_DATASET_INTERACTION_TIMEOUT_MS,
    }


async def _probe_set_dataset(api: MatterApi, node_id: int, endpoint_id: int, tlv_hex: str) -> None:
    """不开 fail-safe 发一次 SetActiveDatasetRequest:设备在检查 fail-safe 之前就会拒绝,
    所以不会改动任何东西。收到 'Failsafe required' 说明路径、会话类型、字节编码都对。"""
    try:
        await _send(api, COMMAND_TIMEOUT, "device_command", **_set_dataset_args(node_id, endpoint_id, tlv_hex))
    except Exception as err:  # noqa: BLE001
        if "failsafe required" in str(err).lower():
            return
        raise CreateNetworkError(CreateNetworkError.PROBE, f"探测收到意外的错误:{_describe(err)}") from err
    raise CreateNetworkError(CreateNetworkError.PROBE, "探测没有被拒绝,设备行为与预期不符,已停止")


def _is_definitive_rejection(err: Exception) -> bool:
    """设备明确拒绝了命令(错误信息带 Matter 状态码,如 'Invalid command (code 133)')。

    这种情况结果是确定的:命令没有生效,不需要再轮询。超时、连接断开、
    'Operation aborted' 之类没有状态码的错误才是"结果不明"。
    """
    return re.search(r"\(code \d+\)", str(err)) is not None


def _is_invalid_in_state(err: Exception) -> bool:
    text = str(err).lower()
    return "invalid in state" in text or "code 203" in text


async def _wait_for_network(
    api: MatterApi,
    node_id: int,
    endpoint_id: int,
    *,
    timeout: float,
    interval: float,
    sleep: Callable[[float], Awaitable[None]],
    clock: Callable[[], float],
) -> NetworkState:
    deadline = clock() + timeout
    state = await read_state(api, node_id, endpoint_id)
    while not state.has_network and clock() < deadline:
        await sleep(interval)
        state = await read_state(api, node_id, endpoint_id)
    return state


async def create_network(
    api: MatterApi,
    node_id: int,
    tlv_hex: str,
    *,
    endpoint_id: int = DEFAULT_ENDPOINT,
    dry_run: bool = False,
    on_step: Callable[[str], None] | None = None,
    poll_interval: float = POLL_INTERVAL,
    poll_timeout: float = POLL_TIMEOUT,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> CreateResult:
    """在边界路由器上创建网络。

    流程:预检查 → ArmFailSafe → SetActiveDatasetRequest → CommissioningComplete → 确认。
    dry_run 只做预检查、命令格式探测和一次 ArmFailSafe/撤销,不会创建网络。
    SetActiveDatasetRequest 的结果不明时(超时、连接断开)不重试,而是读属性判断,
    重试可能打乱设备上正在进行的操作。
    """
    result = CreateResult(dry_run=dry_run, state=NetworkState(None, None))

    def step(message: str) -> None:
        result.steps.append(message)
        _LOGGER.debug("%s", message)
        if on_step:
            on_step(message)

    step("检查边界路由器当前状态")
    state = await read_state(api, node_id, endpoint_id)
    result.state = state
    if state.has_network:
        raise CreateNetworkError(
            CreateNetworkError.ALREADY_EXISTS,
            "边界路由器上已经有 Thread 网络。Matter 没有删除网络的命令,"
            "需要在设备上清除后才能重新创建",
        )

    if dry_run:
        step("演练:探测 SetActiveDatasetRequest 的命令格式(设备会因未开 fail-safe 而拒绝)")
        await _probe_set_dataset(api, node_id, endpoint_id, tlv_hex)
        step("演练:开启 fail-safe 后立即撤销")
        await _arm_fail_safe(api, node_id, 60)
        await _disarm_quietly(api, node_id)
        step("演练完成,没有创建网络")
        return result

    step("开启 fail-safe")
    await _arm_fail_safe(api, node_id, FAILSAFE_SECONDS)

    step("下发 dataset,等待 Thread 网络建立(通常 6~33 秒)")
    try:
        await _send(api, SET_DATASET_TIMEOUT, "device_command", **_set_dataset_args(node_id, endpoint_id, tlv_hex))
    except Exception as err:  # noqa: BLE001
        if _is_invalid_in_state(err):
            await _disarm_quietly(api, node_id)
            raise CreateNetworkError(
                CreateNetworkError.ALREADY_EXISTS, "边界路由器上已经有 Thread 网络,设备拒绝了新的 dataset"
            ) from err
        if _is_definitive_rejection(err):
            await _disarm_quietly(api, node_id)
            raise CreateNetworkError(CreateNetworkError.SET_DATASET, f"设备拒绝了 dataset:{_describe(err)}") from err
        # 结果不明:命令可能已经生效,只是响应没回来。不重试,先看设备状态
        step(f"下发命令没有正常返回({type(err).__name__}),读取设备状态确认")
        state = await _wait_for_network(
            api, node_id, endpoint_id, timeout=poll_timeout, interval=poll_interval, sleep=sleep, clock=clock
        )
        if not state.has_network:
            await _disarm_quietly(api, node_id)
            raise CreateNetworkError(CreateNetworkError.SET_DATASET, f"下发 dataset 失败:{_describe(err)}") from err

    step("提交(CommissioningComplete)")
    for attempt in (1, 2):
        try:
            response = await _send(
                api,
                COMMAND_TIMEOUT,
                "device_command",
                node_id=node_id,
                endpoint_id=0,
                cluster_id=GENERAL_COMMISSIONING_CLUSTER,
                command_name="CommissioningComplete",
                payload={},
            )
            code = (response or {}).get("errorCode", 0)
            if code != 0:
                raise CreateNetworkError(
                    CreateNetworkError.COMMISSIONING_COMPLETE, f"CommissioningComplete 被拒绝,errorCode={code}"
                )
            break
        except CreateNetworkError:
            raise
        except Exception as err:  # noqa: BLE001
            if attempt == 2:
                raise CreateNetworkError(
                    CreateNetworkError.COMMISSIONING_COMPLETE,
                    f"提交失败,fail-safe 到期后网络可能被回滚:{_describe(err)}",
                ) from err
            step("提交没有正常返回,重试一次")
            await sleep(poll_interval)

    step("确认网络已建立并持久化")
    state = await read_state(api, node_id, endpoint_id)
    result.state = state
    if not state.has_network:
        raise CreateNetworkError(CreateNetworkError.NOT_CONFIRMED, "提交后设备仍显示没有 Thread 网络")
    step("完成")
    return result
