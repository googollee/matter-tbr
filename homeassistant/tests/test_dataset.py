# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""dataset.py 的单元测试。运行:.venv/bin/python -m pytest homeassistant/tests"""

import itertools
import struct

from python_otbr_api.pskc import compute_pskc
from python_otbr_api.tlv_parser import MeshcopTLVType as T, parse_tlv
import pytest


def fixed_randbytes(*chunks):
    """按顺序返回预设的随机字节,用完后重复最后一个,便于构造可复现的 dataset。"""
    it = itertools.chain(chunks, itertools.repeat(chunks[-1]))

    def randbytes(n):
        data = next(it)
        return (data * n)[:n] if len(data) == 1 else data

    return randbytes


def parsed(d):
    return parse_tlv(d.tlv_hex)


def test_default_dataset_is_valid_and_complete(ds):
    d = ds.generate_dataset()
    ds.validate_dataset(d.tlv_hex)
    assert set(ds.REQUIRED_TLVS) <= set(parsed(d))


def test_default_channel_is_the_preferred_one(ds, ch):
    assert ds.DEFAULT_CHANNEL == ch.PREFERRED[0]  # 两处默认值必须一致
    assert ds.generate_dataset().channel == ds.DEFAULT_CHANNEL


def test_defaults_are_random_and_in_range(ds):
    d = ds.generate_dataset()
    assert d.network_name.startswith(ds.DEFAULT_NAME_PREFIX)
    assert len(d.network_name.encode()) <= ds.NETWORK_NAME_MAX_BYTES
    other = ds.generate_dataset()
    assert d.tlv_hex != other.tlv_hex
    assert parsed(d)[T.NETWORKKEY].data != parsed(other)[T.NETWORKKEY].data


def test_explicit_parameters_are_used(ds):
    d = ds.generate_dataset(network_name="我的网络", channel=11)
    p = parsed(d)
    assert d.channel == 11 and d.network_name == "我的网络"
    assert p[T.NETWORKNAME].data.decode() == "我的网络"
    assert p[T.CHANNEL].data == b"\x00\x00\x0b"


def test_tlv_field_encodings(ds):
    d = ds.generate_dataset(channel=25)
    p = parsed(d)
    # 信道 11–26 的标准 TLV 掩码(与 OpenThread 生成的 dataset 中常见的 "35060004001fffe0" 一致)
    assert p[T.CHANNELMASK].data == bytes.fromhex("0004001fffe0")
    assert p[T.MESHLOCALPREFIX].data[0] == 0xFD
    assert p[T.SECURITYPOLICY].data == bytes.fromhex("02a0f7f8")
    assert p[T.ACTIVETIMESTAMP].seconds == 1 and not p[T.ACTIVETIMESTAMP].authoritative
    assert p[T.CHANNEL].channel == 25
    assert d.ext_pan_id == p[T.EXTPANID].data.hex()
    assert d.pan_id == int.from_bytes(p[T.PANID].data, "big")


def test_tlvs_are_sorted_by_tag(ds):
    tags = list(parsed(ds.generate_dataset()))
    assert tags == sorted(tags)


@pytest.mark.parametrize("channel", [10, 27, 0, -1, "15", 15.0, True])
def test_invalid_channel_rejected(ds, channel):
    with pytest.raises(ds.DatasetError):
        ds.generate_dataset(channel=channel)


@pytest.mark.parametrize("name", ["", "a" * 17, "网" * 6])  # 6 个汉字 = 18 字节
def test_invalid_network_name_rejected(ds, name):
    with pytest.raises(ds.DatasetError):
        ds.generate_dataset(network_name=name)


def test_network_name_boundary_accepted(ds):
    assert ds.generate_dataset(network_name="a" * 16).network_name == "a" * 16
    assert ds.generate_dataset(network_name="网" * 5 + "a").network_name == "网" * 5 + "a"  # 16 字节


def test_passphrase_derives_pskc(ds):
    d = ds.generate_dataset(network_name="Test Network", passphrase="12SECRETPASSWORD34")
    p = parsed(d)
    expected = compute_pskc(p[T.EXTPANID].data, "Test Network", "12SECRETPASSWORD34")
    assert p[T.PSKC].data == bytes(expected)


