# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""边界路由器的按钮:用默认参数创建 Thread 网络;采纳 HA 的首选 Thread 网络。"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PREFERRED_DIFFERENT
from .entity import ThreadBrEntity


class CreateNetworkButton(ThreadBrEntity, ButtonEntity):
    """用默认参数(随机网络名、信道和密钥)创建 Thread 网络。

    设备上已有网络或正在创建/迁移信道时不可用:Matter 没有删除网络的命令,不能重复创建。
    需要指定网络名、信道或密码短语时,用 thread_br.create_network 操作。
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "create_network")

    @property
    def available(self) -> bool:
        return (
            super().available
            and not self._manager.has_network(self._node_id)
            and not self._manager.is_busy(self._node_id)
        )

    async def async_press(self) -> None:
        await self._manager.async_create_network(self._node_id)


class AdoptNetworkButton(ThreadBrEntity, ButtonEntity):
    """让边界路由器加入 HA 的首选 Thread 网络。

    设备上没有网络时直接加入;在另一个网络里时,整个旧网络(包括它的所有设备)迁到首选网络,
    需要边界路由器是 Leader。只在设备所在的网络与首选网络不同时可用。
    需要指定来源(另一个边界路由器的网络)时,用 thread_br.adopt_network 操作。
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, manager: Any, node_id: int, endpoint_id: int) -> None:
        super().__init__(manager, node_id, endpoint_id, "adopt_network")

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.preferred_status == PREFERRED_DIFFERENT
            and not self._manager.is_busy(self._node_id)
        )

    async def async_press(self) -> None:
        await self._manager.async_adopt_network(self._node_id)


def create_entities(manager: Any, node_id: int, endpoint_id: int) -> list[Entity]:
    return [CreateNetworkButton(manager, node_id, endpoint_id), AdoptNetworkButton(manager, node_id, endpoint_id)]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    entry.runtime_data.manager.register_platform(Platform.BUTTON, async_add_entities, create_entities)
