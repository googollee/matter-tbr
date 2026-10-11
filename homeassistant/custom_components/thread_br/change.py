# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""通过 Matter 把边界路由器上的 Thread 网络迁移到新信道,不用重建网络。

做法:读出当前 Active Dataset,构造带延迟计时器的 Pending Dataset(新信道),用
SetPendingDatasetRequest 交给设备;延迟到期后整个网络(含所有设备)同时切换。
SetPendingDatasetRequest 不需要开 fail-safe,是同步调用。

同 create.py 一样不依赖 Home Assistant;dataset 含密钥,不写进日志和异常消息。
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time

from .create import (
    COMMAND_TIMEOUT,
    DEFAULT_ENDPOINT,
    THREAD_BR_MANAGEMENT_CLUSTER,
    BorderRouterState,
    MatterApi,
    _describe,
    _is_definitive_rejection,
    _send,
    read_active_dataset,
    read_border_router_state,
)
from .dataset import DEFAULT_DELAY_SECONDS, DatasetError, build_channel_change, build_network_migration, extended_pan_id_of

POLL_INTERVAL = 5.0
EXTRA_WAIT = 120.0  # 延迟到期后再多等多久(切换需要时间)


class ChangeChannelError(Exception):
    """改信道失败。code 是稳定的错误标识,供界面映射成提示。"""

    NO_NETWORK = "no_network"
    NOT_LEADER = "not_leader"
    SAME_CHANNEL = "same_channel"
    PENDING_IN_PROGRESS = "pending_in_progress"
    SAME_NETWORK = "same_network"
    INVALID = "invalid"
    REJECTED = "rejected"
    NOT_SWITCHED = "not_switched"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ChangeResult:
    old_channel: int
    new_channel: int
    state: BorderRouterState
    steps: list[str] = field(default_factory=list)


