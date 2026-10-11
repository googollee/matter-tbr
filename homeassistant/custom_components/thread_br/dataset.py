# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""生成 Thread 网络的 Active Operational Dataset。

纯逻辑,不依赖 Home Assistant,方便单元测试。
dataset 里的 Network Key 和 PSKc 相当于这个网络的密码,调用方不要把结果写进日志。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import secrets
import struct

from python_otbr_api.pskc import compute_pskc
from python_otbr_api.tlv_parser import (
    MeshcopTLVItem,
    MeshcopTLVType,
    TLVError,
    encode_tlv,
    parse_tlv,
)

# 没有指定信道时的默认值。HA 里创建网络会按环境选信道(见 channels.py)再传进来,
# 这里只是命令行和单独使用时的兜底;测试保证它等于 channels.PREFERRED[0]
DEFAULT_CHANNEL = 25
MIN_CHANNEL, MAX_CHANNEL = 11, 26


def channel_mask(first: int = MIN_CHANNEL, last: int = MAX_CHANNEL) -> int:
    """Channel Mask TLV 里的掩码:信道 c 对应 32 位字的第 (31 - c) 位(最高位是信道 0)。

    注意这与 OpenThread 内部/命令行显示的掩码(信道 c 对应第 c 位,如 0x07fff800)相反。
    信道 11–26 的 TLV 掩码是 0x001fffe0。
    """
    return sum(1 << (31 - c) for c in range(first, last + 1))


CHANNEL_MASK = channel_mask()
NETWORK_NAME_MAX_BYTES = 16
PASSPHRASE_MIN_BYTES, PASSPHRASE_MAX_BYTES = 6, 255
DEFAULT_NAME_PREFIX = "ThreadBR-"

# Security Policy:轮换时间 672 小时 + OpenThread 默认标志位(0xF7F8)
SECURITY_POLICY = bytes.fromhex("02a0f7f8")

# Matter 的 SetActiveDatasetRequest 要求 dataset 包含这些 TLV
REQUIRED_TLVS = (
    MeshcopTLVType.ACTIVETIMESTAMP,
    MeshcopTLVType.CHANNEL,
    MeshcopTLVType.CHANNELMASK,
    MeshcopTLVType.EXTPANID,
    MeshcopTLVType.MESHLOCALPREFIX,
    MeshcopTLVType.NETWORKKEY,
    MeshcopTLVType.NETWORKNAME,
    MeshcopTLVType.PANID,
    MeshcopTLVType.PSKC,
    MeshcopTLVType.SECURITYPOLICY,
)

RandomBytes = Callable[[int], bytes]


class DatasetError(ValueError):
    """参数不合法或生成的 dataset 不合法。"""


@dataclass(frozen=True)
class NewDataset:
    """生成结果。tlv_hex 含密钥,不要打印。"""

    tlv_hex: str
    network_name: str
    channel: int
    pan_id: int
    ext_pan_id: str


def _random_pan_id(randbytes: RandomBytes) -> bytes:
    while True:
        pan_id = randbytes(2)
        if pan_id != b"\xff\xff":
            return pan_id


def validate_network_name(name: str) -> str:
    size = len(name.encode())
    if not 1 <= size <= NETWORK_NAME_MAX_BYTES:
        raise DatasetError(f"网络名必须是 1~{NETWORK_NAME_MAX_BYTES} 字节,当前 {size} 字节")
    return name


def validate_channel(channel: int) -> int:
    if isinstance(channel, bool) or not isinstance(channel, int) or not MIN_CHANNEL <= channel <= MAX_CHANNEL:
        raise DatasetError(f"信道必须是 {MIN_CHANNEL}~{MAX_CHANNEL} 的整数,当前 {channel!r}")
    return channel


def validate_passphrase(passphrase: str) -> str:
    size = len(passphrase.encode())
    if not PASSPHRASE_MIN_BYTES <= size <= PASSPHRASE_MAX_BYTES:
        raise DatasetError(f"密码短语必须是 {PASSPHRASE_MIN_BYTES}~{PASSPHRASE_MAX_BYTES} 字节,当前 {size} 字节")
    return passphrase


