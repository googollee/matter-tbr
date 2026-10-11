# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""channels.py 的单元测试。"""

import pytest


def test_wifi_overlap_matches_the_well_known_table(ch):
    assert ch.wifi_overlap(1) == {11, 12, 13, 14}
    assert ch.wifi_overlap(6) == {16, 17, 18, 19}
    assert ch.wifi_overlap(11) == {21, 22, 23, 24}
    assert ch.wifi_overlap(13) == {23, 24, 25, 26}  # 22 的 2460MHz 在 2461MHz 的带宽边缘之外
    assert ch.wifi_overlap(14) == {25, 26}  # 2473~2495MHz 含 2475 和 2480


def test_frequencies(ch):
    assert [ch.thread_frequency(c) for c in (11, 15, 20, 25, 26)] == [2405, 2425, 2450, 2475, 2480]
    assert [ch.wifi_frequency(c) for c in (1, 6, 11, 13, 14)] == [2412, 2437, 2462, 2472, 2484]


def test_without_any_information_prefers_25(ch):
    s = ch.choose_channel()
    assert (s.channel, s.tier) == (25, 1) and s.excluded == {}


def test_the_users_environment_picks_25(ch):
    """用户的真实环境:Wi-Fi 在 6,Zigbee 在 20。"""
    s = ch.choose_channel(zigbee=[20], wifi=[6])
    assert (s.channel, s.tier) == (25, 1)
    assert {16, 17, 18, 19, 20, 19, 21} <= set(s.excluded)


def test_zigbee_on_25_moves_to_15_and_skips_its_neighbours(ch):
    s = ch.choose_channel(zigbee=[25])
    assert (s.channel, s.tier) == (15, 1)
    assert s.excluded[25] == "与 Zigbee 25 信道同频" and 26 in s.excluded  # 26 紧邻 25


def test_relaxes_to_soft_conflicts_before_giving_up_the_preferred_channels(ch):
    # 25、15 都被 Zigbee 占了:26 只是紧邻 25(软冲突),放宽后可以用
    s = ch.choose_channel(zigbee=[25, 15])
    assert (s.channel, s.tier) == (26, 2)


def test_wifi_13_pushes_to_15(ch):
    s = ch.choose_channel(wifi=[13])
    assert (s.channel, s.tier) == (15, 1)


def test_falls_back_to_the_channel_farthest_from_the_interferers(ch):
    # Wi-Fi 13 压住 23~26,Zigbee 15 同频占 15:首选信道全部不可用
    s = ch.choose_channel(wifi=[13], zigbee=[15])
    assert s.tier == 3
    assert s.channel == 20  # 离 2472(Wi-Fi 13)和 2425(Zigbee 15)中最近的一个最远
    assert s.channel not in s.excluded


def test_user_avoid_list_is_a_hard_exclusion(ch):
    assert ch.choose_channel(avoid=[25]).channel == 15
    assert ch.choose_channel(avoid=[25, 15]).channel == 26


def test_all_channels_excluded_raises(ch):
    with pytest.raises(ch.NoChannelAvailable):
        ch.choose_channel(avoid=range(11, 27))


def test_result_is_deterministic(ch):
    args = dict(zigbee=[20], wifi=[1, 11], avoid=[15])
    assert ch.choose_channel(**args) == ch.choose_channel(**args)


@pytest.mark.parametrize("kwargs", [
    {"zigbee": [10]}, {"zigbee": [27]}, {"wifi": [0]}, {"wifi": [15]}, {"avoid": [3]},
    {"zigbee": ["20"]}, {"zigbee": [True]}, {"wifi": [6.0]},
])
def test_invalid_inputs_are_rejected(ch, kwargs):
    with pytest.raises(ValueError):
        ch.choose_channel(**kwargs)


def test_chosen_channel_is_always_valid_and_never_hard_excluded(ch):
    """穷举一批组合:结果必须在 11~26,且不能落在硬冲突里。"""
    import itertools

    for zigbee, wifi in itertools.product([[], [11], [15], [20], [25], [26], [15, 25]], [[], [1], [6], [11], [13], [1, 6, 11]]):
        try:
            s = ch.choose_channel(zigbee=zigbee, wifi=wifi)
        except ch.NoChannelAvailable:
            continue
        assert 11 <= s.channel <= 26
        assert s.channel not in set(zigbee)
        assert all(s.channel not in ch.wifi_overlap(w) for w in wifi)
