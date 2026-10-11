# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""判断设备上的 Thread 网络,HA 的 Thread 凭证库里是否保存了对应的、够新的凭证。

纯逻辑,不依赖 Home Assistant。凭证库里的条目由调用方整理成 (Extended PAN ID, Active Timestamp)。
"""

from __future__ import annotations

from collections.abc import Iterable

from .create import BorderRouterState

NO_NETWORK = "no_network"  # 设备上没有网络,没有什么可保存的
SAVED = "saved"  # HA 里有这个网络的凭证,且不比设备上的旧
MISSING = "missing"  # HA 里没有这个网络的凭证
OUT_OF_SYNC = "out_of_sync"  # HA 里的凭证比设备上的旧(比如设备上的网络换了信道)

STATES = (NO_NETWORK, SAVED, MISSING, OUT_OF_SYNC)


def credentials_status(state: BorderRouterState, stored: Iterable[tuple[str, int]]) -> str:
    """stored: HA 凭证库里每个条目的 (Extended PAN ID 十六进制小写 16 位, Active Timestamp)。

    HA 里的凭证比设备上的还新时算 SAVED:这种情况下提示"同步"没有意义,也会被凭证库拒绝(它只收更新的)。
    """
    if not state.has_network:
        return NO_NETWORK
    if state.extended_pan_id is None:
        # 设备上有网络但读不到 Extended PAN ID(诊断属性读取失败):无法判断,不冤枉用户
        return SAVED
    ext_pan_id = f"{state.extended_pan_id:016x}"
    timestamps = [ts for ext, ts in stored if ext.lower() == ext_pan_id]
    if not timestamps:
        return MISSING
    return SAVED if max(timestamps) >= state.active_timestamp else OUT_OF_SYNC
