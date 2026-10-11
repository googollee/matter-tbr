# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""credentials.py 的单元测试。"""

import pytest

EXT = 0x7CFCB5EFEE8C080A
EXT_HEX = "7cfcb5efee8c080a"


@pytest.fixture
def mod(ch):
    from conftest import _load

    return _load("credentials"), _load("create")


def state(cr, **kw):
    base = dict(interface_enabled=True, active_timestamp=65536, channel=25, role=6, network_name="N", pan_id=1,
                extended_pan_id=EXT, neighbor_count=0, route_count=0, partition_id=1, leader_router_id=1, faults=())
    base.setdefault("pending_timestamp", None)
    base.update(kw)
    return cr.BorderRouterState(**base)


def test_no_network(mod):
    cred, cr = mod
    assert cred.credentials_status(state(cr, active_timestamp=None), [(EXT_HEX, 65536)]) == cred.NO_NETWORK


def test_missing_when_the_network_is_not_stored(mod):
    cred, cr = mod
    assert cred.credentials_status(state(cr), []) == cred.MISSING
    assert cred.credentials_status(state(cr), [("1111111111111111", 65536)]) == cred.MISSING  # 别的网络不算


def test_saved_when_timestamps_match(mod):
    cred, cr = mod
    assert cred.credentials_status(state(cr), [(EXT_HEX, 65536)]) == cred.SAVED
    assert cred.credentials_status(state(cr), [(EXT_HEX.upper(), 65536)]) == cred.SAVED  # 大小写不敏感


def test_out_of_sync_when_stored_is_older(mod):
    cred, cr = mod
    assert cred.credentials_status(state(cr, active_timestamp=131072), [(EXT_HEX, 65536)]) == cred.OUT_OF_SYNC


def test_newer_stored_credentials_do_not_nag(mod):
    cred, cr = mod
    assert cred.credentials_status(state(cr, active_timestamp=65536), [(EXT_HEX, 131072)]) == cred.SAVED


def test_uses_the_newest_matching_entry(mod):
    cred, cr = mod
    stored = [(EXT_HEX, 65536), ("2222222222222222", 999999999), (EXT_HEX, 131072)]
    assert cred.credentials_status(state(cr, active_timestamp=131072), stored) == cred.SAVED


def test_unreadable_extended_pan_id_is_not_blamed_on_the_user(mod):
    cred, cr = mod
    assert cred.credentials_status(state(cr, extended_pan_id=None), []) == cred.SAVED


def test_every_state_is_listed(mod):
    cred, _ = mod
    assert set(cred.STATES) == {cred.NO_NETWORK, cred.SAVED, cred.MISSING, cred.OUT_OF_SYNC}
