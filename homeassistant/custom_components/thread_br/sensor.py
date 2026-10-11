# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""边界路由器的传感器:Thread 网络状态、角色、信道、凭证状态和诊断计数。"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import LOGGER, ROUTING_ROLES
from .credentials import STATES as CREDENTIAL_STATES
from .coordinator import PREFERRED_DIFFERENT, PREFERRED_STATES
from .entity import ThreadBrEntity

NETWORK_NONE = "none"
NETWORK_CREATING = "creating"
NETWORK_CHANGING = "changing"
NETWORK_ACTIVE = "active"


class ThreadNetworkSensor(ThreadBrEntity, SensorEntity):
    """边界路由器上有没有 Thread 网络;有的话,属性里带网络名、信道等公开信息。

    不暴露 Network Key 和 PSKc。
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [NETWORK_NONE, NETWORK_CREATING, NETWORK_CHANGING, NETWORK_ACTIVE]

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "thread_network")

    @property
    def native_value(self) -> str | None:
        # 创建/迁移过程中设备上的状态是中间态,所以优先看管理器的繁忙标记
        if self._manager.is_creating(self._node_id):
            return NETWORK_CREATING
        if self._manager.is_changing(self._node_id):
            return NETWORK_CHANGING
        data = self.state_data
        if data is None:
            return None
        if data.has_network and data.has_pending:  # 别的工具发起的迁移,或本集成超时后仍在等待的迁移
            return NETWORK_CHANGING
        return NETWORK_ACTIVE if data.has_network else NETWORK_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.state_data
        if data is None or not data.has_network:
            return None
        return {
            "network_name": data.network_name,
            "channel": data.channel,
            "pan_id": f"0x{data.pan_id:04x}" if isinstance(data.pan_id, int) else None,
            "extended_pan_id": f"{data.extended_pan_id:016x}" if isinstance(data.extended_pan_id, int) else None,
        }


class ThreadRoleSensor(ThreadBrEntity, SensorEntity):
    """边界路由器在 Thread 网络中的角色(Leader、Router 等)。"""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(ROUTING_ROLES.values())
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "thread_role")

    @property
    def native_value(self) -> str | None:
        data = self.state_data
        if data is None or data.role is None:
            return None
        if data.role not in ROUTING_ROLES:
            LOGGER.debug("未知的 Thread 角色值:%s", data.role)
            return None
        return ROUTING_ROLES[data.role]


class ThreadChannelSensor(ThreadBrEntity, SensorEntity):
    """网络当前使用的信道(11~26)。迁移信道后会变。"""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "thread_channel")

    @property
    def native_value(self) -> int | None:
        return self.state_data.channel if self.state_data else None


class ThreadCredentialsSensor(ThreadBrEntity, SensorEntity):
    """HA 的 Thread 凭证库里是否保存了设备上这个网络的凭证。

    saved:已保存;missing:没有保存;out_of_sync:保存的比设备上的旧(比如设备上换了信道)。
    后两种会同时在"修复"页面给出提示,可以一键同步。
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(CREDENTIAL_STATES)
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "credentials")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.credentials if self.state_data else None


class ThreadPreferredNetworkSensor(ThreadBrEntity, SensorEntity):
    """设备所在的网络与 HA 首选 Thread 网络的关系。

    no_preferred:HA 没有首选网络;adopted:设备在首选网络里(按 Extended PAN ID,即同一个网络);
    different:设备没有网络或在另一个网络里,此时"采纳首选网络"按钮可用。
    在另一个网络里时,属性带上它的邻居数:采纳会把那个网络整个迁走,邻居也会一起走。
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(PREFERRED_STATES)

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "preferred_network")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.preferred_status if self.state_data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.state_data
        if data is None or not data.has_network or self.coordinator.preferred_status != PREFERRED_DIFFERENT:
            return None
        return {"neighbors": data.neighbor_count}


class _CountSensor(ThreadBrEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _field = ""

    @property
    def native_value(self) -> int | None:
        return getattr(self.state_data, self._field) if self.state_data else None


class ThreadNeighborsSensor(_CountSensor):
    """邻居表里的设备数(与边界路由器直接通信的 Thread 设备)。"""

    _field = "neighbor_count"

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "neighbors")


class ThreadRoutesSensor(_CountSensor):
    """路由表里的路由器数。"""

    _field = "route_count"

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "routes")


class _IdSensor(ThreadBrEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False  # 排查问题时才有用,默认不显示
    _field = ""

    @property
    def native_value(self) -> int | None:
        return getattr(self.state_data, self._field) if self.state_data else None


class ThreadPartitionIdSensor(_IdSensor):
    _field = "partition_id"

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "partition_id")


class ThreadLeaderRouterIdSensor(_IdSensor):
    _field = "leader_router_id"

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "leader_router_id")


def create_entities(manager: Any, node_id: int, endpoint_id: int) -> list[Entity]:
    return [
        cls(manager, node_id, endpoint_id)
        for cls in (
            ThreadNetworkSensor,
            ThreadRoleSensor,
            ThreadChannelSensor,
            ThreadCredentialsSensor,
            ThreadPreferredNetworkSensor,
            ThreadNeighborsSensor,
            ThreadRoutesSensor,
            ThreadPartitionIdSensor,
            ThreadLeaderRouterIdSensor,
        )
    ]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    entry.runtime_data.manager.register_platform(Platform.SENSOR, async_add_entities, create_entities)