# 这组向量由板子上的 OpenThread 栈(`matter otcli dataset pskc -p`)实测得到,
# 栈当时的网络名为 OpenThread、Extended PAN ID 为 dead00beef00cafe。
OT_VECTOR_PSKC = "6fd3e3c0e5b83960004923ee0f059d03"


def test_pskc_matches_openthread_on_device(ds):
    d = ds.generate_dataset(
        network_name="OpenThread",
        channel=15,
        passphrase="12SECRETPASSWORD34",
        randbytes=fixed_randbytes(bytes.fromhex("dead00beef00cafe"), b"\x12\x34", b"\x07"),
    )
    assert d.ext_pan_id == "dead00beef00cafe"
    assert parsed(d)[T.PSKC].data.hex() == OT_VECTOR_PSKC


def test_passphrase_is_not_stored(ds):
    secret = "my-very-secret-phrase"
    d = ds.generate_dataset(passphrase=secret)
    assert secret not in repr(d) and secret.encode().hex() not in d.tlv_hex


def test_same_passphrase_different_networks_give_different_pskc(ds):
    a = parsed(ds.generate_dataset(passphrase="same-passphrase"))[T.PSKC].data
    b = parsed(ds.generate_dataset(passphrase="same-passphrase"))[T.PSKC].data
    assert a != b  # 扩展 PAN ID 随机,PSKc 随之不同


def test_empty_passphrase_gives_random_pskc(ds):
    a = parsed(ds.generate_dataset(passphrase=""))[T.PSKC].data
    b = parsed(ds.generate_dataset(passphrase=None))[T.PSKC].data
    assert len(a) == len(b) == 16 and a != b


@pytest.mark.parametrize("phrase", ["short", "x" * 256, "字" * 86])  # 5 字节 / 256 字节 / 258 字节
def test_invalid_passphrase_rejected(ds, phrase):
    with pytest.raises(ds.DatasetError):
        ds.generate_dataset(passphrase=phrase)


def test_passphrase_boundaries_accepted(ds):
    ds.generate_dataset(passphrase="x" * 6)
    ds.generate_dataset(passphrase="x" * 255)


def test_pan_id_never_0xffff(ds):
    d = ds.generate_dataset(randbytes=fixed_randbytes(b"\xaa", b"\xff\xff", b"\x12\x34", b"\x00"))
    assert d.pan_id != 0xFFFF
    assert d.pan_id == 0x1234


def test_deterministic_with_injected_randomness(ds):
    r = lambda: fixed_randbytes(b"\x07")  # noqa: E731
    assert ds.generate_dataset(randbytes=r()).tlv_hex == ds.generate_dataset(randbytes=r()).tlv_hex


def test_validate_rejects_missing_required_tlv(ds):
    d = ds.generate_dataset()
    p = parsed(d)
    del p[T.NETWORKKEY]
    with pytest.raises(ds.DatasetError, match="NETWORKKEY"):
        ds.validate_dataset(ds_encode(p))


def test_validate_rejects_bad_lengths_and_values(ds):
    base = parsed(ds.generate_dataset())

    def mutate(tag, data):
        p = dict(base)
        p[tag] = type(p[tag])(int(tag), data)
        return ds_encode(p)

    with pytest.raises(ds.DatasetError):
        ds.validate_dataset(mutate(T.NETWORKKEY, b"\x00" * 15))
    with pytest.raises(ds.DatasetError):
        ds.validate_dataset(mutate(T.PANID, b"\xff\xff"))
    with pytest.raises(ds.DatasetError):
        ds.validate_dataset(mutate(T.CHANNEL, b"\x00\x00\x1b"))  # 信道 27
    with pytest.raises(ds.DatasetError):
        ds.validate_dataset(mutate(T.ACTIVETIMESTAMP, b"\x00" * 8))
    with pytest.raises(ds.DatasetError):
        ds.validate_dataset(mutate(T.NETWORKNAME, b"a" * 17))


