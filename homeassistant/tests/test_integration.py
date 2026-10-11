# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""集成测试:在 HA 里加载 thread_br,用假的 Matter 客户端和假板子模拟设备。

需要 HA 环境(.venv,装有 HA),在没有装 HA 的环境里整个文件会被跳过:
  .venv/bin/python -m pytest homeassistant/tests -o asyncio_mode=auto
"""

import asyncio
import base64
import contextlib
from datetime import timedelta
import logging
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fakes import FakeBoard

pytest.importorskip("homeassistant.components.matter")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.thread import dataset_store  # noqa: E402
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError  # noqa: E402
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from matter_server.common.models import EventType  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed  # noqa: E402
from python_otbr_api.pskc import compute_pskc  # noqa: E402
from python_otbr_api.tlv_parser import MeshcopTLVType as T, parse_tlv  # noqa: E402
import voluptuous as vol  # noqa: E402

from custom_components.thread_br import manager as manager_module  # noqa: E402
from custom_components.thread_br.const import DOMAIN  # noqa: E402
from custom_components.thread_br.entity import matter_device_identifier, own_device_identifier  # noqa: E402

FABRIC = 0x61A304FB3D062339
BR_NODE, OTHER_NODE = 7, 1
BR_CLUSTER = 0x0452


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    return enable_custom_integrations


class FakeEndpoint:
    def __init__(self, attrs):
        self._attrs = attrs

    def has_attribute(self, cluster, attribute):
        return (cluster, attribute) in self._attrs


class FakeNode:
    """只用于发现节点(是不是边界路由器)和上下线。状态不从这里读:集成直接读设备,见 coordinator.py。"""

    def __init__(self, node_id, endpoints, available=True):
        self.node_id = node_id
        self.available = available
        self.endpoints = {ep: FakeEndpoint(a) for ep, a in endpoints.items()}


def border_router(node_id=BR_NODE):
    return FakeNode(node_id, {0: {(0x28, 3): "Thread BR"}, 1: {(BR_CLUSTER, 0): "Espressif-ThreadBR"}})


def other_device():
    return FakeNode(OTHER_NODE, {0: {(0x28, 3): "Air Quality Monitor"}, 1: {(0x040D, 0): 1}})


class FakeClient:
    """模拟 matter_server.client.MatterClient 中本集成用到的部分:命令转给假板子。"""

    def __init__(self, *nodes, board=None):
        self.server_info = SimpleNamespace(compressed_fabric_id=FABRIC)
        self.nodes = {n.node_id: n for n in nodes}
        self.board = board if board is not None else FakeBoard()
        self.boards = {}  # node_id -> 该节点自己的假板子;没登记的节点用 self.board
        self.synced_datasets = []  # set_thread_operational_dataset 收到的 dataset
        self._subs = []

    async def send_command(self, command, **args):
        return await self.boards.get(args.get("node_id"), self.board).send_command(command, **args)

    async def set_thread_operational_dataset(self, dataset, entry_id="default"):
        self.synced_datasets.append(dataset)

    def get_nodes(self):
        return list(self.nodes.values())

    def get_node(self, node_id):
        return self.nodes[node_id]

    def subscribe_events(self, callback, event_filter=None, node_filter=None, attr_path_filter=None):
        sub = (callback, event_filter, node_filter, attr_path_filter)
        self._subs.append(sub)
        return lambda: self._subs.remove(sub) if sub in self._subs else None

    def emit(self, event, data=None, node_id=None, path=None):
        for callback, event_filter, node_filter, path_filter in list(self._subs):
            if event_filter not in (None, event):
                continue
            if node_filter is not None and node_filter != node_id:
                continue
            if path_filter is not None and path_filter != path:
                continue
            callback(event, data)

    def attribute_updated(self, node_id, path, value=None):
        """matter-server 推送了某个属性的变化。数据是 (node_id, 路径, 值),与真实客户端一致。"""
        self.emit(EventType.ATTRIBUTE_UPDATED, data=(node_id, path, value), node_id=node_id, path=path)

    def subscriptions(self):
        return len(self._subs)

    def reads(self):
        return sum(1 for command, _ in self.board.calls if command == "read_attribute")


CURRENT = {"client": None}


@pytest.fixture(scope="module", autouse=True)
def _fake_matter_integration():
    """用假的 setup/unload 代替 Matter 集成对条目的处理,不连真实服务器。

    模块级夹具:补丁一直保留到最后一个测试的 hass 清理之后,
    否则 hass 清理时会走真实的 Matter 卸载逻辑。
    """

    async def fake_setup(hass, entry):
        entry.runtime_data = SimpleNamespace(adapter=SimpleNamespace(matter_client=CURRENT["client"]))
        return True

    patchers = [
        patch("homeassistant.components.matter.async_setup_entry", side_effect=fake_setup),
        patch("homeassistant.components.matter.async_unload_entry", return_value=True),
        # 只需要 thread 的凭证库;它的 async_setup 会开 mDNS 发现任务,在测试里没意义也会在拆除时报错
        patch("homeassistant.components.thread.async_setup", return_value=True),
        # thread 依赖 zeroconf;测试里不能开真实套接字,模拟的 zeroconf 在拆除时也会报错,所以整个跳过
        patch("homeassistant.components.zeroconf.async_setup", return_value=True),
    ]
    for p in patchers:
        p.start()
    yield
    for p in patchers:
        p.stop()


class VirtualClock:
    """虚拟时钟:sleep 只推进虚拟时间(并让出一次事件循环),不真的等。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    """改信道要等"延迟到期 + 额外等待"(最短 30 秒):测试里用虚拟时钟,真实行为由 test_change.py 的注入时钟覆盖。"""
    clock = VirtualClock()
    real = manager_module.change_channel

    async def fast(*args, **kwargs):
        return await real(*args, sleep=clock.sleep, clock=clock, **kwargs)

    monkeypatch.setattr(manager_module, "change_channel", fast)

    real_migrate = manager_module.migrate_network

    async def fast_migrate(*args, **kwargs):
        return await real_migrate(*args, sleep=clock.sleep, clock=clock, **kwargs)

    monkeypatch.setattr(manager_module, "migrate_network", fast_migrate)


def register_matter_device(hass, matter, client, node_id):
    """模拟 Matter 集成为节点注册设备。"""
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=matter.entry_id,
        identifiers={matter_device_identifier(client, node_id)},
        name="Thread BR" if node_id == BR_NODE else "Air Quality Monitor",
    )


async def setup_matter(hass, client, *, register_devices=True):
    """放一个已加载的 Matter 集成条目,并为节点注册 Matter 集成会创建的设备。"""
    CURRENT["client"] = client
    matter = MockConfigEntry(domain="matter", data={"url": "ws://fake"})
    matter.add_to_hass(hass)
    assert await hass.config_entries.async_setup(matter.entry_id)
    await hass.async_block_till_done()
    if register_devices:
        for node in client.nodes.values():
            register_matter_device(hass, matter, client, node.node_id)
    return matter


async def settle(hass):
    """等所有任务(含后台任务,如协调器的首次读取)跑完。"""
    await hass.async_block_till_done(wait_background_tasks=True)


async def setup_thread_br(hass, client, *, register_devices=True):
    matter = await setup_matter(hass, client, register_devices=register_devices)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return matter, entry


async def env(hass, board=None, *, with_other=True):
    nodes = [border_router()] + ([other_device()] if with_other else [])
    client = FakeClient(*nodes, board=board)
    matter, entry = await setup_thread_br(hass, client)
    own = dr.async_get(hass).async_get_device_by_identifier(own_device_identifier(client, BR_NODE), entry.entry_id)
    return client, entry, own.id


