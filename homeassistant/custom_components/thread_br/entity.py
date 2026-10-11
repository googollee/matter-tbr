# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""Thread Border Router 实体基类。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from matter_server.common.models import EventType

from .const import DOMAIN, MATTER_DOMAIN, MATTER_ID_TYPE_DEVICE_ID
from .create import BorderRouterState

if TYPE_CHECKING:
    from matter_server.client import MatterClient

    from .coordinator import BorderRouterCoordinator
    from .manager import ThreadBrManager


def operational_id(client: MatterClient, node_id: int) -> str:
    """节点在 fabric 内的唯一标识(与 Matter 集成的 Operational Instance ID 同格式)。"""
    return f"{client.server_info.compressed_fabric_id:016X}-{node_id:016X}"


def matter_device_identifier(client: MatterClient, node_id: int) -> tuple[str, str]:
    """Matter 集成给节点设备注册的标识,用来找到那个设备(不是用来给我们的实体挂靠)。

    格式与 homeassistant.components.matter.helpers.get_node_device_identifier 一致,
    测试里会与它对照,Matter 集成改动格式时测试会失败。
    """
    return (MATTER_DOMAIN, f"{MATTER_ID_TYPE_DEVICE_ID}_{operational_id(client, node_id)}-MatterNodeDevice")


def own_device_identifier(client: MatterClient, node_id: int) -> tuple[str, str]:
    """本集成为边界路由器注册的设备的标识。"""
    return (DOMAIN, operational_id(client, node_id))


class ThreadBrEntity(CoordinatorEntity["BorderRouterCoordinator"]):
    """边界路由器节点上的实体:状态来自协调器(直接从设备读),繁忙状态来自管理器。"""

    _attr_has_entity_name = True

    def __init__(self, manager: ThreadBrManager, node_id: int, endpoint_id: int, key: str) -> None:
        super().__init__(manager.coordinator(node_id))
        self._manager = manager
        self._client = manager.client
        self._node_id = node_id
        self._endpoint_id = endpoint_id
        self._attr_unique_id = f"{DOMAIN}-{operational_id(self._client, node_id)}-{endpoint_id}-{key}"
        self._attr_translation_key = key
        self._attr_device_info = manager.device_info(node_id)

    @property
    def state_data(self) -> BorderRouterState | None:
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """读到过一次状态、最近一次读取成功,并且 Matter 节点在线。"""
        if self.coordinator.data is None or not super().available:
            return False
        try:
            return self._client.get_node(self._node_id).available
        except KeyError:
            return False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # 节点上线/离线;管理器的繁忙状态(正在创建/迁移网络)
        self.async_on_remove(
            self._client.subscribe_events(
                callback=self._on_node_event, event_filter=EventType.NODE_UPDATED, node_filter=self._node_id
            )
        )
        self.async_on_remove(self._manager.add_listener(self._node_id, self._on_manager_update))

    @callback
    def _on_node_event(self, event: EventType, data: Any) -> None:
        self.async_write_ha_state()

    @callback
    def _on_manager_update(self) -> None:
        self.async_write_ha_state()
