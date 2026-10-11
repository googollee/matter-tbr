# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""change.py 的单元测试:把网络迁到新信道。"""

import asyncio
import base64
import struct

from fakes import Clock, FakeBoard
from python_otbr_api.tlv_parser import MeshcopTLVType as T, parse_tlv
import pytest

NODE = 7


def run(coro):
    return asyncio.run(coro)


def network(cr, ds, channel=26, board=None):
    """建好网络的假板子(用真实生成的 dataset,信道 channel)。"""
    board = board or FakeBoard()
    clock = Clock()
    run(cr.create_network(board, NODE, ds.generate_dataset(network_name="Net", channel=channel).tlv_hex, sleep=clock.sleep, clock=clock))
    return board


def change(chg, board, channel=25, **kwargs):
    clock = kwargs.pop("clock", None) or Clock()
    kwargs.setdefault("now", lambda: 1_700_000_000.0)
    return run(chg.change_channel(board, NODE, channel, sleep=clock.sleep, clock=clock, **kwargs))


def test_moves_the_network_to_the_new_channel(cr, ds, chg):
    board = network(cr, ds)
    steps = []
    result = change(chg, board, 25, on_step=steps.append, delay_seconds=60)
    assert (result.old_channel, result.new_channel) == (26, 25)
    assert result.state.channel == 25 and result.state.is_leader
    assert board.pending_calls == 1 and steps[-1] == "完成"


def test_pending_dataset_sent_has_the_channel_and_the_delay(cr, ds, chg):
    board = network(cr, ds)
    seen = {}
    original = board.send_command

    async def spy(command, **args):
        if args.get("command_name") == "SetPendingDatasetRequest":
            seen.update(parse_tlv(base64.b64decode(args["payload"]["pendingDataset"]).hex()))
        return await original(command, **args)

    board.send_command = spy
    change(chg, board, 15, delay_seconds=90)
    assert seen[T.CHANNEL].channel == 15
    assert seen[T.DELAYTIMER].data == struct.pack("!I", 90_000)


def test_keeps_network_identity_after_the_switch(cr, ds, chg):
    """换信道后网络名、PAN ID、Extended PAN ID 不变,设备才能跟着一起切。"""
    board = network(cr, ds)
    before = run(cr.read_border_router_state(board, NODE))
    change(chg, board, 25)
    after = run(cr.read_border_router_state(board, NODE))
    assert (after.network_name, after.pan_id, after.extended_pan_id) == (before.network_name, before.pan_id, before.extended_pan_id)
    assert after.active_timestamp > before.active_timestamp  # 新的 Active Timestamp 更大


def test_read_back_dataset_after_switch_is_a_normal_active_dataset(cr, ds, chg):
    board = network(cr, ds)
    change(chg, board, 25)
    tlv = run(cr.read_active_dataset(board, NODE))
    items = parse_tlv(tlv)
    assert items[T.CHANNEL].channel == 25 and T.PENDINGTIMESTAMP not in items and T.DELAYTIMER not in items
    ds.validate_dataset(tlv)


def test_refuses_without_a_network(cr, ds, chg):
    board = FakeBoard()
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board)
    assert err.value.code == chg.ChangeChannelError.NO_NETWORK and board.pending_calls == 0


def test_refuses_when_not_leader(cr, ds, chg):
    board = network(cr, ds)
    board.role_override = 5  # Router
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board)
    assert err.value.code == chg.ChangeChannelError.NOT_LEADER and board.pending_calls == 0


def test_refuses_the_same_channel(cr, ds, chg):
    board = network(cr, ds, channel=26)
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board, 26)
    assert err.value.code == chg.ChangeChannelError.SAME_CHANNEL and board.pending_calls == 0


def test_lost_response_is_not_a_failure_when_the_device_took_the_migration(cr, ds, chg):
    """命令超时,但设备其实收下了:不当作失败,继续等它切换,也不重发。"""
    board = network(cr, ds)
    board.pending_mode = "timeout_applied"
    steps = []
    result = change(chg, board, 25, on_step=steps.append, delay_seconds=30)
    assert result.state.channel == 25 and board.pending_calls == 1
    assert any("读取设备状态确认" in s for s in steps) and steps[-1] == "完成"