def manager_of(entry):
    return entry.runtime_data.manager


async def refresh(hass, entry):
    """立即从设备重新读一次状态(不经过去抖),并等实体更新完。"""
    await manager_of(entry).coordinator(BR_NODE).async_refresh()
    await settle(hass)


async def advance(hass, seconds):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await settle(hass)


def entity_id(hass, platform, key, node_id=BR_NODE, endpoint=1):
    unique_id = f"{DOMAIN}-{FABRIC:016X}-{node_id:016X}-{endpoint}-{key}"
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


def state_of(hass, platform, key):
    return hass.states.get(entity_id(hass, platform, key))


async def call(hass, service, **data):
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


def stored_datasets(hass):
    return list(hass.data[dataset_store.DATA_STORE].datasets.values()) if dataset_store.DATA_STORE in hass.data else []


def sent_channel(client):
    return parse_tlv(base64.b64decode(client.board.dataset_b64).hex())[T.CHANNEL].channel


# ---------------- 设备和实体的创建 ----------------


async def test_matter_device_identifier_matches_matter_integration(hass):
    from homeassistant.components.matter.helpers import get_node_device_identifier

    client = FakeClient(border_router())
    assert matter_device_identifier(client, BR_NODE) == get_node_device_identifier(client.server_info, BR_NODE)


async def test_entities_for_border_router_only_with_own_device_linked_to_matter_device(hass):
    client = FakeClient(border_router(), other_device())
    matter, entry = await setup_thread_br(hass, client)
    assert entry.state is ConfigEntryState.LOADED

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {e.translation_key for e in entities} == {
        "thread_network", "thread_role", "thread_channel", "credentials", "neighbors", "routes",
        "partition_id", "leader_router_id", "thread_interface", "network_fault", "create_network",
        "preferred_network", "adopt_network",
    }
    assert all(f"-{BR_NODE:016X}-" in e.unique_id for e in entities)  # 其他节点没有实体

    # HA 2026.9 起设备按配置条目独立:实体在本集成自己的设备上,不在 Matter 的设备上
    registry = dr.async_get(hass)
    own = registry.async_get_device_by_identifier(own_device_identifier(client, BR_NODE), entry.entry_id)
    matter_device = registry.async_get_device_by_identifier(matter_device_identifier(client, BR_NODE), matter.entry_id)
    assert {e.device_id for e in entities} == {own.id}
    assert own.id != matter_device.id and own.config_entry_id == entry.entry_id
    assert er.async_entries_for_device(er.async_get(hass), matter_device.id) == []
    assert own.via_device_id == matter_device.id  # 在 Matter 设备页显示为"已连接设备"
    assert own.name == "Thread BR Thread"
    assert registry.async_get_device_by_identifier(own_device_identifier(client, OTHER_NODE), entry.entry_id) is None


async def test_rarely_useful_diagnostics_are_disabled_by_default(hass):
    await env(hass)
    registry = er.async_get(hass)
    for key in ("partition_id", "leader_router_id"):
        assert registry.async_get(entity_id(hass, "sensor", key)).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    for key in ("thread_network", "neighbors", "thread_channel"):
        assert registry.async_get(entity_id(hass, "sensor", key)).disabled_by is None


async def test_device_linked_when_matter_device_appears_later(hass):
    client = FakeClient(border_router())
    matter, entry = await setup_thread_br(hass, client, register_devices=False)
    registry = dr.async_get(hass)
    own = registry.async_get_device_by_identifier(own_device_identifier(client, BR_NODE), entry.entry_id)
    assert own is not None and own.via_device_id is None
    matter_device = register_matter_device(hass, matter, client, BR_NODE)
    await advance(hass, 3)
    own = registry.async_get_device_by_identifier(own_device_identifier(client, BR_NODE), entry.entry_id)
    assert own.via_device_id == matter_device.id


async def test_border_router_added_later_and_removed(hass):
    client = FakeClient(other_device())
    matter, entry = await setup_thread_br(hass, client)
    assert entity_id(hass, "sensor", "thread_network") is None
    node = border_router()
    client.nodes[BR_NODE] = node
    register_matter_device(hass, matter, client, BR_NODE)
    client.emit(EventType.NODE_ADDED, data=node)
    await settle(hass)
    assert entity_id(hass, "sensor", "thread_network") is not None
    assert state_of(hass, "sensor", "thread_network").state == "none"  # 新加入的节点也会读一次状态

    before = len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id))
    client.emit(EventType.NODE_UPDATED, data=node, node_id=BR_NODE)  # 重复事件不会重复创建
    await settle(hass)
    assert len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == before

    client.emit(EventType.NODE_REMOVED, data=BR_NODE)
    await settle(hass)
    assert manager_of(entry).border_routers == {}
    assert dr.async_get(hass).async_get_device_by_identifier(own_device_identifier(client, BR_NODE), entry.entry_id) is None


# ---------------- 状态直接从设备读,不依赖缓存 ----------------


async def test_initial_state_is_read_from_the_device(hass):
    board = FakeBoard()
    client, entry, _ = await env(hass, board)
    assert state_of(hass, "sensor", "thread_network").state == "none"
    assert state_of(hass, "sensor", "thread_role").state == "unspecified"
    assert state_of(hass, "binary_sensor", "thread_interface").state == "off"
    assert client.reads() >= 1


async def test_state_comes_from_read_attribute_not_from_the_node_cache(hass):
    """真实的 matter-server 缓存里 Thread 诊断属性不会更新;节点对象在这里连 get_attribute_value 都没有,用了就会报错。"""
    assert not hasattr(border_router(), "get_attribute_value")
    await env(hass)


async def test_network_sensor_reflects_the_device(hass):
    client, entry, _ = await env(hass)
    board = client.board
    board.network, board.channel, board.network_name = 65536, 20, "ThreadBR-ab12"
    board.pan_id, board.ext_pan_id = 0x1E2F, 0x7CFCB5EFEE8C080A
    await refresh(hass, entry)
    state = state_of(hass, "sensor", "thread_network")
    assert state.state == "active"
    assert state.attributes["network_name"] == "ThreadBR-ab12" and state.attributes["channel"] == 20
    assert state.attributes["pan_id"] == "0x1e2f" and state.attributes["extended_pan_id"] == "7cfcb5efee8c080a"
    assert not any("key" in k.lower() or "pskc" in k.lower() for k in state.attributes)  # 不暴露密钥
    assert state_of(hass, "sensor", "thread_channel").state == "20"


@pytest.mark.parametrize(("value", "expected"), [
    (0, "unspecified"), (1, "unassigned"), (2, "sleepy_end_device"), (3, "end_device"),
    (4, "reed"), (5, "router"), (6, "leader"), (99, "unknown"),
])
async def test_role_sensor(hass, value, expected):
    client, entry, _ = await env(hass)
    client.board.role_override = value
    await refresh(hass, entry)
    assert state_of(hass, "sensor", "thread_role").state == expected


async def test_interface_binary_sensor(hass):
    client, entry, _ = await env(hass)
    client.board.network, client.board.channel = 65536, 25
    await refresh(hass, entry)
    assert state_of(hass, "binary_sensor", "thread_interface").state == "on"


async def test_diagnostic_counts_and_faults(hass):
    client, entry, _ = await env(hass)
    board = client.board
    board.neighbors, board.routes, board.faults = [{"0": 1}, {"0": 2}, {"0": 3}], [{"0": 9}, {"0": 8}], [1, 3]
    await refresh(hass, entry)
    assert state_of(hass, "sensor", "neighbors").state == "3" and state_of(hass, "sensor", "routes").state == "2"
    fault = state_of(hass, "binary_sensor", "network_fault")
    assert fault.state == "on" and fault.attributes["faults"] == ["link_down", "network_jammed"]
    board.faults = []
    await refresh(hass, entry)
    fault = state_of(hass, "binary_sensor", "network_fault")
    assert fault.state == "off" and "faults" not in fault.attributes


