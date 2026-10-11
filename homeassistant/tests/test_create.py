# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""create.py 的单元测试:用一个模拟板子行为的假对象验证流程。"""

import asyncio
import base64

import pytest

NODE = 7
GOOD_TLV = "0e080000000000010000" + "00" * 4  # 内容无关紧要,只要是合法十六进制


def run(coro):
    return asyncio.run(coro)


from fakes import Clock, FakeBoard  # noqa: E402


def create(cr, board, *, clock=None, **kwargs):
    clock = clock or Clock()
    return run(cr.create_network(board, NODE, GOOD_TLV, sleep=clock.sleep, clock=clock, **kwargs))


def test_success_path(cr):
    board = FakeBoard()
    steps = []
    result = create(cr, board, on_step=steps.append)
    assert board.commands() == ["ArmFailSafe", "SetActiveDatasetRequest", "CommissioningComplete"]
    assert board.arm_calls() == [cr.FAILSAFE_SECONDS]
    assert board.set_calls == 1 and board.commit_calls == 1
    assert result.state.has_network and not result.dry_run
    assert steps == result.steps and steps[-1] == "完成"
    assert board.armed is False


def test_dataset_is_sent_as_base64(cr):
    board = FakeBoard()
    create(cr, board)
    sent = next(a for c, a in board.calls if a.get("command_name") == "SetActiveDatasetRequest")
    assert base64.b64decode(sent["payload"]["activeDataset"]).hex() == GOOD_TLV
    assert (sent["node_id"], sent["endpoint_id"], sent["cluster_id"]) == (NODE, 1, 0x0452)


def test_existing_network_is_refused_before_any_command(cr):
    board = FakeBoard(has_network=True)
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board)
    assert err.value.code == cr.CreateNetworkError.ALREADY_EXISTS
    assert board.commands() == []


def test_failsafe_rejected(cr):
    board = FakeBoard(failsafe_code=3)  # BusyWithOtherAdmin
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board)
    assert err.value.code == cr.CreateNetworkError.FAILSAFE
    assert board.set_calls == 0


def test_invalid_in_state_race_disarms(cr):
    board = FakeBoard(set_dataset="invalid_in_state")
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board)
    assert err.value.code == cr.CreateNetworkError.ALREADY_EXISTS
    assert board.arm_calls() == [cr.FAILSAFE_SECONDS, 0]


def test_timeout_but_applied_continues_without_retry(cr):
    board = FakeBoard(set_dataset="timeout_applied")
    result = create(cr, board)
    assert board.set_calls == 1  # 结果不明时不能重试下发
    assert board.commands().count("CommissioningComplete") == 1
    assert result.state.has_network


def test_timeout_not_applied_polls_then_fails_and_rolls_back(cr):
    board = FakeBoard(set_dataset="timeout_not_applied")
    clock = Clock()
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board, clock=clock, poll_interval=2.0, poll_timeout=10.0)
    assert err.value.code == cr.CreateNetworkError.SET_DATASET
    assert board.set_calls == 1
    assert 10.0 <= clock.now <= 12.0 and len(clock.sleeps) >= 4  # 按间隔轮询到超时
    assert board.arm_calls() == [cr.FAILSAFE_SECONDS, 0]
    assert board.commit_calls == 0


def test_definitive_rejection_fails_fast_without_polling(cr):
    """设备明确拒绝(带 Matter 状态码)时结果是确定的,不能再白等一轮轮询。"""
    board = FakeBoard(set_dataset="error")
    clock = Clock()
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board, clock=clock)
    assert err.value.code == cr.CreateNetworkError.SET_DATASET
    assert board.arm_calls()[-1] == 0 and board.network is None
    assert clock.sleeps == [] and board.commands().count("SetActiveDatasetRequest") == 1
    assert not any(c == "read_attribute" for c, _ in board.calls[1:])  # 除预检查外没有再读状态


def test_unknown_error_without_status_code_polls_before_giving_up(cr):
    """README 里记过:'Operation aborted' 不一定代表失败,必须先读设备状态。"""
    board = FakeBoard(set_dataset="aborted")
    clock = Clock()
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board, clock=clock, poll_interval=2.0, poll_timeout=6.0)
    assert err.value.code == cr.CreateNetworkError.SET_DATASET
    assert len(clock.sleeps) >= 3 and board.commands().count("SetActiveDatasetRequest") == 1
    assert board.arm_calls()[-1] == 0


