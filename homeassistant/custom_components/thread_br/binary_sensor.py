# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""边界路由器的二进制传感器:Thread 接口是否启用、网络是否有故障。"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ThreadBrEntity

# Thread 网络诊断的 NetworkFault 枚举
NETWORK_FAULTS = {0: "unspecified", 1: "link_down", 2: "hardware_failure", 3: "network_jammed"}


class ThreadInterfaceSensor(ThreadBrEntity, BinarySensorEntity):
    """Thread 网络接口是否在运行。没有网络,或网络被关闭时为"关"。"""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "thread_interface")

    @property
    def is_on(self) -> bool | None:
        data = self.state_data
        return None if data is None or data.interface_enabled is None else bool(data.interface_enabled)


class ThreadNetworkFaultSensor(ThreadBrEntity, BinarySensorEntity):
    """Thread 网络是否报告了故障(链路断开、硬件故障、被干扰)。属性里列出具体的故障。"""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "network_fault")

    @property
    def is_on(self) -> bool | None:
        data = self.state_data
        return None if data is None else bool(data.faults)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.state_data
        if data is None or not data.faults:
            return None
        return {"faults": [NETWORK_FAULTS.get(f, f"unknown_{f}") for f in data.faults]}


def create_entities(manager: Any, node_id: int, endpoint_id: int) -> list[Entity]:
    return [ThreadInterfaceSensor(manager, node_id, endpoint_id), ThreadNetworkFaultSensor(manager, node_id, endpoint_id)]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    entry.runtime_data.manager.register_platform(Platform.BINARY_SENSOR, async_add_entities, create_entities)