# ---------------- 刷新机制:事件、定时、失败恢复 ----------------


async def test_pushed_attribute_update_triggers_a_refresh(hass):
    client, entry, _ = await env(hass)
    client.board.network, client.board.channel = 65536, 15
    before = client.reads()
    client.attribute_updated(BR_NODE, f"1/{BR_CLUSTER}/4", 65536)
    await settle(hass)
    assert client.reads() == before + 1
    assert state_of(hass, "sensor", "thread_network").state == "active"


async def test_diagnostics_attribute_update_also_triggers_a_refresh(hass):
    client, entry, _ = await env(hass)
    before = client.reads()
    client.attribute_updated(BR_NODE, "1/53/1", 6)
    await settle(hass)
    assert client.reads() == before + 1


async def test_unrelated_attribute_update_does_not_trigger_a_refresh(hass):
    client, entry, _ = await env(hass)
    before = client.reads()
    client.attribute_updated(BR_NODE, "1/6/0", True)  # 别的 cluster
    client.attribute_updated(BR_NODE, f"0/{BR_CLUSTER}/4", 1)  # 别的端点
    await settle(hass)
    assert client.reads() == before


async def test_periodic_poll_keeps_state_fresh_even_without_events(hass):
    client, entry, _ = await env(hass)
    client.board.network, client.board.channel = 65536, 26
    await advance(hass, 35)
    assert state_of(hass, "sensor", "thread_network").state == "active"


async def test_unreachable_device_makes_entities_unavailable_then_recovers(hass):
    client, entry, _ = await env(hass)
    original = client.send_command
    failing = {"on": True}

    async def flaky(command, **args):
        if failing["on"] and command == "read_attribute":
            raise ConnectionError("设备没有响应")
        return await original(command, **args)

    client.send_command = flaky
    await manager_of(entry).coordinator(BR_NODE).async_refresh()
    await settle(hass)
    assert state_of(hass, "sensor", "thread_network").state == "unavailable"
    failing["on"] = False
    await refresh(hass, entry)
    assert state_of(hass, "sensor", "thread_network").state == "none"


async def test_availability_follows_the_matter_node(hass):
    client, entry, _ = await env(hass)
    node = client.nodes[BR_NODE]
    node.available = False
    client.emit(EventType.NODE_UPDATED, data=node, node_id=BR_NODE)
    await settle(hass)
    assert state_of(hass, "sensor", "thread_network").state == "unavailable"
    node.available = True
    client.emit(EventType.NODE_UPDATED, data=node, node_id=BR_NODE)
    await settle(hass)
    assert state_of(hass, "sensor", "thread_network").state == "none"


# ---------------- 加载、卸载、配置流 ----------------


async def test_unload_removes_subscriptions(hass):
    client, entry, _ = await env(hass)
    assert client.subscriptions() > 0
    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert client.subscriptions() == 0