def test_commit_rejected(cr):
    board = FakeBoard(commit="error_code")
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board)
    assert err.value.code == cr.CreateNetworkError.COMMISSIONING_COMPLETE
    assert board.commit_calls == 1  # 设备明确拒绝,不重试


def test_commit_retried_once_after_exception(cr):
    board = FakeBoard(commit="raise_once")
    result = create(cr, board)
    assert board.commit_calls == 2 and result.state.has_network


def test_commit_fails_after_retry(cr):
    board = FakeBoard(commit="raise_always")
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board)
    assert err.value.code == cr.CreateNetworkError.COMMISSIONING_COMPLETE
    assert board.commit_calls == 2


def test_not_confirmed_after_commit(cr):
    class Vanishing(FakeBoard):
        async def send_command(self, command, **args):
            out = await super().send_command(command, **args)
            if args.get("command_name") == "CommissioningComplete":
                self.network = None
            return out

    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, Vanishing())
    assert err.value.code == cr.CreateNetworkError.NOT_CONFIRMED


def test_dry_run_never_creates_a_network(cr):
    board = FakeBoard()
    result = create(cr, board, dry_run=True)
    assert result.dry_run and not result.state.has_network
    assert board.network is None and board.armed is False
    assert board.commands() == ["SetActiveDatasetRequest", "ArmFailSafe", "ArmFailSafe"]
    assert board.arm_calls() == [60, 0]
    assert board.commit_calls == 0
    # 探测那次 SetActive 必须发生在开 fail-safe 之前
    assert board.commands().index("SetActiveDatasetRequest") < board.commands().index("ArmFailSafe")


def test_dry_run_with_existing_network_is_refused(cr):
    board = FakeBoard(has_network=True)
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board, dry_run=True)
    assert err.value.code == cr.CreateNetworkError.ALREADY_EXISTS
    assert board.commands() == []


def test_dry_run_stops_if_probe_is_not_rejected(cr):
    board = FakeBoard(require_failsafe=False)  # 设备行为异常:没开 fail-safe 也接受
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board, dry_run=True)
    assert err.value.code == cr.CreateNetworkError.PROBE
    assert "ArmFailSafe" not in board.commands()


def test_dry_run_stops_on_unexpected_probe_error(cr):
    class Odd(FakeBoard):
        async def send_command(self, command, **args):
            if args.get("command_name") == "SetActiveDatasetRequest":
                raise RuntimeError("Constraint error (code 135)")
            return await super().send_command(command, **args)

    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, Odd(), dry_run=True)
    assert err.value.code == cr.CreateNetworkError.PROBE


@pytest.mark.parametrize("kwargs", [{}, {"set_dataset": "error"}, {"set_dataset": "timeout_not_applied"}, {"commit": "error_code"}])
def test_dataset_never_appears_in_steps_or_errors(cr, kwargs):
    board = FakeBoard(**kwargs)
    secret_hex, secret_b64 = GOOD_TLV, base64.b64encode(bytes.fromhex(GOOD_TLV)).decode()
    steps, text = [], ""
    try:
        text = str(create(cr, board, on_step=steps.append, poll_timeout=2.0).steps)
    except cr.CreateNetworkError as err:
        text = str(err)
    text += str(steps)
    assert secret_hex not in text and secret_b64 not in text


def test_find_border_router_endpoint(cr):
    assert cr.find_border_router_endpoint({"0/40/3": "x", "1/1106/4": None, "1/53/0": 1}) == 1
    assert cr.find_border_router_endpoint({"2/1106/0": "n"}) == 2
    assert cr.find_border_router_endpoint({"0/40/3": "x", "1/53/0": 1}) is None


def test_read_state_parses_attributes(cr):
    state = run(cr.read_state(FakeBoard(has_network=True), NODE))
    assert state.has_network and state.interface_enabled is True
    state = run(cr.read_state(FakeBoard(), NODE))
    assert not state.has_network and state.interface_enabled is False


# ---- 命令超时:matter 客户端的 send_command 自己不会超时,服务器不回应时不能一直等 ----


@pytest.fixture
def short_timeouts(cr, monkeypatch):
    monkeypatch.setattr(cr, "COMMAND_TIMEOUT", 0.05)
    monkeypatch.setattr(cr, "SET_DATASET_TIMEOUT", 0.05)


