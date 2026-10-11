# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""为新建的 Thread 网络选择信道:避开已知的 Wi-Fi 和 Zigbee 干扰。

这是启发式,不是测量:它只能避开调用方告诉它的干扰源。Thread 在 2.4GHz 的 11~26 信道,
每个信道间隔 5MHz、信号约 2MHz 宽;Wi-Fi 信号约 20MHz 宽。Zigbee 与 Thread 的信道号和频率相同。

纯逻辑,不依赖 Home Assistant。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

MIN_CHANNEL, MAX_CHANNEL = 11, 26
THREAD_CHANNELS = tuple(range(MIN_CHANNEL, MAX_CHANNEL + 1))
WIFI_CHANNELS = tuple(range(1, 15))  # 2.4GHz Wi-Fi 信道 1~14

# 优先顺序:这些信道夹在 Wi-Fi 的 1/6/11 之间的空隙里。
# 25 离常见的 Wi-Fi 和 Zigbee 信道都最远;15 在 Wi-Fi 6 的边缘;
# 26 在个别射频上会降低发射功率,所以排在最后(这块板子是否如此没有核实)
PREFERRED = (25, 15, 26)

# Wi-Fi 信号带宽约 20MHz,中心两侧各 10MHz 再加一点裙边
_WIFI_HALF_WIDTH_MHZ = 11


class NoChannelAvailable(ValueError):
    """所有信道都被排除了。"""


def thread_frequency(channel: int) -> int:
    """Thread(IEEE 802.15.4)信道的中心频率,MHz。"""
    return 2405 + 5 * (channel - MIN_CHANNEL)


def wifi_frequency(channel: int) -> int:
    """2.4GHz Wi-Fi 信道的中心频率,MHz。14 号信道是特例。"""
    return 2484 if channel == 14 else 2407 + 5 * channel


def wifi_overlap(wifi_channel: int) -> set[int]:
    """被这个 Wi-Fi 信道压住的 Thread 信道(中心频率落在 Wi-Fi 信号带宽内)。

    例:Wi-Fi 1 → Thread 11~14,Wi-Fi 6 → 16~19,Wi-Fi 11 → 21~24。
    """
    center = wifi_frequency(wifi_channel)
    return {c for c in THREAD_CHANNELS if abs(thread_frequency(c) - center) <= _WIFI_HALF_WIDTH_MHZ}


@dataclass(frozen=True)
class Selection:
    channel: int
    # 1:首选信道且避开了所有干扰及其邻道;2:首选信道,只避开了硬冲突;3:不在首选里,按离干扰源最远选
    tier: int
    excluded: dict[int, str] = field(default_factory=dict)  # 被排除的信道 -> 原因,用于日志


def _check(values: Iterable[int], low: int, high: int, what: str) -> list[int]:
    result = list(values)
    for v in result:
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
            raise ValueError(f"{what}必须是 {low}~{high} 的整数,当前为 {v!r}")
    return result


def choose_channel(
    *,
    zigbee: Iterable[int] = (),
    wifi: Iterable[int] = (),
    avoid: Iterable[int] = (),
) -> Selection:
    """选一个信道。

    zigbee: 附近 Zigbee 网络用的信道(与 Thread 同频);同频是硬冲突,相邻一个信道算软冲突。
    wifi:   附近 Wi-Fi 网络用的 2.4GHz 信道(1~14),被压住的 Thread 信道是硬冲突。
    avoid:  用户指定要避开的 Thread 信道,硬冲突。

    依次尝试三档,取第一个有结果的:
      1. 首选信道(25、15、26),排除硬冲突和软冲突;
      2. 首选信道,只排除硬冲突;
      3. 所有信道,排除硬冲突,选离所有已知干扰源频率最远的。
    全部排除时抛 NoChannelAvailable。
    """
    zigbee = _check(zigbee, MIN_CHANNEL, MAX_CHANNEL, "Zigbee 信道")
    wifi = _check(wifi, WIFI_CHANNELS[0], WIFI_CHANNELS[-1], "Wi-Fi 信道")
    avoid = _check(avoid, MIN_CHANNEL, MAX_CHANNEL, "要避开的信道")

    hard: dict[int, str] = {}
    soft: dict[int, str] = {}
    for c in avoid:
        hard[c] = "用户指定避开"
    for w in wifi:
        for c in wifi_overlap(w):
            hard.setdefault(c, f"Wi-Fi {w} 信道的带宽内")
    for z in zigbee:
        hard.setdefault(z, f"与 Zigbee {z} 信道同频")
        for neighbor in (z - 1, z + 1):
            if MIN_CHANNEL <= neighbor <= MAX_CHANNEL:
                soft.setdefault(neighbor, f"紧邻 Zigbee {z} 信道")

    for tier, blocked in ((1, {**soft, **hard}), (2, hard)):
        for c in PREFERRED:
            if c not in blocked:
                return Selection(c, tier, dict(blocked))

    interferers = [wifi_frequency(w) for w in wifi] + [thread_frequency(z) for z in zigbee]
    candidates = [c for c in THREAD_CHANNELS if c not in hard]
    if not candidates:
        raise NoChannelAvailable("所有 Thread 信道都被排除了")
    # 离最近的干扰源越远越好;距离相同时取信道号大的(固定顺序,结果可预测)
    best = max(candidates, key=lambda c: (min((abs(thread_frequency(c) - f) for f in interferers), default=0), c))
    return Selection(best, 3, dict(hard))