async def test_reloads_when_matter_integration_is_unloaded(hass):
    client = FakeClient(border_router())
    matter, entry = await setup_thread_br(hass, client)
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(matter.entry_id)
    await settle(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY  # Matter 没有加载时,本集成重载失败并等待重试


async def test_setup_waits_for_matter(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    with patch("homeassistant.components.matter.async_setup", return_value=True):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_config_flow_requires_matter(hass):
    with patch("homeassistant.components.matter.async_setup", return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "matter_not_loaded"


async def test_config_flow_creates_single_entry(hass):
    await setup_matter(hass, FakeClient(border_router()))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT and again["reason"] == "single_instance_allowed"


async def test_services_are_described_and_registered(hass):
    from homeassistant.helpers.service import async_get_all_descriptions

    await env(hass)
    for name in ("create_network", "change_channel", "sync_network"):
        assert hass.services.has_service(DOMAIN, name)
    descriptions = (await async_get_all_descriptions(hass))[DOMAIN]
    assert set(descriptions["create_network"]["fields"]) == {"network_name", "channel", "passphrase"}
    assert set(descriptions["change_channel"]["fields"]) == {"channel", "delay"}
    for name in ("create_network", "change_channel", "sync_network"):
        assert descriptions[name]["target"]["device"] == [{"integration": DOMAIN}]


# ---------------- 创建网络 ----------------


async def test_create_network_service_end_to_end(hass, caplog):
    caplog.set_level(logging.INFO)
    client, entry, device_id = await env(hass)
    net, button = entity_id(hass, "sensor", "thread_network"), entity_id(hass, "button", "create_network")
    assert hass.states.get(net).state == "none" and hass.states.get(button).state != "unavailable"
    assert state_of(hass, "sensor", "credentials").state == "no_network"

    await call(hass, "create_network", device_id=device_id, network_name="MyThread", channel=20)

    state = hass.states.get(net)
    assert state.state == "active" and state.attributes["network_name"] == "MyThread" and state.attributes["channel"] == 20
    assert hass.states.get(button).state == "unavailable"
    # 凭证保存到了 HA 的 Thread 凭证库,并同步给 matter-server
    sent_tlv = base64.b64decode(client.board.dataset_b64).hex()
    store = await dataset_store.async_get_store(hass)
    (stored,) = store.datasets.values()
    assert stored.network_name == "MyThread" and stored.channel == 20 and stored.source == DOMAIN
    assert client.synced_datasets == [sent_tlv]
    assert store.preferred_dataset == stored.id  # 没有首选网络时,新网络成为首选
    assert state_of(hass, "sensor", "credentials").state == "saved"
    # 创建过程自己保存凭证,不会触发"观察到变化"的自动同步。它是后台任务,要先让它有机会跑完再断言
    await settle(hass)
    assert not any(a.get("command_name") == "GetActiveDatasetRequest" for c, a in client.board.calls if c == "device_command")
    assert "自动同步" not in caplog.text and issue(hass) is None


async def test_create_with_passphrase_derives_pskc_and_never_leaks_it(hass, caplog):
    caplog.set_level(logging.DEBUG)
    client, _, device_id = await env(hass)
    await call(hass, "create_network", device_id=device_id, network_name="PassNet", passphrase="my-secret-phrase")
    sent = parse_tlv(base64.b64decode(client.board.dataset_b64).hex())
    assert sent[T.PSKC].data == bytes(compute_pskc(sent[T.EXTPANID].data, "PassNet", "my-secret-phrase"))
    tlv_hex = base64.b64decode(client.board.dataset_b64).hex()
    for secret in ("my-secret-phrase", tlv_hex, client.board.dataset_b64, sent[T.NETWORKKEY].data.hex()):
        assert secret not in caplog.text


async def test_button_creates_network_with_defaults(hass):
    client, _, _ = await env(hass)
    await hass.services.async_call("button", "press", {"entity_id": entity_id(hass, "button", "create_network")}, blocking=True)
    assert state_of(hass, "sensor", "thread_network").state == "active"
    sent = parse_tlv(base64.b64decode(client.board.dataset_b64).hex())
    assert sent[T.NETWORKNAME].name.startswith("ThreadBR-") and sent[T.CHANNEL].channel == 25


async def test_creating_state_and_button_unavailable_while_in_progress(hass):
    board = FakeBoard()
    board.gate = asyncio.Event()
    client, _, device_id = await env(hass, board)
    net, button = entity_id(hass, "sensor", "thread_network"), entity_id(hass, "button", "create_network")
    task = hass.async_create_task(hass.services.async_call(DOMAIN, "create_network", {"device_id": device_id}, blocking=True))
    for _ in range(200):  # 等流程走到"下发 dataset"并卡在设备那一步。不能用 async_block_till_done:它会一直等到流程结束
        if board.set_calls:
            break
        await asyncio.sleep(0.01)
    assert board.set_calls == 1, "流程没有走到下发 dataset"
    assert hass.states.get(net).state == "creating" and hass.states.get(button).state == "unavailable"
    with pytest.raises(ServiceValidationError, match="already being created"):
        await call(hass, "create_network", device_id=device_id)
    board.gate.set()
    await asyncio.wait_for(task, 10)
    assert hass.states.get(net).state == "active"


async def test_existing_network_is_refused_with_clear_message(hass):
    board = FakeBoard(has_network=True)
    client, _, device_id = await env(hass, board)
    assert hass.states.get(entity_id(hass, "button", "create_network")).state == "unavailable"
    with pytest.raises(ServiceValidationError, match="no command to delete"):
        await call(hass, "create_network", device_id=device_id)
    assert board.set_calls == 0 and stored_datasets(hass) == []


async def test_failed_creation_changes_nothing(hass):
    board = FakeBoard(set_dataset="error")
    client, _, device_id = await env(hass, board)
    with pytest.raises(HomeAssistantError, match="Could not create"):
        await call(hass, "create_network", device_id=device_id)
    assert state_of(hass, "sensor", "thread_network").state == "none"
    assert state_of(hass, "button", "create_network").state != "unavailable"  # 可以再试
    assert stored_datasets(hass) == [] and client.synced_datasets == [] and board.network is None


async def test_invalid_parameters(hass):
    client, _, device_id = await env(hass)
    reads = client.reads()
    with pytest.raises(vol.Invalid):  # 信道在服务的参数校验里就被拒绝
        await call(hass, "create_network", device_id=device_id, channel=5)
    with pytest.raises(ServiceValidationError, match="Invalid parameter"):
        await call(hass, "create_network", device_id=device_id, passphrase="short")
    with pytest.raises(ServiceValidationError, match="Invalid parameter"):
        await call(hass, "create_network", device_id=device_id, network_name="x" * 17)
    assert [c for c, _ in client.board.calls if c != "read_attribute"] == [] and client.reads() == reads


async def test_empty_form_fields_are_treated_as_unset(hass):
    client, _, device_id = await env(hass)
    await call(hass, "create_network", device_id=device_id, network_name="", passphrase="")
    assert parse_tlv(base64.b64decode(client.board.dataset_b64).hex())[T.NETWORKNAME].name.startswith("ThreadBR-")


async def test_wrong_target_device_is_rejected(hass):
    client, entry, _ = await env(hass)
    other = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("thread_br", "else")}, name="x")
    for device_id in (other.id, "no-such-device"):
        with pytest.raises(ServiceValidationError, match="not a Thread border router"):
            await call(hass, "create_network", device_id=device_id)
    assert board_commands(client) == []


def board_commands(client):
    """板子收到的、除读状态以外的命令。"""
    return [a.get("command_name") for c, a in client.board.calls if c != "read_attribute"]


async def test_save_failure_reports_it_and_sync_recovers(hass):
    client, entry, device_id = await env(hass)
    with patch.object(dataset_store, "async_add_dataset", side_effect=OSError("disk full")):
        with pytest.raises(HomeAssistantError, match="credentials could not be saved"):
            await call(hass, "create_network", device_id=device_id)
    assert state_of(hass, "sensor", "thread_network").state == "active"  # 网络确实建好了,只是凭证没存
    assert stored_datasets(hass) == [] and state_of(hass, "sensor", "credentials").state == "missing"
    await call(hass, "sync_network", device_id=device_id)  # 从设备读回并补存
    assert len(stored_datasets(hass)) == 1 and state_of(hass, "sensor", "credentials").state == "saved"
    assert client.synced_datasets == [base64.b64decode(client.board.dataset_b64).hex()]


async def test_sync_without_network(hass):
    _, _, device_id = await env(hass)
    with pytest.raises(ServiceValidationError, match="no Thread network to"):
        await call(hass, "sync_network", device_id=device_id)


async def test_existing_preferred_network_is_not_replaced(hass):
    from custom_components.thread_br.dataset import generate_dataset

    client, _, device_id = await env(hass)
    await dataset_store.async_add_dataset(hass, "test", generate_dataset("OtherNet", 15).tlv_hex)
    store = await dataset_store.async_get_store(hass)
    (other,) = store.datasets.values()
    store.preferred_dataset = other.id
    await call(hass, "create_network", device_id=device_id)
    assert len(store.datasets) == 2 and store.preferred_dataset == other.id


# ---------------- 自动选信道 ----------------


@contextlib.contextmanager
def fake_zha(hass, channel=None, *, loaded=True, raises=None):
    """装一个已加载的 ZHA 条目,并让 get_zha_gateway 返回带指定信道的假网关(.venv 里没装 ZHA 的库)。

    退出时把条目状态还原,否则 hass 拆除时会去卸载它,而卸载要导入真实的 ZHA 库。
    """
    entry = None
    if loaded:
        entry = MockConfigEntry(domain="zha", data={})
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.LOADED)

    def get_zha_gateway(_hass):
        if raises:
            raise raises
        return SimpleNamespace(state=SimpleNamespace(network_info=SimpleNamespace(channel=channel)))

    package = types.ModuleType("homeassistant.components.zha")
    package.__path__ = []
    helpers = types.ModuleType("homeassistant.components.zha.helpers")
    helpers.get_zha_gateway = get_zha_gateway
    try:
        with patch.dict(sys.modules, {"homeassistant.components.zha": package, "homeassistant.components.zha.helpers": helpers}):
            yield
    finally:
        if entry is not None:
            entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def test_default_channel_without_any_information_is_25(hass, caplog):
    caplog.set_level(logging.INFO)
    client, _, device_id = await env(hass)
    await call(hass, "create_network", device_id=device_id)
    assert sent_channel(client) == 25 and "自动选择信道 25" in caplog.text


async def test_zigbee_channel_from_zha_is_avoided(hass):
    client, _, device_id = await env(hass)
    with fake_zha(hass, 25):
        await call(hass, "create_network", device_id=device_id)
    assert sent_channel(client) == 15


async def test_zigbee_on_20_does_not_change_the_default(hass):
    client, _, device_id = await env(hass)
    with fake_zha(hass, 20):
        await call(hass, "create_network", device_id=device_id)
    assert sent_channel(client) == 25


@pytest.mark.parametrize("scenario", ["not_loaded", "gateway_missing", "bad_value"])
async def test_zha_problems_are_ignored(hass, scenario):
    client, _, device_id = await env(hass)
    ctx = {"not_loaded": lambda: fake_zha(hass, 25, loaded=False),
           "gateway_missing": lambda: fake_zha(hass, raises=ValueError("No gateway object exists")),
           "bad_value": lambda: fake_zha(hass, 99)}[scenario]()
    with ctx:
        await call(hass, "create_network", device_id=device_id)
    assert sent_channel(client) == 25


async def test_options_wifi_and_avoid_channels_are_used(hass):
    client, entry, device_id = await env(hass)
    hass.config_entries.async_update_entry(entry, options={"wifi_channels": [13], "avoid_channels": []})
    await call(hass, "create_network", device_id=device_id)
    assert sent_channel(client) == 15  # Wi-Fi 13 压住 23~26


