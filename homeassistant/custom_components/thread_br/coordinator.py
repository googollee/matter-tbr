# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""定期直接从设备读取边界路由器的状态。

不用 matter-server 缓存的节点数据:实测 Thread 网络诊断(0x35)的属性在缓存里不会随设备更新,
网络建立后仍是旧值(角色、信道、网络名都读不到)。所以这里每隔一段时间、以及在相关事件发生时,
用 read_attribute 直接读设备。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components.thread import dataset_store
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from python_otbr_api.tlv_parser import MeshcopTLVType, parse_tlv

from .const import LOGGER
from .create import BorderRouterState, read_border_router_state
from .credentials import NO_NETWORK, credentials_status

if TYPE_CHECKING:
    from matter_server.client import MatterClient

PREFERRED_NONE = "no_preferred"
PREFERRED_ADOPTED = "adopted"
PREFERRED_DIFFERENT = "different"
PREFERRED_STATES = (PREFERRED_NONE, PREFERRED_ADOPTED, PREFERRED_DIFFERENT)

UPDATE_INTERVAL = timedelta(seconds=30)
REFRESH_COOLDOWN = 2.0  # 事件触发的刷新合并窗口(秒)


async def stored_networks(hass: HomeAssistant) -> list[tuple[str, int]]:
    """HA 的 Thread 凭证库里每个网络的 (Extended PAN ID, Active Timestamp)。"""
    store = await dataset_store.async_get_store(hass)
    result = []
    for entry in store.datasets.values():
        try:
            items = parse_tlv(entry.tlv)
            result.append(
                (
                    items[MeshcopTLVType.EXTPANID].data.hex(),
                    int.from_bytes(items[MeshcopTLVType.ACTIVETIMESTAMP].data, "big"),
                )
            )
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("凭证库里有一条无法解析的 dataset,忽略:%s", type(err).__name__)
    return result


async def preferred_extended_pan_id(hass: HomeAssistant) -> int | None:
    """HA 首选 Thread 网络的 Extended PAN ID;没有首选网络、或解析不了时为 None。"""
    tlv = await dataset_store.async_get_preferred_dataset(hass)
    if tlv is None:
        return None
    try:
        return int.from_bytes(parse_tlv(tlv)[MeshcopTLVType.EXTPANID].data, "big")
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("首选网络的 dataset 无法解析,当作没有首选网络:%s", type(err).__name__)
        return None


class BorderRouterCoordinator(DataUpdateCoordinator[BorderRouterState]):
    """一个边界路由器节点的状态。data 是最近一次从设备读到的状态。"""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: MatterClient, node_id: int, endpoint_id: int) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"thread_br node {node_id}",
            update_interval=UPDATE_INTERVAL,
            request_refresh_debouncer=Debouncer(hass, LOGGER, cooldown=REFRESH_COOLDOWN, immediate=True),
        )
        self.client = client
        self.node_id = node_id
        self.endpoint_id = endpoint_id
        # HA 凭证库里是否保存了设备上这个网络的凭证(见 credentials.py)
        self.credentials: str = NO_NETWORK
        # HA 首选 Thread 网络的 Extended PAN ID;没有首选网络时为 None
        self.preferred_ext_pan: int | None = None

    async def _async_update_data(self) -> BorderRouterState:
        try:
            state = await read_border_router_state(self.client, self.node_id, self.endpoint_id)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err) or type(err).__name__) from err
        self.credentials = credentials_status(state, await stored_networks(self.hass))
        self.preferred_ext_pan = await preferred_extended_pan_id(self.hass)
        return state

    @property
    def preferred_status(self) -> str | None:
        """设备的网络与 HA 首选网络的关系;设备上有网络但读不到它的 Extended PAN ID 时为 None(不确定)。"""
        if self.preferred_ext_pan is None:
            return PREFERRED_NONE
        data = self.data
        if data is not None and data.has_network:
            if data.extended_pan_id is None:
                return None
            if data.extended_pan_id == self.preferred_ext_pan:
                return PREFERRED_ADOPTED
        return PREFERRED_DIFFERENT