def generate_dataset(
    network_name: str | None = None,
    channel: int | None = None,
    passphrase: str | None = None,
    *,
    randbytes: RandomBytes = secrets.token_bytes,
) -> NewDataset:
    """生成一个新网络的 dataset。

    network_name 留空则随机;channel 留空用 DEFAULT_CHANNEL;passphrase 留空则 PSKc 为随机值。
    密码短语只用来派生 PSKc,不会出现在结果里。
    """
    ext_pan_id = randbytes(8)
    pan_id = _random_pan_id(randbytes)

    if network_name is None:
        network_name = DEFAULT_NAME_PREFIX + randbytes(2).hex()
    validate_network_name(network_name)

    channel = DEFAULT_CHANNEL if channel is None else validate_channel(channel)

    if passphrase:
        pskc = compute_pskc(ext_pan_id, network_name, validate_passphrase(passphrase))
    else:
        pskc = randbytes(16)

    # Active Timestamp:秒(48 位) | 滴答(15 位) | authoritative(1 位)。秒数必须非 0
    active_timestamp = struct.pack("!Q", 1 << 16)

    items = {
        MeshcopTLVType.CHANNEL: b"\x00" + struct.pack("!H", channel),  # 信道页 0 + 信道
        MeshcopTLVType.PANID: pan_id,
        MeshcopTLVType.EXTPANID: ext_pan_id,
        MeshcopTLVType.NETWORKNAME: network_name.encode(),
        MeshcopTLVType.PSKC: bytes(pskc),
        MeshcopTLVType.NETWORKKEY: randbytes(16),
        MeshcopTLVType.MESHLOCALPREFIX: b"\xfd" + randbytes(7),
        MeshcopTLVType.SECURITYPOLICY: SECURITY_POLICY,
        MeshcopTLVType.ACTIVETIMESTAMP: active_timestamp,
        MeshcopTLVType.CHANNELMASK: b"\x00\x04" + struct.pack("!I", CHANNEL_MASK),  # 信道页 0、掩码 4 字节
    }
    tlv_hex = encode_tlv(
        {tag: MeshcopTLVItem(int(tag), data) for tag, data in sorted(items.items())}
    )
    validate_dataset(tlv_hex)
    return NewDataset(
        tlv_hex=tlv_hex,
        network_name=network_name,
        channel=channel,
        pan_id=int.from_bytes(pan_id, "big"),
        ext_pan_id=ext_pan_id.hex(),
    )


# Pending Dataset 的延迟计时器:Thread 规范的最小值是 30 秒;给睡眠设备时间跟上,默认更长
MIN_DELAY_SECONDS, MAX_DELAY_SECONDS, DEFAULT_DELAY_SECONDS = 30, 600, 120


def build_channel_change(active_tlv_hex: str, channel: int, *, delay_seconds: int, pending_seconds: int) -> str:
    """由当前的 Active Dataset 构造"换信道"用的 Pending Dataset(十六进制 TLV)。

    Pending Dataset = 新的 Active Dataset 内容 + Pending Timestamp + Delay Timer:
    延迟到期后整个网络同时切换到新的 Active Dataset。其余字段(密钥、PAN ID 等)原样保留,
    新的 Active Timestamp 比当前的大 1 秒(Thread 要求新数据集的时间戳更新才会被采用)。

    pending_seconds 是 Pending Timestamp 的秒数,调用方传当前时间,要比此前任何 Pending 的大。
    """
    validate_channel(channel)
    if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int) or not MIN_DELAY_SECONDS <= delay_seconds <= MAX_DELAY_SECONDS:
        raise DatasetError(f"延迟必须是 {MIN_DELAY_SECONDS}~{MAX_DELAY_SECONDS} 秒,当前 {delay_seconds!r}")
    if not isinstance(pending_seconds, int) or pending_seconds < 1:
        raise DatasetError("pending_seconds 必须是正整数")
    validate_dataset(active_tlv_hex)

    items = dict(parse_tlv(active_tlv_hex))
    mask = items[MeshcopTLVType.CHANNELMASK].data  # 页(1) + 长度(1) + 掩码
    if len(mask) < 6 or not int.from_bytes(mask[2:6], "big") & (1 << (31 - channel)):
        raise DatasetError(f"信道 {channel} 不在网络的信道掩码里")

    old_seconds = int.from_bytes(items[MeshcopTLVType.ACTIVETIMESTAMP].data, "big") >> 16
    new = {
        MeshcopTLVType.CHANNEL: b"\x00" + struct.pack("!H", channel),
        MeshcopTLVType.ACTIVETIMESTAMP: struct.pack("!Q", (old_seconds + 1) << 16),
        MeshcopTLVType.PENDINGTIMESTAMP: struct.pack("!Q", pending_seconds << 16),
        MeshcopTLVType.DELAYTIMER: struct.pack("!I", delay_seconds * 1000),  # 毫秒
    }
    for tag, data in new.items():
        items[tag] = MeshcopTLVItem(int(tag), data)
    return encode_tlv(dict(sorted(items.items())))