async def test_options_avoid_channels(hass):
    client, entry, device_id = await env(hass)
    hass.config_entries.async_update_entry(entry, options={"wifi_channels": [], "avoid_channels": [25, 15]})
    await call(hass, "create_network", device_id=device_id)
    assert sent_channel(client) == 26


async def test_explicit_channel_wins_over_options_and_zha(hass):
    client, entry, device_id = await env(hass)
    hass.config_entries.async_update_entry(entry, options={"wifi_channels": [], "avoid_channels": [11]})
    with fake_zha(hass, 11):
        await call(hass, "create_network", device_id=device_id, channel=11)
    assert sent_channel(client) == 11


async def test_no_channel_available_is_reported_without_touching_the_device(hass):
    client, entry, device_id = await env(hass)
    hass.config_entries.async_update_entry(entry, options={"wifi_channels": [], "avoid_channels": list(range(11, 27))})
    with pytest.raises(ServiceValidationError, match="No Thread channel is left"):
        await call(hass, "create_network", device_id=device_id)
    assert board_commands(client) == [] and state_of(hass, "sensor", "thread_network").state == "none"


async def test_button_uses_automatic_selection(hass):
    client, _, _ = await env(hass)
    with fake_zha(hass, 25):
        await hass.services.async_call("button", "press", {"entity_id": entity_id(hass, "button", "create_network")}, blocking=True)
    assert sent_channel(client) == 15


async def test_options_flow_saves_integers_and_prefills(hass):
    _, entry, _ = await env(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"wifi_channels": ["6", "1"], "avoid_channels": ["20"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"wifi_channels": [1, 6], "avoid_channels": [20]}
    again = await hass.config_entries.options.async_init(entry.entry_id)
    suggested = {str(k): k.description["suggested_value"] for k in again["data_schema"].schema if k.description}
    assert suggested == {"wifi_channels": ["1", "6"], "avoid_channels": ["20"]}


# ---------------- 凭证一致性、自动同步、修复提示 ----------------

ISSUE_ID = f"credentials_{BR_NODE}"


def issue(hass):
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)


def device_commands(client, name):
    return [a for c, a in client.board.calls if c == "device_command" and a["command_name"] == name]


async def create_via_service(hass, device_id, **kwargs):
    await call(hass, "create_network", device_id=device_id, **kwargs)


async def test_network_present_at_startup_but_not_saved_raises_an_issue_without_auto_sync(hass):
    """启动时就发现设备上有网络、HA 里没存:不擅自写入(也许是用户删掉的),只给修复提示。"""
    board = FakeBoard(has_network=True)
    board.channel, board.network_name, board.pan_id, board.ext_pan_id = 15, "Preexisting", 4660, 0x1122334455667788
    client, entry, _ = await env(hass, board)
    assert state_of(hass, "sensor", "credentials").state == "missing"
    found = issue(hass)
    assert found is not None and found.is_fixable and found.translation_key == "credentials_missing"
    assert found.translation_placeholders == {"name": "Preexisting"} and found.data == {"node_id": BR_NODE}
    assert device_commands(client, "GetActiveDatasetRequest") == [] and client.synced_datasets == []


async def test_no_issue_when_there_is_no_network(hass):
    await env(hass)
    assert issue(hass) is None and state_of(hass, "sensor", "credentials").state == "no_network"


async def test_no_issue_after_a_normal_create(hass):
    client, entry, device_id = await env(hass)
    await create_via_service(hass, device_id)
    assert issue(hass) is None and state_of(hass, "sensor", "credentials").state == "saved"


async def test_external_channel_change_is_synced_automatically(hass, chg):
    """网络在运行中被别的方式改了信道:观察到时间戳变化,自动把新凭证存进 HA。"""
    client, entry, device_id = await env(hass)
    await create_via_service(hass, device_id, channel=26)
    before = parse_tlv(client.synced_datasets[-1])[T.ACTIVETIMESTAMP].seconds

    await chg.change_channel(client.board, BR_NODE, 25, poll_interval=0, extra_wait=1.0, delay_seconds=30)  # 绕过集成,模拟外部改动
    reads_before = len(device_commands(client, "GetActiveDatasetRequest"))  # 模拟自己读 dataset 也算一次
    await refresh(hass, entry)

    assert len(device_commands(client, "GetActiveDatasetRequest")) == reads_before + 1  # 自动同步读了一次
    assert len(client.synced_datasets) == 2 and parse_tlv(client.synced_datasets[-1])[T.CHANNEL].channel == 25
    assert parse_tlv(client.synced_datasets[-1])[T.ACTIVETIMESTAMP].seconds == before + 1
    assert state_of(hass, "sensor", "credentials").state == "saved" and issue(hass) is None
    assert state_of(hass, "sensor", "thread_channel").state == "25"


async def test_network_created_externally_after_startup_is_synced_automatically(hass):
    client, entry, _ = await env(hass)
    board = client.board
    # 外部创建了网络(比如用命令行工具):下一次读状态时观察到 None -> 有值
    clock = SimpleNamespace(now=0.0)
    from conftest import _load

    ds, cr = _load("dataset"), _load("create")
    new = ds.generate_dataset(network_name="External", channel=20)

    class Clock:
        def __call__(self):
            return clock.now

        async def sleep(self, s):
            clock.now += s

    await cr.create_network(board, BR_NODE, new.tlv_hex, sleep=Clock().sleep, clock=Clock())
    await refresh(hass, entry)
    assert len(client.synced_datasets) == 1 and parse_tlv(client.synced_datasets[0])[T.NETWORKNAME].name == "External"
    assert state_of(hass, "sensor", "credentials").state == "saved" and issue(hass) is None


async def test_failed_auto_sync_raises_an_issue_and_is_not_retried_for_the_same_change(hass, chg):
    client, entry, device_id = await env(hass)
    await create_via_service(hass, device_id, channel=26)
    await chg.change_channel(client.board, BR_NODE, 25, poll_interval=0, extra_wait=1.0, delay_seconds=30)
    with patch.object(dataset_store, "async_add_dataset", side_effect=OSError("disk full")):
        await refresh(hass, entry)
        found = issue(hass)
        assert found is not None and found.translation_key == "credentials_out_of_sync"
        assert state_of(hass, "sensor", "credentials").state == "out_of_sync"
        attempts = len(device_commands(client, "GetActiveDatasetRequest"))
        await refresh(hass, entry)
        await refresh(hass, entry)
    assert len(device_commands(client, "GetActiveDatasetRequest")) == attempts  # 同一个变化只自动尝试一次


async def test_fix_flow_syncs_the_credentials_and_clears_the_issue(hass):
    from custom_components.thread_br import repairs

    board = FakeBoard(has_network=True)
    board.channel, board.network_name, board.pan_id, board.ext_pan_id = 15, "Preexisting", 4660, 0x1122334455667788
    board.dataset_b64 = base64.b64encode(bytes.fromhex(
        __import__("conftest")._load("dataset").generate_dataset(network_name="Preexisting", channel=15).tlv_hex)).decode()
    client, entry, _ = await env(hass, board)
    assert issue(hass) is not None

    flow = await repairs.async_create_fix_flow(hass, ISSUE_ID, {"node_id": BR_NODE})
    flow.hass, flow.handler, flow.flow_id = hass, DOMAIN, "test"
    form = await flow.async_step_confirm()
    assert form["type"] is FlowResultType.FORM and form["step_id"] == "confirm"
    done = await flow.async_step_confirm({})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await settle(hass)
    assert len(client.synced_datasets) == 1 and len(stored_datasets(hass)) == 1


