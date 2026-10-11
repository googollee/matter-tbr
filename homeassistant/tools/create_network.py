#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""通过 matter-server 在 Thread 边界路由器上创建 Thread 网络(命令行版,用于开发和排查)。

默认只做演练(dry-run):检查设备状态、探测命令格式、开启并立即撤销一次 fail-safe,
不会创建网络。真正创建要加 --apply,并且必须用 --save-dataset 指定保存 dataset 的文件。
dataset 含网络密钥,相当于这个 Thread 网络的密码,不要提交到仓库。

用法示例:
  # 演练(安全)
  .venv/bin/python homeassistant/tools/create_network.py
  # 真正创建;密码短语从环境变量读取(可选,留空则 PSKc 随机)
  THREAD_PASSPHRASE='...' .venv/bin/python homeassistant/tools/create_network.py \\
      --apply --save-dataset ~/thread-dataset.hex
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import sys
from typing import Any

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "thread_br"))
import channels  # noqa: E402
import create  # noqa: E402
import dataset  # noqa: E402


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
                continue  # 事件或其他消息
            if "error_code" in message:
                raise MatterApiError(message.get("details") or f"error_code={message['error_code']}")
            return message.get("result")


async def find_node(api: WsMatterApi, node_id: int | None) -> tuple[int, int]:
    """返回 (node_id, 端点)。未指定节点时,自动找带 Thread BR 管理 cluster 的节点。"""
    nodes = await api.send_command("get_nodes")
    candidates = []
    for node in nodes:
        endpoint = create.find_border_router_endpoint(node.get("attributes", {}))
        if endpoint is not None and (node_id is None or node["node_id"] == node_id):
            candidates.append((node["node_id"], endpoint))
    if not candidates:
        raise SystemExit("没有找到带 Thread Border Router Management cluster 的节点")
    if len(candidates) > 1:
        ids = ", ".join(str(n) for n, _ in candidates)
        raise SystemExit(f"找到多个边界路由器节点({ids}),请用 --node-id 指定")
    return candidates[0]


def read_passphrase(args: argparse.Namespace) -> str | None:
    if args.ask_passphrase:
        return getpass.getpass("Thread 密码短语(留空则随机): ") or None
    return os.environ.get("THREAD_PASSPHRASE") or None


def save_dataset(path: Path, tlv_hex: str) -> None:
    """先写文件再下发,以防创建成功后 dataset 丢失。权限 0600,已存在则拒绝覆盖。"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tlv_hex + "\n")


async def main_async(args: argparse.Namespace) -> int:
    passphrase = read_passphrase(args)
    channel = args.channel
    if channel is None:
        try:
            selection = channels.choose_channel(zigbee=args.zigbee_channel, wifi=args.wifi_channel, avoid=args.avoid_channel)
        except (ValueError, channels.NoChannelAvailable) as err:
            print(f"参数错误:{err}", file=sys.stderr)
            return 2
        channel = selection.channel
        print(f"自动选择信道 {channel}(第 {selection.tier} 档);排除:" + (
            "、".join(f"{c}({why})" for c, why in sorted(selection.excluded.items())) or "无"))
    try:
        new = dataset.generate_dataset(args.network_name, channel, passphrase)
    except dataset.DatasetError as err:
        print(f"参数错误:{err}", file=sys.stderr)
        return 2

    if args.apply:
        if args.save_dataset is None:
            print("--apply 必须同时指定 --save-dataset,否则创建后 dataset 会丢失", file=sys.stderr)
            return 2
        try:
            save_dataset(args.save_dataset, new.tlv_hex)
        except FileExistsError:
            print(f"{args.save_dataset} 已存在,为避免覆盖旧 dataset,请换一个文件名", file=sys.stderr)
            return 2
        print(f"dataset 已保存到 {args.save_dataset}(权限 0600),请妥善保管")

    async with websockets.connect(args.url) as ws:
        info = json.loads(await ws.recv())
        print(f"已连接 {info.get('sdk_version')}")
        api = WsMatterApi(ws)
        node_id, endpoint = await find_node(api, args.node_id)
        print(f"边界路由器:node {node_id},端点 {endpoint}")
        print(f"网络参数:名称 {new.network_name},信道 {new.channel},Extended PAN ID {new.ext_pan_id}"
              f"{',PSKc 由密码短语派生' if passphrase else ',PSKc 随机'}")
        print("模式:" + ("真正创建" if args.apply else "演练(不会创建网络)"))
        try:
            result = await create.create_network(
                api, node_id, new.tlv_hex, endpoint_id=endpoint, dry_run=not args.apply,
                on_step=lambda s: print(f"  - {s}"),
            )
        except create.CreateNetworkError as err:
            print(f"失败[{err.code}]:{err}", file=sys.stderr)
            return 1
    state = result.state
    print(f"结果:ActiveDatasetTimestamp={state.active_timestamp},InterfaceEnabled={state.interface_enabled}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:5580/ws", help="matter-server 的 WebSocket 地址")
    ap.add_argument("--node-id", type=int, help="边界路由器的 node id(默认自动查找)")
    ap.add_argument("--network-name", help="网络名,1~16 字节(默认随机)")
    ap.add_argument("--channel", type=int, help="信道 11~26(不指定则按下面的干扰信息自动选择)")
    ap.add_argument("--zigbee-channel", type=int, action="append", default=[], help="附近 Zigbee 网络的信道,可重复")
    ap.add_argument("--wifi-channel", type=int, action="append", default=[], help="附近 Wi-Fi 网络的 2.4GHz 信道 1~14,可重复")
    ap.add_argument("--avoid-channel", type=int, action="append", default=[], help="要避开的 Thread 信道,可重复")
    ap.add_argument("--ask-passphrase", action="store_true", help="交互输入密码短语(也可用环境变量 THREAD_PASSPHRASE)")
    ap.add_argument("--apply", action="store_true", help="真正创建网络(默认只演练)")
    ap.add_argument("--save-dataset", type=Path, help="保存 dataset 的文件,--apply 时必需")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
