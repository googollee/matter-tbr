# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""Thread Border Router:在 Matter 设备页上管理 Thread 边界路由器的 Thread 网络。

依赖 HA 自带的 Matter 集成,复用它到 matter-server 的连接,不另建连接。
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, LOGGER, MATTER_DOMAIN
from .manager import ThreadBrManager
from .services import async_setup_services

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


@dataclass
class ThreadBrData:
    manager: ThreadBrManager


type ThreadBrConfigEntry = ConfigEntry[ThreadBrData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """注册操作(整个集成共用,与配置条目无关)。"""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ThreadBrConfigEntry) -> bool:
    matter_entries = hass.config_entries.async_loaded_entries(MATTER_DOMAIN)
    if not matter_entries:
        raise ConfigEntryNotReady("Matter 集成尚未加载")
    matter_entry = matter_entries[0]
    client = matter_entry.runtime_data.adapter.matter_client

    manager = ThreadBrManager(hass, entry, client, matter_entry.entry_id)
    entry.runtime_data = ThreadBrData(manager)
    entry.async_on_unload(manager.async_stop)

    # Matter 集成被重载或卸载后,它的 client 就失效了,本集成跟着重载以拿到新的
    @callback
    def matter_state_changed() -> None:
        if matter_entry.state is not ConfigEntryState.LOADED:
            LOGGER.debug("Matter 集成状态变为 %s,重载本集成", matter_entry.state)
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(matter_entry.async_on_state_change(matter_state_changed))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for unsubscribe in manager.start():
        entry.async_on_unload(unsubscribe)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ThreadBrConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