def test_lost_response_with_nothing_pending_is_a_failure_and_can_be_retried(cr, ds, chg):
    board = network(cr, ds)
    board.pending_mode = "timeout_not_applied"
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board, 25)
    assert err.value.code == chg.ChangeChannelError.REJECTED and board.pending_calls == 1
    board.pending_mode = "ok"
    assert change(chg, board, 25, delay_seconds=30).state.channel == 25  # 没有残留,可以重试


def test_unreadable_state_after_a_lost_response_does_not_claim_success(cr, ds, chg):
    board = network(cr, ds)
    board.pending_mode = "timeout_applied"
    real = board.send_command

    async def flaky(command, **args):
        if command == "read_attribute" and board.pending_calls:  # 命令发出之后的读取全部失败
            raise TimeoutError()
        return await real(command, **args)

    board.send_command = flaky
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board, 25)
    assert err.value.code == chg.ChangeChannelError.REJECTED and "不知道迁移是否已被收下" in str(err.value)


def test_definitive_rejection_does_not_poll(cr, ds, chg):
    board = network(cr, ds)
    board.pending_mode = "rejected"
    reads_before = sum(1 for c, _ in board.calls if c == "read_attribute")
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board, 25)
    assert err.value.code == chg.ChangeChannelError.REJECTED
    reads_after = sum(1 for c, _ in board.calls if c == "read_attribute")
    assert reads_after - reads_before == 1  # 只有预检查那一次读取


def test_refuses_while_an_earlier_migration_is_still_pending(cr, ds, chg):
    board = network(cr, ds)
    board.pending_mode = "never"  # 第一次迁移被设备收下,但延迟计时器还没到
    with pytest.raises(chg.ChangeChannelError) as first:
        change(chg, board, 25, delay_seconds=30, extra_wait=1)
    assert first.value.code == chg.ChangeChannelError.NOT_SWITCHED and board.pending_calls == 1
    with pytest.raises(chg.ChangeChannelError) as second:
        change(chg, board, 15)
    assert second.value.code == chg.ChangeChannelError.PENDING_IN_PROGRESS
    assert board.pending_calls == 1  # 没有再下发第二个 Pending


def test_pending_is_checked_before_the_same_channel_check(cr, ds, chg):
    """有待生效迁移时,"当前信道"马上会变,所以先报待生效,而不是"已经在这个信道"。"""
    board = network(cr, ds, channel=26)
    board.pending_mode = "never"
    with pytest.raises(chg.ChangeChannelError):
        change(chg, board, 25, delay_seconds=30, extra_wait=1)
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board, 26)
    assert err.value.code == chg.ChangeChannelError.PENDING_IN_PROGRESS


def test_allows_another_change_once_the_pending_one_has_taken_effect(cr, ds, chg):
    board = network(cr, ds)
    change(chg, board, 25, delay_seconds=30)
    assert board.pending_dataset_b64 is None
    result = change(chg, board, 15, delay_seconds=30)
    assert result.state.channel == 15 and board.pending_calls == 2


@pytest.mark.parametrize("kwargs", [{"delay_seconds": 5}, {"delay_seconds": 9999}])
def test_invalid_delay_is_rejected_before_touching_the_device(cr, ds, chg, kwargs):
    board = network(cr, ds)
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board, 25, **kwargs)
    assert err.value.code == chg.ChangeChannelError.INVALID and board.pending_calls == 0


def test_device_rejection_is_reported(cr, ds, chg):
    board = network(cr, ds)
    board.pending_mode = "rejected"
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board)
    assert err.value.code == chg.ChangeChannelError.REJECTED
    assert board.pending_calls == 1  # 被拒绝后不重试


def test_not_switched_in_time_is_reported_as_possibly_still_pending(cr, ds, chg):
    board = network(cr, ds)
    board.pending_mode = "never"
    clock = Clock()
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board, 25, clock=clock, delay_seconds=60, extra_wait=30.0, poll_interval=5.0)
    assert err.value.code == chg.ChangeChannelError.NOT_SWITCHED
    assert "之后仍可能生效" in str(err.value)
    assert 90 <= clock.now <= 100  # 等了 延迟 + 额外等待