def test_arm_fail_safe_hang_becomes_error(cr, short_timeouts):
    board = FakeBoard()
    board.hang = {"ArmFailSafe"}
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board)
    assert err.value.code == cr.CreateNetworkError.FAILSAFE
    assert "TimeoutError" in str(err.value)  # 超时的异常消息为空,要有可读的描述
    assert board.set_calls == 0


def test_set_dataset_hang_is_treated_as_unknown_outcome(cr, short_timeouts):
    board = FakeBoard()
    board.hang = {"SetActiveDatasetRequest"}
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board, poll_timeout=0.2, poll_interval=0.1)
    assert err.value.code == cr.CreateNetworkError.SET_DATASET
    assert board.network is None  # 挂起的命令没有生效
    assert board.commands().count("SetActiveDatasetRequest") == 1  # 只发过一次,没有重试
    assert board.arm_calls()[-1] == 0  # 超时后撤销 fail-safe,设备侧自动回滚


def test_commissioning_complete_hang_fails_after_one_retry(cr, short_timeouts):
    board = FakeBoard()
    board.hang = {"CommissioningComplete"}
    with pytest.raises(cr.CreateNetworkError) as err:
        create(cr, board)
    assert err.value.code == cr.CreateNetworkError.COMMISSIONING_COMPLETE
    assert board.commands().count("CommissioningComplete") == 2  # 原始一次 + 重试一次


def test_read_state_hang_raises_timeout(cr, short_timeouts):
    class Hang:
        async def send_command(self, command, **args):
            await asyncio.sleep(3600)

    with pytest.raises(asyncio.TimeoutError):
        run(cr.read_state(Hang(), NODE))


# ---- 读回 dataset ----


def test_read_active_dataset_returns_none_without_network(cr):
    assert run(cr.read_active_dataset(FakeBoard(), NODE)) is None


def test_read_active_dataset_roundtrip_after_create(cr):
    board = FakeBoard()
    create(cr, board)
    assert run(cr.read_active_dataset(board, NODE)) == GOOD_TLV


# ---- 一次读出完整状态 ----


def test_read_border_router_state_without_network(cr):
    state = run(cr.read_border_router_state(FakeBoard(), NODE))
    assert not state.has_network and not state.is_leader
    assert (state.channel, state.network_name, state.pan_id, state.extended_pan_id) == (None, None, None, None)
    assert state.interface_enabled is False and state.role == 0
    assert state.neighbor_count == 0 and state.route_count == 0 and state.faults == ()


def test_read_border_router_state_after_create_uses_the_dataset(cr, ds):
    board = FakeBoard()
    new = ds.generate_dataset(network_name="StateNet", channel=20)
    run(cr.create_network(board, NODE, new.tlv_hex, sleep=Clock().sleep, clock=Clock()))
    state = run(cr.read_border_router_state(board, NODE))
    assert state.has_network and state.is_leader and state.interface_enabled is True
    assert (state.channel, state.network_name) == (20, "StateNet")
    assert state.pan_id == new.pan_id and f"{state.extended_pan_id:016x}" == new.ext_pan_id


def test_read_border_router_state_counts_tables_and_faults(cr):
    board = FakeBoard()
    board.neighbors, board.routes, board.faults = [{"0": 1}, {"0": 2}, {"0": 3}], [{"0": 9}], [3]
    state = run(cr.read_border_router_state(board, NODE))
    assert (state.neighbor_count, state.route_count, state.faults) == (3, 1, (3,))


def test_read_border_router_state_reports_a_pending_dataset(cr):
    board = FakeBoard()
    assert run(cr.read_border_router_state(board, NODE)).has_pending is False
    board.pending_dataset_b64 = "AA=="
    state = run(cr.read_border_router_state(board, NODE))
    assert state.has_pending is True and state.pending_timestamp == 1


def test_read_border_router_state_asks_for_each_attribute_once_in_one_command(cr):
    board = FakeBoard()
    run(cr.read_border_router_state(board, NODE))
    reads = [a for c, a in board.calls if c == "read_attribute"]
    assert len(reads) == 1 and len(reads[0]["attribute_path"]) == len(set(reads[0]["attribute_path"])) == 13