def extended_pan_id_of(tlv_hex: str) -> int:
    """dataset 的 Extended PAN ID(网络的身份标识)。"""
    try:
        return int.from_bytes(parse_tlv(tlv_hex)[MeshcopTLVType.EXTPANID].data, "big")
    except (KeyError, ValueError, TLVError) as err:
        raise DatasetError("dataset 里没有 Extended PAN ID") from err


def build_network_migration(current_tlv_hex: str, target_tlv_hex: str, *, delay_seconds: int, pending_seconds: int) -> str:
    """构造"整网迁移"用的 Pending Dataset:延迟到期后整个网络(含所有设备)切换到 target 网络。

    内容是 target 的完整 dataset,加上 Pending Timestamp 和 Delay Timer。Active Timestamp 必须比
    当前网络的更新,否则网格会静默忽略它,所以取 target 的和"当前 + 1 秒"中较大的;
    因此设备最终得到的 dataset 与 target 只差时间戳,调用方要把设备上的新 dataset 存回 HA。
    """
    if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int) or not MIN_DELAY_SECONDS <= delay_seconds <= MAX_DELAY_SECONDS:
        raise DatasetError(f"延迟必须是 {MIN_DELAY_SECONDS}~{MAX_DELAY_SECONDS} 秒,当前 {delay_seconds!r}")
    if not isinstance(pending_seconds, int) or pending_seconds < 1:
        raise DatasetError("pending_seconds 必须是正整数")
    validate_dataset(current_tlv_hex)
    validate_dataset(target_tlv_hex)

    current = dict(parse_tlv(current_tlv_hex))
    items = dict(parse_tlv(target_tlv_hex))
    current_seconds = int.from_bytes(current[MeshcopTLVType.ACTIVETIMESTAMP].data, "big") >> 16
    target_seconds = int.from_bytes(items[MeshcopTLVType.ACTIVETIMESTAMP].data, "big") >> 16
    new = {
        MeshcopTLVType.ACTIVETIMESTAMP: struct.pack("!Q", max(target_seconds, current_seconds + 1) << 16),
        MeshcopTLVType.PENDINGTIMESTAMP: struct.pack("!Q", pending_seconds << 16),
        MeshcopTLVType.DELAYTIMER: struct.pack("!I", delay_seconds * 1000),  # 毫秒
    }
    for tag, data in new.items():
        items[tag] = MeshcopTLVItem(int(tag), data)
    return encode_tlv(dict(sorted(items.items())))


def validate_dataset(tlv_hex: str) -> None:
    """检查 dataset 是否可被解析,且包含 Matter 要求的全部 TLV 和合法的长度。"""
    try:
        parsed = parse_tlv(tlv_hex)
    # parse_tlv 对截断的数据会抛 IndexError,对长度不对的时间戳会抛 struct.error
    except (TLVError, IndexError, struct.error) as err:
        raise DatasetError(f"dataset 无法解析:{err}") from err

    missing = [tag.name for tag in REQUIRED_TLVS if tag not in parsed]
    if missing:
        raise DatasetError(f"dataset 缺少必需字段:{', '.join(missing)}")

    sizes = {
        MeshcopTLVType.EXTPANID: (8,),
        MeshcopTLVType.PANID: (2,),
        MeshcopTLVType.NETWORKKEY: (16,),
        MeshcopTLVType.PSKC: (16,),
        MeshcopTLVType.MESHLOCALPREFIX: (8,),
        MeshcopTLVType.ACTIVETIMESTAMP: (8,),
        MeshcopTLVType.CHANNEL: (3,),
    }
    for tag, allowed in sizes.items():
        if len(parsed[tag].data) not in allowed:
            raise DatasetError(f"{tag.name} 长度应为 {allowed} 字节,实际 {len(parsed[tag].data)}")

    name_size = len(parsed[MeshcopTLVType.NETWORKNAME].data)
    if not 1 <= name_size <= NETWORK_NAME_MAX_BYTES:
        raise DatasetError(f"NETWORKNAME 长度不合法:{name_size}")
    channel = int.from_bytes(parsed[MeshcopTLVType.CHANNEL].data[1:], "big")
    if not MIN_CHANNEL <= channel <= MAX_CHANNEL:
        raise DatasetError(f"信道超出范围:{channel}")
    if parsed[MeshcopTLVType.PANID].data == b"\xff\xff":
        raise DatasetError("PAN ID 不能是 0xFFFF")
    if int.from_bytes(parsed[MeshcopTLVType.ACTIVETIMESTAMP].data, "big") >> 16 == 0:
        raise DatasetError("Active Timestamp 的秒数不能为 0")
