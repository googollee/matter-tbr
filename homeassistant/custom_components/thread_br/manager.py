# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""发现边界路由器节点,为它们创建设备和实体,并执行创建、迁移信道、同步凭证等操作。

HA 2026.9 起设备按配置条目各自独立:同一个标识在不同条目下是不同的设备,
子设备的父设备也必须来自同一个条目。所以本集成不能把实体并进 Matter 集成的设备,
而是为每个边界路由器注册自己的设备,并用 via_device_id 指向 Matter 的设备,
在 Matter 设备页上显示为"已连接设备"。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.components.thread import dataset_store
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from matter_server.common.models import EventType
from python_otbr_api.tlv_parser import MeshcopTLVType, parse_tlv

from .change import ChangeChannelError, change_channel, migrate_network
from .channels import NoChannelAvailable, choose_channel
from .const import (
    ATTR_BORDER_ROUTER_NAME,
    CONF_AVOID_CHANNELS,
    CONF_WIFI_CHANNELS,
    DEFAULT_DEVICE_NAME,
    DOMAIN,
    LOGGER,
    THREAD_BR_MANAGEMENT_CLUSTER,
    THREAD_NETWORK_DIAGNOSTICS_CLUSTER,
)
from .coordinator import BorderRouterCoordinator
from .create import CreateNetworkError, create_network, read_active_dataset, read_border_router_state
from .credentials import NO_NETWORK, SAVED
from .dataset import DEFAULT_DELAY_SECONDS, DatasetError, generate_dataset, validate_dataset
from .entity import matter_device_identifier, own_device_identifier

if TYPE_CHECKING:
    from matter_server.client import MatterClient
    from matter_server.client.models.node import MatterNode

# 平台模块提供的工厂函数:(manager, node_id, endpoint_id) -> 实体列表
EntityFactory = Callable[["ThreadBrManager", int, int], list[Entity]]

# 写进 HA Thread 凭证库时标记的来源
DATASET_SOURCE = DOMAIN

# Matter 设备可能比我们晚注册;找不到时按这些间隔(秒)重试后补上关联
LINK_RETRY_DELAYS = (2, 10, 60)

# 改信道时:多久读一次设备状态确认是否已切换;延迟到期后最多再等多久
CHANGE_POLL_INTERVAL = 5.0
CHANGE_EXTRA_WAIT = 120.0


def zigbee_channels(hass: HomeAssistant) -> list[int]:
    """附近(其实是同一个 HA 里)Zigbee 网络的信道。目前只认 ZHA,读不到就当没有。

    Zigbee 与 Thread 同频同信道号,信道相同会互相干扰。ZHA 的内部接口随版本可能变,
    所以任何失败都只记日志、不影响创建。
    """
    if not hass.config_entries.async_loaded_entries("zha"):
        return []
    try:
        from homeassistant.components.zha.helpers import get_zha_gateway  # noqa: PLC0415

        channel = get_zha_gateway(hass).state.network_info.channel
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("读取 ZHA 的 Zigbee 信道失败,忽略:%s", err)
        return []
    return [channel] if isinstance(channel, int) and 11 <= channel <= 26 else []


def border_router_endpoint(node: MatterNode) -> int | None:
    """返回节点上带 Thread BR 管理 cluster 的端点;不是边界路由器则返回 None。"""
    for endpoint_id, endpoint in node.endpoints.items():
        if endpoint.has_attribute(THREAD_BR_MANAGEMENT_CLUSTER, ATTR_BORDER_ROUTER_NAME):
            return endpoint_id
    return None