async def test_fix_flow_reports_failure_without_leaking(hass):
    from custom_components.thread_br import repairs

    board = FakeBoard(has_network=True)
    board.channel, board.network_name, board.pan_id, board.ext_pan_id = 15, "Preexisting", 4660, 1
    board.dataset_b64 = base64.b64encode(bytes.fromhex(
        __import__("conftest")._load("dataset").generate_dataset(network_name="Preexisting", channel=15).tlv_hex)).decode()
    client, entry, _ = await env(hass, board)
    flow = await repairs.async_create_fix_flow(hass, ISSUE_ID, {"node_id": BR_NODE})
    flow.hass, flow.handler, flow.flow_id = hass, DOMAIN, "test"
    with patch.object(dataset_store, "async_add_dataset", side_effect=OSError("disk full")):
        result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "sync_failed"
    assert board.dataset_b64 not in str(result) and issue(hass) is not None


async def test_issue_cleared_when_the_network_goes_away_or_node_is_removed(hass):
    board = FakeBoard(has_network=True)
    board.channel, board.network_name, board.pan_id, board.ext_pan_id = 15, "Preexisting", 4660, 1
    client, entry, _ = await env(hass, board)
    assert issue(hass) is not None
    client.emit(EventType.NODE_REMOVED, data=BR_NODE)
    await settle(hass)
    assert issue(hass) is None


# ---------------- 改信道 ----------------


async def network_on(hass, channel=26, **board_kwargs):
    client, entry, device_id = await env(hass, **board_kwargs) if board_kwargs else await env(hass)
    await create_via_service(hass, device_id, channel=channel)
    return client, entry, device_id


async def test_change_channel_end_to_end(hass, caplog):
    caplog.set_level(logging.INFO)
    client, entry, device_id = await network_on(hass, 26)
    caplog.clear()
    assert state_of(hass, "sensor", "thread_channel").state == "26"
    await call(hass, "change_channel", device_id=device_id, channel=25, delay=30)

    assert state_of(hass, "sensor", "thread_channel").state == "25"
    assert state_of(hass, "sensor", "thread_network").state == "active"
    assert state_of(hass, "sensor", "thread_network").attributes["channel"] == 25
    # 新凭证已保存:信道 25,时间戳比旧的大;凭证状态是已保存
    assert parse_tlv(client.synced_datasets[-1])[T.CHANNEL].channel == 25
    stored = stored_datasets(hass)
    assert len(stored) == 1 and stored[0].channel == 25  # 同一个网络的新凭证替换旧的,不是多出一条
    assert state_of(hass, "sensor", "credentials").state == "saved" and issue(hass) is None
    assert client.board.pending_calls == 1
    await settle(hass)  # 自动同步是后台任务,先让它有机会跑完再断言
    assert "自动同步" not in caplog.text  # 迁移过程自己保存凭证,自动同步不能插手
    assert len(device_commands(client, "GetActiveDatasetRequest")) == 2  # 迁移流程自己读两次(切换前构造 Pending、切换后存新凭证),不含自动同步


async def test_change_channel_picks_automatically_when_no_channel_is_given(hass):
    client, entry, device_id = await network_on(hass, 26)
    await call(hass, "change_channel", device_id=device_id)  # 默认信道选择:25
    assert state_of(hass, "sensor", "thread_channel").state == "25"


async def test_change_channel_uses_zha_and_options_to_choose(hass):
    client, entry, device_id = await network_on(hass, 26)
    with fake_zha(hass, 25):
        await call(hass, "change_channel", device_id=device_id)
    assert state_of(hass, "sensor", "thread_channel").state == "15"


async def test_change_channel_shows_changing_state_while_waiting(hass):
    client, entry, device_id = await network_on(hass, 26)
    client.board.pending_reads = 10_000  # 先不让它切换,方便观察中间状态
    task = hass.async_create_task(hass.services.async_call(DOMAIN, "change_channel", {"device_id": device_id, "channel": 25}, blocking=True))
    for _ in range(300):
        if client.board.pending_calls:
            break
        await asyncio.sleep(0.01)
    assert client.board.pending_calls == 1
    assert state_of(hass, "sensor", "thread_network").state == "changing"
    assert state_of(hass, "button", "create_network").state == "unavailable"
    with pytest.raises(ServiceValidationError, match="already being created"):
        await call(hass, "change_channel", device_id=device_id, channel=15)
    with pytest.raises(ServiceValidationError, match="already being created"):
        await call(hass, "sync_network", device_id=device_id)
    client.board.pending_reads = 1  # 下一次读状态时切换
    await asyncio.wait_for(task, 10)
    assert state_of(hass, "sensor", "thread_network").state == "active"
    assert state_of(hass, "sensor", "thread_channel").state == "25"


async def test_change_channel_without_a_network(hass):
    client, entry, device_id = await env(hass)
    with pytest.raises(ServiceValidationError, match="no Thread network to change"):
        await call(hass, "change_channel", device_id=device_id, channel=25)
    assert client.board.pending_calls == 0


async def test_change_channel_refuses_when_not_leader(hass):
    client, entry, device_id = await network_on(hass, 26)
    client.board.role_override = 5
    with pytest.raises(ServiceValidationError, match="not the leader"):
        await call(hass, "change_channel", device_id=device_id, channel=25)
    assert client.board.pending_calls == 0


async def test_change_channel_to_the_current_channel(hass):
    client, entry, device_id = await network_on(hass, 25)
    with pytest.raises(ServiceValidationError, match="already on channel 25"):
        await call(hass, "change_channel", device_id=device_id, channel=25)
    with pytest.raises(ServiceValidationError, match="already on channel 25"):
        await call(hass, "change_channel", device_id=device_id)  # 自动选择的结果就是当前信道
    assert client.board.pending_calls == 0


async def test_change_channel_parameter_validation(hass):
    client, entry, device_id = await network_on(hass, 26)
    for kwargs in ({"channel": 5}, {"channel": 25, "delay": 5}, {"channel": 25, "delay": 9999}):
        with pytest.raises(vol.Invalid):
            await call(hass, "change_channel", device_id=device_id, **kwargs)
    assert client.board.pending_calls == 0


async def test_change_channel_rejected_by_device(hass):
    client, entry, device_id = await network_on(hass, 26)
    client.board.pending_mode = "rejected"
    with pytest.raises(HomeAssistantError, match="Could not change the channel"):
        await call(hass, "change_channel", device_id=device_id, channel=25)
    assert state_of(hass, "sensor", "thread_channel").state == "26"
    assert state_of(hass, "sensor", "thread_network").state == "active"  # 繁忙标记已解除
    assert state_of(hass, "button", "create_network").state == "unavailable"  # 仍然有网络,按钮不可用


async def test_change_channel_not_switched_in_time_explains_what_to_expect(hass):
    client, entry, device_id = await network_on(hass, 26)
    client.board.pending_mode = "never"
    with pytest.raises(HomeAssistantError, match="did not switch in time"):
        await call(hass, "change_channel", device_id=device_id, channel=25, delay=30)
    # 繁忙标记已解除;但设备上的迁移还在等计时器,所以网络传感器仍显示"迁移信道中"
    assert state_of(hass, "sensor", "thread_network").state == "changing"
    with pytest.raises(ServiceValidationError, match="already waiting"):
        await call(hass, "change_channel", device_id=device_id, channel=15, delay=30)
    assert client.board.pending_calls == 1  # 没有再下发第二个 Pending