@pytest.mark.parametrize("bad", ["", "zz", "0e", "0e08aa", "ff00", "0e0100", "000100"])
def test_validate_rejects_garbage(ds, bad):
    with pytest.raises(ds.DatasetError):
        ds.validate_dataset(bad)


def ds_encode(parsed_items):
    from python_otbr_api.tlv_parser import encode_tlv

    return encode_tlv(parsed_items)


def test_channel_mask_bit_order(ds):
    """信道 c 对应 32 位字的第 (31 - c) 位;逐位核对,不依赖实现里的公式。"""
    mask = int.from_bytes(parsed(ds.generate_dataset())[T.CHANNELMASK].data[2:], "big")
    for channel in range(0, 32):
        expected = 11 <= channel <= 26
        assert bool(mask & (1 << (31 - channel))) == expected, channel
    assert ds.channel_mask(11, 26) == 0x001FFFE0
    assert ds.channel_mask(15, 15) == 1 << 16


def test_every_selectable_channel_is_inside_the_mask(ds):
    for channel in range(11, 27):
        mask = int.from_bytes(parsed(ds.generate_dataset(channel=channel))[T.CHANNELMASK].data[2:], "big")
        assert mask & (1 << (31 - channel))


# ---- 改信道用的 Pending Dataset ----


def _active(ds, channel=26):
    return ds.generate_dataset(network_name="Net", channel=channel).tlv_hex


def _pending(ds, active, channel=25, delay=120, now=1_700_000_000):
    return ds.build_channel_change(active, channel, delay_seconds=delay, pending_seconds=now)


def _migration(ds, current, target, delay=120, now=1_700_000_000):
    return ds.build_network_migration(current, target, delay_seconds=delay, pending_seconds=now)


def test_network_migration_carries_the_target_network_with_pending_fields(ds):
    current = _active(ds)
    target = ds.generate_dataset(network_name="Target", channel=15).tlv_hex
    new, tgt = parse_tlv(_migration(ds, current, target, delay=60)), parse_tlv(target)
    for tag in (T.CHANNEL, T.PANID, T.EXTPANID, T.NETWORKNAME, T.NETWORKKEY, T.PSKC, T.MESHLOCALPREFIX, T.CHANNELMASK):
        assert new[tag].data == tgt[tag].data, tag.name  # 目标网络的内容原样带上
    assert new[T.DELAYTIMER].data == struct.pack("!I", 60_000)
    assert int.from_bytes(new[T.PENDINGTIMESTAMP].data, "big") >> 16 == 1_700_000_000


def test_network_migration_timestamp_is_newer_than_both_networks(ds):
    current = _active(ds)
    cur_s = parse_tlv(current)[T.ACTIVETIMESTAMP].seconds
    target = parse_tlv(ds.generate_dataset(network_name="Target", channel=15).tlv_hex)
    tgt_s = target[T.ACTIVETIMESTAMP].seconds

    def stamp(cur, tgt):
        c = _with_timestamp(ds, current, cur)
        t = _with_timestamp(ds, ds.generate_dataset(network_name="Target", channel=15).tlv_hex, tgt)
        return parse_tlv(_migration(ds, c, t))[T.ACTIVETIMESTAMP].seconds

    assert stamp(100, 50) == 101  # 目标的更旧:取"当前 + 1",否则网格会忽略
    assert stamp(100, 100) == 101
    assert stamp(100, 500) == 500  # 目标的更新:保持目标的
    assert cur_s is not None and tgt_s is not None


def _with_timestamp(ds, tlv, seconds):
    from python_otbr_api.tlv_parser import MeshcopTLVItem, encode_tlv

    items = dict(parse_tlv(tlv))
    items[T.ACTIVETIMESTAMP] = MeshcopTLVItem(int(T.ACTIVETIMESTAMP), (seconds << 16).to_bytes(8, "big"))
    return encode_tlv(dict(sorted(items.items())))