def test_pending_timestamp_comes_from_the_injected_clock(cr, ds, chg):
    board = network(cr, ds)
    seen = {}
    original = board.send_command

    async def spy(command, **args):
        if args.get("command_name") == "SetPendingDatasetRequest":
            seen["ts"] = int.from_bytes(parse_tlv(base64.b64decode(args["payload"]["pendingDataset"]).hex())[T.PENDINGTIMESTAMP].data, "big") >> 16
        return await original(command, **args)

    board.send_command = spy
    change(chg, board, 25, now=lambda: 1_800_000_123.9)
    assert seen["ts"] == 1_800_000_123


def test_dataset_never_leaks_into_steps_or_errors(cr, ds, chg):
    board = network(cr, ds)
    secret = board.dataset_b64
    steps = []
    change(chg, board, 25, on_step=steps.append)
    assert secret not in str(steps)
    board2 = network(cr, ds)
    board2.pending_mode = "rejected"
    with pytest.raises(chg.ChangeChannelError) as err:
        change(chg, board2)
    assert board2.dataset_b64 not in str(err.value)


# ---- 整网迁移(采纳另一个网络) ----


def migrate(chg, board, target_tlv, **kwargs):
    clock = kwargs.pop("clock", None) or Clock()
    kwargs.setdefault("now", lambda: 1_700_000_000.0)
    kwargs.setdefault("delay_seconds", 30)
    return run(chg.migrate_network(board, NODE, target_tlv, sleep=clock.sleep, clock=clock, **kwargs))


def other_network(ds, name="Target", channel=15):
    return ds.generate_dataset(network_name=name, channel=channel).tlv_hex


def test_migrates_the_whole_network_to_the_target(cr, ds, chg):
    board = network(cr, ds, channel=26)
    target = other_network(ds)
    old_ts = board.network
    result = migrate(chg, board, target)
    assert result.state.extended_pan_id == int.from_bytes(parse_tlv(target)[T.EXTPANID].data, "big")
    assert board.network_name == "Target" and board.channel == 15
    assert board.network > old_ts and board.pending_calls == 1
    assert result.steps[-1] == "完成"


@pytest.mark.parametrize("setup, code", [
    ("no_network", "NO_NETWORK"),
    ("not_leader", "NOT_LEADER"),
    ("pending", "PENDING_IN_PROGRESS"),
    ("same_network", "SAME_NETWORK"),
])
def test_migration_preconditions(cr, ds, chg, setup, code):
    board = network(cr, ds) if setup != "no_network" else FakeBoard()
    target = other_network(ds)
    if setup == "not_leader":
        board.role_override = 5
    if setup == "pending":
        board.pending_dataset_b64 = "AA=="
    if setup == "same_network":
        target = base64.b64decode(board.dataset_b64).hex()
    with pytest.raises(chg.ChangeChannelError) as err:
        migrate(chg, board, target)
    assert err.value.code == getattr(chg.ChangeChannelError, code) and board.pending_calls == 0


def test_migration_rejects_a_garbage_target_before_touching_the_device(cr, ds, chg):
    board = network(cr, ds)
    with pytest.raises(chg.ChangeChannelError) as err:
        migrate(chg, board, "0102")
    assert err.value.code == chg.ChangeChannelError.INVALID and board.pending_calls == 0


def test_migration_survives_a_lost_response(cr, ds, chg):
    board = network(cr, ds)
    board.pending_mode = "timeout_applied"
    result = migrate(chg, board, other_network(ds))
    assert result.state.channel == 15 and board.pending_calls == 1


def test_migration_not_switched_in_time_is_reported(cr, ds, chg):
    board = network(cr, ds)
    board.pending_mode = "never"
    with pytest.raises(chg.ChangeChannelError) as err:
        migrate(chg, board, other_network(ds), extra_wait=1)
    assert err.value.code == chg.ChangeChannelError.NOT_SWITCHED