async def test_late_switch_after_timeout_is_picked_up_by_auto_sync(hass):
    """超时后设备才切换:凭证由"观察到网络变化"的自动同步补上,用户不用做任何事。"""
    client, entry, device_id = await network_on(hass, 26)
    client.board.pending_mode = "never"
    with pytest.raises(HomeAssistantError):
        await call(hass, "change_channel", device_id=device_id, channel=25, delay=30)
    assert parse_tlv(client.synced_datasets[-1])[T.CHANNEL].channel == 26

    client.board.pending_mode, client.board.pending_reads = "ok", 1  # 设备终于切换了
    await refresh(hass, entry)  # 这次读取触发切换,读到的还是旧值
    await refresh(hass, entry)  # 下一次(实际中是下一个 30 秒轮询)读到新信道
    assert parse_tlv(client.synced_datasets[-1])[T.CHANNEL].channel == 25
    assert state_of(hass, "sensor", "credentials").state == "saved" and issue(hass) is None


async def test_change_channel_save_failure_is_reported(hass):
    client, entry, device_id = await network_on(hass, 26)
    with patch.object(dataset_store, "async_add_dataset", side_effect=OSError("disk full")):
        with pytest.raises(HomeAssistantError, match="credentials could not be saved"):
            await call(hass, "change_channel", device_id=device_id, channel=25)
    assert state_of(hass, "sensor", "thread_channel").state == "25"  # 网络确实迁了
    assert state_of(hass, "sensor", "credentials").state in ("out_of_sync", "missing")


async def test_change_channel_never_logs_the_dataset(hass, caplog):
    caplog.set_level(logging.DEBUG)
    client, entry, device_id = await network_on(hass, 26)
    await call(hass, "change_channel", device_id=device_id, channel=25)
    dataset_hex = client.synced_datasets[-1]
    key = parse_tlv(dataset_hex)[T.NETWORKKEY].data.hex()
    for secret in (dataset_hex, key, client.board.dataset_b64):
        assert secret not in caplog.text


@pytest.mark.parametrize("busy_set", ["_creating", "_changing"])
async def test_no_auto_sync_and_no_issue_while_an_operation_is_in_progress(hass, caplog, chg, busy_set):
    """操作进行中,设备上的网络变了而 HA 还没存:不能自动同步,也不能报修复提示(操作自己会保存凭证)。"""
    caplog.set_level(logging.INFO)
    client, entry, device_id = await network_on(hass, 26)
    manager = manager_of(entry)
    getattr(manager, busy_set).add(BR_NODE)  # 模拟创建/迁移正在进行
    await chg.change_channel(client.board, BR_NODE, 25, poll_interval=0, extra_wait=1.0, delay_seconds=30)
    reads_before = len(device_commands(client, "GetActiveDatasetRequest"))

    await refresh(hass, entry)
    assert state_of(hass, "sensor", "credentials").state == "out_of_sync"  # 状态如实反映
    assert issue(hass) is None and "自动同步" not in caplog.text
    assert len(device_commands(client, "GetActiveDatasetRequest")) == reads_before

    getattr(manager, busy_set).discard(BR_NODE)  # 操作结束,仍不一致:这时才给出修复提示
    manager._on_coordinator_update(BR_NODE)
    assert issue(hass) is not None and issue(hass).translation_key == "credentials_out_of_sync"


async def test_no_home_assistant_deprecation_warnings_from_the_integration(hass, caplog):
    """HA 对自定义集成用到的已弃用接口只记 WARNING(不抛异常),真实环境里就是这样被发现的。

    走一遍所有操作和选项流,确认没有这类警告。**注意它只能发现测试环境里这个 HA 版本已经会报的弃用**:
    比如 DeviceEntry.config_entries 的警告是 2026.10.1 才加的,在 2026.9.4 里它测不出来,
    这类问题靠 test_static_checks.py 里按源码扫描的检查兜底。
    """
    caplog.set_level(logging.WARNING)
    client, entry, device_id = await env(hass)
    await call(hass, "create_network", device_id=device_id, channel=26)
    await call(hass, "change_channel", device_id=device_id, channel=25, delay=30)
    await call(hass, "sync_network", device_id=device_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {"wifi_channels": ["6"], "avoid_channels": []})
    await settle(hass)
    deprecations = [r.getMessage() for r in caplog.records if "deprecated" in r.getMessage().lower()]
    assert deprecations == []


# ---------------- 采纳已有网络 ----------------


async def env_two_routers(hass, second_board=None):
    """两个边界路由器(node 7 和 8),各有自己的假板子。"""
    client = FakeClient(border_router(7), border_router(8), board=FakeBoard())
    client.boards[8] = second_board if second_board is not None else FakeBoard()
    matter, entry = await setup_thread_br(hass, client)
    registry = dr.async_get(hass)
    first = registry.async_get_device_by_identifier(own_device_identifier(client, 7), entry.entry_id)
    second = registry.async_get_device_by_identifier(own_device_identifier(client, 8), entry.entry_id)
    return client, entry, first.id, second.id


async def prefer_network(hass, name="HomeNet", channel=15):
    from custom_components.thread_br.dataset import generate_dataset

    tlv = generate_dataset(name, channel).tlv_hex
    await dataset_store.async_add_dataset(hass, "test", tlv)
    store = await dataset_store.async_get_store(hass)
    ext = parse_tlv(tlv)[T.EXTPANID].data.hex()
    store.preferred_dataset = next(d.id for d in store.datasets.values() if d.extended_pan_id == ext)
    return tlv


async def test_adopt_network_uses_the_preferred_network(hass, caplog):
    caplog.set_level(logging.DEBUG)
    client, entry, device_id = await env(hass)
    tlv = await prefer_network(hass, "HomeNet", 15)
    await call(hass, "adopt_network", device_id=device_id)

    state = state_of(hass, "sensor", "thread_network")
    assert state.state == "active" and state.attributes["network_name"] == "HomeNet" and state.attributes["channel"] == 15
    assert client.board.commands()[:1] == ["ArmFailSafe"] and "SetActiveDatasetRequest" in client.board.commands()
    assert base64.b64decode(client.board.dataset_b64).hex() == tlv  # 下发的就是首选网络,没有另外生成
    assert len(stored_datasets(hass)) == 1  # 没有多出一条凭证
    assert client.synced_datasets == [tlv]
    assert state_of(hass, "sensor", "credentials").state == "saved" and issue(hass) is None
    key = parse_tlv(tlv)[T.NETWORKKEY].data.hex()
    assert tlv not in caplog.text and key not in caplog.text


async def test_adopt_network_without_a_preferred_network_changes_nothing(hass):
    client, entry, device_id = await env(hass)
    with pytest.raises(ServiceValidationError, match="no preferred Thread network"):
        await call(hass, "adopt_network", device_id=device_id)
    assert client.board.network is None and client.board.commands() == []


async def test_second_border_router_joins_the_first_ones_network(hass):
    client, entry, first, second = await env_two_routers(hass)
    await call(hass, "create_network", device_id=first, network_name="Shared", channel=20)
    await call(hass, "adopt_network", device_id=second, source_device=first)

    a, b = client.boards[8], client.board
    assert a.network is not None and a.channel == 20 and a.network_name == "Shared"
    assert a.ext_pan_id == b.ext_pan_id and a.pan_id == b.pan_id  # 同一个网络
    assert len(stored_datasets(hass)) == 1  # 两块板子共用一条凭证
    assert hass.states.get(entity_id(hass, "sensor", "thread_network", node_id=8)).state == "active"


async def test_adopt_from_a_source_without_a_network_is_refused(hass):
    client, entry, first, second = await env_two_routers(hass)
    with pytest.raises(ServiceValidationError, match="source border router has no Thread network"):
        await call(hass, "adopt_network", device_id=second, source_device=first)
    assert client.boards[8].network is None and client.boards[8].commands() == []


