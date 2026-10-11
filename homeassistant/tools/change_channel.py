#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""通过 matter-server 把边界路由器上的 Thread 网络迁到新信道(命令行版,用于开发和排查)。

默认只演练(dry-run):检查状态、构造 Pending Dataset 并校验,不会下发。
真正迁移要加 --apply:延迟到期后整个网络(包括所有设备)同时切换到新信道。
这个工具只改设备,不更新 HA 的凭证库;HA 里的集成运行时会观察到变化并自动同步,
没有运行的话用 thread_br.sync_network 操作补存。

用法示例:
  .venv/bin/python homeassistant/tools/change_channel.py --channel 25            # 演练
  .venv/bin/python homeassistant/tools/change_channel.py --channel 25 --apply --delay 30
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from pathlib import Path
import sys
import types
from typing import Any

import websockets

_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "thread_br"
# thread_br 的子模块之间用相对导入;这里把目录登记成一个包(不执行依赖 HA 的 __init__.py)再导入
_package = types.ModuleType("thread_br_pkg")
_package.__path__ = [str(_PACKAGE_DIR)]
sys.modules["thread_br_pkg"] = _package
channels = importlib.import_module("thread_br_pkg.channels")
change = importlib.import_module("thread_br_pkg.change")
create = importlib.import_module("thread_br_pkg.create")
dataset = importlib.import_module("thread_br_pkg.dataset")


class MatterApiError(Exception):
    """matter-server 返回了错误。"""


class WsMatterApi:
    """matter-server WebSocket API 的最小适配器,满足 create.MatterApi。"""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._next_id = 0

    async def send_command(self, command: str, **args: Any) -> Any:
        self._next_id += 1
        message_id = str(self._next_id)
        await self._ws.send(json.dumps({"message_id": message_id, "command": command, "args": args}))
        while True:
            message = json.loads(await self._ws.recv())
            if message.get("message_id") != message_id:
                continue
            if "error_code" in message:
                raise MatterApiError(message.get("details") or f"error_code={message['error_code']}")
            return message.get("result")


async def find_node(api: WsMatterApi, node_id: int | None) -> tuple[int, int]:
    nodes = await api.send_command("get_nodes")
    candidates = [
        (n["node_id"], ep)
        for n in nodes
        if (ep := create.find_border_router_endpoint(n.get("attributes", {}))) is not None and (node_id is None or n["node_id"] == node_id)
    ]
    if len(candidates) != 1:
        raise SystemExit("没有找到边界路由器节点" if not candidates else "找到多个边界路由器节点,请用 --node-id 指定")
    return candidates[0]


def describe(state: Any) -> str:
    return (f"信道 {state.channel},网络 {state.network_name},角色 {state.role}(6=Leader),"
            f"邻居 {state.neighbor_count},路由器 {state.route_count}")


async def main_async(args: argparse.Namespace) -> int:
    channel = args.channel
    if channel is None:
        try:
            selection = channels.choose_channel(zigbee=args.zigbee_channel, wifi=args.wifi_channel, avoid=args.avoid_channel)
        except (ValueError, channels.NoChannelAvailable) as err:
            print(f"参数错误:{err}", file=sys.stderr)
            return 2
        channel = selection.channel
        print(f"自动选择信道 {channel}(第 {selection.tier} 档)")
    async with websockets.connect(args.url) as ws:
        info = json.loads(await ws.recv())
        print(f"已连接 {info.get('sdk_version')}")
        api = WsMatterApi(ws)
        node_id, endpoint = await find_node(api, args.node_id)
        before = await create.read_border_router_state(api, node_id, endpoint)
        print(f"边界路由器 node {node_id}:{describe(before)}")
        if not before.has_network:
            print("失败:设备上没有 Thread 网络", file=sys.stderr)
            return 1

        if not args.apply:
            # 演练:构造 Pending Dataset 并校验,但不发送
            active = await create.read_active_dataset(api, node_id, endpoint)
            try:
                pending = dataset.build_channel_change(active, channel, delay_seconds=args.delay, pending_seconds=1)
                dataset.validate_dataset(pending)
            except dataset.DatasetError as err:
                print(f"失败:{err}", file=sys.stderr)
                return 1
            print(f"演练:信道 {before.channel} → {channel},延迟 {args.delay} 秒;Pending Dataset 构造并校验通过(没有下发)")
            print("加 --apply 才会真正迁移")
            return 0

        print(f"迁移:信道 {before.channel} → {channel},{args.delay} 秒后网络里所有设备一起切换")
        try:
            result = await change.change_channel(
                api, node_id, channel, endpoint_id=endpoint, delay_seconds=args.delay, on_step=lambda s: print(f"  - {s}")
            )
        except change.ChangeChannelError as err:
            print(f"失败[{err.code}]:{err}", file=sys.stderr)
            return 1
        print(f"完成:{describe(result.state)}")
        print("提示:HA 的凭证库还没有更新;集成在运行时会自动同步,否则用 thread_br.sync_network 补存")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:5580/ws", help="matter-server 的 WebSocket 地址")
    ap.add_argument("--node-id", type=int, help="边界路由器的 node id(默认自动查找)")
    ap.add_argument("--channel", type=int, help="新信道 11~26(不指定则按下面的干扰信息自动选择)")
    ap.add_argument("--zigbee-channel", type=int, action="append", default=[], help="附近 Zigbee 网络的信道,可重复")
    ap.add_argument("--wifi-channel", type=int, action="append", default=[], help="附近 Wi-Fi 网络的 2.4GHz 信道 1~14,可重复")
    ap.add_argument("--avoid-channel", type=int, action="append", default=[], help="要避开的 Thread 信道,可重复")
    ap.add_argument("--delay", type=int, default=dataset.DEFAULT_DELAY_SECONDS,
                    help=f"切换前的延迟,{dataset.MIN_DELAY_SECONDS}~{dataset.MAX_DELAY_SECONDS} 秒")
    ap.add_argument("--apply", action="store_true", help="真正迁移(默认只演练)")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