class ThreadBrManager:
    """跟踪 matter-server 里的边界路由器节点。"""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: MatterClient, matter_entry_id: str) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self._matter_entry_id = matter_entry_id
        self._platforms: dict[Platform, tuple[AddEntitiesCallback, EntityFactory]] = {}
        self._known: dict[int, int] = {}  # node_id -> 端点
        self._coordinators: dict[int, BorderRouterCoordinator] = {}
        self._node_unsubs: dict[int, list[Callable[[], None]]] = {}
        self._creating: set[int] = set()  # 正在创建网络的节点
        self._changing: set[int] = set()  # 正在迁移信道的节点
        self._listeners: dict[int, list[Callable[[], None]]] = {}
        # 凭证自动同步:记录每个节点上次看到的 Active Timestamp(出现过才算"观察到变化"),
        # 以及已经自动同步过的 (节点, 时间戳),同一个时间戳只自动尝试一次
        self._last_timestamp: dict[int, int | None] = {}
        self._auto_synced: set[tuple[int, int]] = set()

    @property
    def border_routers(self) -> dict[int, int]:
        """当前已发现的边界路由器:node_id -> 端点。"""
        return dict(self._known)

    def coordinator(self, node_id: int) -> BorderRouterCoordinator:
        return self._coordinators[node_id]

    def _matter_device(self, node_id: int) -> dr.DeviceEntry | None:
        return dr.async_get(self.hass).async_get_device_by_identifier(
            matter_device_identifier(self.client, node_id), self._matter_entry_id
        )

    def device_info(self, node_id: int) -> DeviceInfo:
        """本集成为边界路由器注册的设备;Matter 设备已存在时用 via_device_id 关联。"""
        info = DeviceInfo(
            identifiers={own_device_identifier(self.client, node_id)},
            translation_key="thread_br",
            translation_placeholders={"name": DEFAULT_DEVICE_NAME},
        )
        if matter_device := self._matter_device(node_id):
            info["via_device_id"] = matter_device.id
            info["translation_placeholders"] = {"name": matter_device.name_by_user or matter_device.name or DEFAULT_DEVICE_NAME}
        return info

    # ---- 状态:繁忙标记和通知 ----

    def is_creating(self, node_id: int) -> bool:
        return node_id in self._creating

    def is_changing(self, node_id: int) -> bool:
        return node_id in self._changing

    def is_busy(self, node_id: int) -> bool:
        return node_id in self._creating or node_id in self._changing

    def has_network(self, node_id: int) -> bool:
        coordinator = self._coordinators.get(node_id)
        return coordinator is not None and coordinator.data is not None and coordinator.data.has_network

    def add_listener(self, node_id: int, listener: Callable[[], None]) -> Callable[[], None]:
        """登记状态变化的监听;返回取消登记的函数。"""
        self._listeners.setdefault(node_id, []).append(listener)

        def remove() -> None:
            if listener in self._listeners.get(node_id, []):
                self._listeners[node_id].remove(listener)

        return remove

    @callback
    def _notify(self, node_id: int) -> None:
        for listener in list(self._listeners.get(node_id, [])):
            listener()

    def node_for_device(self, device_id: str) -> int:
        """把 HA 设备 id 解析成边界路由器的 node_id;不是本集成的边界路由器设备就报错。"""
        device = dr.async_get(self.hass).async_get(device_id)
        if device is not None and device.config_entry_id == self.entry.entry_id:
            for node_id in self._known:
                if own_device_identifier(self.client, node_id) in device.identifiers:
                    return node_id
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="device_not_found")

    # ---- 创建网络 ----

    async def async_create_network(
        self,
        node_id: int,
        *,
        network_name: str | None = None,
        channel: int | None = None,
        passphrase: str | None = None,
    ) -> None:
        """在边界路由器上创建 Thread 网络,并把凭证保存到 HA。

        成功后设备上已有网络;只有保存凭证失败时才会报"已创建但未保存",
        此时可以用 async_sync_network 从设备读回凭证补存。
        """
        endpoint_id = self._known.get(node_id)
        if endpoint_id is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="device_not_found")
        if self.is_busy(node_id):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="operation_in_progress")
        try:
            if channel is None:
                channel = self._select_channel()
            new = generate_dataset(network_name, channel, passphrase)
        except DatasetError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_parameter", translation_placeholders={"reason": str(err)}
            ) from err

        self._creating.add(node_id)
        self._notify(node_id)
        try:
            try:
                await create_network(self.client, node_id, new.tlv_hex, endpoint_id=endpoint_id)
            except CreateNetworkError as err:
                if err.code == CreateNetworkError.ALREADY_EXISTS:
                    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="network_exists") from err
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="create_failed", translation_placeholders={"reason": str(err)}
                ) from err
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="create_failed",
                    translation_placeholders={"reason": str(err) or type(err).__name__},
                ) from err
            LOGGER.info("已在 node %s 上创建 Thread 网络 %s(信道 %s)", node_id, new.network_name, new.channel)
            await self._async_save_credentials(new.tlv_hex)
        finally:
            await self._async_finish_operation(node_id, self._creating)

    # ---- 采纳已有网络 ----

    async def async_adopt_network(
        self, node_id: int, *, source_node_id: int | None = None, delay_seconds: int = DEFAULT_DELAY_SECONDS
    ) -> None:
        """让一个还没有网络的边界路由器加入一个已有的 Thread 网络(组成多边界路由器)。

        dataset 来自另一台边界路由器(source_node_id),不指定则用 HA 的首选网络。
        下发流程与创建网络相同(ArmFailSafe → SetActiveDataset → CommissioningComplete),
        只是 dataset 不是新生成的。设备已经在另一个网络里时,走整网迁移(见 _async_migrate_to)。
        """
        endpoint_id = self._known.get(node_id)
        if endpoint_id is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="device_not_found")
        if self.is_busy(node_id):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="operation_in_progress")
        if source_node_id == node_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_parameter",
                translation_placeholders={"reason": "来源设备不能是它自己"},
            )

        tlv_hex = await self._async_dataset_to_adopt(source_node_id)
        try:
            validate_dataset(tlv_hex)
            items = parse_tlv(tlv_hex)
            network_name = items[MeshcopTLVType.NETWORKNAME].name
            ext_pan_id = int.from_bytes(items[MeshcopTLVType.EXTPANID].data, "big")
        except (DatasetError, KeyError, ValueError) as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_parameter", translation_placeholders={"reason": str(err)}
            ) from err
        data = self._coordinators[node_id].data
        if data is not None and data.has_network:
            if data.extended_pan_id == ext_pan_id:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="already_on_network")
            # 设备已经在另一个网络里:Matter 不能删除网络,只能把整个旧网络迁到目标网络
            await self._async_migrate_to(node_id, endpoint_id, tlv_hex, network_name, delay_seconds)
            return

        self._creating.add(node_id)
        self._notify(node_id)
        try:
            try:
                await create_network(self.client, node_id, tlv_hex, endpoint_id=endpoint_id)
            except CreateNetworkError as err:
                if err.code == CreateNetworkError.ALREADY_EXISTS:
                    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="network_exists") from err
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="adopt_failed", translation_placeholders={"reason": str(err)}
                ) from err
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="adopt_failed",
                    translation_placeholders={"reason": str(err) or type(err).__name__},
                ) from err
            LOGGER.info("node %s 已加入 Thread 网络 %s", node_id, network_name)
            # 这个网络的凭证多半已经在 HA 里了,这里只是确认并补上同步给 matter-server
            await self._async_save_credentials(tlv_hex, allow_existing=True)
        finally:
            await self._async_finish_operation(node_id, self._creating)

    async def _async_migrate_to(
        self, node_id: int, endpoint_id: int, tlv_hex: str, network_name: str, delay_seconds: int
    ) -> None:
        """把设备上已有的网络整个迁到目标网络:旧网络里的所有设备一起切换,不能取消。"""
        self._changing.add(node_id)
        self._notify(node_id)
        try:
            try:
                await migrate_network(
                    self.client,
                    node_id,
                    tlv_hex,
                    endpoint_id=endpoint_id,
                    delay_seconds=delay_seconds,
                    poll_interval=CHANGE_POLL_INTERVAL,
                    extra_wait=CHANGE_EXTRA_WAIT,
                    on_step=lambda step: LOGGER.debug("采纳网络:%s", step),
                )
            except ChangeChannelError as err:
                if err.code in (ChangeChannelError.PENDING_IN_PROGRESS,):
                    raise ServiceValidationError(translation_domain=DOMAIN, translation_key=err.code) from err
                if err.code == ChangeChannelError.NOT_LEADER:
                    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="adopt_not_leader") from err
                if err.code == ChangeChannelError.SAME_NETWORK:
                    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="already_on_network") from err
                if err.code == ChangeChannelError.INVALID:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN, translation_key="invalid_parameter", translation_placeholders={"reason": str(err)}
                    ) from err
                key = "adopt_not_switched" if err.code == ChangeChannelError.NOT_SWITCHED else "adopt_failed"
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key=key, translation_placeholders={"reason": str(err)}
                ) from err
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="adopt_failed",
                    translation_placeholders={"reason": str(err) or type(err).__name__},
                ) from err
            LOGGER.info("node %s 的 Thread 网络已迁移到网络 %s", node_id, network_name)
            # 设备上的 dataset 与首选网络只差时间戳(为了比旧网络新),把它存回 HA
            new_tlv = await read_active_dataset(self.client, node_id, endpoint_id)
            if new_tlv is not None:
                await self._async_save_credentials(new_tlv, allow_existing=True)
        finally:
            await self._async_finish_operation(node_id, self._changing)

    async def _async_dataset_to_adopt(self, source_node_id: int | None) -> str:
        """取要采纳的 dataset:来源设备的当前网络,或 HA 的首选网络。凭证含密钥,不写日志。"""
        if source_node_id is not None:
            endpoint_id = self._known.get(source_node_id)
            if endpoint_id is None:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="device_not_found")
            try:
                tlv_hex = await read_active_dataset(self.client, source_node_id, endpoint_id)
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="adopt_failed",
                    translation_placeholders={"reason": str(err) or type(err).__name__},
                ) from err
            if tlv_hex is None:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="source_no_network")
            return tlv_hex
        preferred = await dataset_store.async_get_preferred_dataset(self.hass)
        if preferred is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_preferred_dataset")
        return preferred

    # ---- 迁移信道 ----

    async def async_change_channel(
        self,
        node_id: int,
        *,
        channel: int | None = None,
        delay_seconds: int = DEFAULT_DELAY_SECONDS,
    ) -> None:
        """把设备上的 Thread 网络迁到新信道,网络不重建,延迟到期后所有设备一起切换。

        不指定信道时按环境自动选(同创建网络)。会一直等到设备报告已在新信道上,
        再把新的凭证保存到 HA。
        """
        endpoint_id = self._known.get(node_id)
        if endpoint_id is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="device_not_found")
        if self.is_busy(node_id):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="operation_in_progress")

        try:
            state = await read_border_router_state(self.client, node_id, endpoint_id)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="change_failed",
                translation_placeholders={"reason": str(err) or type(err).__name__},
            ) from err
        if not state.has_network:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_network")
        if not state.is_leader:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_leader")
        target = self._select_channel() if channel is None else channel
        if target == state.channel:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="already_on_channel", translation_placeholders={"channel": str(target)}
            )

        self._changing.add(node_id)
        self._notify(node_id)
        try:
            try:
                await change_channel(
                    self.client,
                    node_id,
                    target,
                    endpoint_id=endpoint_id,
                    delay_seconds=delay_seconds,
                    poll_interval=CHANGE_POLL_INTERVAL,
                    extra_wait=CHANGE_EXTRA_WAIT,
                    on_step=lambda step: LOGGER.debug("改信道:%s", step),
                )
            except ChangeChannelError as err:
                if err.code in (ChangeChannelError.NO_NETWORK, ChangeChannelError.NOT_LEADER, ChangeChannelError.SAME_CHANNEL, ChangeChannelError.PENDING_IN_PROGRESS):
                    raise ServiceValidationError(translation_domain=DOMAIN, translation_key=err.code) from err
                if err.code == ChangeChannelError.INVALID:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN, translation_key="invalid_parameter", translation_placeholders={"reason": str(err)}
                    ) from err
                key = "change_not_switched" if err.code == ChangeChannelError.NOT_SWITCHED else "change_failed"
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key=key, translation_placeholders={"reason": str(err)}
                ) from err
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="change_failed",
                    translation_placeholders={"reason": str(err) or type(err).__name__},
                ) from err
            LOGGER.info("node %s 的 Thread 网络已迁移到信道 %s", node_id, target)
            tlv_hex = await read_active_dataset(self.client, node_id, endpoint_id)
            if tlv_hex is not None:
                await self._async_save_credentials(tlv_hex)
        finally:
            await self._async_finish_operation(node_id, self._changing)

    def _select_channel(self) -> int:
        """没指定信道时,避开已知的 Zigbee(ZHA)、用户填的 Wi-Fi 信道和要避开的信道来选。"""
        options = self.entry.options
        zigbee = zigbee_channels(self.hass)
        try:
            selection = choose_channel(
                zigbee=zigbee,
                wifi=[int(c) for c in options.get(CONF_WIFI_CHANNELS, [])],
                avoid=[int(c) for c in options.get(CONF_AVOID_CHANNELS, [])],
            )
        except NoChannelAvailable as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_channel_available") from err
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_parameter", translation_placeholders={"reason": str(err)}
            ) from err
        LOGGER.info(
            "自动选择信道 %s(第 %s 档);Zigbee 信道 %s;排除:%s",
            selection.channel,
            selection.tier,
            zigbee or "无",
            "、".join(f"{c}({why})" for c, why in sorted(selection.excluded.items())) or "无",
        )
        return selection.channel

    async def _async_finish_operation(self, node_id: int, busy: set[int]) -> None:
        """操作结束:先刷新状态(此时仍标记为繁忙,自动同步不会插手),再解除繁忙标记并重新评估。"""
        try:
            await self._coordinators[node_id].async_refresh()
        finally:
            busy.discard(node_id)
            self._notify(node_id)
            self._on_coordinator_update(node_id)

    # ---- 凭证 ----

    async def async_sync_network(self, node_id: int) -> None:
        """从设备读回当前网络的凭证,保存到 HA(用于创建成功但保存失败后的恢复)。"""
        endpoint_id = self._known.get(node_id)
        if endpoint_id is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="device_not_found")
        if self.is_busy(node_id):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="operation_in_progress")
        try:
            tlv_hex = await read_active_dataset(self.client, node_id, endpoint_id)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="sync_failed",
                translation_placeholders={"reason": str(err) or type(err).__name__},
            ) from err
        if tlv_hex is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_network")
        await self._async_save_credentials(tlv_hex)
        await self._coordinators[node_id].async_refresh()

    async def _async_save_credentials(self, tlv_hex: str, *, allow_existing: bool = False) -> None:
        """保存到 HA 的 Thread 凭证库,并同步给 matter-server(之后才能用它给 Thread 设备配网)。

        凭证含密钥,不写日志。没有首选网络时把它设为首选,已有首选网络则不改动。
        """
        try:
            result = await dataset_store.async_add_dataset(self.hass, DATASET_SOURCE, tlv_hex)
            if result is not dataset_store.DatasetAddResult.STORED and not allow_existing:
                raise HomeAssistantError("HA 的 Thread 凭证库里已有这个网络的更新的凭证")
            store = await dataset_store.async_get_store(self.hass)
            if store.preferred_dataset is None:
                ext_pan_id = parse_tlv(tlv_hex)[MeshcopTLVType.EXTPANID].data.hex()
                entry = next((e for e in store.datasets.values() if e.extended_pan_id == ext_pan_id), None)
                if entry is not None:
                    store.preferred_dataset = entry.id
            await self.client.set_thread_operational_dataset(tlv_hex)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="credentials_not_saved",
                translation_placeholders={"reason": str(err) or type(err).__name__},
            ) from err

    @callback
    def _on_coordinator_update(self, node_id: int) -> None:
        """每次读到新状态后评估凭证:观察到网络变化就自动同步一次;仍不一致就给出修复提示。"""
        coordinator = self._coordinators.get(node_id)
        if coordinator is None or coordinator.data is None:
            return
        timestamp = coordinator.data.active_timestamp
        first_update = node_id not in self._last_timestamp
        previous = self._last_timestamp.get(node_id)
        self._last_timestamp[node_id] = timestamp
        if self.is_busy(node_id):
            return  # 创建/迁移过程中由它们自己负责保存凭证

        status = coordinator.credentials
        issue_id = f"credentials_{node_id}"
        if status in (SAVED, NO_NETWORK):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        # 运行中观察到网络变了(被别的方式创建、或信道被换了):凭证落后了,自动同步一次
        if (not first_update and timestamp is not None and timestamp != previous
                and (node_id, timestamp) not in self._auto_synced):
            self._auto_synced.add((node_id, timestamp))
            self.entry.async_create_background_task(self.hass, self._async_auto_sync(node_id), f"thread_br auto sync {node_id}")
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=f"credentials_{status}",
            translation_placeholders={"name": coordinator.data.network_name or DEFAULT_DEVICE_NAME},
            data={"node_id": node_id},
        )

    async def _async_auto_sync(self, node_id: int) -> None:
        LOGGER.info("node %s 上的 Thread 网络发生了变化,自动同步凭证到 HA", node_id)
        try:
            await self.async_sync_network(node_id)
        except HomeAssistantError as err:
            LOGGER.warning("自动同步凭证失败,请在修复页面手动处理:%s", err)
            self._on_coordinator_update(node_id)  # 同步失败:这次要给出修复提示

    # ---- 发现 ----

    def register_platform(self, platform: Platform, add_entities: AddEntitiesCallback, factory: EntityFactory) -> None:
        """平台启动时登记自己,并立即为已发现的节点创建实体。"""
        self._platforms[platform] = (add_entities, factory)
        for node_id, endpoint_id in self._known.items():
            add_entities(factory(self, node_id, endpoint_id))

    @callback
    def start(self) -> list[Callable[[], None]]:
        """发现现有节点并订阅后续变化,返回取消订阅的函数列表。"""
        for node in self.client.get_nodes():
            self._setup_node(node)

        def node_added(event: EventType, node: MatterNode) -> None:
            self._setup_node(node)

        def node_removed(event: EventType, node_id: int) -> None:
            if self._known.pop(node_id, None) is not None:
                self._remove_node(node_id)

        return [
            # 新配对的节点、或节点重新 interview 后才出现 BR cluster,都要重新检查
            self.client.subscribe_events(callback=node_added, event_filter=EventType.NODE_ADDED),
            self.client.subscribe_events(callback=node_added, event_filter=EventType.NODE_UPDATED),
            self.client.subscribe_events(callback=node_removed, event_filter=EventType.NODE_REMOVED),
        ]

    async def async_stop(self) -> None:
        """卸载集成时:取消订阅、关闭协调器、清除修复提示。"""
        for node_id in list(self._known):
            self._remove_node(node_id, remove_device=False)
        self._known.clear()

    def _setup_node(self, node: MatterNode) -> None:
        if node.node_id in self._known:
            return
        endpoint_id = border_router_endpoint(node)
        if endpoint_id is None:
            return
        node_id = node.node_id
        LOGGER.debug("发现 Thread 边界路由器:node %s,端点 %s", node_id, endpoint_id)
        self._known[node_id] = endpoint_id

        coordinator = BorderRouterCoordinator(self.hass, self.entry, self.client, node_id, endpoint_id)
        self._coordinators[node_id] = coordinator
        remove_listener = coordinator.async_add_listener(lambda: self._on_coordinator_update(node_id))

        # matter-server 推送了 TBR 管理或诊断 cluster 的属性变化,或节点上下线时,立即刷新(会合并短时间内的多次)
        prefixes = (f"{endpoint_id}/{THREAD_BR_MANAGEMENT_CLUSTER}/", f"{endpoint_id}/{THREAD_NETWORK_DIAGNOSTICS_CLUSTER}/")

        def refresh_soon() -> None:
            self.entry.async_create_background_task(self.hass, coordinator.async_request_refresh(), f"thread_br refresh {node_id}")

        def attribute_updated(event: EventType, data: object) -> None:
            path = data[1] if isinstance(data, tuple) and len(data) > 1 else ""
            if isinstance(path, str) and path.startswith(prefixes):
                refresh_soon()

        self._node_unsubs[node_id] = [
            remove_listener,
            self.client.subscribe_events(callback=attribute_updated, event_filter=EventType.ATTRIBUTE_UPDATED, node_filter=node_id),
            self.client.subscribe_events(callback=lambda event, data: refresh_soon(), event_filter=EventType.NODE_UPDATED, node_filter=node_id),
        ]

        for add_entities, factory in self._platforms.values():
            add_entities(factory(self, node_id, endpoint_id))
        # 第一次读状态放到后台,设备暂时不可达也不会拖慢 HA 启动;读到之前实体显示为不可用
        self.entry.async_create_background_task(self.hass, coordinator.async_refresh(), f"thread_br first refresh {node_id}")
        if self._matter_device(node_id) is None:
            self._schedule_link(node_id, 0)

    def _remove_node(self, node_id: int, *, remove_device: bool = True) -> None:
        for unsub in self._node_unsubs.pop(node_id, []):
            unsub()
        coordinator = self._coordinators.pop(node_id, None)
        if coordinator is not None:
            self.entry.async_create_background_task(self.hass, coordinator.async_shutdown(), f"thread_br shutdown {node_id}")
        self._last_timestamp.pop(node_id, None)
        ir.async_delete_issue(self.hass, DOMAIN, f"credentials_{node_id}")
        if remove_device:
            self._remove_device(node_id)

    def _remove_device(self, node_id: int) -> None:
        """Matter 节点被移除时,同时移除我们为它注册的设备。"""
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier(own_device_identifier(self.client, node_id), self.entry.entry_id)
        if device is not None:
            registry.async_remove_device(device.id)

    def _schedule_link(self, node_id: int, attempt: int) -> None:
        if attempt >= len(LINK_RETRY_DELAYS):
            LOGGER.debug("node %s 的 Matter 设备一直没有出现,放弃关联", node_id)
            return

        @callback
        def link(_now: datetime) -> None:
            if node_id not in self._known:
                return
            registry = dr.async_get(self.hass)
            matter_device = self._matter_device(node_id)
            own = registry.async_get_device_by_identifier(own_device_identifier(self.client, node_id), self.entry.entry_id)
            if matter_device is None or own is None:
                self._schedule_link(node_id, attempt + 1)
                return
            registry.async_update_device(own.id, via_device_id=matter_device.id)

        self.entry.async_on_unload(async_call_later(self.hass, LINK_RETRY_DELAYS[attempt], link))