async def test_adopt_onto_itself_is_refused(hass):
    client, entry, first, second = await env_two_routers(hass)
    await call(hass, "create_network", device_id=first, channel=20)
    with pytest.raises(ServiceValidationError, match="来源设备不能是它自己"):
        await call(hass, "adopt_network", device_id=first, source_device=first)


async def test_adopt_moves_a_router_that_is_on_another_network(hass):
    """设备已经在另一个网络里:整个旧网络迁到目标网络(Matter 没有删除网络的命令)。"""
    client, entry, first, second = await env_two_routers(hass)
    await call(hass, "create_network", device_id=first, network_name="One", channel=20)
    await call(hass, "create_network", device_id=second, network_name="Two", channel=15)
    old_ts = client.boards[8].network
    await call(hass, "adopt_network", device_id=second, source_device=first, delay=30)

    b, a = client.boards[8], client.board
    assert b.network_name == "One" and b.channel == 20 and b.ext_pan_id == a.ext_pan_id
    assert b.network > old_ts  # 时间戳比旧网络新,否则网格会忽略
    assert b.pending_calls == 1 and "SetActiveDatasetRequest" not in b.commands()[2:]  # 走的是 Pending,不是 SetActive
    assert hass.states.get(entity_id(hass, "sensor", "thread_network", node_id=8)).state == "active"
    assert hass.states.get(entity_id(hass, "sensor", "credentials", node_id=8)).state == "saved"


async def test_adopt_migration_needs_the_leader(hass):
    client, entry, first, second = await env_two_routers(hass)
    await call(hass, "create_network", device_id=first, network_name="One", channel=20)
    await call(hass, "create_network", device_id=second, network_name="Two", channel=15)
    client.boards[8].role_override = 5  # Router
    await manager_of(entry).coordinator(8).async_refresh()
    with pytest.raises(ServiceValidationError, match="not its leader"):
        await call(hass, "adopt_network", device_id=second, source_device=first)
    assert client.boards[8].pending_calls == 0 and client.boards[8].network_name == "Two"


async def test_adopt_twice_says_it_is_already_on_that_network(hass):
    client, entry, first, second = await env_two_routers(hass)
    await call(hass, "create_network", device_id=first, network_name="One", channel=20)
    await call(hass, "adopt_network", device_id=second, source_device=first)
    sets = client.boards[8].set_calls
    with pytest.raises(ServiceValidationError, match="already on that Thread network"):
        await call(hass, "adopt_network", device_id=second, source_device=first)
    assert client.boards[8].set_calls == sets  # 没有再下发


async def test_adopt_failure_is_reported_and_leaves_the_device_untouched(hass):
    client, entry, device_id = await env(hass, FakeBoard(set_dataset="error"))
    await prefer_network(hass)
    with pytest.raises(HomeAssistantError, match="Could not join the Thread network"):
        await call(hass, "adopt_network", device_id=device_id)
    assert client.board.network is None
    assert state_of(hass, "sensor", "thread_network").state == "none"
    assert hass.states.get(entity_id(hass, "button", "create_network")).state != "unavailable"  # 繁忙标记已解除


async def test_adopt_succeeds_even_if_ha_already_holds_newer_credentials_for_the_network(hass):
    """来源设备上的凭证比 HA 里的旧(HA 的是后来迁移信道得到的):板子照样加入,不能报"保存失败"。"""
    from python_otbr_api.tlv_parser import MeshcopTLVItem, encode_tlv

    client, entry, first, second = await env_two_routers(hass)
    await call(hass, "create_network", device_id=first, network_name="One", channel=20)
    items = dict(parse_tlv(client.synced_datasets[-1]))
    old_seconds = int.from_bytes(items[T.ACTIVETIMESTAMP].data, "big") >> 16
    items[T.ACTIVETIMESTAMP] = MeshcopTLVItem(int(T.ACTIVETIMESTAMP), ((old_seconds + 5) << 16).to_bytes(8, "big"))
    newer = encode_tlv(dict(sorted(items.items())))
    assert await dataset_store.async_add_dataset(hass, "test", newer) is dataset_store.DatasetAddResult.STORED

    await call(hass, "adopt_network", device_id=second, source_device=first)
    assert client.boards[8].network is not None and client.boards[8].channel == 20


# ---------------- 首选网络传感器与采纳按钮 ----------------


def pref_state(hass, node_id=BR_NODE):
    return hass.states.get(entity_id(hass, "sensor", "preferred_network", node_id=node_id))


def adopt_button(hass, node_id=BR_NODE):
    return hass.states.get(entity_id(hass, "button", "adopt_network", node_id=node_id))


async def press_adopt(hass, node_id=BR_NODE):
    await hass.services.async_call("button", "press", {"entity_id": entity_id(hass, "button", "adopt_network", node_id=node_id)}, blocking=True)


async def test_preferred_network_sensor_and_button_without_a_preferred_network(hass):
    client, entry, device_id = await env(hass)
    assert pref_state(hass).state == "no_preferred"
    assert adopt_button(hass).state == "unavailable"


async def test_preferred_network_states_follow_the_device_and_the_store(hass):
    client, entry, device_id = await env(hass)
    await prefer_network(hass, "HomeNet", 15)
    await refresh(hass, entry)
    assert pref_state(hass).state == "different" and adopt_button(hass).state != "unavailable"
    assert "neighbors" not in pref_state(hass).attributes  # 设备上没有网络,没有邻居可说

    await press_adopt(hass)
    assert pref_state(hass).state == "adopted" and adopt_button(hass).state == "unavailable"
    assert state_of(hass, "sensor", "thread_network").attributes["network_name"] == "HomeNet"


async def test_preferred_network_sensor_shows_neighbors_when_another_network_would_be_replaced(hass):
    client, entry, device_id = await env(hass)
    await call(hass, "create_network", device_id=device_id, network_name="Mine", channel=20)
    client.board.neighbors = [{"0": 1}, {"0": 2}, {"0": 3}]
    await prefer_network(hass, "HomeNet", 15)
    await refresh(hass, entry)
    state = pref_state(hass)
    assert state.state == "different" and state.attributes["neighbors"] == 3
    assert adopt_button(hass).state != "unavailable"


async def test_adopt_button_migrates_the_network_it_is_on_to_the_preferred_one(hass):
    client, entry, device_id = await env(hass)
    await call(hass, "create_network", device_id=device_id, network_name="Mine", channel=20)
    tlv = await prefer_network(hass, "HomeNet", 15)
    await refresh(hass, entry)
    await press_adopt(hass)

    board = client.board
    assert board.network_name == "HomeNet" and board.channel == 15 and board.pending_calls == 1
    assert pref_state(hass).state == "adopted"
    assert state_of(hass, "sensor", "credentials").state == "saved" and issue(hass) is None
    # 存回 HA 的是设备上的 dataset:同一个网络,时间戳比首选里的新或相等
    ext = parse_tlv(tlv)[T.EXTPANID].data.hex()
    stored = [d for d in stored_datasets(hass) if d.extended_pan_id == ext]
    assert len(stored) == 1


async def test_adopt_button_is_unavailable_while_a_migration_is_pending(hass):
    client, entry, device_id = await env(hass)
    await call(hass, "create_network", device_id=device_id, network_name="Mine", channel=20)
    await prefer_network(hass, "HomeNet", 15)
    await refresh(hass, entry)
    client.board.pending_mode = "never"
    with pytest.raises(HomeAssistantError, match="did not switch in time"):
        await press_adopt(hass)
    assert state_of(hass, "sensor", "thread_network").state == "changing"
    with pytest.raises(ServiceValidationError, match="already waiting"):
        await press_adopt(hass)
    assert client.board.pending_calls == 1