def test_network_migration_replaces_pending_fields_left_in_the_target(ds):
    current = _active(ds)
    target = _pending(ds, ds.generate_dataset(network_name="Target", channel=15).tlv_hex, channel=20, delay=600)
    new = parse_tlv(_migration(ds, current, target, delay=90, now=1_700_000_500))
    assert new[T.DELAYTIMER].data == struct.pack("!I", 90_000)
    assert int.from_bytes(new[T.PENDINGTIMESTAMP].data, "big") >> 16 == 1_700_000_500


@pytest.mark.parametrize("kwargs", [{"delay": 5}, {"delay": 9999}, {"now": 0}])
def test_network_migration_rejects_bad_arguments(ds, kwargs):
    with pytest.raises(ds.DatasetError):
        _migration(ds, _active(ds), ds.generate_dataset(network_name="T", channel=15).tlv_hex, **kwargs)


def test_network_migration_rejects_garbage_datasets(ds):
    with pytest.raises(ds.DatasetError):
        _migration(ds, _active(ds), "0102")
    with pytest.raises(ds.DatasetError):
        _migration(ds, "0102", ds.generate_dataset(network_name="T", channel=15).tlv_hex)


def test_channel_change_replaces_channel_and_adds_pending_fields(ds):
    active = _active(ds)
    new = parse_tlv(_pending(ds, active))
    old = parse_tlv(active)
    assert new[T.CHANNEL].channel == 25
    assert new[T.DELAYTIMER].data == struct.pack("!I", 120_000)  # 毫秒
    assert int.from_bytes(new[T.PENDINGTIMESTAMP].data, "big") >> 16 == 1_700_000_000
    # 新 Active Timestamp 比旧的大 1 秒,ticks 和 authoritative 位为 0
    assert int.from_bytes(new[T.ACTIVETIMESTAMP].data, "big") == (old[T.ACTIVETIMESTAMP].seconds + 1) << 16


def test_channel_change_keeps_every_other_field_untouched(ds):
    active = _active(ds)
    new, old = parse_tlv(_pending(ds, active)), parse_tlv(active)
    changed = {T.CHANNEL, T.ACTIVETIMESTAMP, T.PENDINGTIMESTAMP, T.DELAYTIMER}
    for tag, item in old.items():
        if tag not in changed:
            assert new[tag].data == item.data, tag.name  # 密钥、PAN ID、名字等原样保留
    assert set(new) == set(old) | {T.PENDINGTIMESTAMP, T.DELAYTIMER}
    assert list(new) == sorted(new)


def test_channel_change_replaces_existing_pending_fields(ds):
    once = _pending(ds, _active(ds), channel=25, delay=60, now=1_700_000_000)
    again = parse_tlv(ds.build_channel_change(once, 15, delay_seconds=90, pending_seconds=1_700_000_100))
    assert again[T.DELAYTIMER].data == struct.pack("!I", 90_000)
    assert int.from_bytes(again[T.PENDINGTIMESTAMP].data, "big") >> 16 == 1_700_000_100
    assert again[T.CHANNEL].channel == 15


def test_channel_change_result_is_a_valid_dataset_for_the_matter_validator(ds):
    ds.validate_dataset(_pending(ds, _active(ds)))


@pytest.mark.parametrize("kwargs", [
    {"channel": 10}, {"channel": 27}, {"channel": True},
    {"delay": 29}, {"delay": 601}, {"delay": True}, {"delay": "120"},
    {"now": 0}, {"now": -5},
])
def test_channel_change_rejects_bad_arguments(ds, kwargs):
    with pytest.raises(ds.DatasetError):
        _pending(ds, _active(ds), **kwargs)


def test_channel_change_rejects_channel_outside_the_mask(ds):
    active = parse_tlv(_active(ds))
    from python_otbr_api.tlv_parser import MeshcopTLVItem, encode_tlv

    active[T.CHANNELMASK] = MeshcopTLVItem(int(T.CHANNELMASK), bytes.fromhex("0004") + struct.pack("!I", ds.channel_mask(11, 20)))
    with pytest.raises(ds.DatasetError, match="信道掩码"):
        _pending(ds, encode_tlv(dict(sorted(active.items()))), channel=25)


def test_channel_change_rejects_garbage_active_dataset(ds):
    with pytest.raises(ds.DatasetError):
        _pending(ds, "0e0100")