async def change_channel(
    api: MatterApi,
    node_id: int,
    new_channel: int,
    *,
    endpoint_id: int = DEFAULT_ENDPOINT,
    delay_seconds: int = DEFAULT_DELAY_SECONDS,
    on_step: Callable[[str], None] | None = None,
    poll_interval: float = POLL_INTERVAL,
    extra_wait: float = EXTRA_WAIT,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ChangeResult:
    """把网络迁到 new_channel,并等到设备报告已经在新信道上。

    超时没切换时抛 NOT_SWITCHED:Pending Dataset 已经交给设备,之后仍可能生效,
    所以这不一定是失败,调用方应当提示用户稍后再看。
    """
    result = ChangeResult(old_channel=0, new_channel=new_channel, state=None)  # type: ignore[arg-type]

    def step(message: str) -> None:
        result.steps.append(message)
        if on_step:
            on_step(message)

    step("检查边界路由器状态")
    state = await read_border_router_state(api, node_id, endpoint_id)
    if not state.has_network:
        raise ChangeChannelError(ChangeChannelError.NO_NETWORK, "边界路由器上没有 Thread 网络")
    if not state.is_leader:
        # 只有 Leader 会把 Pending Dataset 分发给全网并计时切换;其他角色的行为我没有验证,不冒险
        raise ChangeChannelError(ChangeChannelError.NOT_LEADER, f"边界路由器不是 Leader(角色 {state.role}),不能改信道")
    if state.has_pending:
        # 已有迁移在等延迟计时器。新的 Pending 的 Active Timestamp 与它相同(都是当前值 + 1),
        # 网格会静默忽略;强行覆盖又会和各设备的计时器竞争,可能把网络分裂。所以只拒绝,等它生效后再改
        raise ChangeChannelError(ChangeChannelError.PENDING_IN_PROGRESS, "设备上已有一次待生效的迁移,等它完成后再改信道")
    if state.channel == new_channel:
        raise ChangeChannelError(ChangeChannelError.SAME_CHANNEL, f"网络已经在信道 {new_channel}")
    result.old_channel = state.channel or 0

    step("读取当前网络的 dataset")
    active = await read_active_dataset(api, node_id, endpoint_id)
    if active is None:
        raise ChangeChannelError(ChangeChannelError.NO_NETWORK, "读不到当前网络的 dataset")
    try:
        pending = build_channel_change(active, new_channel, delay_seconds=delay_seconds, pending_seconds=int(now()))
    except DatasetError as err:
        raise ChangeChannelError(ChangeChannelError.INVALID, str(err)) from err

    result.state = await _submit_pending_and_wait(
        api,
        node_id,
        endpoint_id,
        pending,
        reached=lambda s: s.channel == new_channel,
        what=f"信道 {new_channel}",
        delay_seconds=delay_seconds,
        step=step,
        poll_interval=poll_interval,
        extra_wait=extra_wait,
        sleep=sleep,
        clock=clock,
    )
    step("完成")
    return result


async def _submit_pending_and_wait(
    api: MatterApi,
    node_id: int,
    endpoint_id: int,
    pending_hex: str,
    *,
    reached: Callable[[BorderRouterState], bool],
    what: str,
    delay_seconds: int,
    step: Callable[[str], None],
    poll_interval: float,
    extra_wait: float,
    sleep: Callable[[float], Awaitable[None]],
    clock: Callable[[], float],
) -> BorderRouterState:
    """下发 Pending Dataset,等设备报告 reached(state);超时抛 NOT_SWITCHED(Pending 已交给设备,之后仍可能生效)。

    下发的结果不明(超时、断开)时不重发,先读设备上有没有待生效的迁移,见下。
    """
    step(f"下发 Pending Dataset:{delay_seconds} 秒后切换到{what}")
    try:
        await _send(
            api,
            COMMAND_TIMEOUT,
            "device_command",
            node_id=node_id,
            endpoint_id=endpoint_id,
            cluster_id=THREAD_BR_MANAGEMENT_CLUSTER,
            command_name="SetPendingDatasetRequest",
            payload={"pendingDataset": base64.b64encode(bytes.fromhex(pending_hex)).decode()},
            timed_request_timeout_ms=10_000,
            interaction_timeout_ms=30_000,
        )
    except Exception as err:  # noqa: BLE001
        if _is_definitive_rejection(err):
            raise ChangeChannelError(ChangeChannelError.REJECTED, f"设备拒绝了 Pending Dataset:{_describe(err)}") from err
        # 结果不明(超时、连接断开):命令可能已被设备收下,只是响应没回来。不重试,先看设备上有没有待生效的迁移
        step(f"下发命令没有正常返回({type(err).__name__}),读取设备状态确认")
        try:
            state = await read_border_router_state(api, node_id, endpoint_id)
        except Exception as read_err:  # noqa: BLE001
            raise ChangeChannelError(
                ChangeChannelError.REJECTED,
                f"下发命令没有正常返回({_describe(err)}),也读不到设备状态,不知道迁移是否已被收下;稍后看网络传感器",
            ) from read_err
        if not (state.has_pending or reached(state)):
            raise ChangeChannelError(
                ChangeChannelError.REJECTED, f"下发命令没有正常返回({_describe(err)}),设备上也没有待生效的迁移"
            ) from err
        step("设备已收下迁移,继续等待切换")

    step("等待网络切换")
    deadline = clock() + delay_seconds + extra_wait
    while True:
        await sleep(poll_interval)
        state = await read_border_router_state(api, node_id, endpoint_id)
        if reached(state):
            return state
        if clock() >= deadline:
            raise ChangeChannelError(
                ChangeChannelError.NOT_SWITCHED,
                f"{delay_seconds + int(extra_wait)} 秒内网络没有切换到{what};Pending Dataset 已交给设备,之后仍可能生效",
            )


@dataclass
class MigrateResult:
    state: BorderRouterState
    steps: list[str] = field(default_factory=list)


async def migrate_network(
    api: MatterApi,
    node_id: int,
    target_tlv_hex: str,
    *,
    endpoint_id: int = DEFAULT_ENDPOINT,
    delay_seconds: int = DEFAULT_DELAY_SECONDS,
    on_step: Callable[[str], None] | None = None,
    poll_interval: float = POLL_INTERVAL,
    extra_wait: float = EXTRA_WAIT,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> MigrateResult:
    """把设备上已有的网络整个迁到 target 网络(整网迁移),并等到设备报告已在 target 网络里。

    迁移的是整个旧网络:Leader 把 Pending Dataset 分发给网络里的所有设备,它们一起切换。
    条件与改信道相同(有网络、是 Leader、没有待生效迁移)。超时没切换抛 NOT_SWITCHED。
    """
    result = MigrateResult(state=None)  # type: ignore[arg-type]

    def step(message: str) -> None:
        result.steps.append(message)
        if on_step:
            on_step(message)

    try:
        target_ext = extended_pan_id_of(target_tlv_hex)
    except DatasetError as err:
        raise ChangeChannelError(ChangeChannelError.INVALID, str(err)) from err

    step("检查边界路由器状态")
    state = await read_border_router_state(api, node_id, endpoint_id)
    if not state.has_network:
        raise ChangeChannelError(ChangeChannelError.NO_NETWORK, "边界路由器上没有 Thread 网络")
    if not state.is_leader:
        raise ChangeChannelError(ChangeChannelError.NOT_LEADER, f"边界路由器不是 Leader(角色 {state.role}),不能整网迁移")
    if state.has_pending:
        raise ChangeChannelError(ChangeChannelError.PENDING_IN_PROGRESS, "设备上已有一次待生效的迁移,等它完成后再试")
    if state.extended_pan_id == target_ext:
        raise ChangeChannelError(ChangeChannelError.SAME_NETWORK, "边界路由器已经在这个网络里")

    step("读取当前网络的 dataset")
    active = await read_active_dataset(api, node_id, endpoint_id)
    if active is None:
        raise ChangeChannelError(ChangeChannelError.NO_NETWORK, "读不到当前网络的 dataset")
    try:
        pending = build_network_migration(active, target_tlv_hex, delay_seconds=delay_seconds, pending_seconds=int(now()))
    except DatasetError as err:
        raise ChangeChannelError(ChangeChannelError.INVALID, str(err)) from err

    result.state = await _submit_pending_and_wait(
        api,
        node_id,
        endpoint_id,
        pending,
        reached=lambda s: s.extended_pan_id == target_ext,
        what="目标网络",
        delay_seconds=delay_seconds,
        step=step,
        poll_interval=poll_interval,
        extra_wait=extra_wait,
        sleep=sleep,
        clock=clock,
    )
    step("完成")
    return result
