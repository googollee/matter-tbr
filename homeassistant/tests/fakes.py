# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""测试用的假对象,test_create.py 和 test_integration.py 共用。"""

import asyncio
import base64
import struct

from python_otbr_api.tlv_parser import MeshcopTLVType as T, encode_tlv, parse_tlv

class FakeBoard:
    """模拟边界路由器(通过 matter-server 的命令接口访问)。

    行为开关:
      set_dataset: "ok" | "timeout_applied" | "timeout_not_applied" | "error" | "invalid_in_state"
      commit: "ok" | "error_code" | "raise_once" | "raise_always"
    """

    def __init__(self, *, has_network=False, set_dataset="ok", commit="ok", failsafe_code=0, require_failsafe=True):
        self.network = 1 if has_network else None
        self.armed = False
        self.set_dataset = set_dataset
        self.commit = commit
        self.failsafe_code = failsafe_code
        self.require_failsafe = require_failsafe
        self.calls = []
        self.dataset_b64 = ""
        self.hang = set()  # 这些命令名永不返回(模拟服务器不回应)
        self.gate = None  # 设置为 asyncio.Event 时,SetActiveDatasetRequest 等它被触发才继续
        # 诊断属性(Thread 网络诊断 cluster);有网络时由下发的 dataset 填充,可由测试覆盖
        self.channel = None
        self.network_name = None
        self.pan_id = None
        self.ext_pan_id = None
        self.neighbors = []
        self.routes = []
        self.faults = []
        # SetPendingDatasetRequest:"ok" 经过 pending_reads 次读状态后切换;"rejected" 被拒绝;"never" 收下但不切换;
        # "timeout_applied" 收下并照常切换,但响应丢了(超时);"timeout_not_applied" 超时且没收下
        self.pending_mode = "ok"
        self.pending_reads = 2
        self.pending_dataset_b64 = None
        self.pending_calls = 0
        self.role_override = None
        self.set_calls = 0
        self.commit_calls = 0
        self._uncommitted = False

    async def send_command(self, command, **args):
        self.calls.append((command, args))
        if command == "read_attribute":
            return self._read_attributes(args["attribute_path"])
        assert command == "device_command"
        name = args["command_name"]
        if name in self.hang:
            await asyncio.sleep(3600)
        if name == "SetPendingDatasetRequest":
            self.pending_calls += 1
            assert args["timed_request_timeout_ms"], "SetPendingDatasetRequest 是 timed 命令"
            if self.network is None:
                raise RuntimeError("Invalid in state (code 203)")
            if self.pending_mode == "rejected":
                raise RuntimeError("Failure (code 1)")
            if self.pending_mode == "timeout_not_applied":
                raise TimeoutError()
            self.pending_dataset_b64 = args["payload"]["pendingDataset"]
            if self.pending_mode == "timeout_applied":  # 设备收下了,但响应丢了
                raise TimeoutError()
            return {}
        if name == "GetActiveDatasetRequest":
            return {"dataset": self.dataset_b64 if self.network is not None else ""}
        if name == "ArmFailSafe":
            if self.failsafe_code:
                return {"errorCode": self.failsafe_code, "debugText": ""}
            seconds = args["payload"]["expiryLengthSeconds"]
            if seconds == 0 and self._uncommitted:  # 撤销 fail-safe 会回滚未提交的网络
                self.network, self._uncommitted = None, False
            self.armed = seconds > 0
            return {"errorCode": 0, "debugText": ""}
        if name == "SetActiveDatasetRequest":
            self.set_calls += 1
            assert args["timed_request_timeout_ms"] and args["interaction_timeout_ms"] >= 45_000
            assert base64.b64decode(args["payload"]["activeDataset"])  # 必须是合法 base64
            if self.require_failsafe and not self.armed:
                raise RuntimeError("Failsafe required (code 202)")
            if not self.require_failsafe and not self.armed:
                return {}
            if self.gate is not None:
                await self.gate.wait()
            if self.set_dataset == "invalid_in_state":
                raise RuntimeError("Invalid in state (code 203)")
            if self.set_dataset == "error":
                raise RuntimeError("Invalid command (code 133)")
            if self.set_dataset == "aborted":  # 没有状态码:结果不明
                raise RuntimeError("Operation aborted")
            if self.set_dataset in ("ok", "timeout_applied"):
                self.network, self._uncommitted = 2, True
                self.dataset_b64 = args["payload"]["activeDataset"]
                self._apply_dataset(self.dataset_b64)
            if self.set_dataset in ("timeout_applied", "timeout_not_applied"):
                raise asyncio.TimeoutError()
            return {}
        if name == "CommissioningComplete":
            self.commit_calls += 1
            if self.commit == "raise_always" or (self.commit == "raise_once" and self.commit_calls == 1):
                raise ConnectionError("连接断开")
            if self.commit == "error_code" or not self.armed:
                return {"errorCode": 14, "debugText": ""}
            self.armed, self._uncommitted = False, False
            return {"errorCode": 0, "debugText": ""}
        raise AssertionError(f"未预期的命令 {name}")

    # ---- 内部:状态读取和 dataset 应用 ----

    def _role(self):
        if self.role_override is not None:
            return self.role_override
        return 6 if self.network is not None else 0

    def _read_attributes(self, paths):
        has = self.network is not None
        values = {
            "1106/3": has,
            "1106/4": self.network,
            "1106/5": 1 if self.pending_dataset_b64 is not None else None,
            "53/0": self.channel if has else None,
            "53/1": self._role(),
            "53/2": self.network_name if has else None,
            "53/3": self.pan_id if has else None,
            "53/4": self.ext_pan_id if has else None,
            "53/7": list(self.neighbors),
            "53/8": list(self.routes),
            "53/9": 1234 if has else None,
            "53/13": 7 if has else None,
            "53/62": list(self.faults),
        }
        out = {}
        for path in paths:
            endpoint, key = path.split("/", 1)
            if key in values:
                out[path] = values[key]
        if any(p.endswith("/53/0") for p in paths):
            self._tick_pending()
        return out

    def _apply_dataset(self, b64):
        """尽力解析下发的 dataset,填充诊断属性(测试里有的 dataset 是随便写的十六进制,解析不了就保持默认)。"""
        try:
            items = parse_tlv(base64.b64decode(b64).hex())
            self.channel = items[T.CHANNEL].channel
            self.network_name = items[T.NETWORKNAME].name
            self.pan_id = int.from_bytes(items[T.PANID].data, "big")
            self.ext_pan_id = int.from_bytes(items[T.EXTPANID].data, "big")
        except Exception:  # noqa: BLE001
            self.channel, self.network_name, self.pan_id, self.ext_pan_id = 15, "FakeNet", 4660, 1

    def _tick_pending(self):
        if self.pending_dataset_b64 is None or self.pending_mode not in ("ok", "timeout_applied"):
            return
        self.pending_reads -= 1
        if self.pending_reads > 0:
            return
        items = parse_tlv(base64.b64decode(self.pending_dataset_b64).hex())
        timestamp = int.from_bytes(items[T.ACTIVETIMESTAMP].data, "big")
        for tag in (T.PENDINGTIMESTAMP, T.DELAYTIMER):
            items.pop(tag, None)
        self.dataset_b64 = base64.b64encode(bytes.fromhex(encode_tlv(items))).decode()
        self.network = timestamp
        self._apply_dataset(self.dataset_b64)
        self.pending_dataset_b64 = None

    def commands(self):
        return [a["command_name"] for c, a in self.calls if c == "device_command"]

    def arm_calls(self):
        return [a["payload"]["expiryLengthSeconds"] for c, a in self.calls
                if c == "device_command" and a["command_name"] == "ArmFailSafe"]


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


